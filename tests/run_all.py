#!/usr/bin/env python3
"""Ejecuta TODAS las suites del producto y escribe tests/LAST_RUN.json con los números reales.
Uso:  python3 tests/run_all.py            (desde la raíz del producto)
Suites: contracts, spec_v2, safety, generalization, historical, rules (D010) y legacy_parity (motor migrado: 4 suites + harness)."""
import json, os, subprocess, sys, unittest, warnings
sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); os.environ["OI_PRODUCT_ROOT"] = ROOT
warnings.simplefilter("ignore", ResourceWarning)
SUITES = ["contracts", "spec_v2", "safety", "generalization", "historical", "rules", "e2e", "f12", "f13", "f13_1", "f14", "f15", "f15_1"]


def main():
    res = {}
    for s in SUITES:
        if not os.path.isdir(os.path.join(ROOT, "tests", s)):
            # Versión pública: las suites F12–F15.1 verifican los resultados de experimentos con datos reales que no se publican.
            res[s] = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0, "not_published": "requiere datos reales privados; se ejecuta en la copia privada"}
            continue
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests", s), top_level_dir=ROOT, pattern="test_*.py")
        r = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
        res[s] = {"ran": r.testsRun, "failures": len(r.failures), "errors": len(r.errors), "skipped": len(r.skipped)}
        for t, tb in r.failures + r.errors: print(f"FALLA [{s}] {t}\n{tb[:600]}")
    p = subprocess.run([sys.executable, "-W", "ignore", os.path.join(ROOT, "tests", "run_legacy_parity.py")], capture_output=True, text=True, cwd=ROOT)
    tail = (p.stderr + p.stdout).strip().splitlines()
    ran = next((int(l.split()[1]) for l in tail if l.startswith("Ran ")), 0)
    res["legacy_parity"] = {"ran": ran, "failures": 0 if p.returncode == 0 else 1, "errors": 0, "skipped": 0, "ok_line": tail[-1] if tail else ""}
    tot = {k: sum(v[k] for v in res.values()) for k in ("ran", "failures", "errors", "skipped")}
    res["TOTAL"] = tot
    with open(os.path.join(ROOT, "tests", "LAST_RUN.json"), "w", encoding="utf-8") as f: json.dump(res, f, ensure_ascii=False, indent=1)
    for k, v in res.items(): print(f"{k:15s} ran={v['ran']:4d} fail={v['failures']} err={v['errors']} skip={v['skipped']}" + (f"  NO PUBLICADA: {v['not_published']}" if v.get("not_published") else ""))
    return 0 if tot["failures"] == 0 and tot["errors"] == 0 else 1


if __name__ == "__main__": sys.exit(main())
