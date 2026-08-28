"""Identificación de tendencia actual — decide si el sesgo de un símbolo es compra o venta.

Se deriva del último evento BOS/CHoCH detectado por el motor — mismo signo que ya usan
los setups (1 = alcista/compra, -1 = bajista/venta) — igual para las 3 temporalidades.

Regla de negocio (decisión de Ricardo, documentada en docs/manual-tecnico-interno.md):
a largo plazo (swing, meses) los activos tienden a subir, así que swing solo se ofrece
del lado de compra — pero esa restricción NO se hardcodea aquí (sería mentir sobre la
tendencia real). Este módulo siempre reporta la dirección real detectada; el filtro
"swing solo si es alcista" vive en la capa que orquesta la entrega (T-04 en n8n), que
decide si ofrece o rechaza el símbolo según tipo + dirección real.
"""

import pandas as pd

from .motor import analizar


def obtener_tendencia(ohlc: pd.DataFrame, swing_length: int = 50) -> dict:
    """La dirección real detectada es la misma sin importar la temporalidad elegida
    por el usuario — la restricción "swing solo si es alcista" no vive aquí, ver
    docstring del módulo."""
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

    r = obtener_tendencia(ohlc, swing_length=8)
    assert r["direccion"] in {"compra", "venta"}
    assert r["fuente"] == "estructura_mercado"

    print("motor_smc.tendencia.demo() OK —", r)


if __name__ == "__main__":
    demo()
