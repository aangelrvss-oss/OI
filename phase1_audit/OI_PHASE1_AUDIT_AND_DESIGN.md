> **Versión pública redactada (2026-10-09).** Se han sustituido nombres de empresas evaluadas, de personas y de dominios de terceros por etiquetas neutras. El análisis, las reglas y los grados no cambian. El original se conserva en la copia privada del proyecto.

# Opportunity Intelligence · Fase 1 · Auditoría y diseño de generalización

Fecha: 8 oct 2026 · Estado: **solo diagnóstico y propuesta. No se ha implementado, borrado ni modificado nada del repositorio, y no se ha gastado ningún crédito ni usado ninguna herramienta de pago.**
El experimento del piloto de reventa sigue cerrado e intacto (`RESELLER_PILOT_EXPERIMENT = COMPLETE`).

## 0. Resumen honesto (leer esto primero)

1. **Existe un núcleo real y valioso, pero no es un producto.** Hay un motor determinista bien gobernado (Search Spec validado en 3 capas → Research Plan declarativo → executor con tope de créditos, aprobación por hash y ledger encadenado), con unos 310 tests y mutation testing. Tiene una disciplina rara y buena: *el LLM propone, el código decide*, y *UNKNOWN nunca se convierte en PASS*.
2. **El pipeline completo nunca ha corrido solo.** Toda llamada real a un proveedor se hace *fuera* del código (relay manual: se escribe una petición pendiente, alguien llama por MCP y pega la respuesta). No hay un punto de entrada tipo `oi run "<necesidad>"`.
3. **El único E2E dirigido por el motor (MVP neutral: «empresas españolas 50-500 empleados, automatización con IA») dio resultado comercial nulo:** 0 elegibles, 12 «pendientes de evidencia», cobertura media de señales 2,38 %, y 6 de las 12 empresas enriquecidas eran del ecosistema de empleo/RRHH y 3 medios. Es decir, **el Test 1 de esta fase ya se ejecutó en vivo y falló comercialmente** (el MVP lo declaró `PARTIAL`, y tiene razón).
4. **La página comercial del piloto de reventa se ve bien porque detrás hay trabajo humano**, no motor: evaluación SÍ/DUDOSA/NO de un revisor, investigación web manual por empresa y 12 diccionarios escritos a mano (`build_ce.py`). Es la dirección correcta de producto, pero hoy es una **maqueta alimentada a mano**, no una salida del motor.
5. **La generalización a nivel de Search Spec funciona mejor de lo esperado** (ver §6: los 4 tests se representan y el motor pide aclaración donde corresponde). **Donde se rompe es aguas abajo**: mapeo sector→taxonomía del proveedor, evidencia web, contactos, evaluación y presentación.

**Veredicto contra tu criterio de éxito:** hoy **no** puedes decir «Opportunity Intelligence ya es un motor general de investigación comercial que recibe una necesidad, encuentra y califica empresas, justifica por qué lo son y recomienda cómo contactarlas». Puedes decir: «tengo un motor general de *especificación y gobierno* de la investigación, y una demostración manual de la salida». La sección 7 dice exactamente qué falta.

### Método y fiabilidad de esta auditoría
- Lectura estática del repositorio (`/mnt/user-data/outputs`, 245 `.py`, ~962 archivos), del motor vivo (`/home/claude/intent_engine`) y de su instantánea `intent_engine_fase3i.zip`. Se repartió en tres revisiones de solo lectura (motor; calificación/evidencia; capas del caso del piloto de reventa) y **yo verifiqué directamente** las afirmaciones clave: scoring `NOT_IMPLEMENTED` en el runner, `apollo.people_search` solo catalogado, el literal `"madrid"` en `runner.py`/`geography.py`, copias idénticas (`planner_data/*.v1` = `archive_v1`; `build_3f.py`, `scoring_3g.py` idénticos al motor), el informe final del MVP y el comportamiento del Spec engine (ver §6).
- **No ejecuté la batería de tests.** Los números de tests y de duplicados son de lectura/diff. Las referencias `archivo:línea` del motor son de la instantánea `fase3i`; el motor vivo difiere (runner/config/reporting modificados; añadidos `domain_precheck.py` e `identity_contract/`, que son genéricos) y **no se auditó línea a línea**.
- Lo único ejecutado: el Spec engine (offline, stdlib, sin red ni coste) sobre 4 borradores nuevos; scripts y salida guardados en `generalization_test/`.

---

## 1. AUDIT — qué existe

### 1.1 Mapa por etapa

| Etapa | Estado | Real / simulado | Generalidad |
|---|---|---|---|
| **Search Spec** | Real, madura. Validación L1 (schema) / L2 (registry) / L3 (semántica, grounding por cita literal, términos relativos), versionado append-only con hash | Real. El borrador lo escribe Claude *en la conversación*; el código no llama a ningún LLM | Mecanismo general. Solo `customer_prospect` está soportado (`talent`, `supplier`, `partner`… → `unsupported_yet`). Léxico, mensajes, `pyme` y catálogo geográfico son solo ES |
| **Research Plan** | Real, declarativo (`ruleset.v2.json`), 19 invariantes, `spend_authorized=false` | Real, pero **solo es una especificación**; no ejecuta nada | Reglas en datos (bien). Vinculado a Apollo (parámetros, nombres de campo, presupuesto `max_apollo_credits`) |
| **Discovery** | Parcial: solo Apollo `organizations_lookup`, página 1 | Real vía relay manual | Las consultas (NAICS/keywords) **no las genera el motor**: se inyectan por caso. En el MVP el planner rechazó las consultas propuestas (`organization_num_employees_ranges` no permitido) → pool de 50 sin diversidad |
| **Entity Resolution** | Real (id → dominio → nombre+ciudad; fuzzy solo para revisión) | Real | Sufijos legales y calificadores españoles. No persistente |
| **Cheap Gates** | Real: dedupe, exclusiones, GEO, EMP, `candidate_relevance`, `domain_precheck`(vivo) | Real y offline. `relevance_v2` es **PROPUESTA no medida** (sin precisión/recall) | Reglas por datos, pero listas de ruido en español/`gob.es` |
| **Enrichment** | Real, solo Apollo | Relay manual; en el MVP las respuestas se **transcribieron a mano como subconjunto** y el lote 1 se completó después (`RELAY_AMENDMENTS`) | Un solo proveedor, un solo adaptador, sin heredar de la interfaz base |
| **Evidence** | Motor de contrato real (`evidence_contract_4b`) con estados KNOWN/INFERRED/ESTIMATED/UNKNOWN/CONFLICTING y fiabilidad de fuente | Se cableó por primera vez en el MVP, solo para identidad/GEO/EMP/O4. **No existe ningún productor de evidencia first-party web ni DIRECT** | Política con literales (ventanas 90/365/730 días) copiados, no derivados del Spec |
| **Scoring** | D010 v0.2 existe como motor puro (intervalar, elegibilidad, ranking Pareto), `calibrated: False` | **Tres copias** (`experiment_3h`, `experiment_3i`, `d010_v0_2_official`). El `runner` aún devuelve `"scoring": "NOT_IMPLEMENTED"` | Reglas en dict (parametrizable en teoría); IDs de señales (`F1,FIT_KW,T1,P1,O2,O4,O5`) repetidos en 5 archivos y heredados del piloto por proyectos de 3C |
| **Contact Enrichment** | **Antes de esta semana: solo planeado** (`apollo.people_search` = `deferred`, `NOT_SUPPORTED_IN_3B`). Ahora: capa hecha a mano | Mi capa (`build_ce.py`) son 12 diccionarios Python escritos a mano tras investigación web manual | El esquema es reutilizable; los datos y la lógica (roles de Compras) no |
| **Output** | Motor: JSON + CSV de revisión. Comercial: HTML estático | `template.html` + `make_commercial_html.py` (≈35 parches de texto `rep(old,new)` con asserts) | No hay schema datos→UI. Todo en español, con 30/12/3/15 incrustados |

