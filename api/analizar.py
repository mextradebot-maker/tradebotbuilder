"""API mínima sobre el motor SMC — POST /api/analizar.

Body: {"ohlc": [{"open":..,"high":..,"low":..,"close":..}, ...], "swing_length"?: int, "ventana_fvg"?: int}
Respuesta: {"setups": [...]} — ver motor_smc.setup_ob_fvg para el formato de cada setup.

Vercel detecta cualquier archivo bajo api/ como función serverless propia — no
hace falta vercel.json ni un entrypoint único (rung 4 de la escalera: la
plataforma ya resuelve el ruteo).
"""

import json
from http.server import BaseHTTPRequestHandler

import pandas as pd

from motor_smc import analizar, detectar_setups


def procesar(payload: dict) -> tuple[int, dict]:
    velas = payload.get("ohlc")
    if not velas:
        return 400, {"error": "falta 'ohlc': lista de velas con open/high/low/close"}
    try:
        ohlc = pd.DataFrame(velas)
        resultado = analizar(ohlc, swing_length=payload.get("swing_length", 50))
        setups = detectar_setups(ohlc, resultado, ventana_fvg=payload.get("ventana_fvg", 5))
    except (ValueError, KeyError) as e:
        return 400, {"error": str(e)}
    return 200, {"setups": setups.to_dict(orient="records")}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._responder(200, {"uso": "POST con {'ohlc': [...]} — ver README"})

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
    import numpy as np

    rng = np.random.default_rng(190)
    n = 500
    tramo = np.concatenate([np.linspace(0, 40, 120), np.linspace(40, -20, 160), np.linspace(-20, 30, 220)])
    close = 2000 + tramo + np.cumsum(rng.normal(0, 0.8, n))
    # vectorizado (no por fila) — para reproducir el mismo escenario del seed 190
    # ya validado en motor_smc.setup_ob_fvg.demo(); llamar rng.* dentro de un loop
    # por fila desordena la secuencia y deja de garantizar al menos un setup.
    open_ = close + rng.normal(0, 0.5, n)
    high = close + rng.uniform(0.5, 2.5, n)
    low = close - rng.uniform(0.5, 2.5, n)
    volumen = rng.uniform(100, 1000, n)
    velas = [
        {"open": float(open_[i]), "high": float(high[i]), "low": float(low[i]), "close": float(close[i]), "volume": float(volumen[i])}
        for i in range(n)
    ]

    # swing_length=8 reproduce el mismo escenario ya validado en
    # motor_smc.setup_ob_fvg.demo() para este seed; el default (50) es para
    # datos reales de más velas, no para este fixture sintético de 500.
    status, body = procesar({"ohlc": velas, "swing_length": 8})
    assert status == 200
    assert "setups" in body and len(body["setups"]) >= 1

    status_vacio, body_vacio = procesar({})
    assert status_vacio == 400 and "error" in body_vacio

    print(f"api.analizar.demo() OK — status={status}, {len(body['setups'])} setups")


if __name__ == "__main__":
    demo()
