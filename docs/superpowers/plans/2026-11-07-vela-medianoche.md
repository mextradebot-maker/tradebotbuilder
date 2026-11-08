# Vela de Medianoche — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar la estrategia "Vela de Medianoche" (rotura del máximo del día anterior, PDH, con
BUY STOP) al catálogo en vivo y al mismo motor de licencias/despachador/EA que ya usan Estructural y
Swing Express, para que cualquier alumno pueda ver sus candidatos y descargar un robot licenciado.

**Architecture:** Una sola función Python (`motor_smc/vela_medianoche.py`) calcula el PDH candidato
por símbolo; la usan tanto `/api/catalogo` (para mostrarlo) como `refresco.py`/`despachador.py` (para
crear la orden pendiente). La orden fluye por el mismo `pulso()`/ack/historial que ya usan las otras
2 estrategias — solo se le añade a `MexTradeBot_SeguidorSMC.mq5` un tercer tipo de orden (`BUY_STOP`
con vencimiento a 9 h de medianoche Barcelona) y a `pulso()` un cierre automático a las 7 h de la
entrada (reusando el camino que ya existe para el freno de cuenta). `MexTradeBot_OroMedianoche.mq5`
(el EA independiente de hoy) no se sube a git ni se usa — su lógica queda portada.

**Tech Stack:** Python 3 (stdlib + pandas, sin frameworks), Postgres vía `psycopg`, MQL5, HTML/JS
plano (`public/index.html`), mismo estilo que el resto del repo (self-test `demo()` por módulo).

**Spec:** `docs/superpowers/specs/2026-11-07-vela-medianoche-design.md`

## Global Constraints

- Regla de entrada/salida exacta (spec §2): a las 00:00 Barcelona, PDH = máximo H1 del día anterior
  (si tuvo ≥ 12 velas); BUY STOP en el PDH, vence a las 09:00 Barcelona si no se llena; si entra,
  cierra 7 h después; stop de emergencia 2% bajo la entrada; sin breakeven, sin TP.
- Candidatos en los 34 símbolos de `api.catalogo.SIMBOLO_XM` (spec §3) — sin filtrar por expectativa.
- Nunca mostrar "Expectativa"/"Winrate" para esta estrategia (spec §3) — no aplica, no se inventa.
- Cuentas reales habilitadas desde el día uno (spec §3) — sin restricción DEMO-only.
- El cupo de descargas se comparte con Estructural/Swing Express sin código nuevo (spec §3) — ya
  funciona así por `(correo, modo, símbolo, temporalidad)`; cuidar que `"Diario"` no choque con
  `"Intraday D"` para el mismo símbolo.
- Misma función (`candidatos()`) para decidir la orden real y para pintar el catálogo (spec §4) —
  nunca dos cálculos del PDH que puedan desalinearse.
- No se sube `MexTradeBot_OroMedianoche.mq5` a git (spec §6) — la lógica va dentro de
  `MexTradeBot_SeguidorSMC.mq5` y de `motor_smc/vela_medianoche.py`.
- Cada módulo nuevo/tocado conserva su `demo()` de autoverificación (estilo del repo, no pytest).

## Review Focus

- **Cambio de horario de la UE (2 días al año):** el puerto de `OffsetBarcelona`/`UltimoDomingo` debe
  dar el mismo "día de Barcelona" que el EA real en esas fechas — un desfase de 1 hora puede mostrar
  "viable" en el catálogo cuando la ventana real ya cerró, o al revés.
- **Símbolo con menos de 12 velas H1 el día anterior** (feriado, símbolo recién agregado, hueco en el
  almacén): debe devolver "no viable" sin reventar, no `None`-aritmética ni excepción.
- **Dos refrescos seguidos el mismo día no deben crear dos órdenes** — la clave de idempotencia
  `f"vela_medianoche|{dia_anterior}"` tiene que impedirlo (mismo patrón que Swing Express, pero con
  clave por *día*, no por instante).
- **Posición abierta por otra vía** (cierre forzado del broker, cierre manual, EA reiniciado) nunca
  debe dejar `posicion_abierta_en` colgado para siempre pidiendo cierres repetidos — debe limpiarse en
  el mismo punto donde ya se reconcilia `posicion_abierta` para Swing Express.
- **Un alumno pide `estrategia=vela_medianoche` con un símbolo fuera del catálogo o con temporalidad
  distinta de `"Diario"`** — debe rechazarse con 400, igual que cualquier preset inválido hoy, no
  compilar nada a medias.

