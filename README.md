# Opportunity Intelligence

> **EN:** A contract-driven B2B research engine. It turns a commercial need in plain language into a validated Search Spec, plans
> the research under an explicit budget, and returns candidate opportunities with traceable evidence. It states what is known, what
> is **unknown** and what to do next, without inventing data or spending without authorization. This is the **public edition**: code,
> contracts, rules and tests with synthetic data. Real pilot data is kept private. Docs are in Spanish.

Opportunity Intelligence convierte una necesidad comercial en lenguaje natural («constructoras españolas en crecimiento que puedan
necesitar software de gestión») en una búsqueda estructurada y verificable. Investiga, cualifica y devuelve una lista corta de
**oportunidades candidatas**, cada una con su evidencia: por qué podría serlo, qué se sabe, qué **no** se sabe, con qué fuente y qué
paso sigue.

> **Edición pública.** Este repositorio contiene el motor, los contratos, las reglas, los tests y datos **sintéticos**. Los datos de los
> pilotos reales no se publican: respuestas de proveedores, evaluaciones humanas, contactos, listas de prospectos y entregables a clientes.
> Qué se retiró y cómo se sustituyó: [`docs/SANITIZATION_REPORT.md`](docs/SANITIZATION_REPORT.md).

## Qué lo diferencia de un buscador de leads

| Buscador de leads típico | Opportunity Intelligence |
|---|---|
| Devuelve muchas empresas que «coinciden» | Devuelve pocas, cualificadas por reglas y con la evidencia de cada criterio |
| Un dato ausente se ignora o se rellena | `UNKNOWN` es un estado explícito y **nunca** cuenta como `PASS` |
| El modelo decide | **El LLM propone, el código decide:** validación, estados y elegibilidad son deterministas y versionados |
| Gasta créditos al buscar | Ningún gasto sin presupuesto y autorización explícitos; el transporte real está bloqueado por diseño |
| «Alta confianza» = buen contacto | Evidencia ≠ utilidad comercial: fiabilidad del dato y calidad del contacto se evalúan por separado |
| Un dato del proveedor es un hecho | Un dato de un proveedor de datos nunca es `KNOWN`; un título de página no es una página leída |

## Arquitectura

```
Request → Search Spec → Research Plan → Discovery → Normalize + Entity Resolution → Cheap Gates → Enrichment
        → Qualification / Scoring → Evidence → Results → Approval → Action → Outcome
```

| Etapa | Implementación | Estado |
|---|---|---|
| Request | `oi/cli.py`, `oi/engine/spec/mock_interpreter.py`: intérprete local determinista que extrae literalmente y genera preguntas de aclaración. No hay LLM integrado | PARCIAL |
| Search Spec | `oi/engine/spec/`, contratos `search_spec.v1/v2`, Field Registry 1.3.0. Describe *qué saber* (clases de fuente), nunca proveedores | IMPLEMENTADO |
| Research Plan | `oi/engine/planner/`: presupuesto por dimensiones, capacidades verificadas, plan `ready`/`degraded`/`blocked` | IMPLEMENTADO |
| Discovery | `oi/engine/executor/`, `oi/pipeline/guard.py`: transporte `offline` (sintético) y `replay` (datos capturados). `real` lanza siempre `RealModeBlocked` | PARCIAL (el discovery real de los pilotos lo hizo un operador a mano) |
| Normalize + Entity Resolution | `oi/engine/evaluators/`: identidad en 4 estados, sin fuzzy matching, deduplicación por id/dominio | IMPLEMENTADO |
| Cheap Gates | `oi/qualification/qualifier.py` + `rules/qualification.v1.json` (identidad, geografía, nivel de entidad, tamaño, encaje, modelo de negocio, crecimiento) | IMPLEMENTADO |
| Enrichment | Procesa respuestas ya obtenidas. En los pilotos, el enrichment de pago lo hizo el operador con autorización explícita | PARCIAL |
| Evidence | Contrato `evidence_input.v1`, intake de ficheros, fuerza de evidencia derivada por reglas. **Sin productor automático de evidencia first-party** | PARCIAL |
| Scoring | `oi/rules/d010/` (modelo D010-v0.2 OFFICIAL, paridad por hash con el motor original). **No conectado al pipeline del producto** | PARCIAL |
| Contact Enrichment | `oi/enrichment/contact/`: perfiles, selector y comprobaciones anti-invención | PARCIAL |
| Results | `opportunity_record.v1` + renderer HTML genérico ES/EN (`oi/presentation/`) | IMPLEMENTADO |
| Approval | `oi/pipeline/{approval,estimate}.py`, `config/oi.config.json` (`paid_enrichment_authorized: false`) | IMPLEMENTADO (offline) |
| Action | `oi/rules/next_action.py`: recomienda (`CONTACT`, `ENRICH_FIRST`, `VERIFY_EVIDENCE`, `HUMAN_REVIEW`, `NO_ACTION`…). No ejecuta acciones externas | PARCIAL |
| Outcome | Bucle de resultados comerciales | NO IMPLEMENTADO |

