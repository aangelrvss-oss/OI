"""DOMAIN_PRECHECK (genérico, OFFLINE: sin red ni créditos). Clasifica el dominio de un candidato ANTES de gastar enrichment.
NO valida que el dominio sea el correcto (eso exige red y queda fuera): solo evita gastar cuando el dato es OBVIAMENTE anómalo.
Clases: MISSING | MALFORMED | SUSPICIOUS_THIRD_PARTY | VALID_LOOKING.
No trae ninguna lista propia de dominios de terceros: la APORTA el caso en la configuración (no existe una política global)."""
import re
from oi.engine.evaluators.entity import normalize_domain
MISSING, MALFORMED, SUSPICIOUS, VALID = "MISSING", "MALFORMED", "SUSPICIOUS_THIRD_PARTY", "VALID_LOOKING"
_LABEL = re.compile(r"^[^\W_](?:[^\W_]|-){0,61}$", re.UNICODE)       # letras/dígitos (también IDN), sin guion bajo; el guion no puede ir al final (se comprueba aparte)


def classify(domain, suspicious=(), allow=()):
    """→ {"class", "domain", "reasons"}. `suspicious`: dominios de terceros/anómalos APORTADOS por el caso; `allow`: excepciones explícitas decididas por un humano."""
    if domain is None or not str(domain).strip(): return {"class": MISSING, "domain": None, "reasons": ["empty"]}
    d = normalize_domain(domain)
    if not d: return {"class": MISSING, "domain": None, "reasons": ["empty_after_normalization"]}
    bad = []
    if len(d) > 253: bad.append("longer_than_253")
    if d.startswith(".") or d.endswith("."): bad.append("leading_or_trailing_dot")
    labels = d.split(".")
    if len(labels) < 2: bad.append("no_dot")
    for lb in labels:
        if not lb: bad.append("empty_label"); continue
        if not _LABEL.match(lb) or lb.endswith("-"): bad.append(f"invalid_label:{lb[:30]}")
    if len(labels) >= 2 and labels[-1] and (labels[-1].isdigit() or len(labels[-1]) < 2): bad.append("invalid_tld")
    if bad: return {"class": MALFORMED, "domain": d, "reasons": sorted(set(bad))}
    allow_n, sus_n = {normalize_domain(x) for x in allow}, {normalize_domain(x) for x in suspicious}
    if d not in allow_n and any(d == s or d.endswith("." + s) for s in sus_n if s): return {"class": SUSPICIOUS, "domain": d, "reasons": ["listed_third_party_or_known_anomaly"]}
    return {"class": VALID, "domain": d, "reasons": []}
