"""Lógica de GET/POST /api/backtest?simbolo=XAUUSD&direccion=compra&dias=365 —
invocado desde el router en api/analizar.py (mismo motivo que api/setups.py:
Vercel en modo single-entrypoint no auto-descubre este archivo).

Backtest bajo demanda para una dirección específica ('compra'/'venta', mismo
vocabulario que devuelve /api/tendencia) — usado por T-04 (Telegram) para
mostrarle al usuario el desempeño histórico real antes de entregarle el
robot, no solo el resultado ya guardado del backtest general del proyecto.
"""

from datetime import datetime, timedelta, timezone

from backtesting.backtest import backtest_direccion
from conectividad import SIMBOLOS, obtener_velas

DIRECCION_A_LONG_SHORT = {"compra": "long", "venta": "short"}


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    direccion = payload.get("direccion")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}
    if direccion not in DIRECCION_A_LONG_SHORT:
        return 400, {"error": "falta 'direccion' (compra / venta)"}

    try:
        dias = int(payload.get("dias", 365))
        swing_length = int(payload.get("swing_length", 20))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length' deben ser enteros"}

    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=dias)

    try:
        ohlc = obtener_velas(simbolo, inicio, fin)
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        return 200, {"simbolo": simbolo, "direccion": direccion, "velas": 0, "n_setups": 0, "rentable_sin_optimizar": None}

    reporte = backtest_direccion(ohlc, DIRECCION_A_LONG_SHORT[direccion], swing_length=swing_length)
    return 200, {"simbolo": simbolo, "direccion": direccion, "velas": len(ohlc), **reporte}


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "direccion": "compra", "dias": 365})
    assert status == 200
    assert "n_setups" in body
    print(f"api.backtest.demo() OK — XAUUSD compra 365d: {body}")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
