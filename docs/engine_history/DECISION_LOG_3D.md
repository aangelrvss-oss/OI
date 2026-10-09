# DECISION_LOG_3D — decisiones nuevas (continúa tras D-048 del README; no se sobrescribe ninguna anterior)
| ID | Decisión | Estado | Relación |
|---|---|---|---|
| D-049 | La identidad se valida **después** de cada enrichment con 4 estados; solo `KNOWN_MATCH` es evidencia del candidato pedido; `KNOWN_MISMATCH`/`AMBIGUOUS`/`UNKNOWN` van a cuarentena (pagados e inutilizables) | Accepted (implementada) | complementa D-046 |
| D-050 | Prohibido el fuzzy matching: el nombre nunca decide; dos entidades con nombre parecido e ids/dominios distintos son `KNOWN_MISMATCH` | Accepted | — |
| D-051 | Una petición sin registro y un registro huérfano en el mismo lote se emparejan solo para **validar** (nunca para usar); con ≥2 de cada, no se empareja | Accepted | — |
| D-052 | País devuelto fuera de catálogo = conflicto solo si difiere el id y falta el dominio | Accepted (riesgo anotado) | — |
| D-053 | Geografía: unión del conjunto declarado; Madrid ⊂ Comunidad ⊂ España; «España no implica Madrid» | Accepted | **supersede** el comportamiento implícito de 3C (local ⇒ exigir texto) |
| D-054 | Se mantienen dos fuentes para decidir ubicación; `raw_address` solo confirma en positivo (segmentos), salvo país/región distintos | Accepted | mantiene la regla previa de dos fuentes |
| D-055 | Ventanas temporales: UTC, inclusivas, futuro = `CONFLICTING_DATE`, ilegible ⇒ `UNKNOWN_DATE`; un evento no es reciente por existir | Accepted | **supersede** `field_presence` para `growth.funding_event` |
| D-056 | Selección = estrategias explícitas y auditables sin pesos; el defecto sigue siendo `interleave_position_v1` (reproduce 3C) | Accepted | — |
| D-057 | `overlap_diversity_v2` queda como **candidata** a calibración; no se adopta sin etiquetas | Proposed / OPEN | OD-1 |
| D-058 | Señales de selección clasificadas: gates (dominio, KEEP, exclusiones) vs prioridad (solape, posición, ámbito, grupo); tamaño y afinidad técnica no existen antes del enrichment y no se usan | Accepted | `employee_count_requirement=NOT_APPLICABLE` se mantiene |
| D-059 | El Search Spec describe **qué saber** mediante clases de fuente; el planner decide proveedores; registro 1.2.0 provider-neutral | Accepted | **refuerza/hace cumplir** D-047 |
| D-060 | El motor rechaza nombres de proveedor en un spec (`PROVIDER_NAMED_IN_SPEC`) | Accepted | D-047 |
| D-061 | El plan publica `evidence_requirements` por criterio (clases, proveedores, política, `resolvable`) | Accepted | — |
| D-062 | Spec/plan 3C (registro 1.1.0) no se replanifican con 1.2.0; se conservan como evidencia histórica | Accepted | — |
| D-063 | `min_evidence_items=2` estaba configurado en 3C (el diseño decía 1); se documenta y se deja abierto | Proposed / OPEN | OD-3 |
**OPEN DECISIONS:** OD-1 regla de selección definitiva; OD-2 FAIL de municipio con una sola fuente; OD-3 `min_evidence_items`; OD-4 prioridad geográfica como orden de salida; OD-5 recuperación por id de un registro erróneo (capacidad UNVERIFIED).
