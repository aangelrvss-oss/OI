# Experimento del piloto de reventa (cerrado)

`RESELLER_PILOT_EXPERIMENT = COMPLETE` · cerrado el 2026-10-08 · **no se reabre ni se vuelve a ejecutar.**

> **Edición pública.** Los datos del experimento (candidatos, respuestas de proveedores, evaluaciones humanas, ledgers y contactos) no se
> publican. Aquí solo hay resultados agregados y la metodología. Los 30 registros de `cases/reseller_pilot/records/` son **sintéticos**:
> conservan estados, grupos, decisiones agregadas y acciones, y sustituyen empresas, personas, dominios, emails, teléfonos y textos.

## Objetivo

Comprobar si Opportunity Intelligence, partiendo de una intención comercial, propone empresas que **una persona real** encuentra
comercialmente interesantes, con coste controlado. Caso: empresas españolas como posibles clientes o canales (mayoristas,
distribuidores, cadenas) para un productor alimentario (perfil `SELL_PRODUCT_TO_RESELLER`, `opportunity_type = customer_prospect`).

## Metodología

| Paso | Qué se hizo |
|---|---|
| Discovery gratuito | Matriz de consultas por segmento (mayorista / distribuidor / cadena) y zona (Madrid > Castilla y León > resto): **339 candidatos** |
| Revisión humana previa | **88** candidatos revisados por una persona |
| Gate 1 → T1, Gate 2 → T2 | Puertas de aprobación antes de cada gasto; enrichment de pago: **T1 = 6 créditos, T2 = 4 créditos** |
| T3-PREVIEW (coste cero) | Muestra de **30** empresas en 4 grupos (G1 con enrichment, G2 revisadas con encaje claro, G3 fronterizas, G4 propuestas del sistema sin revisión) evaluada por una persona: SÍ / DUDOSA / NO con motivo |
| T3 de pago | **No autorizado** |

Presupuesto: límite 12 créditos, **10 gastados**, 2 sin usar (margen del límite, no saldo de la cuenta).

## Resultados (descriptivos)

**12 SÍ · 3 DUDOSA · 15 NO** sobre 30 empresas, con un único revisor.

| Grupo | SÍ | DUDOSA | NO |
|---|---:|---:|---:|
| G1 (con enrichment, 5) | 4 | 0 | 1 |
| G2 (revisadas, encaje claro, 12) | 6 | 3 | 3 |
| G3 (fronterizas, 4) | 0 | 0 | 4 |
| G4 (del sistema, sin revisión, 9) | 2 | 0 | 7 |

- G1+G2: 10/17 = **58,8 %** de SÍ frente a G4: 2/9 = **22,2 %** (diferencia descriptiva, muestra pequeña y no aleatoria, **sin causalidad**).
- Las 12 empresas con SÍ son **oportunidades candidatas según la evaluación humana**, no clientes cualificados ni encaje verificado (`FIT_VERIFIED`).

Hallazgos registrados (ninguno implementado): el segmento amplio no basta (`SEGMENT_FILTER_INSUFFICIENT`); los candidatos sin revisar
rinden menos; los fronterizos actúan como señal negativa; parte de los falsos positivos se vería con filtros baratos de categoría
(hipótesis); el tamaño no fue el criterio principal; la revisión humana previa mejora la muestra (descriptivo).

## Qué validó y qué no

| Validado | No validado |
|---|---|
| Descubrir y mantener un pool de candidatos | Que el sistema encuentre automáticamente clientes de alta calidad |
| Aplicar puertas y evidencia con aprobación antes del gasto | Que sustituya la revisión humana |
| Previews de coste cero y reutilización de candidatos | Que el enrichment de pago aporte suficiente valor incremental |
| Obtener feedback humano estructurado | Reuniones, willingness-to-pay, conversión |
| Detectar problemas de segmentación invisibles en discovery | Validez con otras Search Spec / precisión global |

## Limitaciones

Un revisor; 30 empresas con selección no aleatoria; algunos motivos usan conocimiento externo al sistema; el feedback mide percepción
comercial, no encaje verificado; no hubo contacto ni conversión.

## Capas posteriores

Una capa de Contact Enrichment v0.1 (investigación web gratuita: 0 créditos, 0 operaciones de pago) añadió vías de contacto con fuente,
URL y estado de verificación, sin modificar el experimento. Se preparó también un informe comercial para el cliente. Ninguno de los dos se
publica, porque contienen datos de contacto de personas reales.
