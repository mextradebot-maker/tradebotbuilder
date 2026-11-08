# Vela de Medianoche — conectar al catálogo en vivo y al motor emisor de bots

**Fecha:** 2026-11-07
**Rama de trabajo:** `worktree-feature+swing-express` (continúa el trabajo de Swing Express)

## 1. Intención y por qué

"Vela de Medianoche" es la tercera estrategia del Master Trader, pero hoy vive aislada: su EA
(`robots/MexTradeBot_OroMedianoche.mq5`, nunca subido a git) corre de forma independiente en la
cuenta demo de Ricardo desde el 1 de noviembre — sin licencia, sin pasar por `despachador.py`, sin
aparecer en "Mis robots". La pantalla "Catálogo de Estrategias" ya tiene una tarjeta para ella, pero
hoy solo muestra un aviso de "aún no conectada".

Ricardo pidió conectarla de verdad:
1. Que el Catálogo de Estrategias muestre, para cualquier activo del catálogo, si **hoy** cumple la
   condición de entrada (viable para operar).
2. Que un alumno pueda **descargar un robot licenciado** de Vela de Medianoche desde la ficha de
   descarga, igual que Estructural y Swing Express.

## 2. La regla (confirmada con Ricardo, sin cambios)

Gráfico H1, sin indicadores:
1. A las 00:00 hora de Barcelona (con su cambio de horario), se toma el máximo del día anterior (PDH).
2. Si el día anterior tuvo menos de 12 velas H1 (feriado, símbolo recién agregado, etc.), ese día no
   se opera.
3. Si el precio ya está en el PDH o arriba a las 00:00, ese día no se opera.
4. Se coloca un **BUY STOP** exactamente en el PDH. La orden vive 9 horas (hasta las 09:00 Barcelona);
   si no se llena, se cancela.
5. Si entra, la posición se cierra 7 horas después de la entrada.
6. Sin SL/TP normales — solo un **stop de emergencia** ancho (2% bajo la entrada), un seguro contra
   huecos, no una gestión de riesgo real.

Esta regla **no se cambia** en este trabajo. El EA ya existente (`MexTradeBot_OroMedianoche.mq5`)
la implementa correctamente y corre en producción desde hace una semana — es la base de este trabajo,
no algo que se reescribe desde cero.

## 3. Decisiones de Ricardo

- **Alcance de activos: los 34 del catálogo**, no solo XAUUSD. Aunque el backtest del 1 de noviembre
  mostró que la regla solo tiene ventaja histórica probada en oro (PF 1.23; el resto PF<1), Ricardo
  decidió mostrar el candidato en cualquier activo que rompa su PDH — es un disparador mecánico, no
  una apuesta respaldada por expectativa. **Por eso el catálogo de Vela de Medianoche nunca muestra
  "Expectativa" ni "Winrate"** — a diferencia de Estructural, aquí no aplica esa cifra y no se debe
  inventar ni tomar prestada de otra estrategia (mismo principio que ya aplicamos en Swing Express).
- **Arquitectura: integrarla al Master Trader** — licencia, límites de descarga, aparece en "Mis
  robots", respeta el freno de riesgo de cuenta (igual que Estructural y Swing Express), en vez de
  entregar el EA independiente tal cual.
- **Cuentas reales desde el día uno** — no se restringe a DEMO. Ricardo acepta el riesgo de que, con
  solo un stop de emergencia al 2% y ~1 semana de historial en demo, esta estrategia se ofrezca igual
  que Estructural y Swing Express desde el principio.
- **Límite de descargas: comparte cupo con Estructural y Swing Express** — y esto no requiere código
  nuevo: `membresias.robots_descargados(correo, modo)` ya cuenta por `(símbolo, temporalidad)`, sin
  campo de estrategia (confirmado en `persistencia/membresias.py`), así que las 3 estrategias ya
  comparten el mismo cupo de forma natural. Lo único que hay que cuidar es que la clave de
  `temporalidad` de Vela de Medianoche (ver §5.1) no choque por accidente con la que ya usa
  Estructural para el mismo símbolo.

## 4. Arquitectura (revisada): reusar el mismo camino que ya usan Estructural y Swing Express

