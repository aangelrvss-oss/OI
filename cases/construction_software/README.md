# Caso: Construcción + software de gestión

Petición: «Empresas constructoras españolas en crecimiento que puedan ser clientes potenciales de software de gestión.»

**Estado:** configurado y ejecutable **offline** con datos SINTÉTICOS (`oi run … --offline`); **no ejecutado en real**. `PAID_ENRICHMENT_AUTHORIZED = false`; 0 créditos. El tope futuro de 10 créditos Apollo es un límite previsto, **no una autorización**.

| Fichero | Contenido |
|---|---|
| `spec.draft.json` | Borrador Search Spec v2 (generado por el intérprete local desde la petición literal) |
| `pending_parameters.json` | Decisiones que NO se han tomado: dimensión/umbral/ventana de «en crecimiento», función destinataria, cantidad, tamaño |
| `lexicons.json` | Solo términos escritos por el usuario |
| `exclusions.json` | Vacío: el usuario no indicó exclusiones |
| `contact_profile.json` | Referencia a `SELL_SOFTWARE_OR_SERVICE` (`rules/contact_profiles.v1.json`) |
| `evidence_requirements.json` | Requisitos de evidencia por criterio |
| `presentation.json` | Etiquetas y facetas para el renderer genérico |
| `binding` (en `case.json`) | Asociación por forma del Search Spec (tipo de oportunidad, sector, país), no por texto |
| `offline_profile.json` | Supuesto OFFLINE_SYNTHETIC_ASSUMPTION (crecimiento ≥10 % en 365 d; NO decisión del usuario) y planificación simulada |
| `fixtures/e2e_dataset.json` | Dataset SINTÉTICO (empresas `SYNTH …`, dominios `.example`): fuera de sector/país, duplicados, ambiguas, evidencia KNOWN/UNKNOWN/CONFLICTING/INFERRED, contactos A–E |
| `records/` | Copia de referencia de la SALIDA del pipeline (no fixtures preparados); regenerar con `tools/build_demos.py` |

Sector → `rules/sector_mapping/sector_mapping.es.json` (`ES-CONSTRUCTION-001`): CNAE 43 con verificación de tercero; **41 y 42 sin verificar**; NAICS sin mapear; consulta Apollo **no sondeada** (`PROPOSED_NOT_PROBED`).
