"""BLOQUE 5 · Clases de fuente (qué hay que saber) -> proveedores (dónde mirar). El spec nunca nombra un proveedor."""
import json, os, re
PROVIDER_RE = re.compile(r"^(apollo|firecrawl|openrush|gmail|bigquery|sheets|hubspot|salesforce)[._]", re.I)
from oi import paths
_P = paths.data_file("source_classes.v1.json")


def load(path=None):
    return json.load(open(path or _P, encoding="utf-8"))


def providers_for(source_class, tool_status, disabled_tools=(), mapping=None):
    import fnmatch
    m = (mapping or load())["classes"].get(source_class)
    if m is None: return [{"tool": None, "status": "unknown_source_class", "allowed": False, "reason": "clase de fuente no definida"}]
    out = [{"tool": t, "status": tool_status.get(t, "not_in_catalog"), "allowed": not any(fnmatch.fnmatch(t, p) for p in disabled_tools)} for t in m["providers"]]
    for o in out: o["usable"] = o["allowed"] and o["status"] == "available"
    return out or [{"tool": None, "status": "no_provider_registered", "allowed": False, "usable": False, "reason": "ninguna herramienta registrada para esta clase"}]


def requirements(spec, tool_status, disabled_tools=(), mapping=None):
    out = []
    for c in spec.get("criteria", []):
        classes = list(((c.get("evidence") or {}).get("preferred_sources")) or [])
        if not classes: continue
        prov = {k: providers_for(k, tool_status, disabled_tools, mapping) for k in classes}
        out.append({"criterion_id": c["id"], "field": c["field"], "source_classes": classes, "providers": prov, "resolvable": any(p.get("usable") for ps in prov.values() for p in ps)})
    return out
