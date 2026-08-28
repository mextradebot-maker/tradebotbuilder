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
```

## Pendiente

- Conector XM (credenciales de cuenta) y soporte MT4/MT5 en vivo (`MetaTrader5`).
- Filtro de calendario económico (`nfs.faireconomy.media`).
- Backtesting formal (Backtrader/VectorBT) con disciplina out-of-sample.
- Más setups de la metodología (§7) además del insignia OB+FVG.
