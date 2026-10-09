#!/usr/bin/env python3
"""Genera tests/fixtures/d010_golden.json con el motor D010-v0.2 OFFICIAL ORIGINAL (solo lectura) para comprobar paridad sin depender de rutas externas.
Contenido: 30 candidatos del piloto de reventa (entradas del checkpoint 3G) + 600 candidatos sintéticos con semilla fija; resultados de evaluate_candidate y build_ranking."""
import json, os, random, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from oi.paths import HISTORY as O
OFF = os.path.join(O, "d010_v0_2_official")
sys.path.insert(0, OFF); import d010_engine as E
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C3G = json.load(open(O + "/experiment_3g/CHECKPOINT_3G.json")); EVR = json.load(open(O + "/experiment_3f/evidence_registry_3F.json"))
IDMAP = {"IDENTITY_CONFIRMED": "KNOWN_MATCH", "IDENTITY_MISMATCH": "KNOWN_MISMATCH", "IDENTITY_UNKNOWN": "UNKNOWN"}
rp = []
for x in C3G["candidate_results"]:
    sig = {"F1": x["fit"]["affinity"]["F1"]["state"], "FIT_KW": x["fit"]["affinity"]["FIT_KW"]["state"], **{s["signal_id"]: s["state"] for s in x["opportunity"]["signals"]}}
    rp.append({"candidate_id": x["candidate_id"], "identity_status": IDMAP[x["identity_status"]], "gates": {g["gate_id"]: g["status"] for g in x["fit"]["gate_results"] if g["gate_id"] in ("GEO", "EXCL")}, "gate_evidence_ids": [], "signals": sig})
rnd = random.Random(20261008); units = E.RULES["unit_order"]; syn = []
for i in range(600):
    st = {u: rnd.choice(E.STATES) for u in units}
    syn.append({"candidate_id": f"S{i:04d}", "identity_status": rnd.choice(["KNOWN_MATCH"] * 6 + ["KNOWN_MISMATCH", "UNKNOWN"]),
                "gates": {g: rnd.choice(["PASS"] * 6 + ["FAIL", "UNKNOWN"]) for g in E.RULES["hard_gates"]}, "signals": st,
                "unit_evidence": {u: {"evidence_id": f"E{rnd.randint(0, 9)}" if rnd.random() < .5 else f"E_{i}_{u}", "confidence": rnd.choice(["high", "medium", "low"])} for u in units}})
import hashlib
H = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
out = {"engine": E.LABEL, "evidence": EVR, "cases": {}}
for name, cs in (("reseller_pilot", rp), ("synthetic", syn)):
    res = [E.evaluate_candidate(c, EVR) for c in cs]
    out["cases"][name] = {"candidates": cs, "result_sha256": [H(r) for r in res], "ranking_sha256": H(E.build_ranking(res))}
json.dump(out, open(HERE + "/tests/fixtures/d010_golden.json", "w", encoding="utf-8"), ensure_ascii=False, sort_keys=True)
print("golden:", {k: len(v["candidates"]) for k, v in out["cases"].items()})
