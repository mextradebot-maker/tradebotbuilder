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


def firmar(licencia_id: int, simbolo: str, temporalidad: str, accion: str, ahora: float | None = None) -> str:
    if accion not in ACCIONES:
        raise ValueError("accion invalida")
    cuerpo = json.dumps({"l": int(licencia_id), "s": simbolo, "t": temporalidad, "a": accion,
                         "e": int((ahora if ahora is not None else time.time()) + VIGENCIA_S)}, separators=(",", ":")).encode()
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
        return {"licencia_id": int(d["l"]), "simbolo": str(d["s"]), "temporalidad": str(d["t"]), "accion": d["a"]}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def mensaje(base_url: str, licencia_id: int, simbolo: str, temporalidad: str, ahora: float | None = None) -> dict:
    """Cuerpo para sendMessage (sin chat_id)."""
    def enlace(accion):
        return f"{base_url.rstrip('/')}/api/v1/master/aceptar?t={firmar(licencia_id, simbolo, temporalidad, accion, ahora)}"
    return {
        "text": (f"Tu robot {simbolo} ({temporalidad}) puede recibir las oportunidades del Master Trader: "
                 "el Master las revisa, comprueba tu riesgo y le avisa a tu robot. Tu robot sigue operando con sus mismas "
                 "reglas de proteccion y tu puedes apagarlo cuando quieras. ¿Lo activamos?"),
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
    assert verificar(t, t0 + 10) == {"licencia_id": 7, "simbolo": "EURUSD", "temporalidad": "Scalping 15m", "accion": "activar"}
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
    assert "Confirmar" in pagina("T", "x", t) and "Confirmar" not in pagina("T", "x")
    print("master.aceptacion.demo() OK")


if __name__ == "__main__":
    demo()
