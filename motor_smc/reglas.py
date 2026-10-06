"""Reglas R1-R6 del motor SMC v2 — spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md.

Cada regla es una función pura que devuelve {"cumple": True|False|None, "dato": ..., "razon": str}.
cumple=None significa `no_aplica` (no bloquea). Toda regla usa solo velas previas o iguales al
índice que recibe (anti-anticipación); la temporalidad mayor llega ya recortada con `cortar_mayor`.
"""

import numpy as np
import pandas as pd

from smartmoneyconcepts import smc

from .tendencia import obtener_tendencia, tendencia_de_estructura

# Umbrales por VELA, no por perfil (la Etapa 2 solo cambia el mapa perfil -> velas).
UMBRAL_VOLUMEN = {"15m": 3.0, "30m": 3.0, "1H": 2.5, "4H": 2.0, "D": 1.0, "S": None, "M": None}  # L45
VENTANA_VOLUMEN = 20
FACTOR_ATR_FVG = 0.5  # ponytail: el curso (L20) no da número para "gap imperceptible"; punto de partida acordado 28 sep
PERIODO_ATR = 14
FACTOR_ATR_STOP = 0.5  # ponytail: el curso pide "espacio prudente" (§10.3, MACD) sin número; margen acordado con Ricardo 28 sep
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
    # Timedelta: suma vectorizada (mismos valores que el map); DateOffset de meses sigue elemento a elemento
    cierres = ohlc_mayor.index + duracion if isinstance(duracion, pd.Timedelta) else ohlc_mayor.index.map(lambda t: t + duracion)
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
    if pd.isna(multiplo):
        return _res(False, None, "datos insuficientes: sin volumen en la vela del barrido")
    return _res(multiplo >= umbral, round(multiplo, 2), f"volumen del barrido {multiplo:.2f}x vs umbral {umbral}x")


def emas(ohlc: pd.DataFrame) -> dict[int, pd.Series]:
    """EMAs de EMAS_SESGO (y la 20/50 del cruce) sobre toda la serie, una sola vez. ewm(adjust=False)
    es causal: el valor en k es el mismo que sobre el prefijo [:k+1]."""
    return {p: ohlc["close"].ewm(span=p, adjust=False).mean() for p in {*EMAS_SESGO, 20, 50}}


def r2_fvg(ohlc: pd.DataFrame, top: float, bottom: float, j: int, atr_serie: pd.Series | None = None) -> dict:
    atr_serie = atr(ohlc) if atr_serie is None else atr_serie
    valor_atr = atr_serie.iloc[j - 1] if j >= 1 else float("nan")
    if pd.isna(valor_atr) or valor_atr <= 0:
        return _res(False, None, "datos insuficientes para ATR(14)")
    proporcion = float(top - bottom) / float(valor_atr)
    return _res(proporcion >= FACTOR_ATR_FVG, round(proporcion, 2), f"FVG mide {proporcion:.2f} ATR vs mínimo {FACTOR_ATR_FVG}")


def r3_ema(ohlc: pd.DataFrame, k: int, direccion: str, emas_serie: dict | None = None) -> dict:
    cierre = float(ohlc["close"].iloc[k])
    for periodo in EMAS_SESGO:
        if k + 1 >= periodo:
            if emas_serie is None:
                ema = float(ohlc["close"].iloc[: k + 1].ewm(span=periodo, adjust=False).mean().iloc[-1])
            else:
                ema = float(emas_serie[periodo].iloc[k])
            cumple = cierre > ema if direccion == "long" else cierre < ema
            lado = "sobre" if cierre > ema else "bajo"
            return _res(bool(cumple), periodo, f"cierre {lado} la EMA {periodo}")
    return _res(None, None, "no aplica: historia insuficiente")


def cruce_20_50(ohlc: pd.DataFrame, k: int, direccion: str, emas_serie: dict | None = None) -> str | None:
    if k + 1 < 50:
        return None
    if emas_serie is None:
        cierres = ohlc["close"].iloc[: k + 1]
        rapida = cierres.ewm(span=20, adjust=False).mean().iloc[-1]
        lenta = cierres.ewm(span=50, adjust=False).mean().iloc[-1]
    else:
        rapida, lenta = emas_serie[20].iloc[k], emas_serie[50].iloc[k]
    return "a_favor" if (rapida > lenta) == (direccion == "long") else "en_contra"


