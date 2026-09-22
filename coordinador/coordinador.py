"""Agente Coordinador Multi-Cuenta del Master Trader (MexTradeBot).

Estructura modular:
- ciclo(): Lee cuentas_demo del Postgres e itera las 5 cuentas.
- _evaluar_cuenta(): login_cuenta() -> revisa smc_snapshot -> decide.
- _revisar_apertura(): Tendencia activa + sin posición -> calcular_lotes() + abrir_posicion() -> registra en posiciones_abiertas.
- _revisar_cierre(): Sincroniza posiciones de MT5 (auto-detecta trades del EA/manual) + Swing (CHoCH inverso -> cerrar_posicion()).
- _log(): Cada decisión (abrir/cerrar/skip/error/alerta_capital) -> log_coordinador.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from dotenv import load_dotenv

from conectividad.xm import (
    ConexionXMError,
    conectar,
    desconectar,
    info_cuenta,
    historial_operaciones,
    posiciones_abiertas as xm_posiciones_abiertas,
    abrir_posicion as xm_abrir_posicion,
    cerrar_posicion as xm_cerrar_posicion,
    login_cuenta as xm_login_cuenta,
)
from coordinador.sizing import calcular_lotaje_swing, evaluar_capital_intraday
from persistencia.conexion import get_conn

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _log(
    conn,
    login: Optional[int],
    simbolo: Optional[str],
    temporalidad: Optional[str],
    accion: str,
    motivo: Optional[str] = None,
    lotes: Optional[float] = None,
    detalle: Optional[Dict[str, Any]] = None,
) -> None:
    """Registra una entrada de auditoría en la tabla `log_coordinador`."""
    detalle_json = json.dumps(detalle) if detalle else None
    conn.execute(
        """INSERT INTO log_coordinador (login, simbolo, temporalidad, accion, motivo, lotes, detalle)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (login, simbolo, temporalidad, accion, motivo, lotes, detalle_json),
    )


def _cargar_cuentas_demo(conn) -> List[Dict[str, Any]]:
    """Lee las cuentas demo configuradas en la BD Postgres (`cuentas_demo`)."""
    rows = conn.execute(
        """SELECT login, server, nombre, simbolo, temporalidad, pct_riesgo, activa
           FROM cuentas_demo WHERE activa = true
           ORDER BY login"""
    ).fetchall()

    cuentas = []
    for r in rows:
        login = r[0]
        pw = os.environ.get(f"XM_PASSWORD_{login}") or os.environ.get("XM_PASSWORD")
        cuentas.append({
            "login": login,
            "server": r[1],
            "nombre": r[2],
            "simbolo": r[3],
            "temporalidad": r[4],
            "pct_riesgo": float(r[5]),
            "password": pw,
        })
    return cuentas


def _obtener_snapshot_smc(conn, simbolo: str, temporalidad: str) -> Optional[Dict[str, Any]]:
    """Obtiene el último snapshot SMC guardado en Postgres (`smc_snapshot`)."""
    row = conn.execute(
        """SELECT ultimo_timestamp, estructura_smc, tendencia_actual, atr
           FROM smc_snapshot
           WHERE simbolo = %s AND temporalidad = %s""",
        (simbolo, temporalidad),
    ).fetchone()

    if not row:
        return None

    return {
        "ultimo_timestamp": row[0],
        "estructura_smc": row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {}),
        "tendencia_actual": row[2],
        "atr": float(row[3]) if row[3] else 0.0,
    }


