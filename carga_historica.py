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
    reintenta en otra pasada; vacio dos veces = inicio de la historia) o "falla" (descarga truncada: no se
    guarda ni se registra, se reintenta luego). `estado` cuenta los vacios por serie (en memoria)."""
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
    df = descargar(ini, fin)
    if df.empty:
        # Dukascopy tambien responde vacio de forma esporadica: una vez = reintentar mas tarde
        estado[clave] = estado.get(clave, 0) + 1
        if estado[clave] < 2:
            log.info("carga historica %s %s: bloque %s..%s sin datos, se reintenta despues", simbolo, serie, ini, fin)
            return "vacio"
        almacen.marcar_completa(simbolo, serie)
        log.info("carga historica %s %s: completa (inicio de la historia en %s)", simbolo, serie, fin)
        return "completa"
    estado.pop(clave, None)
    if historico.truncada(simbolo, df, historico._BASE[serie][1], fin, ahora):
        log.warning("carga historica %s %s: bloque %s..%s truncado (termina %s), se reintenta despues",
                    simbolo, serie, ini, fin, df.index[-1])
        return "falla"  # registrar solo hasta la ultima vela dejaria un hueco (hasta < desde) o lo daria por cubierto
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


def pasada(series, ahora_fn, descargar_fn, ceder, dormir, estado, reloj=time.monotonic) -> int:
    """Un bloque por (simbolo, serie) de `series`. Devuelve cuantos intento."""
    for simbolo, serie in series:
        _ceder(ceder, dormir, reloj)
        try:
            cargar_bloque(simbolo, serie, ahora_fn(), descargar_fn(simbolo, serie), estado)
        except Exception as e:  # red caida, BD caida...: seguir con el siguiente, esta serie reintenta en la pasada siguiente
            log.warning("carga historica %s %s fallo: %s: %s", simbolo, serie, type(e).__name__, e)
        dormir(PAUSA_BLOQUE_S)
    return len(series)


def _bucle() -> None:
    import refresco

    time.sleep(INICIO_S)
    estado: dict = {}
    while True:
        n = 0
        try:
            n = pasada(series_pendientes(), lambda: datetime.now(timezone.utc), descargar_real,
                       lambda: refresco.hay_pendientes_urgentes(datetime.now(timezone.utc)), time.sleep, estado)
        except Exception:
            log.exception("pasada de carga historica fallo")
        time.sleep(PAUSA_PASADA_S if n else PAUSA_OCIOSA_S)


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

        # 2) fin de la historia: vacio una vez = reintentar; vacio dos veces = completa. Vacio esporadico no.
        limpiar()
        OBJETIVO["15m"] = pd.Timedelta(days=3 * 365)
        f = Fake("15m", 45)
        est = {}
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok"
        assert cargar_bloque(sim, "15m", ahora, f, est) == "ok"  # bloque parcial (15 d de datos)
        assert almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=60)
        assert cargar_bloque(sim, "15m", ahora, f, est) == "vacio" and (sim, "15m") not in almacen.completas()
        n = len(f.calls)
        assert cargar_bloque(sim, "15m", ahora, f, est) == "completa" and len(f.calls) == n + 1
        assert (sim, "15m") in almacen.completas() and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=60)
        assert n_filas("15m") == len(f.h)
        limpiar()
        f = Fake("15m", 100, lambda k, a, b, df: df.iloc[0:0] if k == 3 else df)  # 3.a peticion: vacio esporadico
        est = {}
        assert [cargar_bloque(sim, "15m", ahora, f, est) for _ in range(4)] == ["ok", "ok", "vacio", "ok"]
        assert est == {} and (sim, "15m") not in almacen.completas() and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=90)
        # D: sin objetivo (toda la historia), bloques de 5 años, para al agotarse
        limpiar()
        BLOQUE["D"] = pd.Timedelta(days=5 * 365)
        f, est = Fake("D", 12 * 365), {}
        r = [cargar_bloque(sim, "D", ahora, f, est) for _ in range(5)]
        assert r == ["ok", "ok", "ok", "vacio", "completa"], r
        assert n_filas("D") == len(f.h) and (sim, "D") in almacen.completas()

        # 3) descarga truncada (termina 3 h antes de lo pedido, mercado abierto): no se guarda ni se cubre el hueco
        limpiar()
        OBJETIVO["15m"] = pd.Timedelta(days=90)
        f = Fake("15m", 100, lambda k, a, b, df: df[df.index <= b - pd.Timedelta(hours=3)])
        assert cargar_bloque(sim, "15m", ahora, f, {}) == "falla" and almacen.carga(sim, "15m") is None and n_filas("15m") == 0
        f2 = Fake("15m", 100)
        assert cargar_bloque(sim, "15m", ahora, f2, {}) == "ok"
        f = Fake("15m", 100, lambda k, a, b, df: df[df.index <= b - pd.Timedelta(hours=3)])
        assert cargar_bloque(sim, "15m", ahora, f, {}) == "falla" and almacen.carga(sim, "15m")[0] == ahora - pd.Timedelta(days=30)

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
    finally:
        OBJETIVO.update(o_obj), BLOQUE.update(o_blo)
        limpiar()
    print("carga_historica.demo() OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    demo()
