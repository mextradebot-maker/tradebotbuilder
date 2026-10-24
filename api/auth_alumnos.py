"""Registro, login, logout y recuperacion de contrasena del Panel de Alumnos (antes en n8n).

  POST /webhook/panel-alumnos            registro   {nombre_completo, correo, telefono_recuperacion, password, acepta_terminos=1}
  POST /webhook/panel-alumnos-login      login      {correo, password}
  GET|POST /webhook/panel-alumnos-logout logout
  POST /webhook/panel-alumnos-recuperar  {correo}            -> correo con enlace de un solo uso (1 h); responde igual exista o no
  POST /webhook/panel-alumnos-restablecer {token, password}  -> nueva contrasena, cierra todas las sesiones

Todas responden con una redireccion 303 como el flujo n8n anterior (el panel lee `?error=` / `?aviso=` de la URL final).
Contrasenas: scrypt con sal propia. Las cuentas anteriores (sha256(sal + contrasena), 64 hex) siguen entrando y se
actualizan a scrypt en su primer login correcto. El correo sale por el flujo n8n "MTB Relay Correo" (SMTP de hola@):
este modulo arma el mensaje y n8n solo lo envia (variable MTB_CORREO_KEY en mtb-api = la clave del flujo).
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

log = logging.getLogger(__name__)

BASE = "https://mextradebot.com.mx"
PANEL = f"{BASE}/webhook/panel-alumnos"
COOKIE = "mtb_token={}; Max-Age=604800; Path=/; SameSite=Lax; Secure; HttpOnly"
COOKIE_BORRAR = "mtb_token=; Max-Age=0; Path=/; Secure; HttpOnly"
SESION_DIAS = 7
MIN_PASSWORD = 8
RESET_VIDA = timedelta(hours=1)
RESET_ESPERA = timedelta(seconds=60)  # un correo de recuperacion por cuenta cada 60 s
INTENTOS_LOGIN = 10  # fallos por (ip, correo) en la ventana
VENTANA_LOGIN_S = 600
N8N_CORREO = os.environ.get("N8N_CORREO_URL", "https://chatbotventas-n8n.h0w0dc.easypanel.host/webhook/mtb-correo")

_SCRYPT = (2**14, 8, 1)
_fallos: dict = {}  # (ip, correo) -> [timestamps]; en memoria: el servidor HTTP es un solo proceso


# ── contrasenas ─────────────────────────────────────────────────────────────
def hashear(password: str) -> str:
    n, r, p = _SCRYPT
    sal = secrets.token_hex(16)
    h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(sal), n=n, r=r, p=p, dklen=32).hex()
    return f"scrypt${n}${r}${p}${sal}${h}"


def verificar(password: str, guardado: str, sal: str) -> tuple[bool, bool]:
    """(coincide, hay que actualizar el hash). Formato anterior: sha256(sal + contrasena) en hex."""
    guardado = guardado or ""
    if guardado.startswith("scrypt$"):
        try:
            _, n, r, p, s, h = guardado.split("$")
            calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(s), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2).hex()
        except (ValueError, TypeError):
            return False, False
        return hmac.compare_digest(calc, h), False
    viejo = hashlib.sha256(((sal or "") + password).encode()).hexdigest()
    ok = hmac.compare_digest(viejo, guardado)
    return ok, ok


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── base de datos ───────────────────────────────────────────────────────────
class Repo:
    """Acceso a la tabla alumnos. Se sustituye en las pruebas."""

    def _c(self):
        from persistencia.conexion import get_conn
        return get_conn()

    def por_correo(self, correo: str) -> dict | None:
        with self._c() as c:
            f = c.execute("SELECT id, nombre_completo, correo, password_hash, salt, reset_pedido_en FROM alumnos "
                          "WHERE lower(correo) = lower(%s) LIMIT 1", (correo,)).fetchone()
        return None if not f else dict(zip(("id", "nombre", "correo", "hash", "sal", "reset_pedido_en"), f))

    def crear(self, nombre, correo, telefono, hash_, token, expira) -> bool:
        with self._c() as c:
            f = c.execute("INSERT INTO alumnos (nombre_completo, correo, telefono_recuperacion, password_hash, salt, "
                          "session_token, session_expira) VALUES (%s,%s,%s,%s,'-',%s,%s) "
                          "ON CONFLICT (correo) DO NOTHING RETURNING id",
                          (nombre, correo, telefono, hash_, token, expira)).fetchone()
        return f is not None

    def sesion(self, id_, token, expira) -> None:
        with self._c() as c:
            c.execute("UPDATE alumnos SET session_token=%s, session_expira=%s WHERE id=%s", (token, expira, id_))

    def actualizar_hash(self, id_, hash_) -> None:
        with self._c() as c:
            c.execute("UPDATE alumnos SET password_hash=%s, salt='-' WHERE id=%s", (hash_, id_))

    def guardar_reset(self, id_, token_hash, expira, ahora) -> None:
        with self._c() as c:
            c.execute("UPDATE alumnos SET reset_token_hash=%s, reset_expira=%s, reset_pedido_en=%s WHERE id=%s",
                      (token_hash, expira, ahora, id_))

    def por_reset(self, token_hash, ahora) -> dict | None:
        with self._c() as c:
            f = c.execute("SELECT id, nombre_completo, correo FROM alumnos WHERE reset_token_hash=%s AND reset_expira > %s",
                          (token_hash, ahora)).fetchone()
        return None if not f else dict(zip(("id", "nombre", "correo"), f))

    def nueva_password(self, id_, hash_) -> None:
        """Cambia la contrasena, gasta el enlace y cierra todas las sesiones abiertas."""
        with self._c() as c:
            c.execute("UPDATE alumnos SET password_hash=%s, salt='-', reset_token_hash=NULL, reset_expira=NULL, "
                      "session_token=NULL, session_expira=NULL WHERE id=%s", (hash_, id_))


# ── correo ──────────────────────────────────────────────────────────────────
def _pie() -> str:
    return ("<div style=\"padding:16px 24px;font-size:12px;color:#6b7280;border-top:1px solid #e5e7eb\">MexTradeBot · mextradebot.com.mx<br>"
            "Este mensaje se envía desde hola@mextradebot.com.mx. Para dudas escribe a soporte@mextradebot.com.mx.<br>"
            "El trading conlleva riesgo de pérdida de capital. Los resultados pasados no garantizan resultados futuros. "
            f"Esto no es asesoría financiera. Consulta el <a href=\"{BASE}/aviso-de-riesgo.html\" style=\"color:#6b7280\">aviso de riesgo</a>, "
            f"los <a href=\"{BASE}/terminos-y-condiciones.html\" style=\"color:#6b7280\">términos y condiciones</a> y el "
            f"<a href=\"{BASE}/aviso-de-privacidad.html\" style=\"color:#6b7280\">aviso de privacidad</a>.</div>")


def _marco(titulo: str, cuerpo: str) -> str:
    return ("<div style=\"font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;color:#1a2433;line-height:1.5\">"
            "<div style=\"padding:20px 24px 12px;text-align:center;border-bottom:4px solid #0b1f3a\">"
            f"<img src=\"{BASE}/img/logo-mextradebot.png\" alt=\"MexTradeBot\" width=\"150\" style=\"display:block;margin:0 auto;width:150px;height:auto;border:0\"></div>"
            f"<div style=\"padding:24px\"><h2 style=\"margin-top:0\">{titulo}</h2>{cuerpo}</div>{_pie()}</div>")


def correo_recuperacion(nombre: str, enlace: str) -> tuple[str, str]:
    # El enlace va como TEXTO, no como <a>: el SMTP de Brevo reescribe los <a> con un rastreador de clics y el enlace
    # de recuperacion (con su token) pasaria por ese redireccionador. Gmail y los demas lo vuelven clicable solos.
    primero = escape((nombre or "").split(" ")[0] or "hola")
    cuerpo = (f"<p>Hola {primero}, recibimos una solicitud para restablecer la contraseña de tu panel de alumnos.</p>"
              "<p>Abre este enlace para crear una contraseña nueva (si no se abre al tocarlo, cópialo y pégalo en tu navegador):</p>"
              f"<p style=\"background:#f1f5f9;border:1px solid #cbd5e1;border-radius:8px;padding:12px 14px;word-break:break-all;font-family:monospace;font-size:13px\">{escape(enlace)}</p>"
              "<p>El enlace funciona una sola vez y vence en 1 hora. Si no fuiste tú, ignora este mensaje: tu contraseña actual no cambia.</p>")
    return "Restablece tu contraseña | MexTradeBot", _marco("Restablece tu contraseña", cuerpo)


def correo_bienvenida(nombre: str) -> tuple[str, str]:
    primero = escape((nombre or "").split(" ")[0] or "hola")
    cuerpo = (f"<p>Hola {primero}, tu cuenta del panel de alumnos ya está creada.</p>"
              f"<p>Entra cuando quieras en <a href=\"{BASE}/alumnos\">mextradebot.com.mx/alumnos</a> con este correo y la contraseña que elegiste. "
              "Por seguridad no te enviamos la contraseña por correo; si la olvidas, usa «¿Olvidaste tu contraseña?» en la pantalla de inicio de sesión.</p>")
    return "Tu cuenta del panel MexTradeBot está lista", _marco("Tu cuenta está lista", cuerpo)


def enviar_correo(para: str, asunto: str, html: str) -> bool:
    """Entrega al flujo n8n 'MTB Relay Correo' (SMTP). No lanza: un correo caido no debe romper el registro."""
    clave = os.environ.get("MTB_CORREO_KEY", "")  # clave propia del relay: solo permite enviar correo
    cuerpo = json.dumps({"to": para, "subject": asunto, "html": html}).encode()
    req = urllib.request.Request(N8N_CORREO, data=cuerpo, method="POST", headers={
        "Content-Type": "application/json", "X-MTB-Correo-Key": clave, "User-Agent": "MTB-Auth/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log.warning("no se pudo enviar el correo a n8n: %s", e)
        return False


def _en_segundo_plano(f, *a) -> None:
    threading.Thread(target=f, args=a, daemon=True).start()


# ── acciones ────────────────────────────────────────────────────────────────
def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _frenado(ip: str, correo: str, t: float) -> bool:
    lista = [x for x in _fallos.get((ip, correo), []) if t - x < VENTANA_LOGIN_S]
    _fallos[(ip, correo)] = lista
    return len(lista) >= INTENTOS_LOGIN


def registrar(d: dict, repo, enviar=_en_segundo_plano) -> tuple[int, str, str | None]:
    nombre, correo = (d.get("nombre_completo") or "").strip(), (d.get("correo") or "").strip().lower()
    tel, password = (d.get("telefono_recuperacion") or "").strip(), d.get("password") or ""
    if not (nombre and correo and tel and password) or "@" not in correo or str(d.get("acepta_terminos") or "") != "1":
        return 303, f"{PANEL}?error=datos_invalidos", None
    if len(password) < MIN_PASSWORD:
        return 303, f"{PANEL}?error=contrasena_corta", None
    if repo.por_correo(correo):
        return 303, f"{PANEL}?error=correo_duplicado", None
    token = secrets.token_hex(24)
    if not repo.crear(nombre, correo, tel, hashear(password), token, _ahora() + timedelta(days=SESION_DIAS)):
        return 303, f"{PANEL}?error=correo_duplicado", None
    asunto, html = correo_bienvenida(nombre)
    enviar(enviar_correo, correo, asunto, html)
    return 303, PANEL, COOKIE.format(token)


def entrar(d: dict, repo, ip: str = "", t: float | None = None) -> tuple[int, str, str | None]:
    correo, password = (d.get("correo") or "").strip().lower(), d.get("password") or ""
    t = time.time() if t is None else t
    malo = (303, f"{PANEL}?error=credenciales_invalidas", None)
    if not correo or not password or _frenado(ip, correo, t):
        return malo
    a = repo.por_correo(correo)
    ok, actualizar = verificar(password, a["hash"], a["sal"]) if a else (False, False)
    if not a or not ok:
        _fallos.setdefault((ip, correo), []).append(t)
        return malo
    _fallos.pop((ip, correo), None)
    if actualizar:
        repo.actualizar_hash(a["id"], hashear(password))
    token = secrets.token_hex(24)
    repo.sesion(a["id"], token, _ahora() + timedelta(days=SESION_DIAS))
    return 303, PANEL, COOKIE.format(token)


def salir() -> tuple[int, str, str | None]:
    return 303, PANEL, COOKIE_BORRAR


def pedir_recuperacion(d: dict, repo, enviar=_en_segundo_plano) -> tuple[int, str, str | None]:
    """Responde siempre igual (exista o no el correo) para no revelar quien tiene cuenta."""
    respuesta = (303, f"{PANEL}?aviso=revisa_correo", None)
    correo = (d.get("correo") or "").strip().lower()
    a = repo.por_correo(correo) if "@" in correo else None
    ahora = _ahora()
    if not a or (a.get("reset_pedido_en") and ahora - a["reset_pedido_en"] < RESET_ESPERA):
        return respuesta
    token = secrets.token_urlsafe(32)
    repo.guardar_reset(a["id"], _hash_token(token), ahora + RESET_VIDA, ahora)
    asunto, html = correo_recuperacion(a["nombre"], f"{BASE}/restablecer?token={token}")
    enviar(enviar_correo, a["correo"], asunto, html)
    return respuesta


def restablecer(d: dict, repo) -> tuple[int, str, str | None]:
    token, password = (d.get("token") or "").strip(), d.get("password") or ""
    if len(password) < MIN_PASSWORD:
        return 303, f"{BASE}/restablecer?token={token}&error=contrasena_corta", None
    a = repo.por_reset(_hash_token(token), _ahora()) if token else None
    if not a:
        return 303, f"{BASE}/restablecer?error=enlace_invalido", None
    repo.nueva_password(a["id"], hashear(password))
    return 303, f"{PANEL}?aviso=contrasena_actualizada", None


RUTAS = {
    "/webhook/panel-alumnos": ("POST", lambda d, repo, ip: registrar(d, repo)),
    "/webhook/panel-alumnos-login": ("POST", lambda d, repo, ip: entrar(d, repo, ip)),
    "/webhook/panel-alumnos-logout": ("*", lambda d, repo, ip: salir()),
    "/webhook/panel-alumnos-recuperar": ("POST", lambda d, repo, ip: pedir_recuperacion(d, repo)),
    "/webhook/panel-alumnos-restablecer": ("POST", lambda d, repo, ip: restablecer(d, repo)),
}


def maneja(ruta: str, metodo: str) -> bool:
    r = RUTAS.get(ruta.rstrip("/"))
    return bool(r) and r[0] in ("*", metodo)


def procesar(ruta: str, datos: dict, ip: str = "", repo=None) -> tuple[int, str, str | None]:
    try:
        return RUTAS[ruta.rstrip("/")][1](datos, repo or Repo(), ip)
    except Exception:
        log.exception("auth_alumnos %s fallo", ruta)
        return 303, f"{PANEL}?error=datos_invalidos", None


class RepoMemoria(Repo):
    """Repo en memoria para pruebas locales y de integracion (sin Postgres)."""
    def __init__(self):
        self.f = {}
        self.n = 0

    def _fila(self, a):
        return {"id": a["id"], "nombre": a["nombre"], "correo": a["correo"], "hash": a["hash"], "sal": a["sal"],
                "reset_pedido_en": a.get("reset_pedido_en")}

    def por_correo(self, c):
        a = next((x for x in self.f.values() if x["correo"].lower() == c.lower()), None)
        return self._fila(a) if a else None

    def crear(self, nombre, correo, tel, h, tok, exp):
        if any(x["correo"] == correo for x in self.f.values()):
            return False
        self.n += 1
        self.f[self.n] = dict(id=self.n, nombre=nombre, correo=correo, tel=tel, hash=h, sal="-", tok=tok, exp=exp)
        return True

    def sesion(self, i, t, e): self.f[i].update(tok=t, exp=e)
    def actualizar_hash(self, i, h): self.f[i].update(hash=h, sal="-")
    def guardar_reset(self, i, th, e, a): self.f[i].update(rth=th, rexp=e, reset_pedido_en=a)

    def por_reset(self, th, ahora):
        a = next((x for x in self.f.values() if x.get("rth") == th and x["rexp"] > ahora), None)
        return {"id": a["id"], "nombre": a["nombre"], "correo": a["correo"]} if a else None

    def nueva_password(self, i, h): self.f[i].update(hash=h, sal="-", rth=None, rexp=None, tok=None, exp=None)


def demo() -> None:
    os.environ["MTB_SERVICE_KEY"] = "sk"

    repo, enviados = RepoMemoria(), []
    sin_hilo = lambda f, *a: enviados.append(a)  # captura (correo, asunto, html) sin enviar
    reg = lambda **k: registrar({"nombre_completo": "Ana Prueba", "correo": "Ana@Example.com", "telefono_recuperacion": "55",
                                 "password": "secreta-123", "acepta_terminos": "1", **k}, repo, sin_hilo)
    # hash: scrypt verifica; sha256 anterior verifica y pide actualizar; nada coincide con otra contrasena
    h = hashear("clave-larga-1")
    assert h.startswith("scrypt$") and verificar("clave-larga-1", h, "-") == (True, False) and verificar("otra", h, "-") == (False, False)
    viejo = hashlib.sha256(("131b1a1ee3e1f8d2" + "Prueba-Legacy-123").encode()).hexdigest()
    assert viejo == "3e46b3316ac3eb92808b43a25f984ef19f50be0141d3a0e9abc5fcef4de3110d"  # fila real del flujo n8n
    assert verificar("Prueba-Legacy-123", viejo, "131b1a1ee3e1f8d2") == (True, True) and verificar("x", viejo, "131b1a1ee3e1f8d2") == (False, False)
    assert verificar("x", "basura", "s") == (False, False) and verificar("x", "scrypt$1$2", "") == (False, False)
    # registro
    assert reg(password="corta")[1].endswith("error=contrasena_corta") and reg(acepta_terminos="0")[1].endswith("error=datos_invalidos")
    assert reg(correo="sin-arroba")[1].endswith("error=datos_invalidos") and not repo.f
    st, loc, cookie = reg()
    assert (st, loc) == (303, PANEL) and cookie.startswith("mtb_token=") and "HttpOnly" in cookie and repo.f[1]["correo"] == "ana@example.com"
    assert repo.f[1]["hash"].startswith("scrypt$") and enviados and enviados[0][0] == "ana@example.com" and "Password" not in enviados[0][2]
    assert "secreta-123" not in enviados[0][2]  # nunca se manda la contrasena por correo
    assert reg(correo="ANA@example.com")[1].endswith("error=correo_duplicado")
    # login (correo en cualquier mayuscula), fallos y freno
    assert entrar({"correo": "ANA@EXAMPLE.com", "password": "secreta-123"}, repo, "1.1.1.1")[2].startswith("mtb_token=")
    assert entrar({"correo": "ana@example.com", "password": "mal"}, repo, "1.1.1.1")[1].endswith("error=credenciales_invalidas")
    assert entrar({"correo": "no@existe.com", "password": "x"}, repo, "1.1.1.1")[1].endswith("error=credenciales_invalidas")
    for i in range(INTENTOS_LOGIN):
        entrar({"correo": "ana@example.com", "password": "mal"}, repo, "9.9.9.9", t=1000.0 + i)
    assert entrar({"correo": "ana@example.com", "password": "secreta-123"}, repo, "9.9.9.9", t=1050.0)[2] is None  # frenado
    assert entrar({"correo": "ana@example.com", "password": "secreta-123"}, repo, "9.9.9.9", t=1000.0 + VENTANA_LOGIN_S + 20)[2]  # ventana vencida
    # cuenta anterior (sha256) entra y se actualiza a scrypt
    repo.f[2] = dict(id=2, nombre="Vieja", correo="vieja@example.com", hash=viejo, sal="131b1a1ee3e1f8d2")
    repo.n = 2
    assert entrar({"correo": "vieja@example.com", "password": "Prueba-Legacy-123"}, repo, "2.2.2.2")[2]
    assert repo.f[2]["hash"].startswith("scrypt$") and repo.f[2]["sal"] == "-"
    assert entrar({"correo": "vieja@example.com", "password": "Prueba-Legacy-123"}, repo, "2.2.2.2")[2]  # ya con scrypt
    assert salir()[2] == COOKIE_BORRAR
    # recuperacion: igual respuesta exista o no; solo manda correo si existe; espera de 60 s
    enviados.clear()
    r1 = pedir_recuperacion({"correo": "no@existe.com"}, repo, sin_hilo)
    assert r1 == (303, f"{PANEL}?aviso=revisa_correo", None) and not enviados
    assert pedir_recuperacion({"correo": "ana@example.com"}, repo, sin_hilo) == r1 and len(enviados) == 1
    assert "/restablecer?token=" in enviados[0][2] and escape("Ana") in enviados[0][2]
    assert 'href="' + BASE + '/restablecer' not in enviados[0][2]  # el enlace con token no va en un <a> (rastreador de Brevo)
    pedir_recuperacion({"correo": "ana@example.com"}, repo, sin_hilo)
    assert len(enviados) == 1  # segundo pedido inmediato: ignorado
    token = enviados[0][2].split("/restablecer?token=")[1].split("\"")[0]
    assert repo.f[1]["rth"] == _hash_token(token) and token not in json.dumps({k: v for k, v in repo.f[1].items() if k != "rth"}, default=str)
    # restablecer: contrasena corta, enlace malo, vencido, bueno (un solo uso) y cierra sesiones
    assert restablecer({"token": token, "password": "corta"}, repo)[1].endswith("error=contrasena_corta")
    assert restablecer({"token": "falso", "password": "nueva-clave-9"}, repo)[1].endswith("error=enlace_invalido")
    repo.f[1]["rexp"] = _ahora() - timedelta(seconds=1)
    assert restablecer({"token": token, "password": "nueva-clave-9"}, repo)[1].endswith("error=enlace_invalido")
    repo.f[1]["rexp"] = _ahora() + timedelta(minutes=5)
    assert restablecer({"token": token, "password": "nueva-clave-9"}, repo) == (303, f"{PANEL}?aviso=contrasena_actualizada", None)
    assert repo.f[1]["tok"] is None and repo.f[1]["rth"] is None
    assert restablecer({"token": token, "password": "otra-clave-99"}, repo)[1].endswith("error=enlace_invalido")  # gastado
    assert entrar({"correo": "ana@example.com", "password": "secreta-123"}, repo, "3.3.3.3")[2] is None
    assert entrar({"correo": "ana@example.com", "password": "nueva-clave-9"}, repo, "3.3.3.3")[2]
    # rutas
    assert maneja("/webhook/panel-alumnos", "POST") and not maneja("/webhook/panel-alumnos", "GET")
    assert maneja("/webhook/panel-alumnos-logout", "GET") and maneja("/webhook/panel-alumnos-restablecer/", "POST")
    assert not maneja("/webhook/panel-alumnos-robots", "POST") and not maneja("/webhook/panel-alumnos-demo-status", "GET")
    assert procesar("/webhook/panel-alumnos-login", {"correo": "a@b.c", "password": "x"}, "", repo=type("Roto", (Repo,), {"por_correo": lambda s, c: 1 / 0})())[1].endswith("error=datos_invalidos")
    print("api.auth_alumnos.demo() OK")


if __name__ == "__main__":
    demo()
