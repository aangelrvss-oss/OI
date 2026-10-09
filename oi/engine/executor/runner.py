"""Executor mínimo: ejecuta un ResearchPlan del Planner (no contiene lógica del caso de prueba).
Search Spec -> Planner -> ResearchPlan -> Executor -> ToolAdapter -> Transport -> Proveedor.
Determinista: ningún LLM decide estados, deduplicación, presupuesto, paginación ni comparaciones numéricas."""
import datetime
import hashlib
import json
import math
import os

from oi.engine.spec.versioning import canonical_hash
from oi.engine.evaluators import entity as EN; from oi.engine.evaluators import evaluation as EV; from oi.engine.evaluators import relevance as RV; from oi.engine.planner import validation as PV; from oi.engine.evaluators import identity as IDV; from oi.engine.evaluators import geography as GEOM; from oi.engine.evaluators import selection as SEL
from oi.engine.planner.config import PlannerConfig
from . import EXECUTOR_VERSION
from . import reporting as RP, state as S
from . import domain_precheck as DP
from .config import validate_config
from .errors import AwaitingExternalResponse, ProviderError, classify, sanitize
from .ledger import UNKNOWN, ResultLedger, ToolCallLedger, canon, input_hash

FATAL_FOR_RUN = ("AUTH_ERROR", "PROVIDER_UNAVAILABLE")
STOP_ENRICHMENT = ("AUTH_ERROR", "PROVIDER_UNAVAILABLE", "RATE_LIMIT")


