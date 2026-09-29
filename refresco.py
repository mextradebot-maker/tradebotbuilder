"""Refresco de snapshots SMC dentro del servicio (reemplaza el cron n8n de 180 llamadas HTTP).

Las combinaciones pendientes se procesan por prioridad (vela más corta primero) en un pool pequeño de
hilos (ThreadPoolExecutor): el cálculo cuesta ~1 s de CPU y el resto es esperar la descarga de Dukascopy,
así que los hilos solapan la espera de red sin el RAM de procesos. REFRESCO_HILOS fija el tamaño (por
defecto 6; 1 = en serie, sin pool, para depurar). REFRESCO_WORKERS se lee solo como alias obsoleto.

Una combinación activo×temporalidad solo se refresca cuando cerró una vela nueva
de SU temporalidad (15m, 30m, 1H, 4H) y su snapshot es anterior a ese cierre; las
`diaria` del mapa conectividad.TEMPORALIDADES (Intraday D, Swing S y M) una vez al día a las 08:00 México.
Con el mercado cerrado (fin de semana forex) no se refresca nada salvo cripto. Cada ciclo tiene un tope
de tiempo (PRESUPUESTO_CICLO_S): lo que no alcanza queda para el siguiente ciclo.
"""

import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from conectividad.historico import ALIAS_TEMPORALIDAD, TEMPORALIDADES, resolver_temporalidad

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
HILOS_DEFECTO = 6
_pool: ThreadPoolExecutor | None = None
_pool_hilos = 0


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


# La carga historica del almacen cede el paso mientras haya algo de esto pendiente.
URGENTES = ("Scalping 15m", "Scalping 30m", "Intraday 1H")


def pendientes(ahora: datetime, solo: tuple | None = None, resumen: dict | None = None) -> list[tuple[str, str]]:
    """Combinaciones a refrescar; `solo` limita a esas temporalidades (canonicas). `resumen` =
    persistencia.resumen_snapshots(...): decide con ese dict en vez de leer un snapshot completo por combo."""
    import persistencia

    salida = []
    for c in persistencia.listar_catalogo():
        if not c["activo"]:
            continue
        if solo is not None and ALIAS_TEMPORALIDAD.get(c["temporalidad"], c["temporalidad"]) not in solo:
            continue
        try:  # una temporalidad desconocida en el catálogo no debe abortar el ciclo completo
            canon = resolver_temporalidad(c["temporalidad"])
            # el snapshot se guarda siempre bajo el nombre canónico (una fila vieja con alias,
            # p. ej. re-sembrada por código viejo en el VPS, no debe recalcularse en cada ciclo)
            swing = TEMPORALIDADES[canon]["swing_length"]
            if resumen is not None:
                snap = resumen.get((c["simbolo"], canon))
                vacio = bool(snap) and (not snap["hay_velas"] or snap["motor"] != "v2" or snap["swing_length"] != swing)
            else:
                snap = persistencia.leer_snapshot(c["simbolo"], canon)
                r = (snap or {}).get("respuesta") or {}
                # vacío (descarga fallida) o calculado con otro swing_length (recalibración): rehacer, máx 1/hora
                vacio = bool(snap) and (not r.get("velas") or r.get("motor") != "v2" or r.get("swing_length") != swing)
            if necesita_refresco(c["simbolo"], canon, snap and snap["refrescado_en"], ahora, vacio):
                salida.append((c["simbolo"], canon))
        except ValueError as e:
            logging.warning("refresco: fila de catálogo omitida %s %s: %s", c["simbolo"], c["temporalidad"], e)
    return ordenar(list(dict.fromkeys(salida)))  # alias + canónico del mismo símbolo -> una sola vez


def hay_pendientes_urgentes(ahora: datetime) -> bool:
    """Barato (2 consultas: catalogo + resumen de snapshots 15m/30m/1H): ¿hay algo por refrescar?"""
    import persistencia

    return bool(pendientes(ahora, URGENTES, persistencia.resumen_snapshots(URGENTES)))


def refrescar_uno(simbolo: str, temporalidad: str) -> tuple:
    """(simbolo, temporalidad, status, error); status None si procesar lanzó. procesar abre su propia
    conexión a Postgres por llamada (seguro entre hilos)."""
    import api.setups
    from api.setups import procesar

    if api.setups._persistencia is None:  # import de persistencia falló: no fingir un refresco
        return simbolo, temporalidad, None, "sin persistencia"
    try:
        status, body = procesar({"simbolo": simbolo, "temporalidad": temporalidad, "_force_refresh": True})
        return simbolo, temporalidad, status, body.get("error") if status != 200 else None
    except Exception as e:
        return simbolo, temporalidad, None, f"{type(e).__name__}: {e}"


def _hilos() -> int:
    for var in ("REFRESCO_HILOS", "REFRESCO_WORKERS"):  # WORKERS: alias obsoleto
        if var in os.environ:
            try:
                return max(1, int(os.environ[var]))
            except ValueError:
                break
    return HILOS_DEFECTO


