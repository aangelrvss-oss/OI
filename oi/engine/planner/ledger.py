"""Ledger de presupuesto y autorización de llamadas (SIMULADO: no existe ninguna llamada real).
tool_call_budget y credit_budget son contabilidades SEPARADAS. Los créditos se comprueban antes de llamar solo con costes OBSERVADOS
(catálogo) y, después, con el gasto real. Modelo de coste por registro: tool + operation + calls + estimated/actual credits."""
import fnmatch


class BudgetDenied(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}"); self.code, self.detail = code, detail


class Ledger:
    def __init__(self, plan):
        b = plan["budget"]; self.plan = plan
        self.thr = b["thresholds"]; by = b["phase_allocations"]["by_phase"]
        self.alloc = {p: a["data_calls"] for p, a in by.items()}
        self.reserve_total = b["tool_call_budget"]["reserve"]
        self.max_calls = b["tool_call_budget"]["max"]
        self.infra_max = b["infra_calls"]["max"]
        self.credit_limits = dict(b["credit_budget"]["max"])
        self.credit_reserve = dict(b["reserve"]["credits"])
        self.phase_credit_alloc = {p: dict(a["credits"]) for p, a in by.items()}
        self.release = b["reserve"]["release_conditions"]
        self.spent = {p: 0 for p in self.alloc}
        self.reserve_used = 0
        self.infra_used = 0
        self.credits_used = {t: 0 for t in self.credit_limits}
        self.phase_credits = {p: {} for p in self.alloc}
        self.reserve_credits_used = {t: 0 for t in self.credit_limits}
        self.overrun = False
        self.records, self._n = [], 0

    # --- consulta
    def data_total(self): return sum(self.spent.values()) + self.reserve_used
    def utilization(self):
        u = {"tool_calls": self.data_total() / self.max_calls if self.max_calls else 0.0}
        for t, lim in self.credit_limits.items():
            if lim: u[f"credits.{t}"] = self.credits_used[t] / lim
        if self.infra_max: u["infra_calls"] = self.infra_used / self.infra_max
        return u
    def status(self):
        worst = max(self.utilization().values() or [0])
        return "hard" if (self.overrun or worst >= self.thr["hard"]) else ("soft" if worst >= self.thr["soft"] else "ok")
    def remaining(self):
        return {"tool_calls": self.max_calls - self.data_total(), "reserve_tool_calls": self.reserve_total - self.reserve_used,
                "credits": {t: lim - self.credits_used[t] for t, lim in self.credit_limits.items() if lim is not None},
                "by_phase": {p: self.alloc[p] - self.spent[p] for p in self.alloc}}

    # --- reservas (todas las comprobaciones ANTES de mutar)
    def request(self, phase, calls=1, kind="data", tool=None, operation=None, estimated_credits=None, purpose="normal"):
        if kind == "infra":
            if self.infra_max is not None and self.infra_used + calls > self.infra_max:
                raise BudgetDenied("INFRA_BUDGET_EXCEEDED", f"{self.infra_used}+{calls}>{self.infra_max}")
            self.infra_used += calls
            return self._rec(phase, kind, tool, operation, calls, estimated_credits, purpose)
        if phase not in self.alloc:
            raise BudgetDenied("PHASE_HAS_NO_ALLOCATION", phase)
        if self.overrun:
            raise BudgetDenied("CREDIT_LIMIT_OVERRUN", "el gasto real de créditos superó el límite: parada total")
        if purpose == "normal" and self.status() == "hard":
            raise BudgetDenied("HARD_THRESHOLD_REACHED", "umbral duro: solo la reserva puede cerrar el top-N")
        prov = tool.split(".")[0] if tool else None
        free = self.alloc[phase] - self.spent[phase]
        use_reserve_calls = False
        if purpose == "normal":
            if calls > free: raise BudgetDenied("PHASE_ALLOCATION_EXCEEDED", f"{phase}: pide {calls}, quedan {free}")
            if self.data_total() + calls > self.max_calls - self.reserve_total + self.reserve_used:
                raise BudgetDenied("NORMAL_POOL_EXCEEDED", "el pool normal no incluye la reserva")
        else:
            rel = self.release
            if purpose != rel["purpose"]: raise BudgetDenied("RESERVE_PURPOSE_INVALID", purpose)
            if phase not in rel["phases"]: raise BudgetDenied("RESERVE_PHASE_NOT_ALLOWED", phase)
            if self.alloc[phase] == 0 or self.spent[phase] / self.alloc[phase] < rel["phase_alloc_used_min"]:
                raise BudgetDenied("RESERVE_CONDITION_NOT_MET", f"asignación de {phase} usada < {rel['phase_alloc_used_min']:.0%}")
            if calls > self.reserve_total - self.reserve_used: raise BudgetDenied("RESERVE_EXHAUSTED", "")
            use_reserve_calls = True
        credit_from_reserve = False
        if estimated_credits and prov in self.credit_limits and self.credit_limits[prov] is not None:
            lim, pa = self.credit_limits[prov], self.phase_credit_alloc[phase].get(prov)
            if self.credits_used[prov] + estimated_credits > lim: raise BudgetDenied("CREDIT_LIMIT_EXCEEDED", prov)
            if pa is not None:
                sp = self.phase_credits[phase].get(prov, 0)
                if purpose == "normal":
                    if sp + estimated_credits > pa: raise BudgetDenied("PHASE_CREDIT_ALLOCATION_EXCEEDED", f"{phase}/{prov}: {sp}+{estimated_credits}>{pa}")
                else:
                    if pa == 0 or sp / pa < self.release["phase_alloc_used_min"]: raise BudgetDenied("RESERVE_CONDITION_NOT_MET", "créditos de la fase usados < umbral")
                    if sp + estimated_credits > pa + (self.credit_reserve.get(prov) or 0) - self.reserve_credits_used[prov]:
                        raise BudgetDenied("CREDIT_RESERVE_EXHAUSTED", prov)
                    credit_from_reserve = True
        # commit
        if purpose == "normal": self.spent[phase] += calls
        elif use_reserve_calls: self.reserve_used += calls
        if estimated_credits and prov in self.credit_limits:
            self.credits_used[prov] += estimated_credits
            self.phase_credits[phase][prov] = self.phase_credits[phase].get(prov, 0) + estimated_credits
            if credit_from_reserve:
                pa = self.phase_credit_alloc[phase].get(prov) or 0
                self.reserve_credits_used[prov] = max(0, self.phase_credits[phase][prov] - pa)
        return self._rec(phase, kind, tool, operation, calls, estimated_credits, purpose)

    def _rec(self, phase, kind, tool, operation, calls, est, purpose):
        self._n += 1
        r = {"record_id": f"R{self._n:04d}", "phase": phase, "kind": kind, "tool": tool, "operation": operation, "calls": calls, "purpose": purpose,
             "estimated_credits": est, "actual_credits": None, "status": "reserved"}
        self.records.append(r); return r["record_id"]

    def settle(self, record_id, actual_credits=None):
        r = next(x for x in self.records if x["record_id"] == record_id)
        r["status"] = "settled"; r["actual_credits"] = actual_credits
        if actual_credits is not None and r["tool"]:
            prov = r["tool"].split(".")[0]
            if prov in self.credits_used:
                delta = actual_credits - (r["estimated_credits"] or 0)
                self.credits_used[prov] += delta
                self.phase_credits[r["phase"]][prov] = self.phase_credits[r["phase"]].get(prov, 0) + delta
                lim = self.credit_limits.get(prov)
                if lim is not None and self.credits_used[prov] > lim: self.overrun = True
        return r


