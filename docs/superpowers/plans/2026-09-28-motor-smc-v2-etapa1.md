# Motor SMC v2 — Etapa 1 (motor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el detector SMC v2 (reversión y continuación, reglas R1–R6 del curso, sin anticipación) con su backtest, y exponerlo en `/api/setups` como bloque `motor_v2` en modo sombra, sin tocar lo que hoy reciben los EA.

**Architecture:** Módulos nuevos `motor_smc/reglas.py` (una función pura por regla) y `motor_smc/setups_v2.py` (candidatos + evaluación + embudo). El detector viejo (`setup_ob_fvg.detectar_setups`) queda intacto: lo siguen usando los EA, T-01, `backtest_direccion`, `comparar_indicadores` y el modo sombra de la Etapa 2 necesita los dos motores. `backtesting/backtest.py` gana `simular_v2`/`backtest_v2`. `api/setups.py` agrega `respuesta["motor_v2"]` envuelto en try/except.

**Tech Stack:** Python 3.12+, pandas, `smartmoneyconcepts` (ya instalado en `.venv`), `dukascopy_python`. Pruebas en el estilo del repo: función `demo()` con `assert` al final de cada módulo, ejecutada con `python -m`.

**Spec:** `docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md` (léelo antes de empezar).

## Global Constraints

- Todos los comandos se corren desde `C:\Proyectos\MexTradeBot\tradebotbuilder` en Git Bash, con el prefijo `PYTHONIOENCODING=utf-8` (la librería `smartmoneyconcepts` imprime un emoji al importarse y rompe la consola cp1252 sin él). Intérprete: `.venv/Scripts/python`.
- No modificar `motor_smc/setup_ob_fvg.py`, `motor_smc/tendencia.py`, `backtest_direccion`, `backtest_out_of_sample`, ni los campos existentes de la respuesta de `/api/setups` (`setups`, `setups_confirmados`, `backtests`, `tendencia`, ...). La Etapa 1 solo **agrega**.
- `smc.*` devuelve DataFrames con índice posicional 0..n-1; `ohlc` trae índice de fechas UTC. Todo el motor trabaja en posiciones enteras y usa `.iloc` sobre `ohlc`.
- Umbrales exactos (del spec): volumen por vela `15m 3.0, 30m 3.0, 1H 2.5, 4H 2.0, D 1.0, S None, M None` sobre las 20 velas anteriores; FVG ≥ `0.5 × ATR(14)`; EMA de sesgo `200 → 50 → 20 → no aplica`; RR mínimo `2.0`.
- Resultado de cada regla: `{"cumple": True | False | None, "dato": ..., "razon": str}`; `None` = no aplica (no bloquea). Valores en esos dicts deben ser tipos nativos de Python (float/int/bool/str/None) — el snapshot se guarda con `json.dumps` en una columna `jsonb`, que rechaza `NaN` y tipos numpy.
- Nombres de nodos/strings en español mexicano, igual que el resto del repo. Comentarios `ponytail:` donde se simplifica a propósito.
- Commits terminan con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No hacer `git push` hasta la Task 8.

---

## File Structure

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `motor_smc/reglas.py` | Crear | Constantes por vela, `cortar_mayor`, `atr`, R1–R6, `cruce_20_50`, helpers de prueba `velas`/`zigzag`/`ALCISTA` |
| `motor_smc/indicadores.py` | Modificar (agregar al final, antes de `demo`) | `divergencia()` para RSI/MACD (solo anota) |
| `motor_smc/setups_v2.py` | Crear | Candidatos de reversión y continuación, `evaluar`, `detectar_setups_v2`, `embudo` |
| `backtesting/backtest.py` | Modificar | `reporte` ignora `sin_llenar`; `simular_v2`, `backtest_v2` |
| `api/setups.py` | Modificar | Descarga temporalidad mayor y agrega `respuesta["motor_v2"]` (modo sombra) |
| `backtesting/embudo_v2.py` | Crear | Corrida real contra Dukascopy: tabla de embudo por símbolo × perfil |

---

### Task 1: Reglas base — constantes, recorte de la temporalidad mayor, R1, R2, R3

**Files:**
- Create: `motor_smc/reglas.py`

**Interfaces:**
- Produces: `UMBRAL_VOLUMEN`, `VENTANA_VOLUMEN`, `FACTOR_ATR_FVG`, `PERIODO_ATR`, `EMAS_SESGO`, `RR_MINIMO`, `SWING_LENGTH_POR_VELA: dict[str,int]`, `DURACION_VELA: dict[str, Timedelta|DateOffset]`, `NO_APLICA: dict`, `cortar_mayor(ohlc_mayor, ts_cierre, vela_mayor) -> DataFrame`, `atr(ohlc, periodo=14) -> Series`, `r1_volumen(ohlc, i, vela) -> dict`, `r2_fvg(ohlc, top, bottom, j) -> dict`, `r3_ema(ohlc, k, direccion) -> dict` (dato = periodo de EMA usado o None), `cruce_20_50(ohlc, k, direccion) -> "a_favor"|"en_contra"|None`, helper de prueba `velas(filas, inicio=..., freq=...) -> DataFrame`. `direccion` siempre es `"long"` o `"short"`.

- [ ] **Step 1: Crear el archivo solo con constantes, helpers de prueba y la prueba**

