"""Construcción de opportunity_record.v1 a partir de lo que PRODUCE el pipeline (executor + evidencia + cualificación + contacto). No usa plantillas por caso ni registros preparados.
Los textos (español) son del locale del caso; el record es un contrato genérico."""
from oi.engine.evaluators.evaluation import country_code
from oi.evidence import strength as ST
from oi.qualification import qualifier as Q
from oi.rules import next_action as NA

ES = {
    "elig": "Cumple todas las puertas de la búsqueda con evidencia KNOWN ({passed}).",
    "pend": "Candidata, pero pendiente de evidencia para: {pending}.",
    "notel": "Descartada: {reason}",
    "notass": "{reason}",
    "unk": "Sin información suficiente para evaluar las puertas.",
    "growth_present": "Señal de crecimiento presente: {value} % (estimado).",
    "growth_not": "No se observa el crecimiento pedido ({value} %).",
    "growth_unk": "No se sabe si crece: {why}",
    "disc": "Apareció en el descubrimiento de la consulta {q}.",
    "no_contact": "No hay un contacto recomendado: {why}",
    "level_note": "Revisión de duplicados/entidades pendiente: {why}",
    "search_note": "Lo que la búsqueda PIDIÓ (nivel de entidad, modelo de negocio, oferta). Se compara con lo hallado en company_identity.entity_level y company.business_model.",
}
BM_ENUM = {"manufacturer", "distributor", "retailer", "service_provider", "contractor", "operator", "unknown"}
LEVELS = {"ESTABLISHMENT", "OPERATOR", "GROUP", "COMPANY", "UNKNOWN"}


def _sig_from_gate(g, kind="GATE"):
    st, es = g["status"], g["evidence_status"]
    if st == "PASS" and es == "KNOWN" and g["evidence_ids"]: state, claim = "PRESENT", "KNOWN"
    elif st == "PASS": state, claim = "UNKNOWN", es if es in ("INFERRED", "ESTIMATED") else "UNKNOWN"
    elif st == "FAIL": state, claim = "NOT_OBSERVED", es if es in ("KNOWN", "INFERRED", "ESTIMATED") else "UNKNOWN"
    elif es == "CONFLICTING": state, claim = "CONFLICTING", "CONFLICTING"
    else: state, claim = "UNKNOWN", es if es in ("INFERRED", "UNKNOWN") else "UNKNOWN"
    return {"signal_id": g["gate_id"], "label": g["label"], "kind": kind, "state": state, "claim_status": claim, "evidence_ids": list(g["evidence_ids"]), "observed_at": None}


def growth_signal(crit, prov, prov_ev_id, producer_ev, today):
    """Señal de crecimiento (criterio 'preferred'): NO es puerta. Prefiere evidencia del productor (ESTIMATED/KNOWN) sobre el dato inferido del proveedor."""
    thr, op = crit.get("value"), crit.get("operator", "gte")
    cmp = {"gte": lambda a, b: a >= b, "gt": lambda a, b: a > b, "lte": lambda a, b: a <= b, "lt": lambda a, b: a < b}.get(op)
    label = crit.get("label") or "Crecimiento"
    base = {"signal_id": crit["id"], "label": label, "kind": "NEED", "observed_at": today}
    good = [e for e in producer_ev if e["claim_status"] in ("KNOWN", "ESTIMATED") and isinstance(e["value"], (int, float)) and not isinstance(e["value"], bool)]
    if good and cmp and isinstance(thr, (int, float)):
        e = good[0]; ok = cmp(e["value"], thr)
        return {**base, "state": "PRESENT" if ok else "NOT_OBSERVED", "claim_status": e["claim_status"], "value": e["value"], "evidence_ids": [e["evidence_id"]]}, e["value"], ok
    if prov and prov.get("verdict") in ("PASS", "FAIL") and prov_ev_id:
        pct = prov.get("value_pct"); ok = prov["verdict"] == "PASS"
        # un PASS inferido NO es PRESENT (el contrato prohíbe PRESENT con INFERRED): se declara como desconocido con el valor visible
        return {**base, "state": "UNKNOWN" if ok else "NOT_OBSERVED", "claim_status": prov.get("evidence_status") if prov.get("evidence_status") in ("INFERRED", "ESTIMATED", "KNOWN") else "INFERRED", "value": pct,
                "evidence_ids": [prov_ev_id]}, pct, (None if ok else False)
    return {**base, "state": "UNKNOWN", "claim_status": "UNKNOWN", "value": None, "evidence_ids": []}, None, None


