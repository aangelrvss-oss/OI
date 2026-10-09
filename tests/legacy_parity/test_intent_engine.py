import copy, json, os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import *
from oi.engine.spec import mini_jsonschema as mj
from oi.engine.spec.registry import Registry
from oi.engine.spec.versioning import SpecStore, SpecImmutableError, canonical_hash
from oi.engine.spec import validation as V

def codes(items): return [i["code"] for i in items]
def crit(spec, cid): return next(c for c in spec["criteria"] if c["id"] == cid)
def blocking(r): return [q["id"] for q in r["clarifications"] if q["blocking"]]
def base_draft(**over):
    d = draft("case3"); d.update(over); return d


class TestSevenCases(unittest.TestCase):
    def setUp(self): self.e = new_engine()

    def test_case1_main(self):
        r = self.e.process(draft("case1")); s = r["spec"]
        self.assertEqual(r["status"], "confirmed"); self.assertTrue(r["valid"]); self.assertEqual(r["errors"], [])
        self.assertEqual(crit(s, "G2")["activation"], "active"); self.assertEqual(crit(s, "G2")["strength"], "gate")
        for o in ("O1", "O2"):  # señales inferidas: hipótesis propuestas, nunca activas ni gate
            self.assertEqual(crit(s, o)["activation"], "proposed"); self.assertEqual(crit(s, o)["strength"], "preferred")
        self.assertFalse(any("manual" in json.dumps(c, ensure_ascii=False).lower() for c in s["criteria"]), "no se asume 'procesos manuales'")
        self.assertEqual(s["restrictions"]["geography"]["include"], [{"name": "ES", "level": "country"}])
        self.assertEqual(s["restrictions"]["quantity"]["target"], 20)
        self.assertEqual(blocking(r), []); self.assertEqual([q["id"] for q in r["clarifications"]], ["Q-SIGNALS"])
        self.assertEqual(crit(s, "G2")["evidence"]["origin"], "registry_default")
        self.assertEqual(mj.validate(s, Registry().schema), [])

    def test_case2_digital_madrid_and_offering(self):
        r = self.e.process(draft("case2")); s = r["spec"]
        self.assertEqual(r["status"], "needs_clarification")
        self.assertEqual(blocking(r), ["Q-OFFERING"])  # O.2: offering.description obligatorio
        self.assertIn("Q-TERM-improvable", [q["id"] for q in r["clarifications"]])
        self.assertEqual(next(q for q in r["clarifications"] if q["id"] == "Q-TERM-improvable")["blocking"], False)
        geo = [a for a in s["traceability"]["assumptions"] if a["basis"] == "geo_default"]
        self.assertEqual(len(geo), 1); self.assertEqual(s["restrictions"]["geography"]["include"], [{"name": "Madrid", "level": "municipality"}])
        for d in ("D1", "D2", "D3"):
            self.assertEqual(crit(s, d)["activation"], "proposed"); self.assertEqual(crit(s, d)["definition_status"], "needs_definition")

    def test_case2b_followup_creates_v2_without_touching_v1(self):
        r1 = self.e.process(draft("case2")); sid = r1["spec"]["control"]["spec_id"]
        h1 = r1["spec"]["control"]["content_hash"]
        r2 = self.e.process(draft("case2b"), user_confirmations=CONF_2B, user_inputs=INPUT_2B, parent=(sid, 1))
        s = r2["spec"]
        self.assertEqual(r2["status"], "confirmed")
        self.assertEqual((s["control"]["spec_id"], s["control"]["spec_version"], s["control"]["parent_spec_version"]), (sid, 2, 1))
        self.assertEqual([crit(s, c)["activation"] for c in ("D1", "D2", "D3")], ["active", "proposed", "active"])
        self.assertEqual(self.e.store.get(sid, 1)["control"]["content_hash"], h1)  # v1 intacta
        self.assertEqual(self.e.store.get(sid, 1)["control"]["status"], "needs_clarification")

    def test_case3_size_not_invented(self):
        r = self.e.process(draft("case3")); s = r["spec"]
        self.assertEqual(r["status"], "confirmed")
        self.assertFalse(any(c["field"] == "firmographic.employee_count" for c in s["criteria"]))
        self.assertIn("SIZE_UNSPECIFIED", codes(r["warnings"]))
        self.assertFalse(any("employee" in a["field"] for a in s["traceability"]["assumptions"]))

    def test_case4_relative_term_blocks_and_nothing_activates(self):
        r = self.e.process(draft("case4")); s = r["spec"]
        self.assertEqual(r["status"], "needs_clarification"); self.assertEqual(sorted(blocking(r)), ["Q-OFFERING", "Q-TERM-rapid_growth"])
        q = next(q for q in r["clarifications"] if q["id"] == "Q-TERM-rapid_growth")
        self.assertEqual(len(q["options"]), 6)  # menú de señales sin preselección
        self.assertEqual([c for c in s["criteria"] if c["activation"] == "active"], [])

    def test_case4b_explicit_definition_unlocks(self):
        r1 = self.e.process(draft("case4")); sid = r1["spec"]["control"]["spec_id"]
        r2 = self.e.process(draft("case4b"), user_confirmations=CONF_4B, user_inputs=INPUT_4B, parent=(sid, 1))
        s = r2["spec"]
        self.assertEqual(r2["status"], "confirmed"); self.assertEqual(s["control"]["spec_version"], 2)
        c = crit(s, "C1"); self.assertEqual(c["activation"], "active"); self.assertEqual(c["params"], {"period_days": 365}); self.assertEqual(c["value"], 25)
        self.assertTrue(c["definition"]["explicit"])

    def test_case5a_too_vague(self):
        r = self.e.process(draft("case5a"))
        self.assertEqual(r["status"], "needs_clarification"); self.assertEqual(sorted(blocking(r)), ["Q-OFFERING", "Q-SCOPE-GEO", "Q-SCOPE-SEGMENT"])
        self.assertEqual(r["spec"]["criteria"], [])

    def test_case5b_executable_sme_catalog(self):
        r = self.e.process(draft("case5b")); s = r["spec"]
        self.assertEqual(r["status"], "confirmed")
        g = crit(s, "G2"); self.assertEqual((g["origin"], g["activation"], g["value"]), ("assumed", "active", 249))
        self.assertTrue(any(a["basis"] == "term_catalog" and a["value"]["value"] == 249 for a in s["traceability"]["assumptions"]))
        self.assertEqual([crit(s, o)["activation"] for o in ("O1", "O2")], ["proposed", "proposed"])
        self.assertTrue(any("regulatorio" in x for x in s["traceability"]["proposed_signals"]))

    def test_case6_budget_user_parameter_only_and_no_invented_costs(self):
        r = self.e.process(draft("case6"), user_params={"budget": BUDGET6}); s = r["spec"]
        self.assertEqual(r["status"], "confirmed"); self.assertEqual(s["restrictions"]["quantity"]["target"], 100)
        self.assertEqual(s["budget"]["source"], "user_parameter"); self.assertEqual(s["budget"]["tool_credits_max"], {"apollo": 20})
        self.assertEqual((s["budget"]["data_calls_max"], s["budget"]["deep_research_top_n"]), (15, 5))
        self.assertTrue({"BUDGET_DEEP_RESEARCH_BELOW_TARGET", "BUDGET_FEASIBILITY_DEFERRED"} <= set(codes(r["warnings"])))
        self.assertNotIn("cost", json.dumps(s["budget"]).lower())

    def test_case7_unsupported_without_breaking_schema(self):
        r = self.e.process(draft("case7")); s = r["spec"]
        self.assertEqual(r["status"], "unsupported_yet"); self.assertEqual(r["errors"], [])
        self.assertEqual(s["intent"]["opportunity_type"], "talent"); self.assertEqual(s["criteria"], [])
        self.assertEqual(s["rejection"]["supported_types"], ["customer_prospect"])
        cap = s["rejection"]["captured"]
        self.assertTrue(cap["captured_unvalidated"]); self.assertEqual(cap["criteria"][0]["field"], "candidate.skill")
        self.assertEqual(cap["restrictions"]["quantity"]["target"], 20)
        self.assertEqual(mj.validate(s, Registry().schema), [])
        self.assertEqual(r["unsupported"][0]["requested_opportunity_type"], "talent")

    def test_unsupported_with_unknown_fields_is_not_an_error(self):
        d = draft("case7"); d["criteria"].append({"id": "S2", "dimension": "fit", "field": "skills.totally_new", "operator": "eq", "strength": "gate", "origin": "user"})
        d["intent"]["opportunity_type"] = "supplier"
        r = self.e.process(d); self.assertEqual(r["status"], "unsupported_yet")

    def test_ambiguous_cases_do_not_confirm(self):
        for n in ("case2", "case4", "case5a"):
            self.assertEqual(self.e.process(draft(n))["status"], "needs_clarification", n)


