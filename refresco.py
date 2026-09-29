"""Refresco de snapshots SMC dentro del servicio (reemplaza el cron n8n de 180 llamadas HTTP).

Las combinaciones pendientes se procesan por prioridad (vela más corta primero) en un pool pequeño de
procesos `spawn` con prioridad baja (os.nice), para no ahogar al servidor HTTP en el VPS compartido.
REFRESCO_WORKERS fija el tamaño (1 = en serie, en el mismo proceso, para depurar).

Una combinación activo×temporalidad solo se refresca cuando cerró una vela nueva
de SU temporalidad (15m, 30m, 1H, 4H) y su snapshot es anterior a ese cierre; las
`diaria` del mapa conectividad.TEMPORALIDADES (Intraday D, Swing S y M) una vez al día a las 08:00 México.
Con el mercado cerrado (fin de semana forex) no se refresca nada salvo cripto. Cada ciclo tiene un tope
de tiempo (PRESUPUESTO_CICLO_S): lo que no alcanza queda para el siguiente ciclo.
"""

import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timedelta, timezone

from conectividad.historico import TEMPORALIDADES, resolver_temporalidad

CRIPTO = {"BTCUSD", "ETHUSD", "XRPUSD"}
MARGEN_DATOS = timedelta(minutes=2)
REINTENTO_VACIO = timedelta(hours=1)  # el proveedor publica la vela cerrada con un poco de retraso
PAUSA_CICLO_S = 60
# Las temporalidades `diaria` (Intraday D, Swing S/M) se recalculan una vez al día, no al cerrar su
# vela, para detectar un cambio de tendencia a tiempo: 14:00 UTC = 08:00 México (México no tiene
# horario de verano desde 2022, así que la hora UTC es fija), con la vela diaria ya publicada.
HORA_REFRESCO_DIARIO = 14
# Tope de tiempo por ciclo: no se inicia un cálculo nuevo pasado este presupuesto (nunca se interrumpe
# uno en curso); lo pendiente queda para el siguiente ciclo, donde lo de mayor prioridad va primero.
PRESUPUESTO_CICLO_S = 300
PRIORIDAD = tuple(TEMPORALIDADES)  # orden del mapa = vela más corta primero (caduca antes)
_pool: ProcessPoolExecutor | None = None


def inicio_vela(ahora: datetime, temporalidad: str) -> datetime:
    """Inicio de la vela en curso; la vela anterior cerró justo en este instante."""
    vela = TEMPORALIDADES[resolver_temporalidad(temporalidad)]["vela"]
    t = ahora.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if vela == "15m":
        return t.replace(minute=t.minute - t.minute % 15)
    if vela == "30m":
        return t.replace(minute=t.minute - t.minute % 30)
    if vela == "1H":
        return t.replace(minute=0)
    if vela == "4H":
        return t.replace(minute=0, hour=t.hour - t.hour % 4)
    dia = t.replace(minute=0, hour=0)
    if vela == "D":
        return dia
    if vela == "S":
        return dia - timedelta(days=dia.weekday())  # lunes 00:00 UTC
    return dia.replace(day=1)  # "M"


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
    if TEMPORALIDADES[resolver_temporalidad(temporalidad)]["diaria"]:  # la vela D/S/M en curso cambia cada día
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

    salida = []
    for c in persistencia.listar_catalogo():
        if not c["activo"]:
            continue
        try:  # una temporalidad desconocida en el catálogo no debe abortar el ciclo completo
            canon = resolver_temporalidad(c["temporalidad"])
            # el snapshot se guarda siempre bajo el nombre canónico (una fila vieja con alias,
            # p. ej. re-sembrada por código viejo en el VPS, no debe recalcularse en cada ciclo)
            snap = persistencia.leer_snapshot(c["simbolo"], canon)
            r = (snap or {}).get("respuesta") or {}
            # vacío (descarga fallida) o calculado con otro swing_length (recalibración): rehacer, máx 1/hora
            vacio = bool(snap) and (not r.get("velas") or r.get("motor") != "v2" or r.get("swing_length") != TEMPORALIDADES[canon]["swing_length"])
            if necesita_refresco(c["simbolo"], canon, snap and snap["refrescado_en"], ahora, vacio):
                salida.append((c["simbolo"], canon))
        except ValueError as e:
            logging.warning("refresco: fila de catálogo omitida %s %s: %s", c["simbolo"], c["temporalidad"], e)
    return ordenar(list(dict.fromkeys(salida)))  # alias + canónico del mismo símbolo -> una sola vez


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


