"""Servidor único de MexTradeBot en el VPS (EasyPanel) — reemplaza a Vercel.

  /api/*                 → router de siempre (api/analizar.py), sin cambios de lógica
  GET /alumnos, GET /webhook/panel-alumnos → Panel de Alumnos (plantillas/panel-alumnos.html, api/alumnos.py)
  /webhook/*             → n8n: registro, login, logout y demás webhooks (antes rewrites de vercel.json)
  /salud                 → healthcheck para EasyPanel
  todo lo demás          → archivos estáticos de public/ (panel, master.html, manuales)

Además arranca el refresco de snapshots SMC en segundo plano (refresco.py).
Variables: PORT (8000), N8N_URL, REFRESCO_ACTIVO=0 para apagarlo, ALMACEN_CARGA_ACTIVA=0 apaga la carga
historica de velas (carga_historica.py), BACKTEST_LARGO_ACTIVO=0 apaga el backtest largo semanal (backtest_largo.py), REFRESCO_HILOS (hilos del
refresco, I/O-bound; por defecto 6, 1 = en serie; REFRESCO_WORKERS es alias obsoleto), más las de la API
(DATABASE_URL, MTB_ADMIN_KEY, MTB_SERVICE_KEY).
"""

import http.client
import logging
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from api.analizar import handler as ApiHandler

PUBLICO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
N8N = urlsplit(os.environ.get("N8N_URL", "https://chatbotventas-n8n.h0w0dc.easypanel.host"))
RUTAS_N8N = {"/alumnos": "/webhook/panel-alumnos"}
RUTAS_PANEL = {"/alumnos", "/webhook/panel-alumnos"}  # GET: la página la arma mtb-api; POST (registro) sigue a n8n
_SIN_REENVIO = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding"}


class Handler(SimpleHTTPRequestHandler):
    _responder = ApiHandler._responder
    _responder_archivo = ApiHandler._responder_archivo  # descargas (.ex5) del router de la API

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLICO, **kwargs)

    def _ruta(self) -> str:
        return urlsplit(self.path).path

    def _es_n8n(self) -> bool:
        r = self._ruta().rstrip("/")
        return r in RUTAS_N8N or r.startswith("/webhook/")

    def do_GET(self):
        ruta = self._ruta()
        if ruta.startswith("/api/"):
            return ApiHandler.do_GET(self)
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
        if self._es_n8n():
            return self._a_n8n("POST")
        return ApiHandler.do_POST(self)

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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if os.environ.get("REFRESCO_ACTIVO", "1") != "0":
        import refresco

        refresco.iniciar_en_segundo_plano()
        logging.info("refresco de snapshots SMC activo")
    if os.environ.get("ALMACEN_CARGA_ACTIVA", "1") != "0" and os.environ.get("DATABASE_URL"):
        import carga_historica

        carga_historica.iniciar_en_segundo_plano()
        logging.info("carga historica del almacen de velas activa")
    if os.environ.get("BACKTEST_LARGO_ACTIVO", "1") != "0" and os.environ.get("DATABASE_URL"):
        import backtest_largo

        backtest_largo.iniciar_en_segundo_plano()
        logging.info("backtest largo semanal activo")
    puerto = int(os.environ.get("PORT", 8000))
    logging.info("MexTradeBot escuchando en :%d", puerto)
    ThreadingHTTPServer(("0.0.0.0", puerto), Handler).serve_forever()


if __name__ == "__main__":
    main()
