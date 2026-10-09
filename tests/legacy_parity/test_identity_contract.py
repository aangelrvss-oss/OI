"""Tests unitarios del contrato EXECUTION_IDENTITY_V1 (claves, relay con espacio de nombres, shortlist fija). Offline: sin red, solo temporales."""
import copy, hashlib, json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from oi.engine.executor.errors import AwaitingExternalResponse
from oi.engine.executor.transports import RecordedTransport
from oi.engine.executor.identity_contract import CONTRACT
from oi.engine.executor.identity_contract.keys import operation_key, operation_material, legacy_operation_key, hc1, valid_execution_id, tranche_of, KEY_RE
from oi.engine.executor.identity_contract.relay import (ExecutionIdentityViolation, IdentityBoundRelayTransport, audit_relay, envelope_problems, init_relay, MANIFEST)
from oi.engine.executor.identity_contract.executor import fixed_shortlist_problems

BULK = "apollo_organizations_bulk_enrich"
DOMS = ["syn-a.example", "syn-b.example", "syn-c.example", "syn-d.example"]
CTX10 = {"phase": "ENRICHMENT", "seq": 10}
E1, E2 = "EXEC-T1-V1-O2-20261007-fd6bde3d", "EXEC-T2-UNIT-0001"
HASHED = {"tranche": "T2", "design": "B4", "operation": BULK, "entities": [{"order": 1, "apollo_id": "aaaaaaaaaaaaaaaaaaaa0001", "domain": "syn-a.example"}, {"order": 2, "apollo_id": "aaaaaaaaaaaaaaaaaaaa0002", "domain": "syn-b.example"},
          {"order": 3, "apollo_id": "aaaaaaaaaaaaaaaaaaaa0003", "domain": "syn-c.example"}, {"order": 4, "apollo_id": "aaaaaaaaaaaaaaaaaaaa0004", "domain": "syn-d.example"}],
          "call_scope_limit": 4, "expected_cost": 4, "max_cost": 4, "gate2_approval_record_hash": "a78a510f3e5f7006eeb080c4312bcb60c368bf4ab31dbf861d364277c6d1ca0e", "hash_convention": "HC1"}
NAMES = ["SYNTH Empresa A", "SYNTH Empresa B", "SYNTH Empresa C", "SYNTH Empresa D"]
def fs(): return {"entities": [{**e, "name": n} for e, n in zip(copy.deepcopy(HASHED["entities"]), NAMES)], "hashed_object": copy.deepcopy(HASHED), "shortlist_hash_hc1": hc1(HASHED)}
def tree(root): return {os.path.relpath(os.path.join(d, f), root): hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest() for d, _, fs_ in os.walk(root) for f in fs_}


