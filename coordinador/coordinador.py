"""Agente Coordinador Multi-Cuenta del Master Trader (MexTradeBot).

Estructura modular:
- ciclo(): Lee cuentas_demo del Postgres e itera las 5 cuentas.
- _evaluar_cuenta(): login_cuenta() -> revisa smc_snapshot -> decide.
- Ya NO abre operaciones (29 sep 2026): cada cuenta corre el EA de los alumnos en su propia terminal.
- _revisar_cierre(): Sincroniza posiciones de MT5 (auto-detecta trades del EA/manual) + Swing (CHoCH inverso -> cerrar_posicion()).
- _log(): Cada decisión (abrir/cerrar/skip/error/alerta_capital) -> log_coordinador.
- Latido: cada ciclo escribe CICLO_OK (o ERROR_CICLO si MT5 no responde) en log_coordinador,
  y cada cuenta se guarda en su propia transacción. Sin esto el coordinador podía quedarse
  mudo días enteros sin dejar rastro en Postgres (ver PR "coordinador-sync").

Autoverificación (sin MT5 ni Postgres): `python -m coordinador.coordinador`.
Un ciclo real en el VPS: `python run_coordinador.py` (tarea MTB-Coordinador, cada 15 min).
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

from conectividad.historico import resolver_temporalidad
from conectividad.riesgo import es_swing

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ponytail: conectividad.xm y persistencia se importan tarde (xm necesita MetaTrader5, que solo
# existe en el VPS Windows, y persistencia aplica migraciones al importarse). Así el módulo se
# importa y se autoverifica en cualquier máquina; demo() inyecta dobles de prueba aquí.
_XM = None
_GET_CONN = None
_KILL = None


def _xm():
    """Módulo conectividad.xm (import tardío; `_xm().mt5` son las constantes de MetaTrader5)."""
    global _XM
    if _XM is None:
        from conectividad import xm
        _XM = xm
    return _XM


def _get_conn():
    """persistencia.conexion.get_conn (import tardío)."""
    global _GET_CONN
    if _GET_CONN is None:
        from persistencia.conexion import get_conn
        _GET_CONN = get_conn
    return _GET_CONN


def _kill_switch_activo(conn) -> bool:
    """persistencia.licencias.kill_switch_activo (import tardío: persistencia aplica migraciones)."""
    global _KILL
    if _KILL is None:
        from persistencia.licencias import kill_switch_activo
        _KILL = kill_switch_activo
    return _KILL(conn)


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


def password_de(login: int, env=os.environ) -> Optional[str]:
    """Contraseña XM de una cuenta. Acepta las dos convenciones de .env que existen:
    XM_PASSWORD_<login>, o la del VPS: XM_LOGIN{,_2.._9} + XM_PASSWORD{,_2.._9} por posición.
    """
    if env.get(f"XM_PASSWORD_{login}"):
        return env[f"XM_PASSWORD_{login}"]
    for sufijo in [""] + [f"_{i}" for i in range(2, 10)]:
        if str(env.get(f"XM_LOGIN{sufijo}", "")).strip() == str(login):
            return env.get(f"XM_PASSWORD{sufijo}")
    return env.get("XM_PASSWORD")


def _canonica(temporalidad: str) -> str:
    """Resuelve alias viejos ('Scalping', 'Swing (H)'...) al nombre canonico; desconocidos pasan tal cual."""
    try:
        return resolver_temporalidad(temporalidad)
    except ValueError:
        return temporalidad


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
        pw = password_de(login)
        cuentas.append({
            "login": login,
            "server": r[1],
            "nombre": r[2],
            "simbolo": r[3],
            "temporalidad": _canonica(r[4]),
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
            deals = _xm().historial_operaciones(dias=7)
            deal_cierre = next((d for d in deals if d.get("position_id") == ticket and d.get("entry") == _xm().mt5.DEAL_ENTRY_OUT), None)

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
            direccion_str = "BUY" if p["type"] == _xm().mt5.POSITION_TYPE_BUY else "SELL"
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
    if es_swing(temp) and snapshot:
        pos_activa = next((p for p in mt5_posiciones if p["symbol"] == simbolo), None)
        if pos_activa:
            ticket = pos_activa["ticket"]
            dir_actual = "BUY" if pos_activa["type"] == _xm().mt5.POSITION_TYPE_BUY else "SELL"
            tendencia_actual = snapshot.get("tendencia_actual")

            se_revirtio = (dir_actual == "BUY" and tendencia_actual == "venta") or (dir_actual == "SELL" and tendencia_actual == "compra")

            if se_revirtio:
                logging.info(f"[{login}] CHoCH Inverso detectado en Swing ({dir_actual} vs {tendencia_actual}). Cerrando ticket #{ticket}...")
                _xm().cerrar_posicion(ticket)
                conn.execute("DELETE FROM posiciones_abiertas WHERE ticket = %s", (ticket,))
                _log(conn, login, simbolo, temp, "EXIT_SWING_CHOCH", f"Cambio de tendencia a {tendencia_actual}", float(pos_activa["volume"]))


def _evaluar_cuenta(conn, cuenta: Dict[str, Any], kill_switch: bool = False) -> None:
    """login_cuenta() -> revisa smc_snapshot -> decide (_revisar_cierre y _revisar_apertura)."""
    login = cuenta["login"]
    pw = cuenta.get("password")
    server = cuenta["server"]
    simbolo = cuenta["simbolo"]
    temp = cuenta["temporalidad"]

    logging.info(f"--- Evaluando cuenta {login} ({cuenta['nombre']}) ---")
    try:
        _xm().login_cuenta(login, pw, server)
        info = _xm().info_cuenta()
        balance = info["balance"]
        mt5_pos = _xm().posiciones_abiertas()

        snapshot = _obtener_snapshot_smc(conn, simbolo, temp)

        # 1. Revisar cierres y auto-sincronizar posiciones MT5
        _revisar_cierre(conn, cuenta, mt5_pos, snapshot)

        # 2. Revisar aperturas si no hay posición activa — el kill switch global solo
        #    bloquea entradas nuevas; los cierres de arriba siguen (reducen riesgo).
        # ponytail: desde 29 sep 2026 las cuentas del Master Trader operan con el MISMO EA
        # de los alumnos (una terminal MT5 por cuenta); el coordinador ya no abre para no
        # duplicar entradas. La apertura vieja (a mercado, SL 1 ATR) está en git 8df405b.

    except _xm().ConexionXMError as e:
        msg_err = f"Error en cuenta {login}@{server}: {e}"
        logging.error(msg_err)
        _log(conn, login, simbolo, temp, "ERROR_CONEXION_CUENTA", str(e))
    except Exception as e:  # cualquier otro fallo de esta cuenta: queda registrado y el ciclo sigue
        logging.exception(f"Fallo inesperado en cuenta {login}@{server}")
        _log(conn, login, simbolo, temp, "ERROR_CUENTA", f"{type(e).__name__}: {e}")


def _latido(accion: str, motivo: str) -> None:
    """Escribe el latido del ciclo (CICLO_OK / ERROR_CICLO) en su propia transacción.

    Es la única señal de que el coordinador del VPS sigue vivo: desde que dejó de abrir
    operaciones (29 sep 2026) puede pasar días sin nada que registrar, y así "no escribe"
    dejaba de distinguirse de "la tarea MTB-Coordinador está caída". Nunca tumba el ciclo.
    """
    try:
        with _get_conn()() as conn:
            _log(conn, None, None, None, accion, motivo)
    except Exception as e:
        logging.error(f"No se pudo registrar el latido {accion}: {e}")


def ciclo() -> None:
    """Lee cuentas_demo del Postgres, itera las 5 cuentas."""
    logging.info("=== INICIANDO CICLO DEL COORDINADOR MASTER TRADER ===")
    try:
        _xm().conectar()
    except _xm().ConexionXMError as e:
        # Terminal MT5 cerrada o credenciales del .env incompletas: antes moría aquí sin
        # dejar nada en Postgres. Ahora el motivo exacto queda en log_coordinador.
        _latido("ERROR_CICLO", f"MT5 no disponible: {e}")
        raise

    try:
        with _get_conn()() as conn:
            cuentas = _cargar_cuentas_demo(conn)
            kill = _kill_switch_activo(conn)
        logging.info(f"Cuentas demo encontradas en BD: {len(cuentas)}")
        if kill:
            logging.warning("KILL SWITCH GLOBAL ACTIVO — solo cierres, sin aperturas nuevas")

        # Una transacción por cuenta: un fallo en la última ya no borra los cierres y
        # sincronizaciones que las anteriores acababan de guardar.
        for c in cuentas:
            with _get_conn()() as conn:
                _evaluar_cuenta(conn, c, kill)

        _latido("CICLO_OK", f"{len(cuentas)} cuentas revisadas")
        logging.info("=== CICLO DEL COORDINADOR FINALIZADO CON ÉXITO ===")
    except Exception as e:
        _latido("ERROR_CICLO", f"{type(e).__name__}: {e}")
        raise
    finally:
        _xm().desconectar()


# Alias para compatibilidad
ejecutar_ciclo_coordinacion = ciclo


class _ConnFalso:
    """Conexión de prueba: guarda lo insertado y solo "confirma" al salir del with sin error."""

    def __init__(self, registro: list, filas: Dict[str, list]):
        self._registro, self._filas, self._pendiente = registro, filas, []

    def execute(self, sql, params=None):
        self._pendiente.append((" ".join(sql.split())[:60], params))
        clave = next((k for k in self._filas if k in sql), None)
        self._resultado = self._filas.get(clave, [])
        return self

    def fetchall(self):
        return self._resultado

    def fetchone(self):
        return self._resultado[0] if self._resultado else None

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_):
        if tipo is None:  # commit
            self._registro.extend(self._pendiente)
        self._pendiente = []
        return False


def demo() -> None:
    """Autoverificación sin MT5 ni Postgres: latido, aislamiento por cuenta y ERROR_CICLO."""
    global _XM, _GET_CONN, _KILL
    xm_previo, conn_previo, kill_previo = _XM, _GET_CONN, _KILL
    _KILL = lambda conn: False

    class _MT5Falso:
        DEAL_ENTRY_OUT = 1
        POSITION_TYPE_BUY = 0

    class _XMFalso:
        class ConexionXMError(RuntimeError):
            pass

        mt5 = _MT5Falso()

        def __init__(self, falla_conectar=False, falla_login=None):
            self.falla_conectar, self.falla_login, self.desconectado = falla_conectar, falla_login, False

        def conectar(self):
            if self.falla_conectar:
                raise self.ConexionXMError("No se pudo conectar a MT5: [-10003] IPC initialize failed")

        def desconectar(self):
            self.desconectado = True

        def login_cuenta(self, login, pw, server):
            if self.falla_login == login:
                raise ValueError("terminal ocupada")

        def info_cuenta(self):
            return {"balance": 10000.0}

        def posiciones_abiertas(self):
            return []

        def historial_operaciones(self, dias=7):
            return []

        def cerrar_posicion(self, ticket):
            return {}

    cuentas_bd = [(108460538, "XMGlobal-MT5 5", "ORO SWING", "GOLD", "Swing (S)", 0.02, True),
                  (336903102, "XMGlobal-MT5 9", "ORO INTRADAY", "GOLD", "Intraday 1H", 0.02, True)]

    def _conexiones(registro):
        filas = {"cuentas_demo": cuentas_bd, "kill_switch": [(False,)], "smc_snapshot": [], "posiciones_abiertas": []}
        return lambda: _ConnFalso(registro, filas)

    try:
        # 1. Ciclo sano: cada cuenta en su transacción + un latido CICLO_OK al final.
        registro: list = []
        _XM, _GET_CONN = _XMFalso(), _conexiones(registro)
        ciclo()
        acciones = [p[3] for _, p in registro if p and len(p) == 7]
        assert acciones == ["CICLO_OK"], acciones
        assert _XM.desconectado

        # 2. Una cuenta revienta con un error que no es de conexión: queda ERROR_CUENTA y el
        #    ciclo termina igual (antes la excepción tumbaba el ciclo y borraba lo demás).
        registro = []
        _XM, _GET_CONN = _XMFalso(falla_login=108460538), _conexiones(registro)
        ciclo()
        acciones = [p[3] for _, p in registro if p and len(p) == 7]
        assert acciones == ["ERROR_CUENTA", "CICLO_OK"], acciones
        assert "terminal ocupada" in str(registro)

        # 3. MT5 caído: antes el ciclo moría en silencio; ahora deja ERROR_CICLO con el motivo.
        registro = []
        _XM, _GET_CONN = _XMFalso(falla_conectar=True), _conexiones(registro)
        try:
            ciclo()
            raise AssertionError("ciclo() debía propagar el fallo de MT5")
        except _XMFalso.ConexionXMError:
            pass
        acciones = [p[3] for _, p in registro if p and len(p) == 7]
        assert acciones == ["ERROR_CICLO"], acciones
        assert "IPC initialize failed" in str(registro)

        # 4. Un latido que no puede escribir en Postgres no tumba el ciclo.
        def _conn_rota():
            raise RuntimeError("Postgres inalcanzable")

        _GET_CONN = lambda: _conn_rota()
        _latido("CICLO_OK", "sin BD")
    finally:
        _XM, _GET_CONN, _KILL = xm_previo, conn_previo, kill_previo

    print("coordinador.coordinador.demo() OK — latido, aislamiento por cuenta y ERROR_CICLO")


if __name__ == "__main__":
    demo()
