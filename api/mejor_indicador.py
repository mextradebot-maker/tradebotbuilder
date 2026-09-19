"""Lógica de GET/POST /api/mejor-indicador?simbolo=XAUUSD&direccion=compra&temporalidad=Intraday
— invocado desde el router en api/analizar.py (mismo motivo que api/setups.py:
Vercel en modo single-entrypoint no auto-descubre este archivo).

Corre `backtesting.comparar_indicadores` — compara los 4 indicadores de
confluencia (RSI, Medias Moviles, MACD, Fibonacci) contra no usar ninguno,
decidiendo SIEMPRE sobre el tramo out-of-sample (ver docstring de ese módulo
y de backtesting/backtest.py — "3 reglas de oro"). Pensado para correr una
vez por combinación símbolo+temporalidad+dirección (no en cada vela, como
/api/setups) y guardar el resultado — quién lo guarda y cómo se "pega" al
robot entregado es una decisión de la capa que orquesta la entrega (T-04 en
n8n), todavía pendiente de implementar (ver checkpoint de esta sesión).
"""

from datetime import datetime, timedelta, timezone

from backtesting.comparar_indicadores import comparar_indicadores
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas

DIRECCION_A_LONG_SHORT = {"compra": "long", "venta": "short"}

DIAS_POR_TEMPORALIDAD = {
    "Scalping": 60,
    "Intraday": 365,
    "Swing (H)": 365,
    "Swing (S)": 1095,
    "Swing (M)": 2555,
}


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}

    direccion = payload.get("direccion")
    if direccion not in DIRECCION_A_LONG_SHORT:
        return 400, {"error": "falta 'direccion' (compra / venta)"}

    temporalidad = payload.get("temporalidad", "Intraday")
    if temporalidad not in TEMPORALIDAD_A_INTERVALO:
        return 400, {"error": f"'temporalidad' debe ser una de {list(TEMPORALIDAD_A_INTERVALO)}"}

    try:
        dias = int(payload.get("dias", DIAS_POR_TEMPORALIDAD.get(temporalidad, 365)))
        swing_length = int(payload.get("swing_length", 20))
        fraccion_out_of_sample = float(payload.get("fraccion_out_of_sample", 0.25))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length' deben ser enteros, 'fraccion_out_of_sample' un decimal"}

    if not (0 < fraccion_out_of_sample < 1):
        return 400, {"error": "'fraccion_out_of_sample' debe estar entre 0 y 1"}

    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=dias)

    try:
        ohlc = obtener_velas(simbolo, inicio, fin, intervalo=TEMPORALIDAD_A_INTERVALO[temporalidad])
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad, "velas": 0, "mejor_indicador": None}

    resultado = comparar_indicadores(
        ohlc,
        DIRECCION_A_LONG_SHORT[direccion],
        swing_length=swing_length,
        fraccion_out_of_sample=fraccion_out_of_sample,
    )
    return 200, {"simbolo": simbolo, "direccion": direccion, "temporalidad": temporalidad, "velas": len(ohlc), **resultado}


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "direccion": "compra", "temporalidad": "Swing (H)"})
    assert status == 200
    assert "mejor_indicador" in body
    print(f"api.mejor_indicador.demo() OK — XAUUSD compra Swing (H): mejor_indicador={body['mejor_indicador']}")

    status_malo, body_malo = procesar({"simbolo": "XAUUSD"})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
