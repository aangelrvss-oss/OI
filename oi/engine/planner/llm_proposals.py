"""Frontera LLM -> planner. El LLM solo puede aportar: query_variants / search_wording, explanation, signal_suggestions.
Todo pasa por validación determinista; nada de lo que aporte puede cambiar fases, herramientas, presupuesto ni criterios."""
import re
from oi.engine.spec.text import norm, contains_phrase

ACCEPTED = {"query_variants", "search_wording", "explanation", "signal_suggestions", "discovery_queries"}


def validate(proposals, spec, params):
    out = {"query_variants": [], "discovery_queries": [], "rejected": [], "explanation": None, "signal_suggestions": [], "ignored_keys": []}
    issues = []
    if not proposals:
        return out, issues
    for k in proposals:
        if k not in ACCEPTED:
            out["ignored_keys"].append(k)
            issues.append({"code": "LLM_CLAIM_IGNORED", "severity": "WARNING", "message": f"El LLM propuso '{k}': solo el código decide fases, herramientas, presupuesto y criterios (ignorado)."})
    excl = [norm(e) for e in spec["restrictions"].get("excluded_entities", [])]
    seen, mx = set(), params["discovery"]["max_query_variants"]
    for v in list(proposals.get("query_variants", [])) + list(proposals.get("search_wording", [])):
        reason = None
        if not isinstance(v, str): reason = "no_es_texto"
        else:
            t = v.strip(); n = norm(t)
            if not (2 <= len(t) <= 80): reason = "longitud_fuera_de_2_80"
            elif re.search(r"(https?://|www\.|@)", t.lower()): reason = "contiene_url_o_email"
            elif n in seen: reason = "duplicada"
            elif any(contains_phrase(n, e) for e in excl if e): reason = "contiene_entidad_excluida"
            elif len(out["query_variants"]) >= mx: reason = "excede_max_query_variants"
        if reason: out["rejected"].append({"value": v if isinstance(v, str) else repr(v), "reason": reason})
        else: seen.add(n); out["query_variants"].append(t)
    allowed = params["discovery"]["allowed_query_filters"]; mxq = params["discovery"]["max_discovery_queries"]["value"]; seen_ids = set()
    for q in proposals.get("discovery_queries", []) or []:
        reason = None
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("filters"), dict) or not q["filters"]: reason = "consulta_malformada_o_sin_filtros"
        elif q["id"] in seen_ids: reason = "id_duplicado"
        elif set(q["filters"]) - set(allowed): reason = "filtro_no_permitido:" + ",".join(sorted(set(q["filters"]) - set(allowed)))
        elif any(not isinstance(v, list) or not v or not all(isinstance(x, str) and 1 <= len(x) <= 80 for x in v) for v in q["filters"].values()): reason = "valores_de_filtro_invalidos"
        elif any(not re.fullmatch(r"[0-9]{2,5}", c) for c in q["filters"].get("organization_naics_codes", [])): reason = "naics_invalido"
        elif any(contains_phrase(norm(" ".join(sum(q["filters"].values(), []))), e) for e in excl if e): reason = "contiene_entidad_excluida"
        elif len(out["discovery_queries"]) >= mxq: reason = "excede_max_discovery_queries"
        if reason: out["rejected"].append({"value": str(q.get("id") if isinstance(q, dict) else q)[:60], "reason": reason})
        else:
            seen_ids.add(q["id"]); out["discovery_queries"].append({"id": q["id"], "label": str(q.get("label", ""))[:120], "path_hint": str(q.get("path_hint", ""))[:40], "priority": q.get("priority", len(out["discovery_queries"]) + 1),
                                                               "filters": {k: list(v) for k, v in q["filters"].items()}, "filter_status": {k: allowed[k] for k in q["filters"]}})
    if isinstance(proposals.get("explanation"), str): out["explanation"] = proposals["explanation"][:2000]
    out["signal_suggestions"] = [s for s in proposals.get("signal_suggestions", []) if isinstance(s, str)][:10]
    return out, issues
