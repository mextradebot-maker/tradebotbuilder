"""Filtro de calendario económico — feed gratuito de ForexFactory (nfs.faireconomy.media).

Plan de construcción, Paso 1b. Dato estructurado y determinista (a diferencia
de un resumen de noticias generado por IA, ver Sección 10 del mapa técnico) —
por eso puede entrar al motor como Filtro, respaldable en backtest.

Reglas del proveedor: máx. 2 descargas/5min. Este módulo cachea en disco y
solo refresca 1 vez al día (muy por debajo del límite). Si el feed deja de
responder, usa el cache mientras no esté demasiado viejo, y si ya lo está,
se desactiva explícitamente (CalendarioNoDisponibleError) en vez de operar
con datos viejos sin avisar.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

URL_SEMANA_ACTUAL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# tempfile.gettempdir() en vez de junto al codigo: en Vercel el filesystem del
# paquete es de solo lectura, solo /tmp es escribible (aunque efimero entre
# cold starts). Localmente sigue siendo un directorio temporal normal.
CACHE_PATH = Path(tempfile.gettempdir()) / "mextradebot_cache_calendario.json"
REFRESCO = timedelta(hours=24)  # se refresca 1 vez al dia
MARGEN_ANTES_DE_DESACTIVAR = timedelta(hours=48)  # tolera 1 refresco fallido antes de rendirse


class CalendarioNoDisponibleError(RuntimeError):
    pass


def _leer_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _guardar_cache(eventos: list[dict]) -> None:
    CACHE_PATH.write_text(
        json.dumps({"descargado_en": datetime.now(timezone.utc).isoformat(), "eventos": eventos}),
        encoding="utf-8",
    )


def _edad(cache: dict) -> timedelta:
    return datetime.now(timezone.utc) - datetime.fromisoformat(cache["descargado_en"])


def obtener_eventos(forzar: bool = False) -> list[dict]:
    """Eventos de la semana actual. Usa cache si tiene <24h; si el feed falla,
    tolera cache de hasta 48h antes de rendirse con CalendarioNoDisponibleError."""
    cache = _leer_cache()
    if not forzar and cache and _edad(cache) < REFRESCO:
        return cache["eventos"]

    try:
        resp = requests.get(URL_SEMANA_ACTUAL, timeout=10)
        resp.raise_for_status()
        eventos = resp.json()
    except (requests.RequestException, ValueError) as e:
        if cache and _edad(cache) < MARGEN_ANTES_DE_DESACTIVAR:
            return cache["eventos"]
        raise CalendarioNoDisponibleError(f"Feed no responde y no hay cache utilizable (<{MARGEN_ANTES_DE_DESACTIVAR}): {e}") from e

    _guardar_cache(eventos)
    return eventos


def hay_evento_alto_impacto_cerca(momento: datetime, ventana_minutos: int = 30, moneda: str | None = None) -> bool:
    """True si hay un evento de impacto Alto dentro de +/- ventana_minutos de `momento`
    (debe traer tzinfo). `moneda` filtra por país/divisa (ej. "USD"); None = cualquiera."""
    if momento.tzinfo is None:
        raise ValueError("momento debe traer tzinfo (usar datetime con timezone)")

    for ev in obtener_eventos():
        if ev.get("impact") != "High":
            continue
        if moneda and ev.get("country") != moneda:
            continue
        t_evento = datetime.fromisoformat(ev["date"])
        if abs((t_evento - momento).total_seconds()) <= ventana_minutos * 60:
            return True
    return False


def demo() -> None:
    eventos = obtener_eventos()
    assert isinstance(eventos, list) and len(eventos) > 0, "se esperaban eventos de la semana actual"
    assert all({"title", "country", "date", "impact"} <= ev.keys() for ev in eventos)

    ahora = datetime.now(timezone.utc)
    resultado = hay_evento_alto_impacto_cerca(ahora, ventana_minutos=60 * 24 * 7)  # toda la semana
    altos = [e for e in eventos if e.get("impact") == "High"]
    assert resultado == (len(altos) > 0), "el filtro debe coincidir con si hay eventos High en la semana"

    print(f"conectividad.calendario.demo() OK — {len(eventos)} eventos esta semana, {len(altos)} de impacto Alto")
    for e in altos[:5]:
        print(f"  [{e['country']}] {e['title']} — {e['date']}")


if __name__ == "__main__":
    demo()