La primera versión de este documento proponía que el EA siguiera calculando y colocando su orden por
su cuenta (modelo "híbrido", para no perder precisión de disparo). Al revisar `master/despachador.py`
a fondo encontré que esa preocupación no aplica: `ColocarOrdenPendiente()` en el EA ya coloca una
**orden pendiente nativa de MT5** (no una orden a mercado) — se llena en el mismo tick que el precio
cruza el nivel, sea cual sea el tipo (LIMIT o STOP), con cero intervención del servidor en el momento
del llenado. El servidor solo participa para decidir y entregar el nivel exacto, lo mismo que ya hace
con Estructural. Como el PDH se fija una sola vez a medianoche y no cambia en todo el día, no hay
ninguna urgencia real en que el servidor se lo entregue al robot en el primer segundo exacto — con que
llegue dentro de la ventana de 9 horas (y el ciclo de pulso del robot es de segundos) sobra.

**Por eso Vela de Medianoche reusa el mismo mecanismo que Estructural y Swing Express, de punta a
punta, en vez de un camino propio:**

- El servidor calcula el PDH candidato por símbolo (§5.1) y, cuando es viable, crea una orden
  pendiente igual que `recibir_tendencia_swing_express` — `recibir_candidato_vela_medianoche(repo,
  simbolo, pdh, dia_anterior, ahora)` — con `direccion="long"`, `entrada=pdh`, `stop=pdh*0.98`,
  `estrategia="vela_medianoche"`, y una clave de idempotencia `f"vela_medianoche|{dia_anterior}"`
  (una sola orden por símbolo por día, aunque refresco corra varias veces).
- `pulso()` la entrega igual que cualquier otra orden — **sin tocar la función**: como SÍ trae un stop
  real (a diferencia de Swing Express), `_lotes()` ya calcula el lotaje por riesgo real (2% del
  capital hasta el stop) sin ningún caso especial, igual que Estructural.
- El EA, al recibir una orden con `estrategia=="vela_medianoche"`, usa `ORDER_TYPE_BUY_STOP` en vez de
  `BUY_LIMIT` (una línea en el `if` que ya elige el tipo de orden) y, en vez del vencimiento basado en
  velas que usa Estructural (`InpVelasExpiracion`), calcula su propio vencimiento a las 9 horas desde
  medianoche de Barcelona — mismo cálculo de huso horario que ya tiene el EA independiente de hoy
  (`OffsetBarcelona`/`UltimoDomingo`), portado como una función más dentro de `MexTradeBot_SeguidorSMC.mq5`.
  El resto de `ColocarOrdenPendiente()` no cambia.
- El cierre a las 7 horas de la entrada se resuelve **extendiendo el mecanismo que ya existe para el
  freno de cuenta** (`cierre_auto` en `pulso()`, que ya ordena un cierre cuando corresponde sin que el
  EA tenga que pedirlo): se agrega una columna `posicion_abierta_en` que se marca la primera vez que
  `pulso()` ve pasar `posiciones` de 0 a 1 en un robot `vela_medianoche` (mismo punto donde ya se
  reconcilia `posicion_abierta` para Swing Express), y `pulso()` pide `ordenar_cierre` cuando pasan 7
  horas desde esa marca — ya existe el camino para "el servidor decide cerrar sin que el robot lo
  pida", solo se agrega una segunda condición.
- El stop de emergencia (2%) **no necesita código nuevo**: es el `stop` de la orden, MT5 lo ejecuta
  solo, igual que cualquier SL de Estructural.
- `/api/catalogo` usa la MISMA función (`motor_smc.vela_medianoche.candidatos()`) para mostrar el
  candidato en el Catálogo de Estrategias — no hay dos cálculos del PDH que puedan desalinearse: es
  literalmente la misma llamada que decide si se crea la orden.

Este cambio de enfoque es mío, decidido al escribir el plan — más simple que la versión híbrida,
reusa código ya probado en producción (Estructural/Swing Express) en vez de construir un camino
paralelo, y hace que "candidato visible" y "orden real" sean el mismo cálculo, no dos que puedan
desalinearse.

## 5. Qué construye cada pieza

### 5.0 Decisiones de detalle (mías, resueltas tras el builder-check)

