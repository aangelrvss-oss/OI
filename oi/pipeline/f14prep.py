"""F13.1 — Preparación del primer experimento real (EXP-CS-001 / F14) SIN gasto. Funciones puras y deterministas: sin red, sin transporte, sin reloj.
Genera decisiones, políticas, definición del experimento, métricas, protocolo de revisión humana, condiciones de parada y el PLAN PREVIEW definitivo (0 / 5 / 10 créditos).
Nada aquí ejecuta ni autoriza gasto: `paid_enrichment_authorized` se LEE de la barrera (guard) y debe seguir false."""
import csv, io, json, os

from oi.engine.planner.config import PlannerConfig
from oi.engine.planner.planner import Planner
from oi.pipeline import guard, preview as P
from oi.pipeline.cases import load_json

OUT = "f14_preparation"
ACCOUNT_BALANCE_OBSERVED = 87          # saldo de CUENTA observado en F12/F13 (users_api_profile). NO es presupuesto ni se escribe en ningún libro.
F14_EXPERIMENT_BUDGET = 10             # máximo POTENCIAL del experimento F14. NO es una autorización.
SIGNALS = ("EMPLOYEE_GROWTH", "HIRING_ACTIVITY", "NEW_PROJECTS", "GEOGRAPHIC_EXPANSION", "CAPACITY_EXPANSION", "INVESTMENT")
TBC = "TO_BE_CALIBRATED"


def decisions():
    return {"schema": "oi_f13_1_decisions.v1", "phase": "F13.1", "date": "2026-10-08",
            "credits": {
                "A_account_balance": {"value": ACCOUNT_BALANCE_OBSERVED, "unit": "apollo_credits", "meaning": "saldo de la cuenta Apollo observado en F12/F13 (users_api_profile.num_credits_remaining)", "observed_at": "2026-10-08",
                                       "does_not_overwrite": ["ledgers históricos", "presupuestos de experimentos anteriores", "hashes históricos", "límites históricos"], "used_as_budget": False},
                "B_experiment_budget": {"experiment": "F14 / EXP-CS-001", "max_apollo_credits": F14_EXPERIMENT_BUDGET, "unit": "apollo_credits", "status": "POTENTIAL_MAXIMUM_NOT_AUTHORIZED",
                                         "independent_of": "A_account_balance"},
                "spent_in_f13_1": 0, "paid_enrichment_authorized": guard.paid_enrichment_authorized(),
                "historical_contradiction": "110 (libro de Fase 3) / 172–180 (auditoría de Fase 2) / 87 (hoy): documentada, NO resuelta; ninguna cifra se reescribe en ningún libro. El saldo de cuenta y el presupuesto del experimento son magnitudes distintas.",
                "reconciliation_rule": "En F14 el operador lee el saldo ANTES y DESPUÉS de cada llamada de pago; el gasto del experimento = saldo_antes − saldo_después, medido contra el presupuesto del experimento (10), nunca contra 87."},
            "authorization": {"granted": False, "requires": "AUTHORIZE F14 UP TO 10 APOLLO CREDITS (frase explícita del usuario en una conversación posterior)"}}