def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Executor:
    def __init__(self, plan, spec, adapter, transport, config, cfg=None, clock=None):
        self.plan, self.spec, self.adapter, self.t, self.c = plan, spec, adapter, transport, config
        self.cfg = cfg or PlannerConfig(); self.clock = clock or _now
        self.origin = getattr(transport, "data_origin", "UNKNOWN")
        self.run_id = "run-" + hashlib.sha256(canon({"plan": plan["identity"]["content_hash"], "config": sanitize(config), "origin": self.origin, "v": EXECUTOR_VERSION}).encode()).hexdigest()[:12]
        self.sm = S.StateMachine(self.clock)
        self.calls = ToolCallLedger(self.run_id, plan["identity"]["plan_id"]); self.results = ResultLedger()
        self.seq = 0; self.phase_calls = {}; self.errors = []; self.fatal = None; self.budget_blocked = None; self.unknown_cost_seen = False
        self.rel_decisions, self.already_enriched = {}, []; self.quarantine, self.orphans = {}, []; self.expected_countries = [g["name"] for g in spec["restrictions"]["geography"].get("include", []) if g.get("level") == "country"]
        self.relevance_report = self.relevance_input = self.checkpoint = self.exclusions = self.final_selection = None; self.discovery_cost = None; self.reserved_evidence = 0; self.evidence = []; self.query_meta = {}; self.signals = {}
        self.cap_breached = False; self.pending = None; self.last_balance = None; self.notes = []; self.cache = dict(config["prior_enrichment_cache"]); self.records = {}
        b = plan["budget"]["credit_budget"]["apollo"]["max"]
        self.credit_cap = min(x for x in (config["credit_hard_cap"], b, (config.get("execution_approval") or {}).get("max_credits")) if x is not None)
        self.remaining = self.credit_cap
        bp = config.get("balance_policy") or {}                                 # T1-A3 (opt-in): saldo FRESCO obligatorio, ligado al execution_id; el saldo histórico nunca es el saldo actual
        self.fresh = bp.get("mode") == "fresh_required"; self.exec_id = bp.get("execution_id"); self.provider = bp.get("provider", "apollo")
        self.stop_before_paid = None; self.recon = []; self.recon_mismatch = False; self.planned_cost = None; self.paid_started = False; self.frozen_info = None

    # ───────────────────────── API pública
    def run(self):
        blockers = self._precheck()
        if blockers:
            self.sm.go(S.BLOCKED, "; ".join(blockers)); return self._result("BLOCKED", blockers)
        self.sm.go(S.RUNNING, "precheck OK")
        try:
            cands = self._discovery()
            if self.fatal:
                self.sm.go(S.FAILED, self.fatal); return self._result(self.fatal)
            if not cands:
                self.sm.go(S.PARTIAL, "EMPTY_DISCOVERY"); return self._result("EMPTY_DISCOVERY")
            kept = self._freeze_window(self._resolve(cands))
            if self._planned("CANDIDATE_RELEVANCE"):
                if self.c["relevance_proposals"] is None:
                    self.relevance_input = [{"entity_key": e["entity_key"], "name": e["name"], "domain": e["domain"], "website_url": e.get("website_url"), "queries": e.get("queries", []), "rank": e["rank"]} for e in kept]
                    self.sm.go(S.AWAITING_RELEVANCE, "faltan las propuestas de Candidate Relevance"); return self._result("AWAITING_RELEVANCE_PROPOSALS")
                kept = self._relevance(kept)
            kept = self._review_gate(kept)
            batches = self._select(kept)
            if self._checkpoint(kept):
                self.sm.go(S.AWAITING_CHECKPOINT, "punto de control previo al gasto"); return self._result("AWAITING_CHECKPOINT")
            self._enrich(batches, kept)
            self._gates(kept)
            self._evidence(kept)
            self._finalize()
            self._final_selection(kept)
        except AwaitingExternalResponse as a:
            self.pending = a.request; self.sm.go(S.AWAITING, f"petición externa pendiente {a.request['input_hash']}"); return self._result("AWAITING_EXTERNAL_RESPONSE")
        st, why = self._final_state(); self.sm.go(st, why); return self._result(why)

    # ───────────────────────── comprobaciones previas (nada se llama si fallan)
    def _precheck(self):
        b, p = [], self.plan
        b += [f"PLAN_INVALID:{c}" for c, _ in PV.validate_plan(p, self.cfg)]
        if p["status"] == "blocked": b.append("PLAN_BLOCKED")
        if p["status"] == "degraded" and not self.c["confirm_degraded"]: b.append("DEGRADED_PLAN_NOT_CONFIRMED")
        if canonical_hash(self.spec) != self.spec["control"]["content_hash"] or self.spec["control"]["content_hash"] != p["spec_ref"]["content_hash"]: b.append("SPEC_MISMATCH")
        b += validate_config(self.c, self.adapter.capabilities())
        d = next(x for x in p["phases"] if x["phase_id"] == "DISCOVERY")
        if not self.adapter.supports_tool(d["selected_tool"]) or p["tool_policy"]["status_by_tool"].get(d["selected_tool"]) != "available": b.append(f"TOOL_NOT_SUPPORTED_BY_ADAPTER:{d['selected_tool']}")
        if self._planned("ENRICHMENT") and self._unit_cost("apollo.org_enrich") is None: b.append("ENRICHMENT_COST_NOT_OBSERVED")
        jp = self.c["job_postings"]
        if jp["enabled"]:
            if self._unit_cost("apollo.job_postings") is None: b.append("JOB_POSTINGS_COST_NOT_OBSERVED")
            elif not self._planned("EVIDENCE"): b.append("JOB_POSTINGS_ENABLED_BUT_EVIDENCE_NOT_PLANNED")
        eu, ju = self._unit_cost("apollo.org_enrich") or 0, (self._unit_cost("apollo.job_postings") or 0) if jp["enabled"] else 0
        if (jp["max_requests"] if jp["enabled"] else 0) * ju > self.credit_cap: b.append("EXPOSURE_EXCEEDS_HARD_CAP: la evidencia reservada por sí sola supera el tope duro")   # el enrichment se ACOTA al saldo restante del tope
        for ph in ("DECISION_MAKER_ENRICHMENT",) + (() if jp["enabled"] else ("EVIDENCE",)):
            if self._planned(ph): self.notes.append(f"{ph} planificada pero NO ejecutada: fuera del alcance de 3B (sin people search, Firecrawl, OpenRush ni job postings)")
        return b

    def _planned(self, ph): return any(x["phase_id"] == ph and x["status"] == "planned" for x in self.plan["phases"])
    def _unit_cost(self, tool):
        o = self.plan["tool_policy"]["observed_credits_by_tool"].get(tool); return None if not o else o["credits_per_unit"]

    # ───────────────────────── llamada instrumentada
    def _measure(self, point, of_seq):
        if self.c["measure_credits"] == "never" or (self.c["measure_credits"] == "paid_calls" and point.endswith("free")): return None
        op, params = self.adapter.build_request("credit_balance", {})
        ctx = {"measure": point, "of_seq": of_seq}
        if self.fresh: ctx["execution_id"] = self.exec_id          # una lectura grabada de OTRA ejecución no puede reproducirse como saldo actual de ésta
        t0 = self.clock()
        meta = {"balance_kind": "FRESH_CURRENT", "execution_id": self.exec_id, "provider": self.provider} if self.fresh else {}
        try:
            v = self.adapter.parse("credit_balance", self.t.invoke(op, params, context=ctx))["credits_remaining"]
            self.calls.add(phase="METERING", tool="credit_balance", operation=op, input_hash=input_hash(op, ctx), entity_ids=[], estimated_cost=0,
                           actual_cost=UNKNOWN, status="OK" if v is not None else "UNMEASURABLE", result_count=None, started_at=t0, finished_at=self.clock(), data_origin=self.origin,
                           credit_after=v, capability_notes=["lectura de saldo; su propio coste no está auditado formalmente"], **meta, **({"balance_read_at": self.clock(), "balance_value": v} if self.fresh else {}))
            return v
        except AwaitingExternalResponse: raise
        except Exception as e:
            self.calls.add(phase="METERING", tool="credit_balance", operation=op, entity_ids=[], estimated_cost=0, actual_cost=UNKNOWN, status="ERROR", error_code=classify(e),
                           error_message=str(e)[:300], started_at=t0, finished_at=self.clock(), data_origin=self.origin, **meta)
            return None

    def _call(self, phase, lop, generic, entity_ids, est, paid, notes=None):
        tool = self.adapter.tool_ids.get(lop)
        try:
            op, params = self.adapter.build_request(lop, generic)
        except ProviderError as e:
            self.calls.add(phase=phase, tool=tool, operation=lop, entity_ids=entity_ids, estimated_cost=est, actual_cost=0, status="REFUSED", error_code=e.code, error_message=e.message,
                           started_at=self.clock(), finished_at=self.clock(), data_origin=self.origin)
            self.errors.append(e.code); return None
        base = dict(phase=phase, tool=tool, operation=op, input_hash=input_hash(op, params), entity_ids=entity_ids, estimated_cost=est, data_origin=self.origin, capability_notes=notes)
        refuse = self._authorize(phase, tool, op)
        if refuse:
            self.calls.add(**base, actual_cost=0, status="REFUSED", error_code="OPERATION_NOT_ALLOWED", error_message=refuse, started_at=self.clock(), finished_at=self.clock())
            self.errors.append("OPERATION_NOT_ALLOWED"); return None
        if paid:   # tope duro: se comprueba ANTES de llamar con remaining_credit_budget
            why = None
            if self.unknown_cost_seen and self.c["on_unknown_actual_cost"] == "halt_paid_calls": why = "coste real de una llamada previa no medible: se detienen las llamadas de pago"
            elif self.fresh and self.recon_mismatch: why = "BUDGET_RECONCILIATION_MISMATCH previo: se detienen las llamadas de pago hasta una decisión humana"
            elif est > self.remaining - (self.reserved_evidence if phase == "ENRICHMENT" else 0):
                why = f"estimado {est} > remaining_credit_budget {self.remaining}" + (f" menos {self.reserved_evidence} reservados para evidencia" if phase == "ENRICHMENT" and self.reserved_evidence else "")
            if why:
                self.calls.add(**base, actual_cost=0, status="BUDGET_BLOCKED", error_code="BUDGET_BLOCKED", error_message=why, remaining_credit_budget=self.remaining, started_at=self.clock(), finished_at=self.clock())
                self.budget_blocked = self.budget_blocked or why; return None
        self.seq += 1; seq = self.seq
        measure = self.c["measure_credits"] == "all_calls" or (paid and self.c["measure_credits"] == "paid_calls")
        before = (self.last_balance if self.last_balance is not None else self._measure("before" + ("" if paid else "-free"), seq)) if measure else None   # el 'después' anterior sirve de 'antes'
        if self.fresh and paid:      # T1-A3: STOP_BEFORE_PAID_CALL si no hay saldo FRESCO o no cubre el coste; nunca se recurre a un saldo histórico
            why = None; unit_total = self.planned_cost if not self.paid_started else None
            if before is None: why = "FRESH_BALANCE_UNAVAILABLE: sin lectura de saldo fresca no se inicia ninguna llamada de pago (el saldo histórico no es un sustituto)"
            elif before < max(est, unit_total or 0):
                why = f"INSUFFICIENT_BALANCE: saldo fresco {before} < coste " + (f"total solicitado {unit_total}" if unit_total and before >= est else f"estimado {est}")
            if why:
                self.seq -= 1
                self.calls.add(**base, actual_cost=0, status="BUDGET_BLOCKED", error_code="BUDGET_BLOCKED", error_message="STOP_BEFORE_PAID_CALL: " + why, remaining_credit_budget=self.remaining, balance_before=before,
                               balance_kind="FRESH_CURRENT", execution_id=self.exec_id, provider=self.provider, started_at=self.clock(), finished_at=self.clock())
                self.budget_blocked = self.budget_blocked or why; self.stop_before_paid = self.stop_before_paid or why; return None
        req = self.c["require_measured_balance_before_paid_call"]
        if req is None: req = self.c["mode"] == "relay"
        if paid and req and before is None:
            self.seq -= 1
            self.calls.add(**base, actual_cost=0, status="BUDGET_BLOCKED", error_code="BUDGET_BLOCKED", error_message="CREDIT_BALANCE_UNMEASURABLE: sin saldo medido no se inicia ninguna llamada de pago",
                           remaining_credit_budget=self.remaining, started_at=self.clock(), finished_at=self.clock())
            self.budget_blocked = self.budget_blocked or "CREDIT_BALANCE_UNMEASURABLE"; return None
        t0 = self.clock(); self.phase_calls[phase] = self.phase_calls.get(phase, 0) + 1
        if paid: self.paid_started = True
        try:
            parsed = self.adapter.parse(lop, self.t.invoke(op, params, context={"phase": phase, "seq": seq}))
            err = None
        except AwaitingExternalResponse: raise
        except Exception as e:
            parsed, err = None, e
        after = self._measure("after" + ("" if paid else "-free"), seq) if measure else None
        self.last_balance = after
        actual = (before - after) if (before is not None and after is not None) else UNKNOWN
        rem_before = self.remaining
        if isinstance(actual, (int, float)): self.remaining -= actual
        elif paid:
            self.remaining -= est; self.unknown_cost_seen = True        # provisional: se carga el estimado y se marca
        if isinstance(actual, (int, float)) and paid and actual > rem_before: self.cap_breached = True
        row = dict(base, actual_cost=actual, credit_before=before, credit_after=after, credit_delta=(-actual if isinstance(actual, (int, float)) else UNKNOWN),
                   remaining_credit_budget=self.remaining, started_at=t0, finished_at=self.clock())
        if self.fresh and paid:      # reconciliación: gasto esperado (registros devueltos × coste unitario) frente a gasto medido; el gasto NUNCA se oculta ni se revierte
            n_ = 0 if err is not None else len(parsed.get("items", parsed.get("records", {}))); exp_ = n_ * (self._unit_cost(tool) or 0)
            st_ = "UNRECONCILED_BALANCE_AFTER_UNAVAILABLE" if not isinstance(actual, (int, float)) else ("RECONCILED" if actual == exp_ else "BUDGET_RECONCILIATION_MISMATCH")
            if st_ == "BUDGET_RECONCILIATION_MISMATCH": self.recon_mismatch = True
            rf = {"balance_before": before, "balance_after": after, "measured_delta": (after - before) if isinstance(actual, (int, float)) else UNKNOWN, "expected_delta": -exp_, "reconciliation_status": st_,
                  "balance_kind": "FRESH_CURRENT", "execution_id": self.exec_id, "provider": self.provider}
            row.update(rf); self.recon.append({"call": phase, "seq": seq, **{k: rf[k] for k in ("balance_before", "balance_after", "measured_delta", "expected_delta", "reconciliation_status")}})
        if err is not None:
            code = classify(err); self.errors.append(code)
            self.calls.add(**row, status="ERROR", error_code=code, error_message=str(getattr(err, "message", "") or err)[:300], result_count=None); return None
        n = len(parsed.get("items", parsed.get("records", {})))
        r = self.calls.add(**row, status="OK" if n else "EMPTY_RESULT", error_code=None if n else "EMPTY_RESULT", result_count=n)
        parsed["_call_id"] = r["tool_call_id"]; return parsed

    def _authorize(self, phase, tool, op):
        if op not in self.adapter.READ_OPERATIONS.values(): return "operación fuera de la allowlist de solo lectura del adapter"
        if self.cfg.is_forbidden(op): return "operación prohibida por el catálogo"
        if phase not in self.c["phases_enabled"]: return f"fase {phase} no habilitada en el experimento"
        allowed = {(x["tool"], o) for x in self.plan["tool_policy"]["allowed_by_phase"].get(phase, []) for o in x["operations"]}
        if (tool, op) not in allowed: return f"{tool}/{op} no está seleccionada por el plan para {phase}"
        cap = self.plan["budget"]["phase_allocations"]["by_phase"].get(phase, {}).get("data_calls")
        if cap is not None and self.phase_calls.get(phase, 0) + 1 > cap: return f"asignación de llamadas de {phase} agotada ({cap})"
        amax = (self.c.get("execution_approval") or {}).get("max_data_calls")      # límite APROBADO de llamadas de datos (METERING no cuenta): si se alcanzaría, la llamada se rechaza
        if amax is not None and sum(self.phase_calls.values()) + 1 > amax: return f"límite aprobado de data_calls agotado ({amax})"
        return None

    # ───────────────────────── fases
    def _discovery(self):
        st = next(x for x in self.plan["phases"] if x["phase_id"] == "DISCOVERY")["strategy"]
        queries = [q for q in st["queries"] if q.get("kind") in ("base", "proposed_query")]
        mp, pp = self.c["discovery"]["max_pages"], self.c["discovery"]["per_page"]; cands = []; last_code = None; single = len(queries) == 1
        bounds = self.c["measure_credits"] == "paid_calls" and not self.fresh      # (modo fresco: el coste del discovery reproducido es HISTÓRICO y no se usa como saldo actual) discovery gratuito: se mide el saldo UNA vez al inicio y UNA al final (coste real de todo el discovery)
        start = self._measure("before-discovery", 0) if bounds else None
        if start is not None: self.last_balance = start
        for qi, q in enumerate(queries):
            for page in range(1, mp + 1):
                notes = ["pagination supported_status=UNVERIFIED (página > 1 en modo auditoría)"] if page > 1 else ["página 1 (única página por consulta)"]
                if q.get("kind") == "proposed_query": notes.append(f"consulta {q['id']} · parámetros: " + ", ".join(f"{f['param']}={f.get('mapping_status')}" for f in q["filters"]))
                r = self._call("DISCOVERY", "org_discovery", {"page": page, "per_page": pp, "filters": q["filters"]}, [], 0, False, notes)
                if r is None:
                    last_code = self.errors[-1] if self.errors else "UNKNOWN_ERROR"
                    if last_code in FATAL_FOR_RUN: self.fatal = f"DISCOVERY_FAILED:{last_code}"; return cands
                    break
                self.query_meta.setdefault(q["id"], {"order": qi, "path_hint": q.get("path_hint"), "label": q.get("label"), "returned": 0, "calls": [],
                                                     "geo_tier": 0 if any("madrid" in str(v).lower() for f in q["filters"] if f.get("param") == "organization_locations" for v in f["value"]) else 1})   # solo por el filtro de la CONSULTA, no por la ubicación de la entidad
                self.query_meta[q["id"]]["returned"] += len(r["items"]); self.query_meta[q["id"]]["calls"].append(r["_call_id"])
                for i, it in enumerate(r["items"]):
                    cid = f"p{page}-{i + 1:03d}" if single else f"{q['id']}-p{page}-{i + 1:03d}"
                    cands.append({**it, "cid": cid, "q_index": qi, "q_pos": (page - 1) * pp + i, "query_id": q["id"]})
                    self.results.add(cid, name=it["name"], domain=it.get("domain") or it.get("website_url"), source=r["_call_id"], discovery_query=q["id"], discovery_page=page,
                                     discovery_rank=len(cands), evidence_status="NOT_COLLECTED")
                if len(r["items"]) < pp: break                       # fin de fuente
        if bounds:
            end = self._measure("after-discovery", 0)
            if end is not None: self.last_balance = end
            self.discovery_cost = (start - end) if (start is not None and end is not None) else UNKNOWN
        if not cands and last_code: self.fatal = f"DISCOVERY_FAILED:{last_code}"
        return cands

    def _resolve(self, cands):
        res = EN.resolve_entities([{k: c.get(k) for k in ("id", "name", "domain", "website_url")} for c in cands])
        review = {}
        for rv in res["review"]:
            for k in (rv["a"], rv["b"]): review[k] = rv["state"]
        for e in res["entities"]:
            qs = []
            for j, rank in enumerate(e["sources"]):
                qid = cands[rank]["query_id"]; qs += [qid] if qid not in qs else []
                self.results.rows[cands[rank]["cid"]].update(entity_id=e["entity_key"], dedupe_status="primary" if j == 0 else f"DUPLICATE_MERGED_INTO:{e['entity_key']}",
                                                             review_state=review.get(e["entity_key"]), data_quality=list(e["data_quality"]))
            e["queries"] = qs; e["website_url"] = cands[e["sources"][0]].get("website_url"); e["apollo_id"] = e.get("apollo_id")
            for rank in e["sources"]: self.results.rows[cands[rank]["cid"]]["provenance_queries"] = qs
            e["best_pos"] = min(cands[r]["q_pos"] for r in e["sources"]) + 1; e["overlap"] = len(qs)
        for n, e in enumerate(SEL.order_entities(self.c.get("selection_strategy", "interleave_position_v1"), res["entities"], cands, self.query_meta)): e["rank"] = n      # estrategia de selección explícita (ver selection.py)
        self.dedupe = {"candidates": len(cands), "entities": len(res["entities"]), "merges": [m["kind"] for m in res["merges"]], "silent_merges": res["silent_merges"], "review": res["review"]}
        ex = EN.match_excluded_entities(res["entities"], self.spec["restrictions"].get("excluded_entities", []))
        self.exclusions = {"excluded": ex["excluded"], "review_possible_existing_clients": ex["review"]}
        for x in ex["excluded"]: self.results.update_entity(x["entity_key"], enrichment_status="NOT_ENRICHED_EXCLUDED_BY_USER", final_status="EXCLUDED_BY_USER_RULE")
        for x in ex["review"]: self.results.update_entity(x["entity_key"], review_state="REVIEW_POSSIBLE_EXISTING_CLIENT:" + x["excluded_name"])
        known_ids = {k["apollo_id"]: k for k in self.c["known_enriched"] if k.get("apollo_id")}; known_doms = {EN.normalize_domain(k["domain"]): k for k in self.c["known_enriched"] if k.get("domain")}
        rest = []
        for e in ex["kept"]:        # ya enriquecidas en una prueba anterior: sus datos existen, NO se vuelven a pedir ni a cobrar
            hit = known_ids.get(e.get("apollo_id")) or known_doms.get(e["domain"])
            if hit:
                self.already_enriched.append({"entity_key": e["entity_key"], "name": e["name"], "domain": e["domain"], "apollo_id": e.get("apollo_id"), "status": hit.get("status", "ALREADY_ENRICHED_3C"), "note": hit.get("note")})
                self.results.update_entity(e["entity_key"], enrichment_status="NOT_ENRICHED_ALREADY_ENRICHED_3C", final_status="ALREADY_ENRICHED_3C")
            else: rest.append(e)
        return rest

    def _freeze_window(self, kept):
        """T1-A2: la composición del shortlist queda CONGELADA en las primeras N entidades enriquecibles (tras known_enriched y clientes excluidos). Descartes, exclusiones y precheck RECORTAN la ventana; nada entra después."""
        fs = self.c.get("frozen_shortlist")
        if not fs or not fs.get("enabled"): return kept
        seen, win, out = set(), [], []
        for e in sorted(kept, key=lambda x: x["rank"]):
            if not e["domain"]: continue                                   # sin dominio: no enriquecible (no consume ventana, igual que antes)
            if e["domain"] in seen: continue
            seen.add(e["domain"]); (win if len(win) < fs["window"] else out).append(e)
        for e in out: self.results.update_entity(e["entity_key"], enrichment_status="NOT_IN_FROZEN_SHORTLIST_WINDOW")
        self.frozen_info = {"window": fs["window"], "window_keys": [e["entity_key"] for e in win], "outside_window": len(out)}
        outk = {e["entity_key"] for e in out}
        return [e for e in kept if e["entity_key"] not in outk]

    def _review_gate(self, kept):
        """T1-A2: solo las entidades de la lista blanca (revisión humana atestada) pueden llegar a enrichment de pago; "no revisada" NUNCA equivale a "elegible"."""
        al = self.c.get("enrichment_allowlist")
        if al is None: return kept
        ok = set(al); out = []
        for e in kept:
            if e["domain"] and e["entity_key"] not in ok: self.results.update_entity(e["entity_key"], enrichment_status="NOT_ENRICHED_NOT_HUMAN_REVIEWED", final_status="NOT_HUMAN_REVIEWED_NOT_ELIGIBLE"); continue
            out.append(e)
        return out

    def _relevance(self, kept):
        pol = {**next(x for x in self.plan["phases"] if x["phase_id"] == "CANDIDATE_RELEVANCE")["policy"], "rejected_discard_becomes": self.c.get("relevance_rejected_as", "KEEP")}
        cand = [{"entity_key": e["entity_key"], "name": e["name"], "domain": e["domain"], "website_url": e.get("website_url")} for e in kept]
        res = RV.validate_decisions(cand, self.c["relevance_proposals"], pol); by = {e["entity_key"]: e for e in kept}; self.rel_decisions = res["decisions"]
        for k, d in res["decisions"].items():
            self.results.update_entity(k, relevance_decision=d["decision"], relevance_confidence=d["relevance_confidence"], discard_reason=d["discard_reason"])
            if d["decision"] == "DISCARD": self.results.update_entity(k, enrichment_status="NOT_ENRICHED_DISCARDED_BY_RELEVANCE", final_status="DISCARDED_BY_RELEVANCE")
        self.relevance_report = {"metrics": res["metrics"], "discarded": [{"entity_key": k, "name": by[k]["name"], "domain": by[k]["domain"], "category": d["discard_reason"]["category"], "reason": d["discard_reason"]["text"], "evidence": d["evidence"], "confidence": d["relevance_confidence"]}
                                                                          for k, d in res["decisions"].items() if d["decision"] == "DISCARD"],
                                 "validator_rejections": [{"entity_key": k, "name": by[k]["name"], "problems": d["problems"]} for k, d in res["decisions"].items() if d["validator"] == "discard_rejected"]}
        keep = {c["entity_key"] for c in res["kept"]}
        return [e for e in kept if e["entity_key"] in keep]

    def _select(self, kept):
        if not self._planned("ENRICHMENT"): return []
        unit = self._unit_cost("apollo.org_enrich")
        jp = self.c["job_postings"]; reserve_jobs = jp["max_requests"] * (self._unit_cost("apollo.job_postings") or 0) if jp["enabled"] else 0     # el tope duro cubre enrichment + evidencia
        self.reserved_evidence = reserve_jobs
        cap = min(self.plan["coverage"]["planned_enrichment"], self.c["max_enrichment_entities"], math.floor((self.remaining - reserve_jobs) / unit) if unit else 0)
        bs = self.c["enrichment_batch_size"] or next(x for x in self.plan["phases"] if x["phase_id"] == "ENRICHMENT")["policy"]["batch_size"]["configured_batch_size"]
        pc = self.c.get("domain_precheck") or {}; held = []                                  # DOMAIN_PRECHECK (opt-in, offline): lo obviamente anómalo NO consume capacidad ni crédito
        if pc.get("enabled"):
            ok = []
            for e in kept:
                v = DP.classify(e["domain"], pc.get("suspicious_domains", ()), pc.get("allow_domains", ())) if e["enrichable_by_apollo"] else None
                if v and v["class"] in (DP.MALFORMED, DP.SUSPICIOUS): held.append((e, v)); continue
                ok.append(e)
            for e, v in held: self.results.update_entity(e["entity_key"], enrichment_status="NOT_ENRICHABLE_DOMAIN_MALFORMED" if v["class"] == DP.MALFORMED else "HELD_DOMAIN_SUSPICIOUS_NOT_ENRICHED")
            kept = ok
        q = EN.plan_enrichment_requests(kept, enriched_cache=self.cache, capacity=cap, batch_size=bs, force_reenrich=list(self.c["force_reenrich"]))
        self.selection = {"capacity": cap, "batch_size": bs, "requested_domains": len(q["domains"]), "forced": q["forced"], "skipped": len(q["skipped"]), "domains": list(q["domains"]), "reserve_for_job_postings": reserve_jobs,
                          "domain_precheck": {"enabled": True, "held": [{"entity_key": e["entity_key"], "domain": v["domain"], "class": v["class"], "reasons": v["reasons"]} for e, v in held]} if pc.get("enabled") else None}
        self.planned_cost = len(q["domains"]) * unit if unit else None
        dom = {e["entity_key"]: e["domain"] for e in kept}
        for s in q["skipped"]:
            k, r = s["entity_key"], s["reason"]
            if r.startswith("sin_dominio"): st = "NOT_ENRICHABLE_MISSING_DOMAIN"
            elif r.startswith("ya_enriquecida"): st = "ENRICHMENT_REUSED"; self.records[k] = self.cache[dom[k]]
            elif r.startswith("dominio_duplicado"): st = "SHARED_DOMAIN_SINGLE_ENRICHMENT"
            else: st = "NOT_SELECTED_CAPACITY"
            self.results.update_entity(k, enrichment_status=st)
        self.shared = [(s["entity_key"], dom[s["entity_key"]]) for s in q["skipped"] if s["reason"].startswith("dominio_duplicado")]
        return q["batches"]

    def _checkpoint(self, kept):
        """PUNTO DE CONTROL previo al gasto. Devuelve True si hay que PAUSAR (no hay aprobación humana que coincida con el hash exacto de la lista mostrada)."""
        cp = self.c["checkpoint"]
        if not cp["required"]: return False
        sel = getattr(self, "selection", {"domains": [], "reserve_for_job_postings": 0}); jp = self.c["job_postings"]
        eu, ju = self._unit_cost("apollo.org_enrich") or 0, self._unit_cost("apollo.job_postings") or 0
        sb = self.c.get("shortlist_binding"); kd = {e["domain"]: e["entity_key"] for e in kept if e["domain"]}
        binding = None if sb is None else {**sb, "entity_keys": [kd.get(d) for d in sel.get("domains", [])], "frozen_shortlist": self.c.get("frozen_shortlist"), "enrichment_allowlist_hash": (hashlib.sha256(json.dumps(sorted(self.c["enrichment_allowlist"])).encode()).hexdigest()[:16] if self.c.get("enrichment_allowlist") else None),
                                           "selection_strategy": self.c.get("selection_strategy"), "relevance_discards": sorted(self.rel_decisions and [k for k, d in self.rel_decisions.items() if d["decision"] == "DISCARD"]), "known_enriched": sorted(k.get("apollo_id") or k.get("domain") for k in self.c["known_enriched"])}
        h = RP.shortlist_hash(sel.get("domains", []), jp["max_requests"] if jp["enabled"] else 0, self.c["title_term_families"], self.credit_cap, binding)
        appr = cp["approval"]; ok = bool(appr and appr.get("approved_by") and appr.get("approved_at") and appr.get("shortlist_hash") == h)
        by_dom = {e["domain"]: e for e in kept if e["domain"]}
        flagged = {x["entity_key"]: x for x in (self.exclusions or {}).get("review_possible_existing_clients", [])}
        def _one(d):
            e = by_dom[d]; hints = [self.query_meta.get(q, {}).get("path_hint") for q in e.get("queries", [])]; q0 = (e.get("queries") or ["?"])[0]
            return {"rank": e["rank"], "name": e["name"], "domain": d, "apollo_id": e.get("apollo_id"), "queries": e.get("queries", []), "path_hints": hints, "best_position": e.get("best_pos"), "query_overlap": e.get("overlap"),
                    "relevance": self.rel_decisions.get(e["entity_key"], {}).get("decision", "KEEP"), "inferred_opportunity_type": (hints or [None])[0] or "sin_pista (INFERRED por consulta)",
                    "reason_for_selection": f"{self.c.get('selection_strategy', 'interleave_position_v1')}: mejor posición {e.get('best_pos')} (consulta {q0}); solape {e.get('overlap')}; orden de selección {e['rank'] + 1}",
                    "exclusion_status": ("REVISIÓN: posible coincidencia parcial con cliente actual (" + flagged[e["entity_key"]]["excluded_name"] + ")") if e["entity_key"] in flagged else "no excluida (identidad exacta)", "already_enriched_3c": False,
                    "geography_evidence": "solo el filtro de la consulta (no verificada hasta el enrichment)"}
        proposed = [_one(d) for d in sel.get("domains", []) if d in by_dom]
        dist = {}
        for p in proposed:
            first = (p["path_hints"] or [None])[0]; dist[first or "sin_pista"] = dist.get(first or "sin_pista", 0) + 1
        rows = list(self.results.rows.values()); prim = [r for r in rows if r["dedupe_status"] == "primary"]
        rel = (self.relevance_report or {}).get("metrics", {})
        self.checkpoint = {"stage": "POST_DISCOVERY_PRE_SPEND", "approved": ok, "approval_mismatch": bool(appr) and not ok, "shortlist_hash": h,
            "candidates_discovered": len(rows), "unique_entities": len(prim), "duplicates_merged": len(rows) - len(prim), "missing_domain": sum(1 for r in prim if "MISSING_DOMAIN" in (r["data_quality"] or [])),
            "excluded_existing_clients": self.exclusions["excluded"], "review_possible_existing_clients": self.exclusions["review_possible_existing_clients"],
            "candidates_discarded_by_relevance": rel.get("candidates_discarded_by_relevance", 0), "kept_after_relevance": len(kept), "candidates_kept_for_enrichment": len(proposed),
            "discard_reason": rel.get("discard_reason", {}), "relevance_confidence": rel.get("relevance_confidence", {}), "relevance_discards": (self.relevance_report or {}).get("discarded", []),
            "relevance_validator_rejections": (self.relevance_report or {}).get("validator_rejections", []),
            "proposed_enrichment": proposed, "opportunity_type_distribution": {"by_first_query_path_hint": dist, "basis": "INFERRED: procedencia de consulta, no evidencia de la entidad"},
            "queries": {q: {"returned": m["returned"], "path_hint": m["path_hint"], "label": m["label"]} for q, m in self.query_meta.items()},
            "credit_exposure": {"enrichment_max": len(proposed) * eu, "job_postings_max": (jp["max_requests"] * ju) if jp["enabled"] else 0, "hard_cap": self.credit_cap, "remaining_now": self.remaining,
                                "total_max": len(proposed) * eu + ((jp["max_requests"] * ju) if jp["enabled"] else 0), "within_cap": len(proposed) * eu + ((jp["max_requests"] * ju) if jp["enabled"] else 0) <= self.credit_cap},
            "lexicon_hash_included": bool(self.c["title_term_families"]), "already_enriched_3c": self.already_enriched, "relevance_review": rel.get("candidates_review", 0),
            "approved_entities_template": [{"apollo_id": p["apollo_id"], "name": p["name"], "domain": p["domain"]} for p in proposed], "credit_remaining_now": self.remaining,
            "shortlist_binding": binding, "frozen_window": self.frozen_info}
        return not ok

    def _enrich(self, batches, kept):
        unit = self._unit_cost("apollo.org_enrich"); by_dom = {}
        for e in kept:
            if e["domain"]: by_dom.setdefault(e["domain"], []).append(e["entity_key"])
        stop = None
        if self.frozen_info or self.c.get("shortlist_binding") is not None:      # T1-A2: lo ejecutado debe ser EXACTAMENTE lo hasheado (y, con ventana, nunca salir de ella)
            win = set(self.frozen_info["window_keys"]) if self.frozen_info else None; sent = {k for b in batches for d in b for k in by_dom.get(d, [])}
            if (win is not None and not sent <= win) or [d for b in batches for d in b] != list(self.selection["domains"]):
                self.errors.append("SHORTLIST_MUTATED_AFTER_HASH"); self.notes.append("SHORTLIST_MUTATED_AFTER_HASH: el enrichment solicitado no coincide con la lista hasheada"); batches = []
        for i, batch in enumerate(batches):
            ents = [k for d in batch for k in by_dom.get(d, [])][:len(batch)]
            if stop:
                for k in ents: self.results.update_entity(k, enrichment_status=f"NOT_ATTEMPTED_AFTER:{stop}")
                continue
            appr_e = ((self.c["checkpoint"] or {}).get("approval") or {}).get("entities")
            if appr_e:           # ANTES de cada enrichment: id, nombre, dominio, exclusión y caché deben coincidir con el checkpoint aprobado; si no, STOP
                ap = {x["domain"]: x for x in appr_e}; ek = {e["entity_key"]: e for e in kept}; excl = {x["entity_key"] for x in (self.exclusions or {}).get("excluded", [])}; probs = []
                for d in batch:
                    for k in by_dom.get(d, []):
                        e_ = ek.get(k, {}); a = ap.get(d)
                        if a is None: probs.append(f"{d}: no figura en el checkpoint aprobado")
                        elif a.get("apollo_id") != e_.get("apollo_id") or a.get("name") != e_.get("name"): probs.append(f"{d}: id/nombre difieren del checkpoint")
                        if k in excl: probs.append(f"{d}: entidad excluida")
                        if k in {x["entity_key"] for x in self.already_enriched}: probs.append(f"{d}: ALREADY_ENRICHED_3C")
                if probs:
                    stop = "CHECKPOINT_MISMATCH"; self.errors.append("CHECKPOINT_MISMATCH"); self.notes.append("CHECKPOINT_MISMATCH: " + "; ".join(probs))
                    for k in ents: self.results.update_entity(k, enrichment_status="NOT_ATTEMPTED_AFTER:CHECKPOINT_MISMATCH")
                    continue
            reason = {d: self.c["force_reenrich"][d] for d in batch if d in self.c["force_reenrich"]}
            r = self._call("ENRICHMENT", "org_enrich_bulk", {"domains": batch}, ents, len(batch) * unit, True, [f"force_reenrich: {reason}"] if reason else None)
            if r is None:
                last = self.calls.rows[-1]
                if last["status"] == "BUDGET_BLOCKED":
                    stop = "BUDGET_BLOCKED"
                    for k in ents: self.results.update_entity(k, enrichment_status="NOT_ENRICHED_BUDGET_BLOCKED")
                else:
                    for k in ents: self.results.update_entity(k, enrichment_status=f"ENRICHMENT_ERROR:{last['error_code']}", enrichment_call_id=last["tool_call_id"])
                    if last["error_code"] in STOP_ENRICHMENT: stop = last["error_code"]
                continue
            ent_by_key = {e["entity_key"]: e for e in kept}; used, unmatched = set(), []
            for d in batch:
                for k in by_dom.get(d, []):
                    ent = ent_by_key.get(k, {}); rec = r["records"].get(d) or (r.get("by_id") or {}).get(ent.get("apollo_id"))
                    if rec is None: unmatched.append((k, d)); continue
                    used.add(id(rec)); self._accept_or_quarantine(k, d, ent, rec, r["_call_id"])
            orphans = [o for o in (r.get("all") or []) if id(o) not in used]
            if len(unmatched) == 1 and len(orphans) == 1:                  # 1 petición sin registro y 1 registro sin petición: se emparejan SOLO para VALIDARLOS (nunca para usarlos)
                (k, d) = unmatched.pop(); self._accept_or_quarantine(k, d, ent_by_key.get(k, {}), orphans.pop(), r["_call_id"], paired_by="cardinality_1_to_1")
            for k, d in unmatched: self.results.update_entity(k, enrichment_status="NOT_RETURNED_BY_PROVIDER", enrichment_call_id=r["_call_id"], identity_check="UNKNOWN")
            self.orphans += [{"call_id": r["_call_id"], "id": o.get("id"), "name": o.get("name"), "primary_domain": o.get("primary_domain"), "country": o.get("country")} for o in orphans]
            if self.cap_breached: stop = "HARD_CAP_BREACHED_BY_PROVIDER_BILLING"
            if not stop and self.c["stop_when_enough"]:
                self._gates(kept)
                if len(RP.select_final(self._entity_view(kept), self.c["final_selection"]["target"], self.c["final_selection"]["min_evidence_items"], self.c["final_selection"].get("reportable_contract"))["final"]) >= self.c["final_selection"]["target"]: stop = "ENOUGH_ELIGIBLE"
        for k, d in self.shared:
            if d in self.cache: self.records[k] = self.cache[d]

    def _accept_or_quarantine(self, k, d, ent, rec, call_id, paired_by=None):
        v = IDV.validate_post_enrichment({"apollo_id": ent.get("apollo_id"), "name": ent.get("name"), "domain": d}, rec, self.expected_countries)
        if paired_by: v["paired_by"] = paired_by
        if v["state"] == IDV.KNOWN_MATCH:
            self.cache[d] = rec; self.records[k] = rec
            self.results.update_entity(k, enrichment_status="ENRICHED" if not (k, d) in self.shared else "SHARED_DOMAIN_SINGLE_ENRICHMENT", enrichment_call_id=call_id, identity_check=v["state"])
        else:                                                                # NO es evidencia del candidato pedido: cuarentena (pagado, inutilizable)
            self.quarantine[k] = {"record": {x: rec.get(x) for x in ("id", "name", "primary_domain", "website_url", "country", "city")}, "validation": v}
            self.results.update_entity(k, enrichment_status=f"ENRICHED_IDENTITY_{v['state']}", enrichment_call_id=call_id, identity_check=v["state"])

    def _row(self, key):
        if not hasattr(self, "_prim") or key not in self._prim: self._prim = {r["entity_id"]: r for r in self.results.rows.values() if r["dedupe_status"] == "primary"}
        return self._prim[key]

    def _gates(self, kept):
        pol = next(x for x in self.plan["phases"] if x["phase_id"] == "VERIFIED_GATES")["policy"]["criterion_evaluation"]
        crit = {c["id"]: c for c in self.spec["criteria"]}; geo = self.spec["restrictions"]["geography"]
        unav = {s["criterion_id"]: s for s in self.plan.get("signal_sources_unavailable", [])}
        ev_ids = {c["id"]: c["class"] for c in self.plan["criteria_digest"]["active"] if c["stage"] == "evidence" and c["id"] not in unav}
        for e in kept:
            k = e["entity_key"]; rec = self.records.get(k)
            if rec is None: continue
            det = {}
            for ce in pol:
                d = _evaluate(ce, crit.get(ce["criterion_id"]), geo, rec, as_of=self.clock(), industry_mapping=self.c.get("industry_mapping")) | {"class": ce["class"]}
                if ce["class"] != "HARD":      # señal opcional: nunca un gate; la ausencia NO es FAIL
                    d["signal_state"] = {"PASS": "PRESENT", "FAIL": "NOT_OBSERVED", "CONFLICTING": "CONFLICTING"}.get(d["verdict"], "UNKNOWN"); d["not_a_gate"] = True
                det[ce["criterion_id"]] = d
            for cid, s_ in unav.items(): det[cid] = {"signal_state": "UNKNOWN", "evidence_status": "UNKNOWN", "reason": s_["reason"], "class": s_["class"], "not_a_gate": True}
            for cid, cls in ev_ids.items(): det.setdefault(cid, {"signal_state": "UNKNOWN", "evidence_status": "UNKNOWN", "reason": "no consultada (presupuesto/selección de job postings)", "class": cls, "not_a_gate": True})
            det.update(self.signals.get(k, {}))
            if self.c.get("fit_lexicon"): det["FIT_KW"] = {**EV.evaluate_fit_keywords(rec, self.c["fit_lexicon"]), "class": "FIT_EVIDENCE", "not_a_gate": True}
            geo_d = det.get("GEO") or {}
            self.results.update_entity(k, gate_status=_combine([d for d in det.values() if d["class"] == "HARD"]), gate_details=det, geo_priority_tier=geo_d.get("matched_tier_corroborated") or ("UNKNOWN" if geo_d else None), geo_tier_hint=geo_d.get("tier_single_source_hint"))

    def _entity_view(self, kept):
        out = []
        for e in kept:
            k = e["entity_key"]; row = self._row(k); rec = self.records.get(k); det = row["gate_details"] or {}; tp = RP.fit_paths(rec, self.c["path_taxonomy"])
            if self.c.get("evidence_model") == "fit_opportunity_v2":       # FIT y OPPORTUNITY separados; el CONTEXTO (tamaño, ingresos, matriz, ubicación, solape) NUNCA cuenta como evidencia
                opp_ids = {c["id"] for c in self.spec["criteria"] if c["dimension"] == "opportunity"}
                fit = [{"item": n} for n, cid in (("FIT_industry_naics", "F1"), ("FIT_keywords", "FIT_KW")) if (det.get(cid) or {}).get("signal_state") == "PRESENT"]
                paths, sig = fit, [cid for cid, d in det.items() if cid in opp_ids and d.get("signal_state") == "PRESENT"]
            else: paths, sig = tp, [cid for cid, d in det.items() if d.get("signal_state") == "PRESENT"]
            out.append({"entity_key": k, "name": e["name"], "domain": e["domain"], "rank": e["rank"], "enriched": rec is not None, "gate_status": row["gate_status"], "paths": paths, "type_paths": tp,
                        "signals_present": sig, "queries": e.get("queries", []), "apollo_id": e.get("apollo_id"),
                        "identity": row.get("identity_check"), "gate_results": {cid: {"verdict": d.get("verdict"), "evidence_status": d.get("evidence_status")} for cid, d in det.items() if isinstance(d, dict)}})
        return out

    def _profile(self, k, e):
        row = self._row(k); rec = self.records.get(k); det = row["gate_details"] or {}; ic = row.get("identity_check")
        prof = {"data_confidence": "NOT_CALIBRATED", "identity": {"state": ic, "evidence_status": "KNOWN" if ic == "KNOWN_MATCH" else "UNKNOWN"}}
        if rec is None: return prof
        for name, cid, dim in (("geography", "GEO", "GEO"), ("fit_industry_naics", "F1", "FIT"), ("fit_keywords", "FIT_KW", "FIT"), ("opp_funding_O4", "O4", "OPPORTUNITY"), ("opp_growth_O5", "O5", "OPPORTUNITY"), ("opp_tech_hiring_T1", "T1", "OPPORTUNITY"), ("opp_hiring_pace_O2", "O2", "OPPORTUNITY"), ("opp_projects_P1", "P1", "OPPORTUNITY")):
            d = det.get(cid)
            if d is not None: prof[name] = {"dimension": dim, "state": d.get("signal_state") or d.get("verdict"), "evidence_status": d.get("evidence_status", "UNKNOWN")}
        par = (rec.get("ultimate_parent_organization") or {}).get("name")
        prof["context_not_evidence"] = {"estimated_num_employees": rec.get("estimated_num_employees"), "revenue": rec.get("organization_revenue"), "parent": par, "naics": rec.get("naics_codes"), "industry": rec.get("industry"),
                                        "location": [rec.get("city"), rec.get("state"), rec.get("country")], "query_overlap": e.get("overlap"), "geo_priority_tier": row.get("geo_priority_tier")}
        return prof

    def _evidence(self, kept):
        """EVIDENCE = job postings (1 crédito/petición, observado), solo top candidatos y como máximo max_requests. Opt-in explícito. Señales opcionales: nunca FAIL."""
        jp = self.c["job_postings"]
        if not (jp["enabled"] and self._planned("EVIDENCE")): return
        crit = {c["id"]: c for c in self.spec["criteria"]}
        ev = [c for c in self.plan["criteria_digest"]["active"] if c["evidence_source"] == "apollo_job_postings"]
        view = [v for v in self._entity_view(kept) if v["enriched"] and v["gate_status"] not in RP.GATE_BAD]
        order = sorted(view, key=lambda v: (0 if v["paths"] else 1, v["rank"]))[:jp["max_requests"]]        # orden de selección declarado: ruta de encaje primero, luego rango
        for v in order:
            r = self._call("EVIDENCE", "org_job_postings", {"id": v["apollo_id"], "page": 1, "per_page": jp["per_page"]}, [v["entity_key"]], self._unit_cost("apollo.job_postings"), True, ["job postings: forma de respuesta UNVERIFIED (parser tolerante)"])
            if r is None:
                last = self.calls.rows[-1]
                self.results.update_entity(v["entity_key"], evidence_status=f"JOB_POSTINGS_{last['status']}:{last['error_code']}")
                if last["status"] == "BUDGET_BLOCKED" or last["error_code"] in STOP_ENRICHMENT: break
                continue
            row = self.calls.rows[-1]; pg = r.get("pagination") or {}; all_seen = (pg.get("total_pages") or 1) <= 1
            sigs = {}
            for c in ev:
                cr = crit.get(c["id"], {}); prm = cr.get("params") or {}
                if c["field"] == "hiring.technical_roles": res = EV.evaluate_title_families(r["items"], prm.get("title_term_families") or self.c["title_term_families"], all_seen)
                elif c["field"] == "growth.hiring_velocity": res = EV.evaluate_hiring_velocity(r["items"], row["finished_at"], prm.get("period_days", 90), cr.get("value") or 1)
                else: res = {"signal_state": "UNKNOWN", "evidence_status": "UNKNOWN", "reason": "sin evaluador para esta señal"}
                sigs[c["id"]] = {**res, "class": c["class"], "not_a_gate": True, "source": "apollo.organizations_job_postings", "tool_call_id": r["_call_id"], "retrieved_at": row["finished_at"]}
            self.signals[v["entity_key"]] = sigs
            self.evidence.append({"entity_key": v["entity_key"], "name": v["name"], "source": "apollo_organizations_job_postings", "tool_call_id": r["_call_id"], "retrieved_at": row["finished_at"], "postings_returned": len(r["items"]), "pagination": r.get("pagination"),
                                  "urls": [p["url"] for p in r["items"] if p.get("url")][:10], "signals": {k_: {kk: vv for kk, vv in s_.items() if kk != "matches"} | ({"matches": s_["matches"]} if "matches" in s_ else {}) for k_, s_ in sigs.items()}})
            self.results.update_entity(v["entity_key"], evidence_status=f"JOB_POSTINGS_RETRIEVED:{len(r['items'])}")
        for v in view:
            if v["entity_key"] not in self.signals and (self._row(v["entity_key"])["evidence_status"] in (None, "NOT_COLLECTED")):      # no pisar un error técnico ya registrado
                self.results.update_entity(v["entity_key"], evidence_status="NOT_QUERIED_JOB_POSTINGS")
        self._gates(kept)

    def _final_selection(self, kept):
        fs = self.c["final_selection"]; res = RP.select_final(self._entity_view(kept), fs["target"], fs["min_evidence_items"], fs.get("reportable_contract"))
        ok = {x["entity_key"]: x for x in res["final"]}
        for e in kept:
            row = self._row(e["entity_key"]); x = ok.get(e["entity_key"]); row["reportable"] = bool(x); row["evidence_items"] = x["evidence_items"] if x else None
        if self.c.get("evidence_model") == "fit_opportunity_v2":
            allv = {v["entity_key"]: v for v in self._entity_view(kept)}
            for e in kept:
                row = self._row(e["entity_key"]); v = allv[e["entity_key"]]; row["evidence_items"] = (len(v["paths"]) + len(v["signals_present"])) if v["enriched"] else None; row["evidence_profile"] = self._profile(e["entity_key"], e)
        self.final_selection = res

    def _finalize(self):
        fin = {"passed": "PASSED_VERIFIED_GATES (sin evidencia ni scoring todavía)", "failed": "FAILED_GATE_KNOWN", "gate_failed_estimated": "GATE_FAILED_ESTIMATED (conservado para revisión)",
               "gate_failed_inferred": "GATE_FAILED_INFERRED (conservado para revisión)", "conflicting_review": "REVIEW_REQUIRED_CONFLICT", "pending_verification": "PENDING_VERIFICATION"}
        for r in self.results.rows.values():
            if r["final_status"]: continue
            if r["dedupe_status"] and r["dedupe_status"].startswith("DUPLICATE_MERGED"): r["final_status"] = "DUPLICATE_MERGED"
            elif r["gate_status"]: r["final_status"] = fin[r["gate_status"]]
            elif r["enrichment_status"] and r["enrichment_status"].startswith("ENRICHMENT_ERROR"):
                r["final_status"] = "ENRICHMENT_ERROR"; r["gate_status"] = "NOT_EVALUATED_TECHNICAL_ERROR"     # error técnico registrado; NO es un criterio UNKNOWN
            else: r["final_status"] = r["enrichment_status"] or "NOT_PROCESSED"
            if r["gate_status"] is None: r["gate_status"] = "NOT_EVALUATED"

    def _final_state(self):
        if self.fresh and self.recon_mismatch: return (S.BUDGET_EXHAUSTED if self.cap_breached else S.PARTIAL), "BUDGET_RECONCILIATION_MISMATCH"
        if self.fresh and self.stop_before_paid: return S.PARTIAL, "STOP_BEFORE_PAID_CALL:" + self.stop_before_paid.split(":")[0]
        pending = any(r["enrichment_status"] in ("NOT_ENRICHED_BUDGET_BLOCKED",) for r in self.results.rows.values())
        if self.cap_breached: return S.BUDGET_EXHAUSTED, "HARD_CAP_BREACHED_BY_PROVIDER_BILLING"
        if self.budget_blocked and self.unknown_cost_seen and self.c["on_unknown_actual_cost"] == "halt_paid_calls": return S.PARTIAL, "PAID_CALLS_HALTED_UNKNOWN_ACTUAL_COST"
        if self.budget_blocked and pending: return S.BUDGET_EXHAUSTED, "BUDGET_BLOCKED"
        tech = [e for e in self.errors if e not in ("EMPTY_RESULT",)]
        if tech: return S.PARTIAL, "TECHNICAL_ERRORS:" + ",".join(sorted(set(tech)))
        if self.plan["status"] == "degraded": return S.DEGRADED, "PLAN_DEGRADED_CONFIRMED_SCOPE_EXECUTED"
        return S.COMPLETED, "EXPERIMENT_SCOPE_EXECUTED"

    # ───────────────────────── resultado y persistencia
    def _result(self, stop_reason, blockers=None):
        rows = list(self.results.rows.values()); cr = self.calls.credits_actual()
        return {"run_id": self.run_id, "plan_id": self.plan["identity"]["plan_id"], "executor_version": EXECUTOR_VERSION, "state": self.sm.state, "state_meaning": S.MEANING.get(self.sm.state),
                "stop_reason": stop_reason, "blockers": blockers or [], "data_origin": self.origin, "events": self.sm.events, "notes": self.notes,
                "credit": {"hard_cap": self.credit_cap, "remaining_credit_budget": self.remaining, "actual_known": cr["known_total"], "calls_with_unknown_actual_cost": cr["unknown_calls"], "paid_calls_with_unknown_actual_cost": cr["unknown_paid_calls"],
                           "remaining_is_provisional": self.unknown_cost_seen, "budget_blocked": self.budget_blocked, "cap_breached": self.cap_breached,
                           **({"balance_policy": {"mode": "fresh_required", "execution_id": self.exec_id, "provider": self.provider}, "reconciliation": {"calls": self.recon, "mismatch": self.recon_mismatch, "rollback": "NONE (el gasto medido no se oculta ni se revierte)"},
                               "stop_before_paid_call": self.stop_before_paid, "next_tranche_authorized": False} if self.fresh else {})},
                "pending_request": self.pending, "checkpoint": self.checkpoint, "relevance": self.relevance_report, "relevance_input": self.relevance_input, "exclusions": self.exclusions,
                "final_selection": self.final_selection, "evidence": self.evidence, "identity_quarantine": self.quarantine, "already_enriched_3c": self.already_enriched, "orphan_records": self.orphans, "discovery_actual_cost": self.discovery_cost, "query_meta": self.query_meta, "tool_calls": self.calls.rows, "results": rows, "metrics": _metrics(self, rows),
                "capabilities": self.adapter.capabilities(), "scoring": "NOT_IMPLEMENTED (D-010 Proposed): no se inventan puntuaciones",
                "prioritization": "orden preliminar = gate_status y luego discovery_rank; NO es scoring"}


