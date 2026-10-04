"""Almacen de velas en Postgres: series base 15m / 1H / D; 30m, 4H, S y M se derivan.

`leer` devuelve lo mismo que `obtener_velas` (open/high/low/close/volume float64,
indice DatetimeIndex UTC) para poder sustituirlo de forma transparente.
"""
import logging
import os
import threading
from contextlib import contextmanager

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
# maximo de conexiones Postgres simultaneas del almacen por proceso (hilos HTTP + refresco + carga)
_CONEXIONES = threading.BoundedSemaphore(4)


@contextmanager
def get_conn():
    # import perezoso: importar persistencia corre las migraciones y exige DATABASE_URL
    from persistencia.conexion import get_conn as _g
    with _CONEXIONES, _g() as conn:
        yield conn


COLS = ["open", "high", "low", "close", "volume"]
# serie derivada -> (serie base, regla de resample). 4H alinea a 00/04/.. UTC (origen = medianoche).
DERIVADAS = {"30m": ("15m", "30min"), "4H": ("1H", "4h"), "S": ("D", "W-MON"), "M": ("D", "MS")}
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def agregar(df_base: pd.DataFrame, serie_destino: str) -> pd.DataFrame:
    # ponytail: incluye el bloque en curso/parcial igual que obtener_velas con S/M; no exige bloques
    # completos porque los cierres de mercado dejan bloques legitimos con menos velas base.
    regla = DERIVADAS[serie_destino][1]
    if df_base.empty:
        return df_base
    return (df_base.resample(regla, label="left", closed="left").agg(_AGG).dropna(subset=["open"]))


def guardar(simbolo: str, serie: str, df: pd.DataFrame, lote: int = 5000) -> int:
    """Upsert por lotes; la ultima vela puede corregirse. Devuelve filas escritas."""
    if df.empty:
        return 0
    idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    filas = [(simbolo, serie, ts.to_pydatetime(), *r)
             for ts, r in zip(idx, df[COLS].astype(float).itertuples(index=False, name=None))]
    sql = """INSERT INTO velas (simbolo, serie, ts, open, high, low, close, volume)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
             ON CONFLICT (simbolo, serie, ts) DO UPDATE SET
               open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
               close = EXCLUDED.close, volume = EXCLUDED.volume"""
    with get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(filas), lote):
            cur.executemany(sql, filas[i:i + lote])
    return len(filas)


def leer(simbolo: str, serie: str, inicio, fin) -> pd.DataFrame:
    """Velas con inicio <= ts <= fin (naive = UTC)."""
    with get_conn() as conn:
        filas = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM velas "
            "WHERE simbolo = %s AND serie = %s AND ts >= %s AND ts <= %s ORDER BY ts",
            (simbolo, serie, _utc(inicio), _utc(fin))).fetchall()
    df = pd.DataFrame(filas, columns=["ts", *COLS])
    # resolucion ms = la misma que devuelve dp.fetch
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="timestamp").as_unit("ms")
    return df.astype("float64")


def rango(simbolo: str, serie: str):
    """(vela mas antigua, vela mas reciente) guardadas, o None si no hay."""
    with get_conn() as conn:
        d, h = conn.execute("SELECT min(ts), max(ts) FROM velas WHERE simbolo = %s AND serie = %s",
                            (simbolo, serie)).fetchone()
    return None if d is None else (pd.Timestamp(d), pd.Timestamp(h))


def carga(simbolo: str, serie: str):
    """(desde, hasta, sincronizado) de la cobertura contigua registrada en velas_carga, o None.
    desde = inicio pedido de la descarga (no la primera vela: un fin de semana no rompe la cobertura);
    hasta = ultima vela guardada; sincronizado (columna actualizado_en) = ultima vez que una descarga llego
    hasta el cierre mas reciente de la serie."""
    with get_conn() as conn:
        f = conn.execute("SELECT desde, hasta, actualizado_en FROM velas_carga WHERE simbolo = %s AND serie = %s",
                         (simbolo, serie)).fetchone()
    return None if f is None or f[0] is None else tuple(_utc(x) for x in f)


