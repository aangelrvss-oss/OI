# -*- coding: utf-8 -*-
"""Tests de project_opportunity. Cada test comprueba un comportamiento que puede fallar de verdad."""
import copy, datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
import project_opportunity as po

AS_OF = dt.date(2026, 10, 8)


def base(**kw):
    o = dict(id="T1", signal_level="B", match="DEMOSTRADO", geo_level=1, route="SUBCONTRATA", role="CONSTRUCTORA",
             status_date="2026-09-01", explicit_need=None,
             why_now=dict(event="e", date="2026-09-01", location="Madrid", company="X", potential_need="n",
                          offering_capability="cableado", source="https://x"),
             claims=[dict(id="C1", claim="adjudicación", value="X", claim_status="KNOWN", source_class="OFFICIAL",
                          source_reference="https://x", snippet="X se adjudica", seen_in="page", kind="fact")],
             contact=dict(quality="INDIRECT", channel_type="company_phone", value="+34 900", evidence=["C1"]))
    o.update(kw)
    return o


class TestRecency(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(po.recency("2026-09-01", AS_OF), "RECIENTE")
        self.assertEqual(po.recency("2026-03-01", AS_OF), "ACTIVA")
        self.assertEqual(po.recency("2024-10-01", AS_OF), "ANTIGUA")
        self.assertEqual(po.recency(None, AS_OF), "SIN_FECHA")
        self.assertEqual(po.recency("2027-01-01", AS_OF), "CONFLICTING_DATE")
        self.assertEqual(po.recency("2026-08", AS_OF), "RECIENTE")

    def test_boundary_90_days(self):
        d = (AS_OF - dt.timedelta(days=90)).isoformat()
        self.assertEqual(po.recency(d, AS_OF), "RECIENTE")
        d = (AS_OF - dt.timedelta(days=91)).isoformat()
        self.assertEqual(po.recency(d, AS_OF), "ACTIVA")


class TestValidate(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(po.validate(base()), [])

    def test_known_from_directory_rejected(self):
        o = base(); o["claims"][0]["source_class"] = "PROFESSIONAL_DIRECTORY"
        self.assertTrue(any("fuente no admitida" in x for x in po.validate(o)))

    def test_known_without_url_or_snippet(self):
        o = base(); o["claims"][0]["source_reference"] = None; o["claims"][0]["snippet"] = None
        errs = po.validate(o)
        self.assertTrue(any("sin URL" in x for x in errs)); self.assertTrue(any("fragmento" in x for x in errs))

    def test_known_from_search_snippet_rejected(self):
        o = base(); o["claims"][0]["seen_in"] = "search_snippet"
        self.assertTrue(any("snippet de búsqueda" in x for x in po.validate(o)))

    def test_inference_cannot_be_known(self):
        o = base(); o["claims"][0]["kind"] = "inference"
        self.assertTrue(any("inferencia" in x for x in po.validate(o)))

    def test_level_a_requires_explicit_need_with_evidence(self):
        o = base(signal_level="A")
        self.assertTrue(any("nivel A" in x for x in po.validate(o)))
        o = base(signal_level="A", explicit_need="cableado CPD")
        self.assertTrue(any("explicit_need sin evidencia" in x for x in po.validate(o)))
        o["claims"][0]["supports"] = "explicit_need"
        self.assertEqual(po.validate(o), [])

    def test_constructed_email_rejected(self):
        o = base()
        o["claims"].append(dict(id="C2", claim="email", value="persona.sintetica@x.example", claim_status="KNOWN", source_class="OFFICIAL",
                                source_reference="https://x/c", snippet="contacto: info@x.example", seen_in="page", kind="fact"))
        self.assertTrue(any("email no presente" in x for x in po.validate(o)))

    def test_direct_requires_direct_channel(self):
        o = base(); o["contact"]["quality"] = "DIRECT"
        self.assertTrue(any("DIRECT con canal no directo" in x for x in po.validate(o)))

    def test_contact_evidence_must_exist(self):
        o = base(); o["contact"]["evidence"] = ["C9"]
        self.assertTrue(any("evidencia inexistente" in x for x in po.validate(o)))


class TestClassify(unittest.TestCase):
    def test_prioritaria(self):
        self.assertEqual(po.classify(base(), AS_OF)[0], "PRIORITARIA")

    def test_incomplete_why_now_is_not_top(self):
        o = base(); o["why_now"]["potential_need"] = None
        b, why = po.classify(o, AS_OF)
        self.assertEqual(b, "A_VERIFICAR"); self.assertIn("potential_need", why[0])

    def test_size_alone_never_top(self):
        o = base(signal_level="D")
        self.assertNotEqual(po.classify(o, AS_OF)[0], "PRIORITARIA")

    def test_no_contact_is_verify(self):
        o = base(); o["contact"] = dict(quality="NONE")
        self.assertEqual(po.classify(o, AS_OF)[0], "A_VERIFICAR")

    def test_old_status_not_top(self):
        self.assertNotEqual(po.classify(base(status_date="2024-01-01"), AS_OF)[0], "PRIORITARIA")

    def test_awaiting_award(self):
        self.assertEqual(po.classify(base(awaiting="pendiente de adjudicación"), AS_OF)[0], "A_VIGILAR")

    def test_discarded(self):
        self.assertEqual(po.classify(base(discard_reason="obra terminada"), AS_OF)[0], "DESCARTADA")


class TestPriority(unittest.TestCase):
    def test_contact_page_does_not_count_as_project_evidence(self):
        o = base(); o["claims"][0]["source_class"] = "SECTOR_SOURCE"
        o["claims"].append(dict(id="C2", claim="email", value="info@x.example", claim_status="KNOWN", source_class="OFFICIAL",
                                source_reference="https://x/c", snippet="info@x.example", seen_in="page", kind="fact"))
        o["contact"]["evidence"] = ["C2"]
        self.assertEqual(po.best_source(o), "SECTOR_SOURCE")

    def test_explicit_need_beats_everything(self):
        a = base(id="A", signal_level="A", explicit_need="x", match="POSIBLE", geo_level=3, status_date="2026-03-01",
                 contact=dict(quality="NONE"))
        a["claims"][0]["supports"] = "explicit_need"
        b = base(id="B")
        self.assertLess(po.priority_key(a, AS_OF), po.priority_key(b, AS_OF))

    def test_recency_before_match(self):
        recent_probable = base(id="R", match="PROBABLE", status_date="2026-09-20")
        older_demo = base(id="O", match="DEMOSTRADO", status_date="2026-02-01")
        self.assertLess(po.priority_key(recent_probable, AS_OF), po.priority_key(older_demo, AS_OF))

    def test_madrid_before_evidence_and_contact(self):
        cap = base(id="M", geo_level=1, contact=dict(quality="INDIRECT", channel_type="company_phone", value="x", evidence=["C1"]))
        cm = base(id="N", geo_level=2, contact=dict(quality="DIRECT", channel_type="supplier_portal", value="x", evidence=["C1"]))
        self.assertLess(po.priority_key(cap, AS_OF), po.priority_key(cm, AS_OF))

    def test_contact_breaks_ties(self):
        d = base(id="Z", contact=dict(quality="DIRECT", channel_type="supplier_portal", value="x", evidence=["C1"]))
        i = base(id="A")
        self.assertLess(po.priority_key(d, AS_OF), po.priority_key(i, AS_OF))

    def test_rank_numbers_only_prioritarias(self):
        out = po.rank([base(id="P"), base(id="V", awaiting="pendiente")], AS_OF)
        self.assertEqual([x.get("rank") for x in out], [1, None])

    def test_labels(self):
        self.assertEqual(po.priority_label(base(signal_level="A", explicit_need="x"), AS_OF)[0], "ALTA")
        self.assertEqual(po.priority_label(base(), AS_OF)[0], "ALTA")
        self.assertEqual(po.priority_label(base(status_date="2026-01-10"), AS_OF)[0], "MEDIA")
        self.assertEqual(po.priority_label(base(signal_level="C"), AS_OF)[0], "BAJA")


if __name__ == "__main__":
    unittest.main()
