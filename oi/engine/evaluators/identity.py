"""BLOQUE 1 · Validación de IDENTIDAD POST-ENRICHMENT.
Una entidad pedida y la entidad devuelta por el proveedor pueden ser distintas (observado en 3C: un dominio español devolvió la filial de otro país, y otro devolvió una entidad sin dominio en otro continente).
Estados: KNOWN_MATCH / KNOWN_MISMATCH / AMBIGUOUS / UNKNOWN. Solo KNOWN_MATCH puede usarse como evidencia del candidato pedido.
SIN fuzzy matching: el nombre NUNCA decide (se registra solo la igualdad exacta normalizada como dato de apoyo). Nunca se fusionan entidades por parecido de nombre."""
from oi.engine.spec.text import norm
from .entity import normalize_domain
from .evaluation import country_code

KNOWN_MATCH, KNOWN_MISMATCH, AMBIGUOUS, UNKNOWN = "KNOWN_MATCH", "KNOWN_MISMATCH", "AMBIGUOUS", "UNKNOWN"


def _chain_ids(rec):
    ids = set()
    for k in ("owned_by_organization_id",):
        if rec.get(k): ids.add(rec[k])
    ids |= set(rec.get("owned_by_chain") or [])
    for k in ("owned_by_organization", "ultimate_parent_organization"):
        if isinstance(rec.get(k), dict) and rec[k].get("id"): ids.add(rec[k]["id"])
    return ids


def validate_post_enrichment(requested, returned, expected_countries=()):
    """requested: {apollo_id, name, domain, [country]} · returned: registro devuelto (dict) o None · expected_countries: p. ej. ('ES',) del spec."""
    if not returned:
        return {"state": UNKNOWN, "usable_as_evidence": False, "reasons": ["no_record_returned"], "checks": {}}
    qid, rid = requested.get("apollo_id"), returned.get("id")
    qdom = normalize_domain(requested.get("domain"))
    rdom = normalize_domain(returned.get("primary_domain") or returned.get("website_url") or returned.get("domain"))
    exp = {c for c in ([country_code(requested.get("country"))] if requested.get("country") else []) + [country_code(c) or c for c in expected_countries] if c}
    rc = country_code(returned.get("country"))
    rtext = norm(returned.get("country") or "")
    # país devuelto fuera del catálogo (p. ej. 'Thailand'): si no es alias del país esperado, es un país distinto -> conflicto (solo se usa si ya difiere el id y falta el dominio)
    country_conflict = bool(exp and ((rc and rc not in exp) or (not rc and rtext)))
    if not rc and rtext: rc = rtext
    sub_of_requested = bool(qid and qid in _chain_ids(returned))
    checks = {"id": "equal" if (qid and rid and qid == rid) else ("different" if (qid and rid) else "unavailable"),
              "domain": "equal" if (qdom and rdom and qdom == rdom) else ("different" if (qdom and rdom) else "unavailable"),
              "country": "conflict" if country_conflict else ("compatible" if rc else "unavailable"), "returned_is_subsidiary_of_requested": sub_of_requested,
              "name_equal_normalized": norm(requested.get("name") or "") == norm(returned.get("name") or "") if requested.get("name") and returned.get("name") else None,
              "requested": {"id": qid, "domain": qdom}, "returned": {"id": rid, "domain": rdom, "country": rc, "name": returned.get("name")}}
    reasons = []
    if checks["id"] == "equal":
        state = KNOWN_MATCH; reasons.append("same_apollo_id")
    elif checks["id"] == "different":
        if checks["domain"] == "equal": state = AMBIGUOUS; reasons.append("same_domain_different_id")
        elif sub_of_requested: state = AMBIGUOUS; reasons.append("returned_is_subsidiary_of_requested")
        elif checks["domain"] == "different": state = KNOWN_MISMATCH; reasons.append("id_and_domain_both_differ")
        elif country_conflict: state = KNOWN_MISMATCH; reasons.append("id_differs_domain_missing_country_conflict")
        else: state = AMBIGUOUS; reasons.append("id_differs_domain_missing_no_other_evidence")
    else:                                                   # sin ids comparables
        if checks["domain"] == "equal" and not country_conflict: state = KNOWN_MATCH; reasons.append("domain_only_match")
        elif checks["domain"] == "equal": state = AMBIGUOUS; reasons.append("domain_equal_but_country_conflict")
        elif checks["domain"] == "different": state = AMBIGUOUS; reasons.append("domain_differs_no_ids_to_confirm")
        else: state = UNKNOWN; reasons.append("no_comparable_identifiers")
    return {"state": state, "usable_as_evidence": state == KNOWN_MATCH, "reasons": reasons, "checks": checks}