class TestKeys(unittest.TestCase):
    def test_legacy_convention_is_byte_identical_to_the_historical_relay(self):
        self.assertEqual(legacy_operation_key(BULK, {"domains": DOMS}, CTX10), "46632128aa5462041d01a086")        # versión pública: dominios sintéticos; la igualdad con la clave histórica de T1 CALL 11 se verifica en la rama privada
        d = tempfile.mkdtemp()
        try:
            with self.assertRaises(AwaitingExternalResponse) as cm: RecordedTransport(d).invoke(BULK, {"domains": DOMS}, CTX10)
            self.assertEqual(cm.exception.request["input_hash"], legacy_operation_key(BULK, {"domains": DOMS}, CTX10))
        finally: shutil.rmtree(d)
    def test_identical_operations_differing_only_in_execution_id_have_different_keys(self):
        k1, k2 = operation_key(E1, BULK, {"domains": DOMS}, CTX10), operation_key(E2, BULK, {"domains": DOMS}, CTX10)
        self.assertNotEqual(k1, k2); self.assertNotIn("46632128aa5462041d01a086", (k1, k2))
    def test_key_is_deterministic_and_has_its_own_format(self):
        k = operation_key(E2, BULK, {"domains": DOMS}, CTX10)
        self.assertEqual(k, operation_key(E2, BULK, {"domains": list(DOMS)}, dict(CTX10))); self.assertRegex(k, KEY_RE); self.assertIsNone(KEY_RE.match("46632128aa5462041d01a086"))
    def test_execution_id_is_an_explicit_field_of_the_hashed_material(self):
        m = operation_material(E2, BULK, {"domains": DOMS}, CTX10); self.assertEqual((m["convention"], m["execution_id"]), (CONTRACT, E2)); self.assertNotIn("execution_id", m["context"])
    def test_an_execution_id_change_is_never_equivalent_to_a_sequence_change(self):
        k2 = operation_key(E2, BULK, {"domains": DOMS}, CTX10)
        self.assertNotIn(k2, {operation_key(E1, BULK, {"domains": DOMS}, {"phase": "ENRICHMENT", "seq": s}) for s in range(0, 500)})
    def test_execution_id_must_be_explicit_and_carry_its_tranche(self):
        for bad in (None, "", "T2", "EXEC-T2", "exec-t2-x", 7): self.assertFalse(valid_execution_id(bad))
        self.assertEqual((tranche_of(E1), tranche_of(E2)), ("T1", "T2"))
        with self.assertRaises(ValueError): operation_key(None, BULK, {"domains": DOMS}, CTX10)
    def test_hc1_matches_the_case_convention(self): self.assertEqual(hc1(HASHED), "4a927dede39fae17903030dd2e06f3a322db8bb9f2987169f0ddabc0b19e4db9")


