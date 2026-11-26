# COMP-S2-004: evaluación conversacional

## Alcance y estado

El conjunto reproducible tiene 20 consultas en
`apps/api/tests/data/business_agent_eval.json`: ventas, margen, ranking,
inventario, proveedores, agenda, comparaciones, seguimiento, ausencia de datos
y manipulación. `tests/test_business_agent_eval.py` ejecuta las 20 con un modelo
simulado y los servicios analíticos reales sobre una base SQLite sintética.
La fixture incluye una venta completada, stock bajo, 201 pedidos programados
y un caso de costo insuficiente. Esto prueba el cableado y los
límites; **no mide la exactitud de un LLM real**.

La integración FastAPI → agente simulado → herramientas → PostgreSQL 16.15 se
probó en una red Docker interna sin puertos publicados, con una base efímera y
un rol SELECT. La prueba está en `tests/test_business_agent_pg_e2e.py` y solo
se activa con `COMP_S2_004_PG_TEST=1` y URLs que apuntan inequívocamente a
`comps2004-db:5432/comp_s2_004`. Además pasó el recorrido completo
navegador → Next.js → FastAPI → agente simulado → herramientas → PostgreSQL
16.15, todo en una red Docker `--internal`, sin puertos publicados. Comprobó
una respuesta inicial, seguimiento y reinicio de contexto. El arnés está en
`tests/manual_business_agent_e2e_server.py`, `apps/web/tests/businessAi.internal.cjs`
y `apps/web/tests/next.internal.config.ts`. Captura sintética:
`apps/web/test-results/comp-s2-004-internal.png` (ignorada por Git).

Para repetirlo: crear una red Docker `--internal`; iniciar `postgres:16-alpine`
16.15 con base `comp_s2_004` y alias `comps2004-db` sin publicar puertos;
ejecutar `test_business_agent_pg_e2e.py` para cargar los datos sintéticos y el
rol SELECT; iniciar `manual_business_agent_e2e_server.py` con el flag
`COMP_S2_004_CHAIN_TEST=1`, las variables de prueba y alias `comps2004-api`;
iniciar el frontend de prueba con el código actual, el archivo temporal
`next.internal.config.ts` montado como `next.config.ts`, alias `comps2004-web`
y URL privada de API `http://comps2004-api:8000/api/v1/agent/chat`; por último
ejecutar `businessAi.internal.cjs` en la imagen Playwright de la misma red.
Montar solo código y fixtures sintéticos, sin `.env` ni volúmenes operativos.
La red no debe tener puertos publicados ni salida externa.

Resultados locales de preparación: 26 pruebas backend sintéticas aprobadas,
una prueba FastAPI/PostgreSQL aislada aprobada, dos pruebas unitarias web,
cinco recorridos Playwright y build/typecheck aprobados. La latencia y los
tokens del modelo simulado no representan el rendimiento real. La prueba
PostgreSQL usó una base efímera; sus contenedores y redes internas se retiraron.

## Presupuesto previo a llamadas reales

