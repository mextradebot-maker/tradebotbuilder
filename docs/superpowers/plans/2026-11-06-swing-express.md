# Swing Express Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Swing Express" as a third Master Trader strategy — enters on bare Swing-trend (no setup), fixed 0.01 lot, no SL, exits on trend reversal — without touching Estructural or Vela de Medianoche, and surface it in the Panel de Alumnos' new "Catálogo de estrategias" screen.

**Architecture:** Reuses the existing despachador push model (`refresco.py` → `master/despachador.py` → EA pulso) end to end. No new trend detector and no new EA close mechanism are needed — both already exist (see Deviations from spec below). The only new pieces are: two DB columns that tag a robot/order as `swing_express`, one new despachador function that opens/closes on tendencia instead of on a confirmed setup, a widened close-ack so the despachador learns what actually closed, a new history table fed by that ack, and one new screen that reads data the catálogo endpoint already returns.

**Tech Stack:** Python 3.12, psycopg3, MQL5 (MetaTrader 5 EA), vanilla JS/HTML (no framework) for the panel.

**Spec:** `docs/superpowers/specs/2026-11-06-swing-express-design.md` — this plan implements it with two deliberate refinements found while planning (both explained below, both safer/simpler than the spec's placeholder guesses, both left open by the spec's own §5 "queda para quien construya"):

1. **No `motor_smc/swing_express.py`.** `api/setups.py::procesar()` (line ~242-247) already calls `obtener_tendencia(ohlc, swing_length=swing_length)` for *every* temporalidad it's asked about, Swing (S)/(M) included, and already puts the result in the response body as `tendencia_actual` / `tendencia`. `refresco.py::_avisar_master(simbolo, temporalidad, body)` (line 178) already receives that body for every refresh. Swing Express reads `body["tendencia_actual"]` directly — the spec's §4.2 guessed a new module before this was checked against the running code; it isn't needed.
2. **Swing Express orders still pass through `master/riesgo.py::evaluar()`** (the halt/drawdown/streak/daily-loss gate), just with `riesgo_pct=0.0` so the open-risk-sum and R:R checks can't block it (R:R is already off project-wide, `min_rr=0`). The spec's §4.4 said to skip `compuerta.evaluar()` entirely "porque no hay SL que evaluar" — true for the SL-shaped checks, but `evaluar()` also enforces the account-wide drawdown pause, loss-streak pause, and daily-loss cap, which have nothing to do with SL and should not have a Swing-Express-shaped hole in them. Flagged for Ricardo in the handoff message below.

## Global Constraints

- Lote fijo 0.01, no editable (spec §2).
- Sin stop loss (spec §2).
- Entrada: `tendencia["direccion"] == "compra"` en Semanal o Mensual, nada más (spec §2, §4.1).
- Salida: `tendencia["direccion"]` pasa a `"venta"` (spec §2, §4.1).
- Máx. 2 posiciones por activo bajo Swing Express: una por temporalidad (S y M pueden convivir, nunca dos en la misma) — spec §2, §4.5. Se cumple gratis: cada temporalidad es una licencia distinta (spec §4.6), así que "no reabrir si ya hay una posición/orden en vuelo para esta licencia" ya implementa el límite.
- Nomenclatura de licencia Swing Express: `[SIGLAS_ALUMNO][ACTIVO][SWE][SEM|MEN]` (spec §4.6) — no se renombra ninguna licencia existente de Estructural/Vela de Medianoche.
- No tocar `motor_smc/setups_v2.py`, ni la dirección/lotaje/SL-TP de Estructural o Vela de Medianoche (spec §3).
- No rediseñar ni reducir el Panel de Alumnos existente — solo se agrega el ítem "Catálogo de estrategias" al sidebar real (spec §3).
- Nada a producción sin OK explícito de Ricardo; probar primero en el laboratorio (spec §6).
- Backtest en Python contra los 34 activos sobre velas XM antes de tocar MT5/producción (spec §5, §6).
- No usar atribución en commits ni PRs (instrucción permanente de la sesión).

## Review Focus

- **Un alumno activa Swing Express y Estructural en el mismo activo/temporalidad.** Sin el filtro de `estrategia` en `robots_activados`, el robot Swing Express recibiría también las órdenes de Estructural (bug real encontrado durante el builder-check del 6 nov). Task 2 lo cubre con un test que activa ambas estrategias y confirma que cada una solo recibe lo suyo.
- **La tendencia pasa a "venta" sin que haya posición abierta** (por ejemplo, el alumno activó el robot tarde, después de que ya se hubiera revertido). `ordenar_cierre` debe ser inofensivo (no-op) en este caso — ya lo es hoy (`CerrarTodoMaster` cierra "lo suyo", cero posiciones si no hay nada), pero Task 3 lo prueba explícitamente para Swing Express.
- **La tendencia regresa a "compra" después de un ciclo apertura→cierre completo** (re-entrada). Si la `clave` de la orden no cambia entre ciclos, `ON CONFLICT (licencia_id, clave) DO NOTHING` bloquearía la reapertura para siempre. Task 3 prueba dos ciclos completos.
- **Cuenta en freno (halt por drawdown/racha/pérdida diaria) con Swing Express activo.** Sin este chequeo, un alumno podría seguir abriendo Swing Express mientras el resto de sus robots están frenados. Task 4 prueba que `evaluar()` sigue bloqueando a Swing Express bajo halt.
- **`CerrarTodoMaster()` cierra más de una posición en el mismo pulso** (no debería pasar bajo el límite de 2/activo de Swing Express, pero si otra estrategia reusa este mismo ack en el futuro, sí). El ack amplía a una lista precisamente por esto — Task 6 prueba un cierre con 2 posiciones en la lista.

