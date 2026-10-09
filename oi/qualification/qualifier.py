"""Cualificación REAL del pipeline: candidato + puertas del proveedor + evidencia -> qualification (opportunity_record.v1).

Genérico: no conoce ningún caso. Qué puertas existen y qué valor se espera llega en `gate_defs` (derivado del Search Spec y de la configuración del caso).
Las reglas de estado son datos (rules/qualification.v1.json). Invariantes (se prueban):
  UNKNOWN != PASS · INFERRED != KNOWN · CONFLICTING != PASS · ELIGIBLE exige TODAS las puertas bloqueantes en PASS con KNOWN.
"""
import hashlib
import json
import os

from oi import paths
from oi.engine.spec.text import norm

_M = None


def model():
    global _M
    if _M is None:
        p = os.path.join(paths.RULES, "qualification.v1.json")
        with open(p, encoding="utf-8") as f: raw = f.read()
        _M = json.loads(raw); _M["_hash"] = hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True).encode()).hexdigest()
    return _M


def _n(v): return norm(str(v)) if v is not None else ""


_NUM = r"(\d[\d.,]*)"


def _to_num(t):
    t = t.strip(); t = t.replace(" ", "")
    if "," in t and "." in t: t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or t.count(",") > 1: t = t.replace(".", "").replace(",", "")
    elif "," in t: t = t.replace(",", "") if len(t.split(",")[-1]) == 3 else t.replace(",", ".")
    elif "." in t and len(t.split(".")[-1]) == 3: t = t.replace(".", "")
    return float(t)


def parse_interval(v):
    """Valor de un claim de cantidad -> (lo, hi) o None. Entiende: 380, '51-500', '>5000', '5.000+', 'more than 5,000', 'más de 500', 'over 500', 'up to 50'. Regla general, sin casos."""
    import re
    if v is None or isinstance(v, bool): return None
    if isinstance(v, (int, float)): return (float(v), float(v))
    t = str(v).strip().lower()
    m = re.fullmatch(_NUM + r"\s*[-–—]\s*" + _NUM, t)
    if m: a, b = _to_num(m.group(1)), _to_num(m.group(2)); return (min(a, b), max(a, b))
    m = re.fullmatch(r"(?:>=?|more than|over|above|greater than|más de|mas de|superior a|\+)\s*" + _NUM + r"(?:\s*\+)?", t) or re.fullmatch(_NUM + r"\s*\+", t)
    if m:
        n = _to_num(m.group(1)); return (n + (0 if t.startswith(">=") or t.endswith("+") else 1), float("inf"))
    m = re.fullmatch(r"(?:<=?|less than|under|up to|hasta|menos de)\s*" + _NUM, t)
    if m: return (0.0, _to_num(m.group(1)))
    m = re.fullmatch(_NUM, t)
    if m: n = _to_num(m.group(1)); return (n, n)
    return None


def resolve_range_gate(gate_def, evid):
    """Puerta NUMÉRICA (p. ej. tamaño 51–500). Compara el INTERVALO de cada claim con [min,max]. Si dos claims con intervalos distintos se contradicen (uno dentro, otro fuera) -> UNKNOWN/CONFLICTING:
    una estimación del proveedor NO oculta una contradicción fuerte (p. ej. 380 estimados vs «más de 5.000»). FAIL solo con KNOWN aceptable; PASS/KNOWN solo con KNOWN aceptable."""
    lo, hi = gate_def["numeric_range"]["min"], gate_def["numeric_range"]["max"]; ids = [e["evidence_id"] for e in evid]; acc = gate_def.get("acceptable_source_classes")
    def out(status, es, reason, trace): return {"gate_id": gate_def["gate_id"], "label": gate_def["label"], "status": status, "evidence_status": es, "evidence_ids": ids, "reason": reason, "blocking": gate_def["blocking"], "trace": trace}
    inn, outt, part, flags = [], [], [], []
    for e in evid:
        if e["claim_status"] == "CONFLICTING": flags.append(e); continue
        if e["claim_status"] == "UNKNOWN" or e["value"] is None: continue
        iv = parse_interval(e["value"])
        if iv is None: continue
        kind = "in" if (iv[0] >= lo and iv[1] <= hi) else ("out" if (iv[1] < lo or iv[0] > hi) else "part")
        strong = e["claim_status"] == "KNOWN" and e["source_class"] not in ("UNVERIFIED", "NOT_FOUND") and (not acc or e["source_class"] in acc)
        (inn if kind == "in" else outt if kind == "out" else part).append((e, strong))
    show = lambda xs: ", ".join(f"{e['evidence_id']}={e['value']} ({e['claim_status']})" for e, _ in xs)
    if flags or (inn and outt): return out("UNKNOWN", "CONFLICTING", "Contradicción de tamaño entre fuentes: dentro de [%g,%g]: %s; fuera: %s. CONFLICTING no es PASS ni FAIL; la estimación no oculta la contradicción." % (lo, hi, show(inn) or "—", show(outt) or "—"), "RANGE_CONFLICT")
    if part and not outt and not inn: return out("UNKNOWN", "INFERRED", "Rango parcialmente solapado con [%g,%g]: %s." % (lo, hi, show(part)), "RANGE_PARTIAL")
    if inn and not part:
        if any(s for _, s in inn): return out("PASS", "KNOWN", "Evidencia KNOWN dentro de [%g,%g]: %s." % (lo, hi, show([x for x in inn if x[1]])), "RANGE_KNOWN_IN")
        es = "ESTIMATED" if any(e["claim_status"] == "ESTIMATED" for e, _ in inn) else "INFERRED"
        return out("PASS", es, "Solo %s dentro de [%g,%g]: %s; no es evidencia KNOWN." % (es.lower(), lo, hi, show(inn)), "RANGE_" + es + "_IN")
    if outt and not inn:
        if any(s for _, s in outt): return out("FAIL", "KNOWN", "Evidencia KNOWN fuera de [%g,%g]: %s." % (lo, hi, show([x for x in outt if x[1]])), "RANGE_KNOWN_OUT")
        return out("UNKNOWN", "ESTIMATED" if any(e["claim_status"] == "ESTIMATED" for e, _ in outt) else "INFERRED", "Solo hay datos no KNOWN fuera de [%g,%g]: %s; no se descarta (INFERRED/ESTIMATED nunca producen FAIL)." % (lo, hi, show(outt)), "RANGE_WEAK_OUT")
    return out("UNKNOWN", "UNKNOWN", "Sin información de tamaño interpretable.", "NO_DATA")


