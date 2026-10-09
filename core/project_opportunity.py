# -*- coding: utf-8 -*-
"""project_opportunity · capacidad genérica del núcleo (v0.1)

Cualifica oportunidades basadas en PROYECTOS (obra, apertura, licitación, ampliación) en lugar de
firmográficos. Es independiente del caso: el caso aporta capacidades, referencias y oportunidades como datos.

Principios (heredados de evidence-contract-v0.2):
- Función pura: sin red, sin reloj implícito (as_of explícito), sin azar.
- Nada se promueve por suposición: lo que no tiene fuente no es un hecho.
- Prioridad lexicográfica explicable en una frase (no hay puntuación numérica).
"""
import datetime as dt

VERSION = "project-opportunity-v0.1"

SIGNAL_LEVELS = ("A", "B", "C", "D")          # A necesidad explícita · B proyecto concreto · C expansión · D indirecta
MATCH_LEVELS = ("DEMOSTRADO", "PROBABLE", "POSIBLE")
CONTACT_QUALITY = ("DIRECT", "INDIRECT", "NONE")
CLAIM_STATUS = ("KNOWN", "INFERRED", "ESTIMATED", "UNKNOWN", "CONFLICTING")   # = evidence-contract-v0.2
SOURCE_CLASS = ("OFFICIAL", "PUBLIC_OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE",
                "PROFESSIONAL_DIRECTORY", "UNVERIFIED", "NOT_FOUND", "HUMAN_REVIEW")
ROUTES = ("CLIENTE_FINAL", "SUBCONTRATA", "LICITACION")
ROLES = ("CLIENTE_FINAL", "PROMOTORA", "CONSTRUCTORA", "INTEGRADORA", "INGENIERIA", "ORGANISMO_PUBLICO")
BUCKETS = ("PRIORITARIA", "A_VIGILAR", "A_VERIFICAR", "DESCARTADA")
GEO_LEVELS = (1, 2, 3)                         # 1 Madrid capital · 2 Comunidad de Madrid · 3 resto de España

# Una afirmación KNOWN solo puede venir de estas clases de fuente; Apollo o un directorio nunca bastan solos.
KNOWN_ALLOWED_SOURCES = ("OFFICIAL", "PUBLIC_OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE")
STRENGTH_BY_SOURCE = {"OFFICIAL": "FUERTE", "PUBLIC_OFFICIAL": "FUERTE", "SECTOR_SOURCE": "MEDIA",
                      "OTHER_VERIFIABLE": "MEDIA", "PROFESSIONAL_DIRECTORY": "DEBIL", "UNVERIFIED": "DEBIL",
                      "NOT_FOUND": "NINGUNA", "HUMAN_REVIEW": "MEDIA"}
DIRECT_CHANNELS = ("tender_platform", "supplier_portal", "procurement_email", "person_email", "department_form")
INDIRECT_CHANNELS = ("contact_form", "company_phone", "general_email", "company_linkedin", "office_address")

RECENT_DAYS = 90       # «reciente»: el último hecho de estado tiene ≤ 90 días
ACTIVE_DAYS = 365      # «activo»: ≤ 365 días


def _date(s):
    """Acepta YYYY-MM-DD o YYYY-MM (día 1). Devuelve None si no hay fecha utilizable (nunca se sustituye)."""
    if not s:
        return None
    s = str(s)
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return dt.datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def recency(status_date, as_of):
    """RECIENTE / ACTIVA / ANTIGUA / SIN_FECHA según el último hecho fechado sobre el estado del proyecto."""
    d = _date(status_date)
    if d is None:
        return "SIN_FECHA"
    if d > as_of:
        return "CONFLICTING_DATE"
    age = (as_of - d).days
    return "RECIENTE" if age <= RECENT_DAYS else ("ACTIVA" if age <= ACTIVE_DAYS else "ANTIGUA")


def evidence_strength(claim):
    return STRENGTH_BY_SOURCE.get(claim.get("source_class"), "NINGUNA")


