# motor_smc — Paso 1 del plan de construcción

Motor propio de reglas SMC (Order Blocks, FVG, liquidez, estructura de mercado),
según la Sección 10 del [mapa técnico](https://github.com/mextradebot-maker/mextradebot-mapa-tecnico) (Plan de construcción).
No reimplementa la detección: envuelve `smartmoneyconcepts` sobre datos OHLC.

## Setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Uso

```python
from motor_smc import analizar, detectar_setups

resultado = analizar(ohlc)              # ohlc: DataFrame con columnas open/high/low/close
setups = detectar_setups(ohlc, resultado)  # setup insignia OB+FVG (metodologia-trading.md §7.4)
```

`analizar()` es genérico: expone swings, FVG, order blocks, estructura (BOS/CHoCH)
y liquidez sin opinar sobre estrategia. `detectar_setups()` es la primera regla
concreta encima de esa detección — el setup "cacería de liquidez + MSB + FVG".

## Datos históricos

`conectividad.historico.obtener_velas("XAUUSD", inicio, fin)` — vía `dukascopy-python`
(reemplaza TickStory, Plan de construcción §Paso 1b). Devuelve un DataFrame ya
compatible con `motor_smc.analizar()`, sin transformar nada:

```python
from datetime import datetime
from conectividad import obtener_velas
from motor_smc import analizar, detectar_setups

ohlc = obtener_velas("XAUUSD", datetime(2024, 1, 1), datetime(2024, 2, 1))
setups = detectar_setups(ohlc, analizar(ohlc, swing_length=20))
```

## Cuenta XM (MT5, en vivo)

Solo lectura por ahora (conexión, info de cuenta, velas en vivo) — **envío de
órdenes no está implementado todavía**, queda para un paso aparte con sus
propias salvaguardas. Requiere una terminal MT5 de XM corriendo/logueada en
esta máquina (`MetaTrader5` es un puente IPC local, Windows-only — **no
funciona en Vercel**, por eso está marcado `sys_platform == 'win32'` en las
dependencias y no se importa desde `conectividad/__init__.py`).

```
copy .env.example .env
:: llena XM_LOGIN / XM_PASSWORD / XM_SERVER en .env (nunca en el repo)
```

```python
from conectividad.xm import conectar, info_cuenta, velas_en_vivo, desconectar

conectar()
cuenta = info_cuenta()
desconectar()
```

## Filtro de calendario económico

`conectividad.calendario` — feed gratuito de ForexFactory (`nfs.faireconomy.media`,
sin costo, sin API key). Dato estructurado y determinista, por eso puede ser
un Filtro del motor (a diferencia de un resumen de noticias por IA — ver
Sección 10 del mapa técnico: eso, si se construye, es contenido aparte, nunca
señal de entrada). Cachea en disco y solo refresca 1 vez al día (el proveedor
permite hasta 2 descargas/5min, muy por debajo); si el feed deja de responder
tolera hasta 48h de cache viejo y después se desactiva explícitamente
(`CalendarioNoDisponibleError`) en vez de operar con datos viejos sin avisar.

```python
from datetime import datetime, timezone
from conectividad.calendario import hay_evento_alto_impacto_cerca

if hay_evento_alto_impacto_cerca(datetime.now(timezone.utc), ventana_minutos=30, moneda="USD"):
    pass  # no abrir posiciones nuevas
```

## Backtesting formal

`backtesting.backtest` — simulación propia sobre pandas, no Backtrader/VectorBT
(ver el porqué en el docstring del módulo: los setups son eventos discretos,
no una estrategia continua barra-por-barra, así que un framework completo es
más de lo que hace falta hoy). Aplica las 3 reglas de oro de la Sección 05
del mapa técnico: nunca optimizar volumen (N/A, no hay money management
todavía), siempre out-of-sample, nunca partir de un perdedor.

```python
from datetime import datetime
from conectividad import obtener_velas
from backtesting import backtest_out_of_sample

ohlc = obtener_velas("XAUUSD", datetime(2020, 1, 1), datetime(2024, 1, 1))
resultado = backtest_out_of_sample(ohlc, corte=datetime(2023, 1, 1, tzinfo=ohlc.index.tz))
# {'in_sample': {...}, 'out_of_sample': {...}} -- cada uno con winrate, r_total,
# expectativa_r, y rentable_sin_optimizar (la regla de oro como gate explícito)
```

**Limitaciones de este v1**, documentadas para no confundir el resultado con
algo más sólido de lo que es:
- Take-profit fijo a 2R (`RETORNO_RIESGO_TP`) — el setup §7.4 no documenta su
  propia regla de salida, solo entrada y stop.
- Asume que la entrada siempre se llena al precio del punto medio del FVG,
  sin verificar que el precio realmente vuelva a tocarlo.
- Si stop y take-profit se tocan en la misma vela, se asume el peor caso
  (pierde) — no hay forma de saber el orden intrabar con velas ya cerradas.
- Sin comisiones, spread ni slippage.

## API (Vercel)

Sin auth ni rate limit — endpoints mínimos para probar el motor desde afuera
(ej. n8n). **Importante:** Vercel corre Python en modo single-entrypoint
(`[tool.vercel] entrypoint` en pyproject.toml) — a diferencia de Next.js/Node,
NO auto-descubre cada archivo de `api/` como su propia función. `api/analizar.py`
es el único entrypoint real; hace de router por `path` hacia la lógica de
`api/setups.py`. Si se agrega un endpoint nuevo, hay que sumarlo al router,
no basta con crear el archivo.

- `POST /api/analizar` — body `{"ohlc": [...], "swing_length"?, "ventana_fvg"?}`
  → `{"setups": [...]}`. Analiza OHLC ya provisto.
- `GET/POST /api/setups?simbolo=XAUUSD&dias=90` → `{"simbolo", "velas", "setups": [...]}`.
  Trae los datos históricos él mismo (dukascopy) y corre el motor completo —
  para consumidores (como n8n) que no pueden llamar a `dukascopy-python`
  directamente por HTTP.

## Self-checks

```
.venv\Scripts\python.exe -m motor_smc.motor
.venv\Scripts\python.exe -m motor_smc.setup_ob_fvg
.venv\Scripts\python.exe -m api.analizar
.venv\Scripts\python.exe -m conectividad.historico
.venv\Scripts\python.exe -m conectividad.xm
.venv\Scripts\python.exe -m conectividad.calendario
.venv\Scripts\python.exe -m backtesting.backtest
```

## Pendiente

- Envío de órdenes vía XM (el conector de arriba es solo lectura por ahora).
- Reparar pipeline n8n T-01→T-05 (Paso 2 del plan de construcción).
- Más setups de la metodología (§7) además del insignia OB+FVG.

## Cerebro de licencias (Master Trader)

`persistencia/licencias.py` + `api/licencias.py`. Criterio: 1 token = 1 cuenta MT5;
DEMO solo en cuentas demo, REAL solo en reales, VIP en ambas con cualquier robot;
en la BD solo vive el SHA-256 del token (se muestra una vez al emitirlo); todo falla cerrado.

| Endpoint | Quién | Qué |
|---|---|---|
| `POST /api/v1/auth` + `Authorization: Bearer <token>` | EA del cliente | Autoriza UNA orden y devuelve los lotes (regla única `conectividad/riesgo.py`) |
| `GET/POST /api/v1/licencias` + `X-Admin-Key` | Página de administración `/master.html` | Emitir, revocar, reautorizar/renovar, kill switch, auditoría |
| `/api/setups` | EA / n8n | Con Bearer valida la licencia; sin Bearer exige `X-MTB-Service-Key` cuando `MTB_SERVICE_KEY` existe |

El kill switch bloquea autorizaciones nuevas en todos los bots **y** las aperturas del coordinador del VPS (los cierres siguen).

**Despliegue (en orden):**
1. Vercel → env `MTB_ADMIN_KEY` (clave larga aleatoria; es la del panel). Las tablas se crean solas al primer uso (`persistencia/migraciones.py`).
2. Emitir licencias desde el panel e instalar el EA v1.10 (`robots/MexTradeBot_SeguidorSMC.mq5`) con `MTB_LICENSE_TOKEN`.
3. Agregar el header `X-MTB-Service-Key` a los nodos HTTP de n8n que llaman `/api/setups` (T-01, Análisis Diario).
4. Solo entonces crear `MTB_SERVICE_KEY` en Vercel: desde ese momento un EA copiado o sin licencia no recibe señales.

## Regla única de lotes

`conectividad/riesgo.py` es la única implementación: el coordinador la llama directo y los EA
reciben los lotes de `/api/v1/auth`. Redondea hacia abajo al step del broker y, si ni el lote
mínimo cabe en el riesgo, marca la operación como no viable (nunca sube al mínimo en silencio).
Autoverificación: `python -m conectividad.riesgo`.
