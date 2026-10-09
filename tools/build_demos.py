#!/usr/bin/env python3
"""Regenera las páginas de demostración con EL MISMO renderer.
  * reseller_pilot: registros históricos importados (solo lectura) -> HTML.
  * construction_software: se EJECUTA el pipeline offline (`oi run ... --offline`) y se guarda su salida (registros + HTML) como copia de referencia SINTÉTICA.
    Nada se escribe a mano: si el pipeline cambia, esta copia cambia al regenerarla; tests/e2e comprueba que coinciden."""
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from oi.presentation.render import render
from oi import cli
E2E_REQUEST = "constructoras españolas en crecimiento que puedan necesitar software de gestión"
d = os.path.join(ROOT, "cases", "reseller_pilot")
R = json.load(open(os.path.join(d, "records", "opportunity_records.json"), encoding="utf-8")); P = json.load(open(os.path.join(d, "presentation.json"), encoding="utf-8"))
with open(os.path.join(d, "records", "report_reseller_pilot.html"), "w", encoding="utf-8") as f: f.write(render(R, P))
print("OK reseller_pilot", len(R), "registros")
S = cli.run(E2E_REQUEST, tempfile.mkdtemp(), offline=True)
assert S["status"] == "COMPLETED_OFFLINE" and S["EXTERNAL_CALLS"] == 0 and S["PAID_CALLS"] == 0 and S["APOLLO_CREDITS"] == 0, S
dst = os.path.join(ROOT, "cases", "construction_software", "records"); os.makedirs(dst, exist_ok=True)
shutil.copy(os.path.join(S["out_dir"], "11_opportunity_records.json"), os.path.join(dst, "opportunity_records_SYNTHETIC.json"))
shutil.copy(os.path.join(S["out_dir"], "12_report.html"), os.path.join(dst, "report_construction_software_SYNTHETIC.html"))
print("OK construction_software", S["records"], "registros (salida del pipeline offline)")
