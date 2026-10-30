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
from datetime import datetime, timedelta, timezone

from master import riesgo as compuerta

TTL_ORDEN_S = 300   # una orden que el robot no recoge en 5 min expira (el setup ya no es de ahora)
VIVO_S = 30         # un robot que no pregunta en 30 s no cuenta como vivo
PCT_MAX = 2.0       # mismo tope que conectividad.riesgo.PCT_MAX_SL


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def clave_oportunidad(s: dict) -> str:
    return f"{s['direccion']}|{float(s['entrada']):.8g}|{float(s['stop']):.8g}"


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


def _lotes(est: dict, orden: dict, riesgo_pct: float) -> tuple[float, str]:
    from conectividad.riesgo import dimensionar
    costo = abs(orden["entrada"] - orden["stop"]) / float(est["tick_size"]) * float(est["tick_value"])
    lot = dimensionar(float(est["balance"]), riesgo_pct / 100.0, costo,
                      float(est["vol_min"]), float(est["vol_max"]), float(est["vol_step"]))
    return (lot.lotes, "") if lot.viable else (0.0, lot.motivo or "capital insuficiente para el lote minimo")


def _publica(o: dict) -> dict:
    return {k: o.get(k) for k in ("orden_id", "direccion", "entrada", "stop", "tp", "lotes", "ref_ts", "ref_cierre")}


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

    c = repo.cuenta(cuenta) or {"pico_equity": 0.0, "halt": None}
    pico = max(float(c.get("pico_equity") or 0), float(est.get("equity") or 0))
    est = {**est, "pico_equity": pico, "halt": c.get("halt")}

    # cierre ordenado: tiene prioridad sobre cualquier orden nueva (los cierres nunca se bloquean)
    cierre = repo.cierre_pendiente(licencia_id, simbolo, temporalidad, ahora)
    if cierre is None and cierre_auto and c.get("halt") and (int(est.get("posiciones") or 0) > 0 or est.get("pendientes")):
        cierre = ordenar_cierre(repo, licencia_id, simbolo, temporalidad, "FRENO de la cuenta: " + str(c["halt"]), ahora)
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
    rr = None
    if orden.get("tp") and orden["entrada"] != orden["stop"]:
        rr = abs(orden["tp"] - orden["entrada"]) / abs(orden["entrada"] - orden["stop"])
    v = compuerta.evaluar(est, min(base, PCT_MAX), rr, cfg)
    repo.guardar_cuenta(cuenta, pico, v["halt"] or c.get("halt"))
    if not v["ok"]:
        repo.marcar(orden["orden_id"], "bloqueada", "; ".join(v["motivos"]))
        return {"orden": None, "cierre": None, "estado": "bloqueada", "motivos": v["motivos"]}
    lotes, motivo = _lotes(est, orden, min(base * v["factor"], PCT_MAX))
    if lotes <= 0:
        repo.marcar(orden["orden_id"], "sin_lotes", motivo)
        return {"orden": None, "cierre": None, "estado": "sin_lotes", "motivos": [motivo]}
    orden = repo.marcar(orden["orden_id"], "entregada", None, lotes, ahora)
    return {"orden": _publica(orden), "cierre": None, "estado": "entregada", "motivos": []}


