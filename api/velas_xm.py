"""POST /api/v1/velas-xm — velas CERRADAS de 15m y 1H leidas del MT5 de XM (colector_velas.py, VPS Windows).

Protegido con X-MTB-Service-Key (MTB_SERVICE_KEY). Body:
  {"simbolo": "EURUSD", "serie": "15m"|"1H", "velas": [{"ts": <epoch s UTC>, "o":..,"h":..,"l":..,"c":..,"v":..}, ...]}

Las velas se guardan en el almacen (upsert, igual que las de Dukascopy) y, si la ultima es la vela que acaba de
cerrar, la serie se marca al dia: el refresco la calcula de inmediato, sin esperar los ~2 min de publicacion de
Dukascopy ni descargar nada. Dukascopy sigue siendo la fuente del historial: en la siguiente sincronizacion vuelve a
bajar las ultimas velas y las sobrescribe con sus valores (diferencias de precio minimas entre brokers).
No calcula nada aqui: el calculo es del proceso de refresco, fuera del proceso HTTP.
"""

import math
from datetime import datetime, timezone

import pandas as pd

PASOS = {"15m": pd.Timedelta(minutes=15), "1H": pd.Timedelta(hours=1)}
MAX_VELAS = 10
MAX_ANTIGEDAD = pd.Timedelta(days=2)  # el colector solo manda lo reciente; el historial es de Dukascopy
TOLERANCIA_RELOJ = pd.Timedelta(seconds=5)


def validar(payload: dict, ahora: pd.Timestamp, simbolos) -> tuple[str | None, str, str, pd.DataFrame | None]:
    """(error, simbolo, serie, DataFrame con indice UTC y open/high/low/close/volume)."""
    simbolo, serie, velas = payload.get("simbolo"), payload.get("serie"), payload.get("velas")
    if simbolo not in simbolos:
        return f"simbolo desconocido: {simbolo!r}", "", "", None
    if serie not in PASOS:
        return f"serie invalida (solo {sorted(PASOS)}): {serie!r}", "", "", None
    if not isinstance(velas, list) or not 1 <= len(velas) <= MAX_VELAS:
        return f"'velas' debe ser una lista de 1 a {MAX_VELAS}", "", "", None
    paso = PASOS[serie]
    filas = {}
    for v in velas:
        try:
            ts = pd.Timestamp(int(v["ts"]), unit="s", tz="UTC")
            o, h, l, c, vol = (float(v[k]) for k in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError):
            return "vela mal formada (se esperan ts, o, h, l, c, v numericos)", "", "", None
        if not all(math.isfinite(x) for x in (o, h, l, c, vol)) or min(o, h, l, c) <= 0 or vol < 0:
            return f"vela con valores no validos en {ts}", "", "", None
        if h < max(o, c) or l > min(o, c):
            return f"vela incoherente (high/low) en {ts}", "", "", None
        if ts != ts.floor(paso):
            return f"vela {ts} no alineada a la serie {serie}", "", "", None
        if ts + paso > ahora + TOLERANCIA_RELOJ:
            return f"vela {ts} aun no ha cerrado", "", "", None
        if ahora - ts > MAX_ANTIGEDAD:
            return f"vela {ts} demasiado antigua", "", "", None
        filas[ts] = (o, h, l, c, vol)
    df = pd.DataFrame.from_dict(filas, orient="index", columns=["open", "high", "low", "close", "volume"]).sort_index()
    df.index = pd.DatetimeIndex(df.index, name="timestamp").as_unit("ms")
    return None, simbolo, serie, df