### 1.2 Realidad del flujo de datos
- **Reales:** discovery y enrichment de Apollo (relay), lectura web gratuita de mi investigación de contactos, evaluación humana de un revisor.
- **Simulados/sintéticos:** `FixtureTransport`, `executor/synthetic.py`, `planner/ledger.py` y `simulate.py` (marcados como simulación), 3H D010 (`SIMULATION_ONLY`), evaluadores de title-families y hiring-velocity probados solo con fixtures.
- **Manuales:** el relay, el contacto (12 diccionarios), la selección T3, el feedback, los diffs de hash (`*_HASH_DIFF.json` no los genera ningún script encontrado).
- **Dependientes de datos estáticos:** `known_set`/`data_4e` leen archivos de ejecuciones antiguas; `build_user_result_data.py` parsea prosa en español de un `.md` (`assert len(c)==8`); hashes congelados en `run_3i.py` impiden relanzar con datos nuevos.
- **No encontrado:** cliente HTTP vivo, scheduler, base de datos persistente, cualquier adaptador distinto de Apollo, productor de evidencia first-party, calibración de pesos, generación de `RULES`/`POLICY` desde el Spec, Gate 2 como código, firmas de aprobación (el aprobador es texto libre).

### 1.3 Qué es reutilizable de verdad
- **Patrón de autoridad** *LLM propone / código decide* con canales separados (`user_params`, `user_confirmations`, `user_inputs`) y `LLM_CLAIM_IGNORED`.
- **Pipeline de validación del Spec** (L1/L2/L3) y `SpecStore` con hash; `mini_jsonschema`.
- **Plan layer:** motor de reglas, presupuesto con `waterfill`, `stopping`, invariantes (sin las líneas Apollo/OpenRush).
- **Semántica de evidencia:** UNKNOWN≠PASS/FAIL, INFERRED no se promueve, frescura medida sobre la fecha del evento, `identity_gate` USABLE/HELD/QUARANTINED, `dedupe`, `group_conflicts`.
- **Executor:** máquina de estados, ledger encadenado reserva/liquidación, `RecordedTransport` idempotente por hash de entrada, checkpoint de aprobación ligado al hash de la shortlist, `domain_precheck` y `identity_contract` (vivos y genéricos).
- **Método:** build → test → mutation test → sellado con hash padre.
- **UX (como componentes):** drawer con foco/Esc/deep link, filtros `FDEF/F/pass()`, tabla de evidencia, badges de confianza, export CSV, tema claro/oscuro, inyección única de JSON.
- **Principio de contactos:** `ev()`, el conjunto de canales, tipos PERSON/DEPARTMENT/GENERIC y «nunca inferir un email».

### 1.4 Lógica específica del piloto de reventa / alimentación / proveedores / distribución
(Archivo:línea en el repo; ninguna aparece en el *código* del motor: ahí solo hay residuos del piloto por proyectos de 3C y de España.)
- Matriz de consultas `RP-(MAY|DIS|CAD)-(MAD|CYL|ESP)` (`case_code/reseller_observation.py:8`, `human_review_batch_1/select_batch1.py:12`), segmentos `MAY/DIS/CAD` y tiers Madrid > Castilla y León > resto (`reseller_geo_priority.py:6-15`).
- Selector T3 con asserts de tamaño (`len(pool)==339`, `len(B)==88`), exclusiones nominales y una regex de ~60 nombres de empresa (`select_t3_preview.py:34-62`); contrato de etapa B (`reseller_stage_b_contract.py:43-45`, incluye el dominio de un tercero).
- Feedback fijo SÍ/DUDOSA/NO de un revisor (el propietario del proyecto), estratos G1–G4 como si fueran un concepto de producto (`BUR:44-56`, `TPL:290`), aserciones 12/3/15 (`register_official_feedback.py:19-20`, `BCE:210-211`).
- Plantilla: «productos alimentarios» (`TPL:203`), «25 de 30», `GN={G1:5,G2:12,G3:4,G4:9}`, segmentos «Mayoristas / Distribuidores / Cadenas y supermercados» (`TPL:339`), zonas «Comunidad de Madrid / Castilla y León / Resto de España», `FIT_UNVERIFIED`, `MATCH_3C`, «PT-02», «Gate 2», diccionario `IND_ES` de 9 industrias alimentarias.
- **Contactos:** el rol objetivo es siempre Compras/Proveedores (`MCH:60-63,84,87`) y las métricas «con contacto de Compras/Comercial» son booleanos del caso.
- Una advertencia sobre un cambio de control de octubre de 2025 de una empresa concreta está como lógica (`BUR:59`).

### 1.5 Residuos de otros casos y de España en el motor «genérico»
- Piloto por proyectos de 3C (aislado a propósito en `experiments/3c/<caso>.py`): `FIT_LABELS/FIT_NAICS/FIT_LEXICON`, léxico T1 (telecom, BMS, facility), empresas de ejemplo del sector de la seguridad.
- IDs de criterio fijos en `runner.py:406,417,571,618` (`F1,FIT_KW,O4,O5,T1,O2,P1,G2`) y la columna `label_relevant_for_ai_process_automation` (`runner.py:619`).
- España/Madrid: `geography.py:9-10,53`, `runner.py:197` (`"madrid"` decide `geo_tier`), `geo_catalog` (50 provincias y 5 regiones, todas ES), `ISO_TO_APOLLO_COUNTRY`, buckets observados `(51,200),(201,500)`, `field_registry`: `hiring.site_management_roles` («jefe de obra») y `project.facility_activity` de sabor construcción/telecom.
- Rutas absolutas `/mnt/user-data/outputs` y `/home/claude/intent_engine` en casi todos los scripts.

