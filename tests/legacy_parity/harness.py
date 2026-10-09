import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from oi.engine.spec.engine import IntentEngine
from oi.engine.spec.versioning import SpecStore

FIXED = lambda: "2026-10-01T00:00:00+00:00"
def draft(name):
    with open(os.path.join(ROOT, "fixtures", "drafts", name + ".json"), encoding="utf-8") as f:
        return json.load(f)
def new_engine(path=None):
    return IntentEngine(store=SpecStore(path), clock=FIXED)

BUDGET6 = {"data_calls_max": 15, "tool_credits_max": {"apollo": 20}, "deep_research_top_n": 5}   # contrato 1a (dentro del spec)
CONF_2B = [{"criterion_id": "D1", "definition_text": "Sin sitio web"},
           {"criterion_id": "D3", "definition_text": "3 o más problemas técnicos detectados", "value": 3}]
CONF_4B = [{"criterion_id": "C1", "definition_text": "+25% de plantilla en 12 meses", "value": 25, "params": {"period_days": 365}}]
INPUT_2B = ["Vendo servicios de marketing digital y SEO local a clínicas"]
INPUT_4B = ["Vendo una plataforma SaaS de selección de personal (ejemplo de test)", "Crecimiento: +25% de plantilla en 12 meses"]

# ───────── Fase 1b ─────────
STD_BUDGET = {"source": "dry_run_example", "max_tool_calls": 80, "infra_calls_max": 60, "max_apollo_credits": 100, "max_time_minutes": 15, "deep_research_top_n": 10}
def make_spec(name, confirm=(), params=None, inputs=None, conf_defs=None, engine=None):
    """Spec confirmado de Fase 1a (+ confirmación explícita de señales vía el canal del usuario)."""
    e = engine or new_engine()
    r = e.process(draft(name), user_params=params or {}, user_inputs=inputs)
    assert r["status"] in ("confirmed", "needs_clarification", "unsupported_yet"), (name, r["status"], r["errors"])
    if confirm:
        sid = r["spec"]["control"]["spec_id"]
        r = e.confirm(sid, 1, [{"criterion_id": c, "definition_text": f"[dry-run] confirmación explícita de {c}"} for c in confirm])
        assert r["status"] == "confirmed", r["errors"]
    return r["spec"]