def evidence_policy():
    prim = ["FIRST_PARTY_WEB", "HUMAN_REVIEW", "OTHER_VERIFIABLE"]
    return {"schema": "oi_f14_evidence_policy.v1", "taxonomy_preserved": {"claim_status": ["KNOWN", "INFERRED", "ESTIMATED", "UNKNOWN", "CONFLICTING"], "confidence": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                                                                         "source_class": ["OFFICIAL", "PROFESSIONAL_DIRECTORY", "SECTOR_SOURCE", "OTHER_VERIFIABLE", "UNVERIFIED", "NOT_FOUND"]},
            "never": ["UNKNOWN == NO", "UNKNOWN == PASS", "INFERRED == VERIFIED", "ESTIMATED == KNOWN", "CONFLICTING == PASS"],
            "origins": {
                "FIRST_PARTY_WEB": {"role": "PRIMARY", "may_support": ["identidad", "actividad", "ubicación/país", "descripción", "proyectos", "expansión", "capacidad", "inversión", "hechos publicados explícitamente"], "must_not": "inventar datos que no aparezcan"},
                "OTHER_VERIFIABLE": {"role": "PRIMARY", "examples": ["memoria corporativa", "informe anual", "página institucional", "comunicado oficial", "página de proyectos", "página oficial de empleo", "documentación corporativa"], "must": "citar fuente, fecha y etiqueta"},
                "HUMAN_REVIEW": {"role": "PRIMARY_EVALUATION", "must": "etiquetarse HUMAN_REVIEW; nunca presentarse como evidencia automática; un SÍ humano no es FIT_VERIFIED"},
                "APOLLO_FREE_LOOKUP": {"role": "DISCOVERY_ONLY", "may_support": ["apollo_org_id", "nombre", "dominio", "web"], "never_supports": ["sector", "país", "tamaño", "crecimiento", "contactos"], "enforced_by": "oi.evidence.intake.check"},
                "APOLLO_ENRICHMENT": {"role": "SECONDARY_NOT_VERIFYING", "may_support": ["pista de sector (etiqueta autodeclarada)", "ubicación estructurada", "plantilla estimada", "dirección de cambio de plantilla", "eventos de financiación"],
                                      "claim_status_ceiling": "ESTIMATED / INFERRED", "never": "KNOWN ni VERIFIED por sí solo", "enforced_by": "oi.evidence.intake.check + oi.qualification.qualifier (el dato del proveedor no pasa de PENDING_EVIDENCE)"}},
            "source_class_for_known_by_claim": {"sector (G1)": ["OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE"], "país (GEO)": ["OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE"], "tamaño (SIZE)": ["OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE"],
                                                "encaje con software (SOFTWARE_FIT)": ["OFFICIAL", "SECTOR_SOURCE", "OTHER_VERIFIABLE"],
                                                "nota": "PROFESSIONAL_DIRECTORY NO basta para KNOWN en este experimento (el contrato general sí lo permite en G1/GEO; el perfil de F14 lo estrecha: `acceptable_source_classes_override`)."},
            "primary_origins": prim, "web_research_note": "La investigación web de F14 la hace una persona o el operador con herramientas gratuitas ya permitidas; Firecrawl de pago / OpenRush / scraping siguen excluidos."}


