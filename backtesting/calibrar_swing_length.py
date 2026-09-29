"""Barrido de swing_length por temporalidad — spec
docs/superpowers/specs/2026-09-27-calibracion-swing-length-design.md.

Para cada temporalidad descarga una vez las velas de una muestra de símbolos
(misma ventana de días que producción) y corre detección + backtest con varios
swing_length. Imprime una tabla por temporalidad para elegir el valor a mano.

Uso: uv run python -m backtesting.calibrar_swing_length [salida.csv]
"""

import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from api.setups import DIAS_POR_TEMPORALIDAD
from backtesting.backtest import reporte, simular
from conectividad import TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia

SIMBOLOS = ["XAUUSD", "EURUSD", "US30", "BTCUSD"]
CANDIDATOS = {
    "Scalping 15m": [5, 8, 10, 15, 20, 30],
    "Intraday 1H": [5, 8, 10, 15, 20, 30],
    "Intraday 4H": [5, 8, 10, 15, 20, 30],
    "Swing (S)": [3, 5, 8, 10, 15, 20],   # ~157 velas semanales en 3 años
    "Swing (M)": [2, 3, 5, 8, 10, 20],    # ~85 velas mensuales en 7 años
}
FRACCION_OOS = 0.3  # último 30% del periodo: validación out-of-sample
UMBRAL = 20         # = api.setups.UMBRAL_SETUPS_SUFICIENTES


def medir(ohlc: pd.DataFrame, swing_length: int) -> dict:
    setups = detectar_setups(ohlc, analizar(ohlc, swing_length=swing_length))
    res = simular(ohlc, setups)
    corte = int(len(ohlc) * (1 - FRACCION_OOS))
    oos = res[res["indice_barrido"] >= corte] if len(res) else res
    por_dir = [reporte(res[res["direccion"] == d]) if len(res) else {"n_setups": 0} for d in ("long", "short")]
    return {
        "setups": len(res),
        "resueltos": reporte(res)["n_setups"] if len(res) else 0,
        "r_suma": float(res.loc[res["resultado"] != "sin_resolver", "r"].sum()) if len(res) else 0.0,
        "ganadas": int((res["resultado"] == "gano").sum()) if len(res) else 0,
        "oos_resueltos": int((oos["resultado"] != "sin_resolver").sum()) if len(oos) else 0,
        "oos_r_suma": float(oos.loc[oos["resultado"] != "sin_resolver", "r"].sum()) if len(oos) else 0.0,
        "dirs_con_umbral": sum((d.get("n_setups") or 0) >= UMBRAL for d in por_dir),
        "tendencia_definida": obtener_tendencia(ohlc, swing_length=swing_length)["direccion"] != "sin_definir",
    }


def barrer() -> pd.DataFrame:
    fin = datetime.now(timezone.utc)
    filas = []
    for temp, valores in CANDIDATOS.items():
        dias = DIAS_POR_TEMPORALIDAD[temp]
        for sim in SIMBOLOS:
            ohlc = obtener_velas(sim, fin - timedelta(days=dias), fin, intervalo=TEMPORALIDAD_A_INTERVALO[temp])
            print(f"{temp:10} {sim:7} {len(ohlc):6} velas", file=sys.stderr, flush=True)
            if ohlc.empty:
                continue
            for sl in valores:
                filas.append({"temporalidad": temp, "swing_length": sl, "simbolo": sim, "dias": dias, **medir(ohlc, sl)})
    return pd.DataFrame(filas)


def resumir(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["temporalidad", "swing_length"], sort=False)
    t = g.agg(setups=("setups", "sum"), resueltos=("resueltos", "sum"), ganadas=("ganadas", "sum"),
              r_suma=("r_suma", "sum"), oos_resueltos=("oos_resueltos", "sum"), oos_r_suma=("oos_r_suma", "sum"),
              dirs_con_umbral=("dirs_con_umbral", "sum"), tendencia=("tendencia_definida", "sum"),
              dias=("dias", "first"), simbolos=("simbolo", "nunique"))
    t["setups_mes"] = (t["setups"] / t["simbolos"] / (t["dias"] / 30)).round(2)
    t["winrate"] = (t["ganadas"] / t["resueltos"]).round(3)
    t["expectativa_r"] = (t["r_suma"] / t["resueltos"]).round(3)
    t["oos_expectativa_r"] = (t["oos_r_suma"] / t["oos_resueltos"]).round(3)
    return t[["setups", "setups_mes", "resueltos", "winrate", "expectativa_r", "oos_resueltos",
              "oos_expectativa_r", "dirs_con_umbral", "tendencia"]].reset_index()


def demo() -> None:
    import numpy as np

    rng = np.random.default_rng(7)
    n = 400
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    ohlc = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close,
                         "volume": 1.0}, index=pd.date_range("2026-01-01", periods=n, freq="h"))
    m = medir(ohlc, 5)
    assert m["setups"] >= m["resueltos"] >= m["oos_resueltos"] >= 0
    df = pd.DataFrame([{"temporalidad": "Intraday 1H", "swing_length": 5, "simbolo": "X", "dias": 30, **m}])
    assert list(resumir(df)["setups_mes"]) == [float(m["setups"])]
    print("calibrar_swing_length.demo() OK", m)


if __name__ == "__main__":
    datos = barrer()
    if len(sys.argv) > 1:
        datos.to_csv(sys.argv[1], index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        for temp, tabla in resumir(datos).groupby("temporalidad", sort=False):
            print(f"\n=== {temp} ===")
            print(tabla.drop(columns="temporalidad").to_string(index=False))
