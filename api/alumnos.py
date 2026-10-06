"""Panel de Alumnos servido por mtb-api (antes era HTML fijo dentro de n8n).

  GET /alumnos, GET /webhook/panel-alumnos  → plantillas/panel-alumnos.html con los datos de la sesión
  GET /api/v1/mis-licencias                → licencias del alumno (crea su DEMO la primera vez)
  GET /api/v1/mi-robot?id=N&simbolo&temporalidad → .ex5 de una licencia suya y vigente, ya configurado
      para ese activo/temporalidad (sin ellos 400: nunca sale con los valores de fabrica);
      nombre MTB_<cuenta>_<SIMBOLO>_<TF>.ex5 en Content-Disposition y X-MTB-Nombre
  GET /api/v1/mi-robot?simbolo&temporalidad&modo=demo|real&capital  → .ex5 de la ficha de descarga
      (token + presets grabados; cabeceras X-MTB-Token, X-MTB-Cuenta, X-MTB-Simbolo-XM)
  POST /api/v1/telegram/enlace  (X-MTB-Service-Key) {chat_id, simbolo?, temporalidad?} → {url} firmada 7 dias
  POST /api/v1/vincular-telegram (cookie) {tg} → guarda alumnos.telegram_chat_id si la firma es valida
  POST /api/v1/telegram/enviar  (X-MTB-Service-Key) {chat_id, text, parse_mode?, reply_markup?} → sendMessage del bot
      (para mensajes con teclado dinamico que el nodo Telegram de n8n no sabe armar; el token vive en MTB_TELEGRAM_BOT_TOKEN)

La sesión es la cookie `mtb_token` que pone n8n al registrarse / iniciar sesión (tabla alumnos);
registro, login y logout siguen en n8n.
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote

PLANTILLA = Path(__file__).resolve().parent.parent / "plantillas" / "panel-alumnos.html"
CLAVE_CURSO = "MXTB-CURSO-2026"  # la misma que entregaba n8n ("Preparar Datos Pagina") a alumnos con sesión
MENSAJES = {
    "correo_duplicado": "Ese correo ya esta registrado. Si ya tienes cuenta, inicia sesion.",
    "credenciales_invalidas": "Correo o contrasena incorrectos.",
    "datos_invalidos": "Faltan datos o son invalidos. Intenta de nuevo.",
}


def _token_sesion(headers: dict) -> str:
    cookie = next((v for k, v in headers.items() if k.lower() == "cookie"), "") or ""
    for parte in cookie.split(";"):
        nombre, _, valor = parte.strip().partition("=")
        if nombre == "mtb_token":
            return valor
    return ""


def _alumno(headers: dict) -> dict | None:
    from persistencia import licencias

    return licencias.alumno_por_sesion(_token_sesion(headers))


def _js(valor) -> str:
    """Literal JS seguro dentro de <script>: el nombre del alumno lo escribe él mismo."""
    return json.dumps(valor).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def pagina_panel(headers: dict, query: str) -> str:
    error = (parse_qs(query).get("error") or [""])[0]
    try:
        alumno = _alumno(headers)
    except Exception:  # BD caída: se muestra el registro/login en vez de romper la página
        alumno = None
    valores = {
        "__MTB_CLAVEWIZARD__": CLAVE_CURSO if alumno else "",
        "__MTB_NOMBREINICIAL__": alumno["nombre"] if alumno else "",
        "__MTB_ERRORTIPO__": error if error in MENSAJES else "",
        "__MTB_MENSAJEERROR__": MENSAJES.get(error, ""),
    }
    html = PLANTILLA.read_text(encoding="utf-8")
    for marcador, valor in valores.items():
        html = html.replace(marcador, _js(valor))
    return html


def _estado(lic: dict, ahora: datetime) -> str:
    if lic["revocada_en"] is not None:
        return "revocada"
    if lic["expira_en"] is not None and ahora >= lic["expira_en"]:
        return "expirada"
    return "activa"


def procesar_mis_licencias(headers: dict) -> tuple[int, dict]:
    alumno = _alumno(headers)
    if not alumno:
        return 401, {"error": "inicia sesion en el panel"}
    from persistencia import licencias

    licencias.asegurar_licencia_demo(alumno["correo"], alumno["nombre"])
    ahora = datetime.now(timezone.utc)
    return 200, {
        "alumno": {"nombre": alumno["nombre"], "correo": alumno["correo"]},
        "licencias": [
            {"id": l["id"], "tipo": l["tipo"], "prefijo": l["token_prefijo"], "cuenta": l["cuenta"],
             "expira_en": l["expira_en"], "estado": _estado(l, ahora)}
            for l in licencias.licencias_de_correo(alumno["correo"])
        ],
    }


def procesar_mi_robot(datos: dict, headers: dict) -> tuple[int, dict]:
    """"Mis robots" (?id=N&simbolo&temporalidad) o la ficha de descarga (sin id).

    200 → {"ex5", "nombre", "cabeceras"}; api/analizar.py lo manda como descarga con ese nombre.
    """
    if "id" not in datos:
        return _robot_ficha(datos, headers)
    alumno = _alumno(headers)
    if not alumno:
        return 401, {"error": "inicia sesion en el panel"}
    from api.catalogo import SIMBOLO_XM
    from api.licencias import ERROR_SIN_PRESETS, compilar_robot, faltan_presets, nombre_ex5
    from persistencia import licencias

    try:
        lid = int(datos.get("id") or 0)
    except ValueError:
        return 400, {"error": "id invalido"}
    simbolo, temporalidad = datos.get("simbolo") or None, datos.get("temporalidad") or None
    if faltan_presets(simbolo, temporalidad):  # un alumno no sabe reconfigurar el robot: sale listo o no sale
        return 400, {"error": ERROR_SIN_PRESETS}
    lic = next((l for l in licencias.licencias_de_correo(alumno["correo"]) if l["id"] == lid), None)
    if lic is None:  # no existe o es de otro alumno: misma respuesta, no se revela cuál
        return 404, {"error": "licencia no encontrada"}
    if _estado(lic, datetime.now(timezone.utc)) != "activa":
        return 403, {"error": "tu licencia no esta vigente"}
    status, ex5 = compilar_robot(lid, simbolo, temporalidad)  # valida catalogo; 503 si el VPS ignora presets
    if status != 200:
        return status, ex5
    nombre = nombre_ex5(lid, lic["cuenta"], simbolo, temporalidad)
    return 200, {"ex5": ex5, "nombre": nombre, "cabeceras": {
        "X-MTB-Nombre": nombre, "X-MTB-Simbolo-XM": SIMBOLO_XM.get(simbolo) or ""}}


MENSAJE_LIMITE = {
    "demo": "Ya usaste tus {limite} robots DEMO gratis. Con la membresía Trader son ilimitados.",
    "real": "Ya usaste tus {limite} robots en cuenta real del plan Trader. Con VIP son ilimitados.",
}


def _nivel(correo: str, ahora: datetime) -> str | None:
    """Nivel que fija los límites: la membresía de Hotmart; una licencia real/vip emitida a mano
    (admin) no tiene límites, como antes de las membresías."""
    from persistencia import licencias, membresias

    nivel = membresias.nivel_de(correo)
    if nivel:
        return nivel
    manual = any(l["tipo"] in ("real", "vip") and l.get("origen") != "hotmart" and _estado(l, ahora) == "activa"
                 for l in licencias.licencias_de_correo(correo))
    return "vip" if manual else None


def _robot_ficha(datos: dict, headers: dict) -> tuple[int, dict]:
    """200 → {"ex5", "nombre", "cabeceras"}; la API valida todo (el front solo deshabilita botones)."""
    alumno = _alumno(headers)
    if not alumno:
        return 401, {"error": "sesion"}
    from api.catalogo import SIMBOLO_XM
    from api.licencias import compilar_token
    from compilador import TF_POR_TEMPORALIDAD, validar_presets
    from persistencia import licencias, membresias

    modo = str(datos.get("modo") or "").lower()
    try:
        if modo not in ("demo", "real"):
            raise ValueError("modo debe ser demo o real")
        simbolo, temporalidad, capital = validar_presets(datos.get("simbolo"), datos.get("temporalidad"), datos.get("capital"))
    except ValueError as e:
        return 400, {"error": str(e)}

    ahora = datetime.now(timezone.utc)
    nivel = _nivel(alumno["correo"], ahora)
    if modo == "demo":
        lic = licencias.asegurar_licencia_demo(alumno["correo"], alumno["nombre"])
        if nivel and _estado(lic, ahora) == "expirada":  # mientras paguen, su DEMO no caduca
            membresias.renovar_demo(lic["id"])
            lic = {**lic, "expira_en": None}
        if _estado(lic, ahora) != "activa":
            return 403, {"error": "licencia_no_vigente"}
    else:
        lic = next((l for l in licencias.licencias_de_correo(alumno["correo"])
                    if l["tipo"] in ("real", "vip") and _estado(l, ahora) == "activa"), None)
        if lic is None:
            return 402, {"error": "membresia_requerida"}
    limite = membresias.LIMITES[nivel][modo]
    if limite is not None:
        ya = membresias.robots_descargados(alumno["correo"], modo)
        if (simbolo, temporalidad) not in ya and len(ya) >= limite:
            return 402, {"error": "limite_descargas", "modo": modo, "limite": limite,
                         "mensaje": MENSAJE_LIMITE[modo].format(limite=limite)}
    try:
        token = licencias.token_de_licencia(lic["id"])
    except ValueError as e:  # licencia sin semilla o MTB_TOKEN_SECRET ausente
        return 500, {"error": str(e)}

    status, ex5 = compilar_token(token, {"simbolo": simbolo, "temporalidad": temporalidad, "capital": capital})
    if status != 200:
        return status, ex5
    membresias.registrar_descarga(alumno["correo"], modo, simbolo, temporalidad)
    tf = TF_POR_TEMPORALIDAD[temporalidad].removeprefix("PERIOD_")
    return 200, {"ex5": ex5, "nombre": f"MexTradeBot_{simbolo}_{tf}.ex5", "cabeceras": {
        "X-MTB-Token": token, "X-MTB-Cuenta": str(lic["cuenta"] or ""), "X-MTB-Simbolo-XM": SIMBOLO_XM.get(simbolo) or ""}}


# ── Telegram: el bot capta, la web registra y entrega ──────────────────────
URL_FICHA = "https://mextradebot.com.mx/#ficha"
TG_VIGENCIA_S = 7 * 86400


def _firma_tg(chat_id: int, exp: int) -> str:
    secreto = os.environ.get("MTB_TOKEN_SECRET", "")
    if not secreto:
        raise RuntimeError("MTB_TOKEN_SECRET no configurado en el servidor")
    clave = hmac.new(secreto.encode(), b"telegram-link", hashlib.sha256).digest()  # clave propia, no la de tokens
    return hmac.new(clave, f"{chat_id}.{exp}".encode(), hashlib.sha256).hexdigest()


def firmar_tg(chat_id: int, ahora: float) -> str:
    exp = int(ahora) + TG_VIGENCIA_S
    return f"{chat_id}.{exp}.{_firma_tg(chat_id, exp)}"


def verificar_tg(tg: str, ahora: float) -> int | None:
    """chat_id si la firma es valida y vigente; None si no."""
    try:
        chat, exp, firma = str(tg).split(".")
        chat_id, exp = int(chat), int(exp)
    except ValueError:
        return None
    if exp <= ahora or not hmac.compare_digest(firma.encode(), _firma_tg(chat_id, exp).encode()):
        return None
    return chat_id


def procesar_telegram_enlace(datos: dict, headers: dict) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h

    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    from compilador import SIMBOLOS
    from conectividad import resolver_temporalidad

    try:
        chat_id = int(datos.get("chat_id"))
        simbolo = str(datos.get("simbolo") or "").strip().upper()
        temporalidad = str(datos.get("temporalidad") or "").strip()
        if simbolo and simbolo not in SIMBOLOS:
            raise ValueError("simbolo fuera del catalogo")
        if temporalidad:
            if not simbolo:
                raise ValueError("temporalidad requiere simbolo")
            temporalidad = resolver_temporalidad(temporalidad)  # acepta nombres viejos de callbacks ya enviados
    except (TypeError, ValueError) as e:
        return 400, {"error": f"datos invalidos: {e}"}
    try:
        tg = firmar_tg(chat_id, time.time())
    except RuntimeError as e:
        return 503, {"error": str(e)}
    ruta = "".join("/" + quote(p, safe="!~*'()") for p in (simbolo, temporalidad) if p)  # = encodeURIComponent
    return 200, {"url": f"{URL_FICHA}{ruta}?tg={tg}"}


CAMPOS_TG_ENVIAR = ("chat_id", "text", "parse_mode", "reply_markup", "disable_web_page_preview")


def _enviar_tg(token: str, cuerpo: dict) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(cuerpo).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "MexTradeBot-API/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # Telegram explica el rechazo en el body (ok: false, description)
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"ok": False, "description": "respuesta no JSON de Telegram"}


def procesar_telegram_enviar(datos: dict, headers: dict) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h

    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    token = os.environ.get("MTB_TELEGRAM_BOT_TOKEN", "")
    if not token:
        return 503, {"error": "MTB_TELEGRAM_BOT_TOKEN no configurado en el servidor"}
    cuerpo = {k: datos[k] for k in CAMPOS_TG_ENVIAR if datos.get(k) is not None}
    if not cuerpo.get("chat_id") or not str(cuerpo.get("text") or "").strip():
        return 400, {"error": "datos invalidos: faltan chat_id o text"}
    try:
        return _enviar_tg(token, cuerpo)
    except OSError as e:
        return 502, {"error": f"Telegram no disponible: {e}"}


def procesar_vincular_telegram(datos: dict, headers: dict) -> tuple[int, dict]:
    alumno = _alumno(headers)
    if not alumno:
        return 401, {"error": "sesion"}
    try:
        chat_id = verificar_tg(datos.get("tg") or "", time.time())
    except RuntimeError as e:
        return 503, {"error": str(e)}
    if chat_id is None:
        return 400, {"error": "firma_invalida"}
    from persistencia import licencias

    licencias.vincular_telegram(alumno["id"], chat_id)
    return 200, {"ok": True}


def demo() -> None:
    import sys
    import types

    ahora = datetime.now(timezone.utc)
    mia = {"id": 5, "tipo": "demo", "token_prefijo": "MTB-AAAAA", "cuenta": None, "expira_en": None, "revocada_en": None}
    vieja = {**mia, "id": 6, "revocada_en": ahora}
    creadas, vinculados = [], []
    estado = {"demo": mia, "extra": []}  # lo que devuelve asegurar_licencia_demo / licencias reales del alumno
    falso = types.SimpleNamespace(
        alumno_por_sesion=lambda t: {"id": 1, "correo": "ana@x.com", "nombre": "Ana </script>"} if t == "ok" else None,
        licencias_de_correo=lambda c: [*estado["extra"], mia, vieja] if c == "ana@x.com" else [],
        asegurar_licencia_demo=lambda c, n: (creadas.append(c), estado["demo"])[1],
        token_de_licencia=lambda i: f"MTB-TOKEN-{i}",
        vincular_telegram=lambda a, c: vinculados.append((a, c)),
    )
    LIMITES = {None: {"demo": 3, "real": 0}, "trader": {"demo": None, "real": 5}, "vip": {"demo": None, "real": None}}
    mem = {"nivel": None, "bajadas": {}, "renovadas": []}
    falso_mem = types.SimpleNamespace(
        LIMITES=LIMITES,
        nivel_de=lambda c: mem["nivel"],
        robots_descargados=lambda c, m: set(mem["bajadas"].get((c, m), set())),
        registrar_descarga=lambda c, m, s, t: mem["bajadas"].setdefault((c, m), set()).add((s, t)),
        renovar_demo=lambda i: mem["renovadas"].append(i),
    )
    compilados = []
    previos = {k: sys.modules.get(k) for k in ("persistencia", "persistencia.licencias", "persistencia.membresias")}
    sys.modules["persistencia"] = types.SimpleNamespace(licencias=falso, membresias=falso_mem)
    sys.modules["persistencia.licencias"] = falso
    sys.modules["persistencia.membresias"] = falso_mem
    import api.licencias as api_lic

    original = api_lic.compilar_robot
    original_pedir = api_lic._pedir_compilacion
    api_lic.compilar_robot = lambda i, s=None, t=None: (compilados.append((i, s, t)) or (200, b"EX5"))
    previas_env = {k: os.environ.get(k) for k in ("COMPILADOR_URL", "COMPILADOR_KEY", "MTB_TOKEN_SECRET", "MTB_SERVICE_KEY", "MTB_TELEGRAM_BOT_TOKEN")}
    try:
        con = {"Cookie": "otra=1; mtb_token=ok"}
        assert procesar_mis_licencias({})[0] == 401
        st, body = procesar_mis_licencias(con)
        assert st == 200 and creadas == ["ana@x.com"], (st, body)
        assert [l["estado"] for l in body["licencias"]] == ["activa", "revocada"]

        # ── Mis robots: siempre con activo + temporalidad, nombre del servidor ──
        oro = {"simbolo": "XAUUSD", "temporalidad": "Swing (S)"}
        assert procesar_mi_robot({"id": "5", **oro}, {})[0] == 401
        assert procesar_mi_robot({"id": "99", **oro}, con)[0] == 404          # no es suya
        assert procesar_mi_robot({"id": "6", **oro}, con)[0] == 403           # revocada
        for sin in ({}, {"simbolo": "XAUUSD"}, {"temporalidad": "Swing (S)"}, {"simbolo": "", "temporalidad": ""}):
            st, body = procesar_mi_robot({"id": "5", **sin}, con)              # id pelon: ya no hay robot de fabrica
            assert st == 400 and "activo y la temporalidad" in body["error"], (sin, st, body)
        assert compilados == []
        st, body = procesar_mi_robot({"id": "5", **oro}, con)
        assert st == 200 and body == {"ex5": b"EX5", "nombre": "MTB_lic5_XAUUSD_SwingS.ex5", "cabeceras": {
            "X-MTB-Nombre": "MTB_lic5_XAUUSD_SwingS.ex5", "X-MTB-Simbolo-XM": "GOLD"}}, body
        assert compilados == [(5, "XAUUSD", "Swing (S)")], compilados
        mia["cuenta"] = 318680674                                              # amarrada: el nombre lleva la cuenta
        assert procesar_mi_robot({"id": "5", "simbolo": "US30", "temporalidad": "Intraday 4H"}, con)[1]["nombre"] \
            == "MTB_318680674_US30_Intraday4H.ex5"
        mia["cuenta"] = None
        api_lic.compilar_robot = original                                      # el de verdad: catalogo cerrado
        for malo in ({"simbolo": "GOLD"}, {"temporalidad": "Swing"}, {"simbolo": 'XAUUSD";'}):
            os.environ.update(COMPILADOR_URL="https://c/compilar", COMPILADOR_KEY="ck")
            assert procesar_mi_robot({"id": "5", **oro, **malo}, con)[0] == 400, malo
        os.environ.pop("COMPILADOR_URL"), os.environ.pop("COMPILADOR_KEY")

        html = pagina_panel(con, "error=credenciales_invalidas")
        assert "var MTB_CLAVE = \"MXTB-CURSO-2026\";" in html
        assert "Ana \\u003c/script\\u003e" in html and "Ana </script>" not in html  # sin XSS
        assert "Correo o contrasena incorrectos." in html
        anon = pagina_panel({}, "error=<script>")
        assert "var MTB_CLAVE = \"\";" in anon and "var MTB_ERROR_TIPO = \"\";" in anon
        assert "__MTB_" not in anon and "vercel.app" not in anon

        # ── ficha de descarga: mi-robot con presets ──
        os.environ.update(COMPILADOR_URL="https://c/compilar", COMPILADOR_KEY="ck")
        pedidos = []
        compilador_nuevo = {"si": True}
        api_lic._pedir_compilacion = lambda u, k, t, p=None: (pedidos.append((t, p)), (b"EX5P", compilador_nuevo["si"]))[1]
        ficha = {"simbolo": "XAUUSD", "temporalidad": "Intraday 1H", "modo": "demo", "capital": "10000"}
        assert procesar_mi_robot(ficha, {}) == (401, {"error": "sesion"})
        for malo in ({"capital": "299"}, {"capital": "mil"}, {"modo": "vip"}, {"simbolo": 'XAUUSD";'},
                     {"temporalidad": "Swing"}, {"simbolo": None}):
            assert procesar_mi_robot({**ficha, **malo}, con)[0] == 400, malo
        assert pedidos == []  # nada invalido llega al compilador

        st, body = procesar_mi_robot(ficha, con)
        assert st == 200 and body["ex5"] == b"EX5P" and body["nombre"] == "MexTradeBot_XAUUSD_H1.ex5", (st, body)
        assert body["cabeceras"] == {"X-MTB-Token": "MTB-TOKEN-5", "X-MTB-Cuenta": "", "X-MTB-Simbolo-XM": "GOLD"}
        assert pedidos == [("MTB-TOKEN-5", {"simbolo": "XAUUSD", "temporalidad": "Intraday 1H", "capital": 10000.0})]

        estado["demo"] = {**mia, "cuenta": 318680674}  # DEMO amarrada: la cabecera lo dice
        st, body = procesar_mi_robot({**ficha, "simbolo": "US30", "temporalidad": "Swing (M)"}, con)
        assert st == 200 and body["nombre"] == "MexTradeBot_US30_MN1.ex5"
        assert body["cabeceras"]["X-MTB-Cuenta"] == "318680674" and body["cabeceras"]["X-MTB-Simbolo-XM"] == "US30Cash"
        estado["demo"] = vieja
        assert procesar_mi_robot(ficha, con) == (403, {"error": "licencia_no_vigente"})

        real = {**ficha, "modo": "real"}
        assert procesar_mi_robot(real, con) == (402, {"error": "membresia_requerida"})  # solo tiene DEMO
        estado["extra"] = [{**mia, "id": 8, "tipo": "real", "expira_en": ahora}]       # REAL vencida
        assert procesar_mi_robot(real, con) == (402, {"error": "membresia_requerida"})
        estado["extra"].append({**mia, "id": 9, "tipo": "vip", "cuenta": 777})
        st, body = procesar_mi_robot(real, con)
        assert st == 200 and body["cabeceras"]["X-MTB-Token"] == "MTB-TOKEN-9" and body["cabeceras"]["X-MTB-Cuenta"] == "777"

        # ── límites por nivel (robots distintos = símbolo + temporalidad) ──
        assert mem["bajadas"][("ana@x.com", "demo")] == {("XAUUSD", "Intraday 1H"), ("US30", "Swing (M)")}
        assert mem["bajadas"][("ana@x.com", "real")] == {("XAUUSD", "Intraday 1H")}
        admin_vip, estado["extra"], estado["demo"] = estado["extra"], [], mia  # Gratis: sin licencias pagadas
        def bajar(simbolo, temporalidad="Intraday 4H", modo="demo"):
            return procesar_mi_robot({**ficha, "simbolo": simbolo, "temporalidad": temporalidad, "modo": modo}, con)
        assert bajar("EURUSD")[0] == 200                                   # 3.º robot DEMO
        st, body = bajar("GBPUSD")
        assert st == 402 and body["error"] == "limite_descargas" and body["limite"] == 3 and "Trader" in body["mensaje"]
        assert bajar("XAUUSD", "Intraday 1H")[0] == 200                     # repetir uno ya bajado no gasta cupo
        assert bajar("GBPUSD", modo="real") == (402, {"error": "membresia_requerida"})
        mem["nivel"] = "trader"
        estado["extra"] = [{**mia, "id": 10, "tipo": "real", "origen": "hotmart"}]
        assert bajar("GBPUSD")[0] == 200                                    # Trader: DEMO ilimitado
        for sim in ("EURUSD", "USDJPY", "AUDUSD", "USDCAD"):
            assert bajar(sim, modo="real")[0] == 200, sim                    # 2.º a 5.º REAL
        st, body = bajar("NZDUSD", modo="real")
        assert st == 402 and body["limite"] == 5 and "VIP" in body["mensaje"]
        assert bajar("EURUSD", modo="real")[0] == 200
        estado["demo"] = {**mia, "expira_en": ahora}                         # DEMO de registro caducada
        assert bajar("EURUSD")[0] == 200 and mem["renovadas"] == [5]          # miembro: se renueva
        mem["nivel"] = "vip"
        estado["extra"] = [{**mia, "id": 11, "tipo": "vip", "origen": "hotmart"}]
        assert bajar("NZDUSD", modo="real")[0] == 200                        # VIP: ilimitado
        mem["nivel"], estado["extra"] = None, []
        assert bajar("EURUSD")[0] == 403 and mem["renovadas"] == [5]          # sin membresía: no se renueva
        estado["extra"], estado["demo"] = admin_vip, mia
        assert bajar("NZDUSD", "Swing (S)")[0] == 200                        # licencia manual: sin límites

        compilador_nuevo["si"] = False  # VPS sin actualizar: no se entrega un .ex5 sin presets
        assert procesar_mi_robot(real, con) == (503, {"error": "generador_actualizando"})
        pedidos.clear()                 # ...tampoco por "Mis robots" (?id=N): mismo candado
        assert procesar_mi_robot({"id": "5", "simbolo": "EURUSD", "temporalidad": "Intraday 1H"}, con) == \
            (503, {"error": "generador_actualizando"})
        assert pedidos == [("MTB-TOKEN-5", {"simbolo": "EURUSD", "temporalidad": "Intraday 1H"})], pedidos
        compilador_nuevo["si"] = True

        # ── Telegram: enlace firmado + vinculacion ──
        os.environ.pop("MTB_SERVICE_KEY", None)
        os.environ["MTB_TOKEN_SECRET"] = "secreto"
        pedido = {"chat_id": 123456, "simbolo": "xauusd", "temporalidad": "Swing (S)"}
        assert procesar_telegram_enlace(pedido, {})[0] == 401  # sin clave configurada: cerrado
        os.environ["MTB_SERVICE_KEY"] = "svc"
        svc = {"X-MTB-Service-Key": "svc"}
        assert procesar_telegram_enlace(pedido, {"X-MTB-Service-Key": "otra"})[0] == 401
        st, body = procesar_telegram_enlace(pedido, svc)
        assert st == 200 and body["url"].startswith("https://mextradebot.com.mx/#ficha/XAUUSD/Swing%20(S)?tg=123456."), body
        tg = body["url"].split("?tg=")[1]
        assert verificar_tg(tg, time.time()) == 123456
        assert verificar_tg(tg[:-1] + ("0" if tg[-1] != "0" else "1"), time.time()) is None   # firma alterada
        assert verificar_tg(tg.replace("123456.", "123457.", 1), time.time()) is None           # otro chat
        assert verificar_tg(tg, time.time() + TG_VIGENCIA_S + 1) is None                         # vencida
        for basura in ("", "abc", "1.2", "1.2.3.4", "x.1.ff", "1.2.ñ"):
            assert verificar_tg(basura, time.time()) is None, basura
        os.environ["MTB_TOKEN_SECRET"] = "otro"
        assert verificar_tg(tg, time.time()) is None  # otra clave
        os.environ["MTB_TOKEN_SECRET"] = "secreto"
        assert procesar_telegram_enlace({"chat_id": 5, "temporalidad": "Scalping", "simbolo": "EURUSD"}, svc)[1]["url"] \
            .startswith("https://mextradebot.com.mx/#ficha/EURUSD/Scalping%2015m?tg=5.")  # alias viejo -> canonico
        assert procesar_telegram_enlace({"chat_id": 5}, svc)[1]["url"].startswith("https://mextradebot.com.mx/#ficha?tg=5.")
        for malo in ({"simbolo": "GOLD"}, {"chat_id": "x"}, {"chat_id": None}, {"temporalidad": "Diario"},
                     {"simbolo": "", "temporalidad": "Intraday 1H"}):
            assert procesar_telegram_enlace({**pedido, **malo}, svc)[0] == 400, malo

        assert procesar_vincular_telegram({"tg": tg}, {}) == (401, {"error": "sesion"})
        assert procesar_vincular_telegram({"tg": tg[:-2]}, con) == (400, {"error": "firma_invalida"})
        assert procesar_vincular_telegram({}, con) == (400, {"error": "firma_invalida"})
        assert vinculados == []
        assert procesar_vincular_telegram({"tg": tg}, con) == (200, {"ok": True}) and vinculados == [(1, 123456)]
        os.environ.pop("MTB_TOKEN_SECRET")
        assert procesar_telegram_enlace(pedido, svc)[0] == 503

        # ── Telegram: envio con teclado dinamico ──
        global _enviar_tg
        original_enviar, enviados = _enviar_tg, []
        _enviar_tg = lambda t, c: (enviados.append((t, c)), (200, {"ok": True}))[1]
        msg = {"chat_id": 5, "text": "menu", "parse_mode": "Markdown", "extra": "x",
               "reply_markup": {"inline_keyboard": [[{"text": "EURUSD", "callback_data": "a|EURUSD"}]]}}
        try:
            assert procesar_telegram_enviar(msg, {"X-MTB-Service-Key": "otra"})[0] == 401
            os.environ.pop("MTB_TELEGRAM_BOT_TOKEN", None)
            assert procesar_telegram_enviar(msg, svc)[0] == 503
            os.environ["MTB_TELEGRAM_BOT_TOKEN"] = "123:abc"
            for malo in ({"chat_id": None}, {"text": "  "}, {"text": None}):
                assert procesar_telegram_enviar({**msg, **malo}, svc)[0] == 400, malo
            assert enviados == []
            assert procesar_telegram_enviar(msg, svc) == (200, {"ok": True})
            assert enviados == [("123:abc", {k: v for k, v in msg.items() if k != "extra"})]  # solo campos permitidos
        finally:
            _enviar_tg = original_enviar
    finally:
        api_lic.compilar_robot = original
        api_lic._pedir_compilacion = original_pedir
        for k, v in previas_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k, v in previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    # el selector de "Mis robots" ofrece exactamente el catalogo que acepta el compilador
    import re

    from compilador import SIMBOLOS, TF_POR_TEMPORALIDAD

    panel = PLANTILLA.read_text(encoding="utf-8")
    def _lista(nombre):
        bloque = re.search(rf"var {nombre} = \[(.*?)\];", panel, re.S)
        assert bloque, f"plantillas/panel-alumnos.html ya no define var {nombre}"
        return re.findall(r"'([^']*)'", bloque.group(1))
    assert _lista("MTB_SIMBOLOS") == list(SIMBOLOS), _lista("MTB_SIMBOLOS")
    assert _lista("MTB_TEMPORALIDADES") == list(TF_POR_TEMPORALIDAD), _lista("MTB_TEMPORALIDADES")
    assert "mi-robot?id=" in panel and "&simbolo=" in panel and "X-MTB-Nombre" in panel
    assert "a.download = 'MexTradeBot_SeguidorSMC.ex5'" not in panel  # el nombre lo decide el servidor
    print("api.alumnos.demo() OK")


if __name__ == "__main__":
    demo()
