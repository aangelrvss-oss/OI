"""Configuración del EXPERIMENTO (no del Planner). Es la autoridad humana sobre lo que se permite ejecutar.
Una configuración insegura bloquea el run antes de cualquier llamada."""
import copy

ENABLED_PHASES = ["DISCOVERY", "NORMALIZATION", "ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS", "CANDIDATE_RELEVANCE", "ENRICHMENT", "VERIFIED_GATES", "SCORING", "PRIORITIZATION", "RESULTS"]
NOT_SUPPORTED_IN_3B = ["EVIDENCE", "DECISION_MAKER_ENRICHMENT"]       # people search, Firecrawl, OpenRush, job postings: fuera de 3B
DEFAULTS = {
    "mode": "dry_execution",                 # dry_execution (fixtures sintéticas) | relay (respuestas reales grabadas)
    "credit_hard_cap": 25,                   # tope DURO de seguridad del experimento
    "max_enrichment_entities": 20,           # objetivo orientativo; el número final lo fijan candidatos, dedupe, presupuesto y plan
    "discovery": {"max_pages": 1, "per_page": 100},
    "allow_unverified_capabilities": [],     # p. ej. ["pagination"] para auditar la paginación más adelante
    "enrichment_batch_size": None,           # None = el configurado (provisional) del plan
    "force_reenrich": {},                    # dominio -> razón explícita (obligatoria)
    "on_unknown_actual_cost": "halt_paid_calls",   # halt_paid_calls | charge_estimate
    "measure_credits": "all_calls",          # all_calls | paid_calls | never
    "phases_enabled": ENABLED_PHASES,
    "confirm_degraded": False,
    "execution_approval": None,              # obligatorio en relay: {"approved_by", "approved_at", "max_credits"}
    "prior_enrichment_cache": {},            # dominio -> registro ya enriquecido (idempotencia entre runs)
    "job_postings": {"enabled": False, "max_requests": 0, "per_page": 10},     # EVIDENCE (job postings) es opt-in explícito
    "checkpoint": {"required": False, "approval": None},                       # approval = {approved_by, approved_at, shortlist_hash}
    "relevance_proposals": None,             # None => pausa y pide propuestas; lista (posiblemente vacía) => se validan
    "final_selection": {"target": 20, "min_evidence_items": 1},                # el objetivo NO obliga a rellenar
    "path_taxonomy": {},                     # {ruta: {naics_prefixes: [...], industry_labels: [...]}} (la aporta el caso, no el código)
    "title_term_families": {},               # léxico de contratación técnica (la aporta el spec/caso)
    "stop_when_enough": False,
    "evidence_model": "legacy_paths_v1",        # legacy_paths_v1 (3C) | fit_opportunity_v2 (3E: FIT y OPPORTUNITY separados; el CONTEXTO nunca cuenta como evidencia)
    "known_enriched": [],                       # [{apollo_id, domain, status, note}] -> ALREADY_ENRICHED_3C: no se vuelven a pedir ni a cobrar
    "fit_lexicon": {},                          # {familia: [frases]} léxico FIJO de evidencia FIT
    "industry_mapping": {},                     # {labels: [...], naics_prefixes: [...]}: mapeo industria->proveedor en la CONFIGURACIÓN del caso (no en el spec)
    "relevance_rejected_as": "KEEP",            # KEEP | REVIEW: destino de un DISCARD propuesto que no supera la validación
    "selection_strategy": "interleave_position_v1",   # interleave_position_v1 (3C) | overlap_diversity_v2 (candidato a calibración)               # parar el enrichment cuando ya hay `target` candidatos elegibles
    "require_measured_balance_before_paid_call": None,   # None = automático: True en relay, False en dry_execution
}


OPTIONAL_KEYS = ("domain_precheck", "frozen_shortlist", "enrichment_allowlist", "shortlist_binding", "balance_policy", "cumulative_budget")       # claves opcionales: SOLO entran en la configuración si se aportan (no alteran el hash de las configuraciones existentes)


