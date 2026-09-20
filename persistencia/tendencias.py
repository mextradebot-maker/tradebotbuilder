from .conexion import get_conn


def leer_ultima_tendencia(simbolo: str, temporalidad: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tendencia_nueva FROM historico_tendencias"
            " WHERE simbolo = %s AND temporalidad = %s"
            " ORDER BY fecha_cambio DESC LIMIT 1",
            (simbolo, temporalidad),
        ).fetchone()
    return row[0] if row else None


def registrar_cambio_tendencia(
    simbolo: str,
    temporalidad: str,
    tendencia_nueva: str | None,
    tendencia_anterior: str | None,
) -> None:
    # Solo registra si hay cambio real
    if tendencia_nueva == tendencia_anterior:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO historico_tendencias (simbolo, temporalidad, tendencia_nueva, tendencia_anterior)"
            " VALUES (%s, %s, %s, %s)",
            (simbolo, temporalidad, tendencia_nueva, tendencia_anterior),
        )


def demo() -> None:
    """Verifica append + lectura. Requiere DATABASE_URL apuntando a Postgres real."""
    simbolo, temp = "DEMO_SYM", "demo_temp_tend"

    registrar_cambio_tendencia(simbolo, temp, "compra", None)
    assert leer_ultima_tendencia(simbolo, temp) == "compra"
    registrar_cambio_tendencia(simbolo, temp, "compra", "compra")  # no debe escribir nada
    registrar_cambio_tendencia(simbolo, temp, "venta", "compra")
    assert leer_ultima_tendencia(simbolo, temp) == "venta"

    # Limpiar
    with get_conn() as conn:
        conn.execute("DELETE FROM historico_tendencias WHERE simbolo = %s AND temporalidad = %s", (simbolo, temp))

    print("persistencia.tendencias.demo() OK")


if __name__ == "__main__":
    demo()