def growth_signals_from_evidence(crits, items, ev_by_id, today, status=None):
    """Una señal por criterio `growth.signal` (S-<SIGNAL>), SIN fundirlas. PRESENT solo con evidencia KNOWN/ESTIMATED, fechada (dated_on) y dentro de la ventana del criterio (params.window_months).
    Ausencia, INFERRED, sin fecha o fuera de ventana -> UNKNOWN (nunca NOT_OBSERVED ni PASS). -> (señales, líneas de 'lo que no se sabe')"""
    import datetime
    sigs, unk = [], []
    for c in crits:
        sid, w = c["params"]["signal_id"], c["params"]["window_months"]; field = "company:growth_signal:" + sid
        mine = [i for i in items if i["field"] == field]; ok = None
        for i in mine:
            e = ev_by_id[i["evidence_id"]]; d = i.get("dated_on")
            if e["claim_status"] in ("KNOWN", "ESTIMATED") and i["value"] in (True, "true", "INCREASED", "PRESENT") and d:
                try: age = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(d)).days
                except ValueError: continue
                if 0 <= age <= round(w * 30.4375): ok = e; break
        base = {"signal_id": c["id"], "label": c["label"], "kind": "NEED", "observed_at": today if mine else None}
        if ok: sigs.append({**base, "state": "PRESENT", "claim_status": ok["claim_status"], "value": ok["value"] if not isinstance(ok["value"], bool) else None, "evidence_ids": [ok["evidence_id"]]})
        else:
            sigs.append({**base, "state": "UNKNOWN", "claim_status": "INFERRED" if any(ev_by_id[i["evidence_id"]]["claim_status"] == "INFERRED" for i in mine) else "UNKNOWN", "value": None, "evidence_ids": [i["evidence_id"] for i in mine]})
            st = (status or {}).get(sid)
            unk.append(f"Señal {sid} (ventana {w} meses): UNKNOWN" + (f" · {st['state']}: {st['why']}" if st else "") + ".")
    return sigs, unk


def provider_evidence(n, rec, details, today, label):
    """Evidencia que aporta el PROVEEDOR (descubrimiento + enrichment). Los estados salen del executor y no se promueven."""
    out, ids = [], {}
    def add(key, field, value, claim, note=None, dated=None):
        eid = f"{n}:prov:{key}"
        out.append({"evidence_id": eid, "field": field, "value": value, "source_class": "PROFESSIONAL_DIRECTORY", "source_url": None, "source_label": label, "retrieved_at": today,
                    "claim_status": claim, "evidence_strength": "UNKNOWN", "note": note, "producer_confidence": "UNKNOWN"}); ids[key] = eid
    if rec is None: return out, ids
    g1, geo, c1 = details.get("G1"), details.get("GEO"), details.get("C1")
    if g1: add("industry", "provider:industry", rec.get("industry"), g1.get("evidence_status") if g1.get("evidence_status") in ("INFERRED", "CONFLICTING", "UNKNOWN", "KNOWN") else "UNKNOWN", f"etiquetas: {sorted((g1.get('evidence') or {}).get('labels', []))}")
    if geo: add("country", "provider:country", rec.get("country"), geo.get("evidence_status") if geo.get("evidence_status") in ("KNOWN", "INFERRED", "CONFLICTING", "UNKNOWN") else "UNKNOWN", f"estado de ubicación: {geo.get('location_state')}")
    if c1 and isinstance(c1.get("value_pct"), (int, float)): add("growth", "provider:headcount_growth_12m_pct", c1["value_pct"], "INFERRED", "ventana de 365 días aproximada a 12 meses por el executor")
    if isinstance(rec.get("estimated_num_employees"), int): add("employees", "provider:employees", rec["estimated_num_employees"], "ESTIMATED", "estimación del proveedor")
    for e in out:   # fuerza por reglas (los estados NO KNOWN dan UNKNOWN; KNOWN sin fecha de un directorio da LOW)
        e["evidence_strength"] = ST.derive(e["source_class"], e["claim_status"], today=today)
        if e["claim_status"] == "KNOWN" and not e.get("source_label"): e["source_label"] = label
    return out, ids


