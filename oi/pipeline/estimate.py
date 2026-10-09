"""Dry-run de costes (`--estimate`): calcula el presupuesto ANTES de ejecutar, sin ejecutar nada. Dice UNKNOWN donde el coste no está confirmado (p. ej. People Search)."""
from oi.engine.planner.planner import Planner
from oi.engine.planner.config import PlannerConfig
from oi.pipeline import guard


def _plan(spec, budget, clock):
    r = Planner(PlannerConfig(), clock=clock).plan(spec, budget_input=budget)
    return r.get("plan"), r


def _usage(plan):
    if not plan: return None
    t = plan["budget"]["estimated_usage"]["totals"]; cb = plan["budget"]["credit_budget"]
    return {"estimated_data_calls": t["tool_calls_upper_bound"], "estimated_infra_calls": t["infra_calls"], "estimated_tool_credits": t["apollo_credits_estimated_from_observed_cost"],
            "credit_cap": cb["max"]["apollo"], "cost_status": cb["apollo"]["cost_status"], "plan_status": plan["status"], "people_search": "UNKNOWN (" + str(cb.get("people")) + ")"}


def estimate(spec, profile, clock):
    sim, _ = _plan(spec, profile["planning"], clock); real, _ = _plan(spec, profile["real_projection"], clock)
    u_sim, u_real = _usage(sim), _usage(real)
    return {"mode": "offline", "executed": False,
            "offline": {"estimated_data_calls": u_sim["estimated_data_calls"] if u_sim else "UNKNOWN", "estimated_infra_calls": u_sim["estimated_infra_calls"] if u_sim else "UNKNOWN",
                        "estimated_tool_credits": 0, "paid_calls": 0, "external_calls": 0,
                        "note": "Offline: el transporte es sintético (saldo 0, coste 0). Las llamadas 'de datos' se sirven desde el dataset del caso. El tope aritmético de simulación (" + str(u_sim["credit_cap"] if u_sim else "?") + ") NO es gasto."},
            "real_projection": {"authorized": guard.paid_enrichment_authorized(), "blocked": True, "blockers": guard.real_mode_blockers(),
                                "credit_cap_stated_by_user_NOT_AN_AUTHORIZATION": profile["real_projection"]["max_apollo_credits"],
                                "estimated_data_calls": u_real["estimated_data_calls"] if u_real else "UNKNOWN", "estimated_infra_calls": u_real["estimated_infra_calls"] if u_real else "UNKNOWN",
                                "estimated_tool_credits": u_real["estimated_tool_credits"] if u_real else "UNKNOWN", "cost_status": u_real["cost_status"] if u_real else "UNKNOWN",
                                "people_search": u_real["people_search"] if u_real else "UNKNOWN", "contact_enrichment": "UNKNOWN (no hay coste confirmado de ninguna herramienta de contactos)",
                                "paid_calls": 0, "plan_status": u_real["plan_status"] if u_real else "UNKNOWN",
                                "note": "Proyección del modo real si algún día se autorizara. Hoy paid_calls=0 porque el modo real está bloqueado."}}
