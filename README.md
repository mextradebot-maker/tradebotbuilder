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

## Self-checks

```
.venv\Scripts\python.exe -m motor_smc.motor
.venv\Scripts\python.exe -m motor_smc.setup_ob_fvg
```

## Pendiente (no incluido en este paso)

- Conectividad: datos reales vía `MetaTrader5`/`dukascopy-python`, no solo datos sintéticos.
- Backtesting formal (Backtrader/VectorBT) con disciplina out-of-sample.
- Más setups de la metodología (§7) además del insignia OB+FVG.
