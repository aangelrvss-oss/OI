"""Entrada de evidencia: valida el contrato de entrada (evidence_input.v1) y lo convierte en la evidencia del registro (common.v1 #/$defs/evidence).

Reglas que hace cumplir (no son opcionales):
  * evidence_strength se DERIVA por reglas (oi/evidence/strength.py); la `confidence` declarada por el productor se conserva como `producer_confidence` y NO decide nada.
  * UNKNOWN != PASS · INFERRED != KNOWN · CONFLICTING != PASS: el estado de la afirmación viaja intacto; nunca se promueve.
  * Un productor OFFLINE solo puede declarar data_mode SYNTHETIC o TEST_FIXTURE.
"""
from oi.contracts.validator import validate
from . import strength as ST


class EvidenceIntakeError(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems[:5])); self.problems = problems


ORIGINS = ("APOLLO_FREE_LOOKUP", "APOLLO_ENRICHMENT", "FIRST_PARTY_WEB", "HUMAN_REVIEW", "OTHER_VERIFIABLE")


def check(items, *, offline=True, require_origin=False):
    """-> lista de problemas (vacía = válido)."""
    problems = []; seen = set()
    for i, it in enumerate(items):
        errs = validate(it, "evidence_input.v1.schema.json")
        for e in errs[:3]: problems.append(f"[{i}] {it.get('evidence_id')}: {e['path']}: {e['message']}")
        eid = it.get("evidence_id")
        if eid in seen: problems.append(f"[{i}] evidence_id duplicado: {eid}")
        seen.add(eid)
        if offline and it.get("data_mode") not in ("SYNTHETIC", "TEST_FIXTURE"): problems.append(f"[{i}] {eid}: un productor offline solo puede declarar data_mode SYNTHETIC o TEST_FIXTURE (recibido {it.get('data_mode')!r})")
        if require_origin and it.get("evidence_origin") not in ORIGINS: problems.append(f"[{i}] {eid}: evidence_origin obligatorio y de {ORIGINS} (recibido {it.get('evidence_origin')!r})")
        if it.get("evidence_origin") == "APOLLO_FREE_LOOKUP" and (it.get("field") or "").split(":")[0] == "company" and it.get("field") not in ("company:apollo_org_id", "company:name", "company:domain", "company:website"):
            problems.append(f"[{i}] {eid}: el lookup gratuito de Apollo solo puede afirmar id/nombre/dominio/web; no {it.get('field')}")
        if it.get("evidence_origin") == "APOLLO_ENRICHMENT" and it.get("claim_status") == "KNOWN": problems.append(f"[{i}] {eid}: un dato de Apollo (enrichment) nunca es KNOWN por sí solo; como máximo INFERRED/ESTIMATED")
        if it.get("source_class") == "UNVERIFIED" and it.get("claim_status") == "KNOWN": problems.append(f"[{i}] {eid}: una fuente UNVERIFIED no puede ser KNOWN")
    return problems


def to_record_evidence(item, today):
    """evidence_input.v1 -> evidencia del registro. La fuerza se deriva; el resto se copia sin promover estados."""
    s = ST.derive(item["source_class"], item["claim_status"], dated_on=item.get("dated_on"), today=today, corroborated=item.get("corroborated", False))
    out = {"evidence_id": item["evidence_id"], "field": item["field"], "value": item["value"], "source_class": item["source_class"], "source_url": item.get("source_url"),
           "source_label": item.get("source_label"), "retrieved_at": item["retrieved_at"], "claim_status": item["claim_status"], "evidence_strength": s,
           "note": item.get("note"), "producer_confidence": item["confidence"]}
    if item.get("evidence_origin"): out["evidence_origin"] = item["evidence_origin"]
    return out


def ingest(items, today, *, offline=True, require_origin=False):
    """Valida y convierte. Lanza EvidenceIntakeError ante cualquier problema (nada se ingiere a medias)."""
    p = check(items, offline=offline, require_origin=require_origin)
    if p: raise EvidenceIntakeError(p)
    return [to_record_evidence(i, today) for i in items]
