"""D010 · motor determinista del modelo de decisión (D010-v0.2 OFFICIAL). Función pura: sin red, reloj, azar ni dependencia del orden de diccionarios.
evaluate_candidate(candidate, evidence, rules) -> dict · build_ranking(results) -> ranking OFICIAL (capas de Pareto del floor) + diagnósticos secundarios.
El score NO es una probabilidad, NO es valor económico y NO es una decisión comercial automática.

LÓGICA separada de CONFIGURACIÓN: todas las reglas (unidades, pesos, suficiencia, orden de elegibilidad, gates) se cargan de rules.json y se validan contra schema.json.
Este módulo no contiene IDs de señales de ningún caso: las unidades son datos. Para cambiar reglas se versiona rules.json; no se duplica el motor."""
import hashlib, json, os

from oi.engine.spec import mini_jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "rules.json")
SCHEMA_PATH = os.path.join(HERE, "schema.json")


class RulesError(Exception):
    pass


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_rules(path=None, schema_path=None):
    """Carga y VALIDA un conjunto de reglas. Las claves '_*' son documentación y se descartan (no afectan a la evaluación)."""
    with open(path or RULES_PATH, encoding="utf-8") as f: raw = json.load(f)
    with open(schema_path or SCHEMA_PATH, encoding="utf-8") as f: schema = json.load(f)
    errs = mini_jsonschema.validate(raw, schema)
    rules = {k: v for k, v in raw.items() if not k.startswith("_")}
    errs = [f"{e['path']}: {e['message']}" for e in errs]
    unit_ids = set(rules.get("units", {}))
    if set(rules.get("unit_order", [])) != unit_ids or len(rules.get("unit_order", [])) != len(unit_ids):
        errs.append("unit_order debe listar exactamente una vez cada unidad de 'units'")
    for uid, u in rules.get("units", {}).items():
        if u.get("class") not in rules.get("weights", {}): errs.append(f"unidad {uid}: clase {u.get('class')!r} sin peso")
    if rules.get("coverage", {}).get("potential_units") != len(unit_ids): errs.append("coverage.potential_units != nº de unidades")
    if errs: raise RulesError("; ".join(errs))
    return rules


def rules_hash(rules):
    """Huella de contenido de las reglas efectivas (sin documentación)."""
    return hashlib.sha256(_canon({k: v for k, v in rules.items() if not k.startswith("_")}).encode()).hexdigest()


RULES = load_rules()
VERSION = RULES["score_version"]; STATUS = RULES["status"]; LABEL = RULES["label"]
STATES = ("PRESENT", "NOT_OBSERVED", "UNKNOWN", "CONFLICTING"); ELIG_RANK = {s: i for i, s in enumerate(RULES["eligibility_order"])}; BAND_RANK = {"STRONG": 3, "MODERATE": 2, "LOW_ONLY": 1, "NONE": 0}


