"""Webhook de Hotmart (postback 2.0.0) → membresías del panel.

  POST /api/v1/hotmart   cabecera X-HOTMART-HOTTOK = MTB_HOTMART_HOTTOK (se pega en EasyPanel)

El correo del comprador es la llave: debe ser el mismo con el que el alumno entra
al panel. Si aún no tiene cuenta, la membresía queda guardada y sus licencias
aparecen en cuanto se registre con ese correo.

Nivel: clave de seguimiento `nivel=trader|vip` en cualquier parte del evento,
si no el nombre del plan ("Trader Mensual", "VIP Anual"), si no el id del producto
(de fábrica 8655745 = Trader y 8655789 = VIP; MTB_HOTMART_PRODUCTOS="id:nivel,..." agrega otros).

Siempre responde 200 a eventos válidos que no cambian nada (Hotmart reintenta lo
que no recibe 200); 401 con hottok incorrecto; 503 si el servidor no tiene hottok.
"""

import json
import os
import re
from datetime import datetime, timezone

ACTIVAR = {"PURCHASE_APPROVED", "PURCHASE_COMPLETE"}
PAUSAR = {"PURCHASE_REFUNDED", "PURCHASE_CHARGEBACK", "PURCHASE_PROTEST", "PURCHASE_CANCELED",
          "PURCHASE_DELAYED", "PURCHASE_EXPIRED"}
CANCELAR = {"SUBSCRIPTION_CANCELLATION"}
PRODUCTOS_FABRICA = {"8655745": "trader", "8655789": "vip"}


def _productos() -> dict[str, str]:
    mapa = dict(PRODUCTOS_FABRICA)
    for par in os.environ.get("MTB_HOTMART_PRODUCTOS", "").split(","):
        pid, _, nivel = par.partition(":")
        if pid.strip() and nivel.strip().lower() in ("trader", "vip"):
            mapa[pid.strip()] = nivel.strip().lower()
    return mapa


def _fecha(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc) if ms else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _nivel(data: dict) -> str | None:
    marca = re.search(r"nivel\s*[=:]\s*(trader|vip)\b", json.dumps(data, ensure_ascii=False), re.I)
    if marca:
        return marca.group(1).lower()
    plan = str(((data.get("subscription") or {}).get("plan") or {}).get("name") or "").lower()
    for nivel in ("vip", "trader"):
        if re.search(rf"\b{nivel}\b", plan):
            return nivel
    return _productos().get(str((data.get("product") or {}).get("id") or ""))


def interpretar(payload: dict) -> dict:
    """Evento de Hotmart → {accion: activar|pausar|cancelar|ignorar, correo, nombre, nivel, vence_en, ...}. Puro (sin BD)."""
    evento = str(payload.get("event") or "").upper()
    data = payload.get("data") or {}
    persona = data.get("buyer") or data.get("subscriber") or {}
    sub = data.get("subscription") or {}
    correo = str(persona.get("email") or "").strip().lower() or None
    accion = ("activar" if evento in ACTIVAR else "pausar" if evento in PAUSAR
              else "cancelar" if evento in CANCELAR else "ignorar")
    res = {
        "evento": evento, "accion": accion, "correo": correo, "nombre": str(persona.get("name") or "").strip(),
        "nivel": _nivel(data), "producto": str((data.get("product") or {}).get("id") or "") or None,
        "suscriptor": str((sub.get("subscriber") or data.get("subscriber") or {}).get("code") or "") or None,
        "vence_en": _fecha(data.get("date_next_charge") or (data.get("purchase") or {}).get("date_next_charge")),
    }
    if accion != "ignorar" and not correo:
        res["accion"] = "ignorar"  # sin correo no hay a quién aplicarlo
    if accion == "activar" and not res["nivel"]:
        res["accion"] = "ignorar"  # producto ajeno a las membresías (p. ej. otro curso)
    return res


def procesar(payload: dict, headers: dict) -> tuple[int, dict]:
    from api.licencias import _h

    esperado = os.environ.get("MTB_HOTMART_HOTTOK", "")
    if not esperado:
        return 503, {"error": "MTB_HOTMART_HOTTOK no configurado en el servidor"}
    import hmac

    recibido = _h(headers, "X-HOTMART-HOTTOK") or str(payload.get("hottok") or "")
    if not hmac.compare_digest(recibido.encode(), esperado.encode()):
        return 401, {"error": "hottok invalido"}

    ev = interpretar(payload)
    from persistencia import membresias

    evento_id = str(payload.get("id") or "")
    if evento_id and not membresias.registrar_evento(evento_id, ev["evento"], ev["correo"], ev["accion"], payload):
        return 200, {"ok": True, "accion": "duplicado"}
    try:
        return _aplicar(ev, membresias)
    except Exception:
        if evento_id:  # que el reintento de Hotmart no se tome por duplicado
            membresias.olvidar_evento(evento_id)
        raise


