# D010 — modelo de decisión (D010-v0.2 OFFICIAL)

Motor determinista que, dadas las señales observadas de un candidato, devuelve **elegibilidad**, un **intervalo de score** (suelo demostrado / techo posible) y un **ranking por capas**. Es lo único que decide "qué candidato va antes"; no hay otra copia viva.

| Fichero | Qué es |
|---|---|
| `engine.py` | Lógica pura (sin red, reloj ni azar). No contiene IDs de señales de ningún caso. |
| `rules.json` | **Toda** la configuración: unidades, pesos, suficiencia, gates, orden de elegibilidad. Versionada. |
| `schema.json` | JSON Schema de `rules.json`. `load_rules()` lo valida + invariantes cruzadas. |

## Qué garantiza
- `UNKNOWN` no es cero: aporta 0 al suelo y su peso al techo.
- `CONFLICTING` no cuenta como evidencia positiva.
- Evidencia duplicada (mismo `evidence_id`) cuenta una vez.
- Una señal `WEAK` nunca basta sola; hace falta ≥ 1 `CORE` (o un `CORE` con confianza alta).
- El score **no es una probabilidad ni valor económico**, el total es informativo y no se usa para ordenar. `calibrated = false`.
- Empates reales siguen siendo empates (sin desempates por tamaño, fama, nombre ni orden de descubrimiento).

## Cómo se cambia una regla (sin duplicar el motor)
1. Copiar `rules.json` → editar → bump de `score_version`/`label`.
2. `load_rules(ruta)` valida contra `schema.json` y rechaza reglas incoherentes.
3. `evaluate_candidate(candidato, evidencia, rules=nuevas_reglas)`.
4. `rules_hash(rules)` identifica la versión efectiva (las claves `_*` son documentación y no cuentan).

## Los IDs de unidad (`F1`, `FIT_KW`, `T1`, `P1`, `O2`, `O4`, `O5`)
Son **claves estables de resultados históricos** (del piloto de reventa). Viven solo en `rules.json`; el significado de cada una está en `rules.json::_doc.unit_names`. Un caso nuevo (p. ej. construcción + software) define sus propias unidades en su propio `rules.json` versionado; el motor no cambia.

## Procedencia y paridad
`rules.json` es el contenido literal de `D010_v0.2_RULES.json` (D010-v0.2 OFFICIAL). Paridad comprobada en `tests/rules/test_d010.py`:
hashes de resultado idénticos sobre 20 candidatos del piloto de reventa + 600 sintéticos (semilla fija), más igualdad en vivo con el motor original si está presente.
El original (`d010_v0_2_official/`), la propuesta 3H y la etiqueta v1.0 del motor 3I son **histórico de solo lectura** (ver `archive/INDEX.md`).