```python
"""Reglas R1-R6 del motor SMC v2 — spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md.

Cada regla es una función pura que devuelve {"cumple": True|False|None, "dato": ..., "razon": str}.
cumple=None significa `no_aplica` (no bloquea). Toda regla usa solo velas previas o iguales al
índice que recibe (anti-anticipación); la temporalidad mayor llega ya recortada con `cortar_mayor`.
"""

import pandas as pd

# Umbrales por VELA, no por perfil (la Etapa 2 solo cambia el mapa perfil -> velas).
UMBRAL_VOLUMEN = {"15m": 3.0, "30m": 3.0, "1H": 2.5, "4H": 2.0, "D": 1.0, "S": None, "M": None}  # L45
VENTANA_VOLUMEN = 20
FACTOR_ATR_FVG = 0.5  # ponytail: el curso (L20) no da número para "gap imperceptible"; punto de partida acordado 28 sep
PERIODO_ATR = 14
EMAS_SESGO = (200, 50, 20)  # cadena de respaldo acordada con Ricardo
RR_MINIMO = 2.0  # §10.5
# ponytail: 30m y D no se calibraron en el barrido del 27 sep; heredan el valor de su vecina.
SWING_LENGTH_POR_VELA = {"15m": 8, "30m": 8, "1H": 10, "4H": 10, "D": 10, "S": 5, "M": 5}
DURACION_VELA = {
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1H": pd.Timedelta(hours=1),
    "4H": pd.Timedelta(hours=4),
    "D": pd.Timedelta(days=1),
    "S": pd.Timedelta(days=7),
    "M": pd.DateOffset(months=1),
}


def _res(cumple, dato, razon: str) -> dict:
    return {"cumple": cumple, "dato": dato, "razon": razon}


NO_APLICA = _res(None, None, "no aplica a este tipo de setup")


# ── pruebas ──────────────────────────────────────────────────────────────────

def velas(filas, inicio="2026-01-05 00:00", freq="15min") -> pd.DataFrame:
    """filas = [(open, high, low, close, volume), ...] con índice UTC."""
    idx = pd.date_range(inicio, periods=len(filas), freq=freq, tz="UTC")
    return pd.DataFrame(filas, columns=["open", "high", "low", "close", "volume"], index=idx)


def demo() -> None:
    # cortar_mayor: a las 10:30 solo cerró la vela 1H de las 09:00, no la de las 10:00
    mayor = velas([(1, 2, 0, 1, 1)] * 3, inicio="2026-01-05 09:00", freq="1h")
    assert list(cortar_mayor(mayor, pd.Timestamp("2026-01-05 10:30", tz="UTC"), "1H").index.hour) == [9]
    mensual = velas([(1, 2, 0, 1, 1)] * 2, inicio="2026-01-01", freq="MS")
    assert len(cortar_mayor(mensual, pd.Timestamp("2026-02-01", tz="UTC"), "M")) == 1

    # R1 volumen
    base = [(10, 11, 9, 10, 100.0)] * 20
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 350.0)]), 20, "15m")["cumple"] is True
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "15m")["cumple"] is False
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "1H")["cumple"] is True
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 20, "S")["cumple"] is None
    assert r1_volumen(velas(base + [(10, 11, 9, 10, 250.0)]), 5, "15m")["cumple"] is False

    # R2 FVG vs ATR: velas de rango 2 -> ATR 2 -> mínimo 1.0
    planas = velas([(10, 11, 9, 10, 100.0)] * 20)
    assert r2_fvg(planas, 11.5, 10.0, 15)["cumple"] is True
    assert r2_fvg(planas, 10.5, 10.0, 15)["cumple"] is False
    assert r2_fvg(planas, 11.5, 10.0, 5)["cumple"] is False  # sin 14 velas previas

    # R3 EMA con cadena 200 -> 50 -> 20 -> no aplica
    def subida(n):
        return velas([(p, p + 1, p - 1, p + 0.5, 100.0) for p in range(1, n + 1)])
    assert r3_ema(subida(250), 249, "long") == _res(True, 200, "cierre sobre la EMA 200")
    assert r3_ema(subida(60), 59, "long")["dato"] == 50
    assert r3_ema(subida(30), 29, "long")["dato"] == 20
    assert r3_ema(subida(10), 9, "long")["cumple"] is None
    assert r3_ema(subida(250), 249, "short")["cumple"] is False
    assert cruce_20_50(subida(60), 59, "long") == "a_favor"
    assert cruce_20_50(subida(30), 29, "long") is None

    print("reglas.demo() OK")


if __name__ == "__main__":
    demo()
```

- [ ] **Step 2: Correr la prueba y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.reglas`
Expected: FAIL con `NameError: name 'cortar_mayor' is not defined`

- [ ] **Step 3: Implementar — pegar esto justo después de `NO_APLICA = ...` (antes del bloque de pruebas)**

```python
def cortar_mayor(ohlc_mayor: pd.DataFrame, ts_cierre: pd.Timestamp, vela_mayor: str) -> pd.DataFrame:
    """Solo las velas mayores que ya cerraron en `ts_cierre` (apertura + duración <= ts_cierre)."""
    if ohlc_mayor.empty:
        return ohlc_mayor
    duracion = DURACION_VELA[vela_mayor]
    cierres = ohlc_mayor.index.map(lambda t: t + duracion)
    return ohlc_mayor[cierres <= ts_cierre]


def atr(ohlc: pd.DataFrame, periodo: int = PERIODO_ATR) -> pd.Series:
    cierre_prev = ohlc["close"].shift()
    rango = pd.concat(
        [ohlc["high"] - ohlc["low"], (ohlc["high"] - cierre_prev).abs(), (ohlc["low"] - cierre_prev).abs()], axis=1
    ).max(axis=1)
    return rango.rolling(periodo).mean()


def r1_volumen(ohlc: pd.DataFrame, i: int, vela: str) -> dict:
    umbral = UMBRAL_VOLUMEN[vela]
    if umbral is None:
        return _res(None, None, f"sin filtro de volumen en vela {vela} (L45)")
    if i < VENTANA_VOLUMEN:
        return _res(False, None, "datos insuficientes para el promedio de volumen")
    media = ohlc["volume"].iloc[i - VENTANA_VOLUMEN : i].mean()
    if not media > 0:
        return _res(False, None, "datos insuficientes: sin volumen en las 20 velas previas")
    multiplo = float(ohlc["volume"].iloc[i] / media)
    return _res(multiplo >= umbral, round(multiplo, 2), f"volumen del barrido {multiplo:.2f}x vs umbral {umbral}x")


def r2_fvg(ohlc: pd.DataFrame, top: float, bottom: float, j: int) -> dict:
    valor_atr = atr(ohlc).iloc[j - 1] if j >= 1 else float("nan")
    if pd.isna(valor_atr):
        return _res(False, None, "datos insuficientes para ATR(14)")
    proporcion = float(top - bottom) / float(valor_atr)
    return _res(proporcion >= FACTOR_ATR_FVG, round(proporcion, 2), f"FVG mide {proporcion:.2f} ATR vs mínimo {FACTOR_ATR_FVG}")


def r3_ema(ohlc: pd.DataFrame, k: int, direccion: str) -> dict:
    cierre = float(ohlc["close"].iloc[k])
    for periodo in EMAS_SESGO:
        if k + 1 >= periodo:
            ema = float(ohlc["close"].iloc[: k + 1].ewm(span=periodo, adjust=False).mean().iloc[-1])
            cumple = cierre > ema if direccion == "long" else cierre < ema
            lado = "sobre" if cierre > ema else "bajo"
            return _res(bool(cumple), periodo, f"cierre {lado} la EMA {periodo}")
    return _res(None, None, "no aplica: historia insuficiente")


def cruce_20_50(ohlc: pd.DataFrame, k: int, direccion: str) -> str | None:
    if k + 1 < 50:
        return None
    cierres = ohlc["close"].iloc[: k + 1]
    rapida = cierres.ewm(span=20, adjust=False).mean().iloc[-1]
    lenta = cierres.ewm(span=50, adjust=False).mean().iloc[-1]
    return "a_favor" if (rapida > lenta) == (direccion == "long") else "en_contra"
```

- [ ] **Step 4: Correr la prueba y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.reglas`
Expected: última línea `reglas.demo() OK`

- [ ] **Step 5: Commit**

