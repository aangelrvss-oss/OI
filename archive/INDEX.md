# Archivo (solo lectura)

Nada de lo listado aquí es motor vivo. Se conserva para reproducibilidad y no debe editarse (la integridad de `reseller_pilot_history/` y `d010_v0_2_official/` se vigila por hash en `tests/historical/`).

## Dentro del producto
* `archive/planner_data_v1/` — reglas/contratos v1 del planner, sustituidos por v2.
* `docs/engine_history/` — documentos de arquitectura y decisiones del motor (3D, D010).

## Fuera del producto (`/mnt/user-data/outputs/`, no trasladados)
| Carpeta | Qué es |
|---|---|
| `reseller_pilot_history/` | Experimento del piloto de reventa **cerrado e inmutable** (ledgers, B1/B2, Gate 1/2, T1, T2, T3-PREVIEW, feedback oficial). Fuente de `cases/reseller_pilot/`. |
| `d010_v0_2_official/` | D010-v0.2 OFFICIAL original. Referencia de paridad del motor `oi/rules/d010/`. |
| `experiment_3h/` | Propuesta de D010 (PROPOSED) y su simulación/sensibilidad; solo simulación. **No es motor vivo.** |
| `experiment_3i/` | Backtest con `d010_engine.py` **D010-v1.0** (versión distinta de la OFFICIAL v0.2; difiere de ella por hash). Histórico. **No es motor vivo.** |
| `experiment_3b2/ 3e/ 3f/ 3g/`, `calibration_3d/`, `4e_*`, `mvp_e2e/`, `phase_3c_pre/`, `run_*`, `v3_*`, `validation_4c/`, `signal_*` | Experimentos, calibraciones y ejecuciones anteriores. |
| `intent_engine_fase*.zip`, `intent_engine_d010_v02.zip` | Instantáneas empaquetadas del motor por fase. |

## Política de duplicados de D010
Hay **una** implementación viva: `oi/rules/d010/engine.py` (lógica) + `rules.json` (configuración versionada). Las copias de `d010_v0_2_official/`, `experiment_3h/` y `experiment_3i/` son historia: no se importan desde el producto. Un test (`tests/generalization`) comprueba que `evaluate_candidate` y `build_ranking` existen solo en `oi/rules/d010/engine.py` dentro del árbol del producto. Esto **no** impide que las carpetas históricas contengan copias antiguas; por eso la política es: leerlas, no ejecutarlas desde el producto, no editarlas.
