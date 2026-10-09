"""VERIFIED GATES: evaluadores deterministas sobre datos ENRIQUECIDOS. Cada resultado conserva el estado de evidencia
(KNOWN / INFERRED / ESTIMATED / UNKNOWN / CONFLICTING) y la fuente. UNKNOWN != PASS, UNKNOWN != FAIL, CONFLICTING no se resuelve solo.
Estas funciones definen la SEMÁNTICA que el plan promete; no llaman a ninguna herramienta."""
import json
import os
import re
from oi.engine.spec.text import norm, contains_phrase

KNOWN, INFERRED, ESTIMATED, UNKNOWN, CONFLICTING = "KNOWN", "INFERRED", "ESTIMATED", "UNKNOWN", "CONFLICTING"
_GEO = json.load(open(__import__("oi.paths", fromlist=["paths"]).data_file("geo_catalog.v1.json"), encoding="utf-8"))
_ALIASES = {a: c["code"] for c in _GEO["countries"] for a in [norm(c["code"]), norm(c["name"])] + [norm(x) for x in c["aliases"]]}
_LOCAL_COUNTRY = {norm(m["name"]): m["country"] for k in ("municipalities", "provinces", "regions") for m in _GEO[k]}


def country_code(s):
    return _ALIASES.get(norm(s)) if s else None


def gate_outcome(verdict, evidence_status):
    """Estado del candidato tras un gate. Un FAIL solo descarta como hecho si la evidencia es KNOWN; con ESTIMATED/INFERRED se conserva para revisión."""
    if verdict == "PASS": return "passed"
    if verdict == "UNKNOWN": return "pending_verification"
    if verdict == "CONFLICTING": return "conflicting_review"
    return {KNOWN: "failed", ESTIMATED: "gate_failed_estimated", INFERRED: "gate_failed_inferred"}.get(evidence_status, "pending_verification")


def _res(verdict, ev, **kw):
    return {"verdict": verdict, "evidence_status": ev, "gate_state": gate_outcome(verdict, ev), **kw}


def evaluate_employee_count(record, lo, hi):
    """estimated_num_employees es una ESTIMACIÓN: el veredicto se calcula sobre ella pero la evidencia queda ESTIMATED, nunca KNOWN."""
    n = (record or {}).get("estimated_num_employees")
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return _res("UNKNOWN", UNKNOWN, source_field="estimated_num_employees", reason="campo ausente", range=[lo, hi])
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    dept = (record or {}).get("departmental_head_count")
    return _res("PASS" if ok else "FAIL", ESTIMATED, value=n, range=[lo, hi], source_field="estimated_num_employees", caveats=["estimacion_no_exacta"],
                corroboration={"departmental_head_count_sum": sum(v for v in dept.values() if isinstance(v, (int, float))) if isinstance(dept, dict) else None,
                               "note": "cobertura parcial observada; no es una contradicción por sí sola"})


def evaluate_location(structured, raw_address, targets, invert=False):
    """Compara DOS fuentes (campos estructurados y raw_address). Nunca 'raw != target => FAIL'. Estados: location_verified / location_conflicting / location_unknown."""
    structured = structured or {}
    t_codes, t_local = set(), []
    for t in targets:
        lvl, name = t.get("level"), t.get("name")
        if lvl == "country":
            c = country_code(name)
            if c: t_codes.add(c)
        else:
            t_local.append(norm(name)); c = _LOCAL_COUNTRY.get(norm(name))
            if c: t_codes.add(c)
    s_code = country_code(structured.get("country"))
    r_code = None
    if raw_address:
        tail = str(raw_address).split(",")[-1].strip()
        r_code = tail.upper() if re.fullmatch(r"[A-Za-z]{2}", tail) else country_code(tail)   # ISO-2 al final aunque el país no esté en el catálogo
    def side(code, blob):
        if code is None: return None
        ok = code in t_codes if t_codes else None
        if ok and t_local: ok = any(contains_phrase(norm(blob), m) for m in t_local)
        return ok
    s_ok = side(s_code, " ".join(str(structured.get(k) or "") for k in ("city", "state")))
    r_ok = side(r_code, raw_address or "")
    src = {"structured": {"country": s_code, "city": structured.get("city"), "state": structured.get("state"), "matches_target": s_ok},
           "raw_address": {"country_code": r_code, "matches_target": r_ok}}
    flip = (lambda v: {"PASS": "FAIL", "FAIL": "PASS"}.get(v, v)) if invert else (lambda v: v)
    have = [x for x in (s_ok, r_ok) if x is not None]
    if not have:
        return _res("UNKNOWN", UNKNOWN, location_state="location_unknown", sources=src, reason="sin ubicación utilizable")
    if len(have) == 1:
        return _res("UNKNOWN", INFERRED, location_state="location_unknown", sources=src, single_source_hint=have[0],
                    reason="una sola fuente: no se puede corroborar (no se decide PASS/FAIL)")
    if s_ok != r_ok:
        return _res("CONFLICTING", CONFLICTING, location_state="location_conflicting", sources=src, reason="estructura y raw_address discrepan: revisión humana")
    return _res(flip("PASS" if s_ok else "FAIL"), KNOWN, location_state="location_verified", sources=src)


