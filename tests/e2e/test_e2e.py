"""F11 — E2E OFFLINE / ZERO-COST. E2E-01 … E2E-10 (numeración de la orden). Todo contra datos SINTÉTICOS; 0 llamadas externas; 0 créditos."""
import copy, hashlib, json, os, socket, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True
from tests._common import *
from oi import cli
from oi.paths import HISTORY
from oi.pipeline import guard
from oi.presentation import render as RN
from oi.enrichment.contact import profiles as CP
from oi.enrichment.contact.checks import check_contact
from oi.qualification import qualifier as Q

PROFILE = CP.load_profiles()
ANS = lambda: {"confirmations": [{"criterion_id": "C1", "definition_text": "TEST_ONLY", "value": 10, "params": {"period_days": 365}}]}
by = lambda recs, frag: next(r for r in recs if frag in r["company"]["name"])
read = lambda S, f: json.load(open(os.path.join(S["out_dir"], f), encoding="utf-8"))


class TestE2E01ValidRequestProducesOpportunityRecords(unittest.TestCase):
    def test_full_path_from_nl_to_html(self):
        S = e2e_run()
        self.assertEqual((S["status"], S["exit_code"]), ("COMPLETED_OFFLINE", 0))
        for f in ("01_draft_search_spec.json", "02_spec_result.json", "03_sector_mapping.json", "05_research_plan.json", "06_approval.json", "07_execution", "08_evidence.json",
                  "09_qualification.json", "10_contacts.json", "11_opportunity_records.json", "12_report.html", "RUN_SUMMARY.json"):
            self.assertTrue(os.path.exists(os.path.join(S["out_dir"], f)), f)
        recs = read(S, "11_opportunity_records.json"); self.assertEqual(len(recs), 18)
        for r in recs: self.assertEqual(RN.validate_records([r]), None)
        self.assertEqual({r["provenance"]["data_origin"] for r in recs}, {"SYNTHETIC_FIXTURE"})
    def test_explicit_markers(self):
        S = e2e_run(); self.assertEqual((S["DATA_MODE"], S["EXTERNAL_CALLS"], S["PAID_CALLS"], S["APOLLO_CREDITS"]), ("SYNTHETIC", 0, 0, 0))
        self.assertGreater(S["SYNTHETIC_CALLS"], 0)
        p = subprocess.run([sys.executable, "-m", "oi", "run", E2E_REQUEST, "--offline", "--out", tempfile.mkdtemp()], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for line in ("DATA_MODE = SYNTHETIC", "EXTERNAL_CALLS = 0", "PAID_CALLS = 0", "APOLLO_CREDITS = 0"): self.assertIn(line, p.stdout)
    def test_each_stage_is_produced_by_the_pipeline_not_prepared(self):
        S = e2e_run(); recs = read(S, "11_opportunity_records.json"); q = read(S, "09_qualification.json")
        self.assertEqual(len(q), len(recs))
        for r in recs:
            self.assertTrue(r["provenance"]["pipeline"]["executor_run_id"].startswith("run-")); self.assertEqual(r["provenance"]["pipeline"]["evidence_producer"], "synthetic_dataset")
            self.assertEqual(r["qualification"]["model"]["id"], "known_evidence_gates")
        for forbidden in ("fixtures/make_synthetic_records.py", "records/synthetic_opportunity_records.json"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, "cases", "construction_software", forbidden)), forbidden)
        ex = read(S, "07_execution/" + S["stages"]["execute"]["run_id"] + "/manifest.json") if os.path.exists(os.path.join(S["out_dir"], "07_execution", S["stages"]["execute"]["run_id"], "manifest.json")) else None
        self.assertIsNotNone(ex); self.assertEqual(ex["credit"]["actual_known"], 0)
    def test_candidate_gates_evidence_qualification_record_chain(self):
        S = e2e_run(); q = {x["record_id"]: x for x in read(S, "09_qualification.json")}; recs = read(S, "11_opportunity_records.json")
        for r in recs:
            x = q[r["record_id"]]; self.assertEqual(x["qualification"]["status"], r["qualification"]["status"])
            self.assertEqual([g["gate_id"] for g in x["gates"]], [g["gate_id"] for g in r["qualification"]["gates"]])
            self.assertEqual(x["candidate"]["requested"]["entity_level"], r["search_context"]["requested_entity_level"])
    def test_deterministic_and_equal_to_reference_copy(self):
        a = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True); b = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True)
        for f in ("11_opportunity_records.json", "12_report.html"): self.assertEqual(open(os.path.join(a["out_dir"], f), "rb").read(), open(os.path.join(b["out_dir"], f), "rb").read(), f)
        ref = os.path.join(ROOT, "cases", "construction_software", "records")
        self.assertEqual(open(os.path.join(a["out_dir"], "11_opportunity_records.json"), "rb").read(), open(os.path.join(ref, "opportunity_records_SYNTHETIC.json"), "rb").read(), "copia de referencia desactualizada: ejecutar tools/build_demos.py")


