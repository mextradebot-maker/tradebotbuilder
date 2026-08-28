"""API — GET/POST /api/setups?simbolo=XAUUSD&dias=90

A diferencia de /api/analizar (que solo analiza OHLC ya provisto), este
endpoint trae los datos históricos él mismo (dukascopy) y corre el motor
completo — pensado para que n8n (T-01 Detector Activos) pregunte "¿hay
setups reales en XAUUSD/EURUSD ahora?" sin tener que conseguir velas por su
cuenta (dukascopy no es una API HTTP que n8n pueda golpear directo).
"""

import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        payload = {k: v[0] for k, v in qs.items()}
        status, body = procesar(payload)
        self._responder(status, body)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            self._responder(400, {"error": "body inválido, se esperaba JSON"})
            return
        status, body = procesar(payload)
        self._responder(status, body)

    def _responder(self, status: int, payload: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "dias": 90})
    assert status == 200
    assert body["velas"] > 0
    print(f"api.setups.demo() OK — {body['velas']} velas XAUUSD, {len(body['setups'])} setups")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
