"""Lógica de GET/POST /api/tendencia?simbolo=XAUUSD&tipo=Intraday&dias=90 —
invocado desde el router en api/analizar.py (mismo motivo que api/setups.py:
Vercel en modo single-entrypoint no auto-descubre este archivo).

Trae los datos históricos y determina el sesgo actual (compra/venta) del
símbolo — usado por T-04 (Telegram, entrega de robots) para avisarle al
usuario el momento del mercado antes de entregarle el robot y su backtest.
"""

from datetime import datetime, timedelta, timezone

from conectividad import SIMBOLOS, obtener_velas
from motor_smc import obtener_tendencia


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    tipo = payload.get("tipo")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}
    if not tipo:
        return 400, {"error": "falta 'tipo' (Scalping / Intraday / Swing)"}

    try:
        dias = int(payload.get("dias", 90))
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
        return 200, {"simbolo": simbolo, "tipo": tipo, "velas": 0, "direccion": "sin_definir", "fuente": "sin_datos", "razon": "Sin velas en el rango solicitado."}

    tendencia = obtener_tendencia(ohlc, tipo, swing_length=swing_length)
    return 200, {"simbolo": simbolo, "tipo": tipo, "velas": len(ohlc), **tendencia}


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "tipo": "Intraday", "dias": 90})
    assert status == 200
    assert body["direccion"] in {"compra", "venta", "sin_definir"}
    print(f"api.tendencia.demo() OK — XAUUSD Intraday: {body['direccion']} ({body.get('fuente')})")

    status_swing, body_swing = procesar({"simbolo": "XAUUSD", "tipo": "Swing", "dias": 90})
    assert status_swing == 200 and body_swing["direccion"] == "venta"

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