---

## Task 1: Motor de candidatos — `motor_smc/vela_medianoche.py`

**Files:**
- Create: `motor_smc/vela_medianoche.py`
- Test: `demo()` dentro del mismo archivo (estilo del repo)

**Interfaces:**
- Consumes: `conectividad.almacen.leer(simbolo: str, serie: str, inicio, fin) -> pd.DataFrame` (ya
  existe; `serie="1H"`, columnas `open/high/low/close` indexadas por tiempo UTC).
- Produces:
  - `offset_barcelona(instante_gmt: datetime) -> int` — segundos que Barcelona va por delante de GMT
    en ese instante (puerto 1:1 de `OffsetBarcelona`/`UltimoDomingo` del .mq5: horario de verano UE
    del último domingo de marzo al último domingo de octubre).
  - `pdh_candidato(simbolo: str, ahora: datetime | None = None) -> dict | None` — `ahora` en UTC
    tz-aware (default `datetime.now(timezone.utc)`). Calcula el PDH del día de Barcelona "ayer" sobre
    las velas H1 de `conectividad.almacen.leer(simbolo, "1H", ...)`. Si el día anterior tuvo < 12
    velas → `None`. Si no → `{"simbolo": str, "pdh": float, "dia_anterior": date, "ventana_hasta":
    datetime}` (`ventana_hasta` = medianoche Barcelona de hoy + 9h, en UTC).
  - `candidatos(ahora: datetime | None = None) -> list[dict]` — corre `pdh_candidato` sobre
    `api.catalogo.SIMBOLO_XM.keys()`, descarta los `None`, devuelve la lista de dicts no vacíos.

- [ ] **Step 1: Escribir el test de `offset_barcelona` en los 2 días de cambio de horario**

```python
def demo() -> None:
    from datetime import datetime, timezone
    # 2026: UE pasa a verano el 29 mar 01:00 UTC, a invierno el 25 oct 01:00 UTC
    assert offset_barcelona(datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)) == 3600   # aun invierno (+1)
    assert offset_barcelona(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == 7200    # ya verano (+2)
    assert offset_barcelona(datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)) == 7200  # aun verano
    assert offset_barcelona(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == 3600   # ya invierno
```

- [ ] **Step 2: Correr y verificar que falla** (función no existe todavía).

- [ ] **Step 3: Implementar `offset_barcelona`** portando `UltimoDomingo`/`OffsetBarcelona` de
  `robots/MexTradeBot_OroMedianoche.mq5:42-58` a Python puro (sin dependencias nuevas).

- [ ] **Step 4: Correr y verificar que pasa.**

- [ ] **Step 5: Escribir el test de `pdh_candidato`** con un `MemRepo`-style stub de
  `conectividad.almacen.leer` (monkeypatch del módulo, mismo patrón que usan los `demo()` existentes
  que stubean `persistencia`): un DataFrame de velas H1 sintéticas con un día de 24 velas (máximo
  conocido) seguido de un día con solo 5 velas — `pdh_candidato` debe devolver el PDH correcto para el
  primer caso y `None` para el segundo (menos de `InpMinVelasDia=12`).

- [ ] **Step 6: Correr y verificar que falla.**

- [ ] **Step 7: Implementar `pdh_candidato` y `candidatos`.**

- [ ] **Step 8: Correr y verificar que pasa; correr `python -m motor_smc.vela_medianoche` (demo) OK.**

- [ ] **Step 9: Commit**

```bash
git add motor_smc/vela_medianoche.py
git commit -m "feat: motor de candidatos de Vela de Medianoche (PDH, hora Barcelona)"
```

---

## Task 2: Migración — columna `posicion_abierta_en` y temporalidad "Diario"

**Files:**
- Modify: `persistencia/migraciones.py`

**Interfaces:**
- Consumes: patrón `_SQL` existente (lista de strings idempotentes, `ALTER TABLE ... ADD COLUMN IF
  NOT EXISTS`).
- Produces: `master_robots.posicion_abierta_en` (timestamptz, nullable) — lo usa Task 3.
  `_TEMPORALIDADES_SEED` incluye `"Diario"` — lo valida `compilador.py` (Task 7) y lo usa Task 6/8.

