"""Datos históricos vía dukascopy-python — reemplaza TickStory.

Plan de construcción, Paso 1b (Conectividad). `dukascopy_python.fetch()` ya
devuelve un DataFrame con columnas open/high/low/close/volume en minúsculas —
exactamente lo que pide `motor_smc.analizar()` — así que no hace falta
transformar nada, solo dar nombres de símbolo cómodos y fechas por defecto.
"""

from datetime import datetime

import dukascopy_python as dp
from dukascopy_python import instruments as inst

# Catálogo (ampliado 14 sep 2026 — spec docs/panel-alumnos-catalogo-indicadores-spec.md
# §5, cerrado con Ricardo tras cruzar cada símbolo contra dukascopy Y la cuenta real
# de XM: 36 símbolos que tienen histórico real Y son operables en el broker que
# usan los alumnos). ponytail: sigue siendo un dict de Python, no la Sheet única —
# la Sheet es la fuente para n8n/UI; este dict es la fuente para "que instrumento
# de dukascopy le corresponde a cada símbolo" y hay que mantenerlo sincronizado a
# mano si se agrega un símbolo a la Sheet (ver spec §2, opción (b) fue aprobada
# pero la migración de Python a leer la Sheet directo todavía no está construida).
SIMBOLOS = {
    # Forex majors
    "EURUSD": inst.INSTRUMENT_FX_MAJORS_EUR_USD,
    "GBPUSD": inst.INSTRUMENT_FX_MAJORS_GBP_USD,
    "USDJPY": inst.INSTRUMENT_FX_MAJORS_USD_JPY,
    "AUDUSD": inst.INSTRUMENT_FX_MAJORS_AUD_USD,
    "USDCAD": inst.INSTRUMENT_FX_MAJORS_USD_CAD,
    "USDCHF": inst.INSTRUMENT_FX_MAJORS_USD_CHF,
    "NZDUSD": inst.INSTRUMENT_FX_MAJORS_NZD_USD,
    # Metales (broker XM: XAUUSD="GOLD", XAGUSD="SILVER")
    "XAUUSD": inst.INSTRUMENT_FX_METALS_XAU_USD,
    "XAGUSD": inst.INSTRUMENT_FX_METALS_XAG_USD,
    # Forex cruces
    "EURGBP": inst.INSTRUMENT_FX_CROSSES_EUR_GBP,
    "EURJPY": inst.INSTRUMENT_FX_CROSSES_EUR_JPY,
    "GBPJPY": inst.INSTRUMENT_FX_CROSSES_GBP_JPY,
    "AUDJPY": inst.INSTRUMENT_FX_CROSSES_AUD_JPY,
    "EURAUD": inst.INSTRUMENT_FX_CROSSES_EUR_AUD,
    "AUDCAD": inst.INSTRUMENT_FX_CROSSES_AUD_CAD,
    "NZDJPY": inst.INSTRUMENT_FX_CROSSES_NZD_JPY,
    "CADJPY": inst.INSTRUMENT_FX_CROSSES_CAD_JPY,
    # Indices (broker XM: sufijo "Cash", ej. "US30Cash")
    "US30": inst.INSTRUMENT_IDX_AMERICA_E_D_J_IND,
    "US100": inst.INSTRUMENT_IDX_AMERICA_E_NQ_100,
    "US500": inst.INSTRUMENT_IDX_AMERICA_E_SANDP_500,
    "GER40": inst.INSTRUMENT_IDX_EUROPE_E_DAAX,
    "UK100": inst.INSTRUMENT_IDX_EUROPE_E_FUTSEE_100,
    "JP225": inst.INSTRUMENT_IDX_ASIA_E_N225JAP,
    # Metales/energia (broker XM: WTI="OILCash", Brent="BRENTCash")
    "XPTUSD": inst.INSTRUMENT_CMD_METALS_XPT_CMD_USD,  # Platino
    "XPDUSD": inst.INSTRUMENT_CMD_METALS_XPD_CMD_USD,  # Paladio
    "WTIUSD": inst.INSTRUMENT_CMD_ENERGY_E_LIGHT,
    "BRENTUSD": inst.INSTRUMENT_CMD_ENERGY_E_BRENT,
    # Cripto (Cobre y TRXUSD quedaron fuera: TRX no existe en XM; Cobre solo
    # existe ahi como futuro con vencimiento, no como CFD continuo. Solana
    # existe en XM pero no en dukascopy — no se puede backtestear, queda fuera)
    "BTCUSD": inst.INSTRUMENT_VCCY_BTC_USD,
    "ETHUSD": inst.INSTRUMENT_VCCY_ETH_USD,
    "XRPUSD": inst.INSTRUMENT_VCCY_XRP_USD,
    # Acciones MX (broker XM: CFD/ADR en USD, no la accion BMV en MXN que da
    # dukascopy — el comportamiento de precio no es identico, ver spec §5).
    # Las otras 4 propuestas (GFNORTEO, FEMSAUBD, GMEXICOB, WALMEX) no existen
    # en XM, quedaron fuera.
    "AMXL": inst.INSTRUMENT_MEXICO_AMXL_MX_MXN,
    "CEMEXCPO": inst.INSTRUMENT_MEXICO_CEMEXCPO_MX_MXN,
    # Acciones US (broker XM llama a Meta "Facebook" — dukascopy tambien usa
    # el ticker viejo FB, ninguno de los dos actualizo el nombre)
    "GOOGL": inst.INSTRUMENT_US_GOOGL_US_USD,
    "NVDA": inst.INSTRUMENT_US_NVDA_US_USD,
    "META": inst.INSTRUMENT_US_FB_US_USD,
    "WMT": inst.INSTRUMENT_US_WMT_US_USD,
}

# Temporalidad -> intervalo real de velas (agregado 08 sep 2026, ampliado a 5
# niveles 14 sep 2026 -- ver spec docs/panel-alumnos-catalogo-indicadores-spec.md
# §3). Usado por api/tendencia.py y api/backtest.py -- cada temporalidad tiene
# su PROPIA tendencia (verificado con datos reales: XAUUSD daba venta rentable
# en H4 pero esa misma direccion no era rentable en H1), asi que no se puede
# comparar temporalidades contra una sola direccion compartida.
TEMPORALIDAD_A_INTERVALO = {
    "Scalping": dp.INTERVAL_MIN_15,
    "Intraday": dp.INTERVAL_HOUR_1,
    "Swing (H)": dp.INTERVAL_HOUR_4,
    "Swing (S)": dp.INTERVAL_WEEK_1,
    "Swing (M)": dp.INTERVAL_MONTH_1,
    # ponytail: alias temporal -- un Telegram callback_data viejo ("t|SIMBOLO|Swing")
    # ya entregado a un alumno antes de este deploy sigue funcionando (equivale a
    # Swing (H), el comportamiento previo). Quitar cuando se confirme que ya no
    # llegan mensajes con el valor viejo.
    "Swing": dp.INTERVAL_HOUR_4,
}

# Tipos de robot con la regla de negocio "solo se entrega si la tendencia real
# es alcista" (a largo plazo los activos tienden a subir -- decision de Ricardo,
# ver motor_smc/tendencia.py). Generalizado a un set porque ahora hay 3 variantes
# de Swing, no una sola -- quien orqueste la entrega (T-04 en n8n) debe revisar
# `tipoRobot in TIPOS_SOLO_ALCISTA`, no comparar contra el string "Swing" a secas.
TIPOS_SOLO_ALCISTA = {"Swing (H)", "Swing (S)", "Swing (M)", "Swing"}


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