def contexto_mayor(mayor: pd.DataFrame, vela_mayor: str) -> tuple:
    """(tendencia, último alto, último bajo) de la temporalidad mayor ya recortada, con la librería:
    lo que R4 y R5 calculan. Es la referencia de `ContextoMayor` (ver demo)."""
    # = obtener_tendencia(mayor) pero sin fvg/ob/liquidez, que la tendencia no usa; los swings sirven a los dos
    swings = smc.swing_highs_lows(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])
    altos = swings.loc[swings["HighLow"] == 1, "Level"]
    bajos = swings.loc[swings["HighLow"] == -1, "Level"]
    return (tendencia_de_estructura(smc.bos_choch(mayor, swings))["direccion"],
            float(altos.iloc[-1]) if len(altos) else None, float(bajos.iloc[-1]) if len(bajos) else None)


class ContextoMayor:
    """`contexto_mayor(ohlc_mayor.iloc[:n])` para cualquier n sin volver a correr la librería por recorte
    (era el costo dominante del detector: un recorte distinto casi por candidato).

    Mismo resultado que la librería porque:
    - las marcas crudas de swing_highs_lows en p solo miran las velas p-sl+1..p+sl: en el recorte de n
      velas son las de la serie completa con p <= n-1-sl (después de eso la ventana queda incompleta);
    - la limpieza de swings consecutivos del mismo tipo deja, en cada racha, el primer extremo; el
      recorte solo trunca la última racha;
    - la librería pone además swings artificiales en la vela 0 y en la n-1 (tipo opuesto al primero y
      al último real);
    - en bos_choch el último evento que sobrevive es el último (por índice) cuya ruptura cae dentro del
      recorte: ningún evento posterior con ruptura puede borrarlo. La ruptura es la primera vela >= i+2
      que cierra más allá del nivel (guardado en float32 por la librería), igual en la serie completa.
    Anti-anticipación: `en(n)` solo usa las velas 0..n-1."""

    def __init__(self, ohlc_mayor: pd.DataFrame, vela_mayor: str):
        sl = self.sl = SWING_LENGTH_POR_VELA[vela_mayor]
        self.high, self.low = ohlc_mayor["high"], ohlc_mayor["low"]
        self.close = ohlc_mayor["close"].to_numpy()
        crudas = np.where(  # misma expresión que smc.swing_highs_lows antes de su limpieza
            self.high == self.high.shift(-sl).rolling(sl * 2).max(), 1,
            np.where(self.low == self.low.shift(-sl).rolling(sl * 2).min(), -1, np.nan))
        self.pos = np.flatnonzero(~np.isnan(crudas))
        self.tipo = crudas[self.pos].astype(int)
        cambio = np.r_[True, self.tipo[1:] != self.tipo[:-1]]
        self.racha = np.cumsum(cambio) - 1           # racha de cada marca cruda
        self.ini_racha = np.flatnonzero(cambio)      # primera marca cruda de cada racha
        fines = np.r_[self.ini_racha[1:], len(self.pos)]
        self.limpios = [self._extremo(a, b) for a, b in zip(self.ini_racha, fines)]
        self.ruptura: dict[int, int] = {}

    def _swing(self, p: int, t: int) -> tuple:
        return p, t, float(self.high.iloc[p] if t == 1 else self.low.iloc[p])

    def _extremo(self, a: int, b: int) -> tuple:
        """Primer máximo (altos) o primer mínimo (bajos) de las marcas crudas a..b-1 de una racha."""
        pos, t = self.pos[a:b], int(self.tipo[a])
        valores = self.high.to_numpy()[pos] if t == 1 else self.low.to_numpy()[pos]
        return self._swing(int(pos[np.argmax(valores) if t == 1 else np.argmin(valores)]), t)

    def _primera_ruptura(self, p: int, t: int, nivel: float) -> int:
        if p not in self.ruptura:
            nivel32 = float(np.float32(nivel))
            resto = self.close[p + 2 :]
            hits = np.flatnonzero(resto > nivel32 if t == 1 else resto < nivel32)
            self.ruptura[p] = p + 2 + int(hits[0]) if len(hits) else len(self.close)
        return self.ruptura[p]

    def en(self, n: int) -> tuple:
        k = int(np.searchsorted(self.pos, n - 1 - self.sl, side="right"))  # marcas crudas visibles
        if k == 0:
            return "sin_definir", None, None
        r = int(self.racha[k - 1])
        reales = self.limpios[:r] + [self._extremo(int(self.ini_racha[r]), k)]
        q = [self._swing(0, -reales[0][1]), *reales, self._swing(n - 1, -reales[-1][1])]
        alto = next(s[2] for s in reversed(q) if s[1] == 1)
        bajo = next(s[2] for s in reversed(q) if s[1] == -1)
        for j in range(len(q) - 3, 0, -1):  # evento en q[j] con el patrón q[j-1..j+2]
            a, (p, t, b), c, d = q[j - 1][2], q[j], q[j + 1][2], q[j + 2][2]
            if t == 1:
                evento = a < c < b < d or d > b > a > c      # BOS / CHoCH alcista
            else:
                evento = a > c > b > d or d < b < a < c      # BOS / CHoCH bajista
            if evento and self._primera_ruptura(p, t, b) <= n - 1:
                return ("compra" if t == 1 else "venta"), alto, bajo
        return "sin_definir", alto, bajo


