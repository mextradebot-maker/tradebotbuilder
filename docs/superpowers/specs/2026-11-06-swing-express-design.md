# Swing Express — diseño (6 nov 2026)

Estado: diseño, nada construido. Aislado de las estrategias existentes (Estructural, Vela de Medianoche): no se toca su lógica, su lotaje, su SL/TP ni su nomenclatura.

## 1. Intención y por qué

Las estrategias actuales (Estructural y Vela de Medianoche) solo generan una oportunidad cuando el motor SMC confirma un setup (CHOCH de reversión, o BOS de continuación — este último prácticamente no dispara en Swing semanal/mensual por la rigidez geométrica del motor). Un alumno que conecta su bot a un activo que **ya** está en tendencia alcista en Swing (como el ORO hoy) se queda esperando sin entrar, porque el setup solo aparece en el cambio de tendencia — se pierde toda la tendencia en curso.

Swing Express resuelve esto con una señal mucho más simple: entra si la tendencia de Swing es alcista (sin esperar un setup geométrico) y sale cuando esa tendencia deja de ser alcista (cambio real de estructura, para asegurar ganancia). Es una tercera estrategia del catálogo, no un reemplazo.

## 2. Goals

- Nueva estrategia "Swing Express", seleccionable en el catálogo de estrategias del Panel de Alumnos junto a Estructural y Vela de Medianoche.
- Entrada: tendencia alcista en la temporalidad Swing (Semanal o Mensual) elegida por el alumno — nada más.
- Salida: cambio real de estructura (CHOCH/BOS que marca bajista), no por una o dos velas rojas.
- Lotaje fijo 0.01, no editable, independiente de la inversión del alumno.
- Sin stop loss.
- Máximo 2 posiciones simultáneas por activo bajo Swing Express: una en Semanal y otra en Mensual (nunca dos en la misma temporalidad).
- Nomenclatura de robot: `[SIGLAS_ALUMNO][ACTIVO][ESTRATEGIA][TEMPORALIDAD]` (ej. `RPCGOLDSWESEM`, `RPCGOLDSWEMEN`).
- Catálogo de estrategias dentro del Panel de Alumnos: reseña de la estrategia + lista en vivo de los activos candidatos (los que cumplen la condición de entrada en ese momento), actualizándose con cada refresco — igual para las 3 estrategias.
- Tabla de historial/estadísticas por robot de Master Trader (hoy no existe para `master_robots`; `historial_posiciones` es del laboratorio de 5 cuentas demo, no de los robots de alumnos).

## 3. Non-goals / anti-goals

- **No rediseñar ni reducir el Panel de Alumnos existente.** La implementación solo AGREGA el ítem "Catálogo de Estrategias" al sidebar real y los badges "en vivo" a las tarjetas que ya existen (Resumen, Catálogo de Activos, ficha de activo). Todo lo demás del sitio (Bots en Producción, Mis robots, Conexión XM, Calendario Económico, Constructor de Bots, Glosario, y cualquier otro ítem del sidebar real) se queda exactamente como está — el mockup de §4.8 solo maquetó las secciones relevantes a este cambio, no es una propuesta de rediseño del sidebar completo.

- No modificar `motor_smc/setups_v2.py` (reversión/continuación) ni su rigor de R1-R6.
- No tocar Estructural ni Vela de Medianoche: ni su dirección permitida, ni su lotaje, ni su SL/TP.
- No es un fallback ni sustituto de las otras estrategias en ningún activo/temporalidad.
- No inventar un detector de tendencia nuevo: reusar `motor_smc/tendencia.py`.
- No construir un nuevo mecanismo de cierre en el EA/despachador — ya existe (ver §4.3) y se reusa tal cual. **Única excepción, decidida por Ricardo (6 nov):** el ack de cierre sí se amplía (§4.3.1) para saber exactamente qué hizo cada bot — no es un mecanismo nuevo, es el mismo `MasterAckCierre` reportando más datos.

## 4. Las partes del cómo ya decididas

### 4.1 Señal de entrada y salida — reusa `motor_smc/tendencia.py`

`obtener_tendencia(ohlc, swing_length)` ya calcula `"compra"` / `"venta"` / `"sin_definir"` a partir del último BOS/CHOCH de la estructura — sin pasar por las reglas R1-R6 ni por FVG. Esa es la señal:

- **Entra** cuando `direccion == "compra"` en la temporalidad Swing del robot y el robot no tiene posición abierta.
- **Sale** cuando, con posición abierta, `direccion` cambia a `"venta"` (nuevo BOS/CHOCH bajista) — eso es por definición un cambio de estructura real, no ruido de 1-2 velas, porque `tendencia_de_estructura()` solo mira el último evento BOS/CHOCH, igual de estricto que lo que ya usan los setups para marcar estructura.

El filtro "swing solo si es alcista" para Swing Express vive donde ya vive esa regla para las demás Swing: en la capa que decide qué se ofrece al alumno (hoy T-04/orquestación), no hardcodeado en `tendencia.py`.

### 4.2 Dónde corre la lógica

