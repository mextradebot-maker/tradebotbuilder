"""Reglas R1-R6 del motor SMC v2 — spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md.

Cada regla es una función pura que devuelve {"cumple": True|False|None, "dato": ..., "razon": str}.
cumple=None significa `no_aplica` (no bloquea). Toda regla usa solo velas previas o iguales al
índice que recibe (anti-anticipación); la temporalidad mayor llega ya recortada con `cortar_mayor`.
"""

import pandas as pd

from smartmoneyconcepts import smc

from .tendencia import obtener_tendencia

# Umbrales por VELA, no por perfil (la Etapa 2 solo cambia el mapa perfil -> velas).
UMBRAL_VOLUMEN = {"15m": 3.0, "30m": 3.0, "1H": 2.5, "4H": 2.0, "D": 1.0, "S": None, "M": None}  # L45
VENTANA_VOLUMEN = 20
FACTOR_ATR_FVG = 0.5  # ponytail: el curso (L20) no da número para "gap imperceptible"; punto de partida acordado 28 sep
PERIODO_ATR = 14
EMAS_SESGO = (200, 50, 20)  # cadena de respaldo acordada con Ricardo
RR_MINIMO = 2.0  # §10.5
# ponytail: 30m y D no se calibraron en el barrido del 27 sep; heredan el valor de su vecina.
SWING_LENGTH_POR_VELA = {"15m": 8, "30m": 8, "1H": 10, "4H": 10, "D": 10, "S": 5, "M": 5}
DURACION_VELA = {
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1H": pd.Timedelta(hours=1),
    "4H": pd.Timedelta(hours=4),
    "D": pd.Timedelta(days=1),
    "S": pd.Timedelta(days=7),
    "M": pd.DateOffset(months=1),
}


def _res(cumple, dato, razon: str) -> dict:
    return {"cumple": cumple, "dato": dato, "razon": razon}


NO_APLICA = _res(None, None, "no aplica a este tipo de setup")


def cortar_mayor(ohlc_mayor: pd.DataFrame, ts_cierre: pd.Timestamp, vela_mayor: str) -> pd.DataFrame:
    """Solo las velas mayores que ya cerraron en `ts_cierre` (apertura + duración <= ts_cierre)."""
    if ohlc_mayor.empty:
        return ohlc_mayor
    duracion = DURACION_VELA[vela_mayor]
    cierres = ohlc_mayor.index.map(lambda t: t + duracion)
    return ohlc_mayor[cierres <= ts_cierre]


def atr(ohlc: pd.DataFrame, periodo: int = PERIODO_ATR) -> pd.Series:
    cierre_prev = ohlc["close"].shift()
    rango = pd.concat(
        [ohlc["high"] - ohlc["low"], (ohlc["high"] - cierre_prev).abs(), (ohlc["low"] - cierre_prev).abs()], axis=1
    ).max(axis=1)
    return rango.rolling(periodo).mean()


def r1_volumen(ohlc: pd.DataFrame, i: int, vela: str) -> dict:
    umbral = UMBRAL_VOLUMEN[vela]
    if umbral is None:
        return _res(None, None, f"sin filtro de volumen en vela {vela} (L45)")
    if i < VENTANA_VOLUMEN:
        return _res(False, None, "datos insuficientes para el promedio de volumen")
    media = ohlc["volume"].iloc[i - VENTANA_VOLUMEN : i].mean()
    if not media > 0:
        return _res(False, None, "datos insuficientes: sin volumen en las 20 velas previas")
    multiplo = float(ohlc["volume"].iloc[i] / media)
    return _res(multiplo >= umbral, round(multiplo, 2), f"volumen del barrido {multiplo:.2f}x vs umbral {umbral}x")


def r2_fvg(ohlc: pd.DataFrame, top: float, bottom: float, j: int) -> dict:
    valor_atr = atr(ohlc).iloc[j - 1] if j >= 1 else float("nan")
    if pd.isna(valor_atr):
        return _res(False, None, "datos insuficientes para ATR(14)")
    proporcion = float(top - bottom) / float(valor_atr)
    return _res(proporcion >= FACTOR_ATR_FVG, round(proporcion, 2), f"FVG mide {proporcion:.2f} ATR vs mínimo {FACTOR_ATR_FVG}")


