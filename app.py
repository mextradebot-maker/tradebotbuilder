"""Servidor único de MexTradeBot en el VPS (EasyPanel).

  /api/*                 → router de siempre (api/analizar.py), sin cambios de lógica
  GET /alumnos, GET /webhook/panel-alumnos → Panel de Alumnos (plantillas/panel-alumnos.html, api/alumnos.py)
  /webhook/*             → n8n: registro, login, logout y demás webhooks
  /salud                 → healthcheck para EasyPanel
  todo lo demás          → archivos estáticos de public/ (panel, master.html, manuales)

Además arranca el refresco de snapshots SMC en segundo plano (refresco.py).
Variables: PORT (8000), N8N_URL, REFRESCO_ACTIVO=0 para apagarlo, REFRESCO_PROCESOS=N (por defecto 1) reparte el refresco entre N procesos en paralelo (pide CPU >= N), ALMACEN_CARGA_ACTIVA=0 apaga la carga
historica de velas (carga_historica.py), BACKTEST_LARGO_ACTIVO=0 apaga el backtest largo incremental (backtest_largo.py), REFRESCO_HILOS (hilos del
refresco, I/O-bound; por defecto 6, 1 = en serie; REFRESCO_WORKERS es alias obsoleto), más las de la API
(DATABASE_URL, MTB_ADMIN_KEY, MTB_SERVICE_KEY).
"""

import http.client
import json
import logging
import multiprocessing
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from api.analizar import handler as ApiHandler

PUBLICO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
N8N = urlsplit(os.environ.get("N8N_URL", "https://chatbotventas-n8n.h0w0dc.easypanel.host"))
RUTAS_N8N = {"/alumnos": "/webhook/panel-alumnos"}
RUTAS_PANEL = {"/alumnos", "/webhook/panel-alumnos"}  # GET: la página la arma mtb-api; POST (registro) sigue a n8n
_SIN_REENVIO = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding"}

# Limite por IP para /api/* sin autenticar (analizar, tendencia, backtest, setups, catalogo...): sin esto
# cualquiera podia automatizar llamadas sin freno (hallazgo de la auditoria de seguridad, 6 nov 2026).
# Una peticion ya autenticada (cookie de alumno, Authorization del robot, o las X-* de servicio/admin/Hotmart)
# no cuenta — ese llamador ya esta identificado y su propio endpoint decide si lo deja pasar.
_RATE_LIMITE = int(os.environ.get("MTB_API_RATE_LIMIT", "30"))  # peticiones
_RATE_VENTANA = int(os.environ.get("MTB_API_RATE_WINDOW", "10"))  # segundos
_rate_lock = threading.Lock()
_rate_marcas: dict[str, list[float]] = {}
_CABECERAS_AUTH = ("authorization", "x-mtb-service-key", "x-admin-key", "x-hotmart-hottok", "cookie")


def _excedido(ip: str, ahora: float, limite: int = _RATE_LIMITE, ventana: int = _RATE_VENTANA) -> bool:
    """Ventana deslizante por IP, en memoria. Logica pura (sin tocar la peticion HTTP) para poder
    probarla sin levantar un servidor; _limite_api la envuelve con la IP y el reloj reales."""
    with _rate_lock:
        marcas = [t for t in _rate_marcas.get(ip, ()) if ahora - t < ventana]
        excedido = len(marcas) >= limite
        if not excedido:
            marcas.append(ahora)
        _rate_marcas[ip] = marcas
    return excedido