def registrar_carga(simbolo: str, serie: str, desde, hasta, sincronizado=None) -> None:
    """Amplia la cobertura con [desde, hasta] solo si se traslapa (no salta huecos); nunca la achica ni
    atrasa `sincronizado`, aunque escriban varios hilos/procesos a la vez (LEAST/GREATEST en una sola
    sentencia). `sincronizado=None` (descarga que no llego al cierre actual) no lo toca. No toca `completa`."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO velas_carga (simbolo, serie, desde, hasta, actualizado_en)
               VALUES (%s, %s, %s, %s, COALESCE(%s, 'epoch'::timestamptz))
               ON CONFLICT (simbolo, serie) DO UPDATE SET
                 desde = LEAST(velas_carga.desde, EXCLUDED.desde),
                 hasta = GREATEST(velas_carga.hasta, EXCLUDED.hasta),
                 actualizado_en = GREATEST(velas_carga.actualizado_en, EXCLUDED.actualizado_en)
               WHERE velas_carga.hasta IS NULL
                  OR (EXCLUDED.desde <= velas_carga.hasta AND EXCLUDED.hasta >= velas_carga.desde)""",
            (simbolo, serie, _utc(desde), _utc(hasta), None if sincronizado is None else _utc(sincronizado)))


def marcar_completa(simbolo: str, serie: str) -> None:
    """La carga historica llego a su objetivo o al inicio de la historia de Dukascopy."""
    with get_conn() as conn:
        conn.execute("INSERT INTO velas_carga (simbolo, serie, completa) VALUES (%s, %s, true) "
                     "ON CONFLICT (simbolo, serie) DO UPDATE SET completa = true", (simbolo, serie))


def vacios(simbolo: str, serie: str):
    """(vacios seguidos contados, cuando se conto el ultimo) del bloque mas viejo, o None si no hay cuenta."""
    with get_conn() as conn:
        f = conn.execute("SELECT vacios, ultimo_vacio FROM velas_carga WHERE simbolo = %s AND serie = %s",
                         (simbolo, serie)).fetchone()
    return None if f is None or not f[0] else (f[0], _utc(f[1]))


def registrar_vacios(simbolo: str, serie: str, n: int, cuando=None) -> None:
    """Guarda la cuenta de vacios (n=0 la reinicia). Solo sobre una fila existente: el primer bloque nunca cuenta."""
    with get_conn() as conn:
        conn.execute("UPDATE velas_carga SET vacios = %s, ultimo_vacio = %s WHERE simbolo = %s AND serie = %s",
                     (n, None if cuando is None else _utc(cuando), simbolo, serie))


def completas() -> set:
    """{(simbolo, serie)} con la carga historica terminada."""
    with get_conn() as conn:
        return set(conn.execute("SELECT simbolo, serie FROM velas_carga WHERE completa").fetchall())


# = refresco.MARGEN_DATOS: Dukascopy publica la vela cerrada con hasta ~2 min de retraso. Una sincronizacion
# antes de cierre + margen no cuenta como fresca (la vela recien cerrada pudo faltar o venir parcial).
MARGEN_PUBLICACION = pd.Timedelta(minutes=2)


