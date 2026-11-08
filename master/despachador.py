"""Despachador del Master Trader: recibe oportunidades (setups confirmados del motor SMC), busca los robots VIVOS y
ACTIVADOS de ese activo y temporalidad, y les entrega una orden cuando el robot pregunta (pulso cada pocos segundos).

El robot MQL5 no puede recibir mensajes: pregunta. En cada pulso manda su estado (balance, equity, resultado del dia,
racha, specs del simbolo) y las confirmaciones de lo que hizo con la orden anterior; el servidor responde con a lo mas
UNA orden (ya con lotes y pasada por la compuerta) o ninguna. Toda orden lleva un orden_id: entregarla dos veces no la
duplica (el robot la ignora si ya la tiene) y una orden no confirmada se reentrega hasta que expire.

Persistencia detras de un `repo` (RepoPg en produccion, MemRepo en las pruebas): la logica no toca SQL.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from master import riesgo as compuerta

TTL_ORDEN_S = 300   # una orden que el robot no recoge en 5 min expira (el setup ya no es de ahora)
VIVO_S = 30         # un robot que no pregunta en 30 s no cuenta como vivo
PCT_MAX = 2.0       # mismo tope que conectividad.riesgo.PCT_MAX_SL


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def clave_oportunidad(s: dict) -> str:
    return f"{s['direccion']}|{float(s['entrada']):.8g}|{float(s['stop']):.8g}"


MAX_EDAD_VELAS = 20      # misma caducidad que la orden pendiente del EA (InpVelasExpiracion)
MAX_DISTANCIA_PCT = 10.0  # ponytail: tope unico; si un swing legitimo queda mas lejos, subirlo por temporalidad


def vigentes(confirmados: list[dict], velas, ref_cierre) -> list[dict]:
    """Descarta del camino automatico los setups que ya no sirven: confirmados hace mas de MAX_EDAD_VELAS velas o con la
    entrada a mas de MAX_DISTANCIA_PCT % del precio actual (p. ej. una zona de hace anos que el motor sigue viendo 'sin llenar').
    Sin velas / ref_cierre (API vieja) no filtra por ese dato."""
    salida = []
    for s in confirmados:
        if velas and s.get("indice_conocido") is not None and velas - 1 - int(s["indice_conocido"]) > MAX_EDAD_VELAS:
            continue
        if ref_cierre and s.get("entrada") and abs(float(s["entrada"]) - float(ref_cierre)) / float(ref_cierre) * 100 > MAX_DISTANCIA_PCT:
            continue
        salida.append(s)
    return salida


def recibir_oportunidades(repo, simbolo: str, temporalidad: str, confirmados: list[dict],
                          ref_ts=None, ref_cierre=None, ahora: datetime | None = None) -> dict:
    """Crea una orden pendiente por cada robot activado de ese activo/temporalidad para el ULTIMO setup confirmado
    (el mismo que toma el EA hoy). Repetir la misma oportunidad no crea nada nuevo."""
    ahora = ahora or _ahora()
    if not confirmados:
        return {"ordenes": 0, "robots": 0}
    s = confirmados[-1]
    if s.get("direccion") not in ("long", "short") or not s.get("entrada") or not s.get("stop"):
        return {"ordenes": 0, "robots": 0, "error": "setup incompleto"}
    clave = clave_oportunidad(s)
    robots = repo.robots_activados(simbolo, temporalidad)
    nuevas = 0
    for r in robots:
        if repo.crear_orden({
                "orden_id": "M" + uuid.uuid4().hex[:14], "licencia_id": r["licencia_id"], "cuenta": r.get("cuenta"),
                "simbolo": simbolo, "temporalidad": temporalidad, "direccion": s["direccion"],
                "entrada": float(s["entrada"]), "stop": float(s["stop"]), "tp": float(s["tp"]) if s.get("tp") else None,
                "clave": clave, "ref_ts": ref_ts, "ref_cierre": ref_cierre,
                "expira_en": ahora + timedelta(seconds=TTL_ORDEN_S)}):
            nuevas += 1
    return {"ordenes": nuevas, "robots": len(robots)}


def recibir_tendencia_swing_express(repo, simbolo: str, temporalidad: str, direccion: str,
                                     ref_cierre: float | None, ahora: datetime | None = None) -> dict:
    """Swing Express no usa setups del motor SMC: abre/cierra segun la tendencia que manda refresco.py.
    'compra' abre (si no hay ya posicion o algo en vuelo); 'venta' cierra (si hay posicion); cualquier otro valor no hace nada."""
    ahora = ahora or _ahora()
    if ref_cierre is None:
        return {"aperturas": 0, "cierres": 0, "robots": 0}
    robots = repo.robots_swing_express(simbolo, temporalidad)
    aperturas = cierres = 0
    for r in robots:
        if direccion == "compra":
            if r["posicion_abierta"] or repo.orden_para_entregar(r["licencia_id"], simbolo, temporalidad, ahora) is not None:
                continue
            if repo.crear_orden({
                    "orden_id": "M" + uuid.uuid4().hex[:14], "licencia_id": r["licencia_id"], "cuenta": r.get("cuenta"),
                    "simbolo": simbolo, "temporalidad": temporalidad, "direccion": "long",
                    "entrada": float(ref_cierre), "stop": float(ref_cierre), "tp": None, "estrategia": "swing_express",
                    "clave": f"swing_express|{int(ahora.timestamp())}", "ref_ts": None, "ref_cierre": ref_cierre,
                    "expira_en": ahora + timedelta(seconds=TTL_ORDEN_S)}):
                aperturas += 1
        elif direccion == "venta":
            if not r["posicion_abierta"]:
                continue
            if ordenar_cierre(repo, r["licencia_id"], simbolo, temporalidad,
                               "Swing Express: tendencia cambió a venta", ahora) is not None:
                cierres += 1
        # cualquier otro valor de direccion ("sin_definir", etc.): no-op, el robot se cuenta pero no se toca
    return {"aperturas": aperturas, "cierres": cierres, "robots": len(robots)}


TF_VELA_MEDIANOCHE = "Diario"
VELA_MEDIANOCHE_STOP_PCT = 0.98   # ponytail: 2% bajo el PDH; ajustar si Task 1 define un stop por estructura real
VELA_MEDIANOCHE_CIERRE_HORAS = 7


def recibir_candidato_vela_medianoche(repo, simbolo: str, pdh: float, dia_anterior: date,
                                       ahora: datetime | None = None) -> dict:
    """Vela de Medianoche: una orden larga diaria por robot activado, entrada en el PDH (maximo del dia anterior) con
    stop al 2 % debajo. El cierre no es por tendencia (eso es Swing Express) sino por tiempo, ver pulso(). Repetir el
    mismo dia_anterior no crea una segunda orden (clave = vela_medianoche|dia_anterior)."""
    ahora = ahora or _ahora()
    robots = repo.robots_vela_medianoche(simbolo, TF_VELA_MEDIANOCHE)
    clave = f"vela_medianoche|{dia_anterior}"
    aperturas = 0
    for r in robots:
        if r["posicion_abierta"] or repo.orden_para_entregar(r["licencia_id"], simbolo, TF_VELA_MEDIANOCHE, ahora) is not None:
            continue
        if repo.crear_orden({
                "orden_id": "M" + uuid.uuid4().hex[:14], "licencia_id": r["licencia_id"], "cuenta": r.get("cuenta"),
                "simbolo": simbolo, "temporalidad": TF_VELA_MEDIANOCHE, "direccion": "long",
                "entrada": float(pdh), "stop": float(pdh) * VELA_MEDIANOCHE_STOP_PCT, "tp": None,
                "estrategia": "vela_medianoche", "clave": clave, "ref_ts": None, "ref_cierre": pdh,
                "expira_en": ahora + timedelta(seconds=TTL_ORDEN_S)}):
            aperturas += 1
    return {"aperturas": aperturas, "robots": len(robots)}


def _lotes(est: dict, orden: dict, riesgo_pct: float) -> tuple[float, str]:
    from conectividad.riesgo import dimensionar, tope_riesgo
    costo = abs(orden["entrada"] - orden["stop"]) / float(est["tick_size"]) * float(est["tick_value"])
    # adaptable al capital: si el lote minimo no cabe en el riesgo pedido, sube hasta el tope de la regla (2 % con SL, 3 % swing)
    lot = dimensionar(float(est["balance"]), riesgo_pct / 100.0, costo,
                      float(est["vol_min"]), float(est["vol_max"]), float(est["vol_step"]),
                      pct_tope=tope_riesgo(orden["temporalidad"]))
    return (lot.lotes, "") if lot.viable else (0.0, lot.motivo or "capital insuficiente para el lote minimo")


def _publica(o: dict) -> dict:
    return {k: o.get(k) for k in ("orden_id", "direccion", "entrada", "stop", "tp", "lotes", "ref_ts", "ref_cierre", "estrategia")}


def ordenar_cierre(repo, licencia_id: int, simbolo: str, temporalidad: str, motivo: str,
                   ahora: datetime | None = None) -> dict | None:
    """Pide al robot que cierre todo lo suyo (posiciones y ordenes pendientes) en su proximo pulso.
    None si ya hay un cierre pendiente. Salir siempre debe ser posible: no pasa por la compuerta."""
    ahora = ahora or _ahora()
    c = {"cierre_id": "C" + uuid.uuid4().hex[:14], "licencia_id": licencia_id, "simbolo": simbolo,
         "temporalidad": temporalidad, "motivo": motivo[:200], "expira_en": ahora + timedelta(seconds=TTL_ORDEN_S)}
    return c if repo.crear_cierre(c) else None


def pulso(repo, licencia_id: int, cuenta: int, simbolo: str, temporalidad: str, est: dict,
          acks: list[dict] | None = None, habilitado: bool = True, ahora: datetime | None = None,
          cfg: dict | None = None, cierre_auto: bool = False) -> dict:
    """Un pulso del robot. -> {"orden": {...}|None, "cierre": {...}|None, "estado": "...", "motivos": [...]}

    cierre_auto: con un freno duro activo en la cuenta (drawdown o racha), el Master pide cerrar lo abierto."""
    ahora = ahora or _ahora()
    repo.upsert_robot(licencia_id, simbolo, temporalidad, cuenta, est, ahora)
    for a in acks or []:
        repo.aplicar_ack(licencia_id, a)
    repo.expirar(ahora)
    robot = repo.robot(licencia_id, simbolo, temporalidad)
    if not habilitado or not robot or not robot["activo"]:
        return {"orden": None, "cierre": None, "estado": "sin_activar" if habilitado else "master_apagado", "motivos": []}

    # Swing Express: si el EA ya no reporta ninguna posicion (cierre forzado -3%, cierre manual, orden pendiente
    # vencida) pero el repo la seguia marcando abierta, se corrige aqui -- sin esto el robot nunca volveria a
    # entrar aunque la tendencia siga alcista (recibir_tendencia_swing_express no abre con posicion_abierta=True).
    if robot.get("estrategia") in ("swing_express", "vela_medianoche") and robot.get("posicion_abierta") \
            and int(est.get("posiciones") or 0) == 0:
        repo.reconciliar_posicion(licencia_id, simbolo, temporalidad)

    c = repo.cuenta(cuenta) or {"pico_equity": 0.0, "halt": None}
    pico = max(float(c.get("pico_equity") or 0), float(est.get("equity") or 0))
    est = {**est, "pico_equity": pico, "halt": c.get("halt")}

    # cierre ordenado: tiene prioridad sobre cualquier orden nueva (los cierres nunca se bloquean)
    cierre = repo.cierre_pendiente(licencia_id, simbolo, temporalidad, ahora)
    if cierre is None and cierre_auto and c.get("halt") and (int(est.get("posiciones") or 0) > 0 or est.get("pendientes")):
        cierre = ordenar_cierre(repo, licencia_id, simbolo, temporalidad, "FRENO de la cuenta: " + str(c["halt"]), ahora)
    if cierre is None and cierre_auto and robot.get("estrategia") == "vela_medianoche" and robot.get("posicion_abierta_en") \
            and ahora - robot["posicion_abierta_en"] >= timedelta(hours=VELA_MEDIANOCHE_CIERRE_HORAS):
        cierre = ordenar_cierre(repo, licencia_id, simbolo, temporalidad,
                                "Vela de Medianoche: 7 horas desde la entrada", ahora)
    if cierre:
        repo.guardar_cuenta(cuenta, pico, c.get("halt"))
        return {"orden": None, "cierre": {"cierre_id": cierre["cierre_id"], "motivo": cierre["motivo"]},
                "estado": "cierre", "motivos": []}

    orden = repo.orden_para_entregar(licencia_id, simbolo, temporalidad, ahora)
    if orden is None:
        repo.guardar_cuenta(cuenta, pico, c.get("halt"))
        return {"orden": None, "cierre": None, "estado": "sin_orden", "motivos": []}

    if orden["estado"] == "entregada":   # ya calculada y entregada: se reentrega igual hasta que el robot confirme
        repo.guardar_cuenta(cuenta, pico, c.get("halt"))
        return {"orden": _publica(orden), "cierre": None, "estado": "reentrega", "motivos": []}

    base = float(est.get("riesgo_pct") or PCT_MAX)
    es_swing_express = orden.get("estrategia") == "swing_express"
    riesgo_pct_compuerta = 0.0 if es_swing_express else min(base, PCT_MAX)
    rr = None
    if orden.get("tp") and orden["entrada"] != orden["stop"]:
        rr = abs(orden["tp"] - orden["entrada"]) / abs(orden["entrada"] - orden["stop"])
    v = compuerta.evaluar(est, riesgo_pct_compuerta, rr, cfg)
    repo.guardar_cuenta(cuenta, pico, v["halt"] or c.get("halt"))
    if not v["ok"]:
        repo.marcar(orden["orden_id"], "bloqueada", "; ".join(v["motivos"]))
        return {"orden": None, "cierre": None, "estado": "bloqueada", "motivos": v["motivos"]}
    # ponytail: lote fijo para swing_express (sin SL real que dimensionar); _lotes() para el resto
    lotes, motivo = (0.01, "") if es_swing_express else _lotes(est, orden, min(base * v["factor"], PCT_MAX))
    if lotes <= 0:
        repo.marcar(orden["orden_id"], "sin_lotes", motivo)
        return {"orden": None, "cierre": None, "estado": "sin_lotes", "motivos": [motivo]}
    orden = repo.marcar(orden["orden_id"], "entregada", None, lotes, ahora)
    return {"orden": _publica(orden), "cierre": None, "estado": "entregada", "motivos": []}


# --------------------------------------------------------------------------- repos
class MemRepo:
    """Repo en memoria: pruebas y demo."""

    def __init__(self, activados=()):
        self.robots, self.ordenes, self.cuentas, self.cierres, self.historial = {}, {}, {}, {}, []
        for lic, sim, tf, cuenta in activados:
            self.robots[(lic, sim, tf)] = {"licencia_id": lic, "cuenta": cuenta, "activo": True, "visto_en": None}

    def robots_activados(self, simbolo, tf):
        return [r for (_, s, t), r in self.robots.items()
                if s == simbolo and t == tf and r["activo"]
                and r.get("estrategia", "estructural") not in ("swing_express", "vela_medianoche")]

    def robots_swing_express(self, simbolo, tf):
        return [{"licencia_id": r["licencia_id"], "cuenta": r["cuenta"], "posicion_abierta": r.get("posicion_abierta", False)}
                for (_, s, t), r in self.robots.items()
                if s == simbolo and t == tf and r["activo"] and r.get("estrategia", "estructural") == "swing_express"]

    def robots_vela_medianoche(self, simbolo, tf):
        return [{"licencia_id": r["licencia_id"], "cuenta": r["cuenta"], "posicion_abierta": r.get("posicion_abierta", False),
                 "posicion_abierta_en": r.get("posicion_abierta_en")}
                for (_, s, t), r in self.robots.items()
                if s == simbolo and t == tf and r["activo"] and r.get("estrategia", "estructural") == "vela_medianoche"]

    def crear_orden(self, o):
        if any(x["licencia_id"] == o["licencia_id"] and x["clave"] == o["clave"] for x in self.ordenes.values()):
            return False
        self.ordenes[o["orden_id"]] = {**o, "estrategia": o.get("estrategia") or "estructural",
                                        "estado": "pendiente", "lotes": None, "motivo": None, "ticket": None}
        return True

    def upsert_robot(self, lic, sim, tf, cuenta, est, ahora):
        r = self.robots.setdefault((lic, sim, tf), {"licencia_id": lic, "cuenta": cuenta, "activo": False})
        r.update(cuenta=cuenta, visto_en=ahora, estado=est)

    def robot(self, lic, sim, tf):
        return self.robots.get((lic, sim, tf))

    def aplicar_ack(self, lic, a):
        if a.get("cierre_id"):
            c = self.cierres.get(a["cierre_id"])
            if c and c["licencia_id"] == lic:
                c["estado"] = "ejecutado" if a.get("estado") == "ejecutado" else "fallido"
                if c["estado"] == "ejecutado":
                    r = self.robots.get((lic, c["simbolo"], c["temporalidad"]))
                    if r:
                        r["posicion_abierta"] = False
                    # sin robot registrado no hay estrategia de referencia: no se agrega fila de historial para
                    # esa posicion (espejo del null-guard de RepoPg cuando no encuentra la ultima orden 'colocada').
                    if r:
                        for p in a.get("posiciones") or []:
                            self.historial.append({
                                "licencia_id": lic, "simbolo": c["simbolo"], "temporalidad": c["temporalidad"],
                                "estrategia": r.get("estrategia"), "ticket": p.get("ticket"),
                                "precio_cierre": p.get("precio_cierre"), "profit_usd": p.get("profit_usd"),
                                "razon_cierre": c["motivo"]})
            return
        o = self.ordenes.get(a.get("orden_id"))
        if o and o["licencia_id"] == lic:
            o["estado"] = "colocada" if a.get("estado") == "colocada" else "rechazada"
            o["ticket"], o["motivo"] = a.get("ticket"), a.get("motivo")
            # ponytail: abrir posicion_abierta en el ack de apertura; Task 6 cierra el ciclo poniendola en False al cerrar
            if o["estado"] == "colocada" and o.get("estrategia") == "swing_express":
                r = self.robots.get((lic, o["simbolo"], o["temporalidad"]))
                if r:
                    r["posicion_abierta"] = True

    def crear_cierre(self, c):
        if any(x["licencia_id"] == c["licencia_id"] and x["simbolo"] == c["simbolo"] and x["temporalidad"] == c["temporalidad"]
               and x["estado"] == "pendiente" and x["expira_en"] > c["expira_en"] - timedelta(seconds=TTL_ORDEN_S)
               for x in self.cierres.values()):   # "ahora" = el de la creacion del nuevo (la prueba usa hora fija)
            return False
        self.cierres[c["cierre_id"]] = {**c, "estado": "pendiente"}
        return True

    def cierre_pendiente(self, lic, sim, tf, ahora):
        for c in self.cierres.values():
            if c["licencia_id"] == lic and c["simbolo"] == sim and c["temporalidad"] == tf \
                    and c["estado"] == "pendiente" and c["expira_en"] > ahora:
                return c
        return None

    def expirar(self, ahora):
        for o in self.ordenes.values():
            if o["estado"] in ("pendiente", "entregada") and o["expira_en"] <= ahora:
                o["estado"] = "expirada"

    def orden_para_entregar(self, lic, sim, tf, ahora):
        c = [o for o in self.ordenes.values() if o["licencia_id"] == lic and o["simbolo"] == sim and o["temporalidad"] == tf
             and o["estado"] in ("pendiente", "entregada") and o["expira_en"] > ahora]
        return min(c, key=lambda o: o["expira_en"]) if c else None

    def marcar(self, orden_id, estado, motivo=None, lotes=None, ahora=None):
        o = self.ordenes[orden_id]
        o["estado"], o["motivo"] = estado, motivo
        if lotes is not None:
            o["lotes"] = lotes
        return o

    def cuenta(self, cuenta):
        return self.cuentas.get(cuenta)

    def guardar_cuenta(self, cuenta, pico, halt):
        self.cuentas[cuenta] = {"pico_equity": pico, "halt": halt}

    def activar(self, lic, sim, tf, activo, estrategia=None):
        r = self.robots.setdefault((lic, sim, tf), {"licencia_id": lic, "cuenta": None, "activo": False})
        r["activo"] = bool(activo)
        if estrategia:
            r["estrategia"] = estrategia

    def reconciliar_posicion(self, lic, sim, tf):
        """Corrige posicion_abierta a False cuando el EA ya no reporta ninguna posicion (ver pulso()),
        aunque el cierre no haya pasado por ordenar_cierre/aplicar_ack (cierre forzado, manual, expiracion)."""
        r = self.robots.get((lic, sim, tf))
        if r:
            r["posicion_abierta"] = False
            r["posicion_abierta_en"] = None

    def registrar_autorizacion(self, lic, sim, tf, evento, chat_id, huella, ip, ua):
        self.autorizaciones = getattr(self, "autorizaciones", [])
        self.autorizaciones.append({"licencia_id": lic, "simbolo": sim, "temporalidad": tf, "evento": evento,
                                    "chat_id": chat_id, "texto_huella": huella, "ip": ip, "user_agent": ua})

    def ultima_autorizacion(self, lic, sim, tf):
        """Ultimo evento 'aceptada' / 'rechazada' de ese robot (None si nunca respondio)."""
        for a in reversed(getattr(self, "autorizaciones", [])):
            if (a["licencia_id"], a["simbolo"], a["temporalidad"]) == (lic, sim, tf) and a["evento"] != "solicitada" and a["chat_id"]:
                return a["evento"]
        return None


COLS_AUT = ("id", "registrada_en", "evento", "simbolo", "temporalidad", "alumno", "correo", "robot", "cuenta", "licencia_id",
            "chat_id", "texto_huella", "ip", "user_agent")


def filtros_autorizaciones(desde=None, hasta=None, usuario=None, nombre=None, activo=None, evento=None, **_) -> tuple[str, list]:
    """WHERE parametrizado del buscador (todo opcional, se combina con AND).
    desde/hasta: fecha AAAA-MM-DD (hasta incluye ese dia); usuario: parte del correo; nombre: parte del nombre del alumno o del robot;
    activo: simbolo exacto (GOLD, EURUSD...); evento: solicitada | aceptada | rechazada."""
    cond, p = [], []
    if desde:
        cond.append("registrada_en >= %s::date"); p.append(desde)
    if hasta:
        cond.append("registrada_en < (%s::date + 1)"); p.append(hasta)
    if usuario:
        cond.append("lower(correo) LIKE %s"); p.append(f"%{usuario.lower()}%")
    if nombre:
        cond.append("(alumno ILIKE %s OR robot ILIKE %s)"); p += [f"%{nombre}%"] * 2
    if activo:
        cond.append("upper(simbolo) = %s"); p.append(activo.upper())
    if evento:
        cond.append("evento = %s"); p.append(evento)
    return ("WHERE " + " AND ".join(cond) if cond else ""), p


def _filas_historial(lic, sim, tf, motivo, ult, posiciones):
    """Filas (en orden de columnas de master_historial, sin id/cerrada_en) a insertar para un cierre ejecutado.
    `ult` es la tupla (estrategia, direccion, lotes) de la ultima orden 'colocada' de esa licencia/simbolo/temporalidad,
    o None si no se encontro ninguna. Sin ella no hay valor para columnas NOT NULL (estrategia/direccion/lotes):
    devuelve [] en vez de filas con None, para no violar la restriccion al insertar."""
    if not ult:
        return []
    estrategia, direccion, lotes = ult
    return [(lic, sim, tf, estrategia, direccion, lotes, p.get("ticket"), p.get("precio_cierre"), p.get("profit_usd"), motivo)
            for p in posiciones or []]


class RepoPg:
    """Postgres (tablas master_* de persistencia/migraciones.py)."""

    def __init__(self):
        from persistencia.conexion import get_conn
        self._conn = get_conn

    def robots_activados(self, simbolo, tf):
        with self._conn() as c:
            filas = c.execute(
                """SELECT m.licencia_id, l.cuenta FROM master_robots m JOIN licencias l ON l.id = m.licencia_id
                   WHERE m.simbolo = %s AND m.temporalidad = %s AND m.activo AND l.revocada_en IS NULL
                     AND m.estrategia NOT IN ('swing_express', 'vela_medianoche')
                     AND (l.expira_en IS NULL OR l.expira_en > now())""", (simbolo, tf)).fetchall()
        return [{"licencia_id": f[0], "cuenta": f[1]} for f in filas]

    def robots_swing_express(self, simbolo, tf):
        with self._conn() as c:
            filas = c.execute(
                """SELECT m.licencia_id, l.cuenta, m.posicion_abierta FROM master_robots m JOIN licencias l ON l.id = m.licencia_id
                   WHERE m.simbolo = %s AND m.temporalidad = %s AND m.activo AND l.revocada_en IS NULL
                     AND m.estrategia = 'swing_express'
                     AND (l.expira_en IS NULL OR l.expira_en > now())""", (simbolo, tf)).fetchall()
        return [{"licencia_id": f[0], "cuenta": f[1], "posicion_abierta": f[2]} for f in filas]

    def robots_vela_medianoche(self, simbolo, tf):
        with self._conn() as c:
            filas = c.execute(
                """SELECT m.licencia_id, l.cuenta, m.posicion_abierta, m.posicion_abierta_en
                   FROM master_robots m JOIN licencias l ON l.id = m.licencia_id
                   WHERE m.simbolo = %s AND m.temporalidad = %s AND m.activo AND l.revocada_en IS NULL
                     AND m.estrategia = 'vela_medianoche'
                     AND (l.expira_en IS NULL OR l.expira_en > now())""", (simbolo, tf)).fetchall()
        return [{"licencia_id": f[0], "cuenta": f[1], "posicion_abierta": f[2], "posicion_abierta_en": f[3]} for f in filas]

    def crear_orden(self, o):
        o = {**o, "estrategia": o.get("estrategia") or "estructural"}
        with self._conn() as c:
            n = c.execute(
                """INSERT INTO master_ordenes (orden_id, licencia_id, cuenta, simbolo, temporalidad, direccion, entrada, stop, tp,
                       estrategia, clave, ref_ts, ref_cierre, expira_en)
                   VALUES (%(orden_id)s, %(licencia_id)s, %(cuenta)s, %(simbolo)s, %(temporalidad)s, %(direccion)s, %(entrada)s,
                       %(stop)s, %(tp)s, %(estrategia)s, %(clave)s, %(ref_ts)s, %(ref_cierre)s, %(expira_en)s)
                   ON CONFLICT (licencia_id, clave) DO NOTHING""", o).rowcount
        return n == 1

    def upsert_robot(self, lic, sim, tf, cuenta, est, ahora):
        resumen = {k: est.get(k) for k in ("balance", "equity", "pnl_hoy", "racha", "riesgo_abierto_pct", "posiciones", "version")}
        with self._conn() as c:
            c.execute(
                """INSERT INTO master_robots (licencia_id, simbolo, temporalidad, cuenta, visto_en, estado)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (licencia_id, simbolo, temporalidad)
                   DO UPDATE SET cuenta = EXCLUDED.cuenta, visto_en = EXCLUDED.visto_en, estado = EXCLUDED.estado""",
                (lic, sim, tf, cuenta, ahora, json.dumps(resumen)))

    def robot(self, lic, sim, tf):
        with self._conn() as c:
            f = c.execute("""SELECT activo, estrategia, posicion_abierta, posicion_abierta_en FROM master_robots
                             WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s""", (lic, sim, tf)).fetchone()
        return {"activo": f[0], "estrategia": f[1], "posicion_abierta": f[2], "posicion_abierta_en": f[3]} if f else None

    def reconciliar_posicion(self, lic, sim, tf):
        """Corrige posicion_abierta a False cuando el EA ya no reporta ninguna posicion (ver pulso()),
        aunque el cierre no haya pasado por ordenar_cierre/aplicar_ack (cierre forzado, manual, expiracion).
        Tambien limpia posicion_abierta_en (Vela de Medianoche): posicion cerrada por otra via, sin marca que contar."""
        with self._conn() as c:
            c.execute("""UPDATE master_robots SET posicion_abierta = false, posicion_abierta_en = NULL
                        WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s""", (lic, sim, tf))

    def crear_cierre(self, c):
        with self._conn() as conn:
            hay = conn.execute(
                """SELECT 1 FROM master_cierres WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s
                   AND estado = 'pendiente' AND expira_en > now()""",
                (c["licencia_id"], c["simbolo"], c["temporalidad"])).fetchone()
            if hay:
                return False
            conn.execute(
                """INSERT INTO master_cierres (cierre_id, licencia_id, simbolo, temporalidad, motivo, expira_en)
                   VALUES (%(cierre_id)s, %(licencia_id)s, %(simbolo)s, %(temporalidad)s, %(motivo)s, %(expira_en)s)""", c)
        return True

    def cierre_pendiente(self, lic, sim, tf, ahora):
        with self._conn() as conn:
            f = conn.execute(
                """SELECT cierre_id, motivo FROM master_cierres WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s
                   AND estado = 'pendiente' AND expira_en > %s ORDER BY creada_en LIMIT 1""", (lic, sim, tf, ahora)).fetchone()
        return {"cierre_id": f[0], "motivo": f[1]} if f else None

    def aplicar_ack(self, lic, a):
        if a.get("cierre_id"):
            estado_c = "ejecutado" if a.get("estado") == "ejecutado" else "fallido"
            with self._conn() as c:
                f = c.execute("""UPDATE master_cierres SET estado = %s WHERE cierre_id = %s AND licencia_id = %s
                                  RETURNING simbolo, temporalidad, motivo""", (estado_c, a["cierre_id"], lic)).fetchone()
                if f and estado_c == "ejecutado":
                    sim, tf, motivo = f
                    c.execute("UPDATE master_robots SET posicion_abierta = false WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s",
                              (lic, sim, tf))
                    ult = c.execute("""SELECT estrategia, direccion, lotes FROM master_ordenes
                                        WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s AND estado = 'colocada'
                                        ORDER BY creada_en DESC LIMIT 1""", (lic, sim, tf)).fetchone()
                    for fila in _filas_historial(lic, sim, tf, motivo, ult, a.get("posiciones")):
                        c.execute("""INSERT INTO master_historial
                                      (licencia_id, simbolo, temporalidad, estrategia, direccion, lotes, ticket, precio_cierre, profit_usd, razon_cierre)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", fila)
            return
        estado = "colocada" if a.get("estado") == "colocada" else "rechazada"
        with self._conn() as c:
            f = c.execute("""UPDATE master_ordenes SET estado = %s, ticket = %s, motivo = %s
                              WHERE orden_id = %s AND licencia_id = %s
                              RETURNING simbolo, temporalidad, estrategia""",
                          (estado, a.get("ticket"), (a.get("motivo") or None), a.get("orden_id"), lic)).fetchone()
            # ponytail: abrir posicion_abierta en el ack de apertura; Task 6 cierra el ciclo poniendola en False al cerrar
            if f and estado == "colocada" and f[2] == "swing_express":
                c.execute("UPDATE master_robots SET posicion_abierta = true WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s",
                          (lic, f[0], f[1]))

    def expirar(self, ahora):
        with self._conn() as c:
            c.execute("UPDATE master_ordenes SET estado = 'expirada' WHERE estado IN ('pendiente','entregada') AND expira_en <= %s", (ahora,))

    _COLS = "orden_id, estado, direccion, entrada, stop, tp, lotes, ref_ts, ref_cierre, licencia_id, estrategia"

    def _dict(self, f):
        return dict(zip(self._COLS.split(", "), f)) if f else None

    def orden_para_entregar(self, lic, sim, tf, ahora):
        with self._conn() as c:
            f = c.execute(f"""SELECT {self._COLS} FROM master_ordenes WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s
                              AND estado IN ('pendiente','entregada') AND expira_en > %s ORDER BY expira_en LIMIT 1""",
                          (lic, sim, tf, ahora)).fetchone()
        return self._dict(f)

    def marcar(self, orden_id, estado, motivo=None, lotes=None, ahora=None):
        with self._conn() as c:
            f = c.execute(f"""UPDATE master_ordenes SET estado = %s, motivo = %s, lotes = COALESCE(%s, lotes),
                              entregada_en = CASE WHEN %s = 'entregada' THEN now() ELSE entregada_en END
                              WHERE orden_id = %s RETURNING {self._COLS}""", (estado, motivo, lotes, estado, orden_id)).fetchone()
        return self._dict(f)

    def cuenta(self, cuenta):
        with self._conn() as c:
            f = c.execute("SELECT pico_equity, halt FROM master_cuentas WHERE cuenta = %s", (cuenta,)).fetchone()
        return {"pico_equity": f[0], "halt": f[1]} if f else None

    def guardar_cuenta(self, cuenta, pico, halt):
        with self._conn() as c:
            c.execute("""INSERT INTO master_cuentas (cuenta, pico_equity, halt) VALUES (%s, %s, %s)
                         ON CONFLICT (cuenta) DO UPDATE SET pico_equity = EXCLUDED.pico_equity, halt = EXCLUDED.halt,
                         actualizado_en = now()""", (cuenta, pico, halt))

    # -- admin
    def activar(self, lic, sim, tf, activo: bool, estrategia: str | None = None):
        """estrategia: None preserva la que ya tenga el robot (o 'estructural' si es nuevo); se usa para crear/
        reactivar un robot Swing Express (ver api/master.py, accion 'activar')."""
        with self._conn() as c:
            c.execute("""INSERT INTO master_robots (licencia_id, simbolo, temporalidad, activo, estrategia)
                         VALUES (%s, %s, %s, %s, COALESCE(%s, 'estructural'))
                         ON CONFLICT (licencia_id, simbolo, temporalidad) DO UPDATE
                         SET activo = EXCLUDED.activo, estrategia = COALESCE(%s, master_robots.estrategia)""",
                      (lic, sim, tf, activo, estrategia, estrategia))

    def reanudar(self, cuenta):
        with self._conn() as c:
            c.execute("UPDATE master_cuentas SET halt = NULL, actualizado_en = now() WHERE cuenta = %s", (cuenta,))

    def registrar_autorizacion(self, lic, sim, tf, evento, chat_id, huella, ip, ua):
        """Usuario = el alumno DUENO DEL TELEGRAM que respondio (no el dueno de la licencia): es quien firmo."""
        with self._conn() as c:
            n = c.execute("""INSERT INTO master_autorizaciones
                                 (licencia_id, simbolo, temporalidad, evento, chat_id, texto_huella, ip, user_agent, correo, alumno, robot, cuenta)
                             SELECT l.id, %s, %s, %s, %s, %s, %s, %s, a.correo, a.nombre_completo, l.cliente, l.cuenta
                             FROM licencias l LEFT JOIN alumnos a ON a.telegram_chat_id = %s WHERE l.id = %s""",
                          (sim, tf, evento, chat_id, huella, ip, (ua or "")[:300], chat_id, lic)).rowcount
            if n != 1:
                raise ValueError("licencia no encontrada")

    def ultima_autorizacion(self, lic, sim, tf):
        with self._conn() as c:
            r = c.execute("""SELECT evento FROM master_autorizaciones
                             WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s AND evento <> 'solicitada' AND chat_id IS NOT NULL
                             ORDER BY registrada_en DESC, id DESC LIMIT 1""", (lic, sim, tf)).fetchone()
        return r[0] if r else None

    def buscar_autorizaciones(self, **f):
        """Busqueda del historial de confirmaciones (ver filtros_autorizaciones)."""
        where, params = filtros_autorizaciones(**f)
        with self._conn() as c:
            rows = c.execute(f"""SELECT id, registrada_en, evento, simbolo, temporalidad, alumno, correo, robot, cuenta, licencia_id,
                                        chat_id, texto_huella, ip, user_agent
                                 FROM master_autorizaciones {where} ORDER BY registrada_en DESC, id DESC LIMIT %s OFFSET %s""",
                             params + [f["limite"], f["salto"]]).fetchall()
        return [dict(zip(COLS_AUT, r)) for r in rows]

    def panel(self):
        with self._conn() as c:
            robots = c.execute(
                """SELECT licencia_id, simbolo, temporalidad, cuenta, activo, visto_en,
                          coalesce(visto_en > now() - make_interval(secs => %s), false) AS vivo, estado
                   FROM master_robots ORDER BY licencia_id, simbolo""", (VIVO_S,)).fetchall()
            ordenes = c.execute(
                """SELECT orden_id, licencia_id, simbolo, temporalidad, direccion, entrada, lotes, estado, motivo, creada_en
                   FROM master_ordenes ORDER BY creada_en DESC LIMIT 30""").fetchall()
            cuentas = c.execute("SELECT cuenta, pico_equity, halt, actualizado_en FROM master_cuentas ORDER BY cuenta").fetchall()
            auts = c.execute("""SELECT licencia_id, simbolo, temporalidad, evento, chat_id, texto_huella, ip, registrada_en, alumno
                                FROM master_autorizaciones ORDER BY registrada_en DESC LIMIT 25""").fetchall()
        return {"autorizaciones": [dict(zip(("licencia_id", "simbolo", "temporalidad", "evento", "chat_id", "texto_huella", "ip", "registrada_en", "alumno"), a)) for a in auts],
                "robots": [dict(zip(("licencia_id", "simbolo", "temporalidad", "cuenta", "activo", "visto_en", "vivo", "estado"), r)) for r in robots],
                "ordenes": [dict(zip(("orden_id", "licencia_id", "simbolo", "temporalidad", "direccion", "entrada", "lotes", "estado", "motivo", "creada_en"), r)) for r in ordenes],
                "cuentas": [dict(zip(("cuenta", "pico_equity", "halt", "actualizado_en"), r)) for r in cuentas]}