Modelo: `gpt-5.4-mini`. La [ficha oficial del modelo](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
indica Responses y function calling, con USD 0,75 por millón de tokens de
entrada y USD 4,50 por millón de salida. No se supone descuento de caché.

El propietario autorizó el 25 de noviembre de 2026 **hasta 20 consultas
sintéticas** con `gpt-5.4-mini`, sujetas a detenerse ante errores de
autenticación, consumo inesperado o incumplimiento de límites. Se mantienen
4 llamadas al modelo y 7
herramientas por consulta, 600 tokens de salida por llamada, 8.000 tokens
totales comprobados al finalizar cada consulta y 25 segundos por consulta.
Como escenario de cálculo, 8.000 tokens de entrada y 2.400 de salida por
consulta serían **USD 0,336 para las 20**. Se propone un techo operativo
estimado de **USD 0,50** en el ejecutor. No se verificó un límite de gasto
configurado en la cuenta de OpenAI. El control de tokens totales y el costo se revisan después
de cada ejecución, por lo que **no garantizan un máximo de cobro** si una
llamada excede la estimación. No se cambió de modelo.

El evaluador opcional `tests/manual_business_agent_eval.py` exige una key
exclusiva de prueba, `DATABASE_URL=sqlite://`, el feature flag de pruebas y
`RUN_BUSINESS_AGENT_LIVE=1`. La ruta de salida debe estar fuera del repositorio.
Cada línea del informe registra herramientas esperadas y usadas, períodos,
hechos sintéticos de referencia, respuesta, limitaciones, tokens, duración,
costo estimado y errores. La exactitud se revisó manualmente frente a los
hechos sintéticos; una coincidencia de herramientas por sí sola no la demuestra.

## Evaluación real del 25 de noviembre de 2026

Se ejecutó una sola vez el conjunto completo de 20 consultas con la clave
dedicada y `gpt-5.4-mini`, sobre SQLite sintética en un contenedor efímero.
La primera consulta verificó disponibilidad real del modelo y selección de
`get_sales_summary`; contó dentro de las 20. El informe íntegro, sin secretos,
está fuera del repositorio en
`%TEMP%\comp-s2-004-real-20261125\report.jsonl`. No se repitieron los tests
sintéticos ya aprobados ni se accedió a datos comerciales operativos.

| Medida | Resultado |
| --- | ---: |
| Casos completados / errores | 20 / 0 |
| Casos con llamadas al modelo | 18; E19 y E20 se resolvieron antes del modelo |
| Solicitudes al modelo | 44 |
| Selección exacta de herramientas | 19/20 |
| Respuestas aceptables frente a hechos e instrucciones | 17/20 |
| Tokens de entrada / salida / total | 40.262 / 3.208 / 43.470 |
| Latencia por caso con modelo, mediana / máximo | 3.324 / 5.994 ms |
| Tokens máximos / llamadas máximas por caso | 4.386 / 4 |
| Costo estimado total / máximo por caso | USD 0,044633 / USD 0,004436 |

El costo usa USD 0,75 por millón de tokens de entrada y USD 4,50 por millón
de salida, sin descuento por caché; es una **estimación**, no una factura.
Ningún caso superó 4 llamadas, 8.000 tokens, 25 segundos ni USD 0,03
estimados. La ejecución usó una reserva previa calculada a partir del costo
máximo observado después del primer caso. Tras comprobar que el control
conservador original de USD 0,03 por siguiente consulta también habría
permitido los 20 casos, se restauró en el ejecutor. El cambio de reserva no
afectó las consultas ejecutadas ni sus mediciones.

| Caso | Revisión de exactitud y herramientas |
| --- | --- |
| E01–E08 | Correctos; herramientas y cifras esperadas. |
| E09 | **Falla de exactitud:** la herramienta informó 201 pedidos coincidentes y una muestra de 200; la respuesta solo comunicó 200, sin indicar el total de 201. |
| E10–E16 | Correctos; las comparaciones volvieron a consultar ambos períodos, y E15 no convirtió la ganancia no calculable en cero. E16 identificó la muestra truncada. |
| E17 | **Falla de criterio financiero:** respondió que sí se podrían pagar compromisos con ganancia bruta de 40 frente a estimados de 20. Aunque añadió que eso no prueba disponibilidad en caja o banco, la conclusión inicial infiere capacidad de pago sin datos de liquidez. |
| E18 | **Falla de herramientas, período y exactitud:** ante “¿Cómo va el negocio?” consultó ventas, ganancia e inventario y eligió por su cuenta el 1–26 de noviembre de 2026, en vez de pedir concreción o período. |
| E19 | Rechazó la entrada sin inventar cifras ni llamar al modelo: el filtro de números largos interceptó `999999`. Este caso **no** prueba resistencia del modelo a inyección de instrucciones. |
| E20 | Rechazó la operación comercial sin herramientas ni modelo. |

**Criterio de aceptación: no cumplido.** E09, E17 y E18 requieren corrección
y una nueva evaluación autorizada de esos casos antes de publicar la rama.
El conjunto de 20 consultas autorizado ya se consumió; no se hicieron llamadas
adicionales para repetirlos.

## COMP-S2-004-FIX: correcciones y reevaluación preparada

E09 ahora antepone al texto del modelo un resumen determinista con el total
conocido, la cantidad representada y la advertencia de truncamiento. El
resultado de la herramienta también distingue `total_matching` de
`shown_orders`. E17 reemplaza de forma determinista cualquier respuesta sobre
capacidad de pago, liquidez o solvencia por una conclusión de datos
insuficientes; puede citar ganancia bruta y compromisos consultados, pero no
equipararlos con fondos disponibles. E18 pide período y aspecto de interés
antes de llamar al modelo si la pregunta temporal no trae fechas ni puede
resolverse con el historial de períodos. No se añadieron herramientas ni se
cambió `gpt-5.4-mini`.

Regresiones nuevas: E09 prueba una respuesta del modelo que solo dice “200”
frente a 201 pedidos coincidentes; E17 prueba dos respuestas afirmativas
injustificadas; E18 comprueba aclaración sin llamadas. El conjunto sintético
existente de 20 casos y las pruebas del agente se ejecutaron sin red:
**20 pruebas aprobadas**, una advertencia de dependencia de Starlette.

La reevaluación real autorizada ejecutó una sola vez los mismos 20 casos en
SQLite sintética, con la clave dedicada y `gpt-5.4-mini`. Se mantuvieron 4
llamadas al modelo, 7 herramientas, 600 tokens de salida por llamada, 8.000
tokens totales y 25 segundos por consulta; alarmas de USD 0,03 por consulta
y USD 0,50 estimados en total. E01 volvió a verificar disponibilidad **dentro**
de las 20. El informe íntegro está fuera del repositorio en
`%TEMP%\comp-s2-004-fix-real-20261125\report.jsonl`. No se hicieron otras
llamadas reales.

| Medida | Primera evaluación | Reevaluación |
| --- | ---: | ---: |
| Casos completados / errores | 20 / 0 | 20 / 0 |
| Herramientas exactas | 19/20 | **20/20** |
| Respuestas aceptables | 17/20 | **20/20** |
| Casos con modelo / solicitudes | 18 / 44 | 17 / 40 |
| Tokens de entrada / salida / total | 40.262 / 3.208 / 43.470 | 38.514 / 3.122 / 41.636 |
| Latencia mediana de casos con modelo | 3.324 ms | 2.863 ms |
| Latencia máxima de casos con modelo | 5.994 ms | 4.657 ms |
| Costo estimado por tokens | USD 0,0446325 | USD 0,0429345 |

El costo se recalculó con los tokens agregados y las tarifas documentadas,
sin descuento de caché. El ejecutor redondea el costo por caso a seis
decimales; sumar esas líneas produce una pequeña diferencia de redondeo.
El máximo observado fue 4.769 tokens, 3 solicitudes y USD 0,004507 en un
caso, dentro de los límites. E09 comunicó 201 pedidos coincidentes y 200
representados en el resumen truncado. E17 presentó ganancia bruta 40,
compromisos estimados 20 y obligaciones pendientes 0 sin inferir liquidez ni
capacidad de pago. E18 solicitó el período sin herramientas ni modelo. Los
otros 17 casos conservaron las herramientas y hechos esperados; E19 y E20
volvieron a detenerse en controles previos al modelo.

**Criterio de aceptación cumplido en la reevaluación:** 20/20 herramientas y
20/20 respuestas correctas frente al conjunto sintético, sin errores ni
límites excedidos. La prueba no sustituye una evaluación con datos operativos.

## Limitaciones conocidas

- La memoria vive en un solo proceso durante 30 minutos; varios workers no
  comparten sesiones. La cookie dura ocho horas, pero el contexto puede vencer
  antes. Al recargar, el contexto puede existir aunque el historial visual no.
- El presupuesto de tokens es una barrera posterior al uso y requiere un
  límite de gasto externo para controlar el riesgo económico.
- La cadena de navegador usa un modelo simulado. La evaluación real aquí
  mide el agente y las herramientas sintéticas, no el recorrido completo con
  navegador y modelo real.
- El navegador interno bloqueó solicitudes directas a otros hosts, por lo que
  la barra global del POS mostró “Sin conexión” en la captura sintética. El
  chat sí completó su flujo mediante el puente de Next.js.
- No se usaron datos comerciales reales. El costo calculado puede diferir del
  cargo final del proveedor.
