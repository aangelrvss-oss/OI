"""Productores de evidencia. HOY solo existe el SINTÉTICO (lee el dataset del caso). El productor real (web/Firecrawl) NO existe: pedirlo lanza RealModeBlocked.
Interfaz: produce(entity_keys) -> lista de evidence_input.v1 (sin transformar). El pipeline las valida con oi.evidence.intake."""
import copy


class SyntheticEvidenceProducer:
    data_mode = "SYNTHETIC"
    name = "synthetic_dataset"

    def __init__(self, dataset):
        self._items = [copy.deepcopy(i) for i in dataset.get("evidence", [])]
        self.requests = 0

    def produce(self, entity_keys):
        self.requests += 1; keys = set(entity_keys)
        return [copy.deepcopy(i) for i in self._items if i["subject"]["entity"] in keys]


class ReplayEvidenceProducer:
    """Evidencia aportada por una persona / página oficial / fuente verificable y GUARDADA en un fichero (no consulta nada). Cada item declara su `evidence_origin`; este productor NO crea evidencia."""
    data_mode = "REAL"
    name = "replay_evidence_file"

    def __init__(self, items):
        self._items = [copy.deepcopy(i) for i in items]; self.requests = 0

    def produce(self, entity_keys):
        self.requests += 1; keys = set(entity_keys)
        return [copy.deepcopy(i) for i in self._items if i["subject"]["entity"] in keys]


def make_producer(mode, *, dataset=None, items=None):
    from oi.pipeline.guard import RealModeBlocked, real_mode_blockers
    if mode == "replay-real": return ReplayEvidenceProducer(items or [])
    if mode == "offline": return SyntheticEvidenceProducer(dataset or {})
    raise RealModeBlocked(real_mode_blockers() + ["no existe productor de evidencia real (web/Firecrawl) en este bloque"])
