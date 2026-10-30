"""Aceptacion del alumno para recibir las oportunidades del Master Trader (diseno V2: una vez por robot).

El alumno recibe un mensaje de Telegram con dos botones que abren un enlace FIRMADO (HMAC con MTB_TOKEN_SECRET,
vigencia 7 dias, un solo fin: activar o no activar ESE robot). El enlace abre una pagina con un boton "Confirmar":
la activacion se hace con un POST desde esa pagina, asi un previsualizador de enlaces nunca activa nada por si solo.
Sin n8n y sin tocar el bot: solo usa /api/v1/telegram/enviar (mismo envio que ya existe).
"""
import base64
import hashlib
import hmac
import json
import os
import time

VIGENCIA_S = 7 * 24 * 3600
ACCIONES = ("activar", "no_activar")


def _secreto() -> bytes:
    s = os.environ.get("MTB_TOKEN_SECRET", "")
    if not s:
        raise RuntimeError("MTB_TOKEN_SECRET no configurado")
    return ("master-aceptacion:" + s).encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def huella(texto: str) -> str:
    """Huella corta del texto exacto que se le mostro al alumno (prueba de que version autorizo)."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def firmar(licencia_id: int, simbolo: str, temporalidad: str, accion: str, ahora: float | None = None,
           chat: int | None = None, th: str | None = None) -> str:
    if accion not in ACCIONES:
        raise ValueError("accion invalida")
    datos = {"l": int(licencia_id), "s": simbolo, "t": temporalidad, "a": accion,
             "e": int((ahora if ahora is not None else time.time()) + VIGENCIA_S)}
    if chat:
        datos["c"] = int(chat)   # el enlace solo existe en ese chat: quien lo abre es quien lo recibio
    if th:
        datos["h"] = th          # huella del texto que acompanaba al enlace
    cuerpo = json.dumps(datos, separators=(",", ":")).encode()
    return _b64(cuerpo) + "." + _b64(hmac.new(_secreto(), cuerpo, hashlib.sha256).digest())


def verificar(token: str, ahora: float | None = None) -> dict | None:
    """-> {"licencia_id","simbolo","temporalidad","accion"} o None si la firma no cuadra o ya vencio."""
    try:
        c64, f64 = str(token).split(".")
        cuerpo = _unb64(c64)
        if not hmac.compare_digest(_unb64(f64), hmac.new(_secreto(), cuerpo, hashlib.sha256).digest()):
            return None
        d = json.loads(cuerpo)
        if d["e"] <= (ahora if ahora is not None else time.time()) or d["a"] not in ACCIONES:
            return None
        return {"licencia_id": int(d["l"]), "simbolo": str(d["s"]), "temporalidad": str(d["t"]), "accion": d["a"],
                "chat": d.get("c"), "texto_huella": d.get("h")}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _texto(simbolo: str, temporalidad: str, detalle: dict | None) -> str:
    """Mensaje HTML de Telegram: que se autoriza, que sigue protegiendo al robot y como se apaga."""
    from html import escape as e

    d = detalle or {}
    nombre = str(d.get("nombre") or f"{simbolo} {temporalidad}").replace("Master Trader - ", "")
    cuenta = d.get("cuenta")
    modo = "demo" if str(d.get("tipo") or "demo") == "demo" else str(d.get("tipo"))
    swing = temporalidad.startswith("Swing")
    cabecera = f"<b>Autorizacion del Master Trader</b>\n\nRobot: <b>{e(nombre)}</b>\n"
    cabecera += f"Cuenta {e(modo)}{' ' + str(cuenta) if cuenta else ''} · {e(simbolo)} · {e(temporalidad)}\n\n"
    cuerpo = (
        "<b>Que estas autorizando</b>\n"
        f"• El Master Trader revisa el analisis de {e(simbolo)} en {e(temporalidad)} y, cuando hay una oportunidad confirmada, "
        "le avisa a tu robot en segundos con el precio de entrada, el stop y el objetivo.\n"
        f"• Los lotes los calcula el servidor con tu regla de riesgo: maximo {'1' if swing else '2'} % del balance por operacion.\n"
        "• Tu robot conserva sus protecciones propias: cierre forzado al -3 %, breakeven escalonado y trailing"
        + ("; en Swing no coloca un stop fijo en el broker y gestiona la salida con sus reglas de swing" if swing else "") + ".\n"
        "• El Master se frena solo en la cuenta si pierde 3 % en un dia, si cae 15 % desde su maximo o tras 5 perdidas seguidas, "
        "y reduce el riesgo a la mitad desde 3 perdidas seguidas.\n\n"
        "<b>Que cambia</b>\n"
        "Al activarlo, tu robot deja de consultar el analisis por su cuenta: solo opera lo que le indica el Master.\n\n"
        "<b>Como lo apagas</b>\n"
        "Quitando el robot del grafico en MetaTrader, o pidiendo que lo desactive desde el panel de despacho.\n\n"
        "Si no pulsas nada, no pasa nada. ¿Lo activamos?")
    return cabecera + cuerpo


def mensaje(base_url: str, licencia_id: int, simbolo: str, temporalidad: str, ahora: float | None = None,
            detalle: dict | None = None, chat: int | None = None) -> dict:
    """Cuerpo para sendMessage (sin chat_id). `detalle` = {nombre, cuenta, tipo} para explicar que se autoriza.
    `chat` y la huella del texto quedan firmados dentro de los enlaces (evidencia de la autorizacion)."""
    texto = _texto(simbolo, temporalidad, detalle)
    th = huella(texto)

    def enlace(accion):
        return f"{base_url.rstrip('/')}/api/v1/master/aceptar?t={firmar(licencia_id, simbolo, temporalidad, accion, ahora, chat, th)}"
    return {
        "text": texto,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "Activar", "url": enlace("activar")},
                                              {"text": "Ahora no", "url": enlace("no_activar")}]]},
        "disable_web_page_preview": True,
    }


def pagina(titulo: str, texto: str, token: str | None = None) -> str:
    """HTML minimo. Con token muestra el boton Confirmar (POST por fetch); sin token solo informa."""
    boton = ""
    if token:
        boton = (f"<button id='b'>Confirmar</button><script>document.getElementById('b').onclick=async()=>{{"
                 f"const r=await fetch('/api/v1/master/aceptar',{{method:'POST',headers:{{'Content-Type':'application/json'}},"
                 f"body:JSON.stringify({{t:{json.dumps(token)}}})}});const d=await r.json();"
                 f"document.getElementById('m').textContent=d.mensaje||d.error||'Listo';document.getElementById('b').remove();}}</script>")
    return ("<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{titulo}</title><style>body{{font:16px system-ui;background:#0b1320;color:#e6edf6;display:grid;place-items:center;"
            "min-height:100vh;margin:0}.c{max-width:420px;padding:28px;text-align:center}button{margin-top:18px;padding:12px 28px;"
            "border:0;border-radius:8px;background:#2dd4bf;color:#06242c;font-weight:600;font-size:16px;cursor:pointer}</style>"
            f"<div class='c'><h2>{titulo}</h2><p id='m'>{texto}</p>{boton}</div></html>")


def demo() -> None:
    os.environ["MTB_TOKEN_SECRET"] = "secreto-de-prueba"
    t0 = 1_800_000_000.0
    t = firmar(7, "EURUSD", "Scalping 15m", "activar", t0)
    assert verificar(t, t0 + 10) == {"licencia_id": 7, "simbolo": "EURUSD", "temporalidad": "Scalping 15m", "accion": "activar",
                                     "chat": None, "texto_huella": None}
    assert verificar(t, t0 + VIGENCIA_S + 1) is None            # vencido
    assert verificar(t[:-2] + "xx", t0 + 10) is None             # firma alterada
    c, f = t.split(".")
    otro = _b64(json.dumps({"l": 8, "s": "EURUSD", "t": "Scalping 15m", "a": "activar", "e": int(t0 + 999999)}).encode())
    assert verificar(otro + "." + f, t0 + 10) is None            # cuerpo cambiado con la firma de otro
    assert verificar("basura", t0) is None and verificar("", t0) is None
    m = mensaje("https://x.test", 7, "EURUSD", "Scalping 15m", t0)
    botones = m["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in botones] == ["Activar", "Ahora no"] and all(b["url"].startswith("https://x.test/api/v1/master/aceptar?t=") for b in botones)
    assert "senal" not in m["text"].lower() and "señal" not in m["text"].lower()   # lenguaje neutro (politica de Ricardo)
    r = mensaje("https://x.test", 7, "XAUUSD", "Swing (M)", t0, {"nombre": "Master Trader - ORO SWING", "cuenta": 108460538, "tipo": "demo"})
    assert r["parse_mode"] == "HTML" and "ORO SWING" in r["text"] and "108460538" in r["text"] and "maximo 1 %" in r["text"], r["text"]
    assert "Master Trader - " not in r["text"] and "no coloca un stop fijo" in r["text"]
    r2 = mensaje("https://x.test", 4, "EURUSD", "Scalping 15m", t0, {"nombre": "Master Trader - EUR/USD <x>", "cuenta": 1, "tipo": "demo"})
    assert "maximo 2 %" in r2["text"] and "&lt;x&gt;" in r2["text"] and "<x>" not in r2["text"]  # escapa HTML
    assert "Confirmar" in pagina("T", "x", t) and "Confirmar" not in pagina("T", "x")
    # evidencia: el enlace firmado lleva el chat y la huella del texto; alterarlos invalida la firma
    m2 = mensaje("https://x.test", 7, "EURUSD", "Scalping 15m", t0, chat=555)
    tok = m2["reply_markup"]["inline_keyboard"][0][0]["url"].split("t=", 1)[1]
    v = verificar(tok, t0 + 5)
    assert v["chat"] == 555 and v["texto_huella"] == huella(m2["text"]) and len(v["texto_huella"]) == 16, v
    c64, f64 = tok.split(".")
    cuerpo = json.loads(_unb64(c64)); cuerpo["c"] = 999
    assert verificar(_b64(json.dumps(cuerpo, separators=(",", ":")).encode()) + "." + f64, t0 + 5) is None
    print("master.aceptacion.demo() OK")


if __name__ == "__main__":
    demo()
