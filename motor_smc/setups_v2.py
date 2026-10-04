"""Detector SMC v2 — dos tipos de apertura (spec 2026-09-28-motor-smc-v2-design.md).

  reversion:    barrido de liquidez externa + CHoCH + FVG (L20). Reglas R1-R6.
  continuacion: BOS a favor tras un retroceso; entrada en el punto medio de la zona
                de mitigación (2-3 últimas velas contrarias del retroceso, L52). Reglas R3-R6.

Anclaje (verificado contra smartmoneyconcepts.smc.bos_choch): el evento BOS/CHoCH se marca en
`i` = el swing cuyo nivel se rompe (Level = nivel de `i`, ruptura en `BrokenIndex`). El patrón
de 4 swings se completa con los dos swings posteriores a `i`:
  s = primer swing después de `i`: el barrido (reversión) o el extremo del retroceso (continuación);
      ahí van el stop, R1, las divergencias y la zona de mitigación.
  t = segundo swing después de `i`: el que la librería necesita para reconocer el patrón.

Anti-anticipación: `t` solo se confirma `swing_length` velas después, así que el setup "existe"
en `indice_conocido = max(BrokenIndex, t + swing_length)`; toda regla y el backtest parten de
ahí, nunca antes.

El detector viejo (`setup_ob_fvg.detectar_setups`) sigue intacto: lo usan los EA, T-01 y el
modo sombra de la Etapa 2 necesita comparar los dos.
"""

import pandas as pd
from smartmoneyconcepts import smc

from . import reglas as R
from .indicadores import divergencia, macd, rsi
from .motor import analizar
from .setup_ob_fvg import VENTANA_FVG_VELAS, _hay_liquidez_confluente, _hay_order_block_confluente

MAX_VELAS_ZONA = 3
BUSQUEDA_ZONA_VELAS = 3  # la zona debe terminar en el swing del retroceso (s) o hasta 2 velas antes

COLUMNAS = [
    "tipo", "direccion", "indice_barrido", "indice_confirmacion", "indice_conocido", "indice_zona",
    "entrada", "stop", "zona_extremo", "tp", "valido", "razon_descarte", "reglas",
    "ema_sesgo", "cruce_20_50", "multiplo_confirmacion", "divergencia_rsi", "divergencia_macd",
    "order_block_confluente", "liquidez_confluente",
]


def _anclaje(swings: pd.DataFrame, i: int, k: int, swing_length: int, n: int) -> tuple[int, int] | None:
    """(s, indice_conocido) del evento marcado en el swing `i`; None si todavía no se conoce."""
    siguientes = swings.index[(swings.index > i) & swings["HighLow"].notna()]
    if len(siguientes) < 2:
        return None
    conocido = max(k, int(siguientes[1]) + swing_length)
    return (int(siguientes[0]), conocido) if conocido < n else None


def _stop_con_margen(ohlc: pd.DataFrame, atr_serie: pd.Series, ini: int, s: int, d: int) -> float | None:
    """§10.3: el stop va MÁS ALLÁ de todo el clúster (velas ini..s), con 0.5 x ATR(14) medido en s-1
    (solo velas previas). None si no hay ATR todavía: el candidato se descarta."""
    margen = atr_serie.iloc[s - 1] if s >= 1 else float("nan")
    if pd.isna(margen):
        return None
    margen = R.FACTOR_ATR_STOP * float(margen)
    if d == 1:
        return float(ohlc["low"].iloc[ini : s + 1].min()) - margen
    return float(ohlc["high"].iloc[ini : s + 1].max()) + margen


