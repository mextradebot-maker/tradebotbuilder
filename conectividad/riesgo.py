"""Regla ÚNICA de volumen (lotes) de MexTradeBot.

Toda pieza que abra posiciones (coordinador, peticiones manuales, bots futuros)
calcula lotes aquí — no existe otra copia de esta regla.

Swing (H/S/M) — sin SL en MT5, salida por CHoCH inverso (coordinador):
    lotes = (capital × pct) / margen_de_1_lote          pct default 2%, máximo 2%

Scalping / Intraday — SL en la estructura:
    lotes = (capital × pct) / pérdida_de_1_lote_en_el_SL          pct default 1%
    pérdida_de_1_lote = (|entrada − sl| / tick_size) × tick_value

`tick_size` / `tick_value` / margen salen del broker (mt5.symbol_info /
order_calc_margin), así la misma fórmula sirve para forex, pares JPY, metales,
índices y cripto sin tablas de tamaño de contrato escritas a mano.

Los lotes se redondean HACIA ABAJO al step del broker: nunca se arriesga más
que el % pedido. Si ni el lote mínimo cabe en el riesgo, la operación NO es
viable (no se sube al mínimo en silencio) y se devuelve el capital mínimo.
"""

import math
from dataclasses import dataclass

TEMPORALIDADES_SWING = {"Swing (H)", "Swing (S)", "Swing (M)"}
PCT_RIESGO_SWING = 0.02
PCT_MAX_SWING = 0.02
PCT_RIESGO_DEFAULT = 0.01


@dataclass(frozen=True)
class Lotaje:
    lotes: float          # 0.0 si no es viable
    viable: bool
    motivo: str
    capital_minimo: float  # capital con el que el lote mínimo cabe en el riesgo


def es_swing(temporalidad: str) -> bool:
    return temporalidad in TEMPORALIDADES_SWING


def perdida_por_lote(precio_entrada: float, sl_precio: float, tick_size: float, tick_value: float) -> float:
    """USD que pierde 1 lote si toca el SL."""
    return abs(precio_entrada - sl_precio) / tick_size * tick_value


def dimensionar(capital: float, pct: float, costo_1_lote: float,
                vol_min: float, vol_max: float, vol_step: float) -> Lotaje:
    """Núcleo puro de la regla: presupuesto de riesgo / costo por lote, al step, hacia abajo."""
    if capital <= 0 or pct <= 0 or costo_1_lote <= 0:
        return Lotaje(0.0, False, "capital, % de riesgo o costo por lote inválido", 0.0)

    capital_minimo = vol_min * costo_1_lote / pct
    pasos = math.floor(capital * pct / costo_1_lote / vol_step + 1e-9)
    lotes = round(min(pasos * vol_step, vol_max), 8)
    if lotes < vol_min - 1e-9:
        return Lotaje(0.0, False,
                      f"Capital insuficiente (${capital:,.2f}): el lote mínimo {vol_min} "
                      f"requiere ${capital_minimo:,.2f} con {pct:.1%} de riesgo", capital_minimo)
    return Lotaje(lotes, True, "ok", capital_minimo)


def calcular_lotes(simbolo: str, capital: float, temporalidad: str, precio_entrada: float,
                   sl_precio: float | None = None, pct_riesgo: float | None = None) -> Lotaje:
    """Lotes para una operación en MT5 (requiere terminal conectada).

    Lanza ValueError por errores de configuración (símbolo inexistente, SL
    faltante en scalping/intraday, % swing > 2%); devuelve viable=False cuando
    el problema es el capital.
    """
    import MetaTrader5 as mt5  # ponytail: import tardío — el núcleo puro se prueba sin MT5

    info = mt5.symbol_info(simbolo)
    if info is None:
        raise ValueError(f"Símbolo no encontrado en MT5: {simbolo}")

    if es_swing(temporalidad):
        pct = PCT_RIESGO_SWING if pct_riesgo is None else pct_riesgo
        if pct > PCT_MAX_SWING:
            raise ValueError(f"Swing permite máximo {PCT_MAX_SWING:.0%} de riesgo, se pidió {pct:.1%}")
        # ponytail: margen calculado como BUY; en XM el margen de SELL es el mismo
        costo = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, simbolo, 1.0, precio_entrada)
        if not costo or costo <= 0:
            raise ValueError(f"order_calc_margin devolvió {costo} para {simbolo}")
    else:
        if sl_precio is None:
            raise ValueError("sl_precio requerido para scalping/intraday")
        pct = PCT_RIESGO_DEFAULT if pct_riesgo is None else pct_riesgo
        costo = perdida_por_lote(precio_entrada, sl_precio, info.trade_tick_size, info.trade_tick_value)

    return dimensionar(capital, pct, costo, info.volume_min, info.volume_max, info.volume_step)


def demo() -> None:
    """Self-check del núcleo con valores reales de XM (sin MT5 en vivo)."""
    mn, mx, st = 0.01, 100.0, 0.01

    # EURUSD scalping: tick 0.00001 = $1/lote. $300, 1%, SL 30 pips → $3 / $300 = 0.01
    r = dimensionar(300, 0.01, perdida_por_lote(1.1050, 1.1020, 0.00001, 1.0), mn, mx, st)
    assert r.viable and r.lotes == 0.01, r

    # EURUSD intraday: $300, 1%, SL 120 pips → no viable, requiere $1,200
    r = dimensionar(300, 0.01, perdida_por_lote(1.1050, 1.0930, 0.00001, 1.0), mn, mx, st)
    assert not r.viable and abs(r.capital_minimo - 1200) < 1e-6, r

    # Capital grande: $10,000, 1%, 50 pips → $100 / $500 = 0.20 (sin error de flotante)
    r = dimensionar(10_000, 0.01, perdida_por_lote(1.1050, 1.1000, 0.00001, 1.0), mn, mx, st)
    assert r.lotes == 0.20, r

    # USDJPY: tick 0.001 ≈ $0.667/lote (USDJPY 150). $10,000, 1%, SL 30 pips (0.30 yen)
    # → pérdida/lote = 300 × 0.667 = $200 → 0.50 lotes. (La tabla de contratos anterior
    # daba "capital insuficiente" aquí porque medía la pérdida en yenes.)
    r = dimensionar(10_000, 0.01, perdida_por_lote(150.00, 149.70, 0.001, 0.667), mn, mx, st)
    assert r.viable and r.lotes == 0.49, r  # 0.4998 → hacia abajo, nunca excede el 1%

    # GOLD: tick 0.01 = $1/lote (100 oz). $10,000, 1%, SL $10 → $1,000/lote → 0.10
    r = dimensionar(10_000, 0.01, perdida_por_lote(2650.0, 2640.0, 0.01, 1.0), mn, mx, st)
    assert r.lotes == 0.10, r

    # Swing EURUSD con $300: margen 1 lote ≈ $3,683 (1:30) → 2% = $6 → no viable.
    # Coincide con la regla confirmada: con $300 solo es viable Scalping/Intraday.
    r = dimensionar(300, 0.02, 3683.0, mn, mx, st)
    assert not r.viable, r

    # Tope del broker
    r = dimensionar(10_000_000, 0.01, 10.0, mn, mx, st)
    assert r.lotes == 100.0, r

    assert dimensionar(0, 0.01, 10.0, mn, mx, st).viable is False
    print("riesgo.demo() OK")


if __name__ == "__main__":
    demo()
