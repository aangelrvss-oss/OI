"""Validación determinista en tres capas.
L1  esquema      : forma, tipos, enums, obligatorios, condicionales por status (search_spec.v1.schema.json).
L2  registry     : cada criterio contra el Field Registry (campo existe y es operativo, entidad, dimensión,
                   operador, tipo/dominio del valor, parámetros, tipos de evidencia compatibles).
L3  semántica    : reglas entre campos: grounding contra la petición, términos relativos, defaults,
                   geografía, cantidad, presupuesto, contradicciones, coherencia gate/origen.
Severidades: ERROR | WARNING | NEEDS_CLARIFICATION | UNSUPPORTED."""
import re
import copy
from . import mini_jsonschema
from .text import norm, contains_phrase, numbers_in

ERROR, WARNING, CLARIFY, UNSUPPORTED = "ERROR", "WARNING", "NEEDS_CLARIFICATION", "UNSUPPORTED"
SUPPORTED_TYPES = ["customer_prospect"]
DEFAULT_QUANTITY = 20


def issue(code, severity, layer, path, message, **extra):
    d = {"code": code, "severity": severity, "layer": layer, "path": path, "message": message}
    d.update(extra)
    return d


# ───────────────────────── L1 ─────────────────────────
def l1_envelope(draft, reg):
    """Valida solo control+intent (usado antes de decidir si el tipo está soportado)."""
    probe = {"control": {"spec_id": "x", "spec_version": 1, "status": "draft", "created_at": "1970-01-01T00:00:00Z",
                         "raw_request": (draft.get("control") or {}).get("raw_request", "")},
             "intent": draft.get("intent"),
             "traceability": {"assumptions": [], "open_questions": []},
             "versions": {"schema_version": reg.schema_version, "field_registry_version": "x", "lexicon_version": "x", "engine_version": "x"}}
    return [issue("SCHEMA_VIOLATION", ERROR, "L1", e["path"], e["message"])
            for e in mini_jsonschema.validate(probe, reg.schema)]


def l1_full(spec, reg):
    return [issue("SCHEMA_VIOLATION", ERROR, "L1", e["path"], e["message"])
            for e in mini_jsonschema.validate(spec, reg.schema)]


# ───────────────────────── L2 ─────────────────────────
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def value_problem(fdef, op, value):
    """None = ok · 'MISSING' = falta (needs_definition) · str = error."""
    t = fdef["type"]
    if op in ("exists", "not_exists"):
        return None if value is None else "exists/not_exists exigen value=null"
    if value is None:
        return "MISSING"
    if op == "between":
        if not (isinstance(value, list) and len(value) == 2 and all(_num(v) for v in value)):
            return "between exige [min, max] numéricos"
        return "between con min > max" if value[0] > value[1] else None
    if op in ("in", "not_in"):
        ok = isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value)
        return None if ok else "in/not_in exigen lista no vacía de strings"
    if t == "string":
        return None if isinstance(value, str) and value.strip() else "se esperaba string no vacío"
    if t == "integer":
        return None if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "se esperaba entero >= 0"
    if t == "number":
        return None if _num(value) else "se esperaba número"
    if t == "boolean":
        return None if isinstance(value, bool) else "se esperaba booleano"
    return "tipo no evaluable"