---

## File Structure

| Archivo | Qué hace aquí |
|---|---|
| `persistencia/migraciones.py` | Modificar — 2 `ALTER TABLE` (columnas nuevas) + 1 `CREATE TABLE` (historial), append a `_SQL`. |
| `master/despachador.py` | Modificar — `robots_activados` filtra `swing_express`; nuevo `robots_swing_express`; nueva `recibir_tendencia_swing_express`; `pulso` rama swing_express; `aplicar_ack` acepta lista `posiciones`; `MemRepo`/`RepoPg` reflejan todo lo anterior; `demo()` ampliado. |
| `refresco.py` | Modificar — `_avisar_master` también dispara `recibir_tendencia_swing_express` cuando la temporalidad es Swing (S)/(M). |
| `robots/MexTradeBot_SeguidorSMC.mq5` | Modificar — `CerrarTodoMaster`/`MasterAckCierre` reportan ticket/precio_cierre/profit_usd por posición cerrada. |
| `backtesting/swing_express_bt.py` | Crear — backtest en Python de la lógica de entrada/salida contra los 34 activos sobre velas XM. |
| `index.html` | Modificar — ítem "Catálogo de estrategias" en el sidebar (grupo ESTRATEGIAS) + su página, siguiendo `diseno/catalogo-estrategias/design-demos/sitio-fiel-produccion.html`. |

---

### Task 1: Migraciones — columnas nuevas y tabla de historial

**Files:**
- Modify: `persistencia/migraciones.py` (`_SQL`, lista — agregar al final, antes de `_SIMBOLOS_SEED`)

**Interfaces:**
- Produces: columna `master_robots.estrategia text NOT NULL DEFAULT 'estructural'`; columna `master_robots.posicion_abierta boolean NOT NULL DEFAULT false`; columna `master_ordenes.estrategia text NOT NULL DEFAULT 'estructural'`; tabla `master_historial(id, licencia_id, simbolo, temporalidad, estrategia, direccion, lotes, ticket, precio_entrada, precio_cierre, profit_usd, razon_cierre, abierta_en, cerrada_en)`.

- [ ] **Step 1: Agregar las 3 sentencias SQL a `_SQL`**

```sql
ALTER TABLE master_robots ADD COLUMN IF NOT EXISTS estrategia text NOT NULL DEFAULT 'estructural',
    ADD COLUMN IF NOT EXISTS posicion_abierta boolean NOT NULL DEFAULT false
```
```sql
ALTER TABLE master_ordenes ADD COLUMN IF NOT EXISTS estrategia text NOT NULL DEFAULT 'estructural'
```
```sql
CREATE TABLE IF NOT EXISTS master_historial (
    id             bigserial   PRIMARY KEY,
    licencia_id    bigint      NOT NULL REFERENCES licencias(id) ON DELETE CASCADE,
    simbolo        text        NOT NULL,
    temporalidad   text        NOT NULL,
    estrategia     text        NOT NULL,
    direccion      text        NOT NULL,
    lotes          double precision NOT NULL,
    ticket         bigint,
    precio_entrada double precision,
    precio_cierre  double precision,
    profit_usd     double precision,
    razon_cierre   text,
    abierta_en     timestamptz,
    cerrada_en     timestamptz NOT NULL DEFAULT now()
)
```
```sql
CREATE INDEX IF NOT EXISTS idx_master_historial_licencia ON master_historial (licencia_id, cerrada_en DESC)
```
(4 strings, cada uno su propio elemento de la lista `_SQL` — sigue el patrón ya usado por `master_autorizaciones`/`master_cierres` arriba en el mismo archivo.)

- [ ] **Step 2: Verificar que las migraciones corren limpias**

Run: `python -c "import persistencia"` con `DATABASE_URL` apuntando al laboratorio (o el runner de migraciones que ya use el proyecto al arrancar `app.py`)
Expected: sin excepciones; `\d master_robots`, `\d master_ordenes`, `\d master_historial` en psql muestran las columnas/tabla nuevas.

- [ ] **Step 3: Commit**

```bash
git add persistencia/migraciones.py
git commit -m "migra: estrategia/posicion_abierta en master_robots y master_ordenes, tabla master_historial"
```

---

### Task 2: `robots_activados` deja fuera a Swing Express + nuevo `robots_swing_express`