### 1.6 Lógica duplicada
- Idénticos byte a byte: `planner_data/*.v1.json` vs `archive_v1/`; `experiment_3f/build_3f.py`, `experiment_3g/scoring_3g.py` vs `IE/experiments/3e/`. `experiments/3b2/research_plan.json` = `3b3/research_plan.json`.
- **Tres motores D010** (3H, 3I, oficial), diferencias de ~105 líneas entre 3I y oficial; dos implementaciones deliberadas de elegibilidad 3G con código muerto (`and False`).
- `v3_evaluator.py` vs `v3_evaluator_frozen.py` (~130 líneas de diferencia), `v31_protocol.py` r1 vs r2.
- 30 `mutation_*.py` y 19 `finalize_*.py` con la misma lista de regresión en 16 archivos; helpers de hash/tabla/`run()` redeclarados por script.
- Emparejamiento por id/dominio/nombre implementado **cuatro veces** (`entity.py`, `known_set.guard`, `relevance_v2`, `data_4e.dom`); dos evaluadores de geografía; `geo_catalog` cargado dos veces.
- Los 7 IDs de señal repetidos en `d010_engine`, `evidence_contract`, `simulate_d010`, `signal_audit_data`, `scoring_3g`.

### 1.7 Control de costes existente (y su límite real)
- Presupuesto obligatorio (`BUDGET_REQUIRED`), dimensiones separadas (llamadas, créditos, tiempo, top-N, reserva), tope duro `min(hard_cap=25, plan, approval.max_credits)`, parada si el coste es desconocido, aprobación humana ligada al hash de la shortlist, caché y «un enrichment por dominio».
- **El Spec ya contiene `budget.data_calls_max`, `infra_calls_max`, `tool_credits_max{tool}`** (D-021) y `governance.approval_required = true` constante. Es decir: tu `cost_budget / data_calls_budget / infra_calls_budget` **ya existe a nivel de Spec**; falta `paid_enrichment_authorized` y su aplicación.
- **Límites:** (a) se hace cumplir *por protocolo*, no técnicamente: nada impide hacer la llamada MCP fuera del executor (lo dice el README); (b) el executor **no cuenta `infra_calls`** (`EXECUTION_APPROVAL_MAX_INFRA_CALLS_NOT_ENFORCEABLE`); (c) solo hay ledger de créditos Apollo: **no se contabilizan los tokens/LLM ni las lecturas web gratuitas** (p. ej. la investigación de contactos de esta semana no figura en ningún ledger); (d) los costes proceden de una única observación («Apollo Free»), varias capacidades están sin verificar.

### 1.8 Tests
~310 `def test_` (planner 94, calibration_3d 50, intent_engine 41, project_pilot_prep 39, 3e_prep 35, executor 29, 3g 22) y 5 mutation scripts en el motor. Fuerte en reglas; **atado a casos** (fixtures españolas, piloto por proyectos de 3C, Madrid). Hay tests con dependencias rotas (`tests/test_3g.py:5` apunta a `/mnt/user-data/outputs/experiment_3f`; `seal_ready.py:19` lee `/tmp/*.json` que ya no existen). Sin tests de la plantilla JS.

---

## 2. GAP ANALYSIS — qué falta de verdad

Severidad = qué bloquea que se lo enseñes a alguien. «Bloquea a»: **C** cliente/contacto comercial/empresa · **P** portfolio/recruiter.

| ID | Cat. | Sev. | Gap | Por qué importa | Bloquea |
|---|---|---|---|---|---|
| G-01 | ENGINE | **CRITICAL** | No hay ejecución autónoma ni punto de entrada: todo proveedor va por relay manual, el LLM del Spec vive fuera del código | No se puede lanzar una necesidad nueva sin mí en la conversación. Una demo en vivo depende de intervención manual | C, P |
| G-02 | EVIDENCE | **CRITICAL** | Cero productores de evidencia first-party web/directa; solo Apollo + O4 | El MVP: 2,38 % de cobertura, 0 elegibles. La promesa «por qué es una oportunidad, con evidencia» no se cumple de forma automática | C |
| G-03 | DATA | **CRITICAL** | Un único proveedor y discovery ruidoso con recall desconocido; consultas inyectadas por caso; el planner rechazó las del MVP | La calidad de la lista de candidatos limita todo lo demás (6/12 RRHH + 3/12 medios) | C |
| G-04 | EVALUATION | **CRITICAL** | No hay ground truth ni precisión/recall para relevancia, F1, FIT_KW, gates; D010 sin calibrar; 18 bloqueos en la revisión humana del batch 1 | No puedes afirmar «oportunidades reales» ni comparar versiones | C, P |
| G-05 | GENERALIZATION | **CRITICAL** | Sector→taxonomía del proveedor (NAICS/keywords/etiquetas) es **configuración manual por caso** (`INDUSTRY_LABEL_MAPPING_MISSING`); sin campo de segmento/modelo de negocio; solo `customer_prospect`; léxico y geografía solo ES; nunca se ha corrido un segundo sector de punta a punta | «Hoteles» o «distribuidores» no se pueden evaluar aguas abajo sin que alguien escriba el mapeo | C, P |
| G-06 | CONTACTS | HIGH | Capa de contactos a mano; sin pipeline L0-L2; sin perfiles de rol por tipo de oportunidad/oferta; el esquema mezcla confianza del dato con utilidad; `data_policy.personal_data_fields` no admite teléfono ni LinkedIn; sin política de caducidad de personas | Es la mitad de la propuesta de valor («a quién contactar mañana») y hoy no escala | C |
| G-07 | COST CONTROL | HIGH | Cumplimiento por protocolo; `infra_calls` no aplicable; solo créditos Apollo en ledger; sin pasarela única de gasto; tope 25 fijo en `preflight` | Un producto con terceros necesita que el gasto sea técnicamente imposible sin autorización | C |
| G-08 | UX | HIGH | Salida = HTML parcheado por texto sobre una plantilla, sin schema, con cifras y vocabulario del caso; sin pantalla de entrada (necesidad → spec → aclaraciones → resultados) | No sirve para otro sector sin reescribirlo | C, P |
| G-09 | ENGINE | HIGH | Dispersión: 245 scripts, copias idénticas, 3 D010, rutas absolutas, dos ubicaciones del motor (zip vs vivo), sin paquete instalable ni CI | Un evaluador de portfolio abre el repo y no encuentra el producto | P |
| G-10 | ENGINE | HIGH | No hay adaptador de LLM: «LLM propone» es una convención de la conversación | Sin él, no hay producto autónomo (ligado a G-01) | C |
| G-11 | COMMERCIAL | HIGH | Sin prueba de valor (un caso donde el motor, sin ayuda humana, entregue empresas justificadas + contacto), sin ICP/propuesta/precio, y sin análisis de cumplimiento: RGPD para datos personales de contacto, términos de uso de scraping y de reutilización de datos de Apollo | Ningún cliente serio avanza sin esto | C |
| G-12 | GENERALIZATION | HIGH | Nivel de entidad ambiguo (hotel-establecimiento vs. grupo/operador), sin regla; sin política de «reciente» salvo que el usuario la defina | Rompe resolución de entidades y contactos en hostelería, retail, etc. | C |
| G-13 | ENGINE | MEDIUM | El runner declara `scoring: NOT_IMPLEMENTED` aunque D010 oficial existe; las reglas D010/POLICY no se generan del Spec | Incoherencia visible y scoring desconectado | C |
| G-14 | EVIDENCE | MEDIUM | Tres vocabularios de fuente (el del contrato de evidencia, el que usé en contactos, el que pides ahora) | Riesgo de dobles interpretaciones | — |
| G-15 | DATA | MEDIUM | `known_set`/cache sin almacén persistente | Se paga dos veces por lo ya conocido en cuanto haya segundo caso | C |
| G-16 | EVALUATION | MEDIUM | Un solo revisor humano y feedback ternario no estructurado | Sin acuerdo entre revisores ni criterio reutilizable | — |
| G-17 | UX | MEDIUM | Solo español; sin i18n | Limita mercado y demos internacionales | P |
| G-18 | ENGINE | LOW | Diffs de hash ad hoc; tests con rutas rotas; sin tests JS | Higiene | P |

