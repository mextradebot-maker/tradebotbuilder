"""Exporta los datos de la calculadora de capital de la ficha de descarga. Correr en el VPS:

    cd C:\\MTB; python tools\\exportar_calculadora.py [C:\\MT5\\cuenta1\\terminal64.exe]

Une dos fuentes reales: (1) el MT5 de XM (contrato, tick, lote minimo por simbolo) y (2) los setups guardados del
backtest largo (distancia entrada-stop). Escribe calculadora_datos.json y lo copia al portapapeles.
Todo numero sale del broker o del motor; nada se escribe a mano.
"""

import json
import os
import statistics
import subprocess
import sys

sys.path.insert(0, os.getcwd())  # se corre desde C:\MTB; python pone tools\ en la ruta, no la raiz
os.environ["MTB_SIN_MIGRACIONES"] = "1"  # ponytail: solo lee; evita el ALTER TABLE de persistencia al importar

import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

from api.catalogo import SIMBOLO_XM
from persistencia.conexion import get_conn


def especificaciones(ruta: str) -> dict:
    if not mt5.initialize(path=ruta, portable=True):
        raise SystemExit(f"No se pudo conectar a {ruta}: {mt5.last_error()}")
    try:
        cuenta = mt5.account_info()
        out = {"_cuenta": {"moneda": cuenta.currency, "apalancamiento": cuenta.leverage}}
        for api, xm in SIMBOLO_XM.items():
            mt5.symbol_select(xm, True)
            i = mt5.symbol_info(xm)
            if i is None:
                out[api] = None
                continue
            out[api] = {"xm": xm, "contrato": i.trade_contract_size, "tick_size": i.trade_tick_size,
                        "tick_value": i.trade_tick_value, "vol_min": i.volume_min, "vol_step": i.volume_step,
                        "moneda_ganancia": i.currency_profit}
        return out
    finally:
        mt5.shutdown()


def stops() -> dict:
    """{(simbolo, temporalidad): [|entrada-stop|, ...]} de los setups validos guardados."""
    d: dict = {}
    with get_conn() as conn:
        for sim, tmp, res in conn.execute("SELECT simbolo, temporalidad, resultado FROM backtest_largo").fetchall():
            for s in (res or {}).get("setups") or []:
                try:
                    d.setdefault((sim, tmp), []).append(abs(float(s["entrada"]) - float(s["stop"])))
                except (KeyError, TypeError, ValueError):
                    pass
    return d


def main() -> None:
    ruta = sys.argv[1] if len(sys.argv) > 1 else r"C:\MT5\cuenta1\terminal64.exe"
    esp, st = especificaciones(ruta), stops()
    filas = []
    for (sim, tmp), v in sorted(st.items()):
        e = esp.get(sim)
        if not e or not v or not e["tick_size"]:
            continue
        v = sorted(v)
        por_lote = lambda dist: round(dist / e["tick_size"] * e["tick_value"], 2)  # USD que pierde 1 lote estandar
        filas.append({"s": sim, "t": tmp, "n": len(v), "med": por_lote(statistics.median(v)),
                      "p75": por_lote(v[int(0.75 * (len(v) - 1))])})
    datos = {"cuenta": esp["_cuenta"], "especificaciones": {k: x for k, x in esp.items() if k != "_cuenta"}, "stops": filas}
    texto = json.dumps(datos, separators=(",", ":"))
    with open("calculadora_datos.json", "w", encoding="utf-8") as f:
        f.write(texto)
    subprocess.run("clip", input=texto.encode("ascii", "replace"), check=True)
    print(f"OK: {len(esp) - 1} simbolos, {len(filas)} pares con stops, {len(texto)} bytes copiados al portapapeles")


if __name__ == "__main__":
    main()
