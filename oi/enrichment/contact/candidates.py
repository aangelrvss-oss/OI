"""Evidencia de contacto -> candidatos de contacto (personas y canales) -> opportunity_contact.v1 (base) listo para el selector.

NO inventa nada: una persona exige nombre y cargo en la evidencia; un canal exige valor, tipo, ámbito y propósito declarados por el productor.
Lo que falta se declara en `not_found` con su causa. La utilidad comercial (grado) la decide después el selector; aquí solo se estructura.
Flujo:  evidence_input[] -> candidatos -> (perfil) -> selector -> recommended_contact.
"""
import re

from oi.engine.spec.text import norm
from . import roles as RL

KINDS = {"email": "EMAIL", "phone": "PHONE", "form": "FORM", "portal": "PORTAL", "linkedin": "LINKEDIN", "postal": "POSTAL"}
SCOPES = ("PERSON", "DEPARTMENT", "COMPANY")
PURPOSES = ("GENERAL", "COMMERCIAL", "PROCUREMENT", "SUPPORT", "PRESS", "LEGAL", "DPO", "UNKNOWN")
ACCESS = ("OPEN", "RESTRICTED", "UNKNOWN")
ROLE_FAMILIES = ("PROCUREMENT", "COMMERCIAL", "DECISION_MAKER", "BUSINESS_DEV", "IT_OPERATIONS", "FINANCE", "MANAGEMENT", "GENERAL")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://")
CONTACT_FIELDS = ("person:", "channel:", "contact:")


def is_contact_item(it): return it["field"].startswith(CONTACT_FIELDS)


def build_base(company_ref, items, record_evidence, *, opportunity_type, offering_kind, target_function):
    """items: evidence_input.v1 de ESTA empresa (todas); record_evidence: {evidence_id -> evidencia ya convertida (fuerza derivada)}.
    Devuelve (base_opportunity_contact, trazas) donde trazas lista lo que se descartó y por qué."""
    cit = [i for i in items if is_contact_item(i)]
    base = {"contract_version": "opportunity_contact.v1", "company_ref": company_ref, "opportunity_type": opportunity_type, "offering_kind": offering_kind, "target_function": target_function,
            "contact_profile_id": None, "enrichment_state": "PENDING", "levels_reached": [], "channels": [], "people": [], "role_contacts": [], "recommended_contact": None, "alternatives": [],
            "not_recommended": [], "evidence": [], "contradictions": [], "not_found": [], "last_verified": None, "selection_blocked": None,
            "cost": {"paid_calls": 0, "credits": 0, "paid_enrichment_authorized": False, "authorization_ref": None}}
    trace = []
    if not cit: return base, trace
    base["levels_reached"] = [0, 1]; base["enrichment_state"] = "LEVEL1_DONE"
    persons = {}      # person_ref -> person dict
    for it in cit:
        base["evidence"].append(record_evidence[it["evidence_id"]])
        f = it["field"]; a = it.get("attributes") or {}
        if f == "contact:any":
            if it["source_class"] == "NOT_FOUND": base["not_found"].append(it.get("note") or "No se localizó ningún contacto.")
            continue
        if f == "person:title":
            ref = it["subject"].get("person_ref"); name = a.get("person_name"); title = it["value"]
            if not (ref and name and isinstance(title, str) and title.strip()):
                base["not_found"].append(f"Persona descartada ({it['evidence_id']}): falta referencia, nombre o cargo en la evidencia"); trace.append((it["evidence_id"], "persona incompleta")); continue
            c = RL.classify(title)
            p = persons.setdefault(ref, {"person_id": f"{company_ref['company_id']}:{ref}", "name": name, "title": title, "department": a.get("department"), "role_family": c["role_family"],
                                          "seniority": c["seniority"], "role_evidence_ids": [], "channel_ids": [], "employment_verified_at": a.get("employment_verified_at") or it.get("dated_on")})
            p["role_evidence_ids"].append(it["evidence_id"]); continue
        if f.startswith("channel:"):
            kind = KINDS.get(f.split(":", 1)[1])
            if kind is None: base["not_found"].append(f"Canal descartado ({it['evidence_id']}): tipo desconocido {f!r}"); continue
            v = it["value"]; scope = a.get("scope"); purpose = a.get("purpose")
            bad = None
            if not isinstance(v, str) or not v.strip(): bad = "valor ausente"
            elif kind == "EMAIL" and not EMAIL_RE.match(v.strip()): bad = "email con formato inválido"
            elif kind in ("FORM", "PORTAL", "LINKEDIN") and not URL_RE.match(v.strip()): bad = "URL inválida"
            elif scope not in SCOPES: bad = "ámbito (scope) no declarado por el productor"
            elif purpose not in PURPOSES: bad = "propósito no declarado por el productor"
            elif scope == "DEPARTMENT" and a.get("role_family") not in ROLE_FAMILIES: bad = "canal de departamento sin role_family declarada"
            if bad:
                base["not_found"].append(f"Canal descartado ({it['evidence_id']}): {bad}"); trace.append((it["evidence_id"], bad)); continue
            base["channels"].append({"channel_id": f"{company_ref['company_id']}:ch{len(base['channels']) + 1}", "kind": kind, "value": v.strip(), "scope": scope, "purpose": purpose,
                                     "evidence_ids": [it["evidence_id"]], "person_id": (f"{company_ref['company_id']}:{it['subject'].get('person_ref')}" if scope == "PERSON" else None),
                                     "role_family": a.get("role_family") if scope == "DEPARTMENT" else None, "outreach_access": a.get("outreach_access") if a.get("outreach_access") in ACCESS else "UNKNOWN"})
        if it["claim_status"] == "CONFLICTING":
            base["contradictions"].append({"field": f, "description": it.get("note") or "Fuentes contradictorias sobre este dato.", "state": "AMBIGUOUS"})
    # los canales de persona solo valen si la persona existe; cada persona conoce sus canales
    ids = {p["person_id"] for p in persons.values()}
    keep = []
    for ch in base["channels"]:
        if ch["scope"] == "PERSON" and ch["person_id"] not in ids:
            base["not_found"].append(f"Canal de persona descartado ({ch['evidence_ids'][0]}): la persona no está identificada con nombre y cargo"); continue
        keep.append(ch)
    base["channels"] = keep
    for ch in keep:
        if ch["scope"] == "PERSON":
            p = next(p for p in persons.values() if p["person_id"] == ch["person_id"]); p["channel_ids"].append(ch["channel_id"])
    base["people"] = list(persons.values())
    return base, trace