def sincronizar(simbolo: str, serie: str, lo, hi, descargar, paso, ahora=None) -> pd.DataFrame:
    """Velas base con lo <= ts <= hi. Si velas_carga cubre `lo`:
    - fresca (sincronizada despues del ultimo cierre + margen) o `hi` antes de la ultima vela guardada →
      solo lee de Postgres, sin red (la unica vela que puede faltar es la que se esta formando, que el motor
      descarta);
    - si no, baja con `descargar(a, b)` desde la vela anterior a la ultima guardada hasta `hi`.
    Si no cubre `lo`: baja la ventana completa (como antes) y la guarda → la siguiente sera incremental.
    `paso` = duracion de una vela base (15m, 1H o 1D; el cierre de D es 00:00 UTC)."""
    lo, hi = _utc(lo), _utc(hi)
    ahora = _utc(pd.Timestamp.now(tz="UTC") if ahora is None else ahora)
    cierre = ahora.floor(paso)

    def sinc(df):
        # "al dia" solo si la descarga pidio hasta el cierre mas reciente Y trajo la ultima vela cerrada:
        # Dukascopy a veces corta una descarga (respuesta vacia) y con mercado cerrado no hay vela nueva;
        # en ambos casos no se marca fresca y la siguiente llamada vuelve a preguntar.
        return ahora if hi >= cierre and df.index[-1] >= cierre - paso else None
    c = carga(simbolo, serie)
    if c is not None and c[0] <= lo:
        fresca = c[2] >= cierre + MARGEN_PUBLICACION
        if hi >= c[1] and not fresca:  # >=: la ultima vela guardada pudo quedar parcial, se vuelve a pedir
            desde = c[1] - paso
            nuevas = descargar(desde.to_pydatetime(), hi.to_pydatetime())
            if not nuevas.empty:
                guardar(simbolo, serie, nuevas)
                registrar_carga(simbolo, serie, desde, nuevas.index[-1], sinc(nuevas))
        return leer(simbolo, serie, lo, hi)
    df = descargar(lo.to_pydatetime(), hi.to_pydatetime())
    if not df.empty and serie in ("15m", "1H") and df.index[0] - lo > pd.Timedelta(days=4):
        log.warning("almacen %s %s: la descarga pedida desde %s empieza en %s (posible descarga truncada)",
                    simbolo, serie, lo, df.index[0])
    if not df.empty:
        guardar(simbolo, serie, df)
        registrar_carga(simbolo, serie, lo, df.index[-1], sinc(df))
    return df


def _utc(t):
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def demo() -> None:
    import sys

    # --- agregacion offline (sin BD) ---
    ix = pd.date_range("2026-01-05 00:00", periods=96, freq="15min", tz="UTC", unit="ms")  # lunes, 1 dia (ms = como dp.fetch)
    rng = np.random.default_rng(1)
    c = 100 + rng.normal(size=96).cumsum()
    base = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c + .1, "volume": rng.uniform(1, 5, 96)}, index=ix)
    a = agregar(base, "30m")
    assert len(a) == 48 and a.iloc[0].open == base.iloc[0].open and a.iloc[0].close == base.iloc[1].close
    assert a.iloc[0].high == base.iloc[:2].high.max() and a.iloc[0].volume == base.iloc[:2].volume.sum()
    h = agregar(base, "4H")
    assert len(h) == 6 and h.index[1] == pd.Timestamp("2026-01-05 04:00", tz="UTC")
    s = agregar(base, "S")
    assert len(s) == 1 and s.index[0] == pd.Timestamp("2026-01-05", tz="UTC")
    assert agregar(base.iloc[0:0], "4H").empty
    print("conectividad.almacen.demo(): agregacion OK")

    if not os.environ.get("DATABASE_URL"):
        print("conectividad.almacen.demo(): DATABASE_URL sin definir -> se omiten las pruebas de Postgres", file=sys.stdout)
        return
    import persistencia  # noqa: F401  (corre migraciones)
    sim = "_TEST_ALM"
    with get_conn() as conn:
        conn.execute("DELETE FROM velas WHERE simbolo = %s", (sim,))
    assert rango(sim, "15m") is None and leer(sim, "15m", ix[0], ix[-1]).empty
    assert guardar(sim, "15m", base) == 96
    got = leer(sim, "15m", ix[0], ix[-1])
    pd.testing.assert_frame_equal(got, base.astype("float64"), check_freq=False, check_names=False)
    assert got.index.tz is not None and str(got.index.tz) == "UTC" and (got.dtypes == "float64").all()
    assert rango(sim, "15m") == (ix[0], ix[-1])
    assert len(leer(sim, "15m", ix[10].tz_localize(None), ix[20].tz_localize(None))) == 11  # naive = UTC, inclusivo
    # upsert: corrige la ultima vela, sin duplicar
    corr = base.iloc[-1:].copy()
    corr["close"] = 999.0
    guardar(sim, "15m", corr)
    got = leer(sim, "15m", ix[0], ix[-1])
    assert len(got) == 96 and got.iloc[-1].close == 999.0
    with get_conn() as conn:
        conn.execute("DELETE FROM velas WHERE simbolo = %s", (sim,))
    print("conectividad.almacen.demo(): Postgres OK")
    _demo_sincronizar(sim)