### Qué bloquea realmente, por audiencia
- **Cliente / contacto comercial / empresa:** G-02, G-03, G-04, G-05 y G-11. Sin evidencia automática, una lista de calidad medida y una prueba de valor en *su* sector, solo puedes enseñar una maqueta.
- **Recruiter / portfolio:** G-09, G-01 y G-08. El valor visible sería: arquitectura gobernada + una demo reproducible de un caso + un repo legible. Hoy eso lo oculta la dispersión y la dependencia del caso del piloto de reventa. (Esto **no** exige resolver G-02…G-04 antes.)
- Una cosa buena para ambos: el diseño de gobierno (sin inventar, UNKNOWN≠PASS, aprobación por hash) es un diferenciador real y ya está construido.

---

## 3. CONTACT ENRICHMENT CONTRACT (propuesta)

### 3.1 Revisión contra la implementación actual
El esquema que pides es un buen punto de partida, pero **la implementación real obliga a cambiarlo**:

| Problema detectado | Dónde se ve | Cambio propuesto |
|---|---|---|
| `procurement_contact` y `commercial_contact` están ligados al caso (alimentación) | `flags.procurement/commercial` en mi capa | Sustituir por `role_contacts[]` con `role_family` ∈ {PROCUREMENT, COMMERCIAL, DECISION_MAKER, BUSINESS_DEV, IT_OPERATIONS, FINANCE, MANAGEMENT, GENERAL}; los alias *procurement/commercial* quedan como vistas derivadas |
| «Canal» mezcla **quién** (persona/departamento/empresa) con **cómo** (email/teléfono/portal) | Tu lista de prioridad los mezcla; `recommended_channel` mío solo da el *cómo* | Dos ejes: `target` (PERSON/DEPARTMENT/COMPANY) × `channel.kind` |
| **Confianza ≠ utilidad**: la empresa SÍ-5 sale HIGH por un email general oficial, pero no es de Compras | `CE[5]` | Separar `evidence_strength` (fiabilidad del dato) de `contact_quality` (utilidad para el objetivo) |
| Un email no distingue su **propósito** (cliente, prensa, RGPD) | `not_recommended[]` y notas en texto libre | `channel.purpose` enumerado + `usable_for_outreach` derivado |
| `directness` es texto libre | `CE[*].directness` | Enum `reachability` = DIRECT / INDIRECT / NONE |
| Fuentes: mi `source_type` (official_website/press/directory/other) no coincide con el contrato de evidencia (DIRECT, FIRST_PARTY_WEB, STRUCTURED_PROVIDER, SECONDARY_WEB, INFERRED, ESTIMATED) ni con tu lista | `ev()` | Ver mapeo en 3.3 (una sola taxonomía) |
| `personal_data_fields` del Spec solo admite name/job_title/company/work_email/location_city | `search_spec.v1.schema.json` (`governance.data_policy`) | Ampliar a `work_phone`, `linkedin_url`, `seniority`; y exigirlo para activar contactos personales |
| No hay estado de proceso: no distingue «no hay nada» de «no pude leerlo» | Empresas SÍ-11/SÍ-12 (la herramienta no abrió ninguna web) | `enrichment_state` con `FREE_ENRICHMENT_EXHAUSTED` solo si todos los pasos gratuitos fueron legibles |
| `last_verified` solo a nivel de dato | `retrieved_at` por evidencia | Mantener por evidencia y derivar `last_verified` de empresa + **política de caducidad** (personas > 12 meses → STALE) |
| «Compras» como cargo único | Perfil implícito | Selección por **perfil de contacto** (§4), no por código |

### 3.2 Contrato propuesto (`opportunity_contact.v1`)

```json
{
  "contract_version": "opportunity_contact.v1",
  "company": {"company_id": "…", "name": "…", "domain": "…", "opportunity_type": "customer_prospect", "offering_kind": "PRODUCT_FOR_RESALE"},
  "company_identity": {"legal_name": null, "trade_name": "…", "website": "…", "location": "…", "identity_state": "KNOWN_MATCH|AMBIGUOUS|UNKNOWN"},
  "enrichment_state": "PENDING | LEVEL0_DONE | LEVEL1_DONE | LEVEL1_BLOCKED | FREE_ENRICHMENT_EXHAUSTED | AWAITING_USER_DECISION | PAID_DONE",
  "levels_reached": [0, 1],
  "channels": [
    {"channel_id": "ch1", "kind": "EMAIL|PHONE|FORM|PORTAL|LINKEDIN|POSTAL",
     "value": "…", "scope": "PERSON|DEPARTMENT|COMPANY",
     "purpose": "GENERAL|COMMERCIAL|PROCUREMENT|SUPPORT|PRESS|LEGAL|DPO|UNKNOWN",
     "evidence_ids": ["e3"]}
  ],
  "people": [
    {"person_id": "p1", "name": "…", "title": "…", "department": "…", "role_family": "PROCUREMENT",
     "role_evidence_ids": ["e7"], "channel_ids": [], "employment_verified_at": "2026-01-20"}
  ],
  "role_contacts": [{"role_family": "PROCUREMENT", "people": ["p1"], "channels": ["ch4"]}],
  "recommended_contact": {
    "target": "PERSON|DEPARTMENT|COMPANY", "ref": "p1",
    "via_channel_id": "ch2",
    "why_this_contact": "…",
    "next_action": "…",
    "contact_quality": {"target_fit": "EXACT|ADJACENT|GENERIC",
                         "reachability": "DIRECT|INDIRECT|NONE",
                         "evidence_strength": "HIGH|MEDIUM|LOW|UNKNOWN",
                         "freshness": "CURRENT|STALE|UNKNOWN",
                         "grade": "A|B|C|D"}
  },
  "alternatives": [ {"target": "…", "ref": "…", "via_channel_id": "…", "contact_quality": {…}} ],
  "not_recommended": [ {"channel_id": "ch9", "reason": "SUPPORT: atención al cliente"} ],
  "evidence": [
    {"evidence_id": "e3", "field": "…", "value": "…", "source_class": "OFFICIAL|PROFESSIONAL_DIRECTORY|SECTOR_SOURCE|OTHER_VERIFIABLE|UNVERIFIED|NOT_FOUND",
     "source_url": "…", "source_label": "…", "retrieved_at": "2026-10-08",
     "claim_status": "KNOWN|INFERRED|ESTIMATED|UNKNOWN|CONFLICTING", "confidence": "HIGH|MEDIUM|LOW|UNKNOWN", "note": null}
  ],
  "contradictions": [{"field": "…", "values": ["…","…"], "state": "AMBIGUOUS"}],
  "not_found": ["…"],
  "confidence": "HIGH|MEDIUM|LOW|UNKNOWN",
  "last_verified": "2026-10-08",
  "cost": {"paid_calls": 0, "credits": 0, "paid_enrichment_authorized": false}
}
```
`confidence` (nivel contacto) se **deriva** de `evidence_strength` del elemento recomendado; no se asigna a mano. `grade` es una etiqueta derivada, no una puntuación numérica.