def evaluate_industry(record, target_labels, target_naics_prefixes=()):
    """industry puede ser multi-etiqueta y la principal puede engañar: nunca se usa solo la principal como verdad."""
    r = record or {}
    tl = {norm(x) for x in target_labels}
    primary = norm(r.get("industry"))
    labels = {x for x in [primary] + [norm(i) for i in (r.get("industries") or []) + (r.get("secondary_industries") or [])] if x}
    naics = [str(c) for c in (r.get("naics_codes") or [])]
    if not labels and not naics:
        return _res("UNKNOWN", UNKNOWN, reason="sin etiquetas ni NAICS", source_field="industry")
    p_match, any_match = primary in tl, bool(labels & tl)
    n_match = any(c.startswith(tuple(target_naics_prefixes)) for c in naics) if target_naics_prefixes else None
    ev = {"primary": r.get("industry"), "labels": sorted(labels), "naics_codes": naics, "primary_matches": p_match, "any_label_matches": any_match, "naics_matches": n_match}
    if p_match and n_match is False:
        return _res("CONFLICTING", CONFLICTING, evidence=ev, reason="la etiqueta principal encaja pero el NAICS no")
    if p_match:
        return _res("PASS", INFERRED, evidence=ev)
    if any_match or n_match:
        return _res("CONFLICTING", CONFLICTING, evidence=ev, reason="la principal no encaja pero otra etiqueta/NAICS sí: la principal puede ser engañosa")
    return _res("FAIL", INFERRED, evidence=ev)


_DAYS_TO_FIELD = {180: "organization_headcount_six_month_growth", 365: "organization_headcount_twelve_month_growth", 730: "organization_headcount_twenty_four_month_growth"}
_MONTHS_TO_FIELD = {6: "organization_headcount_six_month_growth", 12: "organization_headcount_twelve_month_growth", 24: "organization_headcount_twenty_four_month_growth"}


def evaluate_growth(record, requested_window, operator, threshold_pct):
    """requested_window = {'unit':'days'|'months','value':N}. Distingue requested_window de available_window y registra la transformación."""
    unit, val = requested_window["unit"], requested_window["value"]
    field = (_MONTHS_TO_FIELD if unit == "months" else _DAYS_TO_FIELD).get(val)
    base = {"requested_window": requested_window, "threshold_pct": threshold_pct, "operator": operator}
    if field is None:
        return _res("UNKNOWN", UNKNOWN, available_window=None, reason="no existe ventana equivalente: no se estira ni se aproxima", **base)
    frac = (record or {}).get(field)
    transform = "exacta" if unit == "months" else "aproximacion_dias_a_meses"
    avail = {"field": field, "months": {_MONTHS_TO_FIELD[m]: m for m in _MONTHS_TO_FIELD}[field]}
    if not isinstance(frac, (int, float)):
        return _res("UNKNOWN", UNKNOWN, available_window=avail, transformation=transform, reason="campo ausente", **base)
    pct = frac * 100.0
    ok = {"gte": pct >= threshold_pct, "gt": pct > threshold_pct, "lte": pct <= threshold_pct, "lt": pct < threshold_pct}[operator]
    ev = ESTIMATED if unit == "months" else INFERRED          # una aproximación de ventana es INFERRED; el dato de Apollo ya es una estimación
    return _res("PASS" if ok else "FAIL", ev, value_pct=pct, available_window=avail, transformation=transform + "+fraccion_a_porcentaje", **base)


def evaluate_title_families(postings, families, retrieved_all=None):
    """Señal GENÉRICA de contratación técnica: coincidencia léxica de TÍTULOS de oferta con familias de términos definidas por el usuario.
    Una coincidencia es una SEÑAL (INFERRED), no prueba de necesidad. La ausencia de coincidencias NUNCA es FAIL: NOT_OBSERVED / UNKNOWN.
    postings=None => no se obtuvieron ofertas (UNKNOWN). retrieved_all=False => solo se vio una página (no se afirma ausencia)."""
    if postings is None:
        return {"signal_state": "UNKNOWN", "evidence_status": UNKNOWN, "matches": [], "reason": "ofertas no obtenidas", "verdict": "UNKNOWN"}
    matches = []
    for p in postings:
        t = norm(str(p.get("title") or ""))
        for fam, terms in families.items():
            for term in terms:
                if contains_phrase(t, norm(term)):
                    matches.append({"family": fam, "term": term, "title": p.get("title"), "url": p.get("url"), "posted_at": p.get("posted_at"), "last_seen_at": p.get("last_seen_at"), "city": p.get("city")}); break
    if matches:
        return {"signal_state": "PRESENT", "evidence_status": INFERRED, "matches": matches, "postings_seen": len(postings), "verdict": "PASS", "caveat": "coincidencia léxica de título; no prueba necesidad"}
    return {"signal_state": "NOT_OBSERVED", "evidence_status": UNKNOWN, "matches": [], "postings_seen": len(postings), "verdict": "UNKNOWN",
            "caveat": "sin coincidencias en las ofertas recuperadas" + ("" if retrieved_all else " (solo una página; no prueba ausencia)")}


