"""F12 — Vista previa del experimento REAL sin ejecutarlo: Search Spec (con señales de crecimiento separadas y ventana como parámetro) -> Research Plan con tope de créditos -> SpendRequest.

No hay red, no hay transporte, no hay gasto: solo el Intent Engine y el Planner existentes, datos del caso y aritmética. Lo que no se sabe sale como UNKNOWN; lo que cuesta dinero sale como AWAITING_USER_DECISION.
"""
import copy, json, os, tempfile

from oi.engine.spec.engine import IntentEngine
from oi.engine.spec.versioning import SpecStore
from oi.engine.planner.planner import Planner
from oi.engine.planner.config import PlannerConfig
from oi.pipeline import guard
from oi.pipeline.cases import load_json

CLOCK = lambda: "2026-10-08T00:00:00+00:00"
RE = "real_experiment"
U = "UNKNOWN"


class PreviewError(Exception):
    pass


def build_spec(cdir, window_months=None):
    """Search Spec v2 confirmada para la prueba real. Las señales salen del catálogo del caso; la ventana viaja como parámetro de cada criterio.
    employee_growth_pct NO es un criterio: sin umbral confirmado el sistema no puede afirmar «creció X %»."""
    cat = load_json(cdir, f"{RE}/growth_signals.json"); d = copy.deepcopy(load_json(cdir, "spec.draft.json"))
    w = window_months or cat["spec_binding"]["params"]["window_months"]
    crit = [c for c in d["criteria"] if c["id"] == "G1"]
    for s in cat["signals"]:
        crit.append({"id": "S-" + s["signal_id"], "label": "Señal de crecimiento: " + s["signal_id"], "dimension": "opportunity", "field": "growth.signal", "operator": "exists", "value": None,
                     "params": {"signal_id": s["signal_id"], "window_months": w}, "strength": "preferred", "origin": "user", "source_text": "en crecimiento",
                     "rationale": s["definition"]})
    d["criteria"] = crit
    eng = IntentEngine(store=SpecStore(os.path.join(tempfile.mkdtemp(), "spec_store.jsonl")), clock=CLOCK, schema_version="2.0")
    r = eng.process(d)
    if r["status"] != "confirmed": raise PreviewError(f"la Search Spec no queda confirmada: {r['status']} {json.dumps(r.get('errors') or r.get('clarifications'), ensure_ascii=False)[:400]}")
    return r["spec"], r


def _budget(exp_budget):
    return {"source": "user_parameter", "max_tool_calls": exp_budget["max_tool_calls"], "max_apollo_credits": exp_budget["max_apollo_credits"], "deep_research_top_n": exp_budget["deep_research_top_n"],
            "infra_calls_max": exp_budget["infra_calls_max"], "max_time_minutes": exp_budget["max_time_minutes"]}


