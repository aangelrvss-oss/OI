"""Claves de operación. Dos convenciones, nunca mezcladas:
- HISTÓRICA (legacy_operation_key): la de RecordedTransport.invoke = input_hash(op, {**params, "_context": context}). Sin identidad de ejecución.
  Es la que generó TODAS las claves de T1 (p. ej. CALL 11 = fbb6034dbf4431b52cc599aa). No se modifica ni se recalcula.
- EXECUTION_IDENTITY_V1 (operation_key): el material hasheado incluye, como campo EXPLÍCITO y separado, el execution_id de la aprobación.
  Formato distinto ("ei1-" + 40 hex) para que una clave nueva no pueda confundirse jamás con una histórica (24 hex)."""
import hashlib
import re

from ..errors import sanitize
from ..ledger import canon, input_hash
from . import CONTRACT

KEY_PREFIX = "ei1-"
KEY_RE = re.compile(r"^ei1-[0-9a-f]{40}$")
EXECUTION_ID_RE = re.compile(r"^EXEC-(T[0-9]+)-[A-Za-z0-9][A-Za-z0-9._-]{2,120}$")


def valid_execution_id(execution_id):
    return isinstance(execution_id, str) and bool(EXECUTION_ID_RE.match(execution_id))


def tranche_of(execution_id):
    m = EXECUTION_ID_RE.match(execution_id) if isinstance(execution_id, str) else None
    return m.group(1) if m else None


def legacy_operation_key(op, params, context=None):
    """Convención HISTÓRICA (T1), idéntica byte a byte a RecordedTransport.invoke. Solo para reproducir/auditar claves T1."""
    return input_hash(op, {**params, "_context": context or {}})


def operation_material(execution_id, op, params, context=None):
    """Material DETERMINISTA de la clave: convención + execution_id + operación + parámetros + contexto (fase, secuencia…)."""
    return {"convention": CONTRACT, "execution_id": execution_id, "op": op, "params": sanitize(params), "context": sanitize(context or {})}


def operation_key(execution_id, op, params, context=None):
    if not valid_execution_id(execution_id): raise ValueError(f"execution_id inválido para {CONTRACT}: {execution_id!r}")
    return KEY_PREFIX + hashlib.sha256(canon(operation_material(execution_id, op, params, context)).encode("utf-8")).hexdigest()[:40]


def hc1(obj):
    """Convención HC1 del caso: sha256 de json.dumps(sort_keys, separators=(',',':'), ensure_ascii=False) en UTF-8."""
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()