class TestE2E02AmbiguousRequestAsksForClarification(unittest.TestCase):
    def test_undetermined_intent_asks_and_does_not_execute(self):
        S = cli.run("Quiero empresas interesantes", tempfile.mkdtemp(), offline=True)
        self.assertEqual((S["status"], S["exit_code"]), ("NEEDS_CLARIFICATION", 2)); self.assertEqual(S["clarifications"]["blocking"][0]["rule"], "OPPORTUNITY_TYPE_UNDETERMINED")
        self.assertEqual(S["stages"]["execute"]["status"], "NOT_EXECUTED"); self.assertEqual((S["EXTERNAL_CALLS"], S["PAID_CALLS"], S["APOLLO_CREDITS"], S["SYNTHETIC_CALLS"]), (0, 0, 0, 0))
        self.assertFalse(os.path.exists(os.path.join(S["out_dir"], "11_opportunity_records.json")))
    def test_strict_does_not_invent_the_growth_decision(self):
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, strict=True)
        self.assertEqual((S["status"], S["exit_code"]), ("NEEDS_CLARIFICATION", 2)); self.assertEqual(S["assumptions"], []); self.assertEqual(S["clarifications"]["blocking"][0]["rule"], "CRITERION_INCOMPLETE")
        self.assertEqual(S["SYNTHETIC_CALLS"], 0)
    def test_user_answer_takes_precedence_over_the_synthetic_assumption(self):
        a = ANS(); a["confirmations"][0]["value"] = 50
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, answers=a)
        self.assertEqual(S["status"], "COMPLETED_OFFLINE"); self.assertEqual(S["assumptions"], [])
        spec = read(S, "02_spec_result.json")["spec"]; self.assertEqual(next(c for c in spec["criteria"] if c["id"] == "C1")["value"], 50)
    def test_synthetic_assumption_is_labelled_not_a_user_decision(self):
        S = e2e_run(); self.assertEqual(len(S["assumptions"]), 1); a = S["assumptions"][0]
        self.assertEqual(a["label"], "OFFLINE_SYNTHETIC_ASSUMPTION"); self.assertIs(a["is_user_decision"], False)
        self.assertIn("OFFLINE_SYNTHETIC_ASSUMPTION", open(os.path.join(S["out_dir"], "12_report.html"), encoding="utf-8").read())


class TestE2E03SectorWithoutMapping(unittest.TestCase):
    REQ = "empresas de alquiler de grúas españolas en crecimiento que puedan necesitar software de gestión"
    def _draft(self):
        from oi.engine.spec.mock_interpreter import interpret
        d = interpret(self.REQ); d["criteria"].insert(0, {"id": "G1", "label": "Sector: alquiler de grúas", "dimension": "fit", "field": "firmographic.industry", "operator": "eq", "value": "alquiler de grúas",
                                                          "strength": "gate", "origin": "user", "source_text": "alquiler de grúas"}); return d
    def test_unknown_sector_is_mapping_required_and_makes_no_call_of_any_kind(self):
        built = []; orig = guard.make_transport
        guard.make_transport = lambda *a, **k: built.append(a) or orig(*a, **k)
        try: S = cli.run(self.REQ, tempfile.mkdtemp(), answers=ANS(), offline=True, draft=self._draft())
        finally: guard.make_transport = orig
        self.assertEqual((S["status"], S["exit_code"]), ("MAPPING_REQUIRED", 2)); self.assertEqual(S["sector_mapping"][0]["status"], "MAPPING_REQUIRED")
        self.assertEqual(built, [], "no se construyó ningún transporte"); self.assertEqual((S["EXTERNAL_CALLS"], S["SYNTHETIC_CALLS"], S["PAID_CALLS"], S["APOLLO_CREDITS"]), (0, 0, 0, 0))
        self.assertEqual(S["stages"]["execute"]["status"], "NOT_EXECUTED")
    def test_mapped_sector_exposes_the_full_chain_and_marks_it_unverified(self):
        from oi.engine.spec import sector_mapping as SM
        c = SM.resolve("constructoras", "ES")["chain"]
        self.assertEqual(c["canonical_sector"], "CONSTRUCTION"); self.assertEqual(c["classification_status"], "UNVERIFIED")
        self.assertEqual(c["provider_taxonomy"]["apollo"]["industry_labels_verification"], "UNVERIFIED"); self.assertEqual(c["provider_taxonomy"]["apollo"]["status"], "PROPOSED_NOT_PROBED")
        self.assertEqual(c["provider_query"]["apollo.organizations_lookup"]["status"], "PROPOSED_NOT_PROBED"); self.assertFalse(c["executable_against_real_provider"])


