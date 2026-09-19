"""Compara los 4 indicadores de confluencia (motor_smc.indicadores) contra la
misma direccion de un activo, y decide si alguno mejora sobre no usar ninguno
-- SIEMPRE con las 3 reglas de oro de este modulo (ver backtest.py): la
decision se toma sobre el tramo OUT-OF-SAMPLE, nunca sobre el in-sample.

Motivacion (sesion 2026-09-19): "un mismo activo puede optimizarse mejor con
un indicador que otro, y viceversa" -- cierto, pero probar 4 indicadores por
activo y quedarse con el que gane in-sample es la sobreoptimizacion clasica
que las 3 reglas de oro existen para evitar (ver docstring de backtest.py).
Este modulo exige que el indicador elegido sea rentable Y mejor que el
baseline sin filtro EN EL TRAMO QUE NO SE USO PARA ELEGIRLO.

No decide todavia como "pegar" el indicador elegido al robot entregado --
eso vive en la capa que orquesta la entrega (T-04 en n8n), igual que la
regla "swing solo si es alcista" (ver motor_smc/tendencia.py).
"""

import pandas as pd

from motor_smc import analizar, detectar_setups
from motor_smc.indicadores import INDICADORES_DISPONIBLES, aplicar_confluencia

from .backtest import RETORNO_RIESGO_TP, reporte, simular


def _reporte_con_filtro(tramo: pd.DataFrame, direccion: str, swing_length: int, r_multiplo_tp: float, indicador: str | None) -> dict:
    resultado_motor = analizar(tramo, swing_length=swing_length)
    setups = detectar_setups(tramo, resultado_motor)
    setups_direccion = setups[setups["direccion"] == direccion]
    if indicador is not None:
        setups_direccion = aplicar_confluencia(tramo, setups_direccion, resultado_motor, indicador)
    return reporte(simular(tramo, setups_direccion, r_multiplo_tp))


def comparar_indicadores(
    ohlc: pd.DataFrame,
    direccion: str,
    swing_length: int = 20,
    fraccion_out_of_sample: float = 0.25,
    r_multiplo_tp: float = RETORNO_RIESGO_TP,
) -> dict:
    """`fraccion_out_of_sample` (no una fecha) para poder reusar esta funcion con
    cualquier ventana de dias -- cada temporalidad usa una distinta (ver
    api/setups.py::DIAS_POR_TEMPORALIDAD). 0.25 = el ultimo 25% del historico
    pedido es el tramo que decide, nunca el primer 75% con el que se explora."""
    n = len(ohlc)
    idx_corte = int(n * (1 - fraccion_out_of_sample))
    corte = ohlc.index[idx_corte]
    tramos = {"in_sample": ohlc[ohlc.index < corte], "out_of_sample": ohlc[ohlc.index >= corte]}

    resultados = {"Ninguno": {nombre: _reporte_con_filtro(tramo, direccion, swing_length, r_multiplo_tp, None) for nombre, tramo in tramos.items()}}
    for indicador in INDICADORES_DISPONIBLES:
        resultados[indicador] = {nombre: _reporte_con_filtro(tramo, direccion, swing_length, r_multiplo_tp, indicador) for nombre, tramo in tramos.items()}

    baseline_oos = resultados["Ninguno"]["out_of_sample"]
    baseline_expectativa = baseline_oos.get("expectativa_r") or 0.0

    candidatos = []
    for indicador in INDICADORES_DISPONIBLES:
        oos = resultados[indicador]["out_of_sample"]
        expectativa = oos.get("expectativa_r")
        if expectativa is not None and oos.get("rentable_sin_optimizar") and expectativa > baseline_expectativa:
            candidatos.append((indicador, expectativa))

    mejor_indicador = max(candidatos, key=lambda c: c[1])[0] if candidatos else "Ninguno"

    return {
        "direccion": direccion,
        "mejor_indicador": mejor_indicador,
        "baseline_expectativa_out_of_sample": round(baseline_expectativa, 4),
        "detalle_por_indicador": resultados,
    }


def demo() -> None:
    from datetime import datetime

    from conectividad import obtener_velas

    ohlc = obtener_velas("XAUUSD", datetime(2022, 1, 1), datetime(2026, 1, 1))
    resultado = comparar_indicadores(ohlc, "long", swing_length=20)

    assert "mejor_indicador" in resultado
    assert resultado["mejor_indicador"] in ("Ninguno",) + INDICADORES_DISPONIBLES
    for nombre in ("Ninguno",) + INDICADORES_DISPONIBLES:
        assert nombre in resultado["detalle_por_indicador"]
        assert "out_of_sample" in resultado["detalle_por_indicador"][nombre]

    print(f"backtesting.comparar_indicadores.demo() OK — XAUUSD long H1 2022-2026: "
          f"mejor_indicador={resultado['mejor_indicador']}, baseline_oos={resultado['baseline_expectativa_out_of_sample']}R")
    for nombre, tramos in resultado["detalle_por_indicador"].items():
        oos = tramos["out_of_sample"]
        print(f"  {nombre}: n_setups_oos={oos.get('n_setups')}, expectativa_oos={oos.get('expectativa_r')}")


if __name__ == "__main__":
    demo()