def procesar(payload: dict, headers: dict, ahora=None, almacen=None, simbolos=None) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    if simbolos is None:
        from conectividad.historico import SIMBOLOS as simbolos
    ahora = pd.Timestamp(datetime.now(timezone.utc) if ahora is None else ahora)
    error, simbolo, serie, df = validar(payload if isinstance(payload, dict) else {}, ahora, simbolos)
    if error:
        return 400, {"error": error}
    if almacen is None:
        try:
            from conectividad import almacen
        except Exception as e:
            return 503, {"error": f"almacen no disponible: {e}"}
    paso = PASOS[serie]
    try:
        almacen.guardar(simbolo, serie, df)
        cierre = ahora.floor(paso)
        al_dia = bool(df.index[-1] >= cierre - paso)  # trae la vela que acaba de cerrar
        # solo se marca al dia una serie que ya tiene cobertura registrada: sin ella, Dukascopy hace su carga normal
        if al_dia and almacen.carga(simbolo, serie) is not None:
            almacen.registrar_carga(simbolo, serie, df.index[0], df.index[-1], cierre + almacen.MARGEN_PUBLICACION)
        else:
            al_dia = False
    except Exception as e:
        return 503, {"error": f"base de datos no disponible: {e}"}
    return 200, {"ok": True, "guardadas": len(df), "al_dia": al_dia}


def demo() -> None:
    import os
    os.environ["MTB_SERVICE_KEY"] = "sk"
    ahora = pd.Timestamp("2026-10-24 07:00:07", tz="UTC")
    S = {"EURUSD", "BTCUSD"}
    ts = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())
    v = lambda s, **k: {"ts": ts(s), "o": 1.1, "h": 1.2, "l": 1.0, "c": 1.15, "v": 10, **k}

    class Alm:  # almacen falso: registra llamadas
        MARGEN_PUBLICACION = pd.Timedelta(minutes=2)

        def __init__(self, hay_carga=True):
            self.guardado, self.cargas, self.hay = [], [], hay_carga

        def guardar(self, s, se, df): self.guardado.append((s, se, list(df.index))); return len(df)
        def carga(self, s, se): return (1, 2, 3) if self.hay else None
        def registrar_carga(self, *a): self.cargas.append(a)

    h = {"x-mtb-service-key": "sk"}
    ok = {"simbolo": "EURUSD", "serie": "15m", "velas": [v("2026-10-24 06:30"), v("2026-10-24 06:45")]}
    assert procesar(ok, {}, ahora, Alm(), S)[0] == 401
    assert procesar(ok, {"X-MTB-Service-Key": "otra"}, ahora, Alm(), S)[0] == 401
    a = Alm()
    st, b = procesar(ok, h, ahora, a, S)
    assert st == 200 and b == {"ok": True, "guardadas": 2, "al_dia": True}, b
    assert len(a.guardado[0][2]) == 2 and a.cargas[0][4] == pd.Timestamp("2026-10-24 07:02", tz="UTC")  # cierre + margen
    # sin cobertura previa: guarda pero no marca al dia
    a = Alm(hay_carga=False)
    assert procesar(ok, h, ahora, a, S)[1]["al_dia"] is False and not a.cargas and a.guardado
    # vela vieja (no es la que acaba de cerrar): guarda, no marca al dia
    a = Alm()
    assert procesar({**ok, "velas": [v("2026-10-24 06:15")]}, h, ahora, a, S)[1]["al_dia"] is False and not a.cargas
    # validaciones
    malos = [({**ok, "simbolo": "XXX"}, "simbolo"), ({**ok, "serie": "4H"}, "serie"), ({**ok, "velas": []}, "velas"),
             ({**ok, "velas": [v("2026-10-24 06:45", h=0.5)]}, "incoherente"),
             ({**ok, "velas": [v("2026-10-24 06:50")]}, "alineada"),
             ({**ok, "velas": [v("2026-10-24 07:00")]}, "no ha cerrado"),
             ({**ok, "velas": [v("2026-10-20 06:45")]}, "antigua"),
             ({**ok, "velas": [v("2026-10-24 06:45", o=float("nan"))]}, "no validos"),
             ({**ok, "velas": [{"ts": 1}]}, "mal formada"),
             ({**ok, "serie": "1H", "velas": [v("2026-10-24 06:00")]}, None)]  # 1H 06:00 cerro a las 07:00: valida
    for p, frag in malos:
        st, b = procesar(p, h, ahora, Alm(), S)
        assert (st == 400 and frag in b["error"]) if frag else st == 200, (p, st, b)
    assert procesar(ok, h, ahora, type("AF", (), {"MARGEN_PUBLICACION": 0, "guardar": lambda *a: 1 / 0})(), S)[0] == 503
    print("api.velas_xm.demo() OK")


if __name__ == "__main__":
    demo()
