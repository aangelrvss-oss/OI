"""Generalización: el MISMO motor y el MISMO renderer sirven al piloto de reventa y a Construcción+software sin cambiar código; el motor es único y no conoce los casos."""
import copy, json, os, re, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests._common import *
from oi.presentation import render as R
from oi import cli

PRES = lambda n: load("cases", n, "presentation.json")
PRES_DIR = os.path.join(ROOT, "oi", "presentation")


def code_files(base):
    for r, ds, fs in os.walk(base):
        ds[:] = [d for d in ds if d != "__pycache__"]
        for f in fs:
            if f.endswith((".py", ".json")): yield os.path.join(r, f)


class TestSameRendererBothCases(unittest.TestCase):
    def test_renders_reseller_and_construction_with_same_function(self):
        h1 = R.render(reseller_records(), PRES("reseller_pilot")); h2 = R.render(cs_records(), PRES("construction_software"))
        self.assertIn("Piloto de reventa", h1); self.assertIn("Constructoras", h2); self.assertNotIn("Piloto de reventa", h2); self.assertNotIn("Constructoras", h1)
    def test_render_is_deterministic(self):
        self.assertEqual(R.render(cs_records(), PRES("construction_software")), R.render(cs_records(), PRES("construction_software")))
    def test_metrics_are_computed_from_records_not_hardcoded(self):
        m = R.metrics(reseller_records()); self.assertEqual(m["total"], 30)
        m2 = R.metrics(reseller_records()[:7]); self.assertEqual(m2["total"], 7); self.assertNotEqual(m["by_action"], m2["by_action"])
        h = R.render(reseller_records()[:7], PRES("reseller_pilot")); self.assertIn("<b>7</b>oportunidades", h)
    def test_case_vocabulary_comes_only_from_presentation_config(self):
        recs = reseller_records(); with_vocab = R.render(recs, PRES("reseller_pilot")); p = PRES("reseller_pilot"); p["human_decision_vocabulary"] = None
        without = R.render(recs, p); self.assertIn(">DUDOSA</option>", with_vocab); self.assertNotIn(">DUDOSA</option>", without); self.assertIn(">Dudosa</option>", without)
    def test_group_labels_only_from_config(self):
        self.assertIn("G1", R.render(reseller_records(), PRES("reseller_pilot")))
        p = PRES("reseller_pilot"); p["facets"] = []; self.assertNotIn("data-facet=", R.render(reseller_records(), p))
    def test_english_locale_renders_same_records(self):
        h = R.render(cs_records(), PRES("construction_software"), locale="en"); self.assertIn('lang="en"', h); self.assertIn("SYNTHETIC DEMO DATA", h); self.assertIn("What is NOT known", h)
    def test_unknown_locale_fails_loudly(self):
        with self.assertRaises(R.RenderError): R.render(cs_records(), PRES("construction_software"), locale="xx")
    def test_new_case_needs_only_config_no_code(self):
        recs = cs_records(); newp = {"title": "Caso nuevo", "subtitle": "x", "facets": [{"key": "qualification", "label": "Tipo"}], "locale": "es"}
        h = R.render(recs, newp); self.assertIn("Caso nuevo", h); self.assertIn('data-facet="qualification"', h)
    def test_invalid_record_rejected_by_renderer(self):
        r = cs_records(); del r[0]["contact"]
        with self.assertRaises(R.RenderError): R.render(r, PRES("construction_software"))
    def test_html_is_escaped(self):
        r = cs_records(); r[0]["company"]["name"] = "<script>alert(1)</script>"
        h = R.render(r, PRES("construction_software")); self.assertNotIn("<script>alert(1)</script>", h); self.assertIn("&lt;script&gt;alert(1)", h)
    def test_banner_only_for_non_real_origins(self):
        # Versión pública: los registros del piloto de reventa son SYNTHETIC_FIXTURE (identidades sustituidas). La propiedad del renderer se comprueba
        # declarando explícitamente el origen histórico en una copia, como en la rama privada con los registros reales.
        historical = [dict(r, provenance=dict(r["provenance"], data_origin="HISTORICAL_IMPORT")) for r in reseller_records()]
        self.assertIn("SINTÉTICOS", R.render(cs_records(), PRES("construction_software"))); self.assertNotIn("SINTÉTICOS", R.render(historical, PRES("reseller_pilot")))
        self.assertIn("histórica", R.render(historical, PRES("reseller_pilot")))
        self.assertIn("SINTÉTICOS", R.render(reseller_records(), PRES("reseller_pilot")))
    def test_page_states_principle_evidence_is_not_usefulness(self):
        self.assertIn("no equivale a utilidad comercial", R.render(cs_records(), PRES("construction_software")))
    def test_page_explains_unknowns_and_no_contact_reason(self):
        h = R.render(cs_records(), PRES("construction_software")); self.assertIn("Lo que NO se sabe", h); self.assertIn("No hay contacto recomendado", h); self.assertIn("Descartados como contacto", h)


