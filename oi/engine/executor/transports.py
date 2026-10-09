"""Transportes. NINGUNO hace red.
- FixtureTransport: DRY_EXECUTION con datos SINTÉTICOS (no calibrantes). Simula saldo de créditos y errores.
- RecordedTransport: relay para llamadas REALES hechas fuera del proceso (p. ej. por el conector MCP). Si falta la respuesta grabada,
  escribe la petición saneada en pending/ y pausa (AWAITING_EXTERNAL_RESPONSE). Relanzar reproduce lo grabado: nunca vuelve a gastar."""
import json
import os
from oi.engine.evaluators.entity import normalize_domain
from .errors import AwaitingExternalResponse, ProviderError, sanitize
from .ledger import canon, input_hash

SYNTHETIC = "SYNTHETIC / NON-CALIBRATING"
REAL = "REAL_RECORDED"


class FixtureTransport:
    data_origin = SYNTHETIC

    def __init__(self, dataset, balance=180, cost_per_returned_row=1, measurable=True, errors=None, enrich_drop=(), lookup_fn=None, job_cost=1, lookup_cost=0):
        self.ds, self.balance, self.cost, self.measurable = dataset, balance, cost_per_returned_row, measurable
        self.lookup_fn, self.job_cost, self.lookup_cost = lookup_fn, job_cost, lookup_cost
        self.errors = {k: list(v) for k, v in (errors or {}).items()}   # op -> [Exception|None,...] por invocación
        self.drop = {normalize_domain(d) for d in enrich_drop}            # dominios que el 'proveedor' no devuelve
        self.calls = []                                                   # espía: operaciones invocadas (para tests de no-escritura)

    def invoke(self, op, params, context=None):
        self.calls.append((op, sanitize(params)))
        seq = self.errors.get(op)
        if seq:
            e = seq.pop(0)
            if e is not None: raise e
        if op == "apollo_users_api_profile":
            return {"user": {"id": "synthetic-user"}, "num_credits_remaining": self.balance if self.measurable else None}
        if op == "apollo_organizations_lookup":
            orgs = self.lookup_fn(params) if self.lookup_fn else self.ds["lookup"]; pp, pg = params["per_page"], params["page"]; self.balance -= self.lookup_cost
            return {"organizations": orgs[(pg - 1) * pp: pg * pp]}
        if op == "apollo_organizations_job_postings":
            jobs = (self.ds.get("jobs") or {}).get(params["id"], []); self.balance -= self.job_cost
            return {"organization_job_postings": jobs, "pagination": {"total_entries": len(jobs), "total_pages": 1}}
        if op == "apollo_organizations_bulk_enrich":
            out = []
            for d in params["domains"]:
                rec = self.ds["enrich"].get(normalize_domain(d))
                out.append(None if (rec is None or normalize_domain(d) in self.drop) else rec)
            self.balance -= self.cost * sum(1 for r in out if r is not None)
            return {"organizations": out}
        raise ProviderError("OPERATION_NOT_ALLOWED", op)


class RecordedTransport:
    data_origin = REAL

    def __init__(self, root):
        self.root = root; self.calls = []
        for d in ("responses", "pending"): os.makedirs(os.path.join(root, d), exist_ok=True)

    def invoke(self, op, params, context=None):
        h = input_hash(op, {**params, "_context": context or {}})
        self.calls.append((op, sanitize(params)))
        f = os.path.join(self.root, "responses", h + ".json")
        if not os.path.exists(f):
            req = {"input_hash": h, "operation": op, "params": sanitize(params), "context": sanitize(context or {}),
                   "instructions": "Ejecutar esta operación de SOLO LECTURA fuera del proceso y guardar responses/<input_hash>.json con {'response': ...} o {'error': {'code','message'}}"}
            with open(os.path.join(self.root, "pending", h + ".json"), "w", encoding="utf-8") as fh: json.dump(req, fh, indent=2, ensure_ascii=False)
            raise AwaitingExternalResponse(req)
        with open(f, encoding="utf-8") as fh: rec = json.load(fh)
        if rec.get("error"): raise ProviderError(rec["error"].get("code", "UNKNOWN_ERROR"), rec["error"].get("message", ""))
        return rec["response"]

    def record(self, request, response=None, error=None):
        """Ayuda para el operador del relay: guarda la respuesta (saneada) de una petición pendiente."""
        h = request["input_hash"]
        with open(os.path.join(self.root, "responses", h + ".json"), "w", encoding="utf-8") as fh:
            json.dump(sanitize({"input_hash": h, "operation": request["operation"], "response": response, "error": error}), fh, indent=2, ensure_ascii=False)
        p = os.path.join(self.root, "pending", h + ".json")
        if os.path.exists(p): os.remove(p)
