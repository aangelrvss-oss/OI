"""Search Spec v2: offering.kind / offering.target_function / business_model / entity_level; compatibilidad con v1; migración; intérprete local; mapeo sectorial."""
import copy, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests._common import *
from oi.engine.spec.migrate import migrate_v1_to_v2
from oi.engine.spec.versioning import canonical_hash
from oi.engine.spec.mock_interpreter import interpret
from oi.engine.spec import sector_mapping as SM
from oi.engine.planner.planner import Planner

D = lambda n: load("tests", "fixtures", "drafts_v2", n + ".json")
codes = lambda r: [q["trigger_rule"] for q in r["clarifications"]]


class TestV2Fields(unittest.TestCase):
    def test_construction_draft_carries_the_four_new_fields(self):
        s = engine().process(load("cases", "construction_software", "spec.draft.json"))["spec"]
        it = s["intent"]
        self.assertEqual(it["offering"]["kind"], "SOFTWARE"); self.assertEqual(it["offering"]["target_function"], "UNKNOWN")
        self.assertEqual(it["business_model"], ["contractor"]); self.assertEqual(it["entity_level"], "COMPANY"); self.assertEqual(s["versions"]["schema_version"], "2.0")
    def test_only_open_question_is_growth_definition(self):
        r = engine().process(load("cases", "construction_software", "spec.draft.json"))
        self.assertEqual(r["status"], "needs_clarification"); self.assertEqual(codes(r), ["CRITERION_INCOMPLETE"])
        c1 = [c for c in r["spec"]["criteria"] if c["id"] == "C1"][0]
        self.assertIsNone(c1["value"]); self.assertEqual(c1["definition_status"], "needs_definition"); self.assertEqual(c1["activation"], "proposed")
    def test_growth_threshold_is_never_invented_by_the_draft(self):
        d = load("cases", "construction_software", "spec.draft.json"); [c for c in d["criteria"] if c["id"] == "C1"][0]["value"] = 10
        self.assertIn("UNGROUNDED_VALUE", [e["code"] for e in engine().process(d)["errors"]], "un valor que no está en la petición no puede venir del borrador")
    def test_user_answer_confirms_and_reaches_confirmed(self):
        a = load("tests", "fixtures", "answers_TEST_ONLY.json")
        r = engine().process(load("cases", "construction_software", "spec.draft.json"), user_confirmations=a["confirmations"], user_params=a["user_params"])
        self.assertEqual(r["status"], "confirmed", r["errors"])
    def test_invalid_enum_rejected(self):
        for n in ("v2_invalid_kind", "v2_invalid_business_model"):
            r = engine().process(D(n)); self.assertEqual(r["status"], "error", n); self.assertIn("SCHEMA_VIOLATION", [e["code"] for e in r["errors"]])
    def test_missing_offering_kind_blocks(self):
        r = engine().process(D("v2_missing_kind")); self.assertEqual(r["status"], "needs_clarification")
        q = [q for q in r["clarifications"] if q["trigger_rule"] == "OFFERING_KIND_MISSING"]; self.assertTrue(q and q[0]["blocking"])
    def test_unknown_offering_kind_is_flagged_and_never_guessed(self):
        r = engine().process(D("v2_unknown_kind")); self.assertIn("OFFERING_KIND_UNKNOWN", codes(r)); self.assertEqual(r["spec"]["intent"]["offering"]["kind"], "UNKNOWN")


