"""POST /api/refrescar-snapshot — fuerza refresco de snapshot aunque esté fresco.

Llamado por el cron n8n (MTB Refrescar Snapshots SMC) cada 30 minutos.
Autenticado via header X-Internal-Token: <INTERNAL_REFRESH_TOKEN>.
"""

import os


def procesar(payload: dict, headers: dict | None = None) -> tuple[int, dict]:
    token = (headers or {}).get("X-Internal-Token", "")
    if token != os.environ.get("INTERNAL_REFRESH_TOKEN", ""):
        return 401, {"error": "no autorizado"}

    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": "falta 'simbolo'"}

    # ponytail: reutiliza setups.procesar() con _force_refresh=True para no
    # duplicar la logica de descarga + analisis + escritura a Postgres.
    from api.setups import procesar as _setups

    return _setups({**payload, "_force_refresh": True})