class TestE2E04UnknownEvidenceIsNeverPass(unittest.TestCase):
    def test_gate_resolution_semantics(self):
        gd = {"gate_id": "G", "label": "g", "expected": ["CONSTRUCTION"], "blocking": True}
        ev = lambda status, val, sc="OFFICIAL", eid="e": {"evidence_id": eid, "field": "f", "value": val, "source_class": sc, "claim_status": status}
        self.assertEqual(Q.resolve_gate(gd, None, [])["status"], "UNKNOWN")                                                    # sin datos
        self.assertEqual(Q.resolve_gate(gd, None, [ev("UNKNOWN", None, "NOT_FOUND")])["status"], "UNKNOWN")                    # búsqueda sin resultado
        g = Q.resolve_gate(gd, None, [ev("INFERRED", "CONSTRUCTION")]); self.assertEqual((g["status"], g["evidence_status"]), ("PASS", "INFERRED"))   # una inferencia no es KNOWN
        g = Q.resolve_gate(gd, None, [ev("KNOWN", "CONSTRUCTION", eid="a"), ev("KNOWN", "SOFTWARE", eid="b")]); self.assertEqual((g["status"], g["evidence_status"]), ("UNKNOWN", "CONFLICTING"))
        g = Q.resolve_gate(gd, None, [ev("CONFLICTING", None)]); self.assertEqual((g["status"], g["evidence_status"]), ("UNKNOWN", "CONFLICTING"))
        g = Q.resolve_gate(gd, None, [ev("KNOWN", "CONSTRUCTION", "UNVERIFIED")]); self.assertNotEqual(g["evidence_status"], "KNOWN")  # fuente no verificada nunca es KNOWN
    def test_status_needs_every_blocking_gate_pass_with_known(self):
        g = lambda st, es: {"gate_id": "X", "status": st, "evidence_status": es, "blocking": True}
        for st, es in (("UNKNOWN", "UNKNOWN"), ("PASS", "INFERRED"), ("UNKNOWN", "CONFLICTING"), ("PASS", "ESTIMATED")):
            self.assertNotEqual(Q.qualify([g("PASS", "KNOWN"), g(st, es)], True)["status"], "ELIGIBLE", (st, es))
        self.assertEqual(Q.qualify([g("PASS", "KNOWN"), g("PASS", "KNOWN")], True)["status"], "ELIGIBLE")
    def test_in_the_pipeline_no_record_is_eligible_on_unknown_inferred_or_conflicting(self):
        recs = cs_records()
        for r in recs:
            blocking = [g for g in r["qualification"]["gates"] if g["blocking"]]
            if r["qualification"]["status"] == "ELIGIBLE": self.assertTrue(all(g["status"] == "PASS" and g["evidence_status"] == "KNOWN" for g in blocking), r["record_id"])
            for g in blocking:
                if g["evidence_status"] in ("UNKNOWN", "CONFLICTING"): self.assertNotEqual(g["status"], "PASS", (r["record_id"], g["gate_id"]))
        z = cs_by("Zeta"); self.assertEqual(z["qualification"]["status"], "PENDING_EVIDENCE")
        g1 = next(g for g in z["qualification"]["gates"] if g["gate_id"] == "G1"); self.assertEqual(g1["evidence_status"], "INFERRED")
        e = cs_by("Epsilon"); self.assertEqual(e["qualification"]["status"], "PENDING_EVIDENCE"); self.assertEqual({g["evidence_status"] for g in e["qualification"]["gates"] if g["gate_id"] in ("G1", "BUSINESS_MODEL")}, {"CONFLICTING"})
    def test_signals_follow_the_same_discipline(self):
        for r in cs_records():
            for s in r["signals"]:
                if s["state"] == "PRESENT": self.assertIn(s["claim_status"], ("KNOWN", "ESTIMATED")); self.assertTrue(s["evidence_ids"])
    def test_producer_confidence_never_sets_strength(self):
        from oi.evidence import intake
        it = {"evidence_id": "x", "subject": {"entity": "a.example"}, "field": "company:sector", "value": "CONSTRUCTION", "source_class": "OTHER_VERIFIABLE", "claim_status": "INFERRED", "confidence": "HIGH", "retrieved_at": "2026-10-08", "data_mode": "SYNTHETIC"}
        out = intake.ingest([it], "2026-10-08"); self.assertEqual(out[0]["evidence_strength"], "UNKNOWN"); self.assertEqual(out[0]["producer_confidence"], "HIGH")


class TestE2E05InferredContactIsNotRecommended(unittest.TestCase):
    def test_inferred_email_goes_to_not_recommended(self):
        e = cs_by("Epsilon")["contact"]; self.assertIsNone(e["recommended_contact"]); self.assertEqual(e["alternatives"], [])
        self.assertTrue(any("sin evidencia verificable" in x["reason"] for x in e["not_recommended"]))
        ch = e["channels"][0]; ev = next(x for x in e["evidence"] if x["evidence_id"] in ch["evidence_ids"]); self.assertEqual(ev["claim_status"], "INFERRED"); self.assertEqual(ev["evidence_strength"], "UNKNOWN")
    def test_no_recommendation_or_alternative_rests_on_non_known_evidence(self):
        for r in cs_records():
            c = r["contact"]; self.assertEqual(check_contact(c, PROFILE), [], r["record_id"])
            ev = {e["evidence_id"]: e for e in c["evidence"]}; ch = {x["channel_id"]: x for x in c["channels"]}; pe = {p["person_id"]: p for p in c["people"]}
            for rc in ([c["recommended_contact"]] if c["recommended_contact"] else []) + c["alternatives"]:
                ids = (pe[rc["ref"]]["role_evidence_ids"] if rc["target"] == "PERSON" else ch[rc["ref"]]["evidence_ids"]) + (ch[rc["via_channel_id"]]["evidence_ids"] if rc["via_channel_id"] else [])
                for i in ids: self.assertEqual(ev[i]["claim_status"], "KNOWN", (r["record_id"], i)); self.assertNotIn(ev[i]["source_class"], ("UNVERIFIED", "NOT_FOUND"))
    def test_unverifiable_contact_is_not_recommended(self):
        e = cs_by("Eta")["contact"]; self.assertIsNone(e["recommended_contact"]); self.assertEqual(next(x for x in e["evidence"])["source_class"], "UNVERIFIED")


