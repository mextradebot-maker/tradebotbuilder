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

## 4. Decisión de diseño (mía, a confirmar en el builder-check): arquitectura híbrida

El patrón que usan Estructural y Swing Express — el servidor calcula la señal y empuja UNA orden por
pulso, que el EA coloca en el acto — **no encaja bien aquí** por dos razones:

1. **La precisión del disparo importa.** Ricardo fue explícito: "lo más importante... es que la señal
   del momento en que supera el nivel sea lanzada al bot". Un BUY STOP nativo de MT5 se llena en el
   mismo tick que cruza el precio; un servidor que sondea cada pocos segundos (`pulso`) y luego manda
   una orden a mercado siempre llega tarde, y en huecos de apertura puede llenar a un precio peor que
   el PDH exacto.
2. **El ciclo de vida es diario y propio del símbolo**, no un evento único: cada día a medianoche se
   cancela la orden de ayer (haya entrado o no) y se recalcula una nueva. Esto no encaja en el patrón
   "un orden_id, se procesa una vez" que ya usan Estructural/Swing Express.

**Por eso la propuesta es una arquitectura híbrida:**

- **El EA mantiene su propia lógica día a día** (la que ya corre hoy en la cuenta demo, sin cambios de
  comportamiento): calcula su propio PDH con sus propias velas H1, coloca el BUY STOP, lo cancela y
  recalcula cada medianoche Barcelona, cierra por tiempo, aplica el stop de emergencia. Cero
  dependencia del servidor para la precisión del disparo.
- **Lo único que agrega el servidor es una autorización diaria + reporte de resultado:** antes de
  colocar la orden del día, el EA consulta UNA vez a `despachador` (reusando el mismo patrón
  `MasterPulso`/licencia/token que ya tienen los otros robots): "¿mi licencia sigue vigente y mi
  cuenta no está en freno de riesgo?". Si el servidor dice que no, el EA no coloca la orden ese día.
  Cuando la posición se cierra (por tiempo o por el stop de emergencia), el EA reporta el resultado
  al servidor (mismo mecanismo de ack que ya existe) para que quede en `master_historial` y cuente
  contra los límites de descarga de la membresía.
- **El Catálogo de Estrategias calcula su propio candidato, de forma independiente**, puramente para
  mostrarlo en la pantalla — usando las velas H1 que YA están en `conectividad.almacen` (las mismas
  que usa Swing Express), replicando el mismo cálculo de PDH/ventana en Python. Esta copia del cálculo
  es solo informativa (qué le mostramos al alumno para que decida si vale la pena activar el robot);
  **no es la que dispara la orden real** — esa la calcula el EA con sus propios datos, como ya hace
  hoy. Una pequeña discrepancia entre ambos cálculos (p. ej. un retraso de minutos en el candle store
  del catálogo) no pone en riesgo dinero real: en el peor caso, el catálogo muestra un candidato que
  ya no aplica, o se tarda un ciclo de refresco en mostrar uno nuevo.

Este reparto evita reinventar lo que ya funciona (el EA probado en demo) y evita acoplar la precisión
del disparo a la latencia del servidor, a cambio de que "licencia" y "candidato visible" vivan en dos
cálculos independientes del mismo PDH en vez de uno solo compartido. Lo marco explícitamente para que
el builder lo cuestione si ve un problema que no estoy viendo.

## 5. Qué construye cada pieza

### 5.0 Decisiones de detalle (mías, resueltas tras el builder-check)

- **Clave de `temporalidad`:** literal `"Diario"` — distinta de `"Intraday D"` (D1 de Estructural),
  a propósito: son conceptos distintos (Estructural usa D1 como una temporalidad de señal más entre
  5; Vela de Medianoche usa "un día calendario de Barcelona" como su propio ciclo fijo). Esto también
  evita que `robots_descargados` confunda una descarga de Estructural-D1 con una de Vela de
  Medianoche en el mismo símbolo al compartir cupo (§3).
