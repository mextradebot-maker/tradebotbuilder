# Calibración del motor: swing_length por temporalidad

**Fecha:** 2026-09-27 · **Aprobado por:** Ricardo (en sesión)

## Problema
El motor encuentra muy pocos setups (XAUUSD Intraday: 1 en 5,886 velas) y Swing (M)
nunca define tendencia. `swing_length=20` es el mismo para las 5 temporalidades
(15 min … mensual): 20 velas son 5 horas en Scalping y 20 meses en Swing (M).

## Decisiones
- Palanca: `swing_length` por temporalidad. `VENTANA_FVG_VELAS` (5),
  `UMBRAL_SETUPS_SUFICIENTES` (20) y TP 2R no se tocan en esta ronda.
- Método: barrido empírico sobre una muestra de 4 símbolos (XAUUSD, EURUSD,
  US30, BTCUSD) con la misma ventana de días que usa producción
  (`DIAS_POR_TEMPORALIDAD`). Ricardo elige el valor por temporalidad a partir
  de la tabla; los 36 activos lo heredan vía `refresco.py`.
- Regla de oro "siempre out-of-sample": la tabla reporta la expectativa del
  último 30% del periodo por separado, para no elegir un valor que solo
  funciona sobre los datos con los que se eligió.

## Métricas por (temporalidad, swing_length)
setups totales y por mes · resueltos · winrate · expectativa_r (todo el periodo
y out-of-sample) · combinaciones símbolo×dirección que alcanzan el umbral de 20
casos · símbolos con tendencia definida (x/4).

## Implementación tras la elección
- `SWING_LENGTH_POR_TEMPORALIDAD` junto a `DIAS_POR_TEMPORALIDAD`, default en
  `api/setups.py`, `api/tendencia.py`, `api/backtest.py`.
- `desde_snapshot()` compara contra ese dict en vez del 20 escrito a mano.
- n8n no manda `swing_length`: hereda el cambio sin tocarse.
- Tras desplegar: revisar que los 36 activos se comporten como la muestra.