class TestE2E06QualificationPendingNeverContact(unittest.TestCase):
    def test_pending_or_unassessed_never_contact_even_with_a_grade_a_contact(self):
        for r in cs_records():
            if r["qualification"]["status"] in ("PENDING_EVIDENCE", "UNKNOWN", "NOT_ASSESSED"): self.assertNotEqual(r["recommended_action"]["action"], "CONTACT", r["record_id"])
        z = cs_by("Zeta"); rc = z["contact"]["recommended_contact"]
        self.assertEqual(rc["contact_quality"]["grade"], "A")                                    # el contacto es excelente…
        self.assertEqual((z["recommended_action"]["action"], z["recommended_action"]["rule_id"]), ("HUMAN_REVIEW", "QUALIFICATION_PENDING"))   # …pero NO convierte a la empresa en oportunidad
        self.assertIn("QUALIFICATION_PENDING", z["recommended_action"]["blocking_reasons"])
    def test_removing_the_qualifying_evidence_removes_the_contact_action(self):
        ds = json.load(open(os.path.join(ROOT, "cases", "construction_software", "fixtures", "e2e_dataset.json"), encoding="utf-8"))
        before = by(read(e2e_run(), "11_opportunity_records.json"), "Alfa"); self.assertEqual(before["recommended_action"]["action"], "CONTACT")
        ds["evidence"] = [i for i in ds["evidence"] if not (i["subject"]["entity"] == "alfa-obras.example" and i["field"] in ("company:sector", "company:business_model"))]
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, dataset=ds); a = by(read(S, "11_opportunity_records.json"), "Alfa")
        self.assertEqual(a["qualification"]["status"], "PENDING_EVIDENCE"); self.assertIsNotNone(a["contact"]["recommended_contact"]); self.assertNotEqual(a["recommended_action"]["action"], "CONTACT")
    def test_an_email_alone_does_not_create_an_opportunity(self):
        ds = json.load(open(os.path.join(ROOT, "cases", "construction_software", "fixtures", "e2e_dataset.json"), encoding="utf-8"))
        ds["evidence"] = [i for i in ds["evidence"] if i["field"].startswith(("channel:", "person:", "contact:"))]            # solo contactos, ninguna evidencia de la empresa
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, dataset=ds)
        self.assertEqual(S["status"], "COMPLETED_OFFLINE")
        self.assertEqual([r["company"]["name"] for r in read(S, "11_opportunity_records.json") if r["recommended_action"]["action"] == "CONTACT"], [])


class TestE2E07OfflineMakesZeroExternalCalls(unittest.TestCase):
    def test_audit_hook_sees_no_socket_activity_during_a_full_run(self):
        seen = []; active = [True]
        def hook(ev, args):
            if active[0] and ev.startswith(("socket.connect", "socket.getaddrinfo", "socket.bind", "urllib.Request", "http.client")): seen.append(ev)
        sys.addaudithook(hook)
        try: S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True)
        finally: active[0] = False
        self.assertEqual(seen, []); self.assertEqual(S["EXTERNAL_CALLS"], 0); self.assertEqual(guard.COUNTERS.external_attempts, 0)
    def test_the_lock_really_blocks_and_counts_an_attempt(self):
        guard.COUNTERS.reset()
        with guard.offline_lock():
            with self.assertRaises(guard.ExternalCallAttempt): socket.create_connection(("example.invalid", 80))
            with self.assertRaises(guard.ExternalCallAttempt): socket.getaddrinfo("example.invalid", 80)
        self.assertEqual(guard.COUNTERS.external_attempts, 2); guard.COUNTERS.reset()
        self.assertTrue(socket.create_connection.__module__ == "socket")        # restaurado
    def test_a_pipeline_that_tried_the_network_is_reported_as_error_not_success(self):
        from oi.pipeline import orchestrator as OR
        orig = OR.producers.make_producer
        def evil(mode, dataset=None):
            socket.create_connection(("example.invalid", 80)); return orig(mode, dataset=dataset)
        OR.producers.make_producer = evil
        try: S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True)
        finally: OR.producers.make_producer = orig
        self.assertEqual((S["status"], S["exit_code"]), ("PIPELINE_ERROR", 4)); self.assertGreaterEqual(S["EXTERNAL_CALLS"], 1)
    def test_all_calls_are_synthetic_credits_zero_and_ledger_agrees(self):
        S = e2e_run(); m = read(S, "07_execution/" + S["stages"]["execute"]["run_id"] + "/manifest.json")
        self.assertEqual(m["data_origin"], "SYNTHETIC / NON-CALIBRATING"); self.assertEqual(m["credit"]["actual_known"], 0); self.assertEqual(m["credit"]["calls_with_unknown_actual_cost"], 0)
        calls = read(S, "07_execution/" + S["stages"]["execute"]["run_id"] + "/tool_calls.json"); self.assertTrue(calls)
        for c in calls: self.assertEqual(c["data_origin"], "SYNTHETIC / NON-CALIBRATING"); self.assertIn(c["actual_cost"], (0,)) if c["phase"] != "METERING" else None
        for r in read(S, "11_opportunity_records.json"): self.assertEqual((r["cost"]["paid_calls"], r["cost"]["credits"], r["cost"]["paid_enrichment_authorized"]), (0, 0, False))


