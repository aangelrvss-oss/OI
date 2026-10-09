#!/usr/bin/env python3
"""Migración REPRODUCIBLE del motor vivo (/home/claude/intent_engine) al árbol del producto.

Reglas (orden del usuario, Fase 1):
  * COPIA, nunca mueve ni borra. El árbol origen queda intacto (se verifica por hash antes/después).
  * Reescribe imports mecánicamente (intent_engine/research_planner/executor -> oi.engine.*).
  * Los datos versionados salen del código: contratos -> oi/contracts, reglas -> rules/{spec,planner}.
  * Idempotente: borra y regenera SOLO las rutas que él mismo genera.

Uso:  python3 tools/migrate_engine.py [--src /home/claude/intent_engine]
"""
import argparse, hashlib, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = os.path.dirname(HERE)

# ── mapa módulo -> paquete destino ───────────────────────────────────────────────────────────────
PLANNER_MODS = ["budget", "capabilities", "config", "criteria", "ledger", "llm_proposals", "planner", "rules", "simulate", "stopping", "temporal", "validation"]
EVAL_MODS = ["entity", "evaluation", "geography", "identity", "relevance", "selection"]
PKG_OF = {**{m: "planner" for m in PLANNER_MODS}, **{m: "evaluators" for m in EVAL_MODS}}

DATA_MAP = {   # origen (relativo a --src)  ->  destino (relativo al producto)
    "data/search_spec.v1.schema.json": "oi/contracts/search_spec.v1.schema.json",
    "planner_data/research_plan.v2.schema.json": "oi/contracts/research_plan.v2.schema.json",
    "data/field_registry.v1.json": "rules/spec/field_registry.v1.json",
    "data/geo_catalog.v1.json": "rules/spec/geo_catalog.v1.json",
    "data/relative_terms.v1.json": "rules/spec/relative_terms.v1.json",
    "planner_data/capability_matrix.v1.json": "rules/planner/capability_matrix.v1.json",
    "planner_data/field_routing.v2.json": "rules/planner/field_routing.v2.json",
    "planner_data/planning_params.v2.json": "rules/planner/planning_params.v2.json",
    "planner_data/ruleset.v2.json": "rules/planner/ruleset.v2.json",
    "planner_data/source_classes.v1.json": "rules/planner/source_classes.v1.json",
    "planner_data/tool_catalog.v2.json": "rules/planner/tool_catalog.v2.json",
}
# versiones v1 del planner: NO son motor vivo -> archive (solo lectura)
ARCHIVE_MAP = {"planner_data/archive_v1": "archive/planner_data_v1",
               "planner_data/field_routing.v1.json": None, "planner_data/planning_params.v1.json": None}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()


def tree_hash(root):
    out = {}
    for d, _, fs in os.walk(root):
        if "__pycache__" in d: continue
        for f in fs:
            if f.endswith(".pyc"): continue
            p = os.path.join(d, f); out[os.path.relpath(p, root)] = sha(p)
    return out


