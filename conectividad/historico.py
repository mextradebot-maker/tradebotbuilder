"""Datos históricos vía dukascopy-python — reemplaza TickStory.

Plan de construcción, Paso 1b (Conectividad). `dukascopy_python.fetch()` ya
devuelve un DataFrame con columnas open/high/low/close/volume en minúsculas —
exactamente lo que pide `motor_smc.analizar()` — así que no hace falta
transformar nada, solo dar nombres de símbolo cómodos y fechas por defecto.
"""

from datetime import datetime

import dukascopy_python as dp
from dukascopy_python import instruments as inst

# Catálogo básico (ampliado 01 sep 2026, a petición de Ricardo — pendiente T-04 #1):
# metales + los 7 majors de forex. dukascopy soporta muchos más — pasar el string
# de dukascopy_python.instruments directo si hace falta uno que no esté aquí.
SIMBOLOS = {
    "XAUUSD": inst.INSTRUMENT_FX_METALS_XAU_USD,
    "XAGUSD": inst.INSTRUMENT_FX_METALS_XAG_USD,
    "EURUSD": inst.INSTRUMENT_FX_MAJORS_EUR_USD,
    "GBPUSD": inst.INSTRUMENT_FX_MAJORS_GBP_USD,
    "USDJPY": inst.INSTRUMENT_FX_MAJORS_USD_JPY,
    "AUDUSD": inst.INSTRUMENT_FX_MAJORS_AUD_USD,
    "USDCAD": inst.INSTRUMENT_FX_MAJORS_USD_CAD,
    "USDCHF": inst.INSTRUMENT_FX_MAJORS_USD_CHF,
    "NZDUSD": inst.INSTRUMENT_FX_MAJORS_NZD_USD,
}


def obtener_velas(
    simbolo: str,
    inicio: datetime,
    fin: datetime,
    intervalo: str = dp.INTERVAL_HOUR_1,
    offer_side: str = dp.OFFER_SIDE_BID,
):
    """Descarga velas históricas. `simbolo` acepta una clave de SIMBOLOS o un
    instrumento crudo de dukascopy_python.instruments (ej. "XAU/USD")."""
    instrumento = SIMBOLOS.get(simbolo, simbolo)
    return dp.fetch(instrumento, intervalo, offer_side, inicio, fin)


def demo() -> None:
    from motor_smc import analizar, detectar_setups

    # rango fijo en el pasado (dukascopy es dato historico real, no hay datos
    # "futuros" que descargar) — suficiente para validar la conexion completa
    ohlc = obtener_velas("XAUUSD", datetime(2024, 1, 1), datetime(2024, 2, 1))
    assert list(ohlc.columns) == ["open", "high", "low", "close", "volume"]
    assert len(ohlc) > 100, "se esperaban varios cientos de velas H1 en un mes"

    resultado = analizar(ohlc, swing_length=20)
    setups = detectar_setups(ohlc, resultado)
    print(f"conectividad.historico.demo() OK — {len(ohlc)} velas XAUUSD H1, {len(setups)} setups reales detectados")
    if len(setups):
        print(setups.head())


if __name__ == "__main__":
    demo()
