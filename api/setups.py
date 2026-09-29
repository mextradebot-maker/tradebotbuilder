"""Lógica de GET/POST /api/setups?simbolo=XAUUSD&dias=90[&temporalidad=Intraday]
— invocado desde el router en api/analizar.py (mismo motivo que api/backtest.py:
Vercel en modo single-entrypoint no auto-descubre este archivo como su propia función).

A diferencia de /api/analizar (que solo analiza OHLC ya provisto), esto trae
los datos históricos él mismo (dukascopy) y corre el motor completo —
pensado para que n8n (T-01 Detector Activos) pregunte "¿hay setups reales en
XAUUSD/EURUSD ahora?" sin tener que conseguir velas por su cuenta (dukascopy
no es una API HTTP que n8n pueda golpear directo). También es el endpoint que
consulta en vivo el EA entregado a los alumnos (robots/MexTradeBot_SeguidorSMC.mq5)
en cada vela nueva.

`temporalidad` es opcional. Sin ella, el comportamiento es exactamente el de
antes (setups crudos, sin cruzar nada más) — para no romper a T-01 ni a EAs ya
desplegados que no la mandan. Con ella, cada setup se cruza contra dos cosas
que el sistema ya calcula por separado y que hasta ahora vivían desconectadas
del punto donde el bot decide entrar:

  1. La tendencia confirmada del día para ese símbolo+temporalidad
     (motor_smc.obtener_tendencia sobre el mismo OHLC).
  2. El historial de backtest de esa dirección específica
     (backtesting.backtest.backtest_direccion sobre el mismo OHLC) —
     ¿es rentable_sin_optimizar y con muestra suficiente?

Un setup solo se marca "confirmado" si las dos coinciden con su dirección.
Esto es lo mismo que ya hace "MTB Analisis Diario de Mercado" (n8n) y lo que
alimenta la hoja Resumen_Temporalidades, aplicado ahora en el momento en que
un bot decide si entra o no — sin eso, el bot podía tomar un setup técnicamente
válido pero contra la tendencia del día o con historial no rentable.
"""

import json
from datetime import datetime, timedelta, timezone, time as _time

import dukascopy_python as dp

from backtesting.backtest import backtest_direccion, backtest_v2
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia
from motor_smc.setups_v2 import detectar_setups_v2, embudo

# ponytail: dias-por-temporalidad duplicado en api/mejor_indicador.py y en el
# job n8n "MTB Analisis Diario de Mercado" (nodo Consultar Backtest) -- si se
# ajusta aquí, ajustar ahí. swing_length y el umbral viven solo aquí.
# Umbral 10 (antes 20): con las ventanas de DIAS_POR_TEMPORALIDAD ninguna combinación
# símbolo×dirección llegaba a 20 casos → ningún setup podía confirmarse (barrido
# 27 sep 2026, backtesting/calibrar_swing_length.py). Decisión de Ricardo.
UMBRAL_SETUPS_SUFICIENTES = 10

# Cache Postgres (Fase 1 BD SMC persistente). Si DATABASE_URL no esta disponible
# o Postgres esta caido, _persistencia queda None y se cae al compute fresco.
TOLERANCIA_SNAPSHOT_MIN = 10
try:
    import persistencia as _persistencia
except Exception:
    _persistencia = None


def _snapshot_viejo(snap: dict) -> bool:
    ts = snap.get("refrescado_en")
    if ts is None:
        return True
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < datetime.now(timezone.utc) - timedelta(minutes=TOLERANCIA_SNAPSHOT_MIN)

DIAS_POR_TEMPORALIDAD = {
    "Scalping": 60,
    "Intraday": 365,
    "Swing (H)": 365,
    "Swing (S)": 1095,
    "Swing (M)": 2555,
}

# Barrido 27 sep 2026 (spec docs/superpowers/specs/2026-09-27-calibracion-swing-length-design.md):
# con 20 fijo, Scalping/Intraday casi no daban setups y Swing (M) nunca definía tendencia.
# Valores "conservadores" elegidos por Ricardo sobre la tabla del barrido.
SWING_LENGTH_POR_TEMPORALIDAD = {
    "Scalping": 8,
    "Intraday": 10,
    "Swing (H)": 10,
    "Swing (S)": 5,
    "Swing (M)": 5,
}

DIRECCION_LONG_SHORT_A_COMPRA_VENTA = {"long": "compra", "short": "venta"}

