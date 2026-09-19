"""Backtesting formal — Plan de construcción, Paso 2 (Backtrader/VectorBT en el
plan original). Este módulo es una simulación propia sobre pandas en vez de
esas librerías: los setups que detecta `motor_smc.setup_ob_fvg` son eventos
discretos (decenas por año, no miles de barras de una estrategia continua),
así que un framework de backtesting completo es más máquina de la que hace
falta — simular "desde la entrada, ¿toca stop o TP primero?" es simple y
totalmente auditable en unas líneas de pandas. Si más adelante hace falta
comisiones/slippage/múltiples posiciones simultáneas/portfolio, ahí sí vale
la pena migrar a Backtrader o VectorBT.

Aplica las 3 "reglas de oro" de la Sección 05 del mapa técnico:
  - nunca optimizar volumen — N/A todavía (no hay money management/tamaño de
    posición implementado, solo se mide en R = múltiplos de riesgo).
  - siempre out-of-sample — `backtest_out_of_sample()` separa in-sample
    (antes del corte) de out-of-sample (desde el corte), nunca los mezcla.
  - nunca partir de un perdedor — `reporte()` expone `rentable_sin_optimizar`:
    si el setup, TAL CUAL está (sin ajustar ningún parámetro), ya tiene
    expectativa positiva. Si no, la regla de oro dice que no hay que
    optimizar para forzarlo a ganar — eso es la señal de overfitting.
"""

import pandas as pd

# ponytail: TP fijo a un multiplo de R; la Seccion 7.4 del setup insignia no
# documenta una regla propia de take-profit, solo entrada y stop. Ajustar
# aca (o exponerlo como parametro de optimizacion mas adelante) si aparece
# una regla mejor en la metodologia.
RETORNO_RIESGO_TP = 2.0


def _simular_uno(ohlc: pd.DataFrame, setup: pd.Series, r_multiplo_tp: float) -> dict:
    entrada, stop, direccion = setup["entrada"], setup["stop"], setup["direccion"]
    riesgo = abs(entrada - stop)
    riesgo_pct = riesgo / entrada  # distancia del stop como % del precio -- ver reporte()
    tp = entrada + r_multiplo_tp * riesgo if direccion == "long" else entrada - r_multiplo_tp * riesgo

    inicio = int(setup["indice_fvg"]) + 1
    for i in range(inicio, len(ohlc)):
        vela = ohlc.iloc[i]
        if direccion == "long":
            toco_stop, toco_tp = vela["low"] <= stop, vela["high"] >= tp
        else:
            toco_stop, toco_tp = vela["high"] >= stop, vela["low"] <= tp

        if toco_stop or toco_tp:
            # si ambas se tocan en la misma vela no hay forma de saber el
            # orden intrabar con datos OHLC de vela cerrada: se asume el
            # peor caso (stop) para no inflar el resultado
            if toco_stop:
                return {"resultado": "perdio", "r": -1.0, "velas": i - inicio + 1, "riesgo_pct": riesgo_pct}
            return {"resultado": "gano", "r": r_multiplo_tp, "velas": i - inicio + 1, "riesgo_pct": riesgo_pct}

    return {"resultado": "sin_resolver", "r": 0.0, "velas": len(ohlc) - inicio, "riesgo_pct": riesgo_pct}


def simular(ohlc: pd.DataFrame, setups: pd.DataFrame, r_multiplo_tp: float = RETORNO_RIESGO_TP) -> pd.DataFrame:
    """Corre cada setup detectado hacia adelante hasta que toque stop o TP.
    Entrada asumida como fill garantizado al precio de `entrada` (no verifica
    que el precio realmente vuelva a tocar el FVG) — ver limitación en README."""
    columnas_resultado = ["resultado", "r", "velas", "riesgo_pct"]
    if setups.empty:
        return pd.concat([setups, pd.DataFrame(columns=columnas_resultado)], axis=1)
    resultados = [_simular_uno(ohlc, fila, r_multiplo_tp) for _, fila in setups.iterrows()]
    return pd.concat([setups.reset_index(drop=True), pd.DataFrame(resultados)], axis=1)