### 3.3 Una sola taxonomía de fuentes
Dos ejes distintos que hoy se mezclan:
- **`source_class`** (de dónde viene): `OFFICIAL` · `PROFESSIONAL_DIRECTORY` · `SECTOR_SOURCE` · `OTHER_VERIFIABLE` · `UNVERIFIED` · `NOT_FOUND`.
- **`claim_status`** (qué podemos afirmar): el del contrato de evidencia existente (KNOWN/INFERRED/ESTIMATED/UNKNOWN/CONFLICTING). **La inferencia nunca es evidencia**: queda como `INFERRED`, no se promueve a contacto recomendado.

Mapeo con lo ya implementado: `OFFICIAL` ≈ `DIRECT`/`FIRST_PARTY_WEB`; `PROFESSIONAL_DIRECTORY` ≈ `STRUCTURED_PROVIDER` (Apollo, B2B DBs, LinkedIn, directorios); `SECTOR_SOURCE` ≈ `SECONDARY_WEB` de prensa/asociaciones del sector (p. ej. Alimarket); `OTHER_VERIFIABLE` ≈ otro `SECONDARY_WEB` comprobable; `UNVERIFIED` = existe pero no se pudo contrastar; `NOT_FOUND` = búsqueda legible sin resultado (distinto de «no pude leer»).

### 3.4 Asignación de `evidence_strength` (reglas, no criterio libre)

| Condición | Strength |
|---|---|
| `OFFICIAL`, publicado por la empresa, leído en origen (o contrastado en ≥2 lecturas/fuentes), vigente | **HIGH** |
| Persona + cargo en `SECTOR_SOURCE`/`PROFESSIONAL_DIRECTORY` fiable y fechado ≤ 12 meses, sin canal propio; o canal `OFFICIAL` pero de otro propósito (p. ej. atención al cliente) | **MEDIUM** |
| Una sola fuente de directorio no contrastada; persona/dato de >12 meses; fuente que no se pudo leer en origen; dato aparecido solo en un resultado de búsqueda | **LOW** |
| Contradicción entre fuentes → `AMBIGUOUS` (se muestran ambas, no se resuelve); sin dato, o página ilegible | **UNKNOWN** |
| Cualquier derivación (patrón de email, «probable» cargo, mismo dominio ⇒ misma persona) | **No válida**: no genera evidencia ni confianza |

`reachability`: **DIRECT** = el canal llega al *rol objetivo* sin intermediario (email/teléfono propio de la persona o del departamento objetivo, o formulario/portal de alta de ese rol); **INDIRECT** = requiere derivación (centralita, atención al cliente, formulario general, persona sin canal propio); **NONE** = nada accionable.

---

## 4. CONTACT PRIORITIZATION — cómo decide el sistema a quién recomendar

### 4.1 Por qué no copio tu ranking tal cual
Tu ejemplo (persona > departamento > portal > email comercial > teléfono comercial > general > LinkedIn corporativo) tiene tres problemas que muestran los datos reales:
1. **Mezcla ejes.** «Persona» y «departamento» son *destino*; «email», «portal», «teléfono» son *canal*. Una persona sin canal propio (p. ej., un director comercial de compras con nombre verificado pero sin email ni teléfono publicados) solo es alcanzable de forma **indirecta**: el destino es ideal, la vía no.
2. **La especificidad no es alcanzabilidad.** Para un proveedor, un formulario oficial de alta de proveedores (DIRECT) puede ser mejor primer paso que un nombre sin canal; la persona sirve para el seguimiento.
3. **Depende del tipo de oportunidad *y de lo que se ofrece*, no del sector del cliente.** Vender un producto alimentario a un distribuidor y vender automatización con IA a una constructora no comparten perfil de rol, aunque ambos sean `customer_prospect`.

### 4.2 Algoritmo (lexicográfico, sin puntuación numérica)
1. **Candidatos:** todas las personas, canales departamentales/de empresa y portales con evidencia.
2. **Filtros duros:** descartar (→ `not_recommended`) canales con `purpose` ∈ {SUPPORT, PRESS, LEGAL, DPO} salvo si el perfil los admite expresamente; descartar `UNVERIFIED`/`INFERRED`; personas caducadas pasan a alternativa con `freshness=STALE`.
3. **Orden de comparación:** `target_fit` (según el *perfil* de rol, orden de la tabla 4.3) → `reachability` (DIRECT > INDIRECT) → `evidence_strength` → `freshness` → desempate determinista (orden de hallazgo).
4. **Una única recomendación principal** + hasta 3 alternativas. Si el destino mejor es una persona sin canal propio, `via_channel_id` es la mejor vía INDIRECT (formulario/central) y `next_action` dice **a quién pedir**.
5. **Si no hay nada accionable:** `recommended_contact = null`, `enrichment_state` explícito y `next_action` manual («abrir la web oficial en el navegador…»). Nunca se rellena con una suposición.

### 4.3 Perfiles de contacto (datos, no código) — `contact_profiles.v1.json`
Se elige perfil con `(opportunity_type, offering_kind)` del Spec. Hoy el Spec no tiene `offering.kind` → hay que añadirlo (interpretado por el LLM con cita literal, validado por código).

| Perfil | Cuándo | Orden de `target_fit` (de mejor a peor) |
|---|---|---|
| `SELL_PRODUCT_TO_RESELLER` (piloto de reventa con distribuidores/retail) | `customer_prospect` + producto para reventa | Portal/formulario de alta de proveedores → Comprador/Category Manager → Dpto. Compras → Dir. Comercial/Desarrollo de negocio → Dirección (si pequeña) → Contacto general |
| `SELL_SOFTWARE_OR_SERVICE` (IA, software de gestión, transformación digital) | `customer_prospect` + software/servicio | Decisor de la función afectada (COO/Operaciones/Finanzas/IT) → Resp. de área (IT/Operaciones) → Dirección (si pequeña) → Desarrollo de negocio → Email/telefono comercial general → Contacto general |
| `SELL_TO_HOSPITALITY_OPERATIONS` | oferta ligada a operación | Dirección General/Revenue/Operaciones → IT/Digital → Compras → General |
| `SOURCE_SUPPLIERS` | `opportunity_type=supplier` (aún no soportado) | Ventas del proveedor / canal de distribución → Dirección comercial → General |
| `PARTNER` / `TALENT` | `partner` / `talent` (no soportados) | Desarrollo de negocio / Dirección → General · Reclutamiento / Dirección de área |

