"""Contratos: opportunity_record.v1, opportunity_contact.v1, Search Spec v2 (enums), perfiles y mapeo sectorial."""
import copy, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests._common import *
from oi.contracts.validator import validate, UnsupportedKeyword

CS = lambda: copy.deepcopy(cs_records())
RP = lambda: copy.deepcopy(reseller_records())


class TestOpportunityRecord(unittest.TestCase):
    def test_all_reseller_records_valid(self):
        recs = RP(); self.assertEqual(len(recs), 30)
        for r in recs: self.assertEqual(validate(r, "opportunity_record.v1.schema.json"), [], r["record_id"])
    def test_all_construction_synthetic_valid(self):
        for r in CS(): self.assertEqual(validate(r, "opportunity_record.v1.schema.json"), [], r["record_id"])
    def test_missing_required_field_rejected(self):
        for k in ("company", "contact", "provenance", "recommended_action", "cost", "evidence"):
            r = CS()[0]; del r[k]; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"), k)
    def test_additional_property_rejected(self):
        r = CS()[0]; r["inventado"] = 1; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
    def test_bad_enums_rejected(self):
        r = CS()[0]; r["opportunity_type"] = "nope"; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
        r = CS()[0]; r["provenance"]["data_origin"] = "REAL"; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
    def test_verified_fit_requires_eligible(self):
        r = CS()[0]; r["qualification"]["fit_verification"] = "VERIFIED"; r["qualification"]["status"] = "PENDING_EVIDENCE"
        self.assertTrue(validate(r, "opportunity_record.v1.schema.json"), "un encaje VERIFIED con estado no ELIGIBLE debe rechazarse")
    def test_ranking_is_never_probability_or_calibrated(self):
        r = CS()[0]; r["ranking"]["calibrated"] = True; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
        r = CS()[0]; r["ranking"]["not_probability"] = False; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
    def test_present_signal_needs_evidence(self):
        r = CS()[0]; r["signals"][0]["evidence_ids"] = []; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))
    def test_contact_action_cannot_have_blockers(self):
        r = RP(); r = [x for x in r if x["recommended_action"]["action"] == "CONTACT"][0]
        r["recommended_action"]["blocking_reasons"] = ["x"]; self.assertTrue(validate(r, "opportunity_record.v1.schema.json"))


class TestContactContract(unittest.TestCase):
    def test_all_contacts_valid_standalone(self):
        for r in RP() + CS(): self.assertEqual(validate(r["contact"], "opportunity_contact.v1.schema.json"), [], r["record_id"])
    def test_required_contract_fields_present(self):
        c = CS()[0]["contact"]
        for k in ("company_ref", "opportunity_type", "offering_kind", "target_function", "channels", "people", "role_contacts", "recommended_contact", "alternatives", "evidence", "not_found", "enrichment_state", "cost", "last_verified"):
            self.assertIn(k, c)
    def test_evidence_strength_separate_from_contact_quality(self):
        rq = copy.deepcopy(cs_by("Gamma"))["contact"]["recommended_contact"]["contact_quality"]
        self.assertEqual(rq["evidence_strength"], "HIGH"); self.assertEqual(rq["target_fit"], "GENERIC"); self.assertEqual(rq["grade"], "C")   # evidencia alta != contacto útil
    def test_source_class_and_claim_status_enums_enforced(self):
        c = CS()[0]["contact"]; c["evidence"][0]["source_class"] = "MAGIC"; self.assertTrue(validate(c, "opportunity_contact.v1.schema.json"))
        c = CS()[0]["contact"]; c["evidence"][0]["claim_status"] = "SURE"; self.assertTrue(validate(c, "opportunity_contact.v1.schema.json"))
    def test_unverified_evidence_cannot_be_known_strong(self):
        c = copy.deepcopy(cs_by("Eta"))["contact"]; e = c["evidence"][0]; self.assertEqual(e["source_class"], "UNVERIFIED")
        e["claim_status"] = "KNOWN"; e["evidence_strength"] = "HIGH"; self.assertTrue(validate(c, "opportunity_contact.v1.schema.json"))
    def test_profiles_and_mapping_files_valid(self):
        self.assertEqual(validate(load("rules", "contact_profiles.v1.json"), "contact_profiles.v1.schema.json"), [])
        self.assertEqual(validate(load("rules", "sector_mapping", "sector_mapping.es.json"), "sector_mapping.v1.schema.json"), [])
    def test_both_required_profiles_exist(self):
        self.assertTrue({"SELL_PRODUCT_TO_RESELLER", "SELL_SOFTWARE_OR_SERVICE"} <= set(load("rules", "contact_profiles.v1.json")["profiles"]))


class TestSearchSpecEnums(unittest.TestCase):
    def test_enum_parity_between_common_and_spec_v2(self):
        common = load("oi", "contracts", "common.v1.schema.json")["$defs"]; v2 = load("oi", "contracts", "search_spec.v2.schema.json")["properties"]["intent"]["properties"]
        off = v2["offering"]["properties"]
        self.assertEqual(set(off["kind"]["enum"]) - {None}, set(common["offering_kind"]["enum"]))
        self.assertEqual(set(off["target_function"]["enum"]), set(common["target_function"]["enum"]))
        self.assertEqual(set(v2["business_model"]["items"]["enum"]), set(common["business_model"]["enum"]))
        self.assertEqual(set(v2["entity_level"]["enum"]), set(common["entity_level"]["enum"]))
    def test_required_enum_members(self):
        c = load("oi", "contracts", "common.v1.schema.json")["$defs"]
        self.assertEqual(set(c["offering_kind"]["enum"]), {"PRODUCT_FOR_RESALE", "SOFTWARE", "SERVICE", "CONSULTING", "EQUIPMENT", "OTHER", "UNKNOWN"})
        self.assertEqual(set(c["entity_level"]["enum"]), {"ESTABLISHMENT", "OPERATOR", "GROUP", "COMPANY", "UNKNOWN"})
        self.assertEqual(set(c["business_model"]["enum"]), {"manufacturer", "distributor", "retailer", "service_provider", "contractor", "operator", "unknown"})


class TestValidatorItself(unittest.TestCase):
    def test_unsupported_keyword_fails_loudly(self):
        with self.assertRaises(UnsupportedKeyword): validate({}, {"type": "object", "dependentRequired": {"a": ["b"]}})
    def test_strict_json_equality(self):
        self.assertTrue(validate(1, {"const": True})); self.assertTrue(validate(True, {"enum": [1]}))
    def test_cross_check_with_jsonschema_library(self):
        try: import jsonschema
        except ImportError: self.skipTest("jsonschema no instalada")
        store = {}
        for n in os.listdir(os.path.join(ROOT, "oi", "contracts")):
            if n.endswith(".schema.json"): store[n] = load("oi", "contracts", n)
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
        reg = Registry().with_resources([(n, Resource.from_contents(s, default_specification=DRAFT202012)) for n, s in store.items()])
        for r in CS() + RP()[:10]:
            v = jsonschema.Draft202012Validator(store["opportunity_record.v1.schema.json"], registry=reg)
            self.assertEqual([e.message for e in v.iter_errors(r)], [], r["record_id"])
        bad = CS()[0]; del bad["contact"]
        self.assertTrue(list(jsonschema.Draft202012Validator(store["opportunity_record.v1.schema.json"], registry=reg).iter_errors(bad)))
        bad = CS()[0]; self.assertEqual(bool(validate(bad, "opportunity_record.v1.schema.json")), False)


if __name__ == "__main__": unittest.main()
