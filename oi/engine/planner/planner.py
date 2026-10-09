"""Research Planner v2: Search Spec CONFIRMED + presupuesto -> ResearchPlan. Determinista. No ejecuta nada.
Recalibrado con las capacidades REALES de Apollo Free (Fase 2): discovery barato y ruidoso, enrichment como cuello de botella económico,
gates definitivos solo tras enrichment, entity resolution obligatoria antes de gastar créditos."""
import copy
import datetime
import hashlib
import json
import math

from . import capabilities as CAP
from oi.engine.spec import mini_jsonschema
from oi.engine.spec.versioning import canonical_hash
from . import PLANNER_VERSION
from . import budget as B
from . import criteria as K
from oi.engine.evaluators import evaluation as EV
from . import llm_proposals as LP
from . import rules as R
from . import validation as PV
from .config import PHASE_ORDER, TOOL_PHASES, PlannerConfig

META = {  # (type, purpose, input, output)
 "DISCOVERY": ("tool", "Generar candidatos con un fallback gratuito y ruidoso. NO afirma ningún criterio crítico; el pool es mayor que el objetivo pero NO se enriquece entero.", "Search Spec (filtros de recall)", "candidatos crudos {id,name,domain?,website_url?}"),
 "NORMALIZATION": ("deterministic", "Normalizar nombres, dominios y geografía.", "candidatos crudos", "candidatos normalizados"),
 "ENTITY_RESOLUTION": ("deterministic", "OBLIGATORIA antes de enrichment: resolver identidad (id Apollo > dominio > nombre+ubicación > fuzzy solo como revisión). Sin fusiones silenciosas; padre y filiales no se fusionan.", "candidatos normalizados", "entidades únicas + cola de revisión"),
 "PRE_ENRICHMENT_FILTERS": ("deterministic", "Solo filtros con datos disponibles SIN créditos: dedupe, exclusiones deterministas, comprobación de identificadores. Nunca empleados estimados, sector enriquecido ni crecimiento.", "entidades únicas", "entidades enriquecibles / no enriquecibles (con motivo)"),
 "CANDIDATE_RELEVANCE": ("mixed", "Antes de gastar créditos: ¿tiene sentido investigar más esta entidad como oportunidad? Por defecto MANTENER; solo se descarta irrelevancia CLARA, con motivo y evidencia citada literalmente de nombre/dominio. No resuelve tamaño, Fit, Opportunity ni Data Confidence.", "entidades que pasaron los pre-filtros", "decisiones KEEP/DISCARD con motivo y confianza"),
 "ENRICHMENT": ("tool", "Enriquecer un shortlist acotado por presupuesto de créditos (cuello de botella económico), en tranches y sin repetir entidades.", "entidades enriquecibles (selección según capacidad)", "registros enriquecidos"),
 "VERIFIED_GATES": ("deterministic", "Veredicto por criterio sobre datos enriquecidos, conservando evidencia (KNOWN/INFERRED/ESTIMATED/UNKNOWN/CONFLICTING). UNKNOWN != PASS != FAIL.", "registros enriquecidos", "passed | pending_verification | conflicting_review | gate_failed_* | failed"),
 "EVIDENCE": ("mixed", "Investigación profunda SOLO del top-N; job postings como evidencia (D-027); extracción con cita.", "top-N de VERIFIED_GATES", "filas de evidencia"),
 "SCORING": ("deterministic", "Cálculo determinista de puntuaciones (reglas fuera de esta fase; D-010 Proposed).", "evidencia + gates", "puntuaciones (pendiente de especificar)"),
 "PRIORITIZATION": ("deterministic", "Ordenar y seleccionar finalistas.", "puntuaciones", "ranking y finalistas"),
 "DECISION_MAKER_ENRICHMENT": ("tool", "Decision makers SOLO de finalistas y con campos personales mínimos (herramienta diferida: no se ejecuta ni audita).", "finalistas", "contactos profesionales mínimos"),
 "RESULTS": ("mixed", "Resultados con evidencia, estados y cobertura; la síntesis cita evidence_id.", "ranking + evidencia", "informe + hoja de revisión humana")}
OUTCOMES = {
 "completed": "Se alcanzó el objetivo (qualified >= objetivo alcanzable) con el plan en `ready`, la investigación profunda planificada hecha y sin umbral duro.",
 "degraded": "El plan estaba `degraded` (alcance reducido por presupuesto, confirmado por el usuario) y se alcanzó SU objetivo alcanzable reducido.",
 "partial": "Parada que no es de presupuesto con qualified < objetivo (discovery sin candidatos suficientes, cancelación, bloqueo técnico, relajación no autorizada, plan agotado).",
 "budget_exhausted": "Umbral duro de presupuesto (calls o créditos reales) con trabajo pendiente."}
CEILING = {"employee_count_estimate": "ESTIMATED", "location_dual_source": "KNOWN (solo si las dos fuentes coinciden)", "industry_multilabel": "INFERRED", "growth_window_mapping": "INFERRED (días→meses) / ESTIMATED (meses)",
           "field_presence": "INFERRED"}


def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
def _canon(o): return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
def _err(code, msg): return {"code": code, "severity": "ERROR", "message": msg}