def r3_ema(ohlc: pd.DataFrame, k: int, direccion: str) -> dict:
    cierre = float(ohlc["close"].iloc[k])
    for periodo in EMAS_SESGO:
        if k + 1 >= periodo:
            ema = float(ohlc["close"].iloc[: k + 1].ewm(span=periodo, adjust=False).mean().iloc[-1])
            cumple = cierre > ema if direccion == "long" else cierre < ema
            lado = "sobre" if cierre > ema else "bajo"
            return _res(bool(cumple), periodo, f"cierre {lado} la EMA {periodo}")
    return _res(None, None, "no aplica: historia insuficiente")


def cruce_20_50(ohlc: pd.DataFrame, k: int, direccion: str) -> str | None:
    if k + 1 < 50:
        return None
    cierres = ohlc["close"].iloc[: k + 1]
    rapida = cierres.ewm(span=20, adjust=False).mean().iloc[-1]
    lenta = cierres.ewm(span=50, adjust=False).mean().iloc[-1]
    return "a_favor" if (rapida > lenta) == (direccion == "long") else "en_contra"


def r4_estructura(mayor: pd.DataFrame, direccion: str, vela_mayor: str) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    tendencia = obtener_tendencia(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])["direccion"]
    esperado = "compra" if direccion == "long" else "venta"
    return _res(tendencia == esperado, tendencia, f"estructura {vela_mayor}: {tendencia}")


def r5_descuento_premium(mayor: pd.DataFrame, entrada: float, direccion: str, vela_mayor: str) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    swings = smc.swing_highs_lows(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])
    altos = swings.loc[swings["HighLow"] == 1, "Level"]
    bajos = swings.loc[swings["HighLow"] == -1, "Level"]
    if altos.empty or bajos.empty:
        return _res(False, None, f"datos insuficientes: sin swing alto y bajo en {vela_mayor}")
    alto, bajo = float(altos.iloc[-1]), float(bajos.iloc[-1])
    if alto <= bajo:
        return _res(False, None, f"rango {vela_mayor} inválido (último alto <= último bajo)")
    posicion = (entrada - bajo) / (alto - bajo)
    cumple = posicion < 0.5 if direccion == "long" else posicion > 0.5
    zona = "descuento" if posicion < 0.5 else "premium"
    return _res(bool(cumple), round(posicion, 3), f"entrada en {zona} ({posicion:.0%} del rango {vela_mayor})")


def r6_tp_liquidez(ohlc: pd.DataFrame, swings: pd.DataFrame, conocido: int, entrada: float, stop: float,
                   direccion: str, swing_length: int) -> dict:
    long = direccion == "long"
    lado = swings[swings["HighLow"] == (1 if long else -1)]
    lado = lado[lado.index + swing_length <= conocido]  # swing ya confirmado cuando el setup existe
    extremo = ohlc["high"] if long else ohlc["low"]
    candidatos = []
    for idx, nivel in lado["Level"].items():
        if (long and nivel <= entrada) or (not long and nivel >= entrada):
            continue
        despues = extremo.iloc[idx + 1 : conocido + 1]
        sin_buscar = despues.empty or (despues.max() < nivel if long else despues.min() > nivel)
        if sin_buscar:
            candidatos.append(float(nivel))
    if not candidatos:
        return _res(False, None, "no hay liquidez sin buscar en la dirección del trade")
    tp = min(candidatos) if long else max(candidatos)
    rr = abs(tp - entrada) / abs(entrada - stop)
    return _res(rr >= RR_MINIMO, {"tp": tp, "rr": round(rr, 2)}, f"TP en liquidez a {rr:.2f}R (mínimo {RR_MINIMO}R)")


# ── pruebas ──────────────────────────────────────────────────────────────────

def velas(filas, inicio="2026-01-05 00:00", freq="15min") -> pd.DataFrame:
    """filas = [(open, high, low, close, volume), ...] con índice UTC."""
    idx = pd.date_range(inicio, periods=len(filas), freq=freq, tz="UTC")
    return pd.DataFrame(filas, columns=["open", "high", "low", "close", "volume"], index=idx)


def zigzag(tramos, freq="1h", inicio="2026-01-05 00:00") -> pd.DataFrame:
    """Serie a partir de tramos [(desde, hasta, n_velas), ...] — velas limpias de rango 1."""
    closes = []
    for desde, hasta, n in tramos:
        paso = (hasta - desde) / n
        closes += [desde + paso * (p + 1) for p in range(n)]
    filas = []
    prev = tramos[0][0]
    for c in closes:
        filas.append((prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, 100.0))
        prev = c
    return velas(filas, inicio=inicio, freq=freq)


