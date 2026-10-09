"""Adaptador de SOLO LECTURA: artefactos históricos del piloto de reventa -> opportunity_record.v1.

Es CONFIGURACIÓN/IMPORTADOR del caso, no motor: aquí viven el vocabulario del caso (SÍ/DUDOSA/NO, grupos G1–G4, consultas RP-*) y la lectura de su formato legado.
Garantías:
  * Verifica el sha256 de cada fuente histórica antes de leer (si cambió, aborta: la integridad histórica es innegociable).
  * No escribe en el directorio histórico. Sale un JSON nuevo bajo cases/reseller_pilot/records/.
  * No inventa datos: un canal/persona solo se importa si hay evidencia a nivel de dato; lo demás queda en `not_found` con la causa.
  * La fuerza de evidencia se DERIVA por reglas (rules/evidence_strength.v1.json), no se copia el campo `confidence` del enriquecimiento v0.1
    (que mezclaba fiabilidad del dato con utilidad del contacto).
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

from oi.enrichment.contact import profiles as CP, selector as SEL     # noqa: E402
from oi.evidence import strength as ST                                  # noqa: E402
from oi.rules import next_action as NA                                  # noqa: E402
from oi.paths import history_path                                      # noqa: E402

SRC_CLASS = {"official_website": "OFFICIAL", "press": "SECTOR_SOURCE", "directory": "PROFESSIONAL_DIRECTORY", "other": "OTHER_VERIFIABLE"}
FIELD_KIND = {"general_email": "EMAIL", "data_protection_email": "EMAIL", "company_phone": "PHONE", "central_admin_phone": "PHONE", "contact_page": "FORM", "contact_form": "FORM",
              "supplier_page": "PORTAL", "supplier_section": "PORTAL", "company_linkedin": "LINKEDIN"}
PERSON_FIELDS = ("person", "person_ceo")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\(?\+?\d[\d\s().\-]{6,}\d")
URL_RE = re.compile(r"https?://[^\s)]+")
DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f: h.update(f.read())
    return h.hexdigest()


def load_case():
    with open(os.path.join(HERE, "case.json"), encoding="utf-8") as f: return json.load(f)


def load_sources(case, root=None):
    """Lee las fuentes históricas verificando su hash. root permite apuntar a una copia (p. ej. en otra máquina)."""
    base = root or history_path(case["historical_sources"]["root"]); out = {}
    for s in case["historical_sources"]["files"]:
        p = os.path.join(base, s["path"])
        got = _sha(p)
        if got != s["sha256"]: raise RuntimeError(f"INTEGRIDAD HISTÓRICA: {s['path']} cambió (esperado {s['sha256'][:12]}…, actual {got[:12]}…). No se importa.")
        with open(p, encoding="utf-8") as f: out[s["id"]] = json.load(f)
    return out


# ───────────────────────── contacto ─────────────────────────
def _purpose(field, text):
    t = (text or "").lower()
    if field == "data_protection_email" or "protección de datos" in t or "rgpd" in t: return "DPO"
    if "atención al cliente" in t or "atencion al cliente" in t: return "SUPPORT"
    if "prensa" in t: return "PRESS"
    if "aviso legal" in t: return "LEGAL"
    if field in ("supplier_page", "supplier_section"): return "PROCUREMENT"
    return "GENERAL"


def _access(kind, text):
    """OPEN solo con evidencia explícita de que el canal admite un primer contacto; en este caso ninguna evidencia lo afirma."""
    t = (text or "").lower()
    if kind in ("PORTAL", "FORM") and any(k in t for k in ("alta no confirmada", "sin formulario", "existentes", "sin enlace de proveedores")): return "RESTRICTED"
    return "UNKNOWN"


def _role_family(title):
    t = (title or "").lower()
    if "compras" in t or "aprovisionamiento" in t: return "PROCUREMENT"
    if "comercial" in t or "ventas" in t or "food service" in t: return "COMMERCIAL"
    if "desarrollo de negocio" in t: return "BUSINESS_DEV"
    if "director general" in t or "ceo" in t or "consejero delegado" in t or "presidente" in t: return "MANAGEMENT"
    if "financ" in t: return "FINANCE"
    return "GENERAL"


def _seniority(title):
    t = (title or "").lower()
    if any(k in t for k in ("director general", "ceo", "consejero delegado", "presidente")): return "C_LEVEL"
    if "director" in t: return "DIRECTOR"
    if any(k in t for k in ("responsable", "jefe", "manager")): return "MANAGER"
    return "UNKNOWN"


def _iso_from_es(text):
    m = DATE_RE.search(text or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def contact_from_ce(cand, case, today, profile_doc):
    """Construye el contacto SOLO con lo que tiene evidencia a nivel de dato; el resto se declara en not_found."""
    n = cand["n"]; ce = cand.get("ce") or {}
    base = {"contract_version": "opportunity_contact.v1", "company_ref": {"company_id": f"reseller_pilot:{n:02d}", "name": cand["name"], "domain": cand.get("domain")},
            "opportunity_type": case["opportunity_type"], "offering_kind": case["offering"]["kind"], "target_function": case["offering"]["target_function"],
            "contact_profile_id": None, "enrichment_state": "PENDING", "levels_reached": [], "channels": [], "people": [], "role_contacts": [], "recommended_contact": None,
            "alternatives": [], "not_recommended": [], "evidence": [], "contradictions": [], "not_found": [], "last_verified": None, "selection_blocked": None,
            "cost": {"paid_calls": 0, "credits": 0, "paid_enrichment_authorized": False, "authorization_ref": None}}
    ans = cand["feedback"]["answer"]
    if ce.get("enrichment_status") != "COMPLETADA_GRATUITA":
        base["enrichment_state"] = "AWAITING_USER_DECISION" if ans == "DUDOSA" else "PENDING"
        return SEL.build_contact(base, profile_doc, today)
    imported_levels = [0, 1]; ev_out = []; chans = []; people = []; seen = {}
    nf = list(ce.get("not_found") or [])
    for i, e in enumerate(ce.get("evidence") or [], 1):
        eid = f"ce{n}_{i}"; verified = e["verification_status"] == "VERIFIED"
        sc = SRC_CLASS[e["source_type"]] if verified else "UNVERIFIED"
        cs = "KNOWN" if verified else "UNKNOWN"
        field = e["field"]; value = e["value"]; note = e.get("note") or None
        dated = _iso_from_es(f"{e.get('note') or ''} {e.get('source') or ''} {value}") if field in PERSON_FIELDS else None
        if field in FIELD_KIND:
            kind = FIELD_KIND[field]
            if kind == "EMAIL": m = EMAIL_RE.search(value); cv = m.group(0) if m else None
            elif kind == "PHONE": m = PHONE_RE.search(value); cv = m.group(0).strip() if m else None
            else:
                m = URL_RE.search(value); cv = m.group(0) if m else (e.get("source_url") if field == "contact_form" else None)   # supplier_section: el enlace existe pero su destino no se leyó -> NO es un canal
                if cv is None and field in ("supplier_section", "supplier_page"): nf.append(f"Destino sin leer: existe «{value[:80]}» pero no se obtuvo su URL/contenido")
            if cv is None: ev_out.append(_ev(eid, "company:" + field, value, sc, cs, "UNKNOWN", e, note, today, dated)); continue
            ev_out.append(_ev(eid, f"channel:{kind.lower()}", cv, sc, cs, ST.derive(sc, cs, dated_on=dated, today=today), e, ((note + " | ") if note else "") + f"texto original: {value}" if cv != value else note, today, dated))
            key = (kind, SEL.norm_value(cv))
            if key in seen: chans[seen[key]]["evidence_ids"].append(eid); continue
            seen[key] = len(chans)
            chans.append({"channel_id": f"ch{n}_{len(chans) + 1}", "kind": kind, "value": cv, "scope": "COMPANY", "purpose": _purpose(field, f"{value} {note or ''}"), "evidence_ids": [eid], "person_id": None,
                          "outreach_access": _access(kind, f"{value} {note or ''}")})
        elif field in PERSON_FIELDS:
            name, _, title = value.partition("—")
            name = name.strip(); title = re.sub(r"\s*\(.*?\)\s*", " ", title).strip()
            strength = ST.derive(sc, cs, dated_on=dated, today=today)
            ev_out.append(_ev(eid, "person:title", value, sc, cs, strength, e, note, today, dated))
            if name and title and verified:
                people.append({"person_id": f"p{n}_{len(people) + 1}", "name": name, "title": title, "department": None, "role_family": _role_family(title), "seniority": _seniority(title),
                               "role_evidence_ids": [eid], "channel_ids": [], "employment_verified_at": dated})
        elif field == "role_only":
            ev_out.append(_ev(eid, "person:role_only", value, "UNVERIFIED", "UNKNOWN", "UNKNOWN", e, note, today, None))
            nf.append(f"Persona con nombre: solo apareció un rol sin nombre ({value})")
        else:
            ev_out.append(_ev(eid, "company:" + field, value, sc, cs, ST.derive(sc, cs, dated_on=dated, today=today), e, note, today, dated))
    # alternativas del enriquecimiento v0.1 sin evidencia a nivel de dato: NO se importan como contactos
    names = {p["name"] for p in people}
    for a in [ce.get("recommended_contact")] + list(ce.get("alternatives") or []):
        if a and a.get("contact_type") == "PERSON" and a.get("name") and a["name"] not in names:
            nf.append(f"NO IMPORTADO (sin evidencia a nivel de dato en el enriquecimiento v0.1): {a['name']} — {a.get('role')}")
    kinds_have = {c["kind"] for c in chans}
    rc = ce.get("recommended_contact") or {}
    if rc.get("recommended_channel") == "CONTACT_FORM" and "FORM" not in kinds_have:
        nf.append("NO IMPORTADO (sin evidencia a nivel de dato): el formulario de contacto citado por el enriquecimiento v0.1")
    if rc.get("recommended_channel") == "PHONE" and "PHONE" not in kinds_have:
        nf.append("NO IMPORTADO (sin evidencia a nivel de dato): el teléfono citado por el enriquecimiento v0.1")
    contradictions = [{"field": "contacto", "description": s, "state": "AMBIGUOUS"} for s in (ce.get("contradictions") or [])]
    base.update(channels=chans, people=people, evidence=ev_out, not_found=nf, contradictions=contradictions, levels_reached=imported_levels,
                enrichment_state="LEVEL1_BLOCKED" if (nf and nf[0].startswith("Todo:")) else "LEVEL1_DONE")
    c = SEL.build_contact(base, profile_doc, today)
    return c


def _ev(eid, field, value, sc, cs, strength, e, note, today, dated):
    return {"evidence_id": eid, "field": field, "value": value if cs == "KNOWN" or value is not None else None, "source_class": sc, "source_url": e.get("source_url"), "source_label": e.get("source") or None,
            "retrieved_at": e.get("retrieved_at") or today, "claim_status": cs, "evidence_strength": strength, "note": note}


# ───────────────────────── registro ─────────────────────────
def _facts(cand):
    out = {}
    for f in (cand.get("enrichment") or {}).get("fields", []): out[f["field"]] = f["value"]
    return out


def to_record(cand, case, today, profile_doc):
    n = cand["n"]; ans = cand["feedback"]["answer"]; dec = case["decision_vocabulary"]["mapping"][ans]; ef = _facts(cand); rid = f"{case['case_id']}:{n:02d}"
    ev = [{"evidence_id": f"disc{n}", "field": "discovery:appearances", "value": str(cand["signals"]["discovery_hits"]), "source_class": "PROFESSIONAL_DIRECTORY", "source_url": None,
           "source_label": cand["discovery_source"], "retrieved_at": cand["discovery_run"], "claim_status": "KNOWN", "evidence_strength": "LOW",
           "note": "Aparición en resultados de descubrimiento gratuito (listado de proveedor, una sola fuente)."}]
    emp = ef.get("Empleados (estimado Apollo)"); emp_ids = []
    if isinstance(emp, int):
        ev.append({"evidence_id": f"emp{n}", "field": "company:employees", "value": str(emp), "source_class": "PROFESSIONAL_DIRECTORY", "source_url": None, "source_label": "Apollo (T2)",
                   "retrieved_at": cand["discovery_run"], "claim_status": "ESTIMATED", "evidence_strength": "UNKNOWN", "note": cand.get("size_raw")}); emp_ids = [f"emp{n}"]
    contact = contact_from_ce(cand, case, today, profile_doc)
    ev += [e for e in contact["evidence"]]
    contact_light = contact
    ct = (cand.get("company_type") or {}).get("label")
    bm = {"Mayorista": ["distributor"], "Comercio minorista": ["retailer"], "Producción alimentaria": ["manufacturer"]}.get(ct, ["unknown"])
    ident = cand["dimensions"]["Identity"]
    q_gates = [{"gate_id": "GEO", "status": {"PASS": "PASS", "FAIL": "FAIL"}.get(cand["dimensions"]["Geography"], "UNKNOWN")}]
    reasons = [{"text": t, "signal_id": None, "claim_status": "KNOWN", "evidence_ids": [f"disc{n}"] if "búsquedas" in t else []} for t in cand["known"]]
    reasons += [{"text": t, "signal_id": None, "claim_status": "INFERRED", "evidence_ids": []} for t in cand["belief"]]
    contradictions = [{"field": "empresa", "description": f["text"], "state": "AMBIGUOUS"} for f in (cand.get("flags") or []) if f.get("kind") == "contradiction"]
    rec = {
        "schema_version": "opportunity_record.v1", "record_id": rid, "case_id": case["case_id"],
        "company": {"name": cand["name"], "domain": cand.get("domain"), "website": ef.get("Web") or ((cand.get("ce") or {}).get("company_channels") or {}).get("website"),
                    "country": "ES" if cand["dimensions"]["Geography"] == "PASS" else None,
                    "location": {"text": (cand.get("location") or {}).get("label") or cand.get("location_hint")}, "description": ef.get("Descripción corta (Apollo)"),
                    "business_model": bm, "industry_label": cand.get("apollo_industry"),
                    "size": {"employees": emp if isinstance(emp, int) else None, "employees_range": None, "claim_status": "ESTIMATED" if emp_ids else "UNKNOWN", "evidence_ids": emp_ids, "text": cand.get("size_raw")}},
        "company_identity": {"status": ident if ident in ("KNOWN_MATCH", "KNOWN_MISMATCH") else "UNKNOWN", "entity_level": "UNKNOWN",
                             "keys": {k: v for k, v in (("domain", cand.get("domain")), ("apollo_org_id", cand.get("apollo_id"))) if v}, "parent_name": None, "evidence_ids": [], "note": cand.get("technical_state_raw")},
        "opportunity_type": case["opportunity_type"],
        "offering": {"kind": case["offering"]["kind"], "target_function": case["offering"]["target_function"], "description": None},
        "why_opportunity": {"summary": cand["why_text"], "reasons": reasons, "unknowns": list(cand["missing"])},
        "signals": [{"signal_id": "discovery_appearances", "label": "Apariciones en búsquedas de descubrimiento", "kind": "CONTEXT", "state": "PRESENT", "claim_status": "KNOWN",
                     "value": cand["signals"]["discovery_hits"], "evidence_ids": [f"disc{n}"], "observed_at": cand["discovery_run"]}],
        "qualification": {"status": "NOT_ASSESSED", "reason": cand.get("technical_state_raw"), "model": None,
                          "fit_verification": "UNVERIFIED" if cand.get("formal_state") == "FIT_UNVERIFIED" else "NOT_ASSESSED", "gates": q_gates,
                          "human_review": {"decision": dec, "reviewed_by": "revisor_1", "reviewed_at": None, "comment": cand["feedback"].get("comment")}},
        "evidence": ev,
        "ranking": {"status": "NOT_SCORED", "model": None, "position": None, "tied_with": [], "layer": None, "strength_band": None, "coverage": None, "not_probability": True, "calibrated": False},
        "contact": contact_light, "contradictions": contradictions,
        "cost": {"paid_calls": 0, "credits": 0, "data_calls": 0, "paid_enrichment_authorized": False, "cost_status": "UNKNOWN",
                 "note": "El coste histórico de descubrimiento/enrichment Apollo del experimento no se atribuye por empresa. El enriquecimiento de contacto de este registro costó 0 créditos."},
        "facets": {"group": cand["group"], "zone": cand.get("search_zone") or "", "segment": list(cand.get("segments_detected") or []), "query": [q["id"] for q in cand["queries"]]},
        "provenance": {"data_origin": "HISTORICAL_IMPORT",
                       "sources": [{"ref": s["path"], "role": s["role"], "sha256": s["sha256"]} for s in case["historical_sources"]["files"] if s["id"] == "commercial_data"],
                       "spec": None, "pipeline": {"adapter": "cases/reseller_pilot/adapter.py", "importer_version": "1.0.0"},
                       "notes": ["Importación de solo lectura de artefactos históricos cerrados.", "evidence_strength derivada por reglas; no se copió el campo `confidence` del enriquecimiento v0.1."]},
        "timestamps": {"created_at": f"{today}T00:00:00Z", "observed_at": today, "last_verified": contact["last_verified"], "updated_at": None},
        "recommended_action": {"action": "HUMAN_REVIEW", "summary": "-", "next_step": None, "blocking_reasons": [], "requires_authorization": False}}
    rec["recommended_action"] = NA.derive_action(rec)
    return rec


def build_records(source_root=None, today=None):
    case = load_case(); today = today or case["reference_date"]; srcs = load_sources(case, source_root)
    profile_doc = CP.load_profiles()
    data = srcs["commercial_data"]
    # integridad del feedback oficial: coincide con la hoja canónica
    fb = srcs["official_feedback"]["totals"]
    assert fb == case["frozen_facts"]["feedback"], "el feedback oficial no coincide con las cifras congeladas del caso"
    return [to_record(c, case, today, profile_doc) for c in data["candidates"]]


def main(out=None):
    recs = build_records()
    out = out or os.path.join(HERE, "records", "opportunity_records.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f: json.dump(recs, f, ensure_ascii=False, indent=1, sort_keys=True)
    return out, recs


if __name__ == "__main__":
    p, r = main(); print("OK", p, len(r), "registros")
