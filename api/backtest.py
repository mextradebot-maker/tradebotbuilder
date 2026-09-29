"""Lógica de GET/POST /api/backtest?simbolo=XAUUSD&direccion=compra&dias=365&temporalidad=Intraday
— invocado desde el router en api/analizar.py (mismo motivo que api/setups.py:
Vercel en modo single-entrypoint no auto-descubre este archivo).

Backtest bajo demanda para una dirección específica ('compra'/'venta', mismo
vocabulario que devuelve /api/tendencia) — usado por T-04 (Telegram) para
mostrarle al usuario el desempeño histórico real antes de entregarle el
robot, no solo el resultado ya guardado del backtest general del proyecto.

`temporalidad` es opcional (Scalping/Intraday/Swing, default Intraday=H1,
compatible con todos los llamadores existentes) — selecciona el intervalo
real de velas a descargar, para poder comparar el desempeño de un mismo
símbolo/dirección en distintas temporalidades (ver n8n "MTB Analisis Diario
de Mercado", que llama /api/tendencia + este endpoint 3 veces por símbolo —
una por temporalidad, cada una con su propia dirección — para elegir la más
rentable).

Con snapshot o sin él, el reporte es el backtest del motor v2 (el mismo de /api/setups): en un
miss se llama a api.setups.procesar con _force_refresh, que además deja el snapshot al día.
"""

from datetime import datetime, timedelta, timezone, time as _time

from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO
from api.setups import SWING_LENGTH_POR_TEMPORALIDAD

try:
    import persistencia as _persistencia
except Exception:
    _persistencia = None

DIRECCION_A_LONG_SHORT = {"compra": "long", "venta": "short"}


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    direccion = payload.get("direccion")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}
    if direccion not in DIRECCION_A_LONG_SHORT:
        return 400, {"error": "falta 'direccion' (compra / venta)"}

    temporalidad = payload.get("temporalidad", "Intraday")
    if temporalidad not in TEMPORALIDAD_A_INTERVALO:
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDAD_A_INTERVALO)}"}

    try:
        dias = int(payload.get("dias", 365))
        swing_length = int(payload.get("swing_length", SWING_LENGTH_POR_TEMPORALIDAD.get(temporalidad, 20)))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length' deben ser enteros"}

    desde_catalogo = str(payload.get("desde_catalogo", "")).lower() in ("1", "true", "yes")
    import api.setups as setups

    if not desde_catalogo:
        snap = setups.desde_snapshot(simbolo, temporalidad, dias, swing_length)
        reporte = (snap or {}).get("backtests", {}).get(direccion)
        if reporte is not None:
            return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad, "velas": snap.get("velas", 0), **reporte}

    status, respuesta = setups.procesar({"simbolo": simbolo, "temporalidad": temporalidad, "dias": dias,
                                         "swing_length": swing_length, "desde_catalogo": desde_catalogo,
                                         "_force_refresh": True})
    if status != 200:
        return status, respuesta
    # sin velas o motor v2 en error: no hay backtest de esa dirección
    reporte = (respuesta.get("backtests") or {}).get(direccion) or {"n_setups": 0, "rentable_sin_optimizar": None}
    extra = {}
    if desde_catalogo:  # misma fecha de inicio que usó procesar
        fin = datetime.now(timezone.utc)
        inicio = fin - timedelta(days=dias)
        if _persistencia is not None:
            try:
                fecha = _persistencia.leer_fecha_inicio(simbolo, temporalidad)
                if fecha is not None:
                    inicio = datetime.combine(fecha, _time.min, tzinfo=timezone.utc)
            except Exception:
                pass
        extra = {"desde_catalogo": True, "inicio": inicio.date().isoformat()}
    return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad,
                 "velas": respuesta.get("velas", 0), **reporte, **extra}


def _demo_miss_usa_motor_v2() -> None:
    """Sin red: en un miss la respuesta sale del mismo cálculo que /api/setups (motor v2)."""
    import api.setups as setups

    llamadas = []

    def falso(payload):
        llamadas.append(payload)
        return 200, {"velas": 42, "backtests": {"compra": {"n_setups": 7, "rentable_sin_optimizar": True},
                                                "venta": {"n_setups": 0, "rentable_sin_optimizar": None}}}

    orig = setups.procesar, setups.desde_snapshot
    try:
        setups.procesar, setups.desde_snapshot = falso, (lambda *a, **k: None)
        status, body = procesar({"simbolo": "XAUUSD", "direccion": "compra", "dias": 30, "temporalidad": "Scalping"})
        assert status == 200 and body == {"simbolo": "XAUUSD", "direccion": "compra", "temporalidad": "Scalping",
                                          "velas": 42, "n_setups": 7, "rentable_sin_optimizar": True}, body
        assert llamadas[0]["dias"] == 30 and llamadas[0]["_force_refresh"] is True, llamadas
        setups.procesar = lambda p: (502, {"error": "sin datos"})
        assert procesar({"simbolo": "XAUUSD", "direccion": "venta"})[0] == 502
    finally:
        setups.procesar, setups.desde_snapshot = orig
    print("api.backtest._demo_miss_usa_motor_v2() OK")


def demo() -> None:
    _demo_miss_usa_motor_v2()
    status, body = procesar({"simbolo": "XAUUSD", "direccion": "compra", "dias": 365})
    assert status == 200
    assert "n_setups" in body
    assert body["temporalidad"] == "Intraday"
    print(f"api.backtest.demo() OK — XAUUSD compra Intraday 365d: {body}")

    status_swing, body_swing = procesar({"simbolo": "XAUUSD", "direccion": "venta", "dias": 365, "temporalidad": "Swing (H)"})
    assert status_swing == 200 and body_swing["temporalidad"] == "Swing (H)"
    print(f"api.backtest.demo() OK — XAUUSD venta Swing (H) 365d: {body_swing}")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
