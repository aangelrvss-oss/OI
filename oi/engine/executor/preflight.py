"""Revisión de seguridad previa a una ejecución real. Cada comprobación se VERIFICA (ejecutando o inspeccionando código), no se declara.
Incluye un ensayo con fixtures SINTÉTICAS usando la configuración exacta del experimento (solo cambia el transporte).
Si una sola comprobación falla, el resultado es NOT_READY y no debe ejecutarse nada."""
import copy
import json
import re
import tempfile

from oi.engine.planner import validation as PV
from oi.engine.spec.versioning import canonical_hash
from . import state as S, synthetic
from .adapters.apollo import ApolloAdapter
from .config import NOT_SUPPORTED_IN_3B
from .errors import ProviderError, sanitize
from .ledger import ToolCallLedger, ResultLedger
from .runner import Executor
from .transports import FixtureTransport, RecordedTransport

WRITE_VERBS = re.compile(r"(^|_)(create|update|delete|send|add|remove|write|append|post|put|patch|trash|forward|reply|enroll|approve|purchase)(_|$)", re.I)   # por TOKENS: job_postings no es una escritura
REQUIRED_CALL_FIELDS = ("tool_call_id", "run_id", "plan_id", "phase", "operation", "input_hash", "estimated_cost", "status", "started_at", "finished_at", "data_origin")


