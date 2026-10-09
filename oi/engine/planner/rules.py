"""Evaluador de reglas declarativas (ruleset.v2.json). Cada decisión devuelve (cumple, criterios que la disparan).
Selección de herramientas con DISPONIBILIDAD: una herramienta unavailable_on_current_plan se rechaza con motivo y entra su fallback."""
import fnmatch
_KEYS = ("field", "fields", "field_prefix", "stage", "strength", "dimension", "pre_enrichment_gate", "verified_gate", "discovery_filter", "evidence_source")


def _match(c, w):
    for k, v in w.items():
        if k not in _KEYS: raise ValueError(f"clave de condición desconocida: {k}")
        if k == "field" and c["field"] != v: return False
        if k == "fields" and c["field"] not in v: return False
        if k == "field_prefix" and not c["field"].startswith(v): return False
        if k in ("stage", "strength", "dimension", "evidence_source") and c[k] != v: return False
        if k in ("pre_enrichment_gate", "verified_gate", "discovery_filter") and c[k] != v: return False
    return True


def _path(obj, path):
    for p in path.split("."):
        obj = obj.get(p) if isinstance(obj, dict) else None
    return obj


def eval_cond(cond, ctx):
    if "always" in cond: return True, []
    if "crit" in cond:
        ids = [c["id"] for c in ctx["active"] if _match(c, cond["crit"])]
        return bool(ids), ids
    if "geo" in cond: return ctx["has_geo"], (["GEO"] if ctx["has_geo"] else [])
    if "spec" in cond: return _path(ctx["spec"], cond["spec"]["path"]) in cond["spec"]["in"], []
    if "flag" in cond: return bool(ctx["flags"].get(cond["flag"])), []
    if "not" in cond: return (not eval_cond(cond["not"], ctx)[0]), []
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        res = [eval_cond(x, ctx) for x in cond[key]]
        ok = all(r[0] for r in res) if key == "all" else any(r[0] for r in res)
        return ok, sorted({i for r in res if r[0] for i in r[1]}) if ok else []
    raise ValueError(f"condición desconocida: {list(cond)}")


def phase_inclusion(cfg, ctx):
    out = {}
    for r in cfg.ruleset["phase_rules"]:
        ok, ids = eval_cond(r["include_when"], ctx)
        out[r["phase"]] = {"included": ok, "rule_id": r["rule_id"], "matched": ids, "description": r["description"], "skip_reason": None if ok else r["skip_reason"]}
    return out


def tool_selection(cfg, ctx, included):
    out = {p: {"selected": [], "rejected": []} for p in included}
    by_id = {r["rule_id"]: r for r in cfg.ruleset["tool_rules"]}
    for r in cfg.ruleset["tool_rules"]:
        phases = list(included) if r["phase"] == "ALL" else [r["phase"]]
        for p in phases:
            if not included.get(p, {}).get("included"):
                if r["phase"] != "ALL":
                    out.setdefault(p, {"selected": [], "rejected": []})["rejected"].append({"tool": r["tool"], "rule_id": r["rule_id"], "reason": f"fase {p} no incluida"})
                continue
            if r["phase"] == "ALL" and p != "RESULTS": continue   # infra: una selección por plan, anclada en RESULTS
            ok, ids = eval_cond(r["when"], ctx)
            tool, status = r["tool"], cfg.tool_status(r["tool"])
            if ok and any(fnmatch.fnmatch(tool, pat) for pat in ctx.get("disabled_tools", [])):
                out[p]["rejected"].append({"tool": tool, "rule_id": r["rule_id"], "reason": "disabled_by_run_policy", "triggering_criteria": ids}); continue
            rej = lambda reason, **x: out[p]["rejected"].append({"tool": tool, "rule_id": r["rule_id"], "reason": reason, **x})
            if r.get("fallback_of"):
                prim = by_id[r["fallback_of"]]; pstat = cfg.tool_status(prim["tool"])
                if ok and pstat == "unavailable_on_current_plan" and status != "unavailable_on_current_plan":
                    out[p]["selected"].append({"rule_id": r["rule_id"], "reason": r["reason"], "triggering_criteria": ids, "selected_tool": tool, "operations": r["operations"],
                                               "placement_note": r.get("placement_note"), "fallback_for": prim["tool"], "tool_status": status})
                else:
                    rej(r["not_triggered_reason"] if pstat == "available" or not ok else "el fallback tampoco está disponible")
                continue
            if ok and status == "unavailable_on_current_plan":
                rej("unavailable_on_current_plan: " + cfg.tools[tool].get("reason", ""), triggering_criteria=ids, fallback=cfg.tools[tool].get("fallback")); continue
            if ok:
                out[p]["selected"].append({"rule_id": r["rule_id"], "reason": r["reason"], "triggering_criteria": ids, "selected_tool": tool, "operations": r["operations"],
                                           "placement_note": r.get("placement_note"), "fallback_for": None, "tool_status": status})
            else:
                rej(r["not_triggered_reason"])
    for p, d in out.items():  # herramientas del catálogo permitidas en la fase pero sin selección
        sel = {s["selected_tool"] for s in d["selected"]}
        for t, td in cfg.tools.items():
            if p in td["phases"] and t not in sel and t not in {x["tool"] for x in d["rejected"]}:
                d["rejected"].append({"tool": t, "rule_id": "TP-05" if p == "DISCOVERY" else "NO_RULE", "reason": "ninguna regla selecciona esta herramienta en esta fase"})
        for pr in cfg.ruleset["tool_prohibitions"]:
            for t in pr.get("tools", []):
                if p in pr.get("phases", []) and t not in sel and t not in {x["tool"] for x in d["rejected"]}:
                    d["rejected"].append({"tool": t, "rule_id": pr["rule_id"], "reason": pr["reason"]})
    return out
