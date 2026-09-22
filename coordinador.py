"""Agente coordinador autónomo — Bloque 3.

Lee señales SMC de smc_snapshot, gestiona posiciones en las 5 cuentas demo.
Ejecutar periódicamente (cron / Scheduled Task) desde C:\\MTB\\.

Lógica por cuenta:
  - Sin posición abierta + tendencia activa → abrir en dirección de la tendencia
  - Posición abierta + CHoCH inverso         → cerrar (swing sin SL)
  - Posición abierta + SL en MT5             → MT5 lo cierra solo (scalping/intraday)
"""

import os

import MetaTrader5 as mt5
from dotenv import load_dotenv

from conectividad.xm import (
    ConexionXMError,
    abrir_posicion,
    cerrar_posicion,
    conectar,
    desconectar,
    info_cuenta,
    login_cuenta,
)
from conectividad.riesgo import calcular_lotes, TEMPORALIDADES_SWING
from persistencia.conexion import get_conn

load_dotenv()


# ── Credenciales ──────────────────────────────────────────────────────────────

def _credenciales_env() -> dict[int, dict]:
    """Devuelve {login: {password, server}} para cada slot configurado en .env."""
    creds = {}
    for sufijo in ("", "_2", "_3", "_4", "_5"):
        login = os.environ.get(f"XM_LOGIN{sufijo}")
        pw    = os.environ.get(f"XM_PASSWORD{sufijo}")
        srv   = os.environ.get(f"XM_SERVER{sufijo}")
        if login and pw and srv:
            creds[int(login)] = {"password": pw, "server": srv}
    return creds


# ── Lecturas de BD ────────────────────────────────────────────────────────────

def _cuentas_activas() -> list[dict]:
    """Cuentas_demo activas: login, server, simbolo, temporalidad, pct_riesgo."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT login, server, simbolo, temporalidad, pct_riesgo FROM cuentas_demo WHERE activa = true"
        ).fetchall()
    return [dict(zip(("login", "server", "simbolo", "temporalidad", "pct_riesgo"), r)) for r in rows]


def _snapshot(simbolo: str, temporalidad: str) -> dict | None:
    """Última señal SMC para el par. None si no hay datos."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tendencia_actual, atr FROM smc_snapshot WHERE simbolo = %s AND temporalidad = %s",
            (simbolo, temporalidad),
        ).fetchone()
    if row is None:
        return None
    return {"tendencia_actual": row[0], "atr": row[1]}


def _posicion_db(login: int) -> dict | None:
    """Posición abierta en BD para esta cuenta. None si no hay ninguna."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ticket, simbolo, temporalidad, direccion, tendencia_apertura FROM posiciones_abiertas WHERE login = %s",
            (login,),
        ).fetchone()
    if row is None:
        return None
    return dict(zip(("ticket", "simbolo", "temporalidad", "direccion", "tendencia_apertura"), row))


# ── Escrituras de BD ──────────────────────────────────────────────────────────

def _registrar_apertura(login: int, ticket: int, simbolo: str, temporalidad: str,
                        direccion: str, lotes: float, precio: float,
                        tiene_sl: bool, sl_precio: float | None, tendencia: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO posiciones_abiertas
               (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada,
                tiene_sl, sl_precio, tendencia_apertura)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ticket, login, simbolo, temporalidad, direccion, lotes, precio,
             tiene_sl, sl_precio, tendencia),
        )


def _registrar_cierre(pos_db: dict, precio_cierre: float, profit: float, razon: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO historial_posiciones
               (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada,
                precio_cierre, profit_usd, razon_cierre, abierta_en)
               SELECT ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada,
                      %s, %s, %s, abierta_en
               FROM posiciones_abiertas WHERE ticket = %s""",
            (precio_cierre, profit, razon, pos_db["ticket"]),
        )
        conn.execute("DELETE FROM posiciones_abiertas WHERE ticket = %s", (pos_db["ticket"],))


def _log(login: int | None, simbolo: str | None, temporalidad: str | None,
         accion: str, motivo: str = "", lotes: float | None = None, detalle: dict | None = None) -> None:
    import json
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO log_coordinador (login, simbolo, temporalidad, accion, motivo, lotes, detalle)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (login, simbolo, temporalidad, accion, motivo, lotes,
             json.dumps(detalle) if detalle else None),
        )


# ── Peticiones manuales del usuario ──────────────────────────────────────────

def _marcar_peticion(id_: int, estado: str, motivo: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE peticiones_usuario SET estado=%s, motivo_error=%s, ejecutada_en=now() WHERE id=%s",
            (estado, motivo or None, id_),
        )