def zona_usada(ohlc: pd.DataFrame, desde: int, conocido: int, entrada: float, extremo: float, direccion: str) -> str | None:
    """Razón de descarte si, entre `desde` (la zona ya existe) y `conocido` (el setup se confirma), el precio
    ya llegó a la entrada o cerró más allá del extremo de la zona: esa orden se habría llenado o invalidado
    antes de existir. None si la zona sigue intacta."""
    if desde > conocido:
        return None
    v = ohlc.iloc[desde : conocido + 1]
    long = direccion == "long"
    toca = ((v["low"] <= entrada) if long else (v["high"] >= entrada)).to_numpy()
    rompe = ((v["close"] < extremo) if long else (v["close"] > extremo)).to_numpy()
    usada = np.flatnonzero(toca | rompe)
    if len(usada) == 0:
        return None
    p = int(usada[0])
    que = "cerró más allá de la zona" if rompe[p] else "tocó la entrada"
    return f"zona ya usada antes de confirmarse: el precio {que} en {v.index[p]}"


def r4_estructura(mayor: pd.DataFrame, direccion: str, vela_mayor: str, contexto: tuple | None = None) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    if contexto is not None:
        tendencia = contexto[0]
    else:
        tendencia = obtener_tendencia(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])["direccion"]
    esperado = "compra" if direccion == "long" else "venta"
    return _res(tendencia == esperado, tendencia, f"estructura {vela_mayor}: {tendencia}")


def r5_descuento_premium(mayor: pd.DataFrame, entrada: float, direccion: str, vela_mayor: str,
                         contexto: tuple | None = None) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    _, alto, bajo = contexto_mayor(mayor, vela_mayor) if contexto is None else contexto
    if alto is None or bajo is None:
        return _res(False, None, f"datos insuficientes: sin swing alto y bajo en {vela_mayor}")
    if alto <= bajo:
        return _res(False, None, f"rango {vela_mayor} inválido (último alto <= último bajo)")
    posicion = (entrada - bajo) / (alto - bajo)
    cumple = posicion < 0.5 if direccion == "long" else posicion > 0.5
    zona = "descuento" if posicion < 0.5 else "premium"
    return _res(bool(cumple), round(posicion, 3), f"entrada en {zona} ({posicion:.0%} del rango {vela_mayor})")