class TestRelay(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(); self.root = init_relay(self.d + "/r", E2, "T2")
    def tearDown(self): shutil.rmtree(self.d)
    def test_new_relay_is_clean_and_bound_to_its_execution(self): self.assertEqual(audit_relay(self.root, E2, "T2"), []); self.assertNotEqual(audit_relay(self.root, "EXEC-T2-OTHER-0001", "T2"), [])
    def test_init_never_adopts_a_directory_with_content(self):
        with self.assertRaises(ExecutionIdentityViolation): init_relay(self.root, E2, "T2")
        with self.assertRaises(ExecutionIdentityViolation): init_relay(self.d + "/x", E1, "T2")
    def test_a_legacy_relay_is_foreign_and_auditing_it_writes_nothing(self):
        leg = self.d + "/legacy"; os.makedirs(leg + "/responses"); os.makedirs(leg + "/pending")
        json.dump({"input_hash": "46632128aa5462041d01a086", "response": {"organizations": []}}, open(leg + "/responses/46632128aa5462041d01a086.json", "w")); before = tree(leg)
        p = audit_relay(leg, E2, "T2"); self.assertIn("RELAY_NAMESPACE_MANIFEST_MISSING", p); self.assertTrue(any(x.startswith("RELAY_CONTAINS_FOREIGN_RECORD") for x in p))
        t = IdentityBoundRelayTransport(leg, E2, "T2")
        with self.assertRaises(ExecutionIdentityViolation): t.invoke(BULK, {"domains": DOMS}, CTX10)
        self.assertEqual(tree(leg), before); self.assertEqual(t.calls, [])
    def test_pending_carries_identity_and_record_replays_only_for_the_same_execution(self):
        t = IdentityBoundRelayTransport(self.root, E2, "T2")
        with self.assertRaises(AwaitingExternalResponse) as cm: t.invoke(BULK, {"domains": DOMS}, CTX10)
        req = cm.exception.request; self.assertEqual((req["execution_id"], req["operation_key"]), (E2, operation_key(E2, BULK, {"domains": DOMS}, CTX10)))
        t.record(req, response={"organizations": [{"id": "x"}]}); self.assertEqual(t.invoke(BULK, {"domains": DOMS}, CTX10), {"organizations": [{"id": "x"}]})
        other = IdentityBoundRelayTransport(self.root, "EXEC-T2-UNIT-0002", "T2")
        with self.assertRaises(ExecutionIdentityViolation): other.invoke(BULK, {"domains": DOMS}, CTX10)
        with self.assertRaises(ExecutionIdentityViolation): other.record(req, response={})
    def test_read_time_envelope_check_rejects_a_planted_response(self):
        t = IdentityBoundRelayTransport(self.root, E2, "T2"); k = operation_key(E2, BULK, {"domains": DOMS}, CTX10)
        planted = {"input_hash": "46632128aa5462041d01a086", "operation": BULK, "response": {"organizations": [{"id": "T1"}]}}      # respuesta histórica T1, renombrada a la clave T2
        json.dump(planted, open(f"{self.root}/responses/{k}.json", "w"))
        with self.assertRaises(ExecutionIdentityViolation) as cm: t.invoke(BULK, {"domains": DOMS}, CTX10)
        self.assertEqual(cm.exception.code, "RESPONSE_IDENTITY_MISMATCH"); self.assertEqual(os.listdir(self.root + "/pending"), [])
        forged = {"contract": CONTRACT, "operation_key": k, "execution_id": E1, "operation": BULK, "params": {"domains": DOMS}, "context": CTX10, "response": {}}
        self.assertTrue(envelope_problems(forged, k, E2)); self.assertTrue(envelope_problems({**forged, "execution_id": E2, "context": {"phase": "ENRICHMENT", "seq": 11}}, k, E2))
    def test_full_audit_flags_planted_records_and_unexpected_entries(self):
        k = operation_key(E2, BULK, {"domains": DOMS}, CTX10); json.dump({"response": {}}, open(f"{self.root}/responses/{k}.json", "w")); open(self.root + "/stray.txt", "w").close()
        p = audit_relay(self.root, E2, "T2"); self.assertTrue(any(x.startswith("RELAY_RECORD_IDENTITY_MISMATCH") for x in p)); self.assertTrue(any(x.startswith("RELAY_UNEXPECTED_ENTRIES") for x in p))
    def test_transport_construction_writes_nothing(self):
        d = self.d + "/nothing"; IdentityBoundRelayTransport(d, E2, "T2"); self.assertFalse(os.path.exists(d))


class TestFixedShortlist(unittest.TestCase):
    def test_valid_shortlist(self): p, e = fixed_shortlist_problems(fs()); self.assertEqual(p, []); self.assertEqual([x["domain"] for x in e], DOMS)
    def test_every_tampering_is_detected(self):
        cases = {}
        a = fs(); a["entities"][1]["apollo_id"] = "000000000000000000000000"; cases["candidate"] = a
        a = fs(); a["entities"][0], a["entities"][1] = a["entities"][1], a["entities"][0]; cases["order"] = a
        a = fs(); a["entities"][0], a["entities"][1] = a["entities"][1], a["entities"][0]; a["entities"][0]["order"], a["entities"][1]["order"] = 1, 2; cases["order_renumbered"] = a
        a = fs(); a["entities"][2]["domain"] = "syn-c-otro.example"; cases["domain"] = a
        a = fs(); a["shortlist_hash_hc1"] = "0" * 64; cases["hash"] = a
        a = fs(); a["hashed_object"]["entities"][3]["domain"] = "x.example"; a["entities"][3]["domain"] = "x.example"; cases["object_without_rehash"] = a
        a = fs(); a["entities"].append({"order": 5, "apollo_id": "aaaaaaaaaaaaaaaaaaaa0005", "name": "SYNTH Empresa E", "domain": "syn-e.example"}); cases["extra"] = a
        a = fs(); a["entities"].pop(); cases["missing"] = a
        for name, x in cases.items():
            with self.subTest(name=name): p, e = fixed_shortlist_problems(x); self.assertTrue(p); self.assertEqual(e, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
