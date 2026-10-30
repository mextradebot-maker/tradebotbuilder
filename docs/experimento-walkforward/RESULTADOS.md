# Walk-forward: ¿operar todo vs. seleccionar con evidencia? — 25 oct 2026

**Datos:** `docs/experimento-stop/exp_stop_setups.csv`, factor de stop 0.5 (producción): 1,207 setups resueltos, 8 símbolos (EURUSD, GBPUSD, USDJPY, AUDJPY, XAUUSD, US30, BRENTUSD, BTCUSD), Intraday 1H (2019–2026) y Scalping 15m (2023–2026). Script: `wf.py` (solo lectura, sin tocar producción).
**Método:** expansión temporal por trimestre; cada decisión usa **solo datos anteriores** al trimestre (1,052 setups evaluados desde abril 2021). Costos: spread **supuesto** por símbolo (ilustrativo) dividido entre la distancia al stop.

| Estrategia (costos x1) | % de setups | R bruto/op | R neto/op | R neto total | Error est. |
|---|---|---|---|---|---|
| Operar todo (hoy) | 100 | +0.009 | **−0.080** | −83.6 | 0.061 |
| Filtro ingenuo (celda con n≥10 y media>0) | 30 | +0.090 | +0.010 | +3.2 | 0.113 |
| Filtro por grupo (TF×dirección) | 46 | −0.046 | −0.138 | −67.0 | 0.082 |
| Estimación encogida (>0 neto de costos) | 21 | −0.275 | −0.344 | −75.9 | 0.092 |
| Encogida con P(>0) ≥ 80 % | 7 | −0.295 | −0.364 | −25.1 | 0.176 |

- Por temporalidad (neto, costos x1): **1H ≈ 0.00 R/op** (n=390); **15m ≈ −0.13 R/op** (n=662): el costo medio supuesto en 15m es 0.11 R.
- **Persistencia:** la expectativa de cada celda antes de 2023 casi no predice la de después (correlación 0.18, 10 celdas). Las 4 celdas con +0.48 R antes de 2023 dieron +0.08 R después.
- **Concentración:** en 1H, el 62 % de los días con setups de EURUSD/GBPUSD/USDJPY tienen ≥2 en la misma dirección el mismo día (39 % en 15m).

**Límites:** 8 de 34 símbolos; sin BE/trailing/parciales (el backtest no los simula); spreads supuestos; errores estándar ignoran la correlación entre setups; sin línea base aleatoria.
