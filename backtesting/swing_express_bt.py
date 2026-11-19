"""Backtest de Swing Express contra los 34 activos del catalogo, sobre velas XM (series S/M).

Simula el ciclo abrir/cerrar de Swing Express: abre en compra cuando la tendencia real
(`motor_smc.tendencia.obtener_tendencia`, evaluada de forma incremental, vela a vela) pasa a
"compra" sin posicion abierta, y cierra cuando pasa a "venta" con posicion abierta. El
resultado de cada ciclo se mide como (precio_salida - precio_entrada) / precio_entrada --
es un proxy de retorno PROPIO de Swing Express (no hay stop loss en este producto), NO el R
real de la regla 1R/2R que usa el resto del motor (ver backtesting/backtest.py). Ver spec
.superpowers/sdd/2026-11-06-swing-express/task-7-brief.md.

Uso:
  python backtesting/swing_express_bt.py           # test sintetico (rapido, sin red/BD)
  python backtesting/swing_express_bt.py --todos    # backtest real: 34 activos x Swing (S)/(M)
                                                     # lee Postgres (MTB_VELAS_FUENTE=xm), requiere
                                                     # DATABASE_URL y el historial XM ya cargado
                                                     # (conectividad/historial_xm.py)
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from motor_smc.tendencia import obtener_tendencia

# Swing (S)/(M): mismo swing_length que produccion (conectividad/historico.py TEMPORALIDADES,
# "Swing (S)" y "Swing (M)" = 5). Si el backtest real (--todos) muestra demasiados ciclos
# (whipsaw) o muy pocos, ajustar aqui -- no en produccion.
SWING_LENGTH = 5
# dias de historia por serie, igual que conectividad/historico.py TEMPORALIDADES (1095/2555).
DIAS_POR_SERIE = {"S": 1095, "M": 2555}


def simular_swing_express(ohlc: pd.DataFrame, swing_length: int) -> list[dict]:
    """Recorre `ohlc` vela a vela evaluando la tendencia real sobre la ventana expandida hasta
    esa vela (incremental/rolling, igual que hace el motor con datos que llegan en vivo).
    Registra cada ciclo abrir (direccion pasa a "compra" sin posicion abierta) / cerrar
    (direccion pasa a "venta" con posicion abierta). `r` es el proxy de retorno descrito en el
    docstring del modulo, no el R real 1R/2R."""
    minimo = swing_length * 2 + 1  # velas minimas para que obtener_tendencia tenga estructura
    ciclos = []
    abierto = False
    idx_entrada = precio_entrada = None
    for i in range(minimo, len(ohlc)):
        direccion = obtener_tendencia(ohlc.iloc[: i + 1], swing_length=swing_length)["direccion"]
        precio = float(ohlc["close"].iloc[i])
        if not abierto and direccion == "compra":
            abierto, idx_entrada, precio_entrada = True, i, precio
        elif abierto and direccion == "venta":
            ciclos.append({
                "idx_entrada": idx_entrada, "idx_salida": i,
                "precio_entrada": precio_entrada, "precio_salida": precio,
                "r": (precio - precio_entrada) / precio_entrada,
            })
            abierto = False
    return ciclos


def _ohlc_sintetico_alcista_luego_bajista(n_up: int = 80, n_down: int = 30) -> pd.DataFrame:
    """Escalera alcista (`n_up` velas) seguida de una bajista (`n_down` velas): un zigzag
    (seno) montado sobre la tendencia para que smc.swing_highs_lows encuentre swings reales
    (una linea perfectamente recta no tiene) -- cada pata ascendente/descendente produce
    varios BOS seguidos en la misma direccion, y la reversion un unico CHOCH de signo
    contrario, que es justo el patron "1 solo ciclo" que pide este test."""
    n, periodo, amplitud, pendiente = n_up + n_down, 10, 4.0, 1.0
    x = np.arange(n)
    tramo = np.concatenate([
        np.linspace(0, pendiente * n_up, n_up),
        np.linspace(pendiente * n_up, pendiente * n_up - 2 * pendiente * n_down, n_down),
    ])
    zigzag = amplitud * np.sin(2 * np.pi * x / periodo)
    close = 100 + tramo + zigzag
    idx = pd.date_range("2020-01-06", periods=n, freq="7D")
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1.0,
    }, index=idx)


def test_un_ciclo_sintetico():
    # tendencia alcista 30 velas, luego bajista 10: debe producir exactamente 1 ciclo abrir->cerrar
    ohlc = _ohlc_sintetico_alcista_luego_bajista()
    ciclos = simular_swing_express(ohlc, swing_length=5)
    assert len(ciclos) == 1 and ciclos[0]["r"] != 0, ciclos


def reporte_simbolo(ohlc: pd.DataFrame, swing_length: int) -> dict:
    """winrate/expectativa (promedio de `r`) sobre los ciclos de un simbolo+temporalidad."""
    ciclos = simular_swing_express(ohlc, swing_length)
    if not ciclos:
        return {"n_ciclos": 0, "winrate": None, "expectativa_r": None}
    rs = [c["r"] for c in ciclos]
    ganados = sum(1 for r in rs if r > 0)
    return {
        "n_ciclos": len(ciclos),
        "winrate": round(ganados / len(ciclos), 4),
        "expectativa_r": round(float(np.mean(rs)), 4),
    }


def backtest_todos() -> None:
    """Corre el backtest real contra los 34 activos del catalogo en Swing (S) y (M), leyendo
    velas XM desde el almacen (Postgres). Requiere DATABASE_URL y el historial XM ya cargado."""
    os.environ.setdefault("MTB_VELAS_FUENTE", "xm")
    from api.catalogo import SIMBOLO_XM
    from conectividad import almacen

    if almacen.FUENTE != "xm":
        raise RuntimeError("MTB_VELAS_FUENTE debe ser 'xm' para este backtest (ya estaba fijado por otro import)")

    fin = datetime.now(timezone.utc)
    print(f"{'simbolo':10} {'serie':6} {'velas':>6} {'ciclos':>6} {'winrate':>8} {'expectativa_r':>14}")
    for simbolo in SIMBOLO_XM:
        for serie, dias in DIAS_POR_SERIE.items():
            ohlc = almacen.leer(simbolo, serie, fin - timedelta(days=dias), fin)
            rep = reporte_simbolo(ohlc, SWING_LENGTH) if len(ohlc) else {"n_ciclos": 0, "winrate": None, "expectativa_r": None}
            winrate = "" if rep["winrate"] is None else f"{rep['winrate']:.2%}"
            exp = "" if rep["expectativa_r"] is None else f"{rep['expectativa_r']:+.4f}"
            print(f"{simbolo:10} {serie:6} {len(ohlc):6} {rep['n_ciclos']:6} {winrate:>8} {exp:>14}")


if __name__ == "__main__":
    test_un_ciclo_sintetico()
    print("swing_express_bt: test sintetico OK")
    if "--todos" in sys.argv:
        backtest_todos()
