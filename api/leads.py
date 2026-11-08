"""POST /api/v1/leads — captura de leads de la landing de ventas (nombre, correo, WhatsApp).

Guarda el lead en la tabla `leads` (con las dos casillas de consentimiento) y manda por el relay de correo
de siempre el enlace al recurso que eligio la persona (PDF o dashboard). Sin llave de API: es un formulario
publico, asi que valida todo, frena abusos por IP y por correo y no devuelve datos de nadie.
"""

import re
import threading
import time
from html import escape

BASE_ENV = "MTB_BASE_URL"
RECURSOS = {
    "pdf": ("/recursos/checklist-7-preguntas-mextradebot.pdf", "Descargar el PDF", "PDF de 2 páginas"),
    "dashboard": ("/recursos/checklist.html", "Abrir el dashboard", "dashboard interactivo"),
}
_CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}$")
_LIMITE, _VENTANA = 5, 3600  # envios por IP y hora
_lock = threading.Lock()
_marcas: dict[str, list[float]] = {}


def validar(d: dict) -> tuple[dict | None, str | None]:
    """Devuelve (lead limpio, None) o (None, motivo). Logica pura: se prueba sin base de datos."""
    if (d.get("web") or "").strip():  # campo trampa: solo lo llenan los bots
        return None, "bot"
    nombre = " ".join(str(d.get("nombre") or "").split())[:80]
    correo = str(d.get("correo") or "").strip().lower()[:254]
    digitos = re.sub(r"\D", "", str(d.get("whatsapp") or ""))
    if digitos.startswith("52") and len(digitos) == 12:
        digitos = digitos[2:]
    if digitos.startswith("521") and len(digitos) == 13:
        digitos = digitos[3:]
    formato = str(d.get("formato") or "pdf")
    if len(nombre) < 2:
        return None, "nombre"
    if not _CORREO.match(correo):
        return None, "correo"
    if not 10 <= len(digitos) <= 15:
        return None, "whatsapp"
    if formato not in RECURSOS:
        return None, "formato"
    if str(d.get("acepta_correo")) not in ("1", "true", "True") or str(d.get("acepta_whatsapp")) not in ("1", "true", "True"):
        return None, "consentimiento"
    return {"nombre": nombre, "correo": correo, "whatsapp": digitos, "formato": formato}, None


def _excedido(ip: str, ahora: float) -> bool:
    with _lock:
        marcas = [t for t in _marcas.get(ip, ()) if ahora - t < _VENTANA]
        excedido = len(marcas) >= _LIMITE
        if not excedido:
            marcas.append(ahora)
        _marcas[ip] = marcas
    return excedido


def _guardar(lead: dict) -> None:
    from persistencia.conexion import get_conn

    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leads ("
            " id bigserial PRIMARY KEY, creado timestamptz NOT NULL DEFAULT now(),"
            " nombre text NOT NULL, correo text NOT NULL UNIQUE, whatsapp text NOT NULL, formato text NOT NULL,"
            " acepta_correo boolean NOT NULL, acepta_whatsapp boolean NOT NULL, origen text NOT NULL DEFAULT 'landing-trader',"
            " veces integer NOT NULL DEFAULT 1, ultima_vez timestamptz NOT NULL DEFAULT now())")
        conn.execute(
            "INSERT INTO leads (nombre, correo, whatsapp, formato, acepta_correo, acepta_whatsapp)"
            " VALUES (%s, %s, %s, %s, true, true)"
            " ON CONFLICT (correo) DO UPDATE SET nombre = EXCLUDED.nombre, whatsapp = EXCLUDED.whatsapp,"
            " formato = EXCLUDED.formato, veces = leads.veces + 1, ultima_vez = now()",
            (lead["nombre"], lead["correo"], lead["whatsapp"], lead["formato"]))


def correo_recurso(nombre: str, formato: str) -> tuple[str, str]:
    import os

    from api.auth_alumnos import BASE, _marco

    ruta, boton, descripcion = RECURSOS[formato]
    enlace = f"{os.environ.get(BASE_ENV, BASE)}{ruta}"
    primero = escape(nombre.split(" ")[0])
    cuerpo = (f"<p>Hola {primero}, aquí está tu <b>Checklist de 7 preguntas para evaluar un robot de trading</b> ({descripcion}).</p>"
              f"<p style=\"text-align:center;margin:24px 0\"><a href=\"{enlace}\" style=\"background:#c6ea3b;color:#060d24;font-weight:bold;"
              f"text-decoration:none;padding:14px 24px;border-radius:10px;display:inline-block\">{boton}</a></p>"
              "<p>Úsala antes de usar cualquier robot, también los que te venden o te recomiendan. "
              "Cumplir los siete criterios no garantiza resultados.</p>"
              "<p>Cuando quieras, crea tu cuenta gratis en mextradebot.com.mx y prueba tu primer robot en DEMO, con dinero virtual.</p>")
    return "Tu checklist de 7 preguntas | MexTradeBot", _marco("Tu checklist está lista", cuerpo)


