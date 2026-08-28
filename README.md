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

## API (Vercel)

`POST /api/analizar` — body `{"ohlc": [{"open","high","low","close","volume"}, ...], "swing_length"?, "ventana_fvg"?}`,
responde `{"setups": [...]}`. Es un endpoint mínimo (sin auth, sin rate limit) para
probar el motor desde afuera; no reemplaza la conectividad real (MT5/dukascopy).

## Self-checks

```
.venv\Scripts\python.exe -m motor_smc.motor
.venv\Scripts\python.exe -m motor_smc.setup_ob_fvg
.venv\Scripts\python.exe -m api.analizar
.venv\Scripts\python.exe -m conectividad.historico
.venv\Scripts\python.exe -m conectividad.xm
.venv\Scripts\python.exe -m conectividad.calendario
```

## Pendiente

- Envío de órdenes vía XM (el conector de arriba es solo lectura por ahora).
- Backtesting formal (Backtrader/VectorBT) con disciplina out-of-sample.
- Más setups de la metodología (§7) además del insignia OB+FVG.