Parámetro de empresa pequeña (`small_company_max_employees`, por defecto 50): **solo se aplica si hay evidencia de plantilla**; si es UNKNOWN no se asume tamaño y no se reordena.

### 4.4 Prueba retrospectiva sobre las 12 SÍ del piloto de reventa (qué diría la regla)
| Empresa | Destino | Vía | target_fit | reachability | strength | Grado |
|---|---|---|---|---|---|---|
| Empresa SÍ-1 | Persona: Dir. Comercial de Compras | Formulario/teléfono web, pedir por él | EXACT | INDIRECT | MEDIUM | **B** |
| Empresa SÍ-2 | Sección «Proveedores» (destino sin leer) | Página de contacto / centralita | EXACT | INDIRECT | MEDIUM | **B** |
| Empresa SÍ-3 | Portal de proveedores B2B | Portal / central | EXACT | INDIRECT (alta no confirmada) | MEDIUM | **B** |
| Empresa SÍ-4 | General (equipo comercial sin contacto) | Teléfono | GENERIC | INDIRECT | HIGH | **C** |
| Empresa SÍ-5 | General | Email + teléfono | GENERIC | INDIRECT | HIGH | **C** |
| Empresa SÍ-6 | General (atención al cliente) | Formulario | GENERIC | INDIRECT | MEDIUM | **C** |
| Empresa SÍ-7 | General (atención al cliente) | Teléfono/email | GENERIC | INDIRECT | MEDIUM | **C** |
| Empresas SÍ-8, SÍ-9, SÍ-10 | General (directorio / sin leer) | Formulario / teléfono | GENERIC | INDIRECT | LOW | **C** |
| Empresas SÍ-11, SÍ-12 | — | — | — | NONE | UNKNOWN | **D** |

**Lectura honesta: de 12, ninguna es grado A (destino exacto + vía directa + evidencia alta).** Mi capa actual marcaba las empresas SÍ-4 y SÍ-5 con confianza HIGH, lo cual **sobrestimaba su utilidad**; el modelo nuevo las deja en C. Es una corrección de mi trabajo anterior, no de los datos.

---

## 5. COST POLICY — cuándo se permite gastar

### 5.1 Niveles (sin gasto automático)
| Nivel | Contenido | Coste | Estado hoy |
|---|---|---|---|
| **0** | Todo lo ya disponible (artefactos, caché, known-set, enrichment previo) | 0 | Parcial: sin almacén persistente |
| **1** | Investigación web gratuita: web oficial, contacto/proveedores/compras, prensa sectorial, directorios, LinkedIn público | 0 créditos; coste de infra/LLM **no contabilizado** | Manual (mi investigación) |
| **2** | Fuentes estructuradas gratuitas o de coste conocido y bajo (APIs abiertas, Registro Mercantil abierto, Apollo *organization lookup* = 0 créditos) | 0 o bajo | Solo Apollo lookup |
| **3** | Pago: Apollo People Enrichment (1–9 créditos/persona: 1 email, +8 móvil), otros | Créditos/€ | Catalogado, **no soportado**; People Search: coste **no confirmado** |

Estados: `PENDING → LEVEL0_DONE → LEVEL1_DONE (o LEVEL1_BLOCKED) → LEVEL2_DONE → FREE_ENRICHMENT_EXHAUSTED → AWAITING_USER_DECISION → PAID_AUTHORIZED → PAID_DONE`.
`FREE_ENRICHMENT_EXHAUSTED` **solo** si todos los pasos gratuitos aplicables se ejecutaron *con fuentes legibles*. Si la herramienta no pudo abrir las webs (caso de las empresas SÍ-11/SÍ-12 esta semana), el estado correcto es `LEVEL1_BLOCKED`, no «agotado». Mi capa actual no distinguía esto.

### 5.2 Parámetros (reutilizando lo que el Spec ya tiene)
| Tu campo | Dónde vive hoy | Acción |
|---|---|---|
| `cost_budget` | `budget.tool_credits_max{tool}` + `reserve_pct` | Mantener; añadir `cost_budget.currency_max` opcional |
| `data_calls_budget` | `budget.data_calls_max` | Ya existe y se aplica |
| `infra_calls_budget` | `budget.infra_calls_max` | Existe en el Spec, **no es aplicable en el executor**: contar llamadas LLM/web/cómputo y registrarlas en el ledger |
| `paid_enrichment_authorized` | `plan.authorization.spend_authorized=false` + `execution_approval{approved_by,approved_at,max_credits}` + hash de shortlist | Añadir objeto explícito: `{authorized:false, tool_allowlist:[], max_credits_per_tool, max_per_company, scope: company_ids|tier, expires_at, approved_by, approved_at}` |

### 5.3 Pre-gasto: la solicitud de gasto (`SpendRequest`)
Antes de cualquier fuente de pago el sistema debe poder rellenar (y registrar) las 7 respuestas:
1. **¿Necesario?** el campo objetivo sigue vacío tras `FREE_ENRICHMENT_EXHAUSTED`.
2. **¿Qué dato falta?** (p. ej. email profesional de la persona P1).
3. **¿Valor comercial?** prioridad de la oportunidad (p. ej. SÍ humano / top del ranking) y *grado* actual del contacto.
4. **¿Probabilidad?** tasa de acierto observada de esa herramienta en ese campo; **UNKNOWN si no hay histórico** (no se inventa).
5. **¿Coste?** estimación de la propia herramienta (en Apollo, el bloque `mcp_credits`) y máximo.
6. **¿Alternativa gratuita?** debe listarse (formulario, llamada, LinkedIn manual).
7. **¿Autorizado?** `paid_enrichment_authorized` con alcance, tope y caducidad vigentes.

Regla: **si cualquier respuesta es «no/desconocida» en (1), (5) o (7) → `DO NOT SPEND`**. El resultado es `AWAITING_USER_DECISION` con la solicitud completa para que decidas.

### 5.4 Qué haría falta para que sea una garantía y no una convención
Una **pasarela única de gasto** (todo adaptador de pago solo accesible a través de ella), ledger encadenado como fuente de verdad (ya existe para Apollo), aprobaciones con caducidad, modo `--estimate` sin ejecución, y contadores de infra/LLM. Hoy es protocolo.

---

## 6. GENERALIZATION TEST — 4 Search Specs

**Ejecutado (offline, gratis):** cada petición se convirtió en un borrador como lo haría el intérprete y se pasó por el **motor real de Spec** (`IntentEngine.process`, stdlib, sin red). Artefactos: `generalization_test/draft_t*.json`, `RESULT_spec_engine_offline.txt`, `drafts.py`, `run.py`. No se hizo ninguna investigación de empresas.

