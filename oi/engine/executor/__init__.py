"""Ejecutor mínimo (Fase 3B-1). Ejecuta un ResearchPlan del Planner a través de un adapter de proveedor y un transporte.
Transportes: FixtureTransport (DRY_EXECUTION, datos SINTÉTICOS / NO CALIBRANTES) y RecordedTransport (relay grabado para llamadas reales
hechas fuera del proceso). Este paquete NO contiene clientes de red: nunca llama a Apollo por sí mismo."""
EXECUTOR_VERSION = "0.1.0"
