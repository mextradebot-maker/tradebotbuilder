"""Refresco de snapshots SMC dentro del servicio (reemplaza el cron n8n de 180 llamadas HTTP).

Una combinación activo×temporalidad solo se refresca cuando cerró una vela nueva
de SU temporalidad (Scalping 15m, Intraday 1h, Swing H 4h, Swing S semanal,
Swing M mensual) y su snapshot es anterior a ese cierre. Con el mercado cerrado
(fin de semana forex) no se refresca nada salvo cripto.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

CRIPTO = {"BTCUSD", "ETHUSD", "XRPUSD"}
MARGEN_DATOS = timedelta(minutes=2)  # el proveedor publica la vela cerrada con un poco de retraso
PAUSA_CICLO_S = 60


def inicio_vela(ahora: datetime, temporalidad: str) -> datetime:
    """Inicio de la vela en curso; la vela anterior cerró justo en este instante."""
    t = ahora.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if temporalidad == "Scalping":
        return t.replace(minute=t.minute - t.minute % 15)
    if temporalidad == "Intraday":
        return t.replace(minute=0)
    if temporalidad in ("Swing (H)", "Swing"):
        return t.replace(minute=0, hour=t.hour - t.hour % 4)
    dia = t.replace(minute=0, hour=0)
    if temporalidad == "Swing (S)":
        return dia - timedelta(days=dia.weekday())  # lunes 00:00 UTC
    if temporalidad == "Swing (M)":
        return dia.replace(day=1)
    raise ValueError(f"temporalidad desconocida: {temporalidad}")


def mercado_abierto(simbolo: str, ahora: datetime) -> bool:
    if simbolo in CRIPTO:
        return True
    t = ahora.astimezone(timezone.utc)
    # forex: cierra viernes 21:00 UTC, abre domingo 21:00 UTC
    if t.weekday() == 5:
        return False
    if t.weekday() == 4 and t.hour >= 21:
        return False
    if t.weekday() == 6 and t.hour < 21:
        return False
    return True


def necesita_refresco(simbolo: str, temporalidad: str, refrescado_en: datetime | None, ahora: datetime) -> bool:
    if not mercado_abierto(simbolo, ahora) and refrescado_en is not None:
        return False
    cierre = inicio_vela(ahora, temporalidad)
    if ahora < cierre + MARGEN_DATOS:  # vela recién cerrada: esperar a que el proveedor la publique
        return False
    if refrescado_en is None:
        return True
    if refrescado_en.tzinfo is None:
        refrescado_en = refrescado_en.replace(tzinfo=timezone.utc)
    return refrescado_en < cierre + MARGEN_DATOS


def ciclo() -> int:
    """Refresca lo pendiente, uno por uno. Devuelve cuántos refrescó."""
    import persistencia
    from api.setups import procesar

    ahora = datetime.now(timezone.utc)
    hechos = 0
    for c in persistencia.listar_catalogo():
        if not c["activo"]:
            continue
        snap = persistencia.leer_snapshot(c["simbolo"], c["temporalidad"])
        if not necesita_refresco(c["simbolo"], c["temporalidad"], snap and snap["refrescado_en"], ahora):
            continue
        try:
            status, body = procesar({"simbolo": c["simbolo"], "temporalidad": c["temporalidad"], "_force_refresh": True})
            if status != 200:
                logging.warning("refresco %s %s -> %s %s", c["simbolo"], c["temporalidad"], status, body.get("error"))
            hechos += 1
        except Exception:
            logging.exception("refresco %s %s falló", c["simbolo"], c["temporalidad"])
    return hechos


def _bucle() -> None:
    while True:
        try:
            n = ciclo()
            if n:
                logging.info("refresco: %d snapshots actualizados", n)
        except Exception:
            logging.exception("ciclo de refresco falló")  # BD caída, etc. — reintenta el próximo minuto
        time.sleep(PAUSA_CICLO_S)


def iniciar_en_segundo_plano() -> threading.Thread:
    hilo = threading.Thread(target=_bucle, name="refresco-smc", daemon=True)
    hilo.start()
    return hilo


def demo() -> None:
    u = timezone.utc
    ahora = datetime(2026, 9, 23, 14, 37, tzinfo=u)  # miércoles
    assert inicio_vela(ahora, "Scalping") == datetime(2026, 9, 23, 14, 30, tzinfo=u)
    assert inicio_vela(ahora, "Intraday") == datetime(2026, 9, 23, 14, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (H)") == datetime(2026, 9, 23, 12, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (S)") == datetime(2026, 9, 21, 0, 0, tzinfo=u)
    assert inicio_vela(ahora, "Swing (M)") == datetime(2026, 9, 1, 0, 0, tzinfo=u)

    # snapshot de antes del cierre de la vela 14:30 → refrescar; de después → no
    assert necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 20, tzinfo=u), ahora)
    assert not necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 33, tzinfo=u), ahora)
    # Swing mensual refrescado este mes → no se toca en todo el mes
    assert not necesita_refresco("EURUSD", "Swing (M)", datetime(2026, 9, 2, tzinfo=u), ahora)
    assert necesita_refresco("EURUSD", "Swing (M)", None, ahora)
    # vela de 15m recién cerrada (14:31, dentro del margen de 2 min) → esperar
    assert not necesita_refresco("EURUSD", "Scalping", datetime(2026, 9, 23, 14, 10, tzinfo=u), datetime(2026, 9, 23, 14, 31, tzinfo=u))

    sabado = datetime(2026, 9, 26, 12, 5, tzinfo=u)
    assert not mercado_abierto("EURUSD", sabado) and mercado_abierto("BTCUSD", sabado)
    assert not necesita_refresco("EURUSD", "Intraday", datetime(2026, 9, 25, 20, 0, tzinfo=u), sabado)
    assert necesita_refresco("BTCUSD", "Intraday", datetime(2026, 9, 26, 10, 0, tzinfo=u), sabado)
    assert mercado_abierto("EURUSD", datetime(2026, 9, 27, 21, 30, tzinfo=u))  # domingo reapertura

    # carga diaria por activo en días hábiles: 96 + 24 + 6 + ~0 = ~126 (antes: 48 × 5 = 240)
    print("refresco.demo() OK")


if __name__ == "__main__":
    demo()
