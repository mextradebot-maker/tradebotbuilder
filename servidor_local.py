"""Servidor HTTP local para T-05 (Monitor Demo MT5) — backend real, Paso T-05.

Expone GET /demo-status con el resumen semanal de P&L + drawdown de las 5
cuentas demo del Master Trader. El nodo "VPS Demo Status" de T-05 en n8n lo
llama cada 6h. La evaluación de negocio la sigue haciendo Claude en n8n.

Credenciales: la cuenta 1 usa XM_LOGIN/XM_PASSWORD/XM_SERVER (igual que antes).
Las cuentas 2-5 usan XM_LOGIN_2..5 / XM_PASSWORD_2..5 / XM_SERVER_2..5.
Las que no tengan credenciales configuradas se reportan con "error": "no configurada".

Usa: python servidor_local.py   (puerto 8765, override con PORT_DEMO_STATUS)
"""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import MetaTrader5 as mt5
from dotenv import load_dotenv

from conectividad.xm import ConexionXMError, conectar, desconectar, historial_operaciones, info_cuenta, login_cuenta

load_dotenv()

RUTA_DEMO_STATUS = "/demo-status"
PUERTO_DEFAULT = 8765


def _cuentas_demo() -> list[dict]:
    """Lee hasta 5 slots de credenciales desde .env (sufijos "", "_2" … "_5").

    Devuelve lista de dicts con login/password/server; omite slots sin XM_LOGIN*.
    """
    cuentas = []
    for sufijo in ("", "_2", "_3", "_4", "_5"):
        login = os.environ.get(f"XM_LOGIN{sufijo}")
        pw = os.environ.get(f"XM_PASSWORD{sufijo}")
        srv = os.environ.get(f"XM_SERVER{sufijo}")
        if login and pw and srv:
            cuentas.append({"login": int(login), "password": pw, "server": srv})
    return cuentas


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


def resumen_todas(dias: int = 60) -> dict:
    """Reporta las N cuentas demo configuradas en .env.

    Si una cuenta falla (credenciales incorrectas, servidor caído), la incluye
    con "error": "..." en vez de abortar el request completo.
    """
    cuentas = _cuentas_demo()
    if not cuentas:
        raise ConexionXMError("No hay cuentas configuradas (verifica XM_LOGIN en .env)")

    conectar()
    try:
        resultados = []
        for c in cuentas:
            try:
                login_cuenta(c["login"], c["password"], c["server"])
                resultados.append(calcular_resumen(info_cuenta(), historial_operaciones(dias=dias)))
            except ConexionXMError as e:
                resultados.append({"cuenta": {"login": c["login"], "server": c["server"]}, "error": str(e)})
        return {"cuentas": resultados}
    finally:
        desconectar()


def procesar(qs: dict) -> tuple[int, dict]:
    try:
        dias = int(qs.get("dias", 60))
    except ValueError:
        return 400, {"error": "'dias' debe ser un entero"}
    try:
        return 200, resumen_todas(dias=dias)
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

    deals = [
        {"time": ts(3, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": 200.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(4, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 100.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(10, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -50.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(11, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 300.0, "swap": -1.0, "commission": -2.0},
        {"time": ts(17, 10), "entry": mt5.DEAL_ENTRY_OUT, "profit": -400.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(18, 15), "entry": mt5.DEAL_ENTRY_OUT, "profit": 150.0, "swap": 0.0, "commission": 0.0},
        {"time": ts(19, 9), "entry": mt5.DEAL_ENTRY_IN, "profit": 0.0, "swap": 0.0, "commission": 0.0},
    ]

    resumen = calcular_resumen(cuenta, deals)
    assert resumen["num_operaciones"] == 6, resumen
    semanas = {s["semana"]: s for s in resumen["semanas"]}
    assert len(semanas) == 3
    assert round(semanas["2026-W32"]["profit"], 2) == 300.0
    assert round(semanas["2026-W33"]["profit"], 2) == 247.0
    assert round(semanas["2026-W34"]["profit"], 2) == -250.0
    assert resumen["drawdown_pct"] == 3.72, resumen["drawdown_pct"]

    status, body = procesar({"dias": "no-es-numero"})
    assert status == 400 and "error" in body

    print(f"servidor_local.demo() OK — calcular_resumen pasa; resumen_todas requiere MT5 en vivo")


if __name__ == "__main__":
    demo()

    puerto = int(os.environ.get("PORT_DEMO_STATUS", PUERTO_DEFAULT))
    print(f"Sirviendo GET {RUTA_DEMO_STATUS} en http://localhost:{puerto} (Ctrl+C para detener)")
    HTTPServer(("0.0.0.0", puerto), Handler).serve_forever()
