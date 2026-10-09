"""Definición del experimento real 3B-2. El caso vive en el Search Spec (Intent Engine) y en esta configuración; el Executor no tiene lógica del caso."""
import json, os
from oi.engine.spec.engine import IntentEngine
from oi.engine.spec.versioning import SpecStore
from oi.engine.planner.planner import Planner
from oi.engine.executor.config import make_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/
CLOCK = lambda: "2026-10-02T00:00:00+00:00"
# Research Budget: los 25 créditos son del usuario (hard cap); max_tool_calls=20 es una PROPUESTA (cubre 1 discovery + ≤5 enrichments con holgura)
BUDGET = {"source": "user_parameter", "max_apollo_credits": 25, "max_tool_calls": 20}


def build_spec():
    with open(os.path.join(ROOT, "fixtures", "drafts", "case1.json"), encoding="utf-8") as f: draft = json.load(f)
    r = IntentEngine(store=SpecStore(None), clock=CLOCK).process(draft)
    assert r["status"] == "confirmed", r["errors"]
    return r["spec"]


def build_plan(spec):
    o = Planner(clock=CLOCK).plan(spec, budget_input=BUDGET)
    assert o["status"] == "planned", o["errors"]
    return o["plan"]


def build_config(approval=None, confirm_degraded=False):
    return make_config(mode="relay", credit_hard_cap=25, max_enrichment_entities=20, discovery={"max_pages": 1, "per_page": 100}, allow_unverified_capabilities=[],
                       enrichment_batch_size=4, on_unknown_actual_cost="halt_paid_calls", measure_credits="all_calls", require_measured_balance_before_paid_call=True,
                       confirm_degraded=confirm_degraded, execution_approval=approval)
