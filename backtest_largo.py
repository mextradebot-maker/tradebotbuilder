"""Backtest largo semanal sobre el almacen de velas (spec 2026-09-29 §5).

Lee del almacen la ventana larga de cada temporalidad, corre detectar_setups_v2 + backtest_v2 y guarda
{"backtests", "embudo"} en `backtest_largo` (Postgres). Es informacion extra (/api/setups.backtest_largo y
/api/backtest); no gatea nada. Un hilo (prioridad baja): combos sin fila apenas su historia esta completa;
recalculo de los viejos (> 6 dias) solo en domingo UTC. Una combinacion a la vez, con pausa entre ellas, y
sin competir con el refresco mientras el mercado esta abierto y hay trabajo urgente.
Variable: BACKTEST_LARGO_ACTIVO=0 lo apaga (app.py).
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)

ANIO = timedelta(days=365)
# Ventana por temporalidad canonica (None = toda la historia del almacen).
VENTANA = {"Scalping 15m": 3 * ANIO, "Scalping 30m": 3 * ANIO, "Intraday 1H": 8 * ANIO, "Intraday 4H": 15 * ANIO,
           "Intraday D": 15 * ANIO, "Swing (S)": None, "Swing (M)": None}
SERIE_BASE = {"15m": "15m", "30m": "15m", "1H": "1H", "4H": "1H", "D": "D", "S": "D", "M": "D"}  # vela -> serie guardada
DIAS_RECALCULO = 5  # < 7: una fila de un domingo tardio no se salta el domingo siguiente
PAUSA_COMBO_S = 30
CICLO_TRABAJO = 2  # pausa tras un combo >= 2 x lo que tardo: el hilo usa como mucho ~33% de CPU
PAUSA_OCIOSA_S = 600
PAUSA_ERROR_S = 3600  # un combo que fallo no se reintenta antes de esto


def series_de(temporalidad: str) -> tuple[str, str]:
    """(serie base de la vela, serie base de la vela mayor) que deben estar `completa`."""
    from conectividad import TEMPORALIDADES
    t = TEMPORALIDADES[temporalidad]
    return SERIE_BASE[t["vela"]], SERIE_BASE[t["vela_mayor"]]


def elegibles(catalogo: list, completas: set) -> list[tuple[str, str]]:
    """Combos activos del catalogo (simbolo conocido, temporalidad canonica) con las dos series base completas."""
    from conectividad import SIMBOLOS, TEMPORALIDADES
    res = set()
    for c in catalogo:
        s, t = c["simbolo"], c["temporalidad"]
        if c["activo"] and s in SIMBOLOS and t in TEMPORALIDADES:
            a, b = series_de(t)
            if (s, a) in completas and (s, b) in completas:
                res.add((s, t))
    return sorted(res)


def _numpy(x):
    if hasattr(x, "item"):  # escalares numpy
        return x.item()
    raise TypeError(f"no serializable: {type(x).__name__}")


def a_json_estricto(d: dict) -> dict:
    """Ida y vuelta estricta (mismo enfoque que api.setups.motor_v2): un NaN lanza aqui, no en la BD."""
    return json.loads(json.dumps(d, allow_nan=False, default=_numpy))


def calcular(simbolo: str, temporalidad: str, ahora: datetime | None = None, obtener=None) -> dict:
    """{"desde","hasta","velas","resultado"} de la ventana larga. `obtener(simbolo, inicio, fin, intervalo)` por defecto
    conectividad.obtener_velas (sirve del almacen: sin descarga grande si las series estan completas)."""
    from backtesting.backtest import backtest_v2
    from conectividad import TEMPORALIDADES, almacen, obtener_velas
    from motor_smc.reglas import DURACION_VELA
    from motor_smc.setups_v2 import detectar_setups_v2, embudo
    obtener = obtener or (lambda s, a, b, iv: obtener_velas(s, a, b, iv, solo_almacen=True))  # nunca descarga directa de anios
    ahora = ahora or datetime.now(timezone.utc)
    t = TEMPORALIDADES[temporalidad]
    vela, mayor = t["vela"], t["vela_mayor"]

    def inicio_de(serie):
        desde = almacen.carga(simbolo, serie)[0]
        return max(ahora - VENTANA[temporalidad], desde) if VENTANA[temporalidad] else desde

    ini = inicio_de(SERIE_BASE[vela])
    ohlc = obtener(simbolo, ini, ahora, t["intervalo"])
    if len(ohlc) and ohlc.index[-1] + DURACION_VELA[vela] > pd.Timestamp(ahora):  # sin la vela en formacion
        ohlc = ohlc.iloc[:-1]
    if mayor == vela:
        ohlc_mayor = ohlc
    else:
        iv_mayor = next(x["intervalo"] for x in TEMPORALIDADES.values() if x["vela"] == mayor)
        ohlc_mayor = obtener(simbolo, max(ini, inicio_de(SERIE_BASE[mayor])), ahora, iv_mayor)
    if ohlc.empty or ohlc_mayor.empty:
        raise ValueError("sin velas para el backtest largo")
    setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, mayor)
    resultado = a_json_estricto({"backtests": backtest_v2(ohlc, setups), "embudo": embudo(setups)})
    return {"desde": ohlc.index[0].to_pydatetime(), "hasta": ohlc.index[-1].to_pydatetime(),
            "velas": len(ohlc), "resultado": resultado}


def guardar(simbolo: str, temporalidad: str, calc: dict, calculado_en: datetime | None = None) -> None:
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO backtest_largo (simbolo, temporalidad, calculado_en, desde, hasta, velas, resultado)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (simbolo, temporalidad) DO UPDATE SET calculado_en = EXCLUDED.calculado_en,
                 desde = EXCLUDED.desde, hasta = EXCLUDED.hasta, velas = EXCLUDED.velas, resultado = EXCLUDED.resultado""",
            (simbolo, temporalidad, calculado_en or datetime.now(timezone.utc), calc["desde"], calc["hasta"],
             calc["velas"], json.dumps(calc["resultado"], allow_nan=False)))


