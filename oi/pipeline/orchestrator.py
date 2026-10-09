"""Tramo posterior al Search Spec confirmado:  Plan -> aprobación -> Executor -> Discovery -> Entity Resolution -> Gates baratos -> Enrichment -> Evidencia
-> Cualificación -> Selección de contacto -> Opportunity Records -> (el renderer lo invoca el CLI).

Reutiliza el Executor EXISTENTE; el único punto de salida al exterior es oi.pipeline.guard. No contiene lógica de ningún caso: todo lo del caso llega por `case`/`cdir`.
"""
import hashlib
import json
import os

from oi.engine.executor.adapters.apollo import ApolloAdapter
from oi.engine.executor.config import make_config
from oi.engine.executor.runner import Executor, save
from oi.engine.planner.config import PlannerConfig
from oi.engine.planner.planner import Planner
from oi.enrichment.contact import candidates as CC, checks as CK, profiles as CP, selector as SEL
from oi.evidence import intake, producers
from oi.pipeline import approval as AP, guard, records as RC
from oi.pipeline.cases import load_json
from oi.qualification import qualifier as Q
from oi.contracts.validator import validate

ACTION_ORDER = {"CONTACT": 0, "HUMAN_REVIEW": 1, "VERIFY_EVIDENCE": 2, "ENRICH_FIRST": 3, "DEFER": 4, "NO_ACTION": 5}


class PipelineError(Exception):
    def __init__(self, code, detail=None):
        super().__init__(f"{code}: {detail}" if detail else code); self.code = code; self.detail = detail


