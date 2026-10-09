"""Interfaz de adapter de proveedor. El Executor solo conoce operaciones LÓGICAS (org_discovery, org_enrich_bulk, credit_balance);
el adapter las traduce a operaciones y parámetros del proveedor y parsea sus respuestas. Cambiar de proveedor = cambiar de adapter."""


class ToolAdapter:
    provider = None
    tool_ids = {}          # operación lógica -> tool id del catálogo del Planner
    READ_OPERATIONS = {}   # operación lógica -> operación del proveedor (ALLOWLIST de solo lectura)

    def capabilities(self): raise NotImplementedError
    def supports_tool(self, tool_id): return tool_id in self.tool_ids.values()
    def build_request(self, logical_op, generic): raise NotImplementedError
    def parse(self, logical_op, raw): raise NotImplementedError
