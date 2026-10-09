"""Asignación de evidence_strength por reglas (rules/evidence_strength.v1.json). La fuerza describe la fiabilidad del DATO, no la utilidad comercial."""
import datetime
import json
import os

from oi import paths

PATH = os.path.join(paths.RULES, "evidence_strength.v1.json")
_R = None


def rules():
    global _R
    if _R is None:
        with open(PATH, encoding="utf-8") as f: _R = json.load(f)
    return _R


def derive(source_class, claim_status, *, dated_on=None, today=None, corroborated=False):
    """dated_on: fecha (ISO) de la publicación/lectura del dato si se conoce. Sin fecha, no cuenta como 'fechado y reciente'."""
    R = rules(); within = False
    if dated_on and today:
        within = 0 <= (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(dated_on)).days <= R["freshness_days_for_dated_sector_source"]
    facts = {"source_class": source_class, "claim_status": claim_status, "dated_within_freshness": within, "corroborated": bool(corroborated)}
    for r in R["rules"]:
        if all(facts.get(k) in v for k, v in r["when"].items()): return r["strength"]
    return R["default"]
