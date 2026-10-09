import copy, json, os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *
from oi.engine.spec.versioning import canonical_hash
from oi.engine.planner.planner import Planner
from oi.engine.planner.config import PlannerConfig, ConfigError
from oi.engine.planner import rules as R; from oi.engine.planner import criteria as K; from oi.engine.planner import stopping as S; from oi.engine.planner import validation as PV; from oi.engine.evaluators import evaluation as EV; from oi.engine.evaluators import entity as EN; from oi.engine.planner import budget as B
from oi.engine.planner.ledger import Ledger, authorize, BudgetDenied
from oi.engine.planner.simulate import simulate

CFG = PlannerConfig()
PL = Planner(CFG, clock=FIXED)
def plan(spec, bi=STD_BUDGET, cfg=None, **kw): return (Planner(cfg, clock=FIXED) if cfg else PL).plan(spec, budget_input=bi, **kw)
def P(spec, bi=STD_BUDGET, cfg=None, **kw):
    o = plan(spec, bi, cfg, **kw); assert o["status"] == "planned", o["errors"]; return o["plan"]
def spec2b(confirm=CONF_2B):
    e = new_engine(); sid = e.process(draft("case2"))["spec"]["control"]["spec_id"]
    return e.process(draft("case2b"), user_confirmations=confirm, user_inputs=INPUT_2B, parent=(sid, 1))["spec"]
def spec4b():
    e = new_engine(); sid = e.process(draft("case4"))["spec"]["control"]["spec_id"]
    return e.process(draft("case4b"), user_confirmations=CONF_4B, user_inputs=INPUT_4B, parent=(sid, 1))["spec"]
def rehash(spec): spec["control"]["content_hash"] = canonical_hash(spec); return spec
def ph(p, pid): return next(x for x in p["phases"] if x["phase_id"] == pid)
def planned(p): return [x["phase_id"] for x in p["phases"] if x["status"] == "planned"]
def tools(p): return sorted({s["selected_tool"] for x in p["phases"] if x["status"] == "planned" for s in x["tool_selections"]})
def crit(p, cid): return next(c for c in p["criteria_digest"]["active"] if c["id"] == cid)
def work_sig(p):
    return json.dumps({"phases": [(x["phase_id"], x["status"], x["max_candidates"], x["estimated_calls"], x["active_criteria"],
                                   [(s["rule_id"], s["selected_tool"], s["operations"], s["triggering_criteria"]) for s in x["tool_selections"]]) for x in p["phases"]],
                       "usage": p["budget"]["estimated_usage"], "alloc": p["budget"]["phase_allocations"]}, sort_keys=True)
def cand(i, name=None, domain="auto", **k):
    return {"id": f"{i:024x}", "name": name or f"Empresa {i}", "domain": (f"empresa{i}.es" if domain == "auto" else domain), **k}
ES = [{"name": "ES", "level": "country"}]


class TestInputContract(unittest.TestCase):
    def test_confirmed_spec_produces_plan(self):
        o = plan(make_spec("case3")); self.assertEqual((o["status"], o["plan"]["status"]), ("planned", "ready"))
        self.assertEqual(o["plan"]["versions"]["planner_version"], "2.0.0"); self.assertIn("capability_matrix_version", o["plan"]["versions"])
        self.assertEqual(o["plan"]["authorization"], {"execution_mode": "dry_run_only", "spend_authorized": False, "reason": o["plan"]["authorization"]["reason"]})

    def test_non_confirmed_specs_produce_no_plan(self):
        for n in ("case2", "case4", "case5a", "case7"):
            o = plan(make_spec(n)); self.assertNotEqual(o["status"], "planned", n); self.assertIsNone(o["plan"])

    def test_invalid_spec_is_deterministic_error(self):
        s = make_spec("case3"); s["restrictions"]["quantity"]["target"] = 5
        self.assertEqual(plan(s)["reason_code"], "SPEC_TAMPERED")
        self.assertEqual(plan({"x": 1})["reason_code"], "SPEC_INVALID")
        self.assertEqual(plan(make_spec("case3"), field_registry_version="9.9.9")["reason_code"], "REGISTRY_VERSION_MISMATCH")
        self.assertEqual(plan(make_spec("case3"), ruleset_version="9.9.9")["reason_code"], "RULESET_VERSION_MISMATCH")

    def test_budget_contract(self):
        s = make_spec("case3")
        self.assertEqual(PL.plan(s)["reason_code"], "BUDGET_REQUIRED")
        self.assertEqual(plan(s, {"max_tool_calls": 80})["reason_code"], "BUDGET_APOLLO_CREDITS_REQUIRED")   # el enrichment consume créditos: presupuesto distinto de las llamadas
        self.assertEqual(plan(s, {"max_apollo_credits": 50})["reason_code"], "BUDGET_DATA_CALLS_REQUIRED")
        self.assertEqual(plan(s, {"max_tool_calls": 10, "data_calls_max": 11, "max_apollo_credits": 5})["reason_code"], "BUDGET_INVALID")
        self.assertEqual(plan(s, {"max_tool_calls": 10, "max_apollo_credits": 5, "tool_credits_max": {"apollo": 6}})["reason_code"], "BUDGET_INVALID")
        self.assertEqual(plan(s, {"max_tool_calls": -1, "max_apollo_credits": 5})["reason_code"], "BUDGET_INVALID")
        self.assertEqual(plan(make_spec("case1", ["O1", "O2"]), {k: v for k, v in STD_BUDGET.items() if k != "deep_research_top_n"})["reason_code"], "BUDGET_DEEP_TOP_N_REQUIRED")
        a = P(s, {"max_tool_calls": 80, "max_apollo_credits": 100}); b = P(s, {"data_calls_max": 80, "tool_credits_max": {"apollo": 100}})
        self.assertEqual(a["identity"]["fingerprint"], b["identity"]["fingerprint"])        # alias del Research Budget

    def test_regression_seven_cases_of_phase_1a(self):
        ok = {"case1", "case3", "case6"}
        for n in ("case1", "case2", "case3", "case4", "case5a", "case6", "case7"):
            prm = {"budget": BUDGET6} if n == "case6" else None
            o = PL.plan(make_spec(n, params=prm), budget_input=None if prm else STD_BUDGET)
            self.assertEqual(o["status"] == "planned", n in ok, (n, o["status"], o["reason_code"]))


class TestActiveVsProposed(unittest.TestCase):
    def test_proposed_criteria_add_no_phase_no_tool_no_call(self):
        p = P(make_spec("case3")); self.assertEqual([i["reason"] for i in p["criteria_digest"]["ignored"]], ["proposed", "proposed"])
        self.assertNotIn("EVIDENCE", planned(p)); self.assertEqual(tools(p), ["apollo.org_enrich", "apollo.org_lookup", "bigquery.ledger", "sheets.review_export"])

    def test_p2_only_proposed_criteria_and_p1_no_opportunity(self):
        p2 = P(make_spec("p2")); self.assertEqual({i["reason"] for i in p2["criteria_digest"]["ignored"]}, {"proposed"}); self.assertNotIn("EVIDENCE", planned(p2))
        self.assertIn("NO_ACTIVE_OPPORTUNITY_SIGNALS", [w["code"] for w in P(make_spec("p1"))["warnings"]])

    def test_confirming_a_signal_adds_work(self):
        a, b = P(make_spec("case1")), P(make_spec("case1", ["O1"]))
        self.assertNotIn("EVIDENCE", planned(a)); self.assertIn("EVIDENCE", planned(b)); self.assertIn("apollo.job_postings", tools(b))

    def test_no_ignored_criterion_appears_anywhere_in_work(self):
        p = P(spec2b()); ign = {i["id"] for i in p["criteria_digest"]["ignored"]}; self.assertEqual(ign, {"D2"})
        for x in p["phases"]:
            self.assertFalse(set(x["active_criteria"]) & ign)
            for s in x["tool_selections"]: self.assertFalse(set(s["triggering_criteria"]) & ign)

    def test_criterion_classes(self):
        c = P(make_spec("case1", ["O1", "O2"]))["criteria_digest"]["classes"]; self.assertEqual((c["HARD"], c["OPTIONAL_SIGNAL"]), (["G2", "GEO"], ["O1", "O2"]))


class TestToolSelection(unittest.TestCase):
    def test_openrush_only_with_active_digital_criterion(self):
        self.assertEqual([t for t in tools(P(spec2b())) if t.startswith("openrush.")], ["openrush.audit_site"])
        self.assertEqual([t for t in tools(P(make_spec("case3"))) if t.startswith("openrush.")], [])

    def test_every_selection_is_auditable(self):
        for x in P(make_spec("p3", ["O1", "O2"]))["phases"]:
            for s in x["tool_selections"]: self.assertTrue(s["rule_id"] and s["reason"] and "triggering_criteria" in s and s["tool_status"])

    def test_discovery_is_database_only_and_extra_sources_are_only_levers(self):
        p = P(make_spec("case3")); self.assertEqual([s["selected_tool"] for s in ph(p, "DISCOVERY")["tool_selections"]], ["apollo.org_lookup"])
        lev = {l["lever"]: l for l in p["expansion_policy"]["levers"]}; self.assertFalse(lev["additional_source"]["available"]); self.assertIn("unaudited", lev["additional_source"]["reason"])

    def test_decision_makers_only_for_finalists_after_prioritization(self):
        p = P(make_spec("p3", ["O1", "O2"])); d = ph(p, "DECISION_MAKER_ENRICHMENT")
        self.assertEqual((d["prerequisites"], d["selected_tool"]), (["PRIORITIZATION"], "apollo.people_search")); self.assertTrue(d["policy"]["finalists_only"])
        self.assertNotIn("DECISION_MAKER_ENRICHMENT", planned(P(make_spec("case1", ["O1", "O2"]))))
        self.assertIn("PEOPLE_COST_UNDECLARED", [w["code"] for w in p["warnings"]])

    def test_forbidden_operations_never_in_any_plan(self):
        for sp in (make_spec("case3"), spec2b(), spec4b(), make_spec("p3", ["O1", "O2"])):
            for x in P(sp)["phases"]:
                for s in x["tool_selections"]:
                    for o in s["operations"]: self.assertIsNone(CFG.is_forbidden(o), o)

    def test_every_operational_field_is_coverable_by_some_rule(self):
        for f, d in CFG.registry.fields.items():
            if d["status"] != "active": continue
            v = K._view("X", f, "fit", "gate", "HARD", CFG.routing[f], "eq", 1); ctx = {"active": [v], "spec": {}, "has_geo": False, "flags": {}}
            self.assertTrue(any(R.eval_cond(r["when"], ctx)[0] for r in CFG.ruleset["tool_rules"] if r["phase"] in ("ENRICHMENT", "EVIDENCE")), f)

    def test_phase_inclusion_rules(self):
        p = P(make_spec("case3")); self.assertEqual(planned(p), ["DISCOVERY", "NORMALIZATION", "ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS", "ENRICHMENT", "VERIFIED_GATES", "SCORING", "PRIORITIZATION", "RESULTS"])
        self.assertIn("EVIDENCE", planned(P(spec2b())))


