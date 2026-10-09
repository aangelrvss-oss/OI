# Informe de saneamiento: edición pública (2026-10-09)

Este repositorio es público. Esta edición se construyó desde cero, partiendo del commit inicial publicado (`cab9f5e`), a partir de la copia
privada del proyecto. **No reutiliza ningún commit de la copia privada**: su historial contenía datos de pilotos y no se publica. Este
informe no nombra las entidades retiradas ni las empresas de los casos.

## 1. Criterio

Publicar el valor técnico (arquitectura, contratos, reglas, motor, tests) y retirar:

- datos personales identificables (nombres, cargos asociados a personas, emails y teléfonos);
- listas de clientes y de prospectos, y evaluaciones humanas sobre empresas concretas;
- datos de proveedores de datos cuya licencia no permita confirmar su publicación (respuestas de enrichment y de lookup, identificadores de organización, cifras de plantilla e ingresos, descripciones);
- entregables y documentos dirigidos a clientes;
- la identidad de las empresas para las que se hicieron los pilotos.

Método: **lista de permitidos**. Solo entra lo revisado. Lo que hacía falta para que los tests fueran reproducibles se sustituyó por datos
sintéticos claramente etiquetados (`SYNTH`, `Sintético`, dominios `.example`, `data_origin = SYNTHETIC_FIXTURE`).

## 2. Casos con identificadores neutrales

Los casos reales se identifican con nombres neutrales que no corresponden a ninguna empresa:

| Identificador público | Qué es |
|---|---|
| `reseller_pilot` («Piloto de reventa») | Primer piloto: canales y clientes para un productor alimentario (perfil `SELL_PRODUCT_TO_RESELLER`). Prefijo de ids sintéticos `RP-` / `rp-`; consultas `RP-(MAY\|DIS\|CAD)-(MAD\|CYL\|ESP)` |
| `construction_software` | Generalización a constructoras como clientes de software de gestión (F12–F15.1) |
| Piloto por proyectos | Oportunidades por proyecto en Madrid para una empresa instaladora (`core/project_opportunity.py`; el campo de capacidad se llama `offering_capability`) |

El cambio de nombre afecta a carpetas (`cases/reseller_pilot/`, `docs/experiments/reseller-pilot/`), ids de caso y de registro, títulos,
configuración, documentación, tests y fixtures. Los tests que comprueban que el motor y el renderer no conocen ningún caso se mantienen
con el vocabulario nuevo (`reseller_pilot`, `RP-…`, «Piloto de reventa»). Se verificó que siguen detectando una inserción de ese
vocabulario en el renderer y en el motor.

## 3. No publicado

| Área de la copia privada | Motivo |
|---|---|
| Histórico de experimentos (fases 3B–4E, V3, MVP E2E, pilotos, D010 OFFICIAL original, ledgers, instantáneas del motor) | Respuestas de proveedores, candidatos reales, evaluaciones humanas, ledgers de gasto |
| Motor original previo a la migración | Su carpeta de experimentos contiene ejecuciones con datos reales; el motor migrado está en `oi/engine/` |
| Contact Enrichment y entregables a clientes | Datos de contacto de personas reales; documentos para un cliente |
| Datos reales del piloto de construcción (`real_experiment`, `real_replay`, `f14_*`, `f15_*`) | Respuestas de proveedor, ground truth sobre empresas reales |
| Caso completo del piloto por proyectos | Información comercial de la empresa del piloto, incluida su cartera de clientes; datos de prospectos |
| Suites `tests/f12` … `tests/f15_1` (230 tests) y sus herramientas `tools/f12_*`…`tools/f15_*` | Verifican los resultados de un experimento real e incrustan sus datos; sustituirlos por datos inventados no probaría nada |
| Informes F12–F15.1, manifest de recuperación, registro de integración, un informe de calibración 3D, salida de replay | Describen datos reales, contexto privado del propietario o la relación con terceros |
| Transcripciones de sesión y órdenes originales | Conversación privada |

## 4. Sustituido por datos sintéticos

| Fichero | Transformación | Qué se conserva |
|---|---|---|
| `cases/reseller_pilot/records/opportunity_records.json` | Para cada registro, un mapeo consistente sustituye empresa, personas, dominios, URL, emails, teléfonos e id de organización por valores sintéticos (el mismo valor real da siempre el mismo sintético). Texto libre, motivos del revisor, notas, contradicciones y direcciones se sustituyen por texto sintético; las cifras de proveedor pasan a bandas. `data_origin = SYNTHETIC_FIXTURE` | Estructura de claves idéntica, estados de verdad, grupos, SÍ/DUDOSA/NO agregados (12/3/15), acciones (15/8/4/3), coherencia canal = evidencia |
| `cases/reseller_pilot/records/report_reseller_pilot.html` | Regenerado con `tools/build_demos.py` desde los registros sintéticos. Título «Piloto de reventa · datos sintéticos» y aviso de datos sintéticos | — |
| `cases/construction_software/records/opportunity_records_SYNTHETIC.json` | Regenerado con `tools/build_demos.py`: al generalizar los textos de `capability_matrix.v1.json` cambia el hash del plan y, con él, los ids de plan y de ejecución de la demo sintética | Mismos 18 registros y acciones |
| `tests/fixtures/d010_golden.json` (caso `reseller_pilot`, 20 candidatos) | Ids de candidato sintéticos (`synthetic:rp-NN`); valor, fuente, nota y fecha de cada evidencia retirados | Estados de señales y gates idénticos. D010 no lee esos valores: los resultados son idénticos salvo el id. Hashes esperados recalculados **con el motor D010-v0.2 OFFICIAL original**. Los 600 candidatos sintéticos no cambian |
| `tests/legacy_parity/test_identity_contract.py`, `test_planner.py` | Dominios, nombres e ids reales sustituidos por valores `.example`; valores esperados (clave de operación, HC1) recalculados con las mismas funciones | Las 180 pruebas de paridad pasan |
| `core/test_project_opportunity.py` | Emails de prueba con dominio `.example` | Mismo comportamiento probado |
| `tests/fixtures/historical_manifest.json` | Rutas del histórico privado renombradas con los identificadores neutrales; un nombre de fichero que identificaba a una empresa, neutralizado | Recuento y fechas. Los tests que comparan rutas se omiten sin el histórico |