def candidatos_reversion(ohlc: pd.DataFrame, res: dict, swing_length: int, atr_serie: pd.Series | None = None) -> list[dict]:
    estructura, fvg, swings = res["estructura"], res["fvg"], res["swings"]
    atr_serie = R.atr(ohlc) if atr_serie is None else atr_serie
    salida = []
    for i in estructura.index[estructura["CHOCH"].notna() & (estructura["CHOCH"] != 0)]:
        d = int(estructura.at[i, "CHOCH"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        ancla = _anclaje(swings, i, k, swing_length, len(ohlc))
        if ancla is None:
            continue
        s, conocido = ancla  # s = swing del barrido
        ventana = fvg.iloc[s : min(s + VENTANA_FVG_VELAS, k) + 1]
        # el FVG en j (vela central) solo está completo tras la vela j+1: debe ser <= conocido
        js = ventana.index[(ventana["FVG"] == d) & (ventana.index + 1 <= conocido)]
        if len(js) == 0:
            continue
        j = int(js[0])
        top, bottom = float(fvg.at[j, "Top"]), float(fvg.at[j, "Bottom"])
        entrada = (top + bottom) / 2
        stop = _stop_con_margen(ohlc, atr_serie, s, s, d)  # clúster del FVG = la vela del barrido
        if stop is None or (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "reversion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": s, "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": j,
            "entrada": entrada, "stop": stop, "zona_extremo": bottom if d == 1 else top,
            "fvg_top": top, "fvg_bottom": bottom,
        })
    return salida


def candidatos_continuacion(ohlc: pd.DataFrame, res: dict, swing_length: int, atr_serie: pd.Series | None = None) -> list[dict]:
    estructura, swings = res["estructura"], res["swings"]
    atr_serie = R.atr(ohlc) if atr_serie is None else atr_serie
    salida = []
    for i in estructura.index[estructura["BOS"].notna() & (estructura["BOS"] != 0)]:
        d = int(estructura.at[i, "BOS"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        ancla = _anclaje(swings, i, k, swing_length, len(ohlc))
        if ancla is None:
            continue
        s, conocido = ancla  # s = extremo del retroceso (HL alcista / LH bajista)
        contraria = (ohlc["close"] < ohlc["open"]) if d == 1 else (ohlc["close"] > ohlc["open"])
        fin = next((p for p in range(s, max(s - BUSQUEDA_ZONA_VELAS, -1), -1) if contraria.iloc[p]), None)
        if fin is None:
            continue
        ini = fin
        while ini - 1 >= 0 and contraria.iloc[ini - 1] and fin - ini + 1 < MAX_VELAS_ZONA:
            ini -= 1
        zona = ohlc.iloc[ini : fin + 1]
        alto, bajo = float(zona["high"].max()), float(zona["low"].min())
        entrada = (alto + bajo) / 2
        stop = _stop_con_margen(ohlc, atr_serie, ini, s, d)
        if stop is None or (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "continuacion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": s, "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": ini,
            "entrada": entrada, "stop": stop, "zona_extremo": bajo if d == 1 else alto,
        })
    return salida


def _multiplo(ohlc: pd.DataFrame, k: int) -> float | None:
    if k < R.VENTANA_VOLUMEN:
        return None
    media = ohlc["volume"].iloc[k - R.VENTANA_VOLUMEN : k].mean()
    return round(float(ohlc["volume"].iloc[k] / media), 2) if media > 0 else None


def _divergencias(ohlc: pd.DataFrame, swings: pd.DataFrame, i: int, direccion: str,
                  rsi_serie: pd.Series | None = None, macd_serie: pd.Series | None = None) -> tuple:
    lado = -1 if direccion == "long" else 1
    previos = swings.index[(swings["HighLow"] == lado) & (swings.index < i)]
    if len(previos) == 0:
        return None, None
    p = int(previos[-1])
    rsi_serie = rsi(ohlc) if rsi_serie is None else rsi_serie
    macd_serie = macd(ohlc)["MACD"] if macd_serie is None else macd_serie
    return divergencia(ohlc, rsi_serie, i, p, direccion), divergencia(ohlc, macd_serie, i, p, direccion)


def _precalculo(ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela_mayor: str) -> dict:
    """Lo que no depende del candidato, una vez por serie (todo causal: el valor en k solo usa velas
    <= k), el contexto R4/R5 de la temporalidad mayor para cualquier recorte y la memoria de los
    swings recortados (por conocido)."""
    return {"atr": R.atr(ohlc), "emas": R.emas(ohlc), "rsi": rsi(ohlc), "macd": macd(ohlc)["MACD"],
            "mayor": R.ContextoMayor(ohlc_mayor, vela_mayor), "swings": {}}


def evaluar(c: dict, ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str,
            res: dict, swing_length: int, pre: dict | None = None) -> dict:
    """`pre` = _precalculo(ohlc, ohlc_mayor, vela_mayor) compartido entre candidatos; sin él se calcula
    para este candidato."""
    pre = _precalculo(ohlc, ohlc_mayor, vela_mayor) if pre is None else pre
    d, k, conocido = c["direccion"], c["indice_confirmacion"], c["indice_conocido"]
    mayor = R.cortar_mayor(ohlc_mayor, ohlc.index[conocido] + R.DURACION_VELA[vela], vela_mayor)
    # los conjuntos "cierre <= t" son anidados: el recorte es el prefijo de len(mayor) velas
    ctx = None if mayor.empty else pre["mayor"].en(len(mayor))
    es_reversion = c["tipo"] == "reversion"
    # swing_highs_lows borra swings consecutivos del mismo tipo mirando swings posteriores: para R6 y
    # las divergencias se recalculan solo con lo visible en indice_conocido (el recorte empieza en 0,
    # así que los índices posicionales coinciden). Memo de UNA entrada (el ultimo conocido): guardar uno por
    # conocido crece como candidatos x velas (15m de 3 años: ~3 GB) y tumbaba el servicio.
    swings = pre["swings"].get(conocido)
    if swings is None:
        pre["swings"].clear()
        swings = pre["swings"][conocido] = smc.swing_highs_lows(ohlc.iloc[: conocido + 1], swing_length=swing_length)
    reglas = {
        "R1": R.r1_volumen(ohlc, c["indice_barrido"], vela) if es_reversion else R.NO_APLICA,
        "R2": R.r2_fvg(ohlc, c["fvg_top"], c["fvg_bottom"], c["indice_zona"], pre["atr"]) if es_reversion else R.NO_APLICA,
        "R3": R.r3_ema(ohlc, k, d, pre["emas"]),
        "R4": R.r4_estructura(mayor, d, vela_mayor, ctx),
        "R5": R.r5_descuento_premium(mayor, c["entrada"], d, vela_mayor, ctx),
        "R6": R.r6_tp_liquidez(ohlc, swings, conocido, c["entrada"], c["stop"], d, swing_length),
    }
    fallas = [f"{nombre}: {r['razon']}" for nombre, r in reglas.items() if r["cumple"] is False]
    div_rsi, div_macd = (_divergencias(ohlc, swings, c["indice_barrido"], d, pre["rsi"], pre["macd"])
                         if es_reversion else (None, None))
    direccion_num = 1 if d == "long" else -1
    liq = res["liquidez"]
    liq = liq[liq["Swept"] <= conocido]  # sin barridos posteriores a indice_conocido
    return {
        **{col: c[col] for col in COLUMNAS if col in c},
        "tp": reglas["R6"]["dato"]["tp"] if isinstance(reglas["R6"]["dato"], dict) else None,
        "valido": not fallas,
        "razon_descarte": "; ".join(fallas),
        "reglas": reglas,
        "ema_sesgo": reglas["R3"]["dato"],
        "cruce_20_50": R.cruce_20_50(ohlc, k, d, pre["emas"]),
        "multiplo_confirmacion": _multiplo(ohlc, k),
        "divergencia_rsi": div_rsi,
        "divergencia_macd": div_macd,
        "order_block_confluente": (_hay_order_block_confluente(res["order_blocks"], direccion_num, c["fvg_top"], c["fvg_bottom"], k)
                                   if es_reversion else None),
        "liquidez_confluente": _hay_liquidez_confluente(liq, direccion_num, c["indice_barrido"]) if es_reversion else None,
    }


def detectar_setups_v2(ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str) -> pd.DataFrame:
    swing_length = R.SWING_LENGTH_POR_VELA[vela]
    res = analizar(ohlc, swing_length=swing_length)
    pre = _precalculo(ohlc, ohlc_mayor, vela_mayor)
    candidatos = (candidatos_reversion(ohlc, res, swing_length, pre["atr"])
                  + candidatos_continuacion(ohlc, res, swing_length, pre["atr"]))
    filas = [None] * len(candidatos)
    # en orden de indice_conocido para que el memo de swings sirva; las filas vuelven a su orden original
    for i in sorted(range(len(candidatos)), key=lambda i: candidatos[i]["indice_conocido"]):
        filas[i] = evaluar(candidatos[i], ohlc, ohlc_mayor, vela, vela_mayor, res, swing_length, pre)
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

# swl 5: swings 23 H, 37 L, 61 H, 91 L, 111 H, 125 L, 149 H... -> CHoCH -1 en 37 (rompe en 86), BOS +1 en 111 (138)
TRAMOS_EJEMPLO = [(100, 120, 24), (120, 110, 14), (110, 135, 24), (135, 104, 30), (104, 125, 20),
                  (125, 111, 14), (111, 140, 24), (140, 128, 14), (128, 150, 24)]

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
    # Reversión: CHoCH alcista marcado en el máximo roto i=16, barrido s=20, t=26, ruptura en 24
    # (sl=3 -> conocido = max(24, 26 + 3) = 29)
    ohlc, res = _res_prueba()
    ohlc.iloc[20, ohlc.columns.get_loc("low")] = 90.0
    res["swings"].loc[16, ["HighLow", "Level"]] = [1, 102.0]
    res["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res["swings"].loc[26, ["HighLow", "Level"]] = [1, 110.0]
    res["estructura"].loc[16, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    rev = candidatos_reversion(ohlc, res, swing_length=3)
    assert len(rev) == 1, rev
    # stop = extremo del clúster (low 90 en s) - 0.5 x ATR(14) en s-1 (velas de rango 2 -> ATR 2)
    assert rev[0]["entrada"] == 104.0 and rev[0]["stop"] == 89.0, rev[0]
    assert rev[0]["zona_extremo"] == 103.0 and rev[0]["indice_conocido"] == 29 and rev[0]["indice_barrido"] == 20
    # sin el swing t el CHoCH todavía no se conoce -> no hay candidato
    ohlc_b, res_b = _res_prueba()
    res_b["swings"].loc[16, ["HighLow", "Level"]] = [1, 102.0]
    res_b["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res_b["estructura"].loc[16, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res_b["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    assert candidatos_reversion(ohlc_b, res_b, swing_length=3) == []

    # Continuación: BOS alcista marcado en el máximo roto i=14; s=20 (mínimo del retroceso),
    # zona = velas bajistas 18-20
    ohlc_c, res_c = _res_prueba()
    for p, fila in {17: (106, 107, 104, 105), 18: (104, 105, 101, 102), 19: (102, 103, 99, 100), 20: (100, 101, 95, 97)}.items():
        ohlc_c.iloc[p, :4] = fila
    res_c["swings"].loc[14, ["HighLow", "Level"]] = [1, 108.0]
    res_c["swings"].loc[20, ["HighLow", "Level"]] = [-1, 95.0]
    res_c["swings"].loc[26, ["HighLow", "Level"]] = [1, 115.0]
    res_c["estructura"].loc[14, ["BOS", "BrokenIndex"]] = [1, 24]
    cont = candidatos_continuacion(ohlc_c, res_c, swing_length=3)
    assert len(cont) == 1, cont
    assert cont[0]["indice_zona"] == 18  # máximo 3 velas: 18, 19, 20 (la 17 queda fuera)
    margen = R.FACTOR_ATR_STOP * float(R.atr(ohlc_c).iloc[19])  # ATR medido en s-1
    assert cont[0]["entrada"] == 100.0 and cont[0]["stop"] == 95.0 - margen and cont[0]["zona_extremo"] == 95.0
    assert margen > 0 and cont[0]["stop"] < 95.0  # estrictamente más allá del extremo del clúster
    # el extremo es el de TODO el clúster (zona 18..s), no solo low[s]
    ohlc_c2 = ohlc_c.copy()
    ohlc_c2.iloc[19, ohlc_c2.columns.get_loc("low")] = 94.0
    cont2 = candidatos_continuacion(ohlc_c2, res_c, swing_length=3)
    assert cont2[0]["stop"] == 94.0 - R.FACTOR_ATR_STOP * float(R.atr(ohlc_c2).iloc[19]), cont2
    assert cont2[0]["zona_extremo"] == 94.0  # zona_extremo no cambia de definición (mínimo de la zona)
    # sin ATR en s-1 (historia insuficiente) el candidato se descarta
    sin_atr = pd.Series(float("nan"), index=ohlc_c.index)
    assert candidatos_continuacion(ohlc_c, res_c, swing_length=3, atr_serie=sin_atr) == []

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
    # anti-anticipación: un FVG que termina de formarse después de indice_conocido no cuenta
    ohlc_f, res_f = _res_prueba()
    ohlc_f.iloc[20, ohlc_f.columns.get_loc("low")] = 90.0
    res_f["swings"].loc[16, ["HighLow", "Level"]] = [1, 102.0]
    res_f["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res_f["swings"].loc[22, ["HighLow", "Level"]] = [1, 110.0]      # t = 22 + sl 1 = 23
    res_f["estructura"].loc[16, ["CHOCH", "BrokenIndex"]] = [1, 24]  # conocido = max(24, 23) = 24
    res_f["fvg"].loc[24, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]  # j = 24: necesita la vela 25
    assert candidatos_reversion(ohlc_f, res_f, swing_length=1) == []
    res_f["fvg"].loc[23, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]  # j = 23: completo en 24
    assert [c["indice_zona"] for c in candidatos_reversion(ohlc_f, res_f, swing_length=1)] == [23]

    # la anotación de liquidez no usa barridos posteriores a indice_conocido
    ev_rev = evaluar(rev[0], ohlc, mayor_pasado, "15m", "1H", res, 3)          # conocido 29
    assert ev_rev["liquidez_confluente"] is False
    res["liquidez"].loc[0, ["Liquidity", "Swept"]] = [-1, 20]                 # barrido en 20 <= 29
    assert evaluar(rev[0], ohlc, mayor_pasado, "15m", "1H", res, 3)["liquidez_confluente"] is True
    res["liquidez"].loc[0, ["Liquidity", "Swept"]] = [-1, 35]                 # "barrido" en 35 > 29: futuro
    res["liquidez"].loc[1, ["Liquidity", "Swept"]] = [-1, 35]
    assert evaluar(rev[0], ohlc, mayor_pasado, "15m", "1H", res, 3)["liquidez_confluente"] is False

    # Librería real: el evento se marca en el swing cuyo nivel se rompe (i); s = primer swing
    # posterior (barrido / retroceso), t = segundo (el que completa el patrón).
    o = R.zigzag(TRAMOS_EJEMPLO)
    r = analizar(o, swing_length=5)
    assert r["estructura"].at[37, "CHOCH"] == -1 and r["estructura"].at[111, "BOS"] == 1
    choch = [c for c in candidatos_reversion(o, r, 5) if c["indice_confirmacion"] == 86]
    assert len(choch) == 1, candidatos_reversion(o, r, 5)
    atr_o = R.atr(o)
    assert choch[0]["indice_barrido"] == 61
    assert choch[0]["stop"] == float(o["high"].iloc[61]) + R.FACTOR_ATR_STOP * float(atr_o.iloc[60]), choch[0]
    assert choch[0]["indice_conocido"] == 96 and 61 <= choch[0]["indice_zona"] <= 66  # max(86, 91 + 5)
    bos = [c for c in candidatos_continuacion(o, r, 5) if c["indice_confirmacion"] == 138]
    assert len(bos) == 1, candidatos_continuacion(o, r, 5)
    assert bos[0]["indice_barrido"] == 125
    assert bos[0]["stop"] == float(o["low"].iloc[123:126].min()) - R.FACTOR_ATR_STOP * float(atr_o.iloc[124]), bos[0]
    assert bos[0]["indice_conocido"] == 154 and bos[0]["indice_zona"] == 123  # max(138, 149 + 5); zona 123-125

    # R6 sin look-ahead: con toda la serie la librería borra el máximo 49 (130.5) porque después
    # (tras indice_conocido 56) llega otro máximo mayor sin mínimo entre ambos. En 56 era liquidez
    # sin buscar y debe ser el TP.
    o = R.zigzag([(100, 120, 20), (120, 110, 10), (110, 130, 20), (130, 129, 2), (129, 129.8, 6),
                  (129.8, 140, 20), (140, 120, 20)])
    o.iloc[50, o.columns.get_loc("high")] = 130.2  # la vela que abre el retroceso no toca 130.5
    r = analizar(o, swing_length=5)
    assert 49 not in r["swings"].index[r["swings"]["HighLow"] == 1]  # borrado usando el futuro
    c = {"tipo": "continuacion", "direccion": "long", "indice_barrido": 51, "indice_confirmacion": 56,
         "indice_conocido": 56, "indice_zona": 50, "entrada": 128.0, "stop": 127.0, "zona_extremo": 127.0}
    r6 = evaluar(c, o, mayor_pasado, "1H", "1H", r, 5)["reglas"]["R6"]
    assert r6["dato"] == {"tp": 130.5, "rr": 2.5}, r6
    print("setups_v2.demo() OK")


if __name__ == "__main__":
    demo()