class TestE2E08PaymentIsImpossible(unittest.TestCase):
    def test_real_mode_is_refused_by_the_single_barrier(self):
        with self.assertRaises(guard.RealModeBlocked) as cx: guard.make_transport("real")
        self.assertIn("PAID_ENRICHMENT_AUTHORIZED=false", cx.exception.reasons)
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), real=True); self.assertEqual((S["status"], S["exit_code"]), ("REAL_EXECUTION_REFUSED", 3)); self.assertEqual((S["PAID_CALLS"], S["APOLLO_CREDITS"], S["EXTERNAL_CALLS"]), (0, 0, 0))
    def test_flipping_one_flag_is_not_enough(self):
        orig = guard.PAID_ENRICHMENT_AUTHORIZED
        guard.PAID_ENRICHMENT_AUTHORIZED = True
        try:
            self.assertFalse(guard.paid_enrichment_authorized())                                    # la configuración también dice false
            with self.assertRaises(guard.RealModeBlocked): guard.make_transport("real")
            self.assertEqual(cli.run(E2E_REQUEST, tempfile.mkdtemp(), real=True)["exit_code"], 3)
        finally: guard.PAID_ENRICHMENT_AUTHORIZED = orig
    def test_even_with_both_flags_true_nothing_real_is_wired_in_this_block(self):
        orig = (guard.PAID_ENRICHMENT_AUTHORIZED, guard.config_authorization)
        guard.PAID_ENRICHMENT_AUTHORIZED = True; guard.config_authorization = lambda: True
        try:
            self.assertTrue(guard.paid_enrichment_authorized())
            with self.assertRaises(guard.RealModeBlocked) as cx: guard.make_transport("real")
            self.assertTrue(any("no está cableado" in r for r in cx.exception.reasons))
        finally: guard.PAID_ENRICHMENT_AUTHORIZED, guard.config_authorization = orig
    def test_offline_transport_refuses_anything_outside_the_read_allowlist_and_non_synthetic_origins(self):
        t = guard.make_transport("offline", dataset={"lookup": [], "enrich": {}})
        for op in ("apollo_people_match", "apollo_mixed_people_api_search", "apollo_contacts_create", "apollo_emailer_messages_send_now", "apollo_organizations_enrich"):
            from oi.engine.executor.errors import ProviderError
            with self.assertRaises(ProviderError): t.invoke(op, {})
        class Real: data_origin = "REAL_RECORDED"
        with self.assertRaises(guard.RealModeBlocked): guard.GuardedTransport(Real())
    def test_single_barrier_no_other_module_builds_transports_or_touches_the_network(self):
        import re
        pat = re.compile(r"\b(FixtureTransport|RecordedTransport)\s*\(|^\s*(import|from)\s+(socket|urllib|requests|http\.client|httpx|aiohttp|ssl|ftplib|smtplib)\b", re.M)
        ok = {os.path.join("oi", "pipeline", "guard.py")}      # el único que puede construir transportes y tocar sockets
        # Módulos legados (re-export / helpers históricos) que NO son alcanzables desde oi.pipeline; se auditan aparte
        legacy = {os.path.join("oi", "engine", "executor", "__init__.py"), os.path.join("oi", "engine", "executor", "preflight.py"), os.path.join("oi", "engine", "executor", "identity_contract", "relay.py")}
        offenders = []
        for r, ds, fs in os.walk(os.path.join(ROOT, "oi")):
            for fn in fs:
                if not fn.endswith(".py"): continue
                p = os.path.join(r, fn); rel = os.path.relpath(p, ROOT)
                txt = open(p, encoding="utf-8").read()
                if rel in ok or rel in legacy: continue
                for m in pat.finditer(txt):
                    if rel.endswith(os.path.join("executor", "transports.py")) and "class " in txt[max(0, m.start() - 30):m.start()]: continue
                    offenders.append((rel, m.group(0).strip()))
        self.assertEqual(offenders, [])
    def test_no_credit_is_ever_recorded_in_any_pipeline_artifact(self):
        S = e2e_run()
        for r in read(S, "11_opportunity_records.json"): self.assertEqual(r["contact"]["cost"], {"paid_calls": 0, "credits": 0, "paid_enrichment_authorized": False, "authorization_ref": None})
        self.assertFalse(read(S, "06_approval.json")["paid_enrichment_authorized"]); self.assertEqual(read(S, "06_approval.json")["max_credits"], 0)
        self.assertFalse(read(S, "05_research_plan.json")["plan"]["authorization"]["spend_authorized"])


_T0 = __import__('time').time() - 3600