def eligibility_policy():
    ap = "Apollo NO puede resolverlo por sí solo."
    return {"schema": "oi_f14_eligibility_policy.v1", "rule": "ELIGIBLE exige que TODAS las puertas bloqueantes estén en PASS con evidencia KNOWN (rules/qualification.v1.json, regla ELIGIBLE). Si falta evidencia: PENDING_EVIDENCE (o NOT_ASSESSED si no hay ninguna). No se fuerza ningún resultado.",
            "mandatory": [
                {"n": 1, "gate_id": "IDENTITY", "claim": "identidad de la empresa", "evidence_field": "company:identity", "pass_requires": "evidencia KNOWN cuyo valor sea el apollo_org_id (revisión humana o fuente oficial)", "apollo_alone": ap + " El lookup no confirma identidad."},
                {"n": 2, "gate_id": "G1 + BUSINESS_MODEL", "claim": "actividad compatible con construcción (contratista)", "evidence_field": "company:sector = CONSTRUCTION; company:business_model = contractor",
                 "pass_requires": "KNOWN de web oficial / documento oficial / revisión humana con fuente citada", "apollo_alone": ap + " NAICS 236–238 ≠ construction_company; la etiqueta de industria de Apollo es autodeclarada (INFERRED). Promotora ≠ constructora salvo evidencia de actividad constructora."},
                {"n": 3, "gate_id": "GEO", "claim": "España", "evidence_field": "company:country = ES", "pass_requires": "KNOWN oficial/humana", "apollo_alone": ap + " Un dominio .es no prueba que la empresa sea española; el filtro de ubicación es una pista de recall."},
                {"n": 4, "gate_id": "SIZE", "claim": "51–500 empleados (D1: decisión de este experimento, no regla global)", "evidence_field": "company:employee_range = 51-500", "pass_requires": "KNOWN oficial/humana; la plantilla estimada de Apollo es ESTIMATED y deja PENDING_EVIDENCE", "apollo_alone": ap + " El rango enviado como filtro no es evidencia."},
                {"n": 5, "gate_id": "GROWTH", "claim": "requisito de crecimiento de la Search Spec: al menos 1 de las 6 señales PRESENT en 24 meses (agregación ANY del catálogo; sin umbral numérico)",
                 "evidence_field": "company:growth_signal:<SIGNAL>", "pass_requires": "señal KNOWN, fechada (dated_on) y dentro de la ventana de 24 meses (parámetro del criterio)", "apollo_alone": ap + " Un campo de plantilla que existe no es «la empresa crece»; ESTIMATED deja PENDING_EVIDENCE."},
                {"n": 6, "gate_id": "SOFTWARE_FIT", "claim": "encaje con software de gestión", "evidence_field": "company:software_management_fit = YES", "pass_requires": "indicio citado (revisión humana u oficial)", "apollo_alone": ap},
                {"n": 7, "gate_id": "ENTITY_LEVEL", "claim": "nivel de entidad COMPANY (no establecimiento/grupo)", "evidence_field": "company:entity_level = COMPANY", "pass_requires": "KNOWN", "apollo_alone": ap}],
            "desirable_not_gating": ["contacto recomendado (A/B/C) — Contacto disponible ≠ oportunidad válida", "señales adicionales de las 6 (se informan una a una)", "intensidad de la señal (TO_BE_CALIBRATED)"],
            "state_mapping": {"ELIGIBLE": "todas las obligatorias PASS+KNOWN", "PENDING_EVIDENCE": "alguna no KNOWN (UNKNOWN/INFERRED/ESTIMATED/CONFLICTING) y ninguna FAIL, con alguna evidencia", "NOT_ELIGIBLE": "alguna obligatoria FAIL con evidencia KNOWN",
                              "NOT_ASSESSED": "sin enrichment ni evidencia para esa empresa", "UNKNOWN": "ninguna puerta evaluable", "HUMAN_REVIEW": "acción recomendada cuando hay contradicción o ambigüedad que decide una persona", "MAPPING_REQUIRED": "estado de la ejecución: término de sector sin mapeo"}}


def growth_policy():
    return {"schema": "oi_f14_growth_policy.v1", "signals": list(SIGNALS), "window_months": 24, "window_lives_in": "Search Spec: criterios S-<SIGNAL>.params.window_months",
            "numeric_threshold": {"employee_growth_pct": "USER_CONFIRMATION_REQUIRED", "value": None, "rule": "no se introduce ningún umbral; el sistema puede decir «hay señal» pero no «creció X %»"},
            "four_dimensions": {"existence": "state (PRESENT / NOT_OBSERVED / UNKNOWN / CONFLICTING) — PRESENT solo con KNOWN/ESTIMATED fechada y en ventana", "intensity": f"value — {TBC}; sin umbral no se califica la intensidad",
                                "source": "evidence_ids → evidence_origin + source_class de cada evidencia", "verification": "claim_status (KNOWN / ESTIMATED / INFERRED / UNKNOWN) — es lo que decide si la puerta GROWTH pasa"},
            "rules": ["employee_growth field exists ≠ company is growing: un campo presente sin valor, o sin fecha, es UNKNOWN", "ausencia de hallazgo = UNKNOWN (nunca NOT_OBSERVED ni FAIL)", "INFERRED nunca es PRESENT", "ESTIMATED es PRESENT pero deja la puerta en PENDING_EVIDENCE (no KNOWN)",
                      "las 6 señales se informan separadas y nunca se funden en un indicador único", "aggregation ANY solo decide la puerta GROWTH"],
            "source_status": {"EMPLOYEE_GROWTH": "Apollo enrichment (ESTIMATED máx.) o dos cifras fechadas citables", "HIRING_ACTIVITY": "job_postings de pago (NO incluido en F14) o página oficial de empleo", "NEW_PROJECTS": "web oficial / página de proyectos",
                              "GEOGRAPHIC_EXPANSION": "web oficial / comunicados", "CAPACITY_EXPANSION": "web oficial / memoria", "INVESTMENT": "funding_events del enrichment (parcial) o informe/comunicado oficial"}}