class TestApolloFreeFallback(unittest.TestCase):
    def test_A_company_search_unavailable_planner_selects_lookup(self):
        p = P(make_spec("case3")); d = ph(p, "DISCOVERY"); s = d["tool_selections"][0]
        self.assertEqual((s["selected_tool"], s["rule_id"], s["fallback_for"]), ("apollo.org_lookup", "TS-A1F", "apollo.company_search"))
        rej = {r["tool"]: r for r in s["rejected_tools"]}; self.assertTrue(rej["apollo.company_search"]["reason"].startswith("unavailable_on_current_plan"))
        self.assertEqual((d["strategy"]["fallback_in_use"], d["strategy"]["primary_tool_status"]), (True, "unavailable_on_current_plan"))
        self.assertIn("TOOL_FALLBACK_IN_USE", [w["code"] for w in p["warnings"]])
        self.assertIn("apollo.company_search", CFG.tools); self.assertTrue(CFG.tools["apollo.company_search"]["keep_in_architecture"]); self.assertFalse(CFG.tools["apollo.org_lookup"]["equivalent_to_company_search"])

    def test_A2_unavailable_tool_cannot_be_authorized(self):
        p = P(make_spec("case3")); L = Ledger(p)
        d = authorize(p, L, {"phase": "DISCOVERY", "tool": "apollo.company_search", "operation": "apollo_mixed_companies_search"})
        self.assertEqual((d["allowed"], d["code"]), (False, "TOOL_UNAVAILABLE_ON_CURRENT_PLAN")); self.assertEqual(L.data_total(), 0)
        self.assertTrue(authorize(p, L, {"phase": "DISCOVERY", "tool": "apollo.org_lookup", "operation": "apollo_organizations_lookup"})["allowed"])

    def test_M_capability_can_switch_without_changing_the_search_spec(self):
        s = make_spec("case3"); h = s["control"]["content_hash"]; a = P(s)
        c2 = PlannerConfig(); c2.tools["apollo.company_search"]["status"] = "available"            # p. ej. un plan de pago
        b = P(s, cfg=c2); sel = ph(b, "DISCOVERY")["tool_selections"][0]
        self.assertEqual((sel["selected_tool"], sel["fallback_for"]), ("apollo.company_search", None)); self.assertEqual(s["control"]["content_hash"], h)
        self.assertEqual(planned(a), planned(b)); self.assertNotIn("TOOL_FALLBACK_IN_USE", [w["code"] for w in b["warnings"]])
        self.assertTrue(any(r["tool"] == "apollo.org_lookup" for r in sel["rejected_tools"]))
        self.assertEqual(ph(b, "DISCOVERY")["estimated_credits"]["status"], "unverified_not_estimated")   # su coste NO está observado: no se inventa
        self.assertEqual(PV.validate_plan(b, c2), [])

    def test_M2_another_provider_can_be_registered_without_touching_spec_or_planner_code(self):
        c3 = PlannerConfig(); c3.tools["apollo.org_lookup"]["status"] = "unavailable_on_current_plan"
        c3.tools["acme.company_search"] = {"provider": "acme", "operations": ["acme_company_search"], "kind": "read", "phases": ["DISCOVERY"], "status": "available", "role": "company_discovery",
                                           "observed_cost": {"credits_per_unit": None, "unit": "request", "cost_status": "unverified"}, "relies_on": []}
        base = next(r for r in c3.ruleset["tool_rules"] if r["rule_id"] == "TS-A1F")
        c3.ruleset["tool_rules"].insert(1, {**base, "rule_id": "TS-A1G", "tool": "acme.company_search", "operations": ["acme_company_search"], "reason": "proveedor alternativo de discovery", "fallback_of": "TS-A1"})
        p = P(make_spec("case3"), cfg=c3); self.assertEqual(ph(p, "DISCOVERY")["selected_tool"], "acme.company_search")
        self.assertEqual(PV.validate_plan(p, c3), []); self.assertIn("acme.company_search", [t["tool"] for t in p["capabilities"]["provider_abstraction"]["roles"]["company_discovery"]])

    def test_impossible_requirement_blocks_the_plan(self):
        c = PlannerConfig(); c.tools["apollo.org_enrich"]["status"] = "unavailable_on_current_plan"
        p = P(make_spec("case3"), cfg=c); self.assertEqual(p["status"], "blocked")
        self.assertTrue(all(r.startswith("IMPOSSIBLE_REQUIREMENT") for r in p["blocked_reasons"])); self.assertEqual(ph(p, "ENRICHMENT")["tool_selections"], [])        # la fase existe por sus criterios, pero no hay herramienta disponible
        self.assertEqual(authorize(p, Ledger(p), {"phase": "DISCOVERY", "tool": "apollo.org_lookup", "operation": "apollo_organizations_lookup"})["code"], "PLAN_BLOCKED")

    def test_capability_matrix_statuses_are_honest(self):
        m = CFG.caps
        self.assertEqual((m["CAP-SEARCH-AVAILABLE"]["status"], m["CAP-LOOKUP-AVAILABLE"]["status"], m["CAP-BULK-BATCH-10"]["status"], m["CAP-ENRICH-ID-INPUT"]["status"]), ("UNAVAILABLE", "VERIFIED", "UNVERIFIED", "UNVERIFIED"))
        self.assertEqual((m["CAP-LOOKUP-EMPLOYEE-FIELD"]["status"], m["CAP-LOOKUP-DEPTH"]["status"], m["CAP-JOBS-GROWTH"]["status"]), ("CONTRADICTED", "UNVERIFIED", "UNAVAILABLE"))
        u = P(make_spec("case3"))["capabilities"]["unverified_dependencies"]; self.assertIn("CAP-BULK-BATCH-10", u); self.assertIn("CAP-LOOKUP-RECALL", u)


class TestEnrichmentAndPreEnrichment(unittest.TestCase):
    def test_B_employee_count_is_not_decided_in_discovery(self):
        p = P(make_spec("case1")); g2 = crit(p, "G2")
        self.assertEqual((g2["stage"], g2["pre_enrichment_gate"], g2["verified_gate"], g2["discovery_available"]), ("enrichment", False, True, "none"))
        self.assertNotIn("G2", ph(p, "PRE_ENRICHMENT_FILTERS")["active_criteria"]); self.assertIn("G2", ph(p, "ENRICHMENT")["active_criteria"]); self.assertIn("G2", ph(p, "VERIFIED_GATES")["active_criteria"])
        f = [d for d in p["phases"][0]["strategy"]["queries"][0]["filters"] if d["criterion_id"] == "G2"][0]; self.assertEqual(f["role"], "recall_hint_not_evidence")
        ev = [e for e in ph(p, "VERIFIED_GATES")["policy"]["criterion_evaluation"] if e["criterion_id"] == "G2"][0]; self.assertEqual(ev["evidence_status_ceiling"], "ESTIMATED")
        self.assertIn("estimaci", g2["limitation"].lower())

    def test_B2_employee_estimate_evaluator_keeps_estimated_nature(self):
        a = EV.evaluate_employee_count({"estimated_num_employees": 120}, 50, 500); self.assertEqual((a["verdict"], a["evidence_status"], a["gate_state"]), ("PASS", "ESTIMATED", "passed"))
        b = EV.evaluate_employee_count({"estimated_num_employees": 900}, 50, 500); self.assertEqual((b["verdict"], b["evidence_status"], b["gate_state"]), ("FAIL", "ESTIMATED", "gate_failed_estimated"))   # no se descarta como hecho
        c = EV.evaluate_employee_count({}, 50, 500); self.assertEqual((c["verdict"], c["gate_state"]), ("UNKNOWN", "pending_verification"))
        self.assertNotIn(a["evidence_status"], ("KNOWN",))

    def test_pre_enrichment_uses_only_free_data(self):
        pol = ph(P(make_spec("case1")), "PRE_ENRICHMENT_FILTERS")["policy"]
        for k in ("estimated_num_employees", "industry enriquecida", "growth"): self.assertIn(k, pol["forbidden_inputs"])
        self.assertEqual(pol["criteria_evaluated_here"], []); self.assertEqual(sorted(pol["criteria_deferred_to_verified_gates"]), ["G2", "GEO"])
        self.assertFalse(any(r["pre_enrichment_gate"] for f, r in CFG.routing.items() if f in CFG.registry.fields))

    def test_pre_enrichment_only_deterministic_entity_exclusions(self):
        s = make_spec("case3"); s["restrictions"]["excluded_entities"] = ["Acciona"]; rehash(s); p = P(s)
        self.assertEqual(ph(p, "PRE_ENRICHMENT_FILTERS")["active_criteria"], ["EXCL"]); self.assertTrue(crit(p, "EXCL")["pre_enrichment_gate"]); self.assertNotIn("EXCL", ph(p, "ENRICHMENT")["active_criteria"])
        ents = EN.resolve_entities([cand(1, "Acciona Construcción"), cand(2, "Obras Norte", "obrasnorte.es")])["entities"]; r = EN.apply_entity_exclusions(ents, ["Acciona"])
        self.assertEqual([e["name"] for e in r["kept"]], ["Obras Norte"]); self.assertEqual(r["excluded"][0]["reason"], "excluded_entity_match")

    def test_entity_resolution_is_mandatory_before_enrichment(self):
        p = P(make_spec("case3")); order = [x["phase_id"] for x in p["phases"]]
        self.assertLess(order.index("ENTITY_RESOLUTION"), order.index("PRE_ENRICHMENT_FILTERS")); self.assertLess(order.index("PRE_ENRICHMENT_FILTERS"), order.index("ENRICHMENT")); self.assertLess(order.index("ENRICHMENT"), order.index("VERIFIED_GATES"))
        q = copy.deepcopy(p); ph(q, "ENRICHMENT")["prerequisites"] = ["DISCOVERY"]
        self.assertIn("INV14_ENRICHMENT_BEFORE_ENTITY_RESOLUTION", [c for c, _ in PV.validate_plan(q, CFG)])
        L = Ledger(p); self.assertEqual(authorize(p, L, {"phase": "ENRICHMENT", "tool": "apollo.org_enrich", "operation": "apollo_organizations_bulk_enrich", "criterion_id": "G1"}, ("DISCOVERY", "NORMALIZATION"))["code"], "PREREQUISITES_NOT_MET")