def best_source(opp):
    """Mejor clase de fuente entre las evidencias KNOWN de la oportunidad (para el criterio 6)."""
    order = ["OFFICIAL", "PUBLIC_OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE"]
    contact_ids = set((opp.get("contact") or {}).get("evidence") or [])   # la página de contacto no prueba el proyecto
    seen = [c["source_class"] for c in opp.get("claims", [])
            if c.get("claim_status") == "KNOWN" and c.get("id") not in contact_ids]
    for s in order:
        if s in seen:
            return s
    return "UNVERIFIED"


def why_now(opp):
    """Las 7 piezas de «¿por qué ahora?». Devuelve (completo, faltan)."""
    w = opp.get("why_now") or {}
    keys = ("event", "date", "location", "company", "potential_need", "offering_capability", "source")
    missing = [k for k in keys if not w.get(k)]
    return (not missing, missing)


def validate(opp):
    """Errores de contrato. Una lista vacía significa que la oportunidad respeta las reglas."""
    e = []
    oid = opp.get("id", "?")
    if opp.get("signal_level") not in SIGNAL_LEVELS: e.append(f"{oid}: signal_level inválido")
    if opp.get("match") not in MATCH_LEVELS: e.append(f"{oid}: match inválido")
    if opp.get("geo_level") not in GEO_LEVELS: e.append(f"{oid}: geo_level inválido")
    if opp.get("route") not in ROUTES: e.append(f"{oid}: route inválida")
    if opp.get("role") not in ROLES: e.append(f"{oid}: role inválido")
    claims = opp.get("claims") or []
    if not claims: e.append(f"{oid}: sin evidencias")
    for c in claims:
        cid = c.get("id", "?")
        if c.get("claim_status") not in CLAIM_STATUS: e.append(f"{oid}/{cid}: claim_status inválido")
        if c.get("source_class") not in SOURCE_CLASS: e.append(f"{oid}/{cid}: source_class inválida")
        if c.get("claim_status") == "KNOWN":
            if c.get("source_class") not in KNOWN_ALLOWED_SOURCES:
                e.append(f"{oid}/{cid}: KNOWN con fuente no admitida ({c.get('source_class')})")
            if not c.get("source_reference"):
                e.append(f"{oid}/{cid}: KNOWN sin URL")
            if not c.get("snippet"):
                e.append(f"{oid}/{cid}: KNOWN sin fragmento literal")
            if c.get("seen_in") == "search_snippet":
                e.append(f"{oid}/{cid}: KNOWN visto solo en snippet de búsqueda")
        if c.get("claim_status") == "KNOWN" and c.get("kind") == "inference":
            e.append(f"{oid}/{cid}: una inferencia no puede ser KNOWN")
    # la necesidad explícita solo puede afirmarse si una evidencia KNOWN la respalda
    if opp.get("explicit_need") and not any(c.get("supports") == "explicit_need" and c.get("claim_status") == "KNOWN" for c in claims):
        e.append(f"{oid}: explicit_need sin evidencia KNOWN que la respalde")
    if opp.get("signal_level") == "A" and not opp.get("explicit_need"):
        e.append(f"{oid}: nivel A exige necesidad explícita")
    ct = opp.get("contact") or {}
    q = ct.get("quality")
    if q not in CONTACT_QUALITY: e.append(f"{oid}: calidad de contacto inválida")
    if q == "DIRECT" and ct.get("channel_type") not in DIRECT_CHANNELS:
        e.append(f"{oid}: DIRECT con canal no directo ({ct.get('channel_type')})")
    if q == "INDIRECT" and ct.get("channel_type") not in INDIRECT_CHANNELS:
        e.append(f"{oid}: INDIRECT con canal no reconocido ({ct.get('channel_type')})")
    if q in ("DIRECT", "INDIRECT"):
        if not ct.get("value"): e.append(f"{oid}: contacto sin valor")
        if not ct.get("evidence"): e.append(f"{oid}: contacto sin evidencia")
        ids = {c.get("id") for c in claims}
        for ref in ct.get("evidence") or []:
            if ref not in ids: e.append(f"{oid}: el contacto cita evidencia inexistente {ref}")
    # emails: deben aparecer literalmente en el fragmento de su evidencia (nunca construidos)
    for c in claims:
        v = str(c.get("value") or "")
        if "@" in v and c.get("claim_status") == "KNOWN" and v.lower() not in str(c.get("snippet") or "").lower():
            e.append(f"{oid}/{c.get('id')}: email no presente en el fragmento citado")
    return e