def experiment(cid):
    return {"schema": "oi_experiment.v2", "experiment_id": "EXP-CS-001", "phase": "F14", "case": "Construction + Software Management", "case_id": cid, "status": "DEFINED_NOT_EXECUTED", "paid_enrichment_authorized": guard.paid_enrichment_authorized(),
            "hypothesis": "El pipeline puede convertir un lookup gratuito + enrichment de organización acotado + evidencia verificable en estados auditables de cualificación para constructoras españolas de 51–500 empleados con señales de crecimiento, sin promover jamás UNKNOWN/INFERRED/ESTIMATED a PASS/KNOWN.",
            "objective": "Medir qué parte del pipeline funciona con datos reales y cuánto cuesta cada estado auditable; NO conseguir 20 empresas ni validar el negocio.",
            "population": "Empresas españolas relacionadas con construcción, 51–500 empleados, con señales de crecimiento durante los últimos 24 meses y potencial necesidad de software de gestión.",
            "mandatory_criteria": ["IDENTITY", "G1 sector construcción", "BUSINESS_MODEL contractor", "GEO España", "SIZE 51–500 (D1)", "GROWTH (≥1 de 6 señales, 24 m)", "SOFTWARE_FIT", "ENTITY_LEVEL COMPANY"],
            "desirable_criteria": ["contacto recomendado A/B/C", "varias señales de crecimiento a la vez", "dominio disponible (permite enrichment por dominio)"],
            "required_evidence": {"IDENTITY": ["HUMAN_REVIEW | FIRST_PARTY_WEB | OTHER_VERIFIABLE (KNOWN)"], "G1/BUSINESS_MODEL": ["FIRST_PARTY_WEB | OTHER_VERIFIABLE | HUMAN_REVIEW con fuente citada"], "GEO": ["idem"], "SIZE": ["idem"], "GROWTH": ["≥1 señal KNOWN fechada en 24 m"], "SOFTWARE_FIT": ["HUMAN_REVIEW u oficial con indicio citado"]},
            "optional_evidence": ["Apollo enrichment (APOLLO_ENRICHMENT; pista, nunca KNOWN)", "contacto de empresa/departamento verificable", "señales adicionales"],
            "search_spec": {"source": "cases/" + cid + "/real_experiment/search_spec.real.json", "version_basis": "F12 (Search Spec v2 confirmada; registro 1.3.0)", "growth": "6 criterios growth.signal, ventana 24 m, sin umbral"},
            "sample": {"discovery": "Q1 (NAICS 236/237/238) y Q2 (keyword construction): lookup GRATUITO ya capturado (44 únicos); no se hacen consultas nuevas salvo instrucción", "enrichment_max_companies": 9, "selection": "muestra de la hoja de revisión humana: solo RELEVANT/UNCERTAIN con dominio y sin enriquecer antes (hash de la lista fijado antes del gasto)",
                       "reserve_credits": 1},
            "budget": {"experiment_max_apollo_credits": F14_EXPERIMENT_BUDGET, "max_paid_calls": 3, "max_paid_calls_basis": "ceil(9 dominios / 4 por lote verificado) = 3; el plan asume 1 llamada con lote 10 (NO verificado)", "account_balance_observed": ACCOUNT_BALANCE_OBSERVED,
                       "account_balance_role": "informativo; no es presupuesto", "authorization": "NOT GRANTED"},
            "allowed_sources": ["apollo_organizations_lookup (gratis)", "apollo_users_api_profile (saldo, gratis)", "apollo_organizations_bulk_enrich / organizations_enrich (de pago, SOLO si se autoriza)", "web oficial de la empresa (lectura humana/gratuita)", "documentos oficiales", "revisión humana etiquetada HUMAN_REVIEW"],
            "disallowed_sources": ["apollo_mixed_companies_search", "People Search / People Enrichment", "teléfonos móviles / direct dials", "apollo_organizations_job_postings (AWAITING_USER_DECISION)", "Firecrawl de pago", "OpenRush", "scraping real masivo", "contactos/secuencias/emails/listas/tareas/CRM de Apollo", "inferir emails"]}


