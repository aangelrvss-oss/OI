"""Seguridad: estos tests deben FALLAR el pipeline si se inventa un email, se promueve una inferencia, se recomienda un contacto no verificado,
se gasta sin autorización, falta el mapeo sectorial o falta offering.kind para elegir contacto."""
import copy, json, os, socket, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests._common import *
from oi.contracts.validator import validate
from oi.enrichment.contact import profiles as CP, selector as SEL
from oi.enrichment.contact.checks import check_contact, assert_safe, ContactSafetyError, validate_contact
from oi.engine.spec import sector_mapping as SM
from oi import cli

PROFILE = CP.load_profiles()
CS = lambda: copy.deepcopy(cs_records())
codes = lambda c: [v["code"] for v in check_contact(c, PROFILE)]


def base(offering_kind="SOFTWARE", evidence=(), channels=(), people=()):
    return {"contract_version": "opportunity_contact.v1", "company_ref": {"company_id": "t:1", "name": "T", "domain": "t.example"}, "opportunity_type": "customer_prospect", "offering_kind": offering_kind,
            "target_function": "UNKNOWN", "contact_profile_id": None, "enrichment_state": "LEVEL1_DONE", "levels_reached": [0, 1], "channels": list(channels), "people": list(people), "role_contacts": [],
            "recommended_contact": None, "alternatives": [], "not_recommended": [], "evidence": list(evidence), "contradictions": [], "not_found": [], "last_verified": None, "selection_blocked": None,
            "cost": {"paid_calls": 0, "credits": 0, "paid_enrichment_authorized": False, "authorization_ref": None}}


def ev(eid, field, value, sc="OFFICIAL", cs="KNOWN", strength="HIGH"):
    return {"evidence_id": eid, "field": field, "value": value, "source_class": sc, "source_url": None if sc == "UNVERIFIED" else "https://x.example/" + eid, "source_label": "t", "retrieved_at": "2026-10-08", "claim_status": cs, "evidence_strength": strength, "note": None}


def chan(cid, kind, value, evids, purpose="GENERAL"): return {"channel_id": cid, "kind": kind, "value": value, "scope": "COMPANY", "purpose": purpose, "evidence_ids": evids, "person_id": None, "outreach_access": "OPEN"}


class TestNoInventedData(unittest.TestCase):
    def test_email_without_supporting_evidence_is_not_recommended(self):
        c = SEL.build_contact(base(channels=[chan("c1", "EMAIL", "ceo@inventado.example", [])]), PROFILE, "2026-10-08")
        self.assertIsNone(c["recommended_contact"], "un email sin evidencia no puede ser contacto recomendado")
    def test_email_whose_evidence_has_a_different_value_is_not_recommended(self):
        c = SEL.build_contact(base(evidence=[ev("e1", "channel:email", "info@t.example")], channels=[chan("c1", "EMAIL", "ceo@t.example", ["e1"])]), PROFILE, "2026-10-08")
        self.assertIsNone(c["recommended_contact"])
    def test_forced_invented_channel_is_flagged_by_safety_check(self):
        c = SEL.build_contact(base(evidence=[ev("e1", "channel:email", "info@t.example")], channels=[chan("c1", "EMAIL", "info@t.example", ["e1"])]), PROFILE, "2026-10-08")
        self.assertIsNotNone(c["recommended_contact"]); self.assertEqual(codes(c), [])
        c["channels"][0]["value"] = "inventado@t.example"; self.assertIn("INVENTED_CHANNEL", codes(c))
        with self.assertRaises(ContactSafetyError): assert_safe(c, PROFILE)
    def test_every_channel_in_every_record_has_matching_evidence(self):
        for r in reseller_records() + cs_records():
            self.assertEqual([v for v in check_contact(r["contact"], PROFILE) if v["code"] in ("INVENTED_CHANNEL", "EVIDENCE_MISSING")], [], r["record_id"])
    def test_all_records_pass_full_safety_check(self):
        for r in reseller_records() + cs_records(): self.assertEqual(check_contact(r["contact"], PROFILE), [], r["record_id"])


