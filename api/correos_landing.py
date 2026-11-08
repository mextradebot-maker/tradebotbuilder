"""Plantilla de correo de las landings (ventas y afiliados): logo, portada, titular, cuerpo, firma de Ricardo y pie legal.

HTML de tablas con estilos en línea (lo que sí respetan Gmail y Outlook). Las imágenes se piden a MTB_BASE_URL.
No toca api/auth_alumnos._marco: los correos del panel siguen igual.
"""

import os
from html import escape

NAVY, TEAL, LIME, INK, MUTED = "#060d24", "#2dd4bf", "#c6ea3b", "#0d1530", "#5b6788"
WHATSAPP = "https://wa.me/524421851478"
TELEGRAM = "https://t.me/mextradebot_comunidad"
SOPORTE = "soporte@mextradebot.com.mx"
HOTMART_TRADER = "https://affiliate.hotmart.com/affiliate-recruiting/view/9438M107884215"
HOTMART_VIP = "https://affiliate.hotmart.com/affiliate-recruiting/view/9612M107884304"


def base() -> str:
    return os.environ.get("MTB_BASE_URL", "https://mextradebot.com.mx").rstrip("/")


def boton(texto: str, url: str, relleno: bool = True, ancho: int = 0) -> str:
    fondo, color, borde = (LIME, NAVY, LIME) if relleno else ("#ffffff", NAVY, NAVY)
    td_ancho = f' width="{ancho}"' if ancho else ""
    a_estilo = "display:block;padding:14px 0" if ancho else "display:inline-block;padding:14px 30px"
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto"><tr>'
            f'<td align="center" bgcolor="{fondo}"{td_ancho} style="border-radius:10px;border:2px solid {borde}">'
            f'<a href="{url}" style="{a_estilo};font:bold 16px Arial,Helvetica,sans-serif;color:{color};text-decoration:none">{texto}</a>'
            f'</td></tr></table>')


def titulo(texto: str) -> str:
    return f'<h2 style="margin:28px 0 10px;font:bold 18px Arial,Helvetica,sans-serif;color:{INK}">{texto}</h2>'


def parrafo(texto: str) -> str:
    return f'<p style="margin:0 0 14px;font:16px/1.6 Arial,Helvetica,sans-serif;color:{INK}">{texto}</p>'


def pasos(items: list[tuple[str, str]]) -> str:
    filas = ""
    for n, (t, d) in enumerate(items, 1):
        filas += (f'<tr><td width="40" valign="top" style="padding:0 0 14px"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
                  f'<td width="30" height="30" align="center" bgcolor="{NAVY}" style="border-radius:15px;font:bold 14px Arial,sans-serif;color:{LIME}">{n}</td></tr></table></td>'
                  f'<td valign="top" style="padding:0 0 14px;font:15px/1.5 Arial,Helvetica,sans-serif;color:{INK}"><b>{t}</b><br>'
                  f'<span style="color:{MUTED}">{d}</span></td></tr>')
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{filas}</table>'


def caja(html: str) -> str:
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 14px"><tr>'
            f'<td bgcolor="#f1f4fb" style="border-left:4px solid {TEAL};border-radius:6px;padding:14px 18px;font:15px/1.6 Arial,Helvetica,sans-serif;color:{INK}">{html}</td></tr></table>')


