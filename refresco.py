"""Refresco de snapshots SMC dentro del servicio (reemplaza el cron n8n de 180 llamadas HTTP).

Las combinaciones pendientes se procesan por prioridad (Scalping primero) en un pool pequeño de
procesos `spawn` con prioridad baja (os.nice), para no ahogar al servidor HTTP en el VPS compartido.
REFRESCO_WORKERS fija el tamaño (1 = en serie, en el mismo proceso, para depurar).

Una combinación activo×temporalidad solo se refresca cuando cerró una vela nueva
de SU temporalidad (Scalping 15m, Intraday 1h, Swing H 4h) y su snapshot es
anterior a ese cierre; Swing S y M, una vez al día (su vela en curso cambia a diario). Con el mercado cerrado
(fin de semana forex) no se refresca nada salvo cripto.
"""

import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timedelta, timezone

CRIPTO = {"BTCUSD", "ETHUSD", "XRPUSD"}
MARGEN_DATOS = timedelta(minutes=2)
REINTENTO_VACIO = timedelta(hours=1)  # el proveedor publica la vela cerrada con un poco de retraso
PAUSA_CICLO_S = 60
# Swing S/M se recalculan a diario (no solo al cerrar su vela) para detectar un cambio
# de tendencia a tiempo. 03:00 UTC: tras el cierre diario forex (21:00) y con la vela
# diaria ya publicada, antes del Análisis Diario de n8n (06:00 UTC).
REFRESCO_DIARIO = {"Swing (S)", "Swing (M)"}
HORA_REFRESCO_DIARIO = 3
PRIORIDAD = ("Scalping", "Intraday", "Swing (H)", "Swing (S)", "Swing (M)")  # la vela más corta caduca antes
_pool: ProcessPoolExecutor | None = None


def inicio_vela(ahora: datetime, temporalidad: str) -> datetime:
    """Inicio de la vela en curso; la vela anterior cerró justo en este instante."""
    t = ahora.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if temporalidad == "Scalping":
        return t.replace(minute=t.minute - t.minute % 15)
    if temporalidad == "Intraday":
        return t.replace(minute=0)
    if temporalidad in ("Swing (H)", "Swing"):
        return t.replace(minute=0, hour=t.hour - t.hour % 4)
    dia = t.replace(minute=0, hour=0)
    if temporalidad == "Swing (S)":
        return dia - timedelta(days=dia.weekday())  # lunes 00:00 UTC
    if temporalidad == "Swing (M)":
        return dia.replace(day=1)
    raise ValueError(f"temporalidad desconocida: {temporalidad}")


def mercado_abierto(simbolo: str, ahora: datetime) -> bool:
    if simbolo in CRIPTO:
        return True
    t = ahora.astimezone(timezone.utc)
    # forex: cierra viernes 21:00 UTC, abre domingo 21:00 UTC
    if t.weekday() == 5:
        return False
    if t.weekday() == 4 and t.hour >= 21:
        return False
    if t.weekday() == 6 and t.hour < 21:
        return False
    return True


def necesita_refresco(simbolo: str, temporalidad: str, refrescado_en: datetime | None, ahora: datetime,
                      vacio: bool = False) -> bool:
    if refrescado_en is not None and refrescado_en.tzinfo is None:
        refrescado_en = refrescado_en.replace(tzinfo=timezone.utc)
    if vacio and refrescado_en is not None:  # descarga fallida antes: reintentar aunque sea fin de semana
        return ahora - refrescado_en >= REINTENTO_VACIO
    if not mercado_abierto(simbolo, ahora) and refrescado_en is not None:
        return False
    if temporalidad in REFRESCO_DIARIO:  # la vela semanal/mensual en curso cambia cada día
        hoy = ahora.astimezone(timezone.utc).replace(hour=HORA_REFRESCO_DIARIO, minute=0, second=0, microsecond=0)
        cierre = hoy if ahora >= hoy else hoy - timedelta(days=1)
    else:
        cierre = inicio_vela(ahora, temporalidad)
    if ahora < cierre + MARGEN_DATOS:  # vela recién cerrada: esperar a que el proveedor la publique
        return False
    if refrescado_en is None:
        return True
    if refrescado_en.tzinfo is None:
        refrescado_en = refrescado_en.replace(tzinfo=timezone.utc)
    return refrescado_en < cierre + MARGEN_DATOS