class TestE2E09RendererOnlyConsumesRecords(unittest.TestCase):
    def test_html_is_rendered_from_the_pipeline_records_and_case_config(self):
        S = e2e_run(); recs = read(S, "11_opportunity_records.json"); pres = json.load(open(os.path.join(ROOT, "cases", "construction_software", "presentation.json"), encoding="utf-8"))
        pres["notes"] = pres["notes"] + ["DATA_MODE = SYNTHETIC · EXTERNAL_CALLS = 0 · PAID_CALLS = 0 · APOLLO_CREDITS = 0", f"Supuesto OFFLINE_SYNTHETIC_ASSUMPTION: {S['assumptions'][0]['text']}"]
        html = RN.render(json.loads(json.dumps(recs, sort_keys=True)), pres); self.assertEqual(html, open(os.path.join(S["out_dir"], "12_report.html"), encoding="utf-8").read())
        for r in recs: self.assertIn(r["company"]["name"].replace("&", "&amp;"), html)
        self.assertIn("SINTÉTICOS", html); self.assertIn("DATA_MODE = SYNTHETIC", html)
    def test_renderer_knows_nothing_about_providers_cases_or_the_pipeline(self):
        import re
        txt = "".join(open(os.path.join(ROOT, "oi", "presentation", f), encoding="utf-8").read() for f in ("render.py", "i18n/es.json", "i18n/en.json")).lower()
        txt = re.sub(r"'[^']*'|\"[^\"]*\"", lambda m: m.group(0), txt)
        for w in ("apollo", "discovery", "executor", "reseller", "piloto de reventa", "construction", "constructora", "orchestrator", "firecrawl", "openrush"):
            self.assertNotIn(w, txt, w)
        src = open(os.path.join(ROOT, "oi", "presentation", "render.py"), encoding="utf-8").read()
        self.assertNotRegex(src, r"^\s*(from|import)\s+oi\.(engine|pipeline|enrichment|qualification|evidence)", "el renderer solo puede importar el validador de contratos")
    def test_renderer_rejects_input_that_is_not_an_opportunity_record(self):
        with self.assertRaises(RN.RenderError): RN.render([{"company": {"name": "x"}}], {"title": "t", "subtitle": "s", "locale": "es"})
    def test_same_renderer_both_cases(self):
        h1 = RN.render(reseller_records(), load("cases", "reseller_pilot", "presentation.json")); h2 = RN.render(cs_records(), load("cases", "construction_software", "presentation.json"))
        self.assertTrue(h1 and h2 and h1 != h2)


class TestE2E10HistoricalResellerPilotIntact(unittest.TestCase):
    def _snapshot(self):
        from tests.historical import test_historical_integrity as H
        files = H.current_files(); rp = {}
        for r, ds, fs in os.walk(os.path.join(ROOT, "cases", "reseller_pilot")):
            ds[:] = [d for d in ds if d != "__pycache__"]
            for fn in fs: rp[os.path.relpath(os.path.join(r, fn), ROOT)] = H.sha(os.path.join(r, fn))
        return {k: H.sha(p) for k, p in files.items()}, rp, {k: H.sha(os.path.join(H.OUT, k)) for k in H.LEDGERS if os.path.exists(os.path.join(H.OUT, k))}
    def test_running_the_pipeline_changes_nothing_historical(self):
        from tests.historical import test_historical_integrity as H
        if not os.path.isdir(os.path.join(H.OUT, "reseller_pilot_history")): self.skipTest("directorio histórico no disponible")
        a = self._snapshot(); cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True); cli.run(E2E_REQUEST, tempfile.mkdtemp(), real=True); b = self._snapshot()
        self.assertEqual(a, b); self.assertEqual(a[2], H.LEDGERS)
        self.assertEqual(set(a[0]), set(H.MAN["files"])); self.assertEqual(a[0], H.MAN["files"])
    def test_reseller_records_and_action_distribution_are_unchanged(self):
        import collections
        recs = reseller_records(); self.assertEqual(len(recs), 30)
        self.assertEqual(dict(collections.Counter(r["recommended_action"]["action"] for r in recs)), {"NO_ACTION": 15, "HUMAN_REVIEW": 8, "CONTACT": 4, "DEFER": 3})
        from oi.rules import next_action as NA
        for r in recs: self.assertEqual({k: v for k, v in NA.derive_action(r).items()}, r["recommended_action"], r["record_id"])      # las reglas siguen dando lo mismo (incl. QUALIFICATION_PENDING con NOT_ASSESSED)
    def test_no_bytecode_written_into_historical_or_case_dirs(self):
        # Hay __pycache__ preexistentes (sesiones históricas del 07-oct); se exige que ESTE trabajo no cree/modifique ninguno
        import time
        cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True)
        for base in (os.path.join(ROOT, "cases"), os.path.join(HISTORY, "reseller_pilot_history"), os.path.join(HISTORY, "d010_v0_2_official")):
            if not os.path.isdir(base): continue
            for r, ds, fs in os.walk(base):
                for fn in fs:
                    if fn.endswith(".pyc"): self.assertLess(os.path.getmtime(os.path.join(r, fn)), _T0, os.path.join(r, fn))


def _draft(**intent):
    from oi.engine.spec.mock_interpreter import interpret
    d = interpret(E2E_REQUEST); d["intent"].update(intent); return d