class TestRendererPurity(unittest.TestCase):
    FORBIDDEN = [r"[Pp]iloto de reventa", r"reseller", r"alimentari", r"\bG[1-4]\b", r"RP-", r"\bSÍ\b", r"\bDUDOSA\b", r"construcci", r"constructora", r"\b30\b", r"\b12\b", r"\b15\b", r"software de gesti"]
    def test_no_case_literals_in_presentation_package(self):
        for p in code_files(PRES_DIR):
            if p.endswith("render.py") or "i18n" in p:
                txt = open(p, encoding="utf-8").read()
                if p.endswith("render.py"): txt = re.sub(r"\.metric b\{font-size:20px", "", txt)
                for rx in self.FORBIDDEN:
                    hits = [m.group(0) for m in re.finditer(rx, txt)]
                    if rx in (r"\b30\b", r"\b12\b", r"\b15\b"): hits = [h for h in hits if not re.search(r"font|margin|padding|gap|width|\d+px|rem|0\.|minmax", txt[max(0, txt.find(h) - 30):txt.find(h) + 30])]
                    self.assertEqual(hits, [], f"{os.path.basename(p)}: literal de caso {rx!r}")
    def test_renderer_has_no_replace_chains(self):
        txt = open(os.path.join(PRES_DIR, "render.py"), encoding="utf-8").read(); self.assertNotIn(".replace(", txt)
        self.assertNotRegex(txt, r"\brep\(")


class TestSingleLiveEngine(unittest.TestCase):
    def _defs(self, name):
        out = []
        for base in ("oi", "cases", "rules", "config"):
            b = os.path.join(ROOT, base)
            if not os.path.isdir(b): continue
            for p in code_files(b):
                if p.endswith(".py") and re.search(r"^def %s\(" % name, open(p, encoding="utf-8").read(), re.M): out.append(os.path.relpath(p, ROOT))
        return out
    def test_exactly_one_d010_engine(self): self.assertEqual(self._defs("evaluate_candidate"), ["oi/rules/d010/engine.py"])
    def test_exactly_one_ranking_builder(self): self.assertEqual(self._defs("build_ranking"), ["oi/rules/d010/engine.py"])
    def test_one_spec_engine_planner_executor(self):
        for cls, f in (("IntentEngine", "oi/engine/spec/engine.py"), ("Planner", "oi/engine/planner/planner.py"), ("Executor", "oi/engine/executor/runner.py")):
            hits = []
            for p in code_files(os.path.join(ROOT, "oi")):
                if p.endswith(".py") and re.search(r"^class %s\b" % cls, open(p, encoding="utf-8").read(), re.M): hits.append(os.path.relpath(p, ROOT))
            self.assertEqual(hits, [f])
    def test_engine_code_does_not_know_case_ids(self):
        bad = []
        for p in code_files(os.path.join(ROOT, "oi")):
            if not p.endswith(".py"): continue
            txt = open(p, encoding="utf-8").read()
            for rx in (r"reseller_pilot", r"RP-(MAY|DIS|CAD)", r"construction_software"):
                if re.search(rx, txt, re.I): bad.append((os.path.relpath(p, ROOT), rx))
        self.assertEqual([b for b in bad if not b[0].endswith("cli.py")], [], "el motor no debe conocer ningún caso por nombre")
    def test_d010_rules_have_no_scattered_case_ids_in_logic(self):
        txt = open(os.path.join(ROOT, "oi", "rules", "d010", "engine.py"), encoding="utf-8").read()
        for rid in ("F1", "FIT_KW", "O4", "O5", "T1", "O2", "P1"): self.assertNotRegex(txt, r"""["']%s["']""" % rid, f"ID de regla {rid} disperso en la lógica")
    def test_reseller_facts_live_only_in_reseller_case_config(self):
        c = load("cases", "reseller_pilot", "case.json"); self.assertEqual(c["frozen_facts"]["feedback"], {"SÍ": 12, "DUDOSA": 3, "NO": 15}); self.assertIn("RP-MAY", c["queries"])
        cs = load("cases", "construction_software", "case.json"); self.assertNotIn("frozen_facts", cs); self.assertNotIn("queries", cs)


