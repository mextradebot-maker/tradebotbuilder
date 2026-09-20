"""GET /api/catalogo — lista todos los simbolos del catalogo con su fecha_inicio."""


def procesar(_payload: dict) -> tuple[int, dict]:
    try:
        import persistencia as _p
    except Exception:
        return 503, {"error": "base de datos no disponible"}
    try:
        entradas = _p.listar_catalogo()
    except Exception as e:
        return 500, {"error": str(e)}
    return 200, {"catalogo": entradas, "total": len(entradas)}
