"""Historial de velas de XM (MT5) en las tablas velas_xm / velas_carga_xm.

Lo llena el cargador del VPS (cargar_historial_xm.py) por lotes con POST /api/v1/velas-xm/carga; el colector en vivo
(colector_velas.py -> api/velas_xm.py) lo mantiene al dia (espejo mientras la fuente activa sea Dukascopy). El diario (D)
no existe en XM con la misma hora de cierre: se arma aqui desde el 1H, con dias UTC (cierre 00:00 UTC, igual que el
almacen de Dukascopy); semanal y mensual se derivan del D al leer, como siempre (almacen.DERIVADAS).
La fuente que lee el motor se elige con MTB_VELAS_FUENTE=xm (conectividad/almacen.py); sin eso nada de esto se lee.
`spread` = puntos del broker por vela (0 = XM no lo guardo en esa epoca).
"""
import logging
import math

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
PASOS = {"15m": pd.Timedelta(minutes=15), "1H": pd.Timedelta(hours=1)}
MAX_VELAS_LOTE = 10000
TOLERANCIA_RELOJ = pd.Timedelta(seconds=5)
T_VELAS, T_CARGA = "velas_xm", "velas_carga_xm"  # fijas: este modulo nunca depende de MTB_VELAS_FUENTE
COLS = ["open", "high", "low", "close", "volume"]
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


# ------------------------------------------------------------------------------------------------ funciones puras
def _domingo(anio: int, mes: int, n: int) -> pd.Timestamp:
    d = pd.Timestamp(anio, mes, 1)
    return d + pd.Timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))


def servidor_a_utc(segundos) -> pd.DatetimeIndex:
    """Epoch que entrega el MT5 (hora de pared del servidor de XM, como si fuera UTC) -> DatetimeIndex UTC.
    Servidor = GMT+2 y GMT+3 con el horario de verano de EE. UU. (2.o dom de marzo 07:00 UTC -> 1.er dom de nov 06:00 UTC);
    misma regla que colector_velas.offset_regla."""
    w = pd.Series(pd.to_datetime(np.asarray(segundos, dtype="int64"), unit="s"))
    tent = w - pd.Timedelta(hours=2)
    verano = pd.Series(False, index=w.index)
    for y in range(int(w.dt.year.min()), int(w.dt.year.max()) + 1):
        ini, fin = _domingo(y, 3, 2) + pd.Timedelta(hours=7), _domingo(y, 11, 1) + pd.Timedelta(hours=6)
        verano |= (tent >= ini) & (tent < fin)
    utc = w - pd.to_timedelta(np.where(verano, 3, 2), unit="h")
    return pd.DatetimeIndex(utc).tz_localize("UTC")


def inicio_real(df: pd.DataFrame) -> pd.DataFrame:
    """Recorta el principio donde el MT5 solo tiene 1 barra por dia (XM rellena asi lo viejo: el oro, antes de may 2013,
    traia ~260 barras de 'H1' al ano). Empieza en el primer dia a partir del cual 5 dias operados seguidos tienen al menos
    la mitad de las velas de un dia normal (percentil 90 del conteo diario)."""
    if df.empty:
        return df
    c = df.groupby(df.index.normalize()).size()
    denso = c >= 0.5 * c.quantile(0.9)
    cinco = denso[::-1].rolling(5, min_periods=5).sum()[::-1] == 5
    if not cinco.any():
        return df.iloc[0:0]
    return df[df.index >= cinco.idxmax()]


def derivar_diario(h1: pd.DataFrame) -> pd.DataFrame:
    """Velas D (dia UTC) a partir de velas 1H; incluye el dia en curso, igual que almacen.agregar."""
    if h1.empty:
        return h1[COLS]
    return h1[COLS].resample("1D", label="left", closed="left").agg(_AGG).dropna(subset=["open"])


