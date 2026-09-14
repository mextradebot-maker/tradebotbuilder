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

Order Block y Liquidez — activados 14 sep 2026 (spec
docs/panel-alumnos-catalogo-indicadores-spec.md §4: se calculaban en
`motor.analizar()` y nadie los leía). Primer intento: exigirlos como filtro
obligatorio (setup solo cuenta si AMBOS confluyen). Descartado tras probar con
datos reales — XAUUSD H1 2022-2024 (11,805 velas): de 7 candidatos CHOCH+FVG,
CERO tenían Order Block superpuesto y CERO tenían el barrido a menos de 12 velas
de un Swept de liquidez real. No es un bug de índices (hay 23 Order Blocks y 56
barridos de liquidez reales en el período — simplemente casi nunca caen sobre el
candidato exacto), es que exigir los 4 a la vez como puerta dura deja el motor en
cero señales — justo el tipo de sobre-filtrado que las "3 reglas de oro" de
backtesting/backtest.py ya advierten evitar.

Diseño final: Order Block y Liquidez se calculan y se anotan como columnas
booleanas en cada setup (`order_block_confluente`, `liquidez_confluente`) — el
alumno ve cuántas confluencias extra respalda cada setup, pero CHOCH+FVG solo
sigue siendo suficiente para que un setup exista. Esto sí los "activa" (se leen,
se muestran) sin volver el producto inservible.

