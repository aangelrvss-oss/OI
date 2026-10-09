"""Validación del ResearchPlan v2: schema (L1) + invariantes deterministas. Un plan que viole una invariante NO se entrega."""
from oi.engine.spec import mini_jsonschema
from .config import PHASE_ORDER

DISCOVERY_ROLES = ("company_discovery", "discovery")


def _ancestors(plan, phase):
    pre = {p["phase_id"]: p["prerequisites"] for p in plan["phases"]}; seen, todo = set(), list(pre.get(phase, []))
    while todo:
        x = todo.pop()
        if x not in seen: seen.add(x); todo += pre.get(x, [])
    return seen


def validate_plan(plan, cfg):
    errs = [("SCHEMA_VIOLATION", f"{e['path']}: {e['message']}") for e in mini_jsonschema.validate(plan, cfg.schema)]
    if errs: return errs
    act = {c["id"] for c in plan["criteria_digest"]["active"]}; ign = {c["id"] for c in plan["criteria_digest"]["ignored"]}
    planned = {p["phase_id"]: p for p in plan["phases"] if p["status"] == "planned"}
    order = [p["phase_id"] for p in plan["phases"]]
    if order != [p for p in PHASE_ORDER if p in order]: errs.append(("INV_PHASE_ORDER", "orden de fases inválido"))
    for p in plan["phases"]:
        if p["status"] == "skipped":
            if p["tool_selections"] or p["estimated_calls"]["data_calls_max"]: errs.append(("INV_SKIPPED_HAS_WORK", p["phase_id"]))
            continue
        if set(p["active_criteria"]) & ign: errs.append(("INV01_IGNORED_CRITERION_GENERATES_WORK", p["phase_id"]))
        if not set(p["active_criteria"]) <= act: errs.append(("INV01_UNKNOWN_CRITERION_IN_PHASE", p["phase_id"]))
        for s in p["tool_selections"]:
            if set(s["triggering_criteria"]) & ign: errs.append(("INV01_IGNORED_CRITERION_TRIGGERS_TOOL", s["rule_id"]))
            if not set(s["triggering_criteria"]) <= act: errs.append(("INV01_TRIGGER_NOT_ACTIVE", s["rule_id"]))
            for o in s["operations"]:
                if cfg.is_forbidden(o): errs.append(("INV04_FORBIDDEN_TOOL", o))
            if s["selected_tool"].startswith("openrush.") and not any(c["field"].startswith("digital.") for c in plan["criteria_digest"]["active"]):
                errs.append(("INV02_OPENRUSH_WITHOUT_ACTIVE_DIGITAL_CRITERION", s["rule_id"]))
            if s["selected_tool"] == "apollo.people_search" and p["phase_id"] != "DECISION_MAKER_ENRICHMENT":
                errs.append(("INV03_PEOPLE_TOOL_OUTSIDE_DM_PHASE", p["phase_id"]))
            if p["phase_id"] == "DISCOVERY" and cfg.tools[s["selected_tool"]]["role"] not in DISCOVERY_ROLES:
                errs.append(("INV03_NON_DATABASE_TOOL_IN_DISCOVERY", s["selected_tool"]))
            if plan["tool_policy"]["status_by_tool"].get(s["selected_tool"]) == "unavailable_on_current_plan":
                errs.append(("INV13_UNAVAILABLE_TOOL_SELECTED", s["selected_tool"]))
            if s.get("fallback_for") is None and s["rule_id"] == "TS-A1F":
                errs.append(("INV16_FALLBACK_WITHOUT_PRIMARY", s["rule_id"]))
    dm = planned.get("DECISION_MAKER_ENRICHMENT")
    if dm and ("PRIORITIZATION" not in dm["prerequisites"] or not planned.get("PRIORITIZATION")): errs.append(("INV03_DM_WITHOUT_PRIORITIZATION", ""))
    pos = {p: i for i, p in enumerate(order)}
    for d in plan["dependencies"]:
        if pos[d["from"]] >= pos[d["to"]]: errs.append(("INV06_DEPENDENCY_CYCLE_OR_ORDER", f"{d['from']}->{d['to']}"))
    # INV12: pre-enrichment solo con datos sin créditos
    pre = planned.get("PRE_ENRICHMENT_FILTERS")
    if pre is None: errs.append(("INV12_NO_PRE_ENRICHMENT_PHASE", ""))
    else:
        bycrit = {c["id"]: c for c in plan["criteria_digest"]["active"]}
        if any(not bycrit[i]["pre_enrichment_gate"] or bycrit[i]["stage"] != "discovery_prefilter" for i in pre["active_criteria"] if i in bycrit):
            errs.append(("INV12_PRE_ENRICHMENT_USES_ENRICHMENT_DATA", ""))
        pol = pre.get("policy", {})
        if "estimated_num_employees" not in pol.get("forbidden_inputs", []) or pol.get("missing_domain", {}).get("automatic_fail") is not False:
            errs.append(("INV12_PRE_ENRICHMENT_POLICY", ""))
    # INV14: entity resolution y pre-filtros SIEMPRE antes de gastar créditos en enrichment; gates verificados después
    en = planned.get("ENRICHMENT")
    if en:
        anc = _ancestors(plan, "ENRICHMENT")
        if not {"ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS"} <= anc: errs.append(("INV14_ENRICHMENT_BEFORE_ENTITY_RESOLUTION", ""))
        vg = planned.get("VERIFIED_GATES")
        if not vg or "ENRICHMENT" not in _ancestors(plan, "VERIFIED_GATES"): errs.append(("INV14_VERIFIED_GATES_BEFORE_ENRICHMENT", ""))
        bs = en.get("policy", {}).get("batch_size", {})
        if (bs.get("status") != "provisional" or bs.get("max_verified") != "unknown") and cfg.caps["CAP-BULK-BATCH-10"]["status"] != "VERIFIED": errs.append(("INV17_BATCH_SIZE_PRESENTED_AS_VERIFIED", ""))
    cr = planned.get("CANDIDATE_RELEVANCE")
    if cr:
        if "CANDIDATE_RELEVANCE" not in _ancestors(plan, "ENRICHMENT") and "ENRICHMENT" in planned: errs.append(("INV18_RELEVANCE_NOT_BEFORE_ENRICHMENT", ""))
        if cr.get("policy", {}).get("default_decision") != "KEEP": errs.append(("INV18_RELEVANCE_DEFAULT_NOT_KEEP", ""))
    b = plan["budget"]
    if plan["status"] != "blocked":
        alloc = sum(a["data_calls"] for a in b["phase_allocations"]["by_phase"].values())
        if alloc + b["reserve"]["data_calls"] != b["tool_call_budget"]["max"]: errs.append(("INV05_ALLOCATION_SUM", f"{alloc}+{b['reserve']['data_calls']}!={b['tool_call_budget']['max']}"))
        for ph, a in b["phase_allocations"]["by_phase"].items():
            e = b["estimated_usage"]["by_phase"].get(ph, {})
            if e.get("data_calls_max", 0) > a["data_calls"]: errs.append(("INV05_ESTIMATE_EXCEEDS_ALLOCATION", f"{ph}: llamadas {e['data_calls_max']}>{a['data_calls']}"))
            for prov, ac in a["credits"].items():
                if prov == "apollo" and ac is not None and (e.get("credits") or 0) > ac: errs.append(("INV05_CREDIT_ESTIMATE_EXCEEDS_ALLOCATION", f"{ph}: {e['credits']}>{ac}"))
        for prov, lim in b["credit_budget"]["max"].items():
            rs = b["reserve"]["credits"].get(prov); parts = [a["credits"].get(prov) for a in b["phase_allocations"]["by_phase"].values()]
            un = (b["credit_budget"].get(prov) or {}).get("unallocated") or 0
            if lim is not None and rs is not None and any(x is not None for x in parts) and sum(x or 0 for x in parts) + rs + un != lim:
                errs.append(("INV05_CREDIT_ALLOCATION_SUM", f"{prov}: {sum(x or 0 for x in parts)}+{rs}+{un}!={lim}"))
    if plan["expansion_policy"]["hard_relaxation"]["automatic"] is not False or any(l["lever"].startswith("relax_hard") for l in plan["expansion_policy"]["levers"]):
        errs.append(("INV07_HARD_RELAXATION", ""))
    if any(l["lever"] == "relax_preferred_discovery_filters" and l["available"] and not plan["criteria_digest"]["classes"]["PREFERRED"] for l in plan["expansion_policy"]["levers"]):
        errs.append(("INV07_RELAX_WITHOUT_PREFERRED", ""))
    if plan["authorization"]["spend_authorized"] is not False: errs.append(("INV08_SPEND_AUTHORIZED", ""))
    for p in plan["phases"]:      # INV11: créditos solo desde coste OBSERVADO
        ec = p["estimated_credits"]
        if ec.get("credits") and (ec["status"] != "estimated_from_observed_cost" or ec.get("cost_status") != "observed"): errs.append(("INV11_CREDITS_ESTIMATED_WITHOUT_OBSERVED_COST", p["phase_id"]))
        for s in p["tool_selections"]:
            if s["selected_tool"] == "apollo.people_search" and ec.get("credits") is not None: errs.append(("INV11_UNDECLARED_COST_ESTIMATED", p["phase_id"]))
    sc = {c["id"]: c["parameters"] for c in plan["stop_conditions"]}
    if not isinstance(sc.get("MAX_ITERATIONS", {}).get("max_iterations_total"), int): errs.append(("INV10_UNBOUNDED_LOOP", ""))
    dr = sc.get("DIMINISHING_RETURNS", {})
    if dr.get("enabled") and (dr.get("min_new_entity_pct") is None or dr.get("consecutive_rounds") is None): errs.append(("INV10_THRESHOLD_ENABLED_WITHOUT_VALUE", ""))
    if plan["status"] != "blocked":
        covered = {i for p in plan["phases"] if p["status"] == "planned" and p["phase_id"] in ("ENRICHMENT", "EVIDENCE") for s in p["tool_selections"] for i in s["triggering_criteria"]}
        for c in plan["criteria_digest"]["active"]:
            unavail = {s["criterion_id"] for s in plan.get("signal_sources_unavailable", [])}
            if c["stage"] in ("enrichment", "evidence") and c["id"] not in covered and c["id"] not in unavail: errs.append(("INV09_ACTIVE_CRITERION_NOT_COVERED", c["id"]))
            if c["id"] in unavail and c["class"] == "HARD": errs.append(("INV19_HARD_CRITERION_MARKED_UNAVAILABLE", c["id"]))
    return errs
