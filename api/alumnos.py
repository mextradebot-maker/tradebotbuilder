"""Panel de Alumnos servido por mtb-api (antes era HTML fijo dentro de n8n).

  GET /alumnos, GET /webhook/panel-alumnos  → plantillas/panel-alumnos.html con los datos de la sesión
  GET /api/v1/mis-licencias                → licencias del alumno (crea su DEMO la primera vez)
  GET /api/v1/mi-robot?id=N                → .ex5 personalizado, solo de una licencia suya y vigente

La sesión es la cookie `mtb_token` que pone n8n al registrarse / iniciar sesión (tabla alumnos);
registro, login y logout siguen en n8n.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

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


def procesar_mi_robot(datos: dict, headers: dict) -> tuple[int, bytes | dict]:
    alumno = _alumno(headers)
    if not alumno:
        return 401, {"error": "inicia sesion en el panel"}
    from api.licencias import compilar_robot
    from persistencia import licencias

    try:
        lid = int(datos.get("id") or 0)
    except ValueError:
        return 400, {"error": "id invalido"}
    lic = next((l for l in licencias.licencias_de_correo(alumno["correo"]) if l["id"] == lid), None)
    if lic is None:  # no existe o es de otro alumno: misma respuesta, no se revela cuál
        return 404, {"error": "licencia no encontrada"}
    if _estado(lic, datetime.now(timezone.utc)) != "activa":
        return 403, {"error": "tu licencia no esta vigente"}
    return compilar_robot(lid)


def demo() -> None:
    import sys
    import types

    ahora = datetime.now(timezone.utc)
    mia = {"id": 5, "tipo": "demo", "token_prefijo": "MTB-AAAAA", "cuenta": None, "expira_en": None, "revocada_en": None}
    vieja = {**mia, "id": 6, "revocada_en": ahora}
    creadas = []
    falso = types.SimpleNamespace(
        alumno_por_sesion=lambda t: {"id": 1, "correo": "ana@x.com", "nombre": "Ana </script>"} if t == "ok" else None,
        licencias_de_correo=lambda c: [mia, vieja] if c == "ana@x.com" else [],
        asegurar_licencia_demo=lambda c, n: creadas.append(c),
    )
    compilados = []
    previos = {k: sys.modules.get(k) for k in ("persistencia", "persistencia.licencias")}
    sys.modules["persistencia"] = types.SimpleNamespace(licencias=falso)
    sys.modules["persistencia.licencias"] = falso
    import api.licencias as api_lic

    original = api_lic.compilar_robot
    api_lic.compilar_robot = lambda i: (compilados.append(i) or (200, b"EX5"))
    try:
        con = {"Cookie": "otra=1; mtb_token=ok"}
        assert procesar_mis_licencias({})[0] == 401
        st, body = procesar_mis_licencias(con)
        assert st == 200 and creadas == ["ana@x.com"], (st, body)
        assert [l["estado"] for l in body["licencias"]] == ["activa", "revocada"]

        assert procesar_mi_robot({"id": "5"}, {})[0] == 401
        assert procesar_mi_robot({"id": "99"}, con)[0] == 404          # no es suya
        assert procesar_mi_robot({"id": "6"}, con)[0] == 403           # revocada
        assert procesar_mi_robot({"id": "5"}, con) == (200, b"EX5") and compilados == [5]

        html = pagina_panel(con, "error=credenciales_invalidas")
        assert "var MTB_CLAVE = \"MXTB-CURSO-2026\";" in html
        assert "Ana \\u003c/script\\u003e" in html and "Ana </script>" not in html  # sin XSS
        assert "Correo o contrasena incorrectos." in html
        anon = pagina_panel({}, "error=<script>")
        assert "var MTB_CLAVE = \"\";" in anon and "var MTB_ERROR_TIPO = \"\";" in anon
        assert "__MTB_" not in anon and "vercel.app" not in anon
    finally:
        api_lic.compilar_robot = original
        for k, v in previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    print("api.alumnos.demo() OK")


if __name__ == "__main__":
    demo()
