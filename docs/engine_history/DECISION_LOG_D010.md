# DECISION_LOG_D010 — D010-v0.2 OFFICIAL
Continúa tras D-063 (DECISION_LOG_3D). No se sobrescribe ninguna decisión anterior.
| ID | Decisión | Estado | Relación |
|---|---|---|---|
| D-010 | Modelo de decisión (FIT/need/score/elegibilidad/ranking): **D010-v0.2 OFFICIAL** | **OFFICIAL** | sustituye `D010-proposed-v0.1` (README: «Proposed») |
| D-064 | `fit_minimal` reemplaza al «FIT gate»; solo cumplimiento mínimo de gates | Accepted | resuelve OD-5 de 3H |
| D-065 | `fit_affinity` dimensión separada y preparada; sin señales nuevas | Accepted | — |
| D-066 | `need`: CORE (T1, P1, O2) y WEAK (O4, O5); pesos ordinales 2/1 sin lectura probabilística ni económica | Accepted | resuelve OD-2 |
| D-067 | Suficiencia: min_units=2, min_core_units=1, allow_single_high_core=true | Accepted | resuelve OD-3 y OD-4; D3E-003 |
| D-068 | UNKNOWN = intervalo (nunca cero); CONFLICTING visible y no positivo; PENDING_EVIDENCE definido | Accepted | sustituye ELIGIBILITY_UNDEFINED |
| D-069 | Elegibilidad antes del ranking; NOT_ELIGIBLE solo por gate FAIL, mismatch o suficiencia imposible | Accepted | — |
| D-070 | El total 0–12 es solo informativo; usos prohibidos (criterio comercial absoluto, probabilidad, valor económico) | Accepted | — |
| D-071 | Ranking oficial por capas de Pareto del floor → banda → cobertura; dominancia por intervalos y orden parcial = diagnósticos secundarios | Accepted | **supersede** al orden parcial como ranking principal de 3I (decisión 18) y cierra OD-1 |
| D-072 | Versionado: `D010-v0.2 OFFICIAL`; `D010-proposed-v0.1` y la etiqueta `D010-v1.0` de 3I quedan como históricos | Accepted | — |
Pendiente declarado: el plan y el spec congelados (hashes `e34420bb…` y `62835e4c…`) siguen mencionando «D-010 Proposed» y no pueden editarse sin romper sus hashes.
