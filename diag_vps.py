"""Diagnostico de solo lectura para el VPS Windows (no opera, no hace login, no toca el EA).

  python diag_vps.py cuentas            # por terminal C:\\MT5\\cuentaN: login, servidor, netting/hedging
  python diag_vps.py spreads 60         # muestrea spreads (ask-bid) ~60 min -> C:\\MTB\\spreads.csv
  python diag_vps.py resumen            # mediana y p90 del spread por simbolo (en puntos) desde spreads.csv
"""
import csv
import os
import statistics
import sys
import time
from datetime import datetime, timezone

RUTA = r"C:\MT5\cuenta%d\terminal64.exe"
CSV = r"C:\MTB\spreads.csv"
MODOS = {0: "NETTING", 1: "EXCHANGE", 2: "HEDGING"}


def cuentas() -> None:
    import MetaTrader5 as mt5
    for n in range(1, 6):
        ruta = RUTA % n
        if not os.path.exists(ruta):
            print(f"cuenta{n}: no existe {ruta}")
            continue
        if not mt5.initialize(path=ruta, portable=True):
            print(f"cuenta{n}: no conecta {mt5.last_error()}")
            continue
        a = mt5.account_info()
        print(f"cuenta{n}: login {a.login} {a.server} {MODOS.get(a.margin_mode, a.margin_mode)} apalancamiento 1:{a.leverage} {a.currency}")
        mt5.shutdown()


def spreads(minutos: int) -> None:
    import MetaTrader5 as mt5
    from api.catalogo import SIMBOLO_XM
    if not mt5.initialize(path=RUTA % 1, portable=True):
        raise SystemExit(f"no conecta {mt5.last_error()}")
    nuevo = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(["utc", "simbolo", "puntos"])
        fin = time.time() + minutos * 60
        while time.time() < fin:
            ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            for api, xm in SIMBOLO_XM.items():
                t, i = mt5.symbol_info_tick(xm), mt5.symbol_info(xm)
                if t and i and t.ask > t.bid:
                    w.writerow([ahora, api, round((t.ask - t.bid) / i.point)])
            f.flush()
            time.sleep(60)
    mt5.shutdown()


def resumen() -> None:
    por: dict[str, list[int]] = {}
    with open(CSV) as f:
        for r in csv.DictReader(f):
            por.setdefault(r["simbolo"], []).append(int(r["puntos"]))
    for s, v in por.items():
        v.sort()
        print(f"{s:8} n={len(v):4} mediana={statistics.median(v)} p90={v[int(len(v) * 0.9) - 1] if len(v) > 1 else v[0]}")


if __name__ == "__main__":
    {"cuentas": lambda: cuentas(), "spreads": lambda: spreads(int(sys.argv[2])), "resumen": lambda: resumen()}[sys.argv[1]]()
