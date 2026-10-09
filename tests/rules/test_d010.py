"""Tests de D010 consolidado (oi/rules/d010). Demuestran: paridad con el motor OFFICIAL, reglas como datos, ausencia de IDs de caso en la lógica,
validación de reglas y que cambiar una regla NO requiere duplicar el motor."""
import copy, hashlib, json, os, re, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0, ROOT); sys.dont_write_bytecode = True
from oi.rules.d010 import engine as E
from oi.paths import HISTORY

GOLD = json.load(open(os.path.join(ROOT, "tests", "fixtures", "d010_golden.json"), encoding="utf-8"))
H = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
OFFICIAL_DIR = os.path.join(HISTORY, "d010_v0_2_official")


class TestParity(unittest.TestCase):
    def test_golden_results_reseller_pilot(self):
        c = GOLD["cases"]["reseller_pilot"]; res = [E.evaluate_candidate(x, GOLD["evidence"]) for x in c["candidates"]]
        self.assertEqual([H(r) for r in res], c["result_sha256"]); self.assertEqual(H(E.build_ranking(res)), c["ranking_sha256"])

    def test_golden_results_synthetic_600(self):
        c = GOLD["cases"]["synthetic"]; res = [E.evaluate_candidate(x, GOLD["evidence"]) for x in c["candidates"]]
        self.assertEqual(len(res), 600); self.assertEqual([H(r) for r in res], c["result_sha256"]); self.assertEqual(H(E.build_ranking(res)), c["ranking_sha256"])

    @unittest.skipUnless(os.path.isdir(OFFICIAL_DIR), "motor oficial original no disponible en esta máquina")
    def test_live_parity_with_official_engine_and_rules(self):
        sys.path.insert(0, OFFICIAL_DIR)
        import d010_engine as O
        self.assertEqual(E.RULES, O.RULES, "las reglas efectivas difieren del motor oficial")
        with open(os.path.join(OFFICIAL_DIR, "D010_v0.2_RULES.json"), encoding="utf-8") as f: self.assertEqual(E.RULES, json.load(f))
        for x in GOLD["cases"]["synthetic"]["candidates"][:200]: self.assertEqual(E.evaluate_candidate(x, GOLD["evidence"]), O.evaluate_candidate(x, GOLD["evidence"]))


class TestRulesAsData(unittest.TestCase):
    def test_rules_json_is_valid_and_hashed(self):
        r = E.load_rules(); self.assertEqual(r, E.RULES); self.assertEqual(len(E.rules_hash(r)), 64)
        self.assertEqual(r["score_version"], "D010-v0.2"); self.assertEqual(r["status"], "OFFICIAL")

    def test_documentation_keys_do_not_change_the_effective_rules(self):
        raw = json.load(open(E.RULES_PATH, encoding="utf-8")); self.assertIn("_doc", raw); self.assertNotIn("_doc", E.RULES)
        self.assertEqual(E.rules_hash(raw), E.rules_hash(E.RULES))

    def test_engine_logic_contains_no_unit_ids(self):
        src = open(os.path.join(os.path.dirname(E.__file__), "engine.py"), encoding="utf-8").read()
        for uid in E.RULES["unit_order"]: self.assertIsNone(re.search(r"[\"']%s[\"']" % re.escape(uid), src), f"ID de unidad {uid} hardcodeado en la lógica")

    def test_changing_a_rule_changes_behaviour_without_a_second_engine(self):
        cand = {"candidate_id": "x", "identity_status": "KNOWN_MATCH", "gates": {"GEO": "PASS", "EXCL": "PASS"}, "signals": {u: "NOT_OBSERVED" for u in E.RULES["unit_order"]}}
        first_core = next(u for u in E.RULES["unit_order"] if E.RULES["units"][u]["class"] == "CORE"); cand["signals"][first_core] = "PRESENT"
        base = E.evaluate_candidate(cand, [])["score_interval"]["floor"]["total"]
        r2 = copy.deepcopy(E.RULES); r2["weights"]["CORE"] = 5
        self.assertEqual(E.evaluate_candidate(cand, [], r2)["score_interval"]["floor"]["total"], 5); self.assertEqual(base, 2)

    def test_invalid_rules_are_rejected(self):
        raw = json.load(open(E.RULES_PATH, encoding="utf-8"))
        def bad(fn):
            r = copy.deepcopy(raw); fn(r); p = os.path.join(os.environ.get("TMPDIR", "/tmp"), "bad_d010_rules.json"); json.dump(r, open(p, "w")); 
            with self.assertRaises(E.RulesError): E.load_rules(p)
        bad(lambda r: r["units"].pop(r["unit_order"][0]))                    # unit_order inconsistente
        bad(lambda r: r.update(calibrated=True))                              # nunca calibrado
        bad(lambda r: r.update(not_probability=False))
        bad(lambda r: r["weights"].pop("WEAK"))
        bad(lambda r: r["units"][r["unit_order"][0]].update({"class": "MEDIUM"}))
        bad(lambda r: r["coverage"].update(potential_units=99))

    def test_no_network_or_io_dependencies(self):
        src = open(os.path.join(os.path.dirname(E.__file__), "engine.py"), encoding="utf-8").read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+(requests|urllib|http|socket|subprocess|random|datetime|time)\b", src, re.M))


if __name__ == "__main__":
    unittest.main(verbosity=2)
