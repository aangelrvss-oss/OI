"""Pipeline E2E: NL -> Search Spec -> Plan -> aprobación -> Executor -> evidencia -> cualificación -> contacto -> Opportunity Records -> HTML.
No contiene un segundo executor: reutiliza oi.engine.executor.Executor. Toda salida al exterior pasa por oi.pipeline.guard."""
