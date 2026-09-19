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

from datetime import datetime, timedelta, timezone

from backtesting.backtest import backtest_direccion
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia

# ponytail: umbral y dias-por-temporalidad duplicados en 3 lugares (este
# archivo, el job n8n "MTB Analisis Diario de Mercado", y el webhook "Panel
# Alumnos - Resumen") -- no hay todavia una fuente unica de configuracion
# compartida entre Python y n8n. Si se ajusta uno, ajustar los tres.
UMBRAL_SETUPS_SUFICIENTES = 20

DIAS_POR_TEMPORALIDAD = {
    "Scalping": 60,
    "Intraday": 365,
    "Swing (H)": 365,
    "Swing (S)": 1095,
    "Swing (M)": 2555,
}

DIRECCION_LONG_SHORT_A_COMPRA_VENTA = {"long": "compra", "short": "venta"}


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
        swing_length = int(payload.get("swing_length", 20))
        ventana_fvg = int(payload.get("ventana_fvg", 5))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length'/'ventana_fvg' deben ser enteros"}

    fin = datetime.now(timezone.utc)
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
        return 200, {"simbolo": simbolo, "velas": len(ohlc), "setups": setups_dict}

    tendencia = obtener_tendencia(ohlc, swing_length=swing_length)
    tendencia_actual = tendencia.get("direccion")

    reportes_por_direccion = {}
    for direccion_ls in {s["direccion"] for s in setups_dict}:
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

    return 200, {
        "simbolo": simbolo,
        "temporalidad": temporalidad,
        "velas": len(ohlc),
        "tendencia_actual": tendencia_actual,
        "setups": setups_dict,
        "setups_confirmados": confirmados,
    }


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["velas"] > 0
    assert "setups_confirmados" not in body  # sin temporalidad, comportamiento historico
    print(f"api.setups.demo() OK — {body['velas']} velas XAUUSD, {len(body['setups'])} setups (sin temporalidad)")

    status_t, body_t = procesar({"simbolo": "XAUUSD", "temporalidad": "Swing (H)"})
    assert status_t == 200
    assert body_t["temporalidad"] == "Swing (H)"
    assert "tendencia_actual" in body_t
    assert len(body_t["setups_confirmados"]) <= len(body_t["setups"])
    print(f"api.setups.demo() OK — XAUUSD Swing (H): tendencia {body_t['tendencia_actual']}, "
          f"{len(body_t['setups'])} setups crudos, {len(body_t['setups_confirmados'])} confirmados")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo

    status_temp_mala, body_temp_mala = procesar({"simbolo": "XAUUSD", "temporalidad": "Diaria"})
    assert status_temp_mala == 400 and "error" in body_temp_mala


if __name__ == "__main__":
    demo()