def refrescar_todos(combos: list[tuple[str, str]], workers: int, funcion=None,
                    limite: float | None = None) -> list[tuple]:
    """Resultados en el orden de `combos`. El pool se crea una vez (perezoso) y se reutiliza; si se
    rompe, se descarta y el siguiente ciclo crea uno nuevo. `funcion` debe ser de nivel de módulo.
    `limite` (time.monotonic) es el tope del ciclo: se revisa ANTES de iniciar cada cálculo (en serie) o
    cada tanda de `workers` (pool); pasado el límite se deja de iniciar trabajo y lo que falta se omite
    (no aparece en el resultado). Nunca se interrumpe un cálculo en curso ni se abandonan futuros ya
    enviados: cada tanda se espera completa, así que todo lo enviado se devuelve (y se registra)."""
    global _pool
    funcion = funcion or refrescar_uno
    vencido = lambda: limite is not None and time.monotonic() >= limite
    if workers <= 1:
        salida = []
        for s, t in combos:
            if vencido():
                break
            try:
                salida.append(funcion(s, t))
            except Exception as e:
                salida.append((s, t, None, f"{type(e).__name__}: {e}"))
        return salida
    if _pool is None:
        # spawn, no fork: el proceso padre tiene hilos (servidor HTTP) y conexiones abiertas
        _pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=_bajar_prioridad)
    salida, roto = [], False
    for i in range(0, len(combos), workers):
        if vencido():
            break
        futuros = [(s, t, _pool.submit(funcion, s, t)) for s, t in combos[i:i + workers]]
        for s, t, f in futuros:
            try:
                salida.append(f.result())
            except BrokenProcessPool as e:
                roto = True
                salida.append((s, t, None, f"BrokenProcessPool: {e}"))
            except Exception as e:
                salida.append((s, t, None, f"{type(e).__name__}: {e}"))
        if roto:
            break
    if roto:
        _cerrar_pool()
    return salida


