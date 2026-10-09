"""Máquina de estados del ejecutor. Transiciones deterministas y cerradas; ninguna la decide un LLM."""
PLANNED, RUNNING, AWAITING = "PLANNED", "RUNNING", "AWAITING_EXTERNAL_RESPONSE"
AWAITING_RELEVANCE, AWAITING_CHECKPOINT = "AWAITING_RELEVANCE_PROPOSALS", "AWAITING_CHECKPOINT"
PARTIAL, COMPLETED, DEGRADED, BUDGET_EXHAUSTED, BLOCKED, FAILED = "PARTIAL", "COMPLETED", "DEGRADED", "BUDGET_EXHAUSTED", "BLOCKED", "FAILED"
TERMINAL = {PARTIAL, COMPLETED, DEGRADED, BUDGET_EXHAUSTED, BLOCKED, FAILED, AWAITING, AWAITING_RELEVANCE, AWAITING_CHECKPOINT}
ALLOWED = {PLANNED: {RUNNING, BLOCKED}, RUNNING: {AWAITING, AWAITING_RELEVANCE, AWAITING_CHECKPOINT, PARTIAL, COMPLETED, DEGRADED, BUDGET_EXHAUSTED, FAILED}}
MEANING = {
 COMPLETED: "Se ejecutó todo el trabajo del alcance del experimento sobre un plan `ready`, sin errores técnicos ni bloqueos de presupuesto. NO significa que se alcanzara el objetivo de cualificadas.",
 DEGRADED: "Igual que COMPLETED pero sobre un plan `degraded` confirmado por el usuario (alcance reducido).",
 PARTIAL: "Terminó con trabajo incompleto por causas no presupuestarias: discovery vacío, error técnico en una fase posterior al discovery, o pausa por coste no medible.",
 BUDGET_EXHAUSTED: "Una operación de pago se bloqueó (BUDGET_BLOCKED) con trabajo pendiente, o la facturación real superó el tope.",
 BLOCKED: "No se ejecutó nada: plan inválido/bloqueado/no confirmado, configuración insegura o herramienta no soportada.",
 FAILED: "Error técnico que impide obtener candidatos (p. ej. proveedor no disponible o autenticación en discovery).",
 AWAITING_RELEVANCE: "Pausa antes de gastar: faltan las decisiones de Candidate Relevance (el LLM propone sobre los candidatos reales; el código valida).",
 AWAITING_CHECKPOINT: "Pausa en el PUNTO DE CONTROL previo al gasto: ninguna llamada de pago se ejecuta hasta recibir una aprobación humana que coincida con el hash exacto de la lista mostrada.",
 AWAITING: "Pausa del modo relay: hay una petición externa pendiente. Al relanzar, se reproduce lo grabado y se continúa."}


class IllegalTransition(Exception):
    pass


class StateMachine:
    def __init__(self, clock):
        self.state, self.clock, self.events = PLANNED, clock, [{"at": clock(), "from": None, "to": PLANNED, "reason": "run creado"}]

    def go(self, to, reason=""):
        if to not in ALLOWED.get(self.state, set()):
            raise IllegalTransition(f"{self.state} -> {to}")
        self.events.append({"at": self.clock(), "from": self.state, "to": to, "reason": reason}); self.state = to
