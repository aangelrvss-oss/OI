"""CANDIDATE RELEVANCE (genérico). Una sola pregunta: ¿tiene sentido gastar recursos para investigar más esta entidad como oportunidad comercial?
NO resuelve tamaño, Fit, Opportunity ni Data Confidence. Conservadora: POR DEFECTO MANTENER.
El LLM (operador) PROPONE decisiones; este validador determinista las ACEPTA o las degrada a KEEP. Nada se descarta sin motivo, sin confianza 'clara'
y sin evidencia citada literalmente del nombre/dominio. Todo queda registrado y es reversible."""
from oi.engine.spec.text import norm

CONFIDENCES = ("clear", "likely", "unsure")


def validate_decisions(candidates, proposals, policy):
    """candidates: [{entity_key, name, domain, website_url}] · proposals: [{entity_key, decision, category, reason, evidence:[{field, quote}], confidence}]."""
    by_key = {c["entity_key"]: c for c in candidates}
    allowed_f, cats = set(policy["allowed_evidence_fields"]), set(policy["reason_categories"])
    decisions, unknown = {}, []
    for pr in proposals or []:
        k = pr.get("entity_key"); c = by_key.get(k)
        if c is None: unknown.append({"entity_key": k, "why": "entidad desconocida"}); continue
        if k in decisions: continue                                   # primera propuesta por entidad; no se sobrescribe
        conf = pr.get("confidence") if pr.get("confidence") in CONFIDENCES else "unsure"
        if pr.get("decision") == "REVIEW":                    # duda explícita: se CONSERVA para selección pero queda marcada para revisión humana
            if len(str(pr.get("reason") or "").strip()) >= 10:
                decisions[k] = {"entity_key": k, "decision": "REVIEW", "relevance_confidence": conf, "discard_reason": None, "reason": pr.get("reason"), "evidence": pr.get("evidence") or [], "validator": "accepted_review"}
            else:
                decisions[k] = {"entity_key": k, "decision": "KEEP", "relevance_confidence": "unsure", "discard_reason": None, "reason": pr.get("reason"), "evidence": [], "validator": "review_without_reason_kept"}
            continue
        if pr.get("decision") != "DISCARD":
            decisions[k] = {"entity_key": k, "decision": "KEEP", "relevance_confidence": conf if pr.get("decision") == "KEEP" else "unsure", "discard_reason": None, "reason": pr.get("reason"), "evidence": pr.get("evidence") or [], "validator": "accepted_keep"}
            continue
        problems = []
        if pr.get("confidence") != "clear": problems.append("confianza_no_clara")
        if pr.get("category") not in cats: problems.append("categoria_no_valida")
        if len(str(pr.get("reason") or "").strip()) < 10: problems.append("motivo_ausente_o_insuficiente")
        ev = pr.get("evidence") or []
        if not ev: problems.append("sin_evidencia")
        for e in ev:
            if e.get("field") not in allowed_f: problems.append(f"campo_de_evidencia_no_permitido:{e.get('field')}")
            elif not norm(str(e.get("quote") or "")).strip() or norm(str(e["quote"])) not in norm(str(c.get(e["field"]) or "")): problems.append(f"cita_no_literal:{e.get('field')}")
        if problems:
            decisions[k] = {"entity_key": k, "decision": policy.get("rejected_discard_becomes", "KEEP"), "relevance_confidence": "discard_rejected_by_validator", "discard_reason": None, "reason": pr.get("reason"), "evidence": ev, "validator": "discard_rejected", "problems": sorted(set(problems))}
        else:
            decisions[k] = {"entity_key": k, "decision": "DISCARD", "relevance_confidence": "clear", "discard_reason": {"category": pr["category"], "text": pr["reason"].strip()}, "reason": pr["reason"], "evidence": ev, "validator": "accepted_discard"}
    for k in by_key:
        decisions.setdefault(k, {"entity_key": k, "decision": "KEEP", "relevance_confidence": "not_assessed", "discard_reason": None, "reason": None, "evidence": [], "validator": "default_keep"})
    kept = [by_key[k] for k, d in decisions.items() if d["decision"] in ("KEEP", "REVIEW")]       # REVIEW sigue siendo elegible
    disc = [d for d in decisions.values() if d["decision"] == "DISCARD"]
    cat = {}
    for d in disc: cat[d["discard_reason"]["category"]] = cat.get(d["discard_reason"]["category"], 0) + 1
    metrics = {"candidates_discovered": len(by_key), "candidates_discarded_by_relevance": len(disc), "candidates_kept_for_enrichment": len(kept), "candidates_review": sum(1 for d in decisions.values() if d["decision"] == "REVIEW"), "discard_reason": cat,
               "relevance_confidence": {c: sum(1 for d in decisions.values() if d["relevance_confidence"] == c) for c in sorted({d["relevance_confidence"] for d in decisions.values()})},
               "discards_rejected_by_validator": sum(1 for d in decisions.values() if d["validator"] == "discard_rejected"), "unknown_entities_in_proposals": unknown}
    return {"decisions": decisions, "kept": kept, "metrics": metrics}
