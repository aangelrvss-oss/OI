"""Simulación DRY-RUN del plan: aplica authorize + ledger + stopping sobre un ESCENARIO sintético de contadores.
No ejecuta ninguna herramienta: los 'resultados' son números del escenario. Todo está acotado: termina siempre."""
import math
from .ledger import Ledger, authorize
from .stopping import new_progress, next_action, decide_outcome, claims


def simulate(plan, scenario, actual_credits_per_unit=None):
    """scenario: discovery_rounds [{returned,dup,exhausted?}], prefilter_survival, gate_pass_rate, minutes_per_call.
    actual_credits_per_unit: créditos REALES simulados por unidad (si no se da, se liquida con la estimación observada)."""
    L, pr, log, done = Ledger(plan), new_progress(), [], set()
    ph = {p["phase_id"]: p for p in plan["phases"] if p["status"] == "planned"}
    rounds = [dict(r) for r in scenario.get("discovery_rounds", [])]
    ps, gp, mpc = scenario.get("prefilter_survival", 0.5), scenario.get("gate_pass_rate", 0.5), scenario.get("minutes_per_call", 0.0)
    sc = {c["id"]: c["parameters"] for c in plan["stop_conditions"]}
    dr = sc["DIMINISHING_RETURNS"]
    result = lambda reason, extra=None: _final(plan, L, pr, log, reason, extra)

    def call(phase, tool, op, crit=None, purpose="normal", units=1):
        d = authorize(plan, L, {"phase": phase, "tool": tool, "operation": op, "criterion_id": crit, "purpose": purpose, "units": units}, done)
        log.append({"phase": phase, "tool": tool, "op": op, "purpose": purpose, "units": units, "allowed": d["allowed"], "code": d["code"], "estimated_credits": d.get("estimated_credits")})
        if d["allowed"]:
            prov = tool.split(".")[0]
            L.settle(d["record_id"], (actual_credits_per_unit * units) if (actual_credits_per_unit is not None and prov in L.credit_limits and d.get("estimated_credits")) else d.get("estimated_credits"))
        return d

    dsel = ph["DISCOVERY"]["tool_selections"][0]
    for _ in range(sc["MAX_ITERATIONS"]["max_iterations_total"] + 2):  # cota defensiva
        d = call("DISCOVERY", dsel["selected_tool"], dsel["operations"][0])
        if not d["allowed"]:
            return result("TECHNICAL_BLOCK" if d["code"] in ("TOOL_UNAVAILABLE_ON_CURRENT_PLAN", "PLAN_BLOCKED") else "BUDGET_HARD_THRESHOLD", {"denied": d["code"]})
        pr["round"] += 1; pr["pages_used"] += 1; pr["elapsed_minutes"] += mpc
        r = rounds.pop(0) if rounds else {"returned": 0, "dup": 0, "exhausted": True}
        ret, dup = r.get("returned", 0), r.get("dup", 0); new = max(0, ret - dup)
        pr["new_unique_last"] = new
        pr["unique_total"] = min(plan["coverage"]["planned_pool"], pr["unique_total"] + new)
        if dr["enabled"]:
            pr["low_yield_rounds"] = pr["low_yield_rounds"] + 1 if (new / max(1, ret)) < dr["min_new_entity_pct"] else 0
        per_page = ph["DISCOVERY"]["strategy"]["pagination"]["per_page"]
        pr["source_exhausted"] = bool(r.get("exhausted")) or ret < per_page   # sin total_count: una página incompleta o vacía indica fuente agotada
        pr["enrichable"] = int(pr["unique_total"] * ps); pr["stage"] = "post_prefilter"
        done.update(p for p in ("DISCOVERY", "NORMALIZATION", "ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS") if p in ph)
        act = next_action(plan, pr, L.status()); log.append({"decision": act["action"], "stop_reason": act["stop_reason"], "lever": (act["lever"] or {}).get("lever")})
        if act["action"] == "expand": pr["levers_used"].append(act["lever"]["lever"]); pr["expansion_rounds_used"] += 1; continue
        if act["action"] == "degrade": pr["soft_degrade_applied"] = True; continue
        if act["action"] == "stop": return result(act["stop_reason"])
        if act["action"] == "close_discovery": pr["discovery_close_reason"] = act["stop_reason"]
        break
    else:
        return result("MAX_ITERATIONS")
    # ENRICHMENT en tranches (créditos = unidades × coste observado; se comprueban ANTES de llamar)
    pr["stage"] = "enrichment"
    if "ENRICHMENT" in ph:
        e = ph["ENRICHMENT"]; sel = e["tool_selections"][0]; crit = e["active_criteria"][0]
        total = min(plan["coverage"]["planned_enrichment"], pr["enrichable"]); bs = e["policy"]["batch_size"]["configured_batch_size"]; enriched = 0
        for _ in range(math.ceil(total / bs)):
            n = min(bs, total - enriched)
            d = call("ENRICHMENT", sel["selected_tool"], sel["operations"][0], crit, units=n)
            if not d["allowed"]: return result("BUDGET_HARD_THRESHOLD", {"denied": d["code"]})
            enriched += n; pr["enriched"] = enriched; pr["qualified"] = int(enriched * gp)
            if pr["qualified"] >= plan["coverage"]["achievable_qualified_target"] and "EVIDENCE" not in ph: break
        done.add("ENRICHMENT")
    else:
        pr["qualified"] = int(pr["enrichable"] * gp)
    done.add("VERIFIED_GATES")
    if "EVIDENCE" in ph:   # top-N; la reserva solo puede cerrar el top-N cuando se cumple su condición
        n = min(plan["coverage"]["planned_deep_research"], pr["qualified"])
        for i in range(n):
            ok = True
            for s in ph["EVIDENCE"]["tool_selections"]:
                for _k in range(1 if s["selected_tool"] != "firecrawl.scrape" else 2):
                    args = ("EVIDENCE", s["selected_tool"], s["operations"][0], s["triggering_criteria"][0])
                    d = call(*args)
                    if not d["allowed"] and d["code"] in ("PHASE_ALLOCATION_EXCEEDED", "PHASE_CREDIT_ALLOCATION_EXCEEDED"):
                        d = call(*args, purpose="close_top_n")
                    if not d["allowed"]: ok = False; break
                if not ok: break
            if not ok: return result("BUDGET_HARD_THRESHOLD", {"denied": d["code"]})
            pr["tier1_done"] += 1
        done.add("EVIDENCE")
    pr["stage"] = "final"; act = next_action(plan, pr, L.status())
    if act["action"] == "await_user":
        pr["relaxation_proposed"] = True
        if scenario.get("user_authorizes_relaxation"): pr["relaxation_authorized"] = True
        else: pr["relaxation_declined"] = True
        log.append({"proposal": act["proposal"]}); act = next_action(plan, pr, L.status())
    reason = act["stop_reason"]
    if reason == "PLAN_EXHAUSTED" and pr.get("discovery_close_reason") not in (None, "ENOUGH_CANDIDATES"): reason = pr["discovery_close_reason"]
    return result(reason)


def _final(plan, L, pr, log, reason, extra=None):
    return {"stop_reason": reason, "outcome": decide_outcome(plan, pr, reason), "progress": pr, "claims": claims(plan, pr), "utilization": L.utilization(),
            "ledger_status": L.status(), "tool_calls_used": L.data_total(), "reserve_used": L.reserve_used, "credits_used": dict(L.credits_used), "log": log, "extra": extra or {}}
