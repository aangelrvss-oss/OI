"""Ledgers del ejecutor. El ledger de llamadas es la FUENTE DE VERDAD del consumo real; el de resultados permite reconstruir
qué ocurrió con cada candidato. Todo se sanea antes de registrarse."""
import hashlib
import json
from .errors import sanitize

UNKNOWN = "UNKNOWN"


def canon(o): return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
def input_hash(op, params): return hashlib.sha256(canon({"op": op, "params": sanitize(params)}).encode()).hexdigest()[:24]


class ToolCallLedger:
    FIELDS = ("tool_call_id", "run_id", "plan_id", "phase", "tool", "operation", "input_hash", "entity_ids", "estimated_cost", "actual_cost",
              "credit_before", "credit_after", "credit_delta", "remaining_credit_budget", "status", "error_code", "error_message", "result_count",
              "started_at", "finished_at", "data_origin", "capability_notes")

    def __init__(self, run_id, plan_id):
        self.run_id, self.plan_id, self.rows = run_id, plan_id, []

    def add(self, **kw):
        row = {k: None for k in self.FIELDS}; row.update(sanitize(kw)); row["run_id"], row["plan_id"] = self.run_id, self.plan_id
        row["tool_call_id"] = f"{self.run_id}-C{len(self.rows) + 1:04d}"; self.rows.append(row); return row

    def credits_actual(self):
        data = [r for r in self.rows if r["phase"] != "METERING" and r["status"] in ("OK", "EMPTY_RESULT", "ERROR")]   # llamadas realmente ejecutadas al proveedor
        k = [r["actual_cost"] for r in data if isinstance(r["actual_cost"], (int, float))]
        return {"known_total": sum(k), "known_calls": len(k), "unknown_calls": sum(1 for r in data if r["actual_cost"] == UNKNOWN),
                "unknown_paid_calls": sum(1 for r in data if r["actual_cost"] == UNKNOWN and (r["estimated_cost"] or 0) > 0)}


class ResultLedger:
    FIELDS = ("candidate_id", "entity_id", "name", "domain", "source", "discovery_query", "discovery_page", "discovery_rank", "dedupe_status",
              "review_state", "data_quality", "enrichment_status", "enrichment_call_id", "gate_status", "gate_details", "evidence_status", "final_status",
              "identity_check", "geo_priority_tier", "geo_tier_hint", "evidence_profile", "provenance_queries", "relevance_decision", "relevance_confidence", "discard_reason", "reportable", "evidence_items")

    def __init__(self):
        self.rows = {}

    def add(self, candidate_id, **kw):
        row = {k: None for k in self.FIELDS}; row.update(sanitize(kw)); row["candidate_id"] = candidate_id; self.rows[candidate_id] = row; return row

    def update_entity(self, entity_id, **kw):
        for r in self.rows.values():
            if r["entity_id"] == entity_id: r.update(sanitize(kw))

    def reconstruct(self, candidate_id, tool_ledger):
        """'¿Qué ocurrió con este candidato?': su fila + todas las llamadas que lo tocaron (discovery y enrichment)."""
        r = self.rows[candidate_id]
        calls = [c for c in tool_ledger.rows if r["entity_id"] in (c["entity_ids"] or []) or (c["phase"] == "DISCOVERY" and c["result_count"] and c["tool_call_id"] == r["source"])]
        return {"candidate": r, "tool_calls": calls}
