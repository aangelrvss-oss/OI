"""Asociación petición -> caso configurado. Por la FORMA del Search Spec interpretado (tipo de oportunidad, sector mapeado, país), nunca por el texto literal."""
import json
import os

from oi import paths
from oi.engine.spec import sector_mapping as SM


def _cases():
    for name in sorted(os.listdir(paths.CASES)):
        p = os.path.join(paths.CASES, name, "case.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f: yield json.load(f), os.path.join(paths.CASES, name)


def shape_of(doc):
    """Forma comparable de un borrador o spec: (opportunity_type, entry_id del sector, país). None en lo que no se pueda determinar."""
    intent = doc.get("intent") or {}; sect = next((c for c in doc.get("criteria", []) if c.get("field") == "firmographic.industry" and (c.get("source_text") or c.get("value"))), None)
    entry = None
    if sect:
        m = SM.resolve(sect.get("source_text") or sect.get("value"), "ES")
        entry = m.get("entry_id") if m["status"] == "MAPPED" else None
    inc = ((doc.get("restrictions") or {}).get("geography") or {}).get("include") or []
    country = next((g["name"] for g in inc if g.get("level") == "country"), None)
    return {"opportunity_type": intent.get("opportunity_type"), "sector_entry_id": entry, "country": country}


def find_case(doc, case_id=None):
    """-> (case, dir) o (None, None). Con case_id explícito se devuelve ese caso si existe."""
    shape = shape_of(doc)
    for c, d in _cases():
        if case_id:
            if c["case_id"] == case_id: return c, d
            continue
        b = c.get("binding")
        if b and all(shape.get(k) == b.get(k) for k in ("opportunity_type", "sector_entry_id", "country")): return c, d
    return None, None


def load_json(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as f: return json.load(f)