def plan_preview(spec, exp):
    b = exp["budget"]; res = Planner(PlannerConfig(), clock=CLOCK).plan(spec, budget_input=_budget(b))
    pl = res.get("plan")
    if not pl: raise PreviewError(f"el planner no genera plan: {res.get('reason_code')} {json.dumps(res.get('errors'), ensure_ascii=False)[:300]}")
    B = pl["budget"]; ph = B["estimated_usage"]["by_phase"]; cb = B["credit_budget"]["apollo"]; cov = pl["coverage"]
    unit = {"enrich_credits_per_row": 1, "job_postings_credits_per_request": 1}
    ev = ph["EVIDENCE"]; mx = cb["max"]
    unresolved = [e["criterion_id"] for e in pl["evidence_requirements"] if not e.get("resolvable")]
    preview = {
        "status": "PLAN_PREVIEW_NOT_EXECUTED", "plan_status": pl["status"], "executed": False, "network_calls_made_to_build_this": 0,
        "inputs_provenance": {"max_apollo_credits": "USER_CAP_NOT_AN_AUTHORIZATION (10)", "max_tool_calls / infra_calls_max / max_time_minutes / deep_research_top_n": "PROPOSED_BY_ASSISTANT_FOR_USER_CONFIRMATION"},
        "discovery_calls": {"data_calls": ph["DISCOVERY"]["data_calls_max"], "infra_calls": ph["DISCOVERY"]["infra_calls"], "credits": 0, "tool": "apollo.organizations_lookup (gratuito)", "pages": 1, "per_page": 100,
                            "note": "Una página de 100 es la profundidad VERIFICADA (2026-10-01). El total disponible es UNKNOWN (el lookup no devuelve total_count)."},
        "enrichment_calls": {"data_calls_planned": ph["ENRICHMENT"]["data_calls_max"], "companies_planned": B["candidate_capacity"]["enrichment_planned"], "tool": "apollo.organizations_bulk_enrich",
                             "batch_size_assumed": 10, "batch_size_max_verified": 4, "calls_if_batches_of_4": -(-B["candidate_capacity"]["enrichment_planned"] // 4),
                             "note": "El plan supone 1 llamada (lote de 10, NO verificado). Con el máximo probado (4) serían hasta 3 llamadas; el coste en créditos no cambia (1 por fila devuelta)."},
        "estimated_credits": {"enrichment": ph["ENRICHMENT"]["credits"], "evidence_apollo_job_postings": "UNKNOWN (no planificado: HIRING_ACTIVITY está AWAITING_USER_DECISION)", "evidence_firecrawl": "UNKNOWN (herramienta sin auditar)",
                              "total_known": B["estimated_usage"]["totals"]["apollo_credits_estimated_from_observed_cost"], "cost_status": cb["cost_status"], "unit_costs_observed": unit,
                              "upper_bound_rule": "máximo exacto = nº de dominios enviados (1 crédito por fila devuelta no nula, duplicados incluidos); dominio inexistente = 0; re-enriquecer se cobra de nuevo"},
        "reserved_credits": {"apollo": cb["reserve"], "released_when": "solo para cerrar el top-N de evidencia; NO se gasta antes ni sin instrucción"},
        "remaining_budget": {"apollo_cap": mx, "after_estimated_enrichment": mx - ph["ENRICHMENT"]["credits"], "of_which_reserved": cb["reserve"], "tool_calls_headroom": B["estimated_usage"]["headroom_tool_calls"]},
        "expected_candidates": {"discovered_pool_upper_bound": cov["planned_pool"], "pool_basis": "profundidad verificada de 1 página; recall y total UNKNOWN", "enrichment_capacity": cov["planned_enrichment"], "bottleneck": cov["enrichment_capacity"]["bottleneck"],
                                "achievable_qualified_upper_bound": cov["achievable_qualified_target"], "achievable_basis": cov["achievable_basis"], "requested_target_default": cov["requested_target"],
                                "target_reachable": cov["target_reachable"], "note": "El objetivo de 20 viene del planner (la petición no dice cantidad); 10 créditos NO bastan para evaluar 20. 9 es una COTA de capacidad, no una predicción de cuántas pasarán los gates."},
        "top_n_strategy": {"deep_research_top_n": cov["planned_deep_research"], "tiers": cov["evidence_tiers"], "selection": "por orden de VERIFIED_GATES; solo supervivientes de gates HARD", "claims_policy": pl["outcome_policy"]["claims_policy"]},
        "stopping_conditions": [{"id": s["id"], "kind": s["kind"], "outcome": s["outcome"]} for s in pl["stop_conditions"]],
        "budget_thresholds": B["thresholds"], "exhaustion_policy": B["exhaustion_policy"],
        "degradations": pl["degradations"], "approvals_required": pl["approvals"],
        "awaiting_user_decision": [{"item": "confirm_degraded_plan", "why": "el presupuesto reduce el enrichment de 60 a 9 candidatos"},
                                   {"item": "evidence_for_signals", "criteria": unresolved, "why": "firecrawl.search/scrape están sin auditar y sin permiso de pago; apollo.job_postings consume los mismos créditos"},
                                   {"item": "paid_enrichment", "why": "PAID_ENRICHMENT_AUTHORIZED=false"}],
        "unknown": ["total de empresas disponibles en Apollo para el filtro", "precisión del filtro NAICS (medida solo tras revisión humana)", "coste de Firecrawl (search/scrape)", "coste de People Search / enrichment de personas (no se hace)",
                    "tamaño máximo de lote de bulk_enrich (probado 4, declarado 10)", "contador de créditos canónico (87 vs 110 vs 172/180 en libros distintos)", "tasa de supervivencia de gates (sin histórico)"],
        "warnings": [w["code"] for w in pl["warnings"]]}
    return preview, pl


def scenarios(exp):
    cap = exp["budget"]["max_apollo_credits"]; rows = []
    for name, enrich, jobs in (("A_enrich_only", 9, 0), ("B_enrich_plus_job_postings_top5", 5, 5), ("C_would_not_fit", 9, 5)):
        tot = enrich + jobs; rows.append({"scenario": name, "enrichment_credits": enrich, "job_postings_credits": jobs, "total": tot, "cap": cap, "fits_in_cap": tot <= cap,
                                         "note": {"A_enrich_only": "plan del planner: 9 + 1 de reserva sin usar", "B_enrich_plus_job_postings_top5": "ejemplo de reparto alternativo: menos empresas, más señal de contratación", "C_would_not_fit": "9 enriquecidas + 5 peticiones de ofertas = 14 > 10"}[name]})
    return rows


def spend_request(exp, preview, scen):
    cap = exp["budget"]["max_apollo_credits"]
    return {
        "schema": "oi_spend_request.v1", "request_id": "SR-EXP-CS-001-A", "experiment_id": exp["experiment_id"], "status": "AWAITING_USER_DECISION", "paid_enrichment_authorized": guard.paid_enrichment_authorized(),
        "what_we_want_to_buy": {"tool": "apollo_organizations_bulk_enrich (o organizations_enrich)", "description": "Enrichment de ORGANIZACIÓN (no de personas): industria, códigos NAICS/SIC, país/ubicación, plantilla estimada, crecimiento de plantilla 6/12/24 meses, eventos de financiación, teléfono y web corporativos.",
                                "unit_cost_observed": "1 crédito por fila devuelta no nula (duplicados incluidos); inexistente = 0; re-enrich se cobra de nuevo"},
        "for_which_companies": {"scope": "máximo 9 dominios únicos elegidos con el método de selección del experimento (muestra revisada por un humano; solo etiquetas RELEVANT/UNCERTAIN; con dominio; no enriquecidos antes)", "max_companies": 9,
                                "selection_method": exp["selection_method"]["enrichment_sample"]},
        "missing_data": ["sector y país VERIFICABLES de cada candidato (el lookup no los devuelve)", "tamaño (el lookup solo lo filtra)", "dirección del cambio de plantilla en 24 meses (EMPLOYEE_GROWTH, ESTIMATED)", "eventos de inversión con fecha (INVESTMENT, parcial)"],
        "commercial_relevance": "Sin esos datos solo hay nombres y dominios: no se puede afirmar que una empresa sea una constructora española ni que haya señales de crecimiento; es decir, no hay oportunidad que presentar.",
        "free_alternative": {"exists": "PARCIAL", "options": ["Lookup gratuito (ya hecho: candidatos sin atributos)", "Revisión humana gratuita de nombre/dominio/web pública para sector, país y tamaño aproximado (más lenta; sirve como evidencia KNOWN si el revisor cita la fuente oficial)",
                                                          "WebSearch/WebFetch de la sesión para señales (0 créditos Apollo; es «investigación real», excluida de F12; requeriría tu decisión)"],
                            "limit": "Ninguna alternativa gratuita produce la estimación de plantilla ni el crecimiento a 24 meses del proveedor."},
        "cost": {"estimated_credits": preview["estimated_credits"]["total_known"], "expected": "= nº de dominios enviados (≤ 9)", "maximum": cap, "hard_cap_enforced_by": "el operador no envía más dominios que el crédito autorizado y lee el saldo antes/después de cada llamada",
                 "unknown_costs": ["Firecrawl", "job postings de pago por petición (1 observado; NO incluido)"], "scenarios": scen},
        "if_budget_is_exhausted": "STOP. No se relaja ningún gate, no se rellena con INFERRED, no se reintenta ni se usa la reserva sin instrucción. Se informa lo evaluado como `partial`/`budget_exhausted`, con el resto en NOT_ASSESSED. Umbral blando 80 % (degradar), duro 90 % (parar).",
        "expected_evidence": [{"field": "company:sector", "from": "industry/naics_codes del enrichment", "claim_status_ceiling": "INFERRED (multi-etiqueta) hasta que un humano cite fuente oficial → KNOWN"}, {"field": "company:country", "from": "ubicación estructurada vs raw_address", "claim_status_ceiling": "KNOWN solo si ambas coinciden; si discrepan → CONFLICTING"},
                              {"field": "employee_count", "from": "estimated_num_employees", "claim_status_ceiling": "ESTIMATED"}, {"field": "growth.signal:EMPLOYEE_GROWTH", "from": "organization_headcount_twenty_four_month_growth > 0", "claim_status_ceiling": "ESTIMATED (dirección, nunca %)"},
                              {"field": "growth.signal:INVESTMENT", "from": "funding_events con fecha", "claim_status_ceiling": "ESTIMATED/KNOWN; cobertura parcial"}],
        "will_not_buy": [{"item": "People Search / People Enrichment", "reason": "fuera de F12/F13"}, {"item": "teléfonos móviles / direct dials", "reason": "dato personal de pago"}, {"item": "enriquecimiento personal de contacto", "reason": "no autorizado"},
                         {"item": "apollo_mixed_companies_search", "reason": "1 crédito por petición; el lookup gratuito cubre el discovery (y la auditoría de Fase 2 lo midió «no accesible en el plan»: contradicción a aclarar)"},
                         {"item": "apollo_organizations_job_postings", "reason": "AWAITING_USER_DECISION: consume los mismos créditos"}, {"item": "Firecrawl de pago / OpenRush / scraping real", "reason": "excluidos"},
                         {"item": "contactos, secuencias, emails, listas, tareas, CRM de Apollo", "reason": "ninguna escritura ni envío"}],
        "allowed_operations_if_authorized": {"free": ["apollo_organizations_lookup", "apollo_users_api_profile (saldo)", "apollo_usage_stats_credit_usage_stats (saldo)"], "paid": ["apollo_organizations_bulk_enrich", "apollo_organizations_enrich"]},
        "authorization_required_exact": {"phrase_template": "Autorizo hasta N créditos Apollo para F13, solo organizations_bulk_enrich/organizations_enrich sobre ≤ N dominios únicos de la muestra del experimento; sin People, sin job_postings, sin Firecrawl.",
                                         "recommended_N": 9, "alternative_N": cap, "effect": "paid_enrichment_authorized pasa a true SOLO para este experimento y este tope; sin esa frase, sigue false",
                                         "also_needed": ["confirmar las decisiones abiertas D1–D9 del experimento", "confirmar el plan degradado (60→9)"]}}
