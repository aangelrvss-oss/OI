# Mapa de migración (F1)

Principio: **COPIAR, no mover; nada histórico se borra**. El árbol origen del motor (`/home/claude/intent_engine`, 289 ficheros) sigue intacto (sha256 del árbol en `tools/MIGRATION_DONE.json`). La migración es reproducible: `python3 tools/migrate_engine.py` (se niega a re-ejecutarse sin `--force`).

## Dónde vive ahora cada cosa

| Antes (origen / histórico) | Ahora (vivo) | Notas |
|---|---|---|
| `intent_engine/*` (Search Spec) | `oi/engine/spec/` | imports reescritos mecánicamente; añadidos `migrate.py`, `sector_mapping.py`, `mock_interpreter.py` |
| `research_planner/*` | `oi/engine/planner/` | valida cada spec con su propio esquema (1.0 / 2.0) |
| `executor/*` | `oi/engine/executor/` | sin cambios de lógica; sigue sin autorizar gasto |
| evaluadores / identidad / geografía / relevancia | `oi/engine/evaluators/` | |
| `data/*.json`, `planner_data/*.json` (reglas) | `rules/spec/`, `rules/planner/` | datos fuera del código |
| esquemas JSON | `oi/contracts/` | `search_spec.v1/v2`, `research_plan.v2`, `opportunity_record.v1`, `opportunity_contact.v1`, `common.v1`, `contact_profiles.v1`, `sector_mapping.v1` |
| `planner_data/archive_v1`, `*.v1.json` del planner | `archive/planner_data_v1/` | solo lectura |
| `d010_v0_2_official/d010_engine.py` + reglas | `oi/rules/d010/{engine.py,rules.json,schema.json,README.md}` | paridad por hash (20 del piloto de reventa + 600 sintéticos) |
| tests del motor (4 suites + harness) | `tests/legacy_parity/` | 180 tests, mismos resultados |
| `experiments/exp_3b2.py` (fixture de preflight) | `tests/legacy_parity/preflight_fixture.py` | |
| docs del motor | `docs/engine_history/` | |
| Piloto de reventa (vista) | `cases/reseller_pilot/` | `case.json`, `adapter.py` (solo lectura), `presentation.json`, `records/` |
| (nuevo) Construcción+software | `cases/construction_software/` | configurado; solo ejecutable offline con datos sintéticos etiquetados |

## Qué NO se movió (deliberadamente)
Las carpetas de experimentos de `/mnt/user-data/outputs/` (`reseller_pilot_history/`, `experiment_3*/`, `mvp_e2e/`, `4e_*`, zips `intent_engine_fase*.zip`, …) **no se han trasladado**: moverlas cambiaría rutas citadas en artefactos históricos y sus hashes de referencia. Quedan donde están, como historia de solo lectura, listadas en `archive/INDEX.md`. Trasladarlas físicamente es una decisión separada y reversible que no hace falta para tener un único motor vivo.

## Verificación
`tests/historical/` comprueba 444 ficheros históricos por sha256 (ninguno cambia, ninguno se añade o desaparece) y `tests/legacy_parity/` el motor migrado.


## F11 — pipeline E2E offline
* Nuevo `oi/pipeline/` (guard, cases, approval, estimate, records, orchestrator), `oi/qualification/`, `oi/evidence/{intake,producers}.py`, `oi/enrichment/contact/{roles,candidates}.py`.
* `oi/cli.py` reescrito: `--offline`, `--estimate`, `--real` (bloqueado), `--strict`; `--demo-synthetic` eliminado.
* Eliminados `make_synthetic_records.py` y `synthetic_opportunity_records.json` (registros prefabricados); sustituidos por `fixtures/e2e_dataset.json` + salida del pipeline.
* Sin cambios en datos históricos del piloto de reventa, ledgers, hashes ni D-xxx.

## F12 — preflight real (sin gasto)
* Field Registry 1.2.0 → 1.3.0: campo genérico `growth.signal` (presencia; params `signal_id` (código) y `window_months`); routing 2.3.0; regla TS-F2 lo incluye; validación acepta parámetros «código». Test de paridad actualizado al nuevo número de versión.
* Nuevos: `oi/pipeline/{preview,review}.py`, `tools/f12_preview.py`, `cases/construction_software/real_experiment/`, `tests/f12/`.

## F13 — replay de datos reales (sin gasto)
* Nuevo `oi/pipeline/replay.py` y modo `replay` en `oi/pipeline/guard.py` (misma barrera única; `real` sigue bloqueado). Orquestador, aprobación, productor de evidencia y CLI (`--offline --replay-real`) parametrizados; sin segundo motor.
* Contratos: campo opcional `evidence_origin` (APOLLO_FREE_LOOKUP · FIRST_PARTY_WEB · HUMAN_REVIEW · OTHER_VERIFIABLE) en `evidence_input.v1` y `common.v1#evidence`. `rules/planner/planning_params.v2.json`: `organization_num_employees_ranges` admitido en consultas explícitas.
* Nuevos: `cases/construction_software/real_replay/` (captura, perfil, evidencia vacía), `tools/f13_capture.py`, `tests/f13/` (70), `docs/F13_REAL_DATA_REPLAY_REPORT.md`, `docs/f13_replay_output/`.
* Sin cambios en datos históricos del piloto de reventa, ledgers, hashes ni D-xxx; el sondeo de F12 solo se lee.

## F13.1 — Preparación de F14 (cero gasto)
- `oi/pipeline/f14prep.py` + `tools/f13_1_prepare.py` → `cases/construction_software/f14_preparation/`.
- Motor: puertas SIZE/SOFTWARE_FIT/GROWTH por perfil en replay; origen `APOLLO_ENRICHMENT` (nunca KNOWN); `acceptable_source_classes_override`.
- Tests: `tests/f13_1` (31). Total 509. Informe: `docs/F13_1_F14_PREPARATION_REPORT.md`; solicitud: `docs/F14_EXECUTION_AUTHORIZATION_REQUEST.md`.

## F15 — Ground truth + evaluación de F14 (cero gasto)
- Motor: `qualifier.resolve_range_gate` (conflicto de tamaño general), acción `VERIFY_EVIDENCE` (rules/next_action 1.1.0, contrato y i18n).
- Datos/herramientas: `cases/construction_software/f15_evaluation/`, `tools/f15_review_data.py`, `tools/f15_evaluate.py`; tests `tests/f15` (36). Total 553. Informe: `docs/F15_GROUND_TRUTH_EVALUATION.md`.

## F15.1 — Validación humana asistida y cierre de F14/F15 (cero gasto, ENGINE_CHANGES = 0)

- Datos/herramienta: `cases/construction_software/f15_1_validation/` (nuevo; `f14_run` y `f15_evaluation` intactos por hash), `tools/f15_1_validate.py`; tests `tests/f15_1`. Informe: `docs/F15_1_HUMAN_VALIDATION_REPORT.md`.
- Evidencia reclasificada: `SEARCH_RESULT_TITLE` / `OFFICIAL_PAGE_READ` / `OTHER_VERIFIABLE_READ` / `PROVIDER_ONLY` / `NO_EVIDENCE_READ` (sustituye a `WEAK_TITLE`). Etiquetas: sin cambios (1/3/5); `HUMAN_CONFIRMED = NOT_AVAILABLE`.
