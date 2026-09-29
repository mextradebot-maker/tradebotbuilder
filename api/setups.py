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
antes (setups crudos del detector viejo, sin cruzar nada más) — para no romper
a T-01 ni a EAs ya desplegados que no la mandan.

Con ella, la respuesta viene del motor SMC v2 (motor_smc/setups_v2.py): un
setup queda "confirmado" (`setups_confirmados`) si cumple las reglas R1-R6 del
motor v2 (spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md), ya no
por "tendencia + backtest rentable". El backtest v2 se devuelve como
información, no gatea. `setups_confirmados` es SIEMPRE la última llave: el EA
toma la última ocurrencia de "direccion" tras `"setups_confirmados":[`.
"""

import json
from datetime import datetime, timedelta, timezone, time as _time

import dukascopy_python as dp

from backtesting.backtest import backtest_v2, simular_v2
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia
from motor_smc.setups_v2 import detectar_setups_v2, embudo

# ponytail: dias-por-temporalidad duplicado en api/mejor_indicador.py y en el
# job n8n "MTB Analisis Diario de Mercado" (nodo Consultar Backtest) -- si se
# ajusta aquí, ajustar ahí. swing_length vive solo aquí.

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

# Motor v2: mapa PROVISIONAL perfil -> (vela de entrada, temporalidad mayor).
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


_PRIMERO_EA = ("direccion", "entrada", "stop", "tp")


def _forma_ea(respuesta: dict) -> dict:
    """Copia con el orden que exige el parser del EA: en cada setup confirmado direccion,
    entrada, stop, tp primero (los busca HACIA ADELANTE desde la última "direccion"), y
    `setups_confirmados` al final. El snapshot vive en jsonb, que reordena las llaves."""
    confirmados = [{**{k: s[k] for k in _PRIMERO_EA if k in s}, **{k: v for k, v in s.items() if k not in _PRIMERO_EA}}
                   for s in respuesta.get("setups_confirmados") or []]
    return {**{k: v for k, v in respuesta.items() if k != "setups_confirmados"}, "setups_confirmados": confirmados}


def motor_v2(simbolo: str, temporalidad: str, ohlc, inicio, fin) -> dict:
    """Motor v2 (motor principal con temporalidad). Nunca propaga una excepción al llamador:
    ante un fallo devuelve estado "error" y `procesar` responde sin setups confirmados."""
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
        simulados = simular_v2(ohlc, setups)
        validos = [s for s in registros if s["valido"]]
        for s, res in zip(validos, simulados["resultado"]):  # simular_v2 conserva el orden de los válidos
            s["resultado"] = res
        resultado = {
            "estado": "ok", "vela": vela, "vela_mayor": vela_mayor,
            "setups": registros,
            # al EA solo va la orden todavía vigente: ni llenada, ni cancelada, ni expirada
            "setups_validos": [s for s in validos if s["resultado"] == "sin_llenar"],
            "embudo": embudo(setups),
            "backtests": backtest_v2(ohlc, setups, simulados),
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
                return 200, _forma_ea(snap["respuesta"]) if temporalidad else snap["respuesta"]
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
            cuerpo = _forma_ea({**cuerpo, "temporalidad": temporalidad, "setups_confirmados": []})
        return 200, cuerpo

    if not temporalidad:
        # Comportamiento historico sin cambios -- llamadores que no piden
        # temporalidad (T-01 Detector Activos, o un EA viejo sin ese input)
        # siguen recibiendo setups crudos, sin confirmacion.
        resultado_motor = analizar(ohlc, swing_length=swing_length)
        setups = detectar_setups(ohlc, resultado_motor, ventana_fvg=ventana_fvg)
        respuesta = {"simbolo": simbolo, "velas": len(ohlc), "setups": setups.to_dict(orient="records")}
        if _persistencia is not None:
            try:
                _persistencia.escribir_snapshot(simbolo, cache_key_temp, respuesta, None)
            except Exception:
                pass
        return 200, respuesta

    # tendencia: la usan registrar_cambio_tendencia, /api/tendencia y T-01
    tendencia = obtener_tendencia(ohlc, swing_length=swing_length)
    tendencia_actual = tendencia.get("direccion")

    v2 = motor_v2(simbolo, temporalidad, ohlc, inicio, fin)
    backtests = v2.get("backtests") or {}
    # `setups_confirmados` DEBE ser la ULTIMA llave: el EA toma la ultima ocurrencia de
    # "direccion" tras `"setups_confirmados":[` en todo el JSON.
    respuesta = {
        "simbolo": simbolo,
        "temporalidad": temporalidad,
        "velas": len(ohlc),
        "motor": "v2",
        "estado_motor": v2["estado"],  # "ok" | "sin_datos_temporalidad_mayor" | "error"
        "vela": v2.get("vela"),
        "vela_mayor": v2.get("vela_mayor"),
        "tendencia_actual": tendencia_actual,
        "swing_length": swing_length,
        "tendencia": tendencia,
        "embudo": v2.get("embudo"),
        "backtests": backtests.get("total", {}),  # {"compra": {...}, "venta": {...}}
        "backtests_por_tipo": {k: v for k, v in backtests.items() if k != "total"},
        "setups": v2.get("setups", []),
        "setups_confirmados": v2.get("setups_validos", []),
    }
    if v2["estado"] == "error":
        respuesta = {**{k: v for k, v in respuesta.items() if k != "setups_confirmados"},
                     "error_motor": v2.get("error"), "setups_confirmados": []}
    if _persistencia is not None:
        try:
            tendencia_prev = _persistencia.leer_ultima_tendencia(simbolo, cache_key_temp)
            _persistencia.escribir_snapshot(simbolo, cache_key_temp, respuesta, tendencia_actual)
            _persistencia.registrar_cambio_tendencia(simbolo, cache_key_temp, tendencia_actual, tendencia_prev)
        except Exception:
            pass
    return 200, _forma_ea(respuesta)


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

        # solo pasa a setups_validos (-> setups_confirmados) la orden todavía vigente ("sin_llenar")
        reglas = {n: {"cumple": True, "dato": None, "razon": ""} for n in ("R1", "R2", "R3", "R4", "R5", "R6")}
        fila = {c: None for c in COLUMNAS} | {"tipo": "continuacion", "direccion": "long", "valido": True,
                                              "razon_descarte": "", "reglas": reglas}
        ya_gano = fila | {"indice_conocido": 2, "entrada": 1.0, "stop": 0.2, "tp": 1.9, "zona_extremo": 0.3}
        vigente = fila | {"indice_conocido": 8, "entrada": 0.1, "stop": 0.05, "tp": 5.0, "zona_extremo": 0.08}
        g.detectar_setups_v2 = lambda *a, **k: pd.DataFrame([ya_gano, vigente], columns=COLUMNAS)
        r = motor_v2("XAUUSD", "Intraday", ohlc, ini, fin)
        assert [s["resultado"] for s in r["setups"]] == ["gano", "sin_llenar"], r["setups"]
        assert [s["indice_conocido"] for s in r["setups_validos"]] == [8], r["setups_validos"]
        assert r["backtests"]["total"]["compra"]["n_setups"] == 1
    finally:
        for n, v in orig.items():
            setattr(g, n, v)
    print("api.setups._demo_aislamiento() OK — motor_v2 no propaga y devuelve JSON estricto")


def _parser_ea(texto: str):
    """Copia en Python de ExtraerUltimoSetupConfirmado (robots/MexTradeBot_SeguidorSMC.mq5)."""
    marcador = texto.find('"setups_confirmados":[')
    if marcador < 0:
        return None
    ultimo, desde = -1, marcador
    while (p := texto.find('"direccion":"', desde)) >= 0:
        ultimo, desde = p, p + 1
    if ultimo < 0:
        return None

    def numero(campo):
        buscar = f'"{campo}":'
        pos = texto.find(buscar, ultimo)
        if pos < 0:
            return None
        pos += len(buscar)
        fin = pos
        while fin < len(texto) and (texto[fin].isdigit() or texto[fin] in ".-"):
            fin += 1
        return float(texto[pos:fin]) if fin > pos else None

    ini = ultimo + len('"direccion":"')
    return texto[ini : texto.find('"', ini)], numero("entrada"), numero("stop"), numero("tp")


def _demo_forma_ea() -> None:
    """Sin red: una respuesta que pasó por jsonb (llaves reordenadas) sigue siendo legible para el EA."""
    def jsonb(x):  # Postgres jsonb ordena las llaves por longitud y luego por bytes
        if isinstance(x, dict):
            return {k: jsonb(x[k]) for k in sorted(x, key=lambda k: (len(k), k))}
        return [jsonb(v) for v in x] if isinstance(x, list) else x

    def setup(d, e, s, tp):
        return {"tipo": "reversion", "direccion": d, "indice_conocido": 90, "entrada": e, "stop": s,
                "zona_extremo": s, "tp": tp, "valido": True, "razon_descarte": "",
                "reglas": {"R6": {"cumple": True, "dato": {"tp": tp, "rr": 2.5}, "razon": "x"}}}

    confirmados = [setup("short", 2650.5, 2660.0, 2620.0), setup("long", 2642.5, 2635.0, 2665.0)]
    respuesta = {"simbolo": "XAUUSD", "temporalidad": "Intraday", "velas": 100, "motor": "v2",
                 "setups": confirmados, "setups_confirmados": confirmados}
    compacto = lambda r: json.dumps(r, separators=(",", ":"))  # igual que api/analizar.py
    esperado = ("long", 2642.5, 2635.0, 2665.0)
    assert _parser_ea(compacto(respuesta)) == esperado
    cacheada = jsonb(respuesta)
    assert _parser_ea(compacto(cacheada)) != esperado  # el bug: jsonb rompe el parser del EA
    forma = _forma_ea(cacheada)
    assert _parser_ea(compacto(forma)) == esperado, compacto(forma)
    assert list(forma)[-1] == "setups_confirmados"
    assert _parser_ea(compacto(_forma_ea({**respuesta, "setups_confirmados": []}))) is None
    print("api.setups._demo_forma_ea() OK — el EA lee respuestas cacheadas en jsonb")


def demo() -> None:
    _demo_forma_ea()
    _demo_aislamiento()
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["velas"] > 0
    assert "setups_confirmados" not in body  # sin temporalidad, comportamiento historico
    assert "motor" not in body  # sin temporalidad no hay motor v2
    print(f"api.setups.demo() OK — {body['velas']} velas XAUUSD, {len(body['setups'])} setups (sin temporalidad)")

    status_t, body_t = procesar({"simbolo": "XAUUSD", "temporalidad": "Swing (H)"})
    assert status_t == 200
    assert body_t["temporalidad"] == "Swing (H)"
    assert "tendencia_actual" in body_t
    assert len(body_t["setups_confirmados"]) <= len(body_t["setups"])
    print(f"api.setups.demo() OK — XAUUSD Swing (H): tendencia {body_t['tendencia_actual']}, "
          f"{len(body_t['setups'])} setups crudos, {len(body_t['setups_confirmados'])} confirmados")

    assert list(body_t)[-1] == "setups_confirmados"  # contrato del parser del EA
    assert body_t["motor"] == "v2" and body_t["estado_motor"] in ("ok", "sin_datos_temporalidad_mayor")
    assert (body_t["vela"], body_t["vela_mayor"]) == ("4H", "D")
    json.dumps(body_t, allow_nan=False)  # el snapshot va a jsonb: sin NaN ni tipos numpy
    assert all(s["valido"] for s in body_t["setups_confirmados"])
    assert set(body_t["backtests"]) <= {"compra", "venta"}
    for s in body_t["setups"]:
        assert s["valido"] or s["razon_descarte"], s  # todo descarte trae su razón
    print(f"api.setups.demo() OK — motor v2 {body_t['vela']}->{body_t['vela_mayor']}: embudo {body_t.get('embudo')}")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo

    status_temp_mala, body_temp_mala = procesar({"simbolo": "XAUUSD", "temporalidad": "Diaria"})
    assert status_temp_mala == 400 and "error" in body_temp_mala


if __name__ == "__main__":
    demo()