class Handler(SimpleHTTPRequestHandler):
    _responder = ApiHandler._responder
    _responder_archivo = ApiHandler._responder_archivo  # descargas (.ex5) del router de la API
    _responder_html = ApiHandler._responder_html        # paginas HTML del router (aceptacion del Master)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLICO, **kwargs)

    def end_headers(self) -> None:
        # Un solo punto para TODA respuesta (estaticos, API, descargas, HTML): send_response/send_header/
        # end_headers es el flujo que usan por igual SimpleHTTPRequestHandler y los _responder* de arriba.
        # Cabeceras que faltaban (auditoria de seguridad, 6 nov 2026). Sin CSP: el sitio usa scripts inline
        # y CDNs (Tailwind, Google Fonts) y retocar eso a ciegas puede romper paginas; no se pidio en la auditoria.
        self.send_header("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def _ruta(self) -> str:
        return urlsplit(self.path).path

    def _es_n8n(self) -> bool:
        r = self._ruta().rstrip("/")
        return r in RUTAS_N8N or r.startswith("/webhook/")

    def _ip(self) -> str:
        return (self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def _limite_api(self) -> bool:
        """True si hay que frenar esta peticion a /api/* (429). Deja pasar sin contar a quien ya trae
        una cabecera de autenticacion; cada endpoint protegido sigue validandola por su cuenta."""
        if any(self.headers.get(h) for h in _CABECERAS_AUTH):
            return False
        return _excedido(self._ip(), time.monotonic())

    def _responder_limite(self) -> None:
        cuerpo = json.dumps({"error": "demasiadas peticiones, intenta de nuevo en unos segundos"}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", str(_RATE_VENTANA))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self._ruta()
        if ruta.startswith("/api/"):
            if self._limite_api():
                return self._responder_limite()
            return ApiHandler.do_GET(self)
        if ruta.rstrip("/") == "/restablecer":  # enlace del correo de recuperacion de contrasena
            self.path = "/restablecer.html" + (f"?{urlsplit(self.path).query}" if urlsplit(self.path).query else "")
            return super().do_GET()
        from api import auth_alumnos

        if auth_alumnos.maneja(ruta, "GET"):  # logout
            return self._auth({})
        if ruta.rstrip("/") in RUTAS_PANEL:
            return self._panel()
        if self._es_n8n():
            return self._a_n8n("GET")
        if ruta == "/salud":
            return self._responder(200, {"ok": True})
        return super().do_GET()

    def do_HEAD(self):
        if self._es_n8n():
            return self._a_n8n("HEAD")
        return super().do_HEAD()

    def do_POST(self):
        from api import auth_alumnos

        if auth_alumnos.maneja(self._ruta(), "POST"):  # registro, login, recuperacion de contrasena
            largo = int(self.headers.get("Content-Length", 0) or 0)
            crudo = self.rfile.read(min(largo, 20000)).decode("utf-8", "replace")
            if "json" in (self.headers.get("Content-Type") or "").lower():
                try:
                    datos = json.loads(crudo or "{}")
                except ValueError:
                    datos = {}
            else:
                datos = {k: v[0] for k, v in parse_qs(crudo, keep_blank_values=True).items()}
            return self._auth(datos if isinstance(datos, dict) else {})
        if self._es_n8n():
            return self._a_n8n("POST")
        if self._ruta().startswith("/api/") and self._limite_api():
            return self._responder_limite()
        return ApiHandler.do_POST(self)

    def _auth(self, datos: dict) -> None:
        from api import auth_alumnos

        status, destino, cookie = auth_alumnos.procesar(self._ruta(), datos, self._ip())
        self.send_response(status)
        self.send_header("Location", destino)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _panel(self) -> None:
        from api.alumnos import pagina_panel

        datos = pagina_panel(dict(self.headers), urlsplit(self.path).query).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _a_n8n(self, metodo: str) -> None:
        partes = urlsplit(self.path)
        destino = RUTAS_N8N.get(partes.path.rstrip("/"), partes.path) + (f"?{partes.query}" if partes.query else "")
        cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) if metodo == "POST" else None
        cabeceras = {k: v for k, v in self.headers.items() if k.lower() not in _SIN_REENVIO}
        cabeceras["X-Forwarded-For"] = self.client_address[0]
        Conexion = http.client.HTTPSConnection if N8N.scheme == "https" else http.client.HTTPConnection
        conn = Conexion(N8N.netloc, timeout=60)
        try:
            conn.request(metodo, destino, body=cuerpo, headers=cabeceras)
            r = conn.getresponse()
            datos = r.read()
        except OSError as e:
            return self._responder(502, {"error": f"n8n no disponible: {e}"})
        finally:
            conn.close()
        self.send_response(r.status)
        for k, v in r.getheaders():  # getheaders conserva cada Set-Cookie por separado
            if k.lower() not in _SIN_REENVIO:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        if metodo != "HEAD":
            self.wfile.write(datos)


def _segundo_plano(grupo: tuple[int, int] | None = None, solo_refresco: bool = False, sin_refresco: bool = False) -> None:
    """Proceso hijo: refresco, carga historica y backtest largo (o solo el refresco de un grupo de activos). Fuera del proceso HTTP para que su CPU no le quite el
    GIL a /salud (healthcheck de 4 s → Swarm reinicia el contenedor → 502 de Cloudflare). nice 10: el kernel prefiere
    al servidor web cuando compiten por el CPU del contenedor."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if hasattr(os, "nice"):  # Linux (contenedor); no existe en Windows
        os.nice(10)
    sys.setswitchinterval(0.001)  # entre los hilos del hijo (el refresco cede al backtest largo)
    if os.environ.get("REFRESCO_ACTIVO", "1") != "0" and not sin_refresco:
        import refresco

        refresco.iniciar_en_segundo_plano(grupo) if grupo else refresco.iniciar_en_segundo_plano()
        logging.info("refresco de snapshots SMC activo%s", f" (grupo {grupo[0] + 1}/{grupo[1]})" if grupo else "")
    if solo_refresco:
        threading.Event().wait()
    if os.environ.get("MTB_VELAS_FUENTE", "").strip().lower() == "xm":
        logging.info("fuente de velas XM: la carga historica de Dukascopy no se inicia")
    elif os.environ.get("ALMACEN_CARGA_ACTIVA", "1") != "0" and os.environ.get("DATABASE_URL"):
        import carga_historica

        carga_historica.iniciar_en_segundo_plano()
        logging.info("carga historica del almacen de velas activa")
    if os.environ.get("BACKTEST_LARGO_ACTIVO", "1") != "0" and os.environ.get("DATABASE_URL"):
        import backtest_largo

        backtest_largo.iniciar_en_segundo_plano()
        logging.info("backtest largo incremental activo")
    threading.Event().wait()  # los hilos son daemon: el hijo vive mientras ellos trabajan


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # ponytail: si el hijo muere (p. ej. OOM) nadie lo relanza; el sitio sigue y el siguiente deploy lo revive.
    ctx = multiprocessing.get_context("spawn")
    try:
        n = max(1, int(os.environ.get("REFRESCO_PROCESOS", "1")))
    except ValueError:
        n = 1
    if n == 1 or os.environ.get("REFRESCO_ACTIVO", "1") == "0":
        ctx.Process(target=_segundo_plano, name="segundo-plano", daemon=True).start()
    else:
        # n procesos de refresco, cada uno con su reparto de activos (el calculo es CPU: los hilos de un solo
        # proceso comparten GIL y solo solapan la espera de red), mas el hijo de carga historica y backtest largo.
        # Necesita CPU >= n en el contenedor.
        for i in range(n):
            ctx.Process(target=_segundo_plano, args=((i, n), True), name=f"refresco-{i}", daemon=True).start()
        ctx.Process(target=_segundo_plano, kwargs={"sin_refresco": True}, name="segundo-plano", daemon=True).start()
    puerto = int(os.environ.get("PORT", 8000))
    logging.info("MexTradeBot escuchando en :%d", puerto)
    ThreadingHTTPServer(("0.0.0.0", puerto), Handler).serve_forever()


def demo() -> None:
    """Self-check del limite por IP (sin levantar servidor): python -c "import app; app.demo()" """
    _rate_marcas.clear()
    ip, t0 = "1.2.3.4", 1000.0
    for _ in range(30):  # hasta el limite: pasa
        assert _excedido(ip, t0, limite=30, ventana=10) is False
    assert _excedido(ip, t0, limite=30, ventana=10) is True  # peticion 31: se frena
    assert _excedido("otra.ip", t0, limite=30, ventana=10) is False  # otra IP no comparte el cupo
    assert _excedido(ip, t0 + 11, limite=30, ventana=10) is False  # pasada la ventana, vuelve a dejar pasar
    _rate_marcas.clear()

    # cabeceras de seguridad: servidor local real, una peticion de verdad
    import urllib.request

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{servidor.server_port}/salud") as r:
            for cab, esperado in [("Strict-Transport-Security", "max-age"), ("X-Content-Type-Options", "nosniff"),
                                   ("X-Frame-Options", "DENY"), ("Referrer-Policy", "strict-origin"),
                                   ("Permissions-Policy", "camera=()")]:
                assert esperado in r.headers.get(cab, ""), f"falta {cab} en la respuesta"
    finally:
        servidor.shutdown()

    print("app.demo() OK — limite por IP y cabeceras de seguridad funcionan")


if __name__ == "__main__":
    main()
