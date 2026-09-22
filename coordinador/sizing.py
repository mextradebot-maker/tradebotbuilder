"""Módulo de sizing de lotes adaptativo por clase de activo y gestión de riesgo.

Reglas MexTradeBot:
1. Intraday / Scalping:
   - Usa Stop Loss estricto en la vela de barrido/estructura.
   - Riesgo por operación por defecto = 1% del balance.
   - Lotes = (Balance * 1%) / (distancia_sl * valor_pip_por_lote).
   - Si la cuenta no tiene capital suficiente para la distancia del stop (requiere menos de 0.01 lotes),
     alerta de capital insuficiente y recomienda temporalidades de stop más corto.

2. Swing Trade (Regla de Oro):
   - SIN Stop Loss en MT5 (evita cazadores de stop / manipulación).
   - Salida gestionada 100% por el Coordinador al detectar CHoCH inverso en la temporalidad Swing (H/S/M).
   - Volumen basado en Opción A (Margen expuesto <= 2% del capital):
     Lotes = (Balance * 2%) / margen_requerido_por_lote.
"""

import math
from typing import Optional, Dict, Any, Tuple

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

CONTRATO_DEFAULT = {
    "FOREX": 100000,
    "GOLD": 100,      # XAUUSD / GOLD: 1 lote = 100 oz
    "XAUUSD": 100,
    "XAGUSD": 5000,   # Plata: 1 lote = 5000 oz
    "US30": 1,        # 1 punto = $1 por lote
    "US500": 1,
    "US100": 1,
    "GER40": 1,
    "UK100": 1,
    "JP225": 100,
    "BTCUSD": 1,
    "ETHUSD": 1,
    "XRPUSD": 1000,
}


def obtener_categoria(simbolo: str) -> str:
    s = simbolo.upper()
    if s in ("GOLD", "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"):
        return "METALES"
    if s in ("US30", "US500", "US100", "GER40", "UK100", "JP225"):
        return "INDICES"
    if s in ("BTCUSD", "ETHUSD", "XRPUSD"):
        return "CRYPTO"
    if s in ("WTIUSD", "BRENTUSD"):
        return "MATERIAS_PRIMAS"
    if s in ("AMXL", "CEMEXCPO", "GOOGL", "NVDA", "META", "WMT"):
        return "ACCIONES"
    return "FOREX"


def redondear_lote(lotes: float, min_vol: float = 0.01, max_vol: float = 100.0, step_vol: float = 0.01) -> float:
    """Redondea el volumen al paso (step) permitido por el broker y aplica min/max."""
    if lotes <= 0 or math.isnan(lotes):
        return min_vol

    pasos = round(lotes / step_vol)
    lotes_ajustados = pasos * step_vol
    lotes_final = max(min_vol, min(max_vol, lotes_ajustados))
    return round(lotes_final, 2)


def evaluar_capital_intraday(
    simbolo: str,
    balance: float,
    precio_entrada: float,
    sl_precio: float,
    pct_riesgo: float = 0.01,
) -> Tuple[float, bool, str, float]:
    """Evalúa la viabilidad de capital para un trade Intraday/Scalping con Stop Loss.

    Retorna: (lotes_optimos, es_viable, mensaje, capital_minimo_requerido)
    """
    if balance <= 0 or precio_entrada <= 0 or sl_precio <= 0:
        return 0.01, False, "Parámetros de precio o balance inválidos", 0.0

    distancia_sl = abs(precio_entrada - sl_precio)
    if distancia_sl <= 1e-8:
        return 0.01, False, "Distancia de Stop Loss nula", 0.0

    riesgo_usd = balance * pct_riesgo
    simbolo_upper = simbolo.upper()
    categoria = obtener_categoria(simbolo_upper)
    contrato = CONTRATO_DEFAULT.get(simbolo_upper, CONTRATO_DEFAULT.get(categoria, 100000))

    valor_perdida_1_lote = distancia_sl * contrato
    if valor_perdida_1_lote <= 0:
        return 0.01, False, "Cálculo de valor de pérdida inválido", 0.0

    lotes_teoricos = riesgo_usd / valor_perdida_1_lote
    capital_minimo = (0.01 * valor_perdida_1_lote) / pct_riesgo

    if lotes_teoricos < 0.01:
        msg = f"Capital insuficiente (${balance:.2f}) para SL de {distancia_sl:.4f}. Requiere min ${capital_minimo:.2f}."
        return 0.01, False, msg, capital_minimo

    lotes_finales = redondear_lote(lotes_teoricos, 0.01, 10.0, 0.01)
    return lotes_finales, True, "Capital y riesgo adecuados", capital_minimo