def preflight(spec, plan, config, cfg, hard_cap_expected=25):
    checks = []; ck = lambda cid, ok, detail: checks.append({"check": cid, "result": "PASS" if ok else "FAIL", "detail": detail})
    ad = ApolloAdapter()
    errs = PV.validate_plan(plan, cfg)
    ck("PLAN_VALID", not errs, f"invariantes del plan: {len(errs)} errores")
    ck("SPEC_INTEGRITY", canonical_hash(spec) == spec["control"]["content_hash"] == plan["spec_ref"]["content_hash"], "hash del spec = hash referenciado por el plan")
    cap = min(x for x in (config["credit_hard_cap"], plan["budget"]["credit_budget"]["apollo"]["max"]) if x is not None)
    ck("HARD_CREDIT_CAP", config["credit_hard_cap"] == hard_cap_expected and cap <= hard_cap_expected, f"tope del experimento {config['credit_hard_cap']}, tope efectivo {cap} (≤{hard_cap_expected})")
    ops = set(ad.READ_OPERATIONS.values())
    ck("NO_WRITES_ADAPTER_ALLOWLIST", not any(WRITE_VERBS.search(o) for o in ops) and not any(cfg.is_forbidden(o) for o in ops), f"allowlist de solo lectura: {sorted(ops)}")
    writes = sorted({s["selected_tool"] for p in plan["phases"] if p["status"] == "planned" for s in p["tool_selections"] if any(WRITE_VERBS.search(o) for o in s["operations"])})
    ck("PLAN_WRITE_TOOLS_NOT_EXECUTABLE", not any(ad.supports_tool(t) for t in writes), f"herramientas de escritura presentes en el plan y NO ejecutables por el ejecutor: {writes}")
    try: ad.build_request("apollo_contacts_create", {}); refused = False
    except ProviderError as e: refused = e.code == "OPERATION_NOT_ALLOWED"
    ck("NO_WRITES_ADAPTER_REFUSES", refused, "el adapter rechaza operaciones fuera de la allowlist")
    ck("NO_GMAIL_PEOPLE_FIRECRAWL_OPENRUSH_PHASES", not set(config["phases_enabled"]) & set(NOT_SUPPORTED_IN_3B), f"fases habilitadas: {config['phases_enabled']}")
    ck("NO_PAGINATION_BEYOND_PAGE_1", config["discovery"]["max_pages"] == 1 and "pagination" not in config["allow_unverified_capabilities"], f"max_pages={config['discovery']['max_pages']}; paginación UNVERIFIED (no usada, no prohibida)")
    ck("REAL_MODE_REQUIRES_BALANCE_BEFORE_PAID_CALL", config["mode"] == "relay" and config["require_measured_balance_before_paid_call"] in (None, True), "relay ⇒ ninguna llamada de pago sin saldo medido justo antes")
    with tempfile.TemporaryDirectory() as d:          # sin aprobación humana, el modo real se bloquea antes de crear ninguna petición
        r0 = Executor(plan, spec, ad, RecordedTransport(d), {**config, "execution_approval": None}, cfg=cfg).run()
        import os; pend = os.listdir(os.path.join(d, "pending"))
    ck("REAL_MODE_BLOCKED_WITHOUT_APPROVAL", r0["state"] == S.BLOCKED and "REAL_EXECUTION_REQUIRES_HUMAN_APPROVAL" in r0["blockers"] and not pend, "sin aprobación: BLOCKED y 0 peticiones pendientes")
    # ── ensayo SINTÉTICO con la misma configuración (solo cambia el transporte)
    rc = copy.deepcopy(config); rc.update(mode="dry_execution", confirm_degraded=True, execution_approval=None, require_measured_balance_before_paid_call=True)
    runs = []
    for _ in range(2):
        t = FixtureTransport(synthetic.build()); r = Executor(plan, spec, ad, t, rc, cfg=cfg, clock=lambda: "REHEARSAL").run(); runs.append((r, t))
    r, t = runs[0]
    seen = [c[0] for c in t.calls]
    ck("REHEARSAL_ONLY_ALLOWLISTED_OPERATIONS", set(seen) <= ops, f"operaciones invocadas en el ensayo: {sorted(set(seen))}")
    jp_on = config.get("job_postings", {}).get("enabled", False)
    ck("REHEARSAL_NO_PEOPLE_GMAIL_FIRECRAWL_OPENRUSH", not any(re.search(r"(people|gmail|firecrawl|openrush|contacts|sequence|email" + ("" if jp_on else "|job_postings") + ")", o) for o in seen), "ninguna operación de personas, email, Firecrawl ni OpenRush" + ("" if jp_on else " ni job postings"))
    look = [c for c in t.calls if c[0] == "apollo_organizations_lookup"]
    ck("REHEARSAL_ONE_PAGE", len(look) == 1 and look[0][1]["page"] == 1, f"{len(look)} llamada(s) de discovery, página {look[0][1]['page'] if look else None}")
    doms = [d for c in t.calls if c[0] == "apollo_organizations_bulk_enrich" for d in c[1]["domains"]]
    ck("REHEARSAL_NO_DUPLICATE_ENRICHMENT", len(doms) == len(set(doms)), f"{len(doms)} dominios pedidos, {len(set(doms))} únicos")
    ck("REHEARSAL_CREDITS_WITHIN_CAP", r["credit"]["actual_known"] <= cap and not r["credit"]["cap_breached"], f"créditos sintéticos {r['credit']['actual_known']} ≤ {cap}")
    probe = "api_key=sk-PROBE-123 Authorization: Bearer tok.PROBE"
    ck("NO_SECRET_LEAKAGE", "PROBE" not in json.dumps(sanitize({"m": probe, "api_key": "sk-PROBE", "headers": {"Authorization": "Bearer tok.PROBE"}})) and "sk-" not in json.dumps(r), "saneado de claves y valores en línea; ledger sin claves")
    complete = all(all(k in c for k in ToolCallLedger.FIELDS) and all(c[k] is not None for k in REQUIRED_CALL_FIELDS) for c in r["tool_calls"])
    traced = all(x["final_status"] and x["entity_id"] and x["gate_status"] for x in r["results"]) and len(r["results"]) == sum(c["result_count"] or 0 for c in r["tool_calls"] if c["phase"] == "DISCOVERY")
    ck("COMPLETE_LEDGER", complete and traced, f"{len(r['tool_calls'])} llamadas con todos los campos; {len(r['results'])} candidatos trazados con estado final")
    legal = all(e["from"] is None or e["to"] in S.ALLOWED.get(e["from"], set()) for e in r["events"])
    ck("DETERMINISTIC_STATE_TRANSITIONS", legal and json.dumps(runs[0][0], sort_keys=True) == json.dumps(runs[1][0], sort_keys=True), f"transiciones {[e['to'] for e in r['events']]}; dos ensayos idénticos")
    bs = config["enrichment_batch_size"]
    ck("BATCH_SIZE_WITHIN_TESTED", bs is not None and bs <= ad.capabilities()["bulk_batch_size"]["max_tested"], f"lote {bs} ≤ máximo probado {ad.capabilities()['bulk_batch_size']['max_tested']} (10 sigue UNVERIFIED)")
    ok = all(c["result"] == "PASS" for c in checks)
    return {"verdict": "ALL_CHECKS_PASS" if ok else "NOT_READY", "checks": checks,
            "rehearsal": {"label": "SYNTHETIC / NON-CALIBRATING", "state": r["state"], "external_calls": len(r["tool_calls"]), "paid_calls": sum(1 for c in r["tool_calls"] if c["phase"] == "ENRICHMENT"),
                          "note": "el ensayo solo verifica flujo y protecciones; sus números NO predicen el resultado real"}}