def firma(tipo: str = "ricardo") -> str:
    """Firma con foto y datos. 'ricardo' = personal (bienvenidas, seguimiento); 'soporte' = equipo (seguridad, pagos, cancelaciones)."""
    b = base()
    enlaces = " &nbsp;·&nbsp; ".join(
        f'<a href="{u}" style="color:{NAVY};text-decoration:underline">{t}</a>'
        for t, u in (("mextradebot.com.mx", b + "/"), ("WhatsApp", WHATSAPP), ("Telegram", TELEGRAM), (SOPORTE, "mailto:" + SOPORTE)))
    if tipo == "soporte":
        avatar = (f'<img src="{b}/img/logo-mtb.png" width="76" height="76" alt="MexTradeBot" '
                  f'style="display:block;width:76px;height:76px;border-radius:14px;border:1px solid #e1e6f2;background:#ffffff;border-width:1px">')
        texto = (f'<span style="font:15px/1.5 Arial,Helvetica,sans-serif;color:{MUTED}">Estamos para ayudarte,</span><br>'
                 f'<b style="font-size:17px">Equipo de soporte de MexTradeBot</b><br>'
                 f'<span style="color:{MUTED}">Respondemos en días hábiles</span><br>')
    else:
        avatar = (f'<img src="{b}/img/email/ricardo.jpg" width="76" height="76" alt="Ricardo Peña" '
                  f'style="display:block;width:76px;height:76px;border-radius:38px;border:0">')
        texto = (f'<span style="font:15px/1.5 Arial,Helvetica,sans-serif;color:{MUTED}">Un abrazo,</span><br>'
                 f'<b style="font-size:17px">Ricardo Peña Covarrubias</b><br>'
                 f'Fundador de MexTradeBot<br>'
                 f'<span style="color:{MUTED}">Ponente invitado en The Trading Festival México</span><br>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:30px;border-top:1px solid #e1e6f2"><tr>'
            f'<td width="92" valign="top" style="padding:22px 16px 0 0">{avatar}</td>'
            f'<td valign="top" style="padding:22px 0 0;font:14px/1.6 Arial,Helvetica,sans-serif;color:{INK}">{texto}'
            f'<span style="font-size:13px">{enlaces}</span></td></tr></table>')


def pie(motivo: str, extra: str = "") -> str:
    b = base()
    return (f'<tr><td bgcolor="#f5f7fb" style="padding:22px 32px;font:12px/1.6 Arial,Helvetica,sans-serif;color:{MUTED}">'
            f'{motivo}<br><br>{extra}'
            f'El trading conlleva riesgo de pérdida de capital, incluida la pérdida total. Los resultados pasados, incluidos los de DEMO y backtesting, '
            f'no garantizan resultados futuros. Esto no es asesoría financiera. MexTradeBot no es asesor de inversiones ni representante de XM.<br><br>'
            f'<a href="{b}/aviso-de-privacidad.html" style="color:{MUTED}">Aviso de privacidad</a> · '
            f'<a href="{b}/aviso-de-riesgo.html" style="color:{MUTED}">Aviso de riesgo</a> · '
            f'<a href="{b}/terminos-y-condiciones.html" style="color:{MUTED}">Términos y condiciones</a><br>'
            f'MexTradeBot · Querétaro, Qro., México · {SOPORTE}</td></tr>')


def plantilla(preheader: str, kicker: str, h1: str, subtitulo: str, cuerpo: str, motivo: str, extra_pie: str = "", firma_tipo: str = "ricardo") -> str:
    b = base()
    oculto = "&#8199;&#65279;&#847; " * 30
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px">{escape(preheader)}{oculto}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#e9edf6"><tr><td align="center" style="padding:24px 10px">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#ffffff;border-radius:14px;overflow:hidden">'
        f'<tr><td align="center" style="padding:18px 32px"><a href="{b}/"><img src="{b}/img/logo-mextradebot.png" width="150" alt="MexTradeBot" style="display:block;width:150px;height:auto;border:0"></a></td></tr>'
        f'<tr><td style="line-height:0;font-size:0" bgcolor="{NAVY}"><img src="{b}/img/email/hero.jpg" width="600" alt="MexTradeBot: robots de trading para MetaTrader 5" '
        f'style="display:block;width:100%;max-width:600px;height:auto;border:0"></td></tr>'
        f'<tr><td bgcolor="{NAVY}" align="left" style="padding:30px 32px 32px">'
        f'<div style="font:bold 12px Arial,Helvetica,sans-serif;letter-spacing:2px;color:{TEAL};text-transform:uppercase;margin-bottom:10px">{kicker}</div>'
        f'<h1 style="margin:0 0 10px;font:bold 28px/1.2 Arial,Helvetica,sans-serif;color:#ffffff">{h1}</h1>'
        f'<div style="font:17px/1.5 Arial,Helvetica,sans-serif;color:#b9c5e3">{subtitulo}</div></td></tr>'
        f'<tr><td style="padding:30px 32px 34px">{cuerpo}{firma(firma_tipo)}</td></tr>'
        f'{pie(motivo, extra_pie)}'
        f'</table></td></tr></table>')
