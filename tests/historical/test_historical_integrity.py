"""Integridad histórica: piloto de reventa intacto, sin cambios de hash, feedback oficial sin cambios, ledgers sin cambios, D010 oficial sin cambios."""
import hashlib, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests._common import *

from oi.paths import HISTORY as OUT, history_path
MAN = load("tests", "fixtures", "historical_manifest.json")
LEDGERS = {"reseller_pilot_history/stage_a_relay/LEDGER.jsonl": "76d4eee659fd81140aebdcb5cad3f257bb638ea59d8ab0332f9409557cfcd2ca",
           "reseller_pilot_history/gate1_t1_run/CREDIT_LEDGER_T1.jsonl": "5b99b9b870d2475b400b4d34ad01a45e87903b23669be8607f84ce9962463e74"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def current_files():
    out = {}
    for d in MAN["dirs"]:
        for r, ds, fs in os.walk(os.path.join(OUT, d)):
            ds[:] = [x for x in ds if x != "__pycache__"]
            for fn in fs: out[os.path.relpath(os.path.join(r, fn), OUT)] = os.path.join(r, fn)
    return out


@unittest.skipUnless(os.path.isdir(os.path.join(OUT, "reseller_pilot_history")), "directorio histórico no disponible en esta máquina")
class TestHistoricalFilesUntouched(unittest.TestCase):
    def test_no_historical_file_changed_added_or_removed(self):
        cur = current_files(); want = MAN["files"]
        self.assertEqual(sorted(set(want) - set(cur)), [], "ficheros históricos desaparecidos")
        self.assertEqual(sorted(set(cur) - set(want)), [], "ficheros nuevos dentro de directorios históricos")
        changed = [k for k in want if sha(cur[k]) != want[k]]; self.assertEqual(changed, [], "ficheros históricos con hash distinto")
    def test_manifest_covers_substantial_history(self): self.assertGreaterEqual(MAN["count"], 400)
    def test_manifest_baseline_predates_this_work(self): self.assertLess(MAN["max_mtime"], MAN["audit_doc_mtime"])
    def test_no_file_in_history_newer_than_baseline(self):
        import datetime
        lim = datetime.datetime.fromisoformat(MAN["max_mtime"]).timestamp() + 1
        newer = [k for k, p in current_files().items() if os.path.getmtime(p) > lim]; self.assertEqual(newer, [])
    def test_ledgers_unchanged(self):
        for rel, h in LEDGERS.items(): self.assertEqual(sha(os.path.join(OUT, rel)), h, rel)
    def test_official_d010_unchanged(self):
        for k, h in MAN["files"].items():
            if k.startswith("d010_v0_2_official/"): self.assertEqual(sha(os.path.join(OUT, k)), h, k)
    def test_no_bytecode_cache_created_during_this_work(self):
        import datetime
        lim = datetime.datetime.fromisoformat(MAN["max_mtime"]).timestamp() + 1; new = []
        for d in MAN["dirs"]:
            for r, ds, fs in os.walk(os.path.join(OUT, d)):
                if os.path.basename(r) == "__pycache__": new += [os.path.join(r, f) for f in fs if os.path.getmtime(os.path.join(r, f)) > lim]
        self.assertEqual(new, [], "se creó un .pyc dentro del directorio histórico")


@unittest.skipUnless(os.path.isdir(os.path.join(OUT, "reseller_pilot_history")), "directorio histórico no disponible en esta máquina")
class TestHHCaseReproducible(unittest.TestCase):
    def test_declared_source_hashes_match(self):
        c = load("cases", "reseller_pilot", "case.json")
        for s in c["historical_sources"]["files"]: self.assertEqual(sha(os.path.join(history_path(c["historical_sources"]["root"]), s["path"])), s["sha256"], s["id"])
    def test_official_feedback_totals_unchanged(self):
        c = load("cases", "reseller_pilot", "case.json"); fb = json.load(open(os.path.join(history_path(c["historical_sources"]["root"]), "t3_preview/T3_PREVIEW_OFFICIAL_FEEDBACK.json"), encoding="utf-8"))
        self.assertEqual(fb["totals"], {"SÍ": 12, "DUDOSA": 3, "NO": 15})
    def test_reseller_records_regenerate_identically(self):
        sys.path.insert(0, os.path.join(ROOT, "cases", "reseller_pilot"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("reseller_adapter", os.path.join(ROOT, "cases", "reseller_pilot", "adapter.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        new = json.loads(json.dumps(m.build_records(), sort_keys=True)); self.assertEqual(new, reseller_records())
    def test_reseller_records_keep_the_official_feedback(self):
        import collections
        self.assertEqual(dict(collections.Counter((r["qualification"]["human_review"] or {})["decision"] for r in reseller_records())), {"ACCEPT": 12, "MAYBE": 3, "REJECT": 15})
    def test_reseller_adapter_writes_only_its_own_records_file(self):
        import re
        txt = open(os.path.join(ROOT, "cases", "reseller_pilot", "adapter.py"), encoding="utf-8").read()
        writes = re.findall(r"open\(([^)]*)['\"]w['\"]", txt); self.assertEqual(len(writes), 1); self.assertIn("out", writes[0])

    def test_reseller_import_adds_zero_cost(self):
        self.assertEqual(sum(r["cost"]["credits"] + r["cost"]["paid_calls"] for r in reseller_records()), 0)


class TestProductArchivePolicy(unittest.TestCase):
    def test_audit_document_not_modified_marker(self):
        p = os.path.join(ROOT, "phase1_audit", "OI_PHASE1_AUDIT_AND_DESIGN.md"); self.assertTrue(os.path.isfile(p))
        m = json.load(open(os.path.join(ROOT, "tests", "fixtures", "historical_manifest.json"))); self.assertIn("audit_doc_mtime", m)
    def test_migration_marker_present_and_single(self):
        d = load("tools", "MIGRATION_DONE.json"); self.assertIn("source_tree_sha256", json.dumps(d))


if __name__ == "__main__": unittest.main()
