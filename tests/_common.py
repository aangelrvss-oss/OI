import json, os, sys, tempfile
sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
os.environ.setdefault("OI_PRODUCT_ROOT", ROOT)
CASES = os.path.join(ROOT, "cases")
def load(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f: return json.load(f)
def reseller_records(): return load("cases", "reseller_pilot", "records", "opportunity_records.json")
E2E_REQUEST = "constructoras españolas en crecimiento que puedan necesitar software de gestión"
_E2E = {}
def e2e_run(request=E2E_REQUEST, **kw):
    """Ejecuta el pipeline offline UNA vez por proceso y por petición (determinista). -> resumen RUN_SUMMARY."""
    from oi import cli
    k = (request, tuple(sorted(kw.items())))
    if k not in _E2E: _E2E[k] = cli.run(request, tempfile.mkdtemp(), offline=True, **kw)
    return _E2E[k]
def cs_records():
    """Registros de Construcción+software PRODUCIDOS por el pipeline offline (no hay registros preparados a mano)."""
    S = e2e_run(); assert S["status"] == "COMPLETED_OFFLINE", S
    with open(os.path.join(S["out_dir"], "11_opportunity_records.json"), encoding="utf-8") as f: return json.load(f)
CLOCK = lambda: "2026-10-08T00:00:00+00:00"
CS_REQUEST = "Empresas constructoras españolas en crecimiento que puedan ser clientes potenciales de software de gestión."
def engine(version="2.0"):
    from oi.engine.spec.engine import IntentEngine
    from oi.engine.spec.versioning import SpecStore
    return IntentEngine(store=SpecStore(os.path.join(tempfile.mkdtemp(), "s.jsonl")), clock=CLOCK, schema_version=version)

def cs_by(fragment):
    """Registro de Construcción+software cuyo nombre contiene `fragment` (p. ej. 'Alfa'). Falla si no hay exactamente uno."""
    m = [r for r in cs_records() if fragment in r["company"]["name"]]
    assert len(m) == 1, (fragment, len(m)); return m[0]