def authorize(plan, ledger, call, completed_phases=(), mode="dry_run"):
    """Decide si una llamada está permitida. Solo reglas; sin efectos externos. `units` = filas/empresas de la llamada (coste por unidad observado)."""
    deny = lambda code, d="": {"allowed": False, "code": code, "detail": d, "record_id": None}
    if mode != "dry_run":
        return deny("PLAN_NOT_AUTHORIZED_FOR_REAL_SPEND", "el planner no autoriza gasto real")
    if plan["status"] == "blocked":
        return deny("PLAN_BLOCKED")
    op, tool, phase = call.get("operation"), call.get("tool"), call["phase"]
    for f in plan["tool_policy"]["forbidden"]:
        if op and any(fnmatch.fnmatch(op, p) for p in f["match"]):
            return deny("FORBIDDEN_OPERATION", f["id"])
    if plan["tool_policy"]["status_by_tool"].get(tool) == "unavailable_on_current_plan":
        return deny("TOOL_UNAVAILABLE_ON_CURRENT_PLAN", tool)
    ph = next((p for p in plan["phases"] if p["phase_id"] == phase), None)
    if ph is None or ph["status"] != "planned":
        return deny("PHASE_NOT_IN_PLAN", phase)
    missing = [p for p in ph["prerequisites"] if p not in completed_phases]
    if missing:
        return deny("PREREQUISITES_NOT_MET", ",".join(missing))
    if (tool, op) not in {(s["selected_tool"], o) for s in ph["tool_selections"] for o in s["operations"]}:
        return deny("TOOL_NOT_SELECTED_FOR_PHASE", f"{tool}/{op}")
    crit = call.get("criterion_id")
    if phase in ("ENRICHMENT", "EVIDENCE") and call.get("kind", "data") == "data" and crit not in ph["active_criteria"]:
        return deny("NO_ACTIVE_CRITERION_FOR_CALL", str(crit))
    kind = "infra" if plan["tool_policy"]["tool_kinds"].get(tool) == "infra" else "data"
    est = call.get("estimated_credits")
    obs = plan["tool_policy"]["observed_credits_by_tool"].get(tool)
    if est is None and obs and obs["credits_per_unit"] is not None:
        est = obs["credits_per_unit"] * call.get("units", 1)
    try:
        rid = ledger.request(phase, call.get("calls", 1), kind, tool, op, est, call.get("purpose", "normal"))
    except BudgetDenied as e:
        return deny(e.code, e.detail)
    return {"allowed": True, "code": "OK", "detail": "", "record_id": rid, "estimated_credits": est}
