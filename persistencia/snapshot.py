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


def resumen_snapshots(temporalidades) -> dict:
    """{(simbolo, temporalidad): {refrescado_en, hay_velas, motor, swing_length}} en UNA consulta, sin traer el
    JSON completo (lo justo para decidir si toca refrescar)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT simbolo, temporalidad, refrescado_en,"
            # mismo criterio que bool(respuesta.get("velas")): /api/setups guarda el CONTEO de velas (numero)
            " CASE jsonb_typeof(estructura_smc->'velas')"
            "   WHEN 'array' THEN jsonb_array_length(estructura_smc->'velas') > 0"
            "   WHEN 'number' THEN (estructura_smc->>'velas')::numeric <> 0"
            "   ELSE false END,"
            " estructura_smc->'motor', estructura_smc->'swing_length'"
            " FROM smc_snapshot WHERE temporalidad = ANY(%s)", (list(temporalidades),)).fetchall()
    return {(r[0], r[1]): {"refrescado_en": r[2], "hay_velas": r[3], "motor": r[4], "swing_length": r[5]} for r in rows}


def filas_catalogo() -> list[tuple]:
    """(simbolo, temporalidad, tendencia_actual, refrescado_en, backtests_snapshot, backtests_largo_total, largo_en)
    de todos los pares en UNA consulta, sin el arreglo de velas."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT s.simbolo, s.temporalidad, s.tendencia_actual, s.refrescado_en,"
            " s.estructura_smc->'backtests', b.resultado->'backtests'->'total', b.calculado_en"
            " FROM smc_snapshot s LEFT JOIN backtest_largo b USING (simbolo, temporalidad)").fetchall()


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
