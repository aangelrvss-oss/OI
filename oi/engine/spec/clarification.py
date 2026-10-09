"""Motor de aclaración. Genera preguntas estructuradas SOLO desde reglas deterministas.
Las preguntas propuestas por el LLM entran como 'confirmation' no bloqueante y nunca cambian el status."""
MAX_QUESTIONS_PER_TURN = 3

# prioridad: menor = antes
_PRIORITY = {"OFFERING_MISSING": 1, "OFFERING_KIND_MISSING": 1, "OFFERING_KIND_UNKNOWN": 8, "RELATIVE_TERM_INCLUSION": 2, "RELATIVE_TERM_UNLINKED": 2, "SCOPE_SEGMENT": 3,
             "SCOPE_GEOGRAPHY": 3, "QUANTITY_VAGUE": 4, "QUANTITY_UNVERIFIED": 4, "CRITERION_INCOMPLETE": 5,
             "RELATIVE_TERM_RANKING_CONFIRMATION": 6, "INFERRED_SIGNALS_NEED_CONFIRMATION": 7, "llm_proposed": 9}


def q(id_, rule, question, why, blocking, options=None, origin="deterministic"):
    return {"id": id_, "kind": "blocking" if blocking else "confirmation", "question": question,
            "why_it_matters": why, "options": options or [], "blocking": blocking, "trigger_rule": rule, "origin": origin}


def offering_missing():
    return q("Q-OFFERING", "OFFERING_MISSING",
             "¿Qué producto o servicio ofreces y qué problema resuelve a tus clientes?",
             "Sin la oferta no se puede definir qué señales indicarían necesidad, solo qué empresas encajan por perfil.", True)


def offering_kind_missing():
    return q("Q-OFFERING-KIND", "OFFERING_KIND_MISSING",
             "¿Lo que ofreces es un producto para reventa, un software, un servicio, consultoría, equipamiento u otra cosa?",
             "Decide a quién se recomienda contactar en cada empresa (no es lo mismo vender producto a un distribuidor que software a una constructora).", True,
             ["PRODUCT_FOR_RESALE", "SOFTWARE", "SERVICE", "CONSULTING", "EQUIPMENT", "OTHER"])


def offering_kind_unknown():
    return q("Q-OFFERING-KIND-UNKNOWN", "OFFERING_KIND_UNKNOWN",
             "No he podido clasificar tu oferta (producto para reventa / software / servicio / consultoría / equipamiento). ¿Cuál es?",
             "Mientras sea UNKNOWN el sistema puede buscar empresas, pero NO recomendará a quién contactar (no se adivina qué se vende).", False,
             ["PRODUCT_FOR_RESALE", "SOFTWARE", "SERVICE", "CONSULTING", "EQUIPMENT", "OTHER"])


def relative_term(term, reg, blocking, unlinked=False):
    t = reg.relative_terms[term["id"]]
    why = ("Un término relativo no es un criterio medible. No elijo yo la definición: la defines tú y queda versionada."
           if blocking else "Propongo deficiencias observables; no se activan hasta que las confirmes.")
    rule = ("RELATIVE_TERM_UNLINKED" if unlinked else
            "RELATIVE_TERM_INCLUSION" if blocking else "RELATIVE_TERM_RANKING_CONFIRMATION")
    return q(f"Q-TERM-{term['id']}", rule, t["question"], why, blocking, [m["label"] for m in t["menu"]])


def scope_segment():
    return q("Q-SCOPE-SEGMENT", "SCOPE_SEGMENT", "¿A qué tipo de empresas o sector te diriges?",
             "Sin sector ni geografía el universo es demasiado abierto: cambia el resultado y el coste en órdenes de magnitud.", True)


def scope_geography():
    return q("Q-SCOPE-GEO", "SCOPE_GEOGRAPHY", "¿En qué geografía (país, región o ciudad)?",
             "Sin sector ni geografía el universo es demasiado abierto: cambia el resultado y el coste en órdenes de magnitud.", True)


def quantity_vague(word, reg):
    v = reg.vague_quantifiers
    return q("Q-QUANTITY", "QUANTITY_VAGUE", v["question"], f"«{word}» no es una cantidad; el volumen determina el coste.", True, v["options"])


def quantity_unverified(n):
    return q("Q-QUANTITY", "QUANTITY_UNVERIFIED", f"¿Confirmas que necesitas {n} resultados?",
             "No he podido verificar la cantidad en tu petición.", True)


def criterion_incomplete(c):
    return q(f"Q-CRIT-{c['id']}", "CRITERION_INCOMPLETE",
             f"El criterio «{c.get('label') or c['field']}» está incompleto: indica el umbral/valor y, si aplica, el periodo.",
             "Un criterio sin valor o periodo no puede evaluarse ni activarse.", True)


def signals_confirmation(criteria):
    return q("Q-SIGNALS", "INFERRED_SIGNALS_NEED_CONFIRMATION",
             "He propuesto estas señales de oportunidad (inferidas, no pedidas por ti). ¿Cuáles activo?",
             "Las señales inferidas por el LLM no se activan sin tu confirmación; sin ellas solo se evalúa el encaje (Fit).",
             False, [f"{c['id']}: {c.get('label') or c['field']}" for c in criteria])


def assemble(questions, llm_questions=()):
    """Ordena (bloqueantes primero, luego prioridad) y aplica el tope por turno. Devuelve (mostradas, diferidas)."""
    qs = sorted(questions, key=lambda x: (not x["blocking"], _PRIORITY.get(x["trigger_rule"], 8)))
    for lq in llm_questions:
        qs.append(q(f"Q-LLM-{len(qs)}", "llm_proposed", lq.get("question", ""), lq.get("why_it_matters", ""), False,
                    lq.get("options"), origin="llm"))
    seen, uniq = set(), []
    for x in qs:
        if x["id"] not in seen and x["question"]:
            seen.add(x["id"]); uniq.append(x)
    return uniq[:MAX_QUESTIONS_PER_TURN], uniq[MAX_QUESTIONS_PER_TURN:]
