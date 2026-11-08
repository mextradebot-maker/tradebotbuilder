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
    """Correo de la landing de ventas: entrega la checklist en el formato elegido (y ofrece el otro)."""
    from api import correos_landing as c

    ruta, boton, descripcion = RECURSOS[formato]
    b = c.base()
    enlace = f"{b}{ruta}"
    otro = "dashboard" if formato == "pdf" else "pdf"
    ruta_o, _, desc_o = RECURSOS[otro]
    nombre_1 = nombre.split(" ")[0]
    primero = escape(nombre_1)
    cuerpo = (
        c.parrafo(f"Hola <b>{primero}</b>, gracias por pedir la checklist. Ya está lista: es un {descripcion} para que revises cualquier robot de trading antes de confiar en él.")
        + c.boton(boton, enlace)
        + f'<p style="margin:12px 0 0;text-align:center;font:13px/1.5 Arial,Helvetica,sans-serif;color:{c.MUTED}">Si el botón no abre, copia este enlace:<br>'
          f'<a href="{enlace}" style="color:{c.MUTED}">{enlace}</a></p>'
        + c.titulo("Qué vas a encontrar")
        + '<ul style="margin:0 0 6px;padding-left:20px;font:15px/1.6 Arial,Helvetica,sans-serif;color:' + c.INK + '">'
          "<li style=\"margin-bottom:6px\"><b>7 preguntas clave</b> para cualquier robot: reglas de entrada y salida, número de operaciones, activos y periodos, costos reales, pérdidas, prueba en DEMO y promesas de ganancias.</li>"
          "<li style=\"margin-bottom:6px\"><b>Qué pedir en cada punto</b>, para que sepas exactamente qué información exigir.</li>"
          "<li><b>Cómo leer tu resultado</b>, sin veredictos de «bueno» o «malo».</li></ul>"
        + c.titulo("Cómo usarla en 3 pasos")
        + c.pasos([("Ten a la mano el robot", "El tuyo, o uno que te estén vendiendo o recomendando."),
                   ("Contesta Sí, No o No sé", "Con lo que sabes hoy. Lo que no sepas es justo lo que debes pedir."),
                   ("Pide lo que falte", "Un robot serio responde estas preguntas sin rodeos.")])
        + c.caja(f'<b>También tienes la otra versión:</b> {desc_o}. <a href="{b}{ruta_o}" style="color:{c.NAVY}">Ábrela aquí</a>.')
        + c.titulo("Siguiente paso: pruébalo sin arriesgar dinero")
        + c.parrafo("Crea tu cuenta gratis, genera tu primer robot y míralo operar en DEMO, con dinero virtual. No necesitas tarjeta.")
        + c.boton("Crear mi cuenta gratis", f"{b}/", relleno=False)
        + f'<p style="margin:22px 0 0;font:15px/1.6 Arial,Helvetica,sans-serif;color:{c.INK}">¿Dudas? Escríbenos por <a href="{c.WHATSAPP}" style="color:{c.NAVY}">WhatsApp</a> '
          f'o a <a href="mailto:{c.SOPORTE}" style="color:{c.NAVY}">{c.SOPORTE}</a>. Respondemos en días hábiles.</p>')
    html = c.plantilla(
        preheader="Úsala antes de confiar en cualquier robot de trading, el tuyo o el que te vendan.",
        kicker="Recurso gratuito",
        h1="Tu checklist de 7 preguntas para evaluar un robot de trading",
        subtitulo="Antes de usar un robot, hazle estas 7 preguntas. Te toma unos minutos.",
        cuerpo=cuerpo,
        motivo=f"Recibes este correo porque pediste la checklist en mextradebot.com.mx y aceptaste recibir correos de MexTradeBot. Si no quieres recibir más, escribe a {c.SOPORTE}.")
    return f"{nombre_1}, tu checklist de 7 preguntas está lista", html


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
    """(destino, asunto, html): confirmación para quien solicita y aviso a soporte con todos los datos."""
    from api import correos_landing as c

    nombre_1 = a["nombre"].split(" ")[0]
    primero = escape(nombre_1)
    b = c.base()
    resumen = (f'<b>Nombre:</b> {escape(a["nombre"])}<br><b>Correo:</b> {escape(a["correo"])}<br><b>WhatsApp:</b> {escape(a["whatsapp"])}<br>'
               f'<b>Tus redes o sitio:</b> {escape(a["redes"])}<br><b>Cómo piensas compartir MexTradeBot:</b> {escape(a["como"])}')
    cuerpo = (
        c.parrafo(f"Hola <b>{primero}</b>, gracias por querer recomendar MexTradeBot. Esto fue lo que nos enviaste:")
        + c.caja(resumen)
        + c.titulo("Qué sigue")
        + c.pasos([("Revisamos tu perfil", "Aprobamos cada solicitud a mano, en menos de 48 horas hábiles."),
                   ("Completa tu afiliación en Hotmart", "La aprobación y tu enlace salen de Hotmart. Pide la afiliación al plan que quieras recomendar:"),
                   ("Recibes tu enlace y el kit", "Guía en PDF, banners en varios formatos y textos con el aviso de riesgo incluido.")])
        + c.boton("Afiliarme al plan Trader", c.HOTMART_TRADER, ancho=280)
        + '<div style="height:10px;line-height:10px">&nbsp;</div>'
        + c.boton("Afiliarme al plan VIP", c.HOTMART_VIP, ancho=280)
        + c.titulo("Lo que ganas, en claro")
        + c.caja("<b>40 %</b> de la primera compra y <b>25 %</b> de cada renovación, mientras tu referido siga suscrito. Atribución permanente. "
                 "Hotmart calcula el monto final sobre el valor neto de la venta, y la comisión depende de ventas efectivas: no es un ingreso garantizado.")
        + c.titulo("Mientras esperas")
        + c.parrafo("Prueba MexTradeBot en DEMO: recomiendas mejor lo que conoces. Y lee las reglas, que son cortas y claras.")
        + c.boton("Probar MexTradeBot en DEMO", f"{b}/", relleno=False)
        + f'<p style="margin:18px 0 0;text-align:center;font:14px/1.6 Arial,Helvetica,sans-serif;color:{c.MUTED}">'
          f'<a href="{b}/reglas-del-afiliado.html" style="color:{c.NAVY}">Reglas del afiliado</a> &nbsp;·&nbsp; '
          f'<a href="{b}/afiliados.html" style="color:{c.NAVY}">Cómo funciona el programa</a></p>')
    confirmacion = (f"{nombre_1}, recibimos tu solicitud de afiliación", c.plantilla(
        preheader="Respondemos en menos de 48 horas hábiles. Esto es lo que sigue.",
        kicker="Programa de afiliados",
        h1="Recibimos tu solicitud",
        subtitulo="Revisamos tu perfil y te respondemos en menos de 48 horas hábiles.",
        cuerpo=cuerpo,
        motivo="Recibes este correo porque enviaste una solicitud de afiliación en mextradebot.com.mx y aceptaste que te contactemos.",
        extra_pie="Los afiliados son terceros independientes: no son empleados, representantes ni asesores de MexTradeBot y no están autorizados a prometer resultados.<br><br>"))
    interno = (
        c.parrafo(f"<b>{escape(a['nombre'])}</b> pidió afiliarse. Estos son sus datos:")
        + c.caja(resumen)
        + c.parrafo("Revísala y apruébala o recházala desde tu cuenta de productor en Hotmart:")
        + c.boton("Solicitudes del plan Trader", "https://app.hotmart.com/products/manage/8655745/affiliation-setup", ancho=280)
        + '<div style="height:10px;line-height:10px">&nbsp;</div>'
        + c.boton("Solicitudes del plan VIP", "https://app.hotmart.com/products/manage/8655789/affiliation-setup", ancho=280)
        + '<div style="height:10px;line-height:10px">&nbsp;</div>'
        + c.boton("Escribirle por WhatsApp", f"https://wa.me/52{a['whatsapp'][-10:]}", relleno=False, ancho=280))
    aviso = (f"Nueva solicitud de afiliación: {a['nombre']}", c.plantilla(
        preheader=f"{a['nombre']} · {a['redes']}", kicker="Aviso interno", h1="Nueva solicitud de afiliación",
        subtitulo="Llegó desde la landing de afiliados.", cuerpo=interno,
        motivo="Aviso automático de mextradebot.com.mx para el equipo."))
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
    assert "&lt;b&gt;Ana" in html and "<b>Ana" not in html  # el nombre se escapa en el correo
    assert "/recursos/checklist-7-preguntas-mextradebot.pdf" in html and "Descargar el PDF" in html
    assert "/recursos/checklist.html" in html  # ofrece también la otra versión
    assert "img/email/hero.jpg" in html and "Ricardo Peña Covarrubias" in html and "Aviso de riesgo" in html
    assert asunto == "<b>Ana, tu checklist de 7 preguntas está lista"
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
    assert "<script>" not in h1 + h2 and "&lt;script&gt;" in h1 and "&lt;script&gt;" in h2 and "&lt;b&gt;Luis" in h1  # todo se escapa
    assert "affiliate-recruiting/view/9438M107884215" in h1 and "affiliate-recruiting/view/9612M107884304" in h1
    assert "Ricardo Peña Covarrubias" in h1 and "app.hotmart.com/products/manage/8655789" in h2 and "wa.me/524421112233" in h2
    print("api.leads (afiliados) OK")
    print("api.leads.demo() OK")


if __name__ == "__main__":
    demo()
