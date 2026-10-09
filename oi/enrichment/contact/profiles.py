"""Carga y selección de perfiles de contacto (rules/contact_profiles.v1.json). Los perfiles son DATOS; aquí solo se cargan, validan y eligen."""
import json
import os

from oi import paths
from oi.contracts.validator import validate

PROFILES_PATH = os.path.join(paths.RULES, "contact_profiles.v1.json")


class ProfileError(Exception):
    """code: OFFERING_KIND_REQUIRED | NO_PROFILE_FOR_COMBINATION | PROFILES_INVALID | AMBIGUOUS_PROFILE"""
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}"); self.code = code; self.message = message


def load_profiles(path=None):
    with open(path or PROFILES_PATH, encoding="utf-8") as f: doc = json.load(f)
    errs = validate(doc, "contact_profiles.v1.schema.json")
    if errs: raise ProfileError("PROFILES_INVALID", "; ".join(f"{e['path']}: {e['message']}" for e in errs[:5]))
    for pid, p in doc["profiles"].items():
        ids = [r["id"] for r in p["ladder"]]
        if len(ids) != len(set(ids)): raise ProfileError("PROFILES_INVALID", f"{pid}: ids de peldaño duplicados")
        uses_fn = any("$FUNCTION" in r["match"].get("role_family", []) for r in p["ladder"])
        if uses_fn and not (p.get("function_to_role_family") and p.get("unknown_function_role_families")):
            raise ProfileError("PROFILES_INVALID", f"{pid}: usa $FUNCTION sin function_to_role_family/unknown_function_role_families")
    return doc


def select_profile(doc, opportunity_type, offering_kind):
    """(opportunity_type, offering.kind) -> id de perfil. NUNCA adivina: sin kind conocido, error explícito."""
    if offering_kind in (None, "UNKNOWN"):
        raise ProfileError("OFFERING_KIND_REQUIRED", "offering.kind es obligatorio (y distinto de UNKNOWN) para elegir a quién contactar: no se asume qué se vende.")
    hits = [pid for pid, p in doc["profiles"].items() if opportunity_type in p["applies_to"]["opportunity_type"] and offering_kind in p["applies_to"]["offering_kind"]]
    if not hits: raise ProfileError("NO_PROFILE_FOR_COMBINATION", f"no hay perfil de contacto para ({opportunity_type}, {offering_kind})")
    if len(hits) > 1: raise ProfileError("AMBIGUOUS_PROFILE", f"varios perfiles para ({opportunity_type}, {offering_kind}): {hits}")
    return hits[0]