def pausa_tras(segundos_calculo: float) -> float:
    return max(PAUSA_COMBO_S, CICLO_TRABAJO * segundos_calculo)


def _iso(t) -> str:
    return t.astimezone(timezone.utc).isoformat()


def leer(simbolo: str, temporalidad: str) -> dict | None:
    """El resultado guardado + desde, hasta, velas, calculado_en (ISO); None si no hay (o falla la BD)."""
    try:
        from persistencia.conexion import get_conn
        with get_conn() as conn:
            f = conn.execute("SELECT calculado_en, desde, hasta, velas, resultado FROM backtest_largo"
                             " WHERE simbolo = %s AND temporalidad = %s", (simbolo, temporalidad)).fetchone()
    except Exception:
        return None
    if f is None:
        return None
    return {**f[4], "desde": _iso(f[1]), "hasta": _iso(f[2]), "velas": f[3], "calculado_en": _iso(f[0])}


def ultimos() -> dict:
    """{(simbolo, temporalidad): calculado_en}"""
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        return {(s, t): c for s, t, c in conn.execute("SELECT simbolo, temporalidad, calculado_en FROM backtest_largo")}


def siguiente(disponibles: list, ultimo: dict, ahora: datetime, urgentes, mercado_abierto,
              vetados=frozenset()) -> tuple[str, str] | None:
    """Combo a calcular ahora o None. Sin fila: cualquier dia. Con fila mas vieja que DIAS_RECALCULO: solo domingo UTC.
    Con el mercado del simbolo abierto no se calcula si hay trabajo urgente del refresco (`urgentes()`, lazy)."""
    sin_fila = [c for c in disponibles if c not in ultimo]
    viejos = sorted((c for c in disponibles if c in ultimo and ahora - ultimo[c] > timedelta(days=DIAS_RECALCULO)),
                    key=lambda c: ultimo[c]) if ahora.weekday() == 6 else []
    hay_urgentes = None
    for c in sin_fila + viejos:
        if c in vetados:
            continue
        if mercado_abierto(c[0], ahora):
            if hay_urgentes is None:
                hay_urgentes = urgentes()
            if hay_urgentes:
                continue
        return c
    return None