def l2_criterion(c, i, reg, entity_type):
    """Devuelve (issues, info). info = {'value_missing': bool, 'params_missing': [..]}"""
    p = f"$.criteria[{i}]"
    out, info = [], {"value_missing": False, "params_missing": []}
    fdef = reg.fields.get(c["field"])
    if fdef is None:
        return [issue("UNKNOWN_FIELD", ERROR, "L2", p + ".field",
                      f"'{c['field']}' no existe en el Field Registry v{reg.version}. El LLM no puede crear campos operativos.")], info
    if fdef["status"] != "active":
        return [issue("FIELD_NOT_OPERATIONAL", ERROR, "L2", p + ".field",
                      f"'{c['field']}' está {fdef['status']} (no operativo en el MVP).")], info
    if entity_type not in fdef["allowed_entity_types"]:
        out.append(issue("ENTITY_TYPE_NOT_ALLOWED", ERROR, "L2", p + ".field", f"'{c['field']}' no aplica a {entity_type}."))
    if c["dimension"] not in fdef["allowed_dimensions"]:
        out.append(issue("DIMENSION_NOT_ALLOWED", ERROR, "L2", p + ".dimension",
                         f"'{c['field']}' solo admite {fdef['allowed_dimensions']}."))
    if c["operator"] not in fdef["allowed_operators"]:
        out.append(issue("OPERATOR_NOT_ALLOWED", ERROR, "L2", p + ".operator",
                         f"operador '{c['operator']}' no permitido para '{c['field']}' (permitidos: {fdef['allowed_operators']})."))
    else:
        vp = value_problem(fdef, c["operator"], c.get("value"))
        if vp == "MISSING":
            info["value_missing"] = True
        elif vp:
            out.append(issue("VALUE_INVALID", ERROR, "L2", p + ".value", vp))
    params = c.get("params") or {}
    for rp in fdef.get("requires_parameters", []):
        v = params.get(rp)
        if v is None:
            info["params_missing"].append(rp)
        elif (fdef.get("parameter_types") or {}).get(rp) == "code":      # parámetro CÓDIGO (genérico): identificador MAYÚSCULAS_Y_GUIONES_BAJOS que apunta a un catálogo versionado del caso
            if not (isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", v)): out.append(issue("PARAM_INVALID", ERROR, "L2", p + f".params.{rp}", f"{rp} debe ser un código MAYÚSCULAS_Y_GUIONES_BAJOS"))
        elif rp == "title_term_families":      # parámetro ESTRUCTURADO (genérico): {familia: [términos]} acotado
            ok = (isinstance(v, dict) and 1 <= len(v) <= 12 and all(isinstance(k, str) and 1 <= len(k) <= 40 and isinstance(t, list) and 1 <= len(t) <= 30
                  and all(isinstance(x, str) and 2 <= len(x.strip()) <= 60 for x in t) for k, t in v.items()))
            if not ok: out.append(issue("PARAM_INVALID", ERROR, "L2", p + f".params.{rp}", "title_term_families debe ser {familia: [términos]} (1-12 familias, 1-30 términos de 2-60 caracteres)"))
        elif not (isinstance(v, int) and not isinstance(v, bool) and v >= 1):
            out.append(issue("PARAM_INVALID", ERROR, "L2", p + f".params.{rp}", f"{rp} debe ser un entero >= 1"))
    for k in params:
        if k not in fdef.get("requires_parameters", []):
            out.append(issue("PARAM_UNKNOWN", ERROR, "L2", p + f".params.{k}", f"parámetro '{k}' no admitido por '{c['field']}'"))
    ev = c.get("evidence")
    if ev and ev.get("allowed_evidence_types"):
        bad = set(ev["allowed_evidence_types"]) - set(fdef["evidence_compatible"])
        if bad:
            out.append(issue("EVIDENCE_TYPE_NOT_ALLOWED", ERROR, "L2", p + ".evidence",
                             f"tipos de evidencia {sorted(bad)} incompatibles con '{c['field']}' (compatibles: {fdef['evidence_compatible']})."))
    return out, info


# ───────────────────────── L3 (piezas) ─────────────────────────
def detect_relative_terms(raw_request, reg):
    """Detección determinista sobre la petición ORIGINAL; no depende de lo que diga el LLM."""
    n = norm(raw_request)
    found = []
    for tid, t in reg.relative_terms.items():
        for pat in t["patterns"]:
            if contains_phrase(n, pat):
                found.append((tid, pat))
                break
    return found


def detect_vague_quantifier(raw_request, reg):
    n = norm(raw_request)
    return next((p for p in reg.vague_quantifiers["patterns"] if contains_phrase(n, p)), None)


def ground_criterion(c, i, corpus_n, reg, value_from_user):
    """Un criterio no puede inventarse: origin decide qué prueba se exige."""
    p = f"$.criteria[{i}]"
    out = []
    o = c["origin"]
    if c["strength"] == "gate" and o == "inferred":
        out.append(issue("INFERRED_GATE", ERROR, "L3", p, "Un criterio inferido por el LLM no puede ser gate obligatorio."))
    if o == "user":
        st = norm(c.get("source_text"))
        if not st or not contains_phrase(corpus_n, st):
            out.append(issue("UNGROUNDED_CRITERION", ERROR, "L3", p + ".source_text",
                             "Criterio 'user' sin source_text literal presente en la petición del usuario."))
        elif not value_from_user and c.get("value") is not None and isinstance(c["value"], (int, float, list)):
            vals = c["value"] if isinstance(c["value"], list) else [c["value"]]
            nums = numbers_in(c.get("source_text"))
            if any(_num(v) and int(v) not in nums for v in vals):
                out.append(issue("UNGROUNDED_VALUE", ERROR, "L3", p + ".value",
                                 f"El valor {c['value']} no aparece en el texto citado '{c.get('source_text')}'."))
    elif o == "assumed":
        st = norm(c.get("source_text"))
        match = None
        for tname, td in reg.term_defaults.items():
            if any(contains_phrase(corpus_n, norm(a)) for a in td["aliases"]) and any(contains_phrase(st, norm(a)) for a in td["aliases"]):
                match = (tname, td)
        if not match:
            out.append(issue("UNGROUNDED_ASSUMPTION", ERROR, "L3", p,
                             "Criterio 'assumed' sin término del catálogo de defaults (term_defaults) que lo respalde. No se aceptan requisitos inventados."))
        else:
            cd = match[1]["criterion"]
            if (c["field"], c["operator"], c.get("value")) != (cd["field"], cd["operator"], cd["value"]):
                out.append(issue("ASSUMPTION_MISMATCH", ERROR, "L3", p,
                                 f"El default del catálogo para '{match[0]}' es {cd}; el borrador propone otra cosa."))
    return out


def geo_lookup(name, level, geo):
    key = {"country": "countries", "region": "regions", "province": "provinces", "municipality": "municipalities"}[level]
    n = norm(name)
    for e in geo[key]:
        if n == norm(e["name"]) or n in [norm(a) for a in e["aliases"]] or n == norm(e.get("code", "")):
            return e
    return None


def l3_geography(geo_draft, corpus_n, reg):
    """Devuelve (geografía normalizada, issues, assumptions)."""
    out, assumptions, res = [], [], {"include": [], "exclude": [g for g in (geo_draft or {}).get("exclude", [])]}
    for j, g in enumerate((geo_draft or {}).get("include", [])):
        p = f"$.restrictions.geography.include[{j}]"
        g = dict(g)
        amb = reg.geo["ambiguous"].get(norm(g["name"]))
        entry = geo_lookup(g["name"], g["level"], reg.geo)
        if amb:
            explicit = any(contains_phrase(corpus_n, m) for m in amb["explicit_markers"])
            if not explicit:
                dflt = amb["default"]
                if (g["name"], g["level"]) != (dflt["name"], dflt["level"]):
                    out.append(issue("LLM_CLAIM_OVERRIDDEN", WARNING, "L3", p,
                                     f"El borrador propuso {g['level']} para un nombre ambiguo sin marcador explícito; se aplica el default {dflt['level']}."))
                g = dict(dflt)
                entry = geo_lookup(g["name"], g["level"], reg.geo)
                assumptions.append({"field": "restrictions.geography", "value": dflt,
                                    "reason": f"'{dflt['name']}' es ambiguo ({dflt['level']} vs {[a['level'] for a in amb['alternatives']]}); se asume {dflt['level']}.",
                                    "basis": "geo_default"})
        if entry is None:
            if g["level"] == "country":
                out.append(issue("GEO_UNKNOWN_COUNTRY", ERROR, "L3", p, f"País '{g['name']}' fuera del catálogo geográfico."))
                continue
            out.append(issue("GEO_NOT_IN_CATALOG", WARNING, "L3", p, f"'{g['name']}' ({g['level']}) no está en el catálogo; se tratará como texto libre."))
            if not contains_phrase(corpus_n, norm(g["name"])):
                out.append(issue("GEO_UNGROUNDED", ERROR, "L3", p, f"'{g['name']}' no aparece en la petición."))
                continue
        else:
            aliases = [norm(a) for a in entry["aliases"]] + [norm(entry["name"])]
            if not any(contains_phrase(corpus_n, a) for a in aliases):
                out.append(issue("GEO_UNGROUNDED", ERROR, "L3", p, f"'{g['name']}' no aparece en la petición del usuario."))
                continue
        res["include"].append(g)
    return res, out, assumptions


def numeric_interval(c):
    op, v = c["operator"], c.get("value")
    if op == "eq" and _num(v): return (v, v)
    if op == "gte" and _num(v): return (v, float("inf"))
    if op == "lte" and _num(v): return (float("-inf"), v)
    if op == "between" and isinstance(v, list) and len(v) == 2: return (v[0], v[1])
    return None


def l3_contradictions(criteria):
    out, by_field = [], {}
    for i, c in enumerate(criteria):
        if c["strength"] == "gate":
            by_field.setdefault(c["field"], []).append((i, c))
    for field, items in by_field.items():
        ivs = [(i, numeric_interval(c)) for i, c in items if numeric_interval(c)]
        if len(ivs) > 1:
            lo = max(iv[0] for _, iv in ivs); hi = min(iv[1] for _, iv in ivs)
            if lo > hi:
                out.append(issue("CRITERIA_CONTRADICTION", ERROR, "L3", "$.criteria",
                                 f"Los gates sobre '{field}' no tienen intersección ({lo}..{hi})."))
        eqs = {str(c.get("value")) for _, c in items if c["operator"] == "eq" and not numeric_interval(c)}
        if len(eqs) > 1:
            out.append(issue("CRITERIA_CONTRADICTION", ERROR, "L3", "$.criteria", f"Gates 'eq' incompatibles sobre '{field}': {sorted(eqs)}."))
    return out


def l3_budget(budget, quantity_target):
    out = []
    if budget is None:
        return [issue("BUDGET_UNSET", WARNING, "L3", "$.budget",
                      "No hay límites de investigación. El planner deberá obtenerlos antes de gastar; el Intent Engine no inventa presupuestos.")]
    if budget.get("data_calls_max") == 0:
        out.append(issue("BUDGET_ZERO_DATA_CALLS", ERROR, "L3", "$.budget.data_calls_max", "data_calls_max=0 hace imposible cualquier búsqueda."))
    top = budget.get("deep_research_top_n")
    if top is not None and quantity_target and top < quantity_target:
        out.append(issue("BUDGET_DEEP_RESEARCH_BELOW_TARGET", WARNING, "L3", "$.budget.deep_research_top_n",
                         f"deep_research_top_n={top} < objetivo={quantity_target}: solo {top} resultados podrán tener investigación profunda."))
    out.append(issue("BUDGET_FEASIBILITY_DEFERRED", WARNING, "L3", "$.budget",
                     "La viabilidad (costes unitarios por llamada/crédito) NO se evalúa aquí: los costes de Apollo no están verificados. "
                     "Lo decide el Research Planner antes de gastar."))
    return out
