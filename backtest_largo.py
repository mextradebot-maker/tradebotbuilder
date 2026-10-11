"""Backtest largo sobre el almacen de velas (spec 2026-09-29 §5).

Lee del almacen la ventana larga de cada temporalidad, corre detectar_setups_v2 + backtest_v2 y guarda
{"backtests", "embudo", "setups", "version", "completo_en"} en `backtest_largo` (Postgres). Es informacion extra
(/api/setups.backtest_largo, /api/backtest, catalogo); no gatea nada. Un hilo (prioridad baja), una combinacion a la vez:
  - completo: sin fila, motor distinto (VERSION_MOTOR) o mas de DIAS_COMPLETO desde el ultimo completo;
  - incremental: cada vela nueva (como mucho una vez al dia en D/S/M). Solo detecta setups en la cola
    (COLA_VELAS velas de contexto + las provisionales + las nuevas), suma los validos a los firmes y vuelve a simular todos con
    las velas al dia. Los setups ya conocidos no cambian con velas nuevas (se evaluan con datos hasta su
    indice_conocido), asi que acierto, expectativa y n_setups salen iguales que en el completo (probado con velas
    reales 2026-10-11). Las tasas de confluencia (OB/liquidez, informativas) y la ventana que avanza las corrige el
    completo semanal.
Sin competir con el refresco mientras el mercado esta abierto y hay trabajo urgente.
Variable: BACKTEST_LARGO_ACTIVO=0 lo apaga (app.py).
"""

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

ANIO = timedelta(days=365)
# Ventana por temporalidad canonica (None = toda la historia del almacen).
VENTANA = {"Scalping 15m": 3 * ANIO, "Scalping 30m": 3 * ANIO, "Intraday 1H": 8 * ANIO, "Intraday 4H": 15 * ANIO,
           "Intraday D": 15 * ANIO, "Swing (S)": None, "Swing (M)": None}
SERIE_BASE = {"15m": "15m", "30m": "15m", "1H": "1H", "4H": "1H", "D": "D", "S": "D", "M": "D"}  # vela -> serie guardada
DIAS_COMPLETO = 7  # el completo corrige lo que el incremental no ve (ventana que avanza, velas corregidas)
COLA_VELAS = 3000  # contexto del incremental: con 1000 ya da los mismos setups que el completo (15m, 1H, 2026-10-11)
# Las ultimas velas son provisionales: ahi la estructura (swings sin confirmar) todavia puede cambiar con velas
# nuevas y un candidato puede desaparecer. El incremental vuelve a detectar desde `firme_hasta` (20 x swing_length).
VELAS_PROVISIONALES = 200
CADA_MAX = timedelta(days=1)  # D/S/M: incremental diario aunque la vela dure mas
PAUSA_COMBO_S = 30
PAUSA_INCREMENTAL_S = 5
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


def _version_motor() -> str:
    """Huella del codigo que decide setups y resultados: si cambia, los incrementales no sirven (completo)."""
    raiz = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted((raiz / "motor_smc").glob("*.py")) + [raiz / "backtesting" / "backtest.py"]:
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:16]


VERSION_MOTOR = _version_motor()
CAMPOS_SETUP = ("tipo", "direccion", "entrada", "stop", "tp", "zona_extremo", "order_block_confluente", "liquidez_confluente")


def cada(temporalidad: str) -> timedelta:
    """Cada cuanto toca el incremental: una vela (15 min como minimo), un dia como maximo."""
    from conectividad import TEMPORALIDADES
    from motor_smc.reglas import DURACION_VELA
    d = DURACION_VELA[TEMPORALIDADES[temporalidad]["vela"]]
    if not isinstance(d, timedelta):  # M: DateOffset de un mes
        return CADA_MAX
    return min(max(d, timedelta(minutes=15)), CADA_MAX)


def _setups_guardables(ohlc: pd.DataFrame, setups: pd.DataFrame) -> list[dict]:
    """Los validos, con la fecha de su vela conocida en vez del indice (el indice cambia al mover la ventana)."""
    v = setups[setups["valido"].astype(bool)]
    return [{"ts": ohlc.index[int(f["indice_conocido"])].isoformat(), **{k: f[k] for k in CAMPOS_SETUP}}
            for _, f in v.iterrows()]