**Files:**
- Modify: `master/despachador.py:151-199` (`MemRepo`), `master/despachador.py:264-340` (`RepoPg`)

**Interfaces:**
- Consumes: columnas del Task 1.
- Produces: `robots_activados(self, simbolo, tf) -> list[{"licencia_id", "cuenta"}]` (excluye `estrategia='swing_express'`, mismo shape que hoy — ningún llamador existente cambia). `robots_swing_express(self, simbolo, tf) -> list[{"licencia_id", "cuenta", "posicion_abierta"}]` (solo `estrategia='swing_express'`, activos, no revocados).

- [ ] **Step 1: Escribir el test en `demo()` de `despachador.py`**

```python
# en el bloque de demo() que ya construye un MemRepo con robots activados:
repoX = MemRepo(activados=[(1, "EURUSD", "Scalping 15m", 111)])
repoX.robots[(1, "EURUSD", "Scalping 15m")]["estrategia"] = "estructural"
repoX.robots[(2, "EURUSD", "Scalping 15m")] = {"licencia_id": 2, "cuenta": 222, "activo": True, "estrategia": "swing_express"}
assert [r["licencia_id"] for r in repoX.robots_activados("EURUSD", "Scalping 15m")] == [1], "swing_express no debe salir aqui"
assert [r["licencia_id"] for r in repoX.robots_swing_express("EURUSD", "Scalping 15m")] == [2]
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `python master/despachador.py`
Expected: `AttributeError: 'MemRepo' object has no attribute 'robots_swing_express'` (o el robot 2 aparece en `robots_activados`).

- [ ] **Step 3: Implementar en `MemRepo`**

`robots_activados`: añadir `and r.get("estrategia", "estructural") != "swing_express"` al filtro existente (línea 157-158).
`robots_swing_express(self, simbolo, tf)`: mismo patrón que `robots_activados`, filtrando `estrategia == "swing_express"`, devolviendo también `"posicion_abierta": r.get("posicion_abierta", False)`.
`MemRepo.__init__`: los robots creados vía `activados=(...)` siguen por defecto `estrategia="estructural"` (no rompe nada existente); agregar un segundo parámetro opcional o dejar que los tests los inyecten a mano como arriba — decisión del implementador, sin romper la firma actual de `__init__`.

- [ ] **Step 4: Implementar en `RepoPg`**

`robots_activados`: añadir `AND m.estrategia <> 'swing_express'` al `WHERE` del `SELECT` existente.
`robots_swing_express(self, simbolo, tf)`: mismo `SELECT` pero `AND m.estrategia = 'swing_express'`, agregando `m.posicion_abierta` a la lista de columnas.

- [ ] **Step 5: Correr y confirmar que pasa**

Run: `python master/despachador.py`
Expected: sin `AssertionError`, sin excepciones.

- [ ] **Step 6: Commit**

```bash
git add master/despachador.py
git commit -m "despachador: robots_activados excluye swing_express, nuevo robots_swing_express"
```

---

### Task 3: `recibir_tendencia_swing_express` — abrir y cerrar por tendencia

**Files:**
- Modify: `master/despachador.py` (nueva función, junto a `recibir_oportunidades`)

**Interfaces:**
- Consumes: `robots_swing_express` y `orden_para_entregar` (Task 2, ya existe), `crear_orden`, `ordenar_cierre` (ya existen).
- Produces: `recibir_tendencia_swing_express(repo, simbolo: str, temporalidad: str, direccion: str, ref_cierre: float | None, ahora: datetime | None = None) -> dict` → `{"aperturas": int, "cierres": int, "robots": int}`.

- [ ] **Step 1: Escribir el test en `demo()`**

```python
t0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
repoS = MemRepo()
repoS.robots[(9, "XAUUSD", "Swing (S)")] = {"licencia_id": 9, "cuenta": 999, "activo": True, "estrategia": "swing_express", "posicion_abierta": False}
# compra sin posicion abierta -> crea 1 orden
r1 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2650.0, t0)
assert r1 == {"aperturas": 1, "cierres": 0, "robots": 1}, r1
orden = next(o for o in repoS.ordenes.values() if o["licencia_id"] == 9)
assert orden["estrategia"] == "swing_express" and orden["direccion"] == "long" and orden["entrada"] == orden["stop"] == 2650.0
# compra otra vez, misma posicion todavia no marcada abierta -> no debe duplicar
r2 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2651.0, t0 + timedelta(seconds=5))
assert r2 == {"aperturas": 0, "cierres": 0, "robots": 1}, r2
# el robot "coloca" la orden (ack) y queda con posicion abierta
repoS.aplicar_ack(9, {"orden_id": orden["orden_id"], "estado": "colocada", "ticket": 777})
assert repoS.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is True
# venta con posicion abierta -> ordena cierre, nunca abre
r3 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "venta", 2640.0, t0 + timedelta(minutes=10))
assert r3 == {"aperturas": 0, "cierres": 1, "robots": 1}, r3
# venta de nuevo, sin posicion -> no-op (CerrarTodoMaster ya cerro, posicion_abierta ya en False tras el ack del cierre)
repoS.aplicar_ack(9, {"cierre_id": next(iter(repoS.cierres)), "estado": "ejecutado"})
assert repoS.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is False
r4 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "venta", 2639.0, t0 + timedelta(minutes=11))
assert r4 == {"aperturas": 0, "cierres": 0, "robots": 1}, r4
# re-entrada: compra de nuevo tras el ciclo completo -> SI debe poder abrir otra vez (clave distinta)
r5 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2655.0, t0 + timedelta(minutes=12))
assert r5 == {"aperturas": 1, "cierres": 0, "robots": 1}, r5
# "sin_definir" nunca hace nada
r6 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "sin_definir", 2655.0, t0 + timedelta(minutes=13))
assert r6 == {"aperturas": 0, "cierres": 0, "robots": 1}, r6
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `python master/despachador.py`
Expected: `NameError: name 'recibir_tendencia_swing_express' is not defined`

