"""GET /api/v1/grafica?simbolo=XAUUSD&temporalidad=Intraday 1H&limite=1500 — datos de la pantalla «Análisis técnico».

Devuelve las velas CERRADAS del almacén (mismas que usa el motor) y, encima, lo que el motor SMC detecta en esa serie:
estructura (BOS/CHoCH), FVG, order blocks y liquidez. Requiere sesión de alumno (cookie `mtb_token`) o X-MTB-Service-Key.
Respuesta cacheada 30 s por (símbolo, temporalidad, límite): el cálculo SMC cuesta ~1 s y la API la usan muchos alumnos.

Tiempos en segundos UNIX (UTC), el formato que espera Lightweight Charts.
"""

import math
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from conectividad import SIMBOLOS, TEMPORALIDADES, TEMPORALIDAD_A_INTERVALO, obtener_velas, resolver_temporalidad

TEMPORALIDADES_GRAFICA = ("Scalping 15m", "Intraday 1H", "Intraday 4H", "Intraday D", "Swing (S)", "Swing (M)")
LIMITE_DEFECTO, LIMITE_MAX = 1500, 3000
MAX_POR_TIPO = {"fvg": 40, "order_blocks": 30, "liquidez": 25, "estructura": 60}
VIDA_CACHE_S = 30
_cache: dict = {}


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _t(ohlc: pd.DataFrame, i) -> int | None:
    """Segundo UNIX de la vela en la posición `i` (índice entero del DataFrame de la serie), o None."""
    f = _num(i)
    if f is None or not 0 <= int(f) < len(ohlc):
        return None
    return int(ohlc.index[int(f)].timestamp())


def smc_a_json(ohlc: pd.DataFrame, res: dict) -> dict:
    """Pasa los DataFrames del motor SMC (una fila por vela) a listas pequeñas listas para dibujar."""
    out = {"estructura": [], "fvg": [], "order_blocks": [], "liquidez": []}
    est = res["estructura"]
    for i in range(len(est)):
        for tipo in ("BOS", "CHOCH"):
            d = _num(est[tipo].iloc[i])
            if d is not None:
                out["estructura"].append({"t": _t(ohlc, i), "tipo": tipo, "dir": int(d), "nivel": _num(est["Level"].iloc[i]),
                                          "t_fin": _t(ohlc, est["BrokenIndex"].iloc[i])})
    fvg = res["fvg"]
    for i in range(len(fvg)):
        d = _num(fvg["FVG"].iloc[i])
        if d is not None:
            out["fvg"].append({"t": _t(ohlc, i), "dir": int(d), "arriba": _num(fvg["Top"].iloc[i]), "abajo": _num(fvg["Bottom"].iloc[i]),
                               "t_fin": _t(ohlc, fvg["MitigatedIndex"].iloc[i])})
    ob = res["order_blocks"]
    for i in range(len(ob)):
        d = _num(ob["OB"].iloc[i])
        if d is not None:
            out["order_blocks"].append({"t": _t(ohlc, i), "dir": int(d), "arriba": _num(ob["Top"].iloc[i]), "abajo": _num(ob["Bottom"].iloc[i]),
                                        "t_fin": _t(ohlc, ob["MitigatedIndex"].iloc[i])})
    liq = res["liquidez"]
    for i in range(len(liq)):
        d = _num(liq["Liquidity"].iloc[i])
        if d is not None:
            out["liquidez"].append({"t": _t(ohlc, i), "dir": int(d), "nivel": _num(liq["Level"].iloc[i]),
                                    "t_fin": _t(ohlc, liq["End"].iloc[i]), "barrida": _t(ohlc, liq["Swept"].iloc[i])})
    return {k: [x for x in v if x["t"] is not None][-MAX_POR_TIPO[k]:] for k, v in out.items()}


def _autorizado(headers: dict) -> bool:
    from api.licencias import _clave_ok, _h
    if _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return True
    try:
        from api.alumnos import _alumno
        return _alumno(headers) is not None
    except Exception:
        return False


