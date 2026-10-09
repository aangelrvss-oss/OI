"""Adapter de Apollo (solo lectura). No contiene cliente de red: produce (operación, parámetros) y parsea respuestas.
Las equivalencias criterio -> parámetro llevan su estado de verificación; nada se presenta como verificado si no lo está."""
import re
from oi.engine.spec.text import norm
from oi.engine.evaluators.entity import normalize_domain
from ..errors import ProviderError

ISO_TO_APOLLO_COUNTRY = {"ES": "Spain", "PT": "Portugal", "FR": "France"}   # 'Spain' observado en la Fase 2; el resto, sin verificar
OBSERVED_EMPLOYEE_BUCKETS = [(51, 200), (201, 500)]   # únicos tramos usados en la Fase 2 (formato 'min,max'); otros tramos sin verificar


class ApolloAdapter:
    provider = "apollo"
    tool_ids = {"org_discovery": "apollo.org_lookup", "org_enrich_bulk": "apollo.org_enrich", "credit_balance": None, "org_job_postings": "apollo.job_postings"}
    READ_OPERATIONS = {"org_discovery": "apollo_organizations_lookup", "org_enrich_bulk": "apollo_organizations_bulk_enrich", "credit_balance": "apollo_users_api_profile", "org_job_postings": "apollo_organizations_job_postings"}
    LOOKUP_PARAMS = {"organization_naics_codes": "PARTIALLY_VERIFIED", "q_organization_keyword_tags": "PARTIALLY_VERIFIED", "organization_locations": "PARTIALLY_VERIFIED", "organization_num_employees_ranges": "PARTIALLY_VERIFIED"}   # solo estos; el resto: rechazado
    CREDIT_FIELD = "num_credits_remaining"

    def supports_tool(self, tool_id): return tool_id in self.tool_ids.values()

    def capabilities(self):
        return {"provider": "apollo",
                "pagination": {"supported_status": "UNVERIFIED", "enabled_for_experiment": False, "implementation_ready": True, "type": "page", "observed_per_page_max": 100,
                               "note": "solo se ha observado la página 1 con per_page=100 y páginas 1-2 con per_page=10; más profundidad sin verificar (no prohibida)"},
                "credit_measurement": {"operation": self.READ_OPERATIONS["credit_balance"], "params": {"include_credit_usage": True}, "field": self.CREDIT_FIELD, "status": "PARTIALLY_VERIFIED",
                                       "field_status": "UNVERIFIED: el nombre exacto del campo de saldo se confirma con la PRIMERA lectura real; si no se encuentra, ninguna llamada de pago se ejecuta",
                                       "note": "en la Fase 2 solo el delta de créditos restantes fue fiable; puede verse afectado por uso concurrente de la cuenta"},
                "enrichment_identifier": {"verified": ["domain"], "unverified": ["id"]},
                "bulk_batch_size": {"configured": 10, "status": "provisional", "max_verified": "unknown", "max_tested": 4},
                "lookup_params": dict(self.LOOKUP_PARAMS), "job_postings": {"cost": "1 crédito/petición (observado)", "fields": ["id", "title", "url", "city", "state", "country", "posted_at", "last_seen_at"], "no_department_no_description": True, "response_shape_status": "UNVERIFIED (parser tolerante)"},
                "filter_mappings": {"firmographic.employee_count": {"param": "organization_num_employees_ranges", "format": "tramos observados '51,200','201,500'", "status": "PARTIALLY_VERIFIED",
                                                                     "note": "solo pista de recall: un spec 50-500 no recupera la empresa de exactamente 50 por el filtro; el gate verificado usa [50,500]"},
                                    "restrictions.geography": {"param": "organization_locations", "status": "PARTIALLY_VERIFIED (solo 'Spain' observado)"},
                                    "firmographic.industry": {"param": "q_organization_keyword_tags", "status": "UNVERIFIED para texto en español (recall hint)"}}}

    # ── peticiones
    def build_request(self, logical_op, generic):
        if logical_op not in self.READ_OPERATIONS:
            raise ProviderError("OPERATION_NOT_ALLOWED", f"operación lógica no permitida: {logical_op}")
        op = self.READ_OPERATIONS[logical_op]
        if logical_op == "org_discovery":
            p = {"page": generic["page"], "per_page": generic["per_page"]}
            for f in generic.get("filters", []):
                if "param" in f and "field" not in f:       # consulta estructurada (query del plan): parámetro del proveedor con estado de verificación
                    if f["param"] not in self.LOOKUP_PARAMS or not isinstance(f["value"], list) or not f["value"]:
                        raise ProviderError("OPERATION_NOT_ALLOWED", f"parámetro de discovery no permitido: {f['param']}")
                    if f["param"] == "organization_naics_codes" and not all(isinstance(c, str) and re.fullmatch(r"[0-9]{2,5}", c) for c in f["value"]):
                        raise ProviderError("INVALID_PARAMETERS", "organization_naics_codes: el proveedor exige 2 a 5 dígitos (prefijo)")      # contrato del esquema del proveedor
                    p[f["param"]] = list(f["value"]); continue
                if f["field"] == "firmographic.employee_count" and isinstance(f["value"], list) and len(f["value"]) == 2:
                    lo, hi = f["value"]
                    b = [f"{a},{z}" for a, z in OBSERVED_EMPLOYEE_BUCKETS if a <= hi and z >= lo]   # pista de recall con tramos observados; el gate verificado decide
                    if b: p["organization_num_employees_ranges"] = b
                elif f["field"] == "restrictions.geography":
                    locs = [ISO_TO_APOLLO_COUNTRY.get(g["name"], g["name"]) if g.get("level") == "country" else g["name"] for g in f["value"]]
                    p["organization_locations"] = locs
                elif f["field"] == "firmographic.industry" and isinstance(f["value"], str):
                    p["q_organization_keyword_tags"] = [f["value"]]
            return op, p
        if logical_op == "org_job_postings":
            if not generic.get("id"): raise ProviderError("MALFORMED_RESULT", "petición de job postings sin id de organización")
            return op, {"id": generic["id"], "page": generic.get("page", 1), "per_page": generic.get("per_page", 10)}
        if logical_op == "org_enrich_bulk":
            if not generic.get("domains"): raise ProviderError("MALFORMED_RESULT", "petición de enrichment sin dominios")
            return op, {"domains": list(generic["domains"])}
        return op, {"include_credit_usage": True}

    # ── respuestas
    def parse(self, logical_op, raw):
        if logical_op == "credit_balance":
            v = _find(raw, self.CREDIT_FIELD)
            return {"credits_remaining": v if isinstance(v, (int, float)) and not isinstance(v, bool) else None}
        if logical_op == "org_job_postings":
            if not isinstance(raw, dict): raise ProviderError("MALFORMED_RESULT", "respuesta de job postings no es un objeto")
            lst = next((raw[k] for k in ("organization_job_postings", "job_postings", "postings", "jobs") if isinstance(raw.get(k), list)), None)
            if lst is None: lst = next((v for v in raw.values() if isinstance(v, list) and v and isinstance(v[0], dict) and "title" in v[0]), [])
            items = [{k: o.get(k) for k in ("id", "title", "url", "city", "state", "country", "posted_at", "last_seen_at")} for o in lst if isinstance(o, dict)]
            return {"items": items, "pagination": raw.get("pagination") if isinstance(raw.get("pagination"), dict) else None}
        if not isinstance(raw, dict) or not isinstance(raw.get("organizations"), list):
            raise ProviderError("MALFORMED_RESULT", "la respuesta no contiene una lista 'organizations'")
        orgs = raw["organizations"]
        if logical_op == "org_discovery":
            out = []
            for o in orgs:
                if not isinstance(o, dict) or not o.get("id") or not o.get("name"):
                    raise ProviderError("MALFORMED_RESULT", "organización sin id o name")
                out.append({"id": o["id"], "name": o["name"], "domain": o.get("domain") or o.get("primary_domain"), "website_url": o.get("website_url")})
            return {"items": out}
        if logical_op == "org_enrich_bulk":
            recs, by_id, alls = {}, {}, []
            for o in orgs:
                if o is None: continue                         # fila nula: no devuelta (no facturada según la Fase 2)
                if not isinstance(o, dict): raise ProviderError("MALFORMED_RESULT", "registro enriquecido no es un objeto")
                alls.append(o)
                if o.get("id"): by_id[o["id"]] = o
                d = normalize_domain(o.get("primary_domain") or o.get("website_url") or o.get("domain"))
                if d: recs[d] = o
            return {"records": recs, "by_id": by_id, "all": alls}     # 'all' conserva también los registros SIN dominio (p. ej. el de Minor en 3C)
        raise ProviderError("OPERATION_NOT_ALLOWED", logical_op)


def _find(o, key):
    if isinstance(o, dict):
        if key in o: return o[key]
        for v in o.values():
            r = _find(v, key)
            if r is not None: return r
    if isinstance(o, list):
        for v in o:
            r = _find(v, key)
            if r is not None: return r
    return None