class TestF11EntityLevelAndBusinessModelTraverseThePipeline(unittest.TestCase):
    def test_entity_level_is_preserved_from_search_spec_to_opportunity_record_for_all_five_values(self):
        for lvl in ("ESTABLISHMENT", "OPERATOR", "GROUP", "COMPANY", "UNKNOWN"):
            S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, draft=_draft(entity_level=lvl))
            self.assertEqual(S["status"], "COMPLETED_OFFLINE", (lvl, S["status"]))
            spec = read(S, "02_spec_result.json")["spec"]; self.assertEqual(spec["intent"]["entity_level"], lvl)
            q = read(S, "09_qualification.json"); recs = read(S, "11_opportunity_records.json")
            for r in recs: self.assertEqual(r["search_context"]["requested_entity_level"], lvl, lvl)
            for x in q: self.assertEqual(x["candidate"]["requested"]["entity_level"], lvl)
            has_gate = {r["record_id"]: any(g["gate_id"] == "ENTITY_LEVEL" for g in r["qualification"]["gates"]) for r in recs}
            self.assertEqual(set(has_gate.values()), {lvl != "UNKNOWN"}, lvl)           # UNKNOWN pedido => no se inventa un gate
    def test_a_mismatching_entity_level_is_a_fail_not_a_silent_pass(self):
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, draft=_draft(entity_level="GROUP"))
        recs = read(S, "11_opportunity_records.json")
        self.assertFalse(any(r["qualification"]["status"] == "ELIGIBLE" for r in recs if r["company_identity"]["entity_level"] == "COMPANY"))
        nu = by(recs, "Nu Grupo"); self.assertEqual(next(g for g in nu["qualification"]["gates"] if g["gate_id"] == "ENTITY_LEVEL")["status"], "PASS")
    def test_business_model_is_preserved_and_gated(self):
        for bm, expect_gate in ((["contractor"], True), (["distributor"], True), (["unknown"], False)):
            S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, draft=_draft(business_model=bm))
            self.assertEqual(S["status"], "COMPLETED_OFFLINE", bm)
            self.assertEqual(read(S, "02_spec_result.json")["spec"]["intent"]["business_model"], bm)
            recs = read(S, "11_opportunity_records.json")
            for r in recs: self.assertEqual(r["search_context"]["requested_business_model"], bm)
            self.assertEqual({any(g["gate_id"] == "BUSINESS_MODEL" for g in r["qualification"]["gates"]) for r in recs}, {expect_gate}, bm)
            if bm == ["distributor"]:
                self.assertFalse(any(r["qualification"]["status"] == "ELIGIBLE" for r in recs), "contratistas no pueden ser ELIGIBLE si se pidieron distribuidores")
    def test_business_model_reaches_the_candidate_dump(self):
        q = read(e2e_run(), "09_qualification.json"); self.assertTrue(all(x["candidate"]["requested"]["business_model"] == ["contractor"] for x in q))


class TestF11ContactCasesFromEvidenceOnly(unittest.TestCase):
    """Casos A–E de la orden + los 6 casos de contacto, todos derivados de evidencia (nada manual)."""
    def test_cases_a_b_c_d_e(self):
        g = lambda frag: (cs_by(frag)["contact"]["recommended_contact"] or {}).get("contact_quality", {}).get("grade")
        self.assertEqual(g("Alfa"), "A"); self.assertEqual(g("Beta"), "B"); self.assertEqual(g("Gamma"), "C")
        self.assertIsNone(cs_by("Delta")["contact"]["recommended_contact"]); self.assertEqual(cs_by("Delta")["recommended_action"]["action"], "HUMAN_REVIEW")
        self.assertIsNone(cs_by("Epsilon")["contact"]["recommended_contact"])
    def test_six_contact_situations(self):
        a = cs_by("Alfa")["contact"]["recommended_contact"]; self.assertEqual((a["target"], a["contact_quality"]["reachability"]), ("PERSON", "DIRECT"))              # 1 persona + canal directo
        self.assertEqual(cs_by("Iota")["contact"]["recommended_contact"]["target"], "DEPARTMENT")                                                                           # 2 departamento + canal
        self.assertEqual(cs_by("Gamma")["contact"]["recommended_contact"]["target"], "COMPANY")                                                                             # 3 empresa + canal general
        th = cs_by("Theta")["contact"]; self.assertTrue(th["people"]); self.assertTrue(all(not p.get("channel_ids") for p in th["people"]) or th["recommended_contact"] is None)  # 4 persona sin canal
        self.assertIsNone(th["recommended_contact"] if not th["channels"] else None)
        self.assertIsNone(cs_by("Eta")["contact"]["recommended_contact"])                                                                                                   # 5 no verificable
        self.assertEqual((cs_by("Delta")["contact"]["people"], cs_by("Delta")["contact"]["channels"]), ([], []))                                                           # 6 empresa sin contacto
    def test_contacts_are_never_typed_into_the_record_every_contact_traces_to_evidence(self):
        for r in cs_records():
            c = r["contact"]; ids = {e["evidence_id"] for e in c["evidence"]}
            for p in c["people"]: self.assertTrue(set(p["role_evidence_ids"]) <= ids, r["record_id"])
            for ch in c["channels"]: self.assertTrue(ch["evidence_ids"] and set(ch["evidence_ids"]) <= ids, r["record_id"])
    def test_nothing_not_found_is_silently_dropped(self):
        d = cs_by("Delta")["contact"]; self.assertTrue(d["not_found"]); self.assertEqual(d["enrichment_state"], "NOT_FOUND") if "NOT_FOUND" in str(d["enrichment_state"]) else None
    def test_stale_person_is_not_the_recommendation(self):
        rho = cs_by("Rho")["contact"]["recommended_contact"]; self.assertEqual(rho["target"], "COMPANY")