| Test | Estado devuelto por el motor | Lo que dice |
|---|---|---|
| 1 · IA 50-500 empleados | `confirmed` (L1/L2/L3 OK) | Plantilla como gate; 2 señales inferidas quedan `proposed` (piden confirmación) |
| 2 · Hoteles Madrid | `needs_clarification` (bloqueante) | `OFFERING_MISSING` (no dices qué ofreces) y `CRITERION_INCOMPLETE` (expansión «reciente» sin valor/periodo) |
| 3 · Constructoras en crecimiento | `needs_clarification` | `CRITERION_INCOMPLETE` («en crecimiento» sin umbral/periodo) y confirmación de señales inferidas |
| 4 · Distribuidores alimentarios | `needs_clarification` | `OFFERING_MISSING` («un nuevo producto» sin especificar) |

**Conclusión de la prueba:** la capa Spec **generaliza correctamente** y hace lo que debe (no inventa umbrales ni ofertas). Pero «`confirmed`» solo significa que la *especificación* es válida: no demuestra que el motor pueda *ejecutarla*.

### Detalle por test

**TEST 1 — 20 empresas españolas 50–500 empleados, automatización con IA**
- *Spec:* `customer_prospect`/company; gate `employee_count between [50,500]`; geografía ES; cantidad 20; oferta «automatización de procesos con IA».
- *Gates:* GEO (ES), EMP 50–500; exclusiones si las hay. **No hay gate de sector** (el usuario no lo dio): el MVP ya mostró el efecto (RRHH/medios en la lista).
- *Señales:* `hiring.ops_admin_roles`, `hiring.finance_admin_roles`, `web.digital_transformation_mentions`, `web.management_stack_visible`, `growth.hiring_velocity`; todas *inferidas* y requieren confirmación.
- *Enrichment necesario:* firmográfico (Apollo) + **evidencia web propia** (páginas de empleo, menciones de transformación, herramientas visibles): **sin productor hoy**.
- *Contacto prioritario:* perfil `SELL_SOFTWARE_OR_SERVICE` → decisor de Operaciones/Finanzas/IT; Dirección si plantilla < 50 (solo con dato).
- *Evidencia necesaria:* ofertas de empleo con título y fecha; cita de la web propia; plantilla con fuente; contacto con cargo en fuente `SECTOR_SOURCE`/`PROFESSIONAL_DIRECTORY` o `OFFICIAL`.
- *Representable hoy:* **Sí** a nivel Spec. **Ejecutado hoy:** sí (MVP) con resultado comercial nulo (G-02, G-03).

**TEST 2 — 15 hoteles de Madrid, señales recientes de expansión o transformación digital**
- *Spec:* sector «hoteles» (gate); geografía Madrid (`municipality`; el motor asume `geo_default`); cantidad 15; criterios `growth.new_locations`, `web.digital_transformation_mentions`; **oferta ausente → bloqueo**.
- *Gates:* GEO (municipio vs. Comunidad de Madrid: ambigüedad a confirmar), sector hotelero, **nivel de entidad** (establecimiento vs. grupo operador) — no hay regla (G-12).
- *Señales:* nuevas aperturas/reformas (`project.facility_activity`, `growth.new_locations`), transformación digital en la web; «reciente» requiere ventana definida por el usuario.
- *Enrichment:* mapeo «hoteles»→NAICS 7211 / etiquetas del proveedor (**no existe**, G-05), evidencia de prensa del sector (aperturas), web propia.
- *Contacto prioritario:* depende de la oferta (no dada): operaciones/IT/Revenue si es tecnología; Compras/F&B si es producto. **Sin oferta no se puede elegir perfil**: el sistema debe preguntar.
- *Evidencia:* nota de prensa/registro de apertura fechada; cita web; cargo en fuente fiable.
- *Representable hoy:* Spec sí (con aclaraciones). Aguas abajo: **no** (G-05, G-12, G-02).

**TEST 3 — 20 constructoras españolas en crecimiento, software de gestión**
- *Spec:* sector «construcción» (gate), `growth.employee_growth_pct` (**sin umbral → bloqueo**), `hiring.site_management_roles` inferido.
- *Gates:* GEO ES; sector construcción (taxonomía NAICS 236–238 / CNAE 41–43, **mapeo no existente**); tamaño no dado (`SIZE_UNSPECIFIED`).
- *Señales:* crecimiento de plantilla (ventana), ofertas de jefe de obra/administración, obras/ampliaciones (`project.facility_activity`), herramientas de gestión visibles en la web.
- *Enrichment:* Apollo (plantilla, crecimiento) + fuentes sectoriales públicas (licitaciones, registro de contratistas) **no están en el catálogo de herramientas**; web propia.
- *Contacto prioritario:* mayoría de PYMEs → propietario/gerente primero **si hay dato de tamaño**; si no, director de administración/finanzas o de operaciones/obra.
- *Evidencia:* ofertas fechadas; obras con fecha; plantilla con fuente y fecha.
- *Representable hoy:* Spec sí; los campos del registry están (de hecho son sus fixtures). Ejecución: bloqueada por G-02/G-05.

**TEST 4 — 15 distribuidores alimentarios españoles, compradores de un nuevo producto**
- *Spec:* sector «distribución alimentaria» (gate libre); **oferta ausente → bloqueo** («un nuevo producto»); geografía ES.
- *Gates:* GEO; **tipo de empresa** (mayorista/distribuidor/cadena/horeca) — **no existe campo de segmento/modelo de negocio**: el piloto de reventa lo resolvió con una convención de IDs de consulta `RP-MAY/DIS/CAD` y revisión humana, es decir, *fuera* del motor.
- *Señales:* ampliación de gama/proveedores, nuevos centros logísticos, prensa sectorial; web con sección de proveedores.
- *Enrichment:* NAICS 4244/4245 + etiquetas; web oficial (secciones Proveedores/Compras); prensa sectorial (Alimarket) para personas.
- *Contacto prioritario:* perfil `SELL_PRODUCT_TO_RESELLER` → portal de alta de proveedores → comprador/Category Manager → Compras → Comercial.
- *Evidencia:* página oficial de proveedores; persona y cargo en fuente sectorial fechada.
- *Representable hoy:* Spec sí tras aclarar oferta. Aguas abajo: es el caso del piloto de reventa y **dependió de revisión humana y de investigación manual** (G-02, G-05, G-06).

### Qué cambiaría en el modelo tras esta prueba
1. `offering.kind` (enum) y `offering.target_function` en el Spec: determinan el perfil de contacto.
2. Campo de **segmento/modelo de negocio** (`firmographic.business_model`) con valores definidos por caso pero con tipo genérico.
3. Regla de **nivel de entidad** (`entity_level`: ESTABLISHMENT | OPERATOR | GROUP).
4. **Capa de mapeo sector→taxonomía del proveedor** (`sector_mapping.<país>.json`), generada con ayuda de LLM, validada por código y confirmada por el usuario.

---

## 7. COMMERCIAL READINESS — qué falta para enseñarlo o venderlo

