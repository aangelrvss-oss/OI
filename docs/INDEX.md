# Índice de documentación (edición pública)

## Empezar aquí
| Documento | Para qué |
|---|---|
| [`README.md`](../README.md) | Qué es, arquitectura, estado real, cómo probarlo |
| [`docs/SANITIZATION_REPORT.md`](SANITIZATION_REPORT.md) | Qué se retiró de esta edición, qué se sustituyó por datos sintéticos y cómo se comprobó |
| [`CHANGELOG.md`](../CHANGELOG.md) | Cambios relevantes |

## Arquitectura y contratos
| Documento | Contenido |
|---|---|
| [`phase1_audit/OI_PHASE1_AUDIT_AND_DESIGN.md`](../phase1_audit/OI_PHASE1_AUDIT_AND_DESIGN.md) | Auditoría y diseño previos a F1 (versión redactada) |
| [`docs/MIGRATION_MAP.md`](MIGRATION_MAP.md) | Del motor original al motor único (`oi/engine/`) |
| [`docs/SEARCH_SPEC_V2_COMPATIBILITY.md`](SEARCH_SPEC_V2_COMPATIBILITY.md) | Search Spec v2 y migración desde v1 |
| [`oi/rules/d010/README.md`](../oi/rules/d010/README.md) | Modelo de decisión D010-v0.2 OFFICIAL (reglas como datos) |
| `oi/contracts/*.schema.json` | Contratos: `search_spec` v1/v2, `research_plan.v2`, `evidence_input.v1`, `opportunity_record.v1`, `opportunity_contact.v1`, `contact_profiles.v1`, `sector_mapping.v1` |
| `core/project_opportunity.py` | Cualificación de oportunidades basadas en proyectos (v0.1) |
| [`archive/INDEX.md`](../archive/INDEX.md) | Archivo del producto |
| `docs/engine_history/` | Arquitectura y logs de decisiones del motor previo (3D, D010) |

## Informes de fase publicados
| Fase | Documento |
|---|---|
| F1–F10 | [`docs/OI_BLOCK_F1_F10_REPORT.md`](OI_BLOCK_F1_F10_REPORT.md) |
| F11 | [`docs/OI_F11_E2E_OFFLINE_REPORT.md`](OI_F11_E2E_OFFLINE_REPORT.md) |
| F12–F15.1 | No publicados: son informes sobre datos reales de un piloto (resumen agregado en el `README.md`) |

## Experimentos
| Experimento | Documento |
|---|---|
| Piloto de reventa (cerrado) | [`docs/experiments/reseller-pilot/README.md`](experiments/reseller-pilot/README.md) |
| Construcción + software (F12–F15.1) y piloto por proyectos | Resumen agregado en el `README.md`; datos privados |