def metrics():
    m = lambda name, how: {"metric": name, "how": how, "target": TBC}
    return {"schema": "oi_f14_metrics.v1", "experiment_id": "EXP-CS-001", "baseline_policy": "El primer experimento FIJA la línea base; ningún objetivo numérico se inventa antes (TO_BE_CALIBRATED).",
            "discovery": [m("candidatos descubiertos", "filas devueltas por el lookup"), m("candidatos únicos", "entidades tras entity resolution"), m("duplicados", "filas − entidades"), m("descartados por identidad", "identity_check ≠ KNOWN_MATCH tras enrichment o sin evidencia de identidad")],
            "qualification": [m(s, "recuento de registros") for s in ("ELIGIBLE", "PENDING_EVIDENCE", "NOT_ELIGIBLE", "HUMAN_REVIEW (acción)", "UNKNOWN / NOT_ASSESSED")],
            "evidence": [m("% con evidencia oficial (FIRST_PARTY_WEB / OTHER_VERIFIABLE)", "registros con ≥1 evidencia KNOWN de esas orígenes / evaluados"), m("% con evidencia suficiente", "registros con todas las puertas obligatorias PASS+KNOWN / evaluados"),
                         m("claims sin evidencia", "nº de puertas obligatorias UNKNOWN/UNKNOWN"), m("contradicciones", "puertas CONFLICTING")],
            "commercial_utility": [m("oportunidades accionables", "ELIGIBLE con contacto recomendado A/B/C y acción CONTACT"), m("oportunidades con contacto", "recommended_contact ≠ null"), m("requieren revisión humana", "acción HUMAN_REVIEW"), m("sin contacto", "recommended_contact = null")],
            "cost": [m("créditos gastados", "saldo_antes − saldo_después (operador) y ledger del executor; deben coincidir"), m("coste por candidato", "créditos / candidatos evaluados"), m("coste por oportunidad defendible", "créditos / (ELIGIBLE + PENDING_EVIDENCE con ≥4 puertas KNOWN)"),
                     m("coste por oportunidad accionable", "créditos / accionables")],
            "integrity_invariants": [{"id": "INV-1", "text": "ningún UNKNOWN/INFERRED/ESTIMATED/CONFLICTING aparece como PASS/KNOWN", "target": "0 violaciones (no es un objetivo comercial)"}, {"id": "INV-2", "text": "créditos ≤ 10 y coinciden con el saldo", "target": "exacto"}]}


def review_protocol(cid):
    f = ["sector_fit", "country_fit", "size_fit", "growth_signal", "software_need", "overall_fit"]
    return {"schema": "oi_human_review_protocol.v2", "experiment_id": "EXP-CS-001", "sheet_origin": "cases/" + cid + "/real_experiment/human_review_sheet.csv (59 filas; se CONSERVA sin tocar y sin rellenar)",
            "fields": {k: {"values": ["YES", "NO", "UNKNOWN"]} for k in f} | {"review_reason": {"optional": True, "text": "motivo libre; obligatorio con NO"}}, "extra_columns": ["source_consulted_url", "reviewer", "reviewed_at", "evidence_origin_declared"],
            "rules": ["Un campo vacío NO es UNKNOWN: es «sin revisar». UNKNOWN se escribe explícitamente cuando no se puede decidir con información pública razonable.", "YES exige source_consulted_url (página pública oficial); sin fuente, UNKNOWN.",
                      "No uses la salida del sistema como fuente.", "overall_fit = YES no implica FIT_VERIFIED ni que las puertas pasen (un SÍ humano no es FIT_VERIFIED).",
                      "Un NO con review_reason alimenta el ground truth de false positives."],
            "purpose": "Evaluar el sistema, no ocultar sus fallos: la etiqueta humana se compara con el estado que el sistema produjo ANTES de verla. Nunca se corrige el estado del sistema para que coincida con la etiqueta; los desacuerdos se cuentan como errores del sistema o del revisor.",
            "no_look_good_rule": "Que el HTML se vea bien no cuenta como éxito.", "ground_truth_use": "overall_fit YES/NO → precisión de ELIGIBLE/PENDING_EVIDENCE; sector_fit/country_fit/size_fit → calidad por puerta; software_need → utilidad comercial. TO_BE_CALIBRATED hasta tener la primera tanda."}