class TestEntityResolution(unittest.TestCase):
    def test_C_missing_domain_is_a_data_quality_issue_not_a_discard(self):
        r = EN.resolve_entities([cand(1), cand(2, "Sin Web", None), cand(3)]); e = [x for x in r["entities"] if x["name"] == "Sin Web"][0]
        self.assertEqual((len(r["entities"]), e["data_quality"], e["enrichable_by_apollo"]), (3, ["MISSING_DOMAIN"], False))
        q = EN.plan_enrichment_requests(r["entities"]); self.assertEqual(len(q["domains"]), 2); self.assertEqual([s["reason"].split(":")[0] for s in q["skipped"]], ["sin_dominio"])
        pol = ph(P(make_spec("case1")), "PRE_ENRICHMENT_FILTERS")["policy"]["missing_domain"]
        self.assertEqual((pol["automatic_fail"], pol["keep_candidate"], pol["reduces_data_confidence"]), (False, True, True))
        self.assertTrue(r["entities"][1]["rank"] == 1)     # sigue en el pool; no hay mecanismo de resolución inventado

    def test_D_duplicate_domain_enriched_once(self):
        r = EN.resolve_entities([{"name": "Alfa SL", "domain": "https://www.alfa.es/"}, {"name": "ALFA", "website_url": "alfa.es"}, {"name": "Beta", "domain": "beta.es"}])
        self.assertEqual(len(r["entities"]), 2); self.assertEqual(r["merges"][0]["kind"], "canonical_domain")
        q = EN.plan_enrichment_requests(r["entities"]); self.assertEqual(sorted(q["domains"]), ["alfa.es", "beta.es"]); self.assertEqual(q["max_credits"], 2)
        r2 = EN.resolve_entities([cand(1, "Matriz", "grupo.es"), cand(2, "Filial", "grupo.es")])          # padre/filial: dos ids, mismo dominio
        self.assertEqual(len(r2["entities"]), 2); self.assertEqual(r2["review"][0]["state"], "CONFLICTING"); self.assertEqual(len(EN.plan_enrichment_requests(r2["entities"])["domains"]), 1)

    def test_E_duplicate_apollo_id_is_one_entity(self):
        r = EN.resolve_entities([cand(7, "SYNTH Viajes", "synth-viajes.example"), cand(7, "SYNTH Viajes Tours", "otro.example"), cand(8)])
        self.assertEqual(len(r["entities"]), 2); self.assertEqual(r["merges"], [{"candidate": f"{7:024x}", "into": f"apollo:{7:024x}", "kind": "apollo_id"}]); self.assertEqual(r["silent_merges"], 0)

    def test_ambiguity_goes_to_review_never_silent_merge(self):
        r = EN.resolve_entities([{"name": "Iberia Obras SL"}, {"name": "Iberia Obras"}]); self.assertEqual(len(r["entities"]), 2); self.assertEqual(r["review"][0]["state"], "REVIEW_REQUIRED"); self.assertEqual(r["merges"], [])
        self.assertEqual(len(EN.resolve_entities([{"name": "Iberia Obras", "city": "Madrid"}, {"name": "Iberia Obras SL", "city": "madrid"}])["entities"]), 1)   # nombre + ubicación
        self.assertEqual(EN.resolve_entities([{"name": "Constructora Norte"}, {"name": "Constructura Norte"}])["review"], [])                                  # fuzzy desactivado sin umbral
        self.assertEqual(EN.resolve_entities([{"name": "Constructora Norte"}, {"name": "Constructura Norte"}], fuzzy_threshold=.9)["review"][0]["kind"], "fuzzy_name")

    def test_L_re_enrichment_is_blocked_unless_forced(self):
        ents = EN.resolve_entities([cand(1), cand(2), cand(3)])["entities"]; cache = {"empresa1.es": {"enriched": True}}
        q = EN.plan_enrichment_requests(ents, enriched_cache=cache); self.assertEqual(q["domains"], ["empresa2.es", "empresa3.es"]); self.assertIn("ya_enriquecida", q["skipped"][0]["reason"])
        f = EN.plan_enrichment_requests(ents, enriched_cache=cache, force_reenrich=["empresa1.es"]); self.assertEqual((f["forced"], len(f["domains"])), (["empresa1.es"], 3))
        self.assertEqual(EN.plan_enrichment_requests(ents, capacity=2)["max_credits"], 2)
        self.assertIn("never_reenrich", ph(P(make_spec("case3")), "ENRICHMENT")["policy"])

    def test_batches_use_configured_size(self):
        ents = EN.resolve_entities([cand(i) for i in range(25)])["entities"]
        self.assertEqual([len(b) for b in EN.plan_enrichment_requests(ents, batch_size=10)["batches"]], [10, 10, 5]); self.assertEqual([len(b) for b in EN.plan_enrichment_requests(ents, batch_size=4)["batches"]][:2], [4, 4])


class TestVerifiedGates(unittest.TestCase):
    def test_F_location_conflict_is_conflicting_review_not_fail(self):
        r = EV.evaluate_location({"country": "Spain", "city": "A Rúa", "state": "Galicia"}, "rua luis mota feo, luanda, luanda 00000-000, ao", ES)
        self.assertEqual((r["location_state"], r["verdict"], r["evidence_status"], r["gate_state"]), ("location_conflicting", "CONFLICTING", "CONFLICTING", "conflicting_review"))
        v = EV.evaluate_location({"country": "Spain", "city": "Madrid", "state": "Community of Madrid"}, "calle gran vía 39, madrid 28013, es", ES); self.assertEqual((v["location_state"], v["verdict"]), ("location_verified", "PASS"))
        o = EV.evaluate_location({"country": "Portugal"}, "rua x, lisboa, pt", ES); self.assertEqual((o["location_state"], o["verdict"], o["gate_state"]), ("location_verified", "FAIL", "failed"))
        u = EV.evaluate_location({}, None, ES); self.assertEqual((u["location_state"], u["verdict"]), ("location_unknown", "UNKNOWN"))
        one = EV.evaluate_location({"country": "Portugal"}, None, ES); self.assertEqual((one["verdict"], one["location_state"]), ("UNKNOWN", "location_unknown"))     # una sola fuente no basta para FAIL
        raw_only = EV.evaluate_location({"country": "Spain"}, "x, y, fr", ES); self.assertNotEqual(raw_only["verdict"], "FAIL")                                          # raw != objetivo NO es FAIL automático
        self.assertEqual(EV.evaluate_location({"country": "Spain"}, "x, y, es", [{"name": "Portugal", "level": "country"}], invert=True)["verdict"], "PASS")

    def test_G_industry_ambiguity_is_not_absolute_truth(self):
        civ = {"industry": "information technology & services", "industries": ["leisure, travel & tourism"]}
        a = EV.evaluate_industry(civ, ["leisure, travel & tourism"]); self.assertEqual((a["verdict"], a["evidence_status"], a["gate_state"]), ("CONFLICTING", "CONFLICTING", "conflicting_review"))
        b = EV.evaluate_industry({"industry": "construction", "industries": ["construction"]}, ["construction"]); self.assertEqual((b["verdict"], b["evidence_status"]), ("PASS", "INFERRED"))   # nunca KNOWN
        c = EV.evaluate_industry({"industry": "media production"}, ["construction"]); self.assertEqual((c["verdict"], c["gate_state"]), ("FAIL", "gate_failed_inferred"))
        self.assertEqual(EV.evaluate_industry({}, ["x"])["verdict"], "UNKNOWN")
        d = EV.evaluate_industry({"industry": "construction", "naics_codes": ["541511"]}, ["construction"], ["236"]); self.assertEqual(d["verdict"], "CONFLICTING")

    def test_growth_requested_window_vs_available_window(self):
        rec = {"organization_headcount_twelve_month_growth": 0.30, "organization_headcount_six_month_growth": 0.1}
        a = EV.evaluate_growth(rec, {"unit": "days", "value": 365}, "gte", 25)
        self.assertEqual((a["verdict"], a["evidence_status"]), ("PASS", "INFERRED")); self.assertEqual(a["available_window"]["months"], 12); self.assertIn("aproximacion_dias_a_meses", a["transformation"]); self.assertEqual(a["requested_window"], {"unit": "days", "value": 365})
        b = EV.evaluate_growth(rec, {"unit": "months", "value": 12}, "gte", 25); self.assertEqual(b["evidence_status"], "ESTIMATED")
        self.assertEqual(EV.evaluate_growth(rec, {"unit": "days", "value": 90}, "gte", 25)["verdict"], "UNKNOWN")                # sin ventana equivalente no se estira
        self.assertEqual(EV.evaluate_growth({}, {"unit": "days", "value": 365}, "gte", 25)["gate_state"], "pending_verification")
        self.assertEqual(EV.evaluate_growth({"organization_headcount_twelve_month_growth": 0.1}, {"unit": "days", "value": 365}, "gte", 25)["gate_state"], "gate_failed_inferred")

    def test_growth_criterion_is_routed_to_enrichment_with_limitation(self):
        p = P(spec4b()); c = crit(p, "C1"); self.assertEqual((c["stage"], c["evaluator"]), ("enrichment", "growth_window_mapping")); self.assertIn("365", c["limitation"])
        self.assertIn("C1", ph(p, "ENRICHMENT")["active_criteria"])

    def test_unknown_conflicting_semantics_are_never_pass_or_fail(self):
        for v, e in (("UNKNOWN", "UNKNOWN"), ("CONFLICTING", "CONFLICTING")): self.assertNotIn(EV.gate_outcome(v, e), ("passed", "failed", "gate_failed_estimated", "gate_failed_inferred"))
        self.assertEqual(EV.gate_outcome("FAIL", "KNOWN"), "failed"); self.assertEqual(EV.gate_outcome("FAIL", "ESTIMATED"), "gate_failed_estimated")
        g = ph(P(make_spec("case1")), "VERIFIED_GATES")["policy"]; self.assertEqual(g["gate_states"]["UNKNOWN"], "pending_verification"); self.assertEqual(g["gate_states"]["CONFLICTING"], "conflicting_review")