- **Mecánica de la autorización diaria:** el EA sigue con su `EventSetTimer(5)` actual (no cambia a
  sondear todo el tiempo como `SeguidorSMC`); dentro de `Ciclo()`, justo antes de
  `ColocarOrdenDelDia`, hace UNA llamada HTTP al servidor (mismo patrón de headers
  `Authorization: Bearer <token>` que ya usan los otros EAs) pidiendo autorización para hoy.
  Es una llamada puntual de una vez al día, no un pulso continuo — más simple y consistente con que
  esta estrategia ya no necesita que el servidor le entregue una orden (la calcula ella misma).
- **Si el servidor no autoriza vs. si no responde:** son casos distintos y se tratan distinto.
  - **No autorizado** (freno de riesgo activo o licencia inválida, respuesta 200 con `autorizado:false`
    o 401/403): no se coloca la orden ese día, se registra motivo explícito en el log del EA
    (`Print`) y se avisa igual que los otros EAs avisan sus bloqueos.
  - **Sin respuesta** (timeout, error de red, 5xx): se reintenta con backoff corto DENTRO de la
    ventana de 9 h (no se da por vencido en el primer intento, un error transitorio no debe costar
    el día completo); si tras los reintentos sigue sin respuesta, se fallan cerrado — no se coloca la
    orden — y se deja un log distinto ("sin respuesta del servidor", no "rechazada") para que quede
    claro en diagnóstico que no fue un freno de riesgo real.
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

### 5.1 Motor de candidatos (nuevo, para el catálogo — solo lectura)