def _de_guardados(ohlc: pd.DataFrame, guardados: list[dict]) -> pd.DataFrame | None:
    """Los setups guardados con su indice en `ohlc`; los de antes de la ventana se caen. None si alguna fecha ya no
    esta en las velas (velas corregidas): entonces toca completo."""
    filas = [g for g in guardados if pd.Timestamp(g["ts"]) >= ohlc.index[0]]
    pos = ohlc.index.get_indexer(pd.DatetimeIndex([pd.Timestamp(g["ts"]) for g in filas]))
    if (pos < 0).any():
        return None
    return pd.DataFrame([{**{k: g[k] for k in CAMPOS_SETUP}, "indice_conocido": int(p), "valido": True}
                         for g, p in zip(filas, pos)], columns=[*CAMPOS_SETUP, "indice_conocido", "valido"])


def _sumar_embudo(a: dict, b: dict) -> dict:
    """Suma dos embudos (mismas llaves anidadas, hojas enteras)."""
    return {k: _sumar_embudo(a.get(k, {}), v) if isinstance(v, dict) else a.get(k, 0) + v for k, v in b.items()}


def _paquete(ohlc: pd.DataFrame, setups: pd.DataFrame, emb_firme: dict, emb_resto: dict, firme: int,
             completo_en: str, modo: str) -> dict:
    from backtesting.backtest import backtest_v2
    return a_json_estricto({"backtests": backtest_v2(ohlc, setups), "embudo": _sumar_embudo(emb_firme, emb_resto),
                            "setups": _setups_guardables(ohlc, setups), "version": VERSION_MOTOR,
                            "completo_en": completo_en, "modo": modo,
                            "firme_hasta": ohlc.index[firme].isoformat(), "embudo_firme": emb_firme})


def resultado_de(ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str, informativas: frozenset,
                 previo: dict | None = None, ahora: datetime | None = None) -> dict:
    """El paquete que se guarda en `resultado`. Con `previo` (resultado guardado + "hasta") de la misma version y
    completo reciente: incremental desde su `firme_hasta`; si no, completo."""
    from motor_smc.setups_v2 import detectar_setups_v2, embudo
    ahora = ahora or datetime.now(timezone.utc)
    firme = max(0, len(ohlc) - 1 - VELAS_PROVISIONALES)  # ultima vela firme de esta corrida
    viejos = None
    if (previo and previo.get("version") == VERSION_MOTOR and "setups" in previo and previo.get("firme_hasta")
            and previo.get("completo_en")
            and ahora - datetime.fromisoformat(previo["completo_en"]) < timedelta(days=DIAS_COMPLETO)):
        f_ts = pd.Timestamp(previo["firme_hasta"])
        f_prev = int(ohlc.index.searchsorted(f_ts, side="right")) - 1  # ultima vela firme de la corrida anterior
        if 0 <= f_prev < len(ohlc) and ohlc.index[f_prev] == f_ts and f_prev <= firme:
            viejos = _de_guardados(ohlc, [g for g in previo["setups"] if pd.Timestamp(g["ts"]) <= f_ts])
    if viejos is None:
        setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor, informativas)
        return _paquete(ohlc, setups, embudo(setups[setups["indice_conocido"] <= firme]),
                        embudo(setups[setups["indice_conocido"] > firme]), firme, ahora.isoformat(), "completo")
    ini = max(0, f_prev + 1 - COLA_VELAS)
    cola = detectar_setups_v2(ohlc.iloc[ini:], ohlc_mayor, vela, vela_mayor, informativas)
    cola["indice_conocido"] = cola["indice_conocido"] + ini
    cola = cola[cola["indice_conocido"] > f_prev]
    emb_firme = _sumar_embudo(previo["embudo_firme"], embudo(cola[cola["indice_conocido"] <= firme]))
    nuevos = cola[cola["valido"].astype(bool)][list(viejos.columns)]
    todos = pd.concat([viejos, nuevos], ignore_index=True) if len(nuevos) else viejos
    return _paquete(ohlc, todos, emb_firme, embudo(cola[cola["indice_conocido"] > firme]), firme,
                    previo["completo_en"], "incremental")