def procesar(payload: dict, ip: str = "") -> tuple[int, dict]:
    lead, motivo = validar(payload if isinstance(payload, dict) else {})
    if motivo == "bot":  # respuesta de exito falsa: el bot no aprende nada
        return 200, {"ok": True}
    if motivo:
        return 400, {"error": "datos_invalidos", "campo": motivo}
    if _excedido(ip or "?", time.monotonic()):
        return 429, {"error": "demasiados_intentos"}
    try:
        _guardar(lead)
    except Exception:  # noqa: BLE001 — la persona no debe ver un traceback; el log lo conserva
        import logging

        logging.getLogger("leads").exception("no se pudo guardar el lead")
        return 500, {"error": "no_se_pudo_guardar"}
    from api.auth_alumnos import enviar_correo

    asunto, html = correo_recurso(lead["nombre"], lead["formato"])
    threading.Thread(target=enviar_correo, args=(lead["correo"], asunto, html), daemon=True).start()
    try:
        from slack_avisos import avisar

        avisar(f"Nuevo lead de la landing: {lead['nombre']} · {lead['whatsapp']} · {lead['formato']}")
    except Exception:  # noqa: BLE001 — el aviso es un extra; nunca debe romper el registro
        pass
    return 200, {"ok": True, "formato": lead["formato"], "recurso": RECURSOS[lead["formato"]][0]}


# ── solicitudes de afiliacion ────────────────────────────────────────────────
_SI = ("1", "true", "True")


def validar_afiliado(d: dict) -> tuple[dict | None, str | None]:
    """Como validar(), para el formulario de la landing de afiliados. Logica pura."""
    if (d.get("web") or "").strip():
        return None, "bot"
    base, motivo = validar({**d, "formato": "pdf", "acepta_correo": "1", "acepta_whatsapp": "1"})
    if motivo:
        return None, motivo
    redes = " ".join(str(d.get("redes") or "").split())[:200]
    como = " ".join(str(d.get("como") or "").split())[:600]
    if len(redes) < 3:
        return None, "redes"
    if len(como) < 10:
        return None, "como"
    if str(d.get("acepta_reglas")) not in _SI or str(d.get("acepta_contacto")) not in _SI:
        return None, "consentimiento"
    return {"nombre": base["nombre"], "correo": base["correo"], "whatsapp": base["whatsapp"], "redes": redes, "como": como}, None


def _guardar_afiliado(a: dict) -> None:
    from persistencia.conexion import get_conn

    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS solicitudes_afiliado ("
            " id bigserial PRIMARY KEY, creado timestamptz NOT NULL DEFAULT now(),"
            " nombre text NOT NULL, correo text NOT NULL UNIQUE, whatsapp text NOT NULL, redes text NOT NULL, como text NOT NULL,"
            " acepta_reglas boolean NOT NULL, acepta_contacto boolean NOT NULL, estado text NOT NULL DEFAULT 'nueva',"
            " veces integer NOT NULL DEFAULT 1, ultima_vez timestamptz NOT NULL DEFAULT now())")
        conn.execute(
            "INSERT INTO solicitudes_afiliado (nombre, correo, whatsapp, redes, como, acepta_reglas, acepta_contacto)"
            " VALUES (%s, %s, %s, %s, %s, true, true)"
            " ON CONFLICT (correo) DO UPDATE SET nombre = EXCLUDED.nombre, whatsapp = EXCLUDED.whatsapp, redes = EXCLUDED.redes,"
            " como = EXCLUDED.como, veces = solicitudes_afiliado.veces + 1, ultima_vez = now()",
            (a["nombre"], a["correo"], a["whatsapp"], a["redes"], a["como"]))


def correos_afiliado(a: dict) -> list[tuple[str, str, str]]:
    """(destino, asunto, html): confirmacion para quien solicita y aviso a soporte con todos los datos."""
    from api.auth_alumnos import _marco

    primero = escape(a["nombre"].split(" ")[0])
    confirmacion = ("Recibimos tu solicitud de afiliación | MexTradeBot", _marco(
        "Recibimos tu solicitud",
        f"<p>Hola {primero}, recibimos tu solicitud para el programa de afiliados de MexTradeBot.</p>"
        "<p>Revisamos tu perfil y te respondemos en menos de 48 horas hábiles. Si te aprobamos, recibirás tu enlace de afiliado y el kit.</p>"
        "<p>Mientras tanto, crea tu cuenta gratis en mextradebot.com.mx y prueba el producto en DEMO: recomiendas mejor lo que conoces.</p>"))
    aviso = ("Nueva solicitud de afiliación | MexTradeBot", _marco(
        "Nueva solicitud de afiliación",
        f"<p><b>Nombre:</b> {escape(a['nombre'])}<br><b>Correo:</b> {escape(a['correo'])}<br><b>WhatsApp:</b> {escape(a['whatsapp'])}<br>"
        f"<b>Redes o sitio:</b> {escape(a['redes'])}</p><p><b>Cómo piensa compartir MexTradeBot:</b><br>{escape(a['como'])}</p>"
        "<p>Apruébala o recházala desde tu cuenta de productor en Hotmart (Afiliados → Solicitudes).</p>"))
    return [(a["correo"], *confirmacion), ("soporte@mextradebot.com.mx", *aviso)]


