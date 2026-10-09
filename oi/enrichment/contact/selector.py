"""Selección DETERMINISTA del contacto recomendado. Aplica un perfil de rules/contact_profiles.v1.json a las personas/canales/evidencias ya reunidas.

Qué NO hace: no busca datos, no llama a ninguna herramienta, no infiere emails ni cargos, no usa reloj (la fecha de referencia `today` es un parámetro).
Orden lexicográfico (sin puntuación numérica): filtros duros -> peldaño del perfil -> reachability -> evidence_strength -> freshness -> orden de hallazgo.
"""
import copy
import datetime

from .profiles import ProfileError, select_profile

STRENGTH_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
REACH_RANK = {"DIRECT": 0, "INDIRECT": 1, "NONE": 2}
FRESH_RANK = {"CURRENT": 0, "UNKNOWN": 1, "STALE": 2}
KIND_PREF = {"EMAIL": 0, "PHONE": 1, "FORM": 2, "PORTAL": 3, "LINKEDIN": 4, "POSTAL": 5}
PURPOSE_FAMILY = {"PROCUREMENT": "PROCUREMENT", "COMMERCIAL": "COMMERCIAL", "GENERAL": "GENERAL"}


def norm_value(v):
    v = (v or "").strip().lower()
    for p in ("mailto:", "tel:"):
        if v.startswith(p): v = v[len(p):]
    return "".join(v.split())


def _age_days(d, today):
    if not d: return None
    return (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(d)).days


def _worst(vals, rank):
    return max(vals, key=lambda v: rank[v])


def derive_grade(quality, grading):
    for r in grading["rules"]:
        if all(quality.get(k) in v for k, v in r["when"].items()): return r["grade"]
    return grading["default"]


def usable_evidence(ev, hard):
    return ev["claim_status"] in hard["allowed_claim_status"] and ev["source_class"] not in hard["disallowed_source_classes"]


def channel_support(ch, ev_idx, hard):
    """Evidencias que SOSTIENEN un canal: existen, son usables y su valor coincide con el del canal (así no se puede 'inventar' un email)."""
    out = []
    for eid in ch["evidence_ids"]:
        e = ev_idx.get(eid)
        if e and usable_evidence(e, hard) and norm_value(e["value"]) == norm_value(ch["value"]): out.append(e)
    return out


def person_support(p, ev_idx, hard):
    return [e for eid in p["role_evidence_ids"] for e in [ev_idx.get(eid)] if e and usable_evidence(e, hard)]


def usable_purpose(purpose, hard):
    return purpose not in hard["excluded_channel_purposes"]


def _best(evs):
    return min((e["evidence_strength"] for e in evs), key=lambda s: STRENGTH_RANK[s]) if evs else "UNKNOWN"


def _fresh(date, days_max, today):
    a = _age_days(date, today)
    if a is None: return "UNKNOWN"
    return "CURRENT" if a <= days_max else "STALE"


def _expand(vals, fn_families):
    out = []
    for v in vals: out += fn_families if v == "$FUNCTION" else [v]
    return out


def _match(rung, cand, fn_families, small_ok):
    if rung.get("requires_small_company") and not small_ok: return False
    for key, vals in rung["match"].items():
        a = cand.get(key)
        if a is None or a not in _expand(vals, fn_families): return False
    return True