def calcular_lotaje_swing(
    simbolo: str,
    balance: float,
    precio_entrada: float,
    pct_riesgo: float = 0.02,
    apalancamiento: float = 30.0,
) -> float:
    """Calcula el volumen para Swing Trading usando la Regla de Oro (Opción A).

    SIN Stop Loss en MT5. Lotes = (Balance * pct_riesgo) / margen_por_lote.
    """
    if balance <= 0 or precio_entrada <= 0:
        return 0.01

    simbolo_upper = simbolo.upper()
    categoria = obtener_categoria(simbolo_upper)
    contrato = CONTRATO_DEFAULT.get(simbolo_upper, CONTRATO_DEFAULT.get(categoria, 100000))

    # Intentar consultar MT5 para margen real si está disponible
    if mt5 is not None:
        try:
            margen_1_lote = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, simbolo_upper, 1.0, precio_entrada)
            if margen_1_lote and margen_1_lote > 0:
                margen_permitido = balance * pct_riesgo
                lotes_raw = margen_permitido / margen_1_lote
                return redondear_lote(lotes_raw, 0.01, 10.0, 0.01)
        except Exception:
            pass

    # Heurística de margen por apalancamiento
    # Margen para 1 lote = (precio_entrada * contrato) / apalancamiento
    margen_estimado_1_lote = (precio_entrada * contrato) / apalancamiento
    if margen_estimado_1_lote <= 0:
        return 0.01

    margen_permitido = balance * pct_riesgo
    lotes_raw = margen_permitido / margen_estimado_1_lote
    return redondear_lote(lotes_raw, 0.01, 10.0, 0.01)


def demo() -> None:
    print("--- DEMO DE CALCULADORA DE SIZING (REGLAS MEXTRADEBOT) ---")

    # Pruebas con $300 USD
    balance_pequeno = 300.0

    # 1. Swing EURUSD con $300 (Regla de Oro: Opción A - Sin SL en MT5, 2% riesgo = $6 de margen)
    lotes_swing = calcular_lotaje_swing("EURUSD", balance_pequeno, 1.1050, pct_riesgo=0.02, apalancamiento=30.0)
    print(f"[Swing EURUSD $300] Lotes: {lotes_swing} (Sin SL en MT5, salida por CHoCH inverso)")
    assert lotes_swing == 0.01, f"Esperado 0.01, obtenido {lotes_swing}"

    # 2. Intraday EURUSD con $300 (SL amplio de 120 pips: 1.1050 a 1.0930)
    lotes_intraday, viable, msg, cap_min = evaluar_capital_intraday("EURUSD", balance_pequeno, 1.1050, 1.0930, pct_riesgo=0.01)
    print(f"[Intraday EURUSD $300 - 120 pips SL] Viable: {viable} | Mensaje: {msg}")
    assert not viable, "Debe ser no viable por capital insuficiente para 120 pips"
    assert abs(cap_min - 1200.0) < 1e-4, f"Esperado capital min $1200, obtenido {cap_min}"

    # 3. Scalping EURUSD con $300 (SL estrecho de 25 pips: 1.1050 a 1.1025)
    lotes_scalp, viable_s, msg_s, cap_min_s = evaluar_capital_intraday("EURUSD", balance_pequeno, 1.1050, 1.1025, pct_riesgo=0.01)
    print(f"[Scalping EURUSD $300 - 25 pips SL] Viable: {viable_s} | Lotes: {lotes_scalp} | Mensaje: {msg_s}")
    assert viable_s, "Debe ser viable para 25 pips de SL con $300"
    assert lotes_scalp == 0.01, f"Esperado 0.01, obtenido {lotes_scalp}"

    # 4. Intraday GOLD con $10,000 balance (SL de 10 USD: 2650 a 2640)
    lotes_gold, viable_g, msg_g, _ = evaluar_capital_intraday("GOLD", 10000.0, 2650.0, 2640.0, pct_riesgo=0.01)
    print(f"[Intraday GOLD $10,000 - $10 SL] Lotes: {lotes_gold} | Riesgo: $100.00 USD (1%)")
    assert lotes_gold == 0.10, f"Esperado 0.10, obtenido {lotes_gold}"

    print("PRUEBAS DE SIZING MEXTRADEBOT OK.")


if __name__ == "__main__":
    demo()