```bash
git add motor_smc/reglas.py
git commit -m "Motor v2: reglas R1 volumen, R2 FVG vs ATR, R3 sesgo EMA 200/50/20

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Reglas con temporalidad mayor y liquidez — R4, R5, R6

**Files:**
- Modify: `motor_smc/reglas.py`

**Interfaces:**
- Consumes: `_res`, `SWING_LENGTH_POR_VELA`, `RR_MINIMO`, `velas` (Task 1).
- Produces: `r4_estructura(mayor, direccion, vela_mayor) -> dict` (dato = "compra"|"venta"|"sin_definir"), `r5_descuento_premium(mayor, entrada, direccion, vela_mayor) -> dict` (dato = posición 0..1 en el rango), `r6_tp_liquidez(ohlc, swings, conocido, entrada, stop, direccion, swing_length) -> dict` (dato = `{"tp": float, "rr": float}` o None), helpers de prueba `zigzag(tramos, freq="1h", inicio=...) -> DataFrame` y `ALCISTA: list`.

- [ ] **Step 1: Agregar imports, helpers de prueba y las nuevas pruebas**

Agregar debajo de `import pandas as pd`:

```python
from smartmoneyconcepts import smc

from .tendencia import obtener_tendencia
```

Agregar debajo de la función `velas(...)` en el bloque de pruebas:

```python
def zigzag(tramos, freq="1h", inicio="2026-01-05 00:00") -> pd.DataFrame:
    """Serie a partir de tramos [(desde, hasta, n_velas), ...] — velas limpias de rango 1."""
    closes = []
    for desde, hasta, n in tramos:
        paso = (hasta - desde) / n
        closes += [desde + paso * (p + 1) for p in range(n)]
    filas = []
    prev = tramos[0][0]
    for c in closes:
        filas.append((prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, 100.0))
        prev = c
    return velas(filas, inicio=inicio, freq=freq)


# Tramos largos a propósito: con swing_length 10 (1H) un swing necesita 10 velas a cada lado.
ALCISTA = [(100, 120, 24), (120, 110, 14), (110, 135, 24), (135, 124, 14), (124, 150, 24),
           (150, 138, 14), (138, 165, 24), (165, 150, 14), (150, 172, 24)]
```

Agregar dentro de `demo()`, justo antes de `print("reglas.demo() OK")`:

```python
    # R4 / R5 sobre una temporalidad mayor alcista limpia (HH/HL)
    mayor_alcista = zigzag(ALCISTA)
    r4 = r4_estructura(mayor_alcista, "long", "1H")
    assert r4["cumple"] is True, r4
    assert r4_estructura(mayor_alcista, "short", "1H")["cumple"] is False
    assert r4_estructura(mayor_alcista.iloc[:0], "long", "1H")["cumple"] is False
    r5_bajo = r5_descuento_premium(mayor_alcista, 152.0, "long", "1H")
    r5_alto = r5_descuento_premium(mayor_alcista, 163.0, "long", "1H")
    assert r5_bajo["cumple"] is True and r5_alto["cumple"] is False, (r5_bajo, r5_alto)
    assert r5_descuento_premium(mayor_alcista, 163.0, "short", "1H")["cumple"] is True

    # R6 TP en liquidez: swings controlados a mano (misma forma que smc.swing_highs_lows)
    plano = velas([(10, 10, 9, 10, 100.0)] * 30)
    swings = pd.DataFrame({"HighLow": [float("nan")] * 30, "Level": [float("nan")] * 30})
    swings.loc[5, ["HighLow", "Level"]] = [1, 20.0]
    swings.loc[8, ["HighLow", "Level"]] = [1, 15.0]
    swings.loc[25, ["HighLow", "Level"]] = [1, 12.0]  # 25 + 5 > 29: aún no confirmado
    r6 = r6_tp_liquidez(plano, swings, 29, entrada=10.0, stop=8.0, direccion="long", swing_length=5)
    assert r6["cumple"] is True and r6["dato"] == {"tp": 15.0, "rr": 2.5}, r6
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 6.0, "long", 5)["cumple"] is False  # 1.25R
    tomado = plano.copy()
    tomado.iloc[12, tomado.columns.get_loc("high")] = 16.0  # el precio ya buscó el 15
    assert r6_tp_liquidez(tomado, swings, 29, 10.0, 8.0, "long", 5)["dato"]["tp"] == 20.0
    assert r6_tp_liquidez(plano, swings, 29, 10.0, 12.0, "short", 5)["cumple"] is False  # no hay bajos
```

- [ ] **Step 2: Correr la prueba y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.reglas`
Expected: FAIL con `NameError: name 'r4_estructura' is not defined`

- [ ] **Step 3: Implementar — pegar después de `cruce_20_50` (antes del bloque de pruebas)**

```python
def r4_estructura(mayor: pd.DataFrame, direccion: str, vela_mayor: str) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    tendencia = obtener_tendencia(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])["direccion"]
    esperado = "compra" if direccion == "long" else "venta"
    return _res(tendencia == esperado, tendencia, f"estructura {vela_mayor}: {tendencia}")


def r5_descuento_premium(mayor: pd.DataFrame, entrada: float, direccion: str, vela_mayor: str) -> dict:
    if mayor.empty:
        return _res(False, None, "sin velas cerradas de la temporalidad mayor")
    swings = smc.swing_highs_lows(mayor, swing_length=SWING_LENGTH_POR_VELA[vela_mayor])
    altos = swings.loc[swings["HighLow"] == 1, "Level"]
    bajos = swings.loc[swings["HighLow"] == -1, "Level"]
    if altos.empty or bajos.empty:
        return _res(False, None, f"datos insuficientes: sin swing alto y bajo en {vela_mayor}")
    alto, bajo = float(altos.iloc[-1]), float(bajos.iloc[-1])
    if alto <= bajo:
        return _res(False, None, f"rango {vela_mayor} inválido (último alto <= último bajo)")
    posicion = (entrada - bajo) / (alto - bajo)
    cumple = posicion < 0.5 if direccion == "long" else posicion > 0.5
    zona = "descuento" if posicion < 0.5 else "premium"
    return _res(bool(cumple), round(posicion, 3), f"entrada en {zona} ({posicion:.0%} del rango {vela_mayor})")


def r6_tp_liquidez(ohlc: pd.DataFrame, swings: pd.DataFrame, conocido: int, entrada: float, stop: float,
                   direccion: str, swing_length: int) -> dict:
    long = direccion == "long"
    lado = swings[swings["HighLow"] == (1 if long else -1)]
    lado = lado[lado.index + swing_length <= conocido]  # swing ya confirmado cuando el setup existe
    extremo = ohlc["high"] if long else ohlc["low"]
    candidatos = []
    for idx, nivel in lado["Level"].items():
        if (long and nivel <= entrada) or (not long and nivel >= entrada):
            continue
        despues = extremo.iloc[idx + 1 : conocido + 1]
        sin_buscar = despues.empty or (despues.max() < nivel if long else despues.min() > nivel)
        if sin_buscar:
            candidatos.append(float(nivel))
    if not candidatos:
        return _res(False, None, "no hay liquidez sin buscar en la dirección del trade")
    tp = min(candidatos) if long else max(candidatos)
    rr = abs(tp - entrada) / abs(entrada - stop)
    return _res(rr >= RR_MINIMO, {"tp": tp, "rr": round(rr, 2)}, f"TP en liquidez a {rr:.2f}R (mínimo {RR_MINIMO}R)")
```