def preparar_mt5(rates: pd.DataFrame, serie: str, ahora: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Filas crudas de mt5.copy_rates_* (time, open, high, low, close, tick_volume, spread) -> velas listas para enviar:
    UTC, sin la vela aun abierta, alineadas, high/low corregidos para contener open/close, sin duplicados y recortadas al
    inicio real (inicio_real). Devuelve (DataFrame open/high/low/close/volume/spread, descartes por motivo)."""
    paso = PASOS[serie]
    desc = {"recibidas": len(rates)}
    d = rates.copy()
    d.index = servidor_a_utc(d["time"].to_numpy())
    d = d.rename(columns={"tick_volume": "volume"})[[*COLS, "spread"]]
    d = d[~d.index.duplicated(keep="last")].sort_index()
    n = len(d)
    d = d[(d.index == d.index.floor(paso)) & (d.index + paso <= ahora)]
    desc["fuera_de_serie_o_abierta"] = n - len(d)
    n = len(d)
    d = d[np.isfinite(d[[*COLS, "spread"]]).all(axis=1) & (d[["open", "high", "low", "close"]] > 0).all(axis=1)
          & (d.volume >= 0) & (d.spread >= 0)]
    desc["invalidas"] = n - len(d)
    d = d.assign(high=d[["open", "high", "close"]].max(axis=1), low=d[["open", "low", "close"]].min(axis=1))
    n = len(d)
    d = inicio_real(d)
    desc["recortadas_al_inicio"] = n - len(d)
    desc["enviar"] = len(d)
    return d.astype("float64"), desc


def validar_lote(payload: dict, ahora: pd.Timestamp, simbolos) -> tuple[str | None, str, str, pd.DataFrame | None]:
    """(error, simbolo, serie, DataFrame UTC con open/high/low/close/volume/spread). Mismas reglas que
    api.velas_xm.validar (alineada, ya cerrada, high/low coherentes) pero sin tope de antiguedad y hasta MAX_VELAS_LOTE."""
    simbolo, serie, velas = payload.get("simbolo"), payload.get("serie"), payload.get("velas")
    if simbolo not in simbolos:
        return f"simbolo desconocido: {simbolo!r}", "", "", None
    if serie not in PASOS:
        return f"serie invalida (solo {sorted(PASOS)}): {serie!r}", "", "", None
    if not isinstance(velas, list) or not 1 <= len(velas) <= MAX_VELAS_LOTE:
        return f"'velas' debe ser una lista de 1 a {MAX_VELAS_LOTE}", "", "", None
    paso = PASOS[serie]
    filas = {}
    for v in velas:
        try:
            ts = pd.Timestamp(int(v["ts"]), unit="s", tz="UTC")
            o, h, l, c, vol = (float(v[k]) for k in ("o", "h", "l", "c", "v"))
            s = float(v.get("s", 0))
        except (KeyError, TypeError, ValueError, AttributeError):
            return "vela mal formada (se esperan ts, o, h, l, c, v numericos; s opcional)", "", "", None
        if not all(math.isfinite(x) for x in (o, h, l, c, vol, s)) or min(o, h, l, c) <= 0 or vol < 0 or s < 0:
            return f"vela con valores no validos en {ts}", "", "", None
        if h < max(o, c) or l > min(o, c):
            return f"vela incoherente (high/low) en {ts}", "", "", None
        if ts != ts.floor(paso):
            return f"vela {ts} no alineada a la serie {serie}", "", "", None
        if ts + paso > ahora + TOLERANCIA_RELOJ:
            return f"vela {ts} aun no ha cerrado", "", "", None
        filas[ts] = (o, h, l, c, vol, s)
    df = pd.DataFrame.from_dict(filas, orient="index", columns=[*COLS, "spread"]).sort_index()
    df.index = pd.DatetimeIndex(df.index, name="timestamp").as_unit("ms")
    return None, simbolo, serie, df


# ------------------------------------------------------------------------------------------------ Postgres
def _get_conn():
    from conectividad.almacen import get_conn
    return get_conn()


def guardar_lote(simbolo: str, serie: str, df: pd.DataFrame, lote: int = 5000) -> int:
    """Upsert en velas_xm (con spread si el DataFrame lo trae; sin el, queda NULL en filas nuevas)."""
    if df.empty:
        return 0
    idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    spread = df["spread"].astype(float) if "spread" in df.columns else pd.Series(np.nan, index=df.index)
    filas = [(simbolo, serie, ts.to_pydatetime(), *r, None if math.isnan(s) else s)
             for ts, r, s in zip(idx, df[COLS].astype(float).itertuples(index=False, name=None), spread)]
    sql = f"""INSERT INTO {T_VELAS} (simbolo, serie, ts, open, high, low, close, volume, spread)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
              ON CONFLICT (simbolo, serie, ts) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume, spread = COALESCE(EXCLUDED.spread, {T_VELAS}.spread)"""
    with _get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(filas), lote):
            cur.executemany(sql, filas[i:i + lote])
    return len(filas)


def _leer(simbolo: str, serie: str, desde=None) -> pd.DataFrame:
    with _get_conn() as conn:
        filas = conn.execute(
            f"SELECT ts, open, high, low, close, volume FROM {T_VELAS} "
            "WHERE simbolo = %s AND serie = %s AND ts >= COALESCE(%s, '-infinity'::timestamptz) ORDER BY ts",
            (simbolo, serie, desde)).fetchall()
    df = pd.DataFrame(filas, columns=["ts", *COLS])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="timestamp").as_unit("ms")
    return df.astype("float64")


def actualizar_diario(simbolo: str, desde) -> int:
    """Rearma las velas D desde el dia UTC de `desde` con el 1H guardado (lo usa el colector en vivo y cerrar())."""
    desde = pd.Timestamp(desde)
    desde = (desde.tz_localize("UTC") if desde.tzinfo is None else desde.tz_convert("UTC")).normalize()
    return guardar_lote(simbolo, "D", derivar_diario(_leer(simbolo, "1H", desde.to_pydatetime())))


def registrar_cobertura(simbolo: str, serie: str, completa: bool = False) -> tuple | None:
    """Cobertura de velas_carga_xm = (primera, ultima) vela guardada; completa=True marca la carga historica terminada."""
    with _get_conn() as conn:
        d, h = conn.execute(f"SELECT min(ts), max(ts) FROM {T_VELAS} WHERE simbolo = %s AND serie = %s",
                            (simbolo, serie)).fetchone()
        if d is None:
            return None
        conn.execute(
            f"""INSERT INTO {T_CARGA} (simbolo, serie, desde, hasta, completa, actualizado_en)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (simbolo, serie) DO UPDATE SET desde = EXCLUDED.desde, hasta = EXCLUDED.hasta,
                  completa = {T_CARGA}.completa OR EXCLUDED.completa,
                  actualizado_en = GREATEST({T_CARGA}.actualizado_en, EXCLUDED.actualizado_en)""",
            (simbolo, serie, d, h, completa))
    return d, h


def cerrar(simbolo: str) -> dict:
    """Fin de la carga de un activo: arma el D completo desde el 1H y registra la cobertura de 15m, 1H y D como completa."""
    n_d = actualizar_diario(simbolo, pd.Timestamp("1990-01-01", tz="UTC"))
    out = {"D_velas": n_d}
    for serie in ("15m", "1H", "D"):
        c = registrar_cobertura(simbolo, serie, completa=True)
        out[serie] = None if c is None else [c[0].isoformat(), c[1].isoformat()]
    return out


def tras_guardar(simbolo: str, serie: str, df: pd.DataFrame, ya_en_xm: bool) -> None:
    """Llamada por api.velas_xm tras guardar velas en vivo: espeja a velas_xm si la fuente activa es Dukascopy, mantiene
    el D del dia en curso y extiende la cobertura si ya existe (nunca la crea: eso solo lo hace cerrar())."""
    if not ya_en_xm:
        guardar_lote(simbolo, serie, df)
    if serie == "1H":
        actualizar_diario(simbolo, df.index[0])
    with _get_conn() as conn:
        for s in ("1H", "D") if serie == "1H" else (serie,):
            conn.execute(f"UPDATE {T_CARGA} SET hasta = GREATEST(hasta, %s) WHERE simbolo = %s AND serie = %s",
                         (df.index[-1].to_pydatetime(), simbolo, s))


def procesar_carga(payload: dict, headers: dict, ahora=None, simbolos=None) -> tuple[int, dict]:
    """POST /api/v1/velas-xm/carga (X-MTB-Service-Key). Dos formas:
      {"simbolo","serie","velas":[{ts,o,h,l,c,v,s}...]}  -> guarda un lote en velas_xm
      {"simbolo","cerrar":true}                          -> arma el D y marca la carga completa."""
    from api.licencias import _clave_ok, _h
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    if simbolos is None:
        from conectividad.historico import SIMBOLOS as simbolos
    payload = payload if isinstance(payload, dict) else {}
    try:
        if payload.get("cerrar") is True:
            if payload.get("simbolo") not in simbolos:
                return 400, {"error": f"simbolo desconocido: {payload.get('simbolo')!r}"}
            return 200, {"ok": True, **cerrar(payload["simbolo"])}
        ahora = pd.Timestamp.now(tz="UTC") if ahora is None else pd.Timestamp(ahora)
        error, simbolo, serie, df = validar_lote(payload, ahora, simbolos)
        if error:
            return 400, {"error": error}
        return 200, {"ok": True, "guardadas": guardar_lote(simbolo, serie, df)}
    except Exception as e:
        log.exception("carga de historial XM fallo")
        return 503, {"error": f"base de datos no disponible: {e}"}


# ------------------------------------------------------------------------------------------------ pruebas
def demo() -> None:
    import os
    os.environ["MTB_SERVICE_KEY"] = "sk"
    from datetime import datetime, timezone
    import colector_velas as col

    # hora del servidor -> UTC: misma regla que el colector, incluidos los dos cambios de horario de EE. UU.
    for ano in (2010, 2013, 2024, 2026):
        for mes, dia, hora in ((1, 15, 10), (3, 5, 10), (3, 20, 10), (7, 1, 10), (10, 25, 10), (11, 20, 10)):
            utc = datetime(ano, mes, dia, hora, tzinfo=timezone.utc)
            pared = int(utc.timestamp()) + col.offset_regla(utc.replace(tzinfo=None))
            assert servidor_a_utc([pared])[0] == pd.Timestamp(utc), (ano, mes, dia)

    # recorte de 'H1' diario de XM: 3 anos de 1 barra/dia y luego velas por hora
    ini = pd.Timestamp("2012-01-02", tz="UTC")
    viejas = pd.date_range(ini, periods=60, freq="1D")
    nuevas = pd.date_range("2012-04-02", periods=24 * 10, freq="1h", tz="UTC")
    d = pd.DataFrame({c: 1.0 for c in COLS}, index=viejas.append(nuevas))
    r = inicio_real(d)
    assert r.index[0] == pd.Timestamp("2012-04-02", tz="UTC") and len(r) == len(nuevas), (r.index[0], len(r))
    assert inicio_real(d.iloc[:60]).empty is False  # sin nada denso, todo es "normal": se queda
    # el primer dia parcial no cuenta como denso
    asi = pd.date_range("2012-04-02 20:00", periods=4 + 24 * 8, freq="1h", tz="UTC")
    assert inicio_real(pd.DataFrame({c: 1.0 for c in COLS}, index=asi)).index[0] == pd.Timestamp("2012-04-03", tz="UTC")

    # diario desde 1H: dia UTC, OHLC y volumen
    h = pd.date_range("2026-10-26", periods=48, freq="1h", tz="UTC")
    x = pd.DataFrame({"open": range(48), "high": [v + 5 for v in range(48)], "low": [v - 1 for v in range(48)],
                      "close": [v + 1 for v in range(48)], "volume": 1.0}, index=h)
    dd = derivar_diario(x)
    assert len(dd) == 2 and dd.iloc[0].open == 0 and dd.iloc[0].high == 28 and dd.iloc[0].low == -1 \
        and dd.iloc[0].close == 24 and dd.iloc[0].volume == 24 and dd.index[1] == pd.Timestamp("2026-10-27", tz="UTC")

    # preparar_mt5: UTC, vela abierta fuera, duplicado fuera, high/low corregidos, inicio recortado
    ahora = pd.Timestamp("2026-10-30 12:30", tz="UTC")
    base = int(pd.Timestamp("2026-10-20 00:00").timestamp()) + 10800  # hora del servidor en verano de EE. UU. (GMT+3)
    fil = [(base + 3600 * i, 10 + i, 12 + i, 9 + i, 11 + i, 100, 30) for i in range(24 * 10)]
    fil.append(fil[-1])  # duplicado
    fil.append((base + 3600 * (24 * 10 + 12), 1.0, 1.0, 1.0, 1.0, 5, 30))  # esa hora (2026-10-30 12:00 UTC) cierra a las 13:00: abierta
    fil[5] = (fil[5][0], 15, 14, 9, 16, 100, 30)  # high < close
    r, desc = preparar_mt5(pd.DataFrame(fil, columns=["time", "open", "high", "low", "close", "tick_volume", "spread"]),
                           "1H", ahora)
    assert desc["recibidas"] == 242 and len(r) == 24 * 10 and r.index[0] == pd.Timestamp("2026-10-20 00:00", tz="UTC"), desc
    assert r.iloc[5].high == 16 and r.iloc[5].low == 9 and r.volume.iloc[0] == 100 and r.spread.iloc[0] == 30
    assert desc["fuera_de_serie_o_abierta"] == 1 and desc["invalidas"] == 0

    # lote: validaciones
    ahora = pd.Timestamp("2026-10-31 12:00", tz="UTC"); S = {"XAUUSD"}
    ts = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())
    v = lambda t, **k: {"ts": ts(t), "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "v": 10, "s": 30, **k}
    ok = {"simbolo": "XAUUSD", "serie": "1H", "velas": [v("2010-01-04 10:00"), v("2010-01-04 11:00")]}
    e, sim, se, df = validar_lote(ok, ahora, S)
    assert e is None and len(df) == 2 and list(df.columns) == [*COLS, "spread"] and df.spread.iloc[0] == 30
    malos = [({**ok, "simbolo": "X"}, "simbolo"), ({**ok, "serie": "4H"}, "serie"), ({**ok, "velas": []}, "velas"),
             ({**ok, "velas": [v("2010-01-04 11:00", h=0.5)]}, "incoherente"),
             ({**ok, "velas": [v("2010-01-04 10:30")]}, "alineada"),
             ({**ok, "velas": [v("2026-10-31 12:00")]}, "no ha cerrado"),
             ({**ok, "velas": [v("2010-01-04 10:00", s=-1)]}, "no validos"),
             ({**ok, "velas": [{"ts": 1}]}, "mal formada"),
             ({**ok, "velas": [v("2010-01-04 10:00")] * (MAX_VELAS_LOTE + 1)}, "velas")]
    for p, frag in malos:
        e = validar_lote(p, ahora, S)[0]
        assert e and frag in e, (p, e)
    # historico (varios anos atras) NO se rechaza por antiguo
    assert validar_lote({**ok, "velas": [v("2005-01-03 00:00")]}, ahora, S)[0] is None
    # clave de servicio
    assert procesar_carga(ok, {}, ahora, S)[0] == 401
    assert procesar_carga({"simbolo": "X", "cerrar": True}, {"X-MTB-Service-Key": "sk"}, ahora, S)[0] == 400
    print("conectividad.historial_xm.demo() OK (Postgres: se prueba en el laboratorio)")


if __name__ == "__main__":
    demo()