def _w(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def requested_of(spec):
    it = spec["intent"]; off = it.get("offering") or {}
    return {"entity_level": it.get("entity_level") or "UNKNOWN", "business_model": list(it.get("business_model") or ["unknown"]), "offering_kind": off.get("kind") or "UNKNOWN",
            "target_function": off.get("target_function") or "UNKNOWN", "offering_description": off.get("description")}


def gate_defs(spec, mapping, case_reqs, requested, extra=()):
    """Puertas de cualificación derivadas del Search Spec (+ requisitos de evidencia del caso). Nada hardcodeado por caso."""
    req = {r["criterion_id"]: r for r in case_reqs.get("requirements", [])}; out = []
    for c in spec["criteria"]:
        if c.get("strength") != "gate": continue
        r = req.get(c["id"], {})
        if c["field"] == "firmographic.industry":
            out.append({"gate_id": c["id"], "label": c["label"], "evidence_field": r.get("evidence_field", "company:sector"), "expected": [mapping["canonical_sector"]], "blocking": True,
                        "provider_gate": c["id"], "provider_evidence": "industry", "acceptable_source_classes": r.get("acceptable_source_classes")})
        else:
            out.append({"gate_id": c["id"], "label": c["label"] + " (sin evaluador offline)", "evidence_field": r.get("evidence_field", "unsupported:" + c["field"]), "expected": [str(c.get("value"))], "blocking": True,
                        "provider_gate": None, "provider_evidence": None, "acceptable_source_classes": r.get("acceptable_source_classes")})
    geo = [g["name"] for g in spec["restrictions"]["geography"].get("include", []) if g.get("level") == "country"]
    if geo:
        r = req.get("GEO", {})
        out.append({"gate_id": "GEO", "label": "Geografía: " + ", ".join(geo), "evidence_field": r.get("evidence_field", "company:country"), "expected": geo, "blocking": True, "provider_gate": "GEO",
                    "provider_evidence": "country", "acceptable_source_classes": r.get("acceptable_source_classes")})
    M = Q.model()
    if requested["entity_level"] not in M["entity_level_gate"]["not_applicable_when_requested"]:
        g = M["entity_level_gate"]; out.append({"gate_id": g["id"], "label": g["label"] + ": " + requested["entity_level"], "evidence_field": g["evidence_field"], "expected": [requested["entity_level"]], "blocking": True,
                                                "provider_gate": None, "provider_evidence": None})
    for x in extra:   # puertas propias del EXPERIMENTO (p. ej. tamaño 51–500, decisión D1), declaradas por el perfil del caso; no se hardcodean aquí
        out.append({"gate_id": x["gate_id"], "label": x["label"], "evidence_field": x["evidence_field"], "expected": list(x["expected"]), "blocking": bool(x.get("blocking", True)), "provider_gate": None, "provider_evidence": None,
                    "acceptable_source_classes": x.get("acceptable_source_classes"), **({"numeric_range": x["numeric_range"]} if x.get("numeric_range") else {})})
    bm = [b for b in requested["business_model"] if b not in M["business_model_gate"]["not_applicable_when_requested"]]
    if bm:
        g = M["business_model_gate"]; out.append({"gate_id": g["id"], "label": g["label"] + ": " + ", ".join(bm), "evidence_field": g["evidence_field"], "expected": bm, "blocking": True, "provider_gate": None, "provider_evidence": None})
    return out


def execute(spec, case, cdir, mapping, out, *, mode, clock, dataset=None, replay_evidence=None):
    """-> dict con artefactos y cifras. Lanza guard.RealModeBlocked en modo real, PipelineError ante bloqueos del plan/executor."""
    if mode not in ("offline", "replay-real"): raise guard.RealModeBlocked(guard.real_mode_blockers())
    replay = mode == "replay-real"
    if replay:     # datos REALES ya capturados del lookup gratuito (F13): mismo pipeline, otro transporte; no hay dataset sintético
        prof = load_json(cdir, case["files"]["replay_profile"]); ds_path = os.path.join(cdir, prof["capture"]); ds = load_json(cdir, prof["capture"]); ds_sha = hashlib.sha256(open(ds_path, "rb").read()).hexdigest()
        ev_in = replay_evidence if replay_evidence is not None else load_json(cdir, prof["evidence_file"])["items"]      # `replay_evidence`: solo pruebas
    else:
        prof = load_json(cdir, case["files"]["offline_profile"]); ds_path = os.path.join(cdir, prof["dataset"]); ds = dataset if dataset is not None else load_json(cdir, prof["dataset"])     # `dataset`: dataset sintético alternativo (solo pruebas de mutación)
        ds_sha = hashlib.sha256(open(ds_path, "rb").read() if dataset is None else json.dumps(dataset, sort_keys=True).encode()).hexdigest()
    today = clock()[:10]
    guard.COUNTERS.reset()
    art = {}
    with guard.offline_lock():
        cfg = PlannerConfig(); plan_res = Planner(cfg, clock=clock).plan(spec, budget_input=prof["planning"], llm_proposals=({"discovery_queries": prof["discovery_queries"]} if replay else None)); plan = plan_res.get("plan")
        if not plan or plan["status"] == "blocked": raise PipelineError("PLAN_BLOCKED", (plan or {}).get("blocked_reasons") or plan_res.get("reason_code"))
        _w(os.path.join(out, "05_research_plan.json"), plan_res)
        approval = AP.approve(plan, mode, clock=clock); _w(os.path.join(out, "06_approval.json"), approval)
        transport = guard.make_transport("replay", capture=ds) if replay else guard.make_transport(mode, dataset=ds)
        labels = mapping["chain"]["provider_taxonomy"].get("apollo", {}).get("industry_labels", [])
        if not labels: raise PipelineError("MAPPING_REQUIRED", "el mapeo sectorial no aporta etiquetas de proveedor")
        config = make_config(confirm_degraded=approval["degraded_plan_confirmed"], industry_mapping={"labels": labels, "naics_prefixes": []}, enrichment_batch_size=10, **({"phases_enabled": prof["phases_enabled"]} if replay else {}))
        ex = Executor(plan, spec, ApolloAdapter(), transport, config, cfg=cfg, clock=clock)
        res = ex.run()
        if res["blockers"] or res["state"] in ("BLOCKED", "FAILED"): raise PipelineError("EXECUTOR_" + res["state"], res["blockers"] or res["stop_reason"])
        save(res, os.path.join(out, "07_execution"))
        usage = transport.usage(); credits = res["credit"]["actual_known"]
        refused = [c for c in ex.calls.rows if c["status"] == "REFUSED"]
        if replay and any(c["phase"] != "ENRICHMENT" for c in refused): raise PipelineError("REPLAY_UNEXPECTED_REFUSAL", [(c["phase"], c["error_message"]) for c in refused if c["phase"] != "ENRICHMENT"][:3])
        if credits != 0 or usage["paid_calls"] != 0: raise PipelineError("OFFLINE_SPEND_DETECTED", {"credits": credits, "usage": usage})
        # ── candidatos
        requested = requested_of(spec); reqs = load_json(cdir, case["files"]["evidence_requirements"]); gdefs = gate_defs(spec, mapping, reqs, requested, prof.get("experiment_gates", []) if replay else ())
        if replay:      # política de fuentes del experimento (F14): qué clases de fuente pueden dar KNOWN a cada puerta (anula la lista del caso, solo en replay)
            for gd in gdefs:
                if gd["gate_id"] in prof.get("acceptable_source_classes_override", {}): gd["acceptable_source_classes"] = list(prof["acceptable_source_classes_override"][gd["gate_id"]])
        prim = sorted([r for r in res["results"] if r["dedupe_status"] == "primary"], key=lambda r: r["discovery_rank"])
        names = {r["entity_id"]: r["name"] for r in prim}; reviews = {}
        for rv in (ex.dedupe or {}).get("review", []):
            reviews.setdefault(rv["a"], []).append(f"{rv['kind']} ({rv['state']}) con {names.get(rv['b'], rv['b'])}"); reviews.setdefault(rv["b"], []).append(f"{rv['kind']} ({rv['state']}) con {names.get(rv['a'], rv['a'])}")
        cands = []
        for i, r in enumerate(prim, 1):
            key = r["entity_id"]; rec = ex.records.get(key)
            cands.append({"n": f"{i:02d}", "entity_key": key, "name": r["name"], "domain": r["domain"], "provider_id": key.split(":", 1)[-1], "identity": r.get("identity_check") or ("UNKNOWN" if replay else None), "discovery_query": r.get("discovery_query"),
                          "rank": r["discovery_rank"], "enriched": rec is not None, "rec": rec, "details": r.get("gate_details") or {}, "requested": requested,
                          "review_note": "; ".join(reviews.get(key, [])) or None, "enrichment_status": r["enrichment_status"]})
        # ── puertas baratas -> quién necesita evidencia (no se pide evidencia de lo ya descartado: ahorra coste real en el futuro)
        def survives(c):
            if replay: return bool(c["domain"])          # sin enrichment (AWAITING_USER_DECISION) la evidencia solo puede atribuirse por dominio; sin dominio -> UNKNOWN, nunca inventado
            return c["enriched"] and c["identity"] == "KNOWN_MATCH" and not any(d.get("verdict") == "FAIL" for d in c["details"].values() if isinstance(d, dict) and d.get("class") == "HARD")
        survivors = [c for c in cands if survives(c)]
        producer = producers.make_producer(mode, dataset=ds, **({"items": ev_in} if replay else {}))
        items = producer.produce({c["domain"] for c in survivors})
        intake_ev = intake.ingest(items, today, offline=not replay, require_origin=replay)                        # lanza EvidenceIntakeError si algo no cumple el contrato
        rec_ev = {i["evidence_id"]: e for i, e in zip(items, intake_ev)}
        items_by_dom = {}
        for it in items: items_by_dom.setdefault(it["subject"]["entity"], []).append(it)
        _w(os.path.join(out, "08_evidence.json"), {"producer": producer.name, "data_mode": producer.data_mode, "requested_for": sorted(c["domain"] for c in survivors),
                                                   "not_requested_for": [{"entity": c["name"], "why": "descartada por puerta barata/identidad no firme antes de pedir evidencia"} for c in cands if c not in survivors],
                                                   "items": items, "derived": intake_ev})
        profile_doc = CP.load_profiles(); cprof = load_json(cdir, case["files"]["contact_profile"]); tfn = requested["target_function"]
        qual_dump, contact_dump, records = [], [], []
        provenance_base = {"data_origin": "REAL_PROVIDER" if replay else "SYNTHETIC_FIXTURE",
                           "sources": [{"ref": os.path.relpath(ds_path, os.path.dirname(os.path.dirname(cdir))), "role": ("captura REAL del lookup GRATUITO de Apollo (source=APOLLO, mode=FREE_LOOKUP, paid=false, credits_spent=0; solo id/nombre/dominio) reproducida sin red" if replay else "dataset sintético: transporte del proveedor + productor de evidencia"), "sha256": ds_sha}],
                           "spec": {"spec_id": spec["control"]["spec_id"], "spec_version": spec["control"]["spec_version"], "content_hash": spec["control"].get("content_hash"), "schema_version": spec.get("schema_version", "2.0")},
                           "pipeline": {"mode": mode, "DATA_MODE": "REAL_RECORDED_FREE_LOOKUP" if replay else "SYNTHETIC", "executor_run_id": res["run_id"], "plan_id": plan["identity"]["plan_id"], "qualification_model": f"{Q.model()['model']['id']}@{Q.model()['model']['version']}",
                                        "evidence_producer": producer.name, "external_calls": guard.COUNTERS.external_attempts, "paid_calls": 0},
                           "notes": (["Datos REALES ya capturados del lookup GRATUITO de Apollo (id, nombre, dominio). Reproducidos sin red y sin créditos. Sector, país, tamaño, crecimiento y contactos NO vienen de Apollo: son UNKNOWN salvo evidencia aportada y separada por fuente.",
                                      "Generado por el pipeline, no escrito a mano."] if replay else
                                     ["Datos SINTÉTICOS: ninguna empresa, persona ni contacto existe. Generado por el pipeline, no escrito a mano.", "Nada de este registro procede de un proveedor real."])}
        mnote = f" Sector «{mapping['term']}» → {mapping['canonical_sector']} (clasificación {mapping['classification_status']}; consulta al proveedor {next(iter(mapping['chain']['provider_query'].values()))['status']})."
        crit_growth = next((c for c in spec["criteria"] if c.get("strength") == "preferred" and str(c.get("field", "")).startswith("growth.") and c.get("field") != "growth.signal"), None)
        sig_crits = [c for c in spec["criteria"] if c.get("field") == "growth.signal"]       # señales de crecimiento SEPARADAS (S-<SIGNAL>); la ventana viaja en params.window_months
        usage_note = (f"Replay: {usage['replayed_calls']} lecturas de la captura (sin red) en toda la ejecución; 0 llamadas externas; 0 de pago; 0 créditos." if replay else
                      f"Offline: {usage['synthetic_calls']} llamadas SINTÉTICAS (sin red) en toda la ejecución; 0 llamadas de pago; 0 créditos.")
        for c in cands:
            n = c["n"]; dom = c["domain"]; rec = c["rec"]; details = c["details"]
            pev, pids = RC.provider_evidence(n, rec, details, today, "Apollo · lookup gratuito (captura real F12)" if replay else "Proveedor sintético (SYNTHETIC)")
            own_items = items_by_dom.get(dom, []) if c in survivors else []
            own_ev = [rec_ev[i["evidence_id"]] for i in own_items]
            company_ev = [e for e in own_ev if e["field"].startswith("company:")]
            if replay and c["identity"] == "UNKNOWN" and any(e["field"] == "company:identity" and e["claim_status"] == "KNOWN" and str(e["value"]) in (c["provider_id"], dom) for e in company_ev): c["identity"] = "KNOWN_MATCH"   # el lookup NO confirma identidad; solo evidencia KNOWN explícita lo hace
            sigs, sig_unk = RC.growth_signals_from_evidence(sig_crits, own_items, rec_ev, today, prof.get("signal_status") if replay else None) if sig_crits else ([], [])
            # puertas
            gates = [Q.identity_gate(c["identity"], c["review_note"])]
            for gd in gdefs:
                g = Q.resolve_gate(gd, details.get(gd["provider_gate"]) if gd["provider_gate"] and rec is not None else None, [e for e in company_ev if e["field"] == gd["evidence_field"]])
                if gd.get("provider_evidence") in pids and g["trace"].startswith("PROVIDER"): g["evidence_ids"] = g["evidence_ids"] + [pids[gd["provider_evidence"]]]
                gates.append(g)
            if replay and prof.get("growth_gate") and sigs: gates.append(Q.growth_any_gate(sigs, **prof["growth_gate"]))
            qual = Q.qualify(gates, c["enriched"] or (replay and any(e["field"] != "company:identity" for e in company_ev)))
            if replay and qual["rule_id"] == "NOT_ENRICHED": qual = {**qual, "reason": "No evaluada: el enrichment (de pago) está AWAITING_USER_DECISION y no hay evidencia humana/first-party para esta empresa. El lookup gratuito solo aporta id, nombre y dominio; lo demás es UNKNOWN y UNKNOWN no es PASS."}
            growth = RC.growth_signal(crit_growth, details.get("C1"), pids.get("growth"), [e for e in company_ev if e["field"] == "company:growth_12m_pct"], today) if crit_growth else None
            # contacto: evidencia -> candidatos -> perfil -> selector
            ref = {"company_id": f"{case['case_id']}:{n}", "name": c["name"], "domain": dom}
            base, trace = CC.build_base(ref, own_items, rec_ev, opportunity_type=case["opportunity_type"], offering_kind=requested["offering_kind"], target_function=tfn)
            emp = (rec or {}).get("estimated_num_employees")
            contact = SEL.build_contact(base, profile_doc, today, {"value": emp, "claim_status": "ESTIMATED"} if isinstance(emp, int) else None, tfn)
            errs, viol = [], CK.check_contact(contact, profile_doc)
            errs = validate(contact, "opportunity_contact.v1.schema.json")
            if errs or viol: raise PipelineError("CONTACT_INVALID", {"entity": c["name"], "schema": errs[:2], "safety": viol[:2]})
            contact["enrichment_state"] = contact["enrichment_state"]
            prov = dict(provenance_base); prov["notes"] = list(prov["notes"]) + ([] if c in survivors else ["No se pidió evidencia ni contacto: la empresa quedó fuera por una puerta barata o por identidad no firme."])
            ev_items = [e for e in own_ev if not e["field"].startswith(("person:", "channel:", "contact:"))]
            rcd = RC.build(n=n, case=case, spec_ctx={"spec_id": spec["control"]["spec_id"], "spec_version": spec["control"]["spec_version"]}, cand=c, details=details, rec=rec, gates=gates, qual=qual, producer_ev=company_ev,
                           contact=contact, ev_items=ev_items, prov_ev=pev, prov_ids=pids, growth=growth, extra_signals=sigs, extra_unknowns=sig_unk, today=today, usage_note=usage_note, provenance=prov, rank=c["rank"], mapping_note=mnote)
            records.append(rcd)
            qual_dump.append({"entity": c["name"], "record_id": rcd["record_id"], "candidate": {"entity_key": c["entity_key"], "identity": c["identity"], "enriched": c["enriched"], "passed_cheap_gates": c in survivors,
                                                                                                                   "requested": c["requested"], "review_note": c["review_note"]},
                              "qualification": rcd["qualification"], "rule_id": qual["rule_id"], "gates": [{k: v for k, v in g.items()} for g in gates]})
            contact_dump.append({"entity": c["name"], "record_id": rcd["record_id"], "candidates_trace": trace, "contact": contact})
        records.sort(key=lambda r: (ACTION_ORDER[r["recommended_action"]["action"]], r["record_id"]))
        for r in records:
            errs = validate(r, "opportunity_record.v1.schema.json")
            if errs: raise PipelineError("RECORD_INVALID", {"record": r["record_id"], "errors": errs[:3]})
        _w(os.path.join(out, "09_qualification.json"), qual_dump); _w(os.path.join(out, "10_contacts.json"), contact_dump); _w(os.path.join(out, "11_opportunity_records.json"), records)
    ext = guard.COUNTERS.external_attempts + usage["external_calls"]
    return {"records": records, "approval": approval, "plan": {"status": plan["status"], "plan_id": plan["identity"]["plan_id"]}, "usage": {**usage, "external_calls": ext, "credits_spent": credits},
            "executor": {"run_id": res["run_id"], "state": res["state"], "stop_reason": res["stop_reason"], "candidates": len(cands), "primary_by_query": {q: sum(1 for c in cands if c["discovery_query"] == q) for q in sorted({c["discovery_query"] for c in cands})}, "refused_paid_calls": len(refused), "paid_phase_refused_before_transport": bool(refused) and all(c["phase"] == "ENRICHMENT" for c in refused),
                         "awaiting_user_decision": (prof.get("awaiting_user_decision") if replay else None), "enriched": sum(1 for c in cands if c["enriched"]), "evidence_requested_for": len(survivors),
                         "dedupe": {k: v for k, v in (ex.dedupe or {}).items() if k in ("candidates", "entities")}}, "dataset": {"path": prof.get("dataset") or prof.get("capture"), "sha256": ds_sha}}