**Hoy puedes enseñar con honestidad:**
- La **arquitectura de gobierno** (Spec → Plan → aprobación → ledger), el principio «no inventar», y la rigurosidad (tests, mutation tests, hashes).
- La **dirección de producto**: el HTML del piloto de reventa con contacto recomendado, evidencia y alternativas (presentado como *maqueta alimentada con investigación manual*).
- **No puedes enseñar** como capacidad autónoma: «dame una necesidad y te devuelvo oportunidades justificadas con contacto».

**Bloqueos reales por audiencia**
| Audiencia | Qué necesita ver | Qué lo impide hoy |
|---|---|---|
| Cliente potencial / contacto comercial / empresa | Ejecutar *su* búsqueda y obtener 10–20 empresas razonables con motivo, evidencia y a quién llamar | G-01, G-02, G-03, G-04, G-05, G-11 |
| Recruiter / portfolio | Un repo legible, un caso reproducible, decisiones de diseño defendibles | G-09, G-01, G-08, G-05 (dependencia del piloto de reventa) |

**Criterio mínimo para decir «motor general» (lista de salida):**
1. Un comando/API ejecuta *necesidad → spec confirmado → plan → discovery → calificación → evidencia → contacto → salida* sin pasos manuales, con aprobaciones explícitas de gasto.
2. Dos sectores no relacionados (p. ej. hoteles y distribución alimentaria) pasan por el mismo código **cambiando solo datos de caso** (spec, mapeos, perfiles).
3. Una evaluación con ground truth de al menos dos casos y precisión/recall reportados para relevancia y fit.
4. Evidencia first-party generada por el sistema (no a mano) para ≥ 1 señal de necesidad.
5. Análisis de cumplimiento (RGPD para datos personales, términos de uso) documentado.

Hasta que 1–4 no se cumplan, la frase de tu criterio de éxito sería falsa.

---

## 8. IMPLEMENTATION PLAN — orden recomendado (nada se ejecuta sin tu aprobación)

Principio: **primero lo barato que desbloquea lo caro**; cada fase con criterio de salida verificable; sin tocar el histórico (se archiva en solo lectura, no se borra).

| # | Fase | Contenido | Salida verificable | Coste |
|---|---|---|---|---|
| 0 | **Aprobar contratos** | Contrato de contacto (§3), taxonomía de fuentes, perfiles (§4), política de coste (§5), campos nuevos del Spec | Documento aprobado por ti; vocabulario congelado | 0 |
| 1 | **Separar capas en el repo (sin borrar)** | Estructura destino (tabla abajo); paquete único instalable `oi`; rutas parametrizadas; un solo D010 con `RULES` en datos; archivar experimentos como solo lectura con índice | `pip install -e .` + suite verde en un entorno limpio; 0 rutas absolutas | 0 |
| 2 | **Contrato de Oportunidad + JSON Schema** | Schema `opportunity_record.v1` (empresa, razones, señales, evidencia, contacto); validador | Los 12 registros del piloto de reventa (migrados del layer manual) validan; golden tests | 0 |
| 3 | **Renderizador genérico** | Componentes UI dirigidos por schema y por *etiquetas del caso* (idioma, vocabulario, métricas calculadas) en vez de parches de texto | Mismo HTML para el piloto de reventa y para un caso sintético de hoteles, sin tocar la plantilla | 0 |
| 4 | **Mapeos de caso y perfiles de contacto como datos** | `cases/<id>/` (spec, mapeo sector→taxonomía, léxicos, exclusiones, perfil de contacto); `offering.kind`; segmento; nivel de entidad | Cambiar de caso = cambiar una carpeta | 0 |
| 5 | **Pasarela de gasto + contadores** | Spend gateway único, `--estimate`, contadores `infra_calls` y LLM, aprobaciones con caducidad, estados de enrichment | Tests que prueban que ninguna ruta llega a un proveedor de pago sin autorización | 0 |
| 6 | **Contact Enrichment L0–L2 como pipeline** | Perfiles + algoritmo (§4); lectores web gratuitos con captura de evidencia; estados `LEVEL1_BLOCKED` vs `EXHAUSTED` | Reproduce los 12 del piloto de reventa (grados §4.4) y falla si se inventa un dato | tokens/infra (contabilizados) |
| 7 | **Evidencia first-party** | Productor de evidencia web (secciones de la propia web, ofertas, menciones) conectado al contrato de evidencia | Cobertura de señales medida; ≥1 señal de necesidad con evidencia automática | tokens/infra |
| 8 | **Evaluación** | Ground truth de 2 sectores, métricas de relevancia/fit/contacto, regresión | Informe con precisión/recall; D010 calibrado o declarado «sin calibrar» con intervalos | tiempo humano |
| 9 | **Prueba de generalización real** | Un segundo sector (p. ej. hoteles Madrid) de punta a punta con presupuesto pequeño | **Requiere tu autorización de gasto**; informe honesto de resultado | créditos (a decidir) |
| 10 | **Entrada autónoma + demo** | Adaptador LLM para el Spec, CLI/API, demo reproducible, README de producto, nota de cumplimiento | Un evaluador externo ejecuta un caso con un comando | LLM |

### Estructura destino de las 7 separaciones que pides (y dónde está hoy)
| Separación | Carpeta propuesta | Hoy |
|---|---|---|
| 1. Datos del caso | `cases/<id>/{spec,mappings,lexicons,exclusions,feedback}` | Dispersos en `reseller_pilot_history/*`, `case_*.py`, `<caso de 3C>.py` |
| 2. Lógica del motor | `oi/engine/` (spec, planner, executor, evaluators) | `/home/claude/intent_engine` + copias en zips |
| 3. Evidencia | `oi/evidence/` (contrato, productores, política en datos) | `evidence_contract_4b`, partes de `mvp_pipeline.py` |
| 4. Enriquecimiento | `oi/enrichment/{company,contact}` + adaptadores | Apollo en `executor/adapters`, contactos a mano en `contact_enrichment/` |
| 5. Presentación | `oi/presentation/` (schema→UI, etiquetas por caso) | `user_result/`, `contact_enrichment/*.html` |
| 6. Configuración | `config/` (presupuestos, políticas de gasto, perfiles) | `experiment_config`, `case_mvp.py`, constantes sueltas |
| 7. Reglas de decisión | `rules/` (D010, gates, perfiles de contacto) como datos versionados | `d010_engine.py` (3 copias), `ruleset.v2.json` |

### Decisiones que necesito de ti antes de implementar
1. ¿Apruebas el contrato y la taxonomía de fuentes de §3 (incluida la corrección de que **confianza ≠ utilidad**)?
2. ¿Qué meta tiene prioridad para las próximas semanas: **portfolio/recruiter** (fases 1-3 y 10 primero) o **cliente** (fases 6-9 primero)? Cambia el orden.
3. ¿Quieres que añada `offering.kind` y `business_model`/`entity_level` al Spec (cambio de contrato)?
4. ¿Presupuesto máximo para la prueba de generalización real (fase 9), y sector candidato?
