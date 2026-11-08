"""Correos transaccionales del ecosistema (compra, renovación, pago atrasado, cancelación, reembolso, seguimiento
post-compra y panel de alumnos) con la misma plantilla de las landings (api/correos_landing.py).

Una sola fuente: se renderiza con valores reales (panel de alumnos, en Python) o con expresiones de n8n
(flujos H-00 y H-06): `para_n8n(clave, ref)` devuelve el HTML con `{{ ... }}` listo para pegar en el nodo de correo.
"""

from html import escape

from api import correos_landing as c

# claves: bienvenida, renovacion, atrasado, cancelacion, reembolso, dia2, dia5, dia7 (n8n) · recuperacion, panel (Python)
FIRMA = {"bienvenida": "ricardo", "renovacion": "ricardo", "dia2": "ricardo", "dia5": "ricardo", "dia7": "ricardo", "panel": "ricardo",
         "atrasado": "soporte", "cancelacion": "soporte", "reembolso": "soporte", "recuperacion": "soporte"}

_ESC = '.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")'


def _n8n(ref: str, campo: str) -> str:
    return "{{ String(" + ref + "." + campo + ' ?? "")' + _ESC + " }}"


def _valores_n8n(ref: str) -> dict:
    return {
        "nombre": _n8n(ref, "nombre"), "plan": _n8n(ref, "plan"), "correo": _n8n(ref, "correo"),
        "vence_proximo": "{{ " + ref + '.vence ? "y tu próximo cobro será el " + ' + ref + '.vence : "" }}',
        "vence_hasta": "{{ " + ref + '.vence ? "hasta el " + ' + ref + '.vence : "hasta el final del período ya pagado" }}',
    }


def _valores_py(nombre: str, plan: str, correo: str, vence: str = "") -> dict:
    return {
        "nombre": escape(nombre), "plan": escape(plan), "correo": escape(correo),
        "vence_proximo": f"y tu próximo cobro será el {escape(vence)}" if vence else "",
        "vence_hasta": f"hasta el {escape(vence)}" if vence else "hasta el final del período ya pagado",
    }


def _contacto() -> str:
    return (f'<p style="margin:22px 0 0;font:15px/1.6 Arial,Helvetica,sans-serif;color:{c.INK}">¿Dudas? Escríbenos por '
            f'<a href="{c.WHATSAPP}" style="color:{c.NAVY}">WhatsApp</a> o a '
            f'<a href="mailto:{c.SOPORTE}" style="color:{c.NAVY}">{c.SOPORTE}</a>. Respondemos en días hábiles.</p>')


MOTIVO_COMPRA = "Recibes este correo porque tienes una compra o suscripción de MexTradeBot en Hotmart. Es un mensaje sobre tu cuenta."