def _bucle(dormir=time.sleep) -> None:
    import persistencia
    import refresco
    from conectividad import almacen
    vetados: dict = {}
    dormir(120)
    while True:
        pausa = PAUSA_OCIOSA_S
        try:
            ahora = datetime.now(timezone.utc)
            for c in [c for c, h in vetados.items() if h <= ahora]:
                del vetados[c]
            disp = elegibles(persistencia.listar_catalogo(), almacen.completas())
            c = siguiente(disp, ultimos(), ahora, lambda: refresco.hay_pendientes_urgentes(ahora),
                          refresco.mercado_abierto, set(vetados))
            if c:
                t0 = time.monotonic()
                try:
                    guardar(*c, calcular(*c))
                    log.info("backtest largo %s %s listo en %.0f s", *c, time.monotonic() - t0)
                except Exception as e:
                    vetados[c] = ahora + timedelta(seconds=PAUSA_ERROR_S)
                    log.warning("backtest largo %s %s fallo: %s: %s", *c, type(e).__name__, e)
                pausa = pausa_tras(time.monotonic() - t0)
        except Exception:
            log.exception("ciclo de backtest largo fallo")
            pausa = 60
        dormir(pausa)


def iniciar_en_segundo_plano() -> threading.Thread:
    hilo = threading.Thread(target=_bucle, name="backtest-largo", daemon=True)
    hilo.start()
    return hilo


