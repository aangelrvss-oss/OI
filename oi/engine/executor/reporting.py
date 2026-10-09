"""Informes genéricos del ejecutor: selección final SIN relleno, rutas de encaje (taxonomía aportada por la configuración) y punto de control previo al gasto."""
import hashlib
from oi.engine.spec.text import norm
from .ledger import canon

GATE_BAD = ("failed", "gate_failed_estimated", "gate_failed_inferred", "conflicting_review")


def fit_paths(rec, taxonomy):
    """Rutas (A/B/C…) a las que encaja una entidad según NAICS / etiquetas de industria de Apollo. Estado SIEMPRE INFERRED (clasificación de terceros)."""
    out = []
    if not rec: return out
    naics = [str(c) for c in (rec.get("naics_codes") or [])]; labels = [norm(x) for x in [rec.get("industry")] + list(rec.get("industries") or []) if x]
    for pid, t in taxonomy.items():
        basis = [f"naics:{c}" for c in naics if any(c.startswith(p) for p in t.get("naics_prefixes", []))] + [f"industry:{l}" for l in labels if l in {norm(x) for x in t.get("industry_labels", [])}]
        if basis: out.append({"path": pid, "basis": basis, "evidence_status": "INFERRED"})
    return out


def contract_reasons(e, contract):
    """Contrato de «reportable» APORTADO POR EL CASO (opt-in). Solo AÑADE motivos de exclusión: NO toca los gates ni GATE_BAD.
    Falla CERRADO: lo no evaluado cuenta como no verificado. UNKNOWN nunca equivale a PASS. No depende de la evidencia secundaria (rutas/señales)."""
    out = []
    want = contract.get("identity")
    if want and e.get("identity") != want: out.append(f"contract_identity_{e.get('identity') or 'NOT_EVALUATED'}")
    for gid in contract.get("gates_verified") or []:
        g = (e.get("gate_results") or {}).get(gid)
        if not g or g.get("verdict") != "PASS" or g.get("evidence_status") != "KNOWN": out.append(f"contract_gate_{gid}_not_verified({(g or {}).get('verdict') or 'NOT_EVALUATED'})")
    return out


def select_final(entities, target, min_evidence_items, contract=None):
    """entities: [{entity_key, name, rank, enriched, gate_status, paths, signals_present, [identity, gate_results]}]. El objetivo NO obliga a rellenar: se devuelve solo lo que tiene evidencia suficiente.
    contract (opcional): {identity, gates_verified} — exigencia adicional de «reportable» aportada por la configuración; None = comportamiento histórico."""
    final, not_eligible = [], []
    for e in sorted(entities, key=lambda x: (x["rank"], x["entity_key"])):
        reasons = []
        if not e["enriched"]: reasons.append("no_enriquecida")
        if e["gate_status"] in GATE_BAD: reasons.append(f"gate_{e['gate_status']}")
        items = len(e["paths"]) + len(e["signals_present"])
        if items < min_evidence_items: reasons.append(f"evidencia_insuficiente({items}<{min_evidence_items})")
        if contract: reasons += contract_reasons(e, contract)
        if reasons: not_eligible.append({"entity_key": e["entity_key"], "name": e["name"], "reasons": reasons})
        else: final.append({**e, "evidence_items": items})
    chosen = final[:target]
    out = {"final": chosen, "eligible_total": len(final), "target": target, "returned": len(chosen), "shortfall": max(0, target - len(chosen)), "padded": False,
           "not_eligible": not_eligible, "priority": "PRELIMINARY (orden de selección; NO es scoring)", "scoring": "NOT_IMPLEMENTED"}
    if contract: out["reportable_contract"] = contract
    return out


def shortlist_hash(domains, job_max, lexicon, cap, binding=None):
    """binding (opt-in, T1-A2): si se aporta, el hash también cubre entidades, orden, estrategia, exclusiones y versiones. Sin binding el hash es el histórico (idéntico)."""
    body = {"domains": domains, "job_postings_max": job_max, "lexicon": lexicon, "hard_cap": cap}
    if binding is not None: body["binding"] = binding
    return hashlib.sha256(canon(body).encode()).hexdigest()[:16]
