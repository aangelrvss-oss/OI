"""Única fuente de rutas del producto. Ningún módulo calcula rutas con __file__ por su cuenta.
Los datos versionados NO viven junto al código: contratos en oi/contracts, reglas en rules/."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # raíz del producto
CONTRACTS = os.path.join(ROOT, "oi", "contracts")
RULES = os.path.join(ROOT, "rules")
SPEC_RULES = os.path.join(RULES, "spec")
PLANNER_RULES = os.path.join(RULES, "planner")
CASES = os.path.join(ROOT, "cases")
CONFIG = os.path.join(ROOT, "config")
ARCHIVE = os.path.join(ROOT, "archive")

# Históricos de solo lectura (pilotos cerrados, D010 OFFICIAL, ledgers…). Se crearon en /mnt/user-data/outputs y los ficheros históricos
# (p. ej. el case.json de un caso cerrado) conservan esa ruta literal; aquí solo se re-enraíza, nunca se reescribe el histórico.
# Prioridad: $OI_HISTORY_ROOT > <raíz>/history (repositorio) > /mnt/user-data/outputs (entorno original).
LEGACY_OUTPUTS = "/mnt/user-data/outputs"
_REPO_HISTORY = os.path.join(ROOT, "history")
HISTORY = os.environ.get("OI_HISTORY_ROOT") or (_REPO_HISTORY if os.path.isdir(_REPO_HISTORY) else LEGACY_OUTPUTS)


def history_path(path):
    """Re-enraíza una ruta absoluta del entorno original (/mnt/user-data/outputs/…) bajo HISTORY. Otras rutas no cambian."""
    if path == LEGACY_OUTPUTS or path.startswith(LEGACY_OUTPUTS + "/"):
        rel = path[len(LEGACY_OUTPUTS):].lstrip("/")
        return os.path.join(HISTORY, rel) if rel else HISTORY
    return path


_SEARCH = (CONTRACTS, SPEC_RULES, PLANNER_RULES)
# Ficheros que forman la identidad de datos del Spec Engine (mismos nombres que en el motor original -> hashes comparables)
SPEC_DATA_FILES = ("field_registry.v1.json", "geo_catalog.v1.json", "relative_terms.v1.json", "search_spec.v1.schema.json")


def data_file(name):
    """Resuelve un fichero de datos versionado por nombre. Falla ruidosamente si no existe o está duplicado."""
    hits = [os.path.join(d, name) for d in _SEARCH if os.path.isfile(os.path.join(d, name))]
    if len(hits) != 1:
        raise FileNotFoundError(f"data_file({name!r}): {len(hits)} coincidencias en {_SEARCH}")
    return hits[0]
