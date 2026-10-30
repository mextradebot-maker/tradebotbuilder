"""Master Trader: endpoints del despachador (ver master/despachador.py).

  POST /api/v1/master/pulso        (robot)  Authorization: Bearer <token de licencia>
       {"account","mode","robot","simbolo","temporalidad", "estado":{balance,equity,pnl_hoy,racha,riesgo_abierto_pct,posiciones,
        riesgo_pct,tick_size,tick_value,vol_min,vol_max,vol_step,version}, "acks":[{"orden_id","estado":"colocada|rechazada","ticket","motivo"}]}
       → {"ok":true,"estado":"sin_orden|entregada|reentrega|bloqueada|sin_lotes|sin_activar|master_apagado","motivos":[...],
          "orden": null | {"orden_id","direccion","entrada","stop","tp","lotes","ref_ts","ref_cierre"}}
       El cuerpo se escribe en el orden y con las llaves que lee el parser minimo del EA ("orden":{...} al final).
  POST /api/v1/master/oportunidad  (servicio) X-MTB-Service-Key  {"simbolo","temporalidad","setups_confirmados":[...],"ref_ts","ref_cierre"}
  GET/POST /api/v1/master/admin    (admin)    X-Admin-Key: estado | {"accion":"activar|desactivar","licencia_id","simbolo","temporalidad"} | {"accion":"reanudar","cuenta"}

El Master solo entrega ordenes si MASTER_ACTIVO=1 en el entorno del servidor (apagado por defecto: los robots siguen con su
camino de siempre, /api/setups a la vela nueva). Sin el interruptor, el pulso igual registra que el robot esta vivo.
"""
import os

from api.licencias import _bearer, _clave_ok, _h, _identidad, _ip, _verificar

_ESTADO = ("balance", "equity", "tick_size", "tick_value", "vol_min", "vol_max", "vol_step")


def habilitado() -> bool:
    return os.environ.get("MASTER_ACTIVO") == "1"


def cierre_auto() -> bool:
    return os.environ.get("MASTER_CIERRE_AUTO") == "1"


def _repo():
    from master.despachador import RepoPg
    return RepoPg()


def procesar_pulso(datos: dict, headers: dict, repo=None) -> tuple[int, dict]:
    from master import despachador

    token = _bearer(headers)
    if not token:
        return 401, {"ok": False, "motivo": "falta_token"}
    try:
        cuenta, modo, robot = _identidad(datos)
        simbolo, temporalidad = str(datos["simbolo"]), str(datos["temporalidad"])
        est = dict(datos.get("estado") or {})
        for k in _ESTADO:
            est[k] = float(est[k])
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"ok": False, "motivo": f"datos invalidos: {e}"}
    try:
        ok, motivo, lic = _verificar(token, cuenta, modo, robot, _ip(headers), {"endpoint": "master_pulso"})
    except Exception as e:  # BD caida: falla cerrado, el robot reintenta en el siguiente pulso
        return 503, {"ok": False, "motivo": f"cerebro_no_disponible: {type(e).__name__}"}
    if not ok:
        return 403, {"ok": False, "motivo": motivo}
    try:
        r = despachador.pulso(repo or _repo(), lic["id"], cuenta, simbolo, temporalidad, est,
                              datos.get("acks") or [], habilitado=habilitado(), cierre_auto=cierre_auto())
    except Exception as e:
        return 503, {"ok": False, "motivo": f"master_no_disponible: {type(e).__name__}"}
    # "cierre" antes que "orden": el parser del EA busca cada bloque desde su propia llave
    return 200, {"ok": True, "estado": r["estado"], "motivos": r["motivos"], "cierre": r.get("cierre"), "orden": r["orden"]}


def procesar_oportunidad(datos: dict, headers: dict, repo=None) -> tuple[int, dict]:
    from master import despachador

    if not os.environ.get("MTB_SERVICE_KEY") or not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    if not habilitado():
        return 200, {"ordenes": 0, "master": "apagado"}
    try:
        return 200, despachador.recibir_oportunidades(
            repo or _repo(), str(datos["simbolo"]), str(datos["temporalidad"]), datos.get("setups_confirmados") or [],
            datos.get("ref_ts"), datos.get("ref_cierre"))
    except KeyError as e:
        return 400, {"error": f"falta {e}"}