def calcular(simbolo: str, temporalidad: str, ahora: datetime | None = None, obtener=None, previo: dict | None = None) -> dict:
    """{"desde","hasta","velas","resultado"} de la ventana larga. `obtener(simbolo, inicio, fin, intervalo)` por defecto
    conectividad.obtener_velas (sirve del almacen: sin descarga grande si las series estan completas).
    `previo`: lo guardado (leer_previo) para el incremental."""
    from conectividad import TEMPORALIDADES, almacen, obtener_velas
    from motor_smc.reglas import DURACION_VELA
    from motor_smc.setups_v2 import reglas_informativas
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
    resultado = resultado_de(ohlc, ohlc_mayor, vela, mayor, reglas_informativas(simbolo, temporalidad), previo, ahora)
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


def pausa_tras(segundos_calculo: float, incremental: bool = False) -> float:
    return max(PAUSA_INCREMENTAL_S if incremental else PAUSA_COMBO_S, CICLO_TRABAJO * segundos_calculo)


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
    publico = {k: v for k, v in f[4].items() if k not in ("setups", "version", "completo_en", "modo", "firme_hasta", "embudo_firme")}
    return {**publico, "desde": _iso(f[1]), "hasta": _iso(f[2]), "velas": f[3], "calculado_en": _iso(f[0])}


def leer_previo(simbolo: str, temporalidad: str) -> dict | None:
    """El resultado guardado completo (con setups) + "hasta", para el incremental; None si no hay."""
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        f = conn.execute("SELECT hasta, resultado FROM backtest_largo WHERE simbolo = %s AND temporalidad = %s",
                         (simbolo, temporalidad)).fetchone()
    return {**f[1], "hasta": _iso(f[0])} if f else None


def ultimos() -> dict:
    """{(simbolo, temporalidad): (calculado_en, version del motor con que se calculo)}"""
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        return {(s, t): (c, v) for s, t, c, v in conn.execute(
            "SELECT simbolo, temporalidad, calculado_en, resultado->>'version' FROM backtest_largo")}


