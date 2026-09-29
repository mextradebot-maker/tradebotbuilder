"""Datos históricos vía dukascopy-python — reemplaza TickStory.

Plan de construcción, Paso 1b (Conectividad). `dukascopy_python.fetch()` ya
devuelve un DataFrame con columnas open/high/low/close/volume en minúsculas —
exactamente lo que pide `motor_smc.analizar()` — así que no hace falta
transformar nada, solo dar nombres de símbolo cómodos y fechas por defecto.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import dukascopy_python as dp
from dukascopy_python import instruments as inst

log = logging.getLogger(__name__)

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

# FUENTE UNICA de las 7 temporalidades (Etapa 2, decision de Ricardo 29 sep 2026): el
# nombre incluye la vela. Cada temporalidad tiene su PROPIA tendencia (XAUUSD daba venta
# rentable en H4 pero no en H1), asi que no se comparan contra una direccion compartida.
# dias / swing_length: barrido 27 sep 2026 (spec 2026-09-27-calibracion-swing-length-design.md).
# diaria = se recalcula una vez al dia (08:00 Mexico); las demas al cierre de su vela.
# solo_compras = regla de negocio "solo se entrega si la tendencia real es alcista".
def _t(vela, mayor, intervalo, dias, swing, diaria=False, swing_perfil=False):
    return {"vela": vela, "vela_mayor": mayor, "intervalo": intervalo, "dias": dias, "swing_length": swing,
            "diaria": diaria, "solo_compras": swing_perfil, "es_swing": swing_perfil}


TEMPORALIDADES = {
    "Scalping 15m": _t("15m", "1H", dp.INTERVAL_MIN_15, 60, 8),
    "Scalping 30m": _t("30m", "1H", dp.INTERVAL_MIN_30, 120, 8),
    "Intraday 1H": _t("1H", "D", dp.INTERVAL_HOUR_1, 365, 10),
    "Intraday 4H": _t("4H", "D", dp.INTERVAL_HOUR_4, 365, 10),
    "Intraday D": _t("D", "S", dp.INTERVAL_DAY_1, 1095, 10, diaria=True),
    "Swing (S)": _t("S", "M", dp.INTERVAL_WEEK_1, 1095, 5, diaria=True, swing_perfil=True),
    "Swing (M)": _t("M", "M", dp.INTERVAL_MONTH_1, 2555, 5, diaria=True, swing_perfil=True),
}

# Nombres viejos: solo se aceptan de entrada (Telegram callback_data ya entregados, EA viejo,
# n8n); nunca se guardan ni se devuelven.
ALIAS_TEMPORALIDAD = {
    "Scalping": "Scalping 15m",
    "Intraday": "Intraday 1H",
    "Swing (H)": "Intraday 4H",
    "Swing": "Intraday 4H",
}


def resolver_temporalidad(nombre: str) -> str:
    """Nombre canonico de `nombre` (canonico o alias); ValueError si no existe."""
    if nombre in TEMPORALIDADES:
        return nombre
    if nombre in ALIAS_TEMPORALIDAD:
        return ALIAS_TEMPORALIDAD[nombre]
    raise ValueError(f"temporalidad desconocida: {nombre!r} (validas: {list(TEMPORALIDADES) + list(ALIAS_TEMPORALIDAD)})")


# Compatibilidad: canonicos + alias -> intervalo de dukascopy.
TEMPORALIDAD_A_INTERVALO = {**{n: t["intervalo"] for n, t in TEMPORALIDADES.items()},
                            **{a: TEMPORALIDADES[c]["intervalo"] for a, c in ALIAS_TEMPORALIDAD.items()}}

# Tipos de robot con la regla "solo se entrega si la tendencia real es alcista" (decision de
# Ricardo, ver motor_smc/tendencia.py). Quien orqueste la entrega debe revisar
# `tipoRobot in TIPOS_SOLO_ALCISTA`.
TIPOS_SOLO_ALCISTA = {n for n, t in TEMPORALIDADES.items() if t["solo_compras"]}


def obtener_velas(
    simbolo: str,
    inicio: datetime,
    fin: datetime,
    intervalo: str = dp.INTERVAL_HOUR_1,
    offer_side: str = dp.OFFER_SIDE_BID,
):
    """Descarga velas históricas. `simbolo` acepta una clave de SIMBOLOS o un
    instrumento crudo de dukascopy_python.instruments (ej. "XAU/USD").

    Con BD (DATABASE_URL) y un símbolo del catálogo lee del almacén de velas y solo baja de Dukascopy lo
    nuevo (conectividad/almacen.py); mismo resultado que la descarga directa. Si el almacén falla → directa."""
    instrumento = SIMBOLOS.get(simbolo, simbolo)
    if (simbolo in SIMBOLOS and intervalo in _ALMACEN and offer_side == dp.OFFER_SIDE_BID
            and os.environ.get("DATABASE_URL") and time.monotonic() >= _ALMACEN_FALLA[0]):
        try:
            return _velas_almacen(simbolo, instrumento, inicio, fin, intervalo)
        except Exception:
            # ponytail: pausa global de 60 s tras cualquier falla (BD caida no cuesta un timeout por llamada)
            _ALMACEN_FALLA[0] = time.monotonic() + 60
            log.warning("almacen de velas fallo (%s %s); descarga directa", simbolo, intervalo, exc_info=True)
    return _velas_directo(instrumento, inicio, fin, intervalo, offer_side)


def _velas_directo(instrumento, inicio, fin, intervalo, offer_side=dp.OFFER_SIDE_BID):
    """Comportamiento previo al almacén: todo de Dukascopy en cada llamada."""
    regla = _AGREGAR_DESDE_DIARIO.get(intervalo)
    if regla is None:
        return dp.fetch(instrumento, intervalo, offer_side, inicio, fin)
    # Dukascopy publica W1/MN1 con 1-2 semanas de retraso (27 sep 2026: última semanal
    # = 14 sep, última mensual = agosto). Las diarias sí vienen al día → se agregan
    # aquí, incluyendo la vela en curso, para ver un cambio de tendencia a tiempo.
    diario = dp.fetch(instrumento, dp.INTERVAL_DAY_1, offer_side, inicio, fin)
    if diario.empty:
        return diario
    return (diario.resample(regla, label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open"]))


# intervalo pedido -> (serie base guardada, serie derivada o None). Solo se guardan 15m, 1H y D.
_ALMACEN = {dp.INTERVAL_MIN_15: ("15m", None), dp.INTERVAL_MIN_30: ("15m", "30m"),
            dp.INTERVAL_HOUR_1: ("1H", None), dp.INTERVAL_HOUR_4: ("1H", "4H"),
            dp.INTERVAL_DAY_1: ("D", None), dp.INTERVAL_WEEK_1: ("D", "S"), dp.INTERVAL_MONTH_1: ("D", "M")}
_BASE = {"15m": (dp.INTERVAL_MIN_15, timedelta(minutes=15)), "1H": (dp.INTERVAL_HOUR_1, timedelta(hours=1)),
         "D": (dp.INTERVAL_DAY_1, timedelta(days=1))}
# Bordes de dp.fetch (medido 29 sep 2026): 15m/30m/4H devuelven la vela que CONTIENE `inicio`; 1H y D
# empiezan en la primera vela >= `inicio`; todas terminan en la última vela <= `fin`.
_VELA_QUE_CONTIENE = {dp.INTERVAL_MIN_15: "15min", dp.INTERVAL_MIN_30: "30min", dp.INTERVAL_HOUR_4: "4h"}
# 30m/4H nativas traen la vela completa aunque pase de `fin`: se leen las velas base de ese bloque.
_EXTRA_FIN = {dp.INTERVAL_MIN_30: timedelta(minutes=15), dp.INTERVAL_HOUR_4: timedelta(hours=3)}
_ALMACEN_FALLA = [0.0]  # time.monotonic() hasta el que no se intenta el almacén


def _velas_almacen(simbolo, instrumento, inicio, fin, intervalo):
    from conectividad import almacen
    import pandas as pd

    serie, derivada = _ALMACEN[intervalo]
    iv_base, paso = _BASE[serie]
    # .timestamp() = misma interpretación que dp.fetch (naive = hora local de la máquina)
    ini = pd.Timestamp(datetime.fromtimestamp(inicio.timestamp(), timezone.utc))
    f = pd.Timestamp(datetime.fromtimestamp(fin.timestamp(), timezone.utc))
    lo = ini.floor(_VELA_QUE_CONTIENE[intervalo]) if intervalo in _VELA_QUE_CONTIENE else ini
    base = almacen.sincronizar(simbolo, serie, lo, f + _EXTRA_FIN.get(intervalo, timedelta(0)),
                               lambda a, b: dp.fetch(instrumento, iv_base, dp.OFFER_SIDE_BID, a, b), paso)
    if derivada is None:
        return base
    out = almacen.agregar(base, derivada)
    return out[out.index <= f]


_AGREGAR_DESDE_DIARIO = {dp.INTERVAL_WEEK_1: "W-MON", dp.INTERVAL_MONTH_1: "MS"}


def _demo_temporalidades() -> None:
    """Sin red: mapa unico de las 7 temporalidades y sus alias."""
    assert list(TEMPORALIDADES) == ["Scalping 15m", "Scalping 30m", "Intraday 1H", "Intraday 4H", "Intraday D",
                                    "Swing (S)", "Swing (M)"]
    for n in TEMPORALIDADES:
        assert resolver_temporalidad(n) == n
    assert [resolver_temporalidad(a) for a in ("Scalping", "Intraday", "Swing (H)", "Swing")] ==         ["Scalping 15m", "Intraday 1H", "Intraday 4H", "Intraday 4H"]
    try:
        resolver_temporalidad("Nope")
        raise AssertionError("debio fallar")
    except ValueError:
        pass
    assert TIPOS_SOLO_ALCISTA == {"Swing (S)", "Swing (M)"}
    assert TEMPORALIDAD_A_INTERVALO["Swing"] == TEMPORALIDAD_A_INTERVALO["Intraday 4H"]
    assert [t["dias"] for t in TEMPORALIDADES.values()] == [60, 120, 365, 365, 1095, 1095, 2555]
    print("conectividad.historico._demo_temporalidades() OK")


def demo() -> None:
    _demo_temporalidades()
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


def demo_almacen() -> None:
    """Red + Postgres (DATABASE_URL): con almacen frio y luego incremental, obtener_velas == descarga
    directa de hoy (_velas_directo) para las 7 temporalidades; sin BD disponible cae a la directa."""
    import pandas as pd
    from conectividad import almacen

    logging.disable(logging.INFO)
    U, s = timezone.utc, "XAUUSD"

    def limpiar():
        with almacen.get_conn() as conn:
            conn.execute("DELETE FROM velas WHERE simbolo = %s", (s,))
            conn.execute("DELETE FROM velas_carga WHERE simbolo = %s", (s,))

    def igual(a, b, que):
        assert len(a) > 10, que
        pd.testing.assert_frame_equal(a[["open", "high", "low", "close"]], b[["open", "high", "low", "close"]],
                                      check_freq=False, check_dtype=False, rtol=0, atol=0, obj=que)
        pd.testing.assert_series_equal(a.volume, b.volume, check_freq=False, check_dtype=False, rtol=1e-9, obj=que)
        assert a.index.name == b.index.name and str(a.index.tz) == "UTC" and (a.dtypes == "float64").all(), que

    import persistencia  # noqa: F401  (migraciones)
    fin = datetime(2026, 9, 25, 13, 7, tzinfo=U)  # fin y cortes sin alinear: prueba los bordes de Dukascopy
    casos = [(dp.INTERVAL_MIN_15, 30), (dp.INTERVAL_MIN_30, 30), (dp.INTERVAL_HOUR_1, 120),
             (dp.INTERVAL_HOUR_4, 120), (dp.INTERVAL_DAY_1, 1095), (dp.INTERVAL_WEEK_1, 1095),
             (dp.INTERVAL_MONTH_1, 1095)]
    for iv, dias in casos:
        limpiar()
        ini1, fin1 = fin - timedelta(days=dias, minutes=-37), fin - timedelta(days=5, minutes=22)
        ini2 = ini1 + timedelta(days=2, hours=3, minutes=11)
        igual(obtener_velas(s, ini1, fin1, iv), _velas_directo(SIMBOLOS[s], ini1, fin1, iv), f"{iv} frio")
        assert almacen.carga(s, _ALMACEN[iv][0]) is not None, f"{iv}: la llamada fria debe poblar el almacen"
        igual(obtener_velas(s, ini2, fin, iv), _velas_directo(SIMBOLOS[s], ini2, fin, iv), f"{iv} incremental")
        # misma ventana otra vez (tambien incremental) y fechas naive (hora local, como dukascopy)
        n1, n2 = ini2.astimezone().replace(tzinfo=None), fin.astimezone().replace(tzinfo=None)
        igual(obtener_velas(s, n1, n2, iv), _velas_directo(SIMBOLOS[s], n1, n2, iv), f"{iv} naive")
        print(f"  {iv}: frio e incremental == directo")

    # sin BD alcanzable: cae a la descarga directa, sin romper
    url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = "postgresql://postgres@localhost:1/nada?connect_timeout=2"
    try:
        _ALMACEN_FALLA[0] = 0.0
        igual(obtener_velas(s, fin - timedelta(days=3), fin, dp.INTERVAL_HOUR_1),
              _velas_directo(SIMBOLOS[s], fin - timedelta(days=3), fin, dp.INTERVAL_HOUR_1), "sin BD")
        assert _ALMACEN_FALLA[0] > time.monotonic(), "tras una falla el almacen se pausa un rato"
    finally:
        os.environ["DATABASE_URL"], _ALMACEN_FALLA[0] = url, 0.0

    # tiempos: ventana del refresco (hasta ahora), almacen frio vs incremental
    for iv, dias in ((dp.INTERVAL_MIN_15, 60), (dp.INTERVAL_HOUR_1, 365)):
        limpiar()
        t = []
        for _ in range(2):
            ahora = datetime.now(U)
            t0 = time.perf_counter()
            obtener_velas(s, ahora - timedelta(days=dias), ahora, iv)
            t.append(time.perf_counter() - t0)
        print(f"  tiempo {iv}/{dias}d: frio {t[0]:.1f} s, incremental {t[1]:.2f} s")
    limpiar()
    print("conectividad.historico.demo_almacen() OK")


if __name__ == "__main__":
    import os as _os
    if _os.environ.get("HISTORICO_DEMO_ALMACEN") == "1":
        demo_almacen()
    else:
        demo()
