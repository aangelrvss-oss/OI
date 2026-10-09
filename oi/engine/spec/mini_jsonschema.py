"""Validador JSON Schema mínimo (stdlib). Cubre SOLO las keywords usadas por search_spec.v1.schema.json:
type, enum, const, required, properties, additionalProperties(schema), items, minItems, minLength,
minimum, maximum, pattern, $ref (#/$defs/..), allOf, if/then. 'format' se ignora.
El schema sigue siendo JSON Schema 2020-12 estándar: puede validarse con `jsonschema` donde exista."""
import re

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(instance, schema, root=None, path="$"):
    """Devuelve lista de errores [{path, message}]. Lista vacía = válido."""
    root = root or schema
    errs = []

    def err(msg):
        errs.append({"path": path, "message": msg})

    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(instance, node, root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            err(f"tipo esperado {types}, recibido {type(instance).__name__}")
            return errs
    if "const" in schema and instance != schema["const"]:
        err(f"debe ser {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        err(f"valor {instance!r} fuera de enum {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            err(f"longitud mínima {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            err(f"no cumple el patrón {schema['pattern']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            err(f"mínimo {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            err(f"máximo {schema['maximum']}")
    if isinstance(instance, dict):
        for r in schema.get("required", []):
            if r not in instance:
                errs.append({"path": f"{path}.{r}", "message": "campo obligatorio ausente"})
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in instance:
                errs += validate(instance[k], sub, root, f"{path}.{k}")
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            for k, v in instance.items():
                if k not in props:
                    errs += validate(v, ap, root, f"{path}.{k}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            err(f"mínimo {schema['minItems']} elementos")
        if "items" in schema:
            for i, it in enumerate(instance):
                errs += validate(it, schema["items"], root, f"{path}[{i}]")
    for sub in schema.get("allOf", []):
        errs += validate(instance, sub, root, path)
    if "if" in schema:
        if not validate(instance, schema["if"], root, path):
            if "then" in schema:
                errs += validate(instance, schema["then"], root, path)
    return errs
