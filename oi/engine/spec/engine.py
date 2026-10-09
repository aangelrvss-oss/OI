"""Intent Engine: borrador del LLM -> validación determinista -> status -> spec versionado.
AUTORIDAD: el LLM propone; este módulo decide. Todo lo que el borrador afirme sobre status, validez,
definition_status, activation, definiciones explícitas, presupuesto, versiones o pesos se IGNORA y se registra."""
import copy
import datetime
import hashlib

from . import ENGINE_VERSION
from . import clarification as C
from . import validation as V
from .registry import Registry, DEFAULT_SCHEMA
from .text import norm, contains_phrase, numbers_in
from .versioning import SpecStore, canonical_hash

BUDGET_KEYS = {"data_calls_max", "infra_calls_max", "tool_credits_max", "max_time_minutes", "deep_research_top_n", "reserve_pct"}
COMPUTED = ("definition_status", "activation", "definition", "weight")
SUGGESTIONS = {
    "talent": "Puedes reformularlo como prospección de empresas que contratan este perfil (customer_prospect), que sí está soportado.",
    "supplier": "El descubrimiento de proveedores llegará en una fase posterior.",
    "partner": "El descubrimiento de partners llegará en una fase posterior.",
    "market_research": "La investigación de mercado llegará en una fase posterior.",
    "opportunity_discovery": "Reformúlalo indicando qué ofreces y a quién para tratarlo como customer_prospect."}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class IntentEngine:
    def __init__(self, store=None, registry=None, clock=None, schema_version=None):
        """schema_version: '1.0' (por defecto, compatibilidad) o '2.0' (offering.kind/target_function, business_model, entity_level)."""
        self.reg = registry or Registry(schema_version=schema_version or DEFAULT_SCHEMA)
        if registry is not None and schema_version and registry.schema_version != schema_version:
            raise ValueError(f"registry con schema {registry.schema_version} != schema_version pedida {schema_version}")
        self.store = store if store is not None else SpecStore()
        self.clock = clock or _now

    # ───────────────────────── API pública ─────────────────────────
    def process(self, draft, user_params=None, user_confirmations=None, user_inputs=None, parent=None):
        reg = self.reg
        issues, assumptions = [], []
        d = copy.deepcopy(draft) if isinstance(draft, dict) else {}
        user_params = user_params or {}
        confirmations = list(user_confirmations or [])
        inputs = list(user_inputs or [])

        # 0) autoridad: ignorar afirmaciones del LLM sobre estados/validez/versiones/presupuesto
        for k in ("valid", "status", "versions", "rejection", "budget"):
            if k in d:
                issues.append(V.issue("LLM_CLAIM_IGNORED", V.WARNING, "L3", f"$.{k}", f"El borrador afirmó '{k}'; solo el código lo decide (ignorado)."))
        ctl = d.get("control") or {}
        for k in ("status", "spec_id", "spec_version", "parent_spec_version", "content_hash", "created_at"):
            if ctl.get(k) not in (None, "draft"):
                issues.append(V.issue("LLM_CLAIM_IGNORED", V.WARNING, "L3", f"$.control.{k}", f"El borrador afirmó control.{k}; lo asigna el código (ignorado)."))

        # 1) linaje
        pspec = None
        if parent:
            pspec = self.store.get(*parent)
            if pspec is None:
                return self._error([V.issue("PARENT_NOT_FOUND", V.ERROR, "L1", "$.parent", f"No existe la versión padre {parent}.")])
            raw = pspec["control"]["raw_request"]
            if ctl.get("raw_request") and norm(ctl["raw_request"]) != norm(raw):
                issues.append(V.issue("LLM_CLAIM_OVERRIDDEN", V.WARNING, "L3", "$.control.raw_request",
                                      "En revisiones, la petición original es inmutable (evita ocultar términos relativos)."))
            inputs = list(pspec["traceability"].get("user_inputs", [])) + inputs
        else:
            raw = ctl.get("raw_request", "")
        intent = d.get("intent") or {}

        # 2) L1 sobre el envelope (control + intent)
        probe = copy.deepcopy(d); probe.setdefault("control", {})["raw_request"] = raw
        l1 = V.l1_envelope(probe, reg)
        if l1:
            return self._error(issues + l1)

        # 3) tipo de oportunidad no soportado -> unsupported_yet (sin L2/L3 sobre criterios)
        otype, etype = intent["opportunity_type"], intent["target_entity_type"]
        if otype not in V.SUPPORTED_TYPES or etype != "company":
            return self._finalize_unsupported(d, raw, otype, etype, issues, pspec, parent, inputs)

        # 4) construir criterios y aplicar confirmaciones del usuario (canal separado del LLM)
        criteria = []
        for c in d.get("criteria") or []:
            c = copy.deepcopy(c)
            for k in COMPUTED:
                if k in c and c[k] not in (None,):
                    issues.append(V.issue("LLM_CLAIM_IGNORED", V.WARNING, "L3", f"$.criteria[{c.get('id')}].{k}",
                                          f"El borrador afirmó '{k}' en el criterio {c.get('id')}; se recalcula (ignorado)."))
                c.pop(k, None)
            c.setdefault("params", {})
            criteria.append(c)
        by_id = {c["id"]: c for c in criteria if isinstance(c, dict) and "id" in c}
        explicit_ids, value_from_user = set(), set()
        for cf in confirmations:
            c = by_id.get(cf.get("criterion_id"))
            if c is None:
                issues.append(V.issue("CONFIRMATION_TARGET_MISSING", V.ERROR, "L3", "$.user_confirmations",
                                      f"La confirmación apunta a un criterio inexistente: {cf.get('criterion_id')}"))
                continue
            if not (cf.get("definition_text") or "").strip():
                issues.append(V.issue("CONFIRMATION_EMPTY", V.ERROR, "L3", "$.user_confirmations", "La definición confirmada no puede estar vacía."))
                continue
            if "value" in cf: c["value"] = cf["value"]; value_from_user.add(c["id"])
            if cf.get("params"): c["params"] = {**c.get("params", {}), **cf["params"]}
            c["definition"] = {"explicit": True, "text": cf["definition_text"], "confirmed_by": "user",
                               "definition_id": f"{cf['criterion_id']}@{cf['definition_text'][:24]}", "value": c.get("value"), "params": c.get("params")}
            explicit_ids.add(c["id"])

        # 5) presupuesto (solo por parámetro de usuario)
        budget, bissues = self._budget(user_params.get("budget") if "budget" in user_params else (pspec or {}).get("budget"))
        issues += bissues

        # 6) L1 completo sobre un spec provisional
        gov_in = d.get("governance") or {}
        provisional = self._assemble(d, raw, "pending", 1, None, "draft", criteria, d.get("restrictions") or {}, budget, gov_in, [], [], [], inputs, intent)
        issues += V.l1_full(provisional, reg)
        if any(i["severity"] == V.ERROR for i in issues):
            return self._error(issues, layers_stop="L1")

        # 7) L2
        infos = {}
        seen = set()
        for i, c in enumerate(criteria):
            if c["id"] in seen:
                issues.append(V.issue("DUPLICATE_CRITERION_ID", V.ERROR, "L2", f"$.criteria[{i}].id", f"id duplicado: {c['id']}"))
            seen.add(c["id"])
            iss, info = V.l2_criterion(c, i, reg, etype)
            issues += iss; infos[c["id"]] = info
            if c.get("evidence") is None and c["field"] in reg.fields and reg.fields[c["field"]].get("default_evidence"):
                c["evidence"] = copy.deepcopy(reg.fields[c["field"]]["default_evidence"])
            elif c.get("evidence"):
                c["evidence"]["origin"] = "llm"
        from oi.engine.planner.capabilities import PROVIDER_RE
        for c in criteria:       # D-047: el Search Spec describe QUÉ saber; nunca nombra proveedores
            for s_ in ((c.get("evidence") or {}).get("preferred_sources") or []):
                if PROVIDER_RE.match(str(s_)): issues.append(V.issue("PROVIDER_NAMED_IN_SPEC", V.ERROR, "L2", f"$.criteria[{c['id']}].evidence.preferred_sources", f"'{s_}' nombra un proveedor; el spec solo puede pedir clases de fuente (job_postings, official_website…)."))
        if any(i["severity"] == V.ERROR for i in issues):
            return self._error(issues, layers_stop="L2")

        # 8) L3
        corpus_n = norm(raw + " " + " ".join(inputs))
        questions = []
        # 8a grounding de criterios
        for i, c in enumerate(criteria):
            issues += V.ground_criterion(c, i, corpus_n, reg, c["id"] in value_from_user)
            if c["origin"] == "assumed":
                for tname, td in reg.term_defaults.items():
                    cd = td["criterion"]
                    if (c["field"], c["operator"], c.get("value")) == (cd["field"], cd["operator"], cd["value"]):
                        assumptions.append({"field": c["id"], "value": cd, "reason": f"Término «{tname}» interpretado con el catálogo: {td['basis']}",
                                            "basis": "term_catalog"})
        issues += V.l3_contradictions(criteria)
        if sum(1 for c in criteria if c["strength"] == "gate") > 5:
            issues.append(V.issue("TOO_MANY_GATES", V.WARNING, "L3", "$.criteria", "Más de 5 gates: riesgo de universo vacío."))
        if not (intent.get("objective") or "").strip():
            issues.append(V.issue("OBJECTIVE_MISSING", V.ERROR, "L3", "$.intent.objective", "Falta el objetivo."))
        # 8b geografía y exclusiones
        rs = d.get("restrictions") or {}
        geo, gi, ga = V.l3_geography(rs.get("geography"), corpus_n, reg)
        exc, ei, _ = V.l3_geography({"include": (rs.get("geography") or {}).get("exclude", [])}, corpus_n, reg)
        geo["exclude"] = exc["include"]
        issues += gi + ei; assumptions += ga
        for e in rs.get("excluded_entities") or []:
            if not contains_phrase(corpus_n, norm(e)):
                issues.append(V.issue("UNGROUNDED_EXCLUSION", V.ERROR, "L3", "$.restrictions.excluded_entities", f"La exclusión '{e}' no aparece en la petición."))
        # 8b2 requisito de plantilla: decisión EXPLÍCITA del usuario (no se inventa un rango para satisfacer al motor)
        ecr = None
        raw_ecr = rs.get("employee_count_requirement")
        if raw_ecr:
            st_ = (raw_ecr.get("source_text") or "").strip()
            if raw_ecr.get("value") != "NOT_APPLICABLE":
                issues.append(V.issue("EMPLOYEE_REQUIREMENT_INVALID", V.ERROR, "L3", "$.restrictions.employee_count_requirement", "Solo se admite el valor NOT_APPLICABLE; un requisito real se expresa como criterio."))
            elif not st_ or not contains_phrase(corpus_n, norm(st_)):
                issues.append(V.issue("UNGROUNDED_SIZE_DECISION", V.ERROR, "L3", "$.restrictions.employee_count_requirement", "NOT_APPLICABLE exige una decisión del usuario citada literalmente en su petición o en sus respuestas."))
            elif any(c["field"] == "firmographic.employee_count" and c["strength"] == "gate" for c in criteria):
                issues.append(V.issue("EMPLOYEE_REQUIREMENT_CONFLICT", V.ERROR, "L3", "$.criteria", "Hay un gate de plantilla y a la vez employee_count_requirement=NOT_APPLICABLE."))
            else:
                ecr = {"value": "NOT_APPLICABLE", "source_text": st_, "effect": "sin gate ni filtro de plantilla; el dato se usa solo como señal contextual cuando exista información fiable"}
                assumptions.append({"field": "restrictions.employee_count_requirement", "value": "NOT_APPLICABLE", "reason": "Decisión explícita del usuario: la plantilla no es un criterio del objetivo.", "basis": "user_decision"})
        # 8c cantidad
        quantity, qi, qa, qq = self._quantity(rs.get("quantity"), raw, corpus_n)
        issues += qi; assumptions += qa; questions += qq
        # 8d oferta
        off = intent.get("offering") or {}
        desc = (off.get("description") or "").strip()
        if not desc:
            questions.append(C.offering_missing())
        else:
            st = norm(off.get("source_text"))
            if not st or not contains_phrase(corpus_n, st):
                issues.append(V.issue("UNGROUNDED_OFFERING", V.ERROR, "L3", "$.intent.offering.source_text",
                                      "La oferta debe citar literalmente la petición del usuario (source_text)."))
            if not (off.get("problem_solved") or "").strip():
                issues.append(V.issue("OFFERING_PROBLEM_UNSPECIFIED", V.WARNING, "L3", "$.intent.offering.problem_solved",
                                      "No se indicó qué problema resuelve la oferta; limita la calidad de las señales de oportunidad."))
        # 8d2 (Search Spec v2) clasificación de la oferta, modelo de negocio y nivel de entidad
        v2 = self.reg.schema_version == "2.0"
        if not v2:
            ignored = [k for k in ("business_model", "entity_level") if k in intent] + [f"offering.{k}" for k in ("kind", "target_function") if k in off]
            if ignored: issues.append(V.issue("V2_FIELDS_IGNORED_IN_V1", V.WARNING, "L3", "$.intent", f"Campos de Search Spec v2 ignorados al procesar con el esquema 1.0: {ignored}. Use schema_version='2.0' para conservarlos."))
        if v2:
            kind = off.get("kind")
            if desc and kind is None: questions.append(C.offering_kind_missing())
            elif desc and kind == "UNKNOWN": questions.append(C.offering_kind_unknown())
            elif desc and kind is not None:
                assumptions.append({"field": "intent.offering.kind", "value": kind, "basis": "llm_interpretation",
                                    "reason": "Clasificación de la oferta propuesta por el intérprete a partir de lo citado por el usuario; confirmable. Decide a quién se recomienda contactar."})
            if not off.get("target_function"):
                assumptions.append({"field": "intent.offering.target_function", "value": "UNKNOWN", "basis": "engine_default", "reason": "El usuario no indicó a qué función de la empresa cliente va dirigida la oferta; no se inventa."})
            if not intent.get("business_model"):
                assumptions.append({"field": "intent.business_model", "value": ["unknown"], "basis": "engine_default", "reason": "No se indicó el modelo de negocio de las entidades objetivo."})
            if not intent.get("entity_level"):
                assumptions.append({"field": "intent.entity_level", "value": "UNKNOWN", "basis": "engine_default", "reason": "No se indicó el nivel de entidad (establecimiento/operador/grupo/empresa)."})
        # 8e presupuesto
        issues += V.l3_budget(budget, quantity.get("target"))
        if any(i["severity"] == V.ERROR for i in issues):
            return self._error(issues, layers_stop="L3")

        # 8f términos relativos (detección sobre la petición original)
        detected = V.detect_relative_terms(raw, reg)
        det_ids = {t for t, _ in detected}
        for c in criteria:
            for tid in c.get("defines_terms", []):
                if tid not in reg.relative_terms:
                    issues.append(V.issue("UNKNOWN_TERM_ID", V.ERROR, "L3", f"$.criteria[{c['id']}].defines_terms", f"'{tid}' no está en el léxico."))
                elif tid not in det_ids:
                    issues.append(V.issue("LLM_CLAIM_IGNORED", V.WARNING, "L3", f"$.criteria[{c['id']}].defines_terms",
                                          f"El término '{tid}' no aparece en la petición; el vínculo se ignora."))
        if any(i["severity"] == V.ERROR for i in issues):
            return self._error(issues, layers_stop="L3")
        unresolved = set()
        for tid, pat in detected:
            if ecr and any(m.get("field") == "firmographic.employee_count" for m in reg.relative_terms[tid].get("menu", [])):
                continue          # el usuario decidió que la plantilla no aplica: el término relativo de tamaño deja de preguntar
            linked = [c for c in criteria if tid in c.get("defines_terms", [])]
            resolved = any(c["id"] in explicit_ids and not infos[c["id"]]["value_missing"] and not infos[c["id"]]["params_missing"] for c in linked)
            if resolved:
                continue
            unresolved.add(tid)
            scope = reg.relative_terms[tid]["scope"]
            blocking = scope == "inclusion" or not linked or any(c["strength"] == "gate" for c in linked)
            questions.append(C.relative_term({"id": tid}, reg, blocking, unlinked=not linked))
        # 8g clasificación por criterio
        for c in criteria:
            inf = infos[c["id"]]
            base = not inf["value_missing"] and not inf["params_missing"]
            linked_unres = [t for t in c.get("defines_terms", []) if t in unresolved]
            defined = base and not linked_unres
            c["definition_status"] = "defined" if defined else "needs_definition"
            if not defined:
                c["activation"] = "proposed"
                if c["origin"] in ("user", "assumed") and not base and not linked_unres:
                    questions.append(C.criterion_incomplete(c))
            elif c["origin"] in ("user", "assumed"):
                c["activation"] = "active"
            else:
                c["activation"] = "active" if (c["id"] in explicit_ids or reg.policy["auto_activate_inferred_signals"]) else "proposed"
            c["weight"] = None
        pending_signals = [c for c in criteria if c["origin"] == "inferred" and c["activation"] == "proposed"
                           and not any(t in unresolved for t in c.get("defines_terms", []))]
        if pending_signals:
            questions.append(C.signals_confirmation(pending_signals))
        # 8h alcance
        has_ind = any(c["field"] == "firmographic.industry" and c["activation"] == "active" for c in criteria)
        has_geo = bool(geo["include"])
        if not has_ind and not has_geo:
            questions += [C.scope_segment(), C.scope_geography()]
        else:
            if not has_ind:
                issues.append(V.issue("SECTOR_UNSPECIFIED", V.WARNING, "L3", "$.criteria", "Sin sector: el universo incluye todos los sectores de la geografía indicada."))
            if ecr:
                issues.append(V.issue("SIZE_NOT_APPLICABLE", V.INFO if hasattr(V, "INFO") else V.WARNING, "L3", "$.restrictions.employee_count_requirement", "Plantilla declarada NOT_APPLICABLE por el usuario: sin filtro de tamaño."))
            elif not any(c["field"] == "firmographic.employee_count" and c["activation"] == "active" for c in criteria):
                issues.append(V.issue("SIZE_UNSPECIFIED", V.WARNING, "L3", "$.criteria",
                                      "Tamaño no especificado: no se aplica filtro (no se inventa un rango). El universo incluye micro y grandes empresas."))
        # 8i defaults de gobierno
        gov, ga2 = self._governance(gov_in, issues)
        assumptions += ga2
        if any(i["severity"] == V.ERROR for i in issues):
            return self._error(issues, layers_stop="L3")

        # 9) status
        shown, deferred = C.assemble(questions, (d.get("traceability") or {}).get("proposed_clarifications") or [])
        blocking = [x for x in questions if x["blocking"]]
        status = "needs_clarification" if blocking else "confirmed"
        if blocking and not any(x["blocking"] for x in shown):  # garantía: si hay bloqueantes, se muestran
            shown = [x for x in questions if x["blocking"]][:C.MAX_QUESTIONS_PER_TURN]

        spec = self._assemble(d, raw, None, None, None, status, criteria, {"geography": geo, "excluded_entities": rs.get("excluded_entities") or [],
                              "quantity": quantity, **({"employee_count_requirement": ecr} if ecr else {})}, budget, gov, assumptions, shown, deferred, inputs, intent)
        return self._persist(spec, issues, pspec, parent)

    def confirm(self, spec_id, version, confirmations, user_inputs=None):
        """Atajo: el usuario confirma/define criterios de una versión existente -> nueva versión."""
        sp = self.store.get(spec_id, version)
        if sp is None:
            return self._error([V.issue("PARENT_NOT_FOUND", V.ERROR, "L1", "$", f"No existe {spec_id} v{version}")])
        prior = [{"criterion_id": c["id"], "definition_text": c["definition"]["text"], "value": c["definition"]["value"],
                  "params": c["definition"]["params"]} for c in sp["criteria"] if c.get("definition")]
        crit = []
        for c in sp["criteria"]:
            c = copy.deepcopy(c)
            for k in COMPUTED: c.pop(k, None)
            crit.append(c)
        draft = {"meta": {"prompt_version": sp["versions"].get("prompt_version"), "author": "engine:confirm"},
                 "control": {"raw_request": sp["control"]["raw_request"], "language": sp["control"].get("language")},
                 "intent": sp["intent"], "criteria": crit,
                 "restrictions": {"geography": sp["restrictions"]["geography"], "excluded_entities": sp["restrictions"]["excluded_entities"],
                                  "quantity": sp["restrictions"]["quantity"],
                                  **({"employee_count_requirement": sp["restrictions"]["employee_count_requirement"]} if sp["restrictions"].get("employee_count_requirement") else {})},
                 "governance": self._carry_governance(sp),
                 "traceability": {"proposed_signals": sp["traceability"].get("proposed_signals", []), "llm_notes": sp["traceability"].get("llm_notes")}}
        return self.process(draft, user_confirmations=prior + list(confirmations), user_inputs=user_inputs, parent=(spec_id, version))

    @staticmethod
    def _carry_governance(sp):
        """Al re-procesar una versión, conserva la política de datos del usuario pero NO convierte defaults del motor en valores del usuario."""
        amap = {a["field"] for a in sp["traceability"]["assumptions"]}
        gov = {}
        if "governance.desired_action" not in amap:
            gov["desired_action"] = sp["governance"]["desired_action"]
        dp = copy.deepcopy(sp["governance"]["data_policy"])
        if "governance.data_policy.purpose" in amap:
            dp.pop("purpose", None)
        gov["data_policy"] = dp
        if sp["governance"].get("candidate_relevance"): gov["candidate_relevance"] = sp["governance"]["candidate_relevance"]
        return gov

    # ───────────────────────── internos ─────────────────────────
    def _budget(self, b):
        if b is None:
            return None, []
        out = []
        if b.get("source") == "unset":
            return None, []
        unknown = set(b) - BUDGET_KEYS - {"source"}
        for k in unknown:
            out.append(V.issue("BUDGET_UNKNOWN_KEY", V.ERROR, "L1", f"$.budget.{k}", f"Clave de presupuesto desconocida: {k}"))
        nb = {k: b.get(k) for k in BUDGET_KEYS}
        nb["tool_credits_max"] = b.get("tool_credits_max") or {}
        nb["source"] = "user_parameter"
        for tool in nb["tool_credits_max"]:
            if tool != "apollo":
                out.append(V.issue("BUDGET_UNKNOWN_TOOL", V.WARNING, "L3", f"$.budget.tool_credits_max.{tool}", f"Herramienta '{tool}' sin modelo de coste conocido."))
        return nb, out

    def _quantity(self, q_in, raw, corpus_n):
        out, ass, qs = [], [], []
        q_in = dict(q_in or {})
        target, st = q_in.get("target"), q_in.get("source_text")
        vague = V.detect_vague_quantifier(raw, self.reg)
        if st:
            nums = numbers_in(st)
            if norm(st) and contains_phrase(corpus_n, norm(st)) and target in nums:
                pass
            else:
                qs.append(C.quantity_unverified(target)); target = None; st = None
        elif target is not None and target != V.DEFAULT_QUANTITY:
            qs.append(C.quantity_unverified(target)); target = None
        if target is None and not qs:
            if vague:
                qs.append(C.quantity_vague(vague, self.reg))
            else:
                target = V.DEFAULT_QUANTITY
                ass.append({"field": "restrictions.quantity.target", "value": target,
                            "reason": "No se indicó cantidad; se aplica el default del motor.", "basis": "engine_default"})
        mx = q_in.get("max")
        if mx is not None and target is not None and mx < target:
            out.append(V.issue("QUANTITY_RANGE", V.ERROR, "L3", "$.restrictions.quantity", "quantity.max < quantity.target"))
        quantity = {"target": target, "max": mx}
        if q_in.get("fill_policy") == "TARGET_NOT_OBLIGATION": quantity["fill_policy"] = "TARGET_NOT_OBLIGATION"   # el objetivo NO obliga a rellenar la lista
        if st: quantity["source_text"] = st
        return quantity, out, ass, qs

    def _governance(self, g, issues):
        ass = []
        da = g.get("desired_action")
        if da is None:
            da = "shortlist"
            ass.append({"field": "governance.desired_action", "value": da, "reason": "Acción por defecto del MVP: lista de revisión humana.", "basis": "engine_default"})
        dp = dict(g.get("data_policy") or {})
        if not dp.get("purpose"):
            dp["purpose"] = "prospección B2B (customer_prospect)"
            ass.append({"field": "governance.data_policy.purpose", "value": dp["purpose"],
                        "reason": "Finalidad por defecto, PROVISIONAL hasta que se confirme (pregunta M.4 abierta).", "basis": "engine_default"})
        dp.setdefault("personal_data_fields", []); dp.setdefault("raw_retention_days", None)
        out = {"approval_required": True, "desired_action": da, "data_policy": dp}
        if g.get("candidate_relevance") in ("off", "conservative"): out["candidate_relevance"] = g["candidate_relevance"]
        return out, ass

    def _intent_block(self, intent, off):
        block = {"opportunity_type": intent.get("opportunity_type"), "target_entity_type": intent.get("target_entity_type"),
                 "objective": intent.get("objective"), "offering": off if off is not None else {"description": None, "problem_solved": None}}
        if self.reg.schema_version != "2.0" and isinstance(block["offering"], dict):
            block["offering"] = {k: v for k, v in block["offering"].items() if k not in ("kind", "target_function")}
        if self.reg.schema_version == "2.0":
            o = dict(block["offering"]); o.setdefault("kind", None); o["target_function"] = o.get("target_function") or "UNKNOWN"; block["offering"] = o
            block["business_model"] = list(intent.get("business_model") or ["unknown"]); block["entity_level"] = intent.get("entity_level") or "UNKNOWN"
        return block

    def _assemble(self, d, raw, spec_id, version, parent_v, status, criteria, restrictions, budget, gov, assumptions, shown, deferred, inputs, intent):
        tr = d.get("traceability") or {}
        off = intent.get("offering")
        spec = {
            "control": {"spec_id": spec_id or "pending", "spec_version": version or 1, "parent_spec_version": parent_v, "status": status,
                        "created_at": self.clock(), "raw_request": raw, "language": (d.get("control") or {}).get("language"), "content_hash": None},
            "intent": self._intent_block(intent, off),
            "criteria": criteria,
            "restrictions": {"geography": restrictions.get("geography", {"include": [], "exclude": []}),
                             "excluded_entities": restrictions.get("excluded_entities", []),
                             "quantity": restrictions.get("quantity") or {"target": None, "max": None},
                             **({"employee_count_requirement": restrictions["employee_count_requirement"]} if restrictions.get("employee_count_requirement") else {})},
            "budget": budget,
            "governance": gov if gov.get("approval_required") is not None else {"approval_required": (d.get("governance") or {}).get("approval_required", True),
                                                                                  **{k: v for k, v in gov.items() if k != "approval_required"}},
            "traceability": {"assumptions": assumptions, "open_questions": shown, "deferred_questions": deferred, "user_inputs": inputs,
                             "proposed_signals": tr.get("proposed_signals", []), "llm_notes": tr.get("llm_notes"),
                             "confidence": tr.get("confidence"), "llm_proposed_assumptions": tr.get("assumptions", [])},
            "versions": {"schema_version": self.reg.schema_version, "field_registry_version": self.reg.version, "lexicon_version": self.reg.lexicon_version,
                         "geo_catalog_version": self.reg.geo_version, "engine_version": ENGINE_VERSION,
                         "prompt_version": (d.get("meta") or {}).get("prompt_version")}}
        return spec

    def _stamp(self, spec, pspec, parent):
        if pspec:
            sid = pspec["control"]["spec_id"]
            spec["control"].update(spec_id=sid, spec_version=self.store.latest_version(sid) + 1, parent_spec_version=parent[1])
        else:
            spec["control"].update(spec_id=self.store.next_spec_id(), spec_version=1, parent_spec_version=None)
        spec["control"]["content_hash"] = canonical_hash(spec)
        return spec

    def _invariants(self, spec):
        out = []
        if spec["control"]["status"] == "confirmed":
            for c in spec["criteria"]:
                if c["activation"] == "active" and c["definition_status"] != "defined":
                    out.append(f"criterio activo no definido: {c['id']}")
                if c["dimension"] == "opportunity" and c["strength"] == "gate":
                    out.append(f"opportunity gate: {c['id']}")
                if c["activation"] == "active" and c["origin"] == "inferred" and not c.get("definition") and not self.reg.policy["auto_activate_inferred_signals"]:
                    out.append(f"inferido activo sin confirmación: {c['id']}")
            if any(q["blocking"] for q in spec["traceability"]["open_questions"]):
                out.append("confirmed con pregunta bloqueante")
        if spec["governance"]["approval_required"] is not True:
            out.append("approval_required != true")
        return out

    def _persist(self, spec, issues, pspec, parent):
        spec = self._stamp(spec, pspec, parent)
        l1b = V.l1_full(spec, self.reg)
        inv = self._invariants(spec)
        if l1b or inv:
            bad = [V.issue("INTERNAL_INVARIANT", V.ERROR, "L1", "$", m) for m in inv] + l1b
            return self._error(issues + bad, layers_stop="L1")
        self.store.append(spec)
        return self._result(spec["control"]["status"], issues, spec, True)

    def _finalize_unsupported(self, d, raw, otype, etype, issues, pspec, parent, inputs):
        supported = V.SUPPORTED_TYPES
        reason = (f"opportunity_type '{otype}' aún no está soportado en el MVP (solo: {', '.join(supported)})." if otype not in supported
                  else f"target_entity_type '{etype}' no está soportado para customer_prospect en el MVP.")
        captured = {"captured_unvalidated": True, "intent": d.get("intent"), "criteria": d.get("criteria") or [],
                    "restrictions": d.get("restrictions") or {}}
        un = [{"code": "UNSUPPORTED_OPPORTUNITY_TYPE" if otype not in supported else "TARGET_ENTITY_UNSUPPORTED",
               "message": reason, "requested_opportunity_type": otype}]
        spec = self._assemble(d, raw, None, None, None, "unsupported_yet", [], {}, None,
                              {"approval_required": True, "desired_action": "shortlist", "data_policy": {"purpose": None, "personal_data_fields": [], "raw_retention_days": None}},
                              [], [], [], inputs, d.get("intent") or {})
        spec["restrictions"] = {"geography": {"include": [], "exclude": []}, "excluded_entities": [], "quantity": {"target": None, "max": None}}
        spec["rejection"] = {"reason": reason, "requested_opportunity_type": otype, "supported_types": supported,
                             "suggestion": SUGGESTIONS.get(otype), "captured": captured}
        res = self._persist(spec, issues, pspec, parent)
        res["unsupported"] = un
        return res

    def _result(self, status, issues, spec, persisted):
        errs = [i for i in issues if i["severity"] == V.ERROR]
        layers = {}
        for L in ("L1", "L2", "L3"):
            li = [i for i in issues if i["layer"] == L]
            layers[L] = {"passed": not any(i["severity"] == V.ERROR for i in li), "issues": len(li)}
        return {"valid": status == "confirmed", "status": status, "errors": errs,
                "warnings": [i for i in issues if i["severity"] == V.WARNING],
                "clarifications": spec["traceability"]["open_questions"] if spec else [],
                "unsupported": [], "layers": layers, "spec": spec, "persisted": persisted}

    def _error(self, issues, layers_stop=None):
        r = self._result("error", issues, None, False)
        r["valid"] = False
        r["clarifications"] = []
        if layers_stop:
            order = ["L1", "L2", "L3"]
            for L in order[order.index(layers_stop) + 1:]:
                r["layers"][L] = {"passed": None, "issues": 0, "skipped": True}
        return r