- [ ] **Step 3: Implementar `recibir_tendencia_swing_express`**

```python
def recibir_tendencia_swing_express(repo, simbolo: str, temporalidad: str, direccion: str,
                                     ref_cierre: float | None, ahora: datetime | None = None) -> dict:
```
Cuerpo: `ahora = ahora or _ahora()`; si `direccion not in ("compra", "venta")` o `ref_cierre` es `None` → `return {"aperturas": 0, "cierres": 0, "robots": 0}`. `robots = repo.robots_swing_express(simbolo, temporalidad)`. Para cada robot:
- si `direccion == "compra"`: saltar si `robot["posicion_abierta"]` o si `repo.orden_para_entregar(robot["licencia_id"], simbolo, temporalidad, ahora)` no es `None` (ya hay algo en vuelo); si no, `repo.crear_orden({"orden_id": "M"+uuid..., "licencia_id": ..., "cuenta": ..., "simbolo": simbolo, "temporalidad": temporalidad, "direccion": "long", "entrada": float(ref_cierre), "stop": float(ref_cierre), "tp": None, "estrategia": "swing_express", "clave": f"swing_express|{int(ahora.timestamp())}", "ref_ts": None, "ref_cierre": ref_cierre, "expira_en": ahora + timedelta(seconds=TTL_ORDEN_S)})` — contar apertura si devolvió `True`.
- si `direccion == "venta"`: saltar si no `robot["posicion_abierta"]`; si sí, `ordenar_cierre(repo, robot["licencia_id"], simbolo, temporalidad, "Swing Express: tendencia cambió a venta", ahora)` — contar cierre si no devolvió `None`.

`crear_orden`/`MemRepo.crear_orden` y `RepoPg.crear_orden` deben propagar el campo `estrategia` del dict `o` al INSERT/al registro en memoria (ajustar el `INSERT INTO master_ordenes (...)` del Task 1 en `RepoPg.crear_orden` para incluir la columna `estrategia`, y el dict literal de `MemRepo.crear_orden` ídem).

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `python master/despachador.py`
Expected: sin `AssertionError`.

- [ ] **Step 5: Commit**

```bash
git add master/despachador.py
git commit -m "despachador: recibir_tendencia_swing_express — abre/cierra por tendencia, sin setup"
```

---

### Task 4: `pulso()` — lote fijo, sin `_lotes()`, compuerta con `riesgo_pct=0`

**Files:**
- Modify: `master/despachador.py:96-147` (`pulso`), `:341-344` (`_COLS`/RepoPg), `:82-83` (`_publica`)

**Interfaces:**
- Consumes: campo `estrategia` en la orden (Task 1/3).
- Produces: `pulso(...)` entrega lotes `0.01` sin invocar `_lotes()` cuando `orden["estrategia"] == "swing_express"`; sigue invocando `compuerta.evaluar()` (con `riesgo_pct=0.0`, `rr=None`) para que los frenos de cuenta (drawdown/racha/pérdida diaria) sigan aplicando.

- [ ] **Step 1: Escribir el test en `demo()`**

