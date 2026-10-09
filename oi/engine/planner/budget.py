"""Modelo de presupuesto v2 (Fase 3A). Cuatro conceptos SEPARADOS:
  tool_call_budget   -> cuántas llamadas se pueden hacer (el lookup es gratis pero gasta llamadas y tiempo)
  credit_budget      -> cuántas empresas se pueden enriquecer (el enrichment es el cuello de botella económico)
  candidate_capacity -> pool descubierto vs. capacidad de enrichment (descubrir 100 NO significa enriquecer 100)
  deep_research_capacity -> top-N con evidencia profunda.
Los créditos solo se estiman a partir de costes OBSERVADOS (catálogo); sin coste observado -> null. Nada se inventa."""
import math

DIMS_INT = ("data_calls_max", "infra_calls_max", "max_time_minutes", "deep_research_top_n")
SOURCES = ("user_parameter", "planner_input", "dry_run_example")
INF = float("inf")


def _issue(code, sev, msg): return {"code": code, "severity": sev, "message": msg}


def normalize_budget(spec, budget_input, params, needs_deep, needs_credits):
    raw, issues = (budget_input if budget_input is not None else spec.get("budget")), []
    if not raw:
        return None, [_issue("BUDGET_REQUIRED", "ERROR", "No hay Research Budget: el planner no inventa límites de gasto.")]
    raw = dict(raw)
    src = raw.get("source") or ("planner_input" if budget_input is not None else "user_parameter")
    b = {"source": src if src in SOURCES else "planner_input"}
    if raw.get("data_calls_max") is None and raw.get("max_tool_calls") is not None:   # alias del Research Budget de primera clase
        raw["data_calls_max"] = raw["max_tool_calls"]
    elif raw.get("max_tool_calls") is not None and raw["max_tool_calls"] != raw.get("data_calls_max"):
        issues.append(_issue("BUDGET_INVALID", "ERROR", "max_tool_calls y data_calls_max difieren"))
    credits = dict(raw.get("tool_credits_max") or {})
    if raw.get("max_apollo_credits") is not None:
        if credits.get("apollo") not in (None, raw["max_apollo_credits"]):
            issues.append(_issue("BUDGET_INVALID", "ERROR", "max_apollo_credits y tool_credits_max.apollo difieren"))
        credits["apollo"] = raw["max_apollo_credits"]
    for k in DIMS_INT:
        v = raw.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            issues.append(_issue("BUDGET_INVALID", "ERROR", f"{k} debe ser un entero >= 0"))
        b[k] = v
    for t, v in credits.items():
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            issues.append(_issue("BUDGET_INVALID", "ERROR", f"tool_credits_max.{t} debe ser un entero >= 0"))
    b["tool_credits_max"] = credits
    b["max_apollo_credits"] = credits.get("apollo")
    rp = raw.get("reserve_pct")
    b["reserve_pct"] = params["reserve_pct_default"] if rp is None else rp
    b["reserve_pct_source"] = "planner_default" if rp is None else "budget_input"
    if not (0 <= b["reserve_pct"] <= 50):
        issues.append(_issue("BUDGET_INVALID", "ERROR", "reserve_pct fuera de [0, 50]"))
    if not b["data_calls_max"]:
        issues.append(_issue("BUDGET_DATA_CALLS_REQUIRED", "ERROR", "max_tool_calls / data_calls_max es obligatorio y > 0"))
    if needs_credits and b["max_apollo_credits"] is None:
        issues.append(_issue("BUDGET_APOLLO_CREDITS_REQUIRED", "ERROR", "El plan consume créditos de Apollo (enrichment/evidencia): max_apollo_credits es obligatorio. Llamadas y créditos son presupuestos distintos."))
    if needs_deep and b["deep_research_top_n"] is None:
        issues.append(_issue("BUDGET_DEEP_TOP_N_REQUIRED", "ERROR", "El plan incluye investigación profunda/decision makers: deep_research_top_n es obligatorio"))
    if b["infra_calls_max"] is None:
        issues.append(_issue("INFRA_UNBOUNDED", "WARNING", "infra_calls_max sin definir: las llamadas de infraestructura se estiman pero no se limitan."))
    if b["max_time_minutes"] is None:
        issues.append(_issue("TIME_UNBOUNDED", "WARNING", "max_time_minutes sin definir: no hay límite de tiempo (el cierre por MAX_TIME no se puede evaluar)."))
    return b, issues


def apportion(total, weights):
    """Reparto en enteros por mayor resto; suma exacta = total. Determinista (desempate por orden de clave)."""
    if not weights: return {}
    s = sum(weights.values()); raw = {k: total * w / s for k, w in weights.items()}
    out = {k: math.floor(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), list(weights).index(k)))[:total - sum(out.values())]:
        out[k] += 1
    return out