def _revisar_cierre(conn, cuenta: Dict[str, Any], mt5_posiciones: List[dict], snapshot: Optional[dict]) -> None:
    """Sincroniza posiciones MT5 ↔ Postgres y revisa reglas de salida.

    Auto-detecta operaciones abiertas por el EA nativo o manuales en MT5 y las inserta en `posiciones_abiertas`.
    Para Swing Trading: evalúa CHoCH inverso -> `cerrar_posicion()`.
    """
    login = cuenta["login"]
    simbolo = cuenta["simbolo"]
    temp = cuenta["temporalidad"]

    # 1. Leer estado actual de posiciones en la BD
    pos_bd_rows = conn.execute(
        """SELECT ticket, simbolo, temporalidad, direccion, lotes, precio_entrada, abierta_en
           FROM posiciones_abiertas WHERE login = %s""",
        (login,),
    ).fetchall()

    tickets_mt5 = {p["ticket"]: p for p in mt5_posiciones}
    tickets_bd = {r[0]: r for r in pos_bd_rows}

    # A) Posiciones que estaban en BD pero ya cerraron en MT5 -> mover a historial_posiciones
    for ticket, r in tickets_bd.items():
        if ticket not in tickets_mt5:
            deals = historial_operaciones(dias=7)
            deal_cierre = next((d for d in deals if d.get("position_id") == ticket and d.get("entry") == mt5.DEAL_ENTRY_OUT), None)

            precio_cierre = float(deal_cierre["price"]) if deal_cierre else float(r[5])
            profit_usd = float(deal_cierre["profit"] + deal_cierre.get("swap", 0.0) + deal_cierre.get("commission", 0.0)) if deal_cierre else 0.0
            razon_cierre = "SL / TP tocado (MT5)" if deal_cierre else "Cierre en MT5"

            conn.execute(
                """INSERT INTO historial_posiciones
                   (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada, precio_cierre, profit_usd, razon_cierre, abierta_en)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ticket, login, r[1], r[2], r[3], float(r[4]), float(r[5]), precio_cierre, profit_usd, razon_cierre, r[6]),
            )
            conn.execute("DELETE FROM posiciones_abiertas WHERE ticket = %s", (ticket,))
            _log(conn, login, r[1], r[2], "CIERRE_POSICION", razon_cierre, float(r[4]), {"profit_usd": profit_usd})
            logging.info(f"[{login}] Posición cerrada ticket #{ticket} {r[1]} P&L: ${profit_usd:.2f}")

    # B) Posiciones vivas en MT5 que NO estaban en BD aún (EA nativo o manual) -> insertar en posiciones_abiertas
    for ticket, p in tickets_mt5.items():
        if ticket not in tickets_bd:
            direccion_str = "BUY" if p["type"] == mt5.POSITION_TYPE_BUY else "SELL"
            t_sec = p.get("time", datetime.now(timezone.utc).timestamp())
            abierta_dt = datetime.fromtimestamp(t_sec, tz=timezone.utc)
            conn.execute(
                """INSERT INTO posiciones_abiertas
                   (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada, tiene_sl, sl_precio, abierta_en)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (ticket) DO NOTHING""",
                (ticket, login, p["symbol"], temp, direccion_str, float(p["volume"]), float(p["price_open"]), p.get("sl", 0) > 0, float(p.get("sl", 0)), abierta_dt),
            )
            _log(conn, login, p["symbol"], temp, "SINCRONIZAR_POSICION_MT5", "Detectada posición viva en MT5", float(p["volume"]), {"ticket": ticket})
            logging.info(f"[{login}] Auto-sincronizada posición viva en MT5 ticket #{ticket} {p['symbol']} {direccion_str}")

    # 2. Regla de Oro Swing: Salida por CHoCH inverso
    if "Swing" in temp and snapshot:
        pos_activa = next((p for p in mt5_posiciones if p["symbol"] == simbolo), None)
        if pos_activa:
            ticket = pos_activa["ticket"]
            dir_actual = "BUY" if pos_activa["type"] == mt5.POSITION_TYPE_BUY else "SELL"
            tendencia_actual = snapshot.get("tendencia_actual")

            se_revirtio = (dir_actual == "BUY" and tendencia_actual == "venta") or (dir_actual == "SELL" and tendencia_actual == "compra")

            if se_revirtio:
                logging.info(f"[{login}] CHoCH Inverso detectado en Swing ({dir_actual} vs {tendencia_actual}). Cerrando ticket #{ticket}...")
                xm_cerrar_posicion(ticket)
                conn.execute("DELETE FROM posiciones_abiertas WHERE ticket = %s", (ticket,))
                _log(conn, login, simbolo, temp, "EXIT_SWING_CHOCH", f"Cambio de tendencia a {tendencia_actual}", float(pos_activa["volume"]))


def _revisar_apertura(conn, cuenta: Dict[str, Any], balance: float, mt5_posiciones: List[dict], snapshot: Optional[dict]) -> None:
    """Tendencia activa + sin posición -> calcular_lotes() + abrir_posicion() -> registra en posiciones_abiertas."""
    login = cuenta["login"]
    simbolo = cuenta["simbolo"]
    temp = cuenta["temporalidad"]
    pct_riesgo = cuenta["pct_riesgo"]

    pos_activa = next((p for p in mt5_posiciones if p["symbol"] == simbolo), None)
    if pos_activa:
        return

    if not snapshot:
        _log(conn, login, simbolo, temp, "SKIP_SIN_SNAPSHOT", "No hay datos de snapshot SMC")
        return

    tendencia = snapshot.get("tendencia_actual")
    if tendencia not in ("compra", "venta"):
        _log(conn, login, simbolo, temp, "SKIP_SIN_TENDENCIA", f"Tendencia no definida: {tendencia}")
        return

    tick = mt5.symbol_info_tick(simbolo)
    if not tick:
        return

    if "Swing" in temp:
        # Swing Trade: SIN SL en MT5 (Regla de Oro Opción A)
        precio_entrada = tick.ask if tendencia == "compra" else tick.bid
        lotes = calcular_lotaje_swing(simbolo, balance, precio_entrada, pct_riesgo=pct_riesgo)

        tipo_orden = mt5.ORDER_TYPE_BUY if tendencia == "compra" else mt5.ORDER_TYPE_SELL
        dir_str = "BUY" if tendencia == "compra" else "SELL"

        logging.info(f"[{login}] Abriendo Swing {dir_str} en {simbolo} con {lotes} lotes (SIN SL en MT5)...")
        res = xm_abrir_posicion(simbolo, tipo_orden, lotes, sl_precio=None, tp_precio=None, comentario="MTB_Swing")
        ticket_nuevo = res["order"]

        conn.execute(
            """INSERT INTO posiciones_abiertas
               (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada, tiene_sl, sl_precio, tendencia_apertura)
               VALUES (%s, %s, %s, %s, %s, %s, %s, false, null, %s)""",
            (ticket_nuevo, login, simbolo, temp, dir_str, lotes, precio_entrada, tendencia),
        )
        _log(conn, login, simbolo, temp, "ABRIR_SWING", f"Entrada a favor de {tendencia}", lotes, {"ticket": ticket_nuevo})

    else:
        # Intraday / Scalping: Con SL en la estructura y TP a 2R
        precio_entrada = tick.ask if tendencia == "compra" else tick.bid
        distancia = snapshot.get("atr", 0.0020) or 0.0020
        sl_precio = precio_entrada - distancia if tendencia == "compra" else precio_entrada + distancia
        tp_precio = precio_entrada + (distancia * 2.0) if tendencia == "compra" else precio_entrada - (distancia * 2.0)

        lotes, es_viable, msg, cap_min = evaluar_capital_intraday(simbolo, balance, precio_entrada, sl_precio, pct_riesgo=pct_riesgo)

        if not es_viable:
            _log(conn, login, simbolo, temp, "ALERTA_CAPITAL_INSUFICIENTE", msg, lotes, {"capital_minimo": cap_min, "balance": balance})
            logging.warning(f"[{login}] {msg}")
            return

        tipo_orden = mt5.ORDER_TYPE_BUY if tendencia == "compra" else mt5.ORDER_TYPE_SELL
        dir_str = "BUY" if tendencia == "compra" else "SELL"

        logging.info(f"[{login}] Abriendo Intraday {dir_str} en {simbolo} {lotes} lotes | SL: {sl_precio:.5f} | TP: {tp_precio:.5f}")
        res = xm_abrir_posicion(simbolo, tipo_orden, lotes, sl_precio=sl_precio, tp_precio=tp_precio, comentario="MTB_Intraday")
        ticket_nuevo = res["order"]

        conn.execute(
            """INSERT INTO posiciones_abiertas
               (ticket, login, simbolo, temporalidad, direccion, lotes, precio_entrada, tiene_sl, sl_precio, tendencia_apertura)
               VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s)""",
            (ticket_nuevo, login, simbolo, temp, dir_str, lotes, precio_entrada, sl_precio, tendencia),
        )
        _log(conn, login, simbolo, temp, "ABRIR_INTRADAY", f"Setup FVG a favor de {tendencia}", lotes, {"ticket": ticket_nuevo, "sl": sl_precio, "tp": tp_precio})


def _evaluar_cuenta(conn, cuenta: Dict[str, Any]) -> None:
    """login_cuenta() -> revisa smc_snapshot -> decide (_revisar_cierre y _revisar_apertura)."""
    login = cuenta["login"]
    pw = cuenta.get("password")
    server = cuenta["server"]
    simbolo = cuenta["simbolo"]
    temp = cuenta["temporalidad"]

    logging.info(f"--- Evaluando cuenta {login} ({cuenta['nombre']}) ---")
    try:
        xm_login_cuenta(login, pw, server)
        info = info_cuenta()
        balance = info["balance"]
        mt5_pos = xm_posiciones_abiertas()

        snapshot = _obtener_snapshot_smc(conn, simbolo, temp)

        # 1. Revisar cierres y auto-sincronizar posiciones MT5
        _revisar_cierre(conn, cuenta, mt5_pos, snapshot)

        # 2. Revisar aperturas si no hay posición activa
        _revisar_apertura(conn, cuenta, balance, mt5_pos, snapshot)

    except ConexionXMError as e:
        msg_err = f"Error en cuenta {login}@{server}: {e}"
        logging.error(msg_err)
        _log(conn, login, simbolo, temp, "ERROR_CONEXION_CUENTA", str(e))


def ciclo() -> None:
    """Lee cuentas_demo del Postgres, itera las 5 cuentas."""
    logging.info("=== INICIANDO CICLO DEL COORDINADOR MASTER TRADER ===")
    conectar()

    try:
        with get_conn() as conn:
            cuentas = _cargar_cuentas_demo(conn)
            logging.info(f"Cuentas demo encontradas en BD: {len(cuentas)}")

            for c in cuentas:
                _evaluar_cuenta(conn, c)

        logging.info("=== CICLO DEL COORDINADOR FINALIZADO CON ÉXITO ===")
    finally:
        desconectar()


# Alias para compatibilidad
ejecutar_ciclo_coordinacion = ciclo

if __name__ == "__main__":
    ciclo()