```python
t0 = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
repoP = MemRepo(activados=[(5, "XAUUSD", "Swing (S)", 555)])
repoP.robots[(5, "XAUUSD", "Swing (S)")]["estrategia"] = "swing_express"
repoP.crear_orden({"orden_id": "Mswe1", "licencia_id": 5, "cuenta": 555, "simbolo": "XAUUSD", "temporalidad": "Swing (S)",
                   "direccion": "long", "entrada": 2650.0, "stop": 2650.0, "tp": None, "estrategia": "swing_express",
                   "clave": "swing_express|1", "ref_ts": None, "ref_cierre": 2650.0, "expira_en": t0 + timedelta(seconds=300)})
est = {"balance": 10000, "equity": 10000, "pico_equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 0, "riesgo_pct": 2.0}
r = pulso(repoP, 5, 555, "XAUUSD", "Swing (S)", est, ahora=t0)
assert r["estado"] == "entregada" and r["orden"]["lotes"] == 0.01, r
# con freno duro activo, swing_express SI debe bloquearse (no se salta la compuerta completa)
repoP2 = MemRepo(activados=[(6, "XAUUSD", "Swing (S)", 666)])
repoP2.robots[(6, "XAUUSD", "Swing (S)")]["estrategia"] = "swing_express"
repoP2.crear_orden({"orden_id": "Mswe2", "licencia_id": 6, "cuenta": 666, "simbolo": "XAUUSD", "temporalidad": "Swing (S)",
                    "direccion": "long", "entrada": 2650.0, "stop": 2650.0, "tp": None, "estrategia": "swing_express",
                    "clave": "swing_express|2", "ref_ts": None, "ref_cierre": 2650.0, "expira_en": t0 + timedelta(seconds=300)})
est_halt = {**est, "racha": 5}  # 5 perdidas seguidas = freno duro en la compuerta
r2 = pulso(repoP2, 6, 666, "XAUUSD", "Swing (S)", est_halt, ahora=t0)
assert r2["estado"] == "bloqueada", r2
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `python master/despachador.py`
Expected: `r["orden"]["lotes"]` sale distinto de `0.01` (hoy pasaría por `_lotes()` y probablemente fallaría por falta de `tick_size`/`tick_value` en `est`), o `KeyError`.

- [ ] **Step 3: Implementar la rama en `pulso()`**

Después de `orden = repo.orden_para_entregar(...)` y de la rama `"reentrega"` ya existente, antes de `base = float(est.get("riesgo_pct") or PCT_MAX)`:
```python
es_swing_express = orden.get("estrategia") == "swing_express"
riesgo_pct_compuerta = 0.0 if es_swing_express else min(base, PCT_MAX)
```
(ajustar el orden de las líneas para calcular `base` primero y luego decidir `riesgo_pct_compuerta`). La llamada a `compuerta.evaluar(est, riesgo_pct_compuerta, rr, cfg)` se queda igual — solo cambia qué `riesgo_pct` recibe. Después de pasar la compuerta: si `es_swing_express`, `lotes, motivo = 0.01, ""`; si no, `lotes, motivo = _lotes(est, orden, min(base * v["factor"], PCT_MAX))` (como hoy). El resto del flujo (`repo.marcar(..., "entregada", ...)`, `_publica`) no cambia.

`_COLS` en `RepoPg` (línea 341): agregar `estrategia` a la lista de columnas del `SELECT`. `_publica()` (línea 82): agregar `"estrategia"` a la tupla de claves que expone (el EA no necesita verlo, pero facilita depurar/probar).

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `python master/despachador.py`
Expected: sin `AssertionError`.

- [ ] **Step 5: Commit**

```bash
git add master/despachador.py
git commit -m "despachador: pulso fija lote 0.01 para swing_express, compuerta sigue aplicando frenos de cuenta"
```

---

### Task 5: `refresco.py` dispara `recibir_tendencia_swing_express`

**Files:**
- Modify: `refresco.py:178-186` (`_avisar_master`)

**Interfaces:**
- Consumes: `recibir_tendencia_swing_express` (Task 3), `body["tendencia_actual"]` (ya lo produce `api/setups.py::procesar`).

- [ ] **Step 1: Escribir el test**

No hay suite de tests para `refresco.py` hoy (se verifica por integración manual, como el resto del archivo). Verificación: revisar a mano que, para `temporalidad in ("Swing (S)", "Swing (M)")`, `_avisar_master` llama a `recibir_tendencia_swing_express(RepoPg(), simbolo, temporalidad, body.get("tendencia_actual"), body.get("ref_cierre"))` dentro del mismo `try/except` que ya envuelve `recibir_oportunidades` (nunca debe tumbar el refresco si el Master no responde).

- [ ] **Step 2: Implementar**

```python
def _avisar_master(simbolo: str, temporalidad: str, body: dict) -> None:
    try:
        from master.despachador import RepoPg, recibir_oportunidades, recibir_tendencia_swing_express, vigentes
        confirmados = vigentes(body.get("setups_confirmados") or [], body.get("velas"), body.get("ref_cierre"))
        if confirmados:
            recibir_oportunidades(RepoPg(), simbolo, temporalidad, confirmados, body.get("ref_ts"), body.get("ref_cierre"))
        if temporalidad in ("Swing (S)", "Swing (M)"):
            recibir_tendencia_swing_express(RepoPg(), simbolo, temporalidad, body.get("tendencia_actual"), body.get("ref_cierre"))
    except Exception as e:
        print(f"[master] oportunidad {simbolo} {temporalidad} no despachada: {type(e).__name__}: {e}", flush=True)