- **Clave de `temporalidad`:** literal `"Diario"` — distinta de `"Intraday D"` (D1 de Estructural),
  a propósito: son conceptos distintos (Estructural usa D1 como una temporalidad de señal más entre
  5; Vela de Medianoche usa "un día calendario de Barcelona" como su propio ciclo fijo). Esto también
  evita que `robots_descargados` confunda una descarga de Estructural-D1 con una de Vela de
  Medianoche en el mismo símbolo al compartir cupo (§3).
- **Autorización diaria:** ya no es un mecanismo aparte (ver §4) — el robot de Vela de Medianoche
  corre igual que SeguidorSMC en modo Master: `MasterPulso()` cada pocos segundos, mismo
  `MTB_LICENSE_TOKEN`. La licencia y el freno de riesgo se verifican donde ya se verifican para
  Estructural y Swing Express (`compuerta.evaluar()` dentro de `pulso()`); si la cuenta está en
  freno, la orden del día simplemente no se entrega — mismo comportamiento que ya tienen las otras 2
  estrategias, sin lógica nueva. Si el servidor no responde, el EA ya tiene su propio manejo de
  reintento/standby para `MasterPulso` (ver `EntrarStandby`/`MasterAviso`) — tampoco hace falta nada
  nuevo ahí.
- **Ciclo de refresco del candidato de catálogo (corregido a pedido de Ricardo):** usa el MISMO ciclo
  y la MISMA información de `refresco.py` que las otras estrategias — no un recálculo especial una
  vez al día. `pdh_candidato()`/`candidatos()` (§5.1) se enganchan en `_avisar_master` igual que
  `recibir_tendencia_swing_express`, así que se recalculan en cada refresco como todo lo demás.
  Recalcular el PDH en cada ciclo no cuesta nada extra (es un max() sobre las mismas velas H1 que ya
  están en `conectividad.almacen`) y mantiene una sola forma de actualizar el catálogo en todo el
  sitio, en vez de una regla especial para esta estrategia.
- **Caso de prueba DST:** el plan de implementación debe incluir un test explícito para los dos días
  del año en que cambia el horario de la UE (puerto de `OffsetBarcelona`/`UltimoDomingo`), para no
  desalinear "viable" en el catálogo vs. lo que el EA real decide ese día.

### 5.1 Motor de candidatos (nuevo — lo usan tanto el catálogo como el despachador)

`motor_smc/vela_medianoche.py`:
- `pdh_candidato(simbolo: str, ahora: datetime | None = None) -> dict | None`
  - Lee las velas H1 de `conectividad.almacen.leer(simbolo, "1H", ...)` para el día de Barcelona
    "ayer" (mismas reglas de huso horario que el EA: GMT+1 invierno / GMT+2 verano de la UE —
    puerto directo de `OffsetBarcelona`/`UltimoDomingo` del .mq5 a Python).
  - Si el día anterior tuvo < 12 velas H1 → `None` (no viable).
  - Si no → `{"simbolo", "pdh", "dia_anterior", "ventana_hasta"}` (el PDH y hasta qué hora de hoy
    sigue viva la ventana de 9 h).
- `candidatos(ahora=None) -> list[dict]`: corre `pdh_candidato` sobre los 34 símbolos de
  `api.catalogo.SIMBOLO_XM`. **Una sola función, dos consumidores** (§4): `refresco.py` la llama para
  decidir si crea la orden del día; `/api/catalogo` la llama para pintar el catálogo. Nunca pueden
  desalinearse porque es el mismo cálculo.

### 5.2 Catálogo / frontend

- `renderVelaMedianoche(host)` deja de mostrar el aviso fijo y en su lugar pinta una grilla
  `ag-grid`/`ag-asset` igual a la de Swing Express, pero con sus propias columnas: "Nivel a romper
  (PDH)", "Día de referencia", "Ventana válida hasta" — **nunca** "Expectativa" ni "Tu posición"
  (no aplican aquí; no se inventan).
- Fuente de datos: nuevo campo en la respuesta de `/api/catalogo` (p. ej. `vela_medianoche_todos`)
  con la lista que da `candidatos()`, igual de fresca que el resto del catálogo porque sale de la
  misma llamada HTTP.

### 5.3 despachador.py