def build_contact(base, profile_doc, today, employees=None, target_function=None):
    """Devuelve una COPIA de `base` con: contact_profile_id, selection_blocked, recommended_contact, alternatives, not_recommended, role_contacts, usable_for_outreach, last_verified.
    employees: {"value": int, "claim_status": "KNOWN"} o None. Solo si es KNOWN y <= umbral se considera empresa pequeña (la plantilla UNKNOWN no reordena nada)."""
    c = copy.deepcopy(base); hard = profile_doc["hard_filters"]; fp = profile_doc["freshness_policy"]
    ev_idx = {e["evidence_id"]: e for e in c["evidence"]}
    for ch in c["channels"]: ch["usable_for_outreach"] = usable_purpose(ch["purpose"], hard)
    c["last_verified"] = max((e["retrieved_at"] for e in c["evidence"] if e["source_class"] != "NOT_FOUND"), default=None)
    c["role_contacts"] = _role_contacts(c)
    c["selection_blocked"] = None; c["not_recommended"] = []; c["alternatives"] = []; c["recommended_contact"] = None
    try:
        pid = select_profile(profile_doc, c["opportunity_type"], c["offering_kind"])
    except ProfileError as e:
        c["contact_profile_id"] = None; c["selection_blocked"] = {"code": e.code, "message": e.message}
        return c
    c["contact_profile_id"] = pid; prof = profile_doc["profiles"][pid]; ladder = prof["ladder"]
    tf = target_function or c.get("target_function") or "UNKNOWN"
    ftr = prof.get("function_to_role_family")
    if ftr is None: fn_families, fn_unknown = [], False                      # el perfil no depende de la función
    elif tf in ftr: fn_families, fn_unknown = ftr[tf], False
    else: fn_families, fn_unknown = prof["unknown_function_role_families"], True   # UNKNOWN (o fuera de catálogo): no se inventa la función
    small_ok = bool(employees and employees.get("claim_status") == "KNOWN" and isinstance(employees.get("value"), int) and employees["value"] <= profile_doc["small_company"]["max_employees"])
    nr = c["not_recommended"]

    # candidatos --------------------------------------------------------------------------------------------------------------------------
    chan_cands = []
    for i, ch in enumerate(c["channels"]):
        if ch["scope"] == "PERSON": continue
        if not usable_purpose(ch["purpose"], hard): nr.append({"ref": ch["channel_id"], "reason": f"propósito {ch['purpose']}: no es un canal de prospección comercial"}); continue
        sup = channel_support(ch, ev_idx, hard)
        if not sup: nr.append({"ref": ch["channel_id"], "reason": "sin evidencia verificable que sostenga el valor del canal (no se recomienda)"}); continue
        rf = ch.get("role_family") if ch["scope"] == "DEPARTMENT" else PURPOSE_FAMILY.get(ch["purpose"])
        chan_cands.append({"target": ch["scope"], "ref": ch["channel_id"], "role_family": rf, "seniority": None, "channel_kind": ch["kind"], "channel_purpose": ch["purpose"],
                           "ch": ch, "support": sup, "order": i})
    for cand in chan_cands: _rank(cand, ladder, fn_families, small_ok)
    viable_chan = sorted([x for x in chan_cands if x["rung"] is not None], key=lambda x: (x["rung_idx"], KIND_PREF[x["ch"]["kind"]], x["order"]))
    for x in chan_cands:
        if x["rung"] is None: nr.append({"ref": x["ref"], "reason": "no encaja en ningún peldaño del perfil " + pid})

    person_cands = []
    for i, p in enumerate(c["people"]):
        sup = person_support(p, ev_idx, hard)
        if not sup: nr.append({"ref": p["person_id"], "reason": "el cargo/rol no tiene evidencia verificable (no se recomienda)"}); continue
        cand = {"target": "PERSON", "ref": p["person_id"], "role_family": p["role_family"], "seniority": p.get("seniority"), "channel_kind": None, "channel_purpose": None,
                "p": p, "support": sup, "order": i}
        _rank(cand, ladder, fn_families, small_ok)
        if cand["rung"] is None: nr.append({"ref": p["person_id"], "reason": "su rol no encaja en ningún peldaño del perfil " + pid}); continue
        person_cands.append(cand)

    # vía y calidad -----------------------------------------------------------------------------------------------------------------------
    final = []
    for cand in person_cands:
        p = cand["p"]; own = [ch for ch in c["channels"] if ch["scope"] == "PERSON" and ch.get("person_id") == p["person_id"] and usable_purpose(ch["purpose"], hard) and channel_support(ch, ev_idx, hard)]
        own.sort(key=lambda ch: KIND_PREF[ch["kind"]])
        pfresh = _fresh(p.get("employment_verified_at") or max((e["retrieved_at"] for e in cand["support"]), default=None), fp["person_max_age_days"], today)
        if own:
            via = own[0]; vsup = channel_support(via, ev_idx, hard); reach = "DIRECT"
        elif viable_chan:
            vc = viable_chan[0]; via = vc["ch"]; vsup = vc["support"]; reach = "INDIRECT"
        else:
            via = None; vsup = []; reach = "NONE"
        vfresh = _fresh(max((e["retrieved_at"] for e in vsup), default=None), fp["channel_max_age_days"], today) if via else pfresh
        strength = _worst([_best(cand["support"])] + ([_best(vsup)] if via else []), STRENGTH_RANK)
        final.append(_pack(cand, via, reach, strength, _worst([pfresh, vfresh], FRESH_RANK), prof, fn_unknown, ev_idx, profile_doc))
    for cand in viable_chan:
        ch = cand["ch"]; sup = cand["support"]; fr = _fresh(max(e["retrieved_at"] for e in sup), fp["channel_max_age_days"], today)
        best = _best(sup); top = min(sup, key=lambda e: STRENGTH_RANK[e["evidence_strength"]])
        specific = ch["scope"] == "DEPARTMENT" or (ch["kind"] in ("PORTAL", "FORM") and cand["rung"]["fit"] == "EXACT" and top["source_class"] == "OFFICIAL" and best == "HIGH" and ch.get("outreach_access") == "OPEN")
        final.append(_pack(cand, ch, "DIRECT" if specific else "INDIRECT", best, fr, prof, fn_unknown, ev_idx, profile_doc))

    actionable = [x for x in final if x["contact_quality"]["reachability"] != "NONE"]
    for x in final:
        if x["contact_quality"]["reachability"] == "NONE":
            nr.append({"ref": x["ref"], "reason": "persona identificada pero sin canal accionable: no hay a dónde dirigirse (se conserva el nombre en 'people')"})
    key = lambda x: (x["_rung_idx"], REACH_RANK[x["contact_quality"]["reachability"]], STRENGTH_RANK[x["contact_quality"]["evidence_strength"]], FRESH_RANK[x["contact_quality"]["freshness"]], x["_order"])
    fresh_ok = sorted([x for x in actionable if x["contact_quality"]["freshness"] != "STALE"], key=key)
    stale = sorted([x for x in actionable if x["contact_quality"]["freshness"] == "STALE"], key=key)
    ordered = fresh_ok + stale
    clean = lambda x: {k: v for k, v in x.items() if not k.startswith("_")}
    if fresh_ok: c["recommended_contact"] = clean(fresh_ok[0]); rest = ordered[1:]
    else: rest = ordered
    seen = {c["recommended_contact"]["ref"]} if c["recommended_contact"] else set()
    for x in rest:
        if x["ref"] in seen or len(c["alternatives"]) >= 3: continue
        seen.add(x["ref"]); c["alternatives"].append(clean(x))
    return c