def _demo_sincronizar(sim: str) -> None:
    """Incremental con Dukascopy simulado: que rango se pide y como se mueve velas_carga."""
    paso = pd.Timedelta(minutes=15)
    todo = pd.DataFrame({c: np.arange(400, dtype=float) + i for i, c in enumerate(COLS)},
                        index=pd.date_range("2026-03-02", periods=400, freq="15min", tz="UTC", name="timestamp", unit="ms"))
    pedidos = []

    def descargar(a, b):
        pedidos.append((pd.Timestamp(a), pd.Timestamp(b)))
        return todo[(todo.index >= pd.Timestamp(a)) & (todo.index <= pd.Timestamp(b))]

    def limpiar():
        with get_conn() as conn:
            conn.execute("DELETE FROM velas WHERE simbolo = %s", (sim,))
            conn.execute("DELETE FROM velas_carga WHERE simbolo = %s", (sim,))

    limpiar()
    t = todo.index
    # 1) frio: baja la ventana completa, la guarda y registra la cobertura
    got = sincronizar(sim, "15m", t[10], t[200], descargar, paso)
    assert pedidos == [(t[10], t[200])] and got.equals(todo.iloc[10:201])
    assert carga(sim, "15m")[:2] == (t[10], t[200])
    # 2) cubierto: solo pide desde la ultima vela guardada (menos una) hasta fin; resultado = directo
    pedidos.clear()
    got = sincronizar(sim, "15m", t[50], t[300], descargar, paso)
    assert pedidos == [(t[199], t[300])], pedidos
    pd.testing.assert_frame_equal(got, todo.iloc[50:301], check_freq=False)
    assert carga(sim, "15m")[:2] == (t[10], t[300])
    # 3) ventana dentro de lo guardado: no pide nada a Dukascopy
    pedidos.clear()
    assert sincronizar(sim, "15m", t[20], t[250], descargar, paso).equals(todo.iloc[20:251]) and pedidos == []
    # 3b) fin == ultima vela guardada: se vuelve a pedir (pudo guardarse parcial) y se corrige
    todo.iloc[300, 3] = -1.0
    got = sincronizar(sim, "15m", t[20], t[300], descargar, paso)
    assert pedidos == [(t[299], t[300])] and got.close.iloc[-1] == -1.0, pedidos
    pedidos.clear()
    # 4) ventana anterior sin traslape: bajada directa, la cobertura no salta el hueco
    got = sincronizar(sim, "15m", t[0], t[5], descargar, paso)
    assert pedidos == [(t[0], t[5])] and carga(sim, "15m")[:2] == (t[10], t[300])
    # 5) traslape por la izquierda: amplia desde; nunca achica (LEAST/GREATEST)
    pedidos.clear()
    sincronizar(sim, "15m", t[3], t[100], descargar, paso)
    assert pedidos == [(t[3], t[100])] and carga(sim, "15m")[:2] == (t[3], t[300])
    registrar_carga(sim, "15m", t[250], t[260])
    assert carga(sim, "15m")[:2] == (t[3], t[300])
    # 7) frescura: en el mismo periodo ya sincronizado no se toca la red
    limpiar()
    m = MARGEN_PUBLICACION
    ahora0 = t[200] + m + pd.Timedelta(minutes=1)            # vela t[199] cerrada y publicada
    sincronizar(sim, "15m", t[10], t[200], descargar, paso, ahora=ahora0)
    assert carga(sim, "15m")[2] == ahora0
    pedidos.clear()
    got = sincronizar(sim, "15m", t[20], t[200], descargar, paso, ahora=t[200] + pd.Timedelta(minutes=10))
    assert pedidos == [] and got.equals(todo.iloc[20:201]), pedidos
    # (b) fin en el pasado dentro de la cobertura: sin red aunque ya no este fresca
    assert sincronizar(sim, "15m", t[20], t[150], descargar, paso, ahora=t[390]).equals(todo.iloc[20:151])
    assert pedidos == []
    # dentro del margen tras el siguiente cierre: todavia no cuenta como fresca → pide
    ahora1 = t[201] + pd.Timedelta(minutes=1)
    sincronizar(sim, "15m", t[20], t[201], descargar, paso, ahora=ahora1)
    assert pedidos == [(t[199], t[201])] and carga(sim, "15m")[2] == ahora1, pedidos
    pedidos.clear()
    sincronizar(sim, "15m", t[20], t[201], descargar, paso, ahora=t[201] + m + pd.Timedelta(seconds=30))
    assert pedidos == [(t[200], t[201])], pedidos
    pedidos.clear()
    assert sincronizar(sim, "15m", t[20], t[201], descargar, paso, ahora=t[201] + pd.Timedelta(minutes=9)).equals(
        todo.iloc[20:202]) and pedidos == []
    # sincronizado nunca retrocede; una descarga que no llega al cierre actual no la marca fresca
    ult = carga(sim, "15m")[2]
    registrar_carga(sim, "15m", t[20], t[201], ahora0)
    assert carga(sim, "15m")[2] == ult
    # mercado cerrado (sin vela recien cerrada): no se marca fresca, se vuelve a preguntar
    fin_sem = t[399] + pd.Timedelta(days=1)
    sincronizar(sim, "15m", t[20], fin_sem, descargar, paso, ahora=fin_sem + pd.Timedelta(minutes=3))
    pedidos.clear()
    sincronizar(sim, "15m", t[20], fin_sem, descargar, paso, ahora=fin_sem + pd.Timedelta(minutes=5))
    assert pedidos == [(t[398], fin_sem)] and carga(sim, "15m")[1] == t[399], pedidos
    # descarga cortada por Dukascopy (falta la ultima vela cerrada): no se marca fresca
    limpiar()
    ahora2 = t[300] + m + pd.Timedelta(minutes=1)
    sincronizar(sim, "15m", t[10], t[300], lambda a, b: descargar(a, t[250]), paso, ahora=ahora2)
    assert carga(sim, "15m")[1:] == (t[250], pd.Timestamp("1970-01-01", tz="UTC"))
    pedidos.clear()
    got = sincronizar(sim, "15m", t[10], t[300], descargar, paso, ahora=ahora2 + pd.Timedelta(minutes=1))
    assert pedidos == [(t[249], t[300])] and got.equals(todo.iloc[10:301]), pedidos
    # 6) sin datos (fin de semana / futuro): no registra nada raro
    limpiar()
    fut = pd.Timestamp("2027-01-01", tz="UTC")
    assert sincronizar(sim, "15m", fut, fut + paso, descargar, paso).empty and carga(sim, "15m") is None
    limpiar()
    print("conectividad.almacen.demo(): sincronizar incremental OK")