def ordenar(combos: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Por PRIORIDAD de temporalidad (estable: respeta el orden del catálogo dentro de cada una)."""
    return sorted(combos, key=lambda c: PRIORIDAD.index(c[1]) if c[1] in PRIORIDAD else len(PRIORIDAD))


def pendientes(ahora: datetime) -> list[tuple[str, str]]:
    import persistencia
    from api.setups import SWING_LENGTH_POR_TEMPORALIDAD

    salida = []
    for c in persistencia.listar_catalogo():
        if not c["activo"]:
            continue
        snap = persistencia.leer_snapshot(c["simbolo"], c["temporalidad"])
        r = (snap or {}).get("respuesta") or {}
        # vacío (descarga fallida) o calculado con otro swing_length (recalibración): rehacer, máx 1/hora
        vacio = bool(snap) and (not r.get("velas") or r.get("motor") != "v2" or r.get("swing_length") != SWING_LENGTH_POR_TEMPORALIDAD.get(c["temporalidad"]))
        if necesita_refresco(c["simbolo"], c["temporalidad"], snap and snap["refrescado_en"], ahora, vacio):
            salida.append((c["simbolo"], c["temporalidad"]))
    return ordenar(salida)


def refrescar_uno(simbolo: str, temporalidad: str) -> tuple:
    """Worker (nivel de módulo: se ejecuta en un proceso spawn). (simbolo, temporalidad, status, error);
    status None si procesar lanzó. procesar abre su propia conexión a Postgres por llamada."""
    import api.setups
    from api.setups import procesar

    if api.setups._persistencia is None:  # import de persistencia falló en este proceso: no fingir un refresco
        return simbolo, temporalidad, None, "sin persistencia en el worker"
    try:
        status, body = procesar({"simbolo": simbolo, "temporalidad": temporalidad, "_force_refresh": True})
        return simbolo, temporalidad, status, body.get("error") if status != 200 else None
    except Exception as e:
        return simbolo, temporalidad, None, f"{type(e).__name__}: {e}"


def _workers() -> int:
    # ponytail: 1 por defecto. mtb-api tiene 0.5 CPU / 1 GB y os.cpu_count() en Docker da los núcleos
    # del host: 3 procesos spawn (~150-250 MB c/u) arriesgan OOM sin ganar CPU. Subir a 2 solo tras medir RSS.
    try:
        return max(1, int(os.environ.get("REFRESCO_WORKERS", "1")))
    except ValueError:
        return 1


def _bajar_prioridad() -> None:
    try:
        os.nice(10)
    except (AttributeError, OSError):  # Windows no tiene os.nice
        pass


def _cerrar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def refrescar_todos(combos: list[tuple[str, str]], workers: int, funcion=refrescar_uno) -> list[tuple]:
    """Resultados en el orden de `combos`. El pool se crea una vez (perezoso) y se reutiliza; si se
    rompe, se descarta y el siguiente ciclo crea uno nuevo. `funcion` debe ser de nivel de módulo."""
    global _pool
    if workers <= 1:
        salida = []
        for s, t in combos:
            try:
                salida.append(funcion(s, t))
            except Exception as e:
                salida.append((s, t, None, f"{type(e).__name__}: {e}"))
        return salida
    if _pool is None:
        # spawn, no fork: el proceso padre tiene hilos (servidor HTTP) y conexiones abiertas
        _pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=_bajar_prioridad)
    futuros = [(s, t, _pool.submit(funcion, s, t)) for s, t in combos]  # la cola respeta el orden de envío
    salida, roto = [], False
    for s, t, f in futuros:
        try:
            salida.append(f.result())
        except BrokenProcessPool as e:
            roto = True
            salida.append((s, t, None, f"BrokenProcessPool: {e}"))
        except Exception as e:
            salida.append((s, t, None, f"{type(e).__name__}: {e}"))
    if roto:
        _cerrar_pool()
    return salida


def ciclo() -> int:
    """Refresca lo pendiente por prioridad, en bloques paralelos pequeños. Devuelve cuántos refrescó."""
    hechos = 0
    for simbolo, temporalidad, status, error in refrescar_todos(pendientes(datetime.now(timezone.utc)), _workers()):
        if status is None:
            logging.error("refresco %s %s falló: %s", simbolo, temporalidad, error)
            continue
        if status != 200:
            logging.warning("refresco %s %s -> %s %s", simbolo, temporalidad, status, error)
        hechos += 1
    return hechos


def _bucle() -> None:
    while True:
        try:
            n = ciclo()
            if n:
                logging.info("refresco: %d snapshots actualizados", n)
        except Exception:
            logging.exception("ciclo de refresco falló")  # BD caída, etc. — reintenta el próximo minuto
        time.sleep(PAUSA_CICLO_S)


def iniciar_en_segundo_plano() -> threading.Thread:
    hilo = threading.Thread(target=_bucle, name="refresco-smc", daemon=True)
    hilo.start()
    return hilo


def demo() -> None:
    u = timezone.utc
    ahora = datetime(2026, 9, 23, 14, 37, tzinfo=u)  # miércoles
    assert inicio_vela(ahora, "Scalping") == datetime(2026, 9, 23, 14, 30, tzinfo=u)
    assert inicio_vela(ahora, "Intraday") == datetime(2026, 9, 23, 14, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (H)") == datetime(2026, 9, 23, 12, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (S)") == datetime(2026, 9, 21, 0, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (M)") == datetime(2026, 9, 1, 0, 0, tzinfo=u)

    # snapshot de antes del cierre de la vela 14:30 → refrescar; de después → no
    assert necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 20, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 33, tzinfo=u), ahora)
    # Swing S/M: una vez al día, pasadas las 03:00 UTC (no esperar al cierre semanal/mensual)
    assert necesita_refresco("EURUSD", "Swing (M)", datetime(2026, 9, 22, 4, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Swing (S)", datetime(2026, 9, 23, 3, 10, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Swing (S)", datetime(2026, 9, 22, 4, tzinfo=u), datetime(2026, 9, 23, 2, 0, tzinfo=u))
    assert necesita_refresco("EURUSD", "Swing (M)", None, ahora)
    # vela de 15m recién cerrada (14:31, dentro del margen de 2 min) → esperar
    assert not necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 10, tzinfo=u), datetime(2026, 9, 23, 14, 31, tzinfo=u))

    sabado = datetime(2026, 9, 26, 12, 5, tzinfo=u)
    assert not mercado_abierto("EURUSD", sabado) and mercado_abierto("BTCUSD", sabado)
    assert not necesita_refresco("EURUSD", "Intraday", datetime(2026, 9, 25, 20, 0, tzinfo=u), sabado)
    assert necesita_refresco("BTCUSD", "Intraday", datetime(2026, 9, 26, 10, 0, tzinfo=u), sabado)
    assert mercado_abierto("EURUSD", datetime(2026, 9, 27, 21, 30, tzinfo=u))  # domingo reapertura

    # snapshot vacío (descarga fallida): se reintenta aun en fin de semana, máximo 1 vez por hora
    assert necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 26, 10, 0, tzinfo=u), sabado, vacio=True)
    assert not necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 26, 11, 30, tzinfo=u), sabado, vacio=True)

    # carga diaria por activo en días hábiles: 96 + 24 + 6 + ~0 = ~126 (antes: 48 × 5 = 240)

    # prioridad: Scalping, Intraday, Swing (H), Swing (S), Swing (M); estable dentro de cada perfil
    combos = [("EURUSD", "Swing (M)"), ("XAUUSD", "Intraday"), ("EURUSD", "Scalping"), ("BTCUSD", "Swing (S)"),
              ("XAUUSD", "Scalping"), ("EURUSD", "Swing (H)")]
    assert ordenar(combos) == [("EURUSD", "Scalping"), ("XAUUSD", "Scalping"), ("XAUUSD", "Intraday"),
                               ("EURUSD", "Swing (H)"), ("BTCUSD", "Swing (S)"), ("EURUSD", "Swing (M)")]

    # workers=1: en serie, en el mismo proceso, sin pool y en orden de prioridad
    vistos = []
    res = refrescar_todos(ordenar(combos), 1, lambda s, t: vistos.append((s, t)) or (s, t, 200, None))
    assert vistos == ordenar(combos) and len(res) == 6 and _pool is None

    # workers=3: pool spawn con prioridad baja; procesa todos y devuelve en el orden enviado
    import time as _t
    seis = [(f"S{n}", "Scalping") for n in range(6)]
    t0 = _t.perf_counter()
    res = refrescar_todos(seis, 3, _worker_prueba)
    assert [(s, t) for s, t, *_ in res] == seis and all(st == 200 for _, _, st, _ in res), res
    assert len({pid for *_, pid in res}) > 1  # corrió en más de un proceso
    t0 = _t.perf_counter()
    refrescar_todos(seis, 3, _worker_prueba)  # pool reutilizado (ya caliente)
    paralelo = _t.perf_counter() - t0
    assert paralelo < 6 * 0.3, paralelo  # en serie serían >= 1.8 s
    assert refrescar_todos([("X", "Scalping")], 3, _worker_error)[0][2] is None  # excepción -> status None
    _cerrar_pool()
    print("refresco.demo() OK")


def _worker_prueba(simbolo: str, temporalidad: str):
    import os as _os
    time.sleep(0.3)
    return simbolo, temporalidad, 200, _os.getpid()


def _worker_error(simbolo: str, temporalidad: str):
    raise RuntimeError("boom")


if __name__ == "__main__":
    demo()
