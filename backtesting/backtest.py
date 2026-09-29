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

# v2: "sin_llenar" (el precio nunca volvió a la entrada) y "sin_resolver" no cuentan.
RESULTADOS_RESUELTOS = ("gano", "perdio", "invalidado")


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
    resueltos = resultados[resultados["resultado"].isin(RESULTADOS_RESUELTOS)]
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


def _simular_v2_uno(ohlc: pd.DataFrame, s) -> dict:
    """Motor v2: orden límite en `entrada` desde la vela siguiente a `indice_conocido`.
    - Vela que llena: si toca el stop -> perdió (peor caso); si cierra más allá de
      `zona_extremo` -> invalidado, sale al cierre (§1.3); el TP no cuenta en esa vela
      (el orden intrabar es desconocido).
    - Después: stop antes que TP si ambos se tocan en la misma vela (peor caso)."""
    long = s["direccion"] == "long"
    entrada, stop, tp, extremo = s["entrada"], s["stop"], s["tp"], s["zona_extremo"]
    riesgo = abs(entrada - stop)
    riesgo_pct = riesgo / entrada
    lleno_en = None
    for p in range(int(s["indice_conocido"]) + 1, len(ohlc)):
        v = ohlc.iloc[p]
        toca_stop = v["low"] <= stop if long else v["high"] >= stop
        if lleno_en is None:
            if not (v["low"] <= entrada if long else v["high"] >= entrada):
                continue
            lleno_en = p
            if toca_stop:
                return {"resultado": "perdio", "r": -1.0, "velas": 1, "riesgo_pct": riesgo_pct}
            if (v["close"] < extremo) if long else (v["close"] > extremo):
                r = (v["close"] - entrada) / riesgo if long else (entrada - v["close"]) / riesgo
                return {"resultado": "invalidado", "r": float(r), "velas": 1, "riesgo_pct": riesgo_pct}
            continue
        velas = p - lleno_en + 1
        if toca_stop:
            return {"resultado": "perdio", "r": -1.0, "velas": velas, "riesgo_pct": riesgo_pct}
        if v["high"] >= tp if long else v["low"] <= tp:
            return {"resultado": "gano", "r": float(abs(tp - entrada) / riesgo), "velas": velas, "riesgo_pct": riesgo_pct}
    estado = "sin_llenar" if lleno_en is None else "sin_resolver"
    return {"resultado": estado, "r": 0.0, "velas": 0, "riesgo_pct": riesgo_pct}


def simular_v2(ohlc: pd.DataFrame, setups: pd.DataFrame) -> pd.DataFrame:
    """Simula solo los setups válidos del motor v2."""
    validos = setups[setups["valido"].astype(bool)].reset_index(drop=True)
    columnas = ["resultado", "r", "velas", "riesgo_pct"]
    if validos.empty:
        return pd.concat([validos, pd.DataFrame(columns=columnas)], axis=1)
    return pd.concat([validos, pd.DataFrame([_simular_v2_uno(ohlc, f) for _, f in validos.iterrows()])], axis=1)


def backtest_v2(ohlc: pd.DataFrame, setups: pd.DataFrame) -> dict:
    """Reporte separado por tipo de apertura y dirección (compra/venta, mismo vocabulario que /api/tendencia)."""
    resultados = simular_v2(ohlc, setups)
    salida = {}
    for tipo in ("reversion", "continuacion"):
        salida[tipo] = {}
        for ls, cv in (("long", "compra"), ("short", "venta")):
            sub = resultados[(resultados["tipo"] == tipo) & (resultados["direccion"] == ls)] if len(resultados) else resultados
            salida[tipo][cv] = reporte(sub)
    return salida


def demo() -> None:
    from datetime import datetime

    from conectividad import obtener_velas

    # ── v2 (sintético, sin red) ──
    idx = pd.date_range("2026-01-05", periods=8, freq="h", tz="UTC")

    def serie(filas):
        return pd.DataFrame(filas, columns=["open", "high", "low", "close", "volume"], index=idx[: len(filas)])

    base = {"tipo": "continuacion", "direccion": "long", "indice_conocido": 0, "entrada": 100.0,
            "stop": 95.0, "tp": 110.0, "zona_extremo": 97.0, "valido": True}
    s = pd.Series(base)
    gana = serie([(105, 106, 104, 105, 1), (102, 103, 99, 101, 1), (101, 104, 100, 103, 1), (103, 111, 102, 110, 1)])
    r = _simular_v2_uno(gana, s)
    assert r["resultado"] == "gano" and r["r"] == 2.0, r
    assert _simular_v2_uno(serie([(105, 106, 104, 105, 1), (106, 112, 105, 111, 1)]), s)["resultado"] == "sin_llenar"
    inval = _simular_v2_uno(serie([(105, 106, 104, 105, 1), (101, 101, 95.5, 96, 1)]), s)
    assert inval["resultado"] == "invalidado" and round(inval["r"], 2) == -0.8, inval  # sale al cierre 96
    ambos = serie([(105, 106, 104, 105, 1), (102, 103, 99, 101, 1), (101, 111, 94, 100, 1)])
    assert _simular_v2_uno(ambos, s)["resultado"] == "perdio"  # stop y TP en la misma vela: peor caso
    tp_en_llenado = serie([(105, 106, 104, 105, 1), (101, 111, 99, 108, 1), (108, 112, 107, 111, 1)])
    assert _simular_v2_uno(tp_en_llenado, s)["velas"] == 2  # el TP de la vela que llena no cuenta
    corto = pd.Series({**base, "direccion": "short", "stop": 105.0, "tp": 90.0, "zona_extremo": 103.0})
    assert _simular_v2_uno(serie([(95, 96, 94, 95, 1), (98, 101, 97, 99, 1), (99, 100, 89, 90, 1)]), corto)["resultado"] == "gano"
    rep = backtest_v2(gana, pd.DataFrame([base, {**base, "valido": False}]))
    assert rep["continuacion"]["compra"]["n_setups"] == 1 and rep["continuacion"]["compra"]["expectativa_r"] == 2.0
    assert rep["reversion"]["compra"]["n_setups"] == 0
    sin_llenar = backtest_v2(serie([(105, 106, 104, 105, 1), (106, 112, 105, 111, 1)]), pd.DataFrame([base]))
    assert sin_llenar["continuacion"]["compra"]["n_setups"] == 0  # sin_llenar no cuenta como resuelto
    print("backtest v2 OK")

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
