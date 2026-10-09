"""MÉTRICAS 3E: se calculan AUTOMÁTICAMENTE desde el ledger guardado (manifest.json + results.json + tool_calls.json). Ninguna a mano.
Definiciones CONGELADAS (no se mezclan):
  relevant_after_enrichment (SISTEMA) = identity KNOWN_MATCH  Y  geografía != FAIL  Y  >= min_evidence_items evidencias (FIT + OPPORTUNITY; el CONTEXTO no cuenta).
  useful_evidence = identity KNOWN_MATCH Y >= 1 evidencia FIT u OPPORTUNITY.
  rúbrica 3C (INFERRED, juicio del operador) = se informa APARTE y nunca se mezcla con la anterior.
Data Confidence = NOT_CALIBRATED: solo se informa el perfil real de estados de evidencia."""
import collections, json, os, sys


def load(run_dir):
    L = lambda n: json.load(open(os.path.join(run_dir, n), encoding="utf-8"))
    return L("manifest.json"), L("results.json"), L("tool_calls.json")


def _pct(a, b): return round(100.0 * a / b, 2) if b else None


def compute(manifest, results, tool_calls, min_evidence=2, rubric=None):
    prim = [r for r in results if r["dedupe_status"] == "primary"]; C = collections.Counter
    raw, uniq = len(results), len(prim)
    attempted = [r for r in prim if r.get("enrichment_call_id") and not str(r.get("enrichment_status") or "").startswith("NOT_ATTEMPTED") and not str(r.get("enrichment_status") or "").startswith("NOT_ENRICHED")]
    paid = [c for c in tool_calls if c["phase"] == "ENRICHMENT" and c["status"] in ("OK", "EMPTY_RESULT", "ERROR")]
    credits = sum((c.get("actual_cost") if c.get("actual_cost") is not None else 0) for c in paid); n_att = len(attempted); unit = (credits / n_att) if n_att else 0
    idc = C(r.get("identity_check") for r in attempted)
    matched = [r for r in attempted if r.get("identity_check") == "KNOWN_MATCH"]
    geo = C(((r.get("gate_details") or {}).get("GEO") or {}).get("verdict", "NOT_EVALUATED") for r in matched)
    tier = C(r.get("geo_priority_tier") or "NOT_EVALUATED" for r in matched)
    ev_n = {r["entity_id"]: (r.get("evidence_items") or 0) for r in matched}
    ge2, eq1, eq0 = sum(1 for v in ev_n.values() if v >= min_evidence), sum(1 for v in ev_n.values() if v == 1), sum(1 for v in ev_n.values() if v == 0)
    prof = C(); fit_av = opp_av = 0
    for r in matched:
        p = r.get("evidence_profile") or {}
        if any((p.get(k) or {}).get("state") == "PRESENT" for k in ("fit_industry_naics", "fit_keywords")): fit_av += 1
        if any((p.get(k) or {}).get("state") == "PRESENT" for k in p if str(k).startswith("opp_")): opp_av += 1
        for k, v in p.items():
            if isinstance(v, dict) and "evidence_status" in v: prof[f"{k}:{v['evidence_status']}"] += 1
    relevant = [r for r in matched if (((r.get("gate_details") or {}).get("GEO") or {}).get("verdict") != "FAIL") and ev_n.get(r["entity_id"], 0) >= min_evidence]
    useful = [r for r in matched if ev_n.get(r["entity_id"], 0) >= 1]
    quarantined = [r for r in attempted if r.get("identity_check") not in (None, "KNOWN_MATCH")]
    cp = manifest.get("checkpoint") or {}; sel = cp.get("proposed_enrichment") or []
    rel = C(r.get("relevance_decision") for r in prim if r.get("relevance_decision"))
    hints = C(((p.get("path_hints") or [None])[0] or "sin_pista") for p in sel)
    pos = [p["best_position"] for p in sel if p.get("best_position")]
    out = {"data_confidence": "NOT_CALIBRATED", "definitions": {"relevant_after_enrichment": f"identity=KNOWN_MATCH AND geography!=FAIL AND evidence_items>={min_evidence} (FIT+OPPORTUNITY; CONTEXT excluido)", "useful_evidence": "identity=KNOWN_MATCH AND evidence_items>=1"},
           "discovery": {"raw_records": raw, "unique_entities": uniq, "duplicates": raw - uniq, "entities_without_domain": sum(1 for r in prim if "MISSING_DOMAIN" in (r.get("data_quality") or [])),
                         "excluded_entities": sum(1 for r in prim if r["final_status"] == "EXCLUDED_BY_USER_RULE"), "possible_existing_client_review": sum(1 for r in prim if (r.get("review_state") or "").startswith("REVIEW_POSSIBLE_EXISTING_CLIENT")),
                         "already_enriched_3c": sum(1 for r in prim if r["final_status"] == "ALREADY_ENRICHED_3C"), "relevance": {"KEEP": rel.get("KEEP", 0), "DISCARD": rel.get("DISCARD", 0), "REVIEW": rel.get("REVIEW", 0)}},
           "selection": {"selected_candidates": len(sel), "query_overlap_distribution": dict(C(p.get("query_overlap") for p in sel)), "average_best_position": round(sum(pos) / len(pos), 2) if pos else None,
                         "technical_affinity_pre_enrichment_INFERRED_by_query": dict(hints), "direct_customer": sum(v for k, v in hints.items() if str(k).startswith("A")), "contractor_integrator": sum(v for k, v in hints.items() if str(k).startswith("B")),
                         "project_investment": sum(v for k, v in hints.items() if str(k).startswith("C"))},
           "enrichment": {"attempted": n_att, "credits_spent": credits, "credits_per_candidate": round(credits / n_att, 4) if n_att else None, "identity": {k: idc.get(k, 0) for k in ("KNOWN_MATCH", "KNOWN_MISMATCH", "AMBIGUOUS", "UNKNOWN")},
                          "identity_rates_pct": {k: _pct(idc.get(k, 0), n_att) for k in ("KNOWN_MATCH", "KNOWN_MISMATCH", "AMBIGUOUS", "UNKNOWN")}},
           "quality": {"geography": {k: geo.get(k, 0) for k in ("PASS", "FAIL", "UNKNOWN", "CONFLICTING")}, "geography_priority_tier": dict(tier), "fit_evidence_available": fit_av, "opportunity_evidence_available": opp_av,
                       "candidates_with_ge_min_evidence": ge2, "candidates_with_1_evidence": eq1, "candidates_with_0_evidence": eq0, "candidates_with_useful_evidence": len(useful), "evidence_status_profile": dict(prof)},
           "efficiency": {"pct_discovered_reaching_enrichment": _pct(n_att, uniq), "pct_enriched_relevant_after_enrichment": _pct(len(relevant), n_att), "credits_spent_on_quarantined": round(unit * len(quarantined), 4),
                          "pct_credits_on_quarantined": _pct(unit * len(quarantined), credits), "pct_enriched_with_useful_evidence": _pct(len(useful), n_att), "useful_evidence_per_credit": round(len(useful) / credits, 4) if credits else None,
                          "relevant_after_enrichment_count": len(relevant)}}
    if rubric is not None:
        rc = C(rubric.get(r["entity_id"], "SIN_ETIQUETA") for r in matched); out["rubric_3c_INFERRED"] = {"labels": dict(rc), "note": "juicio del operador; NO se mezcla con relevant_after_enrichment"}
    else: out["rubric_3c_INFERRED"] = "NOT_PROVIDED"
    return out


if __name__ == "__main__":
    run_dir = sys.argv[1]; rub = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
    m = compute(*load(run_dir), rubric=rub); print(json.dumps(m, ensure_ascii=False, indent=1))
