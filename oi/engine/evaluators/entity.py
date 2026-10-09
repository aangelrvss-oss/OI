"""ENTITY RESOLUTION previa al enrichment. Prioridad: 1) Apollo organization id 2) dominio canónico 3) nombre normalizado + ubicación
4) fuzzy SOLO como revisión. Sin fusiones silenciosas; padre y filiales NO se fusionan. Un dominio ausente es un problema de calidad de datos, no un descarte."""
import difflib
import re
from oi.engine.spec.text import norm

_LEGAL = {"sl", "slu", "sa", "sau", "sas", "ltd", "llc", "inc", "gmbh", "sociedad", "limitada", "anonima"}


def normalize_domain(d):
    if not d: return None
    d = re.sub(r"^https?://", "", str(d).strip().lower()); d = re.sub(r"^www\.", "", d)
    return d.split("/")[0].split("?")[0] or None


def normalize_name(n):
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", norm(n).replace(".", "")).split() if t not in _LEGAL]
    return " ".join(toks)


def resolve_entities(candidates, fuzzy_threshold=None):
    """candidates: [{id, name, domain|website_url, city?}] en orden de descubrimiento. Devuelve entidades, fusiones, revisión y calidad de datos."""
    ents, merges, review, dq = [], [], [], {}
    by_id, by_dom, by_nameloc = {}, {}, {}
    for rank, c in enumerate(candidates):
        dom = normalize_domain(c.get("domain") or c.get("website_url")); nm = normalize_name(c.get("name")); city = norm(c.get("city")) if c.get("city") else None
        ent = None
        if c.get("id") and c["id"] in by_id:
            ent = by_id[c["id"]]; merges.append({"candidate": c.get("id"), "into": ent["entity_key"], "kind": "apollo_id"})
        elif dom and dom in by_dom and not (c.get("id") and any(e["apollo_id"] and e["apollo_id"] != c["id"] for e in by_dom[dom])):
            ent = by_dom[dom][0]; merges.append({"candidate": c.get("id") or c.get("name"), "into": ent["entity_key"], "kind": "canonical_domain"})
        elif nm and city and (nm, city) in by_nameloc and not c.get("id"):
            ent = by_nameloc[(nm, city)]; merges.append({"candidate": c.get("name"), "into": ent["entity_key"], "kind": "name_location"})
        if ent is None:
            key = f"apollo:{c['id']}" if c.get("id") else (f"domain:{dom}" if dom else f"name:{nm}")
            ent = {"entity_key": key, "apollo_id": c.get("id"), "name": c.get("name"), "name_norm": nm, "domain": dom, "rank": rank, "enrichable_by_apollo": bool(dom), "data_quality": [], "sources": []}
            if not dom: ent["data_quality"].append("MISSING_DOMAIN")      # calidad de datos: NO descarte automático
            ents.append(ent)
            if c.get("id"):
                by_id[c["id"]] = ent
            if dom:
                if dom in by_dom and by_dom[dom][0]["apollo_id"] != c.get("id"):
                    review.append({"kind": "shared_domain_different_ids", "state": "CONFLICTING", "a": by_dom[dom][0]["entity_key"], "b": key, "action": "mantener separadas (padre/filial); un solo enrichment por dominio"})
                    for e in (by_dom[dom][0], ent): e["data_quality"].append("SHARED_DOMAIN") if "SHARED_DOMAIN" not in e["data_quality"] else None
                by_dom.setdefault(dom, []).append(ent)
            if nm and city: by_nameloc[(nm, city)] = ent
            for o in ents[:-1]:                     # solo REVISIÓN, nunca fusión
                if not (o["name_norm"] and nm): continue
                if o["name_norm"] == nm and not (o["apollo_id"] and c.get("id") and o["apollo_id"] == c["id"]):
                    review.append({"kind": "same_normalized_name_without_location", "state": "REVIEW_REQUIRED", "a": o["entity_key"], "b": key, "action": "revisión humana: no se fusionan"})
                elif fuzzy_threshold and difflib.SequenceMatcher(None, o["name_norm"], nm).ratio() >= fuzzy_threshold:
                    review.append({"kind": "fuzzy_name", "state": "REVIEW_REQUIRED", "a": o["entity_key"], "b": key, "action": "revisión humana: no se fusionan"})
        ent["sources"].append(rank)
    return {"entities": ents, "merges": merges, "review": review, "silent_merges": 0}