## 5. Redactado o adaptado

- `phase1_audit/OI_PHASE1_AUDIT_AND_DESIGN.md`: empresas evaluadas, una persona, dominios de terceros y nombres de casos sustituidos por etiquetas neutras (aviso al inicio del documento). Un test exige que el documento exista.
- `docs/OI_BLOCK_F1_F10_REPORT.md`: menciones a empresas concretas sustituidas.
- `oi/engine/evaluators/identity.py` y `rules/planner/capability_matrix.v1.json`: ejemplos que citaban organizaciones reales, generalizados (solo comentarios y textos descriptivos; ninguna regla cambia).
- `cases/reseller_pilot/{case.json, presentation.json, adapter.py}`: el nombre del revisor humano se sustituye por su rol, y la descripción del producto por una categoría genérica.
- `tests/generalization/test_generalization.py`: el test del banner por origen de datos declaraba históricos los registros del primer piloto. Ahora comprueba la misma propiedad del renderer con una copia marcada explícitamente como histórica, y verifica que los registros públicos muestran el banner de datos sintéticos.
- `tests/run_all.py`: las suites no publicadas aparecen como «NO PUBLICADA» con su motivo. No se cuentan como ejecutadas ni superadas, y no se ocultan.
- `oi/paths.py` y 5 ficheros que lo usan: la raíz de los datos históricos se resuelve con `HISTORY` (`$OI_HISTORY_ROOT` > `history/` > ruta original).

## 6. Comprobaciones

1. **Escáner de privacidad** sobre cada blob alcanzable del historial que se publicaría, las **rutas** de los ficheros y los mensajes de commit. Detecta emails (salvo `.example`), teléfonos, URLs de LinkedIn, claves de respuesta de proveedor, términos identificativos (incluidos los nombres reales de los casos y sus abreviaturas) y un diccionario privado de entidades reales construido desde los datos privados: 1.855 nombres, 1.795 dominios y 2.597 identificadores. El diccionario no se publica.
2. **Búsqueda de falsos negativos:** nombres cortos del diccionario con distinción de mayúsculas, secuencias de nombres propios revisadas a mano, descriptores comerciales de los casos (producto, zona, especialidad) y variantes con y sin tilde.
3. **Comprobación exhaustiva** de los registros sintéticos: ningún string original de 5 o más caracteres sobrevive, salvo etiquetas genéricas revisadas una a una (p. ej. «Teléfono central: NO DISPONIBLE»).
4. **Ficheros no analizables como texto:** la edición pública no contiene binarios (ni PDF, ni imágenes, ni hojas de cálculo, ni zips).
5. **Tests** (sección 7) en un clon limpio.

Resultado: ningún email, teléfono, URL de LinkedIn, dominio de prospecto, identificador de organización real ni nombre de las empresas de
los casos. Coincidencias residuales revisadas y aceptadas:

| Coincidencia | Por qué se acepta |
|---|---|
| Nombres de campos del proveedor en el código (`estimated_num_employees`, `raw_address`…) | Esquema, no datos |
| Palabras comunes que coinciden con nombres del diccionario («expansión», «Madrid», «gmail» como nombre de conector) | Falsos positivos |
| Hashes hexadecimales de 24 caracteres (claves de petición, nombres de fichero del manifest) | Son hashes de peticiones; ninguno coincide con un identificador de organización de los datos privados |
| `gob.es` y `conversoriaecnae.es` | Dominio genérico de la administración y herramienta pública de conversión CNAE citada como referencia del mapeo sectorial |
| Una empresa cotizada usada como exclusión hipotética en un test del planner | No procede de datos de pilotos ni de proveedor |
| Saldos de créditos de la cuenta del proveedor en `oi/pipeline/f14prep.py` | Metodología de control de gasto; no son datos personales ni de terceros |

## 7. Resultados de tests (edición pública)

| Ejecución | Resultado |
|---|---|
| `tests/run_all.py` | **371 ejecutados, 0 fallos, 0 errores, 15 omitidos**. 6 suites «NO PUBLICADA» (F12–F15.1): **no se ejecutan** en esta edición |
| `core/test_project_opportunity.py` | **25/25** |
| Demo `oi run … --offline` | `COMPLETED_OFFLINE`, 18 registros sintéticos, 0 llamadas externas |

Los 15 omitidos necesitan el histórico privado y lo declaran: integridad histórica (13), paridad viva con el motor D010 original (1) e
invariancia del histórico al ejecutar el pipeline (1). Las 6 suites no publicadas (230 tests) solo se ejecutan en la copia privada.

## 8. Limitaciones

- Los resultados de los pilotos no son reproducibles en esta edición (datos privados). El adaptador del primer piloto sigue publicado, pero sin el histórico no puede ejecutarse.
- `--replay-real` falla en esta edición: no hay datos de replay.
- Un escáner basado en diccionario no garantiza la ausencia de toda referencia. Por eso el método es una lista de permitidos más revisión manual, y no una lista de bloqueo.
