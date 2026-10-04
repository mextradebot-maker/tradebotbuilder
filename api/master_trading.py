"""GET /api/v1/master/trading — operación en vivo de las cuentas demo del Master Trader, solo para el admin.

Protegido con X-Admin-Key (MTB_ADMIN_KEY), como /api/v1/licencias. Lee de Postgres lo que el coordinador
del VPS escribe: cuentas_demo, posiciones_abiertas e historial_posiciones (últimas 100 cerradas).
Reemplaza a la vista "Master Trader en Vivo" del panel de alumnos, que leía el VPS sin autenticación.

GET /api/v1/operacion-en-vivo — lo mismo para alumnos con sesión (cookie mtb_token), sin número de cuenta
ni servidor: cada cuenta se identifica solo por su nombre (o "Cuenta N" si no tiene).
"""

from datetime import datetime, timezone

LIMITE_HISTORIAL = 100


def _valor(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (bool, int)):
        return float(v)
    return v


def _filas(cols: tuple, filas) -> list[dict]:
    return [{c: _valor(v) for c, v in zip(cols, f)} for f in filas]


CUENTA = ("login", "server", "nombre", "simbolo", "temporalidad", "activa")
POSICION = ("login", "simbolo", "temporalidad", "direccion", "lotes", "precio_entrada", "abierta_en")
CERRADA = ("login", "simbolo", "temporalidad", "direccion", "lotes", "profit_usd", "razon_cierre", "cerrada_en")


def _leer_db() -> tuple[list, list, list]:
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        cuentas = conn.execute("SELECT " + ", ".join(CUENTA) + " FROM cuentas_demo ORDER BY login").fetchall()
        pos = conn.execute("SELECT " + ", ".join(POSICION) + " FROM posiciones_abiertas ORDER BY abierta_en DESC").fetchall()
        hist = conn.execute("SELECT " + ", ".join(CERRADA) + " FROM historial_posiciones "
                            "ORDER BY cerrada_en DESC LIMIT %s", (LIMITE_HISTORIAL,)).fetchall()
    return cuentas, pos, hist


def procesar(headers: dict, leer=_leer_db) -> tuple[int, dict]:
    import os
    from api.licencias import _clave_ok, _h
    if not os.environ.get("MTB_ADMIN_KEY"):
        return 503, {"error": "MTB_ADMIN_KEY no configurada en el servidor"}
    if not _clave_ok(_h(headers, "X-Admin-Key"), "MTB_ADMIN_KEY"):
        return 401, {"error": "se requiere X-Admin-Key"}
    try:
        cuentas, pos, hist = leer()
    except Exception as e:
        return 503, {"error": f"base de datos no disponible: {e}"}
    return 200, {"cuentas": _filas(CUENTA, cuentas), "posiciones_abiertas": _filas(POSICION, pos),
                 "historial": _filas(CERRADA, hist), "actualizado_en": datetime.now(timezone.utc).isoformat()}


def _alias(cuentas: list[dict]) -> dict:
    alias, usados = {}, set()
    for i, c in enumerate(cuentas, 1):
        nombre = (c.get("nombre") or "").strip() or f"Cuenta {i}"
        if nombre in usados:
            nombre = f"{nombre} {i}"
        usados.add(nombre)
        alias[c["login"]] = nombre
    return alias


def _sin_login(filas: list[dict], alias: dict) -> list[dict]:
    return [{"cuenta": alias.get(f["login"], "Cuenta demo"), **{k: v for k, v in f.items() if k != "login"}} for f in filas]


def procesar_alumnos(headers: dict, leer=_leer_db, alumno=None) -> tuple[int, dict]:
    if alumno is None:
        from api.alumnos import _alumno as alumno
    try:
        if not alumno(headers):
            return 401, {"error": "inicia sesion en el panel"}
        cuentas, pos, hist = leer()
    except Exception:
        return 503, {"error": "base de datos no disponible"}
    cuentas = _filas(CUENTA, cuentas)
    alias = _alias(cuentas)
    return 200, {"cuentas": [{"cuenta": alias[c["login"]], "simbolo": c["simbolo"], "temporalidad": c["temporalidad"],
                              "activa": c["activa"]} for c in cuentas],
                 "posiciones_abiertas": _sin_login(_filas(POSICION, pos), alias),
                 "historial": _sin_login(_filas(CERRADA, hist), alias),
                 "actualizado_en": datetime.now(timezone.utc).isoformat()}


def demo() -> None:
    import os
    from decimal import Decimal
    previa = os.environ.pop("MTB_ADMIN_KEY", None)
    try:
        t = datetime(2026, 10, 4, tzinfo=timezone.utc)
        datos = ([(1, "XMGlobal-MT5 7", "ORO", "GOLD", "Intraday 1H", True)],
                 [(1, "GOLD", "Intraday 1H", "BUY", Decimal("0.02"), Decimal("2650.5"), t)],
                 [(1, "GOLD", "Intraday 1H", "SELL", Decimal("0.02"), Decimal("-12.4"), "SL", t)])
        assert procesar({"X-Admin-Key": "x"}, lambda: datos)[0] == 503
        os.environ["MTB_ADMIN_KEY"] = "adm"
        assert procesar({}, lambda: datos)[0] == 401
        assert procesar({"X-Admin-Key": "otra"}, lambda: datos)[0] == 401
        assert procesar({"x-admin-key": "adm"}, lambda: 1 / 0)[0] == 503
        st, b = procesar({"x-admin-key": "adm"}, lambda: datos)
        assert st == 200 and b["cuentas"][0] == {"login": 1, "server": "XMGlobal-MT5 7", "nombre": "ORO", "simbolo": "GOLD",
                                                 "temporalidad": "Intraday 1H", "activa": True}
        assert b["posiciones_abiertas"][0]["lotes"] == 0.02 and b["posiciones_abiertas"][0]["abierta_en"] == t.isoformat()
        assert b["historial"][0]["profit_usd"] == -12.4 and b["historial"][0]["razon_cierre"] == "SL"
        assert procesar_alumnos({}, lambda: datos, alumno=lambda h: None)[0] == 401
        assert procesar_alumnos({}, lambda: 1 / 0, alumno=lambda h: {"correo": "a@b"})[0] == 503
        dos = ([(318680674, "XMGlobal-MT5 7", "ORO", "GOLD", "Intraday 1H", True), (5, "s", "", "EURUSD", "Intraday 4H", True)],
               [(318680674, "GOLD", "Intraday 1H", "BUY", Decimal("0.02"), Decimal("2650.5"), t),
                (5, "EURUSD", "Intraday 4H", "SELL", Decimal("0.1"), Decimal("1.1"), t)],
               [(318680674, "GOLD", "Intraday 1H", "SELL", Decimal("0.02"), Decimal("-12.4"), "SL", t)])
        st, b = procesar_alumnos({}, lambda: dos, alumno=lambda h: {"correo": "a@b"})
        assert st == 200 and "318680674" not in str(b) and "XMGlobal" not in str(b), b
        assert [c["cuenta"] for c in b["cuentas"]] == ["ORO", "Cuenta 2"]
        assert [p["cuenta"] for p in b["posiciones_abiertas"]] == ["ORO", "Cuenta 2"] and b["historial"][0]["cuenta"] == "ORO"
    finally:
        os.environ.pop("MTB_ADMIN_KEY", None)
        if previa is not None:
            os.environ["MTB_ADMIN_KEY"] = previa
    print("api.master_trading.demo() OK")


if __name__ == "__main__":
    demo()
