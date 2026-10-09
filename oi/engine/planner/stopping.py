"""Condiciones de parada, expansión y resultado. Funciones PURAS sobre (plan, progress, estado del ledger). Todo está acotado.
close_discovery termina SOLO el discovery (el plan sigue con enrichment, gates, evidencia y scoring); stop termina el plan."""
import math


def new_progress(**kw):
    p = {"round": 0, "stage": "discovery", "unique_total": 0, "new_unique_last": 0, "low_yield_rounds": 0, "enrichable": 0, "enriched": 0, "qualified": 0,
         "tier1_done": 0, "expansion_rounds_used": 0, "levers_used": [], "source_exhausted": False, "pages_used": 0, "elapsed_minutes": 0.0,
         "user_cancelled": False, "technical_block": False, "relaxation_proposed": False, "relaxation_authorized": False, "relaxation_declined": False,
         "soft_degrade_applied": False}
    p.update(kw); return p


def lever_ladder(plan):
    return [l for l in plan["expansion_policy"]["levers"] if l["available"]]


def next_action(plan, pr, ledger_status):
    """-> {'action': continue|expand|degrade|close_discovery|stop|await_user, 'stop_reason', 'lever', 'proposal', 'reasons'}"""
    sp = {c["id"]: c["parameters"] for c in plan["stop_conditions"]}
    cov = plan["coverage"]; T, A = cov["requested_target"], cov["achievable_qualified_target"]
    need_evidence = any(p["phase_id"] == "EVIDENCE" and p["status"] == "planned" for p in plan["phases"])
    target_met = pr["qualified"] >= A and (not need_evidence or pr["tier1_done"] >= cov["planned_deep_research"])
    mk = lambda a, r=None, why="": {"action": a, "stop_reason": r, "lever": None, "proposal": None, "reasons": [why] if why else []}
    stop = lambda r, why: mk("stop", r, why)
    in_disc = pr["stage"] == "post_prefilter"
    def end_discovery(r, why):
        return stop("NO_VIABLE_CANDIDATES", "ningún candidato enriquecible: " + why) if pr["enrichable"] == 0 else mk("close_discovery", r, why)
    if pr["user_cancelled"]: return stop("USER_CANCELLED", "el usuario canceló")
    if pr["technical_block"]: return stop("TECHNICAL_BLOCK", "bloqueo técnico (herramienta/plan)")
    if target_met: return stop("TARGET_REACHED", f"qualified {pr['qualified']} >= objetivo alcanzable {A}")
    if ledger_status == "hard": return stop("BUDGET_HARD_THRESHOLD", "umbral duro con trabajo pendiente")
    if pr["stage"] == "final":
        if pr["qualified"] < A and pr["enriched"] >= cov["planned_enrichment"] > 0:   # capacidad de enrichment agotada: ¿gates demasiado estrictos?
            if pr["relaxation_declined"]: return stop("USER_DECLINED_RELAXATION", "el usuario no autorizó revisar un gate HARD")
            if pr["relaxation_authorized"]: return stop("RELAXATION_REQUIRES_NEW_SPEC", "relajar un HARD exige una NUEVA versión del Search Spec y un nuevo plan")
            if not pr["relaxation_proposed"] and plan["criteria_digest"]["classes"]["HARD"]:
                a = mk("await_user", None, "enrichment agotado con qualified < objetivo")
                a["proposal"] = {"type": "proposed_relaxation", "applied": False, "requires": plan["expansion_policy"]["hard_relaxation"]["requires"],
                                 "hard_criteria": plan["criteria_digest"]["classes"]["HARD"]}
                return a
        return stop("PLAN_EXHAUSTED", "todas las fases planificadas terminaron con qualified < objetivo alcanzable")
    if pr["round"] >= sp["MAX_ITERATIONS"]["max_iterations_total"]:
        return end_discovery("MAX_ITERATIONS", "tope de iteraciones") if in_disc else stop("MAX_ITERATIONS", "tope de iteraciones")
    if ledger_status == "soft" and not pr["soft_degrade_applied"]:
        return mk("degrade", None, "umbral blando: aplicar degradación (reducir deep research antes que nada)")
    if in_disc:
        need = cov["planned_enrichment"] or A
        if pr["enrichable"] >= need: return mk("close_discovery", "ENOUGH_CANDIDATES", f"enrichable {pr['enrichable']} >= {need}: DISCOVERY completed, el plan continúa")
        if pr["unique_total"] >= cov["planned_pool"]: return end_discovery("MAX_POOL", "pool máximo alcanzado")
        if pr["source_exhausted"]: return end_discovery("SOURCE_EXHAUSTED", "fuente agotada (página vacía o incompleta)")
        if pr["pages_used"] >= sp["MAX_PAGES"]["max_pages_total"]: return end_discovery("MAX_PAGES", "máximo de páginas")
        mt = sp["MAX_TIME"].get("discovery_minutes")
        if mt is not None and pr["elapsed_minutes"] >= mt: return end_discovery("MAX_TIME", "tiempo de discovery agotado")
        dr = sp["DIMINISHING_RETURNS"]
        if dr["enabled"] and pr["low_yield_rounds"] >= dr["consecutive_rounds"]: return end_discovery("DIMINISHING_RETURNS", "páginas repetidas aportan pocas entidades nuevas")
        if pr["expansion_rounds_used"] < plan["expansion_policy"]["max_rounds"]:
            nxt = next((l for l in lever_ladder(plan) if l["lever"] not in pr["levers_used"]), None)
            if nxt:
                a = mk("expand", None, f"enrichable {pr['enrichable']} < {need}; palanca {nxt['lever']}; los gates HARD no se tocan"); a["lever"] = nxt; return a
        return end_discovery("EXPANSION_EXHAUSTED", "sin más palancas automáticas")
    return mk("continue")


def decide_outcome(plan, pr, stop_reason):
    """completed | partial | degraded | budget_exhausted. Definición exacta en plan['outcome_policy']."""
    if stop_reason == "BUDGET_HARD_THRESHOLD": return "budget_exhausted"
    if stop_reason == "TARGET_REACHED": return "completed" if plan["status"] == "ready" else "degraded"
    return "partial"


def claims(plan, pr):
    """Lo ÚNICO que el informe puede afirmar: nunca más cualificadas que las que superaron los gates verificados."""
    return {"requested": plan["coverage"]["requested_target"], "discovered_unique": pr["unique_total"], "enriched": pr["enriched"], "qualified": pr["qualified"],
            "deep_researched": pr["tier1_done"], "may_claim_target_qualified": pr["qualified"] >= plan["coverage"]["requested_target"]}