def procesar_admin(metodo: str, datos: dict, headers: dict, repo=None) -> tuple[int, dict]:
    if not os.environ.get("MTB_ADMIN_KEY"):
        return 503, {"error": "MTB_ADMIN_KEY no configurada en el servidor"}
    if not _clave_ok(_h(headers, "X-Admin-Key"), "MTB_ADMIN_KEY"):
        return 401, {"error": "clave de administrador invalida"}
    r = repo or _repo()
    if metodo == "GET":
        return 200, {"master_activo": habilitado(), **r.panel()}
    accion = datos.get("accion")
    try:
        if accion in ("activar", "desactivar"):
            r.activar(int(datos["licencia_id"]), str(datos["simbolo"]), str(datos["temporalidad"]), accion == "activar")
            return 200, {"ok": True}
        if accion == "reanudar":
            r.reanudar(int(datos["cuenta"]))
            return 200, {"ok": True}
        if accion == "cerrar":
            from master.despachador import ordenar_cierre
            c = ordenar_cierre(r, int(datos["licencia_id"]), str(datos["simbolo"]), str(datos["temporalidad"]),
                               str(datos.get("motivo") or "cierre pedido por el administrador"))
            return (200, {"ok": True, "cierre_id": c["cierre_id"]}) if c else (409, {"error": "ya hay un cierre pendiente"})
        if accion == "solicitar":
            dest = datos.get("destino_licencia_id")
            return solicitar_activacion(int(datos["licencia_id"]), str(datos["simbolo"]), str(datos["temporalidad"]),
                                        destino_licencia_id=int(dest) if dest else None)
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": f"datos invalidos: {e}"}
    return 400, {"error": "accion debe ser activar | desactivar | reanudar"}


def solicitar_activacion(licencia_id: int, simbolo: str, temporalidad: str, chat_de=None, enviar=None,
                         destino_licencia_id: int | None = None, datos_de=None) -> tuple[int, dict]:
    """Manda al alumno el mensaje de Telegram con los botones Activar / Ahora no (enlaces firmados)."""
    from master import aceptacion

    if chat_de is None:
        from persistencia.licencias import chat_de_licencia as chat_de
    if enviar is None:
        from api.alumnos import _enviar_tg as enviar
    if datos_de is None:
        try:
            from persistencia.licencias import datos_licencia as datos_de
        except Exception:
            datos_de = lambda _l: None
    chat = chat_de(destino_licencia_id or licencia_id)  # destino_licencia_id: pedir la autorizacion al dueno de OTRA licencia
    if not chat:
        return 409, {"error": "el alumno de esa licencia no tiene Telegram vinculado"}
    token = os.environ.get("MTB_TELEGRAM_BOT_TOKEN", "")
    if not token:
        return 503, {"error": "MTB_TELEGRAM_BOT_TOKEN no configurado en el servidor"}
    try:
        base = os.environ.get("MTB_BASE_URL", "https://mextradebot.com.mx")
        d = datos_de(licencia_id) or {}
        st, resp = enviar(token, {"chat_id": chat, **aceptacion.mensaje(
            base, licencia_id, simbolo, temporalidad, detalle={"nombre": d.get("cliente"), "cuenta": d.get("cuenta"), "tipo": d.get("tipo")})})
    except (OSError, RuntimeError) as e:
        return 502, {"error": f"no se pudo enviar: {e}"}
    return (200, {"ok": True}) if st == 200 else (502, {"error": "Telegram rechazo el mensaje", "detalle": resp.get("description")})


def procesar_aceptar_get(qs: dict) -> tuple[int, str]:
    """GET /api/v1/master/aceptar?t=... -> pagina HTML (nunca activa nada por si sola)."""
    from master import aceptacion

    d = aceptacion.verificar(qs.get("t", ""))
    if not d:
        return 400, aceptacion.pagina("Enlace no valido", "Este enlace ya vencio o no es valido. Pide uno nuevo desde tu panel o desde Telegram.")
    que = "activar" if d["accion"] == "activar" else "dejar sin activar"
    return 200, aceptacion.pagina("Master Trader", f"Vas a {que} las oportunidades del Master Trader para tu robot {d['simbolo']} ({d['temporalidad']}).", qs["t"])


def procesar_aceptar_post(datos: dict, repo=None) -> tuple[int, dict]:
    from master import aceptacion

    d = aceptacion.verificar(str(datos.get("t", "")))
    if not d:
        return 400, {"error": "enlace no valido o vencido"}
    (repo or _repo()).activar(d["licencia_id"], d["simbolo"], d["temporalidad"], d["accion"] == "activar")
    if d["accion"] == "activar":
        return 200, {"ok": True, "mensaje": "Listo: tu robot recibira las oportunidades del Master Trader. Puedes apagarlo cuando quieras."}
    return 200, {"ok": True, "mensaje": "De acuerdo: tu robot sigue como hasta hoy."}