```

- [ ] **Step 3: Verificación manual en el laboratorio**

Con `MASTER_ACTIVO=1` en `mtb-api-preview`, forzar un refresco de XAUUSD Swing (S) y confirmar en `master_ordenes` (o en los logs) que aparece una fila `estrategia='swing_express'` cuando `tendencia_actual == 'compra'`.

- [ ] **Step 4: Commit**

```bash
git add refresco.py
git commit -m "refresco: dispara Swing Express en cada ciclo de refresco Swing (S)/(M)"
```

---

### Task 6: Ack de cierre ampliado (EA + despachador) y alimenta el historial

**Files:**
- Modify: `robots/MexTradeBot_SeguidorSMC.mq5` (`CerrarTodoMaster`, `MasterAckCierre`, líneas ~329-360)
- Modify: `master/despachador.py` (`aplicar_ack` en `MemRepo` y `RepoPg`)

**Interfaces:**
- Produces: ack de cierre con forma `{"cierre_id": "...", "estado": "ejecutado"|"fallido", "posiciones": [{"ticket": int, "precio_cierre": float, "profit_usd": float}]}` (lista vacía si no había nada que cerrar). `aplicar_ack` inserta una fila en `master_historial` por cada posición de la lista y pone `master_robots.posicion_abierta = false` para esa licencia cuando `estado == "ejecutado"`.

- [ ] **Step 1: Escribir el test en `demo()` de `despachador.py`**

```python
repoH = MemRepo()
repoH.robots[(9, "XAUUSD", "Swing (S)")] = {"licencia_id": 9, "cuenta": 999, "activo": True, "estrategia": "swing_express", "posicion_abierta": True}
repoH.cierres["Chist1"] = {"cierre_id": "Chist1", "licencia_id": 9, "simbolo": "XAUUSD", "temporalidad": "Swing (S)", "motivo": "tendencia cambio", "estado": "pendiente", "expira_en": t0 + timedelta(seconds=300)}
repoH.aplicar_ack(9, {"cierre_id": "Chist1", "estado": "ejecutado",
                      "posiciones": [{"ticket": 555001, "precio_cierre": 2639.5, "profit_usd": -12.3}]})
assert repoH.cierres["Chist1"]["estado"] == "ejecutado"
assert repoH.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is False
fila = repoH.historial[-1]
assert fila["licencia_id"] == 9 and fila["ticket"] == 555001 and fila["profit_usd"] == -12.3, fila
# un cierre con 2 posiciones en la misma lista inserta 2 filas
repoH.cierres["Chist2"] = {"cierre_id": "Chist2", "licencia_id": 9, "simbolo": "XAUUSD", "temporalidad": "Swing (S)", "motivo": "freno", "estado": "pendiente", "expira_en": t0 + timedelta(seconds=300)}
repoH.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] = True
repoH.aplicar_ack(9, {"cierre_id": "Chist2", "estado": "ejecutado",
                      "posiciones": [{"ticket": 1, "precio_cierre": 1.0, "profit_usd": 1.0}, {"ticket": 2, "precio_cierre": 2.0, "profit_usd": 2.0}]})
assert len(repoH.historial) == 3, repoH.historial
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `python master/despachador.py`
Expected: `AttributeError: 'MemRepo' object has no attribute 'historial'`

- [ ] **Step 3: Implementar en `MemRepo.aplicar_ack`**

Dentro del `if a.get("cierre_id"):`, después de actualizar `c["estado"]`: si `estado == "ejecutado"`, poner `self.robots[(lic, c["simbolo"], c["temporalidad"])]["posicion_abierta"] = False` (si la llave existe) y, por cada `p` en `a.get("posiciones") or []`, anexar a `self.historial` (lista nueva, inicializada en `__init__`) un dict con `licencia_id=lic`, `simbolo=c["simbolo"]`, `temporalidad=c["temporalidad"]`, `estrategia=self.robots.get((lic, c["simbolo"], c["temporalidad"]), {}).get("estrategia")`, `ticket=p.get("ticket")`, `precio_cierre=p.get("precio_cierre")`, `profit_usd=p.get("profit_usd")`, `razon_cierre=c["motivo"]`.

- [ ] **Step 4: Implementar en `RepoPg.aplicar_ack`**

Dentro del mismo `if a.get("cierre_id"):`, tras el `UPDATE master_cierres`: si `a.get("estado") == "ejecutado"`, `UPDATE master_robots SET posicion_abierta = false WHERE licencia_id = %s` (filtrando también `simbolo`/`temporalidad` del cierre), y por cada posición en `a.get("posiciones") or []`, `INSERT INTO master_historial (licencia_id, simbolo, temporalidad, estrategia, direccion, lotes, ticket, precio_cierre, profit_usd, razon_cierre) SELECT ... FROM master_robots/master_ordenes` o pasando los valores que ya se tienen a mano (simbolo/temporalidad del cierre, estrategia/direccion/lotes de la última orden `colocada` de esa licencia — un `SELECT` auxiliar sobre `master_ordenes ORDER BY creada_en DESC LIMIT 1`).

- [ ] **Step 5: Correr y confirmar que pasa**

Run: `python master/despachador.py`
Expected: sin `AssertionError`.

- [ ] **Step 6: Ampliar el EA (`MexTradeBot_SeguidorSMC.mq5`)**

