"""Identificación de tendencia actual — decide si el sesgo de un símbolo es compra o venta.

Para temporalidad Swing es una regla de negocio fija (decisión de Ricardo, documentada en
docs/manual-tecnico-interno.md): siempre venta, independiente de la estructura técnica.
Para Scalping/Intraday se deriva del último evento BOS/CHoCH detectado por el motor —
mismo signo que ya usan los setups (1 = alcista/compra, -1 = bajista/venta).
"""

import pandas as pd

from .motor import analizar

TEMPORALIDADES_VENTA_FIJA = {"Swing"}


def obtener_tendencia(ohlc: pd.DataFrame, tipo: str, swing_length: int = 50) -> dict:
    if tipo in TEMPORALIDADES_VENTA_FIJA:
        return {
            "direccion": "venta",
            "fuente": "regla_negocio",
            "razon": "En temporalidad swing operamos siempre del lado de venta, por decisión de estrategia.",
        }

    estructura = analizar(ohlc, swing_length=swing_length)["estructura"]
    señales = estructura[estructura["BOS"].notna() | estructura["CHOCH"].notna()]
    if señales.empty:
        return {
            "direccion": "sin_definir",
            "fuente": "estructura_mercado",
            "razon": "No hay suficiente estructura de mercado reciente para determinar tendencia.",
        }

    ultima = señales.iloc[-1]
    es_choch = pd.notna(ultima["CHOCH"])
    valor = ultima["CHOCH"] if es_choch else ultima["BOS"]
    direccion = "compra" if valor > 0 else "venta"
    evento = "CHOCH" if es_choch else "BOS"
    return {
        "direccion": direccion,
        "fuente": "estructura_mercado",
        "evento": evento,
        "razon": f"Último {evento} detectado marca estructura {'alcista' if direccion == 'compra' else 'bajista'}.",
    }


def demo() -> None:
    import numpy as np

    rng = np.random.default_rng(190)
    n = 500
    tramo = np.concatenate([np.linspace(0, 40, 120), np.linspace(40, -20, 160), np.linspace(-20, 30, 220)])
    close = 2000 + tramo + np.cumsum(rng.normal(0, 0.8, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    ohlc = pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.5, n),
            "high": close + rng.uniform(0.5, 2.5, n),
            "low": close - rng.uniform(0.5, 2.5, n),
            "close": close,
            "volume": rng.uniform(100, 1000, n),
        },
        index=idx,
    )

    swing = obtener_tendencia(ohlc, "Swing", swing_length=8)
    assert swing == {
        "direccion": "venta",
        "fuente": "regla_negocio",
        "razon": "En temporalidad swing operamos siempre del lado de venta, por decisión de estrategia.",
    }

    intraday = obtener_tendencia(ohlc, "Intraday", swing_length=8)
    assert intraday["direccion"] in {"compra", "venta"}
    assert intraday["fuente"] == "estructura_mercado"

    print("motor_smc.tendencia.demo() OK —", {"swing": swing, "intraday": intraday})


if __name__ == "__main__":
    demo()
