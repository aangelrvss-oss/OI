# Search Spec v2 — compatibilidad y migración

## Qué añade v2
`intent.offering.kind`, `intent.offering.target_function`, `intent.business_model[]`, `intent.entity_level` (enums pequeños y extensibles en `oi/contracts/common.v1.schema.json`; la paridad con `search_spec.v2.schema.json` se comprueba en test).

| Campo | Valores |
|---|---|
| `offering.kind` | PRODUCT_FOR_RESALE · SOFTWARE · SERVICE · CONSULTING · EQUIPMENT · OTHER · UNKNOWN (+ `null` = aún no indicado) |
| `offering.target_function` | OPERATIONS · FINANCE · IT · PROCUREMENT · SALES · MARKETING · HR · MANAGEMENT · LEGAL_COMPLIANCE · OTHER · UNKNOWN |
| `business_model[]` | manufacturer · distributor · retailer · service_provider · contractor · operator · unknown |
| `entity_level` | ESTABLISHMENT · OPERATOR · GROUP · COMPANY · UNKNOWN |

## Convivencia con v1
* Los esquemas v1 y v2 **coexisten** en `oi/contracts/`. La librería usa **1.0 por defecto** (no rompe nada existente); el producto (`oi run`) usa **2.0**.
* Un spec lleva su `versions.schema_version`; el planner valida cada spec con **su** esquema.
* Procesar un borrador con campos v2 usando el esquema 1.0 **no los conserva en silencio**: los descarta y emite el aviso `V2_FIELDS_IGNORED_IN_V1`.
* Los artefactos históricos (specs v1) no se tocan: sus `content_hash` siguen válidos.

## Migración v1 → v2 (`oi/engine/spec/migrate.py`)
* Produce un documento **nuevo** (hash propio) y deja el v1 intacto; se niega a migrar un spec cuyo hash no coincide.
* No inventa: `offering.kind = UNKNOWN`, `target_function = UNKNOWN`, `business_model = ["unknown"]`, `entity_level = UNKNOWN`, con una pregunta de confirmación abierta y rastro en `assumptions`/`traceability.migrated_from`.
* Consecuencia práctica: un spec migrado puede buscar empresas, pero **no recomienda a quién contactar** hasta que alguien clasifique la oferta.

## Comportamiento de las preguntas
* `offering.kind` ausente con oferta descrita → pregunta **bloqueante** (`OFFERING_KIND_MISSING`).
* `offering.kind = UNKNOWN` → aviso no bloqueante (`OFFERING_KIND_UNKNOWN`); el selector de contacto se niega a elegir perfil (`OFFERING_KIND_REQUIRED`).
* `target_function` nunca se rellena por defecto con algo distinto de UNKNOWN.

## Piloto de reventa
Su Search Spec era v1 y no tenía `offering.kind`. La clasificación `PRODUCT_FOR_RESALE` se asignó **retroactivamente** (aprobación de la auditoría) y vive solo en `cases/reseller_pilot/case.json`, marcada `RETROACTIVE_CLASSIFICATION`. No se modificó ningún artefacto del experimento.
