# Estrategias por probar (variantes que compiten)

Registro de las alternativas que Ricardo dejó fuera al definir las reglas de gestión
(29–30 sep 2026). Ninguna es un descarte: la idea es ponerlas a competir contra la
versión en producción con el backtest largo y decidir con números.

## Versión en producción (la que se compara)

EA `robots/MexTradeBot_SeguidorSMC.mq5` v1.15:

| Regla | Producción |
|---|---|
| Riesgo por operación | 2% (Scalping / Intraday); no abre si ni el lote mínimo cabe en 2% |
| Swing S / M | Sin SL al abrir |
| Freno de pérdida | Cierre forzado a -3% del capital, todas las temporalidades |
| Breakeven | Escalonado: 1R → entrada, 1.5R → +0.5R, 2R → +1R |
| Trailing | Desde 2R, el SL sigue la estructura (curso L44, "cadena de demanda") |
| Salida | TP del motor (siguiente liquidez, mínimo 2R) |

## Variantes por probar

| # | Variante | Qué cambia contra producción | Pregunta que responde | Origen |
|---|---|---|---|---|
| V1 | **Swing sin freno -3%** | Swing S/M sin cierre forzado; confiar en la tendencia y aguantar el drawdown | ¿Cuántas veces se recupera de un drawdown mayor a 3% contra cuántas veces pierde ese 3%? | Ricardo, regla 2 |
| V2 | **Trailing C: SL fijo en +1R después de 2R** | Sin trailing: el SL se queda en +1R hasta el TP | ¿El trailing por estructura gana más de lo que corta antes de tiempo? | Ricardo, regla 4 |
| V3 | **Trailing A: escalera continua** | El SL sigue 1R detrás del precio cada +0.5R hasta el TP | ¿Números fijos contra estructura? | Opción A, regla 4 |
| V4 | **Parciales 75/25 (curso L27)** | En 1R se cierra el 75%; el 25% sigue con SL en la entrada | ¿Asegurar pronto mejora la curva de capital? | Opción B, regla 3 |
| V5 | **Parciales 50% escalonado (curso L43)** | En cada nivel se cierra el 50% y el SL pasa a la entrada | Igual que V4, más gradual | Opción C, regla 3 |
| V6 | **Tamaño de Swing S/M** | Lote más grande (aguanta ~1.5 veces la distancia del stop del motor antes del -3%) contra el provisional (~3 veces) | ¿Cuánto espacio necesita el swing para no ser sacado por ruido? | Regla 2, pregunta 2 |

## Pendiente para poder compararlas

- El backtest (`backtesting/backtest.py`) todavía simula SL fijo y TP a 2R: hay que
  enseñarle breakeven, trailing, freno -3% y parciales antes de comparar.
- Esperar a que termine la carga histórica de 15m / 1H para tener muestra suficiente.
- Medir por variante: expectativa en R, % ganadoras, drawdown máximo, número de
  operaciones, y (V1) recuperaciones contra pérdidas del 3%.