def _procesar_peticiones(creds: dict) -> None:
    with get_conn() as conn:
        peticiones = conn.execute(
            "SELECT id, simbolo, direccion, cuenta, lotes FROM peticiones_usuario WHERE estado='pendiente' ORDER BY creada_en"
        ).fetchall()

    if not peticiones:
        return

    for id_, simbolo, direccion, cuenta_pref, lotes_pref in peticiones:
        with get_conn() as conn:
            cuentas = conn.execute(
                "SELECT login, server, temporalidad, pct_riesgo FROM cuentas_demo WHERE simbolo=%s AND activa=true",
                (simbolo,),
            ).fetchall()

        if not cuentas:
            _marcar_peticion(id_, "error", f"sin cuenta activa para {simbolo}")
            _log(None, simbolo, None, "peticion_error", f"sin cuenta para {simbolo}")
            continue

        cuenta_row = next((c for c in cuentas if cuenta_pref is None or c[0] == cuenta_pref), cuentas[0])
        login, server, temporalidad, pct_riesgo = cuenta_row

        if login not in creds:
            _marcar_peticion(id_, "error", "sin credenciales en .env")
            continue

        try:
            login_cuenta(login, creds[login]["password"], creds[login]["server"])
        except ConexionXMError as e:
            _marcar_peticion(id_, "error", str(e))
            continue

        if direccion == "CERRAR":
            pos_db = _posicion_db(login)
            if pos_db:
                try:
                    r = cerrar_posicion(pos_db["ticket"])
                    _registrar_cierre(pos_db, r.get("price", 0.0), r.get("profit", 0.0), "usuario")
                    _log(login, simbolo, temporalidad, "cerrar", "peticion usuario",
                         detalle={"ticket": pos_db["ticket"]})
                    _marcar_peticion(id_, "ejecutada")
                except ConexionXMError as e:
                    _marcar_peticion(id_, "error", str(e))
            else:
                _marcar_peticion(id_, "error", "sin posicion abierta en esta cuenta")
            continue

        tipo_orden = {"BUY": mt5.ORDER_TYPE_BUY, "SELL": mt5.ORDER_TYPE_SELL}.get(direccion)
        if tipo_orden is None:
            _marcar_peticion(id_, "error", f"direccion invalida: {direccion}")
            continue

        if lotes_pref:
            lotes = float(lotes_pref)
        else:
            snap = _snapshot(simbolo, temporalidad)
            pips_stop = None
            if snap and snap.get("atr") and temporalidad not in TEMPORALIDADES_SWING:
                info = mt5.symbol_info(simbolo)
                if info:
                    pips_stop = float(snap["atr"]) / (info.point * 10)
            try:
                capital = info_cuenta()["balance"]
                lotes = calcular_lotes(simbolo, capital, temporalidad,
                                       pips_stop=pips_stop, pct_riesgo=float(pct_riesgo))
            except (ValueError, ConexionXMError) as e:
                _marcar_peticion(id_, "error", f"calcular_lotes: {e}")
                continue

        try:
            r = abrir_posicion(simbolo, tipo_orden, lotes, comentario="MTB-user")
            ticket = r["order"]
            tendencia_dir = "alcista" if tipo_orden == mt5.ORDER_TYPE_BUY else "bajista"
            _registrar_apertura(login, ticket, simbolo, temporalidad, direccion, lotes,
                                r.get("price", 0.0), False, None, tendencia_dir)
            _log(login, simbolo, temporalidad, "abrir", f"peticion usuario {direccion}",
                 lotes=lotes, detalle={"ticket": ticket})
            _marcar_peticion(id_, "ejecutada")
        except ConexionXMError as e:
            _marcar_peticion(id_, "error", str(e))


# ── Lógica por cuenta ─────────────────────────────────────────────────────────

_DIRECCION_OPUESTA = {"alcista": "bajista", "bajista": "alcista"}
_TIPO_MT5 = {"alcista": mt5.ORDER_TYPE_BUY, "bajista": mt5.ORDER_TYPE_SELL}
_DIRECCION_STR = {mt5.ORDER_TYPE_BUY: "BUY", mt5.ORDER_TYPE_SELL: "SELL"}


def _evaluar_cuenta(cuenta: dict, creds: dict) -> None:
    login = cuenta["login"]
    if login not in creds:
        _log(login, cuenta["simbolo"], cuenta["temporalidad"], "skip", "sin credenciales en .env")
        return

    try:
        login_cuenta(login, creds[login]["password"], creds[login]["server"])
    except ConexionXMError as e:
        _log(login, cuenta["simbolo"], cuenta["temporalidad"], "error", str(e))
        return

    snap = _snapshot(cuenta["simbolo"], cuenta["temporalidad"])
    if snap is None or snap["tendencia_actual"] is None:
        _log(login, cuenta["simbolo"], cuenta["temporalidad"], "skip", "sin señal SMC en snapshot")
        return

    tendencia = snap["tendencia_actual"]
    pos_db = _posicion_db(login)

    if pos_db is not None:
        _revisar_cierre(login, cuenta, pos_db, tendencia)
    else:
        _revisar_apertura(login, cuenta, snap, tendencia)