def _cerrar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def _obtener_pool(hilos: int) -> ThreadPoolExecutor:
    """Pool creado una vez (perezoso) y reutilizado; se recrea si cambia el tamaño o se rompió."""
    global _pool, _pool_hilos
    if _pool is not None and (_pool_hilos != hilos or getattr(_pool, "_broken", False) or getattr(_pool, "_shutdown", False)):
        _cerrar_pool()
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="refresco")
        _pool_hilos = hilos
    return _pool


def refrescar_todos(combos: list[tuple[str, str]], hilos: int, funcion=None,
                    limite: float | None = None) -> list[tuple]:
    """Resultados en el orden de `combos` (los enviados). Con hilos > 1 mantiene hasta `hilos` cálculos en
    vuelo y envía el siguiente en cuanto uno termina, en orden de prioridad. `limite` (time.monotonic) se
    revisa ANTES de enviar cada cálculo nuevo; pasado el límite lo que falta se omite (no aparece en el
    resultado). Nunca se interrumpe uno en curso: todo lo enviado se espera y se devuelve."""
    funcion = funcion or refrescar_uno
    vencido = lambda: limite is not None and time.monotonic() >= limite

    def seguro(s, t):
        try:
            return funcion(s, t)
        except Exception as e:
            return s, t, None, f"{type(e).__name__}: {e}"

    if hilos <= 1:
        salida = []
        for s, t in combos:
            if vencido():
                break
            salida.append(seguro(s, t))
        return salida
    pool = _obtener_pool(hilos)
    enviados, vuelo = [], set()
    for s, t in combos:
        while len(vuelo) >= hilos:
            vuelo -= wait(vuelo, return_when=FIRST_COMPLETED).done
        if vencido():
            break
        try:
            f = pool.submit(seguro, s, t)
        except RuntimeError:  # pool cerrado/roto: se recrea al siguiente ciclo
            _cerrar_pool()
            break
        enviados.append(f)
        vuelo.add(f)
    wait(vuelo)
    return [f.result() for f in enviados]


