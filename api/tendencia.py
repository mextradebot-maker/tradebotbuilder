"""Lógica de GET/POST /api/tendencia?simbolo=XAUUSD&dias=90&temporalidad=Intraday
— invocado desde el router en api/analizar.py (mismo motivo que api/setups.py:
Vercel en modo single-entrypoint no auto-descubre este archivo).

Trae los datos históricos y determina el sesgo actual (compra/venta) del
símbolo — usado por T-04 (Telegram, entrega de robots) para avisarle al
usuario el momento del mercado antes de entregarle el robot y su backtest.

`temporalidad` es opcional (Scalping/Intraday/Swing, default Intraday=H1,
compatible con todos los llamadores existentes) — cada temporalidad tiene su
PROPIA tendencia, no son la misma dirección vista a distinta escala:
verificado con datos reales el 08 sep 2026, XAUUSD mostraba venta rentable en
H4 pero esa misma dirección no era rentable en H1. Por eso "swing solo si es
alcista" y comparar cuál temporalidad conviene más se resuelven por separado,
comparando la tendencia de cada intervalo, no reutilizando una sola dirección
para las tres (ver n8n "MTB Analisis Diario de Mercado").
"""

from datetime import datetime, timedelta, timezone

from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import obtener_tendencia


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}

    temporalidad = payload.get("temporalidad", "Intraday")
    if temporalidad not in TEMPORALIDAD_A_INTERVALO:
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDAD_A_INTERVALO)}"}

    try:
        dias = int(payload.get("dias", 90))
        swing_length = int(payload.get("swing_length", 20))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length' deben ser enteros"}

    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=dias)

    try:
        ohlc = obtener_velas(simbolo, inicio, fin, intervalo=TEMPORALIDAD_A_INTERVALO[temporalidad])
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        return 200, {"simbolo": simbolo, "temporalidad": temporalidad, "velas": 0, "direccion": "sin_definir", "fuente": "sin_datos", "razon": "Sin velas en el rango solicitado."}

    tendencia = obtener_tendencia(ohlc, swing_length=swing_length)
    return 200, {"simbolo": simbolo, "temporalidad": temporalidad, "velas": len(ohlc), **tendencia}


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["direccion"] in {"compra", "venta", "sin_definir"}
    assert body["temporalidad"] == "Intraday"
    print(f"api.tendencia.demo() OK — XAUUSD Intraday: {body['direccion']} ({body.get('fuente')})")

    status_swing, body_swing = procesar({"simbolo": "XAUUSD", "dias": 365, "temporalidad": "Swing"})
    assert status_swing == 200 and body_swing["temporalidad"] == "Swing"
    print(f"api.tendencia.demo() OK — XAUUSD Swing: {body_swing['direccion']}")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
