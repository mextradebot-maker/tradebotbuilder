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
"""

from datetime import datetime, timedelta, timezone, time as _time

from backtesting.backtest import backtest_direccion
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas

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
        swing_length = int(payload.get("swing_length", 20))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length' deben ser enteros"}

    fin = datetime.now(timezone.utc)
    desde_catalogo = str(payload.get("desde_catalogo", "")).lower() in ("1", "true", "yes")
    inicio = None
    if desde_catalogo and _persistencia is not None:
        try:
            fecha = _persistencia.leer_fecha_inicio(simbolo, temporalidad)
            if fecha is not None:
                inicio = datetime.combine(fecha, _time.min, tzinfo=timezone.utc)
        except Exception:
            pass
    if inicio is None:
        inicio = fin - timedelta(days=dias)

    try:
        ohlc = obtener_velas(simbolo, inicio, fin, intervalo=TEMPORALIDAD_A_INTERVALO[temporalidad])
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad, "velas": 0, "n_setups": 0, "rentable_sin_optimizar": None}

    reporte = backtest_direccion(ohlc, DIRECCION_A_LONG_SHORT[direccion], swing_length=swing_length)
    extra = {"desde_catalogo": desde_catalogo, "inicio": inicio.date().isoformat()} if desde_catalogo else {}
    return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad, "velas": len(ohlc), **reporte, **extra}


def demo() -> None:
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