def siguiente(disponibles: list, ultimo: dict, ahora: datetime, urgentes, mercado_abierto,
              vetados=frozenset()) -> tuple[str, str] | None:
    """Combo a calcular ahora o None. Sin fila primero; luego las de otra version del motor o a las que ya les toca
    (`cada`), la calculada hace mas tiempo primero. `ultimo`: {combo: (calculado_en, version)}.
    Con el mercado del simbolo abierto no se calcula si hay trabajo urgente del refresco (`urgentes()`, lazy)."""
    sin_fila = [c for c in disponibles if c not in ultimo]
    viejos = sorted((c for c in disponibles if c in ultimo
                     and (ultimo[c][1] != VERSION_MOTOR or ahora - ultimo[c][0] >= cada(c[1]))),
                    key=lambda c: ultimo[c][0])
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
                modo = "completo"
                try:
                    calc = calcular(*c, previo=leer_previo(*c))
                    modo = calc["resultado"]["modo"]
                    guardar(*c, calc)
                    log.info("backtest largo %s %s %s listo en %.1f s", *c, modo, time.monotonic() - t0)
                except Exception as e:
                    vetados[c] = ahora + timedelta(seconds=PAUSA_ERROR_S)
                    log.warning("backtest largo %s %s fallo: %s: %s", *c, type(e).__name__, e)
                pausa = pausa_tras(time.monotonic() - t0, modo == "incremental")
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

    # decisiones del hilo (lunes 2026-09-28)
    lun = datetime(2026, 9, 28, 15, tzinfo=timezone.utc)
    A, B = ("XAUUSD", "Intraday 1H"), ("EURUSD", "Intraday 1H")
    V = VERSION_MOTOR
    viejo, fresco = (lun - timedelta(minutes=61), V), (lun - timedelta(minutes=59), V)  # 1H: le toca cada hora
    cerrado, abierto = (lambda s, t: False), (lambda s, t: True)
    no_urg, si_urg = (lambda: False), (lambda: True)
    assert cada("Scalping 15m") == timedelta(minutes=15) and cada("Intraday 1H") == timedelta(hours=1)
    assert cada("Intraday D") == cada("Swing (S)") == cada("Swing (M)") == timedelta(days=1)  # D/S/M: diario
    assert siguiente([A, B], {A: viejo}, lun, no_urg, abierto) == B  # sin fila primero
    assert siguiente([A], {A: viejo}, lun, no_urg, abierto) == A  # ya hay vela nueva: incremental
    assert siguiente([A], {A: fresco}, lun, no_urg, abierto) is None  # todavia no cierra otra vela
    assert siguiente([A], {A: (fresco[0], "otro motor")}, lun, no_urg, abierto) == A  # motor nuevo: ya, sin esperar
    assert siguiente([A], {A: (fresco[0], None)}, lun, no_urg, abierto) == A  # filas de antes del incremental
    assert siguiente([A, B], {A: viejo, B: (viejo[0] - timedelta(days=1), V)}, lun, no_urg, abierto) == B  # el mas viejo primero
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
    assert pausa_tras(1, incremental=True) == PAUSA_INCREMENTAL_S and pausa_tras(4, incremental=True) == 8
    assert _sumar_embudo({"a": {"x": 1, "y": {"z": 2}}}, {"a": {"x": 3, "y": {"z": 1}}}) == {"a": {"x": 4, "y": {"z": 3}}}

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
    assert calc["velas"] > 3000 and set(calc["resultado"]) >= {"backtests", "embudo", "setups", "version", "completo_en"}
    assert calc["resultado"]["modo"] == "completo" and calc["resultado"]["version"] == VERSION_MOTOR
    assert set(calc["resultado"]["backtests"]["total"]) == {"compra", "venta"}
    pedidos.clear()
    calcular(sim, "Swing (M)", ahora, obtener=falso)
    assert pedidos[0] == ahora - 20 * ANIO, pedidos  # Swing: toda la historia (desde de la serie D)
    pedidos.clear()
    calcular(sim, "Intraday 1H", ahora, obtener=falso)
    assert pedidos[0] == ahora - 8 * ANIO, pedidos

    # incremental = completo (resultados de trading; las tasas de confluencia las corrige el completo semanal)
    o15 = sintetica(ahora - timedelta(days=40), ahora, "15min")
    o1h = sintetica(ahora - timedelta(days=60), ahora, "1h")
    inf = frozenset({"R1", "R2", "R6"})
    sin_conf = lambda bt: {d: {k: v for k, v in x.items() if not k.startswith("confluencia")} for d, x in bt["total"].items()}
    corte = len(o15) - 300
    prev = {**resultado_de(o15.iloc[:corte], o1h, "15m", "1H", inf, None, ahora), "hasta": o15.index[corte - 1].isoformat()}
    for fin in (len(o15) - 200, len(o15) - 200, len(o15)):  # el repetido: sin velas nuevas
        r_inc = resultado_de(o15.iloc[:fin], o1h, "15m", "1H", inf, prev, ahora)
        assert r_inc["modo"] == "incremental" and r_inc["completo_en"] == prev["completo_en"]
        prev = {**r_inc, "hasta": o15.index[fin - 1].isoformat()}
    r_full = resultado_de(o15, o1h, "15m", "1H", inf, None, ahora)
    assert sin_conf(r_inc["backtests"]) == sin_conf(r_full["backtests"]) and r_inc["embudo"] == r_full["embudo"]
    assert len(r_inc["setups"]) == len(r_full["setups"])
    # completo si el motor cambio, si el ultimo completo tiene una semana o si su vela firme ya no esta
    for malo in ({**prev, "version": "otro"}, {**prev, "completo_en": (ahora - timedelta(days=DIAS_COMPLETO)).isoformat()},
                 {**prev, "firme_hasta": (o15.index[-1] + timedelta(minutes=5)).isoformat()},  # vela que ya no esta
                 {k: v for k, v in prev.items() if k != "setups"}):  # fila de antes del incremental
        assert resultado_de(o15, o1h, "15m", "1H", inf, malo, ahora)["modo"] == "completo"

    assert leer(sim, "Scalping 15m") is None
    guardar(sim, "Scalping 15m", calc, calculado_en=ahora)
    guardar(sim, "Scalping 15m", calc, calculado_en=ahora)  # idempotente (upsert)
    r = leer(sim, "Scalping 15m")
    assert r["backtests"] == calc["resultado"]["backtests"] and r["embudo"] == calc["resultado"]["embudo"]
    assert r["velas"] == calc["velas"] and r["calculado_en"] == ahora.isoformat() and r["desde"] < r["hasta"]
    assert ultimos()[(sim, "Scalping 15m")] == (ahora, VERSION_MOTOR)
    assert "setups" not in r and "version" not in r  # lo interno del incremental no sale por la API
    previo = leer_previo(sim, "Scalping 15m")
    assert previo["setups"] == calc["resultado"]["setups"] and previo["hasta"] == calc["hasta"].isoformat()
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