def _rank(cand, ladder, fn_families, small_ok):
    cand["rung"] = None; cand["rung_idx"] = None
    for i, r in enumerate(ladder):
        if _match(r, cand, fn_families, small_ok): cand["rung"] = r; cand["rung_idx"] = i; return


def _pack(cand, via, reach, strength, fresh, prof, fn_unknown, ev_idx, profile_doc):
    r = cand["rung"]; q = {"target_fit": r["fit"], "reachability": reach, "evidence_strength": strength, "freshness": fresh}
    q["grade"] = derive_grade(q, profile_doc["grading"])
    if cand["target"] == "PERSON":
        p = cand["p"]; who = f"{p['name']} ({p['title']})"
        if via is None: how = "no hay canal accionable"
        elif reach == "DIRECT": how = f"su propio canal {via['kind']} {via['value']}"
        else: how = f"el canal {via['kind']} de empresa {via['value']} (propósito {via['purpose']}) pidiendo por esa persona"
        next_action = (f"Contactar con {p['name']} a través de {via['kind']} {via['value']}." if reach == "DIRECT" else
                       f"Usar {via['kind']} {via['value']} y pedir por {p['name']} ({p['title']})." if via else "Buscar un canal verificable de esta persona.")
    else:
        ch = cand["ch"]; who = f"{r['label']} — canal {ch['kind']} {ch['value']}"; how = f"el propio canal ({ch['purpose']})"
        next_action = f"Escribir/contactar por {ch['kind']} {ch['value']} ({ch['purpose'].lower()})."
    why = f"{r['label']}: {who}; vía {how}."
    if fn_unknown and "$FUNCTION" in str(r["match"]): why += " La función a la que se dirige la oferta es UNKNOWN: se aceptó cualquiera de las familias de rol por defecto del perfil."
    return {"target": cand["target"], "ref": cand["ref"], "via_channel_id": via["channel_id"] if via else None, "ladder_rung": r["id"], "why_this_contact": why, "next_action": next_action,
            "contact_quality": q, "_rung_idx": cand["rung_idx"], "_order": cand["order"]}


def _role_contacts(c):
    out = {}
    for p in c["people"]:
        e = out.setdefault(p["role_family"], {"role_family": p["role_family"], "person_ids": [], "channel_ids": []}); e["person_ids"].append(p["person_id"])
        e["channel_ids"] += [x for x in p["channel_ids"] if x not in e["channel_ids"]]
    for ch in c["channels"]:
        rf = ch.get("role_family") if ch["scope"] == "DEPARTMENT" else None
        if rf:
            e = out.setdefault(rf, {"role_family": rf, "person_ids": [], "channel_ids": []})
            if ch["channel_id"] not in e["channel_ids"]: e["channel_ids"].append(ch["channel_id"])
    return [out[k] for k in sorted(out)]