def waterfill(pool, demands, weights):
    """Reparto por NECESIDAD: ninguna fase recibe más de lo que puede usar; si hay escasez, los pesos reparten lo que falta. Lo que sobra queda SIN asignar."""
    alloc = {k: 0 for k in demands}; active = {k for k, d in demands.items() if d > 0}
    for _ in range(len(demands) + 2):
        remaining = pool - sum(alloc.values())
        if not active or remaining <= 0: break
        share = apportion(remaining, {k: weights[k] for k in sorted(active)}); moved = False
        for k in sorted(active):
            g = min(share[k], demands[k] - alloc[k])
            if g > 0: alloc[k] += g; moved = True
        active = {k for k in active if alloc[k] < demands[k]}
        if not moved: break
    return alloc, pool - sum(alloc.values())


def credit_demand(T, present, params, bud, enrich_credit, evid_credit, history, n_variants):
    """Créditos que cada fase podría usar con el plan ideal (solo costes OBSERVADOS)."""
    top = bud["deep_research_top_n"]
    d_ideal = min(top, T) if "EVIDENCE" in present else 0
    f_ideal = min(top, T) if "DECISION_MAKER_ENRICHMENT" in present else 0
    e_ideal = pool_policy(T, max(d_ideal, f_ideal), params, history, n_variants)["enrichment_ideal"] if "ENRICHMENT" in present else 0
    out = {}
    if "ENRICHMENT" in present and enrich_credit: out["ENRICHMENT"] = e_ideal * enrich_credit
    if "EVIDENCE" in present and evid_credit: out["EVIDENCE"] = d_ideal * evid_credit
    return out


def allocate(budget, tool_phases, credit_users, weights, credit_demand=None):
    """credit_users: {fase: {proveedor}} SOLO para herramientas con coste observado > 0 (el lookup gratuito no consume créditos)."""
    w = {p: weights[p] for p in tool_phases}
    rp = budget["reserve_pct"] / 100
    reserve_calls = math.floor(budget["data_calls_max"] * rp)
    calls = apportion(budget["data_calls_max"] - reserve_calls, w)
    minutes = reserve_min = None
    if budget["max_time_minutes"] is not None:
        reserve_min = math.floor(budget["max_time_minutes"] * rp)
        minutes = apportion(budget["max_time_minutes"] - reserve_min, w)
    credits, reserve_credits, unalloc = {}, {}, {}
    for tool, lim in budget["tool_credits_max"].items():
        users = {p: w[p] for p in tool_phases if tool in credit_users.get(p, set())}
        if lim is None or not users:
            credits[tool] = None; reserve_credits[tool] = None if lim is None else lim; unalloc[tool] = 0; continue
        r = math.floor(lim * rp); reserve_credits[tool] = r
        dem = (credit_demand or {}).get(tool)
        if dem is not None:
            credits[tool], unalloc[tool] = waterfill(lim - r, {p: dem.get(p, 0) for p in users}, users)
        else:
            credits[tool], unalloc[tool] = apportion(lim - r, users), 0
    shares = {p: round(w[p] / sum(w.values()) * (1 - rp), 6) for p in tool_phases}
    return {"by_phase": {p: {"share": shares[p], "data_calls": calls[p], "minutes": None if minutes is None else minutes[p],
                              "credits": {t: (None if c is None else c.get(p, 0)) for t, c in credits.items()}} for p in tool_phases},
            "reserve": {"pct": budget["reserve_pct"], "data_calls": reserve_calls, "minutes": reserve_min, "credits": reserve_credits},
            "allocatable_data_calls": budget["data_calls_max"] - reserve_calls,
            "allocatable_credits": {t: (None if lim is None else lim - reserve_credits[t]) for t, lim in budget["tool_credits_max"].items()},
            "unallocated_credits": unalloc,
            "redistributed_from_skipped_phases": sorted(set(weights) - set(tool_phases))}


def pool_policy(T, deep_n, params, history, n_variants):
    """Tamaño del pool SIN multiplicadores universales: depende del objetivo, del ruido esperado, de los gates, del presupuesto
    (aplicado después) y del histórico observado. Sin histórico NO se inventa una tasa: configuración conservadora + CALIBRATION_REQUIRED."""
    pp, d = params["pool_policy"], params["discovery"]
    h = history or {}
    g = h.get("gate_pass_rate"); s = h.get("pre_filter_survival")
    calib = []
    if g:
        e_ideal = math.ceil(T / g); e_basis = "historico:gate_pass_rate"
    else:
        e_ideal = math.ceil(T * pp["enrichment_multiplier_without_history"]["value"]); e_basis = "conservador_sin_historico"; calib.append("gate_pass_rate")
    e_ideal = max(e_ideal, deep_n or 0)                       # el top-N de evidencia sale de lo enriquecido
    if s:
        pool_ideal = math.ceil(e_ideal / s); p_basis = "historico:pre_filter_survival"
    else:
        pool_ideal = math.ceil(e_ideal / pp["pre_filter_survival_without_history"]["value"]); p_basis = "conservador_sin_historico"; calib.append("pre_filter_survival")
    depth = d["verified_depth_per_query"]["value"]
    cap = depth * (1 + n_variants); mx = d["max_candidates_cap"]["value"]
    if mx: cap = min(cap, mx)
    return {"enrichment_ideal": e_ideal, "pool_ideal": pool_ideal, "depth_cap": cap, "pool_capped_by_depth": pool_ideal > cap,
            "basis": {"enrichment": e_basis, "pre_filter": p_basis, "history": h or None, "calibration_required": sorted(calib),
                      "factors": {"target": T, "deep_research_top_n": deep_n, "verified_depth_per_query": depth, "validated_query_variants": n_variants,
                                  "expected_noise": "incluido en pre_filter_survival (no medido)", "active_gates": "no cuantificado: sin datos"}}}


