"""Carga el historial completo de velas de XM (MT5) al servidor, por lotes. Corre en el VPS Windows desde C:\\MTB.

Por cada activo del catalogo baja 1H y 15m del MT5 (con el spread por vela), pasa la hora del servidor a UTC, recorta el
principio donde XM solo tiene 1 barra por dia y lo manda a POST /api/v1/velas-xm/carga (tabla velas_xm, aparte de
Dukascopy). Al terminar cada activo manda {"cerrar": true}: el servidor arma el D y marca la carga completa.
Es seguro repetirlo o retomarlo: el servidor hace upsert.

Uso (VPS, desde C:\\MTB):  python cargar_historial_xm.py                  # los 34 activos
                           python cargar_historial_xm.py XAUUSD EURUSD   # solo esos (nombres de la API)
                           python cargar_historial_xm.py --seco XAUUSD   # prepara y cuenta, sin enviar
Variables (.env): MTB_SERVICE_KEY (la misma del servidor), MTB_API_URL (por defecto https://mextradebot.com.mx),
COLECTOR_MT5_PATH (por defecto C:\\MT5\\cuenta1\\terminal64.exe). Requiere "Max. de barras en el grafico" = Unlimited.
"""
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from conectividad.historial_xm import preparar_mt5

LOTE = 5000  # velas por POST (~600 KB de JSON)
SERIES = (("1H", "TIMEFRAME_H1"), ("15m", "TIMEFRAME_M15"))
UA = "MTB-Colector/1.0"  # Cloudflare bloquea el User-Agent por defecto de urllib


def leer_mt5(mt5, xm: str, tf) -> pd.DataFrame:
    """Todo el historial del simbolo, anio por anio (un rango enorme de golpe a veces devuelve None)."""
    mt5.symbol_select(xm, True)
    fin = datetime.now() + timedelta(days=1)  # naive: mt5 lo toma como UTC
    partes = []
    for y in range(2000, fin.year + 1):
        r = mt5.copy_rates_range(xm, tf, datetime(y, 1, 1), min(datetime(y + 1, 1, 1), fin))
        if r is None:
            raise RuntimeError(f"{xm}: copy_rates_range fallo en {y}: {mt5.last_error()}")
        if len(r):
            partes.append(pd.DataFrame(r))
    return pd.concat(partes) if partes else pd.DataFrame()


def post(url: str, clave: str, cuerpo: dict, intentos: int = 4) -> dict:
    import requests
    for i in range(intentos):
        try:
            r = requests.post(url, json=cuerpo, headers={"X-MTB-Service-Key": clave, "User-Agent": UA}, timeout=180)
        except requests.RequestException as e:
            logging.warning("envio fallo (%s), intento %d/%d", e, i + 1, intentos)
        else:
            if r.status_code == 200:
                return r.json()
            if 400 <= r.status_code < 500:  # un 4xx no se arregla reintentando
                raise SystemExit(f"El servidor rechazo el lote ({r.status_code}): {r.text[:300]}")
            logging.warning("servidor %s: %s, intento %d/%d", r.status_code, r.text[:200], i + 1, intentos)
        time.sleep(5 * (i + 1))
    raise SystemExit("No se pudo enviar el lote tras varios intentos; vuelve a correr el script (retoma sin duplicar)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    seco = "--seco" in sys.argv
    elegidos = [a for a in sys.argv[1:] if not a.startswith("--")]
    from dotenv import load_dotenv
    load_dotenv()
    import MetaTrader5 as mt5
    from api.catalogo import SIMBOLO_XM

    desconocidos = [a for a in elegidos if a not in SIMBOLO_XM]
    if desconocidos:
        raise SystemExit(f"No estan en el catalogo: {desconocidos}")
    activos = {a: SIMBOLO_XM[a] for a in (elegidos or sorted(SIMBOLO_XM))}
    clave = os.environ.get("MTB_SERVICE_KEY")
    if not seco and not clave:
        raise SystemExit("Falta MTB_SERVICE_KEY en .env")
    url = os.environ.get("MTB_API_URL", "https://mextradebot.com.mx") + "/api/v1/velas-xm/carga"
    ruta = os.environ.get("COLECTOR_MT5_PATH", r"C:\MT5\cuenta1\terminal64.exe")
    if not mt5.initialize(path=ruta, portable=True):
        raise SystemExit(f"No se pudo conectar a la terminal MT5 {ruta}: {mt5.last_error()}")
    ahora = pd.Timestamp.now(tz="UTC")
    for api, xm in activos.items():
        for serie, nombre_tf in SERIES:
            crudo = leer_mt5(mt5, xm, getattr(mt5, nombre_tf))
            if crudo.empty:
                logging.warning("%s %s: sin datos en el MT5", api, serie)
                continue
            velas, desc = preparar_mt5(crudo, serie, ahora)
            logging.info("%s (%s) %s: %s", api, xm, serie, desc)
            if velas.empty:
                continue
            logging.info("%s %s: %s -> %s", api, serie, velas.index[0], velas.index[-1])
            if seco:
                continue
            for i in range(0, len(velas), LOTE):
                tr = velas.iloc[i:i + LOTE]
                lote = [{"ts": int(t.timestamp()), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume, "s": r.spread}
                        for t, r in zip(tr.index, tr.itertuples())]
                post(url, clave, {"simbolo": api, "serie": serie, "velas": lote})
                if (i // LOTE) % 20 == 0:
                    logging.info("%s %s: %d/%d", api, serie, min(i + LOTE, len(velas)), len(velas))
        if not seco:
            logging.info("%s cerrado: %s", api, post(url, clave, {"simbolo": api, "cerrar": True}))
    mt5.shutdown()
    logging.info("listo")


if __name__ == "__main__":
    main()