En `CerrarTodoMaster` (línea ~336-360): antes de cerrar cada posición con `OrderSend`, leer `ticket`, `PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + PositionGetDouble(POSITION_COMMISSION)` (mismo criterio que `DealNeto()` ya usa para deals cerrados, línea 260, adaptado a una posición todavía abierta) y el precio de cierre (`tick.bid`/`tick.ask` según sentido, como ya hace `cerrar_posicion` en `conectividad/xm.py` del lado servidor para el mismo cálculo). Acumular un arreglo JSON `posiciones` con `{"ticket":...,"precio_cierre":...,"profit_usd":...}` por cada una. `MasterAckCierre(cierre_id, estado, posiciones_json)` — construir `g_master_ack` con el nuevo campo `"posiciones":[...]` (arreglo vacío `[]` si `CerrarTodoMaster` no cerró nada).
Compilar en MetaEditor: 0 errores, 0 advertencias (mismo criterio que versiones anteriores del EA, ver `docs/superpowers/specs/2026-10-29-master-trader-despachador-v3-construido.md`).

- [ ] **Step 7: Commit**

```bash
git add master/despachador.py robots/MexTradeBot_SeguidorSMC.mq5
git commit -m "ack de cierre reporta ticket/precio/ganancia por posicion; alimenta master_historial"
```

---

### Task 7: Backtest en Python contra los 34 activos (bloquea cualquier activación real)

**Files:**
- Create: `backtesting/swing_express_bt.py`

**Interfaces:**
- Consumes: `motor_smc.tendencia.obtener_tendencia` (ya existe), velas XM vía `conectividad.almacen` o el mismo patrón que `backtesting/oro_medianoche_bt_xm.py` ya usa para leer velas XM.
- Produces: reporte impreso (winrate, expectativa R, número de ciclos apertura→cierre) por símbolo y temporalidad Swing (S)/(M), sobre los 34 activos del catálogo.

- [ ] **Step 1: Escribir el test (`demo()`/`__main__` con assert)**

```python
def test_un_ciclo_sintetico():
    # tendencia alcista 30 velas, luego bajista 10: debe producir exactamente 1 ciclo abrir->cerrar
    ohlc = _ohlc_sintetico_alcista_luego_bajista()
    ciclos = simular_swing_express(ohlc, swing_length=5)
    assert len(ciclos) == 1 and ciclos[0]["r"] != 0, ciclos
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `python backtesting/swing_express_bt.py`
Expected: `NameError: name 'simular_swing_express' is not defined`

- [ ] **Step 3: Implementar `simular_swing_express(ohlc, swing_length) -> list[dict]`**

Recorre el `ohlc` vela a vela (o usa `obtener_tendencia` de forma incremental/rolling, igual que el motor ya hace), registrando cada ciclo abrir(cuando `direccion` pasa a `"compra"` sin posición)/cerrar(cuando pasa a `"venta"` con posición abierta) con precio de entrada, precio de salida y el resultado en R simbólico (ya que no hay SL, usar `(precio_salida - precio_entrada) / precio_entrada` como proxy de retorno, documentado en el reporte — no es un R real de la regla 1R/2R del motor, es una medida propia de Swing Express). Reusa el patrón de lectura de velas XM de `backtesting/oro_medianoche_bt_xm.py` (mismo repo, ya construido).

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `python backtesting/swing_express_bt.py`
Expected: sin `AssertionError`.

- [ ] **Step 5: Correr el backtest real contra los 34 activos y guardar el reporte**

Run: `python backtesting/swing_express_bt.py --todos` (o el flag que el implementador decida, siguiendo el patrón de los demás scripts en `backtesting/`)
Expected: reporte con winrate/expectativa por símbolo/temporalidad — **este resultado se le muestra a Ricardo antes de activar cualquier licencia Swing Express real** (spec §5, §6). Si el `swing_length` heredado de `SWING_LENGTH_POR_VELA` (5 para S/M, ver `conectividad/historico.py`) produce demasiados ciclos (whipsaw) o muy pocos, ajustar aquí — no en producción.

- [ ] **Step 6: Commit**

```bash
git add backtesting/swing_express_bt.py
git commit -m "backtest: Swing Express contra los 34 activos sobre velas XM"
```

---

### Task 8: Panel de Alumnos — "Catálogo de estrategias"

**Files:**
- Modify: `index.html` (sidebar `menus`, `titles`, nueva página `page-catalogoEstrategias`)

**Interfaces:**
- Consumes: `/api/catalogo` (ya existe, `api/catalogo.py`) — cada fila ya trae `simbolo`, `temporalidad`, `tendencia`/`direccion`, `expectativa_r`. Swing Express filtra client-side `temporalidad in ('Swing (S)','Swing (M)') && direccion === 'compra'` — **no se agrega ningún endpoint nuevo.**
- Sigue el markup real: `diseno/catalogo-estrategias/design-demos/sitio-fiel-produccion.html` (clases `.ag-asset`/`.ag-row`/`.ag-symbol`/`.ag-trend`/`.ag-metrics`/`.ag-foot`, ya verificadas contra `index.html` reconciliado).

- [ ] **Step 1: Agregar el ítem al sidebar**

En el arreglo `menus` (línea ~1830 de `index.html` tras la reconciliación del 6 nov): dentro del grupo `'ESTRATEGIAS'`, insertar `['catalogoEstrategias', MTB_ICO.algúnIcono]` entre `backtester` y `analisis` (o donde el implementador decida — visualmente antes de Backtester, porque es el punto de entrada conceptual). Agregar `catalogoEstrategias:'Catálogo de estrategias'` a `titles` (línea ~1541). Si no existe un `MTB_ICO` adecuado, añadir una entrada nueva al objeto `MTB_ICO` (mismo estilo SVG — trazo fino, `viewBox="0 0 24 24"`, sin relleno — que las demás 13 entradas ya definidas) en vez de reusar una existente con significado distinto.

- [ ] **Step 2: Página `page-catalogoEstrategias`**

Agregar al `host.appendChild` que ya crea páginas dinámicas (línea ~1837, junto a `'backtester','analisis','validaciones','ficha'`) la entrada `'catalogoEstrategias'`. Implementar `renderCatalogoEstrategias()`: 3 tarjetas (Estructural, Vela de Medianoche, Swing Express) con reseña fija (texto del spec §1/§2) + contador de candidatos en vivo; clic en Swing Express abre su vista de detalle con la rejilla `.ag-grid`/`.ag-asset` filtrando `window.MTB_FILAS_CATALOGO` (ya poblado por `cargarCatalogoTopLevel`, ver `index.html` línea ~1131) por `temporalidad in ('Swing (S)','Swing (M)') && (tendencia||direccion) === 'compra'`. Reusa `metrics()`/`badge()`/`timestamp()` ya definidas en el módulo del catálogo (mismo archivo) en vez de reescribirlas — reetiquetar solo las 3 columnas de `ag-metrics` a Expectativa/Lote/Tu posición para la vista de Swing Express, como ya hace el mockup.

- [ ] **Step 3: Verificación manual**

Abrir el laboratorio (`mtb-api-preview`) con sesión de alumno, navegar a "Catálogo de estrategias" → Swing Express, confirmar que la lista de candidatos coincide con lo que `/api/catalogo` reporta para Swing (S)/(M) con tendencia alcista, y que el resto del sidebar (Bots en operación, Mis robots, Conexión XM, etc.) sigue intacto.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "panel: Catálogo de estrategias — lista + detalle Swing Express, sin tocar el resto del sidebar"
```

