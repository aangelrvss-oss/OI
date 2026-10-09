"""Intérprete LOCAL y DETERMINISTA (sin LLM, sin red): necesidad en lenguaje natural -> BORRADOR de Search Spec v2.

Solo extrae lo que el texto dice LITERALMENTE; lo que no aparece queda en null/UNKNOWN y el motor lo convierte en pregunta.
No inventa umbrales, cantidades, funciones destinatarias ni códigos sectoriales (el sector se resuelve con el mapeo curado).
Sirve para ejecutar `oi run` offline y reproducible. Un intérprete LLM real sustituiría a este módulo con el mismo contrato de salida.
"""
import json
import os
import re

from oi import paths
from .sector_mapping import load_mapping
from .text import norm

VERSION = "mock_interpreter@1.0.0"
OFFERING_RULES = [("CONSULTING", r"consultor[ií]a[s]?(?: [a-záéíóúñ]+){0,3}"), ("SOFTWARE", r"software(?: de [a-záéíóúñ]+| [a-záéíóúñ]+)?"),
                  ("EQUIPMENT", r"(?:maquinaria|equipos?)(?: [a-záéíóúñ]+){0,2}"), ("SERVICE", r"servicios?(?: de [a-záéíóúñ]+)?")]
GROWTH_RE = re.compile(r"\ben crecimiento\b|\bque (?:est[eé]n )?crecen\b|\bcreciendo\b", re.I)
QTY_RE = re.compile(r"\b(\d{1,4})\s+(?:empresas|compa[ñn][ií]as|constructoras|organizaciones)\b", re.I)
CUSTOMER_RE = re.compile(r"clientes?\b|que (?:puedan |pudieran )?(?:necesit\w+|comprar\w*|contrat\w+)", re.I)


def _countries():
    with open(os.path.join(paths.SPEC_RULES, "geo_catalog.v1.json"), encoding="utf-8") as f: return json.load(f)["countries"]


def interpret(raw, country_for_mapping="ES"):
    n = norm(raw); toks = set(re.findall(r"[a-z0-9]+", n))
    mapping = load_mapping(country_for_mapping) or {"entries": []}
    criteria, notes = [], []
    # sector: SOLO si algún término del usuario está en el mapeo curado; si no, no se crea criterio (se anota)
    sector = None
    for e in mapping["entries"]:
        for u in sorted(e["user_terms"], key=len, reverse=True):
            if re.search(r"\b" + re.escape(norm(u)) + r"\b", n): sector = (e, u); break
        if sector: break
    if sector:
        e, u = sector
        criteria.append({"id": "G1", "label": f"Sector: {u}", "dimension": "fit", "field": "firmographic.industry", "operator": "eq", "value": u, "strength": "gate", "origin": "user", "source_text": u})
    else: notes.append("Ningún término de sector del texto está en el mapeo curado: no se crea criterio de sector (MAPPING_REQUIRED).")
    m = GROWTH_RE.search(raw)
    if m: criteria.append({"id": "C1", "label": "En crecimiento", "dimension": "opportunity", "field": "growth.employee_growth_pct", "operator": "gte", "value": None, "strength": "preferred", "origin": "user", "source_text": m.group(0)})
    kind, desc = None, None
    for k, rx in OFFERING_RULES:
        mm = re.search(rx, raw, re.I)
        if mm: kind, desc = k, mm.group(0).strip(); break
    geo = [{"name": c["code"], "level": "country"} for c in _countries() if toks & set(c["aliases"])]
    qm = QTY_RE.search(raw)
    entity = "company" if re.search(r"empresas|compa[ñn][ií]as|constructoras|organizaciones", raw, re.I) else None
    otype = "customer_prospect" if CUSTOMER_RE.search(raw) else None
    draft = {"meta": {"prompt_version": VERSION, "author": "mock:deterministic", "fixture": True},
             "control": {"raw_request": raw, "language": "es"},
             "intent": {"opportunity_type": otype, "target_entity_type": entity, "objective": raw.rstrip(". "),
                        "offering": {"description": desc, "problem_solved": None, "source_text": desc, "kind": kind or "UNKNOWN", "target_function": "UNKNOWN"},
                        "business_model": list(sector[0]["business_model_hint"]) if sector else ["unknown"], "entity_level": "COMPANY" if entity == "company" else "UNKNOWN"},
             "criteria": criteria, "restrictions": {"geography": {"include": geo, "exclude": []}, "excluded_entities": [], "quantity": {"target": int(qm.group(1)) if qm else None, "source_text": qm.group(0) if qm else None}},
             "traceability": {"proposed_signals": [], "llm_notes": "Extracción literal sin LLM. " + " ".join(notes)}}
    return draft
