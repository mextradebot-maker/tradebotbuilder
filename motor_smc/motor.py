"""Motor propio SMC — detección nativa de Order Blocks, FVG, liquidez y estructura de mercado.

No reimplementa la detección (ver docs/mextradebot-mapa-tecnico, Sección 10): envuelve la
librería abierta `smartmoneyconcepts` sobre datos OHLC ya cargados (MT5, dukascopy, CSV...).
"""

import pandas as pd
from smartmoneyconcepts import smc

COLUMNAS_REQUERIDAS = {"open", "high", "low", "close"}


def analizar(ohlc: pd.DataFrame, swing_length: int = 50) -> dict[str, pd.DataFrame]:
    """Corre la detección SMC completa sobre un DataFrame OHLC (columnas en minúsculas).

    Devuelve un dict con las cinco piezas que alimentan el motor de reglas:
    swings, fvg, order_blocks, estructura (BOS/CHoCH) y liquidez (barridos).
    """
    faltantes = COLUMNAS_REQUERIDAS - set(ohlc.columns)
    if faltantes:
        raise ValueError(f"Faltan columnas OHLC: {sorted(faltantes)}")

    swings = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    return {
        "swings": swings,
        "fvg": smc.fvg(ohlc),
        "order_blocks": smc.ob(ohlc, swings),
        "estructura": smc.bos_choch(ohlc, swings),
        "liquidez": smc.liquidity(ohlc, swings),
    }


def demo() -> None:
    import numpy as np

    rng = np.random.default_rng(42)
    n = 300
    close = 2000 + np.cumsum(rng.normal(0, 2, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    ohlc = pd.DataFrame(
        {
            "open": close + rng.normal(0, 1, n),
            "high": close + rng.uniform(0.5, 3, n),
            "low": close - rng.uniform(0.5, 3, n),
            "close": close,
            "volume": rng.uniform(100, 1000, n),
        },
        index=idx,
    )

    resultado = analizar(ohlc, swing_length=10)
    assert set(resultado) == {"swings", "fvg", "order_blocks", "estructura", "liquidez"}
    for nombre, df in resultado.items():
        assert len(df) == len(ohlc), f"{nombre} debe tener una fila por vela"
    assert resultado["swings"]["HighLow"].notna().any(), "debe detectar al menos un swing"
    print("motor_smc.motor.demo() OK —", {k: v.shape for k, v in resultado.items()})


if __name__ == "__main__":
    demo()
