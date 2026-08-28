"""Lógica de GET/POST /api/setups?simbolo=XAUUSD&dias=90 — invocado desde el
router en api/analizar.py (ver ese módulo para el porqué: Vercel en modo
single-entrypoint no auto-descubre este archivo como su propia función).

A diferencia de /api/analizar (que solo analiza OHLC ya provisto), esto trae
los datos históricos él mismo (dukascopy) y corre el motor completo —
pensado para que n8n (T-01 Detector Activos) pregunte "¿hay setups reales en
XAUUSD/EURUSD ahora?" sin tener que conseguir velas por su cuenta (dukascopy
no es una API HTTP que n8n pueda golpear directo).
"""

from datetime import datetime, timedelta, timezone

from conectividad import SIMBOLOS, obtener_velas
from motor_smc import analizar, detectar_setups


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": f"falta 'simbolo' (uno de {list(SIMBOLOS)} o un instrumento crudo de dukascopy_python.instruments)"}

    try:
        dias = int(payload.get("dias", 90))
        swing_length = int(payload.get("swing_length", 20))
        ventana_fvg = int(payload.get("ventana_fvg", 5))
    except (TypeError, ValueError):
        return 400, {"error": "'dias'/'swing_length'/'ventana_fvg' deben ser enteros"}

    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=dias)

    try:
        ohlc = obtener_velas(simbolo, inicio, fin)
    except Exception as e:
        return 502, {"error": f"no se pudieron obtener velas de {simbolo}: {e}"}

    if ohlc.empty:
        return 200, {"simbolo": simbolo, "velas": 0, "setups": []}

    resultado = analizar(ohlc, swing_length=swing_length)
    setups = detectar_setups(ohlc, resultado, ventana_fvg=ventana_fvg)
    return 200, {"simbolo": simbolo, "velas": len(ohlc), "setups": setups.to_dict(orient="records")}


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["velas"] > 0
    print(f"api.setups.demo() OK — {body['velas']} velas XAUUSD, {len(body['setups'])} setups")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