class TestEvidenceStatusDiscipline(unittest.TestCase):
    def test_inferred_evidence_never_becomes_recommended(self):
        c = SEL.build_contact(base(evidence=[ev("e1", "channel:email", "info@t.example", cs="INFERRED", strength="LOW")], channels=[chan("c1", "EMAIL", "info@t.example", ["e1"])]), PROFILE, "2026-10-08")
        self.assertIsNone(c["recommended_contact"]); self.assertEqual(c["alternatives"], [])
    def test_forced_promotion_of_inferred_is_flagged(self):
        c = SEL.build_contact(base(evidence=[ev("e1", "channel:email", "info@t.example")], channels=[chan("c1", "EMAIL", "info@t.example", ["e1"])]), PROFILE, "2026-10-08")
        c["evidence"][0]["claim_status"] = "INFERRED"; self.assertIn("INFERRED_PROMOTED", codes(c))
    def test_unverified_contact_is_not_recommended_and_goes_to_not_recommended(self):
        r = cs_by("Eta")["contact"]; self.assertIsNone(r["recommended_contact"]); self.assertTrue(r["not_recommended"])
    def test_forced_unverified_recommendation_is_flagged(self):
        c = SEL.build_contact(base(evidence=[ev("e1", "channel:email", "info@t.example")], channels=[chan("c1", "EMAIL", "info@t.example", ["e1"])]), PROFILE, "2026-10-08")
        c["evidence"][0]["source_class"] = "UNVERIFIED"; self.assertIn("UNVERIFIED_RECOMMENDED", codes(c))
    def test_stale_person_is_never_primary(self):
        c = cs_by("Rho")["contact"]; self.assertNotEqual(c["recommended_contact"]["target"], "PERSON")
        self.assertTrue(any(a["target"] == "PERSON" for a in c["alternatives"]))   # la persona obsoleta queda como alternativa, nunca como principal
    def test_human_yes_is_not_verified_fit(self):
        for r in reseller_records():
            if (r["qualification"]["human_review"] or {}).get("decision") == "ACCEPT": self.assertNotEqual(r["qualification"]["fit_verification"], "VERIFIED", r["record_id"])
    def test_unknown_gate_is_never_promoted_to_pass(self):
        for r in reseller_records() + cs_records():
            for g in r["qualification"]["gates"]:
                if g["status"] == "PASS": self.assertTrue(r["evidence"] or r["company_identity"]["status"] == "KNOWN_MATCH", r["record_id"])
    def test_quality_must_be_derived_not_hand_set(self):
        c = copy.deepcopy(CS()[1]["contact"]); c["recommended_contact"]["contact_quality"]["grade"] = "A"; self.assertIn("QUALITY_NOT_DERIVED", codes(c))
    def test_strength_overstatement_is_flagged(self):
        c = copy.deepcopy(cs_by("Gamma")["contact"]); c["evidence"][0]["evidence_strength"] = "LOW"; self.assertIn("STRENGTH_OVERSTATED", codes(c))


