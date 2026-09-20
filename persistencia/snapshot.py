"""Lee y escribe smc_snapshot — caché de la respuesta completa de /api/setups.

`estructura_smc` guarda el body completo (dict serializable) que devolvería
/api/setups, no solo el resultado de motor_smc.analizar(). Esto evita
re-ejecutar detectar_setups + backtest + tendencia en cada consulta — el ahorro
principal de Fase 1 es no llamar a Dukascopy en cada vela nueva del bot.
"""

import json

from .conexion import get_conn


def leer_snapshot(simbolo: str, temporalidad: str) -> dict | None:
    """Devuelve {respuesta, tendencia_actual, refrescado_en} o None si no existe."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT estructura_smc, tendencia_actual, refrescado_en"
            " FROM smc_snapshot"
            " WHERE simbolo = %s AND temporalidad = %s",
            (simbolo, temporalidad),
        ).fetchone()
    if row is None:
        return None
    return {"respuesta": row[0], "tendencia_actual": row[1], "refrescado_en": row[2]}


def escribir_snapshot(simbolo: str, temporalidad: str, respuesta: dict, tendencia_actual: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO smc_snapshot (simbolo, temporalidad, estructura_smc, tendencia_actual, refrescado_en)
            VALUES (%s, %s, %s::jsonb, %s, now())
            ON CONFLICT (simbolo, temporalidad) DO UPDATE
              SET estructura_smc   = EXCLUDED.estructura_smc,
                  tendencia_actual = EXCLUDED.tendencia_actual,
                  refrescado_en    = now()
            """,
            (simbolo, temporalidad, json.dumps(respuesta), tendencia_actual),
        )


def demo() -> None:
    """Verifica round-trip jsonb. Requiere DATABASE_URL apuntando a Postgres real."""
    simbolo, temp = "DEMO_SYM", "demo_temp"
    respuesta = {"simbolo": simbolo, "velas": 42, "setups": []}

    escribir_snapshot(simbolo, temp, respuesta, "compra")
    snap = leer_snapshot(simbolo, temp)
    assert snap is not None
    assert snap["respuesta"]["velas"] == 42
    assert snap["tendencia_actual"] == "compra"

    # Limpiar
    with get_conn() as conn:
        conn.execute("DELETE FROM smc_snapshot WHERE simbolo = %s AND temporalidad = %s", (simbolo, temp))

    assert leer_snapshot(simbolo, temp) is None
    print("persistencia.snapshot.demo() OK")


if __name__ == "__main__":
    demo()