Una capacidad adicional, `core/project_opportunity.py` (v0.1), cualifica oportunidades basadas en **proyectos con un evento fechado**
(licitación, obra adjudicada, apertura) en lugar de firmografía. Es una función pura con 25 tests y aún no está conectada al CLI `oi`.

**Modos de ejecución:** `offline` = transporte sintético con datos etiquetados `SYNTHETIC` · `replay` = datos reales capturados, sin red
(sus datos no se publican) · `real` = **bloqueado**. `oi/pipeline/guard.py` es el único módulo que puede construir transportes o abrir
sockets. En `--offline` los sockets están bloqueados y contados.

## Contratos, evidencia y cualificación

- **Contratos versionados** (`oi/contracts/`): `search_spec.v1/v2`, `research_plan.v2`, `evidence_input.v1`, `opportunity_record.v1`, `opportunity_contact.v1`, con un validador JSON Schema propio y sin dependencias.
- **Estados de verdad:** `KNOWN` · `INFERRED` · `ESTIMATED` · `UNKNOWN` · `CONFLICTING`. `UNKNOWN` nunca es `PASS` (ni `NO`); `INFERRED`, `ESTIMATED` y `CONFLICTING` tampoco son `KNOWN`. Dos cantidades incompatibles dan `CONFLICTING`: no se elige una.
- **Elegibilidad por reglas:** `ELIGIBLE` exige evidencia `KNOWN` de fuente oficial o humana. Lo que no se puede resolver queda `PENDING_EVIDENCE` o `NOT_ASSESSED`. Un `FAIL` solo sale de evidencia `KNOWN`.
- **Reglas como datos** (`rules/`): cualificación, fuerza de evidencia, siguiente acción, perfiles de contacto, taxonomía de roles, mapeo sectorial. Un término sin mapeo da `MAPPING_REQUIRED`: no se inventan CNAE/NAICS.
- **Contacto:** nunca se construyen emails, teléfonos, cargos ni perfiles por patrón. Un email genérico no se atribuye a una persona. Un SÍ humano no es `FIT_VERIFIED`.

## Control de presupuesto y llamadas externas

- El presupuesto separa `data_calls`, `infra_calls` y `tool_credits{herramienta}`. Los créditos no se estiman: se limitan y se aplican con el gasto real observado.
- Sin presupuesto no hay plan (`BUDGET_REQUIRED`). Un tope (*hard cap*) no es una autorización, y el saldo de una cuenta no es presupuesto.
- En los pilotos, cada gasto real exigió una autorización explícita con tope; el saldo se leyó antes y después de cada llamada de pago y se paró ante cualquier discrepancia.
- `oi run --real` termina con código 3 mientras `paid_enrichment_authorized = false`. Aunque se pusiera a `true`, el transporte real no está cableado.

## Estado real del MVP

| Implementado y probado | Parcial | Pendiente |
|---|---|---|
| Pipeline E2E offline reproducible: spec → plan → aprobación → ejecución sintética → puertas → evidencia → cualificación → contacto → registros → HTML | Intérprete local en lugar de LLM | Productor automático de evidencia first-party (el cuello de botella medido en los pilotos) |
| Gates, contratos, reglas versionadas, renderer ES/EN | D010 existe como biblioteca, sin conectar al pipeline | Ejecución real autónoma (bloqueada por diseño; requiere aprobación humana) |
| Cualificación de oportunidades por proyecto (`core/`) | Discovery y enrichment reales hechos a mano por el operador | Bucle de Outcome, envío de mensajes, CRM |
| Tests: 371 del producto + 25 de `core/` en esta edición (ver abajo) | Contact Enrichment real solo como capa manual en un piloto | Confirmación humana externa del ground truth de los pilotos |

## Pilotos con datos reales (resultados agregados; los datos no se publican)

Los experimentos reales **no están incluidos** en esta edición: ni sus datos, ni sus informes detallados, ni sus tests específicos. Los casos se
identifican con nombres neutrales (`reseller_pilot`, `construction_software`, piloto por proyectos) que no corresponden a ninguna empresa.

**Piloto de reventa: canales y clientes para un productor alimentario (cerrado).** Discovery gratuito (339 candidatos), 88 revisados por una
persona, enrichment con puertas de aprobación (10 de 12 créditos) y una muestra de 30 evaluada por un revisor: **12 SÍ · 3 DUDOSA ·
15 NO.** Las 12 son oportunidades candidatas según la evaluación humana, **no clientes cualificados**. Los candidatos revisados antes por
una persona obtuvieron más SÍ (58,8 %) que los propuestos solo por el sistema (22,2 %); es una diferencia descriptiva, con muestra pequeña
y no aleatoria. En esta edición, sus 30 registros se publican como **datos sintéticos** que conservan estados, decisiones agregadas y
acciones, y sustituyen identidades y textos.
→ [`docs/experiments/reseller-pilot/README.md`](docs/experiments/reseller-pilot/README.md)