def demo() -> None:
    # vigentes(): casos reales del 30 oct 2026
    oro_m = {"direccion": "long", "entrada": 1905.4, "stop": 1750.9, "indice_conocido": 79}   # zona de hace anos, oro a 3883
    assert vigentes([oro_m], 83, 3883.74) == []                                                 # 51 % lejos del precio
    fresco = {"direccion": "short", "entrada": 1.1213, "stop": 1.1218, "indice_conocido": 4220}
    assert vigentes([fresco], 4224, 1.1151) == [fresco]                                         # 3 velas, 0.55 % lejos
    assert vigentes([{**fresco, "indice_conocido": 4100}], 4224, 1.1151) == []                  # 123 velas: ya expiro
    assert vigentes([fresco], None, None) == [fresco]                                           # API vieja: no filtra
    assert filtros_autorizaciones() == ("", [])
    w, p = filtros_autorizaciones(desde="2026-10-01", hasta="2026-10-31", usuario="Ricardo", nombre="Oro", activo="gold", evento="aceptada")
    assert w.count("%s") == len(p) == 7 and w.startswith("WHERE ") and p[-2:] == ["GOLD", "aceptada"] and p[2] == "%ricardo%", (w, p)
    assert "'" not in w and ";" not in w   # nada se pega al SQL: todo va como parametro
    t0 = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    est = {"balance": 10000, "equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 0, "riesgo_pct": 2.0,
           "tick_size": 0.00001, "tick_value": 1.0, "vol_min": 0.01, "vol_max": 100, "vol_step": 0.01}
    setup = {"direccion": "long", "entrada": 1.1000, "stop": 1.0990, "tp": 1.1020}   # SL de 100 ticks = 100 USD por lote
    repo = MemRepo(activados=[(7, "EURUSD", "Scalping 15m", 111)])
    repo.robots[(8, "EURUSD", "Scalping 15m")] = {"licencia_id": 8, "cuenta": 222, "activo": False}     # no aceptada
    repo.robots[(9, "GBPUSD", "Scalping 15m")] = {"licencia_id": 9, "cuenta": 333, "activo": True}     # otro activo
    # 1. oportunidad -> solo el robot activado de ese activo/temporalidad; repetirla no duplica
    assert recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [setup], 1, 1.1, t0) == {"ordenes": 1, "robots": 1}
    assert recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [setup], 1, 1.1, t0) == {"ordenes": 0, "robots": 1}
    assert recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [], None, None, t0) == {"ordenes": 0, "robots": 0}
    # 2. pulso: entrega con lotes (2 % de 10000 = 200 USD; 100 USD por lote -> 2 lotes)
    r = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", est, ahora=t0 + timedelta(seconds=5))
    assert r["estado"] == "entregada" and abs(r["orden"]["lotes"] - 2.0) < 1e-9, r
    oid = r["orden"]["orden_id"]
    # 3. reentrega idempotente (mismo orden_id y lotes) hasta que el robot confirme
    r2 = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", est, ahora=t0 + timedelta(seconds=10))
    assert r2["estado"] == "reentrega" and r2["orden"]["orden_id"] == oid and r2["orden"]["lotes"] == r["orden"]["lotes"], r2
    # 4. ack colocada -> ya no se entrega
    r3 = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", est, [{"orden_id": oid, "estado": "colocada", "ticket": 99}],
               ahora=t0 + timedelta(seconds=15))
    assert r3["orden"] is None and repo.ordenes[oid]["estado"] == "colocada" and repo.ordenes[oid]["ticket"] == 99, r3
    # 5. robot no activado: pulsa, aparece vivo, no recibe nada
    r = pulso(repo, 8, 222, "EURUSD", "Scalping 15m", est, ahora=t0)
    assert r["estado"] == "sin_activar" and repo.robots[(8, "EURUSD", "Scalping 15m")]["visto_en"] == t0
    # 6. master apagado: nada se entrega
    recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [{**setup, "entrada": 1.2, "stop": 1.199}], 1, 1.1, t0)
    assert pulso(repo, 7, 111, "EURUSD", "Scalping 15m", est, habilitado=False, ahora=t0)["estado"] == "master_apagado"
    # 7. racha de 5 -> bloquea y deja freno; sigue bloqueando aunque la racha baje, hasta que una persona lo levante
    r = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", {**est, "racha": 5}, ahora=t0 + timedelta(seconds=20))
    assert r["estado"] == "bloqueada" and repo.cuentas[111]["halt"], r
    recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [{**setup, "entrada": 1.3, "stop": 1.299}], 1, 1.1, t0 + timedelta(seconds=25))
    r = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", est, ahora=t0 + timedelta(seconds=30))
    assert r["estado"] == "bloqueada" and "FRENO activo" in r["motivos"][0], r
    repo.cuentas[111]["halt"] = None
    recibir_oportunidades(repo, "EURUSD", "Scalping 15m", [{**setup, "entrada": 1.4, "stop": 1.399}], 1, 1.1, t0 + timedelta(seconds=35))
    r = pulso(repo, 7, 111, "EURUSD", "Scalping 15m", {**est, "racha": 3}, ahora=t0 + timedelta(seconds=40))
    assert r["estado"] == "entregada" and abs(r["orden"]["lotes"] - 1.0) < 1e-9, r   # racha 3: mitad de riesgo (1 % = 1 lote)
    # 8. sin lotes: capital que no alcanza ni para el lote minimo
    repo2 = MemRepo(activados=[(7, "EURUSD", "Scalping 15m", 111)])
    recibir_oportunidades(repo2, "EURUSD", "Scalping 15m", [setup], 1, 1.1, t0)
    r = pulso(repo2, 7, 111, "EURUSD", "Scalping 15m", {**est, "balance": 10, "equity": 10}, ahora=t0)
    assert r["estado"] == "sin_lotes", r
    # 9. expira: una orden que nadie recoge en 5 min no se entrega
    repo3 = MemRepo(activados=[(7, "EURUSD", "Scalping 15m", 111)])
    recibir_oportunidades(repo3, "EURUSD", "Scalping 15m", [setup], 1, 1.1, t0)
    r = pulso(repo3, 7, 111, "EURUSD", "Scalping 15m", est, ahora=t0 + timedelta(seconds=TTL_ORDEN_S + 1))
    assert r["estado"] == "sin_orden", r
    # 10. cierre ordenado: el robot lo recibe primero, aunque haya una orden pendiente; el ack lo cierra
    repo4 = MemRepo(activados=[(7, "EURUSD", "Scalping 15m", 111)])
    recibir_oportunidades(repo4, "EURUSD", "Scalping 15m", [setup], 1, 1.1, t0)
    assert ordenar_cierre(repo4, 7, "EURUSD", "Scalping 15m", "prueba", t0) is not None
    assert ordenar_cierre(repo4, 7, "EURUSD", "Scalping 15m", "otra vez", t0) is None  # ya hay uno pendiente
    r = pulso(repo4, 7, 111, "EURUSD", "Scalping 15m", est, ahora=t0 + timedelta(seconds=1))
    assert r["estado"] == "cierre" and r["orden"] is None and r["cierre"]["motivo"] == "prueba", r
    cid = r["cierre"]["cierre_id"]
    r = pulso(repo4, 7, 111, "EURUSD", "Scalping 15m", est, [{"cierre_id": cid, "estado": "ejecutado"}], ahora=t0 + timedelta(seconds=6))
    assert repo4.cierres[cid]["estado"] == "ejecutado" and r["estado"] == "entregada", r  # despues del cierre sigue la orden
    # 11. cierre automatico: con freno activo y posiciones abiertas, el Master pide cerrar (solo si cierre_auto)
    repo5 = MemRepo(activados=[(7, "EURUSD", "Scalping 15m", 111)])
    repo5.cuentas[111] = {"pico_equity": 10000, "halt": "drawdown 16 %"}
    r = pulso(repo5, 7, 111, "EURUSD", "Scalping 15m", {**est, "posiciones": 1}, ahora=t0)
    assert r["estado"] == "sin_orden", r  # apagado por defecto
    r = pulso(repo5, 7, 111, "EURUSD", "Scalping 15m", {**est, "posiciones": 1}, ahora=t0, cierre_auto=True)
    assert r["estado"] == "cierre" and "FRENO" in r["cierre"]["motivo"], r
    # 12. robots_activados excluye swing_express; robots_swing_express solo trae esos
    repoX = MemRepo(activados=[(1, "EURUSD", "Scalping 15m", 111)])
    repoX.robots[(1, "EURUSD", "Scalping 15m")]["estrategia"] = "estructural"
    repoX.robots[(2, "EURUSD", "Scalping 15m")] = {"licencia_id": 2, "cuenta": 222, "activo": True, "estrategia": "swing_express"}
    assert [r["licencia_id"] for r in repoX.robots_activados("EURUSD", "Scalping 15m")] == [1], "swing_express no debe salir aqui"
    assert [r["licencia_id"] for r in repoX.robots_swing_express("EURUSD", "Scalping 15m")] == [2]
    # 12b. robots_activados tambien excluye vela_medianoche; robots_vela_medianoche solo trae esos
    repoX.robots[(3, "EURUSD", "Scalping 15m")] = {"licencia_id": 3, "cuenta": 333, "activo": True,
                                                    "estrategia": "vela_medianoche", "posicion_abierta": False,
                                                    "posicion_abierta_en": None}
    assert [r["licencia_id"] for r in repoX.robots_activados("EURUSD", "Scalping 15m")] == [1], "vela_medianoche no debe salir aqui"
    assert [r["licencia_id"] for r in repoX.robots_vela_medianoche("EURUSD", "Scalping 15m")] == [3]
    # 13. recibir_tendencia_swing_express: abre en "compra", cierra en "venta", nunca duplica, re-entra tras ciclo completo
    t1 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    repoS = MemRepo()
    repoS.robots[(9, "XAUUSD", "Swing (S)")] = {"licencia_id": 9, "cuenta": 999, "activo": True, "estrategia": "swing_express", "posicion_abierta": False}
    # compra sin posicion abierta -> crea 1 orden
    r1 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2650.0, t1)
    assert r1 == {"aperturas": 1, "cierres": 0, "robots": 1}, r1
    orden = next(o for o in repoS.ordenes.values() if o["licencia_id"] == 9)
    assert orden["estrategia"] == "swing_express" and orden["direccion"] == "long" and orden["entrada"] == orden["stop"] == 2650.0
    # compra otra vez, misma posicion todavia no marcada abierta -> no debe duplicar
    r2 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2651.0, t1 + timedelta(seconds=5))
    assert r2 == {"aperturas": 0, "cierres": 0, "robots": 1}, r2
    # el robot "coloca" la orden (ack) y queda con posicion abierta
    repoS.aplicar_ack(9, {"orden_id": orden["orden_id"], "estado": "colocada", "ticket": 777})
    assert repoS.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is True
    # venta con posicion abierta -> ordena cierre, nunca abre
    r3 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "venta", 2640.0, t1 + timedelta(minutes=10))
    assert r3 == {"aperturas": 0, "cierres": 1, "robots": 1}, r3
    # venta de nuevo, sin posicion -> no-op (CerrarTodoMaster ya cerro, posicion_abierta ya en False tras el ack del cierre)
    repoS.aplicar_ack(9, {"cierre_id": next(iter(repoS.cierres)), "estado": "ejecutado"})
    assert repoS.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is False
    r4 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "venta", 2639.0, t1 + timedelta(minutes=11))
    assert r4 == {"aperturas": 0, "cierres": 0, "robots": 1}, r4
    # re-entrada: compra de nuevo tras el ciclo completo -> SI debe poder abrir otra vez (clave distinta)
    r5 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "compra", 2655.0, t1 + timedelta(minutes=12))
    assert r5 == {"aperturas": 1, "cierres": 0, "robots": 1}, r5
    # "sin_definir" nunca hace nada
    r6 = recibir_tendencia_swing_express(repoS, "XAUUSD", "Swing (S)", "sin_definir", 2655.0, t1 + timedelta(minutes=13))
    assert r6 == {"aperturas": 0, "cierres": 0, "robots": 1}, r6
    # 7. pulso con estrategia swing_express: lote fijo 0.01, sin pasar por _lotes()
    t7 = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
    repoP = MemRepo(activados=[(5, "XAUUSD", "Swing (S)", 555)])
    repoP.robots[(5, "XAUUSD", "Swing (S)")]["estrategia"] = "swing_express"
    repoP.crear_orden({"orden_id": "Mswe1", "licencia_id": 5, "cuenta": 555, "simbolo": "XAUUSD", "temporalidad": "Swing (S)",
                       "direccion": "long", "entrada": 2650.0, "stop": 2650.0, "tp": None, "estrategia": "swing_express",
                       "clave": "swing_express|1", "ref_ts": None, "ref_cierre": 2650.0, "expira_en": t7 + timedelta(seconds=300)})
    est7 = {"balance": 10000, "equity": 10000, "pico_equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 0, "riesgo_pct": 2.0}
    r7 = pulso(repoP, 5, 555, "XAUUSD", "Swing (S)", est7, ahora=t7)
    assert r7["estado"] == "entregada" and r7["orden"]["lotes"] == 0.01, r7
    # con freno duro activo, swing_express SI debe bloquearse (no se salta la compuerta completa)
    repoP2 = MemRepo(activados=[(6, "XAUUSD", "Swing (S)", 666)])
    repoP2.robots[(6, "XAUUSD", "Swing (S)")]["estrategia"] = "swing_express"
    repoP2.crear_orden({"orden_id": "Mswe2", "licencia_id": 6, "cuenta": 666, "simbolo": "XAUUSD", "temporalidad": "Swing (S)",
                        "direccion": "long", "entrada": 2650.0, "stop": 2650.0, "tp": None, "estrategia": "swing_express",
                        "clave": "swing_express|2", "ref_ts": None, "ref_cierre": 2650.0, "expira_en": t7 + timedelta(seconds=300)})
    est_halt = {**est7, "racha": 5}  # 5 perdidas seguidas = freno duro en la compuerta
    r8 = pulso(repoP2, 6, 666, "XAUUSD", "Swing (S)", est_halt, ahora=t7)
    assert r8["estado"] == "bloqueada", r8
    # 8b. pulso reconcilia posicion_abierta a False si el EA ya no reporta posiciones (cierre forzado -3%, manual o
    # expiracion -- no por el Master): sin esto, recibir_tendencia_swing_express nunca volveria a abrir aunque la
    # tendencia siga alcista, porque siempre veria posicion_abierta=True.
    repoR = MemRepo(activados=[(10, "XAUUSD", "Swing (S)", 1010)])
    repoR.robots[(10, "XAUUSD", "Swing (S)")]["estrategia"] = "swing_express"
    repoR.robots[(10, "XAUUSD", "Swing (S)")]["posicion_abierta"] = True
    pulso(repoR, 10, 1010, "XAUUSD", "Swing (S)", {**est7, "posiciones": 0}, ahora=t7)
    assert repoR.robots[(10, "XAUUSD", "Swing (S)")]["posicion_abierta"] is False
    # si el EA todavia reporta una posicion, no se toca
    repoR.robots[(10, "XAUUSD", "Swing (S)")]["posicion_abierta"] = True
    pulso(repoR, 10, 1010, "XAUUSD", "Swing (S)", {**est7, "posiciones": 1}, ahora=t7)
    assert repoR.robots[(10, "XAUUSD", "Swing (S)")]["posicion_abierta"] is True
    # 9. ack de cierre con posiciones -> alimenta master_historial y cierra posicion_abierta
    repoH = MemRepo()
    repoH.robots[(9, "XAUUSD", "Swing (S)")] = {"licencia_id": 9, "cuenta": 999, "activo": True, "estrategia": "swing_express", "posicion_abierta": True}
    repoH.cierres["Chist1"] = {"cierre_id": "Chist1", "licencia_id": 9, "simbolo": "XAUUSD", "temporalidad": "Swing (S)", "motivo": "tendencia cambio", "estado": "pendiente", "expira_en": t0 + timedelta(seconds=300)}
    repoH.aplicar_ack(9, {"cierre_id": "Chist1", "estado": "ejecutado",
                          "posiciones": [{"ticket": 555001, "precio_cierre": 2639.5, "profit_usd": -12.3}]})
    assert repoH.cierres["Chist1"]["estado"] == "ejecutado"
    assert repoH.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] is False
    fila = repoH.historial[-1]
    assert fila["licencia_id"] == 9 and fila["ticket"] == 555001 and fila["profit_usd"] == -12.3, fila
    # un cierre con 2 posiciones en la misma lista inserta 2 filas
    repoH.cierres["Chist2"] = {"cierre_id": "Chist2", "licencia_id": 9, "simbolo": "XAUUSD", "temporalidad": "Swing (S)", "motivo": "freno", "estado": "pendiente", "expira_en": t0 + timedelta(seconds=300)}
    repoH.robots[(9, "XAUUSD", "Swing (S)")]["posicion_abierta"] = True
    repoH.aplicar_ack(9, {"cierre_id": "Chist2", "estado": "ejecutado",
                          "posiciones": [{"ticket": 1, "precio_cierre": 1.0, "profit_usd": 1.0}, {"ticket": 2, "precio_cierre": 2.0, "profit_usd": 2.0}]})
    assert len(repoH.historial) == 3, repoH.historial
    # 10. cierre ejecutado sin dato de referencia (RepoPg: sin orden 'colocada'; MemRepo: sin robot registrado) ->
    # no revienta, no agrega fila de historial para esa posicion, pero el resto del ack (cierre ejecutado) si aplica
    assert _filas_historial(9, "XAUUSD", "Swing (S)", "m", None, [{"ticket": 1, "precio_cierre": 1.0, "profit_usd": 1.0}]) == []
    assert _filas_historial(9, "XAUUSD", "Swing (S)", "m", ("estructural", "long", 0.5), [{"ticket": 1, "precio_cierre": 1.0, "profit_usd": 1.0}]) \
        == [(9, "XAUUSD", "Swing (S)", "estructural", "long", 0.5, 1, 1.0, 1.0, "m")]
    repoNR = MemRepo()   # licencia 77 nunca tuvo robot registrado (equivalente a "nunca tuvo orden colocada")
    repoNR.cierres["Chist3"] = {"cierre_id": "Chist3", "licencia_id": 77, "simbolo": "XAUUSD", "temporalidad": "Swing (S)",
                                 "motivo": "freno", "estado": "pendiente", "expira_en": t0 + timedelta(seconds=300)}
    repoNR.aplicar_ack(77, {"cierre_id": "Chist3", "estado": "ejecutado",
                            "posiciones": [{"ticket": 999, "precio_cierre": 1.0, "profit_usd": 0.0}]})
    assert repoNR.cierres["Chist3"]["estado"] == "ejecutado"
    assert repoNR.historial == []
    # 11. activar() acepta estrategia -- crea/reactiva un robot swing_express; sin ella, preserva la estrategia actual
    # (no rompe el flujo existente de Estructural/Vela de Medianoche, que nunca manda este parametro)
    repoA = MemRepo()
    repoA.activar(20, "XAUUSD", "Swing (S)", True, "swing_express")
    assert repoA.robots[(20, "XAUUSD", "Swing (S)")]["activo"] is True
    assert repoA.robots[(20, "XAUUSD", "Swing (S)")]["estrategia"] == "swing_express"
    repoA.activar(20, "XAUUSD", "Swing (S)", False)   # desactivar sin mandar estrategia: no la borra
    assert repoA.robots[(20, "XAUUSD", "Swing (S)")]["activo"] is False
    assert repoA.robots[(20, "XAUUSD", "Swing (S)")]["estrategia"] == "swing_express"
    # 12. recibir_candidato_vela_medianoche: 1 orden larga diaria en el PDH, stop 2% debajo; repetir el mismo
    # dia_anterior no duplica (clave = vela_medianoche|dia_anterior)
    tM = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
    repoVM = MemRepo()
    repoVM.robots[(11, "XAUUSD", "Diario")] = {"licencia_id": 11, "cuenta": 1111, "activo": True,
                                               "estrategia": "vela_medianoche", "posicion_abierta": False,
                                               "posicion_abierta_en": None}
    rv1 = recibir_candidato_vela_medianoche(repoVM, "XAUUSD", 2650.0, date(2026, 11, 6), tM)
    assert rv1 == {"aperturas": 1, "robots": 1}, rv1
    ordenVM = next(o for o in repoVM.ordenes.values() if o["licencia_id"] == 11)
    assert ordenVM["estrategia"] == "vela_medianoche" and ordenVM["direccion"] == "long", ordenVM
    assert ordenVM["entrada"] == 2650.0 and abs(ordenVM["stop"] - 2650.0 * 0.98) < 1e-9, ordenVM
    assert ordenVM["clave"] == "vela_medianoche|2026-11-06", ordenVM
    rv2 = recibir_candidato_vela_medianoche(repoVM, "XAUUSD", 2650.0, date(2026, 11, 6), tM + timedelta(minutes=1))
    assert rv2 == {"aperturas": 0, "robots": 1}, rv2
    # 13. cierre a las 7h: un robot vela_medianoche con posicion abierta hace >= 7h se cierra solo (cierre_auto=True)
    repoVM2 = MemRepo(activados=[(12, "XAUUSD", "Diario", 1212)])
    repoVM2.robots[(12, "XAUUSD", "Diario")]["estrategia"] = "vela_medianoche"
    repoVM2.robots[(12, "XAUUSD", "Diario")]["posicion_abierta"] = True
    repoVM2.robots[(12, "XAUUSD", "Diario")]["posicion_abierta_en"] = tM - timedelta(hours=7, minutes=1)
    estVM = {"balance": 10000, "equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 1, "riesgo_pct": 2.0}
    r = pulso(repoVM2, 12, 1212, "XAUUSD", "Diario", estVM, ahora=tM, cierre_auto=True)
    assert r["estado"] == "cierre" and r["cierre"]["motivo"] == "Vela de Medianoche: 7 horas desde la entrada", r
    # todavia no llegan 7h -> no cierra por tiempo (ni por freno: no hay halt)
    repoVM3 = MemRepo(activados=[(13, "XAUUSD", "Diario", 1313)])
    repoVM3.robots[(13, "XAUUSD", "Diario")]["estrategia"] = "vela_medianoche"
    repoVM3.robots[(13, "XAUUSD", "Diario")]["posicion_abierta"] = True
    repoVM3.robots[(13, "XAUUSD", "Diario")]["posicion_abierta_en"] = tM - timedelta(hours=6)
    r = pulso(repoVM3, 13, 1313, "XAUUSD", "Diario", estVM, ahora=tM, cierre_auto=True)
    assert r["estado"] == "sin_orden", r
    print("master.despachador.demo() OK")


if __name__ == "__main__":
    demo()