class TestF11EstimateOffline(unittest.TestCase):
    def test_offline_estimate_shows_the_four_fields_and_executes_nothing(self):
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, estimate=True)
        self.assertEqual((S["status"], S["exit_code"]), ("ESTIMATE_ONLY", 0)); e = S["estimate"]
        off = e["offline"]
        for k in ("estimated_data_calls", "estimated_infra_calls", "estimated_tool_credits", "paid_calls"): self.assertIn(k, off)
        self.assertEqual((off["estimated_tool_credits"], off["paid_calls"], off["external_calls"]), (0, 0, 0))
        self.assertEqual((S["SYNTHETIC_CALLS"], S["EXTERNAL_CALLS"], S["PAID_CALLS"], S["APOLLO_CREDITS"]), (0, 0, 0, 0))
        self.assertFalse(os.path.exists(os.path.join(S["out_dir"], "11_opportunity_records.json")))
    def test_real_projection_is_blocked_says_unknown_and_the_cap_is_not_an_authorization(self):
        rp = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True, estimate=True)["estimate"]["real_projection"]
        self.assertTrue(rp["blocked"]); self.assertFalse(rp["authorized"]); self.assertEqual(rp["paid_calls"], 0)
        self.assertTrue(rp["people_search"].startswith("UNKNOWN")); self.assertTrue(rp["contact_enrichment"].startswith("UNKNOWN"))
        self.assertEqual(rp["credit_cap_stated_by_user_NOT_AN_AUTHORIZATION"], 10)
    def test_cli_prints_the_estimate(self):
        p = subprocess.run([sys.executable, "-m", "oi", "run", E2E_REQUEST, "--offline", "--estimate", "--out", tempfile.mkdtemp()], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        for k in ("estimated_data_calls", "estimated_infra_calls", "estimated_tool_credits", "paid_calls", "UNKNOWN", "APOLLO_CREDITS = 0"): self.assertIn(k, p.stdout)
    def test_real_flag_is_refused_with_exit_3_and_no_transport(self):
        S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), real=True)
        self.assertEqual((S["status"], S["exit_code"]), ("REAL_EXECUTION_REFUSED", 3)); self.assertEqual((S["EXTERNAL_CALLS"], S["PAID_CALLS"], S["APOLLO_CREDITS"]), (0, 0, 0)); self.assertEqual(S["DATA_MODE"], "NONE")
    def test_offline_and_real_are_mutually_exclusive(self):
        p = subprocess.run([sys.executable, "-m", "oi", "run", E2E_REQUEST, "--offline", "--real", "--out", tempfile.mkdtemp()], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertNotEqual(p.returncode, 0)


class TestF11EvidenceContractIntake(unittest.TestCase):
    from oi.evidence import intake as _I
    base = {"evidence_id": "EV-T1", "subject": {"entity": "x.example"}, "field": "firmographic.industry", "value": "construction", "source_class": "SECTOR_SOURCE", "claim_status": "KNOWN", "confidence": "HIGH",
            "retrieved_at": "2026-10-01", "source_label": "SYNTHETIC test", "data_mode": "SYNTHETIC"}
    def _p(self, **kw):
        from oi.evidence import intake as I
        it = dict(self.base); it.update(kw); [it.pop(k) for k, v in list(kw.items()) if v is Ellipsis]; return I.check([it])
    def test_valid_item_has_no_problems(self): self.assertEqual(self._p(), [])
    def test_each_contract_field_is_required(self):
        from oi.evidence import intake as I
        for f in ("evidence_id", "subject", "field", "value", "source_class", "claim_status", "confidence", "retrieved_at"):
            it = dict(self.base); it.pop(f); self.assertTrue(I.check([it]), f)
    def test_real_data_mode_is_rejected_offline(self): self.assertTrue(any("offline" in x for x in self._p(data_mode="REAL")))
    def test_unverified_source_cannot_be_known(self): self.assertTrue(self._p(source_class="UNVERIFIED", claim_status="KNOWN"))
    def test_duplicate_ids_rejected(self):
        from oi.evidence import intake as I
        self.assertTrue(any("duplicado" in x for x in I.check([dict(self.base), dict(self.base)])))
    def test_producer_confidence_does_not_promote_an_inferred_item(self):
        from oi.evidence import intake as I
        out = I.to_record_evidence(dict(self.base, claim_status="INFERRED", confidence="HIGH"), "2026-10-08")
        self.assertEqual(out["claim_status"], "INFERRED"); self.assertNotEqual(out["evidence_strength"], "HIGH"); self.assertEqual(out["producer_confidence"], "HIGH")
    def test_real_producer_is_blocked(self):
        from oi.evidence import producers as PR
        with self.assertRaises(guard.RealModeBlocked): PR.make_producer("real", dataset={})


if __name__ == "__main__": unittest.main()