# ----------------------------------------------------------------------------------------------- pruebas
def demo() -> None:
    """Necesita DATABASE_URL (Postgres local). Sin red: velas sinteticas."""
    import numpy as np

    import persistencia  # noqa: F401  (corre las migraciones)
    from conectividad import almacen
    from persistencia.conexion import get_conn

    # elegibilidad: activo + simbolo conocido + las DOS series base completas
    cat = [{"simbolo": "XAUUSD", "temporalidad": t, "activo": True} for t in
           ("Scalping 15m", "Scalping 30m", "Intraday 1H", "Intraday 4H", "Intraday D", "Swing (S)", "Swing (M)")]
    cat += [{"simbolo": "EURUSD", "temporalidad": "Intraday 1H", "activo": False},
            {"simbolo": "NOEXISTE", "temporalidad": "Intraday 1H", "activo": True},
            {"simbolo": "EURUSD", "temporalidad": "Swing", "activo": True}]
    assert elegibles(cat, set()) == []
    assert elegibles(cat, {("XAUUSD", "15m"), ("XAUUSD", "1H")}) == [("XAUUSD", "Scalping 15m"), ("XAUUSD", "Scalping 30m")]
    assert elegibles(cat, {("XAUUSD", "15m")}) == []  # falta la 1H de la vela mayor
    assert elegibles(cat, {("XAUUSD", "1H")}) == []  # 1H/4H piden ademas D
    todas = {("XAUUSD", "1H"), ("XAUUSD", "D"), ("EURUSD", "1H"), ("EURUSD", "D"), ("NOEXISTE", "1H"), ("NOEXISTE", "D")}
    assert elegibles(cat, todas) == [("XAUUSD", "Intraday 1H"), ("XAUUSD", "Intraday 4H"), ("XAUUSD", "Intraday D"),
                                     ("XAUUSD", "Swing (M)"), ("XAUUSD", "Swing (S)")]
    assert elegibles(cat, {("XAUUSD", "D")}) == [("XAUUSD", "Intraday D"), ("XAUUSD", "Swing (M)"), ("XAUUSD", "Swing (S)")]

    # JSON estricto
    assert a_json_estricto({"a": np.float64(1.5), "b": np.int64(2), "c": [np.bool_(True)]}) == {"a": 1.5, "b": 2, "c": [True]}
    for malo in (float("nan"), np.float64("inf")):
        try:
            a_json_estricto({"x": malo})
            raise AssertionError("debio rechazar NaN/inf")
        except ValueError:
            pass

    # decisiones del hilo (domingo 2026-09-27; lunes 2026-09-28)
    dom, lun = datetime(2026, 9, 27, 3, tzinfo=timezone.utc), datetime(2026, 9, 28, 15, tzinfo=timezone.utc)
    A, B = ("XAUUSD", "Intraday 1H"), ("EURUSD", "Intraday 1H")
    viejo, fresco = dom - timedelta(days=6), dom - timedelta(days=4)  # 6 dias (domingo tardio): se recalcula; 4: no
    cerrado, abierto = (lambda s, t: False), (lambda s, t: True)
    no_urg, si_urg = (lambda: False), (lambda: True)
    assert siguiente([A, B], {A: viejo}, lun, no_urg, abierto) == B  # sin fila: cualquier dia
    assert siguiente([A], {A: viejo}, lun, no_urg, abierto) is None  # viejo: solo domingo
    assert siguiente([A], {A: viejo}, dom, no_urg, abierto) == A
    assert siguiente([A], {A: fresco}, dom, no_urg, abierto) is None  # reciente (< 6 dias): no se recalcula
    assert siguiente([A, B], {A: viejo, B: viejo - timedelta(days=1)}, dom, no_urg, abierto) == B  # el mas viejo primero
    assert siguiente([A], {}, lun, si_urg, abierto) is None  # mercado abierto y refresco urgente: cede
    assert siguiente([A], {}, lun, si_urg, cerrado) == A  # mercado cerrado: no compite con el refresco
    assert siguiente([A], {}, lun, no_urg, abierto) == A
    assert siguiente([A, B], {}, lun, no_urg, abierto, vetados={A}) == B
    llamadas = []
    siguiente([A, B], {}, lun, lambda: llamadas.append(1) or False, abierto)
    assert len(llamadas) == 1  # urgentes() se consulta una vez y solo si hace falta
    siguiente([A], {}, lun, lambda: llamadas.append(1) or False, cerrado)
    assert len(llamadas) == 1

    ahora = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    # duty cycle: pausa >= 2 x el calculo (max 33% de CPU), nunca menos que PAUSA_COMBO_S
    assert pausa_tras(1) == PAUSA_COMBO_S and pausa_tras(60) == 120 and pausa_tras(600) == 1200

    # solo_almacen: sin almacen o sin cobertura lanza y NO hay descarga directa; por defecto todo igual
    import os

    import dukascopy_python as dp
    import pandas as _pd

    from conectividad import historico as h
    descargas = []
    fetch0 = dp.fetch
    dp.fetch = lambda *a, **k: descargas.append(a) or _pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    ini, fin = ahora - timedelta(days=400), ahora
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM velas_carga WHERE simbolo = 'XAUUSD'")
        try:
            h.obtener_velas("XAUUSD", ini, fin, dp.INTERVAL_HOUR_1, solo_almacen=True)
            raise AssertionError("debio lanzar: el almacen no cubre")
        except RuntimeError:
            pass
        assert descargas == [], "solo_almacen no debe bajar historia de Dukascopy"
        h._ALMACEN_FALLA[0] = time.monotonic() + 60  # almacen en pausa
        try:
            h.obtener_velas("XAUUSD", ini, fin, dp.INTERVAL_HOUR_1, solo_almacen=True)
            raise AssertionError("debio lanzar: almacen en pausa")
        except RuntimeError:
            pass
        assert descargas == []
        h.obtener_velas("XAUUSD", ini, fin, dp.INTERVAL_HOUR_1)  # por defecto: cae a la descarga directa como siempre
        assert len(descargas) == 1
        h._ALMACEN_FALLA[0] = 0.0
        url = os.environ.pop("DATABASE_URL")
        try:
            h.obtener_velas("XAUUSD", ini, fin, dp.INTERVAL_HOUR_1, solo_almacen=True)
            raise AssertionError("debio lanzar: sin BD")
        except RuntimeError:
            pass
        assert len(descargas) == 1
        os.environ["DATABASE_URL"] = url
        h.obtener_velas("XAUUSD", ini, fin, dp.INTERVAL_HOUR_1)  # por defecto sin cambios: el almacen baja la ventana y la guarda
        assert len(descargas) == 2
    finally:
        dp.fetch = fetch0
        h._ALMACEN_FALLA[0] = 0.0
        os.environ.setdefault("DATABASE_URL", "")
    # calcular usa por defecto solo_almacen=True para base y mayor
    import conectividad as _c
    llamadas_kw = []
    orig_ov = _c.obtener_velas
    _c.obtener_velas = lambda s, a, b, iv, **kw: llamadas_kw.append(kw) or (_ for _ in ()).throw(RuntimeError("x"))
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM velas_carga WHERE simbolo = 'TSTLARGO'")
        almacen.registrar_carga("TSTLARGO", "15m", ahora - 4 * ANIO, ahora)
        try:
            calcular("TSTLARGO", "Scalping 15m", ahora)
        except RuntimeError:
            pass
        assert llamadas_kw == [{"solo_almacen": True}], llamadas_kw
    finally:
        _c.obtener_velas = orig_ov

    # calcular + guardar/leer con velas sinteticas
    sim = "TSTLARGO"
    ahora = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    rng = np.random.default_rng(7)

    def sintetica(ini, fin, paso):
        idx = pd.date_range(ini, fin, freq=paso, tz="UTC", inclusive="left")
        c = 100 + np.cumsum(rng.normal(0, 0.4, len(idx)))
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + rng.random(len(idx)) * 0.3,
                             "low": np.minimum(o, c) - rng.random(len(idx)) * 0.3, "close": c, "volume": 1.0}, index=idx)

    pedidos = []

    def falso(s, ini, fin, intervalo):
        from conectividad import TEMPORALIDADES
        pedidos.append(ini)
        paso = {t["intervalo"]: t["vela"] for t in TEMPORALIDADES.values()}[intervalo]
        return sintetica(max(ini, ahora - timedelta(days=60 if paso in ("15m", "1H") else 3000)), fin,
                         {"15m": "15min", "1H": "1h", "D": "1D", "S": "7D", "M": "30D"}[paso])

    with get_conn() as conn:
        for tabla in ("velas_carga", "backtest_largo"):
            conn.execute(f"DELETE FROM {tabla} WHERE simbolo = %s", (sim,))
    almacen.registrar_carga(sim, "15m", ahora - 3 * ANIO - timedelta(days=30), ahora)
    almacen.registrar_carga(sim, "1H", ahora - 15 * ANIO, ahora)
    almacen.registrar_carga(sim, "D", ahora - 20 * ANIO, ahora)
    calc = calcular(sim, "Scalping 15m", ahora, obtener=falso)
    assert pedidos == [ahora - 3 * ANIO, ahora - 3 * ANIO], pedidos  # ventana de 3 anios, mayor 1H
    assert calc["velas"] > 3000 and set(calc["resultado"]) == {"backtests", "embudo"}
    assert set(calc["resultado"]["backtests"]["total"]) == {"compra", "venta"}
    pedidos.clear()
    calcular(sim, "Swing (M)", ahora, obtener=falso)
    assert pedidos[0] == ahora - 20 * ANIO, pedidos  # Swing: toda la historia (desde de la serie D)
    pedidos.clear()
    calcular(sim, "Intraday 1H", ahora, obtener=falso)
    assert pedidos[0] == ahora - 8 * ANIO, pedidos

    assert leer(sim, "Scalping 15m") is None
    guardar(sim, "Scalping 15m", calc, calculado_en=ahora)
    guardar(sim, "Scalping 15m", calc, calculado_en=ahora)  # idempotente (upsert)
    r = leer(sim, "Scalping 15m")
    assert r["backtests"] == calc["resultado"]["backtests"] and r["embudo"] == calc["resultado"]["embudo"]
    assert r["velas"] == calc["velas"] and r["calculado_en"] == ahora.isoformat() and r["desde"] < r["hasta"]
    assert ultimos()[(sim, "Scalping 15m")] == ahora
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM backtest_largo WHERE simbolo = %s", (sim,)).fetchone()[0] == 1
        txt = conn.execute("SELECT resultado::text FROM backtest_largo WHERE simbolo = %s", (sim,)).fetchone()[0]
    assert "NaN" not in txt and "Infinity" not in txt  # jsonb estricto

    # API: backtest_largo antes de setups_confirmados (ultima llave) y null si no hay
    import api.backtest as ab
    import api.setups as st
    conf = [{"tipo": "reversion", "direccion": "long", "entrada": 1.0, "stop": 0.5, "tp": 2.0, "valido": True}]
    base = {"simbolo": sim, "temporalidad": "Scalping 15m", "velas": 5, "setups": conf, "setups_confirmados": conf}
    con = st._con_largo(st._forma_ea(base), sim, "Scalping 15m")
    llaves = list(con)  # backtest_largo va antes de ref_ts/ref_cierre (precio de referencia del EA)
    assert llaves[-3:] == ["ref_ts", "ref_cierre", "setups_confirmados"] and llaves[-4] == "backtest_largo", llaves
    assert con["backtest_largo"]["velas"] == calc["velas"] and "backtests" in con["backtest_largo"]
    assert st._parser_ea(json.dumps(con, separators=(",", ":"))) == ("long", 1.0, 0.5, 2.0)
    sin = st._con_largo(st._forma_ea(base), sim, "Swing (S)")
    assert sin["backtest_largo"] is None and list(sin)[-1] == "setups_confirmados"
    assert list(st._con_largo(sin, sim, "Scalping 15m")).count("backtest_largo") == 1  # idempotente
    # /api/backtest: largo si existe (misma forma), si no el corto
    codigo, cuerpo = ab.procesar({"simbolo": sim, "direccion": "compra", "temporalidad": "Scalping 15m"})
    assert codigo == 200 and cuerpo["fuente"] == "largo" and cuerpo["velas"] == calc["velas"], cuerpo
    assert cuerpo["n_setups"] == calc["resultado"]["backtests"]["total"]["compra"]["n_setups"]
    assert set(cuerpo) >= {"simbolo", "direccion", "temporalidad", "velas", "n_setups", "fuente"}
    orig = st.procesar, st.desde_snapshot
    try:
        st.procesar = lambda p: (200, {"velas": 9, "backtests": {"compra": {"n_setups": 3, "rentable_sin_optimizar": None}}})
        st.desde_snapshot = lambda *a: None
        codigo, cuerpo = ab.procesar({"simbolo": sim, "direccion": "compra", "temporalidad": "Swing (S)"})
        assert cuerpo["fuente"] == "corto" and cuerpo["n_setups"] == 3, cuerpo
    finally:
        st.procesar, st.desde_snapshot = orig
    with get_conn() as conn:
        for tabla in ("velas_carga", "backtest_largo"):
            conn.execute(f"DELETE FROM {tabla} WHERE simbolo = %s", (sim,))
    print("backtest_largo.demo() OK")


if __name__ == "__main__":
    demo()
