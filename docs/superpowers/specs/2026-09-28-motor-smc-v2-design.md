# Motor SMC v2 — reglas completas de la metodología + nuevo mapa de temporalidades

**Fecha:** 2026-09-28 · **Estado:** diseño aprobado por Ricardo (brainstorming 28 sep 2026)
**Fuente de las reglas:** `../docs/metodologia-trading.md` (57 lecciones + MACD/Elliott externos). Cada regla cita su lección.

## Por qué

El motor actual solo implementa un eslabón del proceso del curso (barrido + CHoCH + FVG). Le faltan el contexto de mayor a menor, el volumen, descuento/premium, el tamaño mínimo del FVG y el TP en liquidez. Alargar el historial del backtest sin corregir esto solo junta más muestra de un setup que no es el del curso (decisión de Ricardo: primero el detector, después el historial).

Orden de trabajo: **Etapa 1 motor → Etapa 2 migración de temporalidades → (spec aparte) historial largo por temporalidad.**

## Reglas del setup

Hay **dos tipos de apertura** (L32: "la liquidez interna se necesita para continuar la tendencia, y la liquidez externa se necesita para cambiar la tendencia"). Cada setup lleva `tipo: "reversion" | "continuacion"` y el backtest los mide por separado.

| Tipo | Candidato | Reglas que debe cumplir |
|---|---|---|
| **Reversión** | barrido de liquidez externa + CHoCH + FVG (setup insignia, L20, §7.4) | R1, R2, R3, R4, R5, R6 |
| **Continuación** | BOS a favor de la tendencia tras un retroceso (liquidez interna) — zona de mitigación, L52/§3.6 | R3, R4, R5, R6 (R1 y R2 **no aplican**) |

Un candidato es **válido** si cumple todas las reglas de su tipo; si no, se conserva con la razón de descarte. Las reglas que no aplican a un tipo se registran como `no_aplica`.

**Continuación — definición:** tras un BOS a favor de la tendencia de la vela de entrada, la *zona de mitigación* son las 2–3 últimas velas de color contrario del retroceso previo al impulso que hizo el BOS. Entrada = punto medio de esa zona (del máximo al mínimo de esas velas); stop = mínimo (compra) / máximo (venta) del retroceso completo. Si el retroceso tiene 1 sola vela contraria, la zona es esa vela.

| # | Regla | Cómo se evalúa | Fuente |
|---|---|---|---|
| R1 | Volumen del barrido | `volumen(vela del barrido) / media(volumen de las 20 velas anteriores) ≥ umbral`. Umbrales por vela: 15m 3x, 30m 3x, 1H 2.5x, 4H 2x, D 1x, S y M sin filtro. Iguales para todo tipo de activo. El múltiplo de la vela de confirmación solo se anota. | L8, L17, L45 |
| R2 | FVG real | alto del FVG ≥ 0.5 × ATR(14) de la vela de entrada, calculado con velas previas | L20 (umbral nuestro: el curso no da número) |
| R3 | Sesgo EMA | compra solo si la vela de confirmación cierra sobre la EMA; venta solo si cierra debajo. EMA 200; si no hay 200 velas previas → EMA 50 → EMA 20 → si no hay 20: `no aplica: historia insuficiente` (no bloquea). Se registra `ema_sesgo: 200 / 50 / 20 / null`. Cruce EMA 20/50 anotado (`a_favor` / `en_contra`), no filtra. | L48, L51 |
| R4 | Estructura mayor | `obtener_tendencia` sobre la temporalidad mayor apunta en la dirección del setup | L43, L47 (§1.11) |
| R5 | Descuento/premium | rango = último swing alto y swing bajo de la temporalidad mayor; la entrada (CE) de una compra debe quedar bajo el 50%, la de una venta sobre el 50% | L57 (§2.9) |
| R6 | TP en liquidez | TP = siguiente swing sin buscar en la dirección del trade (vela de entrada). Si `(TP − entrada) / riesgo < 2` → descartado | L6, §10.5 |

Reglas de ejecución, no de filtro:
- **Entrada** (orden límite): reversión = Consequent Encroachment (50% del FVG; el 0 y el 1 se anotan, L11, L21); continuación = punto medio de la zona de mitigación (L52).
- **Invalidación**: si una vela **cierra** más allá del extremo lejano de la zona (FVG o zona de mitigación) antes de tocar la entrada, el setup se cancela; una mecha no lo cancela. (§1.3)
- **Stop**: reversión = extremo de la vela del barrido (sin cambios); continuación = extremo del retroceso.
- **Solo compras** en Swing (S) y Swing (M) (`TIPOS_SOLO_ALCISTA`); todo lo demás permite compras y ventas.

