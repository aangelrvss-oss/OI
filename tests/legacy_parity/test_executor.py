import copy, json, os, re, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *
from oi.engine.planner.planner import Planner
from oi.engine.planner.config import PlannerConfig
from oi.engine.executor import synthetic, state as S
from oi.engine.executor.runner import Executor, save
from oi.engine.executor.adapters.apollo import ApolloAdapter
from oi.engine.executor.transports import FixtureTransport, RecordedTransport, SYNTHETIC, REAL
from oi.engine.executor.config import make_config
from oi.engine.executor.errors import ProviderError, sanitize

CFG = PlannerConfig(); PL = Planner(CFG, clock=FIXED); SPEC = make_spec("case1")
def mkplan(credits=25, calls=30, spec=SPEC): return PL.plan(spec, budget_input={"source": "user_parameter", "max_tool_calls": calls, "max_apollo_credits": credits})["plan"]
PLAN = mkplan()
def run(transport=None, plan=PLAN, spec=SPEC, **cfg):
    cfg = {"confirm_degraded": True, "enrichment_batch_size": 4, **cfg}
    t = transport or FixtureTransport(synthetic.build())
    return Executor(plan, spec, ApolloAdapter(), t, make_config(**cfg), cfg=CFG, clock=FIXED).run(), t
data = lambda r, ph=None: [c for c in r["tool_calls"] if c["phase"] != "METERING" and (ph is None or c["phase"] == ph)]
bulk = lambda t: [c for c in t.calls if c[0] == "apollo_organizations_bulk_enrich"]


class TestBudget(unittest.TestCase):
    def test_A_hard_cap_limits_selection_and_blocks_calls(self):
        r, t = run(credit_hard_cap=6); self.assertEqual(r["credit"]["hard_cap"], 6)
        self.assertEqual(sum(c["actual_cost"] for c in data(r, "ENRICHMENT")), 6); self.assertEqual(r["credit"]["remaining_credit_budget"], 0)
        r, t = run(FixtureTransport(synthetic.build(), cost_per_returned_row=2), credit_hard_cap=10)          # el proveedor factura el doble de lo observado
        st = [c["status"] for c in data(r, "ENRICHMENT")]; self.assertIn("BUDGET_BLOCKED", st)
        self.assertEqual(len(bulk(t)), st.count("OK")); self.assertLessEqual(r["credit"]["actual_known"], 10)     # la llamada bloqueada NO llegó al transporte
        self.assertEqual((r["state"], r["stop_reason"]), ("BUDGET_EXHAUSTED", "BUDGET_BLOCKED"))
        for c in data(r):
            if c["status"] == "OK": self.assertLessEqual(c["estimated_cost"], 10)

    def test_A2_billing_above_observed_breaking_the_cap_is_detected_and_halts(self):
        r, t = run(FixtureTransport(synthetic.build(), cost_per_returned_row=3), credit_hard_cap=10)
        self.assertTrue(r["credit"]["cap_breached"]); self.assertEqual((r["state"], r["stop_reason"]), ("BUDGET_EXHAUSTED", "HARD_CAP_BREACHED_BY_PROVIDER_BILLING")); self.assertEqual(len(bulk(t)), 1)

    def test_A3_cap_is_the_minimum_of_experiment_plan_and_approval(self):
        self.assertEqual(run(credit_hard_cap=25)[0]["credit"]["hard_cap"], 25); self.assertEqual(run(plan=mkplan(credits=12), credit_hard_cap=25)[0]["credit"]["hard_cap"], 12)

    def test_B_actual_cost_is_measured_not_assumed(self):
        r, _ = run(FixtureTransport(synthetic.build(), cost_per_returned_row=2), credit_hard_cap=25)
        c = data(r, "ENRICHMENT")[0]
        self.assertEqual((c["estimated_cost"], c["actual_cost"], c["credit_before"] - c["credit_after"], c["credit_delta"]), (4, 8, 8, -8))
        for k in ("tool_call_id", "entity_ids", "timestamp" if False else "started_at", "phase", "operation", "status", "remaining_credit_budget", "input_hash"): self.assertIsNotNone(c[k], k)
        self.assertNotEqual(r["metrics"]["credit_variance_actual_minus_estimated"], 0)

    def test_C_unknown_actual_cost_is_recorded_as_unknown_and_halts_paid_calls(self):
        r, t = run(FixtureTransport(synthetic.build(), measurable=False))
        c = data(r, "ENRICHMENT")
        self.assertEqual((c[0]["actual_cost"], c[0]["credit_delta"]), ("UNKNOWN", "UNKNOWN")); self.assertEqual(c[1]["status"], "BUDGET_BLOCKED")
        self.assertTrue(r["credit"]["remaining_is_provisional"]); self.assertEqual((r["state"], r["stop_reason"]), ("PARTIAL", "PAID_CALLS_HALTED_UNKNOWN_ACTUAL_COST")); self.assertEqual(len(bulk(t)), 1)
        self.assertIsNone(r["metrics"]["credits_per_passed"])
        r2, _ = run(FixtureTransport(synthetic.build(), measurable=False), on_unknown_actual_cost="charge_estimate")
        self.assertTrue(all(x["status"] == "OK" for x in data(r2, "ENRICHMENT"))); self.assertEqual(r2["credit"]["remaining_credit_budget"], 25 - 20)

    def test_metering_reads_are_not_paid_calls(self):
        r, _ = run(); self.assertEqual(r["credit"]["paid_calls_with_unknown_actual_cost"], 0); self.assertEqual(sum(1 for c in r["tool_calls"] if c["phase"] == "METERING"), len(data(r)) + 1)


