"""Exporta los simbolos reales del MT5 de XM y propone el nombre XM de cada simbolo del catalogo.

Uso en el VPS:  cd C:\\MTB; python exportar_simbolos_xm.py
  (opcional: ruta de otra terminal, p.ej. python exportar_simbolos_xm.py C:\\MT5\\cuenta2\\terminal64.exe)
Escribe C:\\MTB\\simbolos_xm.csv con TODOS los simbolos (nombre, descripcion, ruta) y muestra
en pantalla, para los 36 simbolos del catalogo, los candidatos XM. Con esa salida se llena
SIMBOLO_XM en api/catalogo.py (solo nombres confirmados por XM).
"""

import csv
import sys

import MetaTrader5 as mt5

from compilador import SIMBOLOS

CLAVES = {
    "XAUUSD": ["GOLD", "XAUUSD"], "XAGUSD": ["SILVER", "XAGUSD"],
    "XPTUSD": ["PLATINUM", "XPTUSD"], "XPDUSD": ["PALLADIUM", "XPDUSD"],
    "US30": ["US30", "DOW", "DJ30"], "US100": ["US100", "NAS100", "NASDAQ", "USTEC"],
    "US500": ["US500", "SP500", "S&P"], "GER40": ["GER40", "DAX", "DE40"],
    "UK100": ["UK100", "FTSE"], "JP225": ["JP225", "NIKKEI", "JPN225"],
    "WTIUSD": ["OILCASH", "WTI", "CRUDE"], "BRENTUSD": ["BRENT", "UKOIL"],
    "BTCUSD": ["BTCUSD", "BITCOIN"], "ETHUSD": ["ETHUSD", "ETHEREUM"], "XRPUSD": ["XRPUSD", "RIPPLE"],
    "AMXL": ["AMERICA MOVIL", "AMX"], "CEMEXCPO": ["CEMEX"], "GOOGL": ["ALPHABET", "GOOGL"],
    "NVDA": ["NVIDIA", "NVDA"], "META": ["META PLATFORMS", "FACEBOOK"], "WMT": ["WALMART", "WMT"],
}


def candidatos(api: str, todos) -> list:
    claves = CLAVES.get(api, [api])  # forex: el propio par
    hallados = [s for s in todos if any(k in s.name.upper() or k in (s.description or "").upper() for k in claves)]
    return sorted(hallados, key=lambda s: (s.name.upper() != api, len(s.name)))  # exacto primero


def main() -> None:
    ruta = sys.argv[1] if len(sys.argv) > 1 else r"C:\MT5\cuenta1\terminal64.exe"
    if not mt5.initialize(path=ruta, portable=True):
        raise SystemExit(f"No se pudo conectar a {ruta}: {mt5.last_error()}")
    try:
        todos = mt5.symbols_get() or []
    finally:
        mt5.shutdown()
    with open("simbolos_xm.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nombre", "descripcion", "ruta"])
        w.writerows([s.name, s.description, s.path] for s in todos)
    print(f"{len(todos)} simbolos en XM -> simbolos_xm.csv\n")
    for api in SIMBOLOS:
        c = candidatos(api, todos)[:5]
        print(f"{api:9}| " + (" ; ".join(f"{s.name} [{s.path.split(chr(92))[0]}]" for s in c) or "SIN COINCIDENCIAS"))


if __name__ == "__main__":
    main()