---

## Self-Review

**1. Spec coverage:** §2 Goals: lote fijo/sin SL → Task 4; entrada/salida por tendencia → Task 3; límite 2/activo → Task 3 (gratis vía licencia+pre-chequeo); nomenclatura → ya es responsabilidad del admin al crear la licencia, no de código (spec §5 lo deja así, no genera tarea); catálogo de estrategias → Task 8; tabla de historial → Task 1 + Task 6. §3 Non-goals: Estructural/Vela de Medianoche intactas → verificado en cada task (ningún cambio toca `setups_v2.py` ni sus rutas); sidebar no se reduce → Task 8 Step 1 solo agrega. §4.3.1 ack ampliado → Task 6. §6 constraints → backtest Task 7 antes de cualquier activación real; "nada a producción sin OK" → ninguna tarea despliega, todas terminan en commit local.

**2. Step scan:** revisado — cada step de código trae firma exacta + valores del spec; los únicos bodies completos son los que un test ya fija (los `assert` de `demo()`) o cálculos que el spec mismo fija (lote 0.01, riesgo_pct 0.0).

**3. Type consistency:** `recibir_tendencia_swing_express` devuelve siempre `{"aperturas", "cierres", "robots"}` — mismo shape en Task 3 y en todos sus usos. `estrategia` es el nombre de campo usado consistentemente en `master_robots`, `master_ordenes` y `master_historial` (Task 1, 2, 3, 4, 6) — no se mezcla con "clase" (nombre que usaba el spec en borrador, descartado aquí a favor de un solo nombre en todo el código).

**4. Review Focus:** las 4 entradas de arriba ya tienen su test en la task correspondiente (Task 2 mezcla de estrategias, Task 3 no-op sin posición + re-entrada, Task 4 freno de cuenta, Task 6 cierre con 2 posiciones).

**5. Proportion:** el spec son ~115 líneas; este plan es más largo porque cada task incluye el `assert` completo (el spec no los tenía) — es proporcional al código real que describe, no una transcripción: los cuerpos de función siguen sin escribirse donde el signature+test ya los determinan (Task 3 Step 3, Task 4 Step 3 dan el algoritmo en prosa, no código completo).

---

**Desviaciones del spec que Ricardo debe confirmar** (ya explicadas arriba en "Spec", resumidas aquí para no perderlas):
1. No se construye `motor_smc/swing_express.py` — la tendencia ya viene en el `body` del refresco.
2. Swing Express sigue pasando por `compuerta.evaluar()` (con `riesgo_pct=0`) para que los frenos de cuenta (drawdown, racha, pérdida diaria) sigan protegiendo aunque esta estrategia no tenga SL — el spec decía saltarla por completo.
