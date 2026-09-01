"""Lógica de GET/POST /api/calendario?simbolo=XAUUSD&horas=24 — invocado desde
el router en api/analizar.py (mismo motivo que api/setups.py: Vercel en modo
single-entrypoint no auto-descubre este archivo).

Traduce un símbolo (XAUUSD, EURUSD, ...) a las divisas que le afectan y revisa
si hay un evento de calendario de impacto Alto próximo — para aplicar la
regla de disciplina "no operar con noticias" antes de recomendar un activo.
Los metales (XAU/XAG) no tienen banco central propio, así que solo se filtran
por USD (su divisa de cotización).
"""

from datetime import datetime, timezone

from conectividad.calendario import obtener_eventos

METALES = {"XAU", "XAG"}


def _monedas_de_simbolo(simbolo: str) -> list[str]:
    s = simbolo.upper()
    base, cotizada = s[:3], s[3:6]
    if base in METALES:
        return [cotizada]
    return [base, cotizada]


def procesar(payload: dict) -> tuple[int, dict]:
    simbolo = payload.get("simbolo")
    if not simbolo:
        return 400, {"error": "falta 'simbolo' (ej. XAUUSD, EURUSD)"}

    try:
        horas = int(payload.get("horas", 24))
    except (TypeError, ValueError):
        return 400, {"error": "'horas' debe ser entero"}

    monedas = _monedas_de_simbolo(simbolo)
    ahora = datetime.now(timezone.utc)

    try:
        eventos = obtener_eventos()
    except Exception as e:
        return 502, {"error": f"calendario no disponible: {e}"}

    proximos = []
    for ev in eventos:
        if ev.get("impact") != "High" or ev.get("country") not in monedas:
            continue
        t_evento = datetime.fromisoformat(ev["date"])
        delta_horas = (t_evento - ahora).total_seconds() / 3600
        if 0 <= delta_horas <= horas:
            proximos.append({"title": ev.get("title"), "country": ev.get("country"), "date": ev.get("date")})

    return 200, {
        "simbolo": simbolo,
        "monedas": monedas,
        "hay_evento_alto_impacto": len(proximos) > 0,
        "eventos": proximos,
    }


def demo() -> None:
    status, body = procesar({"simbolo": "XAUUSD", "horas": 24 * 7})
    assert status == 200
    assert "hay_evento_alto_impacto" in body
    print(f"api.calendario.demo() OK — XAUUSD: {body['hay_evento_alto_impacto']}, {len(body['eventos'])} eventos en 7 dias")

    status_malo, body_malo = procesar({})
    assert status_malo == 400 and "error" in body_malo


if __name__ == "__main__":
    demo()