def resolve_gate(gate_def, provider, evid, *, M=None):
    """gate_def: {gate_id, label, expected:[valores aceptables], blocking}. provider: detalle del executor (o None). evid: evidencia (ya convertida) del campo de la puerta.
    -> {gate_id, status, evidence_status, evidence_ids, reason, blocking, trace}"""
    if gate_def.get("numeric_range"): return resolve_range_gate(gate_def, evid)
    M = M or model(); exp = {_n(x) for x in gate_def["expected"]}; ids = [e["evidence_id"] for e in evid]
    acc = gate_def.get("acceptable_source_classes")
    known = [e for e in evid if e["claim_status"] == "KNOWN" and e["source_class"] not in ("UNVERIFIED", "NOT_FOUND") and e["value"] is not None and (not acc or e["source_class"] in acc)]
    inferred = [e for e in evid if e["claim_status"] in ("INFERRED", "ESTIMATED") and e["value"] is not None]
    conflicting = [e for e in evid if e["claim_status"] == "CONFLICTING"]
    km = [e for e in known if _n(e["value"]) in exp]; kx = [e for e in known if _n(e["value"]) not in exp]
    pv = None if provider is None else M["provider_verdict_map"].get((provider or {}).get("verdict"), "UNKNOWN")
    pe = None if provider is None else (provider.get("evidence_status") or "UNKNOWN")
    def out(status, es, reason, trace): return {"gate_id": gate_def["gate_id"], "label": gate_def["label"], "status": status, "evidence_status": es, "evidence_ids": ids, "reason": reason,
                                              "blocking": gate_def["blocking"], "trace": trace}
    if conflicting or (km and kx):
        return out("UNKNOWN", "CONFLICTING", "Fuentes en conflicto: " + ("; ".join(f"{e['evidence_id']}={e['value']}" for e in (conflicting or km + kx))) + ". CONFLICTING no es PASS.", "CONFLICTING_EVIDENCE")
    if known and pv in ("PASS", "FAIL") and ((km and pv == "FAIL") or (kx and pv == "PASS")):
        return out("UNKNOWN", "CONFLICTING", "La fuente KNOWN y la etiqueta del proveedor se contradicen: se muestra el conflicto, no se elige.", "KNOWN_VS_PROVIDER_CONFLICT")
    if km: return out("PASS", "KNOWN", f"Evidencia KNOWN confirma {sorted({str(e['value']) for e in km})}.", "KNOWN_MATCH")
    if kx: return out("FAIL", "KNOWN", f"Evidencia KNOWN indica {sorted({str(e['value']) for e in kx})}, no {sorted(gate_def['expected'])}.", "KNOWN_MISMATCH")
    if pv in ("PASS", "FAIL"):
        return out(pv, pe, f"Solo el dato del proveedor ({pe}) respalda este resultado: no es evidencia KNOWN.", "PROVIDER_" + pv)
    if any(_n(e["value"]) in exp for e in inferred):
        return out("PASS", "INFERRED", "Solo hay una inferencia que coincide con lo pedido; una inferencia no es evidencia KNOWN.", "INFERRED_MATCH")
    if inferred: return out("UNKNOWN", "INFERRED", "La única información es una inferencia que no coincide con lo pedido.", "INFERRED_MISMATCH")
    if provider is not None and (provider.get("evidence_status") == "CONFLICTING" or provider.get("verdict") == "CONFLICTING"):
        return out("UNKNOWN", "CONFLICTING", "El proveedor presenta datos contradictorios (p. ej. la etiqueta principal y otras no coinciden).", "PROVIDER_CONFLICT")
    nf = [e for e in evid if e["source_class"] == "NOT_FOUND"]
    return out("UNKNOWN", "UNKNOWN", "Sin información" + (" (búsqueda sin resultado: NOT_FOUND)." if nf else "."), "NO_DATA")