class TestIdempotencyAndEntities(unittest.TestCase):
    def test_D_duplicates_are_never_enriched_twice(self):
        r, t = run(); doms = [d for c in bulk(t) for d in c[1]["domains"]]
        self.assertEqual(len(doms), len(set(doms))); self.assertTrue(any(x["dedupe_status"].startswith("DUPLICATE_MERGED") for x in r["results"]))
        cache = {x["domain"].replace("http://www.", ""): synthetic.build()["enrich"][x["domain"].replace("http://www.", "")] for x in r["results"] if x["enrichment_status"] == "ENRICHED"}
        r2, t2 = run(prior_enrichment_cache=cache); asked = {d for c in bulk(t2) for d in c[1]["domains"]}
        self.assertFalse(asked & set(cache))                                                                     # lo ya enriquecido NO se vuelve a pedir
        self.assertEqual(sum(1 for x in r2["results"] if x["enrichment_status"] == "ENRICHMENT_REUSED" and x["dedupe_status"] == "primary"), len(cache))
        self.assertTrue(all(x["gate_status"] != "NOT_EVALUATED" for x in r2["results"] if x["enrichment_status"] == "ENRICHMENT_REUSED"))

    def test_E_force_reenrich_requires_explicit_reason(self):
        ds = synthetic.build(); cache = {"synth001.example": ds["enrich"]["synth001.example"]}
        r, t = run(prior_enrichment_cache=cache, max_enrichment_entities=1); self.assertNotIn("synth001.example", [d for c in bulk(t) for d in c[1]["domains"]])
        r, t = run(prior_enrichment_cache=cache, max_enrichment_entities=1, force_reenrich={"synth001.example": "auditar el cobro de re-enrichment"})
        self.assertEqual(bulk(t)[0][1]["domains"], ["synth001.example"]); self.assertIn("force_reenrich", json.dumps(data(r, "ENRICHMENT")[0]["capability_notes"]))
        r, t = run(prior_enrichment_cache=cache, force_reenrich={"synth001.example": " "}); self.assertEqual(r["state"], "BLOCKED"); self.assertIn("FORCE_REENRICH_WITHOUT_REASON", r["blockers"]); self.assertEqual(t.calls, [])

    def test_missing_domain_and_parent_subsidiary_rules(self):
        r, _ = run(max_enrichment_entities=20)
        nd = [x for x in r["results"] if "MISSING_DOMAIN" in (x["data_quality"] or [])]; self.assertTrue(nd)
        self.assertTrue(all(x["final_status"] in ("NOT_ENRICHABLE_MISSING_DOMAIN", "NOT_SELECTED_CAPACITY", "DUPLICATE_MERGED") for x in nd))
        g = [x for x in r["results"] if x["domain"] and "grupo-synth" in x["domain"]]; self.assertEqual(len({x["entity_id"] for x in g}), 2)      # padre/filial no se fusionan
        self.assertEqual(r["metrics"]["review_queue"], 1)


