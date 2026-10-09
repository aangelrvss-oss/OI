"""BARRERA ÚNICA offline/real. Ningún otro módulo del producto puede construir un transporte, abrir un socket ni decidir si se puede pagar.

* make_transport("offline", ...) -> FixtureTransport (SINTÉTICO, saldo 0, coste 0) envuelto en GuardedTransport (cuenta, allowlist, solo SYNTHETIC).
* make_transport("real", ...) -> SIEMPRE RealModeBlocked en este bloque: paid_enrichment_authorized=false y el transporte real no está cableado.
* offline_lock(): mientras está activo, cualquier intento de abrir un socket / resolver DNS lanza ExternalCallAttempt y se cuenta (EXTERNAL_CALLS).
La autorización de pago es PAID_ENRICHMENT_AUTHORIZED (aquí, única constante) Y config/oi.config.json; si cualquiera dice false, no hay pago posible.
"""
import contextlib
import json
import os
import socket

from oi import paths
from oi.engine.executor.transports import FixtureTransport, SYNTHETIC
from oi.engine.executor.errors import ProviderError

PAID_ENRICHMENT_AUTHORIZED = False          # decisión del usuario; solo él la cambia

OFFLINE_ALLOWED_OPS = frozenset({"apollo_users_api_profile", "apollo_organizations_lookup", "apollo_organizations_bulk_enrich", "apollo_organizations_job_postings"})
PAID_OPS = frozenset({"apollo_organizations_bulk_enrich", "apollo_organizations_job_postings"})   # operaciones que en MODO REAL consumirían créditos


class RealModeBlocked(Exception):
    """La ejecución real está bloqueada. `reasons` explica por qué (todas las que aplican)."""
    def __init__(self, reasons):
        super().__init__("REAL_MODE_BLOCKED: " + "; ".join(reasons)); self.reasons = list(reasons)


class ExternalCallAttempt(Exception):
    """Se intentó una conexión de red durante una ejecución offline."""


def config_authorization():
    with open(os.path.join(paths.CONFIG, "oi.config.json"), encoding="utf-8") as f:
        return bool(json.load(f).get("paid_enrichment_authorized"))


def paid_enrichment_authorized():
    """True SOLO si la constante y la configuración lo dicen a la vez."""
    return bool(PAID_ENRICHMENT_AUTHORIZED) and config_authorization()


def real_mode_blockers():
    out = []
    if not PAID_ENRICHMENT_AUTHORIZED: out.append("PAID_ENRICHMENT_AUTHORIZED=false")
    if not config_authorization(): out.append("config/oi.config.json: paid_enrichment_authorized=false")
    out.append("el transporte real no está cableado en este bloque (F11 es offline); requiere aprobación humana de ejecución (execution_approval)")
    return out


# ───────────────────────── contadores y bloqueo de red
class _Counters:
    def __init__(self): self.reset()
    def reset(self): self.external_attempts = 0; self.log = []

COUNTERS = _Counters()


@contextlib.contextmanager
def offline_lock():
    """Bloquea la red del proceso mientras dura. Cualquier intento se CUENTA y lanza ExternalCallAttempt."""
    orig = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def _deny(kind):
        def f(*a, **k):
            COUNTERS.external_attempts += 1; COUNTERS.log.append(kind)
            raise ExternalCallAttempt(f"intento de red bloqueado en modo offline ({kind})")
        return f
    socket.socket.connect = _deny("connect"); socket.socket.connect_ex = _deny("connect_ex"); socket.create_connection = _deny("create_connection"); socket.getaddrinfo = _deny("getaddrinfo")
    try:
        yield COUNTERS
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = orig


class GuardedTransport:
    """Envuelve un transporte SINTÉTICO. Rechaza cualquier operación fuera de la allowlist de solo lectura y cualquier origen que no sea SYNTHETIC."""
    def __init__(self, inner):
        if getattr(inner, "data_origin", None) != SYNTHETIC: raise RealModeBlocked(["el transporte offline solo admite data_origin SYNTHETIC"])
        self.inner = inner; self.data_origin = inner.data_origin; self.calls = []; self.external_calls = 0

    def invoke(self, op, params, context=None):
        if op not in OFFLINE_ALLOWED_OPS: raise ProviderError("OPERATION_NOT_ALLOWED", f"{op}: fuera de la allowlist offline")
        self.calls.append((op, params))
        return self.inner.invoke(op, params, context)

    @property
    def balance(self): return self.inner.balance

    def usage(self):
        """Cifras de coste de la ejecución offline. Todas las llamadas son contra datos SINTÉTICOS: ninguna es externa ni de pago."""
        return {"synthetic_calls": len(self.calls), "external_calls": self.external_calls, "paid_calls": 0, "credits_spent": 0,
                "synthetic_calls_by_operation": {o: sum(1 for c in self.calls if c[0] == o) for o in sorted({c[0] for c in self.calls})},
                "operations_that_would_be_paid_in_real_mode": sorted(o for o in {c[0] for c in self.calls} if o in PAID_OPS)}


class GuardedReplayTransport:
    """Envuelve el transporte de REPRODUCCIÓN (datos reales capturados del lookup gratuito). Allowlist mínima (lookup + saldo); jamás una operación de pago; 0 llamadas externas."""
    def __init__(self, inner):
        from oi.pipeline import replay as RP
        if getattr(inner, "data_origin", None) != RP.REAL_RECORDED_FREE_LOOKUP: raise RealModeBlocked(["el replay solo admite data_origin REAL_RECORDED_FREE_LOOKUP"])
        self.inner = inner; self.data_origin = inner.data_origin; self.calls = []; self.external_calls = 0; self._allowed = RP.REPLAY_ALLOWED_OPS

    def invoke(self, op, params, context=None):
        if op not in self._allowed or op in PAID_OPS: raise ProviderError("OPERATION_NOT_ALLOWED", f"{op}: fuera de la allowlist del replay (solo lookup gratuito y lectura de saldo)")
        self.calls.append((op, params))
        return self.inner.invoke(op, params, context)

    @property
    def balance(self): return self.inner.balance

    def usage(self):
        return {"synthetic_calls": 0, "replayed_calls": len(self.calls), "external_calls": self.external_calls, "paid_calls": 0, "credits_spent": 0, "people_calls": 0, "enrichment_calls": 0,
                "replayed_calls_by_operation": {o: sum(1 for c in self.calls if c[0] == o) for o in sorted({c[0] for c in self.calls})}, "operations_that_would_be_paid_in_real_mode": []}


def make_transport(mode, *, dataset=None, capture=None):
    """ÚNICO constructor de transportes del producto."""
    if mode == "offline":
        if not dataset: raise ValueError("el modo offline necesita un dataset sintético")
        return GuardedTransport(FixtureTransport(dataset, balance=0, cost_per_returned_row=0, job_cost=0, lookup_cost=0))
    if mode == "replay":
        from oi.pipeline import replay as RP
        if not capture: raise ValueError("el modo replay necesita una captura versionada")
        return GuardedReplayTransport(RP.ReplayTransport(capture))
    if mode == "real":
        raise RealModeBlocked(real_mode_blockers())
    raise ValueError(f"modo desconocido: {mode!r}")
