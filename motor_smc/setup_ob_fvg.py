"""Setup OB+FVG — "cacería de liquidez + cambio de estructura + FVG".

Estrategia insignia documentada en docs/metodologia-trading.md, Sección 7.4 (L20).
Receta de 7 pasos operacionalizada sobre la salida de `motor.analizar()`:

  1-3. Microestructura + barrido de liquidez + MSB inmediato después del barrido:
       ya vienen resueltos por `smc.bos_choch` — un CHOCH solo se marca cuando el
       swing más reciente ya rompió (barrió) el swing anterior en la dirección
       opuesta a la tendencia previa, así que la fila donde CHOCH != NaN ES la
       vela del barrido, y su propio low/high (según dirección) es el nivel barrido.
  4-5. Vela dominante + FVG real: se busca el primer FVG del mismo signo que el
       CHOCH dentro de una ventana corta después de la vela de barrido (el FVG es
       parte del impulso inicial de reversión, no depende de cuánto tarde en
       llegar la confirmación — BrokenIndex puede quedar muy lejos de la vela
       de barrido y buscar hasta ahí encuentra FVGs de movimientos posteriores
       no relacionados con este barrido).
  6.   Entrada = punto medio del FVG (Top+Bottom)/2.
  7.   Stop = low (long) / high (short) de la vela de barrido.

Nota: los DataFrame que devuelve `smartmoneyconcepts` siempre usan un índice
posicional 0..n-1, sin importar el índice del `ohlc` de entrada — por eso todo
este módulo trabaja en posiciones enteras y solo usa `.iloc` sobre `ohlc`.
"""

import pandas as pd

VENTANA_FVG_VELAS = 5  # ponytail: ventana fija corta; ajustar si el timeframe lo pide


def detectar_setups(ohlc: pd.DataFrame, resultado_motor: dict, ventana_fvg: int = VENTANA_FVG_VELAS) -> pd.DataFrame:
    """Encuentra setups OB+FVG confirmados. Devuelve un DataFrame, una fila por setup."""
    estructura = resultado_motor["estructura"]
    fvg = resultado_motor["fvg"]

    filas = []
    for i in estructura.index[estructura["CHOCH"].notna()]:
        direccion = int(estructura.at[i, "CHOCH"])
        roto_en = estructura.at[i, "BrokenIndex"]
        if pd.isna(roto_en):
            continue  # CHoCH todavía sin confirmar, no es un setup operable
        roto_en = int(roto_en)

        ventana = fvg.iloc[i : min(i + ventana_fvg, roto_en) + 1]
        candidatos = ventana.index[ventana["FVG"] == direccion]
        if len(candidatos) == 0:
            continue
        j = candidatos[0]

        entrada = (fvg.at[j, "Top"] + fvg.at[j, "Bottom"]) / 2
        stop = ohlc["low"].iloc[i] if direccion == 1 else ohlc["high"].iloc[i]
        # geometría inválida (p.ej. el precio ya siguió más allá del FVG antes de
        # que se formara) — no es un setup operable, se descarta en vez de reportarlo
        if (direccion == 1 and stop >= entrada) or (direccion == -1 and stop <= entrada):
            continue

        filas.append(
            {
                "indice_barrido": i,
                "indice_confirmacion": roto_en,
                "indice_fvg": j,
                "direccion": "long" if direccion == 1 else "short",
                "entrada": entrada,
                "stop": stop,
            }
        )

    return pd.DataFrame(
        filas,
        columns=["indice_barrido", "indice_confirmacion", "indice_fvg", "direccion", "entrada", "stop"],
    )


def demo() -> None:
    from .motor import analizar

    import numpy as np

    # ponytail: seed fija a propósito — un random-walk típico casi nunca produce
    # un FVG del mismo signo tan cerca del barrido (mediana observada: ~30 velas
    # de distancia, ver bitácora de esta sesión), así que un seed al azar deja
    # el chequeo de geometría sin ejercitar en la mayoría de las corridas. Este
    # seed sí genera al menos un setup real de punta a punta.
    rng = np.random.default_rng(190)
    n = 500
    # Camino con tendencia + reversiones marcadas para forzar CHoCH/FVG reales,
    # en vez de puro ruido (donde el setup casi nunca aparece).
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

    resultado = analizar(ohlc, swing_length=8)
    setups = detectar_setups(ohlc, resultado)
    assert list(setups.columns) == [
        "indice_barrido",
        "indice_confirmacion",
        "indice_fvg",
        "direccion",
        "entrada",
        "stop",
    ]
    assert (setups["direccion"].isin(["long", "short"])).all()
    assert len(setups) >= 1, "el seed fijo debe producir al menos un setup"
    # el stop siempre debe quedar del lado correcto de la entrada
    largos = setups[setups["direccion"] == "long"]
    cortos = setups[setups["direccion"] == "short"]
    assert (largos["stop"] < largos["entrada"]).all()
    assert (cortos["stop"] > cortos["entrada"]).all()
    print(f"setup_ob_fvg.demo() OK — {len(setups)} setups detectados sobre {n} velas")
    print(setups.head())


if __name__ == "__main__":
    demo()
