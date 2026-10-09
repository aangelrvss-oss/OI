"""Resolución de términos de sector del usuario -> criterios de descubrimiento, SOLO con el mapeo curado (rules/sector_mapping/sector_mapping.<país>.json).
Nunca fabrica un código: un término sin entrada devuelve MAPPING_REQUIRED. El LLM no participa en esta decisión."""
import json
import os

from oi import paths
from oi.contracts.validator import validate
from .text import norm

EXECUTABLE_STATUS = ("VERIFIED", "USED_IN_HISTORICAL_RUN")


class MappingRequired(Exception):
    """El término no tiene entrada curada: hay que añadir (y revisar) un mapeo antes de usarlo en un descubrimiento."""
    def __init__(self, term, country):
        super().__init__(f"MAPPING_REQUIRED: '{term}' ({country})"); self.term = term; self.country = country


def load_mapping(country="ES", directory=None):
    p = os.path.join(directory or os.path.join(paths.RULES, "sector_mapping"), f"sector_mapping.{country.lower()}.json")
    if not os.path.isfile(p): return None
    with open(p, encoding="utf-8") as f: doc = json.load(f)
    errs = validate(doc, "sector_mapping.v1.schema.json")
    if errs: raise ValueError("sector_mapping inválido: " + "; ".join(f"{e['path']}: {e['message']}" for e in errs[:5]))
    return doc


def classification_status(cl):
    """VERIFIED solo si TODOS los códigos están verificados en fuente oficial; sin códigos o con alguno no verificado => UNVERIFIED. Un supuesto nunca se presenta como hecho."""
    codes = (cl.get("cnae_2009") or []) + (cl.get("naics_2022") or [])
    if not codes: return "UNVERIFIED"
    v = [c.get("verification") for c in codes]
    if all(x == "VERIFIED_OFFICIAL" for x in v): return "VERIFIED"
    return "UNVERIFIED"


def resolve(term, country="ES", doc=None):
    """-> {status: MAPPED|MAPPING_REQUIRED, ...}. MAPPED incluye entry_id, canonical_sector, classification, provider_query, confidence, evidence, review y
    `provider_query_executable` (False si la consulta no se ha probado: el plan debe tratarla como hipótesis, no como hecho)."""
    doc = doc if doc is not None else load_mapping(country)
    if doc is None: return {"status": "MAPPING_REQUIRED", "term": term, "country": country, "reason": f"no existe sector_mapping.{country.lower()}.json"}
    t = norm(term)
    for e in doc["entries"]:
        if any(norm(u) == t for u in e["user_terms"]):
            pq = e["provider_query"]
            cls = classification_status(e["classification"]); tax = e["provider_taxonomy"]
            chain = {"user_term": term, "canonical_sector": e["canonical_sector"], "classification_status": cls,
                     "provider_taxonomy": {k: {"status": v["status"], "industry_labels": v.get("industry_labels", []), "industry_labels_verification": v.get("industry_labels_verification", "UNVERIFIED")} for k, v in tax.items()},
                     "provider_query": {k: {"status": v["status"], "params": v["params"]} for k, v in pq.items()},
                     "executable_against_real_provider": all(v["status"] in EXECUTABLE_STATUS for v in pq.values()) and cls != "UNVERIFIED"}
            return {"status": "MAPPED", "term": term, "country": country, "entry_id": e["entry_id"], "canonical_sector": e["canonical_sector"], "classification": e["classification"], "classification_status": cls, "chain": chain,
                    "business_model_hint": e["business_model_hint"], "provider_query": pq, "provider_taxonomy": e["provider_taxonomy"], "confidence": e["confidence"], "evidence": e["evidence"], "review": e["review"],
                    "provider_query_executable": all(v["status"] in EXECUTABLE_STATUS for v in pq.values()),
                    "needs_human_review": e["review"]["status"] != "REVIEWED"}
    return {"status": "MAPPING_REQUIRED", "term": term, "country": country, "reason": "término sin entrada curada: no se convierte a la fuerza (ni el LLM crea códigos)"}


def require(term, country="ES", doc=None):
    r = resolve(term, country, doc)
    if r["status"] != "MAPPED": raise MappingRequired(term, country)
    return r
