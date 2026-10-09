"""Clasificación de criterios. REGLA CRÍTICA: solo `active` + `defined` + campo operativo + enrutable genera trabajo."""
HARD, PREFERRED, OPTIONAL = "HARD", "PREFERRED", "OPTIONAL_SIGNAL"


def _view(id, field, dimension, strength, klass, route, op, value, params=None, implicit=False):
    return {"id": id, "field": field, "dimension": dimension, "strength": strength, "class": klass, "stage": route["stage"],
            "pre_enrichment_gate": route["pre_enrichment_gate"], "verified_gate": route["verified_gate"], "discovery_available": route["discovery_available"],
            "discovery_filter": bool(route["discovery_filter"]), "evidence_source": route["evidence_source"], "evaluator": route["evaluator"],
            "source_fields": route["source_fields"], "capability_ref": route["capability_ref"], "limitation": route["confidence_limitations"],
            "cost_class": route["cost_class"], "deep_research": route["deep_research"], "operator": op, "value": value, "params": params or {}, "implicit": implicit}


def classify(spec, cfg):
    reg, active, ignored = cfg.registry, [], []
    for c in spec.get("criteria", []):
        fdef = reg.fields.get(c["field"]); route = cfg.routing.get(c["field"]) or {}
        reason = None
        if c.get("activation") != "active": reason = "proposed"
        elif c.get("definition_status") != "defined": reason = "needs_definition"
        elif fdef is None: reason = "unknown_field"
        elif fdef["status"] != "active": reason = "field_not_operational"
        elif route.get("stage") is None: reason = "no_routing"
        if reason:
            ignored.append({"id": c["id"], "field": c["field"], "reason": reason}); continue
        klass = HARD if c["strength"] == "gate" else (OPTIONAL if c["dimension"] == "opportunity" else PREFERRED)
        active.append(_view(c["id"], c["field"], c["dimension"], c["strength"], klass, route, c["operator"], c.get("value"), c.get("params")))
    geo = spec["restrictions"]["geography"]; R = cfg.routing
    if geo.get("include"):
        active.append(_view("GEO", "restrictions.geography", "fit", "gate", HARD, R["restrictions.geography"], "in", geo["include"], implicit=True))
    if geo.get("exclude"):
        active.append(_view("GEO_EXCL", "restrictions.geography_exclude", "fit", "gate", HARD, R["restrictions.geography_exclude"], "not_in", geo["exclude"], implicit=True))
    if spec["restrictions"].get("excluded_entities"):
        active.append(_view("EXCL", "restrictions.excluded_entities", "fit", "gate", HARD, R["restrictions.excluded_entities"], "not_in", spec["restrictions"]["excluded_entities"], implicit=True))
    classes = {k: [c["id"] for c in active if c["class"] == k] for k in (HARD, PREFERRED, OPTIONAL)}
    return active, ignored, classes
