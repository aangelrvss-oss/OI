"""Transporte de REPRODUCCIÓN de datos reales capturados (F13). NO es un cliente de red: sirve, desde un fichero versionado, exactamente lo que devolvió el lookup GRATUITO de Apollo.

* Solo sirve consultas cuyos parámetros de filtro (sin page/per_page) coinciden con una capturada. Una consulta no capturada -> ProviderError REPLAY_QUERY_NOT_CAPTURED (nunca se fabrica nada).
* Solo operaciones gratuitas de solo lectura: lookup y lectura de saldo. Cualquier otra (enrichment, job postings, People, mixed_companies_search) -> OPERATION_NOT_ALLOWED.
* data_origin = REAL_RECORDED_FREE_LOOKUP. El saldo que devuelve es el OBSERVADO en la captura y NO cambia: coste 0.
Se construye SOLO desde oi.pipeline.guard.make_transport("replay", ...).
"""
import hashlib
import json

from oi.engine.executor.errors import ProviderError

REAL_RECORDED_FREE_LOOKUP = "REAL_RECORDED_FREE_LOOKUP"
REPLAY_ALLOWED_OPS = frozenset({"apollo_users_api_profile", "apollo_organizations_lookup"})
IGNORED = ("page", "per_page")


def query_key(params):
    return hashlib.sha256(json.dumps({k: params[k] for k in sorted(params) if k not in IGNORED}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class ReplayTransport:
    data_origin = REAL_RECORDED_FREE_LOOKUP

    def __init__(self, capture):
        if capture.get("schema") != "oi_replay_capture.v1" or capture.get("source") != "APOLLO" or capture.get("mode") != "FREE_LOOKUP" or capture.get("paid") is not False or capture.get("credits_spent") != 0:
            raise ValueError("captura no válida para replay: exige source=APOLLO, mode=FREE_LOOKUP, paid=false, credits_spent=0")
        self.capture = capture; self.balance = capture["balance_observed"]["after"]; self.calls = []
        self._by_key = {c["query_key"]: c for c in capture["calls"]}

    def invoke(self, op, params, context=None):
        if op not in REPLAY_ALLOWED_OPS: raise ProviderError("OPERATION_NOT_ALLOWED", f"{op}: no es una operación gratuita capturada")
        self.calls.append((op, dict(params)))
        if op == "apollo_users_api_profile": return {"user": {"id": "replay-captured-balance"}, "num_credits_remaining": self.balance}
        c = self._by_key.get(query_key(params))
        if c is None: raise ProviderError("REPLAY_QUERY_NOT_CAPTURED", "la consulta no existe en la captura; el replay nunca fabrica resultados")
        pp, pg = params["per_page"], params["page"]
        orgs = [{"id": r["apollo_org_id"], "name": r["name"], "domain": r["domain"], "website_url": r["website_url"]} for r in c["rows"]]
        return {"organizations": orgs[(pg - 1) * pp: pg * pp]}