def classify(opp, as_of):
    """PRIORITARIA solo si: nivel A/B, «por qué ahora» completo, contacto accionable y estado no antiguo."""
    if opp.get("discard_reason"):
        return "DESCARTADA", [opp["discard_reason"]]
    reasons = []
    ok, missing = why_now(opp)
    if not ok:
        reasons.append("«¿Por qué ahora?» incompleto: falta " + ", ".join(missing))
    if opp.get("signal_level") not in ("A", "B"):
        reasons.append("señal de expansión o indirecta, sin proyecto concreto")
    rec = recency(opp.get("status_date"), as_of)
    if rec in ("ANTIGUA", "SIN_FECHA", "CONFLICTING_DATE"):
        reasons.append(f"estado del proyecto sin confirmar recientemente ({rec})")
    contact_q = (opp.get("contact") or {}).get("quality")
    if opp.get("awaiting"):
        return "A_VIGILAR", [opp["awaiting"]] + reasons
    if not reasons and contact_q in ("DIRECT", "INDIRECT"):
        return "PRIORITARIA", []
    if contact_q == "NONE" and not reasons:
        return "A_VERIFICAR", ["falta un canal de contacto verificado"]
    return "A_VERIFICAR", reasons


_RANK = {"signal_level": {"A": 0, "B": 1, "C": 2, "D": 3},
         "recency": {"RECIENTE": 0, "ACTIVA": 1, "ANTIGUA": 2, "SIN_FECHA": 3, "CONFLICTING_DATE": 3},
         "match": {"DEMOSTRADO": 0, "PROBABLE": 1, "POSIBLE": 2},
         "source": {"OFFICIAL": 0, "PUBLIC_OFFICIAL": 0, "SECTOR_SOURCE": 1, "OTHER_VERIFIABLE": 2, "UNVERIFIED": 3},
         "contact": {"DIRECT": 0, "INDIRECT": 1, "NONE": 2}}


def priority_key(opp, as_of):
    """Orden lexicográfico pedido: necesidad explícita → proyecto concreto → recencia → match → Madrid → evidencia → contacto."""
    return (_RANK["signal_level"][opp["signal_level"]],
            _RANK["recency"][recency(opp.get("status_date"), as_of)],
            _RANK["match"][opp["match"]],
            opp["geo_level"],
            _RANK["source"][best_source(opp)],
            _RANK["contact"][(opp.get("contact") or {}).get("quality", "NONE")],
            opp.get("id", ""))


def priority_label(opp, as_of):
    """ALTA / MEDIA / BAJA con una frase que lo explica."""
    lvl, rec, m = opp["signal_level"], recency(opp.get("status_date"), as_of), opp["match"]
    if lvl == "A":
        return "ALTA", "Necesidad publicada (licitación abierta) en un trabajo que el proveedor del caso ya ha hecho."
    if lvl == "B" and rec == "RECIENTE" and m in ("DEMOSTRADO", "PROBABLE"):
        return "ALTA", "Proyecto concreto con novedades en los últimos 90 días y de un tipo que el proveedor del caso ya ha ejecutado."
    if lvl == "B":
        return "MEDIA", "Proyecto concreto en marcha, pero con señal menos reciente o encaje sin detallar."
    return "BAJA", "Señal de expansión o indirecta: seguimiento, no llamada inmediata."


def rank(opps, as_of):
    out = []
    for o in opps:
        b, why = classify(o, as_of)
        lab, lab_why = priority_label(o, as_of)
        out.append(dict(o, bucket=b, bucket_reasons=why, recency=recency(o.get("status_date"), as_of),
                        priority=lab, priority_reason=lab_why, best_source=best_source(o)))
    order = {k: i for i, k in enumerate(BUCKETS)}
    out.sort(key=lambda x: (order[x["bucket"]], priority_key(x, as_of)))
    for i, x in enumerate([x for x in out if x["bucket"] == "PRIORITARIA"], 1):
        x["rank"] = i
    return out