class TestV1Compatibility(unittest.TestCase):
    def test_all_v1_fixtures_still_process_with_default_schema(self):
        n = 0
        for f in sorted(os.listdir(os.path.join(ROOT, "tests", "fixtures", "drafts"))):
            r = engine("1.0").process(load("tests", "fixtures", "drafts", f)); n += 1
            self.assertIn(r["status"], ("confirmed", "needs_clarification", "unsupported_yet", "error"), f)
            if r["spec"]: self.assertEqual(r["spec"]["versions"]["schema_version"], "1.0", f)
        self.assertGreaterEqual(n, 10)
    def test_default_library_schema_is_v1_for_compatibility(self):
        from oi.engine.spec.engine import IntentEngine
        from oi.engine.spec.versioning import SpecStore
        e = IntentEngine(store=SpecStore(os.path.join(__import__("tempfile").mkdtemp(), "s.jsonl")), clock=CLOCK)
        self.assertEqual(e.reg.schema_version, "1.0")
    def test_v1_processing_never_silently_keeps_or_drops_v2_fields(self):
        d = load("cases", "construction_software", "spec.draft.json"); r = engine("1.0").process(d)
        self.assertIn("V2_FIELDS_IGNORED_IN_V1", [w["code"] for w in r["warnings"]])
        it = r["spec"]["intent"]; self.assertNotIn("kind", it["offering"]); self.assertNotIn("business_model", it)
    def test_migration_creates_new_document_and_leaves_v1_intact(self):
        e1 = engine("1.0"); s1 = e1.process(load("tests", "fixtures", "drafts", "case1.json"))["spec"]; h = canonical_hash(s1); before = json.dumps(s1, sort_keys=True)
        m = migrate_v1_to_v2(s1, clock=CLOCK)
        self.assertEqual(json.dumps(s1, sort_keys=True), before); self.assertEqual(canonical_hash(s1), h)
        s2 = m["spec"] if isinstance(m, dict) and "spec" in m else m
        self.assertEqual(s2["versions"]["schema_version"], "2.0"); self.assertNotEqual(canonical_hash(s2), h)
        self.assertIn(s2["intent"]["offering"]["kind"], ("UNKNOWN", None)); self.assertEqual(s2["intent"]["business_model"], ["unknown"]); self.assertEqual(s2["intent"]["entity_level"], "UNKNOWN")
    def test_migration_refuses_tampered_spec(self):
        s1 = copy.deepcopy(engine("1.0").process(load("tests", "fixtures", "drafts", "case1.json"))["spec"]); s1["control"]["raw_request"] += " x"
        from oi.engine.spec.migrate import MigrationError
        with self.assertRaises(MigrationError): migrate_v1_to_v2(s1, clock=CLOCK)
    def test_planner_validates_each_spec_with_its_own_schema(self):
        s1 = engine("1.0").process(load("tests", "fixtures", "drafts", "case1.json"))["spec"]
        P = Planner(clock=CLOCK); r = P.plan(s1, budget_input={"source": "user_parameter", "max_tool_calls": 30, "max_apollo_credits": 25})
        self.assertNotEqual(r["reason_code"], "SPEC_INVALID")
        r = P.plan({"control": {}, "versions": {"schema_version": "9.9"}}, budget_input=None); self.assertEqual(r["reason_code"], "SPEC_INVALID")


class TestLocalInterpreter(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(interpret(CS_REQUEST), interpret(CS_REQUEST))
    def test_extracts_only_what_text_says(self):
        d = interpret(CS_REQUEST)
        self.assertIsNone(d["restrictions"]["quantity"]["target"]); self.assertEqual(d["intent"]["offering"]["target_function"], "UNKNOWN")
        self.assertIsNone([c for c in d["criteria"] if c["id"] == "C1"][0]["value"])
        self.assertEqual([c["field"] for c in d["criteria"]], ["firmographic.industry", "growth.employee_growth_pct"])
    def test_does_not_invent_sector_for_unmapped_term(self):
        d = interpret("Hoteles en Portugal que necesiten consultoría"); self.assertEqual([c for c in d["criteria"] if c["field"] == "firmographic.industry"], [])
        self.assertEqual(d["intent"]["business_model"], ["unknown"])
    def test_no_offering_text_means_unknown_kind(self):
        self.assertEqual(interpret("Empresas constructoras españolas")["intent"]["offering"]["kind"], "UNKNOWN")
    def test_quantity_only_when_stated(self):
        self.assertEqual(interpret("Encuentra 20 empresas constructoras españolas que necesiten software")["restrictions"]["quantity"]["target"], 20)


class TestSectorMapping(unittest.TestCase):
    def test_mapped_term(self):
        r = SM.resolve("constructoras"); self.assertEqual(r["status"], "MAPPED"); self.assertEqual(r["canonical_sector"], "CONSTRUCTION")
    def test_unmapped_term_requires_mapping_and_does_not_convert(self):
        r = SM.resolve("hoteles"); self.assertEqual(r["status"], "MAPPING_REQUIRED"); self.assertNotIn("classification", r)
        with self.assertRaises(SM.MappingRequired): SM.require("hoteles")
    def test_missing_country_file_is_mapping_required(self):
        self.assertEqual(SM.resolve("constructoras", "FR")["status"], "MAPPING_REQUIRED")
    def test_unverified_provider_query_is_not_executable(self):
        r = SM.resolve("constructoras"); self.assertFalse(r["provider_query_executable"]); self.assertTrue(r["needs_human_review"])
    def test_unverified_cnae_codes_are_labelled(self):
        cn = {x["code"]: x["verification"] for x in SM.resolve("constructoras")["classification"]["cnae_2009"]}
        self.assertTrue(cn["41"].startswith("NOT_VERIFIED") and cn["42"].startswith("NOT_VERIFIED"))
    def test_naics_not_invented(self):
        self.assertIsNone(SM.resolve("constructoras")["classification"]["naics_2022"])


if __name__ == "__main__": unittest.main()
