"""Suite de PARIDAD: las 4 suites genéricas del motor original, sobre el árbol nuevo (oi.engine.*).
Debe reproducir el baseline del motor vivo: 'Ran 180 tests ... OK'."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["OI_PRODUCT_ROOT"] = ROOT
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True
if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests", "legacy_parity"), pattern="test_*.py", top_level_dir=os.path.join(ROOT, "tests", "legacy_parity"))
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)