# Tramos largos a propósito: con swing_length 10 (1H) un swing necesita 10 velas a cada lado.
ALCISTA = [(100, 120, 24), (120, 110, 14), (110, 135, 24), (135, 124, 14), (124, 150, 24),
           (150, 138, 14), (138, 165, 24), (165, 150, 14), (150, 172, 24)]


def demo() -> None:
    # cortar_mayor: a las 10:30 solo cerró la vela 1H de las 09:00, no la de las 10:00
    mayor = velas([(1, 2, 0, 1, 1)] * 3, inicio="2026-01-05 09:00", freq="1h")
    assert list(cortar_mayor(mayor, pd.Timestamp("2026-01-05 10:30", tz="UTC"), "1H").index.hour) == [9]
    mensual = velas([(1, 2, 0, 1, 1)] * 2, inicio="2026-01-01", freq="MS")
    assert len(cortar_mayor(mensual, pd.Timestamp("2026-02-01", tz="UTC"), "M")) == 1

    # R1 volumen
    base = [(10, 11, 9, 10, 100.0)] * 20
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 350.0)]), 20, "15m")["cumple"] is True
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "15m")["cumple"] is False
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "1H")["cumple"] is True
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "S")["cumple"] is None
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 5, "15m")["cumple"] is False

    # R2 FVG vs ATR: velas de rango 2 -> ATR 2 -> mínimo 1.0
    planas = velas([(10, 11, 9, 10, 100.0)] * 20)
    assert r2_fvg(planas, 11.5, 10.0, 15)["cumple"] is True
    assert r2_fvg(planas, 10.5, 10.0, 15)["cumple"] is False
    assert r2_fvg(planas, 11.5, 10.0, 5)["cumple"] is False  # sin 14 velas previas

    # R3 EMA con cadena 200 -> 50 -> 20 -> no aplica
    def subida(n):
        return velas([(p, p + 1, p - 1, p + 0.5, 100.0) for p in range(1, n + 1)])
    assert r3_ema(subida(250), 249, "long") == _res(True, 200, "cierre sobre la EMA 200")
    assert r3_ema(subida(60), 59, "long")["dato"] == 50
    assert r3_ema(subida(30), 29, "long")["dato"] == 20
    assert r3_ema(subida(10), 9, "long")["cumple"] is None
    assert r3_ema(subida(250), 249, "short")["cumple"] is False
    assert cruce_20_50(subida(60), 59, "long") == "a_favor"
    assert cruce_20_50(subida(30), 29, "long") is None

    # R4 / R5 sobre una temporalidad mayor alcista limpia (HH/HL)
    mayor_alcista = zigzag(ALCISTA)
    r4 = r4_estructura(mayor_alcista, "long", "1H")
    assert r4["cumple"] is True, r4
    assert r4_estructura(mayor_alcista, "short", "1H")["cumple"] is False
    assert r4_estructura(mayor_alcista.iloc[:0], "long", "1H")["cumple"] is False
    r5_bajo = r5_descuento_premium(mayor_alcista, 152.0, "long", "1H")
    r5_alto = r5_descuento_premium(mayor_alcista, 163.0, "long", "1H")
    assert r5_bajo["cumple"] is True and r5_alto["cumple"] is False, (r5_bajo, r5_alto)
    assert r5_descuento_premium(mayor_alcista, 163.0, "short", "1H")["cumple"] is True

    # R6 TP en liquidez: swings controlados a mano (misma forma que smc.swing_highs_lows)
    plano = velas([(10, 10, 9, 10, 100.0)] * 30)
    swings = pd.DataFrame({"HighLow": [float("nan")] * 30, "Level": [float("nan")] * 30})
    swings.loc[5, ["HighLow", "Level"]] = [1, 20.0]
    swings.loc[8, ["HighLow", "Level"]] = [1, 15.0]
    swings.loc[25, ["HighLow", "Level"]] = [1, 12.0]  # 25 + 5 > 29: aún no confirmado
    r6 = r6_tp_liquidez(plano, swings, 29, entrada=10.0, stop=8.0, direccion="long", swing_length=5)
    assert r6["cumple"] is True and r6["dato"] == {"tp": 15.0, "rr": 2.5}, r6
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 6.0, "long", 5)["cumple"] is False  # 1.25R
    tomado = plano.copy()
    tomado.iloc[12, tomado.columns.get_loc("high")] = 16.0  # el precio ya buscó el 15
    assert r6_tp_liquidez(tomado, swings, 29, 10.0, 8.0, "long", 5)["dato"]["tp"] == 20.0
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 12.0, "short", 5)["cumple"] is False  # no hay bajos

    print("reglas.demo() OK")


if __name__ == "__main__":
    demo()
