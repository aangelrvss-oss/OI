# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]: edición pública inicial (2026-10-09)

### Añadido
- Motor de Opportunity Intelligence (`oi/`), contratos, reglas versionadas, configuración, CLI y renderer ES/EN.
- `core/project_opportunity.py` v0.1 (cualificación de oportunidades por proyecto) con 25 tests.
- Suites del producto publicables: contracts, spec_v2, safety, generalization, historical, rules, e2e y legacy_parity.
- Datos sintéticos: dataset offline de construcción y 30 registros sintéticos del primer piloto, que conservan estados, decisiones agregadas y acciones.
- `docs/SANITIZATION_REPORT.md`: qué se retiró, qué se sustituyó y cómo se comprobó.

### Cambiado respecto a la copia privada
- Casos con identificadores neutrales: `reseller_pilot` («Piloto de reventa»), `construction_software` y piloto por proyectos (campo `offering_capability`); carpetas, ids, títulos, configuración, documentación y tests actualizados.
- Registros del piloto de reventa y golden de D010: identidades, textos y valores de proveedor sustituidos por valores sintéticos. Hashes esperados recalculados (D010 con el motor OFFICIAL original).
- Tests de paridad del motor migrado: dominios, nombres e ids reales sustituidos por valores `.example`; valores esperados recalculados con las mismas funciones.
- `tests/run_all.py` informa como «NO PUBLICADA» las suites que requieren datos privados (F12–F15.1).
- Rutas a los datos históricos resueltas con `oi.paths.HISTORY` (`$OI_HISTORY_ROOT` > `history/` > ruta original).
- Documentos de diseño redactados donde citaban empresas evaluadas, personas o dominios de terceros.

### Conocido
- `--replay-real` necesita datos de un piloto que no se publican.
- Los tests de integridad histórica y la paridad viva con el motor D010 original se omiten sin el histórico privado (motivo explícito).
