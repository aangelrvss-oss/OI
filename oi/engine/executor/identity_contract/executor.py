"""Ejecutor con contrato EXECUTION_IDENTITY_V1 (opt-in). Subclase del Executor histórico: la ruta T1 no cambia.

El contrato vive en la APROBACIÓN de la ejecución (config["execution_approval"]):
  execution_id        → identidad explícita de ESTA ejecución (no se genera aquí; nada aleatorio)
  execution_contract  → {"contract": "EXECUTION_IDENTITY_V1", "tranche": "T2", "candidate_source": "FIXED_SHORTLIST" | "DISCOVERY",
                         "discovery": "DISABLED" | "ENABLED", "expected_paid_calls": int (opcional),
                         "fixed_shortlist": {"entities": [{order, apollo_id, name, domain}], "hashed_object": {..., "entities": [{order, apollo_id, domain}]},
                                             "shortlist_hash_hc1": "<sha256 HC1 de hashed_object>"}}
Todo lo que no cuadra se rechaza en el precheck (estado BLOCKED, 0 llamadas de cualquier tipo, 0 pending)."""
import math

from oi.engine.evaluators import entity as EN
from .. import state as S
from ..runner import Executor
from . import CONTRACT, DISCOVERY, FIXED_SHORTLIST
from .keys import hc1, tranche_of, valid_execution_id
from .relay import ExecutionIdentityViolation, IdentityBoundRelayTransport

FIXED_QUERY_ID = "FIXED_SHORTLIST"


def fixed_shortlist_problems(fs):
    """Valida la shortlist fija aprobada. Devuelve (problemas, entidades normalizadas)."""
    if not isinstance(fs, dict): return ["FIXED_SHORTLIST_MISSING"], []
    ents, ho, h = fs.get("entities"), fs.get("hashed_object"), fs.get("shortlist_hash_hc1")
    if not isinstance(ents, list) or not ents: return ["FIXED_SHORTLIST_ENTITIES_MISSING"], []
    if not isinstance(ho, dict) or not isinstance(ho.get("entities"), list): return ["FIXED_SHORTLIST_HASHED_OBJECT_MISSING"], []
    p, out = [], []
    for i, e in enumerate(ents, 1):
        if not (isinstance(e, dict) and e.get("order") == i and all(isinstance(e.get(k), str) and e[k].strip() for k in ("apollo_id", "name", "domain"))):
            p.append(f"FIXED_SHORTLIST_ENTITY_INVALID_OR_OUT_OF_ORDER:{i}"); continue
        if EN.normalize_domain(e["domain"]) != e["domain"]: p.append(f"FIXED_SHORTLIST_DOMAIN_NOT_NORMALIZED:{e['domain']}")
        out.append({"order": i, "apollo_id": e["apollo_id"], "name": e["name"], "domain": e["domain"], "entity_key": "apollo:" + e["apollo_id"]})
    if len({e["apollo_id"] for e in out}) != len(out) or len({e["domain"] for e in out}) != len(out): p.append("FIXED_SHORTLIST_DUPLICATE_ENTITY")
    if not (isinstance(h, str) and hc1(ho) == h): p.append("FIXED_SHORTLIST_HASH_MISMATCH")
    proj = lambda xs: [{"order": x.get("order"), "apollo_id": x.get("apollo_id"), "domain": x.get("domain")} for x in xs]
    if proj(ents) != proj(ho["entities"]): p.append("FIXED_SHORTLIST_ENTITIES_DIFFER_FROM_HASHED_OBJECT")
    if "call_scope_limit" in ho and ho["call_scope_limit"] != len(ents): p.append("FIXED_SHORTLIST_COUNT_MISMATCH")
    return p, (out if not p else [])