def demo_red() -> None:
    """Necesita red: agregar(base) == velas nativas de Dukascopy (sin los bloques de borde de la ventana,
    que quedan cortados por la descarga) y == agregacion actual de obtener_velas para S/M."""
    import logging
    from datetime import datetime
    import dukascopy_python as dp
    from conectividad.historico import obtener_velas

    logging.disable(logging.INFO)
    s, i, f = "XAUUSD", datetime(2026, 8, 3), datetime(2026, 9, 14)
    for base, dest, nativo in (("15m", "30m", dp.INTERVAL_MIN_30), ("1H", "4H", dp.INTERVAL_HOUR_4)):
        b = obtener_velas(s, i, f, {"15m": dp.INTERVAL_MIN_15, "1H": dp.INTERVAL_HOUR_1}[base])
        ag, nat = agregar(b, dest).iloc[1:-1], obtener_velas(s, i, f, nativo)
        assert ag.index.equals(nat.loc[ag.index].index) and len(ag) > 100
        pd.testing.assert_frame_equal(ag, nat.loc[ag.index].astype("float64"), check_freq=False, rtol=1e-12)
    d = obtener_velas(s, datetime(2025, 1, 1), f, dp.INTERVAL_DAY_1)
    for dest, nativo in (("S", dp.INTERVAL_WEEK_1), ("M", dp.INTERVAL_MONTH_1)):
        pd.testing.assert_frame_equal(agregar(d, dest), obtener_velas(s, datetime(2025, 1, 1), f, nativo), check_freq=False)
    print("conectividad.almacen.demo_red() OK: 30m, 4H, S, M equivalentes")


if __name__ == "__main__":
    demo()
    if os.environ.get("ALMACEN_DEMO_RED") == "1":
        demo_red()
