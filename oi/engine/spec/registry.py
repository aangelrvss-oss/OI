"""Carga y AUTOCOMPROBACIÓN de los datos versionados (schema, registry, léxico, catálogo geo)."""
import hashlib
import json
import os

from oi import paths


def _load(name):
    with open(paths.data_file(name), encoding="utf-8") as f:
        return json.load(f)


class RegistryError(Exception):
    pass


SCHEMA_FILES = {"1.0": "search_spec.v1.schema.json", "2.0": "search_spec.v2.schema.json"}
LATEST_SCHEMA = "2.0"
DEFAULT_SCHEMA = "1.0"      # compatibilidad: quien no pide versión sigue obteniendo v1 (ver docs/SEARCH_SPEC_V2_COMPATIBILITY.md). El producto (oi.run, casos) pide LATEST_SCHEMA.


class Registry:
    def __init__(self, data_dir=None, schema_version=DEFAULT_SCHEMA):
        if schema_version not in SCHEMA_FILES: raise RegistryError(f"schema_version desconocida: {schema_version}")
        self.schema_version = schema_version
        self.schemas = {v: _load(f) for v, f in SCHEMA_FILES.items()}
        self.schema = self.schemas[schema_version]
        raw = _load("field_registry.v1.json")
        self.version = raw["registry_version"]
        self.policy = raw["policy"]
        self.term_defaults = raw["term_defaults"]
        self.fields = {f["field"]: f for f in raw["fields"]}
        lex = _load("relative_terms.v1.json")
        self.lexicon_version = lex["lexicon_version"]
        self.relative_terms = {t["id"]: t for t in lex["relative_terms"]}
        self.vague_quantifiers = lex["vague_quantifiers"]
        self.geo = _load("geo_catalog.v1.json")
        self.geo_version = self.geo["geo_catalog_version"]
        self._selfcheck()

    def _selfcheck(self):
        tcat = self.policy["type_operator_catalog"]
        for name, f in self.fields.items():
            if f["type"] not in tcat:
                raise RegistryError(f"{name}: tipo desconocido {f['type']}")
            bad = set(f["allowed_operators"]) - set(tcat[f["type"]])
            if bad:
                raise RegistryError(f"{name}: operadores {bad} incompatibles con el tipo {f['type']}")
            if f["registry_version"] != self.version:
                raise RegistryError(f"{name}: registry_version inconsistente")
            for k in ("field", "type", "allowed_operators", "allowed_dimensions", "allowed_entity_types",
                      "evidence_compatible", "status", "registry_version"):
                if k not in f:
                    raise RegistryError(f"{name}: falta {k}")
            de = f.get("default_evidence")
            if de and not set(de["allowed_evidence_types"]) <= set(f["evidence_compatible"]):
                raise RegistryError(f"{name}: default_evidence fuera de evidence_compatible")
        for t in self.relative_terms.values():
            for m in t["menu"]:
                if m["field"] not in self.fields:
                    raise RegistryError(f"léxico {t['id']}: menú apunta a campo inexistente {m['field']}")

    def schema_for(self, version):
        """Esquema con el que se valida un spec ya emitido (v1 y v2 conviven: los artefactos históricos siguen siendo v1)."""
        if version not in self.schemas: raise RegistryError(f"schema_version desconocida: {version}")
        return self.schemas[version]

    def data_hashes(self):
        out = {}
        names = list(paths.SPEC_DATA_FILES) + ([SCHEMA_FILES["2.0"]] if self.schema_version == "2.0" else [])
        for n in names:
            with open(paths.data_file(n), "rb") as f:
                out[n] = hashlib.sha256(f.read()).hexdigest()[:16]
        return out
