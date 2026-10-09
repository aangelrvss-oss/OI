"""Comprobaciones de SEGURIDAD sobre un opportunity_contact.v1 (más allá de la forma que ya valida el JSON Schema).

Devuelven una lista de violaciones [{code, ref, message}]; vacía = seguro. `assert_safe` lanza ContactSafetyError.
Garantías: no se inventan emails/teléfonos; INFERRED/ESTIMATED nunca se promueve a recomendación; un contacto UNVERIFIED no se recomienda;
la calidad del contacto se DERIVA (no se asigna a mano); no hay gasto sin autorización; no se recomienda sin perfil (sin offering.kind).
"""
from oi.contracts.validator import validate
from .selector import derive_grade, norm_value, usable_evidence, usable_purpose, STRENGTH_RANK, _best


class ContactSafetyError(Exception):
    def __init__(self, violations):
        super().__init__("; ".join(f"{v['code']}[{v['ref']}]" for v in violations)); self.violations = violations


def _v(code, ref, msg): return {"code": code, "ref": ref, "message": msg}


def check_contact(contact, profile_doc):
    out = []
    ev = {e["evidence_id"]: e for e in contact.get("evidence", [])}
    hard = profile_doc["hard_filters"]
    ch_idx = {c["channel_id"]: c for c in contact.get("channels", [])}
    pe_idx = {p["person_id"]: p for p in contact.get("people", [])}
    # 1) ningún canal sin evidencia que lo sostenga (anti-invención de emails/teléfonos)
    for ch in contact.get("channels", []):
        missing = [i for i in ch["evidence_ids"] if i not in ev]
        if missing: out.append(_v("EVIDENCE_MISSING", ch["channel_id"], f"evidencia inexistente: {missing}"))
        backing = [ev[i] for i in ch["evidence_ids"] if i in ev and norm_value(ev[i]["value"]) == norm_value(ch["value"])]
        if not backing: out.append(_v("INVENTED_CHANNEL", ch["channel_id"], f"el valor {ch['value']!r} no aparece en ninguna evidencia que lo respalde: posible dato inventado"))
        if ch.get("usable_for_outreach") is not None and ch["usable_for_outreach"] != usable_purpose(ch["purpose"], hard):
            out.append(_v("USABLE_FLAG_NOT_DERIVED", ch["channel_id"], "usable_for_outreach no coincide con el propósito del canal"))
    for p in contact.get("people", []):
        missing = [i for i in p["role_evidence_ids"] if i not in ev]
        if missing: out.append(_v("EVIDENCE_MISSING", p["person_id"], f"evidencia inexistente: {missing}"))
        for cid in p["channel_ids"]:
            if cid not in ch_idx: out.append(_v("CHANNEL_REF_MISSING", p["person_id"], f"canal inexistente: {cid}"))
    # 2) recomendados y alternativas: solo evidencia usable (KNOWN, no UNVERIFIED), refs válidas, calidad derivada
    recs = ([("recommended_contact", contact["recommended_contact"])] if contact.get("recommended_contact") else []) + [(f"alternatives[{i}]", a) for i, a in enumerate(contact.get("alternatives", []))]
    if recs and contact.get("contact_profile_id") is None:
        out.append(_v("RECOMMENDATION_WITHOUT_PROFILE", "recommended_contact", "hay recomendación pero no se eligió perfil de contacto (¿falta offering.kind?)"))
    if recs and contact.get("offering_kind") in (None, "UNKNOWN"):
        out.append(_v("OFFERING_KIND_REQUIRED", "recommended_contact", "no se puede recomendar a quién contactar sin saber qué se ofrece (offering.kind)"))
    for name, r in recs:
        comp_ev = []
        if r["target"] == "PERSON":
            p = pe_idx.get(r["ref"])
            if not p: out.append(_v("RECOMMENDED_REF_MISSING", name, f"persona inexistente: {r['ref']}")); continue
            comp_ev += [ev[i] for i in p["role_evidence_ids"] if i in ev]
        else:
            ch = ch_idx.get(r["ref"])
            if not ch: out.append(_v("RECOMMENDED_REF_MISSING", name, f"canal inexistente: {r['ref']}")); continue
            comp_ev += [ev[i] for i in ch["evidence_ids"] if i in ev]
            if not usable_purpose(ch["purpose"], hard): out.append(_v("EXCLUDED_PURPOSE_RECOMMENDED", name, f"propósito {ch['purpose']} no es de prospección"))
        via = ch_idx.get(r["via_channel_id"]) if r["via_channel_id"] else None
        if r["via_channel_id"] and not via: out.append(_v("VIA_CHANNEL_MISSING", name, f"canal inexistente: {r['via_channel_id']}"))
        if via:
            comp_ev += [ev[i] for i in via["evidence_ids"] if i in ev]
            if not usable_purpose(via["purpose"], hard): out.append(_v("EXCLUDED_PURPOSE_RECOMMENDED", name, f"vía con propósito {via['purpose']}"))
        for e in comp_ev:
            if e["source_class"] in hard["disallowed_source_classes"]:
                out.append(_v("UNVERIFIED_RECOMMENDED", name, f"evidencia {e['evidence_id']} es {e['source_class']}: no puede sostener un contacto recomendado"))
            if e["claim_status"] not in hard["allowed_claim_status"]:
                out.append(_v("INFERRED_PROMOTED", name, f"evidencia {e['evidence_id']} es {e['claim_status']}: una inferencia no se promueve a contacto recomendado"))
        q = r["contact_quality"]
        if r["via_channel_id"] is None and q["reachability"] != "NONE": out.append(_v("QUALITY_NOT_DERIVED", name, "sin vía no puede haber alcanzabilidad"))
        if q["reachability"] == "NONE": out.append(_v("NON_ACTIONABLE_RECOMMENDED", name, "reachability NONE: no se recomienda un destino inalcanzable"))
        if comp_ev:
            real = min((e["evidence_strength"] for e in comp_ev if usable_evidence(e, hard)), key=lambda s: STRENGTH_RANK[s], default="UNKNOWN")
            # el mínimo de fuerza entre componentes usables no puede ser peor que el declarado, ni el declarado mejor que la evidencia
            if STRENGTH_RANK[q["evidence_strength"]] < STRENGTH_RANK[_best([e for e in comp_ev if usable_evidence(e, hard)])]:
                out.append(_v("STRENGTH_OVERSTATED", name, f"evidence_strength {q['evidence_strength']} es mejor que la mejor evidencia disponible"))
        if q["grade"] != derive_grade(q, profile_doc["grading"]): out.append(_v("QUALITY_NOT_DERIVED", name, f"grade {q['grade']} no coincide con el derivado por reglas"))
    # 3) coste y autorización
    cost = contact.get("cost", {})
    if (cost.get("paid_calls", 0) > 0 or cost.get("credits", 0) > 0) and not cost.get("paid_enrichment_authorized"):
        out.append(_v("SPEND_WITHOUT_AUTHORIZATION", "cost", "hay gasto registrado sin paid_enrichment_authorized=true"))
    if contact.get("enrichment_state") == "PAID_DONE" and not cost.get("paid_enrichment_authorized"):
        out.append(_v("PAID_STATE_WITHOUT_AUTHORIZATION", "enrichment_state", "PAID_DONE exige autorización"))
    if contact.get("enrichment_state") == "FREE_ENRICHMENT_EXHAUSTED" and 1 not in contact.get("levels_reached", []):
        out.append(_v("EXHAUSTED_WITHOUT_LEVEL1", "enrichment_state", "FREE_ENRICHMENT_EXHAUSTED solo si se agotó el nivel 1 gratuito"))
    return out


def validate_contact(contact, profile_doc):
    """Forma (JSON Schema) + seguridad. Devuelve (errores_de_esquema, violaciones_de_seguridad)."""
    schema_errs = validate(contact, "opportunity_contact.v1.schema.json")
    return schema_errs, ([] if schema_errs else check_contact(contact, profile_doc))


def assert_safe(contact, profile_doc):
    s, v = validate_contact(contact, profile_doc)
    if s: raise ContactSafetyError([_v("SCHEMA_VIOLATION", e["path"], e["message"]) for e in s])
    if v: raise ContactSafetyError(v)
    return True