class TestOiRunEndToEnd(unittest.TestCase):
    def test_run_is_reproducible(self):
        a = cli.run(CS_REQUEST, tempfile.mkdtemp()); b = cli.run(CS_REQUEST, tempfile.mkdtemp())
        strip = lambda S: {k: v for k, v in S.items() if k != "out_dir"}; self.assertEqual(strip(a), strip(b))
        ha = [open(os.path.join(a["out_dir"], f), "rb").read() for f in sorted(os.listdir(a["out_dir"])) if f.startswith(("01_", "02_", "04_"))]
        hb = [open(os.path.join(b["out_dir"], f), "rb").read() for f in sorted(os.listdir(b["out_dir"])) if f.startswith(("01_", "02_", "04_"))]
        self.assertEqual(ha, hb)
    def test_construction_stops_asking_about_growth(self):
        S = cli.run(CS_REQUEST, tempfile.mkdtemp()); self.assertEqual((S["exit_code"], S["status"], S["case"]), (2, "NEEDS_CLARIFICATION", "construction_software"))
        self.assertEqual([q["rule"] for q in S["clarifications"]["blocking"]], ["CRITERION_INCOMPLETE"]); self.assertIn("GROWTH_DEFINITION", S["pending_parameters"])
    def test_with_user_answer_reaches_plan_that_is_blocked_by_zero_credits(self):
        a = load("tests", "fixtures", "answers_TEST_ONLY.json"); S = cli.run(CS_REQUEST, tempfile.mkdtemp(), answers=a)
        self.assertEqual(S["status"], "PLANNED_NOT_EXECUTED"); self.assertEqual(S["stages"]["plan"]["status"], "planned"); self.assertEqual(S["stages"]["plan"]["plan_status"], "blocked")
        self.assertEqual(S["stages"]["execute"]["status"], "NOT_EXECUTED"); self.assertEqual(S["credits_spent"], 0)
        plan = load_path(os.path.join(S["out_dir"], "05_research_plan.json"))["plan"]; self.assertFalse(plan["authorization"]["spend_authorized"])
    def test_offline_produces_valid_records_and_html(self):
        S = cli.run(CS_REQUEST, tempfile.mkdtemp(), offline=True); self.assertEqual(S["stages"]["execute"]["status"], "EXECUTED_SYNTHETIC")
        recs = load_path(os.path.join(S["out_dir"], "11_opportunity_records.json")); self.assertEqual(len(recs), 18); R.validate_records(recs); self.assertTrue(os.path.isfile(os.path.join(S["out_dir"], "12_report.html")))
    def test_unknown_request_does_not_fabricate_a_case(self):
        S = cli.run("Quiero hoteles con spa en Lisboa", tempfile.mkdtemp(), offline=True); self.assertIsNone(S["case"]); self.assertNotIn("present", S["stages"]); self.assertEqual(S["EXTERNAL_CALLS"], 0)
    def test_cli_module_entry_point(self):
        import subprocess
        p = subprocess.run([sys.executable, "-m", "oi", "run", CS_REQUEST, "--out", tempfile.mkdtemp()], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(p.returncode, 2); self.assertIn("NEEDS_CLARIFICATION", p.stdout); self.assertIn("APOLLO_CREDITS = 0", p.stdout)


def load_path(p):
    with open(p, encoding="utf-8") as f: return json.load(f)


if __name__ == "__main__": unittest.main()
