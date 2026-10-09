#!/usr/bin/env python3
"""Genera tests/fixtures/historical_manifest.json: sha256 de cada fichero de los directorios históricos (SOLO LECTURA).
Línea base tomada al final de la implementación del bloque F1–F10; ver `max_mtime` y `audit_doc_mtime`: ningún fichero histórico
tiene mtime posterior a la aprobación de la auditoría, es decir, no se modificó nada durante este trabajo. Ignora __pycache__."""
import hashlib, json, os, sys, datetime
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from oi.paths import HISTORY as O
DIRS = ["reseller_pilot_history", "d010_v0_2_official"]
AUDIT = os.path.join(HERE, "phase1_audit", "OI_PHASE1_AUDIT_AND_DESIGN.md")
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
files, mx = {}, 0
for d in DIRS:
    for r, ds, fs in os.walk(os.path.join(O, d)):
        ds[:] = [x for x in ds if x != "__pycache__"]
        for fn in fs:
            p = os.path.join(r, fn); files[os.path.relpath(p, O)] = sha(p); mx = max(mx, os.path.getmtime(p))
iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
out = {"schema": "historical_manifest.v1", "dirs": DIRS, "files": dict(sorted(files.items())), "count": len(files), "max_mtime": iso(mx), "audit_doc_mtime": iso(os.path.getmtime(AUDIT)),
       "note": "Línea base de integridad. Cualquier diferencia de hash, fichero nuevo o desaparecido en estos directorios es una violación de la regla 'experimento cerrado e inmutable'."}
with open(os.path.join(HERE, "tests", "fixtures", "historical_manifest.json"), "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
print("OK", len(files), "ficheros; max_mtime", out["max_mtime"], "< audit", out["audit_doc_mtime"], out["max_mtime"] < out["audit_doc_mtime"])