# --------------------------------------------------------------------------- repos
class MemRepo:
    """Repo en memoria: pruebas y demo."""

    def __init__(self, activados=()):
        self.robots, self.ordenes, self.cuentas, self.cierres = {}, {}, {}, {}
        for lic, sim, tf, cuenta in activados:
            self.robots[(lic, sim, tf)] = {"licencia_id": lic, "cuenta": cuenta, "activo": True, "visto_en": None}

    def robots_activados(self, simbolo, tf):
        return [r for (_, s, t), r in self.robots.items() if s == simbolo and t == tf and r["activo"]]

    def crear_orden(self, o):
        if any(x["licencia_id"] == o["licencia_id"] and x["clave"] == o["clave"] for x in self.ordenes.values()):
            return False
        self.ordenes[o["orden_id"]] = {**o, "estado": "pendiente", "lotes": None, "motivo": None, "ticket": None}
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
            return
        o = self.ordenes.get(a.get("orden_id"))
        if o and o["licencia_id"] == lic:
            o["estado"] = "colocada" if a.get("estado") == "colocada" else "rechazada"
            o["ticket"], o["motivo"] = a.get("ticket"), a.get("motivo")

    def crear_cierre(self, c):
        if any(x["licencia_id"] == c["licencia_id"] and x["simbolo"] == c["simbolo"] and x["temporalidad"] == c["temporalidad"]
               and x["estado"] == "pendiente" and x["expira_en"] > _ahora() for x in self.cierres.values()):
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

    def activar(self, lic, sim, tf, activo):
        self.robots.setdefault((lic, sim, tf), {"licencia_id": lic, "cuenta": None, "activo": False})["activo"] = bool(activo)


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
                     AND (l.expira_en IS NULL OR l.expira_en > now())""", (simbolo, tf)).fetchall()
        return [{"licencia_id": f[0], "cuenta": f[1]} for f in filas]

    def crear_orden(self, o):
        with self._conn() as c:
            n = c.execute(
                """INSERT INTO master_ordenes (orden_id, licencia_id, cuenta, simbolo, temporalidad, direccion, entrada, stop, tp,
                       clave, ref_ts, ref_cierre, expira_en)
                   VALUES (%(orden_id)s, %(licencia_id)s, %(cuenta)s, %(simbolo)s, %(temporalidad)s, %(direccion)s, %(entrada)s,
                       %(stop)s, %(tp)s, %(clave)s, %(ref_ts)s, %(ref_cierre)s, %(expira_en)s)
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
            f = c.execute("SELECT activo FROM master_robots WHERE licencia_id = %s AND simbolo = %s AND temporalidad = %s",
                          (lic, sim, tf)).fetchone()
        return {"activo": f[0]} if f else None

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
            with self._conn() as c:
                c.execute("UPDATE master_cierres SET estado = %s WHERE cierre_id = %s AND licencia_id = %s",
                          ("ejecutado" if a.get("estado") == "ejecutado" else "fallido", a["cierre_id"], lic))
            return
        with self._conn() as c:
            c.execute("""UPDATE master_ordenes SET estado = %s, ticket = %s, motivo = %s
                         WHERE orden_id = %s AND licencia_id = %s""",
                      ("colocada" if a.get("estado") == "colocada" else "rechazada", a.get("ticket"),
                       (a.get("motivo") or None), a.get("orden_id"), lic))

    def expirar(self, ahora):
        with self._conn() as c:
            c.execute("UPDATE master_ordenes SET estado = 'expirada' WHERE estado IN ('pendiente','entregada') AND expira_en <= %s", (ahora,))

    _COLS = "orden_id, estado, direccion, entrada, stop, tp, lotes, ref_ts, ref_cierre, licencia_id"

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
    def activar(self, lic, sim, tf, activo: bool):
        with self._conn() as c:
            c.execute("""INSERT INTO master_robots (licencia_id, simbolo, temporalidad, activo) VALUES (%s, %s, %s, %s)
                         ON CONFLICT (licencia_id, simbolo, temporalidad) DO UPDATE SET activo = EXCLUDED.activo""",
                      (lic, sim, tf, activo))

    def reanudar(self, cuenta):
        with self._conn() as c:
            c.execute("UPDATE master_cuentas SET halt = NULL, actualizado_en = now() WHERE cuenta = %s", (cuenta,))

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
        return {"robots": [dict(zip(("licencia_id", "simbolo", "temporalidad", "cuenta", "activo", "visto_en", "vivo", "estado"), r)) for r in robots],
                "ordenes": [dict(zip(("orden_id", "licencia_id", "simbolo", "temporalidad", "direccion", "entrada", "lotes", "estado", "motivo", "creada_en"), r)) for r in ordenes],
                "cuentas": [dict(zip(("cuenta", "pico_equity", "halt", "actualizado_en"), r)) for r in cuentas]}


def demo() -> None:
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
    print("master.despachador.demo() OK")


if __name__ == "__main__":
    demo()
