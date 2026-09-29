"""Detector SMC v2 — dos tipos de apertura (spec 2026-09-28-motor-smc-v2-design.md).

  reversion:    barrido de liquidez externa + CHoCH + FVG (L20). Reglas R1-R6.
  continuacion: BOS a favor tras un retroceso; entrada en el punto medio de la zona
                de mitigación (2-3 últimas velas contrarias del retroceso, L52). Reglas R3-R6.

Anti-anticipación: `smc.bos_choch` marca el evento en un swing que solo se confirma cuando
existe el swing siguiente + `swing_length` velas, y la ruptura en `BrokenIndex`. El setup
"existe" en `indice_conocido = max(BrokenIndex, siguiente_swing + swing_length)`; toda regla y
el backtest parten de ahí, nunca antes.

El detector viejo (`setup_ob_fvg.detectar_setups`) sigue intacto: lo usan los EA, T-01 y el
modo sombra de la Etapa 2 necesita comparar los dos.
"""

import pandas as pd

from . import reglas as R
from .indicadores import divergencia, macd, rsi
from .motor import analizar
from .setup_ob_fvg import VENTANA_FVG_VELAS, _hay_liquidez_confluente, _hay_order_block_confluente

MAX_VELAS_ZONA = 3
BUSQUEDA_ZONA_VELAS = 3  # la zona debe terminar en el swing del retroceso o hasta 2 velas antes

COLUMNAS = [
    "tipo", "direccion", "indice_barrido", "indice_confirmacion", "indice_conocido", "indice_zona",
    "entrada", "stop", "zona_extremo", "tp", "valido", "razon_descarte", "reglas",
    "ema_sesgo", "cruce_20_50", "multiplo_confirmacion", "divergencia_rsi", "divergencia_macd",
    "order_block_confluente", "liquidez_confluente",
]


def _indice_conocido(swings: pd.DataFrame, i: int, k: int, swing_length: int, n: int) -> int | None:
    siguientes = swings.index[(swings.index > i) & swings["HighLow"].notna()]
    if len(siguientes) == 0:
        return None
    conocido = max(k, int(siguientes[0]) + swing_length)
    return conocido if conocido < n else None