- [ ] **Step 4: Correr la prueba y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.reglas`
Expected: última línea `reglas.demo() OK`

- [ ] **Step 5: Commit**

```bash
git add motor_smc/reglas.py
git commit -m "Motor v2: reglas R4 estructura mayor, R5 descuento/premium, R6 TP en liquidez

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Divergencia RSI/MACD (anotación)

**Files:**
- Modify: `motor_smc/indicadores.py` (agregar la función antes de `def demo()`, y sus asserts dentro de `demo()` antes de su `print` final)

**Interfaces:**
- Consumes: `rsi(ohlc)`, `macd(ohlc)` ya existentes en el módulo.
- Produces: `divergencia(ohlc, oscilador: Series, i: int, p: int, direccion: str) -> bool | None` — `i` = vela actual, `p` = swing previo del mismo tipo.

- [ ] **Step 1: Agregar la prueba dentro de `demo()` de `motor_smc/indicadores.py`, antes de su `print` final**

```python
    # divergencia (§4.3, §4.8): solo anotación
    idx_div = pd.date_range("2026-01-05", periods=6, freq="h", tz="UTC")
    ohlc_div = pd.DataFrame({"open": 10.0, "high": [11, 11, 11, 11, 11, 13], "low": [9, 9, 9, 9, 9, 8.0],
                             "close": 10.0, "volume": 1.0}, index=idx_div)
    osc = pd.Series([float("nan"), 30.0, 50, 50, 50, 40.0], index=idx_div)
    assert divergencia(ohlc_div, osc, 5, 1, "long") is True    # mínimo más bajo, oscilador más alto
    assert divergencia(ohlc_div, osc, 5, 2, "long") is False   # oscilador más bajo: sin divergencia
    assert divergencia(ohlc_div, osc, 5, 2, "short") is True   # máximo más alto, oscilador más bajo
    assert divergencia(ohlc_div, osc, 5, 0, "long") is None    # oscilador sin calcular
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.indicadores`
Expected: FAIL con `NameError: name 'divergencia' is not defined`

- [ ] **Step 3: Implementar — agregar antes de `def demo()`**

```python
def divergencia(ohlc: pd.DataFrame, oscilador: pd.Series, i: int, p: int, direccion: str) -> bool | None:
    """Divergencia (§4.3 RSI, §4.8 MACD): el precio hace un extremo nuevo en `i` respecto al
    swing previo `p` pero el oscilador no lo acompaña. Motor v2: solo anota, nunca filtra
    ni es entrada por sí sola (regla explícita del curso)."""
    a, b = oscilador.iloc[p], oscilador.iloc[i]
    if pd.isna(a) or pd.isna(b):
        return None
    if direccion == "long":
        return bool(ohlc["low"].iloc[i] < ohlc["low"].iloc[p] and b > a)
    return bool(ohlc["high"].iloc[i] > ohlc["high"].iloc[p] and b < a)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.indicadores`
Expected: termina sin `AssertionError` (imprime su mensaje OK habitual)

- [ ] **Step 5: Commit**

```bash
git add motor_smc/indicadores.py
git commit -m "Indicadores: divergencia RSI/MACD como anotacion (uso del curso)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Detector v2 — reversión, continuación, evaluación y embudo

**Files:**
- Create: `motor_smc/setups_v2.py`

**Interfaces:**
- Consumes: todo `motor_smc.reglas` (Tasks 1–2), `divergencia`/`rsi`/`macd` (Task 3), `motor.analizar`, `setup_ob_fvg.VENTANA_FVG_VELAS`, `setup_ob_fvg._hay_order_block_confluente`, `setup_ob_fvg._hay_liquidez_confluente`.
- Produces:
  - `COLUMNAS: list[str]` (orden de columnas del DataFrame de salida).
  - `candidatos_reversion(ohlc, res, swing_length) -> list[dict]`, `candidatos_continuacion(ohlc, res, swing_length) -> list[dict]`. Cada dict trae `tipo, direccion, indice_barrido, indice_confirmacion, indice_conocido, indice_zona, entrada, stop, zona_extremo` (+ `fvg_top, fvg_bottom` en reversión).
  - `evaluar(c, ohlc, ohlc_mayor, vela, vela_mayor, res, swing_length) -> dict` con todas las `COLUMNAS`.
  - `detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor) -> DataFrame` (columnas = `COLUMNAS`, ordenado por `indice_conocido`).
  - `embudo(setups) -> {"reversion"|"continuacion": {"candidatos": int, "descartados_por_regla": {"R1".."R6": int}, "validos": int}}`.

- [ ] **Step 1: Crear el archivo con constantes, fixture de prueba y prueba**

```python
"""Detector SMC v2 — dos tipos de apertura (spec 2026-09-28-motor-smc-v2-design.md).

  reversion:    barrido de liquidez externa + CHoCH + FVG (L20). Reglas R1-R6.
  continuacion: BOS a favor tras un retroceso; entrada en el punto medio de la zona
                de mitigación (2-3 últimas velas contrarias del retroceso, L52). Reglas R3-R6.

Anti-anticipación: `smc.bos_choch` marca el evento en un swing que solo se confirma cuando
existe el swing siguiente + `swing_length` velas, y la ruptura en `BrokenIndex`. El setup
"existe" en `indice_conocido = max(BrokenIndex, siguiente_swing + swing_length)`; toda regla y
el backtest parten de ahí, nunca antes.

El detector viejo (`setup_ob_fvg.detectar_setups`) sigue intacto: lo usan los EA, T-01 y el
modo sombra de la Etapa 2 necesita comparar los dos.
"""

import pandas as pd

from . import reglas as R
from .indicadores import divergencia, macd, rsi
from .motor import analizar
from .setup_ob_fvg import VENTANA_FVG_VELAS, _hay_liquidez_confluente, _hay_order_block_confluente

MAX_VELAS_ZONA = 3
BUSQUEDA_ZONA_VELAS = 3  # la zona debe terminar en el swing del retroceso o hasta 2 velas antes

COLUMNAS = [
    "tipo", "direccion", "indice_barrido", "indice_confirmacion", "indice_conocido", "indice_zona",
    "entrada", "stop", "zona_extremo", "tp", "valido", "razon_descarte", "reglas",
    "ema_sesgo", "cruce_20_50", "multiplo_confirmacion", "divergencia_rsi", "divergencia_macd",
    "order_block_confluente", "liquidez_confluente",
]


# ── pruebas ──────────────────────────────────────────────────────────────────

def _res_prueba(n: int = 40) -> tuple:
    """ohlc + resultado de `analizar` 100% controlado (mismas columnas que smartmoneyconcepts)."""
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    ohlc = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100.0}, index=idx)
    nan = float("nan")
    swings = pd.DataFrame({"HighLow": [nan] * n, "Level": [nan] * n})
    estructura = pd.DataFrame({"BOS": [nan] * n, "CHOCH": [nan] * n, "Level": [nan] * n, "BrokenIndex": [nan] * n})
    fvg = pd.DataFrame({"FVG": [nan] * n, "Top": [nan] * n, "Bottom": [nan] * n})
    order_blocks = pd.DataFrame({"OB": [nan] * n, "Top": [nan] * n, "Bottom": [nan] * n, "MitigatedIndex": [nan] * n})
    liquidez = pd.DataFrame({"Liquidity": [nan] * n, "Swept": [nan] * n})
    return ohlc, {"swings": swings, "estructura": estructura, "fvg": fvg, "order_blocks": order_blocks, "liquidez": liquidez}


