"""Reglas R1-R6 del motor SMC v2 — spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md.

Cada regla es una función pura que devuelve {"cumple": True|False|None, "dato": ..., "razon": str}.
cumple=None significa `no_aplica` (no bloquea). Toda regla usa solo velas previas o iguales al
índice que recibe (anti-anticipación); la temporalidad mayor llega ya recortada con `cortar_mayor`.
"""

import pandas as pd

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


# ── pruebas ──────────────────────────────────────────────────────────────────

def velas(filas, inicio="2026-01-05 00:00", freq="15min") -> pd.DataFrame:
    """filas = [(open, high, low, close, volume), ...] con índice UTC."""
    idx = pd.date_range(inicio, periods=len(filas), freq=freq, tz="UTC")
    return pd.DataFrame(filas, columns=["open", "high", "low", "close", "volume"], index=idx)


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

    print("reglas.demo() OK")


if __name__ == "__main__":
    demo()