def review_template(cd):
    src = os.path.join(cd, P.RE, "human_review_sheet.csv")
    with open(src, encoding="utf-8", newline="") as f: rows = list(csv.DictReader(f))
    cols = ["review_id", "apollo_org_id", "name", "domain", "found_by", "sector_fit", "country_fit", "size_fit", "growth_signal", "software_need", "overall_fit", "review_reason", "source_consulted_url", "reviewer", "reviewed_at", "evidence_origin_declared"]
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n"); w.writeheader()
    for r in rows: w.writerow({c: r.get(c, "") if c in ("review_id", "apollo_org_id", "name", "domain", "found_by") else "" for c in cols})
    return buf.getvalue(), len(rows)


def stop_conditions():
    return {"schema": "oi_f14_stop_conditions.v1", "principle": "Cualquiera detiene el experimento ANTES de gastar más; no se inventa ningún umbral comercial.", "conditions": [
        {"id": "SC-01", "when": "la fuente no devuelve los datos necesarios (el enrichment no trae ubicación, plantilla ni industria para la mayoría de dominios enviados)", "action": "STOP; informar «el proveedor no resuelve las puertas»; no reintentar"},
        {"id": "SC-02", "when": "no existe evidencia suficiente para los claims críticos (sector, país, tamaño, identidad) tras la revisión de la primera tanda", "action": "STOP; el resto queda PENDING_EVIDENCE; no se gasta el resto del presupuesto"},
        {"id": "SC-03", "when": "el coste real supera lo esperado: saldo_antes − saldo_después > filas devueltas no nulas, o el gasto acumulado > 10", "action": "STOP_AND_REPORT; no más llamadas"},
        {"id": "SC-04", "when": "el proveedor devuelve resultados claramente incompatibles (p. ej. empresas de otro país o sector en la mayoría de la tanda)", "action": "STOP; registrar como hallazgo"},
        {"id": "SC-05", "when": "se detecta una contradicción grave (fuentes KNOWN en conflicto en una puerta bloqueante, o contadores de créditos que no cuadran)", "action": "STOP; HUMAN_REVIEW"},
        {"id": "SC-06", "when": "el pipeline no puede producir estados auditables (error de contrato, registro inválido, evidencia sin origen)", "action": "STOP; INVALIDATES"},
        {"id": "SC-07", "when": "se solicitaría una operación fuera de la lista permitida (People, mixed_companies_search, job_postings sin decisión, contactos)", "action": "STOP; rechazada por la barrera"},
        {"id": "SC-08", "when": "se alcanza el umbral duro del presupuesto del plan (90 % de lo asignado) o 9 créditos", "action": "STOP; la reserva (1) no se usa sin instrucción"},
        {"id": "SC-09", "when": "se intentaría enriquecer dos veces la misma empresa", "action": "STOP"}],
            "tiers": {"batch_1": "enriquecer 1 lote de ≤4 dominios, leer saldo, comprobar SC-01..SC-05; solo si todo cuadra, continuar con el siguiente lote (checkpoint por lote)"}}


