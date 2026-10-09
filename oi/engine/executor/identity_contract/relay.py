"""Relay con ESPACIO DE NOMBRES por ejecución (EXECUTION_IDENTITY_V1). Ninguna función hace red.

Capas (cada una detiene ANTES de cualquier llamada nueva y nunca devuelve una respuesta ajena):
  1. Manifiesto RELAY_IDENTITY.json: el relay pertenece a UNA ejecución (execution_id + tramo). Un relay sin manifiesto (p. ej. el de T1) o con
     otro execution_id no se usa; tampoco se escribe en él (ni siquiera se crean directorios).
  2. Nombres: solo se admiten registros "ei1-<40 hex>". Un registro con nombre histórico (24 hex, convención T1) es AJENO.
  3. Sobre de identidad: cada respuesta guarda execution_id, clave y petición; la clave se recalcula. Se verifica en la auditoría previa
     (todos los registros) y otra vez al leer (el registro concreto)."""
import json
import os

from ..errors import AwaitingExternalResponse, ProviderError, sanitize
from ..transports import REAL
from . import CONTRACT
from .keys import KEY_RE, operation_key, tranche_of, valid_execution_id

MANIFEST = "RELAY_IDENTITY.json"
SUBDIRS = ("responses", "pending")


class ExecutionIdentityViolation(BaseException):
    """BaseException A PROPÓSITO: el ejecutor histórico captura `Exception` en cada llamada y la registraría como un error técnico del proveedor
    (y seguiría con la siguiente llamada). Una violación de identidad no es un error del proveedor: aborta la ejecución sin pending y sin pago."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}"); self.code, self.detail = code, detail


def manifest_for(execution_id, tranche):
    return {"contract": CONTRACT, "execution_id": execution_id, "tranche": tranche, "namespace": "relay:" + execution_id}


def init_relay(root, execution_id, tranche):
    """Crea un relay NUEVO y VACÍO ligado a una ejecución. Nunca adopta un directorio con contenido (un relay ajeno no se convierte en propio)."""
    if not valid_execution_id(execution_id): raise ExecutionIdentityViolation("EXECUTION_ID_INVALID", repr(execution_id))
    if tranche_of(execution_id) != tranche: raise ExecutionIdentityViolation("EXECUTION_ID_TRANCHE_MISMATCH", f"{execution_id} / {tranche}")
    if os.path.exists(root) and os.listdir(root): raise ExecutionIdentityViolation("RELAY_ROOT_NOT_EMPTY", root)
    for d in SUBDIRS: os.makedirs(os.path.join(root, d), exist_ok=True)
    with open(os.path.join(root, MANIFEST), "w", encoding="utf-8") as fh: json.dump(manifest_for(execution_id, tranche), fh, indent=2, ensure_ascii=False)
    return root


def envelope_problems(rec, key, execution_id):
    """Un registro solo pertenece a ESTA ejecución si su sobre lo dice y la clave recalculada coincide con su nombre."""
    if not isinstance(rec, dict): return ["NOT_AN_OBJECT"]
    p = []
    if rec.get("contract") != CONTRACT: p.append("CONTRACT_MISSING_OR_DIFFERENT")
    if rec.get("execution_id") != execution_id: p.append(f"EXECUTION_ID_DIFFERS:{rec.get('execution_id')!r}")
    if rec.get("operation_key") != key: p.append("OPERATION_KEY_DIFFERS_FROM_NAME")
    try:
        if operation_key(rec.get("execution_id"), rec.get("operation"), rec.get("params") or {}, rec.get("context") or {}) != key: p.append("OPERATION_KEY_NOT_RECOMPUTABLE")
    except (ValueError, TypeError): p.append("OPERATION_KEY_NOT_RECOMPUTABLE")
    return p


def audit_relay(root, execution_id, tranche, verify_records=True):
    """SOLO LECTURA. Devuelve la lista de problemas (vacía = el relay es de esta ejecución y solo contiene registros suyos)."""
    if not os.path.isdir(root): return ["RELAY_ROOT_MISSING"]
    p = []; mf = os.path.join(root, MANIFEST)
    if not os.path.isfile(mf): p.append("RELAY_NAMESPACE_MANIFEST_MISSING")
    else:
        try:
            with open(mf, encoding="utf-8") as fh: m = json.load(fh)
        except (OSError, ValueError): m = None
        if m != manifest_for(execution_id, tranche): p.append("RELAY_NAMESPACE_MISMATCH")
    extra = sorted(set(os.listdir(root)) - {MANIFEST, *SUBDIRS})
    if extra: p.append("RELAY_UNEXPECTED_ENTRIES:" + ",".join(extra[:5]))
    for sub in SUBDIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d): continue
        for f in sorted(os.listdir(d)):
            k = f[:-5] if f.endswith(".json") else None
            if k is None or not KEY_RE.match(k): p.append(f"RELAY_CONTAINS_FOREIGN_RECORD:{sub}/{f}"); continue
            if verify_records:
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh: rec = json.load(fh)
                except (OSError, ValueError): rec = None
                ep = envelope_problems(rec, k, execution_id)
                if ep: p.append(f"RELAY_RECORD_IDENTITY_MISMATCH:{sub}/{f}:{'|'.join(ep)}")
    return p


class IdentityBoundRelayTransport:
    """Relay de UNA ejecución. Mismo protocolo que RecordedTransport (pending/ → respuesta grabada → relanzar reproduce), con clave EXECUTION_IDENTITY_V1."""
    data_origin = REAL

    def __init__(self, root, execution_id, tranche):
        self.root, self.execution_id, self.tranche = root, execution_id, tranche
        self.calls = []; self.pending_written = []; self.replayed = []           # NO escribe nada aquí: un relay ajeno no se toca ni para crear directorios

    def identity(self):
        return {"contract": CONTRACT, "execution_id": self.execution_id, "tranche": self.tranche, "namespace": manifest_for(self.execution_id, self.tranche)["namespace"]}

    def audit(self, verify_records=True):
        return audit_relay(self.root, self.execution_id, self.tranche, verify_records=verify_records)

    def invoke(self, op, params, context=None):
        ctx = context or {}
        p = self.audit(verify_records=False)                                    # capa 1+2 en cada invocación (barata)
        if p: raise ExecutionIdentityViolation("RELAY_IDENTITY_INVALID", "; ".join(p[:5]))
        k = operation_key(self.execution_id, op, params, ctx)
        f = os.path.join(self.root, "responses", k + ".json")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh: rec = json.load(fh)
            ep = envelope_problems(rec, k, self.execution_id)                    # capa 3 al leer: una respuesta ajena NUNCA se devuelve
            if ep: raise ExecutionIdentityViolation("RESPONSE_IDENTITY_MISMATCH", f"{k}: {'|'.join(ep)}")
            self.calls.append((op, sanitize(params))); self.replayed.append(k)
            if rec.get("error"): raise ProviderError(rec["error"].get("code", "UNKNOWN_ERROR"), rec["error"].get("message", ""))
            return rec["response"]
        req = {"contract": CONTRACT, "operation_key": k, "input_hash": k, "execution_id": self.execution_id, "tranche": self.tranche, "operation": op,
               "params": sanitize(params), "context": sanitize(ctx),
               "instructions": "Ejecutar esta operación de SOLO LECTURA fuera del proceso SOLO bajo la aprobación de este execution_id y grabarla con record() (sobre de identidad)"}
        self.calls.append((op, sanitize(params)))
        with open(os.path.join(self.root, "pending", k + ".json"), "w", encoding="utf-8") as fh: json.dump(req, fh, indent=2, ensure_ascii=False)
        self.pending_written.append(k)
        raise AwaitingExternalResponse(req)

    def record(self, request, response=None, error=None, provenance=None):
        """Operador del relay: graba la respuesta de una petición pendiente DE ESTA ejecución, con su sobre de identidad."""
        k = request.get("operation_key")
        if request.get("execution_id") != self.execution_id or not k or operation_key(self.execution_id, request["operation"], request.get("params") or {}, request.get("context") or {}) != k:
            raise ExecutionIdentityViolation("RECORD_IDENTITY_MISMATCH", str(k))
        p = self.audit(verify_records=False)
        if p: raise ExecutionIdentityViolation("RELAY_IDENTITY_INVALID", "; ".join(p[:5]))
        env = {"contract": CONTRACT, "operation_key": k, "execution_id": self.execution_id, "tranche": self.tranche, "operation": request["operation"],
               "params": request.get("params") or {}, "context": request.get("context") or {}, "response": response, "error": error, "provenance": provenance}
        with open(os.path.join(self.root, "responses", k + ".json"), "w", encoding="utf-8") as fh: json.dump(sanitize(env), fh, indent=2, ensure_ascii=False)
        pf = os.path.join(self.root, "pending", k + ".json")
        if os.path.exists(pf): os.remove(pf)
        return k