def _tasas_confluencia(resultados: pd.DataFrame) -> dict:
    """% de los setups (todos, no solo los resueltos) que ademas tuvieron Order
    Block/Liquidez confluente -- ver motor_smc/setup_ob_fvg.py: son anotaciones
    informativas, no un filtro, asi que se reportan como tasa histórica en vez
    de exigirlas. `None` si las columnas no vienen (p.ej. resultados vacio)."""
    total = len(resultados)
    if total == 0 or "order_block_confluente" not in resultados.columns:
        return {"confluencia_order_block": None, "confluencia_liquidez": None}
    return {
        "confluencia_order_block": round(float(resultados["order_block_confluente"].sum()) / total, 4),
        "confluencia_liquidez": round(float(resultados["liquidez_confluente"].sum()) / total, 4),
    }


def reporte(resultados: pd.DataFrame) -> dict:
    confluencia = _tasas_confluencia(resultados)
    resueltos = resultados[resultados["resultado"] != "sin_resolver"]
    n = len(resueltos)
    if n == 0:
        return {"n_setups": 0, "sin_resolver": len(resultados), "rentable_sin_optimizar": None, **confluencia}

    ganadas = (resueltos["resultado"] == "gano").sum()
    expectativa = float(resueltos["r"].mean())
    # riesgo_pct_promedio: distancia promedio del stop como % del precio de
    # entrada -- insumo del position sizing (Modulo 10 de metodologia-trading.md,
    # L54: riesgo 0.25%-1% del capital por operacion). Independiente del
    # instrumento (forex/metales/indices/cripto/acciones tienen tamanos de
    # contrato y valor de pip distintos; el % de distancia no).
    riesgo_pct_promedio = float(resueltos["riesgo_pct"].mean()) if "riesgo_pct" in resueltos.columns else None
    return {
        "n_setups": n,
        "sin_resolver": len(resultados) - n,
        "winrate": round(float(ganadas / n), 4),
        "r_total": round(float(resueltos["r"].sum()), 4),
        "expectativa_r": round(expectativa, 4),
        "rentable_sin_optimizar": expectativa > 0,
        "riesgo_pct_promedio": round(riesgo_pct_promedio, 6) if riesgo_pct_promedio is not None else None,
        **confluencia,
    }


def backtest_direccion(ohlc: pd.DataFrame, direccion: str, swing_length: int = 20, r_multiplo_tp: float = RETORNO_RIESGO_TP) -> dict:
    """Corre detección + backtest sobre todo el ohlc, filtrado a una sola
    dirección ('long'/'short') — usado por el backtest bajo demanda de T-04
    (Telegram): el usuario ya eligió símbolo y el motor ya determinó la
    tendencia (`motor_smc.obtener_tendencia`), así que solo interesa el
    desempeño histórico de esa dirección específica."""
    from motor_smc import analizar, detectar_setups

    resultado_motor = analizar(ohlc, swing_length=swing_length)
    setups = detectar_setups(ohlc, resultado_motor)
    setups_direccion = setups[setups["direccion"] == direccion]
    return reporte(simular(ohlc, setups_direccion, r_multiplo_tp))


def backtest_out_of_sample(ohlc: pd.DataFrame, corte, swing_length: int = 20, r_multiplo_tp: float = RETORNO_RIESGO_TP) -> dict:
    """Separa in-sample (antes de `corte`) de out-of-sample (desde `corte`) y
    corre deteccion + simulacion en cada tramo por separado, sin mezclar."""
    from motor_smc import analizar, detectar_setups

    tramos = {"in_sample": ohlc[ohlc.index < corte], "out_of_sample": ohlc[ohlc.index >= corte]}
    salida = {}
    for nombre, tramo in tramos.items():
        resultado_motor = analizar(tramo, swing_length=swing_length)
        setups = detectar_setups(tramo, resultado_motor)
        salida[nombre] = reporte(simular(tramo, setups, r_multiplo_tp))
    return salida


def demo() -> None:
    from datetime import datetime

    from conectividad import obtener_velas

    ohlc = obtener_velas("XAUUSD", datetime(2020, 1, 1), datetime(2024, 1, 1))
    resultado = backtest_out_of_sample(ohlc, corte=datetime(2023, 1, 1, tzinfo=ohlc.index.tz), swing_length=20)

    for tramo in ("in_sample", "out_of_sample"):
        assert "n_setups" in resultado[tramo]
        if resultado[tramo]["n_setups"] > 0:
            assert "riesgo_pct_promedio" in resultado[tramo]
            assert resultado[tramo]["riesgo_pct_promedio"] > 0

    print("backtesting.backtest.demo() OK — XAUUSD H1 2020-2024, corte 2023-01-01")
    for nombre, r in resultado.items():
        print(f"  {nombre}: {r}")


if __name__ == "__main__":
    demo()