def demo() -> None:
    from datetime import datetime, timedelta, timezone
    from master.despachador import MemRepo
    from api import licencias

    os.environ.update(MTB_ADMIN_KEY="admin-k", MTB_SERVICE_KEY="svc-k")
    est = {"balance": 10000, "equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 0, "riesgo_pct": 2,
           "tick_size": 0.00001, "tick_value": 1, "vol_min": 0.01, "vol_max": 100, "vol_step": 0.01}
    datos = {"account": 111, "mode": "demo", "robot": "seguidor-smc", "simbolo": "EURUSD", "temporalidad": "Scalping 15m", "estado": est}
    h = {"Authorization": "Bearer MTB-T"}
    repo = MemRepo()

    guardado = licencias._verificar
    try:
        licencias._verificar = lambda *a, **k: (True, "ok", {"id": 7})
        globals()["_verificar"] = licencias._verificar
        assert procesar_pulso(datos, {}, repo)[0] == 401
        assert procesar_pulso({**datos, "estado": {}}, h, repo)[0] == 400
        os.environ.pop("MASTER_ACTIVO", None)
        st, b = procesar_pulso(datos, h, repo)
        assert st == 200 and b["estado"] == "master_apagado" and b["orden"] is None, b   # apagado: no entrega pero registra
        os.environ["MASTER_ACTIVO"] = "1"
        assert procesar_pulso(datos, h, repo)[1]["estado"] == "sin_activar"             # vivo, pero nadie lo activo
        assert procesar_admin("GET", {}, {}, repo)[0] == 401
        assert procesar_oportunidad({}, {}, repo)[0] == 401
        repo.robots[(7, "EURUSD", "Scalping 15m")]["activo"] = True                      # lo que hace admin.activar
        setup = {"direccion": "short", "entrada": 1.1, "stop": 1.101, "tp": 1.098}
        st, b = procesar_oportunidad({"simbolo": "EURUSD", "temporalidad": "Scalping 15m", "setups_confirmados": [setup]},
                                     {"X-MTB-Service-Key": "svc-k"}, repo)
        assert st == 200 and b["ordenes"] == 1, b
        st, b = procesar_pulso(datos, h, repo)
        assert b["estado"] == "entregada" and b["orden"]["direccion"] == "short" and b["orden"]["lotes"] == 2.0, b
        os.environ["MASTER_ACTIVO"] = "0"
        assert procesar_oportunidad({"simbolo": "EURUSD", "temporalidad": "Scalping 15m"}, {"X-MTB-Service-Key": "svc-k"}, repo)[1]["master"] == "apagado"
    finally:
        licencias._verificar = guardado
        os.environ.pop("MASTER_ACTIVO", None)
    # cierre por admin, solicitud por Telegram y aceptacion firmada
    os.environ["MTB_TOKEN_SECRET"] = "secreto-de-prueba"
    os.environ["MTB_TELEGRAM_BOT_TOKEN"] = "tg-prueba"
    enviados = []
    st, b = solicitar_activacion(7, "EURUSD", "Scalping 15m", chat_de=lambda l: None, enviar=lambda t, c: (200, {}))
    assert st == 409, (st, b)
    st, b = solicitar_activacion(7, "EURUSD", "Scalping 15m", chat_de=lambda l: 555, enviar=lambda t, c: (enviados.append(c) or (200, {})))
    assert st == 200 and enviados[0]["chat_id"] == 555 and "inline_keyboard" in enviados[0]["reply_markup"], (st, b)
    # pedir la autorizacion al dueno de OTRA licencia (el chat sale de la licencia destino; el texto, de la licencia del robot)
    pedidos = []
    st, b = solicitar_activacion(3, "WTIUSD", "Swing (S)", chat_de=lambda l: (pedidos.append(l) or 777),
                                 enviar=lambda t, c: (enviados.append(c) or (200, {})), destino_licencia_id=9,
                                 datos_de=lambda l: {"cliente": "Master Trader - PETROLEO", "cuenta": 318680674, "tipo": "demo"})
    assert st == 200 and pedidos == [9] and enviados[-1]["chat_id"] == 777 and "PETROLEO" in enviados[-1]["text"] and "318680674" in enviados[-1]["text"]
    url = enviados[0]["reply_markup"]["inline_keyboard"][0][0]["url"]
    token = url.split("t=", 1)[1]
    st, html = procesar_aceptar_get({"t": token})
    assert st == 200 and "Confirmar" in html
    assert procesar_aceptar_get({"t": "x"})[0] == 400
    repo2 = MemRepo()
    st, b = procesar_aceptar_post({"t": token}, repo2)
    assert st == 200 and b["ok"] is True
    assert procesar_aceptar_post({"t": token + "x"}, repo2)[0] == 400
    os.environ["MASTER_CIERRE_AUTO"] = "1"
    assert cierre_auto() is True
    os.environ.pop("MASTER_CIERRE_AUTO")
    print("api.master.demo() OK")


if __name__ == "__main__":
    demo()