def _aplicar(ev: dict, membresias) -> tuple[int, dict]:
    if ev["accion"] == "activar":
        lic = membresias.activar(ev["correo"], ev["nombre"], ev["nivel"], ev["suscriptor"], ev["producto"], ev["evento"])
        return 200, {"ok": True, "accion": "activar", "nivel": ev["nivel"], "licencia": lic["id"]}
    if ev["accion"] in ("pausar", "cancelar"):
        estado = "pausada" if ev["accion"] == "pausar" else "cancelada"
        vence = ev["vence_en"] if estado == "cancelada" else None
        hecho = membresias.suspender(ev["correo"], estado, vence, ev["evento"], ev["nivel"])
        return 200, {"ok": True, "accion": ev["accion"] if hecho else "sin_cambios"}
    return 200, {"ok": True, "accion": "ignorar"}


def demo() -> None:
    compra = {"id": "e1", "event": "PURCHASE_APPROVED", "version": "2.0.0", "data": {
        "product": {"id": 8655745, "name": "MexTradeBot Trader"},
        "buyer": {"email": " Ana@X.com ", "name": "Ana"},
        "purchase": {"status": "APPROVED", "transaction": "HP1"},
        "subscription": {"status": "ACTIVE", "plan": {"id": 1, "name": "Trader Mensual"}, "subscriber": {"code": "S1"}}}}
    r = interpretar(compra)
    assert (r["accion"], r["correo"], r["nivel"], r["suscriptor"], r["producto"]) == ("activar", "ana@x.com", "trader", "S1", "8655745"), r

    vip = json.loads(json.dumps(compra))
    vip["data"]["product"]["id"] = 999
    vip["data"]["subscription"]["plan"]["name"] = "VIP Anual"
    assert interpretar(vip)["nivel"] == "vip"
    vip["data"]["subscription"]["plan"]["name"] = "Plan 1"
    vip["data"]["purchase"]["origin"] = {"sck": "nivel=vip"}  # clave de seguimiento gana
    assert interpretar(vip)["nivel"] == "vip"
    vip["data"]["purchase"]["origin"] = {}
    assert interpretar(vip)["accion"] == "ignorar"  # producto desconocido sin pistas
    os.environ["MTB_HOTMART_PRODUCTOS"] = "999:vip, basura, 5:oro"
    assert interpretar(vip)["nivel"] == "vip" and _productos() == {"8655745": "trader", "8655789": "vip", "999": "vip"}
    os.environ.pop("MTB_HOTMART_PRODUCTOS")

    cancel = {"id": "e2", "event": "SUBSCRIPTION_CANCELLATION", "data": {
        "date_next_charge": 1893456000000, "product": {"id": 8655745},
        "subscriber": {"code": "S1", "email": "ana@x.com", "name": "Ana"},
        "subscription": {"plan": {"name": "Trader Mensual"}}, "cancellation_date": 1}}
    r = interpretar(cancel)
    assert r["accion"] == "cancelar" and r["correo"] == "ana@x.com" and r["suscriptor"] == "S1"
    assert r["vence_en"] == datetime(2030, 1, 1, tzinfo=timezone.utc), r["vence_en"]

    for ev in ("PURCHASE_REFUNDED", "PURCHASE_CHARGEBACK", "PURCHASE_DELAYED", "PURCHASE_CANCELED"):
        assert interpretar({**compra, "event": ev})["accion"] == "pausar", ev
    assert interpretar({**compra, "event": "PURCHASE_BILLET_PRINTED"})["accion"] == "ignorar"
    sin_correo = json.loads(json.dumps(compra))
    sin_correo["data"]["buyer"]["email"] = ""
    assert interpretar(sin_correo)["accion"] == "ignorar"
    assert interpretar({})["accion"] == "ignorar" and _fecha("x") is None

    os.environ.pop("MTB_HOTMART_HOTTOK", None)
    assert procesar(compra, {"X-HOTMART-HOTTOK": "x"})[0] == 503
    os.environ["MTB_HOTMART_HOTTOK"] = "secreto"
    assert procesar(compra, {})[0] == 401
    assert procesar(compra, {"X-HOTMART-HOTTOK": "otro"})[0] == 401
    os.environ.pop("MTB_HOTMART_HOTTOK")
    print("api.hotmart.demo() OK")


if __name__ == "__main__":
    demo()