class TestLLMHasNoAuthority(unittest.TestCase):
    def setUp(self): self.e = new_engine()

    def test_llm_claims_are_ignored_and_logged(self):
        d = draft("case5a"); d["status"] = "confirmed"; d["valid"] = True; d["control"]["status"] = "confirmed"
        d["budget"] = {"data_calls_max": 9999}; d["versions"] = {"schema_version": "9.9"}
        r = self.e.process(d)
        self.assertEqual(r["status"], "needs_clarification"); self.assertIsNone(r["spec"]["budget"])
        self.assertEqual(r["spec"]["versions"]["schema_version"], "1.0")
        self.assertGreaterEqual(codes(r["warnings"]).count("LLM_CLAIM_IGNORED"), 4)

    def test_llm_cannot_activate_or_define_criteria(self):
        d = draft("case1"); c = [x for x in d["criteria"] if x["id"] == "O1"][0]
        c.update(activation="active", definition_status="defined", weight=0.9, definition={"explicit": True, "text": "yo lo defino", "confirmed_by": "user"})
        r = self.e.process(d); o1 = crit(r["spec"], "O1")
        self.assertEqual(o1["activation"], "proposed"); self.assertIsNone(o1["weight"]); self.assertIsNone(o1.get("definition"))

    def test_llm_hides_relative_term_by_defining_it_itself(self):
        d = draft("case4"); d["criteria"] = [{"id": "X1", "dimension": "fit", "field": "growth.hiring_velocity", "operator": "gte", "value": 10,
            "params": {"period_days": 365}, "strength": "gate", "origin": "user", "source_text": "rápidamente"}]
        r = self.e.process(d)  # (a) umbral inventado: el 10 no está en el texto citado
        self.assertEqual(r["status"], "error"); self.assertIn("UNGROUNDED_VALUE", codes(r["errors"]))
        d["criteria"][0].update(value=None, params={})  # (b) sin umbral: el término sigue sin definir
        r = self.e.process(d)
        self.assertEqual(r["status"], "needs_clarification"); self.assertIn("Q-TERM-rapid_growth", blocking(r))
        self.assertEqual(crit(r["spec"], "X1")["activation"], "proposed")
        d["criteria"][0]["defines_terms"] = ["rapid_growth"]  # (c) vinculado pero sin confirmación del usuario
        r = self.e.process(d); self.assertEqual(r["status"], "needs_clarification")
        self.assertEqual(crit(r["spec"], "X1")["activation"], "proposed"); self.assertIn("Q-TERM-rapid_growth", blocking(r))

    def test_llm_cannot_erase_term_by_rewriting_request_in_revision(self):
        r1 = self.e.process(draft("case4")); sid = r1["spec"]["control"]["spec_id"]
        d = draft("case4"); d["control"]["raw_request"] = "Encuentra 20 empresas españolas."
        r2 = self.e.process(d, parent=(sid, 1))
        self.assertEqual(r2["status"], "needs_clarification"); self.assertIn("LLM_CLAIM_OVERRIDDEN", codes(r2["warnings"]))
        self.assertIn("rápidamente", r2["spec"]["control"]["raw_request"])

    def test_llm_proposed_clarifications_never_change_status(self):
        d = draft("case1"); d["traceability"]["proposed_clarifications"] = [{"question": "¿Sector?", "why_it_matters": "x"}]
        r = self.e.process(d); self.assertEqual(r["status"], "confirmed")
        self.assertTrue(all(not q["blocking"] for q in r["clarifications"]))
        self.assertIn("llm", [q["origin"] for q in r["clarifications"]])

    def test_invented_requirements_are_rejected(self):
        d = draft("case3")  # el LLM inventa un rango de tamaño
        d["criteria"].append({"id": "G9", "dimension": "fit", "field": "firmographic.employee_count", "operator": "between", "value": [20, 500],
                              "strength": "gate", "origin": "assumed", "source_text": "20 empresas"})
        self.assertIn("UNGROUNDED_ASSUMPTION", codes(self.e.process(d)["errors"]))
        d["criteria"][-1].update(origin="user")  # y lo disfraza de petición del usuario con un número tomado de otro sitio
        self.assertIn("UNGROUNDED_VALUE", codes(self.e.process(d)["errors"]))
        d["criteria"][-1].update(source_text="inventado literal")
        self.assertIn("UNGROUNDED_CRITERION", codes(self.e.process(d)["errors"]))

    def test_inferred_gate_rejected_and_opportunity_gate_rejected_by_schema(self):
        d = draft("case1"); [x for x in d["criteria"] if x["id"] == "O1"][0]["strength"] = "gate"
        self.assertIn("SCHEMA_VIOLATION", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["criteria"].append({"id": "F9", "dimension": "fit", "field": "firmographic.industry", "operator": "eq", "value": "x", "strength": "gate", "origin": "inferred"})
        self.assertIn("INFERRED_GATE", codes(self.e.process(d)["errors"]))

    def test_ungrounded_offering_geo_exclusion(self):
        d = draft("case3"); d["intent"]["offering"]["source_text"] = "un CRM carísimo"
        self.assertIn("UNGROUNDED_OFFERING", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["restrictions"]["geography"]["include"].append({"name": "Barcelona", "level": "municipality"})
        self.assertIn("GEO_UNGROUNDED", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["restrictions"]["excluded_entities"] = ["Acciona"]
        self.assertIn("UNGROUNDED_EXCLUSION", codes(self.e.process(d)["errors"]))


class TestRegistryAndValidationLayers(unittest.TestCase):
    def setUp(self): self.e = new_engine()
    def add(self, **c):
        d = draft("case3"); c.setdefault("id", "Z1"); c.setdefault("dimension", "fit"); c.setdefault("strength", "preferred"); c.setdefault("origin", "inferred")
        d["criteria"].append(c); return self.e.process(d)

    def test_unknown_field_cannot_become_operational(self):
        r = self.add(field="firmographic.revenue", operator="gte", value=1000000)
        self.assertEqual(r["status"], "error"); self.assertIn("UNKNOWN_FIELD", codes(r["errors"])); self.assertFalse(r["persisted"]); self.assertIsNone(r["spec"])

    def test_reserved_field_not_operational_in_customer_flow(self):
        self.assertIn("FIELD_NOT_OPERATIONAL", codes(self.add(field="candidate.skill", operator="contains", value="python")["errors"]))

    def test_operator_dimension_value_param_evidence_errors(self):
        self.assertIn("OPERATOR_NOT_ALLOWED", codes(self.add(field="firmographic.employee_count", operator="contains", value=5)["errors"]))
        self.assertIn("DIMENSION_NOT_ALLOWED", codes(self.add(field="web.management_stack_visible", operator="exists", dimension="fit", strength="gate", origin="user", source_text="de construcción")["errors"]))
        self.assertIn("VALUE_INVALID", codes(self.add(field="firmographic.employee_count", operator="between", value=[500, 50], strength="gate", origin="user", source_text="de construcción")["errors"]))
        self.assertIn("VALUE_INVALID", codes(self.add(field="digital.has_website", dimension="opportunity", operator="eq", value="si")["errors"]))
        self.assertIn("PARAM_INVALID", codes(self.add(field="growth.hiring_velocity", dimension="opportunity", operator="gte", value=3, params={"period_days": 0})["errors"]))
        self.assertIn("PARAM_UNKNOWN", codes(self.add(field="hiring.ops_admin_roles", dimension="opportunity", operator="exists", params={"foo": 1})["errors"]))
        self.assertIn("EVIDENCE_TYPE_NOT_ALLOWED", codes(self.add(field="hiring.ops_admin_roles", dimension="opportunity", operator="exists", evidence={"allowed_evidence_types": ["seo_metric"]})["errors"]))

    def test_missing_params_means_needs_definition_not_active(self):
        r = self.add(field="growth.hiring_velocity", dimension="opportunity", operator="gte", value=3)
        z = crit(r["spec"], "Z1"); self.assertEqual((z["definition_status"], z["activation"]), ("needs_definition", "proposed"))

    def test_user_criterion_incomplete_blocks(self):
        d = draft("case4b")  # C1 vinculado al término, sin confirmación explícita
        r = self.e.process(d, user_inputs=INPUT_4B)
        self.assertEqual(r["status"], "needs_clarification"); self.assertIn("Q-TERM-rapid_growth", blocking(r))
        self.assertEqual(crit(r["spec"], "C1")["activation"], "proposed")
        d["criteria"][0]["defines_terms"] = []  # sin vínculo a término: queda 'criterio incompleto' (falta valor y periodo)
        r = self.e.process(d, user_inputs=INPUT_4B)
        self.assertEqual(r["status"], "needs_clarification"); self.assertIn("Q-CRIT-C1", blocking(r))
        r = self.e.process(draft("case4b"))  # sin la respuesta del usuario, la cita no existe en la petición
        self.assertIn("UNGROUNDED_CRITERION", codes(r["errors"]))

    def test_structural_errors(self):
        d = draft("case3"); d["criteria"].append(copy.deepcopy(d["criteria"][0]))
        self.assertIn("DUPLICATE_CRITERION_ID", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["governance"] = {"approval_required": False}
        self.assertIn("SCHEMA_VIOLATION", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["intent"]["opportunity_type"] = "foo"
        r = self.e.process(d); self.assertEqual(r["status"], "error"); self.assertTrue(any("enum" in e["message"] for e in r["errors"]))
        d = draft("case3"); d["restrictions"]["quantity"] = {"target": 0}
        self.assertEqual(self.e.process(d)["status"], "error")
        d = draft("case3"); del d["intent"]["objective"]
        self.assertIn("OBJECTIVE_MISSING", codes(self.e.process(d)["errors"]))
        d = draft("case3"); d["restrictions"]["quantity"] = {"target": 20, "max": 5, "source_text": "20 empresas"}
        self.assertIn("QUANTITY_RANGE", codes(self.e.process(d)["errors"]))

    def test_contradictory_gates(self):
        d = draft("case1"); d["criteria"].append({"id": "G3", "dimension": "fit", "field": "firmographic.employee_count", "operator": "lte", "value": 49,
                                                  "strength": "gate", "origin": "user", "source_text": "50-500 empleados"})
        self.assertIn("CRITERIA_CONTRADICTION", codes(self.e.process(d)["errors"]))

    def test_geography_ambiguity_default_and_override(self):
        d = draft("case2b"); d["restrictions"]["geography"]["include"] = [{"name": "Madrid", "level": "region"}]
        r = self.e.process(d, user_inputs=INPUT_2B)
        self.assertEqual(r["spec"]["restrictions"]["geography"]["include"][0]["level"], "municipality"); self.assertIn("LLM_CLAIM_OVERRIDDEN", codes(r["warnings"]))
        d = draft("case2b"); d["control"]["raw_request"] = "Encuentra 20 clínicas dentales de la Comunidad de Madrid."
        d["restrictions"]["geography"]["include"] = [{"name": "Comunidad de Madrid", "level": "region"}]
        d["criteria"] = [c for c in d["criteria"] if c["id"] == "G1"]
        r = self.e.process(d, user_inputs=INPUT_2B)
        self.assertEqual(r["spec"]["restrictions"]["geography"]["include"], [{"name": "Comunidad de Madrid", "level": "region"}])
        d["restrictions"]["geography"]["include"] = [{"name": "Atlantis", "level": "country"}]
        self.assertIn("GEO_UNKNOWN_COUNTRY", codes(self.e.process(d, user_inputs=INPUT_2B)["errors"]))

    def test_vague_and_unverified_quantity(self):
        d = draft("case3"); d["control"]["raw_request"] = "Encuentra muchas empresas de construcción en España que podrían necesitar software de gestión."
        d["restrictions"]["quantity"] = None
        r = self.e.process(d); self.assertEqual(r["status"], "needs_clarification"); self.assertIn("Q-QUANTITY", blocking(r))
        d = draft("case3"); d["restrictions"]["quantity"] = {"target": 35}
        r = self.e.process(d); self.assertEqual(r["status"], "needs_clarification"); self.assertIn("Q-QUANTITY", blocking(r))
        d = draft("case3"); d["restrictions"]["quantity"] = None
        r = self.e.process(d); self.assertEqual(r["spec"]["restrictions"]["quantity"]["target"], 20)
        self.assertTrue(any(a["basis"] == "engine_default" and a["field"].endswith("quantity.target") for a in r["spec"]["traceability"]["assumptions"]))

    def test_budget_rules(self):
        d = draft("case3")
        self.assertEqual(self.e.process(d, user_params={"budget": {"data_calls_max": 0}})["status"], "error")
        self.assertEqual(self.e.process(d, user_params={"budget": {"data_calls_max": -1}})["status"], "error")
        self.assertEqual(self.e.process(d, user_params={"budget": {"coste_euros": 3}})["status"], "error")
        self.assertIn("BUDGET_UNSET", codes(self.e.process(d)["warnings"]))
        r = self.e.process(d, user_params={"budget": {"tool_credits_max": {"apollo": 10}, "data_calls_max": 5, "infra_calls_max": 20}})
        self.assertEqual((r["spec"]["budget"]["data_calls_max"], r["spec"]["budget"]["infra_calls_max"]), (5, 20))  # llamadas ≠ créditos

    def test_questions_capped_per_turn(self):
        d = draft("case5a"); d["control"]["raw_request"] = "Encuentra muchas empresas innovadoras que necesiten algo."
        d["restrictions"]["quantity"] = None
        r = self.e.process(d); self.assertLessEqual(len(r["clarifications"]), 3)
        self.assertGreaterEqual(len(r["spec"]["traceability"]["deferred_questions"]), 1)
        self.assertTrue(all(q["blocking"] for q in r["clarifications"]))

    def test_confirm_helper_activates_inferred_signals(self):
        r1 = self.e.process(draft("case1")); sid = r1["spec"]["control"]["spec_id"]
        r2 = self.e.confirm(sid, 1, [{"criterion_id": "O1", "definition_text": "Cualquier oferta abierta en operaciones/administración en 90 días"}])
        s = r2["spec"]; self.assertEqual((s["control"]["spec_version"], s["control"]["parent_spec_version"]), (2, 1))
        self.assertEqual((crit(s, "O1")["activation"], crit(s, "O2")["activation"]), ("active", "proposed"))
        r3 = self.e.confirm(sid, 2, [{"criterion_id": "O2", "definition_text": "Al menos una mención en la web"}])  # arrastra la confirmación previa
        self.assertEqual((crit(r3["spec"], "O1")["activation"], crit(r3["spec"], "O2")["activation"]), ("active", "active"))
        self.assertEqual(self.e.store.versions(sid), [1, 2, 3])
        self.assertEqual(self.e.confirm(sid, 1, [{"criterion_id": "NOPE", "definition_text": "x"}])["status"], "error")

    def test_confirm_preserves_data_policy_and_keeps_engine_default_assumptions(self):  # regresión detectada en Fase 1b
        d = draft("case1"); d["governance"] = {"desired_action": "draft_outreach", "data_policy": {"personal_data_fields": ["name", "job_title"]}}
        r1 = self.e.process(d); sid = r1["spec"]["control"]["spec_id"]
        r2 = self.e.confirm(sid, 1, [{"criterion_id": "O1", "definition_text": "x"}]); g = r2["spec"]["governance"]
        self.assertEqual((g["desired_action"], g["data_policy"]["personal_data_fields"]), ("draft_outreach", ["name", "job_title"]))
        ass = {a["field"] for a in r2["spec"]["traceability"]["assumptions"]}
        self.assertIn("governance.data_policy.purpose", ass)       # el default del motor sigue marcado como asunción
        self.assertNotIn("governance.desired_action", ass)         # lo pidió el usuario: no es asunción
        r3 = self.e.confirm(*(lambda s: (s["spec"]["control"]["spec_id"], 1))(self.e.process(draft("case3"))), [{"criterion_id": "O1", "definition_text": "x"}])
        self.assertIn("governance.desired_action", {a["field"] for a in r3["spec"]["traceability"]["assumptions"]})


class TestVersioningAndDeterminism(unittest.TestCase):
    def test_store_is_append_only_and_tamper_evident(self):
        e = new_engine(); s = e.process(draft("case1"))["spec"]
        with self.assertRaises(SpecImmutableError): e.store.append(s)
        bad = copy.deepcopy(s); bad["control"]["spec_version"] = 2
        with self.assertRaises(SpecImmutableError): e.store.append(bad)  # hash no coincide
        self.assertEqual(e.store.verify_integrity(), [])
        e.store._specs[(s["control"]["spec_id"], 1)]["criteria"][0]["value"] = [1, 2]
        self.assertEqual(e.store.verify_integrity(), [(s["control"]["spec_id"], 1)])

    def test_new_request_new_spec_id_and_errors_not_persisted(self):
        e = new_engine(); a = e.process(draft("case1"))["spec"]; b = e.process(draft("case3"))["spec"]
        self.assertNotEqual(a["control"]["spec_id"], b["control"]["spec_id"])
        before = len(e.store._specs); d = draft("case3"); d["intent"]["opportunity_type"] = "foo"; e.process(d)
        self.assertEqual(len(e.store._specs), before)

    def test_jsonl_persistence_roundtrip(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "specs.jsonl")
        e = new_engine(p); s = e.process(draft("case1"))["spec"]
        e2 = new_engine(p); self.assertEqual(e2.store.get(s["control"]["spec_id"], 1), s); self.assertEqual(e2.store.verify_integrity(), [])
        self.assertEqual(e2.process(draft("case3"))["spec"]["control"]["spec_id"], "spec-0002")

    def test_deterministic_same_input_same_output(self):
        a = new_engine().process(draft("case1")); b = new_engine().process(draft("case1"))
        self.assertEqual(a["spec"], b["spec"]); self.assertEqual(a["spec"]["control"]["content_hash"], canonical_hash(a["spec"]))

    def test_versions_block_records_everything_needed(self):
        v = new_engine().process(draft("case1"))["spec"]["versions"]
        self.assertEqual((v["schema_version"], v["field_registry_version"], v["lexicon_version"], v["engine_version"], v["prompt_version"]),
                         ("1.0", "1.3.0", "1.0.0", "1.0.0", "intent_interpreter@1.0.0"))


class TestInfrastructure(unittest.TestCase):
    def test_no_external_tool_or_network_dependencies(self):
        forbidden = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|apollo|firecrawl|openrush|google|anthropic|openai)\b", re.M)
        pkg = os.path.join(os.environ["OI_PRODUCT_ROOT"], "oi", "engine", "spec")
        for f in os.listdir(pkg):
            if f.endswith(".py"):
                with open(os.path.join(pkg, f), encoding="utf-8") as fh: self.assertIsNone(forbidden.search(fh.read()), f)

    def test_registry_selfcheck_and_size(self):
        r = Registry(); self.assertLessEqual(len(r.fields), 22)   # techo 20→22 en la Fase 3C (decisión explícita: +2 campos genéricos)
        for f in r.fields.values():
            for k in ("field", "type", "allowed_operators", "allowed_dimensions", "allowed_entity_types", "evidence_compatible", "status", "registry_version"):
                self.assertIn(k, f)

    def test_mini_jsonschema_keywords(self):
        s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"enum": ["x"]}},
             "allOf": [{"if": {"properties": {"b": {"const": "x"}}, "required": ["b"]}, "then": {"required": ["c"]}}]}
        self.assertEqual(mj.validate({"a": 1}, s), [])
        self.assertEqual(len(mj.validate({"a": 0, "b": "x"}, s)), 2)
        self.assertEqual(len(mj.validate({"a": True}, s)), 1)  # bool no es integer


if __name__ == "__main__":
    unittest.main(verbosity=2)