- [ ] **Step 1: Agregar a `_SQL`** (antes del bloque "Etapa 2", mismo cuidado que el fix de Task 1 de
  Swing Express — revisar que ningún `demo()` asuma la posición exacta en la lista):
  `ALTER TABLE master_robots ADD COLUMN IF NOT EXISTS posicion_abierta_en timestamptz;`

- [ ] **Step 2: Agregar `"Diario"` a `_TEMPORALIDADES_SEED`** (al final de la lista, después de
  `"Swing (M)"`). Esto crea filas `(símbolo, "Diario")` en la tabla general de catálogo para los 34
  símbolos — inertes para Estructural (nadie las consulta: el selector de temporalidades del Catálogo
  de activos en el frontend sigue con sus 5 valores fijos, sin tocar), necesarias solo para que
  `compilador.validar_presets`/`TF_POR_TEMPORALIDAD` (Task 7) acepten `"Diario"` al compilar.

- [ ] **Step 3: Correr `python -m persistencia.migraciones` (demo) si hay Postgres local; si no,
  verificar manualmente que el nuevo `ALTER TABLE` no rompe el slice `_SQL[-6:]` que usa `demo()`.**

- [ ] **Step 4: Commit**

```bash
git add persistencia/migraciones.py
git commit -m "feat: migracion para Vela de Medianoche (posicion_abierta_en, temporalidad Diario)"
```

---

## Task 3: `despachador.py` — robots, creación de orden diaria y cierre a las 7h

**Files:**
- Modify: `master/despachador.py`

**Interfaces:**
- Consumes: `motor_smc.vela_medianoche.candidatos()` (Task 1); `master_robots.posicion_abierta_en`
  (Task 2); `crear_orden`, `_ahora`, `TTL_ORDEN_S`, `ordenar_cierre` (ya existen).
- Produces:
  - `robots_vela_medianoche(repo, simbolo: str, tf: str) -> list[dict]` (método en `MemRepo`/`RepoPg`,
    mismo patrón que `robots_swing_express`: `{"licencia_id", "cuenta", "posicion_abierta",
    "posicion_abierta_en"}`).
  - `recibir_candidato_vela_medianoche(repo, simbolo: str, pdh: float, dia_anterior: date,
    ahora: datetime | None = None) -> dict` — `{"aperturas": int, "robots": int}`.
  - `pulso()` modificado: mismo nombre/firma, nuevo comportamiento interno (ver pasos).

- [ ] **Step 1: Excluir `vela_medianoche` de `robots_activados`** — en `MemRepo.robots_activados` y
  `RepoPg.robots_activados`, el filtro `estrategia <> 'swing_express'` pasa a
  `estrategia not in ('swing_express', 'vela_medianoche')` (mismo motivo que Swing Express: estos
  robots no deben recibir las órdenes del camino de Estructural).

- [ ] **Step 2: Escribir el test de `robots_vela_medianoche` + `recibir_candidato_vela_medianoche`**
  (contra `MemRepo`, mismo estilo que el test existente de `recibir_tendencia_swing_express` en
  `demo()`): un robot activado con `estrategia="vela_medianoche"`, sin posición ni orden en vuelo →
  `recibir_candidato_vela_medianoche(repo, "XAUUSD", 2650.0, date(2026, 11, 6))` crea una orden con
  `direccion="long"`, `entrada=2650.0`, `stop=2650.0*0.98`, `clave="vela_medianoche|2026-11-06"`;
  llamarlo una segunda vez con el mismo `dia_anterior` no crea una segunda orden (`aperturas=0`).

- [ ] **Step 3: Correr y verificar que falla.**

- [ ] **Step 4: Implementar `robots_vela_medianoche` en `MemRepo` y `RepoPg`** (copiar
  `robots_swing_express`, cambiar el filtro de estrategia y agregar `posicion_abierta_en` a la
  proyección/SELECT).

- [ ] **Step 5: Implementar `recibir_candidato_vela_medianoche`** (copiar la forma de
  `recibir_tendencia_swing_express`, sin la rama de cierre por tendencia — aquí el cierre es por
  tiempo, Step 7).

- [ ] **Step 6: Correr y verificar que pasa.**

- [ ] **Step 7: Escribir el test del cierre a las 7h** — un robot `vela_medianoche` con
  `posicion_abierta=True` y `posicion_abierta_en=ahora - timedelta(hours=7, minutes=1)`: una llamada a
  `pulso(..., cierre_auto=True)` debe devolver `estado="cierre"` con un motivo que mencione el tiempo
  (p. ej. `"Vela de Medianoche: 7 horas desde la entrada"`), igual que ya hace el freno de cuenta.
  Con `posicion_abierta_en=ahora - timedelta(hours=6)` (todavía no llega a 7h) no debe cerrar.