def r6_tp_liquidez(ohlc: pd.DataFrame, swings: pd.DataFrame, conocido: int, entrada: float, stop: float,
                   direccion: str, swing_length: int) -> dict:
    # Guardia: riesgo nulo o stop del lado incorrecto
    if abs(entrada - stop) == 0 or (direccion == "long" and stop >= entrada) or (direccion == "short" and stop <= entrada):
        return _res(False, None, "riesgo nulo o stop del lado incorrecto")

    long = direccion == "long"
    lado = swings[swings["HighLow"] == (1 if long else -1)]
    lado = lado[lado.index + swing_length <= conocido]  # swing ya confirmado cuando el setup existe
    extremo = (ohlc["high"] if long else ohlc["low"]).to_numpy()  # numpy: el slice de pandas por swing era lento
    candidatos = []
    for idx, nivel in lado["Level"].items():
        if (long and nivel <= entrada) or (not long and nivel >= entrada):
            continue
        despues = extremo[idx + 1 : conocido + 1]
        sin_buscar = despues.size == 0 or (np.nanmax(despues) < nivel if long else np.nanmin(despues) > nivel)
        if sin_buscar:
            candidatos.append(float(nivel))
    if not candidatos:
        return _res(False, None, "no hay liquidez sin buscar en la dirección del trade")
    tp = min(candidatos) if long else max(candidatos)
    rr = float(abs(tp - entrada) / abs(entrada - stop))
    return _res(bool(rr >= RR_MINIMO), {"tp": float(tp), "rr": round(float(rr), 2)}, f"TP en liquidez a {rr:.2f}R (mínimo {RR_MINIMO}R)")


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

TRAMOS_CORTOS = [(100, 110, 12), (110, 104, 7), (104, 112, 12), (112, 101, 12), (101, 108, 9), (108, 99, 15)]


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
    # series precalculadas (una vez por serie en el detector) = mismo resultado que calcular sobre el prefijo
    s250 = subida(250)
    e250 = emas(s250)
    for k in (29, 59, 120, 249):
        for d in ("long", "short"):
            assert r3_ema(s250, k, d, e250) == r3_ema(s250, k, d) and cruce_20_50(s250, k, d, e250) == cruce_20_50(s250, k, d)
    assert r2_fvg(s250, 3.0, 1.0, 40, atr(s250)) == r2_fvg(s250, 3.0, 1.0, 40)

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
    ctx = contexto_mayor(mayor_alcista, "1H")
    assert r4_estructura(mayor_alcista, "long", "1H", ctx) == r4
    assert r5_descuento_premium(mayor_alcista, 152.0, "long", "1H", ctx) == r5_bajo
    assert r5_descuento_premium(mayor_alcista.iloc[:15], 152.0, "long", "1H")["cumple"] is False  # sin swings aún

    # ContextoMayor.en(n) == librería sobre el recorte de n velas, para TODO n (rachas, empates en
    # precios redondeados, swings artificiales de los extremos, rupturas fuera del recorte)
    import numpy as np
    rng = np.random.default_rng(7)
    series = [mayor_alcista, zigzag(TRAMOS_CORTOS)]
    for semilla in range(2):
        c = np.round(100 + np.cumsum(rng.normal(0, 1, 260)), 0)  # redondeo -> empates de máximos/mínimos
        o = np.r_[c[0], c[:-1]]
        series.append(velas([(o_, max(o_, c_) + rng.integers(0, 2), min(o_, c_) - rng.integers(0, 2), c_, 1.0)
                             for o_, c_ in zip(o, c)], freq="1h"))
    for serie in series:
        for vela_mayor in ("1H", "S"):  # swing_length 10, 5
            rapido = ContextoMayor(serie, vela_mayor)
            for n in range(1, len(serie) + 1):
                assert rapido.en(n) == contexto_mayor(serie.iloc[:n], vela_mayor), (vela_mayor, n)

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

    # tipos nativos y entradas degeneradas (json.dumps -> jsonb)
    import json
    import numpy as np
    r6_np = r6_tp_liquidez(plano, swings, 29, np.float64(10.0), np.float64(8.0), "long", 5)
    assert type(r6_np["cumple"]) is bool and type(r6_np["dato"]["rr"]) is float, r6_np
    json.dumps(r6_np, allow_nan=False)
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 10.0, "long", 5)["cumple"] is False  # riesgo nulo
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 12.0, "long", 5)["cumple"] is False  # stop del lado incorrecto
    assert r2_fvg(velas([(10, 10, 10, 10, 100.0)] * 20), 11.0, 10.0, 15)["cumple"] is False  # ATR 0
    sin_vol = velas(base + [(10, 11, 9, 10, float("nan"))])
    r1_nan = r1_volumen(sin_vol, 20, "15m")
    assert r1_nan["cumple"] is False and r1_nan["dato"] is None

    print("reglas.demo() OK")


if __name__ == "__main__":
    demo()