class TestErrorsAndFlow(unittest.TestCase):
    def test_F_empty_discovery(self):
        r, t = run(FixtureTransport({"lookup": [], "enrich": {}})); self.assertEqual((r["state"], r["stop_reason"]), ("PARTIAL", "EMPTY_DISCOVERY"))
        self.assertEqual(bulk(t), []); self.assertEqual(r["credit"]["actual_known"], 0); self.assertEqual(data(r, "DISCOVERY")[0]["status"], "EMPTY_RESULT")

    def test_G_provider_errors_are_classified_and_visible(self):
        for exc, code in ((ProviderError("PROVIDER_UNAVAILABLE", "API_INACCESSIBLE"), "PROVIDER_UNAVAILABLE"), (Exception("401 Unauthorized"), "AUTH_ERROR"), (TimeoutError("timed out"), "TIMEOUT")):
            r, t = run(FixtureTransport(synthetic.build(), errors={"apollo_organizations_lookup": [exc]}))
            self.assertEqual((r["state"], r["stop_reason"]), ("FAILED", f"DISCOVERY_FAILED:{code}")); self.assertEqual(data(r)[0]["error_code"], code); self.assertEqual(bulk(t), [])
        ds = synthetic.build(); ds["lookup"][3] = {"name": "sin id"}
        r, _ = run(FixtureTransport(ds)); self.assertEqual(data(r)[0]["error_code"], "MALFORMED_RESULT")
        r, t = run(FixtureTransport(synthetic.build(), errors={"apollo_organizations_bulk_enrich": [None, Exception("429 Too Many Requests")]}))
        self.assertEqual(r["state"], "PARTIAL"); self.assertIn("RATE_LIMIT", r["stop_reason"]); self.assertEqual(len(bulk(t)), 2)
        st = {x["enrichment_status"] for x in r["results"]}; self.assertIn("ENRICHMENT_ERROR:RATE_LIMIT", st); self.assertIn("NOT_ATTEMPTED_AFTER:RATE_LIMIT", st)
        err = [x for x in r["results"] if x["enrichment_status"] == "ENRICHMENT_ERROR:RATE_LIMIT"]
        self.assertTrue(all(x["gate_status"] == "NOT_EVALUATED_TECHNICAL_ERROR" and x["final_status"] == "ENRICHMENT_ERROR" for x in err))   # no se convierte en criterio UNKNOWN

    def test_H_partial_enrichment(self):
        r, _ = run(FixtureTransport(synthetic.build(), enrich_drop=["synth001.example", "synth002.example"]))
        self.assertEqual(sum(1 for x in r["results"] if x["enrichment_status"] == "NOT_RETURNED_BY_PROVIDER"), 2); self.assertEqual(r["metrics"]["credit_variance_actual_minus_estimated"], -2)
        self.assertEqual(r["state"], "DEGRADED")

    def test_I_pagination_stays_unverified_but_implementable(self):
        p = ApolloAdapter().capabilities()["pagination"]; self.assertEqual((p["supported_status"], p["enabled_for_experiment"], p["implementation_ready"]), ("UNVERIFIED", False, True))
        self.assertNotIn("forbidden", json.dumps(p).lower()); self.assertEqual(CFG.caps["CAP-LOOKUP-DEPTH"]["status"], "UNVERIFIED")
        r, t = run(discovery={"max_pages": 2, "per_page": 60}); self.assertEqual(r["state"], "BLOCKED"); self.assertEqual(t.calls, [])
        r, t = run(discovery={"max_pages": 2, "per_page": 60}, allow_unverified_capabilities=["pagination"])
        d = data(r, "DISCOVERY"); self.assertEqual(len(d), 2); self.assertIn("UNVERIFIED", d[1]["capability_notes"][0])

    def test_J_one_page_discovery_by_default(self):
        r, t = run(); look = [c for c in t.calls if c[0] == "apollo_organizations_lookup"]
        self.assertEqual(len(look), 1); self.assertEqual((look[0][1]["page"], look[0][1]["per_page"]), (1, 100)); self.assertEqual(len(r["results"]), 100)
        self.assertEqual(look[0][1]["organization_locations"], ["Spain"]); self.assertEqual(look[0][1]["organization_num_employees_ranges"], ["51,200", "201,500"])

    def test_K_state_transitions_are_closed_and_deterministic(self):
        sm = S.StateMachine(FIXED)
        with self.assertRaises(S.IllegalTransition): sm.go(S.COMPLETED)
        sm.go(S.RUNNING); sm.go(S.PARTIAL)
        with self.assertRaises(S.IllegalTransition): sm.go(S.RUNNING)
        self.assertEqual([e["to"] for e in run()[0]["events"]], ["PLANNED", "RUNNING", "DEGRADED"])
        ready = mkplan(credits=100); self.assertEqual(ready["status"], "ready"); self.assertEqual([e["to"] for e in run(plan=ready)[0]["events"]], ["PLANNED", "RUNNING", "COMPLETED"])
        r, t = run(confirm_degraded=False); self.assertEqual(([e["to"] for e in r["events"]], r["blockers"]), (["PLANNED", "BLOCKED"], ["DEGRADED_PLAN_NOT_CONFIRMED"])); self.assertEqual(t.calls, [])
        b = copy.deepcopy(PLAN); b["status"] = "blocked"; self.assertEqual(run(plan=b)[0]["state"], "BLOCKED")
        for k, v in S.MEANING.items(): self.assertTrue(v)

    def test_L_deterministic_rerun(self):
        a, _ = run(); b, _ = run(); self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True)); self.assertEqual(a["run_id"], b["run_id"])

    def test_M_no_external_writes(self):
        r, t = run(); ad = ApolloAdapter()
        self.assertTrue({c[0] for c in t.calls} <= set(ad.READ_OPERATIONS.values()))
        for op in ("contacts_create", "apollo_contacts_create", "send_email"):
            with self.assertRaises(ProviderError) as e: ad.build_request(op, {})
            self.assertEqual(e.exception.code, "OPERATION_NOT_ALLOWED")
        for ph in ("EVIDENCE", "DECISION_MAKER_ENRICHMENT"):
            r2, t2 = run(phases_enabled=["DISCOVERY", ph]); self.assertEqual(r2["state"], "BLOCKED"); self.assertEqual(t2.calls, [])
        p1c = PL.plan(make_spec("case1", ["O1", "O2"]), budget_input={**STD_BUDGET, "max_apollo_credits": 25})["plan"]
        r3, t3 = run(plan=p1c, spec=make_spec("case1", ["O1", "O2"])); self.assertNotIn("apollo_organizations_job_postings", {c[0] for c in t3.calls}); self.assertTrue(r3["notes"])
        bad = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|subprocess|smtplib)\b", re.M)
        root = os.path.join(os.environ["OI_PRODUCT_ROOT"], "oi", "engine", "executor")
        for d, _, fs in os.walk(root):
            for f in fs:
                if f.endswith(".py"): self.assertIsNone(bad.search(open(os.path.join(d, f), encoding="utf-8").read()), f)

    def test_N_no_api_key_leakage(self):
        secret = "sk-live-SECRET123"
        r, _ = run(FixtureTransport(synthetic.build(), errors={"apollo_organizations_bulk_enrich": [Exception(f"500 boom api_key={secret} Authorization: Bearer abc.def-ghi")]}))
        blob = json.dumps(r); self.assertFalse(secret in blob, "fuga de api_key"); self.assertFalse("abc.def-ghi" in blob, "fuga de bearer token"); self.assertTrue("[REDACTED]" in blob)
        with tempfile.TemporaryDirectory() as d:
            p = save(r, d)
            for f in os.listdir(p): self.assertFalse(secret in open(os.path.join(p, f), encoding="utf-8").read(), f)
        self.assertEqual(sanitize({"api_key": "x", "nested": {"Authorization": "y"}, "q": "token=zzz"}), {"api_key": "[REDACTED]", "nested": {"Authorization": "[REDACTED]"}, "q": "token=[REDACTED]"})
        self.assertEqual(sanitize("id 5f3a9c0e1b2d3e4f5a6b7c8d"), "id 5f3a9c0e1b2d3e4f5a6b7c8d")                     # los ids de Apollo no se tocan

    def test_O_every_candidate_is_reconstructible(self):
        r, _ = run(); from oi.engine.executor.ledger import ResultLedger, ToolCallLedger
        self.assertEqual(len(r["results"]), data(r, "DISCOVERY")[0]["result_count"])
        self.assertTrue(all(x["final_status"] and x["gate_status"] and x["entity_id"] for x in r["results"]))
        self.assertTrue(any(x["final_status"].startswith(("GATE_FAILED", "FAILED_GATE", "REVIEW_REQUIRED")) for x in r["results"]))          # quien falla un gate no se pierde
        rl = ResultLedger(); rl.rows = {x["candidate_id"]: x for x in r["results"]}; tl = ToolCallLedger(r["run_id"], r["plan_id"]); tl.rows = r["tool_calls"]
        e = next(x for x in r["results"] if x["enrichment_status"] == "ENRICHED"); h = rl.reconstruct(e["candidate_id"], tl)
        self.assertEqual({c["phase"] for c in h["tool_calls"]}, {"DISCOVERY", "ENRICHMENT"}); self.assertEqual(h["candidate"]["enrichment_call_id"], [c for c in h["tool_calls"] if c["phase"] == "ENRICHMENT"][0]["tool_call_id"])

    def test_synthetic_metrics_are_labelled_non_calibrating(self):
        m = run()[0]["metrics"]; self.assertEqual((m["label"], m["calibrating"]), (SYNTHETIC, False)); self.assertIn("NON-CALIBRATING", m["warning"])

    def test_industry_gate_without_validated_mapping_stays_pending(self):
        s = make_spec("case3"); p = PL.plan(s, budget_input={"source": "user_parameter", "max_tool_calls": 30, "max_apollo_credits": 25})["plan"]
        r, _ = run(plan=p, spec=s); g = [x["gate_details"]["G1"] for x in r["results"] if x["gate_details"]]
        self.assertTrue(g and all(d["gate_state"] == "pending_verification" and "MAPPING_MISSING" in d["reason"] for d in g))

    def test_M2_executor_refuses_a_write_even_if_a_buggy_adapter_produces_one(self):
        class Buggy(ApolloAdapter):
            def build_request(self, logical_op, generic):
                op, p = super().build_request(logical_op, generic)
                return ("apollo_contacts_create", p) if logical_op == "org_discovery" else (op, p)
        t = FixtureTransport(synthetic.build())
        r = Executor(PLAN, SPEC, Buggy(), t, make_config(confirm_degraded=True), cfg=CFG, clock=FIXED).run()
        self.assertNotIn("apollo_contacts_create", [c[0] for c in t.calls]); self.assertEqual(data(r)[0]["status"], "REFUSED"); self.assertEqual(data(r)[0]["error_code"], "OPERATION_NOT_ALLOWED")
        self.assertEqual(r["state"], "FAILED")
        class Sneaky(ApolloAdapter):          # operación permitida por el plan y no prohibida, pero NO vetada por la allowlist del adapter
            def build_request(self, logical_op, generic):
                op, p = super().build_request(logical_op, generic)
                return ("apollo_organizations_enrich", p) if logical_op == "org_enrich_bulk" else (op, p)
        t = FixtureTransport(synthetic.build()); r = Executor(PLAN, SPEC, Sneaky(), t, make_config(confirm_degraded=True), cfg=CFG, clock=FIXED).run()
        self.assertNotIn("apollo_organizations_enrich", [c[0] for c in t.calls]); self.assertTrue(all(c["status"] == "REFUSED" for c in data(r, "ENRICHMENT")))

    def test_D2_duplicate_apollo_id_without_domain_is_one_entity_and_one_enrichment(self):
        ds = synthetic.build(); dup = dict(ds["lookup"][0]); dup["domain"] = None; dup["website_url"] = None; ds["lookup"].insert(1, dup)
        r, t = run(FixtureTransport(ds)); rows = [x for x in r["results"] if x["entity_id"] == "apollo:" + ds["lookup"][0]["id"]]
        self.assertEqual(len(rows), 2); self.assertEqual(sum(1 for x in rows if x["dedupe_status"] == "primary"), 1)
        self.assertEqual([d for c in bulk(t) for d in c[1]["domains"]].count("synth001.example"), 1)



