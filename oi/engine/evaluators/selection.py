"""BLOQUE 4 · Estrategias de selección de candidatos para enrichment: reglas EXPLÍCITAS y auditables, SIN pesos ni score.
Solo usa señales OBSERVADAS antes de gastar (procedencia de consultas, posición, dominio). No usa tamaño, afinidad técnica ni tipo de empresa: no existen antes del enrichment.
· interleave_position_v1 (por defecto; es lo ejecutado en 3C): mejor posición en cualquier consulta, luego prioridad de consulta.
· overlap_diversity_v2 (CALIBRATION_CANDIDATE, no es el defecto): dentro de cada grupo de ruta, más consultas distintas primero, luego consulta de ámbito más local, luego mejor posición; los grupos se intercalan en turnos iguales."""
STRATEGIES = ("interleave_position_v1", "overlap_diversity_v2")


def _occ(e, cands):
    return [cands[i] for i in e["sources"]]


def order_entities(strategy, entities, cands, query_meta):
    if strategy == "interleave_position_v1":
        for e in entities: e["_key"] = min((c["q_pos"], c["q_index"]) for c in _occ(e, cands))
        return sorted(entities, key=lambda e: (e["_key"], e["rank"]))
    if strategy != "overlap_diversity_v2": raise ValueError(f"estrategia desconocida: {strategy}")
    for e in entities:
        occ = _occ(e, cands); qids = sorted({c["query_id"] for c in occ}, key=lambda q: query_meta.get(q, {}).get("order", 99))
        first = qids[0]; e["_overlap"] = len(qids); e["_group"] = query_meta.get(first, {}).get("path_hint") or "sin_pista"
        e["_geo"] = min(query_meta.get(q, {}).get("geo_tier", 1) for q in qids); e["_pos"] = min(c["q_pos"] for c in occ); e["_qorder"] = query_meta.get(first, {}).get("order", 99)
    groups = {}
    for e in sorted(entities, key=lambda e: (e["_qorder"], e["rank"])): groups.setdefault(e["_group"], []).append(e)
    for g in groups.values(): g.sort(key=lambda e: (-e["_overlap"], e["_geo"], e["_pos"], e["_qorder"], e["entity_key"]))
    out, i = [], 0
    names = list(groups)
    while any(groups.values()):
        for gname in names:
            if groups[gname]: out.append(groups[gname].pop(0))
    return out
