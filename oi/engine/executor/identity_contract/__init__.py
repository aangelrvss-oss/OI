"""Contrato de IDENTIDAD DE EJECUCIÓN (opt-in, solo para ejecuciones NUEVAS). Corrige T2_EXECUTION_IDENTITY_SEAM sin tocar el ejecutor histórico.

Dos conceptos explícitos:
  1. execution identity: la clave de cada operación grabada incluye el execution_id de la APROBACIÓN de esa ejecución (convención versionada
     EXECUTION_IDENTITY_V1). El relay de la ejecución es un espacio de nombres propio (RELAY_IDENTITY.json) y cada respuesta lleva un sobre con
     su execution_id. Una respuesta de otra ejecución (p. ej. T1) nunca satisface una operación de ésta: se detiene antes de cualquier llamada.
  2. fixed candidate execution: candidate_source = FIXED_SHORTLIST + discovery DISABLED. Los candidatos salen SOLO de la shortlist aprobada
     (hash HC1 verificado); no se ejecuta discovery; número, orden, identidad y dominio se validan antes de cualquier llamada de pago.

Compatibilidad: los módulos históricos (runner.py, transports.py, ledger.py, config.py, reporting.py…) NO se modifican. La ruta T1 (Executor +
RecordedTransport + input_hash) sigue exactamente igual y sus claves históricas se reproducen con la convención histórica (legacy_operation_key)."""
CONTRACT = "EXECUTION_IDENTITY_V1"
FIXED_SHORTLIST = "FIXED_SHORTLIST"
DISCOVERY = "DISCOVERY"