def _plantilla(clave: str, v: dict) -> tuple[str, dict]:
    """(asunto base, argumentos de plantilla) de cada correo. `v` ya trae los valores seguros para insertar."""
    b = c.base()
    n, plan, correo = v["nombre"], v["plan"], v["correo"]
    if clave == "bienvenida":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, gracias por unirte a MexTradeBot. Tu plan <b>{plan}</b> ya quedó registrado y tu licencia se activa con el correo de tu compra.")
                  + c.pasos([("Regístrate en el panel con este mismo correo", f"Usa {correo}: con él activamos tu licencia en automático."),
                             ("Descarga tu robot y conéctalo a MetaTrader 5", "El panel te guía paso a paso."),
                             ("Empieza en cuenta DEMO", "Míralo operar con dinero virtual antes de usar dinero real.")])
                  + c.boton("Entrar al panel", f"{b}/alumnos")
                  + c.caja(f"<b>Importante:</b> regístrate con el mismo correo con el que compraste ({correo}). Si usas otro, no podremos activar tu licencia de forma automática.")
                  + c.parrafo("Tienes 7 días de garantía: si decides que no es para ti, puedes pedir la devolución desde Hotmart.")
                  + _contacto())
        return "Tu membresía está activa", dict(kicker="Bienvenida", h1="Tu membresía está activa", subtitulo=f"Plan {plan}. Esto es lo que sigue.",
                                               preheader="Regístrate en el panel con el mismo correo de tu compra y activa tu licencia.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "renovacion":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, recibimos el pago de tu plan <b>{plan}</b>. Tu acceso sigue activo {v['vence_proximo']}.")
                  + c.boton("Ir a mi panel", f"{b}/alumnos")
                  + c.parrafo("<br>Gracias por seguir con nosotros. Si necesitas algo, aquí estamos.")
                  + _contacto())
        return "Recibimos tu pago de renovación", dict(kicker="Renovación", h1="Renovación recibida", subtitulo="Tu acceso sigue activo.",
                                                       preheader="Recibimos el pago de tu plan. Tu acceso sigue activo.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "atrasado":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, no pudimos procesar el pago de tu plan <b>{plan}</b>. Tu acceso quedó en pausa mientras se regulariza.")
                  + c.titulo("Cómo reactivarlo")
                  + c.pasos([("Busca el correo de Hotmart", "Te enviará un enlace para actualizar tu forma de pago."),
                             ("Actualiza tu forma de pago", "Con el pago regularizado, tu acceso se reactiva."),
                             ("¿No te llegó el enlace?", "Escríbenos y te ayudamos a resolverlo.")])
                  + c.boton("Escribir a soporte", "mailto:" + c.SOPORTE)
                  + _contacto())
        return "No pudimos procesar tu pago", dict(kicker="Pago pendiente", h1="No pudimos procesar tu pago", subtitulo="Tu acceso está en pausa mientras se regulariza.",
                                                   preheader="Hotmart te enviará un enlace para actualizar tu forma de pago.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "cancelacion":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, cancelamos tu suscripción al plan <b>{plan}</b>. Conservas tu acceso {v['vence_hasta']}.")
                  + c.caja("<b>Si cancelaste por un problema</b> con el sistema o con tu robot, cuéntanos: queremos resolverlo contigo.")
                  + c.boton("Contarnos qué pasó", "mailto:" + c.SOPORTE)
                  + c.parrafo("<br>Puedes volver cuando quieras desde Hotmart.")
                  + c.boton("Ver los planes", f"{b}/planes.html", relleno=False)
                  + _contacto())
        return "Confirmamos la cancelación de tu suscripción", dict(kicker="Suscripción", h1="Cancelación confirmada", subtitulo="Conservas tu acceso hasta el final de tu período pagado.",
                                                                    preheader="Cancelamos tu suscripción. Puedes volver cuando quieras.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "reembolso":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, registramos una devolución o disputa del pago de tu plan <b>{plan}</b>. Tu licencia quedó en pausa.")
                  + c.caja("Si crees que es un error o quieres retomar tu acceso, escríbenos y lo revisamos contigo.")
                  + c.boton("Escribir a soporte", "mailto:" + c.SOPORTE)
                  + _contacto())
        return "Registramos la devolución de tu pago", dict(kicker="Devolución", h1="Devolución registrada", subtitulo="Tu licencia quedó en pausa.",
                                                            preheader="Registramos la devolución de tu pago. Escríbenos si es un error.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "dia2":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, hace un par de días activaste tu plan <b>{plan}</b>. Si todavía no conectas tu primer robot, esta es la ruta más corta:")
                  + c.pasos([("Abre tu cuenta demo de XM", f'Con nuestros manuales: <a href="{b}/manual-registro-xm.html" style="color:{c.NAVY}">cómo registrarte en XM</a> y '
                              f'<a href="{b}/manual-cuenta-xm.html" style="color:{c.NAVY}">cómo abrir tu cuenta demo</a>.'),
                             ("Descarga un robot para cuenta DEMO", "Entra al panel con el mismo correo de tu compra."),
                             ("Conéctalo a MetaTrader 5", "Déjalo operar en demo, sin arriesgar dinero.")])
                  + c.boton("Abrir el panel de alumnos", f"{b}/alumnos")
                  + c.parrafo("<br>Si en algún paso te atoras, escríbenos y te ayudamos.")
                  + _contacto())
        return "¿Ya conectaste tu primer robot?", dict(kicker="Primeros pasos", h1="Tu primer robot, paso a paso", subtitulo="La ruta más corta para empezar en DEMO.",
                                                      preheader="Abre tu cuenta demo de XM, descarga un robot y conéctalo a MetaTrader 5.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "dia5":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, ya pasaron unos días desde que empezaste. Antes de poner cualquier robot a operar, te recomendamos:")
                  + c.pasos([("Revisar el backtesting", "De cada activo y cada estrategia, en el panel de alumnos, para ver cómo se comportó antes de usarlo."),
                             ("Conectar tu canal de Telegram", "Para recibir las operaciones de tus robots."),
                             ("Ver las lecciones del Club", "Cómo abrir tu cuenta en XM, conectar MetaTrader 5 y descargar tu robot.")])
                  + c.caja("<b>Empieza siempre en cuenta DEMO.</b> Cuando te sientas listo, usas tus descargas en cuenta real con el riesgo que tú decidas.")
                  + c.boton("Ir a mi panel", f"{b}/alumnos")
                  + _contacto())
        return "Saca más provecho de tu membresía", dict(kicker="Tu membresía", h1="Tres cosas que conviene revisar en tu panel", subtitulo="Antes de poner un robot a operar.",
                                                         preheader="Backtesting, Telegram y lecciones del Club: revísalos antes de operar.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    if clave == "dia7":
        cuerpo = (c.parrafo(f"Hola <b>{n}</b>, queremos saber cómo vas con tu plan <b>{plan}</b>.")
                  + c.parrafo("Si algo no quedó claro o no funciona como esperabas, <b>escríbenos antes</b>: lo revisamos contigo. Recuerda que cuentas con 7 días de garantía, que puedes solicitar desde Hotmart si decides que no es para ti.")
                  + c.parrafo("Y si ya estás usando tus robots y todo va bien, cuéntanos tu experiencia: nos ayuda a mejorar.")
                  + c.boton("Contarnos cómo te va", "mailto:" + c.SOPORTE)
                  + _contacto())
        return "Una semana con MexTradeBot: ¿cómo te va?", dict(kicker="Tu primera semana", h1="Cumples tu primera semana", subtitulo="Queremos saber cómo te va.",
                                                               preheader="Si algo no quedó claro, escríbenos antes. Tienes 7 días de garantía.", cuerpo=cuerpo, motivo=MOTIVO_COMPRA)
    raise KeyError(clave)


def para_n8n(clave: str, ref: str = "$json") -> tuple[str, str]:
    """(asunto, html) listos para n8n: cadenas que empiezan con '=' y llevan expresiones {{ ... }}. `ref` = de dónde salen los datos."""
    asunto, kw = _plantilla(clave, _valores_n8n(ref))
    if clave in ("bienvenida", "dia2", "dia5", "dia7"):
        asunto = f"{asunto}, " + _n8n(ref, "nombre") if clave in ("bienvenida",) else asunto
    html = c.plantilla(firma_tipo=FIRMA[clave], **kw)
    return "=" + asunto, "=" + html


def para_correo(clave: str, nombre: str, plan: str, correo: str, vence: str = "") -> tuple[str, str]:
    """Mismo correo con valores reales (pruebas y vista previa)."""
    asunto, kw = _plantilla(clave, _valores_py(nombre, plan, correo, vence))
    return asunto, c.plantilla(firma_tipo=FIRMA[clave], **kw)


# ── panel de alumnos (se usa con valores reales desde api/auth_alumnos.py) ────────────────────────────
def recuperacion(nombre: str, enlace: str) -> tuple[str, str]:
    # El enlace va como TEXTO, no como <a>: el SMTP de Brevo reescribe los <a> con un rastreador de clics y el token pasaría por ese redireccionador.
    primero = escape((nombre or "").split(" ")[0] or "hola")
    cuerpo = (c.parrafo(f"Hola <b>{primero}</b>, recibimos una solicitud para restablecer la contraseña de tu panel de alumnos.")
              + c.parrafo("Abre este enlace para crear una contraseña nueva (si no se abre al tocarlo, cópialo y pégalo en tu navegador):")
              + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#f1f5f9" style="border:1px solid #cbd5e1;border-radius:8px;padding:12px 14px;word-break:break-all;font:13px Consolas,monospace;color:{c.INK}">{escape(enlace)}</td></tr></table>'
              + c.caja("<b>Por tu seguridad:</b> el enlace funciona una sola vez y vence en 1 hora. Si no fuiste tú, ignora este mensaje: tu contraseña actual no cambia.")
              + _contacto())
    return "Restablece tu contraseña | MexTradeBot", c.plantilla(
        preheader="Crea una contraseña nueva con este enlace. Vence en 1 hora.", kicker="Seguridad de tu cuenta", h1="Restablece tu contraseña",
        subtitulo="Solicitaste cambiar la contraseña de tu panel de alumnos.", cuerpo=cuerpo,
        motivo="Recibes este correo porque alguien pidió restablecer la contraseña de tu panel en mextradebot.com.mx.", firma_tipo="soporte")


def panel_bienvenida(nombre: str) -> tuple[str, str]:
    primero = escape((nombre or "").split(" ")[0] or "hola")
    b = c.base()
    cuerpo = (c.parrafo(f"Hola <b>{primero}</b>, tu cuenta del panel de alumnos ya está creada.")
              + c.boton("Entrar al panel", f"{b}/alumnos")
              + c.caja("Entra con este correo y la contraseña que elegiste. Por seguridad no te enviamos la contraseña por correo; si la olvidas, usa «¿Olvidaste tu contraseña?» en la pantalla de inicio de sesión.")
              + c.pasos([("Entra y explora el catálogo", "Revisa el backtesting de cada activo y cada estrategia."),
                         ("Prueba un robot en DEMO", "Con dinero virtual, sin arriesgar capital."),
                         ("Conecta tu Telegram", "Para recibir las operaciones de tus robots.")])
              + _contacto())
    return "Tu cuenta del panel MexTradeBot está lista", c.plantilla(
        preheader="Tu cuenta del panel de alumnos ya está creada. Entra con tu correo y tu contraseña.", kicker="Tu cuenta", h1="Tu cuenta está lista",
        subtitulo="Ya puedes entrar al panel de alumnos.", cuerpo=cuerpo,
        motivo="Recibes este correo porque creaste una cuenta en el panel de alumnos de mextradebot.com.mx.", firma_tipo="ricardo")


def demo() -> None:
    for k in ("bienvenida", "renovacion", "atrasado", "cancelacion", "reembolso", "dia2", "dia5", "dia7"):
        a, h = para_correo(k, "<b>Ana", "Plan <i>Trader", "ana@x.com", "7 de diciembre de 2026")
        assert "&lt;b&gt;Ana" in h and "<b>Ana" not in h and "<i>Trader" not in h, k  # todo se escapa (dia5 no usa el plan)
        assert "img/email/hero.jpg" in h and "Aviso de riesgo" in h and "Aviso de privacidad" in h, k
        assert ("Ricardo Peña Covarrubias" in h) == (FIRMA[k] == "ricardo") and ("Equipo de soporte" in h) == (FIRMA[k] == "soporte"), k
        sa, ha = para_n8n(k, '$("Entrada desde H-00").item.json' if k in ("dia5", "dia7") else "$json")
        assert sa.startswith("=") and ha.startswith("=") and "{{ String(" in ha, k
    a, h = para_correo("renovacion", "Ana", "Trader", "a@x.com", "")
    assert "próximo cobro" not in h  # sin fecha no se inventa
    a, h = recuperacion("<b>Luis", "https://x.test/restablecer?t=1")
    assert "https://x.test/restablecer?t=1" in h and 'href="https://x.test/restablecer' not in h and "Equipo de soporte" in h and "&lt;b&gt;" in h
    a, h = panel_bienvenida("José")
    assert "José" in h and "/alumnos" in h
    print("api.correos_ecosistema.demo() OK")


if __name__ == "__main__":
    demo()
