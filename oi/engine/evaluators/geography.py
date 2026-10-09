"""BLOQUE 2 · Jerarquía geográfica explícita: Madrid (municipio) ⊂ Comunidad de Madrid (región) ⊂ España (país).
El spec declara un CONJUNTO de zonas admitidas (unión). Una entidad cumple si está dentro de ALGUNA. 'España' NO implica Madrid; Madrid implica Comunidad de Madrid y España.
Nunca se amplía más de lo declarado. Estados: PASS / FAIL / UNKNOWN / CONFLICTING. Se exigen DOS fuentes (estructurada y raw_address) para decidir PASS/FAIL (decisión previa D-0xx)."""
import re
from oi.engine.spec.text import norm
from . import evaluation as EV

TIER = {"municipality": 0, "province": 1, "region": 2, "country": 3}
REGION_ALIASES = {"comunidad de madrid": {"comunidad de madrid", "community of madrid", "madrid community"}}
MUNICIPALITY_REGION = {"madrid": "comunidad de madrid"}              # solo lo que el catálogo justifica (7 municipios, 1 región con alias inglés)
_REGIONS = {norm(r["name"]): r for r in EV._GEO["regions"]}
_PROVINCES = {norm(p["name"]) for p in EV._GEO["provinces"]}


def _region_key(text):
    t = norm(text or "")
    for k, al in REGION_ALIASES.items():
        if t in {norm(a) for a in al}: return k
    return t if t in _REGIONS else None


def _observe(structured, raw):
    s = structured or {}
    ctext = norm(s.get("country") or "")
    st = {"country": EV.country_code(s.get("country")) or (ctext.upper() if ctext else None), "city": norm(s.get("city") or "") or None, "state": norm(s.get("state") or "") or None}
    st["region"] = _region_key(s.get("state")) or MUNICIPALITY_REGION.get(st["city"]); st["province"] = st["state"] if st["state"] in _PROVINCES else None
    rw = {"country": None, "region": None, "city": None, "province": None, "segments": set()}
    if raw:
        for seg in [norm(x) for x in str(raw).split(",")]:
            seg = seg.strip()
            if not seg: continue
            rw["segments"].add(re.sub(r"[0-9]+", "", seg).strip())      # 'madrid 28033' -> 'madrid' (solo CONFIRMA un municipio; un segmento ausente nunca prueba otro municipio)
            c = EV.country_code(seg) or (seg.upper() if len(seg) == 2 and seg.isalpha() else None)
            rw["country"] = rw["country"] or c
            stripped = re.sub(r"[0-9]+", "", seg).strip()
            rw["region"] = rw["region"] or _region_key(stripped)
    return st, rw


def _eval_target(level, name, o):
    """PASS / FAIL / None (desconocido) de UNA fuente observada frente a UN objetivo."""
    n = norm(name or "")
    if level == "country":
        tc = (EV.country_code(name) or norm(name or "")).upper()
        return None if not o["country"] else (str(o["country"]).upper() == tc)
    tgt_country = _REGIONS.get(_region_key(name) or n, {}).get("country") or EV._LOCAL_COUNTRY.get(n)
    if o["country"] and tgt_country and o["country"] != tgt_country: return False
    if level == "region":
        key = _region_key(name) or n
        if o["region"]: return o["region"] == key
        if any(MUNICIPALITY_REGION.get(seg) == key for seg in (o.get("segments") or ())): return True     # un segmento 'madrid' en la dirección implica la Comunidad de Madrid (solo confirma)
        if o["city"] and MUNICIPALITY_REGION.get(o["city"]): return MUNICIPALITY_REGION[o["city"]] == key
        if key == "comunidad de madrid" and o["province"] and o["province"] != "madrid": return False    # la Comunidad de Madrid es uniprovincial
        return None
    if level == "municipality":
        if o["city"]: return o["city"] == n
        if n in (o.get("segments") or ()): return True
        if o["region"] and MUNICIPALITY_REGION.get(n) and o["region"] != MUNICIPALITY_REGION[n]: return False
        return None
    if level == "province":
        if o["province"]: return o["province"] == n
        return None
    return None


def _source_verdict(targets, o):
    res = [(t.get("level"), _eval_target(t.get("level"), t.get("name"), o)) for t in targets]
    passed = [lv for lv, v in res if v is True]
    if passed: return True, min(passed, key=lambda lv: TIER.get(lv, 9))
    if res and all(v is False for _, v in res): return False, None
    return None, None


def evaluate_geography(structured, raw_address, targets, invert=False):
    st, rw = _observe(structured, raw_address)
    sv, stier = _source_verdict(targets, st); rv, rtier = _source_verdict(targets, rw)
    src = {"structured": {"country": st["country"], "city": (structured or {}).get("city"), "state": (structured or {}).get("state"), "matches_target": sv, "tier": stier},
           "raw_address": {"country_code": rw["country"], "region": rw["region"], "matches_target": rv, "tier": rtier}}
    flip = (lambda v: (not v) if v is not None else None) if invert else (lambda v: v)
    if sv is None and rv is None:
        return EV._res("UNKNOWN", EV.UNKNOWN, location_state="location_unknown", state="UNKNOWN", sources=src, reason="sin ubicación utilizable")
    if sv is None or rv is None:
        one = sv if sv is not None else rv
        return EV._res("UNKNOWN", EV.INFERRED, location_state="location_unknown", state="UNKNOWN", sources=src, single_source_hint=flip(one),
                       tier_single_source_hint=stier if sv is not None else rtier, reason="una sola fuente: no se puede corroborar (no se decide PASS/FAIL)")
    if sv != rv:
        return EV._res("CONFLICTING", EV.CONFLICTING, location_state="location_conflicting", state="CONFLICTING", sources=src, reason="las dos fuentes discrepan")
    verdict = "PASS" if flip(sv) else "FAIL"
    both = [t for t in (stier, rtier) if t]
    common = max(both, key=lambda lv: TIER[lv]) if len(both) == 2 else None          # nivel MÁS ESPECÍFICO que ambas fuentes corroboran (el más amplio de los dos)
    return EV._res(verdict, EV.KNOWN, location_state="location_verified", state=verdict, sources=src, matched_tier_corroborated=common if verdict == "PASS" and not invert else None,
                   tier_single_source_hint=min(both, key=lambda lv: TIER[lv]) if both else None)