def evaluate_hiring_velocity(postings, retrieved_on, period_days, min_count):
    """Señal OPCIONAL: nº de ofertas publicadas en la ventana. Nunca FAIL: por debajo del umbral = NOT_OBSERVED. El umbral lo define el usuario/spec; no es un gate."""
    import datetime
    if postings is None: return {"signal_state": "UNKNOWN", "evidence_status": UNKNOWN, "verdict": "UNKNOWN", "reason": "ofertas no obtenidas"}
    end = datetime.date.fromisoformat(str(retrieved_on)[:10]); n = unparsable = 0
    for p in postings:
        try: d = datetime.date.fromisoformat(str(p.get("posted_at"))[:10])
        except ValueError: unparsable += 1; continue
        if 0 <= (end - d).days <= period_days: n += 1
    st = "PRESENT" if n >= min_count else "NOT_OBSERVED"
    return {"signal_state": st, "evidence_status": INFERRED, "verdict": "PASS" if st == "PRESENT" else "UNKNOWN", "postings_in_window": n, "window_days": period_days, "threshold": min_count, "postings_seen": len(postings), "unparsable_dates": unparsable,
            "caveat": "conteo sobre la página de ofertas recuperada; no equivale a crecimiento"}


def evaluate_funding_window(rec, window_days, as_of):
    """Señal opcional 'evento de financiación dentro de la ventana'. Un evento NO es reciente por existir: se compara su fecha con la ventana."""
    from oi.engine.planner import temporal as T
    dates = [e.get("date") for e in (rec.get("funding_events") or []) if isinstance(e, dict)]
    if rec.get("latest_funding_round_date"): dates.append(rec["latest_funding_round_date"])
    dates = list(dict.fromkeys(d for d in dates if d))
    if not (rec.get("funding_events") or rec.get("latest_funding_round_date")):
        return {"verdict": "UNKNOWN", "evidence_status": UNKNOWN, "gate_state": "pending_verification", "temporal_state": T.UNKNOWN_DATE, "reason": "ausencia en el proveedor no es evidencia"}
    c = T.classify_event_dates(dates, as_of, window_days)
    v = {T.KNOWN_RECENT: ("PASS", INFERRED), T.KNOWN_OLD: ("FAIL", INFERRED), T.UNKNOWN_DATE: ("UNKNOWN", UNKNOWN), T.CONFLICTING_DATE: ("CONFLICTING", CONFLICTING)}[c["state"]]
    return {"verdict": v[0], "evidence_status": v[1], "gate_state": gate_outcome(*v), "temporal_state": c["state"], "window_days": window_days, "events": c["events"], "anomalies": c["anomalies"], "most_recent_in_window": c["most_recent_in_window"]}


def evaluate_fit_keywords(rec, families):
    """EVIDENCIA FIT por LÉXICO FIJO (frases, nunca subcadenas) sobre las palabras clave y la descripción del registro. Es una coincidencia léxica (INFERRED): no prueba actividad ni necesidad. Nunca FAIL."""
    kws = [k for k in (rec.get("keywords") or []) if isinstance(k, str)]; desc = rec.get("short_description") or ""
    if not kws and not desc:
        return {"signal_state": "UNKNOWN", "evidence_status": UNKNOWN, "verdict": "UNKNOWN", "families": {}, "reason": "sin palabras clave ni descripción"}
    hits = {}
    for fam, terms in families.items():
        found = [t for t in terms if any(contains_phrase(norm(k), norm(t)) for k in kws) or contains_phrase(norm(desc), norm(t))]
        if found: hits[fam] = found
    st = "PRESENT" if hits else "NOT_OBSERVED"
    return {"signal_state": st, "evidence_status": INFERRED if hits else UNKNOWN, "verdict": "PASS" if hits else "UNKNOWN", "families": hits, "n_keywords_scanned": len(kws), "description_scanned": bool(desc),
            "caveat": "coincidencia léxica con un léxico fijo; las palabras clave de Apollo contienen ruido"}
