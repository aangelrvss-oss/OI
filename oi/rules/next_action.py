"""Deriva recommended_action de un opportunity_record a partir de rules/next_action.v1.json. Puro: sin reloj, red ni gasto."""
import json
import os

from oi import paths

PATH = os.path.join(paths.RULES, "next_action.v1.json")
_RULES = None


def rules():
    global _RULES
    if _RULES is None:
        with open(PATH, encoding="utf-8") as f: _RULES = json.load(f)
    return _RULES


def facts_of(record):
    """Hechos que evalúan las reglas, tomados SOLO del registro."""
    q = record["qualification"]; c = record["contact"]; rc = c.get("recommended_contact")
    hr = (q.get("human_review") or {}).get("decision")
    return {"human": hr, "qualification": q["status"], "fit": q["fit_verification"], "identity": record["company_identity"]["status"],
            "enrichment_state": c["enrichment_state"], "has_recommendation": rc is not None, "grade": rc["contact_quality"]["grade"] if rc else None,
            "selection_blocked": c.get("selection_blocked") is not None, "unresolved_contradictions": bool(c.get("contradictions") or record.get("contradictions")),
            "org_enrichment": "COMPLETED" if any(e.get("evidence_origin") == "APOLLO_ENRICHMENT" for e in record.get("evidence", [])) else "NONE", "evidence_gap": bool(gaps_of(record))}


def gaps_of(record):
    """Puertas BLOQUEANTES sin PASS+KNOWN (p. ej. identidad sin confirmar, conflicto, desconocido). Hecho calculado solo del registro."""
    return [g["gate_id"] for g in record["qualification"].get("gates", []) if g.get("blocking") and not (g["status"] == "PASS" and g["evidence_status"] == "KNOWN")]


def derive_action(record):
    f = facts_of(record); R = rules()
    for r in R["rules"]:
        if all(f.get(k) in v for k, v in r["when"].items()):
            t = dict(r["then"]); rc = record["contact"].get("recommended_contact")
            fit_note = {"VERIFIED": "está verificado", "UNVERIFIED": "NO está verificado por el sistema (decisión humana)", "NOT_ASSESSED": "no ha sido evaluado"}[f["fit"]]
            fmt = {"fit_note": fit_note, "contact_next_action": rc["next_action"] if rc else "", "gaps": ", ".join(gaps_of(record)) or "—"}
            t["summary"] = t["summary"].format(**fmt); t["next_step"] = t["next_step"].format(**fmt) if t["next_step"] else None
            t["rule_id"] = r["id"]
            return t
    t = dict(R["default"]); t["rule_id"] = "DEFAULT"; return t