class IdentityBoundExecutor(Executor):
    def __init__(self, plan, spec, adapter, transport, config, cfg=None, clock=None):
        super().__init__(plan, spec, adapter, transport, config, cfg=cfg, clock=clock)
        a = config.get("execution_approval") or {}
        self.ic = a.get("execution_contract") if isinstance(a.get("execution_contract"), dict) else {}
        self.execution_id = a.get("execution_id")
        self.fixed = self.ic.get("candidate_source") == FIXED_SHORTLIST
        self.fixed_entities = []; self.contract_blockers = []; self.identity_violation = None

    # ───────────────────────── contrato (antes de cualquier llamada)
    def _contract_problems(self):
        b, c, ic, eid = [], self.c, self.ic, self.execution_id
        if ic.get("contract") != CONTRACT: b.append("EXECUTION_CONTRACT_MISSING_OR_UNKNOWN")
        if not valid_execution_id(eid): b.append("EXECUTION_ID_FROM_APPROVAL_INVALID")
        elif tranche_of(eid) != ic.get("tranche"): b.append("EXECUTION_ID_TRANCHE_MISMATCH")
        if c["mode"] != "relay": b.append("IDENTITY_CONTRACT_REQUIRES_RELAY_MODE")
        bp = c.get("balance_policy") or {}
        if bp.get("mode") != "fresh_required" or bp.get("execution_id") != eid: b.append("BALANCE_POLICY_EXECUTION_ID_MISMATCH")
        if not isinstance(self.t, IdentityBoundRelayTransport): b.append("TRANSPORT_NOT_IDENTITY_BOUND")
        else:
            if self.t.execution_id != eid or self.t.tranche != ic.get("tranche"): b.append("TRANSPORT_EXECUTION_ID_MISMATCH")
            b += self.t.audit(verify_records=True)
        cs, disc = ic.get("candidate_source"), ic.get("discovery")
        if cs not in (FIXED_SHORTLIST, DISCOVERY): b.append("CANDIDATE_SOURCE_UNKNOWN")
        if cs == DISCOVERY and disc != "ENABLED": b.append("DISCOVERY_SOURCE_REQUIRES_DISCOVERY_ENABLED")
        if cs == FIXED_SHORTLIST:
            if disc != "DISABLED": b.append("FIXED_SHORTLIST_REQUIRES_DISCOVERY_DISABLED")
            if "DISCOVERY" in c["phases_enabled"]: b.append("FIXED_SHORTLIST_DISCOVERY_PHASE_ENABLED")
            if c["job_postings"]["enabled"] or "EVIDENCE" in c["phases_enabled"]: b.append("FIXED_SHORTLIST_ADDITIONAL_SEARCH_NOT_ALLOWED")
            fp, ents = fixed_shortlist_problems(ic.get("fixed_shortlist")); b += fp; self.fixed_entities = ents
            if ents:
                n, doms, ids = len(ents), {e["domain"] for e in ents}, {e["apollo_id"] for e in ents}
                for k in c["known_enriched"]:
                    if k.get("apollo_id") in ids or EN.normalize_domain(k.get("domain")) in doms: b.append(f"FIXED_CANDIDATE_DECLARED_KNOWN_ENRICHED:{k.get('domain') or k.get('apollo_id')}")
                for d in c["prior_enrichment_cache"]:
                    if EN.normalize_domain(d) in doms: b.append(f"FIXED_CANDIDATE_IN_PRIOR_ENRICHMENT_CACHE:{d}")
                al = c.get("enrichment_allowlist")
                if al is not None and not {e["entity_key"] for e in ents} <= set(al): b.append("FIXED_CANDIDATE_NOT_IN_ENRICHMENT_ALLOWLIST")
                fz = c.get("frozen_shortlist") or {}
                if fz.get("enabled") and fz.get("window", 0) < n: b.append("FIXED_SHORTLIST_EXCEEDS_FROZEN_WINDOW")
                if c["max_enrichment_entities"] < n: b.append("FIXED_SHORTLIST_EXCEEDS_MAX_ENRICHMENT")
                unit = self._unit_cost("apollo.org_enrich")
                if unit is not None and n * unit > self.credit_cap: b.append(f"FIXED_SHORTLIST_COST_EXCEEDS_CAP:{n * unit}>{self.credit_cap}")
                bs = c["enrichment_batch_size"] or next(x for x in self.plan["phases"] if x["phase_id"] == "ENRICHMENT")["policy"]["batch_size"]["configured_batch_size"]
                ep = ic.get("expected_paid_calls")
                if ep is not None and math.ceil(n / bs) != ep: b.append(f"FIXED_SHORTLIST_BULK_SPLIT:{math.ceil(n / bs)}!={ep}")
        return b

    def _precheck(self):
        self.contract_blockers = self._contract_problems()
        return self.contract_blockers + super()._precheck()

    def run(self):
        try:
            return super().run()
        except ExecutionIdentityViolation as v:         # última barrera: nunca se convierte en 'error del proveedor'
            self.identity_violation = {"code": v.code, "detail": v.detail}
            if self.sm.state == S.RUNNING: self.sm.go(S.FAILED, f"EXECUTION_IDENTITY_VIOLATION:{v.code}")
            return self._result(f"EXECUTION_IDENTITY_VIOLATION:{v.code}")

    # ───────────────────────── candidatos fijos (sin discovery)
    def _discovery(self):
        if not self.fixed: return super()._discovery()
        ents = self.fixed_entities; tag = self.ic["fixed_shortlist"]["shortlist_hash_hc1"][:16]
        self.query_meta[FIXED_QUERY_ID] = {"order": 0, "path_hint": None, "label": "FIXED_SHORTLIST (aprobada; sin discovery)", "returned": len(ents), "calls": [], "geo_tier": 1}
        cands = []
        for i, e in enumerate(ents):
            cid = f"fixed-{e['order']:03d}"
            cands.append({"id": e["apollo_id"], "name": e["name"], "domain": e["domain"], "website_url": None, "cid": cid, "q_index": 0, "q_pos": i, "query_id": FIXED_QUERY_ID})
            self.results.add(cid, name=e["name"], domain=e["domain"], source="FIXED_SHORTLIST:" + tag, discovery_query=FIXED_QUERY_ID, discovery_page=None, discovery_rank=i + 1, evidence_status="NOT_COLLECTED")
        self.notes.append(f"FIXED_SHORTLIST: discovery NO ejecutado; {len(ents)} candidatos de la shortlist aprobada (HC1 {tag}…)")
        return cands

    def _resolve(self, cands):
        rest = super()._resolve(cands)
        if self.fixed:
            pos = {e["entity_key"]: e["order"] - 1 for e in self.fixed_entities}
            for e in rest:
                if e["entity_key"] in pos: e["rank"] = pos[e["entity_key"]]          # el orden es el APROBADO, no el de una estrategia de selección
        return rest

    def _enrich(self, batches, kept):
        if self.fixed:                                       # barrera final ANTES de la llamada de pago: lo enviado debe ser EXACTAMENTE la shortlist aprobada
            exp = [e["domain"] for e in self.fixed_entities]; flat = [d for b in batches for d in b]; probs = []
            if flat != exp: probs.append(f"dominios/orden {flat} != {exp}")
            by_dom = {e["domain"]: e for e in kept}
            for e in self.fixed_entities:
                k = by_dom.get(e["domain"])
                if k is None or k.get("apollo_id") != e["apollo_id"] or k.get("name") != e["name"]: probs.append(f"{e['domain']}: identidad distinta o ausente")
            ep = self.ic.get("expected_paid_calls")
            if ep is not None and len(batches) != ep: probs.append(f"llamadas de pago {len(batches)} != {ep}")
            if probs:
                self.errors.append("FIXED_SHORTLIST_MUTATED"); self.notes.append("FIXED_SHORTLIST_MUTATED: " + "; ".join(probs))
                for e in kept: self.results.update_entity(e["entity_key"], enrichment_status="NOT_ATTEMPTED_AFTER:FIXED_SHORTLIST_MUTATED")
                return
        return super()._enrich(batches, kept)

    # ───────────────────────── resultado
    def _result(self, stop_reason, blockers=None):
        r = super()._result(stop_reason, blockers)
        fs = self.ic.get("fixed_shortlist") or {}
        r["execution_identity"] = {"contract": self.ic.get("contract"), "execution_id": self.execution_id, "tranche": self.ic.get("tranche"),
                                   "candidate_source": self.ic.get("candidate_source"), "discovery": self.ic.get("discovery"),
                                   "fixed_shortlist_hash_hc1": fs.get("shortlist_hash_hc1") if self.fixed else None,
                                   "relay": self.t.identity() if isinstance(self.t, IdentityBoundRelayTransport) else None,
                                   "discovery_calls": sum(1 for c in self.calls.rows if c["phase"] == "DISCOVERY"),
                                   "contract_blockers": self.contract_blockers, "identity_violation": self.identity_violation}
        return r