# Motor v2, Etapa 1 (modo sombra): mapa PROVISIONAL perfil -> (vela de entrada, temporalidad mayor).
# La Etapa 2 lo reemplaza por el mapa definitivo en conectividad/historico.py
# (spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md).
PERFIL_A_VELAS_V2 = {
    "Scalping": ("15m", "1H"),
    "Intraday": ("1H", "D"),
    "Swing (H)": ("4H", "D"),
    "Swing": ("4H", "D"),
    "Swing (S)": ("S", "M"),
    "Swing (M)": ("M", "M"),
}
VELA_A_INTERVALO = {
    "15m": dp.INTERVAL_MIN_15, "30m": dp.INTERVAL_MIN_30, "1H": dp.INTERVAL_HOUR_1, "4H": dp.INTERVAL_HOUR_4,
    "D": dp.INTERVAL_DAY_1, "S": dp.INTERVAL_WEEK_1, "M": dp.INTERVAL_MONTH_1,
}


def motor_v2(simbolo: str, temporalidad: str, ohlc, inicio, fin) -> dict:
    """Modo sombra: calcula el motor v2 junto al actual. Nunca toca `setups`/`setups_confirmados`
    (lo que reciben los EA) y nunca propaga una excepción al llamador."""
    try:
        vela, vela_mayor = PERFIL_A_VELAS_V2[temporalidad]
        if vela_mayor == vela:
            ohlc_mayor = ohlc
        else:
            motivo = None
            try:
                ohlc_mayor = obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela_mayor])
            except Exception as e:
                ohlc_mayor, motivo = None, f"{type(e).__name__}: {e}"
            if ohlc_mayor is None or ohlc_mayor.empty:
                sin = {"estado": "sin_datos_temporalidad_mayor", "vela": vela, "vela_mayor": vela_mayor,
                       "setups": [], "setups_validos": []}
                if motivo:
                    sin["error"] = motivo
                return sin
        setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor)
        # jsonb no acepta NaN: celdas vacías -> None
        registros = setups.astype(object).where(setups.notna(), None).to_dict(orient="records")
        resultado = {
            "estado": "ok", "vela": vela, "vela_mayor": vela_mayor,
            "setups": registros,
            "setups_validos": [s for s in registros if s["valido"]],
            "embudo": embudo(setups),
            "backtests": backtest_v2(ohlc, setups),
        }
        # ida y vuelta estricta: un NaN o tipo numpy en embudo/backtests lanza aquí (-> estado error)
        # en vez de romper json.dumps en escribir_snapshot y dejar sin snapshot al motor viejo.
        return json.loads(json.dumps(resultado, allow_nan=False))
    except Exception as e:
        return {"estado": "error", "error": f"{type(e).__name__}: {e}"}