def procesar_afiliado(payload: dict, ip: str = "") -> tuple[int, dict]:
    a, motivo = validar_afiliado(payload if isinstance(payload, dict) else {})
    if motivo == "bot":
        return 200, {"ok": True}
    if motivo:
        return 400, {"error": "datos_invalidos", "campo": motivo}
    if _excedido("af:" + (ip or "?"), time.monotonic()):
        return 429, {"error": "demasiados_intentos"}
    try:
        _guardar_afiliado(a)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("leads").exception("no se pudo guardar la solicitud de afiliado")
        return 500, {"error": "no_se_pudo_guardar"}
    from api.auth_alumnos import enviar_correo

    for destino, asunto, html in correos_afiliado(a):
        threading.Thread(target=enviar_correo, args=(destino, asunto, html), daemon=True).start()
    try:
        from slack_avisos import avisar

        avisar(f"Nueva solicitud de afiliado: {a['nombre']} · {a['whatsapp']} · {a['redes']}")
    except Exception:  # noqa: BLE001
        pass
    return 200, {"ok": True}


def demo() -> None:
    ok = {"nombre": " Carlos  Pérez ", "correo": "Carlos@Example.com ", "whatsapp": "+52 1 442 123 4567",
          "formato": "dashboard", "acepta_correo": "1", "acepta_whatsapp": "1"}
    lead, motivo = validar(ok)
    assert motivo is None and lead == {"nombre": "Carlos Pérez", "correo": "carlos@example.com",
                                       "whatsapp": "4421234567", "formato": "dashboard"}, (lead, motivo)
    assert validar({**ok, "whatsapp": "123"})[1] == "whatsapp"
    assert validar({**ok, "correo": "sin-arroba"})[1] == "correo"
    assert validar({**ok, "nombre": "A"})[1] == "nombre"
    assert validar({**ok, "formato": "zip"})[1] == "formato"
    assert validar({**ok, "acepta_whatsapp": ""})[1] == "consentimiento"
    assert validar({**ok, "acepta_correo": "0"})[1] == "consentimiento"
    assert validar({**ok, "web": "http://spam"})[1] == "bot"
    assert procesar({**ok, "web": "x"}) == (200, {"ok": True})  # el bot recibe "exito" y no se guarda nada
    assert procesar({**ok, "correo": "x"})[0] == 400
    for _ in range(_LIMITE):
        assert not _excedido("1.2.3.4", 100.0)
    assert _excedido("1.2.3.4", 101.0) and not _excedido("5.6.7.8", 101.0)
    asunto, html = correo_recurso("<b>Ana", "pdf")
    assert "Hola &lt;b&gt;Ana," in html and "Hola <b>" not in html  # el nombre se escapa en el correo
    assert "/recursos/checklist-7-preguntas-mextradebot.pdf" in html and "Descargar el PDF" in html
    af = {"nombre": "Luis Mora", "correo": "luis@example.com", "whatsapp": "442 111 2233", "redes": "@luismora",
          "como": "Videos en YouTube y mi grupo de Telegram", "acepta_reglas": "1", "acepta_contacto": "1"}
    a, m = validar_afiliado(af)
    assert m is None and a["whatsapp"] == "4421112233" and a["redes"] == "@luismora", (a, m)
    assert validar_afiliado({**af, "redes": "x"})[1] == "redes"
    assert validar_afiliado({**af, "como": "corto"})[1] == "como"
    assert validar_afiliado({**af, "acepta_reglas": ""})[1] == "consentimiento"
    assert validar_afiliado({**af, "correo": "mal"})[1] == "correo"
    assert validar_afiliado({**af, "web": "spam"})[1] == "bot"
    assert procesar_afiliado({**af, "web": "spam"}) == (200, {"ok": True})
    assert procesar_afiliado({**af, "nombre": ""})[0] == 400
    (d1, _, h1), (d2, _, h2) = correos_afiliado({**a, "nombre": "<b>Luis", "como": "<script>x</script> y más texto"})
    assert d1 == "luis@example.com" and d2 == "soporte@mextradebot.com.mx"
    assert "<script>" not in h2 and "&lt;script&gt;" in h2 and "Hola &lt;b&gt;Luis," in h1  # todo se escapa en los correos
    print("api.leads (afiliados) OK")
    print("api.leads.demo() OK")


if __name__ == "__main__":
    demo()