Nuevo módulo, p. ej. `motor_smc/swing_express.py`, con una función que, dado el `ohlc` de un activo en Semanal o Mensual, regresa la dirección de tendencia (reusando `obtener_tendencia`) y si implica abrir o cerrar para un robot Swing Express dado su estado actual (abierto/cerrado). Vive junto a `tendencia.py` y `setups_v2.py`, pero no los llama ni es llamado por ellos — generación de oportunidades independiente, igual que hoy `recibir_oportunidades()` consume `confirmados` de `setups_v2`.

El refresco (`refresco.py` / `_avisar_master`) añade una rama Swing Express: en cada ciclo de refresco Swing (S/M), además de pasar los setups confirmados al despachador, calcula la tendencia Swing Express y, si corresponde abrir o cerrar, se lo entrega al despachador por el mismo camino.

### 4.3 Apertura y cierre en el despachador — reusa lo que ya existe

Verificado en el código actual (no hace falta construirlo):

- **Apertura:** igual que hoy — el despachador crea una orden (`crear_orden`) que el robot recoge en su pulso. El robot ya reporta el `ticket` de vuelta en el ack (`g_ultimo_ticket` en `MexTradeBot_SeguidorSMC.mq5`).
- **Cierre:** `despachador.ordenar_cierre(repo, licencia_id, simbolo, temporalidad, motivo, ahora)` ya crea un cierre pendiente (tabla `master_cierres`); el robot lo recoge en su próximo pulso y ejecuta `CerrarTodoMaster()` (cierra todas sus posiciones por `InpMagicNumber`), confirmando `"ejecutado"`/`"fallido"` por `cierre_id`. Esto ya está en producción (lo usa hoy el cierre automático por freno de cuenta). Swing Express solo necesita llamar a esta misma función cuando la tendencia pasa de compra a venta.

#### 4.3.1 Ampliación del ack de cierre (decidida 6 nov 2026)

El ack de cierre hoy es `{"cierre_id", "estado": "ejecutado"|"fallido"}` — no dice qué cerró ni en qué resultó. Ricardo pidió ampliarlo para saber exactamente qué hizo cada bot que sale de aquí (no solo Swing Express — aplica a todo cierre ordenado por el Master, incluido el automático por freno de cuenta). `CerrarTodoMaster()` en el EA ya itera las posiciones por `InpMagicNumber` antes de cerrarlas; ahí mismo puede capturar, por posición, `ticket`, `POSITION_PROFIT` (+ swap/comisión, igual que `DealNeto()` ya calcula para otro fin en el mismo archivo) y el precio de cierre, y mandarlos en el ack. El ack de cierre pasa de un objeto a una lista de posiciones cerradas:
```json
{"cierre_id": "...", "estado": "ejecutado", "posiciones": [
  {"ticket": 123456, "precio_cierre": 2634.10, "profit_usd": 18.40}
]}
```
`aplicar_ack` (despachador) recibe esa lista y, por cada una, inserta una fila en la tabla de historial del §4.7 — ahí sí quedan `precio_cierre` y `profit_usd` reales, no nulos. Si `CerrarTodoMaster()` cierra más de una posición del mismo magic number (no debería pasar bajo el límite de 2 por activo/temporalidad de Swing Express, pero sí podría en otra estrategia), la lista simplemente trae varias entradas.

Como cada combinación alumno+activo+estrategia+temporalidad es una `licencia_id` distinta (ver nomenclatura, §4.6), cerrar el robot Swing Express de un activo nunca toca la posición de Estructural en ese mismo activo/cuenta — ya vienen aisladas por magic number/licencia.

### 4.4 Lotaje fijo y sin compuerta de riesgo por SL

El flujo de `pulso()` hoy calcula lotes con `_lotes(est, orden, ...)` a partir del riesgo % y la distancia al stop, y pasa por `compuerta.evaluar()` (que razona sobre SL/RR). Swing Express no tiene SL, así que esas dos piezas no aplican. Decisión de diseño: las órdenes de Swing Express se marcan con un campo (p. ej. `clase="swing_express"` en `master_ordenes`) que el despachador usa para:
- saltar `compuerta.evaluar()` (no hay SL que evaluar — igual que ya se exceptúa Swing S/M del SL obligatorio en la compuerta actual),
- fijar `lotes = 0.01` directo, sin llamar a `_lotes()`.

El resto del pulso (reentrega idempotente por `orden_id`, expiración de 5 min, etc.) se comporta igual que para las demás estrategias.

### 4.5 Límite de 2 posiciones por activo

Antes de crear una orden de apertura Swing Express para un activo, el despachador comprueba que no haya ya una posición abierta Swing Express de ese activo en la **misma** temporalidad (Semanal o Mensual) para esa licencia — Semanal y Mensual del mismo activo pueden convivir, nunca dos en la misma temporalidad. Esto es una comprobación nueva y pequeña sobre `master_ordenes`/`master_robots`, no un mecanismo nuevo de concurrencia.

### 4.6 Nomenclatura del robot/licencia

