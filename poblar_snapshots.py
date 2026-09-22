"""Script batch para descargar velas y poblar `smc_snapshot` en Postgres.

Pobla los pares de las 5 cuentas demo principales y del catálogo completo.
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.setups import procesar as procesar_setups
from persistencia.conexion import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 5 cuentas demo principales
PARES_PRINCIPALES = [
    ("WTIUSD", "Swing (S)"),
    ("EURUSD", "Scalping"),
    ("USDJPY", "Intraday"),
    ("GOLD", "Intraday"),
    ("GOLD", "Swing (S)"),
]


def poblar_snapshots_principales() -> None:
    logging.info("=== INICIANDO POBLADO DE SNAPSHOTS SMC EN POSTGRES ===")

    for simbolo, temporalidad in PARES_PRINCIPALES:
        logging.info(f"Refrescando snapshot para {simbolo} [{temporalidad}]...")
        try:
            status, res = procesar_setups({"simbolo": simbolo, "temporalidad": temporalidad, "_force_refresh": True})
            if status == 200:
                tendencia = res.get("tendencia_actual")
                setups_cnt = len(res.get("setups", []))
                logging.info(f"✅ {simbolo} [{temporalidad}] -> Tendencia: {tendencia} | Setups: {setups_cnt}")
            else:
                logging.error(f"❌ Error HTTP {status} al procesar {simbolo} [{temporalidad}]: {res}")
        except Exception as e:
            logging.error(f"❌ Excepción al procesar {simbolo} [{temporalidad}]: {e}")

    logging.info("=== POBLADO DE SNAPSHOTS COMPLETADO ===")


if __name__ == "__main__":
    poblar_snapshots_principales()
