"""Aprobación del plan antes de ejecutar. Offline: aprobación automática y ACOTADA a DRY_EXECUTION (datos sintéticos, 0 créditos): no autoriza nada real.
Real: exige una aprobación humana explícita; en este bloque no se puede obtener (guard.RealModeBlocked)."""
from oi.pipeline import guard


def approve(plan, mode, *, clock):
    if mode not in ("offline", "replay-real"):
        raise guard.RealModeBlocked(guard.real_mode_blockers())
    degraded = plan.get("status") == "degraded"
    scope, by = ("DRY_EXECUTION", "OFFLINE_AUTO (sin gasto: datos sintéticos)") if mode == "offline" else ("REPLAY_FREE_LOOKUP", "REPLAY_AUTO (sin gasto: datos reales YA capturados del lookup gratuito; ninguna llamada nueva)")
    return {"mode": mode, "scope": scope, "approved_by": by, "approved_at": clock(), "plan_id": plan["identity"]["plan_id"],
            "plan_status": plan.get("status"), "degraded_plan_confirmed": degraded, "max_credits": 0, "max_external_calls": 0, "paid_enrichment_authorized": False,
            "note": "Esta aprobación solo cubre una ejecución contra el transporte sintético o el replay de datos ya capturados. No es una autorización de ejecución real ni de gasto." + (" El plan está degradado; se confirma solo para la ejecución sintética." if degraded else "")}