Indicadores: MACD y RSI dejan de ser filtros intercambiables y se corrigen a su uso del curso (divergencia contra el precio en el barrido, §4.3/§4.8); quedan como anotación. Fibonacci queda cubierto por R5 + CE. Order Block sigue como anotación (se medirá en el backtest).

**Anti-anticipación (obligatorio):** toda regla que use la temporalidad mayor (R4, R5) solo ve velas mayores **cerradas antes** de la vela de confirmación. EMA, ATR y la media de volumen solo usan velas previas.

## Mapa de temporalidades (Etapa 2)

| Perfil | Velas de entrada → temporalidad mayor | Dirección |
|---|---|---|
| Scalping | 15m → 1H, 30m → 1H | compras y ventas |
| Intraday | 1H → D, 4H → D, D → S | compras y ventas |
| Swing (S) | S → M | solo compras |
| Swing (M) | M → M | solo compras |

- Cada vela de entrada de un perfil se analiza por separado; cada setup indica su vela.
- Aliases heredados: `"Swing (H)"` y `"Swing"` → Intraday vela 4H, **con** compras y ventas (los robots 4H ya entregados siguen en el mismo gráfico).
- Catálogo: 36 símbolos × 7 velas = 252 combinaciones (antes 180).
- S y M se siguen armando desde D1.

## Etapa 1 — motor

```
motor_smc/reglas.py (nuevo)   una función pura por regla R1..R6; umbrales en dicts por VELA
                              (15m,30m,1H,4H,D,S,M), no por nombre de perfil
motor_smc/setup_ob_fvg.py     detectar_setups(ohlc_entrada, ohlc_mayor, vela) → candidatos de reversión (barrido+CHoCH+FVG) y de continuación (BOS+zona de mitigación)
                              con `tipo`, `reglas: {R1: {cumple|no_aplica, dato, razon}, ...}` y `valido`
motor_smc/indicadores.py      RSI/MACD a divergencia; solo anotan
backtesting/backtest.py       entrada límite en CE (no se llena si no toca), invalidación por
                              cierre, TP en liquidez; solo simula setups válidos
api/setups.py                 descarga también las velas de la temporalidad mayor y pasa ambas
```

Contrato de respuesta compatible: `setups` y `setups_confirmados` siguen existiendo, con campos nuevos agregados. En la Etapa 1 se usan los perfiles actuales con este mapeo provisional de temporalidad mayor: Scalping 15m→1H, Intraday 1H→D, Swing (H) 4H→D, Swing (S) S→M, Swing (M) M→M.

Errores:
- Falla la descarga de la temporalidad mayor → `estado: "sin_datos_temporalidad_mayor"`; nada se confirma.
- Una regla sin datos suficientes (ej. ATR al inicio de la serie) → `cumple: false`, razón "datos insuficientes" (excepto R3, que usa la cadena 200→50→20→no aplica).
- Postgres caído → cálculo en fresco (igual que hoy).

## Etapa 2 — migración

1. Mapa único perfil → velas → temporalidad mayor en `conectividad/historico.py` (reemplaza `TEMPORALIDAD_A_INTERVALO`, con aliases).
2. Snapshots y catálogo por (símbolo, perfil, vela); refresco por vela.
3. `/api/setups?simbolo=&temporalidad=&vela=`; sin `vela` devuelve todas las velas del perfil.
4. EA `MexTradeBot_SeguidorSMC.mq5` manda la vela de su gráfico.
5. n8n (T-01, MTB Análisis Diario, T-04), hoja Resumen_Temporalidades, paneles HTML, `coordinador`, `conectividad/riesgo.py`: nuevos nombres de perfil.

**Salida a producción en modo sombra (1 semana):** el servidor calcula los dos motores; los EA siguen recibiendo el actual; el v2 se guarda en paralelo con sus razones de descarte. Tras la semana se comparan el número de señales y su resultado real, y Ricardo decide el cambio.

## Pruebas

`demo()` con `assert` en cada módulo (estilo del repo):
1. `reglas.py`: por regla, un caso sintético que cumple y uno que no; cadena EMA 200→50→20→no aplica; R1/R2 marcan `no_aplica` en continuación.
2. Anti-anticipación: una vela mayor posterior a la confirmación que contradice la señal debe ignorarse.
3. Backtest: sin llenado si el precio no toca el CE; invalidación por cierre cancela; TP < 2R se descarta.
4. Real contra Dukascopy (XAUUSD, EURUSD, cada perfil): todo descarte trae razón, más una **tabla de embudo** (cuántos candidatos elimina cada regla). Si alguna regla deja un perfil en cero, se revisa con Ricardo antes de cerrar la Etapa 1.

## Fuera de alcance (fase siguiente)

Stop detrás de clúster con volumen o del OB completo (§10.3); timing de calendario (§6.4–6.5); Volume Profile/POC/VWAP (L31, L51); parciales 75/25 (L27); historial largo por temporalidad (spec propio, siguiente paso).