def build(*, n, case, spec_ctx, cand, details, rec, gates, qual, producer_ev, contact, ev_items, prov_ev, prov_ids, growth, today, usage_note, provenance, rank, mapping_note, extra_signals=(), extra_unknowns=()):
    """Ensambla el registro. `growth` = (signal, value, ok). Devuelve el record (acción ya derivada por reglas)."""
    q = cand["requested"]; name = cand["name"]
    ent_g = next((g for g in gates if g["gate_id"] == "ENTITY_LEVEL"), None); bm_g = next((g for g in gates if g["gate_id"] == "BUSINESS_MODEL"), None)
    known = lambda field: sorted({str(e["value"]) for e in producer_ev if e["field"] == field and e["claim_status"] == "KNOWN" and e["value"] is not None})
    kl = [x for x in known("company:entity_level") if x in LEVELS]
    found_level = kl[0] if len(kl) == 1 else "UNKNOWN"
    found_bm = sorted(set(x for x in known("company:business_model") if x in BM_ENUM)) or ["unknown"]
    signals = [_sig_from_gate(g) for g in gates if g["gate_id"] != "IDENTITY"] + ([growth[0]] if growth else []) + list(extra_signals)
    present = [s for s in signals if s["state"] == "PRESENT"]
    pending = ", ".join(g["gate_id"] for g in gates if g["blocking"] and not (g["status"] == "PASS" and g["evidence_status"] == "KNOWN") and g["status"] != "FAIL")
    passed = ", ".join(g["gate_id"] for g in gates if g["blocking"])
    st = qual["status"]
    summ = {"ELIGIBLE": ES["elig"].format(passed=passed), "PENDING_EVIDENCE": ES["pend"].format(pending=pending), "NOT_ELIGIBLE": ES["notel"].format(reason=qual["reason"]),
            "NOT_ASSESSED": ES["notass"].format(reason=qual["reason"]), "UNKNOWN": ES["unk"]}[st]
    gs, gval, gok = growth or ({"state": "NA", "claim_status": "UNKNOWN"}, None, None)
    if gs["state"] == "PRESENT": summ += " " + ES["growth_present"].format(value=gval)
    elif gs["state"] == "NOT_OBSERVED": summ += " " + ES["growth_not"].format(value=gval)
    reasons = []
    if cand.get("discovery_query"): reasons.append({"text": ES["disc"].format(q=cand["discovery_query"]), "signal_id": None, "claim_status": "KNOWN", "evidence_ids": [f"{n}:disc"]})
    for s in present: reasons.append({"text": f"{s['label']}: confirmado" + (f" ({s['value']})" if s.get("value") is not None else ""), "signal_id": s["signal_id"], "claim_status": s["claim_status"], "evidence_ids": s["evidence_ids"]})
    if not reasons: reasons.append({"text": "Sin razones verificadas todavía.", "signal_id": None, "claim_status": "UNKNOWN", "evidence_ids": []})
    unknowns = [f"{g['label']}: {g['reason']}" for g in gates if g["blocking"] and not (g["status"] == "PASS" and g["evidence_status"] == "KNOWN") and g["status"] != "FAIL"]
    if gs["state"] in ("UNKNOWN",): unknowns.append(ES["growth_unk"].format(why="dato inferido o ausente" if gs["claim_status"] != "UNKNOWN" else "sin dato"))
    unknowns += list(extra_unknowns)
    rc = contact.get("recommended_contact")
    if not rc:
        why = "; ".join(x["reason"] for x in contact.get("not_recommended", [])[:2]) or ((contact.get("not_found") or ["no se ha buscado contacto"])[0])
        unknowns.append(ES["no_contact"].format(why=why))
    if cand.get("review_note"): unknowns.append(ES["level_note"].format(why=cand["review_note"]))
    contradictions = [{"field": g["gate_id"], "description": g["reason"], "state": "AMBIGUOUS", "evidence_ids": g["evidence_ids"]} for g in gates if g["evidence_status"] == "CONFLICTING"]
    emp = rec.get("estimated_num_employees") if rec else None
    region = rec.get("state") if rec else None
    company = {"name": name, "domain": cand.get("domain"), "website": (rec or {}).get("website_url"), "country": country_code((rec or {}).get("country")) if rec and country_code(rec.get("country")) else None,
               "location": {"city": (rec or {}).get("city"), "region": region, "country": (rec or {}).get("country"), "text": None} if rec else None, "description": (rec or {}).get("short_description"),
               "business_model": found_bm, "industry_label": (rec or {}).get("industry"),
               "size": ({"employees": emp, "employees_range": None, "claim_status": "ESTIMATED", "evidence_ids": [prov_ids["employees"]] if "employees" in prov_ids else [], "text": None} if isinstance(emp, int) else None)}
    ident_status = {"KNOWN_MATCH": "KNOWN_MATCH", "KNOWN_MISMATCH": "KNOWN_MISMATCH", "AMBIGUOUS": "AMBIGUOUS"}.get(cand.get("identity"), "UNKNOWN")
    par = ((rec or {}).get("ultimate_parent_organization") or {}).get("name")
    identity = {"status": ident_status, "entity_level": found_level, "keys": {k: v for k, v in (("domain", cand.get("domain")), ("provider_org_id", cand.get("provider_id"))) if v}, "parent_name": par,
                "evidence_ids": [e["evidence_id"] for e in producer_ev if e["field"] == "company:entity_level"], "note": cand.get("review_note")}
    disc = {"evidence_id": f"{n}:disc", "field": "discovery:appearance", "value": cand.get("discovery_query"), "source_class": "PROFESSIONAL_DIRECTORY", "source_url": None,
            "source_label": prov_ev_label(provenance), "retrieved_at": today, "claim_status": "KNOWN", "evidence_strength": "LOW", "note": "Apareció en los resultados de descubrimiento (una sola fuente).", "producer_confidence": "UNKNOWN",
            **({"evidence_origin": "APOLLO_FREE_LOOKUP"} if provenance["data_origin"] == "REAL_PROVIDER" else {})}
    evid, seen = [], set()
    for e in [disc] + prov_ev + ev_items + list(contact.get("evidence", [])):
        if e["evidence_id"] not in seen: seen.add(e["evidence_id"]); evid.append(e)
    rec_out = {
        "schema_version": "opportunity_record.v1", "record_id": f"{case['case_id']}:{n}", "case_id": case["case_id"], "company": company, "company_identity": identity,
        "opportunity_type": case["opportunity_type"], "offering": {"kind": q["offering_kind"], "target_function": q["target_function"], "description": q.get("offering_description")},
        "search_context": {"requested_entity_level": q["entity_level"], "requested_business_model": q["business_model"], "requested_offering_kind": q["offering_kind"],
                           "spec_id": spec_ctx["spec_id"], "spec_version": spec_ctx["spec_version"], "note": ES["search_note"] + mapping_note},
        "why_opportunity": {"summary": summ, "reasons": reasons, "unknowns": unknowns}, "signals": signals,
        "qualification": {"status": st, "reason": qual["reason"], "model": {"id": Q.model()["model"]["id"], "version": Q.model()["model"]["version"], "rules_hash": Q.model()["_hash"]},
                          "fit_verification": qual["fit_verification"],
                          "gates": [{"gate_id": g["gate_id"], "status": g["status"], "blocking": g["blocking"], "evidence_status": g["evidence_status"], "reason": g["reason"]} for g in gates], "human_review": None},
        "evidence": evid, "ranking": {"status": "NOT_SCORED", "model": None, "position": None, "tied_with": [], "layer": None, "strength_band": None, "coverage": None, "not_probability": True, "calibrated": False},
        "contact": contact, "contradictions": contradictions,
        "cost": {"paid_calls": 0, "credits": 0, "data_calls": 0, "paid_enrichment_authorized": False, "cost_status": "MEASURED", "note": usage_note},
        "facets": {"qualification": st, "contact_grade": rc["contact_quality"]["grade"] if rc else "NONE", "gate_outcome": "BLOCKING_FAIL" if qual.get("rule_id") == "BLOCKING_FAIL" else st},
        "provenance": provenance, "timestamps": {"created_at": f"{today}T00:00:00Z", "observed_at": today, "last_verified": contact.get("last_verified"), "updated_at": None},
        "recommended_action": {"action": "HUMAN_REVIEW", "summary": "-", "next_step": None, "blocking_reasons": [], "requires_authorization": False}}
    rec_out["recommended_action"] = NA.derive_action(rec_out)
    return rec_out


def prov_ev_label(provenance): return "Proveedor sintético (SYNTHETIC): descubrimiento" if provenance["data_origin"] == "SYNTHETIC_FIXTURE" else "Apollo · lookup gratuito (captura real F12): descubrimiento"
