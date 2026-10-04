"""Carga historica del almacen de velas en segundo plano (un hilo, prioridad baja).

Llena Postgres con años de historia de Dukascopy, hacia atras y en bloques, para las 3 series base y los
simbolos con fila activa en `catalogo_activos`. Reanudable: cada bloque se guarda y solo despues amplia
`velas_carga.desde`, asi un reinicio (o un fallo a medias) retoma donde iba, sin duplicados (PK + upsert) ni
huecos (el siguiente bloque termina exactamente en el `desde` ya cubierto; nunca se registra un hueco).

Cede el paso al refresco: antes de cada bloque duerme mientras haya combinaciones 15m/30m/1H pendientes
(refresco.hay_pendientes_urgentes), y descansa unos segundos entre bloques.
Variables: ALMACEN_CARGA_ACTIVA=0 la apaga (app.py).
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)

SERIES = ("D", "1H", "15m")  # D primero: pocos bloques y es lo que mas rinde
# Profundidad objetivo (None = toda la historia disponible) y tamaño de bloque hacia atras, por serie base.
OBJETIVO = {"15m": timedelta(days=3 * 365), "1H": timedelta(days=15 * 365), "D": None}
BLOQUE = {"15m": timedelta(days=30), "1H": timedelta(days=182), "D": timedelta(days=5 * 365)}
PAUSA_BLOQUE_S = 5  # descanso entre bloques
PAUSA_PASADA_S = 60  # entre pasadas (tambien es el "mas tarde" del reintento de un bloque vacio)
PAUSA_OCIOSA_S = 3600  # nada pendiente: revisar el catalogo cada hora
PAUSA_CEDER_S = 30  # espera cuando el refresco tiene trabajo urgente
MAX_CEDER_S = 1800  # tope de espera: un combo que nunca se refresca no debe matar de hambre a la carga
# Dukascopy responde vacio de forma esporadica (visto en real: un bloque 1H vacio a la primera y con datos al
# reintentar); solo tras este numero de vacios seguidos del MISMO bloque se toma como inicio de la historia.
VACIOS_FIN_HISTORIA = 3
MAX_FALLAS = 3  # bloques truncados seguidos tras los que se acepta el bloque (el modelo de mercado abierto no es perfecto)
ESPERA_VACIO = timedelta(hours=1)  # un vacio solo cuenta si pasó al menos esto desde el ultimo vacio contado
SONDA = timedelta(days=7)  # rango reciente conocido con datos para distinguir "fin de historia" de "Dukascopy caido"
PAUSA_ERROR_S = 60  # tras una excepcion en la pasada/bucle (nunca la pausa ociosa de 1 h)
CACHE_CEDER_S = 60  # el aviso "hay pendientes urgentes del refresco" se consulta a lo sumo cada 60 s
INICIO_S = 60  # deja arrancar el servicio y el primer ciclo del refresco


def series_pendientes(catalogo: list | None = None) -> list[tuple[str, str]]:
    """(simbolo, serie) por cargar: simbolos con fila activa en el catalogo (cualquier temporalidad) y en
    conectividad.SIMBOLOS, en las 3 series base, sin las ya `completa`."""
    from conectividad import almacen
    from conectividad.historico import SIMBOLOS
    if catalogo is None:
        import persistencia
        catalogo = persistencia.listar_catalogo()
    activos = sorted({c["simbolo"] for c in catalogo if c["activo"] and c["simbolo"] in SIMBOLOS})
    hechas = almacen.completas()
    return [(s, serie) for serie in SERIES for s in activos if (s, serie) not in hechas]


def descargar_real(simbolo: str, serie: str):
    """descargar(ini, fin) contra Dukascopy con la guarda de truncamiento compartida (historico._descargar)."""
    from conectividad import historico as h
    iv, inst = h._BASE[serie][0], h.SIMBOLOS[simbolo]
    return lambda a, b: h._descargar(inst, iv, a.to_pydatetime(), b.to_pydatetime(), simbolo)


def cargar_bloque(simbolo: str, serie: str, ahora, descargar, estado: dict) -> str:
    """Baja UN bloque hacia atras desde la cobertura registrada. Devuelve:
    "ok" (bloque guardado), "completa" (objetivo alcanzado o fin de la historia), "vacio" (sin datos: se
    reintenta en otra pasada; vacio VACIOS_FIN_HISTORIA veces seguidas = inicio de la historia) o "falla" (descarga truncada: no se
    guarda ni se registra, se reintenta luego). Los vacios se cuentan en velas_carga (sobreviven a un reinicio);
    `estado` (en memoria) solo cuenta las descargas truncadas."""
    from conectividad import almacen, historico

    ahora, clave = pd.Timestamp(ahora), (simbolo, serie)
    limite = None if OBJETIVO[serie] is None else ahora - OBJETIVO[serie]
    c = almacen.carga(simbolo, serie)
    if c is not None and limite is not None and c[0] <= limite:
        almacen.marcar_completa(simbolo, serie)
        return "completa"
    fin = ahora if c is None else c[0]  # el bloque termina donde empieza lo ya cubierto: contiguo, sin huecos
    ini = fin - BLOQUE[serie]
    if limite is not None:
        ini = max(ini, limite)
    kf = ("falla", clave)
    ultimo = almacen.vacios(simbolo, serie)  # (vacios contados, cuando se conto el ultimo); en la BD: sobrevive reinicios
    if ultimo is not None and ahora - ultimo[1] < ESPERA_VACIO:
        return "vacio"  # sin descargar: los vacios solo cuentan con >= 1 h de separacion (fallas transitorias)
    df = descargar(ini, fin)
    if df.empty:
        if c is None:  # el primer bloque (hasta ahora) nunca marca completa: sin datos recientes = problema, no historia
            log.warning("carga historica %s %s: sin datos recientes (%s..%s)", simbolo, serie, ini, fin)
            return "vacio"
        if not descargar(ahora - SONDA, ahora).empty:  # Dukascopy responde: el vacio es real (o esporadico)
            n = (ultimo[0] if ultimo else 0) + 1
            almacen.registrar_vacios(simbolo, serie, n, ahora)
            if n < VACIOS_FIN_HISTORIA:
                log.info("carga historica %s %s: bloque %s..%s sin datos (%d/%d), se reintenta despues",
                         simbolo, serie, ini, fin, n, VACIOS_FIN_HISTORIA)
                return "vacio"
            almacen.registrar_vacios(simbolo, serie, 0)
            almacen.marcar_completa(simbolo, serie)
            log.info("carga historica %s %s: completa (inicio de la historia en %s)", simbolo, serie, fin)
            return "completa"
        log.warning("carga historica %s %s: la sonda reciente tambien vino vacia (Dukascopy caido?); no cuenta", simbolo, serie)
        return "vacio"
    if ultimo is not None:
        almacen.registrar_vacios(simbolo, serie, 0)
    if historico.truncada(simbolo, df, historico._BASE[serie][1], fin, ahora):
        fallas = estado.get(kf, 0)
        if fallas < MAX_FALLAS:
            estado[kf] = fallas + 1
            log.warning("carga historica %s %s: bloque %s..%s truncado (termina %s), se reintenta despues (%d/%d)",
                        simbolo, serie, ini, fin, df.index[-1], fallas + 1, MAX_FALLAS)
            return "falla"  # registrar solo hasta la ultima vela dejaria un hueco (hasta < desde) o lo daria por cubierto
        log.warning("carga historica %s %s: bloque %s..%s sigue truncado tras %d intentos (termina %s); se acepta "
                    "(mercado con pausas propias?)", simbolo, serie, ini, fin, MAX_FALLAS, df.index[-1])
    estado.pop(kf, None)
    n = almacen.guardar(simbolo, serie, df)
    almacen.registrar_carga(simbolo, serie, ini, df.index[-1] if c is None else fin)
    log.info("carga historica %s %s: desde %s (%d filas)", simbolo, serie, ini.strftime("%Y-%m-%d"), n)
    if limite is not None and ini <= limite:
        almacen.marcar_completa(simbolo, serie)
        log.info("carga historica %s %s: completa (objetivo alcanzado)", simbolo, serie)
        return "completa"
    return "ok"


def _ceder(ceder, dormir, reloj) -> None:
    """Duerme mientras el refresco tenga trabajo urgente (hasta MAX_CEDER_S)."""
    t0 = reloj()
    while ceder():
        if reloj() - t0 >= MAX_CEDER_S:
            log.warning("carga historica: el refresco lleva %d s con pendientes urgentes; sigo igual", MAX_CEDER_S)
            return
        dormir(PAUSA_CEDER_S)


def _con_cache(f, ttl_s, reloj=time.monotonic):
    """f() con el resultado en cache ttl_s segundos."""
    ult = [None, 0.0]

    def g():
        if ult[0] is None or reloj() - ult[1] >= ttl_s:
            ult[0], ult[1] = (f(),), reloj()
        return ult[0][0]
    return g


def pasada(series, ahora_fn, descargar_fn, ceder, dormir, estado, reloj=time.monotonic) -> int:
    """Un bloque por (simbolo, serie) de `series`. Devuelve cuantos intento."""
    for simbolo, serie in series:
        try:
            _ceder(ceder, dormir, reloj)
            cargar_bloque(simbolo, serie, ahora_fn(), descargar_fn(simbolo, serie), estado)
        except Exception as e:  # red caida, BD caida...: seguir con el siguiente, esta serie reintenta en la pasada siguiente
            log.warning("carga historica %s %s fallo: %s: %s", simbolo, serie, type(e).__name__, e)
        dormir(PAUSA_BLOQUE_S)
    return len(series)


def _bucle(dormir=time.sleep, pasar=None) -> None:
    estado: dict = {}
    primera = True
    while True:
        pausa = PAUSA_ERROR_S  # cualquier excepcion (import, BD del catalogo...) reintenta pronto, no en 1 h
        try:
            if primera:
                dormir(INICIO_S)
                primera = False
            import refresco

            ceder = _con_cache(lambda: refresco.hay_pendientes_urgentes(datetime.now(timezone.utc)), CACHE_CEDER_S)
            n = (pasar or pasada)(series_pendientes(), lambda: datetime.now(timezone.utc), descargar_real,
                                  ceder, dormir, estado)
            pausa = PAUSA_PASADA_S if n else PAUSA_OCIOSA_S
        except Exception:
            log.exception("pasada de carga historica fallo")
        dormir(pausa)


def iniciar_en_segundo_plano() -> threading.Thread:
    hilo = threading.Thread(target=_bucle, name="carga-historica", daemon=True)
    hilo.start()
    return hilo


# ----------------------------------------------------------------------------------------------- pruebas
def demo() -> None:
    import sys
    if not os.environ.get("DATABASE_URL"):
        print("carga_historica.demo(): DATABASE_URL sin definir -> se omiten las pruebas", file=sys.stdout)
        return
    import numpy as np
    import persistencia  # noqa: F401  (migraciones)
    from conectividad import almacen

    sim, U = "_TEST_A3", timezone.utc
    ahora = pd.Timestamp("2026-09-16 12:07", tz="UTC")  # miercoles
    FREQ = {"15m": "15min", "1H": "1h", "D": "1D"}

    def historia(serie, dias):
        """Serie 24/7 completa desde ahora-dias hasta ahora (sin huecos), como una descarga de Dukascopy."""
        ix = pd.date_range((ahora - pd.Timedelta(days=dias)).ceil(FREQ[serie]), ahora, freq=FREQ[serie], tz="UTC",
                           name="timestamp", unit="ms")
        v = np.arange(len(ix), dtype=float)
        return pd.DataFrame({"open": v, "high": v + 1, "low": v - 1, "close": v + .5, "volume": v + 2}, index=ix)

    class Fake:
        def __init__(self, serie, dias, filtro=None):
            self.h, self.calls, self.filtro = historia(serie, dias), [], filtro

        def __call__(self, a, b):
            self.calls.append((a, b))
            df = self.h[(self.h.index >= a) & (self.h.index <= b)]
            return self.filtro(len(self.calls), a, b, df) if self.filtro else df

    def limpiar():
        with almacen.get_conn() as conn:
            conn.execute("DELETE FROM velas WHERE simbolo = %s", (sim,))
            conn.execute("DELETE FROM velas_carga WHERE simbolo = %s", (sim,))

    def n_filas(serie):
        with almacen.get_conn() as conn:
            return conn.execute("SELECT count(*) FROM velas WHERE simbolo = %s AND serie = %s", (sim, serie)).fetchone()[0]

    o_obj, o_blo = dict(OBJETIVO), dict(BLOQUE)
    try:
        limpiar()
        # 1) reanudable: interrupciones a mitad; sin duplicados ni huecos; se detiene al llegar al objetivo
        OBJETIVO["15m"], BLOQUE["15m"] = pd.Timedelta(days=90), pd.Timedelta(days=30)
        f = Fake("15m", 100)
        est = {}
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok" and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30)
        assert f.calls == [(ahora - pd.Timedelta(days=30), ahora)]
        # descarga que falla a mitad: nada cambia
        def rota(a, b):
            raise ConnectionError("dukascopy caido")
        try:
            cargar_bloque(sim, "15m", ahora, rota, {})
            raise AssertionError("debio propagar")
        except ConnectionError:
            pass
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30)
        # cae entre guardar y registrar la cobertura: el bloque queda guardado pero sin cubrir -> se rehace sin duplicar
        orig_reg = almacen.registrar_carga
        almacen.registrar_carga = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reinicio"))
        try:
            cargar_bloque(sim, "15m", ahora, f, {})
            raise AssertionError("debio propagar")
        except RuntimeError:
            pass
        finally:
            almacen.registrar_carga = orig_reg
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30) and n_filas("15m") > len(f.h[f.h.index >= ahora - pd.Timedelta(days=30)])
        # "reinicio" del proceso: estado nuevo, retoma desde velas_carga
        f.calls.clear()
        assert cargar_bloque(sim, "15m", ahora, f, {}) == "ok" and f.calls == [(ahora - pd.Timedelta(days=60), ahora - pd.Timedelta(days=30))]
        assert cargar_bloque(sim, "15m", ahora, f, {}) == "completa"  # ini == objetivo (ahora-90d)
        assert (sim, "15m") in almacen.completas() and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=90)
        esperado = f.h[f.h.index >= ahora - pd.Timedelta(days=90)]
        got = almacen.leer(sim, "15m", ahora - pd.Timedelta(days=200), ahora)
        assert n_filas("15m") == len(esperado) and got.index.equals(esperado.index), "hueco o duplicado"
        pd.testing.assert_frame_equal(got, esperado.astype("float64"), check_freq=False, check_names=False)
        f.calls.clear()
        assert cargar_bloque(sim, "15m", ahora, f, {}) == "completa" and f.calls == []  # ya completa: sin red

        # 2) fin de la historia: vacios solo cuentan con >= 1 h de separacion y con la sonda reciente OK;
        #    VACIOS_FIN_HISTORIA seguidos = completa; el primer bloque nunca marca completa
        limpiar()
        OBJETIVO["15m"] = pd.Timedelta(days=3 * 365)
        h1 = pd.Timedelta(hours=1)
        f = Fake("15m", 45)
        est = {}
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok"
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok"  # bloque parcial (15 d de datos)
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=60)
        assert cargar_bloque(sim, "15m", ahora, f, est) == "vacio" and (sim, "15m") not in almacen.completas()
        n = len(f.calls)
        assert cargar_bloque(sim, "15m", ahora + h1 / 2, f, est) == "vacio" and len(f.calls) == n  # < 1 h: ni descarga ni cuenta
        assert cargar_bloque(sim, "15m", ahora + h1, f, est) == "vacio" and (sim, "15m") not in almacen.completas()
        assert almacen.vacios(sim, "15m") == (2, ahora + h1)
        assert cargar_bloque(sim, "15m", ahora + 2 * h1, f, {}) == "completa"  # estado nuevo (reinicio): la cuenta sigue
        assert almacen.vacios(sim, "15m") is None
        assert (sim, "15m") in almacen.completas() and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=60)
        assert n_filas("15m") == len(f.h)
        # vacio esporadico: la siguiente descarga trae datos y el contador se reinicia
        limpiar()
        f = Fake("15m", 100, lambda k, a, b, df: df.iloc[0:0] if k == 3 else df)  # 3.a peticion: vacio esporadico
        est = {}
        assert [cargar_bloque(sim, "15m", ahora, f, est) for _ in range(3)] == ["ok", "ok", "vacio"]
        assert cargar_bloque(sim, "15m", ahora + h1, f, est) == "ok"
        assert almacen.vacios(sim, "15m") is None and (sim, "15m") not in almacen.completas()
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=90)
        # caida de Dukascopy: la sonda reciente tambien viene vacia -> no cuenta, nunca marca completa
        limpiar()
        caido = {"si": False}
        f = Fake("15m", 100, lambda k, a, b, df: df.iloc[0:0] if caido["si"] else df)
        est = {}
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok"
        caido["si"] = True
        assert [cargar_bloque(sim, "15m", ahora + i * h1, f, est) for i in range(1, 7)] == ["vacio"] * 6
        assert est == {} and almacen.vacios(sim, "15m") is None and (sim, "15m") not in almacen.completas()
        # el primer bloque (sin cobertura) vacio jamas marca completa
        limpiar()
        f = Fake("15m", 0)
        f.h = f.h.iloc[0:0]
        est = {}
        assert [cargar_bloque(sim, "15m", ahora + i * h1, f, est) for i in range(5)] == ["vacio"] * 5
        assert (sim, "15m") not in almacen.completas() and almacen.carga(sim, "15m") is None
        # D: sin objetivo (toda la historia), bloques de 5 años, para al agotarse
        limpiar()
        BLOQUE["D"] = pd.Timedelta(days=5 * 365)
        f, est = Fake("D", 12 * 365), {}
        r = [cargar_bloque(sim, "D", ahora + i * h1, f, est) for i in range(6)]
        assert r == ["ok", "ok", "ok", "vacio", "vacio", "completa"], r
        assert n_filas("D") == len(f.h) and (sim, "D") in almacen.completas()

        # 3) descarga truncada (termina 3 h antes de lo pedido, mercado abierto): no se guarda ni se cubre el hueco;
        #    tras MAX_FALLAS seguidas se acepta (modelo de mercado imperfecto: pausas diarias, festivos)
        limpiar()
        OBJETIVO["15m"] = pd.Timedelta(days=90)
        f = Fake("15m", 100, lambda k, a, b, df: df[df.index <= b - pd.Timedelta(hours=3)])
        est = {}
        assert [cargar_bloque(sim, "15m", ahora, f, est) for _ in range(MAX_FALLAS)] == ["falla"] * MAX_FALLAS
        assert almacen.carga(sim, "15m") is None and n_filas("15m") == 0
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok" and ("falla", (sim, "15m")) not in est  # aceptado
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30) and n_filas("15m") > 2000
        f = Fake("15m", 100, lambda k, a, b, df: df[df.index <= b - pd.Timedelta(hours=3)])
        assert cargar_bloque(sim, "15m", ahora, f, est) == "falla" and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30)
        # una descarga buena entre medias reinicia la cuenta de fallas
        f_ok = Fake("15m", 100)
        assert cargar_bloque(sim, "15m", ahora, f_ok, est) == "ok" and ("falla", (sim, "15m")) not in est

        # 4) cede el paso al refresco: no corre un bloque mientras haya pendientes urgentes
        limpiar()
        eventos, urgentes = [], [True, True, False]
        f = Fake("15m", 100)
        pasada([(sim, "15m")], lambda: ahora, lambda s, se: (lambda a, b: eventos.append("bloque") or f(a, b)),
               lambda: urgentes.pop(0) if urgentes else False, lambda s: eventos.append(("duerme", s)), {}, reloj=lambda: 0)
        assert eventos == [("duerme", PAUSA_CEDER_S)] * 2 + ["bloque", ("duerme", PAUSA_BLOQUE_S)], eventos
        # ...pero un refresco que nunca termina no la bloquea para siempre
        limpiar()
        reloj, eventos = [0], []
        pasada([(sim, "15m")], lambda: ahora, lambda s, se: (lambda a, b: eventos.append("bloque") or f(a, b)), lambda: True,
               lambda s: reloj.__setitem__(0, reloj[0] + s) or eventos.append(s), {}, reloj=lambda: reloj[0])
        assert eventos.count("bloque") == 1 and reloj[0] >= MAX_CEDER_S and eventos.count(PAUSA_CEDER_S) == MAX_CEDER_S // PAUSA_CEDER_S

        # 4b) fallos: _ceder dentro del try (un error al consultar al refresco no mata la pasada); cache de 60 s;
        #     el bucle tras un error duerme PAUSA_ERROR_S (import roto, BD caida) y nunca la pausa ociosa de 1 h
        limpiar()
        hechos = []
        def ceder_roto():
            raise RuntimeError("BD caida")
        durm = []
        assert pasada([(sim, "15m"), (sim, "1H")], lambda: ahora, lambda s_, se: hechos.append(se), ceder_roto,
                      durm.append, {}) == 2 and hechos == [] and durm == [PAUSA_BLOQUE_S] * 2
        t, llam = [0.0], []
        c = _con_cache(lambda: llam.append(1) or True, 60, reloj=lambda: t[0])
        assert c() and c() and len(llam) == 1
        t[0] = 59.9
        assert c() and len(llam) == 1
        t[0] = 60.0
        assert c() and len(llam) == 2
        durm, veces = [], [0]
        def pasar_roto(*a):
            veces[0] += 1
            if veces[0] == 3:
                raise KeyboardInterrupt  # sale del bucle de prueba
            raise ConnectionError("BD caida")
        try:
            _bucle(dormir=durm.append, pasar=pasar_roto)
        except KeyboardInterrupt:
            pass
        assert durm == [INICIO_S, PAUSA_ERROR_S, PAUSA_ERROR_S] and PAUSA_OCIOSA_S not in durm, durm

        # 5) series pendientes: catalogo activo, en SIMBOLOS, sin las completas
        limpiar()
        cat = [{"simbolo": "EURUSD", "temporalidad": "Scalping 15m", "activo": True},
               {"simbolo": "EURUSD", "temporalidad": "Swing (S)", "activo": True},
               {"simbolo": "GBPUSD", "temporalidad": "Scalping 15m", "activo": False},
               {"simbolo": "NOEXISTE", "temporalidad": "Scalping 15m", "activo": True},
               {"simbolo": "XAUUSD", "temporalidad": "Intraday 1H", "activo": True}]
        almacen.marcar_completa("XAUUSD", "D")
        try:
            assert series_pendientes(cat) == [("EURUSD", "D"), ("EURUSD", "1H"), ("XAUUSD", "1H"), ("EURUSD", "15m"), ("XAUUSD", "15m")]
        finally:
            with almacen.get_conn() as conn:
                conn.execute("DELETE FROM velas_carga WHERE simbolo = 'XAUUSD' AND serie = 'D' AND desde IS NULL")
        # registrar_carga sobre una fila solo-completa (sin cobertura) establece la cobertura
        almacen.marcar_completa(sim, "1H")
        almacen.registrar_carga(sim, "1H", ahora - pd.Timedelta(days=5), ahora - pd.Timedelta(days=1))
        assert almacen.carga(sim, "1H")[:2] == (ahora - pd.Timedelta(days=5), ahora - pd.Timedelta(days=1))
        # resumen_snapshots (lo usa refresco.hay_pendientes_urgentes): una consulta, sin el JSON completo
        persistencia.escribir_snapshot(sim, "Scalping 15m", {"velas": [1], "motor": "v2", "swing_length": 8}, None)
        persistencia.escribir_snapshot(sim, "Scalping 30m", {"velas": [], "motor": "v2", "swing_length": 8}, None)
        persistencia.escribir_snapshot(sim, "Swing (M)", {"velas": [1]}, None)
        # /api/setups guarda el conteo de velas (numero): con velas cuenta como lleno, 0 como vacio
        persistencia.escribir_snapshot(sim, "Intraday 1H", {"velas": 49800, "motor": "v2", "swing_length": 10}, None)
        persistencia.escribir_snapshot(sim, "Scalping 5m", {"velas": 0, "motor": "v2"}, None)
        try:
            r = persistencia.resumen_snapshots(("Scalping 15m", "Scalping 30m", "Intraday 1H", "Scalping 5m"))
            assert set(r) == {(sim, "Scalping 15m"), (sim, "Scalping 30m"), (sim, "Intraday 1H"), (sim, "Scalping 5m")}, r.keys()
            a_, b_ = r[(sim, "Scalping 15m")], r[(sim, "Scalping 30m")]
            assert a_["hay_velas"] is True and a_["motor"] == "v2" and a_["swing_length"] == 8 and a_["refrescado_en"] is not None
            assert b_["hay_velas"] is False
            assert r[(sim, "Intraday 1H")]["hay_velas"] is True and r[(sim, "Scalping 5m")]["hay_velas"] is False
        finally:
            with almacen.get_conn() as conn:
                conn.execute("DELETE FROM smc_snapshot WHERE simbolo = %s", (sim,))
    finally:
        OBJETIVO.update(o_obj), BLOQUE.update(o_blo)
        limpiar()
    print("carga_historica.demo() OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    demo()