def _plan_for(spec, prof, credits):
    b = {"source": "user_parameter", "max_tool_calls": 30, "max_apollo_credits": credits, "deep_research_top_n": 5, "infra_calls_max": 60, "max_time_minutes": 60}
    r = Planner(PlannerConfig(), clock=P.CLOCK).plan(spec, budget_input=b, llm_proposals={"discovery_queries": prof["discovery_queries"]}); pl = r["plan"]
    row = {"max_apollo_credits": credits, "planner_status": pl["status"], "executed": False}
    if pl["status"] == "blocked":
        return row | {"what_it_would_do": "NADA de pago: el plan se bloquea (" + "; ".join(pl["blocked_reasons"]) + "). Solo seguiría disponible el lookup gratuito ya capturado y la revisión humana/oficial (coste 0).", "enrichment_companies": 0,
                       "paid_calls": 0, "estimated_credits": 0, "reserved_credits": 0, "approvals_required": []}
    B = pl["budget"]; ph = B["estimated_usage"]["by_phase"]; n = B["candidate_capacity"]["enrichment_planned"]
    return row | {"what_it_would_do": f"lookup gratuito (2 consultas, ya capturadas) + enrichment de organización de hasta {n} dominios únicos; evidencia KNOWN por revisión humana/web oficial (0 créditos); sin People, sin job_postings.",
                  "enrichment_companies": n, "paid_calls_planned_by_planner": ph["ENRICHMENT"]["data_calls_max"], "paid_calls_if_batches_of_4": -(-n // 4), "estimated_credits": ph["ENRICHMENT"]["credits"], "reserved_credits": B["credit_budget"]["apollo"]["reserve"],
                  "unallocated_credits": B["credit_budget"]["apollo"]["unallocated"], "discovery_calls": ph["DISCOVERY"]["data_calls_max"], "evidence_phase_calls_NOT_PLANNED_TO_RUN": ph["EVIDENCE"]["data_calls_max"], "approvals_required": [a["action"] for a in pl["approvals"]],
                  "expected_candidates": pl["coverage"]["planned_enrichment"], "achievable_qualified_upper_bound": pl["coverage"]["achievable_qualified_target"], "warnings": sorted(w["code"] for w in pl["warnings"])}


def plan_preview(cd):
    spec, _ = P.build_spec(cd); prof = load_json(cd, "real_replay/replay_profile.json"); exp = experiment(os.path.basename(os.path.normpath(cd))); sc = {str(n): _plan_for(spec, prof, n) for n in (0, 5, 10)}
    return {"schema": "oi_f14_plan_preview.v1", "status": "PLAN_PREVIEW_NOT_EXECUTED", "executed": False, "network_calls_made_to_build_this": 0, "experiment_id": exp["experiment_id"],
            "objective": exp["objective"], "search_spec_version": {"spec_id": spec["control"]["spec_id"], "spec_version": spec["control"]["spec_version"], "content_hash": spec["control"].get("content_hash"), "field_registry_version": spec["versions"]["field_registry_version"]},
            "population": exp["population"], "mandatory_criteria": exp["mandatory_criteria"], "desirable_criteria": exp["desirable_criteria"], "allowed_sources": exp["allowed_sources"], "disallowed_sources": exp["disallowed_sources"],
            "max_apollo_credits": F14_EXPERIMENT_BUDGET, "max_paid_calls": exp["budget"]["max_paid_calls"],
            "expected_calls": {"free": {"apollo_organizations_lookup": "0 nuevas (2 consultas ya capturadas se reproducen); 2 como máximo si se repiten", "apollo_users_api_profile": "2 por lote de pago (saldo antes/después) + 2 de arranque/cierre"},
                               "paid": {"apollo_organizations_bulk_enrich": "≤3 llamadas (lotes de ≤4 dominios verificados), ≤9 filas, ≤9 créditos; 1 crédito de reserva sin usar", "people": 0, "mixed_companies_search": 0, "job_postings": 0}},
            "fallbacks": ["sin autorización: todo sigue en coste 0 (replay + revisión humana/oficial)", "enrichment parcial: lo no evaluado queda NOT_ASSESSED/PENDING_EVIDENCE, no se rellena", "dominio ausente (3 organizaciones): no enriquecible por dominio; solo revisión humana",
                          "el lote devuelve menos filas de las enviadas: se cobra lo devuelto; se registra"],
            "stop_conditions": stop_conditions()["conditions"], "evaluation_metrics": "ver f14_preparation/metrics.json", "human_review_protocol": "ver f14_preparation/human_review_protocol.v2.json",
            "output_contract": {"records": "opportunity_record.v1[] generados por el pipeline", "html": "renderer genérico desde los registros", "flags": {"DATA_MODE": "REAL", "EXTERNAL_CALLS": "solo las del operador, listadas", "PAID_CALLS": "≤3", "APOLLO_CREDITS": "≤10"},
                               "evidence": "evidence_origin obligatorio en toda evidencia real", "run_summary": "estados por puerta, claims sin evidencia, contradicciones, coste"},
            "planner_scenarios": sc, "account_balance_observed": ACCOUNT_BALANCE_OBSERVED, "paid_enrichment_authorized": guard.paid_enrichment_authorized(),
            "unknown": ["total de empresas disponibles en Apollo", "precisión del filtro NAICS", "tamaño máximo real de lote de bulk_enrich (probado 4)", "contador canónico de créditos (87/110/172–180)", "cuántas de las 9 pasarán cada puerta (sin histórico)", "coste de Firecrawl"]}


def ux_requirements():
    return {"schema": "oi_ux_requirements.v1", "audience": "usuario que NO conoce Opportunity Intelligence", "translate": {
        "Search Spec": "«Esto es lo que entendí que buscas» (frase + criterios obligatorios y deseables)", "Evidence": "«Lo que está comprobado y dónde» (fuente, fecha, quién lo afirma: web oficial / revisión humana / Apollo)", "Qualification": "«¿Encaja?»: Encaja comprobado / Falta comprobar / No encaja / Sin evaluar",
        "UNKNOWN": "«No lo sabemos todavía» (nunca «no»)", "PENDING_EVIDENCE": "«Parece prometedora, pero faltan estas comprobaciones: …»", "Human Review": "«Necesita que una persona lo decida porque …»", "Contact": "«A quién escribir y por qué», con la calidad del canal; «sin contacto fiable» si no hay"},
        "screens": {"1_pantalla_inicial": ["qué quieres conseguir (campo libre)", "ejemplos", "qué NO hace el sistema (no inventa datos)"],
                    "2_confirmacion_de_busqueda": ["frase interpretada", "criterios obligatorios / deseables / pendientes de decisión", "ventana de crecimiento (24 meses) editable", "decisiones abiertas (umbral de crecimiento)", "coste máximo y quién lo autoriza; «coste 0» si es solo gratuito"],
                    "3_progreso": ["fase actual en lenguaje llano", "llamadas y créditos gastados / máximo", "qué se ha parado y por qué", "botón parar"],
                    "4_resultados": ["recuento por estado humano (Encaja / Falta comprobar / No encaja / Sin evaluar)", "orden por acción recomendada", "filtros por estado, señal, contacto", "aviso de datos reales vs de prueba"],
                    "5_ficha_de_empresa": ["nombre, dominio, país (si comprobado)", "estado + una frase de motivo", "puertas con semáforo y origen de cada dato", "señales de crecimiento una a una"],
                    "6_por_que_se_recomienda": ["razones con evidencia enlazada", "lo que NO se sabe", "contradicciones visibles"],
                    "7_evidencia": ["tabla: afirmación · valor · fuente · origen · fecha · estado (conocido/inferido/estimado/desconocido/en conflicto)", "enlace a la fuente", "quién lo revisó"],
                    "8_contacto": ["persona/departamento/genérico", "canal y cómo se verificó", "calidad A/B/C/D en palabras", "aviso: contacto ≠ oportunidad válida"],
                    "9_acciones_siguientes": ["Revisar", "Aportar evidencia", "Pedir enriquecimiento (muestra coste y requiere autorización)", "Contactar (solo si cualificada y con contacto)", "Descartar con motivo"]},
        "discovered_by_f13_1": ["el usuario necesita ver SIEMPRE la diferencia entre «no encaja» y «no lo sabemos»", "el coste y la autorización deben verse antes de cada paso de pago", "el origen de cada dato es parte del dato", "los estados 'NOT_ASSESSED' y 'ENRICH_FIRST' necesitan traducción propia", "saldo de cuenta ≠ presupuesto del experimento deben mostrarse por separado"]}


def build_all(cd):
    tpl, n = review_template(cd)
    return {"decisions.json": decisions(), "evidence_policy.json": evidence_policy(), "eligibility_policy.json": eligibility_policy(), "growth_policy.json": growth_policy(), "experiment_EXP-CS-001.json": experiment(os.path.basename(os.path.normpath(cd))), "metrics.json": metrics(),
            "human_review_protocol.v2.json": review_protocol(os.path.basename(os.path.normpath(cd))) | {"rows": n}, "stop_conditions.json": stop_conditions(), "f14_plan_preview.json": plan_preview(cd), "ux_requirements.json": ux_requirements()}, tpl
