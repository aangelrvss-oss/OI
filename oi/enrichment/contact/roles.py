"""Clasificación determinista de cargos -> (role_family, seniority) con rules/role_taxonomy.v1.json. Sin LLM, sin adivinar."""
import json
import os

from oi import paths
from oi.engine.spec.text import norm, contains_phrase

_T = None


def taxonomy():
    global _T
    if _T is None:
        with open(os.path.join(paths.RULES, "role_taxonomy.v1.json"), encoding="utf-8") as f: _T = json.load(f)
    return _T


def classify(title):
    """-> {"role_family", "seniority", "matched": {...}}. Un título vacío o desconocido da GENERAL/UNKNOWN."""
    T = taxonomy(); t = norm(title); fam = sen = None; matched = {}
    for r in T["role_family_rules"]:
        hit = next((x for x in r["terms"] if contains_phrase(t, norm(x))), None)
        if hit: fam = r["role_family"]; matched["role_family"] = hit; break
    for r in T["seniority_rules"]:
        hit = next((x for x in r["terms"] if contains_phrase(t, norm(x))), None)
        if hit: sen = r["seniority"]; matched["seniority"] = hit; break
    fb = T["fallback"]
    return {"role_family": fam or fb["role_family"], "seniority": sen or fb["seniority"], "matched": matched}