- [ ] **Step 8: Correr y verificar que falla.**

- [ ] **Step 9: Implementar en `pulso()`:**
  - Donde ya se reconcilia `posicion_abierta` para `swing_express` (la condición
    `robot.get("estrategia") == "swing_express" and robot.get("posicion_abierta") and
    est.get("posiciones") == 0`), replicar el mismo patrón para `"vela_medianoche"` — además, cuando
    un robot `vela_medianoche` PASA de `posicion_abierta=False` a detectarse abierto (en el ack de
    apertura, junto a donde ya se marca `r["posicion_abierta"] = True` para swing_express dentro de
    `aplicar_ack`), marcar también `posicion_abierta_en = ahora`.
  - Extender la condición de `cierre_auto` (la que hoy solo mira `c.get("halt")`) para también pedir
    `ordenar_cierre` cuando `robot.get("estrategia") == "vela_medianoche" and
    robot.get("posicion_abierta_en")` y pasaron ≥ 7 horas desde esa marca — motivo de cierre:
    `"Vela de Medianoche: 7 horas desde la entrada"`.
  - `reconciliar_posicion` (ya existe, se llama cuando el EA deja de reportar posiciones) también
    limpia `posicion_abierta_en` a `None` — cubre el "Review Focus" de posición cerrada por otra vía.

- [ ] **Step 10: Correr y verificar que pasa; correr `python -m master.despachador` (demo) completo,
  sin romper los tests existentes de Estructural/Swing Express.**

- [ ] **Step 11: Commit**

```bash
git add master/despachador.py
git commit -m "feat: despachador crea la orden diaria de Vela de Medianoche y cierra a las 7h"
```

---

## Task 4: `aplicar_ack` — historial con `posicion_abierta_en`

**Files:**
- Modify: `master/despachador.py` (`MemRepo.aplicar_ack`, `RepoPg.aplicar_ack`)

**Interfaces:**
- Consumes: `posicion_abierta_en` (Task 2/3).
- Produces: ningún símbolo nuevo — ajusta el ack existente para marcar/limpiar `posicion_abierta_en`
  junto con `posicion_abierta`, y para que el cierre por `master_historial` siga funcionando igual
  (reusa `_filas_historial`, sin cambios ahí).

- [ ] **Step 1: Escribir el test** — ack de apertura (`{"orden_id": ..., "estado": "colocada"}`) para
  una orden `vela_medianoche` marca `posicion_abierta=True` Y `posicion_abierta_en=ahora` en el
  robot; ack de cierre (`{"cierre_id": ..., "estado": "ejecutado", "posiciones": [...]}"`) limpia
  `posicion_abierta=False` y `posicion_abierta_en=None`, y sigue agregando filas a
  `master_historial` igual que hoy.

- [ ] **Step 2: Correr y verificar que falla.**

- [ ] **Step 3: Implementar** — en la rama de apertura (`o["estado"] == "colocada" and
  o.get("estrategia") == "swing_express"`), ampliar a `o.get("estrategia") in ("swing_express",
  "vela_medianoche")` y, solo para `vela_medianoche`, también fijar `posicion_abierta_en`. En la
  rama de cierre ejecutado, limpiar `posicion_abierta_en = None` junto con `posicion_abierta = False`
  (ambos repos).

- [ ] **Step 4: Correr y verificar que pasa.**

- [ ] **Step 5: Commit**

```bash
git add master/despachador.py
git commit -m "feat: aplicar_ack marca/limpia posicion_abierta_en para Vela de Medianoche"
```

---

## Task 5: `refresco.py` + `/api/catalogo` — enganchar `candidatos()` en ambos consumidores

**Files:**
- Modify: `refresco.py`
- Modify: `api/catalogo.py`

**Interfaces:**
- Consumes: `motor_smc.vela_medianoche.candidatos()` (Task 1),
  `master.despachador.recibir_candidato_vela_medianoche` (Task 3).
- Produces: `/api/catalogo` responde con una clave nueva `vela_medianoche_todos: list[dict]` —
  `[{"simbolo", "simbolo_xm", "nombre", "pdh", "dia_anterior", "ventana_hasta"}]` (mismo criterio de
  nombres legibles que `robot_legible`, sin inventar `winrate`/`expectativa_r`).