def candidatos_reversion(ohlc: pd.DataFrame, res: dict, swing_length: int) -> list[dict]:
    estructura, fvg, swings = res["estructura"], res["fvg"], res["swings"]
    salida = []
    for i in estructura.index[estructura["CHOCH"].notna() & (estructura["CHOCH"] != 0)]:
        d = int(estructura.at[i, "CHOCH"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        conocido = _indice_conocido(swings, i, k, swing_length, len(ohlc))
        if conocido is None:
            continue
        ventana = fvg.iloc[i : min(i + VENTANA_FVG_VELAS, k) + 1]
        js = ventana.index[ventana["FVG"] == d]
        if len(js) == 0:
            continue
        j = int(js[0])
        top, bottom = float(fvg.at[j, "Top"]), float(fvg.at[j, "Bottom"])
        entrada = (top + bottom) / 2
        stop = float(ohlc["low"].iloc[i] if d == 1 else ohlc["high"].iloc[i])
        if (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "reversion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": int(i), "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": j,
            "entrada": entrada, "stop": stop, "zona_extremo": bottom if d == 1 else top,
            "fvg_top": top, "fvg_bottom": bottom,
        })
    return salida


def candidatos_continuacion(ohlc: pd.DataFrame, res: dict, swing_length: int) -> list[dict]:
    estructura, swings = res["estructura"], res["swings"]
    salida = []
    for i in estructura.index[estructura["BOS"].notna() & (estructura["BOS"] != 0)]:
        d = int(estructura.at[i, "BOS"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        conocido = _indice_conocido(swings, i, k, swing_length, len(ohlc))
        if conocido is None:
            continue
        contraria = (ohlc["close"] < ohlc["open"]) if d == 1 else (ohlc["close"] > ohlc["open"])
        fin = next((p for p in range(i, max(i - BUSQUEDA_ZONA_VELAS, -1), -1) if contraria.iloc[p]), None)
        if fin is None:
            continue
        ini = fin
        while ini - 1 >= 0 and contraria.iloc[ini - 1] and fin - ini + 1 < MAX_VELAS_ZONA:
            ini -= 1
        zona = ohlc.iloc[ini : fin + 1]
        alto, bajo = float(zona["high"].max()), float(zona["low"].min())
        entrada = (alto + bajo) / 2
        # en smc.bos_choch el BOS se marca en el swing del retroceso (HL alcista / LH bajista)
        stop = float(ohlc["low"].iloc[i] if d == 1 else ohlc["high"].iloc[i])
        if (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "continuacion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": int(i), "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": ini,
            "entrada": entrada, "stop": stop, "zona_extremo": bajo if d == 1 else alto,
        })
    return salida


def _multiplo(ohlc: pd.DataFrame, k: int) -> float | None:
    if k < R.VENTANA_VOLUMEN:
        return None
    media = ohlc["volume"].iloc[k - R.VENTANA_VOLUMEN : k].mean()
    return round(float(ohlc["volume"].iloc[k] / media), 2) if media > 0 else None


def _divergencias(ohlc: pd.DataFrame, swings: pd.DataFrame, i: int, direccion: str) -> tuple:
    lado = -1 if direccion == "long" else 1
    previos = swings.index[(swings["HighLow"] == lado) & (swings.index < i)]
    if len(previos) == 0:
        return None, None
    p = int(previos[-1])
    return (divergencia(ohlc, rsi(ohlc), i, p, direccion),
            divergencia(ohlc, macd(ohlc)["MACD"], i, p, direccion))


def evaluar(c: dict, ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str,
            res: dict, swing_length: int) -> dict:
    d, k, conocido = c["direccion"], c["indice_confirmacion"], c["indice_conocido"]
    mayor = R.cortar_mayor(ohlc_mayor, ohlc.index[conocido] + R.DURACION_VELA[vela], vela_mayor)
    es_reversion = c["tipo"] == "reversion"
    reglas = {
        "R1": R.r1_volumen(ohlc, c["indice_barrido"], vela) if es_reversion else R.NO_APLICA,
        "R2": R.r2_fvg(ohlc, c["fvg_top"], c["fvg_bottom"], c["indice_zona"]) if es_reversion else R.NO_APLICA,
        "R3": R.r3_ema(ohlc, k, d),
        "R4": R.r4_estructura(mayor, d, vela_mayor),
        "R5": R.r5_descuento_premium(mayor, c["entrada"], d, vela_mayor),
        "R6": R.r6_tp_liquidez(ohlc, res["swings"], conocido, c["entrada"], c["stop"], d, swing_length),
    }
    fallas = [f"{nombre}: {r['razon']}" for nombre, r in reglas.items() if r["cumple"] is False]
    div_rsi, div_macd = _divergencias(ohlc, res["swings"], c["indice_barrido"], d) if es_reversion else (None, None)
    direccion_num = 1 if d == "long" else -1
    return {
        **{col: c[col] for col in COLUMNAS if col in c},
        "tp": reglas["R6"]["dato"]["tp"] if isinstance(reglas["R6"]["dato"], dict) else None,
        "valido": not fallas,
        "razon_descarte": "; ".join(fallas),
        "reglas": reglas,
        "ema_sesgo": reglas["R3"]["dato"],
        "cruce_20_50": R.cruce_20_50(ohlc, k, d),
        "multiplo_confirmacion": _multiplo(ohlc, k),
        "divergencia_rsi": div_rsi,
        "divergencia_macd": div_macd,
        "order_block_confluente": (_hay_order_block_confluente(res["order_blocks"], direccion_num, c["fvg_top"], c["fvg_bottom"], k)
                                   if es_reversion else None),
        "liquidez_confluente": _hay_liquidez_confluente(res["liquidez"], direccion_num, c["indice_barrido"]) if es_reversion else None,
    }


def detectar_setups_v2(ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str) -> pd.DataFrame:
    swing_length = R.SWING_LENGTH_POR_VELA[vela]
    res = analizar(ohlc, swing_length=swing_length)
    candidatos = candidatos_reversion(ohlc, res, swing_length) + candidatos_continuacion(ohlc, res, swing_length)
    filas = [evaluar(c, ohlc, ohlc_mayor, vela, vela_mayor, res, swing_length) for c in candidatos]
    return pd.DataFrame(filas, columns=COLUMNAS).sort_values("indice_conocido", ignore_index=True)


def embudo(setups: pd.DataFrame) -> dict:
    """Cuántos candidatos hay por tipo, cuántos elimina cada regla y cuántos quedan válidos."""
    salida = {}
    for tipo in ("reversion", "continuacion"):
        sub = setups[setups["tipo"] == tipo]
        salida[tipo] = {
            "candidatos": int(len(sub)),
            "descartados_por_regla": {
                regla: int(sum(1 for r in sub["reglas"] if r[regla]["cumple"] is False))
                for regla in ("R1", "R2", "R3", "R4", "R5", "R6")
            },
            "validos": int(sub["valido"].sum()) if len(sub) else 0,
        }
    return salida


# ── pruebas ──────────────────────────────────────────────────────────────────

def _res_prueba(n: int = 40) -> tuple:
    """ohlc + resultado de `analizar` 100% controlado (mismas columnas que smartmoneyconcepts)."""
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    ohlc = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100.0}, index=idx)
    nan = float("nan")
    swings = pd.DataFrame({"HighLow": [nan] * n, "Level": [nan] * n})
    estructura = pd.DataFrame({"BOS": [nan] * n, "CHOCH": [nan] * n, "Level": [nan] * n, "BrokenIndex": [nan] * n})
    fvg = pd.DataFrame({"FVG": [nan] * n, "Top": [nan] * n, "Bottom": [nan] * n})
    order_blocks = pd.DataFrame({"OB": [nan] * n, "Top": [nan] * n, "Bottom": [nan] * n, "MitigatedIndex": [nan] * n})
    liquidez = pd.DataFrame({"Liquidity": [nan] * n, "Swept": [nan] * n})
    return ohlc, {"swings": swings, "estructura": estructura, "fvg": fvg, "order_blocks": order_blocks, "liquidez": liquidez}


