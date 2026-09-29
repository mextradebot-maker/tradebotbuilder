"""Almacen de velas en Postgres: series base 15m / 1H / D; 30m, 4H, S y M se derivan.

`leer` devuelve lo mismo que `obtener_velas` (open/high/low/close/volume float64,
indice DatetimeIndex UTC) para poder sustituirlo de forma transparente.
"""
import os

import numpy as np
import pandas as pd


def get_conn():
    # import perezoso: importar persistencia corre las migraciones y exige DATABASE_URL
    from persistencia.conexion import get_conn as _g
    return _g()


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
    """(desde, hasta) de la cobertura contigua registrada en velas_carga, o None.
    desde = inicio pedido de la descarga (no la primera vela: un fin de semana no rompe la cobertura);
    hasta = ultima vela guardada."""
    with get_conn() as conn:
        f = conn.execute("SELECT desde, hasta FROM velas_carga WHERE simbolo = %s AND serie = %s",
                         (simbolo, serie)).fetchone()
    return None if f is None or f[0] is None else (pd.Timestamp(f[0]), pd.Timestamp(f[1]))


def registrar_carga(simbolo: str, serie: str, desde, hasta) -> None:
    """Amplia la cobertura con [desde, hasta] solo si se traslapa (no salta huecos); nunca la achica,
    aunque escriban varios hilos/procesos a la vez (LEAST/GREATEST en una sola sentencia). No toca `completa`."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO velas_carga (simbolo, serie, desde, hasta) VALUES (%s, %s, %s, %s)
               ON CONFLICT (simbolo, serie) DO UPDATE SET
                 desde = LEAST(velas_carga.desde, EXCLUDED.desde),
                 hasta = GREATEST(velas_carga.hasta, EXCLUDED.hasta), actualizado_en = now()
               WHERE EXCLUDED.desde <= velas_carga.hasta AND EXCLUDED.hasta >= velas_carga.desde""",
            (simbolo, serie, _utc(desde), _utc(hasta)))


def sincronizar(simbolo: str, serie: str, lo, hi, descargar, paso) -> pd.DataFrame:
    """Velas base con lo <= ts <= hi. Si velas_carga cubre `lo`, solo baja con `descargar(a, b)` desde la
    ultima vela guardada (se vuelve a pedir la anterior a esa, pudo quedar incompleta) hasta `hi` y lee
    del almacen; si no, baja la ventana completa (como antes) y la guarda para que la siguiente sea
    incremental. `paso` = duracion de una vela base."""
    lo, hi = _utc(lo), _utc(hi)
    c = carga(simbolo, serie)
    if c is not None and c[0] <= lo:
        if hi > c[1]:
            desde = c[1] - paso
            nuevas = descargar(desde.to_pydatetime(), hi.to_pydatetime())
            if not nuevas.empty:
                guardar(simbolo, serie, nuevas)
                registrar_carga(simbolo, serie, desde, nuevas.index[-1])
        return leer(simbolo, serie, lo, hi)
    df = descargar(lo.to_pydatetime(), hi.to_pydatetime())
    if not df.empty:
        guardar(simbolo, serie, df)
        registrar_carga(simbolo, serie, lo, df.index[-1])
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
    assert carga(sim, "15m") == (t[10], t[200])
    # 2) cubierto: solo pide desde la ultima vela guardada (menos una) hasta fin; resultado = directo
    pedidos.clear()
    got = sincronizar(sim, "15m", t[50], t[300], descargar, paso)
    assert pedidos == [(t[199], t[300])], pedidos
    pd.testing.assert_frame_equal(got, todo.iloc[50:301], check_freq=False)
    assert carga(sim, "15m") == (t[10], t[300])
    # 3) ventana dentro de lo guardado: no pide nada a Dukascopy
    pedidos.clear()
    assert sincronizar(sim, "15m", t[20], t[250], descargar, paso).equals(todo.iloc[20:251]) and pedidos == []
    # 4) ventana anterior sin traslape: bajada directa, la cobertura no salta el hueco
    got = sincronizar(sim, "15m", t[0], t[5], descargar, paso)
    assert pedidos == [(t[0], t[5])] and carga(sim, "15m") == (t[10], t[300])
    # 5) traslape por la izquierda: amplia desde; nunca achica (LEAST/GREATEST)
    pedidos.clear()
    sincronizar(sim, "15m", t[3], t[100], descargar, paso)
    assert pedidos == [(t[3], t[100])] and carga(sim, "15m") == (t[3], t[300])
    registrar_carga(sim, "15m", t[250], t[260])
    assert carga(sim, "15m") == (t[3], t[300])
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