def make_config(**overrides):
    c = copy.deepcopy(DEFAULTS)
    for k, v in overrides.items():
        if k in OPTIONAL_KEYS:
            if v is not None: c[k] = copy.deepcopy(v)
            continue
        if k not in c: raise KeyError(f"clave de configuración desconocida: {k}")
        c[k] = {**c[k], **v} if isinstance(c[k], dict) and isinstance(v, dict) and k in ("discovery", "job_postings", "checkpoint", "final_selection") else v
    return c


def validate_config(c, adapter_caps):
    e = []
    if c["mode"] not in ("dry_execution", "relay"): e.append("CONFIG_MODE_INVALID")
    if not isinstance(c["credit_hard_cap"], int) or isinstance(c["credit_hard_cap"], bool) or c["credit_hard_cap"] < 0: e.append("CONFIG_CREDIT_CAP_INVALID")
    if not isinstance(c["max_enrichment_entities"], int) or c["max_enrichment_entities"] < 0: e.append("CONFIG_MAX_ENRICHMENT_INVALID")
    mp, pp = c["discovery"]["max_pages"], c["discovery"]["per_page"]
    if not (isinstance(mp, int) and mp >= 1 and isinstance(pp, int) and 1 <= pp <= adapter_caps["pagination"]["observed_per_page_max"]): e.append("CONFIG_DISCOVERY_INVALID")
    if mp > 1 and adapter_caps["pagination"]["supported_status"] != "VERIFIED" and "pagination" not in c["allow_unverified_capabilities"]:
        e.append("PAGINATION_UNVERIFIED_NOT_ALLOWED: la paginación está UNVERIFIED (no prohibida); para auditarla añadir 'pagination' a allow_unverified_capabilities")
    if any(not isinstance(r, str) or not r.strip() for r in c["force_reenrich"].values()): e.append("FORCE_REENRICH_WITHOUT_REASON")
    if c["on_unknown_actual_cost"] not in ("halt_paid_calls", "charge_estimate"): e.append("CONFIG_UNKNOWN_COST_POLICY_INVALID")
    if c["measure_credits"] not in ("all_calls", "paid_calls", "never"): e.append("CONFIG_MEASURE_INVALID")
    if c.get("evidence_model") not in ("legacy_paths_v1", "fit_opportunity_v2"): e.append("EVIDENCE_MODEL_UNKNOWN")
    if c.get("relevance_rejected_as") not in ("KEEP", "REVIEW"): e.append("RELEVANCE_REJECTED_AS_INVALID")
    from oi.engine.evaluators.selection import STRATEGIES
    if c.get("selection_strategy") not in STRATEGIES: e.append("SELECTION_STRATEGY_UNKNOWN")
    jp = c["job_postings"]
    unsupported = set(NOT_SUPPORTED_IN_3B) - ({"EVIDENCE"} if jp["enabled"] else set())
    bad = [p for p in c["phases_enabled"] if p in unsupported or (p not in ENABLED_PHASES and not (p == "EVIDENCE" and jp["enabled"]))]
    if jp["enabled"] and ("EVIDENCE" not in c["phases_enabled"] or not isinstance(jp["max_requests"], int) or jp["max_requests"] < 0): e.append("JOB_POSTINGS_CONFIG_INVALID")
    if (not jp["enabled"]) and jp["max_requests"]: e.append("JOB_POSTINGS_MAX_WITHOUT_ENABLE")
    if bad: e.append(f"PHASE_NOT_SUPPORTED_BY_EXECUTOR: {bad}")
    if c["mode"] == "relay":
        a = c["execution_approval"] or {}
        if not (a.get("approved_by") and a.get("approved_at") and isinstance(a.get("max_credits"), int)): e.append("REAL_EXECUTION_REQUIRES_HUMAN_APPROVAL")
        if c["measure_credits"] == "never": e.append("REAL_EXECUTION_REQUIRES_CREDIT_MEASUREMENT")
    if c["enrichment_batch_size"] is not None and (not isinstance(c["enrichment_batch_size"], int) or c["enrichment_batch_size"] < 1): e.append("CONFIG_BATCH_INVALID")
    ap = c["execution_approval"] or {}
    md = ap.get("max_data_calls")
    if md is not None and (isinstance(md, bool) or not isinstance(md, int) or md < 1): e.append("EXECUTION_APPROVAL_MAX_DATA_CALLS_INVALID")
    if ap.get("max_infra_calls") is not None: e.append("EXECUTION_APPROVAL_MAX_INFRA_CALLS_NOT_ENFORCEABLE")      # el ejecutor no cuenta infra_calls: un límite declarado no se podría imponer
    rc = c["final_selection"].get("reportable_contract")
    if rc is not None and not (isinstance(rc, dict) and set(rc) <= {"identity", "gates_verified"} and rc.get("identity") in (None, "KNOWN_MATCH") and isinstance(rc.get("gates_verified", []), list)
                               and all(isinstance(x, str) and x for x in rc.get("gates_verified", [])) and (rc.get("identity") or rc.get("gates_verified"))): e.append("REPORTABLE_CONTRACT_INVALID")
    dp = c.get("domain_precheck")
    if dp is not None and not (isinstance(dp, dict) and set(dp) <= {"enabled", "suspicious_domains", "allow_domains"} and isinstance(dp.get("enabled", False), bool)
                               and all(isinstance(dp.get(k, []), list) and all(isinstance(x, str) and x for x in dp.get(k, [])) for k in ("suspicious_domains", "allow_domains"))): e.append("DOMAIN_PRECHECK_INVALID")
    fs = c.get("frozen_shortlist")
    if fs is not None and not (isinstance(fs, dict) and set(fs) <= {"enabled", "window"} and isinstance(fs.get("enabled"), bool) and isinstance(fs.get("window"), int) and not isinstance(fs.get("window"), bool) and fs["window"] >= 1): e.append("FROZEN_SHORTLIST_INVALID")
    al = c.get("enrichment_allowlist")
    if al is not None and not (isinstance(al, list) and al and all(isinstance(x, str) and x for x in al)): e.append("ENRICHMENT_ALLOWLIST_INVALID")
    sb = c.get("shortlist_binding")
    if sb is not None and not isinstance(sb, dict): e.append("SHORTLIST_BINDING_INVALID")
    bp = c.get("balance_policy")
    if bp is not None:
        if not (isinstance(bp, dict) and set(bp) <= {"mode", "execution_id", "provider"} and bp.get("mode") == "fresh_required" and isinstance(bp.get("execution_id"), str) and bp["execution_id"].strip() and isinstance(bp.get("provider", "apollo"), str)): e.append("BALANCE_POLICY_INVALID")
        elif c["measure_credits"] == "never": e.append("BALANCE_POLICY_REQUIRES_CREDIT_MEASUREMENT")
    cb = c.get("cumulative_budget")
    if cb is not None:
        ints = ("credits_spent_so_far", "global_max_credits", "tranche_cap", "credits_spent_in_tranche")
        if not (isinstance(cb, dict) and set(cb) <= set(ints) | {"tranche"} and all(isinstance(cb.get(k), int) and not isinstance(cb.get(k), bool) and cb[k] >= 0 for k in ints)): e.append("CUMULATIVE_BUDGET_INVALID")
        else:
            cap_ = c["credit_hard_cap"]; mc = (c["execution_approval"] or {}).get("max_credits")
            for v_ in [cap_] + ([mc] if isinstance(mc, int) else []):
                if cb["credits_spent_so_far"] + v_ > cb["global_max_credits"]: e.append("CUMULATIVE_BUDGET_EXCEEDED"); break
                if cb["credits_spent_in_tranche"] + v_ > cb["tranche_cap"]: e.append("TRANCHE_BUDGET_EXCEEDED"); break
    return e