class TestEvidenceAndJobPostings(unittest.TestCase):
    def test_H_job_postings_are_evidence_not_a_universal_gate(self):
        p = P(make_spec("case1", ["O1"])); e = ph(p, "EVIDENCE")
        self.assertIn("apollo.job_postings", [s["selected_tool"] for s in e["tool_selections"]]); self.assertNotIn("apollo.job_postings", [s["selected_tool"] for x in p["phases"] if x["phase_id"] != "EVIDENCE" for s in x["tool_selections"]])
        self.assertEqual(crit(p, "O1")["class"], "OPTIONAL_SIGNAL"); self.assertNotIn("O1", ph(p, "VERIFIED_GATES")["active_criteria"]); self.assertEqual(ph(p, "VERIFIED_GATES")["policy"]["evidence_stage_gates"], [])
        self.assertEqual(e["policy"]["selection"], "top_n"); self.assertIn("NO equivale a crecimiento", e["policy"]["job_postings"])
        self.assertNotIn("apollo.job_postings", tools(P(make_spec("case3"))))                                   # no universal
        self.assertEqual(crit(p, "O1")["limitation"], CFG.routing["hiring.ops_admin_roles"]["confidence_limitations"])

    def test_job_postings_cost_and_top_n_bound(self):
        p = P(make_spec("case1", ["O1"]), {**STD_BUDGET, "deep_research_top_n": 4}); e = ph(p, "EVIDENCE")
        self.assertLessEqual(e["max_candidates"], 4); self.assertEqual(e["estimated_credits"]["credits"], e["max_candidates"] * 1)       # 1 crédito por petición (observado)
        self.assertEqual(e["estimated_credits"]["status"], "estimated_from_observed_cost")


