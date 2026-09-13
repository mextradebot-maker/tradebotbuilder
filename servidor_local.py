"""Servidor HTTP local para T-05 (Monitor Demo MT5) — backend real, Paso T-05.

Expone GET /demo-status con el resumen semanal de P&L + drawdown de la cuenta
demo, para que el nodo "VPS Demo Status" de T-05 en n8n lo llame cada 6h. La
evaluación de negocio ("2 semanas rentables consecutivas, drawdown < 15%") la
sigue haciendo Claude en n8n (mismo patrón que T-01/T-03) — este servidor solo
garantiza que la aritmética (sumas semanales, drawdown) sea exacta y no algo
que el LLM tenga que sumar a mano sobre docenas de deals crudos.

Tiene que correr en ESTA máquina (o cualquier Windows con MT5 logueado):
MetaTrader5 es un puente IPC local, no es desplegable a Vercel — igual que
conectividad/xm.py, del que depende directamente.

Pendiente, fuera del alcance de este archivo: esta máquina no tiene IP pública
(ver checkpoint T-05 en memoria/manual-tecnico-interno.md), así que falta
decidir cómo el n8n del VPS Linux le llega a este puerto — un túnel
(Cloudflare Tunnel / ngrok / Tailscale) apuntando a este puerto resuelve eso
sin tocar este archivo.

Uso: python servidor_local.py   (puerto 8765 por default, override con PORT_DEMO_STATUS)
"""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5

from conectividad.xm import ConexionXMError, conectar, desconectar, historial_operaciones, info_cuenta

RUTA_DEMO_STATUS = "/demo-status"
PUERTO_DEFAULT = 8765


def calcular_resumen(cuenta: dict, deals: list[dict]) -> dict:
    """Agrega deals de cierre en P&L semanal (ISO) + drawdown máximo, en Python puro."""
    cierres = sorted(
        (d for d in deals if d.get("entry") == mt5.DEAL_ENTRY_OUT and d.get("profit") is not None),
        key=lambda d: d["time"],
    )

    semanas: dict[str, dict] = {}
    curva_acumulada = []
    acumulado = 0.0
    for d in cierres:
        neto = d["profit"] + d.get("swap", 0.0) + d.get("commission", 0.0)
        acumulado += neto
        curva_acumulada.append(acumulado)
        semana = datetime.fromtimestamp(d["time"], tz=timezone.utc).strftime("%G-W%V")
        bucket = semanas.setdefault(semana, {"semana": semana, "profit": 0.0, "operaciones": 0})
        bucket["profit"] += neto
        bucket["operaciones"] += 1

    # ponytail: asume sin depósitos/retiros manuales durante la ventana (razonable
    # para una demo dedicada a probar el EA) — si Ricardo deposita/retira a mano,
    # esto desvía el cálculo; agregar filtro por DEAL_TYPE_BALANCE si eso empieza a pasar.
    balance_inicial = cuenta["balance"] - acumulado
    pico = balance_inicial
    drawdown_max = 0.0
    for valor in curva_acumulada:
        saldo = balance_inicial + valor
        pico = max(pico, saldo)
        if pico > 0:
            drawdown_max = max(drawdown_max, (pico - saldo) / pico)

    return {
        "cuenta": {k: cuenta[k] for k in ("login", "server", "balance", "equity", "currency") if k in cuenta},
        "semanas": [semanas[k] for k in sorted(semanas)],
        "drawdown_pct": round(drawdown_max * 100, 2),
        "num_operaciones": len(cierres),
    }


def resumen_demo(dias: int = 60) -> dict:
    conectar()
    try:
        return calcular_resumen(info_cuenta(), historial_operaciones(dias=dias))
    finally:
        desconectar()


def procesar(qs: dict) -> tuple[int, dict]:
    try:
        dias = int(qs.get("dias", 60))
    except ValueError:
        return 400, {"error": "'dias' debe ser un entero"}
    try:
        return 200, resumen_demo(dias=dias)
    except ConexionXMError as e:
        return 502, {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        partes = urlparse(self.path)
        if partes.path.rstrip("/") != RUTA_DEMO_STATUS:
            self._responder(404, {"error": f"usa GET {RUTA_DEMO_STATUS}"})
            return
        qs = {k: v[0] for k, v in parse_qs(partes.query).items()}
        status, body = procesar(qs)
        self._responder(status, body)

    def _responder(self, status: int, payload: dict) -> None:
        cuerpo = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


def demo() -> None:
    cuenta = {"login": 318680674, "server": "XMGlobal-MT5 7", "balance": 10500.0, "equity": 10500.0, "currency": "USD"}

    def ts(dia: int, hora: int) -> int:
        return int(datetime(2026, 8, dia, hora, tzinfo=timezone.utc).timestamp())

    # 3 semanas ISO (lunes 3, 10, 17 ago 2026): +300 / -250 / -250 netos, para
    # ejercitar tanto la suma semanal como un drawdown real (pico en semana 1).
    deals = [
        {"time": ts(3, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": 200.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(4, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 100.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(10, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -50.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(11, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 300.0, "swap": -1.0, "commission": -2.0},
        {"time": ts(17, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -400.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(18, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 150.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(19, 9), "entry": mt5.DEAL_ENTRY_IN, "profit": 0.0, "swap": 0.0, "commission": 0.0},  # apertura, debe ignorarse
    ]

    resumen = calcular_resumen(cuenta, deals)
    assert resumen["num_operaciones"] == 6, resumen  # la apertura (ENTRY_IN) no cuenta
    semanas = {s["semana"]: s for s in resumen["semanas"]}
    assert len(semanas) == 3
    assert round(semanas["2026-W32"]["profit"], 2) == 300.0
    assert round(semanas["2026-W33"]["profit"], 2) == 247.0  # -50 + (300 - 1 swap - 2 commission)
    assert round(semanas["2026-W34"]["profit"], 2) == -250.0
    assert resumen["drawdown_pct"] == 3.72, resumen["drawdown_pct"]  # pico 10750 -> valle 10350

    status, body = procesar({"dias": "no-es-numero"})
    assert status == 400 and "error" in body

    print(f"servidor_local.demo() OK — {resumen}")


if __name__ == "__main__":
    demo()

    puerto = int(os.environ.get("PORT_DEMO_STATUS", PUERTO_DEFAULT))
    print(f"Sirviendo GET {RUTA_DEMO_STATUS} en http://localhost:{puerto} (Ctrl+C para detener)")
    HTTPServer(("0.0.0.0", puerto), Handler).serve_forever()
