"""Endpoints del cerebro de licencias — despachados desde api/analizar.py.

  POST /api/v1/auth        (bot cliente)  Authorization: Bearer <token>
       {"account": 318680674, "mode": "real"|"demo", "robot": "seguidor-smc",
        "symbol": "XAUUSD", "type": "buy"|"sell",
        # opcionales — si vienen, el servidor calcula los lotes con la regla única
        # (conectividad.riesgo.dimensionar) y el bot NO calcula lotes por su cuenta:
        "balance": 1000, "riesgo_pct": 1.0, "entrada": F, "stop": F,
        "tick_size": F, "tick_value": F, "vol_min": F, "vol_max": F, "vol_step": F}
       → 200 {"autorizado": true, "lotes": 0.05, "viable": true, "motivo": "ok", "expira_en": ...}
       → 403 {"autorizado": false, "motivo": "revocada"|"expirada"|"kill_switch"|...}

  GET  /api/v1/licencias   (admin)  X-Admin-Key: <MTB_ADMIN_KEY>  → licencias + kill switch + eventos
  POST /api/v1/licencias   (admin)  X-Admin-Key: <MTB_ADMIN_KEY>
       {"accion": "emitir", "cliente", "cuenta", "tipo": "demo|real|vip", "robot", "vigencia_dias": 30|null}
       {"accion": "revocar", "id"} | {"accion": "reautorizar", "id", "vigencia_dias"?}
       {"accion": "kill_switch", "activo": true|false, "motivo"?}

  /api/setups — ver gate_setups(): con Bearer valida la licencia; sin Bearer
  exige X-MTB-Service-Key (n8n, panel) una vez que MTB_SERVICE_KEY existe en Vercel.

Todo falla cerrado: sin BD, sin MTB_ADMIN_KEY o con datos incompletos → no se autoriza.
"""

import hmac
import os

from conectividad.riesgo import dimensionar

_CAMPOS_LOTES = ("balance", "riesgo_pct", "entrada", "stop", "tick_size", "tick_value", "vol_min", "vol_max", "vol_step")


def _h(headers: dict, nombre: str) -> str:
    nombre = nombre.lower()
    return next((v for k, v in headers.items() if k.lower() == nombre), "") or ""


def _bearer(headers: dict) -> str:
    valor = _h(headers, "Authorization")
    return valor[7:].strip() if valor.lower().startswith("bearer ") else ""


def _ip(headers: dict) -> str | None:
    return (_h(headers, "x-forwarded-for").split(",")[0].strip()) or None


def _clave_ok(recibida: str, env: str) -> bool:
    esperada = os.environ.get(env, "")
    return bool(esperada) and hmac.compare_digest(recibida.encode(), esperada.encode())


def _verificar(token: str, cuenta, modo, robot, ip, detalle) -> tuple[bool, str, dict | None]:
    from persistencia import licencias  # import tardío: aplica migraciones solo cuando se usa

    return licencias.verificar(token, cuenta, modo, robot, ip, detalle)


def _identidad(datos: dict) -> tuple[int, str, str]:
    cuenta = int(datos.get("account") or datos.get("cuenta") or 0)
    modo = str(datos.get("mode") or datos.get("modo") or "").lower()
    robot = str(datos.get("robot") or "")
    if cuenta <= 0 or modo not in ("demo", "real") or not robot:
        raise ValueError("faltan account, mode (demo|real) o robot")
    return cuenta, modo, robot


def procesar_auth(datos: dict, headers: dict) -> tuple[int, dict]:
    token = _bearer(headers)
    if not token:
        return 401, {"autorizado": False, "motivo": "falta_token"}
    try:
        cuenta, modo, robot = _identidad(datos)
    except (TypeError, ValueError) as e:
        return 400, {"autorizado": False, "motivo": str(e)}

    detalle = {"symbol": datos.get("symbol"), "type": datos.get("type")}
    try:
        ok, motivo, lic = _verificar(token, cuenta, modo, robot, _ip(headers), detalle)
    except Exception as e:  # BD caída o sin DATABASE_URL → falla cerrado
        return 503, {"autorizado": False, "motivo": f"cerebro_no_disponible: {type(e).__name__}"}
    if not ok:
        return 403, {"autorizado": False, "motivo": motivo}

    cuerpo = {"autorizado": True, "motivo": "ok", "expira_en": lic["expira_en"]}
    if all(datos.get(c) is not None for c in _CAMPOS_LOTES):
        try:
            d = {c: float(datos[c]) for c in _CAMPOS_LOTES}
            if d["tick_size"] <= 0:
                raise ValueError("tick_size inválido")
            costo = abs(d["entrada"] - d["stop"]) / d["tick_size"] * d["tick_value"]
            lotaje = dimensionar(d["balance"], d["riesgo_pct"] / 100.0, costo, d["vol_min"], d["vol_max"], d["vol_step"])
        except (TypeError, ValueError) as e:
            return 400, {"autorizado": False, "motivo": f"datos de lotes inválidos: {e}"}
        cuerpo.update(lotes=lotaje.lotes, viable=lotaje.viable, motivo_lotes=lotaje.motivo,
                      capital_minimo=round(lotaje.capital_minimo, 2))
    return 200, cuerpo