def demo() -> None:
    # Reversión: CHoCH alcista en la vela 20, ruptura en 24, siguiente swing en 26 (sl=3 -> conocido 29)
    ohlc, res = _res_prueba()
    ohlc.iloc[20, ohlc.columns.get_loc("low")] = 90.0
    res["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res["swings"].loc[26, ["HighLow", "Level"]] = [1, 110.0]
    res["estructura"].loc[20, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    rev = candidatos_reversion(ohlc, res, swing_length=3)
    assert len(rev) == 1, rev
    assert rev[0]["entrada"] == 104.0 and rev[0]["stop"] == 90.0
    assert rev[0]["zona_extremo"] == 103.0 and rev[0]["indice_conocido"] == 29
    # sin swing siguiente el CHoCH todavía no se conoce -> no hay candidato
    ohlc_b, res_b = _res_prueba()
    res_b["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res_b["estructura"].loc[20, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res_b["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    assert candidatos_reversion(ohlc_b, res_b, swing_length=3) == []

    # Continuación: BOS alcista en el swing 20 (mínimo del retroceso), zona = velas bajistas 18-20
    ohlc_c, res_c = _res_prueba()
    for p, fila in {17: (106, 107, 104, 105), 18: (104, 105, 101, 102), 19: (102, 103, 99, 100), 20: (100, 101, 95, 97)}.items():
        ohlc_c.iloc[p, :4] = fila
    res_c["swings"].loc[20, ["HighLow", "Level"]] = [-1, 95.0]
    res_c["swings"].loc[26, ["HighLow", "Level"]] = [1, 115.0]
    res_c["estructura"].loc[20, ["BOS", "BrokenIndex"]] = [1, 24]
    cont = candidatos_continuacion(ohlc_c, res_c, swing_length=3)
    assert len(cont) == 1, cont
    assert cont[0]["indice_zona"] == 18  # máximo 3 velas: 18, 19, 20 (la 17 queda fuera)
    assert cont[0]["entrada"] == 100.0 and cont[0]["stop"] == 95.0 and cont[0]["zona_extremo"] == 95.0

    # Evaluación: R1/R2 no aplican a continuación; anti-anticipación con la temporalidad mayor
    mayor_pasado = R.zigzag(R.ALCISTA, inicio="2025-12-20 00:00")  # cierra antes del setup
    mayor_futuro = R.zigzag(R.ALCISTA, inicio="2026-01-06 00:00")  # abre después del setup
    ev = evaluar(cont[0], ohlc_c, mayor_pasado, "15m", "1H", res_c, 3)
    assert ev["reglas"]["R1"]["cumple"] is None and ev["reglas"]["R2"]["cumple"] is None
    assert ev["reglas"]["R4"]["cumple"] is True, ev["reglas"]["R4"]
    ev_futuro = evaluar(cont[0], ohlc_c, mayor_futuro, "15m", "1H", res_c, 3)
    assert ev_futuro["reglas"]["R4"]["cumple"] is False
    assert "sin velas cerradas" in ev_futuro["reglas"]["R4"]["razon"]
    assert ev_futuro["valido"] is False and "R4" in ev_futuro["razon_descarte"]
    assert set(ev) == set(COLUMNAS)

    e = embudo(pd.DataFrame([ev, ev_futuro], columns=COLUMNAS))
    assert e["continuacion"]["candidatos"] == 2 and e["continuacion"]["descartados_por_regla"]["R4"] == 1
    assert e["reversion"]["candidatos"] == 0
    print("setups_v2.demo() OK")


if __name__ == "__main__":
    demo()