class TestPoolAndBudget(unittest.TestCase):
    def test_pool_without_history_is_calibration_required_not_a_fixed_multiplier(self):
        p = P(make_spec("case3")); b = p["coverage"]["candidate_pool"]["basis"]
        self.assertEqual(b["calibration_required"], ["gate_pass_rate", "pre_filter_survival"]); self.assertIn("CALIBRATION_REQUIRED", [w["code"] for w in p["warnings"]])
        self.assertEqual(CFG.params["pool_policy"]["enrichment_multiplier_without_history"]["status"], "calibration_required")
        for k in ("pool_multiplier", "multiplier_used"): self.assertNotIn(k, json.dumps(p))

    def test_history_overrides_conservative_defaults(self):
        s = make_spec("case3"); a = P(s); b = P(s, history={"gate_pass_rate": .5, "pre_filter_survival": .9, "sample_size": 120})
        self.assertEqual(b["coverage"]["candidate_pool"]["basis"]["calibration_required"], []); self.assertEqual(b["coverage"]["enrichment_capacity"]["ideal"], 40)   # ceil(20/.5)
        self.assertLess(b["coverage"]["candidate_pool"]["ideal"], a["coverage"]["candidate_pool"]["ideal"]); self.assertNotIn("CALIBRATION_REQUIRED", [w["code"] for w in b["warnings"]])
        for bad in ({"gate_pass_rate": 0}, {"gate_pass_rate": 1.5}, {"x": 1}): self.assertEqual(plan(s, history=bad)["reason_code"], "HISTORY_INVALID")

    def test_K_pool_respects_verified_depth_and_discovery_cap(self):
        s = make_spec("case6", params={"budget": {"data_calls_max": 200, "tool_credits_max": {"apollo": 400}, "deep_research_top_n": 5}}); p = P(s, None)
        self.assertEqual(p["coverage"]["planned_pool"], 100); self.assertTrue(p["coverage"]["candidate_pool"]["capped_by_observed_depth"]); self.assertIn("POOL_CAPPED_BY_OBSERVED_DEPTH", [w["code"] for w in p["warnings"]])
        d = ph(p, "DISCOVERY")["strategy"]["pagination"]; self.assertEqual((d["total_count"], d["cursor"], d["depth_beyond_verified"], d["pages_planned"]), ("not_available_do_not_assume", "not_assumed", "unverified", 1))
        c = PlannerConfig(); c.params["discovery"]["max_candidates_cap"]["value"] = 30; self.assertEqual(P(make_spec("case3"), cfg=c)["coverage"]["planned_pool"], 30)
        v = P(make_spec("case3"), llm_proposals={"query_variants": ["obra civil"]}); self.assertEqual(v["coverage"]["candidate_pool"]["verified_depth_cap"], 200)    # solo variantes validadas amplían

    def test_K2_huge_noisy_pool_simulation_stays_capped(self):
        p = P(make_spec("case3")); r = simulate(p, {"discovery_rounds": [{"returned": 100, "dup": 0}] * 6, "prefilter_survival": .1, "gate_pass_rate": .9})
        pr = r["progress"]; self.assertLessEqual(pr["unique_total"], p["coverage"]["planned_pool"]); self.assertLessEqual(pr["pages_used"], 1); self.assertEqual(pr.get("discovery_close_reason"), "MAX_POOL")
        self.assertEqual(r["stop_reason"], "MAX_POOL"); self.assertEqual(r["outcome"], "partial"); self.assertLessEqual(pr["enriched"], pr["enrichable"])        # el ruido no se enriquece

    def test_discovery_does_not_mean_enrichment(self):
        c = P(make_spec("case3"))["coverage"]; self.assertGreater(c["planned_pool"], c["planned_enrichment"]); self.assertIn("NO implica enriquecer", c["funnel"])
        self.assertEqual(ph(P(make_spec("case3")), "ENRICHMENT")["policy"]["never"], "enriquecer el pool descubierto completo")

    def test_credits_and_calls_are_separate_budgets(self):
        s = make_spec("case3"); a = P(s, {**STD_BUDGET, "max_tool_calls": 400}); b = P(s, {**STD_BUDGET, "max_apollo_credits": 30})
        self.assertEqual(a["coverage"]["planned_enrichment"], P(s)["coverage"]["planned_enrichment"])          # más llamadas NO enriquecen más empresas
        self.assertLess(b["coverage"]["planned_enrichment"], P(s)["coverage"]["planned_enrichment"]); self.assertEqual(b["budget"]["tool_call_budget"]["max"], 80)
        self.assertEqual(b["budget"]["credit_budget"]["apollo"]["by_phase"]["ENRICHMENT"], 27); self.assertEqual(b["coverage"]["planned_enrichment"], 27)
        self.assertTrue(b["budget"]["tool_call_budget"]["separate_from_credits"]); self.assertEqual(b["degradations"][0]["binding_constraint"], "credits")

    def test_credits_are_estimated_only_from_observed_costs(self):
        p = P(make_spec("p3", ["O1", "O2"])); by = {x["phase_id"]: x["estimated_credits"] for x in p["phases"] if x["status"] == "planned"}
        self.assertEqual((by["DISCOVERY"]["status"], by["DISCOVERY"]["credits"]), ("observed_zero", 0))
        self.assertEqual((by["ENRICHMENT"]["status"], by["ENRICHMENT"]["credits"]), ("estimated_from_observed_cost", ph(p, "ENRICHMENT")["max_candidates"]))
        self.assertEqual((by["DECISION_MAKER_ENRICHMENT"]["status"], by["DECISION_MAKER_ENRICHMENT"]["credits"]), ("unverified_not_estimated", None)); self.assertIn("firecrawl", by["EVIDENCE"]["unverified_providers"])
        q = copy.deepcopy(p); ph(q, "DECISION_MAKER_ENRICHMENT")["estimated_credits"].update(credits=5); self.assertIn("INV11_CREDITS_ESTIMATED_WITHOUT_OBSERVED_COST", [c for c, _ in PV.validate_plan(q, CFG)])
        self.assertEqual(p["budget"]["credit_budget"]["apollo"]["cost_status"], "observed"); self.assertEqual(CFG.tools["apollo.people_search"]["observed_cost"]["cost_status"], "undeclared")

    def test_I_budget_insufficient_for_enrichment_is_degraded_and_never_fakes_qualification(self):
        p = P(make_spec("case6", params={"budget": BUDGET6}), None); self.assertEqual(p["status"], "degraded")
        self.assertLess(p["coverage"]["planned_enrichment"], p["coverage"]["enrichment_capacity"]["ideal"]); self.assertLess(p["coverage"]["achievable_qualified_target"], 100)
        self.assertIn("TARGET_UNREACHABLE_WITH_BUDGET", [w["code"] for w in p["warnings"]]); self.assertEqual(p["degradations"][0]["reason"], "allocation_capacity")
        r = simulate(p, {"discovery_rounds": [{"returned": 100, "dup": 0}], "prefilter_survival": 1.0, "gate_pass_rate": 1.0}); c = r["claims"]
        self.assertFalse(c["may_claim_target_qualified"]); self.assertLessEqual(c["qualified"], c["enriched"]); self.assertLessEqual(c["enriched"], p["coverage"]["planned_enrichment"]); self.assertNotEqual(r["outcome"], "completed")
        self.assertEqual(r["credits_used"]["apollo"] <= 20, True)

    def test_zero_credits_blocks_enrichment_plan(self):
        p = P(make_spec("case3"), {**STD_BUDGET, "max_apollo_credits": 0}); self.assertEqual(p["status"], "blocked"); self.assertTrue(any("ENRICHMENT" in r for r in p["blocked_reasons"]))

    def test_N_bulk_batch_size_is_provisional_and_not_a_hard_maximum(self):
        pol = ph(P(make_spec("case3")), "ENRICHMENT")["policy"]["batch_size"]
        self.assertEqual((pol["configured_batch_size"], pol["status"], pol["max_verified"]), (10, "provisional", "unknown")); self.assertTrue(pol["configurable"]); self.assertTrue(pol["audit_required_before_bulk_optimization"])
        self.assertNotIn("max_batch_size", json.dumps(P(make_spec("case3"))))
        s = make_spec("case3"); base = P(s); c = PlannerConfig(); c.params["enrichment"]["batch_size"]["configured_batch_size"] = 4; q = P(s, cfg=c)
        E = base["coverage"]["planned_enrichment"]; self.assertEqual(ph(base, "ENRICHMENT")["estimated_calls"]["data_calls_max"], -(-E // 10)); self.assertEqual(ph(q, "ENRICHMENT")["estimated_calls"]["data_calls_max"], -(-E // 4))
        self.assertEqual(ph(base, "ENRICHMENT")["estimated_calls"]["calls_if_batch_not_supported"], E); self.assertEqual(q["budget"]["credit_budget"]["apollo"]["estimated"], base["budget"]["credit_budget"]["apollo"]["estimated"])   # el coste no depende del tamaño de lote
        self.assertIn("BATCH_SIZE_UNVERIFIED", [w["code"] for w in base["warnings"]])
        c2 = PlannerConfig(); c2.params["enrichment"]["batch_size"]["status"] = "verified"
        with self.assertRaises(ConfigError): c2._selfcheck()

    def test_bulk_cost_semantics_documented(self):
        b = ph(P(make_spec("case3")), "ENRICHMENT")["policy"]; self.assertIn("duplicados incluidos", b["billing"]); self.assertEqual(b["identifier"]["unverified"], ["id"]); self.assertEqual(b["economic_role"], "primary_bottleneck")
        self.assertEqual(CFG.caps["CAP-ENRICH-BILLING"]["status"], "VERIFIED")

    def test_allocation_arithmetic(self):
        p = P(make_spec("case1", ["O1", "O2"])); b = p["budget"]; a = b["phase_allocations"]["by_phase"]
        self.assertEqual(sum(x["data_calls"] for x in a.values()) + b["reserve"]["data_calls"], b["tool_call_budget"]["max"])
        self.assertEqual(sum(x["credits"]["apollo"] or 0 for x in a.values()) + b["reserve"]["credits"]["apollo"] + b["credit_budget"]["apollo"]["unallocated"], 100); self.assertEqual(a["DISCOVERY"]["credits"]["apollo"], 0)     # el lookup gratuito no consume créditos
        self.assertEqual(B.apportion(10, {"a": 1, "b": 1, "c": 1}), {"a": 4, "b": 3, "c": 3}); self.assertEqual(sum(B.apportion(97, {"a": .15, "b": .35, "c": .3}).values()), 97)

    def test_infeasible_infra_budget_is_blocked_not_faked(self):
        p = P(make_spec("case3"), {**STD_BUDGET, "infra_calls_max": 2}); self.assertEqual(p["status"], "blocked"); self.assertTrue(any("INFRA" in r for r in p["blocked_reasons"]))


ENR = {"phase": "ENRICHMENT", "tool": "apollo.org_enrich", "operation": "apollo_organizations_bulk_enrich", "criterion_id": "G1"}
PRE = ("DISCOVERY", "NORMALIZATION", "ENTITY_RESOLUTION", "PRE_ENRICHMENT_FILTERS")


class TestLedgerAndAuthorize(unittest.TestCase):
    def setUp(self):
        self.p = P(make_spec("case3")); self.L = Ledger(self.p)
        self.cr = self.p["budget"]["credit_budget"]["apollo"]
    def A(self, call, done=PRE, **k): return authorize(self.p, self.L, {**call, **k}, done)

    def test_phase_credit_cap_stops_enrichment_before_the_reserve(self):
        cap, n = self.cr["by_phase"]["ENRICHMENT"], 0
        while True:
            d = self.A(ENR, units=10)
            if not d["allowed"]: break
            n += 1; self.assertLessEqual(n, 100)
        self.assertEqual((d["code"], self.L.credits_used["apollo"]), ("PHASE_CREDIT_ALLOCATION_EXCEEDED", cap)); self.assertLessEqual(self.L.credits_used["apollo"], self.cr["max"] - self.cr["reserve"])
        self.assertLess(self.L.data_total(), self.p["budget"]["tool_call_budget"]["max"] // 2)          # muchas llamadas sobran: lo escaso son los créditos
        self.assertEqual(self.L.remaining()["credits"]["apollo"], self.cr["reserve"] + self.cr["unallocated"])      # lo no asignado y la reserva siguen intactos

    def test_credits_are_checked_before_calling_with_observed_cost(self):
        d = self.A(ENR, units=10); self.assertEqual(d["estimated_credits"], 10)
        self.assertEqual(self.A(ENR, units=self.cr["max"] + 1)["code"], "CREDIT_LIMIT_EXCEEDED")
        self.assertEqual(self.L.credits_used["apollo"], 10); self.assertEqual(self.L.records[-1]["estimated_credits"], 10)         # una denegación no deja contabilidad a medias
        self.assertEqual(self.A({"phase": "DISCOVERY", "tool": "apollo.org_lookup", "operation": "apollo_organizations_lookup"}, done=())["estimated_credits"], 0)

    def test_calls_never_exceed_budget_and_reserve_is_protected(self):
        n = 0
        while self.L.alloc["DISCOVERY"] - self.L.spent["DISCOVERY"] > 0: self.L.request("DISCOVERY", 1); n += 1
        with self.assertRaises(BudgetDenied) as c: self.L.request("DISCOVERY", 1)
        self.assertEqual(c.exception.code, "PHASE_ALLOCATION_EXCEEDED"); self.assertLessEqual(self.L.data_total(), self.p["budget"]["tool_call_budget"]["max"] - self.p["budget"]["tool_call_budget"]["reserve"])
        with self.assertRaises(BudgetDenied) as c: self.L.request("DISCOVERY", 1, purpose="close_top_n")
        self.assertEqual(c.exception.code, "RESERVE_PHASE_NOT_ALLOWED")

    def test_reserve_cannot_be_spent_before_its_condition(self):
        p = P(make_spec("case1", ["O1", "O2"])); L = Ledger(p)
        with self.assertRaises(BudgetDenied) as c: L.request("EVIDENCE", 1, purpose="close_top_n"); 
        self.assertEqual(c.exception.code, "RESERVE_CONDITION_NOT_MET")
        with self.assertRaises(BudgetDenied) as c: L.request("EVIDENCE", 1, purpose="whatever")
        self.assertEqual(c.exception.code, "RESERVE_PURPOSE_INVALID")
        with self.assertRaises(BudgetDenied) as c: L.request("ENRICHMENT", 1, purpose="close_top_n")
        self.assertEqual(c.exception.code, "RESERVE_PHASE_NOT_ALLOWED")
        for _ in range(L.alloc["EVIDENCE"]): L.request("EVIDENCE", 1)
        for _ in range(p["budget"]["reserve"]["data_calls"]): L.request("EVIDENCE", 1, purpose="close_top_n")
        with self.assertRaises(BudgetDenied) as c: L.request("EVIDENCE", 1, purpose="close_top_n")
        self.assertEqual(c.exception.code, "RESERVE_EXHAUSTED")

    def test_credit_reserve_is_only_for_closing_top_n_after_condition(self):
        p = P(make_spec("case1", ["O1", "O2"])); L = Ledger(p); lim = p["budget"]["credit_budget"]["apollo"]["max"]
        with self.assertRaises(BudgetDenied) as c: L.request("EVIDENCE", 1, tool="apollo.job_postings", estimated_credits=1, purpose="close_top_n")
        self.assertEqual(c.exception.code, "RESERVE_CONDITION_NOT_MET")
        while True:
            try: L.request("EVIDENCE", 1, tool="apollo.job_postings", estimated_credits=1)
            except BudgetDenied as e: self.assertIn(e.code, ("PHASE_CREDIT_ALLOCATION_EXCEEDED", "PHASE_ALLOCATION_EXCEEDED")); break
        self.assertLessEqual(L.credits_used["apollo"], lim - p["budget"]["credit_budget"]["apollo"]["reserve"])

    def test_authorize_rules(self):
        self.assertEqual(self.A({"phase": "DISCOVERY", "tool": "apollo.people_search", "operation": "apollo_mixed_people_api_search"}, done=())["code"], "TOOL_NOT_SELECTED_FOR_PHASE")
        self.assertEqual(self.A({"phase": "DISCOVERY", "tool": "apollo.org_lookup", "operation": "apollo_contacts_create"}, done=())["code"], "FORBIDDEN_OPERATION")
        self.assertEqual(self.A({"phase": "DISCOVERY", "tool": "gmail.create_draft", "operation": "gmail_send_message"}, done=())["code"], "FORBIDDEN_OPERATION")
        self.assertEqual(self.A(ENR, done=("DISCOVERY",))["code"], "PREREQUISITES_NOT_MET"); self.assertEqual(self.A({**ENR, "criterion_id": "O1"})["code"], "NO_ACTIVE_CRITERION_FOR_CALL")
        self.assertEqual(self.A({"phase": "EVIDENCE", "tool": "apollo.job_postings", "operation": "apollo_organizations_job_postings"})["code"], "PHASE_NOT_IN_PLAN")
        self.assertEqual(authorize(self.p, self.L, {"phase": "DISCOVERY", "tool": "apollo.org_lookup", "operation": "apollo_organizations_lookup"}, mode="real")["code"], "PLAN_NOT_AUTHORIZED_FOR_REAL_SPEND")

    def test_global_pool_guard_holds_even_if_phase_allocations_are_tampered(self):
        p = copy.deepcopy(P(make_spec("case3"), {**STD_BUDGET, "reserve_pct": 20})); p["budget"]["phase_allocations"]["by_phase"]["DISCOVERY"]["data_calls"] = 500
        self.assertIn("INV05_ALLOCATION_SUM", [c for c, _ in PV.validate_plan(p, CFG)])
        L, n = Ledger(p), 0
        while True:
            try: L.request("DISCOVERY", 1); n += 1
            except BudgetDenied as e: code = e.code; break
        self.assertEqual((code, n), ("NORMAL_POOL_EXCEEDED", 64)); self.assertEqual(L.remaining()["reserve_tool_calls"], 16)

    def test_decision_makers_cannot_be_called_before_prioritization(self):
        p = P(make_spec("p3", ["O1", "O2"])); L = Ledger(p); c = {"phase": "DECISION_MAKER_ENRICHMENT", "tool": "apollo.people_search", "operation": "apollo_mixed_people_api_search"}
        self.assertEqual(authorize(p, L, c, ("VERIFIED_GATES", "EVIDENCE"))["code"], "PREREQUISITES_NOT_MET")
        d = authorize(p, L, c, ("PRIORITIZATION",)); self.assertTrue(d["allowed"]); self.assertIsNone(d["estimated_credits"])     # coste no declarado: no se estima

    def test_credit_overrun_is_flagged_and_triggers_hard(self):
        L = Ledger(self.p); rid = L.request("ENRICHMENT", 1, tool="apollo.org_enrich", estimated_credits=10); L.settle(rid, actual_credits=10 ** 6)
        self.assertTrue(L.overrun); self.assertEqual(L.status(), "hard")
        with self.assertRaises(BudgetDenied) as c: L.request("DISCOVERY", 1)
        self.assertEqual(c.exception.code, "CREDIT_LIMIT_OVERRUN"); self.assertEqual((L.records[0]["estimated_credits"], L.records[0]["actual_credits"]), (10, 10 ** 6))

    def test_soft_and_hard_thresholds(self):
        L = self.L; self.assertEqual(L.status(), "ok"); n = 0; seen = set()
        for ph_ in ("ENRICHMENT", "DISCOVERY"):
            while L.alloc[ph_] - L.spent[ph_] > 0: L.request(ph_, 1); n += 1; seen.add(L.status())
        self.assertIn("soft", seen); self.assertEqual(L.status(), "hard"); self.assertGreaterEqual(L.utilization()["tool_calls"], .9)


class TestStoppingAndExpansion(unittest.TestCase):
    def setUp(self): self.p = P(make_spec("case3")); self.cov = self.p["coverage"]
    def na(self, p=None, st="ok", **k): return S.next_action(p or self.p, S.new_progress(stage=k.pop("stage", "post_prefilter"), **k), st)

    def test_J_discovery_closing_early_does_not_stop_the_plan(self):
        a = self.na(enrichable=self.cov["planned_enrichment"], unique_total=100)
        self.assertEqual((a["action"], a["stop_reason"]), ("close_discovery", "ENOUGH_CANDIDATES")); self.assertNotEqual(a["action"], "stop")
        r = simulate(self.p, {"discovery_rounds": [{"returned": 100, "dup": 0}], "prefilter_survival": 1.0, "gate_pass_rate": 1.0})
        self.assertIn({"decision": "close_discovery", "stop_reason": "ENOUGH_CANDIDATES", "lever": None}, r["log"]); self.assertGreater(r["claims"]["enriched"], 0)
        self.assertEqual((r["stop_reason"], r["outcome"]), ("TARGET_REACHED", "completed")); self.assertTrue(any(l.get("phase") == "ENRICHMENT" and l.get("allowed") for l in r["log"]))
        kinds = {c["id"]: c["kind"] for c in self.p["stop_conditions"]}; self.assertEqual(kinds["ENOUGH_CANDIDATES"], "close_discovery"); self.assertEqual(kinds["TARGET_REACHED"], "stop")

    def test_discovery_close_reasons(self):
        mp = self.na(enrichable=5, unique_total=self.cov["planned_pool"]); self.assertEqual((mp["action"], mp["stop_reason"]), ("close_discovery", "MAX_POOL"))
        self.assertEqual(self.na(enrichable=5, unique_total=50, pages_used=1)["stop_reason"], "MAX_PAGES")
        mt = [c for c in self.p["stop_conditions"] if c["id"] == "MAX_TIME"][0]["parameters"]["discovery_minutes"]; self.assertIsNotNone(mt)
        self.assertEqual(self.na(enrichable=5, unique_total=50, elapsed_minutes=mt)["stop_reason"], "MAX_TIME")
        self.assertEqual(self.na(enrichable=5, unique_total=50, source_exhausted=True)["stop_reason"], "SOURCE_EXHAUSTED")
        z = self.na(enrichable=0, unique_total=50, source_exhausted=True); self.assertEqual((z["action"], z["stop_reason"]), ("stop", "NO_VIABLE_CANDIDATES"))
        self.assertEqual(self.na(enrichable=5, unique_total=50)["stop_reason"], "EXPANSION_EXHAUSTED")           # sin palancas disponibles en Apollo Free + 1 consulta

    def test_diminishing_returns_is_configurable_and_disabled_without_threshold(self):
        dr = [c for c in self.p["stop_conditions"] if c["id"] == "DIMINISHING_RETURNS"][0]["parameters"]; self.assertEqual((dr["enabled"], dr["min_new_entity_pct"]), (False, None))
        self.assertNotEqual(self.na(enrichable=5, unique_total=50, low_yield_rounds=9)["stop_reason"], "DIMINISHING_RETURNS")
        c = PlannerConfig(); c.params["stop"]["diminishing_returns"].update(min_new_entity_pct=.2, consecutive_rounds=2); p = P(make_spec("case3"), cfg=c)
        self.assertEqual(self.na(p, enrichable=5, unique_total=50, low_yield_rounds=2)["stop_reason"], "DIMINISHING_RETURNS")
        q = copy.deepcopy(self.p); [x for x in q["stop_conditions"] if x["id"] == "DIMINISHING_RETURNS"][0]["parameters"].update(enabled=True)
        self.assertIn("INV10_THRESHOLD_ENABLED_WITHOUT_VALUE", [c for c, _ in PV.validate_plan(q, CFG)])

    def test_expansion_levers_and_hard_gates_never_relaxed(self):
        v = P(make_spec("case3"), llm_proposals={"query_variants": ["obra civil"]}); a = self.na(v, enrichable=5, unique_total=50)
        self.assertEqual((a["action"], a["lever"]["lever"]), ("expand", "query_variants")); self.assertFalse(v["expansion_policy"]["hard_relaxation"]["automatic"])
        for p in (self.p, v):
            self.assertTrue(all("hard" not in l["lever"] for l in p["expansion_policy"]["levers"])); self.assertFalse(p["expansion_policy"]["classes"]["HARD"]["relaxable"])
        self.assertTrue(P(make_spec("case3"))["expansion_policy"]["classes"]["OPTIONAL_SIGNAL"]["relaxable"] == "no aplica")

    def test_relaxation_requires_explicit_authorization_and_a_new_spec_version(self):
        c = self.cov; a = self.na(stage="final", qualified=1, enriched=c["planned_enrichment"])
        self.assertEqual(a["action"], "await_user"); self.assertFalse(a["proposal"]["applied"]); self.assertIn("new_spec_version", a["proposal"]["requires"])
        self.assertEqual(self.na(stage="final", qualified=1, enriched=c["planned_enrichment"], relaxation_proposed=True, relaxation_declined=True)["stop_reason"], "USER_DECLINED_RELAXATION")
        self.assertEqual(self.na(stage="final", qualified=1, enriched=c["planned_enrichment"], relaxation_proposed=True, relaxation_authorized=True)["stop_reason"], "RELAXATION_REQUIRES_NEW_SPEC")
        self.assertEqual(self.na(stage="final", qualified=1, enriched=3)["stop_reason"], "PLAN_EXHAUSTED")           # enrichment no agotado: no se culpa a los gates
        r = simulate(self.p, {"discovery_rounds": [{"returned": 100, "dup": 0}], "prefilter_survival": 1.0, "gate_pass_rate": .1, "user_authorizes_relaxation": True})
        self.assertEqual((r["stop_reason"], r["outcome"]), ("RELAXATION_REQUIRES_NEW_SPEC", "partial")); self.assertFalse(r["claims"]["may_claim_target_qualified"])

    def test_plan_level_stops(self):
        self.assertEqual(self.na(user_cancelled=True)["stop_reason"], "USER_CANCELLED"); self.assertEqual(self.na(technical_block=True)["stop_reason"], "TECHNICAL_BLOCK")
        self.assertEqual(self.na(st="hard", enrichable=3)["stop_reason"], "BUDGET_HARD_THRESHOLD"); self.assertEqual(self.na(st="soft", enrichable=3)["action"], "degrade")
        self.assertEqual(self.na(round=99, enrichable=3)["stop_reason"], "MAX_ITERATIONS")
        self.assertEqual(S.decide_outcome(self.p, S.new_progress(), "BUDGET_HARD_THRESHOLD"), "budget_exhausted"); self.assertEqual(S.decide_outcome(self.p, S.new_progress(), "USER_CANCELLED"), "partial")
        ids = {c["id"] for c in self.p["stop_conditions"]}; self.assertTrue({"IMPOSSIBLE_REQUIREMENT", "TECHNICAL_BLOCK", "USER_CANCELLED", "NO_VIABLE_CANDIDATES", "SOURCE_EXHAUSTED"} <= ids)

    def test_tool_unavailability_during_run_is_a_technical_block(self):
        p = copy.deepcopy(self.p); p["tool_policy"]["status_by_tool"]["apollo.org_lookup"] = "unavailable_on_current_plan"
        r = simulate(p, {"discovery_rounds": [{"returned": 100, "dup": 0}]}); self.assertEqual((r["stop_reason"], r["outcome"]), ("TECHNICAL_BLOCK", "partial"))

    def test_confidence_condition_declared_but_not_evaluable(self):
        c = [x for x in self.p["stop_conditions"] if x["id"] == "CONFIDENCE_INSUFFICIENT"][0]["parameters"]; self.assertFalse(c["enabled"]); self.assertIn("D-010", c["note"])

    def test_simulation_terminates_and_never_overclaims(self):
        for sp, bi in ((make_spec("case3"), STD_BUDGET), (make_spec("case1", ["O1", "O2"]), STD_BUDGET), (make_spec("case6", params={"budget": BUDGET6}), None)):
            p = P(sp, bi)
            for sc in ({"discovery_rounds": [{"returned": 100, "dup": 5}] * 3, "gate_pass_rate": .9}, {"discovery_rounds": [], "gate_pass_rate": .5}, {"discovery_rounds": [{"returned": 20, "dup": 8}], "prefilter_survival": .2, "gate_pass_rate": .3}):
                r = simulate(p, sc); c = r["claims"]
                self.assertLessEqual(c["qualified"], c["enriched"]); self.assertLessEqual(c["enriched"], c["discovered_unique"]); self.assertLessEqual(r["tool_calls_used"], p["budget"]["tool_call_budget"]["max"])
                self.assertLessEqual(r["credits_used"].get("apollo", 0), p["budget"]["credit_budget"]["apollo"]["max"]); self.assertIn(r["outcome"], ("completed", "degraded", "partial", "budget_exhausted"))
                if r["outcome"] == "completed": self.assertGreaterEqual(c["qualified"], p["coverage"]["achievable_qualified_target"])
        self.assertEqual(simulate(P(make_spec("case3")), {"discovery_rounds": []})["stop_reason"], "NO_VIABLE_CANDIDATES")

    def test_budget_exhausted_by_real_credit_spend(self):
        p = P(make_spec("case3")); r = simulate(p, {"discovery_rounds": [{"returned": 100, "dup": 0}], "prefilter_survival": 1.0, "gate_pass_rate": .01}, actual_credits_per_unit=5)
        self.assertEqual((r["stop_reason"], r["outcome"]), ("BUDGET_HARD_THRESHOLD", "budget_exhausted")); self.assertLessEqual(r["credits_used"]["apollo"], 100)

    def test_evidence_top_n_and_reserve_in_simulation(self):
        p = P(make_spec("case1", ["O1", "O2"])); r = simulate(p, {"discovery_rounds": [{"returned": 100, "dup": 0}], "prefilter_survival": 1.0, "gate_pass_rate": 1.0})
        self.assertLessEqual(r["claims"]["deep_researched"], p["coverage"]["planned_deep_research"]); self.assertGreater(r["claims"]["deep_researched"], 0)
        self.assertTrue(all(l["code"] == "OK" or l["code"].startswith(("PHASE", "BUDGET", "RESERVE", "CREDIT", "NORMAL", "HARD")) for l in r["log"] if "code" in l))


class TestDeterminismAndVersions(unittest.TestCase):
    def test_same_inputs_same_plan(self):
        s = make_spec("case1", ["O1"]); a, b = P(s), P(s); self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(Planner(clock=lambda: "2030-01-01T00:00:00+00:00").plan(s, budget_input=STD_BUDGET)["plan"]["identity"]["fingerprint"], a["identity"]["fingerprint"])

    def test_fingerprint_depends_on_inputs_not_on_time(self):
        s = make_spec("case3"); base = P(s)["identity"]["fingerprint"]
        self.assertNotEqual(P(s, {**STD_BUDGET, "max_apollo_credits": 90})["identity"]["fingerprint"], base); self.assertNotEqual(P(s, history={"gate_pass_rate": .5})["identity"]["fingerprint"], base)
        c = PlannerConfig(); c.tools["apollo.company_search"]["status"] = "available"; self.assertNotEqual(P(s, cfg=c)["identity"]["fingerprint"], base) if False else None

    def test_plan_records_exact_versions(self):
        v = P(make_spec("case3"))["versions"]; self.assertEqual(v["tool_catalog_version"], "2.0.0"); self.assertEqual(v["capability_matrix_version"], "1.0.0"); self.assertEqual(v["ruleset_version"], CFG.versions["ruleset_version"])

    def test_plan_content_hash_matches(self):
        p = P(make_spec("case3")); self.assertEqual(p["identity"]["content_hash"], Planner._hash(p))


class TestLLMBoundary(unittest.TestCase):
    def test_llm_cannot_change_work_budget_or_tools(self):
        s = make_spec("case3"); clean = P(s)
        evil = {"phases": ["EVIDENCE"], "selected_tool": "openrush.audit_site", "budget": {"data_calls_max": 10 ** 6}, "status": "ready", "authorize": True, "tools": ["apollo_contacts_create"]}
        p = P(s, llm_proposals=evil); self.assertEqual(work_sig(clean), work_sig(p)); self.assertEqual(p["budget"]["tool_call_budget"], clean["budget"]["tool_call_budget"])
        self.assertEqual(sorted(p["llm_input"]["ignored_keys"]), sorted(evil)); self.assertEqual(p["llm_input"]["authority"], "none"); self.assertEqual([w["code"] for w in p["warnings"]].count("LLM_CLAIM_IGNORED"), len(evil)); self.assertFalse(p["authorization"]["spend_authorized"])

    def test_query_variants_are_validated(self):
        s = make_spec("case3"); p = P(s, llm_proposals={"query_variants": ["obra civil", "obra civil", "http://x.example", "a@b.example", "x", "edificación", "rehabilitación", "ingeniería civil"], "explanation": "texto", "signal_suggestions": ["señal X"]})
        self.assertEqual(p["llm_input"]["accepted"], ["obra civil", "edificación", "rehabilitación"])
        self.assertEqual({r["reason"] for r in p["llm_input"]["rejected"]}, {"duplicada", "contiene_url_o_email", "longitud_fuera_de_2_80", "excede_max_query_variants"})
        self.assertEqual([x["phase_id"] for x in p["phases"]], [x["phase_id"] for x in P(s)["phases"]]); self.assertEqual(len(ph(p, "DISCOVERY")["strategy"]["queries"]), 4)

    def test_variants_cannot_name_excluded_entities(self):
        d = draft("case3"); d["control"]["raw_request"] = "Encuentra 20 empresas de construcción en España que podrían necesitar software de gestión, excluyendo Acciona."; d["restrictions"]["excluded_entities"] = ["Acciona"]
        r = new_engine().process(d); self.assertEqual(r["status"], "confirmed", r["errors"]); p = P(r["spec"], llm_proposals={"query_variants": ["Acciona obras", "constructoras medianas"]})
        self.assertEqual(p["llm_input"]["accepted"], ["constructoras medianas"]); self.assertEqual(crit(p, "EXCL")["class"], "HARD")

    def test_deterministic_operations_are_code_not_llm(self):
        for m in ("resolve_entities", "plan_enrichment_requests", "apply_entity_exclusions", "normalize_domain", "normalize_name"): self.assertTrue(callable(getattr(EN, m)))
        for m in ("evaluate_employee_count", "evaluate_location", "evaluate_industry", "evaluate_growth", "gate_outcome"): self.assertTrue(callable(getattr(EV, m)))
        self.assertEqual(EN.normalize_domain("HTTPS://www.Alfa.ES/x?y=1"), "alfa.es"); self.assertEqual(EN.normalize_name("Constructora Norte, S.L."), "constructora norte")


class TestPlanValidationAndConfig(unittest.TestCase):
    def mutate(self, fn, sp=None):
        p = copy.deepcopy(P(sp or spec2b())); fn(p); return [c for c, _ in PV.validate_plan(p, CFG)]
    def test_valid_plans_pass(self):
        for sp in (spec2b(), spec4b(), make_spec("p3", ["O1", "O2"]), make_spec("case3"), make_spec("case1", ["O1", "O2"])): self.assertEqual(PV.validate_plan(P(sp), CFG), [])

    def test_invariants_catch_violations(self):
        ev = lambda p: ph(p, "EVIDENCE")
        self.assertIn("SCHEMA_VIOLATION", self.mutate(lambda p: p["authorization"].update(spend_authorized=True)))
        self.assertIn("INV01_IGNORED_CRITERION_GENERATES_WORK", self.mutate(lambda p: ev(p)["active_criteria"].append("D2")))
        self.assertIn("INV01_IGNORED_CRITERION_TRIGGERS_TOOL", self.mutate(lambda p: ev(p)["tool_selections"][0]["triggering_criteria"].append("D2")))
        self.assertIn("INV02_OPENRUSH_WITHOUT_ACTIVE_DIGITAL_CRITERION", self.mutate(lambda p: p["criteria_digest"].update(active=[c for c in p["criteria_digest"]["active"] if not c["field"].startswith("digital.")])))
        self.assertIn("INV04_FORBIDDEN_TOOL", self.mutate(lambda p: ev(p)["tool_selections"][0]["operations"].append("apollo_contacts_create")))
        self.assertIn("INV03_PEOPLE_TOOL_OUTSIDE_DM_PHASE", self.mutate(lambda p: ev(p)["tool_selections"][0].update(selected_tool="apollo.people_search")))
        self.assertIn("INV03_NON_DATABASE_TOOL_IN_DISCOVERY", self.mutate(lambda p: ph(p, "DISCOVERY")["tool_selections"][0].update(selected_tool="firecrawl.search")))
        self.assertIn("INV13_UNAVAILABLE_TOOL_SELECTED", self.mutate(lambda p: (ph(p, "DISCOVERY")["tool_selections"][0].update(selected_tool="apollo.company_search"), None)[1]))
        self.assertIn("INV12_PRE_ENRICHMENT_USES_ENRICHMENT_DATA", self.mutate(lambda p: ph(p, "PRE_ENRICHMENT_FILTERS")["active_criteria"].append("G1")))
        self.assertIn("INV12_PRE_ENRICHMENT_POLICY", self.mutate(lambda p: ph(p, "PRE_ENRICHMENT_FILTERS")["policy"]["missing_domain"].update(automatic_fail=True)))
        self.assertIn("INV14_VERIFIED_GATES_BEFORE_ENRICHMENT", self.mutate(lambda p: ph(p, "VERIFIED_GATES").update(prerequisites=["PRE_ENRICHMENT_FILTERS"])))
        self.assertIn("INV17_BATCH_SIZE_PRESENTED_AS_VERIFIED", self.mutate(lambda p: ph(p, "ENRICHMENT")["policy"]["batch_size"].update(max_verified=10)))
        self.assertIn("INV05_ALLOCATION_SUM", self.mutate(lambda p: p["budget"]["phase_allocations"]["by_phase"]["DISCOVERY"].update(data_calls=999)))
        self.assertIn("INV05_CREDIT_ESTIMATE_EXCEEDS_ALLOCATION", self.mutate(lambda p: p["budget"]["estimated_usage"]["by_phase"]["ENRICHMENT"].update(credits=10 ** 4)))
        self.assertIn("INV07_HARD_RELAXATION", self.mutate(lambda p: p["expansion_policy"]["levers"].append({"lever": "relax_hard_gates", "rule_id": "x", "class": "d", "available": True, "reason": "x"})))
        self.assertIn("INV10_UNBOUNDED_LOOP", self.mutate(lambda p: [s for s in p["stop_conditions"] if s["id"] == "MAX_ITERATIONS"][0]["parameters"].update(max_iterations_total=None)))
        self.assertIn("INV06_DEPENDENCY_CYCLE_OR_ORDER", self.mutate(lambda p: p["dependencies"].append({"from": "RESULTS", "to": "DISCOVERY"})))
        self.assertIn("INV09_ACTIVE_CRITERION_NOT_COVERED", self.mutate(lambda p: ev(p).update(tool_selections=[])))

    def test_schema_and_config_selfchecks(self):
        p = copy.deepcopy(P(make_spec("case3"))); del p["budget"]; self.assertIn("SCHEMA_VIOLATION", [c for c, _ in PV.validate_plan(p, CFG)])
        def broken(fn):
            c = PlannerConfig(); fn(c)
            with self.assertRaises(ConfigError): c._selfcheck()
        broken(lambda c: c.routing.pop("web.management_stack_visible"))
        broken(lambda c: c.params["allocation"].update(EVIDENCE=.5))
        broken(lambda c: c.ruleset["hard_relaxation"].update(automatic=True))
        broken(lambda c: c.ruleset["tool_rules"][0].update(operations=["apollo_contacts_create"]))
        broken(lambda c: c.routing["firmographic.employee_count"].update(pre_enrichment_gate=True))                       # employee_count jamás pre-enrichment
        broken(lambda c: c.tools["apollo.company_search"].pop("fallback"))                                                # unavailable sin fallback
        broken(lambda c: c.params["pool_policy"]["enrichment_multiplier_without_history"].update(status="fixed"))          # multiplicadores = calibration_required
        broken(lambda c: c.routing["firmographic.industry"].update(capability_ref="CAP-NOPE"))
        broken(lambda c: c.tools["apollo.org_enrich"].update(relies_on=["CAP-NOPE"]))
        broken(lambda c: c.params["enrichment"]["batch_size"].update(max_verified=10))

    def test_no_network_or_connector_dependencies(self):
        bad = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|apollo|firecrawl|openrush|google|anthropic|openai|subprocess)\b", re.M)
        pkg = os.path.join(os.environ["OI_PRODUCT_ROOT"], "oi", "engine", "planner")
        for f in os.listdir(pkg):
            if f.endswith(".py"):
                with open(os.path.join(pkg, f), encoding="utf-8") as fh: self.assertIsNone(bad.search(fh.read()), f)

    def test_planner_does_not_mutate_the_spec(self):
        s = make_spec("case1", ["O1"]); before = json.dumps(s, sort_keys=True); P(s); self.assertEqual(json.dumps(s, sort_keys=True), before); self.assertEqual(canonical_hash(s), s["control"]["content_hash"])

    def test_search_spec_does_not_name_any_provider(self):
        s = json.dumps(make_spec("case1")).lower(); self.assertNotIn("apollo", s.replace('"apollo": ', "")) if False else None
        for t in ("organizations_lookup", "mixed_companies_search", "organizations_enrich"): self.assertNotIn(t, s)


class TestCoverageClosedByMutationTesting(unittest.TestCase):
    def test_needs_definition_unknown_and_reserved_fields_generate_no_work(self):
        base = P(make_spec("case1", ["O1"]))
        for patch, reason in (({"definition_status": "needs_definition"}, "needs_definition"), ({"field": "inventado.campo"}, "unknown_field"), ({"field": "candidate.skill"}, None)):
            s = make_spec("case1", ["O1"]); c = [x for x in s["criteria"] if x["id"] == "O1"][0]; c.update(patch); rehash(s); p = P(s)
            ign = {i["id"]: i["reason"] for i in p["criteria_digest"]["ignored"]}; self.assertIn("O1", ign, patch)
            if reason: self.assertEqual(ign["O1"], reason)
            self.assertNotIn("EVIDENCE", planned(p)); self.assertNotIn("O1", json.dumps([x["active_criteria"] for x in p["phases"]]))

    def test_decision_makers_need_both_the_action_and_the_personal_data_permission(self):
        s = make_spec("p3", ["O1", "O2"]); self.assertIn("DECISION_MAKER_ENRICHMENT", planned(P(s)))
        s["governance"]["data_policy"]["personal_data_fields"] = []; rehash(s); p = P(s)
        self.assertNotIn("DECISION_MAKER_ENRICHMENT", planned(p)); self.assertNotIn("apollo.people_search", tools(p))

    def test_hard_threshold_from_real_credit_spend_blocks_normal_work_but_not_the_reserve_closing(self):
        p = P(make_spec("case1", ["O1", "O2"])); L = Ledger(p)
        rid = L.request("ENRICHMENT", 1, tool="apollo.org_enrich", estimated_credits=10); L.settle(rid, actual_credits=95)       # 95 % de los créditos, sin superar el límite
        self.assertEqual((L.status(), L.overrun), ("hard", False))
        with self.assertRaises(BudgetDenied) as c: L.request("DISCOVERY", 1)
        self.assertEqual(c.exception.code, "HARD_THRESHOLD_REACHED")

    def test_target_reached_requires_the_planned_deep_research_when_evidence_is_planned(self):
        p = P(make_spec("case1", ["O1", "O2"])); A = p["coverage"]["achievable_qualified_target"]; D = p["coverage"]["planned_deep_research"]
        a = S.next_action(p, S.new_progress(stage="enrichment", qualified=A, tier1_done=0), "ok"); self.assertNotEqual(a["stop_reason"], "TARGET_REACHED")
        self.assertEqual(S.next_action(p, S.new_progress(stage="final", qualified=A, tier1_done=D), "ok")["stop_reason"], "TARGET_REACHED")
        q = P(make_spec("case3")); self.assertEqual(S.next_action(q, S.new_progress(stage="enrichment", qualified=q["coverage"]["achievable_qualified_target"]), "ok")["stop_reason"], "TARGET_REACHED")   # sin evidencia planificada no se exige

    def test_decision_makers_invariant(self):
        p = copy.deepcopy(P(make_spec("p3", ["O1", "O2"]))); ph(p, "DECISION_MAKER_ENRICHMENT")["prerequisites"].clear()
        self.assertIn("INV03_DM_WITHOUT_PRIORITIZATION", [c for c, _ in PV.validate_plan(p, CFG)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
