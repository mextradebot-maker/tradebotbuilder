from datetime import date

from .conexion import get_conn


def leer_fecha_inicio(simbolo: str, temporalidad: str) -> date | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT fecha_inicio FROM catalogo_activos WHERE simbolo=%s AND temporalidad=%s AND activo=true",
            (simbolo, temporalidad),
        ).fetchone()
    return row[0] if row else None


def registrar_simbolo(simbolo: str, temporalidad: str) -> date:
    hoy = date.today()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO catalogo_activos (simbolo, temporalidad, fecha_inicio)"
            " VALUES (%s, %s, %s) ON CONFLICT (simbolo, temporalidad) DO NOTHING",
            (simbolo, temporalidad, hoy),
        )
    return hoy


def listar_catalogo() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT simbolo, temporalidad, fecha_inicio, activo"
            " FROM catalogo_activos ORDER BY simbolo, temporalidad"
        ).fetchall()
    return [
        {"simbolo": r[0], "temporalidad": r[1], "fecha_inicio": str(r[2]), "activo": r[3]}
        for r in rows
    ]