Nota: los DataFrame que devuelve `smartmoneyconcepts` siempre usan un índice
posicional 0..n-1, sin importar el índice del `ohlc` de entrada — por eso todo
este módulo trabaja en posiciones enteras y solo usa `.iloc` sobre `ohlc`.
"""

import pandas as pd

VENTANA_FVG_VELAS = 5  # ponytail: ventana fija corta; ajustar si el timeframe lo pide

# ponytail: ventana de tolerancia calibrada contra datos reales (XAUUSD H1
# 2022-2024) — exigir Swept == barrido exacto (tolerancia 0) daba 0 coincidencias
# sobre 2 años reales; a partir de ~12 velas empieza a encontrar alguna. Queda
# como anotación informativa, no como filtro, así que un valor generoso no
# infla resultados — solo decide qué tan seguido se muestra la bandera.
TOLERANCIA_LIQUIDEZ_VELAS = 8


def _hay_order_block_confluente(order_blocks: pd.DataFrame, direccion: int, fvg_top: float, fvg_bottom: float, hasta_indice: int) -> bool:
    """¿Existe un Order Block de la misma dirección, ya formado antes de
    `hasta_indice`, todavía sin mitigar a esa altura, cuyo rango [Bottom, Top]
    se superpone con el rango del FVG (no solo con el punto medio)?"""
    candidatos = order_blocks[
        (order_blocks.index <= hasta_indice)
        & (order_blocks["OB"] == direccion)
        & (order_blocks["Bottom"] <= fvg_top)
        & (order_blocks["Top"] >= fvg_bottom)
    ]
    if candidatos.empty:
        return False
    sin_mitigar = candidatos["MitigatedIndex"].isna() | (candidatos["MitigatedIndex"] >= hasta_indice)
    return bool(sin_mitigar.any())


def _hay_liquidez_confluente(liquidez: pd.DataFrame, direccion: int, indice_barrido: int, tolerancia: int = TOLERANCIA_LIQUIDEZ_VELAS) -> bool:
    """¿El barrido en `indice_barrido` coincide (dentro de `tolerancia` velas)
    con un barrido de liquidez real detectado por smc.liquidity()? Un long
    barre liquidez bajista (mínimos agrupados, Liquidity=-1); un short barre
    liquidez alcista (máximos agrupados, Liquidity=1). `Swept==0` es la
    convención de la librería para "nunca se barrió", no un índice real."""
    lado_esperado = -1 if direccion == 1 else 1
    coincide = (
        ((liquidez["Swept"] - indice_barrido).abs() <= tolerancia)
        & (liquidez["Liquidity"] == lado_esperado)
        & (liquidez["Swept"] > 0)
    )
    return bool(coincide.any())


def detectar_setups(ohlc: pd.DataFrame, resultado_motor: dict, ventana_fvg: int = VENTANA_FVG_VELAS) -> pd.DataFrame:
    """Encuentra setups OB+FVG confirmados. Devuelve un DataFrame, una fila por
    setup, con dos columnas informativas nuevas: `order_block_confluente` y
    `liquidez_confluente` (ver docstring del módulo — no filtran, solo anotan)."""
    estructura = resultado_motor["estructura"]
    fvg = resultado_motor["fvg"]
    order_blocks = resultado_motor["order_blocks"]
    liquidez = resultado_motor["liquidez"]

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
                "order_block_confluente": _hay_order_block_confluente(order_blocks, direccion, fvg.at[j, "Top"], fvg.at[j, "Bottom"], roto_en),
                "liquidez_confluente": _hay_liquidez_confluente(liquidez, direccion, i),
            }
        )

    return pd.DataFrame(
        filas,
        columns=[
            "indice_barrido",
            "indice_confirmacion",
            "indice_fvg",
            "direccion",
            "entrada",
            "stop",
            "order_block_confluente",
            "liquidez_confluente",
        ],
    )


def _resultado_motor_de_prueba(*, con_ob: bool = True, con_liquidez: bool = True) -> tuple:
    """ohlc + resultado_motor de 10 velas, 100% controlado — para probar la
    mecánica de las anotaciones de confluencia sin depender de que un random
    walk las produzca por suerte. Columnas iguales a las que devuelven
    smc.bos_choch/fvg/ob/liquidity (verificadas leyendo
    .venv/Lib/site-packages/smartmoneyconcepts/smc.py)."""
    n = 10
    ohlc = pd.DataFrame({"open": [100.0] * n, "high": [110.0] * n, "low": [90.0] * n, "close": [100.0] * n, "volume": [100.0] * n})

    estructura = pd.DataFrame(index=range(n), columns=["CHOCH", "BrokenIndex"], dtype="float64")
    estructura.loc[3, ["CHOCH", "BrokenIndex"]] = [1, 6]  # barrido en la vela 3 (long), confirmado en la 6

    fvg = pd.DataFrame(index=range(n), columns=["FVG", "Top", "Bottom"], dtype="float64")
    fvg.loc[4, ["FVG", "Top", "Bottom"]] = [1, 105, 103]  # entrada = (105+103)/2 = 104

    order_blocks = pd.DataFrame(index=range(n), columns=["OB", "Top", "Bottom", "MitigatedIndex"], dtype="float64")
    if con_ob:
        order_blocks.loc[2, ["OB", "Top", "Bottom", "MitigatedIndex"]] = [1, 106, 102, float("nan")]  # se superpone con [103,105], sin mitigar

    liquidez = pd.DataFrame(index=range(n), columns=["Liquidity", "Swept"], dtype="float64")
    if con_liquidez:
        liquidez.loc[0, ["Liquidity", "Swept"]] = [-1, 3]  # pool bajista barrido justo en la vela 3

    return ohlc, {"estructura": estructura, "fvg": fvg, "order_blocks": order_blocks, "liquidez": liquidez}


def demo() -> None:
    # Caso base: CHOCH+FVG alcanza para que el setup exista, con ambas
    # confluencias extra presentes -> las dos banderas quedan en True.
    ohlc, resultado_completo = _resultado_motor_de_prueba(con_ob=True, con_liquidez=True)
    setups = detectar_setups(ohlc, resultado_completo)
    assert list(setups.columns) == [
        "indice_barrido", "indice_confirmacion", "indice_fvg", "direccion", "entrada", "stop",
        "order_block_confluente", "liquidez_confluente",
    ]
    assert len(setups) == 1, f"CHOCH+FVG debía producir 1 setup, salieron {len(setups)}"
    fila = setups.iloc[0]
    assert fila["direccion"] == "long" and fila["entrada"] == 104.0 and fila["stop"] == 90.0
    assert bool(fila["order_block_confluente"]) is True
    assert bool(fila["liquidez_confluente"]) is True

    # Quitar el Order Block -> el setup SIGUE existiendo (no es un filtro), pero
    # con la bandera en False. Igual para liquidez.
    ohlc2, resultado_sin_ob = _resultado_motor_de_prueba(con_ob=False, con_liquidez=True)
    setups_sin_ob = detectar_setups(ohlc2, resultado_sin_ob)
    assert len(setups_sin_ob) == 1, "sin Order Block el setup debe seguir existiendo (no es un filtro)"
    assert bool(setups_sin_ob.iloc[0]["order_block_confluente"]) is False
    assert bool(setups_sin_ob.iloc[0]["liquidez_confluente"]) is True

    ohlc3, resultado_sin_liquidez = _resultado_motor_de_prueba(con_ob=True, con_liquidez=False)
    setups_sin_liq = detectar_setups(ohlc3, resultado_sin_liquidez)
    assert len(setups_sin_liq) == 1, "sin liquidez el setup debe seguir existiendo (no es un filtro)"
    assert bool(setups_sin_liq.iloc[0]["liquidez_confluente"]) is False

    print(
        "setup_ob_fvg.demo() OK — CHOCH+FVG sigue produciendo el setup; "
        "Order Block y Liquidez quedan anotados como confluencia extra, no como filtro"
    )


if __name__ == "__main__":
    demo()
