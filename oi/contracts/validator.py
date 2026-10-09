"""Validador JSON Schema (solo stdlib) para los contratos del producto.

Cubre las keywords que usan los esquemas de oi/contracts: type, enum, const, required, properties, additionalProperties (bool o esquema),
patternProperties, items, minItems, maxItems, uniqueItems, minLength, maxLength, minimum, maximum, exclusiveMinimum, pattern, minProperties,
$ref (local '#/$defs/x' o entre ficheros 'otro.schema.json#/$defs/x'), allOf, anyOf, oneOf, not, if/then/else y 'format' (date, date-time, uri básico).
Una keyword NO soportada hace fallar el esquema (UnsupportedKeyword): un esquema nunca valida "en silencio" menos de lo que declara.

Los esquemas son JSON Schema 2020-12 estándar: en los tests se contrastan además con la librería `jsonschema` cuando está instalada.
Uso:  errors = validate(instance, "opportunity_record.v1.schema.json")   # [] = válido
"""
import json
import os
import re

from oi import paths

SUPPORTED = {"$schema", "$id", "$defs", "$ref", "$comment", "title", "description", "default", "examples", "type", "enum", "const", "required", "properties",
             "additionalProperties", "patternProperties", "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "minimum", "maximum",
             "exclusiveMinimum", "pattern", "minProperties", "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "format"}
_FMT = {"date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "date-time": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"),
        "uri": re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\s]+$")}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


class UnsupportedKeyword(Exception):
    pass


class SchemaStore:
    """Carga perezosa de los esquemas de oi/contracts por nombre de fichero."""
    def __init__(self, directory=None):
        self.dir = directory or paths.CONTRACTS; self._c = {}

    def get(self, name):
        if name not in self._c:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f: self._c[name] = json.load(f)
        return self._c[name]


STORE = SchemaStore()


def _is_type(v, t):
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if t == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def _eq(a, b):
    """Igualdad JSON estricta (True != 1)."""
    if isinstance(a, bool) or isinstance(b, bool): return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _resolve(ref, root, doc_name):
    name, _, frag = ref.partition("#")
    doc = STORE.get(name) if name else root
    cur_name = name or doc_name
    node = doc
    for part in [p for p in frag.split("/") if p]:
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node, doc, cur_name


def _check_keywords(schema, where):
    bad = set(schema) - SUPPORTED
    if bad: raise UnsupportedKeyword(f"{where}: keyword(s) no soportadas {sorted(bad)}")


def validate(instance, schema, root=None, path="$", doc_name=None, _depth=0):
    """Devuelve [{path, message}]; lista vacía = válido. `schema` puede ser un dict o el nombre de un fichero de oi/contracts."""
    if isinstance(schema, str):
        doc_name = schema; schema = STORE.get(schema)
    root = root if root is not None else schema
    if _depth > 60: raise RecursionError("esquema demasiado profundo (¿$ref circular?)")
    if schema is True: return []
    if schema is False: return [{"path": path, "message": "esquema false: nada es válido"}]
    _check_keywords(schema, path)
    errs = []
    err = lambda m: errs.append({"path": path, "message": m})
    sub = lambda inst, sch, p=path: validate(inst, sch, root, p, doc_name, _depth + 1)
    if "$ref" in schema:
        node, ndoc, nname = _resolve(schema["$ref"], root, doc_name)
        return validate(instance, node, ndoc, path, nname, _depth + 1)
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in ts):
            return [{"path": path, "message": f"tipo esperado {ts}, recibido {type(instance).__name__}"}]
    if "const" in schema and not _eq(instance, schema["const"]): err(f"debe ser {schema['const']!r}")
    if "enum" in schema and not any(_eq(instance, e) for e in schema["enum"]): err(f"valor {instance!r} fuera de enum {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]: err(f"longitud mínima {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]: err(f"longitud máxima {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance): err(f"no cumple el patrón {schema['pattern']}")
        if "format" in schema and schema["format"] in _FMT and not _FMT[schema["format"]].match(instance): err(f"formato {schema['format']} inválido: {instance!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]: err(f"mínimo {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]: err(f"máximo {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]: err(f"debe ser > {schema['exclusiveMinimum']}")
    if isinstance(instance, dict):
        for r in schema.get("required", []):
            if r not in instance: errs.append({"path": f"{path}.{r}", "message": "campo obligatorio ausente"})
        if "minProperties" in schema and len(instance) < schema["minProperties"]: err(f"mínimo {schema['minProperties']} propiedades")
        props = schema.get("properties", {}); pp = schema.get("patternProperties", {})
        for k, v in instance.items():
            matched = False
            if k in props: errs += sub(v, props[k], f"{path}.{k}"); matched = True
            for pat, psch in pp.items():
                if re.search(pat, k): errs += sub(v, psch, f"{path}.{k}"); matched = True
            if not matched and "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False: errs.append({"path": f"{path}.{k}", "message": "propiedad no permitida"})
                elif ap is not True: errs += sub(v, ap, f"{path}.{k}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]: err(f"mínimo {schema['minItems']} elementos")
        if "maxItems" in schema and len(instance) > schema["maxItems"]: err(f"máximo {schema['maxItems']} elementos")
        if schema.get("uniqueItems"):
            seen = [json.dumps(i, sort_keys=True) for i in instance]
            if len(set(seen)) != len(seen): err("elementos duplicados")
        if "items" in schema:
            for i, it in enumerate(instance): errs += sub(it, schema["items"], f"{path}[{i}]")
    for s in schema.get("allOf", []): errs += sub(instance, s)
    if "anyOf" in schema and not any(not sub(instance, s) for s in schema["anyOf"]): err("no cumple ninguna alternativa de anyOf")
    if "oneOf" in schema:
        n = sum(1 for s in schema["oneOf"] if not sub(instance, s))
        if n != 1: err(f"debe cumplir exactamente una alternativa de oneOf (cumple {n})")
    if "not" in schema and not sub(instance, schema["not"]): err("cumple un esquema prohibido (not)")
    if "if" in schema:
        if not sub(instance, schema["if"]):
            if "then" in schema: errs += sub(instance, schema["then"])
        elif "else" in schema: errs += sub(instance, schema["else"])
    return errs
