"""Versionado inmutable. Un SpecStore es append-only: nunca sobrescribe, nunca edita."""
import copy
import hashlib
import json
import os


class SpecImmutableError(Exception):
    pass


def canonical_hash(spec):
    s = copy.deepcopy(spec)
    s["control"]["content_hash"] = None
    blob = json.dumps(s, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class SpecStore:
    def __init__(self, path=None):
        self._specs = {}
        self._counter = 0
        self.path = path
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    sp = json.loads(line)
                    self._specs[(sp["control"]["spec_id"], sp["control"]["spec_version"])] = sp
                    self._counter = max(self._counter, int(sp["control"]["spec_id"].split("-")[1]))

    def next_spec_id(self):
        self._counter += 1
        return f"spec-{self._counter:04d}"

    def latest_version(self, spec_id):
        vs = [v for (sid, v) in self._specs if sid == spec_id]
        return max(vs) if vs else 0

    def get(self, spec_id, version):
        sp = self._specs.get((spec_id, version))
        return copy.deepcopy(sp) if sp else None

    def versions(self, spec_id):
        return sorted(v for (sid, v) in self._specs if sid == spec_id)

    def append(self, spec):
        key = (spec["control"]["spec_id"], spec["control"]["spec_version"])
        if key in self._specs:
            raise SpecImmutableError(f"{key} ya existe: las versiones son inmutables; crea una versión nueva.")
        if spec["control"]["content_hash"] != canonical_hash(spec):
            raise SpecImmutableError("content_hash no coincide con el contenido")
        self._specs[key] = copy.deepcopy(spec)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(spec, ensure_ascii=False, sort_keys=True) + "\n")

    def verify_integrity(self):
        return [k for k, sp in self._specs.items() if sp["control"]["content_hash"] != canonical_hash(sp)]
