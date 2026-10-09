"""Migración Search Spec v1 -> v2. NO modifica el spec v1 ni el store: devuelve un documento NUEVO (derivado, con su propio hash) o lo añade como nueva versión.

Qué hace: schema_version 1.0 -> 2.0; añade intent.offering.kind / target_function, intent.business_model, intent.entity_level con valores
"desconocido" (UNKNOWN / ['unknown']) y lo deja trazado en assumptions + traceability.migrated_from.
Qué NO hace: no inventa la clasificación de la oferta. Un spec v1 migrado queda con offering.kind = UNKNOWN y una pregunta de confirmación abierta:
el sistema podrá buscar empresas pero NO recomendará a quién contactar hasta que alguien clasifique la oferta.
"""
import copy

from . import validation as V
from . import clarification as C
from .registry import Registry
from . import mini_jsonschema
from .versioning import canonical_hash


class MigrationError(Exception):
    pass


def migrate_v1_to_v2(spec_v1, registry_v2=None, clock=None):
    reg = registry_v2 or Registry(schema_version="2.0")
    if reg.schema_version != "2.0": raise MigrationError("se necesita un Registry con schema 2.0")
    if (spec_v1.get("versions") or {}).get("schema_version") != "1.0": raise MigrationError("el origen no es un Search Spec v1.0")
    if spec_v1["control"].get("content_hash") != canonical_hash(spec_v1): raise MigrationError("el spec v1 no supera su content_hash: no se migra un documento alterado")
    s = copy.deepcopy(spec_v1); src_ctl = spec_v1["control"]
    s["versions"]["schema_version"] = "2.0"
    off = dict(s["intent"].get("offering") or {"description": None, "problem_solved": None})
    has_desc = bool((off.get("description") or "").strip())
    off["kind"] = "UNKNOWN" if has_desc else None
    off["target_function"] = "UNKNOWN"
    s["intent"]["offering"] = off
    s["intent"]["business_model"] = ["unknown"]; s["intent"]["entity_level"] = "UNKNOWN"
    note = "Migrado desde Search Spec v1: el contrato v1 no recogía este dato; no se infiere."
    s["traceability"]["assumptions"] = list(s["traceability"].get("assumptions", [])) + [
        {"field": "intent.offering.kind", "value": off["kind"], "reason": note, "basis": "engine_default"},
        {"field": "intent.offering.target_function", "value": "UNKNOWN", "reason": note, "basis": "engine_default"},
        {"field": "intent.business_model", "value": ["unknown"], "reason": note, "basis": "engine_default"},
        {"field": "intent.entity_level", "value": "UNKNOWN", "reason": note, "basis": "engine_default"}]
    s["traceability"]["migrated_from"] = {"schema_version": "1.0", "spec_id": src_ctl["spec_id"], "spec_version": src_ctl["spec_version"], "content_hash": src_ctl["content_hash"]}
    if has_desc and not any(q["id"] == "Q-OFFERING-KIND-UNKNOWN" for q in s["traceability"].get("open_questions", [])):
        s["traceability"]["open_questions"] = list(s["traceability"].get("open_questions", [])) + [C.offering_kind_unknown()]
    s["control"] = {**s["control"], "created_at": (clock or (lambda: src_ctl["created_at"]))(), "content_hash": None}
    s["control"]["content_hash"] = canonical_hash(s)
    errs = mini_jsonschema.validate(s, reg.schema)
    if errs: raise MigrationError("el spec migrado no valida contra v2: " + "; ".join(f"{e['path']}: {e['message']}" for e in errs[:5]))
    return s


def migrate_into_store(engine_v2, spec_id, version):
    """Añade la migración como NUEVA versión del mismo spec (la original queda intacta). Devuelve el spec guardado."""
    src = engine_v2.store.get(spec_id, version)
    if src is None: raise MigrationError(f"no existe {spec_id} v{version}")
    new = migrate_v1_to_v2(src, engine_v2.reg, engine_v2.clock)
    new["control"]["spec_version"] = engine_v2.store.latest_version(spec_id) + 1
    new["control"]["parent_spec_version"] = version
    new["control"]["content_hash"] = None; new["control"]["content_hash"] = canonical_hash(new)
    engine_v2.store.append(new)
    return new