def procesar(qs: dict, headers: dict, ahora=None, autorizado=None, obtener=None, analizar_fn=None) -> tuple[int, dict]:
    if not (autorizado or _autorizado)(headers):
        return 401, {"error": "inicia sesion para ver la grafica"}
    simbolo = qs.get("simbolo")
    if simbolo not in SIMBOLOS:
        return 400, {"error": "falta 'simbolo' valido del catalogo"}
    try:
        temporalidad = resolver_temporalidad(qs.get("temporalidad", "Intraday 1H"))
        limite = max(50, min(int(qs.get("limite", LIMITE_DEFECTO)), LIMITE_MAX))
    except (ValueError, TypeError):
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDADES_GRAFICA)} y 'limite' un entero"}
    if temporalidad not in TEMPORALIDADES_GRAFICA:
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDADES_GRAFICA)}"}

    clave = (simbolo, temporalidad, limite)
    t0 = time.time() if ahora is None else ahora
    hit = _cache.get(clave)
    if hit and t0 - hit[0] < VIDA_CACHE_S:
        return 200, hit[1]

    cfg = TEMPORALIDADES[temporalidad]
    fin = datetime.fromtimestamp(t0, timezone.utc)
    try:
        ohlc = (obtener or obtener_velas)(simbolo, fin - timedelta(days=cfg["dias"]), fin, intervalo=TEMPORALIDAD_A_INTERVALO[temporalidad])
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}
    if ohlc is None or ohlc.empty:
        return 200, {"simbolo": simbolo, "temporalidad": temporalidad, "velas": [], "smc": smc_a_json(pd.DataFrame(), {k: pd.DataFrame() for k in ("estructura", "fvg", "order_blocks", "liquidez")})}
    from motor_smc.reglas import DURACION_VELA
    if ohlc.index[-1] + DURACION_VELA[cfg["vela"]] > pd.Timestamp(fin):  # la vela en formación no está cerrada
        ohlc = ohlc.iloc[:-1]
    ohlc = ohlc.iloc[-limite:]

    from api.setups import SWING_LENGTH_POR_TEMPORALIDAD
    try:
        if analizar_fn is None:
            from motor_smc import analizar as analizar_fn
        smc = smc_a_json(ohlc, analizar_fn(ohlc, swing_length=SWING_LENGTH_POR_TEMPORALIDAD.get(temporalidad, 10)))
        aviso = None
    except Exception as e:  # sin análisis la gráfica sigue sirviendo: solo faltan las capas SMC
        smc, aviso = {"estructura": [], "fvg": [], "order_blocks": [], "liquidez": []}, f"sin capas SMC: {type(e).__name__}"
    velas = [{"t": int(ts.timestamp()), "o": round(float(r.open), 6), "h": round(float(r.high), 6), "l": round(float(r.low), 6),
              "c": round(float(r.close), 6), "v": round(float(r.volume), 2)} for ts, r in ohlc.iterrows()]
    cuerpo = {"simbolo": simbolo, "temporalidad": temporalidad, "velas": velas, "smc": smc}
    if aviso:
        cuerpo["aviso"] = aviso
    _cache[clave] = (t0, cuerpo)
    return 200, cuerpo


def demo() -> None:
    import numpy as np
    rng = np.random.default_rng(7)
    n = 400
    close = 1.1 + np.cumsum(rng.normal(0, 0.002, n))
    idx = pd.date_range("2026-09-01", periods=n, freq="h", tz="UTC")
    ohlc = pd.DataFrame({"open": close + rng.normal(0, .0005, n), "high": close + rng.uniform(.0005, .002, n),
                         "low": close - rng.uniform(.0005, .002, n), "close": close, "volume": rng.uniform(100, 900, n)}, index=idx)
    ahora = (idx[-1] + pd.Timedelta(hours=1, minutes=5)).timestamp()  # la ultima vela ya cerro
    sin_sesion, con_sesion = (lambda h: False), (lambda h: True)
    obtener = lambda s, a, b, intervalo=None: ohlc
    assert procesar({"simbolo": "EURUSD"}, {}, ahora, sin_sesion, obtener)[0] == 401
    assert procesar({"simbolo": "XXX"}, {}, ahora, con_sesion, obtener)[0] == 400
    assert procesar({"simbolo": "EURUSD", "temporalidad": "Scalping 30m"}, {}, ahora, con_sesion, obtener)[0] == 400
    assert procesar({"simbolo": "EURUSD", "limite": "abc"}, {}, ahora, con_sesion, obtener)[0] == 400
    st, b = procesar({"simbolo": "EURUSD", "limite": "300"}, {}, ahora, con_sesion, obtener)
    assert st == 200 and len(b["velas"]) == 300 and b["velas"][-1]["t"] == int(idx[-1].timestamp()), (st, len(b["velas"]))
    assert b["temporalidad"] == "Intraday 1H" and set(b["smc"]) == {"estructura", "fvg", "order_blocks", "liquidez"}
    assert all(set(v) == {"t", "o", "h", "l", "c", "v"} for v in b["velas"][:3])
    assert any(b["smc"][k] for k in b["smc"]), "debe detectar algo en 300 velas"
    assert len(b["smc"]["fvg"]) <= MAX_POR_TIPO["fvg"] and all(x["t"] for x in b["smc"]["estructura"])
    # cache 30 s: no vuelve a pedir velas
    llamadas = []
    st2, _ = procesar({"simbolo": "EURUSD", "limite": "300"}, {}, ahora + 5, con_sesion, lambda *a, **k: llamadas.append(1) or ohlc)
    assert st2 == 200 and not llamadas
    # vela en formacion se descarta
    formando = (idx[-1] + pd.Timedelta(minutes=20)).timestamp()
    st3, b3 = procesar({"simbolo": "GBPUSD", "limite": "300"}, {}, formando, con_sesion, obtener)
    assert st3 == 200 and b3["velas"][-1]["t"] == int(idx[-2].timestamp())
    # si el analisis falla, la grafica sigue y avisa
    st4, b4 = procesar({"simbolo": "USDJPY"}, {}, ahora, con_sesion, obtener, lambda *a, **k: 1 / 0)
    assert st4 == 200 and b4["aviso"].startswith("sin capas SMC") and len(b4["velas"]) == 400 - 0 and not any(b4["smc"].values())
    # fallo de datos -> 502
    assert procesar({"simbolo": "AUDUSD"}, {}, ahora, con_sesion, lambda *a, **k: 1 / 0)[0] == 502
    print("api.grafica.demo() OK —", {k: len(v) for k, v in b["smc"].items()})


if __name__ == "__main__":
    demo()