def _revisar_cierre(login: int, cuenta: dict, pos_db: dict, tendencia_actual: str) -> None:
    """Swing: cierra si CHoCH inverso. Scalping/Intraday: MT5 gestiona el SL solo."""
    if cuenta["temporalidad"] not in TEMPORALIDADES_SWING:
        # ponytail: el SL de scalping/intraday lo cierra MT5; solo sincronizamos si ya no existe
        pos_mt5 = mt5.positions_get(ticket=pos_db["ticket"])
        if not pos_mt5:
            # MT5 cerró la posición (SL hit) — mover a historial
            _log(login, pos_db["simbolo"], pos_db["temporalidad"], "cerrar", "sl_hit detectado post-facto")
            # ponytail: precio/profit aproximados; la fuente exacta es historial_operaciones()
            _registrar_cierre(pos_db, 0.0, 0.0, "sl_hit")
        return

    # Swing: verificar CHoCH inverso
    tendencia_apertura = pos_db.get("tendencia_apertura", "")
    if tendencia_actual == _DIRECCION_OPUESTA.get(tendencia_apertura):
        try:
            resultado = cerrar_posicion(pos_db["ticket"])
            precio_cierre = resultado.get("price", 0.0)
            profit = resultado.get("profit", 0.0)
            _registrar_cierre(pos_db, precio_cierre, profit, "choch_inverso")
            _log(login, pos_db["simbolo"], pos_db["temporalidad"], "cerrar",
                 f"CHoCH inverso: {tendencia_apertura} → {tendencia_actual}",
                 detalle={"ticket": pos_db["ticket"], "precio_cierre": precio_cierre})
        except ConexionXMError as e:
            _log(login, pos_db["simbolo"], pos_db["temporalidad"], "error", str(e))


def _revisar_apertura(login: int, cuenta: dict, snap: dict, tendencia: str) -> None:
    """Calcula lotes y abre posición si hay señal."""
    try:
        capital = info_cuenta()["balance"]
    except ConexionXMError as e:
        _log(login, cuenta["simbolo"], cuenta["temporalidad"], "error", f"info_cuenta: {e}")
        return

    simbolo     = cuenta["simbolo"]
    temporalidad = cuenta["temporalidad"]
    pct_riesgo  = float(cuenta["pct_riesgo"])

    # pips_stop: solo para scalping/intraday; usa ATR como proxy de distancia de stop
    pips_stop = None
    sl_precio = None
    if temporalidad not in TEMPORALIDADES_SWING:
        atr = snap.get("atr")
        if not atr:
            _log(login, simbolo, temporalidad, "skip", "ATR no disponible para calcular SL")
            return
        info = mt5.symbol_info(simbolo)
        if info is None:
            _log(login, simbolo, temporalidad, "skip", f"symbol_info devolvió None para {simbolo}")
            return
        pip_size = info.point * 10
        pips_stop = float(atr) / pip_size
        tick = mt5.symbol_info_tick(simbolo)
        precio_actual = tick.ask if tendencia == "alcista" else tick.bid
        sl_precio = precio_actual - float(atr) if tendencia == "alcista" else precio_actual + float(atr)

    try:
        lotes = calcular_lotes(simbolo, capital, temporalidad, pips_stop=pips_stop, pct_riesgo=pct_riesgo)
    except (ValueError, ConexionXMError) as e:
        _log(login, simbolo, temporalidad, "skip", f"calcular_lotes: {e}")
        return

    tipo_orden = _TIPO_MT5[tendencia]
    try:
        resultado = abrir_posicion(simbolo, tipo_orden, lotes, sl_precio=sl_precio,
                                   comentario=f"MTB-{temporalidad[:3]}")
        ticket = resultado["order"]
        precio_entrada = resultado.get("price", 0.0)
        tiene_sl = sl_precio is not None
        _registrar_apertura(login, ticket, simbolo, temporalidad,
                            _DIRECCION_STR[tipo_orden], lotes, precio_entrada,
                            tiene_sl, sl_precio, tendencia)
        _log(login, simbolo, temporalidad, "abrir",
             f"tendencia={tendencia} lotes={lotes}", lotes=lotes,
             detalle={"ticket": ticket, "precio": precio_entrada, "capital": capital})
    except ConexionXMError as e:
        _log(login, simbolo, temporalidad, "error", str(e))


# ── Punto de entrada ──────────────────────────────────────────────────────────

def ciclo() -> None:
    """Un ciclo de evaluación de todas las cuentas activas."""
    creds   = _credenciales_env()
    cuentas = _cuentas_activas()

    _log(None, None, None, "evaluar", f"inicio ciclo — {len(cuentas)} cuentas")
    conectar()
    try:
        _procesar_peticiones(creds)
        for c in cuentas:
            _evaluar_cuenta(c, creds)
    finally:
        desconectar()
    _log(None, None, None, "evaluar", "fin ciclo")


if __name__ == "__main__":
    ciclo()