def ciclo(presupuesto: float = PRESUPUESTO_CICLO_S) -> int:
    """Refresca lo pendiente por prioridad hasta agotar el presupuesto. Devuelve cuántos refrescó."""
    hechos = 0
    pend = pendientes(datetime.now(timezone.utc))
    for simbolo, temporalidad, status, error in refrescar_todos(pend, _hilos(), limite=time.monotonic() + presupuesto):
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

    # hilos=1: en serie, sin pool y en orden de prioridad
    vistos = []
    res = refrescar_todos(ordenar(combos), 1, lambda s, t: vistos.append((s, t)) or (s, t, 200, None))
    assert vistos == ordenar(combos) and len(res) == 8 and _pool is None

    # hilos=6: espera de red simulada (sleep); N/6 x espera en vez de N x espera; orden de envío = prioridad
    import threading as _th
    espera = 0.2
    doce = [(f"S{n}", PRIORIDAD[n % len(PRIORIDAD)]) for n in range(12)]
    doce = ordenar(doce)
    orden_envio, hilos_vistos, en_vuelo, pico = [], set(), [0], [0]
    cerrojo = _th.Lock()

    def dormir(s, t):
        with cerrojo:
            orden_envio.append((s, t)); hilos_vistos.add(_th.get_ident())
            en_vuelo[0] += 1; pico[0] = max(pico[0], en_vuelo[0])
        time.sleep(espera)
        with cerrojo:
            en_vuelo[0] -= 1
        return s, t, 200, None

    t0 = time.perf_counter()
    res = refrescar_todos(doce, 6, dormir)
    con_hilos = time.perf_counter() - t0
    assert [(s, t) for s, t, *_ in res] == doce and all(st == 200 for _, _, st, _ in res), res
    assert len(hilos_vistos) > 1 and 1 < pico[0] <= 6, (hilos_vistos, pico)
    assert orden_envio[:6] == doce[:6]  # los 6 primeros en prioridad arrancan primero
    pool1 = _pool
    t0 = time.perf_counter()
    refrescar_todos(doce, 1, dormir)
    en_serie = time.perf_counter() - t0
    assert con_hilos < en_serie / 3, (con_hilos, en_serie)  # ideal 2 esperas vs 12
    assert con_hilos < (12 / 6) * espera + 0.15, con_hilos
    print(f"refresco: 12 items x {espera}s -> 6 hilos {con_hilos:.2f}s vs serie {en_serie:.2f}s (x{en_serie / con_hilos:.1f})")
    refrescar_todos(doce[:2], 6, dormir)
    assert _pool is pool1  # pool reutilizado, no uno por ciclo
    assert refrescar_todos([("X", "Scalping 15m")], 3, _worker_error)[0][2] is None  # excepción -> status None
    _cerrar_pool()
    assert refrescar_todos(doce[:2], 6, dormir) and _pool is not None and _pool is not pool1  # recreado tras cerrar

    # tope de tiempo: en serie no inicia cálculos nuevos pasado el límite y no interrumpe el que corre
    lento = lambda s, t: time.sleep(0.2) or (s, t, 200, None)
    orden = ordenar(combos)
    res = refrescar_todos(orden, 1, lento, limite=time.monotonic() + 0.3)
    assert [(s, t) for s, t, *_ in res] == orden[:2], res  # 0.2 s ok, 0.4 s > tope: el 3.º ya no inicia
    assert len(refrescar_todos(orden, 1, lento, limite=time.monotonic() - 1)) == 0  # ya vencido: nada
    # hilos: el límite se revisa antes de CADA envío; lo enviado se espera completo y se devuelve
    orden_envio.clear()
    res = refrescar_todos(doce, 2, lambda s, t: dormir(s, t), limite=time.monotonic() + 0.3)
    assert 2 <= len(res) < len(doce) and [(s, t) for s, t, *_ in res] == doce[:len(res)] == orden_envio, res
    assert all(st == 200 for _, _, st, _ in res)  # los ya enviados terminaron (no se interrumpen)
    assert refrescar_todos(doce, 6, dormir, limite=time.monotonic() - 1) == []  # vencido: nada
    # ciclo(): pendientes por prioridad y presupuesto pequeño con refrescar_uno sustituido
    g = globals()
    orig = {n: g[n] for n in ("pendientes", "refrescar_uno", "_hilos")}
    llamadas = []
    try:
        g["pendientes"] = lambda ahora: orden
        g["refrescar_uno"] = lambda s, t: llamadas.append((s, t)) or time.sleep(0.2) or (s, t, 200, None)
        g["_hilos"] = lambda: 1
        assert ciclo(presupuesto=0.3) == 2 and llamadas == orden[:2], llamadas
        llamadas.clear()
        assert ciclo() == len(orden) and llamadas == orden
    finally:
        g.update(orig)
    _cerrar_pool()
    e0 = dict(os.environ)
    try:  # env: HILOS manda; WORKERS es alias obsoleto; por defecto 6
        for v in ("REFRESCO_HILOS", "REFRESCO_WORKERS"):
            os.environ.pop(v, None)
        assert _hilos() == 6
        os.environ["REFRESCO_WORKERS"] = "2"
        assert _hilos() == 2
        os.environ["REFRESCO_HILOS"] = "4"
        assert _hilos() == 4
    finally:
        os.environ.clear(); os.environ.update(e0)

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
    # hay_pendientes_urgentes: solo mira 15m/30m/1H (un 4H/D pendiente no frena la carga historica)
    snaps[("EURUSD", "Scalping 15m")]["refrescado_en"] = datetime(2026, 9, 23, 14, 33, tzinfo=u)  # fresco
    def _resumen(tems):  # como persistencia.resumen_snapshots, a partir de los snaps falsos
        return {k: {"refrescado_en": v["refrescado_en"], "hay_velas": bool(v["respuesta"].get("velas")),
                    "motor": v["respuesta"].get("motor"), "swing_length": v["respuesta"].get("swing_length")}
                for k, v in snaps.items() if k[1] in tems}
    falso.resumen_snapshots = _resumen
    falso.leer_snapshot = lambda s, t: (_ for _ in ()).throw(AssertionError("no debe leer snapshots completos"))
    falso.listar_catalogo = lambda: [{"simbolo": "EURUSD", "temporalidad": "Scalping 15m", "activo": True},
                                     {"simbolo": "EURUSD", "temporalidad": "Swing (M)", "activo": True},
                                     {"simbolo": "EURUSD", "temporalidad": "Intraday 4H", "activo": True},
                                     {"simbolo": "XAUUSD", "temporalidad": "Scalping", "activo": False}]
    sys.modules["persistencia"] = falso
    try:
        assert len(pendientes(ahora2, None, _resumen(("Scalping 15m",))) ) == 2 and not hay_pendientes_urgentes(ahora2)  # M y 4H pendientes: no urgen
        snaps[("EURUSD", "Scalping 15m")]["refrescado_en"] = viejo
        assert hay_pendientes_urgentes(ahora2)
        snaps[("EURUSD", "Scalping 15m")]["respuesta"]["velas"] = []  # snapshot vacio: pendiente (max 1/hora)
        snaps[("EURUSD", "Scalping 15m")]["refrescado_en"] = datetime(2026, 9, 23, 14, 33, tzinfo=u)
        assert not hay_pendientes_urgentes(ahora2) and hay_pendientes_urgentes(datetime(2026, 9, 23, 15, 40, tzinfo=u))
        snaps[("EURUSD", "Scalping 15m")]["respuesta"]["velas"] = [1]
        snaps[("EURUSD", "Scalping 15m")]["refrescado_en"] = viejo
        assert not hay_pendientes_urgentes(datetime(2026, 9, 26, 12, 5, tzinfo=u))  # sabado: nada (no cripto)
    finally:
        if previo is None:
            del sys.modules["persistencia"]
        else:
            sys.modules["persistencia"] = previo
    print("refresco.demo() OK")


def _worker_error(simbolo: str, temporalidad: str):
    raise RuntimeError("boom")


if __name__ == "__main__":
    demo()
