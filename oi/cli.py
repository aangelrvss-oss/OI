"""`oi run "<necesidad>"` — punto de entrada ÚNICO.

NL -> borrador de Search Spec v2 (intérprete local, sin LLM) -> validación -> preguntas/confirmación -> Research Plan -> aprobación -> Executor
-> evidencia -> cualificación -> contacto -> Opportunity Records -> HTML.

Modos:
  (sin flag)   valida, mapea el sector y planifica; NO ejecuta.
  --offline    ejecuta todo contra el transporte SINTÉTICO del caso (DATA_MODE=SYNTHETIC; 0 llamadas externas; 0 créditos).
  --estimate   solo calcula el presupuesto (estimated_data_calls, estimated_infra_calls, estimated_tool_credits, paid_calls); no ejecuta.
  --replay-real  (con --offline) reproduce, SIN red y SIN créditos, los datos REALES capturados del lookup gratuito de Apollo en F12 con el mismo pipeline (DATA_MODE=REAL_RECORDED_FREE_LOOKUP). No es --real.
  --real       RECHAZADO: paid_enrichment_authorized=false. La barrera es oi.pipeline.guard.
  --strict     con --offline, no aplica los supuestos sintéticos del caso: un parámetro pendiente del usuario detiene la ejecución con una pregunta.
Códigos de salida: 0 completo · 2 preguntas bloqueantes / mapeo requerido / sin caso offline · 3 ejecución real rechazada · 4 error.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile

from oi import paths
from oi.engine.spec.engine import IntentEngine
from oi.engine.spec.versioning import SpecStore
from oi.engine.spec.mock_interpreter import interpret
from oi.engine.spec import sector_mapping as SM
from oi.engine.planner.planner import Planner
from oi.pipeline import guard
from oi.pipeline.guard import PAID_ENRICHMENT_AUTHORIZED          # constante ÚNICA (definida en la barrera); aquí solo se re-expone
from oi.pipeline.cases import find_case as _find_case, load_json
from oi.presentation.render import render

CLOCK = lambda: "2026-10-08T00:00:00+00:00"


def collections_count(xs):
    out = {}
    for x in xs: out[x] = out.get(x, 0) + 1
    return out


def _w(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def _norm_req(s): return " ".join((s or "").lower().split()).rstrip(". ")


def find_case(doc, case_id=None): return _find_case(doc, case_id)


def _flags(S, usage=None):
    u = usage or {}
    S["DATA_MODE"] = "SYNTHETIC" if S.get("mode") == "OFFLINE" else ("REAL_RECORDED_FREE_LOOKUP" if S.get("mode") == "OFFLINE_REPLAY_REAL" else "NONE")
    S["EXTERNAL_CALLS"] = u.get("external_calls", 0); S["PAID_CALLS"] = u.get("paid_calls", 0); S["APOLLO_CREDITS"] = u.get("credits_spent", 0)
    S["SYNTHETIC_CALLS"] = u.get("synthetic_calls", 0)
    S["credits_spent"] = S["APOLLO_CREDITS"]; S["paid_calls"] = S["PAID_CALLS"]; S["network_calls"] = S["EXTERNAL_CALLS"]


def run(request, out_dir=None, case_id=None, answers=None, offline=False, real=False, locale=None, draft=None, strict=False, estimate=False, dataset=None, replay_real=False, replay_evidence=None):
    """-> dict resumen (también escrito en RUN_SUMMARY.json). No lanza por falta de decisiones del usuario: las devuelve como preguntas."""
    if real:
        try: guard.make_transport("real")
        except guard.RealModeBlocked as e:
            return {"exit_code": 3, "status": "REAL_EXECUTION_REFUSED", "reason": str(e), "reasons": e.reasons, "mode": "REAL_REFUSED", **{k: 0 for k in ("credits_spent", "paid_calls", "network_calls")},
                    "DATA_MODE": "NONE", "EXTERNAL_CALLS": 0, "PAID_CALLS": 0, "APOLLO_CREDITS": 0}
    if replay_real and not offline:
        return {"exit_code": 2, "status": "REPLAY_REQUIRES_OFFLINE", "reason": "--replay-real exige --offline: el replay nunca toca la red.", "mode": "REPLAY_REFUSED", "DATA_MODE": "NONE", "EXTERNAL_CALLS": 0, "PAID_CALLS": 0, "APOLLO_CREDITS": 0,
                **{k: 0 for k in ("credits_spent", "paid_calls", "network_calls")}}
    mode = ("OFFLINE_REPLAY_REAL" if replay_real else "OFFLINE") if offline else ("ESTIMATE" if estimate else "PLAN_ONLY")
    out = out_dir or tempfile.mkdtemp(prefix="oi_run_")
    rid = hashlib.sha256((_norm_req(request) + "|" + mode + ("|strict" if strict else "")).encode()).hexdigest()[:10]
    out = os.path.join(out, f"run_{rid}"); os.makedirs(out, exist_ok=True)
    S = {"request": request, "run_id": rid, "mode": mode, "paid_enrichment_authorized": guard.paid_enrichment_authorized(), "stages": {}, "assumptions": []}
    _flags(S)
    draft = draft if draft is not None else interpret(request); _w(os.path.join(out, "01_draft_search_spec.json"), draft)
    S["stages"]["interpret"] = {"status": "DONE", "interpreter": (draft.get("meta") or {}).get("prompt_version")}
    sp = os.path.join(out, "spec_store.jsonl")
    if os.path.exists(sp): os.remove(sp)                                   # cada ejecución parte de un almacén limpio: ids de spec deterministas
    eng = IntentEngine(store=SpecStore(sp), clock=CLOCK, schema_version="2.0"); ans = answers or {}
    conf = list(ans.get("confirmations") or [])
    if not (draft.get("intent") or {}).get("opportunity_type"):       # la petición no dice qué se busca: se PREGUNTA, no se adivina ni se rompe con un error de esquema
        r = {"status": "needs_clarification", "errors": [], "warnings": [], "spec": None, "clarifications": [{"trigger_rule": "OPPORTUNITY_TYPE_UNDETERMINED", "blocking": True,
             "question": "¿Qué quieres conseguir con estas empresas? (vender a clientes, encontrar proveedores, talento, socios, investigar un mercado…)"}]}
    else:
        r = eng.process(draft, user_params=ans.get("user_params") or {}, user_confirmations=conf or None, user_inputs=ans.get("user_inputs"))
    case, cdir = find_case(draft, case_id)
    S["case"] = case["case_id"] if case else None
    if replay_real and case and case.get("files", {}).get("replay_profile"):       # el replay usa la Search Spec CONFIRMADA del experimento (F12), no los supuestos sintéticos del modo offline
        from oi.pipeline import preview as PV
        S["nl_draft_status"] = r["status"]; S["nl_draft_blocking"] = [x["question"] for x in r["clarifications"] if x.get("blocking")]
        r = PV.build_spec(cdir)[1]; S["spec_source"] = "cases/%s/real_experiment (Search Spec confirmada en F12; ventana de crecimiento = parámetro de cada criterio S-*; sin umbral numérico de plantilla)" % case["case_id"]
    # supuestos SINTÉTICOS de prueba (solo --offline, nunca con --strict, nunca sobre una respuesta del usuario)
    if offline and not replay_real and not strict and case and r["status"] == "needs_clarification" and case.get("files", {}).get("offline_profile"):
        prof = load_json(cdir, case["files"]["offline_profile"]); answered = {c["criterion_id"] for c in conf}
        extra = [c for c in prof["confirmations"] if c["criterion_id"] not in answered]
        if extra:
            os.remove(sp) if os.path.exists(sp) else None
            eng = IntentEngine(store=SpecStore(sp), clock=CLOCK, schema_version="2.0")
            r2 = eng.process(draft, user_params=ans.get("user_params") or {}, user_confirmations=conf + extra, user_inputs=ans.get("user_inputs"))
            if r2["status"] == "confirmed":
                r = r2; S["assumptions"] = [{"label": prof["label"], "criterion_id": c["criterion_id"], "value": c["value"], "params": c["params"], "text": c["definition_text"], "is_user_decision": False} for c in extra]
    _w(os.path.join(out, "02_spec_result.json"), r)
    S["stages"]["validate"] = {"status": r["status"], "errors": [e["code"] for e in r["errors"]], "error_messages": [e["message"] for e in r["errors"]][:5], "warnings": [w["code"] for w in r["warnings"]]}
    maps = []
    for c in (r["spec"] or {}).get("criteria", []) or draft.get("criteria", []):
        if c.get("field") == "firmographic.industry" and c.get("source_text"): maps.append(SM.resolve(c["source_text"], "ES"))
    S["sector_mapping"] = maps; _w(os.path.join(out, "03_sector_mapping.json"), maps)
    unmapped = [m for m in maps if m["status"] != "MAPPED"]
    q = r["clarifications"]; _w(os.path.join(out, "04_clarifications.json"), q)
    blocking = [x for x in q if x.get("blocking")]
    S["clarifications"] = {"blocking": [{"rule": x["trigger_rule"], "question": x["question"]} for x in blocking], "non_blocking": [x["question"] for x in q if not x.get("blocking")]}
    if case and os.path.isfile(os.path.join(cdir, "pending_parameters.json")):
        S["pending_parameters"] = [p["id"] for p in load_json(cdir, "pending_parameters.json")["parameters"] if p["status"] == "PENDING_USER_DECISION"]
    spec = r["spec"]; usage = {}
    ready = r["status"] == "confirmed" and not unmapped
    if ready and estimate:
        from oi.pipeline.estimate import estimate as est
        prof = load_json(cdir, case["files"]["offline_profile"]) if case else {"planning": {"source": "offline_simulation", "max_tool_calls": 30, "max_apollo_credits": 25},
                                                                              "real_projection": {"source": "user_parameter", "max_tool_calls": 30, "max_apollo_credits": load_json(paths.CONFIG, "oi.config.json")["future_hard_cap_credits_NOT_AN_AUTHORIZATION"]}}
        E = est(spec, prof, CLOCK); _w(os.path.join(out, "05_estimate.json"), E); S["estimate"] = E
        S["stages"]["plan"] = {"status": "ESTIMATED"}; S["stages"]["execute"] = {"status": "NOT_EXECUTED", "reason": "--estimate no ejecuta"}
    elif ready and not offline:
        plan_res = Planner(clock=CLOCK).plan(spec, budget_input={"source": "user_parameter", "max_tool_calls": 30, "max_apollo_credits": 0})
        _w(os.path.join(out, "05_research_plan.json"), plan_res)
        S["stages"]["plan"] = {"status": plan_res["status"], "reason_code": plan_res["reason_code"], "plan_status": (plan_res["plan"] or {}).get("status"), "blocked_reasons": (plan_res["plan"] or {}).get("blocked_reasons")}
        S["stages"]["execute"] = {"status": "NOT_EXECUTED", "reason": "sin --offline no se ejecuta nada; con --real: rechazado"}
    elif ready and offline:
        if not case or (replay_real and not case.get("files", {}).get("replay_profile")):
            S["stages"]["plan"] = {"status": "NOT_PLANNED", "reason": "sin caso configurado"}; S["stages"]["execute"] = {"status": "NOT_EXECUTED", "reason": "NO_CASE_FOR_OFFLINE: no hay dataset sintético para esta petición"}
        else:
            from oi.pipeline import orchestrator as OR
            try:
                X = OR.execute(spec, case, cdir, maps[0], out, mode="replay-real" if replay_real else "offline", clock=CLOCK, dataset=dataset, replay_evidence=replay_evidence)
            except (OR.PipelineError, guard.RealModeBlocked, guard.ExternalCallAttempt) as e:
                S["status"], S["exit_code"] = "PIPELINE_ERROR", 4; S["error"] = str(e)[:600]; S["EXTERNAL_CALLS"] = guard.COUNTERS.external_attempts
                _w(os.path.join(out, "RUN_SUMMARY.json"), S); S["out_dir"] = out; return S
            usage = X["usage"]; recs = X["records"]
            S["stages"]["plan"] = {"status": "planned", "plan_status": X["plan"]["status"], "plan_id": X["plan"]["plan_id"]}
            S["stages"]["approval"] = {"scope": X["approval"]["scope"], "approved_by": X["approval"]["approved_by"], "paid_enrichment_authorized": False}
            S["stages"]["execute"] = {"status": "REPLAYED_REAL_FREE_LOOKUP" if replay_real else "EXECUTED_SYNTHETIC", **X["executor"]}
            pres = dict(load_json(cdir, case["files"]["presentation"])); notes = list(pres.get("notes") or [])
            notes.append(("DATA_MODE = REAL_RECORDED_FREE_LOOKUP (datos reales de Apollo YA capturados; solo id/nombre/dominio)" if replay_real else "DATA_MODE = SYNTHETIC") + " · EXTERNAL_CALLS = %d · PAID_CALLS = %d · APOLLO_CREDITS = %s" % (usage["external_calls"], usage["paid_calls"], usage["credits_spent"]))
            for a in S["assumptions"]: notes.append(f"Supuesto {a['label']}: {a['text']}")
            pres["notes"] = notes
            with open(os.path.join(out, "12_report.html"), "w", encoding="utf-8") as f: f.write(render(recs, pres, locale))
            S["stages"]["present"] = {"status": "DONE", "records": len(recs), "by_action": {a: sum(1 for x in recs if x["recommended_action"]["action"] == a) for a in sorted({x["recommended_action"]["action"] for x in recs})}}
            S["records"] = len(recs)
            if replay_real:
                qs = collections_count([x["qualification"]["status"] for x in recs]); ac = collections_count([x["recommended_action"]["action"] for x in recs])
                S["replay_summary"] = {"ELIGIBLE": qs.get("ELIGIBLE", 0), "PENDING_EVIDENCE": qs.get("PENDING_EVIDENCE", 0), "NOT_ASSESSED": qs.get("NOT_ASSESSED", 0), "NOT_ELIGIBLE": qs.get("NOT_ELIGIBLE", 0), "UNKNOWN": qs.get("UNKNOWN", 0),
                                       "MAPPING_REQUIRED": len(unmapped), "HUMAN_REVIEW": ac.get("HUMAN_REVIEW", 0), "NO_ACTION": ac.get("NO_ACTION", 0), "ENRICH_FIRST": ac.get("ENRICH_FIRST", 0), "VERIFY_EVIDENCE": ac.get("VERIFY_EVIDENCE", 0), "CONTACT": ac.get("CONTACT", 0), "DEFER": ac.get("DEFER", 0),
                                       "sector_classification_status": maps[0]["chain"]["classification_status"], "primary_by_query": X["executor"].get("primary_by_query"), "replayed_reads": usage.get("replayed_calls"),
                                       "paid_phase_refused_before_transport": X["executor"].get("paid_phase_refused_before_transport")}
    else:
        S["stages"]["plan"] = {"status": "NOT_PLANNED", "reason": "spec no confirmado" if r["status"] != "confirmed" else "sector sin mapeo (MAPPING_REQUIRED)"}
        S["stages"]["execute"] = {"status": "NOT_EXECUTED", "reason": "nada que ejecutar: " + S["stages"]["plan"]["reason"]}
    if r["status"] in ("error", "unsupported_yet"): S["status"], S["exit_code"] = "SPEC_" + r["status"].upper(), 4
    elif blocking: S["status"], S["exit_code"] = "NEEDS_CLARIFICATION", 2
    elif unmapped: S["status"], S["exit_code"] = "MAPPING_REQUIRED", 2
    elif offline and not case: S["status"], S["exit_code"] = "NO_CASE_FOR_OFFLINE", 2
    elif estimate: S["status"], S["exit_code"] = "ESTIMATE_ONLY", 0
    elif offline: S["status"], S["exit_code"] = "COMPLETED_OFFLINE", 0
    else: S["status"], S["exit_code"] = "PLANNED_NOT_EXECUTED", 0
    _flags(S, usage)
    S["out_dir"] = out; S["generated_at"] = CLOCK()
    _w(os.path.join(out, "RUN_SUMMARY.json"), S)
    return S


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oi", description="Opportunity Intelligence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ejecuta el flujo para una necesidad")
    r.add_argument("need"); r.add_argument("--out", default="oi_runs"); r.add_argument("--case"); r.add_argument("--answers", help="JSON con confirmations/user_inputs/user_params del usuario")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--offline", action="store_true", help="ejecución completa contra el transporte SINTÉTICO del caso (0 llamadas externas, 0 créditos)")
    g.add_argument("--real", action="store_true", help="ejecución real: RECHAZADA mientras paid_enrichment_authorized=false")
    r.add_argument("--replay-real", action="store_true", dest="replay_real", help="con --offline: reproduce los datos REALES capturados del lookup gratuito de Apollo (F12) sin red ni créditos")
    r.add_argument("--estimate", action="store_true", help="solo calcula el presupuesto; no ejecuta nada")
    r.add_argument("--strict", action="store_true", help="con --offline, no aplica supuestos sintéticos: un parámetro pendiente detiene la ejecución con una pregunta")
    r.add_argument("--locale")
    a = ap.parse_args(argv)
    answers = json.load(open(a.answers, encoding="utf-8")) if a.answers else None
    if a.real and a.replay_real: ap.error("--real y --replay-real son incompatibles")
    S = run(a.need, a.out, a.case, answers, offline=a.offline, real=a.real, locale=a.locale, strict=a.strict, estimate=a.estimate, replay_real=a.replay_real)
    print(f"[oi] estado: {S['status']}")
    print(f"  DATA_MODE = {S['DATA_MODE']}\n  EXTERNAL_CALLS = {S['EXTERNAL_CALLS']}\n  PAID_CALLS = {S['PAID_CALLS']}\n  APOLLO_CREDITS = {S['APOLLO_CREDITS']}")
    for qq in (S.get("clarifications") or {}).get("blocking", []): print(f"  ? {qq['question']}")
    for e in (S.get("stages", {}).get("validate") or {}).get("error_messages", []): print(f"  ! error del Search Spec: {e}")
    for m in S.get("sector_mapping", []):
        if m["status"] != "MAPPED": print(f"  ! MAPPING_REQUIRED: {m.get('term')} — {m.get('reason')}")
    for x in S.get("assumptions", []): print(f"  ~ supuesto sintético ({x['label']}): {x['text']}")
    if S.get("estimate"):
        E = S["estimate"]; print("  estimación (offline):", json.dumps(E["offline"], ensure_ascii=False)); print("  proyección (real, BLOQUEADA):", json.dumps({k: v for k, v in E["real_projection"].items() if k != "blockers"}, ensure_ascii=False))
    if S.get("replay_summary"): print("  replay:", json.dumps(S["replay_summary"], ensure_ascii=False))
    if S.get("records") is not None: print(f"  registros: {S['records']} · acciones: {S['stages']['present']['by_action']}")
    if S.get("out_dir"): print(f"  artefactos: {S['out_dir']}")
    if S.get("error"): print("  ! " + S["error"])
    if S.get("reason"): print("  " + S["reason"])
    return S["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