class TestPreflight3B2(unittest.TestCase):
    def setUp(self):
        from preflight_fixture import build_spec, build_plan, build_config
        from oi.engine.executor.preflight import preflight
        self.spec = build_spec(); self.plan = build_plan(self.spec); self.cfgx = build_config; self.pf = lambda c: preflight(self.spec, self.plan, c, CFG)

    def test_experiment_configuration_passes_every_check(self):
        r = self.pf(self.cfgx()); self.assertEqual(r["verdict"], "ALL_CHECKS_PASS", [c for c in r["checks"] if c["result"] != "PASS"])
        self.assertEqual(r["rehearsal"]["label"], "SYNTHETIC / NON-CALIBRATING")
        c = self.cfgx(); self.assertEqual((c["execution_approval"], c["confirm_degraded"]), (None, False))            # las decisiones humanas NO vienen pre-rellenadas

    def test_unsafe_variants_are_not_ready(self):
        for patch, check in (({"credit_hard_cap": 30}, "HARD_CREDIT_CAP"), ({"discovery": {"max_pages": 2, "per_page": 100}, "allow_unverified_capabilities": ["pagination"]}, "NO_PAGINATION_BEYOND_PAGE_1"),
                             ({"phases_enabled": ["DISCOVERY", "EVIDENCE"]}, "NO_GMAIL_PEOPLE_FIRECRAWL_OPENRUSH_PHASES"), ({"enrichment_batch_size": 10}, "BATCH_SIZE_WITHIN_TESTED"),
                             ({"require_measured_balance_before_paid_call": False}, "REAL_MODE_REQUIRES_BALANCE_BEFORE_PAID_CALL")):
            c = {**self.cfgx(), **patch}; r = self.pf(c)
            self.assertEqual(r["verdict"], "NOT_READY", patch); self.assertIn(check, [x["check"] for x in r["checks"] if x["result"] == "FAIL"])

    def test_plan_write_tools_are_flagged_and_not_executable(self):
        c = [x for x in self.pf(self.cfgx())["checks"] if x["check"] == "PLAN_WRITE_TOOLS_NOT_EXECUTABLE"][0]; self.assertEqual(c["result"], "PASS"); self.assertIn("sheets.review_export", c["detail"])