class TestSpendAuthorization(unittest.TestCase):
    def test_spend_without_authorization_is_flagged(self):
        c = copy.deepcopy(CS()[0]["contact"]); c["cost"]["credits"] = 1; self.assertIn("SPEND_WITHOUT_AUTHORIZATION", codes(c))
        c = copy.deepcopy(CS()[0]["contact"]); c["cost"]["paid_calls"] = 1; self.assertIn("SPEND_WITHOUT_AUTHORIZATION", codes(c))
    def test_paid_state_without_authorization_is_flagged(self):
        c = copy.deepcopy(CS()[0]["contact"]); c["enrichment_state"] = "PAID_DONE"; self.assertIn("PAID_STATE_WITHOUT_AUTHORIZATION", codes(c))
    def test_authorized_flag_is_false_everywhere(self):
        self.assertFalse(cli.PAID_ENRICHMENT_AUTHORIZED)
        for r in reseller_records() + cs_records(): self.assertFalse(r["cost"]["paid_enrichment_authorized"]); self.assertFalse(r["contact"]["cost"]["paid_enrichment_authorized"])
        for n in ("reseller_pilot", "construction_software"): self.assertFalse(load("cases", n, "case.json")["paid_enrichment_authorized"])
    def test_zero_credits_across_all_records(self):
        for r in reseller_records() + cs_records():
            self.assertEqual((r["cost"]["credits"], r["cost"]["paid_calls"], r["contact"]["cost"]["credits"], r["contact"]["cost"]["paid_calls"]), (0, 0, 0, 0))
    def test_real_run_is_refused(self):
        S = cli.run("lo que sea", tempfile.mkdtemp(), real=True); self.assertEqual((S["exit_code"], S["status"], S["credits_spent"]), (3, "REAL_EXECUTION_REFUSED", 0))
    def test_cli_real_flag_exit_code(self):
        p = subprocess.run([sys.executable, "-m", "oi", "run", "x", "--real"], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(p.returncode, 3)
    def test_run_makes_no_network_calls(self):
        orig = socket.socket.connect
        def boom(*a, **k): raise AssertionError("intento de conexión de red en modo offline")
        socket.socket.connect = boom
        try:
            S = cli.run(CS_REQUEST, tempfile.mkdtemp(), offline=True)
        finally: socket.socket.connect = orig
        self.assertEqual(S["status"], "COMPLETED_OFFLINE"); self.assertEqual((S["credits_spent"], S["paid_calls"], S["network_calls"]), (0, 0, 0))
    def test_product_config_keeps_spend_off(self):
        c = load("config", "oi.config.json"); self.assertIs(c["paid_enrichment_authorized"], False); self.assertEqual(c["paid_enrichment_authorized"], cli.PAID_ENRICHMENT_AUTHORIZED)
        self.assertIn("NOT_AN_AUTHORIZATION", " ".join(c))
    def test_budget_cap_is_not_authorization(self):
        self.assertTrue("future_hard_cap_note" in load("cases", "construction_software", "case.json"))
        self.assertIn("NO es una autorización", load("cases", "construction_software", "case.json")["future_hard_cap_note"])


class TestMissingPrerequisites(unittest.TestCase):
    def test_missing_sector_mapping_blocks_the_run(self):
        S = cli.run("Hoteles en Portugal que necesiten software de gestión", tempfile.mkdtemp())
        self.assertNotEqual(S["exit_code"], 0); self.assertNotIn(S["status"], ("COMPLETED_OFFLINE",))
    def test_unmapped_sector_in_a_complete_draft_is_mapping_required_not_converted(self):
        from oi.engine.spec.mock_interpreter import interpret
        d = interpret(CS_REQUEST); g = [c for c in d["criteria"] if c["id"] == "G1"][0]
        d["control"]["raw_request"] = CS_REQUEST.replace("constructoras", "hoteleras"); g["value"] = g["source_text"] = "hoteleras"; d["intent"]["objective"] = d["control"]["raw_request"].rstrip(".")
        S = cli.run(d["control"]["raw_request"], tempfile.mkdtemp(), answers=load("tests", "fixtures", "answers_TEST_ONLY.json"), draft=d)
        self.assertEqual(S["stages"]["validate"]["status"], "confirmed", "el spec es válido: lo que falta es el mapeo")
        self.assertEqual((S["status"], S["exit_code"]), ("MAPPING_REQUIRED", 2)); self.assertEqual(S["stages"]["plan"]["status"], "NOT_PLANNED")
        self.assertEqual(S["sector_mapping"][0]["status"], "MAPPING_REQUIRED")
    def test_mapping_required_error_is_explicit(self):
        with self.assertRaises(SM.MappingRequired): SM.require("hoteles")
    def test_unmapped_sector_run_never_reaches_plan(self):
        S = cli.run("Hoteles en España que necesiten software de gestión", tempfile.mkdtemp()); self.assertNotEqual(S["stages"]["plan"]["status"], "planned")
    def test_missing_offering_kind_blocks_profile_selection(self):
        with self.assertRaises(CP.ProfileError) as cm: CP.select_profile(PROFILE, "customer_prospect", None)
        self.assertEqual(cm.exception.code, "OFFERING_KIND_REQUIRED")
        with self.assertRaises(CP.ProfileError): CP.select_profile(PROFILE, "customer_prospect", "UNKNOWN")
    def test_no_recommendation_without_offering_kind(self):
        c = SEL.build_contact(base(offering_kind="UNKNOWN", evidence=[ev("e1", "channel:email", "info@t.example")], channels=[chan("c1", "EMAIL", "info@t.example", ["e1"])]), PROFILE, "2026-10-08")
        self.assertIsNone(c["recommended_contact"]); self.assertEqual(c["selection_blocked"]["code"], "OFFERING_KIND_REQUIRED")
        c2 = copy.deepcopy(CS()[1]["contact"]); c2["offering_kind"] = "UNKNOWN"; self.assertIn("OFFERING_KIND_REQUIRED", codes(c2))
    def test_unknown_combination_has_no_profile(self):
        with self.assertRaises(CP.ProfileError) as cm: CP.select_profile(PROFILE, "talent", "SOFTWARE")
        self.assertEqual(cm.exception.code, "NO_PROFILE_FOR_COMBINATION")
    def test_unresolved_growth_threshold_stops_before_execution(self):
        S = cli.run(CS_REQUEST, tempfile.mkdtemp()); self.assertEqual((S["exit_code"], S["status"]), (2, "NEEDS_CLARIFICATION")); self.assertEqual(S["stages"]["execute"]["status"], "NOT_EXECUTED")
    def test_portal_is_direct_only_if_open_access(self):
        for r in reseller_records():
            ch = {c["channel_id"]: c for c in r["contact"]["channels"]}
            for x in ([r["contact"]["recommended_contact"]] if r["contact"]["recommended_contact"] else []) + r["contact"]["alternatives"]:
                v = ch.get(x["via_channel_id"])
                if v and v["kind"] == "PORTAL" and v.get("outreach_access") != "OPEN": self.assertNotEqual(x["contact_quality"]["reachability"], "DIRECT", r["record_id"])


class TestSyntheticLabelling(unittest.TestCase):
    def test_synthetic_records_are_labelled_everywhere(self):
        for r in cs_records():
            self.assertEqual(r["provenance"]["data_origin"], "SYNTHETIC_FIXTURE"); self.assertIn("SYNTH", r["company"]["name"]); self.assertTrue(r["company"]["domain"] is None or r["company"]["domain"].endswith(".example"))
    def test_synthetic_emails_use_reserved_domain(self):
        for r in cs_records():
            for c in r["contact"]["channels"]:
                if c["kind"] == "EMAIL": self.assertTrue(c["value"].endswith(".example"))
    def test_synthetic_page_shows_banner_and_real_demo_cannot_hide_it(self):
        S = cli.run(CS_REQUEST, tempfile.mkdtemp(), offline=True)
        with open(os.path.join(S["out_dir"], "12_report.html"), encoding="utf-8") as f: html = f.read()
        self.assertIn("SINTÉTICOS", html); self.assertIn("DATA_MODE = SYNTHETIC", html)


if __name__ == "__main__": unittest.main()
