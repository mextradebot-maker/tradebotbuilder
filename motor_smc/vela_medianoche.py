"""Motor de candidatos de Vela de Medianoche: PDH del dia anterior (hora de Barcelona).

Port 1:1 a Python puro de la logica de `robots/MexTradeBot_OroMedianoche.mq5`:
- horario de verano UE: del ultimo domingo de marzo al ultimo domingo de octubre (01:00 UTC).
- PDH = maximo `high` de las velas H1 del dia de Barcelona anterior; si ese dia tuvo menos
  de 12 velas no hay candidato.
- la ventana de la orden vive 9 h desde la medianoche de Barcelona de hoy.
"""

from datetime import date, datetime, timedelta, timezone

from api.catalogo import SIMBOLO_XM
from conectividad import almacen

INP_MIN_VELAS_DIA = 12
HORAS_VENTANA = 9
SERIE = "1H"


def _utc(instante: datetime) -> datetime:
    """Naive se interpreta como UTC; aware se convierte a UTC."""
    if instante.tzinfo is None:
        return instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def _ultimo_domingo(anio: int, mes: int) -> datetime:
    """Ultimo domingo de marzo u octubre a las 01:00 UTC (mes de 31 dias)."""
    d = datetime(anio, mes, 31, 1, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def offset_barcelona(instante_gmt: datetime) -> int:
    """Segundos que Barcelona va por delante de GMT en ese instante."""
    t = _utc(instante_gmt)
    verano = _ultimo_domingo(t.year, 3) <= t < _ultimo_domingo(t.year, 10)
    return 7200 if verano else 3600


def _dia_barcelona(instante: datetime) -> date:
    t = _utc(instante)
    return (t + timedelta(seconds=offset_barcelona(t))).date()


def _medianoche_barcelona(dia: date) -> datetime:
    """Medianoche local de `dia` en Barcelona, expresada en UTC."""
    naive_utc = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
    return naive_utc - timedelta(seconds=offset_barcelona(naive_utc))


def pdh_candidato(simbolo: str, ahora: datetime | None = None) -> dict | None:
    """None si hoy no se opera: ventana de 9 h ya cerrada, dia anterior con < 12 velas, o el precio ya estaba en el PDH
    o arriba al abrir hoy (regla 3 del spec: el BUY STOP ya no tendria sentido y el broker lo rechazaria)."""
    ahora_utc = _utc(ahora) if ahora is not None else datetime.now(timezone.utc)
    hoy = _dia_barcelona(ahora_utc)
    ayer = hoy - timedelta(days=1)
    medianoche_hoy = _medianoche_barcelona(hoy)
    ventana_hasta = medianoche_hoy + timedelta(hours=HORAS_VENTANA)
    if ahora_utc >= ventana_hasta:
        return None

    velas = almacen.leer(simbolo, SERIE, _medianoche_barcelona(ayer), medianoche_hoy - timedelta(seconds=1))
    if len(velas) < INP_MIN_VELAS_DIA:
        return None
    pdh = float(velas["high"].max())
    # ponytail: "precio a las 00:00" = apertura de la primera vela H1 de hoy; si aun no esta en el almacen no se descarta
    hoy_velas = almacen.leer(simbolo, SERIE, medianoche_hoy, ahora_utc)
    if len(hoy_velas) and float(hoy_velas["open"].iloc[0]) >= pdh:
        return None
    return {"simbolo": simbolo, "pdh": pdh, "dia_anterior": ayer, "ventana_hasta": ventana_hasta}


def candidatos(ahora: datetime | None = None) -> list[dict]:
    ahora_utc = _utc(ahora) if ahora is not None else datetime.now(timezone.utc)
    resultados = (pdh_candidato(s, ahora_utc) for s in SIMBOLO_XM.keys())
    return [c for c in resultados if c is not None]


def demo() -> None:
    import numpy as np
    import pandas as pd

    # --- offset_barcelona en los dos cambios de horario de 2026 ---
    # UE pasa a verano el 29 mar 01:00 UTC, a invierno el 25 oct 01:00 UTC
    assert offset_barcelona(datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)) == 3600   # aun invierno (+1)
    assert offset_barcelona(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == 7200    # ya verano (+2)
    assert offset_barcelona(datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)) == 7200  # aun verano
    assert offset_barcelona(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == 3600   # ya invierno

    # --- pdh_candidato con stub de conectividad.almacen.leer ---
    # Hoy = 2026-11-07 Barcelona (CET, +1), ahora = 10:00 Barcelona = 09:00 UTC.
    # "Ayer" = 2026-11-06 Barcelona = 2026-11-05 23:00 UTC -> 2026-11-06 22:00 UTC.
    # ETH: 24 velas H1 de ayer con maximo conocido 2100.0 -> PDH 2100.0.
    # BTC: solo 5 velas de ayer -> sin candidato (< 12).
    def velas_de(inicio_utc: datetime, n: int, high_max: float) -> pd.DataFrame:
        idx = pd.DatetimeIndex([inicio_utc + timedelta(hours=h) for h in range(n)], name="timestamp")
        high = np.linspace(high_max - 10, high_max, n)
        high[n // 2] = high_max  # el maximo queda en una vela del medio
        return pd.DataFrame(
            {"open": high - 1, "high": high, "low": high - 2, "close": high - 1, "volume": 1.0},
            index=idx,
        )

    ayer_inicio = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)  # 2026-11-06 00:00 Barcelona
    hoy_inicio = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)   # 2026-11-07 00:00 Barcelona
    base = {
        # ETH: primera vela de hoy abre debajo del PDH -> sigue siendo candidato
        "ETHUSD": pd.concat([velas_de(ayer_inicio, 24, 2100.0), velas_de(hoy_inicio, 1, 2091.0)]),
        "BTCUSD": velas_de(ayer_inicio, 5, 9999.0),
        # XRP: hoy abre en 107 (open = high - 1), ya por encima del PDH de ayer (105) -> el nivel ya se rompio
        "XRPUSD": pd.concat([velas_de(ayer_inicio, 24, 105.0), velas_de(hoy_inicio, 1, 108.0)]),
    }
    llamadas = []

    def leer_stub(simbolo, serie, inicio, fin):
        llamadas.append((simbolo, serie, inicio, fin))
        df = base.get(simbolo)
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return df[(df.index >= inicio) & (df.index <= fin)]

    original_leer = almacen.leer
    almacen.leer = leer_stub
    try:
        ahora = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)  # 02:00 Barcelona, dentro de la ventana de 9 h

        c = pdh_candidato("ETHUSD", ahora)
        assert c is not None
        assert c["simbolo"] == "ETHUSD"
        assert c["pdh"] == 2100.0
        assert c["dia_anterior"] == date(2026, 11, 6)
        # ventana: medianoche Barcelona de hoy (2026-11-07 00:00 CET = 2026-11-06 23:00 UTC) + 9 h
        assert c["ventana_hasta"] == datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
        # la consulta al almacen pide la serie H1 del dia de ayer en Barcelona (y luego la de hoy hasta ahora)
        simbolo, serie, inicio, fin = llamadas[-2]
        assert serie == "1H"
        assert inicio == ayer_inicio
        assert fin == datetime(2026, 11, 6, 22, 59, 59, tzinfo=timezone.utc)
        assert llamadas[-1][2:] == (hoy_inicio, ahora)

        assert pdh_candidato("BTCUSD", ahora) is None  # 5 velas < 12
        assert pdh_candidato("XRPUSD", ahora) is None  # hoy abrio por encima del PDH: el nivel ya se rompio

        # ventana cerrada (10:00 Barcelona >= 09:00): ni siquiera consulta el almacen
        n = len(llamadas)
        assert pdh_candidato("ETHUSD", datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)) is None
        assert pdh_candidato("ETHUSD", datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)) is None  # justo 09:00: cerrada
        assert len(llamadas) == n
        assert pdh_candidato("ETHUSD", datetime(2026, 11, 7, 7, 59, tzinfo=timezone.utc)) is not None  # 08:59: abierta

        # candidatos() corre sobre SIMBOLO_XM y descarta los None
        resultado = candidatos(ahora)
        assert isinstance(resultado, list)
        assert all(isinstance(r, dict) for r in resultado)
        assert all(r["simbolo"] in SIMBOLO_XM for r in resultado)
        assert all(r["simbolo"] != "BTCUSD" for r in resultado)
        assert [r["simbolo"] for r in resultado] == ["ETHUSD"]
    finally:
        almacen.leer = original_leer

    print("motor_smc.vela_medianoche.demo() OK")


if __name__ == "__main__":
    demo()
