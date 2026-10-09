"""Hoja de revisión humana (muestra de candidatos) -> ground truth reutilizable. Sin red.

El revisor marca RELEVANT / NOT_RELEVANT / UNCERTAIN y, si procede, uno o varios motivos. Un SÍ humano NO es FIT_VERIFIED: la hoja mide relevancia de discovery, no cualificación.
"""
import csv, hashlib, io

LABELS = ("RELEVANT", "NOT_RELEVANT", "UNCERTAIN")
REASONS = ("wrong_sector", "wrong_entity", "wrong_size", "wrong_growth_signal", "wrong_business_model", "other")
COLUMNS = ["review_id", "apollo_org_id", "name", "domain", "found_by", "sample_role", "reviewer_label", "reason_codes", "reason_notes", "source_consulted_url", "reviewer", "reviewed_at",
           "contact_evidence_url", "contact_channel_type", "contact_channel_value", "contact_target_type", "contact_seen_at"]
PROTOCOL = {"labels": {"RELEVANT": "Parece una empresa constructora española (contratista) que podría necesitar software de gestión, según información PÚBLICA que citas.", "NOT_RELEVANT": "Claramente no lo es (otro sector, otra entidad, no española…). Indica motivo.", "UNCERTAIN": "No puedes decidirlo con información pública razonable."},
            "reasons": {"wrong_sector": "no es construcción", "wrong_entity": "no es la empresa operadora (p. ej. filial, marca, fundación, medio)", "wrong_size": "tamaño claramente fuera de lo razonable", "wrong_growth_signal": "la señal de crecimiento no se sostiene", "wrong_business_model": "promotora/inmobiliaria/ingeniería/proveedor, no contratista", "other": "otro (explícalo)"},
            "rules": ["Rellena reviewer_label con exactamente uno de los tres valores.", "reason_codes: uno o varios de la lista separados por «|»; obligatorio con NOT_RELEVANT; opcional en los demás.", "source_consulted_url: la página pública en la que te basas (obligatorio con RELEVANT y NOT_RELEVANT).",
                      "No uses la salida del sistema como fuente. Si dudas, UNCERTAIN.", "RELEVANT no significa que la empresa cumpla los gates ni que haya señal de crecimiento."]}


def order_key(seed, org_id): return hashlib.sha256(f"{seed}|{org_id}".encode()).hexdigest()


def build_rows(probe, seed="EXP-CS-001", holdout=0):
    found = {}
    for call in probe["calls"]:
        for r in call["records"]: found.setdefault(r["apollo_org_id"], {"r": r, "q": []})["q"].append(call["label"].split("_")[0])
    ids = sorted(found, key=lambda i: order_key(seed, i)); rows = []
    for n, i in enumerate(ids, 1):
        r = found[i]["r"]; rows.append({"review_id": f"HR-{n:03d}", "apollo_org_id": i, "name": r["name"], "domain": r["domain"] or "", "found_by": "|".join(found[i]["q"]), "sample_role": "PILOT_FROM_PROBE", **{c: "" for c in COLUMNS[6:]}})
    return rows


def to_csv(rows):
    b = io.StringIO(); w = csv.DictWriter(b, fieldnames=COLUMNS, lineterminator="\n"); w.writeheader(); [w.writerow(r) for r in rows]; return b.getvalue()


def validate(rows):
    """-> lista de problemas. Filas sin etiquetar no son error (pendientes)."""
    pr = []
    for r in rows:
        lab = (r.get("reviewer_label") or "").strip(); rid = r.get("review_id")
        if not lab: continue
        if lab not in LABELS: pr.append(f"{rid}: reviewer_label inválida «{lab}»"); continue
        rs = [x for x in (r.get("reason_codes") or "").split("|") if x.strip()]
        bad = [x for x in rs if x.strip() not in REASONS]
        if bad: pr.append(f"{rid}: motivos inválidos {bad}")
        if lab == "NOT_RELEVANT" and not rs: pr.append(f"{rid}: NOT_RELEVANT exige al menos un motivo")
        if lab in ("RELEVANT", "NOT_RELEVANT") and not (r.get("source_consulted_url") or "").strip(): pr.append(f"{rid}: falta source_consulted_url")
        if lab == "RELEVANT" and rs: pr.append(f"{rid}: RELEVANT no lleva motivos de error")
    return pr


def ground_truth(rows):
    p = validate(rows)
    if p: raise ValueError("; ".join(p[:6]))
    gt = [{"apollo_org_id": r["apollo_org_id"], "name": r["name"], "domain": r["domain"] or None, "label": r["reviewer_label"].strip(), "reasons": [x.strip() for x in (r.get("reason_codes") or "").split("|") if x.strip()],
           "source": r.get("source_consulted_url") or None, "reviewer": r.get("reviewer") or None, "reviewed_at": r.get("reviewed_at") or None, "label_kind": "HUMAN_RELEVANCE_NOT_FIT_VERIFIED"} for r in rows if (r.get("reviewer_label") or "").strip()]
    return gt


def metrics(rows):
    gt = ground_truth(rows); n = len(rows); c = {k: sum(1 for g in gt if g["label"] == k) for k in LABELS}; reasons = {}
    for g in gt:
        for x in g["reasons"]: reasons[x] = reasons.get(x, 0) + 1
    dec = c["RELEVANT"] + c["NOT_RELEVANT"]
    return {"rows": n, "labelled": len(gt), "pending": n - len(gt), "counts": c, "false_positives_by_reason": reasons,
            "discovery_precision_provisional": (round(c["RELEVANT"] / dec, 3) if dec else "NO_MEDIBLE"), "precision_denominator": dec, "uncertain_rate": (round(c["UNCERTAIN"] / len(gt), 3) if gt else "NO_MEDIBLE"),
            "complete": len(gt) == n, "caveat": "Provisional: muestra pequeña, sin intervalo; no es FIT_VERIFIED. Si pending>0 no se debe citar precisión."}