`[SIGLAS_ALUMNO][ACTIVO][ESTRATEGIA][TEMPORALIDAD]`, ej.:
- `RPCGOLDESTR` (Estructural, sin temporalidad porque Estructural no la necesita en el nombre — a confirmar con el constructor si Estructural/Vela de Medianoche ya siguen otra convención; no se renombran, solo se documenta el patrón para Swing Express)
- `RPCGOLDSWESEM` (Swing Express, Semanal)
- `RPCGOLDSWEMEN` (Swing Express, Mensual)

Este identificador es el que distingue las licencias/robots de un mismo alumno entre estrategias y temporalidades, y es la base para la tabla de historial del §4.7.

### 4.7 Tabla de historial por robot (nueva)

No existe hoy para Master Trader. Nueva tabla, aditiva vía `persistencia/migraciones.py`, keyed por `licencia_id` (no por `login` como `historial_posiciones`, que es del laboratorio). Columnas mínimas: `licencia_id`, `simbolo`, `temporalidad`, `estrategia`, `direccion`, `lotes`, `ticket`, `precio_entrada`, `precio_cierre`, `profit_usd`, `razon_cierre`, `abierta_en`, `cerrada_en`. Se llena cuando el despachador recibe el ack de `"ejecutado"` de un cierre — con el ack ampliado de §4.3.1, `aplicar_ack` ya trae `precio_cierre` y `profit_usd` reales por cada posición cerrada, no solo la confirmación de que se ejecutó.

### 4.8 Catálogo de estrategias en el Panel de Alumnos

Vive dentro del Panel de Alumnos ya existente (nueva sección/pantalla, no un sitio aparte). Por estrategia: reseña fija (texto descriptivo) + lista de activos candidatos en vivo. "Candidato" para Swing Express = activos con `direccion == "compra"` en Semanal o Mensual en el último refresco. La lista se refresca con el mismo ciclo de refresco que ya alimenta el catálogo de activos — no es un polling nuevo, es una vista nueva sobre datos que ya se actualizan.

**Dirección visual (mockup HTML, fiel a producción):** `diseno/catalogo-estrategias/design-demos/sitio-fiel-produccion.html` — construido copiando literal las clases/variables/markup reales de `index.html` (sección "ag-" del panel, ya reconciliado con producción el 6 nov, commit `0333dab`), no un tema inventado. Agrega UN ítem nuevo al sidebar real ("Catálogo de estrategias", dentro del grupo ESTRATEGIAS, junto a Backtester/Análisis técnico/Mis validaciones/Constructor de bots) sin tocar ni quitar nada más del sidebar. Reusa el template real de tarjeta de activo (`.ag-asset`/`.ag-symbol`/`.ag-trend`/`.ag-metrics`/`.ag-foot`, el mismo que ya arma "Catálogo de activos") para los candidatos de Swing Express, solo reetiquetando las 3 columnas de métricas (Expectativa / Lote / Tu posición en vez de Acierto histórico / Expectativa / Muestra). *(Dos mockups anteriores en la misma carpeta — `catalogo-estrategias.html` con tema oscuro del perfil de marca CLAUDE.md §10, y `sitio-completo.html` con el tema claro de una plantilla obsoleta — quedaron descartados al descubrir que ninguno de los dos coincidía con lo que corre realmente en `mextradebot.com.mx`; se conservan en la carpeta solo como historial del proceso, no como referencia válida.)*

## 5. Qué queda para quien construya

- Esquema exacto de la columna/campo que distingue `clase="swing_express"` en `master_ordenes` (o tabla separada) — cualquiera de las dos es válida, el que construya decide con el menor diff.
- Firma exacta de la nueva función en `motor_smc/swing_express.py` y cómo se integra al ciclo de `refresco.py` (seguir el patrón de `_avisar_master` para los setups confirmados).
- Migración SQL exacta de la tabla de historial (§4.7) y si conviene un índice por `licencia_id, cerrada_en` (paralelo al que ya tiene `historial_posiciones`).
- Diseño de la pantalla del catálogo en el Panel de Alumnos (componentes, ruta) — mismo patrón visual que el catálogo de activos existente.
- Confirmar si Estructural y Vela de Medianoche ya tienen un identificador parecido o si hay que migrar licencias existentes — **no migrarlas sin que Ricardo lo pida**; el patrón de nomenclatura del §4.6 es nuevo y solo aplica a licencias Swing Express desde ahora.
- Backtest en Python de Swing Express contra los 34 activos (regla del proyecto: toda estrategia nueva se prueba en Python sobre velas XM antes de tocar MT5/producción) — pendiente antes de activar cualquier robot real.

## 6. Constraints (de proyecto, ya existentes)

- Plan free de n8n: sin variables de entorno, valores literales.
- Nombres de nodo ASCII sin acentos (si esto llega a tocar n8n).
- `PCT_CIERRE_FORZADO = 0.03` sigue siendo la única red de seguridad real para Swing sin SL (ya documentado en `conectividad/riesgo.py`) — Swing Express hereda esa protección del EA igual que las Swing actuales, no se le agrega nada nuevo.
- Nada a producción sin OK explícito de Ricardo; probar primero en el laboratorio (`mtb-api-preview` cuando esté libre, o `mtb-api` si no).
