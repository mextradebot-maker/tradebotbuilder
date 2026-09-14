"""Indicadores clásicos de confluencia — RSI, Medias Móviles, MACD, Fibonacci.

Complementan (no reemplazan) el setup ancla de `setup_ob_fvg.detectar_setups`
(barrido de liquidez real + CHOCH + FVG + Order Block confluente). El alumno
elige UNO de estos 4 para confirmar su entrada — nunca varios a la vez (ver
docs/panel-alumnos-catalogo-indicadores-spec.md §4): combinar filtros reduce la
muestra de operaciones y da una falsa sensación de estrategia óptima cuando en
realidad está sobreajustada a datos pasados — es exactamente lo que las "3
reglas de oro" de backtesting/backtest.py ya advierten evitar.

Todo implementado a mano en pandas, sin dependencia nueva (ni pandas-ta ni
ta-lib) — misma filosofía de auditabilidad que ya siguen motor_smc/backtesting.
"""

import pandas as pd

INDICADORES_DISPONIBLES = ("RSI", "Medias Moviles", "MACD", "Fibonacci")


def rsi(ohlc: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """RSI de Wilder. Serie 0-100 (NaN durante el calentamiento inicial).

    Caso borde manejado explícito: si la pérdida promedio es 0 (racha sin
    ninguna vela roja), RS diverge — RSI debe ser 100 si hubo ganancia, o 50
    (neutral) si tampoco hubo ganancia. División normal solo cuando ambas
    medias ya están definidas y hay pérdida real que dividir."""
    delta = ohlc["close"].diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    media_ganancia = ganancia.ewm(alpha=1 / periodo, min_periods=periodo, adjust=False).mean()
    media_perdida = perdida.ewm(alpha=1 / periodo, min_periods=periodo, adjust=False).mean()

    valor = pd.Series(index=ohlc.index, dtype="float64")
    definidos = media_ganancia.notna() & media_perdida.notna()
    sin_perdidas = definidos & (media_perdida == 0)
    con_perdidas = definidos & ~sin_perdidas

    valor[con_perdidas] = 100 - (100 / (1 + media_ganancia[con_perdidas] / media_perdida[con_perdidas]))
    valor[sin_perdidas & (media_ganancia > 0)] = 100.0
    valor[sin_perdidas & (media_ganancia == 0)] = 50.0
    return valor.rename("RSI")


def medias_moviles(ohlc: pd.DataFrame, rapida: int = 20, lenta: int = 50) -> pd.DataFrame:
    """EMA rápida y lenta — confluencia = tendencia (rápida > lenta = alcista)."""
    ema_rapida = ohlc["close"].ewm(span=rapida, min_periods=rapida, adjust=False).mean()
    ema_lenta = ohlc["close"].ewm(span=lenta, min_periods=lenta, adjust=False).mean()
    return pd.DataFrame({"EMA_rapida": ema_rapida, "EMA_lenta": ema_lenta})


def macd(ohlc: pd.DataFrame, rapida: int = 12, lenta: int = 26, señal: int = 9) -> pd.DataFrame:
    """MACD estándar (12/26/9). Histograma > 0 = momentum alcista."""
    ema_rapida = ohlc["close"].ewm(span=rapida, adjust=False).mean()
    ema_lenta = ohlc["close"].ewm(span=lenta, adjust=False).mean()
    linea = ema_rapida - ema_lenta
    señal_linea = linea.ewm(span=señal, adjust=False).mean()
    return pd.DataFrame({"MACD": linea, "Signal": señal_linea, "Hist": linea - señal_linea})


def fibonacci_retroceso(swings: pd.DataFrame) -> pd.DataFrame:
    """Niveles de Fibonacci (38.2/50/61.8%) entre cada par consecutivo de swings
    — reusa el swing high/low que `motor.analizar()` ya calcula, no detecta nada
    nuevo. Una fila por tramo swing->swing, indexada en el swing de llegada."""
    swing_rows = swings.index[swings["HighLow"].notna()]
    filas = []
    anterior = None
    for idx in swing_rows:
        if anterior is not None:
            nivel_a = swings.at[anterior, "Level"]
            nivel_b = swings.at[idx, "Level"]
            rango = nivel_b - nivel_a  # signo = dirección del tramo (up-leg > 0, down-leg < 0)
            filas.append(
                {
                    "indice_inicio": anterior,
                    "indice_fin": idx,
                    "rango": abs(rango),
                    "fib_382": nivel_b - rango * 0.382,
                    "fib_500": nivel_b - rango * 0.5,
                    "fib_618": nivel_b - rango * 0.618,
                }
            )
        anterior = idx
    return pd.DataFrame(filas, columns=["indice_inicio", "indice_fin", "rango", "fib_382", "fib_500", "fib_618"])


def aplicar_confluencia(ohlc: pd.DataFrame, setups: pd.DataFrame, resultado_motor: dict, indicador: str) -> pd.DataFrame:
    """Filtra `setups` (salida de `setup_ob_fvg.detectar_setups`) a los que además
    confirma `indicador` — selección única, ver INDICADORES_DISPONIBLES. Nunca
    combina varios indicadores a la vez, a propósito (ver docstring del módulo)."""
    if indicador not in INDICADORES_DISPONIBLES:
        raise ValueError(f"indicador debe ser uno de {INDICADORES_DISPONIBLES}")
    if setups.empty:
        return setups

    if indicador == "RSI":
        valores = rsi(ohlc)
        confirma = setups.apply(
            lambda s: (valores.iloc[int(s["indice_barrido"])] <= 30)
            if s["direccion"] == "long"
            else (valores.iloc[int(s["indice_barrido"])] >= 70),
            axis=1,
        )
    elif indicador == "Medias Moviles":
        mm = medias_moviles(ohlc)
        confirma = setups.apply(
            lambda s: (
                mm["EMA_rapida"].iloc[int(s["indice_confirmacion"])] > mm["EMA_lenta"].iloc[int(s["indice_confirmacion"])]
                if s["direccion"] == "long"
                else mm["EMA_rapida"].iloc[int(s["indice_confirmacion"])] < mm["EMA_lenta"].iloc[int(s["indice_confirmacion"])]
            ),
            axis=1,
        )
    elif indicador == "MACD":
        m = macd(ohlc)
        confirma = setups.apply(
            lambda s: (
                m["Hist"].iloc[int(s["indice_confirmacion"])] > 0
                if s["direccion"] == "long"
                else m["Hist"].iloc[int(s["indice_confirmacion"])] < 0
            ),
            axis=1,
        )
    else:  # Fibonacci
        niveles = fibonacci_retroceso(resultado_motor["swings"])
        tolerancia_pct = 0.05  # ponytail: banda fija = 5% del rango del tramo; ajustar con datos reales si hace falta

        def _cerca_de_fib(fila) -> bool:
            tramo = niveles[niveles["indice_fin"] <= fila["indice_confirmacion"]]
            if tramo.empty:
                return False
            ultimo = tramo.iloc[-1]
            banda = ultimo["rango"] * tolerancia_pct
            objetivos = (ultimo["fib_382"], ultimo["fib_500"], ultimo["fib_618"])
            return any(abs(fila["entrada"] - nivel) <= banda for nivel in objetivos)

        confirma = setups.apply(_cerca_de_fib, axis=1)

    return setups[confirma].reset_index(drop=True)


def demo() -> None:
    import numpy as np

    idx = pd.date_range("2026-01-01", periods=80, freq="h")

    # RSI: serie monotona sube -> cerca de 100; serie monotona baja -> cerca de 0
    subida = pd.DataFrame({"close": np.linspace(100, 200, 80)}, index=idx)
    bajada = pd.DataFrame({"close": np.linspace(200, 100, 80)}, index=idx)
    rsi_sube = rsi(subida).iloc[-1]
    rsi_baja = rsi(bajada).iloc[-1]
    assert rsi_sube > 90, f"RSI en tendencia alcista fuerte debe acercarse a 100, dio {rsi_sube}"
    assert rsi_baja < 10, f"RSI en tendencia bajista fuerte debe acercarse a 0, dio {rsi_baja}"

    # Medias moviles: en tendencia alcista sostenida, EMA rapida > EMA lenta al final
    mm = medias_moviles(subida, rapida=5, lenta=20)
    assert mm["EMA_rapida"].iloc[-1] > mm["EMA_lenta"].iloc[-1]

    # MACD: en tendencia alcista sostenida, histograma positivo al final
    m = macd(subida)
    assert m["Hist"].iloc[-1] > 0

    # Fibonacci: swing de 100 (idx 10) a 200 (idx 20) -> fib_500 debe ser 150
    swings = pd.DataFrame(index=range(30), columns=["HighLow", "Level"])
    swings.loc[10, ["HighLow", "Level"]] = [-1, 100.0]
    swings.loc[20, ["HighLow", "Level"]] = [1, 200.0]
    niveles = fibonacci_retroceso(swings)
    assert len(niveles) == 1
    assert abs(niveles.iloc[0]["fib_500"] - 150.0) < 1e-9
    assert abs(niveles.iloc[0]["fib_382"] - 161.8) < 0.1  # 200 - 100*0.382

    # aplicar_confluencia: setup "long" con RSI muy bajo en el barrido debe pasar,
    # el mismo setup pero con RSI alto en el barrido debe quedar filtrado
    ohlc_rsi_bajo = bajada.assign(open=bajada["close"], high=bajada["close"] + 1, low=bajada["close"] - 1, volume=100)
    setups = pd.DataFrame(
        [{"indice_barrido": 79, "indice_confirmacion": 79, "indice_fvg": 79, "direccion": "long", "entrada": 100, "stop": 90}]
    )
    resultado_falso = {"swings": swings}
    pasa = aplicar_confluencia(ohlc_rsi_bajo, setups, resultado_falso, "RSI")
    assert len(pasa) == 1, "RSI sobrevendido en el barrido debe confirmar un setup long"

    ohlc_rsi_alto = subida.assign(open=subida["close"], high=subida["close"] + 1, low=subida["close"] - 1, volume=100)
    no_pasa = aplicar_confluencia(ohlc_rsi_alto, setups, resultado_falso, "RSI")
    assert len(no_pasa) == 0, "RSI sobrecomprado en el barrido NO debe confirmar un setup long"

    print("motor_smc.indicadores.demo() OK —", {
        "rsi_sube": round(float(rsi_sube), 2),
        "rsi_baja": round(float(rsi_baja), 2),
        "fib_500": niveles.iloc[0]["fib_500"],
        "confluencia_rsi_pasa": len(pasa),
        "confluencia_rsi_filtrada": len(no_pasa),
    })


if __name__ == "__main__":
    demo()