def _split_items(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def rewrite(src, own_pkg):
    """own_pkg: 'spec' | 'planner' | 'evaluators' | 'executor' | None (tests). Devuelve texto reescrito."""
    # 1) intent_engine
    src = re.sub(r"\bfrom intent_engine(\.\w+)? import", lambda m: f"from oi.engine.spec{m.group(1) or ''} import", src)
    # 2) executor (absoluto)
    src = re.sub(r"\bfrom executor(\.[\w\.]+)? import", lambda m: f"from oi.engine.executor{m.group(1) or ''} import", src)
    # 3) research_planner.<mod> import ...
    src = re.sub(r"\bfrom research_planner\.(\w+) import",
                 lambda m: f"from oi.engine.{PKG_OF[m.group(1)]}.{m.group(1)} import", src)
    # 4) from research_planner import a as A, b as B  -> una sentencia por módulo (puede ir tras ';')
    def split_rp(m):
        stmts = []
        for it in _split_items(m.group(1)):
            name = it.split(" as ")[0].strip()
            if name == "PLANNER_VERSION": stmts.append(f"from oi.engine.planner import {it}")
            else: stmts.append(f"from oi.engine.{PKG_OF[name]} import {it}")
        return "; ".join(stmts)
    src = re.sub(r"\bfrom research_planner import ([\w ,]+?)(?=[ \t]*(?:;|\n|$))", split_rp, src)
    # 5) imports relativos dentro de research_planner: resolver a absoluto si cruzan de paquete
    if own_pkg in ("planner", "evaluators"):
        def rel_mod(m):
            mod = m.group(1)
            if PKG_OF.get(mod) == own_pkg: return m.group(0)
            return f"from oi.engine.{PKG_OF[mod]}.{mod} import"
        src = re.sub(r"\bfrom \.(\w+) import", rel_mod, src)
        def rel_pkg(m):
            stmts = []
            for it in _split_items(m.group(1)):
                name = it.split(" as ")[0].strip()
                if name in PKG_OF and PKG_OF[name] != own_pkg: stmts.append(f"from oi.engine.{PKG_OF[name]} import {it}")
                else: stmts.append(f"from . import {it}")
            return "; ".join(stmts)
        src = re.sub(r"\bfrom \. import ([\w ,]+?)(?=[ \t]*(?:;|\n|$))", rel_pkg, src)
    return src


def patch_once(text, old, new, label):
    if old not in text: raise SystemExit(f"PATCH NO APLICADO ({label}): no se encontró el texto esperado")
    return text.replace(old, new, 1)


def copy_py(src_file, dst_file, own_pkg):
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    with open(src_file, encoding="utf-8") as f: t = f.read()
    t = rewrite(t, own_pkg)
    with open(dst_file, "w", encoding="utf-8") as f: f.write(t)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--src", default="/home/claude/intent_engine"); ap.add_argument("--force", action="store_true"); a = ap.parse_args()
    S = a.src
    marker = os.path.join(HERE, "MIGRATION_DONE.json")
    if os.path.exists(marker) and not a.force:
        raise SystemExit("La migración ya se aplicó (MIGRATION_DONE.json). Los ficheros migrados se han editado a propósito (Spec v2, contratos...): "
                         "volver a ejecutar los SOBRESCRIBIRÍA. Usa --force solo para regenerar desde cero.")
    before = tree_hash(S)
    gen = ["oi/engine/spec", "oi/engine/planner", "oi/engine/evaluators", "oi/engine/executor", "oi/contracts",
           "rules/spec", "rules/planner", "tests/legacy_parity", "tests/fixtures/drafts", "archive/planner_data_v1", "docs/engine_history",
           "oi/engine/spec/prompts"]
    for g in gen:
        p = os.path.join(PRODUCT, g)
        if os.path.exists(p):
            for r, ds, fs in os.walk(p):               # por si archive quedó en solo lectura
                for x in ds + fs:
                    try: os.chmod(os.path.join(r, x), 0o755 if x in ds else 0o644)
                    except OSError: pass
            shutil.rmtree(p)
    # ── spec (ex intent_engine) ──
    for f in sorted(os.listdir(os.path.join(S, "intent_engine"))):
        if f.endswith(".py"): copy_py(os.path.join(S, "intent_engine", f), os.path.join(PRODUCT, "oi/engine/spec", f), "spec")
    os.makedirs(os.path.join(PRODUCT, "oi/engine/spec/prompts"), exist_ok=True)
    shutil.copy(os.path.join(S, "prompts/intent_interpreter.v1.md"), os.path.join(PRODUCT, "oi/engine/spec/prompts/intent_interpreter.v1.md"))
    # ── planner / evaluators (ex research_planner) ──
    copy_py(os.path.join(S, "research_planner/__init__.py"), os.path.join(PRODUCT, "oi/engine/planner/__init__.py"), "planner")
    os.makedirs(os.path.join(PRODUCT, "oi/engine/evaluators"), exist_ok=True)
    with open(os.path.join(PRODUCT, "oi/engine/evaluators/__init__.py"), "w", encoding="utf-8") as f:
        f.write('"""Evaluadores deterministas (identidad, geografía, relevancia, VERIFIED GATES, selección). No planifican: juzgan evidencia."""\n')
    for m in PLANNER_MODS + EVAL_MODS:
        copy_py(os.path.join(S, f"research_planner/{m}.py"), os.path.join(PRODUCT, f"oi/engine/{PKG_OF[m]}/{m}.py"), PKG_OF[m])
    # ── executor ──
    for d, _, fs in os.walk(os.path.join(S, "executor")):
        if "__pycache__" in d: continue
        for f in fs:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(d, f), os.path.join(S, "executor"))
                copy_py(os.path.join(d, f), os.path.join(PRODUCT, "oi/engine/executor", rel), "executor")
    # ── datos versionados ──
    for o, n in DATA_MAP.items():
        dst = os.path.join(PRODUCT, n); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(os.path.join(S, o), dst)
    shutil.copytree(os.path.join(S, "planner_data/archive_v1"), os.path.join(PRODUCT, "archive/planner_data_v1"))
    shutil.copytree(os.path.join(S, "fixtures/drafts"), os.path.join(PRODUCT, "tests/fixtures/drafts"))
    shutil.copytree(os.path.join(S, "docs"), os.path.join(PRODUCT, "docs/engine_history"))
    # ── patches de los 4 cargadores (rutas) ──
    def edit(rel, fn):
        p = os.path.join(PRODUCT, rel); t = open(p, encoding="utf-8").read(); t = fn(t); open(p, "w", encoding="utf-8").write(t)
    def reg(t):
        t = patch_once(t, 'ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nDATA = os.path.join(ROOT, "data")\n', 'from oi import paths\n', "registry.root")
        t = patch_once(t, '    with open(os.path.join(DATA, name), encoding="utf-8") as f:', '    with open(paths.data_file(name), encoding="utf-8") as f:', "registry.load")
        t = patch_once(t, '        for n in sorted(os.listdir(DATA)):\n            with open(os.path.join(DATA, n), "rb") as f:',
                       '        for n in paths.SPEC_DATA_FILES:\n            with open(paths.data_file(n), "rb") as f:', "registry.hashes")
        return t
    edit("oi/engine/spec/registry.py", reg)
    edit("oi/engine/planner/config.py", lambda t: patch_once(patch_once(t,
        'ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nDATA = os.path.join(ROOT, "planner_data")\n', 'from oi import paths\n', "config.root"),
        '    with open(os.path.join(DATA, n), encoding="utf-8") as f:', '    with open(paths.data_file(n), encoding="utf-8") as f:', "config.load"))
    edit("oi/engine/planner/capabilities.py", lambda t: patch_once(t,
        '_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "planner_data", "source_classes.v1.json")',
        'from oi import paths\n_P = paths.data_file("source_classes.v1.json")', "capabilities"))
    edit("oi/engine/evaluators/evaluation.py", lambda t: patch_once(t,
        'json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "geo_catalog.v1.json"), encoding="utf-8"))',
        'json.load(open(__import__("oi.paths", fromlist=["paths"]).data_file("geo_catalog.v1.json"), encoding="utf-8"))', "evaluation.geo"))
    # ── tests de paridad (la suite genérica de 180 tests) ──
    for t in ("harness.py", "test_intent_engine.py", "test_planner.py", "test_executor.py", "test_identity_contract.py"):
        copy_py(os.path.join(S, "tests", t), os.path.join(PRODUCT, "tests/legacy_parity", t), None)
    P = lambda rel: os.path.join(PRODUCT, "tests/legacy_parity", rel)
    OLDROOT = 'os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "%s")'
    NEW = 'os.path.join(os.environ["OI_PRODUCT_ROOT"], %s)'
    def ed(rel, old, new):
        t = open(P(rel), encoding="utf-8").read(); t = patch_once(t, old, new, rel); open(P(rel), "w", encoding="utf-8").write(t)
    ed("test_intent_engine.py", OLDROOT % "intent_engine", NEW % '"oi", "engine", "spec"')
    ed("test_planner.py", OLDROOT % "research_planner", NEW % '"oi", "engine", "planner"')
    ed("test_executor.py", OLDROOT % "executor", NEW % '"oi", "engine", "executor"')
    # fixture de preflight (30 líneas de experimento 3B-2 que 3 tests usan como caso sintético): se copia como fixture de test, no como motor
    copy_py(os.path.join(S, "experiments/exp_3b2.py"), P("preflight_fixture.py"), None)
    t = open(P("preflight_fixture.py"), encoding="utf-8").read()
    t = patch_once(t, 'ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))', 'ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/', "fixture.root")
    open(P("preflight_fixture.py"), "w", encoding="utf-8").write(t)
    ed("test_executor.py", "from experiments.exp_3b2 import", "from preflight_fixture import")
    after = tree_hash(S)
    assert before == after, "EL ÁRBOL ORIGEN CAMBIÓ — migración abortada"
    json.dump({"source": S, "source_files_hashed": len(after), "source_tree_sha256": hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest(),
               "note": "Estado del origen (motor vivo) en el momento de la copia. El origen NO se modifica."}, open(marker, "w"), indent=1)
    print(f"OK migración: origen intacto ({len(after)} archivos hasheados, sin cambios)")


if __name__ == "__main__":
    main()
