"""Cerebro de licencias — emisión, validación y revocación de tokens de bots.

Criterio (docs/manual_uso_master_trader.md, endurecido):
- 1 token = 1 cuenta MT5. El token se muestra UNA vez al emitirlo; en la BD
  solo vive su SHA-256 (si la BD se filtra, los tokens no sirven).
- DEMO solo opera en cuentas demo, REAL solo en cuentas reales (lo reporta
  el propio terminal: ACCOUNT_TRADE_MODE), VIP en ambas y con cualquier robot.
- Kill switch global: bloquea toda autorización nueva, clientes y coordinador.
- Falla cerrado: si algo no se puede verificar, no se autoriza.
- Cada rechazo queda en `licencias_eventos` (intentos de copia, tokens
  revocados, cuentas que no coinciden); los OK solo actualizan el último
  contacto del nodo para no llenar la tabla con cada vela.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .conexion import get_conn

TIPOS = {"demo", "real", "vip"}
ROBOT_TODOS = "*"
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I: se copia a mano en MT5


def generar_token() -> str:
    """MTB-XXXXX-XXXXX-XXXXX-XXXXX — 100 bits aleatorios."""
    grupos = ["".join(secrets.choice(_ALFABETO) for _ in range(5)) for _ in range(4)]
    return "MTB-" + "-".join(grupos)


def hash_token(token: str) -> str:
    # ponytail: SHA-256 simple basta — el token tiene 100 bits de entropía, no es una contraseña humana
    return hashlib.sha256(token.strip().upper().encode()).hexdigest()


def evaluar(lic: dict | None, cuenta: int, modo: str, robot: str, kill_switch: bool,
            ahora: datetime) -> tuple[bool, str]:
    """Decisión pura de autorización. `modo` = 'demo' | 'real' (del terminal)."""
    if lic is None:
        return False, "token_invalido"
    if kill_switch:
        return False, "kill_switch"
    if lic["revocada_en"] is not None:
        return False, "revocada"
    if lic["expira_en"] is not None and ahora >= lic["expira_en"]:
        return False, "expirada"
    if lic["cuenta"] != cuenta:
        return False, "cuenta_no_coincide"
    if lic["tipo"] != "vip" and lic["tipo"] != modo:
        return False, f"licencia_{lic['tipo']}_en_cuenta_{modo}"
    if lic["robot"] not in (ROBOT_TODOS, robot):
        return False, "robot_no_autorizado"
    return True, "ok"


_COLS = "id, token_prefijo, cliente, cuenta, tipo, robot, expira_en, revocada_en, creada_en, ultimo_contacto, ultima_ip, ultimo_resultado"


def _fila(cur_row, cols=_COLS) -> dict:
    return dict(zip([c.strip() for c in cols.split(",")], cur_row))


def kill_switch_activo(conn) -> bool:
    row = conn.execute("SELECT activo FROM kill_switch WHERE id").fetchone()
    return bool(row and row[0])


def verificar(token: str, cuenta: int, modo: str, robot: str, ip: str | None,
              detalle: dict | None = None) -> tuple[bool, str, dict | None]:
    """Valida un token contra la BD y registra el contacto. Devuelve (ok, motivo, licencia)."""
    import json

    ahora = datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM licencias WHERE token_hash = %s",
                           (hash_token(token),)).fetchone()
        lic = _fila(row) if row else None
        ok, motivo = evaluar(lic, cuenta, modo, robot, kill_switch_activo(conn), ahora)
        if lic:
            conn.execute(
                "UPDATE licencias SET ultimo_contacto = now(), ultima_ip = %s, ultimo_resultado = %s WHERE id = %s",
                (ip, motivo, lic["id"]),
            )
        if not ok:
            conn.execute(
                """INSERT INTO licencias_eventos (licencia_id, cuenta, resultado, motivo, ip, detalle)
                   VALUES (%s, %s, 'rechazado', %s, %s, %s)""",
                (lic["id"] if lic else None, cuenta, motivo, ip, json.dumps(detalle or {})),
            )
    return ok, motivo, lic


def emitir(cliente: str, cuenta: int, tipo: str, robot: str, vigencia_dias: int | None) -> tuple[str, dict]:
    """Crea la licencia. Devuelve (token_en_claro, licencia) — el token no se puede recuperar después."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo debe ser uno de {sorted(TIPOS)}")
    if not cliente.strip() or cuenta <= 0:
        raise ValueError("cliente y cuenta son obligatorios")
    if tipo == "vip":
        robot = ROBOT_TODOS
    elif not robot.strip():
        raise ValueError("robot obligatorio para licencias demo/real")
    expira = datetime.now(timezone.utc) + timedelta(days=vigencia_dias) if vigencia_dias else None

    token = generar_token()
    with get_conn() as conn:
        row = conn.execute(
            f"""INSERT INTO licencias (token_hash, token_prefijo, cliente, cuenta, tipo, robot, expira_en)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_COLS}""",
            (hash_token(token), token[:9], cliente.strip(), cuenta, tipo, robot.strip(), expira),
        ).fetchone()
        _evento_admin(conn, row[0], cuenta, "emitida")
    return token, _fila(row)