def ciclo(presupuesto: float = PRESUPUESTO_CICLO_S) -> int:
    """Refresca lo pendiente por prioridad hasta agotar el presupuesto. Devuelve cuántos refrescó."""
    hechos = 0
    pend = pendientes(datetime.now(timezone.utc))
    for simbolo, temporalidad, status, error in refrescar_todos(pend, _workers(), limite=time.monotonic() + presupuesto):
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
    esperado = {"Scalping 15m": (14, 30), "Scalping 30m": (14, 30), "Intraday 1H": (14, 0), "Intraday 4H": (12, 0),
                "Intraday D": (0, 0)}
    for n, (h, m) in esperado.items():
        assert inicio_vela(ahora, n) == datetime(2026, 9, 23, h, m, tzinfo=u), n
    assert inicio_vela(datetime(2026, 9, 23, 14, 44, tzinfo=u), "Scalping 15m") == datetime(2026, 9, 23, 14, 30, tzinfo=u)
    assert inicio_vela(datetime(2026, 9, 23, 14, 44, tzinfo=u), "Scalping 30m") == datetime(2026, 9, 23, 14, 30, tzinfo=u)
    assert inicio_vela(datetime(2026, 9, 23, 15, 5, tzinfo=u), "Scalping 30m") == datetime(2026, 9, 23, 15, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (S)") == datetime(2026, 9, 21, 0, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (M)") == datetime(2026, 9, 1, 0, 0, tzinfo=u)
    assert inicio_vela(ahora, "Scalping") == inicio_vela(ahora, "Scalping 15m")  # alias aceptado
    assert PRIORIDAD == ("Scalping 15m", "Scalping 30m", "Intraday 1H", "Intraday 4H", "Intraday D", "Swing (S)", "Swing (M)")

    # snapshot de antes del cierre de la vela 14:30 → refrescar; de después → no
    assert necesita_refresco("EURUSD", "Scalping 15m", datetime(2026, 9, 23, 14, 20, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Scalping 15m", datetime(2026, 9, 23, 14, 33, tzinfo=u), ahora)
    # Intraday D / Swing S/M: una vez al día, pasadas las 14:00 UTC (08:00 México)
    assert necesita_refresco("EURUSD", "Swing (M)", datetime(2026, 9, 22, 15, tzinfo=u), ahora)
    assert necesita_refresco("EURUSD", "Intraday D", datetime(2026, 9, 22, 15, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Intraday D", datetime(2026, 9, 23, 14, 10, tzinfo=u), ahora)
    antes, despues = datetime(2026, 9, 23, 13, 59, tzinfo=u), datetime(2026, 9, 23, 14, 3, tzinfo=u)
    ayer = datetime(2026, 9, 22, 14, 10, tzinfo=u)  # refrescado ayer tras las 14:00
    assert not necesita_refresco("EURUSD", "Swing (S)", ayer, antes)
    assert necesita_refresco("EURUSD", "Swing (S)", ayer, despues)
    assert not necesita_refresco("EURUSD", "Swing (S)", datetime(2026, 9, 23, 14, 10, tzinfo=u), datetime(2026, 9, 23, 15, tzinfo=u))
    assert necesita_refresco("EURUSD", "Swing (M)", None, ahora)
    # vela de 15m recién cerrada (14:31, dentro del margen de 2 min) → esperar
    assert not necesita_refresco("EURUSD", "Scalping 15m", datetime(2026, 9, 23, 14, 10, tzinfo=u), datetime(2026, 9, 23, 14, 31, tzinfo=u))

    sabado = datetime(2026, 9, 26, 12, 5, tzinfo=u)
    assert not mercado_abierto("EURUSD", sabado) and mercado_abierto("BTCUSD", sabado)
    assert not necesita_refresco("EURUSD", "Intraday 1H", datetime(2026, 9, 25, 20, 0, tzinfo=u), sabado)
    assert necesita_refresco("BTCUSD", "Intraday 1H", datetime(2026, 9, 26, 10, 0, tzinfo=u), sabado)
    assert mercado_abierto("EURUSD", datetime(2026, 9, 27, 21, 30, tzinfo=u))  # domingo reapertura

    # snapshot vacío (descarga fallida): se reintenta aun en fin de semana, máximo 1 vez por hora
    assert necesita_refresco("EURUSD", "Scalping 15m", datetime(2026, 9, 26, 10, 0, tzinfo=u), sabado, vacio=True)
    assert not necesita_refresco("EURUSD", "Scalping 15m", datetime(2026, 9, 26, 11, 30, tzinfo=u), sabado, vacio=True)

    # carga diaria por activo en días hábiles: 96 + 24 + 6 + ~0 = ~126 (antes: 48 × 5 = 240)

    # prioridad: Scalping, Intraday, Swing (H), Swing (S), Swing (M); estable dentro de cada perfil
    combos = [("EURUSD", "Swing (M)"), ("XAUUSD", "Intraday 1H"), ("EURUSD", "Scalping 15m"), ("BTCUSD", "Swing (S)"),
              ("XAUUSD", "Scalping 15m"), ("EURUSD", "Intraday 4H"), ("EURUSD", "Intraday D"), ("EURUSD", "Scalping 30m")]
    assert ordenar(combos) == [("EURUSD", "Scalping 15m"), ("XAUUSD", "Scalping 15m"), ("EURUSD", "Scalping 30m"),
                               ("XAUUSD", "Intraday 1H"), ("EURUSD", "Intraday 4H"), ("EURUSD", "Intraday D"),
                               ("BTCUSD", "Swing (S)"), ("EURUSD", "Swing (M)")]

    # workers=1: en serie, en el mismo proceso, sin pool y en orden de prioridad
    vistos = []
    res = refrescar_todos(ordenar(combos), 1, lambda s, t: vistos.append((s, t)) or (s, t, 200, None))
    assert vistos == ordenar(combos) and len(res) == 8 and _pool is None

    # workers=3: pool spawn con prioridad baja; procesa todos y devuelve en el orden enviado
    import time as _t
    seis = [(f"S{n}", "Scalping 15m") for n in range(6)]
    t0 = _t.perf_counter()
    res = refrescar_todos(seis, 3, _worker_prueba)
    assert [(s, t) for s, t, *_ in res] == seis and all(st == 200 for _, _, st, _ in res), res
    assert len({pid for *_, pid in res}) > 1  # corrió en más de un proceso
    t0 = _t.perf_counter()
    refrescar_todos(seis, 3, _worker_prueba)  # pool reutilizado (ya caliente)
    paralelo = _t.perf_counter() - t0
    assert paralelo < 6 * 0.3, paralelo  # en serie serían >= 1.8 s
    assert refrescar_todos([("X", "Scalping 15m")], 3, _worker_error)[0][2] is None  # excepción -> status None

    # tope de tiempo: en serie no inicia cálculos nuevos pasado el límite y no interrumpe el que corre
    lento = lambda s, t: time.sleep(0.2) or (s, t, 200, None)
    orden = ordenar(combos)
    res = refrescar_todos(orden, 1, lento, limite=time.monotonic() + 0.3)
    assert [(s, t) for s, t, *_ in res] == orden[:2], res  # 0.2 s ok, 0.4 s > tope: el 3.º ya no inicia
    assert len(refrescar_todos(orden, 1, lento, limite=time.monotonic() - 1)) == 0  # ya vencido: nada
    # pool: por tandas de `workers`, todo lo enviado se espera y se devuelve, prioridad respetada
    res = refrescar_todos(seis, 2, _worker_prueba, limite=time.monotonic() + 0.4)
    assert [(s, t) for s, t, *_ in res] == seis[:4], res  # tandas 1 (t=0) y 2 (t=0.3) inician; la 3 (t=0.6) no
    # ciclo(): pendientes por prioridad y presupuesto pequeño con refrescar_uno sustituido
    g = globals()
    orig = {n: g[n] for n in ("pendientes", "refrescar_uno", "_workers")}
    llamadas = []
    try:
        g["pendientes"] = lambda ahora: orden
        g["refrescar_uno"] = lambda s, t: llamadas.append((s, t)) or time.sleep(0.2) or (s, t, 200, None)
        g["_workers"] = lambda: 1
        assert ciclo(presupuesto=0.3) == 2 and llamadas == orden[:2], llamadas
        llamadas.clear()
        assert ciclo() == len(orden) and llamadas == orden
    finally:
        g.update(orig)
    _cerrar_pool()

    # pendientes(): temporalidad desconocida se omite sin abortar; alias no es "vacío" para siempre
    import sys, types
    ahora2 = datetime(2026, 9, 23, 14, 37, tzinfo=u)
    viejo = datetime(2026, 9, 23, 10, 0, tzinfo=u)
    snaps = {("EURUSD", "Scalping 15m"): {"refrescado_en": viejo, "respuesta": {"velas": [1], "motor": "v2", "swing_length": 8}},
             ("XAUUSD", "Scalping 15m"): {"refrescado_en": datetime(2026, 9, 23, 14, 33, tzinfo=u),
                                          "respuesta": {"velas": [1], "motor": "v2", "swing_length": 8}}}
    falso = types.SimpleNamespace(
        listar_catalogo=lambda: [{"simbolo": "EURUSD", "temporalidad": "Scalping 15m", "activo": True},
                                 {"simbolo": "EURUSD", "temporalidad": "Bogus 7m", "activo": True},
                                 {"simbolo": "XAUUSD", "temporalidad": "Scalping", "activo": True},
                                 {"simbolo": "EURUSD", "temporalidad": "Scalping", "activo": True}],
        leer_snapshot=lambda s, t: snaps.get((s, t)))
    previo = sys.modules.get("persistencia")
    sys.modules["persistencia"] = falso
    try:
        # Bogus omitida; EURUSD pendiente una sola vez aunque el catálogo tenga también su alias;
        # XAUUSD alias lee el snapshot canónico (fresco) -> no se repite
        assert pendientes(ahora2) == [("EURUSD", "Scalping 15m")], pendientes(ahora2)
    finally:
        if previo is None:
            del sys.modules["persistencia"]
        else:
            sys.modules["persistencia"] = previo
    print("refresco.demo() OK")


def _worker_prueba(simbolo: str, temporalidad: str):
    import os as _os
    time.sleep(0.3)
    return simbolo, temporalidad, 200, _os.getpid()


def _worker_error(simbolo: str, temporalidad: str):
    raise RuntimeError("boom")


if __name__ == "__main__":
    demo()
