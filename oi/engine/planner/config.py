"""Carga y AUTOCOMPROBACIÓN de la configuración versionada (planner_data/ v2)."""
import fnmatch
import json
import os
from oi.engine.spec.registry import Registry

from oi import paths
PHASE_ORDER = ["DISCOVERY", "NORMALIZATION", "ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS", "CANDIDATE_RELEVANCE", "ENRICHMENT", "VERIFIED_GATES",
               "EVIDENCE", "SCORING", "PRIORITIZATION", "DECISION_MAKER_ENRICHMENT", "RESULTS"]
TOOL_PHASES = ["DISCOVERY", "ENRICHMENT", "EVIDENCE", "DECISION_MAKER_ENRICHMENT"]
TOOL_STATUSES = ("available", "unavailable_on_current_plan", "deferred", "unaudited")
CAP_STATUSES = ("VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "UNAVAILABLE", "CONTRADICTED")


class ConfigError(Exception):
    pass


def _load(n):
    with open(paths.data_file(n), encoding="utf-8") as f:
        return json.load(f)


class PlannerConfig:
    def __init__(self, registry=None):
        self.registry = registry or Registry()
        self.params = _load("planning_params.v2.json")
        self.routing_doc = _load("field_routing.v2.json")
        self.routing = self.routing_doc["routing"]
        self.ruleset = _load("ruleset.v2.json")
        self.catalog = _load("tool_catalog.v2.json")
        self.tools = self.catalog["tools"]
        self.matrix_doc = _load("capability_matrix.v1.json")
        self.caps = {c["id"]: c for c in self.matrix_doc["capabilities"]}
        self.schema = _load("research_plan.v2.schema.json")
        self.versions = {"ruleset_version": self.ruleset["ruleset_version"], "planning_params_version": self.params["params_version"],
                         "routing_version": self.routing_doc["routing_version"], "tool_catalog_version": self.catalog["tools_version"],
                         "capability_matrix_version": self.matrix_doc["matrix_version"]}
        self._selfcheck()

    # --- consultas
    def weights(self):
        return {k: v for k, v in self.params["allocation"].items() if k != "status"}

    def tool_status(self, tool):
        return self.tools[tool]["status"]

    def observed_credits(self, tool):
        """Créditos por unidad SOLO si el coste está observado y la herramienta está disponible; None si no se conoce. Nunca se inventa."""
        t = self.tools[tool]; c = t["observed_cost"]
        if t["status"] == "available" and c["cost_status"] == "observed" and c["credits_per_unit"] is not None:
            return c["credits_per_unit"]
        return None

    def is_forbidden(self, operation):
        return next((f["id"] for f in self.catalog["forbidden"] if any(fnmatch.fnmatch(operation, p) for p in f["match"])), None)

    # --- autocomprobación
    def _selfcheck(self):
        pseudo = set(self.routing_doc["pseudo_fields"])
        for name, f in self.registry.fields.items():
            if name not in self.routing:
                raise ConfigError(f"campo del registry sin enrutado: {name}")
            r = self.routing[name]
            if f["status"] == "active":
                if r["stage"] not in self.routing_doc["stages"]:
                    raise ConfigError(f"campo activo con etapa inválida: {name}")
                if r["pre_enrichment_gate"]:
                    raise ConfigError(f"{name}: ningún criterio del registry puede ser pre-enrichment gate (solo exclusiones deterministas)")
                if r["evaluator"] is None or r["capability_ref"] not in self.caps:
                    raise ConfigError(f"{name}: sin evaluador o capability_ref inexistente")
                if r["stage"] == "enrichment" and not r["enrichment_available"]:
                    raise ConfigError(f"{name}: etapa enrichment pero enrichment_available=false")
        for name in self.routing:
            if name not in self.registry.fields and name not in pseudo:
                raise ConfigError(f"enrutado de campo inexistente: {name}")
        for c in self.caps.values():
            if c["status"] not in CAP_STATUSES or c["tool"] not in self.tools:
                raise ConfigError(f"capability inválida: {c['id']}")
        for t, d in self.tools.items():
            if d["status"] not in TOOL_STATUSES:
                raise ConfigError(f"estado de herramienta inválido: {t}")
            if d["status"] == "unavailable_on_current_plan" and (not d.get("fallback") or d["fallback"] not in self.tools or not d.get("keep_in_architecture")):
                raise ConfigError(f"{t}: herramienta no disponible sin fallback o sin keep_in_architecture")
            for cid in d.get("relies_on", []):
                if cid not in self.caps: raise ConfigError(f"{t}: relies_on inexistente {cid}")
            if d["observed_cost"]["cost_status"] == "observed" and d["observed_cost"]["credits_per_unit"] is None:
                raise ConfigError(f"{t}: coste 'observed' sin valor")
        ids = [r["rule_id"] for k in ("phase_rules", "tool_rules") for r in self.ruleset[k]]
        if len(ids) != len(set(ids)):
            raise ConfigError("rule_id duplicado")
        by_id = {r["rule_id"]: r for r in self.ruleset["tool_rules"]}
        for r in self.ruleset["tool_rules"]:
            if r["tool"] not in self.tools:
                raise ConfigError(f"{r['rule_id']}: herramienta fuera del catálogo")
            if r.get("fallback_of") and r["fallback_of"] not in by_id:
                raise ConfigError(f"{r['rule_id']}: fallback_of inexistente")
            for op in r["operations"]:
                if self.is_forbidden(op):
                    raise ConfigError(f"{r['rule_id']}: operación prohibida {op}")
                if op not in self.tools[r["tool"]]["operations"]:
                    raise ConfigError(f"{r['rule_id']}: operación {op} no pertenece a {r['tool']}")
        w = self.weights()
        if abs(sum(w.values()) + self.params["reserve_pct_default"] / 100 - 1.0) > 1e-9:
            raise ConfigError("allocation + reserva por defecto debe sumar 1.0")
        if not (0 < self.params["thresholds"]["soft"] < self.params["thresholds"]["hard"] <= 1):
            raise ConfigError("umbrales soft/hard inválidos")
        if self.ruleset["hard_relaxation"]["automatic"] is not False:
            raise ConfigError("la relajación de gates HARD no puede ser automática")
        for pname in ("enrichment_multiplier_without_history", "pre_filter_survival_without_history"):
            if self.params["pool_policy"][pname]["status"] != "calibration_required":
                raise ConfigError(f"{pname} debe permanecer marcado calibration_required hasta que haya histórico")
        bs = self.params["enrichment"]["batch_size"]
        if bs["max_verified"] != "unknown" and self.caps["CAP-BULK-BATCH-10"]["status"] != "VERIFIED":
            raise ConfigError("max_verified del batch declarado sin capacidad VERIFIED")
        if bs["status"] != "provisional" and self.caps["CAP-BULK-BATCH-10"]["status"] != "VERIFIED":
            raise ConfigError("batch_size marcado como verificado sin capacidad VERIFIED")
        for r in self.ruleset["tool_rules"]:
            if r["phase"] == "EVIDENCE" and r["tool"] not in self.params["evidence"]["calls_per_company"]:
                raise ConfigError(f"{r['rule_id']}: herramienta de EVIDENCE sin calls_per_company")