def revocar(licencia_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE licencias SET revocada_en = now() WHERE id = %s", (licencia_id,))
        _evento_admin(conn, licencia_id, None, "revocada")


def reautorizar(licencia_id: int, vigencia_dias: int | None = None) -> None:
    """Quita la revocación; con vigencia_dias además renueva el vencimiento desde hoy."""
    with get_conn() as conn:
        if vigencia_dias:
            conn.execute(
                "UPDATE licencias SET revocada_en = NULL, expira_en = now() + make_interval(days => %s) WHERE id = %s",
                (vigencia_dias, licencia_id),
            )
        else:
            conn.execute("UPDATE licencias SET revocada_en = NULL WHERE id = %s", (licencia_id,))
        _evento_admin(conn, licencia_id, None, "reautorizada")


def fijar_kill_switch(activo: bool, motivo: str | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE kill_switch SET activo = %s, motivo = %s, cambiado_en = now() WHERE id",
                     (activo, motivo))
        _evento_admin(conn, None, None, "kill_switch_on" if activo else "kill_switch_off", motivo)


def estado_panel(limite_eventos: int = 50) -> dict:
    with get_conn() as conn:
        lics = [_fila(r) for r in conn.execute(f"SELECT {_COLS} FROM licencias ORDER BY creada_en DESC").fetchall()]
        ks = conn.execute("SELECT activo, motivo, cambiado_en FROM kill_switch WHERE id").fetchone()
        eventos = conn.execute(
            """SELECT e.registrado_en, e.resultado, e.motivo, e.cuenta, e.ip, l.cliente, l.token_prefijo
               FROM licencias_eventos e LEFT JOIN licencias l ON l.id = e.licencia_id
               ORDER BY e.registrado_en DESC LIMIT %s""",
            (limite_eventos,),
        ).fetchall()
    cols_ev = "registrado_en, resultado, motivo, cuenta, ip, cliente, token_prefijo"
    return {
        "licencias": lics,
        "kill_switch": {"activo": bool(ks[0]), "motivo": ks[1], "cambiado_en": ks[2]} if ks else {"activo": False},
        "eventos": [_fila(r, cols_ev) for r in eventos],
    }


def _evento_admin(conn, licencia_id, cuenta, motivo, detalle_txt=None) -> None:
    import json
    conn.execute(
        """INSERT INTO licencias_eventos (licencia_id, cuenta, resultado, motivo, detalle)
           VALUES (%s, %s, 'admin', %s, %s)""",
        (licencia_id, cuenta, motivo, json.dumps({"nota": detalle_txt} if detalle_txt else {})),
    )


def demo() -> None:
    """Self-check de la decisión pura y del formato de token (sin BD)."""
    ahora = datetime(2026, 9, 25, tzinfo=timezone.utc)
    base = {"id": 1, "cuenta": 318680674, "tipo": "real", "robot": "seguidor-smc",
            "revocada_en": None, "expira_en": ahora + timedelta(days=30)}

    assert evaluar(base, 318680674, "real", "seguidor-smc", False, ahora) == (True, "ok")
    assert evaluar(None, 318680674, "real", "seguidor-smc", False, ahora)[1] == "token_invalido"
    assert evaluar(base, 318680674, "real", "seguidor-smc", True, ahora)[1] == "kill_switch"
    assert evaluar({**base, "revocada_en": ahora}, 318680674, "real", "seguidor-smc", False, ahora)[1] == "revocada"
    assert evaluar({**base, "expira_en": ahora}, 318680674, "real", "seguidor-smc", False, ahora)[1] == "expirada"
    assert evaluar({**base, "expira_en": None}, 318680674, "real", "seguidor-smc", False, ahora)[0]
    assert evaluar(base, 999, "real", "seguidor-smc", False, ahora)[1] == "cuenta_no_coincide"
    assert evaluar(base, 318680674, "demo", "seguidor-smc", False, ahora)[1] == "licencia_real_en_cuenta_demo"
    assert evaluar({**base, "tipo": "demo"}, 318680674, "real", "seguidor-smc", False, ahora)[1] == "licencia_demo_en_cuenta_real"
    assert evaluar(base, 318680674, "real", "otro-bot", False, ahora)[1] == "robot_no_autorizado"
    vip = {**base, "tipo": "vip", "robot": ROBOT_TODOS}
    assert evaluar(vip, 318680674, "demo", "otro-bot", False, ahora) == (True, "ok")
    assert evaluar(vip, 111, "demo", "otro-bot", False, ahora)[1] == "cuenta_no_coincide"  # VIP también 1 cuenta

    t = generar_token()
    assert len(t) == 27 and t.startswith("MTB-") and t != generar_token()
    assert hash_token(t) == hash_token("  " + t.lower() + " ")  # tolera espacios/minúsculas al pegar
    print("licencias.demo() OK")


if __name__ == "__main__":
    demo()