def evaluate_candidate(candidate, evidence, rules=RULES):
    R = rules; W = R["weights"]; S = R["sufficiency"]; order = R["unit_order"]; cid = candidate["candidate_id"]
    idx = {(e["candidate_id"], e["claim"]): e for e in evidence}; ident = candidate.get("identity_status", "UNKNOWN"); gates = candidate.get("gates", {})
    gstat = {g: gates.get(g, "UNKNOWN") for g in R["hard_gates"]}                  # fit_minimal: SOLO cumplimiento mínimo de gates (no afinidad, no calidad del prospecto)
    if ident == "KNOWN_MISMATCH" or "FAIL" in gstat.values(): fm = "FAIL"
    elif ident != R["identity_required"] or "UNKNOWN" in gstat.values(): fm = "UNKNOWN"
    else: fm = "PASS"
    fit_minimal = {"status": fm, "identity": ident, "gates": gstat, "gives_points": False, "meaning": "cumplimiento mínimo de los gates; NO es afinidad comercial ni calidad del prospecto", "evidence_ids": sorted(candidate.get("gate_evidence_ids", []))}
    sig = candidate.get("signals", {}); over = candidate.get("unit_evidence", {}); seen, dedup, trace = set(), [], []
    for uid in order:                                                           # orden FIJO
        spec = R["units"][uid]; st = sig.get(uid, "UNKNOWN")
        if st not in STATES: raise ValueError(f"estado inválido {st} en {uid}")
        e = idx.get((cid, spec["evidence_claim"])) if spec["evidence_claim"] else None; eid = e["evidence_id"] if e and st == "PRESENT" else None; conf = e["confidence"] if e and st in ("PRESENT", "CONFLICTING") else "none"
        if uid in over: eid = over[uid].get("evidence_id", eid); conf = over[uid].get("confidence", conf)
        wt = W[spec["class"]]; eff = st
        if st == "PRESENT" and eid is not None:
            if eid in seen: eff = "DUPLICATE"; dedup.append(eid)
            else: seen.add(eid)
        lo, hi = {"PRESENT": (wt, wt), "NOT_OBSERVED": (0, 0), "DUPLICATE": (0, 0)}.get(eff, (0, wt))
        trace.append({"unit": uid, "dimension": spec["dimension"], "class": spec["class"], "weight": wt, "input_state": st, "effective_state": eff, "confidence": conf, "evidence_id": eid, "lower": lo, "upper": hi})
    present = [t for t in trace if t["effective_state"] == "PRESENT"]; unres = [t for t in trace if t["effective_state"] in ("UNKNOWN", "CONFLICTING")]; resolved = [t for t in trace if t["effective_state"] in ("PRESENT", "NOT_OBSERVED", "DUPLICATE")]
    dsum = lambda dim, k: sum(t[k] for t in trace if t["dimension"] == dim)
    fa = [dsum("FIT_AFFINITY", "lower"), dsum("FIT_AFFINITY", "upper")]; nd = [dsum("NEED", "lower"), dsum("NEED", "upper")]; tot = [fa[0] + nd[0], fa[1] + nd[1]]
    up = len(present); cp = sum(1 for t in present if t["class"] == "CORE"); high_core = any(t["class"] == "CORE" and t["confidence"] == S["high_confidence"] for t in present)
    floor_ok = (up >= S["min_units"] and cp >= S["min_core_units"]) or (S["allow_single_high_core"] and high_core)
    poss, cposs = up + len(unres), cp + sum(1 for t in unres if t["class"] == "CORE")
    ceil_ok = (poss >= S["min_units"] and cposs >= S["min_core_units"]) or (S["allow_single_high_core"] and (high_core or any(t["class"] == "CORE" for t in unres)))
    sufficiency = {"met": bool(floor_ok), "reachable_if_unknowns_resolve_positive": bool(ceil_ok), "units_present": up, "core_present": cp, "weak_present": up - cp, "single_high_core": bool(high_core), "unresolved_units": [t["unit"] for t in unres],
                   "rule": "KNOWN_MATCH ∧ gates sin FAIL ∧ [(>=2 unidades PRESENT ∧ >=1 CORE) ∨ (1 CORE PRESENT con confianza HIGH)]; WEAK (O4/O5) nunca basta solo; UNKNOWN no cuenta como PRESENT; CONFLICTING no cuenta"}
    if fm == "FAIL": el, why = "NOT_ELIGIBLE", "identidad en mismatch o gate HARD en FAIL"
    elif fm == "UNKNOWN": el, why = "UNKNOWN", "identidad o gate HARD sin resolver (bloquea elegibilidad y score)"
    elif floor_ok: el, why = "ELIGIBLE", f"suficiencia demostrada: {up} unidades PRESENT ({cp} CORE)" + (", con una evidencia CORE de confianza HIGH" if high_core and not (up >= S["min_units"] and cp >= S["min_core_units"]) else "")
    elif ceil_ok: el, why = "PENDING_EVIDENCE", f"suficiencia no demostrada ({up} unidades, {cp} CORE) pero alcanzable si se resuelven: {[t['unit'] for t in unres]}"
    else: el, why = "NOT_ELIGIBLE", "suficiencia inalcanzable aunque todo lo UNKNOWN/CONFLICTING se resolviera positivo"
    confs = [t["confidence"] for t in present]; band = "NONE" if not present else ("STRONG" if "high" in confs else ("MODERATE" if "medium" in confs else "LOW_ONLY"))
    quals = sorted([f"{t['unit']}_CONFLICT" for t in trace if t["effective_state"] == "CONFLICTING"] + (["LOW_ONLY_STRENGTH"] if band == "LOW_ONLY" else []))
    potential = len(order); obs = len(resolved); cov = obs / potential
    if fm == "PASS": sc = {"status": "DEFINED_INTERVAL", "score_version": VERSION, "fit_affinity": fa, "need": nd, "total": tot, "total_range": R["score_range"]["total"], "total_informational_only": True, "interval_width": {"fit_affinity": fa[1] - fa[0], "need": nd[1] - nd[0], "total": tot[1] - tot[0]},
                          "floor": {"fit_affinity": fa[0], "need": nd[0], "total": tot[0]}, "ceiling": {"fit_affinity": fa[1], "need": nd[1], "total": tot[1]}, "not_probability": True, "not_economic_value": True, "weights_ordinal": True, "total_used_for_ranking": False}
    else: sc = {"status": "NOT_APPLICABLE_HARD_GATE_FAIL" if fm == "FAIL" else "BLOCKED_UNRESOLVED_HARD_GATE", "score_version": VERSION, "not_probability": True}
    return {"candidate_id": cid, "decision_model": LABEL, "fit_minimal": fit_minimal,
            "fit_affinity": {"states": {t["unit"]: t["effective_state"] for t in trace if t["dimension"] == "FIT_AFFINITY"}, "lower_bound": fa[0], "upper_bound": fa[1], "units_present": [t["unit"] for t in present if t["dimension"] == "FIT_AFFINITY"], "conflicting": [t["unit"] for t in trace if t["dimension"] == "FIT_AFFINITY" and t["effective_state"] == "CONFLICTING"]},
            "need": {"states": {t["unit"]: t["effective_state"] for t in trace if t["dimension"] == "NEED"}, "lower_bound": nd[0], "upper_bound": nd[1], "units_present": [t["unit"] for t in present if t["dimension"] == "NEED"], "weak_signals_alone_sufficient": False},
            "sufficiency": sufficiency, "eligibility": {"status": el, "reason": why, "qualifiers": quals}, "score_interval": sc, "confidence_band": {"band": band, "basis": "máxima confianza entre las unidades PRESENT contadas; metadato, no puntos, no probabilidad"},
            "coverage": {"coverage": round(cov, 6), "coverage_percent": round(100 * cov, 2), "observed_units": obs, "potential_units": potential, "unknown_units": len(unres), "unknown_unit_ids": [t["unit"] for t in unres], "gives_points": False},
            "deduplicated_evidence_ids": sorted(dedup), "unit_trace": trace, "conflicts": [t["unit"] for t in trace if t["effective_state"] == "CONFLICTING"], "unknown_components": [t["unit"] for t in trace if t["effective_state"] == "UNKNOWN"]}


