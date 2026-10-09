# F11 — E2E OFFLINE / ZERO-COST PIPELINE · Informe

Fecha: 2026-10-08 · Datos: SINTÉTICOS · Llamadas externas: 0 · Créditos: 0

## A. Resultado E2E
`python3 -m oi run "constructoras españolas en crecimiento que puedan necesitar software de gestión" --offline` → `COMPLETED_OFFLINE` (exit 0), sin intervención manual:
Search Spec v2 → validación → mapeo de sector (CONSTRUCTION, **UNVERIFIED**, consulta Apollo `PROPOSED_NOT_PROBED`) → Research Plan → aprobación offline (max_credits 0) → **el Executor existente** con `FixtureTransport` tras `GuardedTransport` → descubrimiento / resolución de entidades / puertas baratas → evidencia (contrato `evidence_input.v1`, fuerza derivada) → cualificación (`known_evidence_gates@1.0.0`) → selección de contacto (evidencia → candidatos → perfil → selector) → 18 `opportunity_record.v1` → `12_report.html` (renderer genérico).

Resultado sintético: 18 registros: 5 CONTACT (Alfa A, Beta B, Gamma C, Iota B [departamento], Rho C [persona caducada → empresa]), resto HUMAN_REVIEW / ENRICH_FIRST / NO_ACTION. Zeta tiene contacto grado A pero **no** es CONTACT (QUALIFICATION_PENDING). Un email solo no crea oportunidad. Nivel de entidad y modelo de negocio viajan Search Spec → candidato → cualificación → registro (`search_context.requested_*` + gates ENTITY_LEVEL / BUSINESS_MODEL; sin gate si se pide UNKNOWN).

Supuesto etiquetado `OFFLINE_SYNTHETIC_ASSUMPTION` (crecimiento ≥10 % en 365 d): **no es decisión del usuario**; solo con `--offline` sin `--strict`; cede ante respuestas del usuario.

## B. Tests (ejecución real: `python3 -W ignore -B tests/run_all.py`)
| existing | new (tests/e2e) | total | passed | failed |
|---|---|---|---|---|
| 311 | 60 | 371 | 371 | 0 |

Suites: contracts 21 · spec_v2 24 · safety 35 · generalization 27 · historical 15 · rules 9 · legacy_parity 180 · e2e 60.
Nota de transparencia: 13 tests existentes dependían de registros prefabricados / `--demo-synthetic`; se adaptaron para leer la salida del pipeline (el recuento 311 no cambia). Un cambio en producción: el renderer ordena los atributos de facetas (salida determinista).

## C. Llamadas externas: **0** (EXTERNAL_CALLS). Llamadas sintéticas locales: solo del transporte de fixtures. paid_calls: 0.
## D. Créditos: **0** (APOLLO_CREDITS = 0). `--real` rechazado (exit 3).

## E. Limitaciones
* Todo dato es sintético; no hay productor de evidencia real (web) ni transporte real cableado.
* Mapeo de sector UNVERIFIED (CNAE 41/42 y NAICS sin verificar; etiquetas Apollo sin sondear).
* Umbral/ventana de «en crecimiento» y función destinataria: decisión del usuario pendiente (offline usa supuesto marcado).
* Intérprete de lenguaje natural local determinista (no LLM).
* Coste de People Search y de enriquecimiento de contactos: UNKNOWN.
* Caso ligado por forma del Search Spec; un segundo caso real requeriría su propio dataset/perfil.
* `__pycache__` preexistentes (07-oct, sesiones históricas del piloto de reventa) en `reseller_pilot_history/*`: no creados ni modificados por F11 (el test lo verifica por mtime); no se han tocado.
* `project_write` al Proyecto fue rechazado por falta de espacio; informe solo en `docs/`.

## F. Próximo paso para la primera prueba REAL (no ejecutado)
Decisión humana explícita (umbral de crecimiento; autorización de ejecución con tope y `paid_enrichment_authorized`), sondeo de taxonomía/sector del proveedor, un transporte real detrás de `guard.py` y un productor de evidencia real; sin esto `--real` seguirá bloqueado.

## Integridad histórica
Piloto de reventa, feedback oficial, ledgers, hashes y D-xxx sin cambios (test `historical` y E2E-10 comparan manifiesto de 444 ficheros y hashes de ledger antes/después de ejecutar el pipeline).