def desde_snapshot(simbolo: str, temporalidad: str, dias: int, swing_length: int) -> dict | None:
    """Snapshot que refresco.py mantiene al día por vela, si fue calculado con los mismos
    parámetros; None si no hay (o Postgres caído) y el llamador calcula en fresco.
    Evita que 180 llamadas simultáneas de n8n descarguen Dukascopy a la vez (502)."""
    if _persistencia is None or dias != DIAS_POR_TEMPORALIDAD.get(temporalidad) or swing_length != SWING_LENGTH_POR_TEMPORALIDAD.get(temporalidad):
        return None
    try:
        snap = _persistencia.leer_snapshot(simbolo, temporalidad)
    except Exception:
        return None
    return snap["respuesta"] if snap and snap.get("respuesta") else None


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}

    temporalidad = payload.get("temporalidad") or None
    if temporalidad is not None and temporalidad not in TEMPORALIDAD_A_INTERVALO:
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDAD_A_INTERVALO)}"}

    try:
        dias_default = DIAS_POR_TEMPORALIDAD.get(temporalidad, 90)
        dias = int(payload.get("dias", dias_default))
        swing_length = int(payload.get("swing_length", SWING_LENGTH_POR_TEMPORALIDAD.get(temporalidad, 20)))
        ventana_fvg = int(payload.get("ventana_fvg", 5))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length'/'ventana_fvg' deben ser enteros"}

    # Cache hit — evita Dukascopy + analisis completo si el snapshot es fresco
    force_refresh = payload.get("_force_refresh", False)
    cache_key_temp = temporalidad or ""
    if not force_refresh and _persistencia is not None:
        try:
            snap = _persistencia.leer_snapshot(simbolo, cache_key_temp)
            if snap is not None and not _snapshot_viejo(snap):
                return 200, snap["respuesta"]
        except Exception:
            pass  # Postgres caido -> compute fresco

    fin = datetime.now(timezone.utc)
    desde_catalogo = str(payload.get("desde_catalogo", "")).lower() in ("1", "true", "yes")
    inicio = None
    if desde_catalogo and temporalidad and _persistencia is not None:
        try:
            fecha = _persistencia.leer_fecha_inicio(simbolo, temporalidad)
            if fecha is not None:
                inicio = datetime.combine(fecha, _time.min, tzinfo=timezone.utc)
        except Exception:
            pass
    if inicio is None:
        inicio = fin - timedelta(days=dias)

    try:
        if temporalidad:
            ohlc = obtener_velas(simbolo, inicio, fin, intervalo=TEMPORALIDAD_A_INTERVALO[temporalidad])
        else:
            ohlc = obtener_velas(simbolo, inicio, fin)
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        cuerpo = {"simbolo": simbolo, "velas": 0, "setups": []}
        if temporalidad:
            cuerpo.update({"temporalidad": temporalidad, "setups_confirmados": []})
        return 200, cuerpo

    resultado_motor = analizar(ohlc, swing_length=swing_length)
    setups = detectar_setups(ohlc, resultado_motor, ventana_fvg=ventana_fvg)
    setups_dict = setups.to_dict(orient="records")

    if not temporalidad:
        # Comportamiento historico sin cambios -- llamadores que no piden
        # temporalidad (T-01 Detector Activos, o un EA viejo sin ese input)
        # siguen recibiendo setups crudos, sin confirmacion.
        respuesta = {"simbolo": simbolo, "velas": len(ohlc), "setups": setups_dict}
        if _persistencia is not None:
            try:
                _persistencia.escribir_snapshot(simbolo, cache_key_temp, respuesta, None)
            except Exception:
                pass
        return 200, respuesta

    tendencia = obtener_tendencia(ohlc, swing_length=swing_length)
    tendencia_actual = tendencia.get("direccion")

    # también la dirección de la tendencia: /api/backtest la pide y la sirve desde este snapshot
    direcciones = {s["direccion"] for s in setups_dict}
    direcciones |= {ls for ls, cv in DIRECCION_LONG_SHORT_A_COMPRA_VENTA.items() if cv == tendencia_actual}
    reportes_por_direccion = {}
    for direccion_ls in direcciones:
        reportes_por_direccion[direccion_ls] = backtest_direccion(ohlc, direccion_ls, swing_length=swing_length)

    confirmados = []
    for s in setups_dict:
        direccion_cv = DIRECCION_LONG_SHORT_A_COMPRA_VENTA[s["direccion"]]
        reporte = reportes_por_direccion.get(s["direccion"], {})
        coincide_tendencia = direccion_cv == tendencia_actual
        muestra_suficiente = (reporte.get("n_setups") or 0) >= UMBRAL_SETUPS_SUFICIENTES
        rentable = bool(reporte.get("rentable_sin_optimizar"))
        s["confirmado"] = coincide_tendencia and muestra_suficiente and rentable

        razones = []
        if not coincide_tendencia:
            razones.append(f"tendencia del dia es {tendencia_actual}, no {direccion_cv}")
        if not muestra_suficiente:
            razones.append(f"solo {reporte.get('n_setups', 0)} setups en el backtest de esta direccion (umbral {UMBRAL_SETUPS_SUFICIENTES})")
        elif not rentable:
            razones.append("el backtest de esta direccion no es rentable_sin_optimizar")
        s["razon_confirmacion"] = "; ".join(razones) if razones else "coincide con la tendencia del dia y el backtest es rentable"

        if s["confirmado"]:
            confirmados.append(s)

    respuesta = {
        "simbolo": simbolo,
        "temporalidad": temporalidad,
        "velas": len(ohlc),
        "tendencia_actual": tendencia_actual,
        "swing_length": swing_length,
        "tendencia": tendencia,
        "backtests": {DIRECCION_LONG_SHORT_A_COMPRA_VENTA[ls]: r for ls, r in reportes_por_direccion.items()},
        "setups": setups_dict,
        "setups_confirmados": confirmados,
    }
    respuesta["motor_v2"] = motor_v2(simbolo, temporalidad, ohlc, inicio, fin)
    if _persistencia is not None:
        try:
            tendencia_prev = _persistencia.leer_ultima_tendencia(simbolo, cache_key_temp)
            _persistencia.escribir_snapshot(simbolo, cache_key_temp, respuesta, tendencia_actual)
            _persistencia.registrar_cambio_tendencia(simbolo, cache_key_temp, tendencia_actual, tendencia_prev)
        except Exception:
            pass
    return 200, respuesta