`motor_smc/vela_medianoche.py`:
- `pdh_candidato(simbolo: str, ahora: datetime | None = None) -> dict | None`
  - Lee las velas H1 de `conectividad.almacen.leer(simbolo, "1H", ...)` para el día de Barcelona
    "ayer" (mismas reglas de huso horario que el EA: GMT+1 invierno / GMT+2 verano de la UE —
    puerto directo de `OffsetBarcelona`/`UltimoDomingo` del .mq5 a Python).
  - Si el día anterior tuvo < 12 velas H1 → `None` (no viable).
  - Si no → `{"simbolo", "pdh", "dia_anterior", "ventana_hasta"}` (el PDH y hasta qué hora de hoy
    sigue viva la ventana de 9 h), sin tocar precio actual en vivo (el catálogo no tiene feed de tick;
    "viable" aquí significa "el día anterior califica y la ventana de hoy no ha cerrado", no "ya se
    activó" — eso el EA lo decide con precio real).
- `candidatos(ahora=None) -> list[dict]`: corre `pdh_candidato` sobre los 34 símbolos de
  `api.catalogo.SIMBOLO_XM`.

### 5.2 Catálogo / frontend

- `renderVelaMedianoche(host)` deja de mostrar el aviso fijo y en su lugar pinta una grilla
  `ag-grid`/`ag-asset` igual a la de Swing Express, pero con sus propias columnas: "Nivel a romper
  (PDH)", "Día de referencia", "Ventana válida hasta" — **nunca** "Expectativa" ni "Tu posición"
  (no aplican aquí; no se inventan).
- Fuente de datos: un campo nuevo en la respuesta de `/api/catalogo` (o un endpoint propio, a decidir
  en el plan) con la lista de `candidatos()`.

### 5.3 despachador.py (autorización diaria + historial)

- `robots_vela_medianoche(simbolo)`: lista de robots VIVOS y activados con `estrategia='vela_medianoche'`
  para ese símbolo (mismo patrón que `robots_swing_express`).
- Nuevo campo en el `estado` que manda el EA en `MasterPulso` (o un pulso específico, a decidir en el
  plan): el EA avisa "voy a abrir mi orden del día" y el servidor responde autorizado/no-autorizado
  corriendo `compuerta.evaluar()` igual que las otras dos estrategias (freno de cuenta: drawdown,
  racha, pérdida diaria) — sin inventar un segundo sistema de riesgo.
- Ack de cierre: mismo mecanismo que ya existe (`aplicar_ack`/`_filas_historial`) para registrar la
  operación en `master_historial` cuando el EA reporta que cerró (por tiempo o por el stop de
  emergencia).

### 5.4 EA

- Se parte de `robots/MexTradeBot_OroMedianoche.mq5` (hoy fuera de git) tal cual — **se sube a git en
  este trabajo** — y se le agrega: lectura de `MTB_LICENSE_TOKEN`/`InpMasterUrl` (mismas constantes que
  `MexTradeBot_SeguidorSMC.mq5`), la llamada de autorización diaria antes de `ColocarOrdenDelDia`, y el
  reporte de cierre vía ack. El resto de su lógica (cálculo de PDH, BuyStop, cierre por tiempo, stop de
  emergencia) **no cambia**.
- Queda como archivo propio (no se mezcla con `MexTradeBot_SeguidorSMC.mq5`): su ciclo de vida
  (orden diaria recalculada, no "una orden por señal") es demasiado distinto para compartir el mismo
  flujo de `MasterPulso` sin complicarlo para las otras dos estrategias.

### 5.5 Ficha de descarga / licencias

- `_robot_ficha` (api/alumnos.py) acepta `modo` igual que hoy, pero para `estrategia=vela_medianoche`
  el activo se elige libremente entre los 34 (sin selector de temporalidad — es "Diario" implícito).
- El compilador (servicio externo en el VPS) necesita compilar `MexTradeBot_OroMedianoche.mq5` con el
  token del alumno embebido — ver §7 (debería funcionar sin tocar el VPS, se confirma con una prueba
  real al final del plan).

## 6. No-metas

- No se valida ni se mejora la regla de entrada/salida — se usa tal cual, sin SL/TP normal, sin
  breakeven (Ricardo ya decidió eso el 31 de octubre).
- No se construye expectativa/winrate para esta estrategia — explícitamente no aplica.
- No se toca el flujo de Estructural ni Swing Express.

## 7. Compilador del VPS — resuelto dentro de este trabajo

No hace falta tocar nada en el VPS. Hoy `MexTradeBot_OroMedianoche.mq5` no tiene NINGÚN mecanismo de
token (el .mq5 actual ni siquiera declara la variable); `MexTradeBot_SeguidorSMC.mq5` sí la tiene,
como un `input string MTB_LICENSE_TOKEN = "";` — el compilador del VPS, para generar un .ex5
personalizado, toma ese .mq5, reemplaza esa declaración por el token del alumno y compila. Es un
reemplazo de texto sobre una convención (el nombre `MTB_LICENSE_TOKEN`), no algo amarrado al nombre
del archivo `SeguidorSMC`.

La solución: en §5.4, al adaptar `MexTradeBot_OroMedianoche.mq5`, se le agrega **exactamente la misma
declaración** `input string MTB_LICENSE_TOKEN = "";` (mismo nombre, mismo formato) que usa
`MexTradeBot_SeguidorSMC.mq5`, además de `InpMasterUrl` con el mismo patrón de headers
`Authorization: Bearer`. Así, el mismo mecanismo de reemplazo que ya usa el compilador para el otro
EA funciona para este sin que el VPS necesite ningún cambio.

Como no tengo acceso al VPS desde este entorno para confirmarlo con certeza total, el plan de
implementación termina con una prueba real: descargar un robot de Vela de Medianoche desde la ficha
en el laboratorio de principio a fin. Si el .ex5 llega bien, queda confirmado y resuelto. Si el
compilador lo rechaza, el error que devuelva será concreto (no una suposición) y con eso sabré
exactamente qué pedirte que ajustes en el VPS — pero la expectativa, dado cómo está hecho hoy el
compilador, es que funcione sin tocar nada ahí.