def demo() -> None:
    # Reversión: CHoCH alcista en la vela 20, ruptura en 24, siguiente swing en 26 (sl=3 -> conocido 29)
    ohlc, res = _res_prueba()
    ohlc.iloc[20, ohlc.columns.get_loc("low")] = 90.0
    res["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res["swings"].loc[26, ["HighLow", "Level"]] = [1, 110.0]
    res["estructura"].loc[20, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    rev = candidatos_reversion(ohlc, res, swing_length=3)
    assert len(rev) == 1, rev
    assert rev[0]["entrada"] == 104.0 and rev[0]["stop"] == 90.0
    assert rev[0]["zona_extremo"] == 103.0 and rev[0]["indice_conocido"] == 29
    # sin swing siguiente el CHoCH todavía no se conoce -> no hay candidato
    ohlc_b, res_b = _res_prueba()
    res_b["swings"].loc[20, ["HighLow", "Level"]] = [-1, 90.0]
    res_b["estructura"].loc[20, ["CHOCH", "BrokenIndex"]] = [1, 24]
    res_b["fvg"].loc[22, ["FVG", "Top", "Bottom"]] = [1, 105.0, 103.0]
    assert candidatos_reversion(ohlc_b, res_b, swing_length=3) == []

    # Continuación: BOS alcista en el swing 20 (mínimo del retroceso), zona = velas bajistas 18-20
    ohlc_c, res_c = _res_prueba()
    for p, fila in {17: (106, 107, 104, 105), 18: (104, 105, 101, 102), 19: (102, 103, 99, 100), 20: (100, 101, 95, 97)}.items():
        ohlc_c.iloc[p, :4] = fila
    res_c["swings"].loc[20, ["HighLow", "Level"]] = [-1, 95.0]
    res_c["swings"].loc[26, ["HighLow", "Level"]] = [1, 115.0]
    res_c["estructura"].loc[20, ["BOS", "BrokenIndex"]] = [1, 24]
    cont = candidatos_continuacion(ohlc_c, res_c, swing_length=3)
    assert len(cont) == 1, cont
    assert cont[0]["indice_zona"] == 18  # máximo 3 velas: 18, 19, 20 (la 17 queda fuera)
    assert cont[0]["entrada"] == 100.0 and cont[0]["stop"] == 95.0 and cont[0]["zona_extremo"] == 95.0

    # Evaluación: R1/R2 no aplican a continuación; anti-anticipación con la temporalidad mayor
    mayor_pasado = R.zigzag(R.ALCISTA, inicio="2025-12-20 00:00")  # cierra antes del setup
    mayor_futuro = R.zigzag(R.ALCISTA, inicio="2026-01-06 00:00")  # abre después del setup
    ev = evaluar(cont[0], ohlc_c, mayor_pasado, "15m", "1H", res_c, 3)
    assert ev["reglas"]["R1"]["cumple"] is None and ev["reglas"]["R2"]["cumple"] is None
    assert ev["reglas"]["R4"]["cumple"] is True, ev["reglas"]["R4"]
    ev_futuro = evaluar(cont[0], ohlc_c, mayor_futuro, "15m", "1H", res_c, 3)
    assert ev_futuro["reglas"]["R4"]["cumple"] is False
    assert "sin velas cerradas" in ev_futuro["reglas"]["R4"]["razon"]
    assert ev_futuro["valido"] is False and "R4" in ev_futuro["razon_descarte"]
    assert set(ev) == set(COLUMNAS)

    e = embudo(pd.DataFrame([ev, ev_futuro], columns=COLUMNAS))
    assert e["continuacion"]["candidatos"] == 2 and e["continuacion"]["descartados_por_regla"]["R4"] == 1
    assert e["reversion"]["candidatos"] == 0
    print("setups_v2.demo() OK")


if __name__ == "__main__":
    demo()
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.setups_v2`
Expected: FAIL con `NameError: name 'candidatos_reversion' is not defined`

- [ ] **Step 3: Implementar — pegar después de `COLUMNAS = [...]` (antes del bloque de pruebas)**

```python
def _indice_conocido(swings: pd.DataFrame, i: int, k: int, swing_length: int, n: int) -> int | None:
    siguientes = swings.index[(swings.index > i) & swings["HighLow"].notna()]
    if len(siguientes) == 0:
        return None
    conocido = max(k, int(siguientes[0]) + swing_length)
    return conocido if conocido < n else None


def candidatos_reversion(ohlc: pd.DataFrame, res: dict, swing_length: int) -> list[dict]:
    estructura, fvg, swings = res["estructura"], res["fvg"], res["swings"]
    salida = []
    for i in estructura.index[estructura["CHOCH"].notna() & (estructura["CHOCH"] != 0)]:
        d = int(estructura.at[i, "CHOCH"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        conocido = _indice_conocido(swings, i, k, swing_length, len(ohlc))
        if conocido is None:
            continue
        ventana = fvg.iloc[i : min(i + VENTANA_FVG_VELAS, k) + 1]
        js = ventana.index[ventana["FVG"] == d]
        if len(js) == 0:
            continue
        j = int(js[0])
        top, bottom = float(fvg.at[j, "Top"]), float(fvg.at[j, "Bottom"])
        entrada = (top + bottom) / 2
        stop = float(ohlc["low"].iloc[i] if d == 1 else ohlc["high"].iloc[i])
        if (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "reversion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": int(i), "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": j,
            "entrada": entrada, "stop": stop, "zona_extremo": bottom if d == 1 else top,
            "fvg_top": top, "fvg_bottom": bottom,
        })
    return salida


def candidatos_continuacion(ohlc: pd.DataFrame, res: dict, swing_length: int) -> list[dict]:
    estructura, swings = res["estructura"], res["swings"]
    salida = []
    for i in estructura.index[estructura["BOS"].notna() & (estructura["BOS"] != 0)]:
        d = int(estructura.at[i, "BOS"])
        k = estructura.at[i, "BrokenIndex"]
        if pd.isna(k) or k == 0:
            continue
        k = int(k)
        conocido = _indice_conocido(swings, i, k, swing_length, len(ohlc))
        if conocido is None:
            continue
        contraria = (ohlc["close"] < ohlc["open"]) if d == 1 else (ohlc["close"] > ohlc["open"])
        fin = next((p for p in range(i, max(i - BUSQUEDA_ZONA_VELAS, -1), -1) if contraria.iloc[p]), None)
        if fin is None:
            continue
        ini = fin
        while ini - 1 >= 0 and contraria.iloc[ini - 1] and fin - ini + 1 < MAX_VELAS_ZONA:
            ini -= 1
        zona = ohlc.iloc[ini : fin + 1]
        alto, bajo = float(zona["high"].max()), float(zona["low"].min())
        entrada = (alto + bajo) / 2
        # en smc.bos_choch el BOS se marca en el swing del retroceso (HL alcista / LH bajista)
        stop = float(ohlc["low"].iloc[i] if d == 1 else ohlc["high"].iloc[i])
        if (d == 1 and stop >= entrada) or (d == -1 and stop <= entrada):
            continue
        salida.append({
            "tipo": "continuacion", "direccion": "long" if d == 1 else "short",
            "indice_barrido": int(i), "indice_confirmacion": k, "indice_conocido": conocido, "indice_zona": ini,
            "entrada": entrada, "stop": stop, "zona_extremo": bajo if d == 1 else alto,
        })
    return salida


def _multiplo(ohlc: pd.DataFrame, k: int) -> float | None:
    if k < R.VENTANA_VOLUMEN:
        return None
    media = ohlc["volume"].iloc[k - R.VENTANA_VOLUMEN : k].mean()
    return round(float(ohlc["volume"].iloc[k] / media), 2) if media > 0 else None


def _divergencias(ohlc: pd.DataFrame, swings: pd.DataFrame, i: int, direccion: str) -> tuple:
    lado = -1 if direccion == "long" else 1
    previos = swings.index[(swings["HighLow"] == lado) & (swings.index < i)]
    if len(previos) == 0:
        return None, None
    p = int(previos[-1])
    return (divergencia(ohlc, rsi(ohlc), i, p, direccion),
            divergencia(ohlc, macd(ohlc)["MACD"], i, p, direccion))


def evaluar(c: dict, ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str,
            res: dict, swing_length: int) -> dict:
    d, k, conocido = c["direccion"], c["indice_confirmacion"], c["indice_conocido"]
    mayor = R.cortar_mayor(ohlc_mayor, ohlc.index[conocido] + R.DURACION_VELA[vela], vela_mayor)
    es_reversion = c["tipo"] == "reversion"
    reglas = {
        "R1": R.r1_volumen(ohlc, c["indice_barrido"], vela) if es_reversion else R.NO_APLICA,
        "R2": R.r2_fvg(ohlc, c["fvg_top"], c["fvg_bottom"], c["indice_zona"]) if es_reversion else R.NO_APLICA,
        "R3": R.r3_ema(ohlc, k, d),
        "R4": R.r4_estructura(mayor, d, vela_mayor),
        "R5": R.r5_descuento_premium(mayor, c["entrada"], d, vela_mayor),
        "R6": R.r6_tp_liquidez(ohlc, res["swings"], conocido, c["entrada"], c["stop"], d, swing_length),
    }
    fallas = [f"{nombre}: {r['razon']}" for nombre, r in reglas.items() if r["cumple"] is False]
    div_rsi, div_macd = _divergencias(ohlc, res["swings"], c["indice_barrido"], d) if es_reversion else (None, None)
    direccion_num = 1 if d == "long" else -1
    return {
        **{col: c[col] for col in COLUMNAS if col in c},
        "tp": reglas["R6"]["dato"]["tp"] if isinstance(reglas["R6"]["dato"], dict) else None,
        "valido": not fallas,
        "razon_descarte": "; ".join(fallas),
        "reglas": reglas,
        "ema_sesgo": reglas["R3"]["dato"],
        "cruce_20_50": R.cruce_20_50(ohlc, k, d),
        "multiplo_confirmacion": _multiplo(ohlc, k),
        "divergencia_rsi": div_rsi,
        "divergencia_macd": div_macd,
        "order_block_confluente": (_hay_order_block_confluente(res["order_blocks"], direccion_num, c["fvg_top"], c["fvg_bottom"], k)
                                   if es_reversion else None),
        "liquidez_confluente": _hay_liquidez_confluente(res["liquidez"], direccion_num, c["indice_barrido"]) if es_reversion else None,
    }


def detectar_setups_v2(ohlc: pd.DataFrame, ohlc_mayor: pd.DataFrame, vela: str, vela_mayor: str) -> pd.DataFrame:
    swing_length = R.SWING_LENGTH_POR_VELA[vela]
    res = analizar(ohlc, swing_length=swing_length)
    candidatos = candidatos_reversion(ohlc, res, swing_length) + candidatos_continuacion(ohlc, res, swing_length)
    filas = [evaluar(c, ohlc, ohlc_mayor, vela, vela_mayor, res, swing_length) for c in candidatos]
    return pd.DataFrame(filas, columns=COLUMNAS).sort_values("indice_conocido", ignore_index=True)


def embudo(setups: pd.DataFrame) -> dict:
    """Cuántos candidatos hay por tipo, cuántos elimina cada regla y cuántos quedan válidos."""
    salida = {}
    for tipo in ("reversion", "continuacion"):
        sub = setups[setups["tipo"] == tipo]
        salida[tipo] = {
            "candidatos": int(len(sub)),
            "descartados_por_regla": {
                regla: int(sum(1 for r in sub["reglas"] if r[regla]["cumple"] is False))
                for regla in ("R1", "R2", "R3", "R4", "R5", "R6")
            },
            "validos": int(sub["valido"].sum()) if len(sub) else 0,
        }
    return salida
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.setups_v2`
Expected: última línea `setups_v2.demo() OK`

- [ ] **Step 5: Verificar que los demos anteriores siguen pasando (no se tocó el detector viejo)**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.setup_ob_fvg && PYTHONIOENCODING=utf-8 .venv/Scripts/python -m motor_smc.reglas`
Expected: `setup_ob_fvg.demo() OK ...` y `reglas.demo() OK`

- [ ] **Step 6: Commit**

```bash
git add motor_smc/setups_v2.py
git commit -m "Motor v2: detector de reversion y continuacion con evaluacion R1-R6 y embudo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Backtest v2 — entrada límite, invalidación por cierre, TP en liquidez

**Files:**
- Modify: `backtesting/backtest.py`

**Interfaces:**
- Consumes: DataFrame de `detectar_setups_v2` (Task 4): columnas `tipo, direccion, indice_conocido, entrada, stop, tp, zona_extremo, valido`.
- Produces: `RESULTADOS_RESUELTOS = ("gano", "perdio", "invalidado")`; `simular_v2(ohlc, setups) -> DataFrame` (solo válidos, + `resultado, r, velas, riesgo_pct`); `backtest_v2(ohlc, setups) -> {"reversion"|"continuacion": {"compra"|"venta": <dict de reporte()>}}`. `reporte()` ahora solo cuenta como resueltos los de `RESULTADOS_RESUELTOS` (para los resultados viejos `gano/perdio/sin_resolver` el comportamiento no cambia).

- [ ] **Step 1: Agregar las pruebas al final de `demo()` en `backtesting/backtest.py`**

`demo()` actual descarga Dukascopy; las pruebas nuevas son sintéticas y van **al inicio** de `demo()` (antes de `ohlc = obtener_velas(...)`) para que fallen rápido:

```python
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
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m backtesting.backtest`
Expected: FAIL con `NameError: name '_simular_v2_uno' is not defined`

- [ ] **Step 3: Implementar**

En `reporte()`, reemplazar la línea:

```python
    resueltos = resultados[resultados["resultado"] != "sin_resolver"]
```

por:

```python
    resueltos = resultados[resultados["resultado"].isin(RESULTADOS_RESUELTOS)]
```

Agregar debajo de `RETORNO_RIESGO_TP = 2.0`:

```python
# v2: "sin_llenar" (el precio nunca volvió a la entrada) y "sin_resolver" no cuentan.
RESULTADOS_RESUELTOS = ("gano", "perdio", "invalidado")
```

Agregar antes de `def demo()`:

```python
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
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m backtesting.backtest`
Expected: imprime `backtest v2 OK` y después `backtesting.backtest.demo() OK — XAUUSD H1 2020-2024...` (la parte vieja descarga Dukascopy; tarda ~1 min)

- [ ] **Step 5: Commit**

```bash
git add backtesting/backtest.py
git commit -m "Backtest v2: entrada limite, invalidacion por cierre, TP en liquidez, reporte por tipo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `/api/setups` — bloque `motor_v2` en modo sombra

**Files:**
- Modify: `api/setups.py`

**Interfaces:**
- Consumes: `detectar_setups_v2`, `embudo` (Task 4), `backtest_v2` (Task 5), `obtener_velas` (existente).
- Produces: en la respuesta con `temporalidad`, la llave nueva `motor_v2`:
  `{"estado": "ok"|"sin_datos_temporalidad_mayor"|"error", "vela": str, "vela_mayor": str, "setups": [..], "setups_validos": [..], "embudo": {...}, "backtests": {...}}` (en `error`: `{"estado": "error", "error": str}`). Nada de lo existente cambia.

- [ ] **Step 1: Agregar la prueba en `demo()` de `api/setups.py`**

Después de la línea `print(f"api.setups.demo() OK — XAUUSD Swing (H): ...")` agregar:

```python
    import json
    v2 = body_t["motor_v2"]
    assert v2["estado"] in ("ok", "sin_datos_temporalidad_mayor"), v2
    assert (v2["vela"], v2["vela_mayor"]) == ("4H", "D")
    json.dumps(v2, allow_nan=False)  # el snapshot va a jsonb: sin NaN ni tipos numpy
    for s in v2["setups"]:
        assert s["valido"] or s["razon_descarte"], s  # todo descarte trae su razón
    assert all(s["valido"] for s in v2["setups_validos"])
    print(f"api.setups.demo() OK — motor_v2 {v2['vela']}->{v2['vela_mayor']}: embudo {v2.get('embudo')}")
```

Y en la parte que prueba sin temporalidad, después de `assert "setups_confirmados" not in body`:

```python
    assert "motor_v2" not in body  # sin temporalidad no hay motor v2
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m api.setups`
Expected: FAIL con `KeyError: 'motor_v2'` (necesita red: descarga Dukascopy, ~1–2 min). Si Postgres local no está configurado, `_persistencia` queda en `None` y corre en fresco — es lo esperado.

- [ ] **Step 3: Implementar**

Reemplazar los imports:

```python
from backtesting.backtest import backtest_direccion
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia
```

por:

```python
import dukascopy_python as dp

from backtesting.backtest import backtest_direccion, backtest_v2
from conectividad import SIMBOLOS, TEMPORALIDAD_A_INTERVALO, obtener_velas
from motor_smc import analizar, detectar_setups, obtener_tendencia
from motor_smc.setups_v2 import detectar_setups_v2, embudo
```

Agregar debajo de `DIRECCION_LONG_SHORT_A_COMPRA_VENTA = {...}`:

```python
# Motor v2, Etapa 1 (modo sombra): mapa PROVISIONAL perfil -> (vela de entrada, temporalidad mayor).
# La Etapa 2 lo reemplaza por el mapa definitivo en conectividad/historico.py
# (spec docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md).
PERFIL_A_VELAS_V2 = {
    "Scalping": ("15m", "1H"),
    "Intraday": ("1H", "D"),
    "Swing (H)": ("4H", "D"),
    "Swing": ("4H", "D"),
    "Swing (S)": ("S", "M"),
    "Swing (M)": ("M", "M"),
}
VELA_A_INTERVALO = {
    "15m": dp.INTERVAL_MIN_15, "30m": dp.INTERVAL_MIN_30, "1H": dp.INTERVAL_HOUR_1, "4H": dp.INTERVAL_HOUR_4,
    "D": dp.INTERVAL_DAY_1, "S": dp.INTERVAL_WEEK_1, "M": dp.INTERVAL_MONTH_1,
}


def motor_v2(simbolo: str, temporalidad: str, ohlc, inicio, fin) -> dict:
    """Modo sombra: calcula el motor v2 junto al actual. Nunca toca `setups`/`setups_confirmados`
    (lo que reciben los EA) y nunca propaga una excepción al llamador."""
    try:
        vela, vela_mayor = PERFIL_A_VELAS_V2[temporalidad]
        if vela_mayor == vela:
            ohlc_mayor = ohlc
        else:
            try:
                ohlc_mayor = obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela_mayor])
            except Exception:
                ohlc_mayor = None
            if ohlc_mayor is None or ohlc_mayor.empty:
                return {"estado": "sin_datos_temporalidad_mayor", "vela": vela, "vela_mayor": vela_mayor,
                        "setups": [], "setups_validos": []}
        setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor)
        # jsonb no acepta NaN: celdas vacías -> None
        registros = setups.astype(object).where(setups.notna(), None).to_dict(orient="records")
        return {
            "estado": "ok", "vela": vela, "vela_mayor": vela_mayor,
            "setups": registros,
            "setups_validos": [s for s in registros if s["valido"]],
            "embudo": embudo(setups),
            "backtests": backtest_v2(ohlc, setups),
        }
    except Exception as e:
        return {"estado": "error", "error": f"{type(e).__name__}: {e}"}
```

En `procesar()`, justo después del bloque que construye `respuesta = {"simbolo": simbolo, "temporalidad": temporalidad, ... "setups_confirmados": confirmados,}` y **antes** de `if _persistencia is not None:`, agregar:

```python
    respuesta["motor_v2"] = motor_v2(simbolo, temporalidad, ohlc, inicio, fin)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m api.setups`
Expected: las dos líneas viejas `api.setups.demo() OK — ...` y la nueva `api.setups.demo() OK — motor_v2 4H->D: embudo {...}`

- [ ] **Step 5: Commit**

```bash
git add api/setups.py
git commit -m "API setups: motor_v2 en modo sombra (no cambia lo que reciben los EA)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Corrida real — tabla de embudo por símbolo × perfil (checkpoint con Ricardo)

**Files:**
- Create: `backtesting/embudo_v2.py`

**Interfaces:**
- Consumes: `api.setups.PERFIL_A_VELAS_V2`, `api.setups.VELA_A_INTERVALO`, `api.setups.DIAS_POR_TEMPORALIDAD`, `detectar_setups_v2`, `embudo`, `backtest_v2`, `obtener_velas`.
- Produces: script ejecutable que imprime la tabla del embudo; sin funciones que otros consuman.

- [ ] **Step 1: Crear el script**

```python
"""Corrida real del motor v2 contra Dukascopy — spec 2026-09-28, Pruebas punto 4.

Imprime, por símbolo × perfil, cuántos candidatos hay de cada tipo, cuántos elimina cada
regla y cuántos quedan válidos, más el backtest de los válidos. No exige un número de
setups: sirve para ver si alguna regla deja un perfil en cero ANTES de cerrar la Etapa 1.

Uso: PYTHONIOENCODING=utf-8 .venv/Scripts/python -m backtesting.embudo_v2 [SIMBOLO ...]
"""

import sys
from datetime import datetime, timedelta, timezone

from api.setups import DIAS_POR_TEMPORALIDAD, PERFIL_A_VELAS_V2, VELA_A_INTERVALO
from backtesting.backtest import backtest_v2
from conectividad import obtener_velas
from motor_smc.setups_v2 import detectar_setups_v2, embudo

PERFILES = ("Scalping", "Intraday", "Swing (H)", "Swing (S)", "Swing (M)")


def correr(simbolo: str, perfil: str) -> dict:
    vela, vela_mayor = PERFIL_A_VELAS_V2[perfil]
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=DIAS_POR_TEMPORALIDAD[perfil])
    ohlc = obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela])
    ohlc_mayor = ohlc if vela_mayor == vela else obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela_mayor])
    setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor)
    invalidos_sin_razon = setups[~setups["valido"].astype(bool) & (setups["razon_descarte"] == "")]
    assert invalidos_sin_razon.empty, f"{simbolo} {perfil}: descartes sin razón"
    return {"velas": len(ohlc), "embudo": embudo(setups), "backtests": backtest_v2(ohlc, setups)}