def growth_any_gate(signals, *, gate_id="GROWTH", label="Crecimiento: al menos una señal PRESENT en la ventana", blocking=True):
    """Puerta de crecimiento del EXPERIMENTO (agregación ANY del catálogo). `signals`: señales S-* ya evaluadas (state/claim_status/evidence_ids).
    PASS/KNOWN solo si alguna señal está PRESENT con claim KNOWN; PRESENT ESTIMATED -> PASS/ESTIMATED (no KNOWN: queda PENDING_EVIDENCE). La ausencia o un INFERRED nunca es FAIL ni PASS: UNKNOWN.
    No usa ningún umbral numérico."""
    pres = [s for s in signals if s["state"] == "PRESENT"]; known = [s for s in pres if s["claim_status"] == "KNOWN"]
    ids = sorted({e for s in signals for e in s["evidence_ids"]})
    def out(st, es, why, tr): return {"gate_id": gate_id, "label": label, "status": st, "evidence_status": es, "evidence_ids": ids, "reason": why, "blocking": blocking, "trace": tr}
    if known: return out("PASS", "KNOWN", "Señales PRESENT con evidencia KNOWN: " + ", ".join(s["signal_id"] for s in known) + ".", "GROWTH_KNOWN")
    if pres: return out("PASS", "ESTIMATED", "Solo hay señales ESTIMATED: " + ", ".join(s["signal_id"] for s in pres) + "; una estimación no es evidencia KNOWN.", "GROWTH_ESTIMATED")
    inf = [s for s in signals if s["claim_status"] == "INFERRED"]
    if inf: return out("UNKNOWN", "INFERRED", "Solo hay inferencias de crecimiento; no cuentan como señal presente.", "GROWTH_INFERRED")
    return out("UNKNOWN", "UNKNOWN", "Sin evidencia fechada de ninguna de las señales de crecimiento: UNKNOWN (no es NO).", "NO_DATA")


def identity_gate(identity_status, review_note=None, M=None):
    """review_note != None: hay una revisión de duplicados/entidades pendiente (mismo nombre o dominio compartido): la identidad no es firme y no puede ser PASS."""
    M = M or model(); g = M["identity_gate"]
    if review_note and identity_status in g["pass_when"]:
        return {"gate_id": g["id"], "label": g["label"], "status": "UNKNOWN", "evidence_status": "UNKNOWN", "evidence_ids": [], "reason": f"Revisión de entidades pendiente: {review_note}.", "blocking": True, "trace": "REVIEW_PENDING"}
    st, es, why = ("PASS", "KNOWN", "La identidad del registro coincide con la del descubrimiento.") if identity_status in g["pass_when"] else \
                  ("FAIL", "KNOWN", "La entidad devuelta no corresponde a la buscada.") if identity_status in g["fail_when"] else \
                  ("UNKNOWN", "UNKNOWN", f"Identidad {identity_status}: no se puede atribuir evidencia con seguridad.")
    return {"gate_id": g["id"], "label": g["label"], "status": st, "evidence_status": es, "evidence_ids": [], "reason": why, "blocking": True, "trace": "IDENTITY"}


def _facts(gates, enriched):
    b = [g for g in gates if g["blocking"]]
    known_pass = lambda g: g["status"] == "PASS" and g["evidence_status"] == "KNOWN"
    return {"enriched": enriched, "blocking_has_fail": any(g["status"] == "FAIL" for g in b),
            "blocking_all_unknown": bool(b) and all(g["status"] == "UNKNOWN" and g["evidence_status"] == "UNKNOWN" for g in b),
            "blocking_has_not_known_pass": any(not known_pass(g) for g in b), "blocking_all_known_pass": bool(b) and all(known_pass(g) for g in b)}


def qualify(gates, enriched, M=None):
    """gates: lista de resultados de resolve_gate/identity_gate. -> dict `qualification` (sin human_review) + id de la regla."""
    M = M or model(); f = _facts(gates, enriched); b = [g for g in gates if g["blocking"]]
    for r in M["status_rules"]:
        if all(f.get(k) in v for k, v in r["when"].items()):
            fmt = {"failed": ", ".join(g["gate_id"] for g in b if g["status"] == "FAIL"), "pending": ", ".join(g["gate_id"] for g in b if not (g["status"] == "PASS" and g["evidence_status"] == "KNOWN") and g["status"] != "FAIL"),
                   "passed": ", ".join(g["gate_id"] for g in b)}
            return {"status": r["status"], "fit_verification": r["fit_verification"], "reason": r["reason"].format(**fmt), "rule_id": r["id"]}
    d = M["default"]; return {**d, "rule_id": "DEFAULT"}
