"""Cálculo de volumen (lotes) según temporalidad y reglas de gestión de riesgo.

Swing (H/S/M):     lotes = (capital × 2%) / margen_1_lote  — sin SL en MT5
Scalping/Intraday: lotes = (capital × pct%) / (pips_stop × valor_pip)
"""

import MetaTrader5 as mt5

TEMPORALIDADES_SWING = {"Swing (H)", "Swing (S)", "Swing (M)"}
PCT_RIESGO_SWING = 0.02
PCT_RIESGO_DEFAULT = 0.01


def calcular_lotes(
    simbolo: str,
    capital: float,
    temporalidad: str,
    pips_stop: float | None = None,
    pct_riesgo: float | None = None,
) -> float:
    """Devuelve volumen en lotes, ajustado al step y mínimo del símbolo en XM.

    Para swing no se pasa SL a la orden — el coordinador cierra al detectar
    CHoCH inverso en la temporalidad activa.
    """
    info = mt5.symbol_info(simbolo)
    if info is None:
        raise ValueError(f"Símbolo no encontrado en MT5: {simbolo}")

    if temporalidad in TEMPORALIDADES_SWING:
        lotes = _lotes_swing(simbolo, capital)
    else:
        if not pips_stop or pips_stop <= 0:
            raise ValueError("pips_stop requerido para scalping/intraday")
        pct = pct_riesgo if pct_riesgo is not None else PCT_RIESGO_DEFAULT
        lotes = _lotes_sl(capital, pct, pips_stop, info)

    return _redondear(lotes, info)


def _lotes_swing(simbolo: str, capital: float) -> float:
    # ponytail: ORDER_TYPE_BUY para margen; válido para SELL también (mismo margen en XM)
    precio = mt5.symbol_info_tick(simbolo).ask
    margen_1_lote = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, simbolo, 1.0, precio)
    if not margen_1_lote or margen_1_lote <= 0:
        raise ValueError(f"order_calc_margin devolvió 0 para {simbolo}")
    return (capital * PCT_RIESGO_SWING) / margen_1_lote


def _lotes_sl(capital: float, pct: float, pips_stop: float, info) -> float:
    pip_size = info.point * 10
    valor_pip = info.trade_tick_value * (pip_size / info.trade_tick_size)
    return (capital * pct) / (pips_stop * valor_pip)


def _redondear(lotes: float, info) -> float:
    step = info.volume_step
    lotes = round(lotes / step) * step
    return max(min(lotes, info.volume_max), info.volume_min)


def demo() -> None:
    """Self-check con valores conocidos (sin MT5 en vivo)."""

    class FakeInfo:
        point = 0.00001
        trade_tick_size = 0.00001
        trade_tick_value = 1.0   # $1 por tick por lote → $10 por pip
        volume_step = 0.01
        volume_min = 0.01
        volume_max = 100.0

    info = FakeInfo()

    # EURUSD scalping: $300, 1%, 30 pips → $3 / ($10×30) = 0.01
    lotes = _lotes_sl(300, 0.01, 30, info)
    assert _redondear(lotes, info) == 0.01, lotes

    # EURUSD intraday: $300, 2%, 60 pips → $6 / ($10×60) = 0.01
    lotes = _lotes_sl(300, 0.02, 60, info)
    assert _redondear(lotes, info) == 0.01, lotes

    # Capital grande: $10,000, 1%, 50 pips → $100 / $500 = 0.20
    lotes = _lotes_sl(10_000, 0.01, 50, info)
    assert _redondear(lotes, info) == 0.20, lotes

    print("riesgo.demo() OK")


if __name__ == "__main__":
    demo()