def save(result, root):
    """Persistencia LOCAL (no es una escritura externa). Todo ya está saneado."""
    d = os.path.join(root, result["run_id"]); os.makedirs(d, exist_ok=True)
    for name, key in (("manifest", None), ("tool_calls", "tool_calls"), ("results", "results"), ("metrics", "metrics"), ("events", "events")):
        obj = {k: v for k, v in result.items() if k not in ("tool_calls", "results")} if key is None else result[key]
        with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as f: json.dump(sanitize(obj), f, indent=2, ensure_ascii=False)
    import csv
    rows = review_template(result)
    if rows:
        with open(os.path.join(d, "review_template.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return d


def _evaluate(ce, crit, geo, rec, as_of=None, industry_mapping=None):
    ev = ce["evaluator"]
    if ev == "employee_count_estimate" and crit and isinstance(crit.get("value"), list):
        return EV.evaluate_employee_count(rec, *crit["value"])
    if ev == "location_dual_source":
        targets = geo["include"] if ce["criterion_id"] == "GEO" else geo["exclude"]
        return GEOM.evaluate_geography({k: rec.get(k) for k in ("country", "state", "city")}, rec.get("raw_address"), targets, invert=ce["criterion_id"] == "GEO_EXCL")
    if ev == "industry_multilabel":
        mp = industry_mapping or {}; labels = (crit or {}).get("params", {}).get("apollo_industry_labels") or mp.get("labels")
        if not labels:
            return {"verdict": "UNKNOWN", "evidence_status": "UNKNOWN", "gate_state": "pending_verification", "reason": "INDUSTRY_LABEL_MAPPING_MISSING: no hay mapeo validado del texto del spec a etiquetas del proveedor"}
        return EV.evaluate_industry(rec, labels, (crit.get("params") or {}).get("naics_prefixes") or mp.get("naics_prefixes", ()))
    if ev == "growth_window_mapping" and crit:
        pr = crit.get("params") or {}
        win = {"unit": "days", "value": pr["period_days"]} if "period_days" in pr else ({"unit": "months", "value": pr["period_months"]} if "period_months" in pr else None)
        if win is None: return {"verdict": "UNKNOWN", "evidence_status": "UNKNOWN", "gate_state": "pending_verification", "reason": "ventana no especificada"}
        return EV.evaluate_growth(rec, win, crit["operator"], crit["value"])
    if ev == "dated_event_window":
        pr = (crit or {}).get("params") or {}
        if "period_days" not in pr: return {"verdict": "UNKNOWN", "evidence_status": "UNKNOWN", "gate_state": "pending_verification", "reason": "ventana no especificada"}
        import datetime as _dt
        return EV.evaluate_funding_window(rec, pr["period_days"], as_of or _dt.datetime.now(_dt.timezone.utc))
    if ev == "field_presence":
        ok = any(rec.get(f) for f in ce["source_fields"])
        return {"verdict": "PASS" if ok else "UNKNOWN", "evidence_status": "INFERRED" if ok else "UNKNOWN", "gate_state": "passed" if ok else "pending_verification",
                "reason": None if ok else "ausencia en el proveedor no es evidencia"}
    return {"verdict": "UNKNOWN", "evidence_status": "UNKNOWN", "gate_state": "pending_verification", "reason": f"evaluador {ev} no aplicable"}


def _combine(hard):
    states = [d["gate_state"] for d in hard]
    for s in ("failed", "gate_failed_estimated", "gate_failed_inferred", "conflicting_review", "pending_verification"):
        if s in states: return s
    return "passed"


def _metrics(x, rows):
    """Métricas de la sección 6 de la 3B-2. Lo que no se puede medir automáticamente queda marcado, nunca rellenado."""
    prim = [r for r in rows if r["dedupe_status"] == "primary"]; n = lambda f: sum(1 for r in prim if f(r))
    ok_enr = ("ENRICHED", "ENRICHMENT_REUSED", "SHARED_DOMAIN_SINGLE_ENRICHMENT")
    enr = n(lambda r: r["enrichment_status"] in ok_enr); cr = x.calls.credits_actual()
    paid = [c for c in x.calls.rows if c["phase"] == "ENRICHMENT" and c["status"] in ("OK", "EMPTY_RESULT", "ERROR")]
    est = sum(c["estimated_cost"] or 0 for c in paid); act_ok = cr["unknown_paid_calls"] == 0
    passed = n(lambda r: r["gate_status"] == "passed"); rate = lambda a, b: round(a / b, 4) if b else None
    det = [r["gate_details"] for r in prim if r["gate_details"]]
    per = {}
    for d in det:
        for cid, g in d.items(): per.setdefault(cid, {"PASS": 0, "FAIL": 0, "UNKNOWN": 0, "CONFLICTING": 0}); per[cid][g.get("verdict") or {"PRESENT": "PASS"}.get(g.get("signal_state"), "UNKNOWN")] += 1
    loc = {"location_verified": 0, "location_conflicting": 0, "location_unknown": 0}
    for d in det:
        if "GEO" in d: loc[d["GEO"]["location_state"]] += 1
    recs = [x.records[k] for k in {r["entity_id"] for r in prim} if k in x.records]
    emp_av = sum(1 for r in recs if isinstance(r.get("estimated_num_employees"), (int, float)))
    ind_av = sum(1 for r in recs if r.get("industry")); ind_ml = sum(1 for r in recs if len(r.get("industries") or []) > 1)
    ind_crit = [cid for cid in per if cid.startswith("G") and any(cid in d and "industry" in str(d[cid].get("evidence", d[cid].get("reason", ""))) for d in det)]
    kinds = {}
    for k in getattr(x, "dedupe", {}).get("merges", []): kinds[k] = kinds.get(k, 0) + 1
    gs = {}
    for r in prim: gs[r["gate_status"] or "NOT_EVALUATED"] = gs.get(r["gate_status"] or "NOT_EVALUATED", 0) + 1
    disc = [c for c in x.calls.rows if c["phase"] == "DISCOVERY" and c["status"] in ("OK", "EMPTY_RESULT")]
    measures = {
        "discovery": {"total_returned": sum(c["result_count"] or 0 for c in disc), "unique_entities": len(prim), "duplicates": len(rows) - len(prim),
                      "missing_domains": n(lambda r: "MISSING_DOMAIN" in (r["data_quality"] or [])),
                      "obvious_noise": {"value": None, "status": "REQUIRES_HUMAN_LABEL", "how": "columna label_obvious_noise de review_template.csv"}},
        "entity_resolution": {"duplicate_rate": rate(len(rows) - len(prim), len(rows)), "resolution_methods": kinds, "ambiguous_matches": len(getattr(x, "dedupe", {}).get("review", [])),
                              "silent_merges": getattr(x, "dedupe", {}).get("silent_merges", 0)},
        "enrichment": {"candidates_attempted": sum(len(c["entity_ids"] or []) for c in paid), "successful": enr,
                       "failed_technical": n(lambda r: (r["enrichment_status"] or "").startswith("ENRICHMENT_ERROR")), "not_returned_by_provider": n(lambda r: r["enrichment_status"] == "NOT_RETURNED_BY_PROVIDER"),
                       "budget_blocked": n(lambda r: r["enrichment_status"] == "NOT_ENRICHED_BUDGET_BLOCKED"), "actual_credits_consumed": cr["known_total"] if act_ok else None,
                       "average_actual_cost_per_entity": rate(cr["known_total"], enr) if act_ok else None, "paid_calls_with_unknown_cost": cr["unknown_paid_calls"]},
        "employee_data": {"estimate_available": emp_av, "missing_rate": rate(len(recs) - emp_av, len(recs)),
                          "conflicts_observed": {"value": None, "status": "NOT_AUTOMATICALLY_DETECTABLE", "why": "solo existe estimated_num_employees; contrastarlo exige revisión humana u otra fuente"}},
        "location": loc,
        "industry": {"available": ind_av, "unknown": len(recs) - ind_av, "multi_label": ind_ml,
                     "ambiguous_or_conflicting": "no aplicable sin criterio de sector activo" if not ind_crit else {c: per[c]["CONFLICTING"] for c in ind_crit}},
        "gates": {"by_criterion": per, "combined_gate_status": gs},
        "budget": {"estimated_credits": est, "actual_credits": cr["known_total"] if act_ok else None, "variance_actual_minus_estimated": (cr["known_total"] - est) if act_ok else None,
                   "hard_cap": x.credit_cap, "remaining_credit_budget": x.remaining}}
    idc = {}
    for r_ in prim:
        if r_.get("identity_check"): idc[r_["identity_check"]] = idc.get(r_["identity_check"], 0) + 1
    measures["identity"] = {"post_enrichment_states": idc, "quarantined_paid_records": len(x.quarantine), "orphan_records": len(x.orphans)}
    measures["candidate_relevance"] = (x.relevance_report or {}).get("metrics")
    measures["exclusions"] = {"excluded_existing_clients": len((x.exclusions or {}).get("excluded", [])), "review_possible_existing_clients": len((x.exclusions or {}).get("review_possible_existing_clients", []))}
    measures["evidence"] = {"job_postings_requests": sum(1 for c in x.calls.rows if c["phase"] == "EVIDENCE" and c["status"] in ("OK", "EMPTY_RESULT", "ERROR")), "entities_with_evidence": len(x.evidence),
                            "signals_present": {cid: sum(1 for ev in x.evidence if (ev["signals"].get(cid) or {}).get("signal_state") == "PRESENT") for cid in {c for ev in x.evidence for c in ev["signals"]}}}
    measures["final_selection"] = {k: x.final_selection[k] for k in ("target", "returned", "eligible_total", "shortfall", "padded")} if x.final_selection else None
    return {"label": x.origin, "calibrating": False if x.origin.startswith("SYNTHETIC") else "first_empirical_observation_not_calibration",
            "warning": "SYNTHETIC / NON-CALIBRATING: estas métricas solo verifican flujo, estados, ledger, presupuesto, errores, deduplicación e idempotencia" if x.origin.startswith("SYNTHETIC")
                       else "primera observación empírica con n pequeño: no es una calibración",
            "measures": measures, "review_queue": measures["entity_resolution"]["ambiguous_matches"], "passed_verified_gates": passed,
            "credit_variance_actual_minus_estimated": measures["budget"]["variance_actual_minus_estimated"], "credits_per_passed": rate(cr["known_total"], passed) if act_ok else None}


def review_template(result):
    """Plantilla para el etiquetado HUMANO (ruido evidente, relevancia): no se rellena automáticamente."""
    out = []
    for r in result["results"]:
        if r["dedupe_status"] != "primary": continue
        g = r["gate_details"] or {}
        out.append({"entity_id": r["entity_id"], "name": r["name"], "domain": r["domain"], "final_status": r["final_status"], "gate_status": r["gate_status"],
                    "location_state": (g.get("GEO") or {}).get("location_state"), "employee_estimate": (g.get("G2") or {}).get("value"),
                    "label_obvious_noise": "", "label_relevant_for_ai_process_automation": "", "reviewer_notes": ""})
    return out