# ---------------------------------------------------------------- RANKING OFICIAL (capas de Pareto del floor) + diagnósticos secundarios
def _floors(r): s = r["score_interval"]; return (s["floor"]["fit_affinity"], s["floor"]["need"]) if s["status"] == "DEFINED_INTERVAL" else (0, 0)
def _ceils(r): s = r["score_interval"]; return (s["ceiling"]["fit_affinity"], s["ceiling"]["need"]) if s["status"] == "DEFINED_INTERVAL" else (0, 0)


def dominates(a, b):
    """A domina a B: floor >= en TODAS las dimensiones relevantes y estrictamente > en al menos una."""
    fa, fb = _floors(a), _floors(b); return all(x >= y for x, y in zip(fa, fb)) and any(x > y for x, y in zip(fa, fb))


def robustly_dominates(a, b):
    """diagnóstico de robustez: el FLOOR de A >= el CEILING de B en todas las dimensiones y > en alguna (dominancia de intervalos)."""
    fa, cb = _floors(a), _ceils(b); return all(x >= y for x, y in zip(fa, cb)) and any(x > y for x, y in zip(fa, cb))


def pareto_layers(results):
    out = {}
    for st in RULES["eligibility_order"]:
        grp = {r["candidate_id"]: _floors(r) for r in results if r["eligibility"]["status"] == st}; layer, n = {}, 1
        while grp:
            nd = [i for i, v in grp.items() if not any(o != i and grp[o][0] >= v[0] and grp[o][1] >= v[1] and grp[o] != v for o in grp)]
            for i in nd: layer[i] = n; grp.pop(i)
            n += 1
        out.update(layer)
    return out