class TestRelay(unittest.TestCase):
    APPROVAL = {"approved_by": "test", "approved_at": "2026-10-02", "max_credits": 25}

    def relay(self, d, fixture=None, **cfg):
        fx = fixture or FixtureTransport(synthetic.build()); n = 0
        while True:
            t = RecordedTransport(d); r, _ = run(t, mode="relay", execution_approval=self.APPROVAL, **cfg)
            if r["state"] != "AWAITING_EXTERNAL_RESPONSE": return r, n
            q = r["pending_request"]; n += 1; self.assertLess(n, 60)
            try: t.record(q, response=fx.invoke(q["operation"], q["params"]))
            except ProviderError as e: t.record(q, error={"code": e.code, "message": e.message})

    def test_relay_requires_human_approval(self):
        with tempfile.TemporaryDirectory() as d:
            r, t = run(RecordedTransport(d), mode="relay"); self.assertEqual(r["state"], "BLOCKED"); self.assertIn("REAL_EXECUTION_REQUIRES_HUMAN_APPROVAL", r["blockers"]); self.assertEqual(os.listdir(os.path.join(d, "pending")), [])

    def test_relay_pauses_resumes_and_replays_without_new_spend(self):
        with tempfile.TemporaryDirectory() as d:
            r, n = self.relay(d); self.assertEqual(r["state"], "DEGRADED"); self.assertEqual(r["data_origin"], REAL)
            self.assertEqual(n, len(r["tool_calls"])); self.assertEqual(os.listdir(os.path.join(d, "pending")), [])
            again, _ = run(RecordedTransport(d), mode="relay", execution_approval=self.APPROVAL)
            self.assertEqual(json.dumps(again["results"], sort_keys=True), json.dumps(r["results"], sort_keys=True))
            req = json.load(open(os.path.join(d, "responses", os.listdir(os.path.join(d, "responses"))[0])))
            self.assertNotIn("api_key", json.dumps(req).lower())

    def test_relay_never_starts_a_paid_call_without_a_measured_balance(self):
        with tempfile.TemporaryDirectory() as d:
            r, _ = self.relay(d, FixtureTransport(synthetic.build(), measurable=False))
            e = data(r, "ENRICHMENT"); self.assertEqual(e[0]["status"], "BUDGET_BLOCKED"); self.assertIn("CREDIT_BALANCE_UNMEASURABLE", e[0]["error_message"])
            self.assertEqual(r["credit"]["actual_known"], 0); self.assertEqual(data(r, "DISCOVERY")[0]["status"], "OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
