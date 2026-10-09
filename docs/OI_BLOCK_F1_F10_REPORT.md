# Informe del bloque F1–F10 (implementación)

Estado: **PAID_ENRICHMENT_AUTHORIZED = false · 0 créditos gastados · 0 llamadas de pago · sin red**. No se ha iniciado la prueba real de Construcción+software.

## A. Qué se implementó
* **F1** Motor único en `oi/engine/{spec,planner,executor,evaluators}` por migración mecánica y reproducible (copia, no mueve; origen verificado por hash: 289 ficheros). Datos fuera del código (`rules/`, `oi/contracts/`).
* **F2** D010 consolidado en `oi/rules/d010/` (lógica + `rules.json` versionado + `schema.json` + README). Paridad por hash con el motor OFFICIAL (20 candidatos del piloto de reventa + 600 sintéticos).
* **F3** `opportunity_record.v1` (JSON Schema) y validador propio sin dependencias (rechaza keywords no soportadas; contrastado con `jsonschema`).
* **F4** `opportunity_contact.v1` + `rules/contact_profiles.v1.json` (SELL_PRODUCT_TO_RESELLER, SELL_SOFTWARE_OR_SERVICE), selector léxicográfico, fuerza de evidencia derivada, calidad derivada, comprobaciones de seguridad, siguiente paso por reglas (`rules/next_action.v1.json`).
* **F5** Search Spec v2 (`offering.kind`, `offering.target_function`, `business_model`, `entity_level`) con v1 intacto, migración v1→v2 que no inventa y avisos explícitos.
* **F6** `cases/reseller_pilot/` (importador de solo lectura con verificación de hashes) y `cases/construction_software/` (spec, parámetros pendientes, léxicos, exclusiones, perfil de contacto, requisitos de evidencia, presentación, fixtures sintéticos).
* **F7** Mapeo sectorial curado `rules/sector_mapping/sector_mapping.es.json`: un término sin entrada devuelve MAPPING_REQUIRED.
* **F8** `python3 -m oi run "<necesidad>"`: intérprete local → validación → preguntas → mapeo → plan → (sin ejecución real) → registros → HTML. `--real` se rechaza.
* **F9** Renderer genérico `oi/presentation` (ES/EN). Mismo código para el piloto de reventa y Construcción; sin literales de caso.
* **F10** Tests de contratos, Search Spec v2, seguridad, generalización e integridad histórica.

## D. Tests (números reales, `tests/run_all.py`)
contracts 21 · spec_v2 24 · safety 35 · generalization 27 · historical 15 · rules (D010) 9 · legacy_parity (motor migrado) 180 → **311 ejecutados, 0 fallos, 0 errores, 0 omitidos**.
Además: tests oficiales de D010 contra el motor nuevo: 37/39 (los 2 fallos son idénticos en el original: falta `/tmp/ctx_d010v02.json`).
Sabotaje de comprobación: desactivar `check_contact` hace fallar 8 de 33 tests de seguridad; desactivar el mapeo hace fallar el test de MAPPING_REQUIRED.

## E. Problemas / inconsistencias
1. La tabla §3.4 de la auditoría mezclaba fuerza de evidencia y propósito; corregida en el contrato.
2. La tabla retrospectiva de la auditoría (3 B / 7 C / 2 D) no coincide con lo real para los 12 SÍ: **0 A / 3 B / 5 C / 4 sin contacto recomendado**.
3. El primer adaptador del piloto de reventa sobreestimaba grado A en tres cadenas de distribución (portal tratado como directo); corregido (`outreach_access`; DIRECT solo si OPEN).
4. El contrato no puede expresar un destino «departamento» sin canal (p. ej. la sección «Proveedores» de una cadena): mejora candidata.
5. El motor v1 descartaba en silencio campos v2: ahora avisa (`V2_FIELDS_IGNORED_IN_V1`) y no los conserva.
6. Hueco en las reglas de siguiente paso: con cualificación pendiente salía «Contactar»; añadida `QUALIFICATION_PENDING` (piloto de reventa sin cambios: 15 / 8 / 4 / 3).
7. Mi ejecución dejó un `__pycache__` dentro de `d010_v0_2_official/`; **eliminado** (artefacto mío, ningún fichero histórico cambió). Otros `__pycache__` preexistentes en carpetas históricas no se tocaron.
8. `oi run` **no** conecta el executor ni siquiera en modo dry: los registros de la demo son fixtures sintéticos, no el resultado de ejecutar la petición (la página lo dice).
9. CNAE 41/42 sin verificar; consulta Apollo de construcción sin sondear; `target_function` = UNKNOWN (el usuario no lo dijo).
10. El intérprete local solo extrae literalmente; no sustituye a un LLM.
11. Con 0 créditos el planner devuelve plan `blocked` («ENRICHMENT: capacidad 0»), como corresponde.
12. `tests/fixtures/answers_TEST_ONLY.json` usa valores arbitrarios (10, 365 días) solo para probar mecánica; está marcado y no se usa en `cases/`.
13. Cadena `research_planner.stopping.decide_outcome` dentro de un plan, sin renombrar por ser sensible al hash.
14. Las carpetas de experimentos fuera del producto (incl. dos copias históricas de D010 en 3H/3I) no se trasladaron; ver `archive/INDEX.md`.

## F. Qué falta para la primera prueba real
Decisiones del usuario: dimensión/umbral/ventana de «en crecimiento»; (opcional) función destinataria, cantidad, tamaño, exclusiones. Revisión humana del mapeo ES-CONSTRUCTION-001 y sondeo de la consulta Apollo. Conectar el executor a registros (descubrimiento → enriquecimiento → `opportunity_record`) y volver a planificar con el tope autorizado. Enriquecimiento de contacto gratuito para las empresas halladas.

## G. Coste de esa prueba
Tope propuesto: **10 créditos Apollo** (no autorizado). Consumo real **no estimado**: con 0 créditos el plan queda bloqueado y aún no se ha replanificado con 10. Firecrawl de pago / OpenRush: 0 (no autorizados).