- [ ] **Step 1: Escribir el test de `/api/catalogo`** (en el `demo()` de `api/catalogo.py` o donde ya
  se prueba `procesar`, stubeando `motor_smc.vela_medianoche.candidatos` para devolver una lista fija)
  — la respuesta incluye `vela_medianoche_todos` con esa lista, en el formato de arriba, y **sin**
  claves `winrate`/`expectativa_r`.

- [ ] **Step 2: Correr y verificar que falla.**

- [ ] **Step 3: Implementar en `api/catalogo.py`** — función `_vela_medianoche_todos() -> list[dict]`
  que llama `motor_smc.vela_medianoche.candidatos()` y arma cada fila con `robot_legible`-style
  nombre/símbolo_xm más `pdh`/`dia_anterior`/`ventana_hasta`; `procesar()` la agrega a la respuesta.
  Si `candidatos()` lanza (almacén sin datos, etc.), capturar y devolver `vela_medianoche_todos: []`
  sin tumbar el resto de `/api/catalogo` (mismo criterio defensivo que ya tiene `procesar`).

- [ ] **Step 4: Correr y verificar que pasa.**

- [ ] **Step 5: Escribir el test de `refresco.py`** — en el ciclo que ya llama `_avisar_master` por
  símbolo/temporalidad, verificar que también se llama
  `recibir_candidato_vela_medianoche(repo, simbolo, pdh, dia_anterior)` por cada entrada que devuelva
  `motor_smc.vela_medianoche.candidatos()`, dentro del mismo `try/except` que ya envuelve la llamada
  de Swing Express (un fallo de Vela de Medianoche no debe tumbar el refresco de los demás símbolos).

- [ ] **Step 6: Correr y verificar que falla.**

- [ ] **Step 7: Implementar en `refresco.py`** — junto a donde ya se llama
  `recibir_tendencia_swing_express` para `temporalidad in ("Swing (S)", "Swing (M)")`, agregar (fuera
  de ese `if`, una sola vez por ciclo de refresco, no por cada temporalidad): recorrer
  `motor_smc.vela_medianoche.candidatos()` y llamar `recibir_candidato_vela_medianoche` por cada uno.

- [ ] **Step 8: Correr y verificar que pasa.**

- [ ] **Step 9: Commit**

```bash
git add refresco.py api/catalogo.py
git commit -m "feat: enganchar candidatos de Vela de Medianoche en refresco y /api/catalogo"
```

---

## Task 6: EA — `MexTradeBot_SeguidorSMC.mq5`: BUY STOP y vencimiento a 9h de medianoche

**Files:**
- Modify: `robots/MexTradeBot_SeguidorSMC.mq5`

**Interfaces:**
- Consumes: `estrategia` ya viene en el JSON de la orden (campo existente, ver `es_swing_express`).
- Produces: ningún símbolo nuevo para otras tareas — es la punta final de la cadena.

- [ ] **Step 1: Portar `OffsetBarcelona`/`UltimoDomingo` del `MexTradeBot_OroMedianoche.mq5` actual**
  (líneas 42-58, ver Task 1 — misma lógica, ahora en MQL5 dentro de `SeguidorSMC.mq5`) como funciones
  `long OffsetBarcelona(datetime gmt)` / `datetime UltimoDomingo(int anio, int mes)`.

- [ ] **Step 2: Implementar `datetime VencimientoVelaMedianoche()`** — hora actual de Barcelona
  (`TimeGMT()` + `OffsetBarcelona`), calcula la medianoche de HOY en Barcelona + 9 horas, convertida
  de vuelta a hora de servidor (mismo patrón de conversión que ya usa `CalcularAjuste`/`AHoraBarcelona`
  del EA original) para pasarla a `request.expiration`.

- [ ] **Step 3: En `MasterPulso()`, agregar `bool es_vela_medianoche = (estrategia ==
  "vela_medianoche")`** junto a `es_swing_express` (línea ~462).

