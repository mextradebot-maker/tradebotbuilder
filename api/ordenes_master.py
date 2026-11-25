"""GET /api/v1/ordenes-master?dias=30 — que ordenes mando el Master Trader y como terminaron (solo lectura, X-MTB-Service-Key).

Sirve para auditar el despachador desde el laboratorio: cada orden con su setup (activo, temporalidad, entrada, stop, tp), su estado
(pendiente | entregada | colocada | expirada | rechazada | sin_lotes ...) y, si el robot la abrio y la cerro, el resultado real de
master_historial (profit en USD). No expone licencias, cuentas ni correos de alumnos.
"""
import os

COLS_ORDEN = ("simbolo", "temporalidad", "direccion", "estrategia", "entrada", "stop", "tp", "estado", "lotes", "motivo", "ref_cierre",
              "creada_en", "entregada_en")
COLS_HIST = ("simbolo", "temporalidad", "estrategia", "direccion", "lotes", "precio_entrada", "precio_cierre", "profit_usd", "razon_cierre",
             "abierta_en", "cerrada_en")


def _v(x):
    return x.isoformat() if hasattr(x, "isoformat") else (float(x) if hasattr(x, "__float__") and not isinstance(x, (bool, int)) else x)


def _leer(dias: int) -> tuple[list, list]:
    from persistencia.conexion import get_conn

    with get_conn() as conn:
        o = conn.execute(f"SELECT {', '.join(COLS_ORDEN)} FROM master_ordenes WHERE creada_en > now() - make_interval(days => %s) "
                         "ORDER BY creada_en", (dias,)).fetchall()
        h = conn.execute(f"SELECT {', '.join(COLS_HIST)} FROM master_historial WHERE cerrada_en > now() - make_interval(days => %s) "
                         "ORDER BY cerrada_en", (dias,)).fetchall()
    return o, h


def procesar(qs: dict, headers: dict, leer=_leer) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h

    if not os.environ.get("MTB_SERVICE_KEY"):
        return 503, {"error": "MTB_SERVICE_KEY no configurada"}
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    try:
        dias = max(1, min(int(qs.get("dias", 30)), 90))
    except (TypeError, ValueError):
        return 400, {"error": "'dias' debe ser un entero"}
    try:
        o, h = leer(dias)
    except Exception as e:
        return 503, {"error": f"base de datos no disponible: {type(e).__name__}"}
    return 200, {"dias": dias, "ordenes": [{c: _v(v) for c, v in zip(COLS_ORDEN, f)} for f in o],
                 "historial": [{c: _v(v) for c, v in zip(COLS_HIST, f)} for f in h]}


def demo() -> None:
    previa = os.environ.pop("MTB_SERVICE_KEY", None)
    try:
        assert procesar({}, {})[0] == 503
        os.environ["MTB_SERVICE_KEY"] = "svc"
        assert procesar({}, {"X-MTB-Service-Key": "otra"})[0] == 401
        assert procesar({"dias": "x"}, {"X-MTB-Service-Key": "svc"})[0] == 400
        fila_o = ("XAUUSD", "Swing (S)", "long", "estructural", 1.0, 0.9, 1.3, "colocada", 0.01, None, None, None, None)
        s, b = procesar({"dias": "500"}, {"X-MTB-Service-Key": "svc"}, leer=lambda d: ([fila_o], []))
        assert s == 200 and b["dias"] == 90 and b["ordenes"][0]["simbolo"] == "XAUUSD" and "licencia_id" not in b["ordenes"][0]
        assert procesar({}, {"X-MTB-Service-Key": "svc"}, leer=lambda d: 1 / 0)[0] == 503
    finally:
        os.environ.pop("MTB_SERVICE_KEY", None)
        if previa is not None:
            os.environ["MTB_SERVICE_KEY"] = previa
    print("api.ordenes_master.demo() OK")


if __name__ == "__main__":
    demo()