def plan_enrichment_requests(entities, enriched_cache=None, capacity=None, batch_size=10, force_reenrich=()):
    """Solicitudes de enrichment SIN duplicados (re-enrichment solo si se FUERZA explícitamente): un dominio una sola vez, nunca uno ya enriquecido (Apollo no cachea y re-enriquecer se cobra)."""
    forced = {normalize_domain(d) for d in force_reenrich}
    cache = {normalize_domain(k) for k in (enriched_cache or {})} - forced
    seen, reqs, skipped = set(), [], []
    for e in sorted(entities, key=lambda e: (not e["enrichable_by_apollo"], e["rank"])):
        if not e["enrichable_by_apollo"]:
            skipped.append({"entity_key": e["entity_key"], "reason": "sin_dominio: el único identificador verificado para enrichment es domain"}); continue
        if e["domain"] in cache:
            skipped.append({"entity_key": e["entity_key"], "reason": "ya_enriquecida: se reutiliza la evidencia"}); continue
        if e["domain"] in seen:
            skipped.append({"entity_key": e["entity_key"], "reason": "dominio_duplicado_en_la_misma_tanda"}); continue
        if capacity is not None and len(reqs) >= capacity:
            skipped.append({"entity_key": e["entity_key"], "reason": "fuera_de_capacidad"}); continue
        seen.add(e["domain"]); reqs.append(e["domain"])
    return {"batches": [reqs[i:i + batch_size] for i in range(0, len(reqs), batch_size)], "domains": reqs, "forced": sorted(forced & set(reqs)), "skipped": skipped, "max_credits": len(reqs)}


def apply_entity_exclusions(entities, excluded):
    """PRE-ENRICHMENT FILTER determinista: exclusiones EXPLÍCITAS del usuario por nombre/dominio normalizado. No usa ningún dato de enrichment."""
    ex_names = {normalize_name(x) for x in excluded}; ex_doms = {normalize_domain(x) for x in excluded} - {None}
    kept, out = [], []
    for e in entities:
        hit = e["name_norm"] in ex_names or (e["domain"] in ex_doms) or any(n and n in e["name_norm"].split() for n in ex_names)
        (out if hit else kept).append({"entity_key": e["entity_key"], "reason": "excluded_entity_match"} if hit else e)
    return {"kept": kept, "excluded": out}


_QUALIFIERS = {"spain", "espana", "iberia", "hispania", "international", "internacional", "group", "grupo", "europe", "europa", "holding", "corporation", "corporacion"}


def match_excluded_entities(entities, excluded):
    """Exclusión de clientes actuales por IDENTIDAD NORMALIZADA (no por coincidencia parcial).
    - EXCLUIDA automáticamente solo si el nombre normalizado (sin forma societaria) es IGUAL al excluido, o el dominio coincide.
    - Una coincidencia PARCIAL (el excluido aparece dentro del nombre, o es una sigla que aparece como palabra) NO excluye: se marca para REVISIÓN y la entidad se mantiene.
    - Una sigla de ≤4 letras nunca excluye por contención."""
    ex = []
    for x in excluded:
        nx = normalize_name(x); ex.append({"raw": x, "norm": nx, "tokens": nx.split(), "domain": normalize_domain(x) if "." in x else None})
    kept, excluded_out, review = [], [], []
    for e in entities:
        ne, toks = e["name_norm"], e["name_norm"].split(); hit = None
        for x in ex:
            if ne == x["norm"] or (x["domain"] and e["domain"] == x["domain"]):
                hit = {"entity_key": e["entity_key"], "excluded_name": x["raw"], "match": "exact_normalized_identity"}; break
        if hit: excluded_out.append(hit); continue
        flags = []
        for x in ex:
            t = x["tokens"]
            if not t: continue
            contiguous = any(toks[i:i + len(t)] == t for i in range(len(toks) - len(t) + 1))
            if contiguous:
                rest = [w for w in toks if w not in t]
                flags.append({"entity_key": e["entity_key"], "excluded_name": x["raw"], "match": "partial_identity_with_qualifiers" if set(rest) <= _QUALIFIERS else "partial_phrase_contained",
                              "action": "KEPT: no se excluye por coincidencia parcial; revisión humana"})
        review += flags; kept.append(e)
    return {"kept": kept, "excluded": excluded_out, "review": review}