**Construcción + software (F12–F15.1, cerrado).** Lookup gratuito (59 organizaciones), replay offline y enrichment de 9 empresas
(9 de 10 créditos autorizados). Estado final: 0 `ELIGIBLE`, 6 `PENDING_EVIDENCE`, 3 `NOT_ELIGIBLE`. Precisión provisional de
descubrimiento, sobre un ground truth interno no confirmado por personas externas: 1/9. Conclusión: el descubrimiento por proveedor es
ruidoso para este nicho y, sin evidencia first-party, el motor no puede (ni debe) producir `ELIGIBLE`.

**Piloto por proyectos: oportunidades por proyecto en Madrid.** Discovery gratuito (búsqueda y lectura web, 0 créditos) de proyectos con
evento fechado, compatibles con las capacidades publicadas de una instaladora. Cada afirmación lleva URL y fragmento literal, y la cualifica
`core/project_opportunity.py`. 31 organizaciones → 8 prioritarias · 5 a vigilar · 13 a verificar · 5 descartadas. Son oportunidades
defendibles para contactar, **no clientes ni necesidades confirmadas**.

Contact Enrichment v0.1 (capa posterior sobre el primer piloto) y los entregables a clientes contienen datos de contacto de personas
reales y no se publican.

## Probar y reproducir (sin red, 0 créditos)

Requisitos: Python 3 (probado con 3.13), solo biblioteca estándar. `jsonschema` es opcional: un test de contraste de contratos la usa si
está instalada y se omite si no.

```bash
python3 -B -W ignore tests/run_all.py                      # suites del producto; escribe tests/LAST_RUN.json
(cd core && python3 -B -m unittest test_project_opportunity)   # 25 tests de core/

Q="constructoras españolas en crecimiento que puedan necesitar software de gestión"
python3 -m oi run "$Q" --offline --out /tmp/oi_runs              # E2E con datos SINTÉTICOS etiquetados
python3 -m oi run "$Q" --offline --estimate --out /tmp/oi_runs   # solo presupuesto (no ejecuta)
python3 -m oi run "$Q" --real                                     # RECHAZADO (código 3)
```

Resultado esperado en esta edición: **371 tests ejecutados, 0 fallos, 15 omitidos** (necesitan el histórico privado, con motivo
explícito) y **6 suites marcadas «NO PUBLICADA»** (F12–F15.1, 230 tests sobre los datos reales de un piloto). Más los 25 de `core/`.
En la copia privada del proyecto, con todos los datos, la suite completa ejecuta **601/601**. `--replay-real` necesita datos que no se
publican y falla en esta edición.

Cada ejecución deja `01_draft_search_spec` · `02_spec_result` · `03_sector_mapping` · `05_research_plan` · `06_approval` · `07_execution/`
· `08_evidence` · `09_qualification` · `10_contacts` · `11_opportunity_records` · `12_report.html` · `RUN_SUMMARY`. Códigos de salida:
`0` completo · `2` faltan decisiones del usuario o mapeo de sector · `3` ejecución real rechazada · `4` error de spec.

## Estructura del repositorio

```
oi/                     motor y pipeline (spec, planner, executor, evaluators, qualification, evidence, enrichment, presentation, contracts)
core/                   project_opportunity v0.1: cualificación de oportunidades basadas en proyectos
rules/                  reglas versionadas como datos
cases/                  configuración por caso (reseller_pilot: registros SINTÉTICOS; construction_software: dataset sintético)
config/                 configuración del producto (gasto desactivado)
tests/                  suites del producto (+ paridad del motor migrado)
tools/                  regeneración de demos, golden y manifest
docs/                   informes de diseño y de fase, historial de decisiones, informe de saneamiento → docs/INDEX.md
archive/ phase1_audit/  archivo del producto y auditoría previa a F1 (versión redactada)
```

## Limitaciones conocidas

- Sin LLM integrado: el intérprete local solo extrae literalmente. «El LLM propone» se cumple por protocolo.
- Sin productor automático de evidencia first-party. En los pilotos, la evidencia se obtuvo a mano con búsqueda web gratuita.
- D010 no está conectado al pipeline (la salida se ordena por `discovery_rank`). Data Confidence no está calibrado.
- Ground truth de los pilotos provisional; un único revisor humano en el primero.
- Mapeo CNAE 41/42 → NAICS 236–238 sin verificar. El umbral de crecimiento está pendiente de decisión del usuario.
- Esta edición no puede reproducir los resultados de los pilotos: sus datos son privados.

## Próximos hitos

1. Productor de evidencia first-party a partir de fuentes públicas gratuitas: el cuello de botella medido.
2. Conectar `core/project_opportunity.py` y D010 al pipeline y al CLI.
3. Filtros baratos de categoría/producto antes del enrichment y caché de enrichment (aprendizajes del primer piloto).
4. Definir la siguiente fase (F16).

Índice de documentación: [`docs/INDEX.md`](docs/INDEX.md) · Cambios: [`CHANGELOG.md`](CHANGELOG.md)