- [ ] **Step 4: Rama de colocación** — donde hoy se calcula `tipo = (direccion == "long") ?
  ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT` (línea ~506), si `es_vela_medianoche` usar
  `ORDER_TYPE_BUY_STOP` (la dirección siempre es `"long"` para esta estrategia — spec §3, "solo
  compra"); y donde `ColocarOrdenPendiente` fija `request.expiration =
  TimeCurrent() + InpVelasExpiracion * PeriodSeconds(InpTF)`, para `vela_medianoche` usar
  `VencimientoVelaMedianoche()` en su lugar (agregar un parámetro opcional `datetime vencimiento = 0`
  a `ColocarOrdenPendiente`; `0` conserva el cálculo actual para las otras 2 estrategias).

- [ ] **Step 5: `sl_orden`** — a diferencia de Swing (S)/(M) (`sl_orden = 0.0`), `vela_medianoche` SÍ
  manda su stop real (`stop` del JSON, el 2% de emergencia) — confirmar que la línea `double sl_orden
  = EsSwingSM() ? 0.0 : stop;` ya cubre esto sin tocarla (EsSwingSM() es `false` para
  `vela_medianoche`, así que `sl_orden = stop` ya sale correcto).

- [ ] **Step 6: Comentario de cabecera** — actualizar el bloque de comentarios del EA (donde hoy
  documenta `es_swing_express`) para explicar la tercera estrategia, igual de breve.

- [ ] **Step 7: Revisión manual del diff** (sin compilador MetaEditor en este entorno — igual que
  Swing Express, queda pendiente compilar/probar en el VPS antes de cualquier uso real). Verificar a
  mano que `ColocarOrdenPendiente` sigue compilando sintácticamente (conteo de llaves/paréntesis) y
  que ninguna otra llamada existente a `ColocarOrdenPendiente` quedó sin el nuevo parámetro opcional.

- [ ] **Step 8: Commit**

```bash
git add robots/MexTradeBot_SeguidorSMC.mq5
git commit -m "feat: EA coloca BUY STOP con vencimiento a 9h de medianoche Barcelona para Vela de Medianoche"
```

---

## Task 7: Compilador y licencias — `compilador.py` + `api/alumnos.py`

**Files:**
- Modify: `compilador.py`
- Modify: `api/alumnos.py` (`_robot_ficha`)
- Modify: `api/master.py` (si el docstring/validación de `estrategia` necesita incluir
  `"vela_medianoche"` explícitamente en alguna lista cerrada — revisar línea 11)

**Interfaces:**
- Consumes: `"Diario"` en `_TEMPORALIDADES_SEED` (Task 2); `api.catalogo.SIMBOLO_XM` (ya existe).
- Produces: `compilador.TF_POR_TEMPORALIDAD` incluye `"Diario": "PERIOD_H1"`;
  `_robot_ficha` acepta `estrategia="vela_medianoche"` con cualquier símbolo del catálogo y
  `temporalidad` fija a `"Diario"` (ignora lo que mande el cliente para ese campo).

- [ ] **Step 1: Escribir el test de `compilador.py`** — `TF_POR_TEMPORALIDAD["Diario"] ==
  "PERIOD_H1"`; `validar_presets("XAUUSD", "Diario")` no lanza; el `demo()` existente que compara
  `list(TF_POR_TEMPORALIDAD) == semillas["_TEMPORALIDADES_SEED"]` debe seguir pasando sin tocarlo
  (prueba que Task 2 y este paso quedaron sincronizados).

- [ ] **Step 2: Correr y verificar que falla.**

- [ ] **Step 3: Implementar** — agregar `"Diario": "PERIOD_H1"` a `TF_POR_TEMPORALIDAD` en
  `compilador.py`.

- [ ] **Step 4: Correr y verificar que pasa (incluyendo el `demo()` completo de `compilador.py`).**

- [ ] **Step 5: Escribir el test de `_robot_ficha` para `vela_medianoche`** (en el `demo()` de
  `api/alumnos.py`, mismo estilo que el resto): `{"simbolo": "EURJPY", "modo": "demo",
  "estrategia": "vela_medianoche"}` sin `temporalidad` → 200, con `temporalidad` guardada/usada como
  `"Diario"` internamente (verificar en los `pedidos`/presets que le llegan al compilador falso);
  `{"simbolo": "EURJPY", "modo": "demo", "estrategia": "vela_medianoche", "temporalidad": "Swing
  (S)"}` (el cliente manda algo distinto) → se ignora y se usa `"Diario"` de todos modos, o 400 si se
  decide ser estricto (elegir ignorar, documentarlo en un comentario — es el camino más simple y no
  hay ambigüedad real: para esta estrategia la temporalidad nunca la elige el alumno).

- [ ] **Step 6: Correr y verificar que falla.**

- [ ] **Step 7: Implementar en `_robot_ficha`** (api/alumnos.py) — nuevo parámetro `estrategia` en
  `datos` (default `"estructural"`, ya es como `api/master.py` documenta el campo); si
  `estrategia == "vela_medianoche"`, forzar `temporalidad = "Diario"` antes de llamar
  `validar_presets`, y propagar `estrategia` al `token`/registro de licencia donde haga falta (mismo
  campo `estrategia` que ya usa `activar()` en `api/master.py:11`).

- [ ] **Step 8: Correr y verificar que pasa.**

- [ ] **Step 9: Commit**

```bash
git add compilador.py api/alumnos.py
git commit -m "feat: ficha de descarga y compilador aceptan estrategia vela_medianoche"
```

---

## Task 8: Frontend — `renderVelaMedianoche` con candidatos reales

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: `vela_medianoche_todos` del payload de `/api/catalogo` (Task 5) — se expone como
  `window.MTB_FILAS_VELA_MEDIANOCHE` (mismo patrón que `window.MTB_FILAS_CATALOGO`, en el script
  inline que ya arma ese global).
- Produces: ningún símbolo nuevo para otras tareas — es la punta visual.

- [ ] **Step 1: Exponer `window.MTB_FILAS_VELA_MEDIANOCHE`** en el bloque de script inline donde hoy
  se arma `window.MTB_FILAS_CATALOGO` a partir de la respuesta de `/api/catalogo`.

- [ ] **Step 2: Reemplazar `renderVelaMedianoche(host)`** (hoy solo el aviso de "aún no conectada") —
  mismo patrón visual que `renderSwingExpress`: botón volver, encabezado, grilla `ag-grid`/`ag-asset`
  con `window.MTB_FILAS_VELA_MEDIANOCHE`, cada tarjeta mostrando símbolo, nombre, "Nivel a romper
  (PDH)", "Día de referencia", "Ventana válida hasta" (formateada con la misma función `timestamp`
  que ya existe, o una nueva si el formato de fecha no calza) — **nunca** "Expectativa" ni "Tu
  posición" (spec §3). Lista vacía → mismo patrón de `<p class="ag-empty">` que Swing Express, texto
  acorde ("Ningún activo rompe su máximo del día anterior ahora mismo.").
  La tarjeta del catálogo de estrategias (`data-ag="abrir-vela-medianoche"`) ya existe — actualizar
  solo el contador `${n} candidato${...}` para usar `window.MTB_FILAS_VELA_MEDIANOCHE.length` en vez
  del aviso fijo.

- [ ] **Step 3: Revisión manual en el laboratorio** (no hay test automatizado de frontend en este
  repo): desplegar, forzar `window.MTB_FILAS_VELA_MEDIANOCHE` con datos de prueba vía
  `javascript_tool` y confirmar que la grilla se ve bien en tema claro y oscuro (reusa los estilos ya
  corregidos para Swing Express, sin CSS nuevo).

- [ ] **Step 4: Commit**

```bash
git add public/index.html
git commit -m "feat: Catalogo de Estrategias muestra candidatos reales de Vela de Medianoche"
```

---

## Task 9: Revisión de rama completa + prueba real de descarga

- [ ] **Step 1: Revisión de la rama completa** — mismo proceso que el resto del proyecto (fresh
  reviewer en el modelo más capaz, diff contra la base de esta rama), enfocado en los 5 puntos de
  "Review Focus" arriba.

- [ ] **Step 2: Corregir lo que encuentre la revisión** (una sola ronda de corrección, sin segunda
  puerta — mismo criterio que Swing Express).

- [ ] **Step 3: Desplegar en el laboratorio** (`mtb-api-preview`, mismo webhook que el resto de este
  trabajo).

- [ ] **Step 4: Prueba real de descarga** — desde la ficha en el laboratorio, descargar un robot
  `vela_medianoche` para un símbolo cualquiera y confirmar que llega un `.ex5` válido (spec §7). Si
  falla, reportar el error exacto del compilador — no es una duda de arquitectura a estas alturas,
  así que un fallo aquí sería un bug de este plan, no del VPS.

- [ ] **Step 5: Confirmar en el Catálogo de Estrategias** que "Vela de Medianoche" muestra candidatos
  reales (o la lista vacía honesta si ningún símbolo califica en ese momento) y que Estructural/Swing
  Express siguen funcionando igual que antes de esta rama.