class Planner:
    def __init__(self, cfg=None, clock=None):
        self.cfg = cfg or PlannerConfig(); self.clock = clock or _now

    def plan(self, spec, budget_input=None, field_registry_version=None, ruleset_version=None, llm_proposals=None, history=None, run_policy=None, source_classes=None):
        cfg = self.cfg; P = cfg.params
        res = lambda status, errors, code=None, plan=None, warnings=(): {"status": status, "reason_code": code, "plan": plan, "errors": errors, "warnings": list(warnings)}
        # 1) contrato de entrada
        if not isinstance(spec, dict) or "control" not in spec:
            return res("error", [_err("SPEC_INVALID", "No es un Search Spec")], "SPEC_INVALID")
        sv = (spec.get("versions") or {}).get("schema_version")
        try: spec_schema = cfg.registry.schema_for(sv)
        except Exception: return res("error", [_err("SPEC_INVALID", f"schema_version desconocida: {sv!r}")], "SPEC_INVALID")
        l1 = mini_jsonschema.validate(spec, spec_schema)
        if l1: return res("error", [_err("SPEC_INVALID", f"{e['path']}: {e['message']}") for e in l1], "SPEC_INVALID")
        if spec["control"].get("content_hash") != canonical_hash(spec):
            return res("error", [_err("SPEC_TAMPERED", "content_hash no coincide con el contenido del spec")], "SPEC_TAMPERED")
        frv = field_registry_version or spec["versions"]["field_registry_version"]
        if frv != cfg.registry.version or spec["versions"]["field_registry_version"] != cfg.registry.version:
            return res("error", [_err("REGISTRY_VERSION_MISMATCH", f"spec={spec['versions']['field_registry_version']} entrada={frv} cargado={cfg.registry.version}")], "REGISTRY_VERSION_MISMATCH")
        if ruleset_version and ruleset_version != cfg.versions["ruleset_version"]:
            return res("error", [_err("RULESET_VERSION_MISMATCH", f"pedido={ruleset_version} cargado={cfg.versions['ruleset_version']}")], "RULESET_VERSION_MISMATCH")
        st = spec["control"]["status"]
        if st != "confirmed":
            code = {"needs_clarification": "NOT_PLANNABLE_NEEDS_CLARIFICATION", "unsupported_yet": "NOT_PLANNABLE_UNSUPPORTED"}.get(st, "NOT_PLANNABLE_DRAFT")
            return res("not_planned", [_err(code, f"El Search Spec está '{st}': no se crea plan ejecutable.")], code)
        hist, hissues = self._history(history)
        if hissues: return res("not_planned", hissues, hissues[0]["code"])
        # 2) criterios: SOLO active+defined generan trabajo
        active, ignored, classes = K.classify(spec, cfg)
        ctx = {"active": active, "spec": spec, "has_geo": bool(spec["restrictions"]["geography"].get("include")), "flags": {}, "disabled_tools": list((run_policy or {}).get("disabled_tools", []))}
        allowed_pf = set(P["decision_makers"]["allowed_personal_fields"]); pf = set(spec["governance"]["data_policy"].get("personal_data_fields", []))
        ctx["flags"]["dm_personal_data_allowed"] = bool(pf & {"name", "job_title"}) and pf <= (allowed_pf | {"work_email", "location_city"})
        # 3) fases y herramientas por reglas (con disponibilidad y fallback)
        inc = R.phase_inclusion(cfg, ctx); sel = R.tool_selection(cfg, ctx, inc)
        present = [p for p in TOOL_PHASES if inc[p]["included"]]
        covered = {i for p in ("ENRICHMENT", "EVIDENCE") for s in sel.get(p, {"selected": []})["selected"] for i in s["triggering_criteria"]}   # discovery NO verifica criterios
        uncovered = [c for c in active if c["stage"] in ("enrichment", "evidence") and c["id"] not in covered]
        impossible = [c for c in uncovered if c["class"] == "HARD"]                    # solo un requisito OBLIGATORIO sin fuente bloquea el plan
        sig_unavail = [{"criterion_id": c["id"], "field": c["field"], "class": c["class"], "state": "UNKNOWN", "reason": "sin herramienta autorizada/disponible para esta señal: se informa UNKNOWN y no se simula"} for c in uncovered if c["class"] != "HARD"]
        paid = {p: {cfg.tools[s["selected_tool"]]["provider"] for s in sel[p]["selected"] if cfg.observed_credits(s["selected_tool"])} for p in present}
        # 4) presupuesto: llamadas y créditos son presupuestos distintos
        needs_deep = inc["EVIDENCE"]["included"] or inc["DECISION_MAKER_ENRICHMENT"]["included"]
        needs_credits = any(paid.values())
        bud, bissues = B.normalize_budget(spec, budget_input, P, needs_deep, needs_credits)
        errs = [i for i in bissues if i["severity"] == "ERROR"]; warnings = [i for i in bissues if i["severity"] != "ERROR"]
        if errs: return res("not_planned", errs, errs[0]["code"])
        llm, lissues = LP.validate(llm_proposals, spec, P); warnings += lissues
        enr0 = sel["ENRICHMENT"]["selected"] if "ENRICHMENT" in present else []
        e_cr = cfg.observed_credits(enr0[0]["selected_tool"]) if enr0 else None
        ev_cr = sum(cfg.observed_credits(s["selected_tool"]) or 0 for s in sel["EVIDENCE"]["selected"]) if "EVIDENCE" in present else 0
        dem = {pv: B.credit_demand(spec["restrictions"]["quantity"]["target"], present, P, bud, e_cr, ev_cr, hist, len(llm["query_variants"])) for pv in ("apollo",)}
        dem = {pv: d for pv, d in dem.items() if d}
        alloc = B.allocate(bud, present, paid, cfg.weights(), dem)
        cpc = sum(P["evidence"]["calls_per_company"][s["selected_tool"]] for s in sel["EVIDENCE"]["selected"]) if "EVIDENCE" in present else 0
        enr = [s for s in sel["ENRICHMENT"]["selected"]] if "ENRICHMENT" in present else []
        enrich_credit = cfg.observed_credits(enr[0]["selected_tool"]) if enr else None
        evid_credit = sum(cfg.observed_credits(s["selected_tool"]) or 0 for s in sel["EVIDENCE"]["selected"]) if "EVIDENCE" in present else 0
        T = spec["restrictions"]["quantity"]["target"]; batch = P["enrichment"]["batch_size"]["configured_batch_size"]
        fun = B.size_funnel(T, present, P, alloc, bud, cpc, enrich_credit, evid_credit, hist, len(llm["query_variants"]), batch, explicit_queries=len(llm["discovery_queries"]) or None)
        blocked = list(fun["blocked"]) + [f"IMPOSSIBLE_REQUIREMENT: {c['id']} ({c['field']}) no tiene ninguna herramienta disponible en el plan actual" for c in impossible]
        phases = self._phases(cfg, inc, sel, active, fun, alloc, T, llm, spec, batch, enrich_credit)
        infra_total = sum(p["estimated_calls"]["infra_calls"] for p in phases)
        if bud["infra_calls_max"] is not None and infra_total > bud["infra_calls_max"]:
            blocked.append(f"INFRA_BUDGET_INSUFFICIENT: se necesitan ~{infra_total} llamadas de infraestructura y el límite es {bud['infra_calls_max']}")
        # 5) cobertura y estado
        has_e = "ENRICHMENT" in present
        achievable = min(T, fun["final"]["enrichment"] if has_e else fun["final"]["pool"])
        status = "blocked" if blocked else ("degraded" if (fun["degradations"] or achievable < T) else "ready")
        coverage = self._coverage(T, fun, achievable, present)
        caps = self._capabilities(cfg, sel, active)
        warnings += self._warnings(cfg, active, ignored, T, bud, fun, achievable, present, sel, caps, hist)
        warnings += [{"code": "SIGNAL_SOURCE_UNAVAILABLE", "severity": "WARNING", "message": f"{s['criterion_id']} ({s['field']}): {s['reason']}."} for s in sig_unavail]
        plan = {"identity": {"plan_id": None, "plan_version": 1, "spec_id": spec["control"]["spec_id"], "spec_version": spec["control"]["spec_version"],
                             "planner_version": PLANNER_VERSION, "created_at": self.clock(), "fingerprint": None, "content_hash": None},
                "spec_ref": {"spec_id": spec["control"]["spec_id"], "spec_version": spec["control"]["spec_version"], "status": "confirmed", "content_hash": spec["control"]["content_hash"]},
                "versions": {"planner_version": PLANNER_VERSION, **cfg.versions, "field_registry_version": cfg.registry.version, "spec_schema_version": spec["versions"]["schema_version"]},
                "status": status, "blocked_reasons": blocked, "degradations": fun["degradations"],
                "criteria_digest": {"active": active, "ignored": ignored, "classes": classes,
                                    "rule": "Solo criterios active+defined (campo operativo y enrutable) generan trabajo. proposed / needs_definition / desconocidos se ignoran (TP-06)."},
                "phases": phases, "dependencies": [{"from": q, "to": p["phase_id"]} for p in phases for q in p["prerequisites"]],
                "budget": self._budget_block(bud, alloc, fun, phases, cfg, sel, enrich_credit),
                "stop_conditions": self._stop(cfg, fun, alloc), "expansion_policy": self._expansion(cfg, active, fun, llm, classes),
                "coverage": coverage, "outcome_policy": {"statuses": OUTCOMES, "evaluation": "research_planner.stopping.decide_outcome", "claims_policy": coverage["claims_policy"]},
                "tool_policy": {"forbidden": cfg.catalog["forbidden"], "allowed_by_phase": {p["phase_id"]: [{"tool": s["selected_tool"], "operations": s["operations"]} for s in p["tool_selections"]] for p in phases if p["status"] == "planned"},
                                "tool_kinds": {t: d["kind"] for t, d in cfg.tools.items()}, "status_by_tool": {t: d["status"] for t, d in cfg.tools.items()},
                                "observed_credits_by_tool": {t: d["observed_cost"] for t, d in cfg.tools.items() if cfg.observed_credits(t) is not None}, "post_plan": self._post_plan(cfg, ctx)},
                "capabilities": caps,
                "authorization": {"execution_mode": "dry_run_only", "spend_authorized": False, "reason": "El planner no autoriza gasto real; los costes son OBSERVADOS pero provisionales (calibration_required)."},
                "approvals": self._approvals(status, cfg, ctx), "warnings": warnings,
                "run_policy": {"disabled_tools": ctx["disabled_tools"], "rule": "TP-09"},
                "evidence_requirements": CAP.requirements(spec, {t_: v_.get("status") for t_, v_ in cfg.tools.items()}, ctx["disabled_tools"], source_classes), "signal_sources_unavailable": sig_unavail,
                "llm_input": {"authority": "none", "accepted": llm["query_variants"], "accepted_discovery_queries": [q["id"] for q in llm["discovery_queries"]], "rejected": llm["rejected"], "ignored_keys": llm["ignored_keys"],
                              "explanation": llm["explanation"], "signal_suggestions": llm["signal_suggestions"]}}
        fp = hashlib.sha256(_canon({"spec": spec["control"]["content_hash"], "budget": bud, "versions": plan["versions"], "llm": llm["query_variants"], "dq": llm["discovery_queries"], "history": hist, "run_policy": ctx["disabled_tools"]}).encode()).hexdigest()
        plan["identity"]["fingerprint"] = fp; plan["identity"]["plan_id"] = "plan-" + fp[:12]
        plan["identity"]["content_hash"] = self._hash(plan)
        verrs = PV.validate_plan(plan, cfg)
        if verrs: return res("error", [_err(c, m) for c, m in verrs], "INTERNAL_INVARIANT")
        return res("planned", [], None, plan, warnings)

    @staticmethod
    def _hash(plan):
        p = copy.deepcopy(plan); p["identity"]["content_hash"] = None
        return hashlib.sha256(_canon(p).encode()).hexdigest()

    @staticmethod
    def _history(h):
        """Histórico OBSERVADO opcional (nunca inventado): tasas en (0,1]."""
        if h is None: return None, []
        bad = [k for k in ("gate_pass_rate", "pre_filter_survival") if k in h and not (isinstance(h[k], (int, float)) and 0 < h[k] <= 1)]
        if bad or any(k not in ("gate_pass_rate", "pre_filter_survival", "sample_size") for k in h):
            return None, [_err("HISTORY_INVALID", f"history inválido: {bad or list(h)}")]
        return {k: h[k] for k in sorted(h)}, []

    # ───────────── piezas ─────────────
    def _coverage(self, T, fun, achievable, present):
        f = fun["final"]; has_ev = "EVIDENCE" in present
        return {"requested_target": T, "target_results": T,
                "candidate_pool": {"planned": f["pool"], "ideal": fun["pool_ideal_uncapped"], "ideal_after_depth_cap": fun["ideal"]["pool"], "verified_depth_cap": fun["pool"]["depth_cap"], "capped_by_observed_depth": fun["pool_capped"], "basis": fun["pool"]["basis"]},
                "enrichment_capacity": {"planned": f["enrichment"], "ideal": fun["ideal"]["enrichment"], "bottleneck": "credits (Apollo enrichment ≈ 1 crédito por empresa devuelta, observado)"},
                "deep_research_capacity": {"planned": f["evidence"], "ideal": fun["ideal"]["evidence"]},
                "planned_pool": f["pool"], "planned_enrichment": f["enrichment"], "planned_deep_research": f["evidence"], "planned_finalists": f["finalists"],
                "achievable_qualified_target": achievable, "achievable_basis": "COTA de capacidad = min(objetivo, enrichment o pool planificado); NO es una predicción de cuántos superarán los gates",
                "target_reachable": achievable >= T, "deep_research_covers_target": (f["evidence"] >= T) if has_ev else None,
                "evidence_tiers": {"tier1_full_evidence_max": f["evidence"], "tier2_pending_verification": max(0, achievable - f["evidence"])} if has_ev else None,
                "funnel": "descubiertos → normalización → entity resolution (dedupe) → pre-filtros sin créditos → selección para enrichment según capacidad → enrichment → gates verificados → evidencia top-N → shortlist. descubrir N NO implica enriquecer N.",
                "claims_policy": "Solo se puede afirmar lo que superó los gates verificados con su estado de evidencia: nunca presentar 'descubiertas' o 'enriquecidas' como 'cualificadas'; un valor ESTIMATED/INFERRED se informa como tal."}

    def _capabilities(self, cfg, sel, active):
        tools = sorted({s["selected_tool"] for p in sel.values() for s in p["selected"]})
        ids = sorted({c for t in tools for c in cfg.tools[t].get("relies_on", [])} | {c["capability_ref"] for c in active if c["capability_ref"]})
        used = [{"id": i, "tool": cfg.caps[i]["tool"], "capability": cfg.caps[i]["capability"], "status": cfg.caps[i]["status"], "evidence": cfg.caps[i]["evidence"]} for i in ids]
        return {"account_plan_observed": cfg.catalog["meta"]["account_plan_observed"], "used": used, "unverified_dependencies": [u["id"] for u in used if u["status"] in ("UNVERIFIED", "CONTRADICTED", "UNAVAILABLE")],
                "tool_status": {t: {"status": cfg.tool_status(t), "role": cfg.tools[t]["role"]} for t in tools},
                "provider_abstraction": {"principle": "el plan depende de ROLES (discovery, enrichment, evidence…) y del estado de cada herramienta, no de un proveedor concreto; el Search Spec no menciona proveedores",
                                         "roles": {r: [{"tool": t, "provider": d["provider"], "status": d["status"]} for t, d in sorted(cfg.tools.items()) if d["role"] == r]
                                                   for r in ("company_discovery", "discovery", "enrichment", "evidence", "decision_makers")}}}

    def _phases(self, cfg, inc, sel, active, fun, alloc, T, llm, spec, batch, enrich_credit):
        P = cfg.params; out = []; included = [p for p in PHASE_ORDER if inc[p]["included"]]
        ids = lambda f: [c["id"] for c in active if f(c)]
        allc = ids(lambda c: True)
        crit_by = {"DISCOVERY": ids(lambda c: c["discovery_filter"]), "PRE_ENRICHMENT_FILTERS": ids(lambda c: c["pre_enrichment_gate"]),
                   "ENRICHMENT": ids(lambda c: c["stage"] == "enrichment"), "VERIFIED_GATES": ids(lambda c: c["stage"] == "enrichment"),
                   "EVIDENCE": ids(lambda c: c["stage"] == "evidence")}
        cap = {"DISCOVERY": fun["final"]["pool"], "ENRICHMENT": fun["final"]["enrichment"], "EVIDENCE": fun["final"]["evidence"], "DECISION_MAKER_ENRICHMENT": fun["final"]["finalists"]}
        for i, pid in enumerate(PHASE_ORDER):
            info = inc[pid]; typ, purpose, inp, outp = META[pid]
            if not info["included"]:
                out.append({"phase_id": pid, "type": typ, "purpose": purpose, "status": "skipped", "skip_reason": info["skip_reason"], "prerequisites": [], "input": {"from": None, "description": inp},
                            "output": {"description": outp}, "candidate_tools": [], "selected_tool": None, "tool_selections": [], "applicable_rules": [info["rule_id"]], "active_criteria": [],
                            "max_candidates": 0, "estimated_calls": {"data_calls_max": 0, "infra_calls": 0, "basis": "fase omitida"}, "estimated_credits": {"status": "no_credit_cost", "credits": 0},
                            "stop_conditions": [], "expansion_conditions": []})
                continue
            prev = [p for p in included if PHASE_ORDER.index(p) < i]; prereq = [prev[-1]] if prev else []
            tools = sel.get(pid, {"selected": [], "rejected": []})
            selected = [dict(s, rejected_tools=tools["rejected"]) for s in tools["selected"]]
            calls = fun["calls"].get(pid, 0)
            infra = P["infra"]["calls_per_phase"] + (math.ceil(calls / P["infra"]["settle_every_n_calls"]) if calls else 0) + (P["infra"]["calls_results_write"] if pid == "RESULTS" else 0)
            ph = {"phase_id": pid, "type": typ, "purpose": purpose, "status": "planned", "skip_reason": None, "prerequisites": prereq,
                  "input": {"from": prereq[0] if prereq else "SEARCH_SPEC", "description": inp}, "output": {"description": outp},
                  "candidate_tools": sorted({t for t, d in cfg.tools.items() if pid in d["phases"]}),
                  "selected_tool": selected[0]["selected_tool"] if selected else None, "tool_selections": selected,
                  "applicable_rules": [info["rule_id"]] + [s["rule_id"] for s in tools["selected"]],
                  "active_criteria": crit_by.get(pid, allc if pid in ("SCORING", "PRIORITIZATION", "RESULTS") else []),
                  "max_candidates": cap.get(pid, 0) or 0,
                  "estimated_calls": {"data_calls_max": calls, "infra_calls": infra, "basis": "cota superior por caps de la fase y parámetros de política provisionales"},
                  "estimated_credits": self._credits_of(cfg, pid, selected, fun),
                  "stop_conditions": ["TARGET_REACHED", "BUDGET_THRESHOLD", "MAX_ITERATIONS", "USER_CANCELLED", "TECHNICAL_BLOCK"], "expansion_conditions": ["EXPANSION_LEVERS"] if pid == "DISCOVERY" else []}
            if pid == "ENRICHMENT": ph["estimated_calls"]["calls_if_batch_not_supported"] = fun["calls_if_unbatched"]["ENRICHMENT"]
            if pid == "DISCOVERY": ph["strategy"] = self._discovery(cfg, active, fun, alloc, selected, llm, spec)
            if pid == "NORMALIZATION": ph["policy"] = {"normalize": ["domain (esquema, www, ruta)", "name (acentos, mayúsculas, formas societarias)", "geography"], "missing_domain": "se conserva (MISSING_DOMAIN es un problema de calidad de datos)"}
            if pid == "ENTITY_RESOLUTION": ph["policy"] = {"mandatory_before": "ENRICHMENT", "priority": ["apollo_organization_id", "canonical_domain", "normalized_name+location", "fuzzy_name (solo revisión)"],
                "silent_merges": False, "parent_subsidiary": "NO se fusionan salvo evidencia suficiente (se mantienen como entidades separadas)", "ambiguity": "va a cola de revisión humana",
                "reuse": "evidencia ya obtenida de una entidad se reutiliza: nunca se enriquece dos veces la misma entidad", "shared_domain_different_ids": "entidades separadas; un solo enrichment por dominio"}
            if pid == "CANDIDATE_RELEVANCE":
                rp = P["relevance"]
                ph["policy"] = {"question": "¿Tiene sentido gastar recursos para investigar más esta entidad como posible oportunidad comercial?", "default_decision": rp["default_decision"],
                                "discard_requires": rp["discard_requires"], "allowed_evidence_fields": rp["allowed_evidence_fields"], "reason_categories": rp["reason_categories"],
                                "proposer": "LLM (operador) propone; el código valida y registra. Sin lista de exclusiones prefijada: los juicios se hacen sobre los resultados reales del discovery gratuito.",
                                "never_decides": ["tamaño", "Fit definitivo", "Opportunity definitiva", "Data Confidence", "sector inferido", "ubicación"],
                                "records": ["candidates_discovered", "candidates_discarded_by_relevance", "candidates_kept_for_enrichment", "discard_reason", "relevance_confidence"],
                                "reversible": True, "runs_before": "ENRICHMENT (antes de cualquier gasto de créditos)"}
            if pid == "PRE_ENRICHMENT_FILTERS":
                ph["policy"] = {"allowed_operations": ["deduplicación", "normalización", "exclusiones deterministas por entidad (nombre/dominio)", "comprobación de identificadores disponibles", "eliminación de registros imposibles según datos ya observados"],
                    "forbidden_inputs": ["estimated_num_employees", "industry enriquecida", "growth", "datos detallados de la compañía", "cualquier dato que requiera enrichment"],
                    "criteria_evaluated_here": crit_by["PRE_ENRICHMENT_FILTERS"], "criteria_deferred_to_verified_gates": ids(lambda c: c["stage"] == "enrichment"),
                    "missing_domain": {"automatic_fail": False, "treatment": "data_quality_issue", "keep_candidate": True, "reduces_data_confidence": True,
                                       "enrichable_by_apollo_org_enrich": False, "reason": "el único identificador de enrichment verificado es domain; no se inventa otro mecanismo", "resolution": "solo por fuentes verificadas posteriores (ninguna planificada hoy)"},
                    "enrichment_selection": {"ordering": ["enrichable (domain presente) primero", "discovery_rank asc"], "limited_by": "enrichment_capacity"}}
            if pid == "ENRICHMENT": ph["policy"] = self._enrichment_policy(cfg, fun, alloc, batch, enrich_credit)
            if pid == "VERIFIED_GATES": ph["policy"] = self._gates_policy(cfg, active)
            if pid == "EVIDENCE": ph["policy"] = {"selection": "top_n", "n": cap["EVIDENCE"], "from": "orden preliminar de VERIFIED_GATES", "requires_domain_for": ["web.*", "digital.*"], "missing_domain": "UNKNOWN (no_domain); nunca FAIL",
                    "job_postings": "evidencia de contratación (id,title,url,city,state,country,posted_at,last_seen_at); NO equivale a crecimiento; no es un gate universal", "tranche_size": 1, "reserve_may_close_top_n": True}
            if pid == "DECISION_MAKER_ENRICHMENT": ph["policy"] = {"finalists_only": True, "n": cap["DECISION_MAKER_ENRICHMENT"], "personal_fields_allowed": sorted(set(spec["governance"]["data_policy"]["personal_data_fields"]) & set(P["decision_makers"]["allowed_personal_fields"])),
                    "forbidden_in": ["DISCOVERY", "ENRICHMENT", "EVIDENCE"], "tool_status": "deferred: no ejecutada ni auditada; coste no declarado", "contact_reveal": "no"}
            out.append(ph)
        return out

    def _credits_of(self, cfg, pid, selected, fun):
        """Créditos de Apollo SOLO desde coste OBSERVADO. Cualquier herramienta de pago sin coste observado -> no se estima (null)."""
        nonin = [s for s in selected if cfg.tools[s["selected_tool"]]["kind"] != "infra"]
        if not nonin:
            return {"status": "no_credit_cost", "credits": 0, "unit": "apollo_credits", "unverified_providers": []}
        prov = lambda s: cfg.tools[s["selected_tool"]]["provider"]
        apollo = [s for s in nonin if prov(s) == "apollo"]
        unverified = sorted({prov(s) for s in nonin if cfg.observed_credits(s["selected_tool"]) is None and prov(s) != "apollo"})
        if any(cfg.observed_credits(s["selected_tool"]) is None for s in apollo) or not apollo:
            return {"status": "unverified_not_estimated", "credits": None, "unit": "apollo_credits", "unverified_providers": unverified, "cost_status": "unverified_or_undeclared"}
        c = fun["credits"].get(pid)
        if pid == "DISCOVERY": c = 0
        if c is None:
            return {"status": "unverified_not_estimated", "credits": None, "unit": "apollo_credits", "unverified_providers": unverified, "cost_status": "unverified"}
        return {"status": "estimated_from_observed_cost" if c else "observed_zero", "credits": c, "unit": "apollo_credits", "unverified_providers": unverified, "cost_status": "observed"}

    def _discovery(self, cfg, active, fun, alloc, selected, llm, spec):
        d = cfg.params["discovery"]; rpc = d["results_per_call"]["value"]
        flt = [{"criterion_id": c["id"], "field": c["field"], "param": cfg.routing[c["field"]]["discovery_filter"]["param"], "role": "recall_hint_not_evidence", "value": c["value"], "mapping_status": "partially_verified"}
               for c in active if c["discovery_filter"]]
        tool = selected[0] if selected else None
        queries = [{"id": "Q1", "kind": "base", "source": tool["selected_tool"] if tool else None, "filters": flt, "keywords_from_spec": [c["value"] for c in active if c["field"] == "firmographic.industry"]}]
        if llm["discovery_queries"]:
            queries = [{"id": q["id"], "kind": "proposed_query", "label": q["label"], "path_hint": q["path_hint"], "priority": q["priority"], "source": tool["selected_tool"] if tool else None,
                        "filters": [{"param": k, "role": "recall_hint_not_evidence", "value": v, "mapping_status": q["filter_status"][k]} for k, v in q["filters"].items()], "page": 1}
                       for q in sorted(llm["discovery_queries"], key=lambda x: x["priority"])]
        queries += [{"id": f"Q{i + 2}", "kind": "validated_variant", "source": tool["selected_tool"] if tool else None, "text": v} for i, v in enumerate(llm["query_variants"])]
        pages = math.ceil(fun["final"]["pool"] / rpc)
        return {"selected_tool": tool["selected_tool"] if tool else None, "fallback_in_use": bool(tool and tool.get("fallback_for")), "primary_tool": "apollo.company_search", "primary_tool_status": cfg.tool_status("apollo.company_search"),
                "queries": queries, "pagination": {"type": "page", "per_page": rpc, "pages_planned": pages, "observed_max_per_page": 100, "total_count": "not_available_do_not_assume", "cursor": "not_assumed",
                                                   "verified_depth_results_per_query": d["verified_depth_per_query"]["value"], "depth_beyond_verified": "unverified"},
                "candidate_pool": {"target_results": spec["restrictions"]["quantity"]["target"], "ideal": fun["pool_ideal_uncapped"], "planned": fun["final"]["pool"], "depth_cap": fun["pool"]["depth_cap"], "capped_by_observed_depth": fun["pool_capped"],
                                   "basis": fun["pool"]["basis"], "rule": "pool = f(objetivo, ruido esperado, top-N de evidencia, histórico observado); sin histórico: configuración conservadora CALIBRATION_REQUIRED; acotado por profundidad verificada y asignación de llamadas"},
                "stop_discovery_when": ["ENOUGH_CANDIDATES", "MAX_POOL", "MAX_PAGES", "MAX_TIME", "SOURCE_EXHAUSTED", "DIMINISHING_RETURNS (si está configurado)", "EXPANSION_EXHAUSTED"],
                "result_character": {"noise": "high_observed", "recall": "unknown", "use": "generación de candidatos: NUNCA evidencia de un criterio crítico", "employee_count": "no devuelto", "industry": "no devuelto"},
                "discovery_is_bottleneck": False}

    def _enrichment_policy(self, cfg, fun, alloc, batch, enrich_credit):
        e = cfg.params["enrichment"]; E = fun["final"]["enrichment"]; bs = e["batch_size"]
        return {"max_candidates": E, "tranche_size": min(e["tranche_size"], max(1, math.ceil(E * e["tranche_max_pct_of_phase"]))), "identifier": {"verified": ["domain"], "unverified": ["id"], "rule": "solo se enriquece por domain/domains"},
                "ordering": ["enrichable (domain presente) primero", "discovery_rank asc"], "dedupe_before_enrich": "OBLIGATORIO (ENTITY_RESOLUTION): un dominio una sola vez por tanda",
                "never_reenrich": "no se enriquece dos veces la misma entidad si hay evidencia reutilizable (Apollo no cachea y re-enriquecer se cobra)",
                "batch_size": {"configured_batch_size": batch, "status": bs["status"], "max_verified": bs["max_verified"], "max_tested": bs["max_tested"], "configurable": True,
                              "audit_required_before_bulk_optimization": True, "if_unsupported": "una llamada por empresa (calls_if_batch_not_supported)"},
                "billing": "1 crédito por fila devuelta no nula (duplicados incluidos); dominio inexistente = 0 (observado)", "credit_cost_per_company": {"value": enrich_credit, "cost_status": "observed"},
                "cost_cap": {"credits": alloc["by_phase"]["ENRICHMENT"]["credits"], "tool_calls": alloc["by_phase"]["ENRICHMENT"]["data_calls"]}, "economic_role": "primary_bottleneck",
                "stop_when": ["qualified >= objetivo alcanzable", "umbral duro de la fase", "capacidad de créditos agotada"], "never": "enriquecer el pool descubierto completo"}

    def _gates_policy(self, cfg, active):
        evals = []
        for c in active:
            if c["stage"] != "enrichment": continue
            evals.append({"criterion_id": c["id"], "class": c["class"], "evaluator": c["evaluator"], "source_fields": c["source_fields"], "capability_ref": c["capability_ref"],
                          "evidence_status_ceiling": CEILING.get(c["evaluator"], "INFERRED"), "limitation": c["limitation"]})
        return {"criterion_evaluation": evals,
                "gate_states": {"PASS": EV.gate_outcome("PASS", "KNOWN"), "FAIL_KNOWN": EV.gate_outcome("FAIL", "KNOWN"), "FAIL_ESTIMATED": EV.gate_outcome("FAIL", "ESTIMATED"),
                                "FAIL_INFERRED": EV.gate_outcome("FAIL", "INFERRED"), "UNKNOWN": EV.gate_outcome("UNKNOWN", "UNKNOWN"), "CONFLICTING": EV.gate_outcome("CONFLICTING", "CONFLICTING")},
                "rules": {"UNKNOWN": "pending_verification: NO es PASS ni FAIL", "CONFLICTING": "conflicting_review: nunca se convierte en PASS/FAIL automáticamente",
                          "FAIL con evidencia ESTIMATED/INFERRED": "se conserva para revisión (no se descarta como hecho)", "FAIL con evidencia KNOWN": "descarte permitido con la evidencia guardada"},
                "location": {"states": ["location_verified", "location_conflicting", "location_unknown"], "rule": "se comparan campos estructurados y raw_address; raw != objetivo NO es FAIL automático"},
                "growth": {"requested_window_vs_available_window": "se registran ambas y la transformación; equivalencias días→meses son INFERRED; sin ventana equivalente: UNKNOWN"},
                "evidence_stage_gates": [c["id"] for c in active if c["class"] == "HARD" and c["stage"] == "evidence"]}

    def _warnings(self, cfg, active, ignored, T, bud, fun, achievable, present, sel, caps, hist):
        w = lambda code, sev, msg: {"code": code, "severity": sev, "message": msg}; out = []
        out.append(w("COSTS_OBSERVED_PROVISIONAL", "INFO", "Costes OBSERVADOS en Apollo Free (lookup 0, enrich 1/empresa, job postings 1/petición); provisionales, no permanentes. Créditos de otros proveedores: no verificados."))
        for s in (s for p in sel.values() for s in p["selected"]):
            if s.get("fallback_for"):
                out.append(w("TOOL_FALLBACK_IN_USE", "WARNING", f"{s['fallback_for']} está {cfg.tool_status(s['fallback_for'])} (plan Free): se usa {s['selected_tool']}, que NO es equivalente (sin tamaño ni sector en la respuesta)."))
            td = cfg.tools[s["selected_tool"]]
            if td["status"] in ("unaudited", "deferred") and td["kind"] != "infra":
                out.append(w("TOOL_UNAUDITED", "WARNING", f"{s['selected_tool']} está {td['status']}: no verificada; no debe ejecutarse hasta auditarla."))
        if "apollo.org_lookup" in {s["selected_tool"] for p in sel.values() for s in p["selected"]}:
            out.append(w("DISCOVERY_NOISY_UNKNOWN_RECALL", "WARNING", "El lookup es ruidoso (medios, portales, fundaciones…) y su recall es desconocido; sirve para generar candidatos, no para afirmar criterios."))
        if fun["pool"]["basis"]["calibration_required"]:
            out.append(w("CALIBRATION_REQUIRED", "WARNING", "Sin histórico: " + ", ".join(fun["pool"]["basis"]["calibration_required"]) + " usan valores conservadores NO medidos."))
        if fun["pool_capped"]:
            out.append(w("POOL_CAPPED_BY_OBSERVED_DEPTH", "WARNING", f"El pool ideal ({fun['pool_ideal_uncapped']}) supera la profundidad verificada por consulta ({fun['pool']['depth_cap']}): no se asume profundidad mayor; solo variantes validadas añaden consultas."))
        s_ = (hist or {}).get("pre_filter_survival") or cfg.params["pool_policy"]["pre_filter_survival_without_history"]["value"]
        if "ENRICHMENT" in present and math.floor(fun["final"]["pool"] * s_) < fun["final"]["enrichment"]:
            out.append(w("POOL_MAY_BE_INSUFFICIENT_FOR_ENRICHMENT", "WARNING", f"Con la supervivencia pre-filtro asumida ({s_}) el pool ({fun['final']['pool']}) podría no cubrir la capacidad de enrichment ({fun['final']['enrichment']}) (calibration_required)."))
        if "ENRICHMENT" in present:
            bs = cfg.params["enrichment"]["batch_size"]
            out.append(w("BATCH_SIZE_UNVERIFIED", "WARNING", f"configured_batch_size={bs['configured_batch_size']} es {bs['status']}, max_verified={bs['max_verified']} (máximo probado {bs['max_tested']}); 10 NO se asume como máximo duro. Auditar el bulk antes de optimizar sobre él."))
        for c in active:
            if c["limitation"]: out.append(w("CRITERION_LIMITATION", "WARNING", f"{c['id']} ({c['field']}): {c['limitation']}"))
        if "DECISION_MAKER_ENRICHMENT" in present:
            out.append(w("PEOPLE_COST_UNDECLARED", "WARNING", "people search: coste no declarado y herramienta no ejecutada ni auditada; solo finalistas."))
        if not any(c["class"] == "OPTIONAL_SIGNAL" for c in active):
            out.append(w("NO_ACTIVE_OPPORTUNITY_SIGNALS", "WARNING", "Sin señales de oportunidad activas: Opportunity no será evaluable; solo encaje (Fit)."))
        if ignored:
            out.append(w("CRITERIA_IGNORED", "INFO", "Criterios sin trabajo en el plan: " + ", ".join(f"{i['id']}({i['reason']})" for i in ignored)))
        top = bud["deep_research_top_n"]
        if "EVIDENCE" in present and top is not None and top < T:
            out.append(w("DEEP_RESEARCH_BELOW_TARGET", "WARNING", f"deep_research_top_n={top} < objetivo={T}: dos niveles de resultado (evidencia completa solo en el top-N)."))
        if achievable < T:
            out.append(w("TARGET_UNREACHABLE_WITH_BUDGET", "WARNING", f"Con este presupuesto solo pueden evaluarse hasta {achievable} de {T} candidatos: el informe no puede afirmar {T} cualificados."))
        return out

    def _stop(self, cfg, fun, alloc):
        s, t, d = cfg.params["stop"], cfg.params["thresholds"], cfg.params["stop"]["diminishing_returns"]
        rpc = cfg.params["discovery"]["results_per_call"]["value"]
        dr_on = d["min_new_entity_pct"] is not None and d["consecutive_rounds"] is not None
        return [{"id": "TARGET_REACHED", "kind": "stop", "outcome": "completed|degraded", "parameters": {"rule": "qualified >= coverage.achievable_qualified_target y investigación profunda planificada hecha"}},
                {"id": "ENOUGH_CANDIDATES", "kind": "close_discovery", "outcome": None, "parameters": {"rule": "enrichable >= capacidad de enrichment planificada: DISCOVERY completed y el plan CONTINÚA"}},
                {"id": "MAX_POOL", "kind": "close_discovery", "outcome": "partial", "parameters": {"max_candidates": fun["final"]["pool"]}},
                {"id": "MAX_PAGES", "kind": "close_discovery", "outcome": "partial", "parameters": {"max_pages_total": math.ceil(fun["final"]["pool"] / rpc)}},
                {"id": "MAX_TIME", "kind": "close_discovery", "outcome": "partial", "parameters": {"discovery_minutes": alloc["by_phase"]["DISCOVERY"]["minutes"], "enabled": alloc["by_phase"]["DISCOVERY"]["minutes"] is not None}},
                {"id": "SOURCE_EXHAUSTED", "kind": "close_discovery", "outcome": "partial", "parameters": {"rule": "no hay total_count fiable: se detecta por página vacía o incompleta (menos de per_page resultados)"}},
                {"id": "DIMINISHING_RETURNS", "kind": "close_discovery", "outcome": "partial", "parameters": {"enabled": dr_on, "min_new_entity_pct": d["min_new_entity_pct"], "consecutive_rounds": d["consecutive_rounds"],
                                                                                                                  "note": "umbral NO definido: condición declarada pero deshabilitada hasta configurarla" if not dr_on else "configurado"}},
                {"id": "NO_VIABLE_CANDIDATES", "kind": "stop", "outcome": "partial", "parameters": {"rule": "0 candidatos enriquecibles al cerrar el discovery"}},
                {"id": "PLAN_EXHAUSTED", "kind": "stop", "outcome": "partial", "parameters": {"rule": "todas las fases planificadas terminaron con qualified < objetivo alcanzable"}},
                {"id": "BUDGET_THRESHOLD", "kind": "stop", "outcome": "budget_exhausted", "parameters": {"soft": t["soft"], "hard": t["hard"], "soft_action": "degradar", "hard_action": "parar"}},
                {"id": "MAX_ITERATIONS", "kind": "stop", "outcome": "partial", "parameters": {"max_iterations_total": s["max_iterations_total"], "structural_bound": True}},
                {"id": "IMPOSSIBLE_REQUIREMENT", "kind": "stop", "outcome": "partial", "parameters": {"rule": "criterio activo sin herramienta disponible: el plan nace `blocked`"}},
                {"id": "TECHNICAL_BLOCK", "kind": "stop", "outcome": "partial", "parameters": {"rule": "herramienta/plan inaccesible durante la ejecución"}},
                {"id": "USER_CANCELLED", "kind": "stop", "outcome": "partial", "parameters": {"rule": "el usuario cancela"}},
                {"id": "CONFIDENCE_INSUFFICIENT", "kind": "stop", "outcome": "partial", "parameters": {"enabled": s["min_data_confidence_for_completion"] is not None, "min_data_confidence": s["min_data_confidence_for_completion"],
                                                                                                       "note": "Data Confidence aún no está definida (D-010 Proposed): condición declarada pero no evaluable."}}]

    def _expansion(self, cfg, active, fun, llm, classes):
        d = cfg.params["discovery"]; pre = [c for c in active if c["class"] == "PREFERRED" and c["discovery_filter"]]
        deeper = d["verified_depth_per_query"]["value"] // d["results_per_call"]["value"] > 1
        lev = []
        for r in cfg.ruleset["expansion_rules"]:
            av, why = True, "disponible"
            if r["lever"] == "next_page": av, why = deeper, ("páginas adicionales dentro de la profundidad verificada" if deeper else "la profundidad verificada (100) ya cabe en 1 página de 100: más páginas excederían lo verificado")
            if r["lever"] == "query_variants": av, why = bool(llm["query_variants"]), ("variantes validadas: %d" % len(llm["query_variants"]) if llm["query_variants"] else "sin variantes validadas del LLM")
            if r["lever"] == "additional_source": av = cfg.tool_status("firecrawl.search") == "available"; why = "disponible" if av else f"firecrawl.search está {cfg.tool_status('firecrawl.search')}"
            if r["lever"] == "relax_preferred_discovery_filters": av = bool(pre); why = "hay filtros PREFERRED activos en discovery" if av else "no hay filtros PREFERRED en discovery: nada que relajar"
            lev.append({"lever": r["lever"], "rule_id": r["rule_id"], "class": r["class"], "available": av, "reason": why, "tool": r.get("tool")})
        return {"classes": {"HARD": {"ids": classes["HARD"], "relaxable": False, "rule": "NUNCA se relaja automáticamente"},
                            "PREFERRED": {"ids": classes["PREFERRED"], "relaxable": True, "rule": "solo filtros de discovery, tras agotar el resto de palancas"},
                            "OPTIONAL_SIGNAL": {"ids": classes["OPTIONAL_SIGNAL"], "relaxable": "no aplica", "rule": "señales de oportunidad: nunca filtran ni descartan"}},
                "levers": lev, "max_rounds": cfg.params["stop"]["max_expansion_rounds"],
                "hard_relaxation": {"automatic": False, "requires": cfg.ruleset["hard_relaxation"]["requires"], "mechanism": "proposed_relaxation (applied: false): relajar un HARD exige autorización explícita Y una nueva versión del Search Spec; el planner no muta el spec"}}

    def _budget_block(self, bud, alloc, fun, phases, cfg, sel, enrich_credit):
        P = cfg.params
        by = {p["phase_id"]: {"data_calls_max": p["estimated_calls"]["data_calls_max"], "infra_calls": p["estimated_calls"]["infra_calls"], "credits": p["estimated_credits"]["credits"], "minutes": None} for p in phases if p["status"] == "planned"}
        tot = sum(v["data_calls_max"] for v in by.values())
        apollo_credits = sum(v["credits"] or 0 for v in by.values())
        by_prov = {}
        for p in phases:
            if p["status"] != "planned": continue
            for s in p["tool_selections"]:
                if cfg.tools[s["selected_tool"]]["kind"] == "infra": continue
                n = fun["calls"].get(p["phase_id"], 0) if p["phase_id"] != "EVIDENCE" else fun["final"]["evidence"] * P["evidence"]["calls_per_company"][s["selected_tool"]]
                prov = cfg.tools[s["selected_tool"]]["provider"]; by_prov[prov] = by_prov.get(prov, 0) + n
        mx = {t: v for t, v in bud["tool_credits_max"].items()}
        return {"budget_input": bud, "source": bud["source"],
                "reserve": dict(alloc["reserve"], release_conditions=P["reserve_release"], rule="la reserva no se gasta antes de su condición"),
                "phase_allocations": {"by_phase": alloc["by_phase"], "policy": "llamadas/minutos: pesos de política provisionales renormalizados entre fases presentes, sin reasignación; créditos: por necesidad entre fases con coste observado > 0",
                                      "redistributed_from_skipped_phases": alloc["redistributed_from_skipped_phases"]},
                "estimated_usage": {"by_phase": by, "totals": {"tool_calls_upper_bound": tot, "infra_calls": sum(v["infra_calls"] for v in by.values()), "apollo_credits_estimated_from_observed_cost": apollo_credits},
                                    "headroom_tool_calls": alloc["allocatable_data_calls"] - tot, "basis": "cota superior; costes observados provisionales"},
                "tool_call_budget": {"max": bud["data_calls_max"], "allocatable": alloc["allocatable_data_calls"], "reserve": alloc["reserve"]["data_calls"], "estimated_upper_bound": tot,
                                     "separate_from_credits": True, "note": "poder hacer muchas llamadas NO implica poder enriquecer muchas empresas"},
                "credit_budget": {"max": mx, "apollo": {"max": bud["max_apollo_credits"], "allocatable": alloc["allocatable_credits"].get("apollo"), "reserve": alloc["reserve"]["credits"].get("apollo"),
                                                         "by_phase": {p: a["credits"].get("apollo") for p, a in alloc["by_phase"].items()}, "unallocated": alloc["unallocated_credits"].get("apollo"), "estimated": apollo_credits, "cost_status": "observed",
                                                         "allocation": "por necesidad (demanda observada); los pesos solo reparten si hay escasez; lo que sobra queda sin asignar"},
                                  "enforcement": "estimación previa con coste observado + gasto real posterior", "people": "diferido (coste no declarado)",
                                  "cost_model": {"status": "observed_provisional", "record_schema": ["tool", "operation", "calls", "estimated_credits", "actual_credits", "cost_status"],
                                                 "observed_costs": {"apollo.org_lookup": 0, "apollo.org_enrich": "≈1 por empresa devuelta", "apollo.job_postings": "≈1 por petición", "apollo.company_search": "0 observado por indisponibilidad (declarado 1)"},
                                                 "calls_by_provider_upper_bound": [{"tool": k, "calls": v, "estimated_credits": apollo_credits if k == "apollo" else None, "actual_credits": None, "cost_status": "observed" if k == "apollo" else "unverified"} for k, v in sorted(by_prov.items())]}},
                "candidate_capacity": {"pool_planned": fun["final"]["pool"], "enrichment_planned": fun["final"]["enrichment"], "note": "descubrir ≠ enriquecer: la capacidad de enrichment la fija el presupuesto de créditos"},
                "deep_research_capacity": {"top_n_requested": bud["deep_research_top_n"], "planned": fun["final"]["evidence"], "finalists_planned": fun["final"]["finalists"]},
                "infra_calls": {"max": bud["infra_calls_max"], "estimated": sum(v["infra_calls"] for v in by.values()), "enforced": bud["infra_calls_max"] is not None},
                "time": {"max_minutes": bud["max_time_minutes"], "reserve_minutes": alloc["reserve"]["minutes"]}, "thresholds": P["thresholds"],
                "exhaustion_policy": {"soft": "al alcanzar soft: degradar: (1) reducir top-N de evidencia, (2) omitir señales OPTIONAL, (3) restringir enrichment a los mejores",
                                      "hard": "al alcanzar hard: parar fases normales; solo la reserva puede cerrar el top-N", "outcome_on_hard": "budget_exhausted",
                                      "never": ["relajar gates HARD", "inventar datos", "presentar descubiertas/enriquecidas como cualificadas", "superar límites absolutos"]}}

    def _post_plan(self, cfg, ctx):
        out = []
        for r in cfg.ruleset["post_plan_rules"]:
            ok, _ = R.eval_cond(r["when"], ctx)
            if ok: out.append({"rule_id": r["rule_id"], "tool": r["tool"], "reason": r["reason"], "requires": r["requires"]})
        return out

    def _approvals(self, status, cfg, ctx):
        a = [{"action": "post_plan:" + p["rule_id"], "requires": p["requires"]} for p in self._post_plan(cfg, ctx)]
        if status == "degraded": a.append({"action": "confirm_degraded_plan", "requires": "user_confirmation"})
        return a
