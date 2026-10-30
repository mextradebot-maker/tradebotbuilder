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
                              datos.get("acks") or [], habilitado=habilitado())
    except Exception as e:
        return 503, {"ok": False, "motivo": f"master_no_disponible: {type(e).__name__}"}
    return 200, {"ok": True, "estado": r["estado"], "motivos": r["motivos"], "orden": r["orden"]}


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
    except (KeyError, TypeError, ValueError) as e:
        return 400, {"error": f"datos invalidos: {e}"}
    return 400, {"error": "accion debe ser activar | desactivar | reanudar"}


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
    print("api.master.demo() OK")


if __name__ == "__main__":
    demo()