def sort_key(r, layer): return (ELIG_RANK[r["eligibility"]["status"]], layer, -BAND_RANK[r["confidence_band"]["band"]], -r["coverage"]["coverage"])


def build_ranking(results):
    ids = [r["candidate_id"] for r in results]; by = {r["candidate_id"]: r for r in results}; lay = pareto_layers(results); key = {i: sort_key(by[i], lay[i]) for i in ids}
    pos = {i: 1 + sum(1 for j in ids if key[j] < key[i]) for i in ids}; ties = {i: sorted(j for j in ids if j != i and key[j] == key[i]) for i in ids}
    # diagnósticos secundarios (NO son el ranking principal)
    dom = {i: sorted(j for j in ids if j != i and ELIG_RANK[by[i]["eligibility"]["status"]] == ELIG_RANK[by[j]["eligibility"]["status"]] and dominates(by[i], by[j])) for i in ids}
    inc = {i: sorted(j for j in ids if j != i and ELIG_RANK[by[i]["eligibility"]["status"]] == ELIG_RANK[by[j]["eligibility"]["status"]] and not dominates(by[i], by[j]) and not dominates(by[j], by[i]) and _floors(by[i]) != _floors(by[j])) for i in ids}
    rob = {i: sorted(j for j in ids if j != i and robustly_dominates(by[i], by[j])) for i in ids}
    tied_diff = sorted({tuple(sorted((i, j))) for i in ids for j in ties[i] if _floors(by[i]) != _floors(by[j])})     # empatados oficialmente pero con floors DISTINTOS (no dominados entre sí)
    forced = sorted({tuple(sorted((i, j))) for i in ids for j in inc[i] if key[i] != key[j]})          # pares incomparables por floor que el ranking oficial ordena por banda/cobertura
    return {"official": {"type": "layered_preorder", "candidates": {i: {"eligibility": by[i]["eligibility"]["status"], "pareto_layer": lay[i], "strength_band": by[i]["confidence_band"]["band"], "coverage": by[i]["coverage"]["coverage"], "rank_position": pos[i], "tied_with": ties[i]} for i in sorted(ids)},
                         "n_distinct_positions": len(set(pos.values())), "tie_groups": sum(1 for i in ids if ties[i]) , "tied_candidates": sum(1 for i in ids if ties[i])},
            "secondary_diagnostics": {"dominance_partial_order": {"dominates": dom, "incomparable_by_floor": inc, "incomparable_pairs": sum(len(v) for v in inc.values()) // 2, "ordered_pairs": sum(len(v) for v in dom.values()), "pairs_ordered_officially_by_band_or_coverage_but_incomparable_by_floor": len(forced), "pairs_tied_officially_with_different_floors": len(tied_diff)},
                                      "robust_interval_dominance": {"robustly_dominates": rob, "robust_ordered_pairs": sum(len(v) for v in rob.values()), "note": "diagnóstico de robustez; NO es el ranking principal"}}}