- `recibir_candidato_vela_medianoche(repo, simbolo, pdh, dia_anterior, ahora)`: mismo patrón que
  `recibir_tendencia_swing_express` — busca robots vivos y activados con `estrategia='vela_medianoche'`
  para ese símbolo (`robots_vela_medianoche(simbolo)`, nuevo, mismo patrón que `robots_swing_express`)
  y, si no tienen ya una orden/posición de hoy (clave `f"vela_medianoche|{dia_anterior}"`), crea la
  orden con `crear_orden({..., "direccion":"long", "entrada":pdh, "stop":pdh*0.98, "estrategia":
  "vela_medianoche", ...})`. `pulso()` la entrega y la dimensiona sin ningún caso especial (§4).
- `pulso()` (se modifica, no se reescribe): cuando `robot.get("estrategia")=="vela_medianoche"` y
  `posicion_abierta` pasa a verdadero (mismo punto donde ya se reconcilia Swing Express), se marca
  `posicion_abierta_en=ahora` en el robot; la condición de `cierre_auto` se extiende para también
  pedir `ordenar_cierre` cuando `ahora - posicion_abierta_en >= 7h` para un robot `vela_medianoche`
  — reusa el camino que ya existe para el freno de cuenta, con una segunda razón para activarse.
- Ack de cierre: mismo mecanismo que ya existe (`aplicar_ack`/`_filas_historial`) — no cambia.
- `persistencia/migraciones.py`: nueva columna `master_robots.posicion_abierta_en` (timestamp,
  nullable).

### 5.4 EA (dentro de `MexTradeBot_SeguidorSMC.mq5` — mismo archivo, no uno aparte)

- En `MasterPulso()`, junto al `if(es_swing_express)`, se agrega `bool es_vela_medianoche =
  (estrategia == "vela_medianoche")`.
- Si es vela_medianoche: `tipo = ORDER_TYPE_BUY_STOP` (en vez de `BUY_LIMIT`/`SELL_LIMIT`), y el
  vencimiento de la orden pendiente ya no usa `InpVelasExpiracion` sino una función nueva
  `VencimientoVelaMedianoche()` — puerto de `OffsetBarcelona`/`UltimoDomingo`/el cálculo de "9 horas
  desde medianoche" del `MexTradeBot_OroMedianoche.mq5` existente, ahora como funciones dentro de
  `SeguidorSMC.mq5`. El resto de `ColocarOrdenPendiente()` no cambia.
- `MexTradeBot_OroMedianoche.mq5` (el EA independiente de hoy, fuera de git) **no se sube ni se usa**
  — su lógica de PDH/horario se porta a Python (§5.1) y a las dos funciones nuevas del EA de arriba;
  el archivo en sí queda obsoleto, Ricardo puede quitarlo de la cuenta demo cuando el robot licenciado
  esté probado.

### 5.5 Ficha de descarga / licencias

- `_robot_ficha` (api/alumnos.py) acepta `modo` igual que hoy, pero para `estrategia=vela_medianoche`
  el activo se elige libremente entre los 34 (sin selector de temporalidad — se guarda como
  `"Diario"`, ver §5.0).
- El compilador **no necesita ningún cambio** (§7): sigue siendo el mismo `MexTradeBot_SeguidorSMC.mq5`
  de siempre, con el mismo token embebido de siempre.

## 6. No-metas

- No se valida ni se mejora la regla de entrada/salida — se usa tal cual, sin SL/TP normal, sin
  breakeven (Ricardo ya decidió eso el 31 de octubre).
- No se construye expectativa/winrate para esta estrategia — explícitamente no aplica.
- No se toca el flujo de Estructural ni Swing Express.
- No se sube `MexTradeBot_OroMedianoche.mq5` a git ni se mantiene como archivo aparte — su lógica
  queda portada dentro de `SeguidorSMC.mq5` y de `motor_smc/vela_medianoche.py` (§5.4).

## 7. Compilador del VPS — ya no aplica

Con la arquitectura revisada (§4), Vela de Medianoche es una estrategia más dentro de
`MexTradeBot_SeguidorSMC.mq5` — el mismo archivo que el compilador ya sabe compilar con el token del
alumno embebido, sin ningún archivo nuevo. No hay nada que coordinar en el VPS: `compilador.py`
(`FUENTE = .../MexTradeBot_SeguidorSMC.mq5`) no cambia. El plan de implementación igual termina con
una prueba real de descarga en el laboratorio, por el mismo rigor que todo lo demás en este proyecto,
pero no por una duda de arquitectura — por confirmar que el .ex5 resultante realmente coloca el
BUY STOP como se espera.