def main(simbolos: list[str]) -> None:
    print(f"{'símbolo':8} {'perfil':10} {'tipo':13} {'cand':>5} {'R1':>4} {'R2':>4} {'R3':>4} {'R4':>4} {'R5':>4} {'R6':>4} {'válidos':>8} {'n bt':>5} {'exp R':>6}")
    for simbolo in simbolos:
        for perfil in PERFILES:
            r = correr(simbolo, perfil)
            for tipo, e in r["embudo"].items():
                d = e["descartados_por_regla"]
                bts = [r["backtests"][tipo][cv] for cv in ("compra", "venta")]
                n_bt = sum(b["n_setups"] for b in bts)
                exp = [b["expectativa_r"] for b in bts if b.get("expectativa_r") is not None]
                exp_txt = f"{sum(exp) / len(exp):.2f}" if exp else "-"
                print(f"{simbolo:8} {perfil:10} {tipo:13} {e['candidatos']:>5} {d['R1']:>4} {d['R2']:>4} {d['R3']:>4} "
                      f"{d['R4']:>4} {d['R5']:>4} {d['R6']:>4} {e['validos']:>8} {n_bt:>5} {exp_txt:>6}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["XAUUSD", "EURUSD"])
```

- [ ] **Step 2: Correr la tabla**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m backtesting.embudo_v2 XAUUSD EURUSD 2>&1 | grep -v "INFO:DUKASCRIPT\|Thank you"`
Expected: 20 filas (2 símbolos × 5 perfiles × 2 tipos), sin `AssertionError`. Referencia de la corrida de prueba del 28 sep (prototipo): XAUUSD 1H → reversión 7 candidatos / 0 válidos (R1 y R2 eliminan casi todos), continuación 60 candidatos / 4 válidos; EURUSD 15m → continuación 47 / 6 válidos.

- [ ] **Step 3: Commit**

```bash
git add backtesting/embudo_v2.py
git commit -m "Embudo v2: corrida real por simbolo x perfil con conteo por regla

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: CHECKPOINT — revisar la tabla con Ricardo antes de seguir**

Mostrar la tabla completa a Ricardo. Si una regla deja un perfil completo en 0 válidos (en la prueba del 28 sep, R1 volumen + R2 FVG eliminaron todas las reversiones), **no ajustar umbrales por cuenta propia**: presentar el dato y que Ricardo decida (spec §Pruebas punto 4). No pasar a la Task 8 sin su visto bueno.

---

### Task 8: Despliegue en modo sombra y cierre de la Etapa 1

**Files:**
- Modify: `C:\Users\tezca\.claude\projects\C--Proyectos-MexTradeBot\memory\project_motor_smc_v2.md`

- [ ] **Step 1: Confirmar con Ricardo el despliegue** (push a `main` + trigger de EasyPanel `mtb-api` es la forma de desplegar; ver memoria `project-infra-vps-easypanel`). Solo con su "sí" explícito.

- [ ] **Step 2: Push y despliegue**

```bash
git push origin main
```

Después disparar el trigger de EasyPanel de `mtb-api` como en despliegues anteriores (ver memoria de infra).

- [ ] **Step 3: Verificar en producción**

Run: `curl -s "https://mextradebot.com.mx/api/setups?simbolo=XAUUSD&temporalidad=Intraday&_force_refresh=1" | .venv/Scripts/python -c "import json,sys; b=json.load(sys.stdin); print(b['motor_v2']['estado'], b['motor_v2'].get('embudo')); print(len(b['setups_confirmados']), 'confirmados motor viejo')"`
Expected: `ok {...}` y el conteo de confirmados del motor viejo igual que antes del despliegue (modo sombra: no cambia).

Revisar también la carga de CPU del host EasyPanel durante el siguiente ciclo del refresco: el motor v2 agrega una descarga de la temporalidad mayor y ~5–9 s de cálculo por combinación. Si la carga sube de forma sostenida (la de referencia es ~0.2), avisar a Ricardo antes de seguir.

- [ ] **Step 4: Actualizar la memoria**

En `project_motor_smc_v2.md` agregar una línea: fecha, commit desplegado, "Etapa 1 en modo sombra en producción", resultado de la tabla de embudo y lo que decidió Ricardo en el checkpoint. Siguiente paso: plan de la Etapa 2 (migración de temporalidades + comparación de la semana de sombra).

---

## Notas para quien ejecute

- **Desviación consciente del spec:** el spec dice que `setups`/`setups_confirmados` llevarían los campos nuevos. Para cumplir el modo sombra (los EA no deben notar nada hasta que Ricardo decida), el motor v2 va en la llave separada `motor_v2`. El cambio de llave ocurre en la Etapa 2.
- `api/mejor_indicador.py` y `backtesting/comparar_indicadores.py` siguen usando el detector viejo y los 4 indicadores intercambiables. Se retiran o migran en la Etapa 2 (spec: MACD/RSI dejan de ser filtros).
- La regla de "solo compras en Swing (S)/(M)" se sigue aplicando donde vive hoy (T-04 en n8n); el motor reporta la dirección real (mismo criterio que `motor_smc/tendencia.py`).