def gate_setups(datos: dict, headers: dict) -> tuple[int, dict] | None:
    """None = puede pasar a /api/setups; tupla = respuesta de rechazo."""
    token = _bearer(headers)
    if token:
        try:
            cuenta, modo, robot = _identidad(datos)
        except (TypeError, ValueError) as e:
            return 400, {"error": str(e)}
        try:
            ok, motivo, _ = _verificar(token, cuenta, modo, robot, _ip(headers), {"endpoint": "setups"})
        except Exception as e:
            return 503, {"error": f"cerebro_no_disponible: {type(e).__name__}"}
        return None if ok else (403, {"error": "licencia_rechazada", "motivo": motivo})

    # ponytail: mientras MTB_SERVICE_KEY no exista en Vercel, /api/setups sigue abierto
    # (n8n T-01 y EAs viejos). Al crearla, todo cliente sin licencia ni clave queda fuera.
    if not os.environ.get("MTB_SERVICE_KEY"):
        return None
    if _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return None
    return 401, {"error": "se requiere licencia (Authorization: Bearer) o X-MTB-Service-Key"}


def procesar_admin(metodo: str, datos: dict, headers: dict) -> tuple[int, dict]:
    if not os.environ.get("MTB_ADMIN_KEY"):
        return 503, {"error": "MTB_ADMIN_KEY no configurada en el servidor"}
    if not _clave_ok(_h(headers, "X-Admin-Key"), "MTB_ADMIN_KEY"):
        return 401, {"error": "clave de administrador inválida"}

    from persistencia import licencias

    try:
        if metodo == "GET":
            return 200, licencias.estado_panel()

        accion = datos.get("accion")
        dias = int(datos["vigencia_dias"]) if datos.get("vigencia_dias") else None
        if accion == "emitir":
            token, lic = licencias.emitir(str(datos.get("cliente", "")), int(datos.get("cuenta") or 0),
                                          str(datos.get("tipo", "")).lower(), str(datos.get("robot", "")), dias)
            return 200, {"token": token, "licencia": lic,
                         "aviso": "Guarda el token ahora: no se puede volver a mostrar."}
        if accion == "revocar":
            licencias.revocar(int(datos["id"]))
            return 200, {"ok": True}
        if accion == "reautorizar":
            licencias.reautorizar(int(datos["id"]), dias)
            return 200, {"ok": True}
        if accion == "kill_switch":
            licencias.fijar_kill_switch(bool(datos.get("activo")), datos.get("motivo"))
            return 200, {"ok": True}
        return 400, {"error": "accion debe ser emitir | revocar | reautorizar | kill_switch"}
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": str(e)}


def demo() -> None:
    """Self-check sin BD: parsing, claves y cálculo de lotes con verificación simulada."""
    global _verificar
    original = _verificar
    lic_ok = {"expira_en": None}
    _verificar = lambda token, *a: (True, "ok", lic_ok) if token == "MTB-BUENO" else (False, "token_invalido", None)
    try:
        base = {"account": 318680674, "mode": "demo", "robot": "seguidor-smc"}
        assert procesar_auth(base, {})[0] == 401
        assert procesar_auth({"account": 1}, {"Authorization": "Bearer MTB-BUENO"})[0] == 400
        assert procesar_auth(base, {"authorization": "Bearer MTB-MALO"}) == (403, {"autorizado": False, "motivo": "token_invalido"})

        st, body = procesar_auth(base, {"Authorization": "Bearer MTB-BUENO"})
        assert st == 200 and body["autorizado"] and "lotes" not in body

        # GOLD $10,000, 1%, SL $10 → 0.10 lotes (misma regla que el coordinador)
        con_lotes = {**base, "balance": 10000, "riesgo_pct": 1.0, "entrada": 2650.0, "stop": 2640.0,
                     "tick_size": 0.01, "tick_value": 1.0, "vol_min": 0.01, "vol_max": 100, "vol_step": 0.01}
        st, body = procesar_auth(con_lotes, {"Authorization": "Bearer MTB-BUENO"})
        assert st == 200 and body["lotes"] == 0.10 and body["viable"], body

        st, body = procesar_auth({**con_lotes, "balance": 50}, {"Authorization": "Bearer MTB-BUENO"})
        assert st == 200 and body["lotes"] == 0.0 and not body["viable"], body

        os.environ.pop("MTB_SERVICE_KEY", None)
        assert gate_setups({}, {}) is None  # transición: abierto sin clave configurada
        os.environ["MTB_SERVICE_KEY"] = "svc"
        assert gate_setups({}, {})[0] == 401
        assert gate_setups({}, {"X-MTB-Service-Key": "svc"}) is None
        assert gate_setups(base, {"Authorization": "Bearer MTB-MALO"})[0] == 403
        assert gate_setups(base, {"Authorization": "Bearer MTB-BUENO"}) is None

        os.environ.pop("MTB_ADMIN_KEY", None)
        assert procesar_admin("GET", {}, {})[0] == 503
        os.environ["MTB_ADMIN_KEY"] = "adm"
        assert procesar_admin("GET", {}, {"X-Admin-Key": "otra"})[0] == 401
    finally:
        _verificar = original
        os.environ.pop("MTB_SERVICE_KEY", None)
        os.environ.pop("MTB_ADMIN_KEY", None)
    print("api.licencias.demo() OK")


if __name__ == "__main__":
    demo()