def size_funnel(T, present, params, alloc, budget, cpc_evidence, enrich_credit, evid_credit, history, n_variants, batch, explicit_queries=None):
    d = params["discovery"]; rpc = d["results_per_call"]["value"]
    top = budget["deep_research_top_n"]
    has_e, has_ev, has_dm = "ENRICHMENT" in present, "EVIDENCE" in present, "DECISION_MAKER_ENRICHMENT" in present
    D_ideal = min(top, T) if has_ev else 0
    F_ideal = min(top, T) if has_dm else 0
    pp = pool_policy(T, max(D_ideal, F_ideal), params, history, n_variants)
    E_ideal = pp["enrichment_ideal"] if has_e else 0
    pool_unc = pp["pool_ideal"] if has_e else math.ceil(T / (history or {}).get("pre_filter_survival", params["pool_policy"]["pre_filter_survival_without_history"]["value"]))
    if explicit_queries:      # las consultas explícitas DEFINEN el pool: cada una aporta como máximo una página verificada
        pp = dict(pp, depth_cap=explicit_queries * rpc, pool_ideal=explicit_queries * rpc); pool_unc = explicit_queries * rpc
    pool_ideal = min(pool_unc, pp["depth_cap"])          # el tope por profundidad verificada es ESTRUCTURAL, no una degradación por presupuesto
    by = alloc["by_phase"]; c = {p: by[p]["data_calls"] for p in by}
    cr = lambda p: (by[p]["credits"].get("apollo") if p in by else None)
    P = min(pool_ideal, c["DISCOVERY"] * rpc)
    cons = {}
    E_c = (cr("ENRICHMENT") // enrich_credit) if (has_e and enrich_credit and cr("ENRICHMENT") is not None) else INF
    E_k = c.get("ENRICHMENT", 0) * batch if has_e else INF
    E = min(E_ideal, E_c, E_k, P) if has_e else 0
    cons["enrichment"] = "credits" if E_c < min(E_ideal, E_k, P) else ("calls" if E_k < min(E_ideal, P) else ("pool" if P < E_ideal else None))
    D_c = (cr("EVIDENCE") // evid_credit) if (has_ev and evid_credit and cr("EVIDENCE") is not None) else INF
    D_k = (c.get("EVIDENCE", 0) // cpc_evidence) if (has_ev and cpc_evidence) else INF
    D = min(D_ideal, D_c, D_k, E if has_e else P) if has_ev else 0
    cons["evidence"] = "credits" if D_c < min(D_ideal, D_k) else ("calls" if D_k < D_ideal else None)
    F_k = c.get("DECISION_MAKER_ENRICHMENT", 0) // params["decision_makers"]["calls_per_company"] if has_dm else 0
    F = min(F_ideal, F_k, D if has_ev else (E if has_e else P)) if has_dm else 0
    cons["finalists"] = "calls" if (has_dm and F_k < F_ideal) else None
    ideal = {"pool": pool_ideal, "enrichment": E_ideal, "evidence": D_ideal, "finalists": F_ideal}
    final = {"pool": P, "enrichment": E, "evidence": D, "finalists": F}
    names = {"pool": "DISCOVERY", "enrichment": "ENRICHMENT", "evidence": "EVIDENCE", "finalists": "DECISION_MAKER_ENRICHMENT"}
    degr = []
    for k in final:
        if final[k] < ideal[k]:
            degr.append({"phase": names[k], "parameter": "max_candidates", "from": ideal[k], "to": final[k], "reason": "allocation_capacity", "binding_constraint": cons.get(k) or "calls"})
    blocked = [f"{names[k]}: capacidad 0 con el presupuesto asignado" for k in final if names[k] in present and ideal[k] > 0 and final[k] <= 0]
    calls = {"DISCOVERY": math.ceil(P / rpc), "ENRICHMENT": math.ceil(E / batch) if E else 0, "EVIDENCE": D * cpc_evidence, "DECISION_MAKER_ENRICHMENT": F * params["decision_makers"]["calls_per_company"]}
    credits = {"DISCOVERY": 0, "ENRICHMENT": E * enrich_credit if (E and enrich_credit is not None) else None,
               "EVIDENCE": D * evid_credit if (D and evid_credit) else (0 if has_ev and not evid_credit else None), "DECISION_MAKER_ENRICHMENT": None}
    return {"pool_ideal_uncapped": pool_unc, "pool_capped": pool_unc > pp["depth_cap"], "ideal": ideal, "final": final, "calls": calls, "credits": credits, "degradations": degr, "blocked": blocked, "pool": pp,
            "calls_if_unbatched": {"ENRICHMENT": E}}
