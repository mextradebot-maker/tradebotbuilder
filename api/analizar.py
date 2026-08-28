"""API — router único para todo /api/* (Vercel Python en modo single-entrypoint).

Con `[tool.vercel] entrypoint` declarado en pyproject.toml, Vercel invoca UN
solo handler para TODAS las rutas bajo /api/* — a diferencia de Next.js/Node,
NO auto-descubre cada archivo de api/ como su propia función serverless
(esto se confirmó en vivo: /api/setups devolvía la respuesta de /api/analizar
hasta que se agregó el router de abajo — ver bitácora). Por eso este módulo
despacha por path hacia la lógica de cada endpoint:

  POST /api/analizar          — analiza OHLC ya provisto (esta misma vela abajo)
  GET/POST /api/setups?simbolo=XAUUSD — ver api/setups.py (trae datos + analiza)
  GET/POST /api/tendencia?simbolo=XAUUSD&tipo=Intraday — ver api/tendencia.py
  GET/POST /api/backtest?simbolo=XAUUSD&direccion=compra — ver api/backtest.py
"""

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import pandas as pd

from motor_smc import analizar, detectar_setups

RUTA_SETUPS = "/api/setups"
RUTA_TENDENCIA = "/api/tendencia"
RUTA_BACKTEST = "/api/backtest"


def procesar(payload: dict) -> tuple[int, dict]:
    """POST /api/analizar — analiza OHLC ya provisto en el body."""
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
        partes = urlparse(self.path)
        ruta = partes.path.rstrip("/")
        qs = {k: v[0] for k, v in parse_qs(partes.query).items()}

        if ruta == RUTA_SETUPS:
            from api.setups import procesar as procesar_setups

            status, body = procesar_setups(qs)
        elif ruta == RUTA_TENDENCIA:
            from api.tendencia import procesar as procesar_tendencia

            status, body = procesar_tendencia(qs)
        elif ruta == RUTA_BACKTEST:
            from api.backtest import procesar as procesar_backtest

            status, body = procesar_backtest(qs)
        else:
            status, body = 200, {"uso": "POST /api/analizar con {'ohlc': [...]}. GET/POST /api/setups, /api/tendencia, /api/backtest — ver README"}
        self._responder(status, body)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            self._responder(400, {"error": "body inválido, se esperaba JSON"})
            return

        ruta = urlparse(self.path).path.rstrip("/")
        if ruta == RUTA_SETUPS:
            from api.setups import procesar as procesar_setups

            status, body = procesar_setups(payload)
        elif ruta == RUTA_TENDENCIA:
            from api.tendencia import procesar as procesar_tendencia

            status, body = procesar_tendencia(payload)
        elif ruta == RUTA_BACKTEST:
            from api.backtest import procesar as procesar_backtest

            status, body = procesar_backtest(payload)
        else:
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