def _demo_aislamiento() -> None:
    """Sin red: motor_v2 nunca propaga y su salida es JSON estricto (monkeypatch de globals)."""
    import sys

    import pandas as pd

    from motor_smc.setups_v2 import COLUMNAS

    g = sys.modules[__name__]
    idx = pd.date_range("2026-01-05", periods=10, freq="h", tz="UTC")
    ohlc = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    vacio = pd.DataFrame(columns=COLUMNAS)
    ini, fin = idx[0].to_pydatetime(), idx[-1].to_pydatetime()

    def boom(*a, **k):
        raise RuntimeError("boom")

    orig = {n: getattr(g, n) for n in ("detectar_setups_v2", "obtener_velas", "backtest_v2")}
    try:
        g.obtener_velas = lambda *a, **k: ohlc
        g.detectar_setups_v2 = boom
        assert motor_v2("XAUUSD", "Intraday", ohlc, ini, fin)["estado"] == "error"

        g.detectar_setups_v2 = lambda *a, **k: vacio
        g.obtener_velas = boom
        r = motor_v2("XAUUSD", "Intraday", ohlc, ini, fin)
        assert r["estado"] == "sin_datos_temporalidad_mayor" and "error" in r, r

        g.obtener_velas = lambda *a, **k: ohlc
        g.backtest_v2 = lambda *a, **k: {"x": float("nan")}
        assert motor_v2("XAUUSD", "Intraday", ohlc, ini, fin)["estado"] == "error"

        g.backtest_v2 = orig["backtest_v2"]
        r = motor_v2("XAUUSD", "Intraday", ohlc, ini, fin)
        assert r["estado"] == "ok" and r["setups"] == [], r
        json.dumps(r, allow_nan=False)
    finally:
        for n, v in orig.items():
            setattr(g, n, v)
    print("api.setups._demo_aislamiento() OK — motor_v2 no propaga y devuelve JSON estricto")


def demo() -> None:
    _demo_aislamiento()
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["velas"] > 0
    assert "setups_confirmados" not in body  # sin temporalidad, comportamiento historico
    assert "motor_v2" not in body  # sin temporalidad no hay motor v2
    print(f"api.setups.demo() OK — {body['velas']} velas XAUUSD, {len(body['setups'])} setups (sin temporalidad)")

    status_t, body_t = procesar({"simbolo": "XAUUSD", "temporalidad": "Swing (H)"})
    assert status_t == 200
    assert body_t["temporalidad"] == "Swing (H)"
    assert "tendencia_actual" in body_t
    assert len(body_t["setups_confirmados"]) <= len(body_t["setups"])
    print(f"api.setups.demo() OK — XAUUSD Swing (H): tendencia {body_t['tendencia_actual']}, "
          f"{len(body_t['setups'])} setups crudos, {len(body_t['setups_confirmados'])} confirmados")

    v2 = body_t["motor_v2"]
    assert v2["estado"] in ("ok", "sin_datos_temporalidad_mayor"), v2
    assert (v2["vela"], v2["vela_mayor"]) == ("4H", "D")
    json.dumps(v2, allow_nan=False)  # el snapshot va a jsonb: sin NaN ni tipos numpy
    for s in v2["setups"]:
        assert s["valido"] or s["razon_descarte"], s  # todo descarte trae su razón
    assert all(s["valido"] for s in v2["setups_validos"])
    print(f"api.setups.demo() OK — motor_v2 {v2['vela']}->{v2['vela_mayor']}: embudo {v2.get('embudo')}")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo

    status_temp_mala, body_temp_mala = procesar({"simbolo": "XAUUSD", "temporalidad": "Diaria"})
    assert status_temp_mala == 400 and "error" in body_temp_mala


if __name__ == "__main__":
    demo()
