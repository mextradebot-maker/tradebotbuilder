# Master Trader — Versión 2: del "despachador rápido" al **asignador de capital con disciplina y aprendizaje**

**Fecha:** 2026-10-25 · **Estado:** PROPUESTA para revisión, nada construido · **Reemplaza en ambición a:** `...-design-v1.md` (que se conserva tal cual)
**Skills usadas en esta reflexión:** mtb-trader, mtb-analyst, mtb-mt5-expert, statistical-analyst, financial-analyst, senior-architect, ux-copy, brainstorming. **Fuentes internas:** las 57 lecciones (`docs/metodologia-trading.md`, módulos 1, 2, 6, 7, 10, 11, 12), `docs/backtest-largo-revision-2026-10-20.md`, `docs/checkpoint-2026-10-21.md`, spec del motor SMC v2, `docs/estrategias-por-probar.md`, EA v1.19, `conectividad/riesgo.py`.
**Marcas:** *(verificado)* = lo comprobé en código/documentos; *(propuesta)* = mi diseño; *(estimación)* = a medir.

---

## 0. Autocrítica de la Versión 1 — por qué no bastaba

La V1 hizo lo que pediste: detectar → buscar bots → emparejar temporalidad → ordenar abrir → dejar la salida lista. Es una **tubería rápida**. Pero al cruzarla con lo que ya sabemos del propio sistema, tiene cuatro problemas de fondo:

1. **Despacha rápido algo cuya ventaja hoy es ≈ 0.** *(verificado)* El backtest largo de 204 combinaciones da una expectativa agregada de **+0.025 R** en 15 m, **−0.036 R** en 1H y **−0.038 R** en 4H; las ventas en 1H/4H pierden **−0.27 R / −0.34 R** con ~140 setups cada una; las "17 combinaciones recomendables" son compatibles con el azar (comparaciones múltiples). Una tubería veloz que manda **cada** oportunidad R1–R6 a **cada** bot al 2 % es una máquina para ejecutar una moneda al aire con costos.
2. **Ningún costo está modelado.** *(verificado)* `backtesting/backtest.py` declara que comisiones y slippage están fuera. En 15 m, con stops cortos, el spread puede valer más que la ventaja: *(ilustrativo, a medir)* EURUSD 15 m con stop de ~12 pips y spread de 0.8 pips cuesta ≈ 0.07 R por operación, más que el +0.025 R agregado.
3. **Mi regla de cierre "tesis invalidada" era casi redundante.** En scalping/intraday el stop **ya es** la invalidación (extremo del barrido); cerrar antes solo corta ganadoras que retestean. La única salida estructural distinta (CHoCH inverso) ya existe en Swing.
4. **La latencia no es el cuello de botella.** Las entradas son **órdenes límite**; el precio de entrada no cambia con 3 s más o menos. Lo que sí importa es *qué* oportunidades merecen capital, *cuánto* y *cuántas a la vez*.

**Conclusión de la reflexión:** el valor del Master Trader no está en **ser más rápido**, sino en **elegir, dimensionar, limitar y aprender** — exactamente lo que ningún bot individual puede hacer, y lo que un trader profesional hace antes de apretar el botón. Esta V2 reorienta el diseño a eso, sin perder lo que pediste (receptor, registro de bots, despacho, plan de salida, aceptación por Telegram).

## 1. Tesis

> El Master Trader es el **gestor del presupuesto de riesgo y de la evidencia**: recibe todas las oportunidades, estima con honestidad cuánto vale cada una (con incertidumbre y costos), reparte un presupuesto de riesgo limitado entre las mejores, protege la cuenta con las capas de límite del curso, ordena a los bots por el canal más corto posible y **aprende de cada resultado** — incluso de los que no operó.

Cuatro funciones, en este orden de importancia:
**① Evidencia** (¿esto tiene ventaja real?) → **② Selección y presupuesto** (¿merece capital ahora, junto con lo que ya hay?) → **③ Ejecución** (despacho, salida lista) → **④ Aprendizaje** (cada resultado actualiza la evidencia).

## 2. Lo que dicen las lecciones y cómo se vuelve regla del Master

El curso (57 lecciones) es, en el fondo, un manual de **supervivencia con ventaja estadística**: *"el objetivo de la gestión de riesgo no es ganar más, es mantenerte en el juego"* (L56). El Master es el lugar natural para hacerlo cumplir **de forma automática**, que es justo lo que necesita el alumno que no es experto:

| Lección / módulo | Idea del curso | Cómo la aplica el Master *(propuesta)* |
|---|---|---|
| L47, L36, L54 (§10.1) | Riesgo por operación acotado (2 % en nuestro plan; 0.25–1 % en L54) | **Tope duro 2 %** (ya existe); el Master solo puede **reducir** (multiplicador ≤ 1), nunca subir |
| L54 (§10.8) | **Tres capas**: por operación, por día (2–3 perdedoras), por semana (si ya se alcanzó el objetivo, no seguir por inercia) | Límites por **cuenta** de día y semana (§6) |
| L54 (§10.7) | **Protocolo de rachas**: tras 3 pérdidas bajar tamaño (−50 % a −75 %), no cambiar el sistema, limitar operaciones | Tras 3 pérdidas seguidas el multiplicador baja a **×0.5** automáticamente |
| L56 (§10.6) | Drawdown: definirlo antes, **reducir riesgo al acercarse al límite**, pausar al llegar | Escalera de riesgo por drawdown de la cuenta (§6) |
| L54 (§10.10) | Volatilidad que es **riesgo puro** (spreads anormales, velas desproporcionadas, rupturas falsas repetidas) → no operar | Filtro de **spread anómalo** y de **volatilidad atípica** medibles (§5) |
| L46, L29 | 1–5 operaciones al día; "operar poco pero bien" | Presupuesto diario de operaciones; el Master **prioriza** en lugar de aceptar todo |
| L11, L43, §10.5 | RR mínimo 1:2, el ratio se evalúa **junto con la probabilidad**, no aislado | La calidad de una oportunidad es **expectativa con intervalo**, no solo R:R |
| §1.11 (L3…L47) | **Top-down:** el nivel superior manda sobre el inferior | Ya es R4 en el motor; el Master lo **audita** (alinea con la temporalidad mayor) |
| §6.4–6.7 | Calendario/sesión: evitar aperturas de semana/mes, fines de semana de baja liquidez; lunes rango, martes ejecución (L47/48); volatilidad por sesión (L12) | **Variables a medir**, no filtros impuestos: se prueban contra nuestros datos antes de usarse (§8) |
| §10.4 (L27, L32, L43) | Parciales 75/25, 70/30, escalonado | Entran al **simulador de gestión** para decidir con números (V4/V5 de `estrategias-por-probar`) |
| §10.3 (L44, L57) | Stop detrás del OB completo / zona con volumen; "cadena de demanda" | Ya hay trailing por estructura; el simulador mide su efecto real |
| §11 (L54–L56) | "Riesgo invisible": operar por emoción, aburrimiento, venganza | El Master **retira la decisión emocional del alumno** y le explica el porqué |
| §10.12, Experiencia | "El trader no predice: **reacciona**; espera confirmación" | El Master no anticipa: actúa solo sobre setups **confirmados** y cierres de vela |

Esto convierte al Master en **la versión automática y fiel del curso**: un argumento de producto fuerte (el alumno no solo recibe un robot; recibe la disciplina del curso aplicada).

## 3. Arquitectura V2 (siete etapas)

```
              ┌──────────────── APRENDIZAJE (§7): libro de sombra + actualización bayesiana ◄──────────────┐
              ▼                                                                                            │
1 DETECTAR ─► 2 PUNTUAR ─► 3 SELECCIONAR ─► 4 DIMENSIONAR ─► 5 DESPACHAR ─► 6 GESTIONAR ─► 7 REGISTRAR ────┘
 (Receptor)   expectativa    por presupuesto   multiplicador   canal 5 s +     plan de salida +   bitácora + 
 oportunidad  con intervalo  de riesgo y por   ≤1 × 2 % →      /auth lotes     reglas de cierre   resultados
 R1–R6        y costos       correlación       lotes (riesgo.py)  → límite     del Master          (incl. omitidas)
```

- **1 Detectar (A):** como en la V1: evento justo tras cada snapshot, mismo código que `/api/setups`.
- **2 Puntuar (nuevo):** cada oportunidad recibe una **expectativa estimada en R con intervalo** (§4), descontando costos (§5).
- **3 Seleccionar (nuevo):** con presupuesto de riesgo por cuenta y clústeres de correlación (§6) elige cuáles se operan.
- **4 Dimensionar (nuevo):** multiplicador de riesgo ∈ {0, 0.25, 0.5, 1} aplicado al 2 % y a las capas de racha/drawdown. Los lotes **los sigue calculando `conectividad/riesgo.py`**.
- **5 Despachar (D):** canal de pulso (§9) y `/auth` justo antes de colocar.
- **6 Gestionar (E+F):** plan de salida y reglas de cierre del Master (§8).
- **7 Registrar y aprender:** todo —lo operado **y lo omitido**— alimenta el libro de sombra.

## 4. Motor de evidencia: ¿esta oportunidad tiene ventaja real?

### 4.1 El problema estadístico (con números)
Elegir las combinaciones con mejor backtest entre 204 es la receta de la **maldición del ganador**: lo que parece +0.20 R suele ser ruido. *(Ilustración con la estadística del curso de análisis, a medir con datos reales:)* una combinación con 30 setups, 40 % de aciertos y TP en 2R tiene media +0.20 R y desviación ≈ 1.47 R → error estándar ≈ 0.27 R. Si el conjunto de combinaciones tiene ventaja media ≈ 0 y dispersión real entre combinaciones ≈ 0.10 R, la **estimación encogida** es
`posterior = μ_grupo + (τ² / (τ² + SE²)) · (x̄ − μ_grupo)` = 0 + (0.01 / (0.01 + 0.072)) · 0.20 ≈ **+0.02 R**.
Es decir: lo que parecía "+0.20 R" vale ≈ "+0.02 R" y su intervalo cruza el cero. **La V1 (n ≥ 30 y expectativa > 0) habría despachado eso.**

### 4.2 Diseño *(propuesta)*
- **Encogimiento jerárquico (empirical Bayes):** la expectativa de cada celda *(activo, temporalidad, dirección, tipo: reversión/continuación)* se **estima mezclándola con la de su grupo** (misma temporalidad y dirección), con peso según su muestra. Muestras chicas → casi el promedio del grupo (que hoy es ≈ 0).
- **Dirección por separado** *(verificado: las ventas 1H/4H son −0.27/−0.34 R)*: compra y venta tienen posterior propio. Si la venta en 1H sigue siendo negativa, el Master **no la despacha** y no hace falta una regla manual.
- **Intervalo de credibilidad visible** y **semáforo de confianza** (🟢/🟡/🔴) como en `statistical-analyst`; se despacha solo si `P(expectativa neta de costos > 0)` ≥ umbral (propuesta: 80 %), no por un `n ≥ 30` fijo.
- **Línea base aleatoria (control):** antes de creer en el detector, medir si **gana a entradas aleatorias con el mismo stop y TP** (misma estructura R) en los mismos periodos. Controla la deriva alcista del periodo (la explicación probable de que las ventas pierdan) y responde la pregunta que hoy no tiene respuesta: *¿el motor detecta algo, o solo opera "R:R 2" a ciegas?*
- **Fuera de muestra siempre** (regla de oro ya adoptada, §ref. backtest): la evidencia que **autoriza** capital es la **posterior actualizada con resultados reales hacia adelante** (§7), no el histórico solo.
- **Features por oportunidad** (todas ya salen de R1–R6, `reglas_informativas`): múltiplo de volumen del barrido (R1), tamaño del FVG/ATR (R2), distancia a la EMA (R3), alineación con la temporalidad mayor (R4), profundidad en descuento/premium (R5), R:R hasta la liquidez (R6), tipo (reversión/continuación), sesión, día de la semana. La calidad se estima **por cubos empíricos** (p. ej. "volumen del barrido ≥ 3×") y no con pesos inventados a mano.

## 5. Costos: la ventaja se mide neta
- Hoy el backtest **no** descuenta spread/comisión/slippage *(verificado)*. El Master calculará `costo_R = (spread + comisión + slippage_est) / distancia_al_stop` para **cada** oportunidad y exigirá `expectativa − costo_R > 0`.
- El **EA reporta** el spread real del broker (XM) y el deslizamiento al llenar; esos datos alimentan la estimación por activo/sesión (el spread de XM cambia con la hora y las noticias).
- **Spread anómalo** (> k × su mediana de la hora) = "volatilidad de riesgo puro" (L54): el Master **no abre** y lo explica.
- Consecuencia esperable *(estimación)*: **el scalping de 5–15 m es la zona donde los costos más amenazan la ventaja**; por eso el piso de 5 m (§12) se prueba **en sombra** antes de despachar nada.

## 6. Selección bajo presupuesto: lo único que un bot solo no puede hacer

Hoy cada bot opera como si estuviera solo. Con 34 activos × varias temporalidades, **muchas oportunidades llegan a la vez** (p. ej. tras el cierre de 4H, todos los pares con USD se mueven juntos) y se apilan en la misma cuenta. El Master ve el conjunto:

- **Presupuesto de riesgo abierto por cuenta** ("calor"): suma del riesgo vivo + pendiente. Propuesta inicial: **6 %** de tope (3 operaciones al 2 %), editable.
- **Clústeres de correlación** *(propuesta)*: USD-largo/corto (EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD, USDCHF…), metales, índices de EE. UU., petróleo, cripto. **Dos operaciones del mismo clúster y dirección cuentan como una apuesta** para el presupuesto.
- **Cola de prioridad:** cuando hay más oportunidades que presupuesto, entra primero la de **mayor expectativa neta con confianza**, y se descartan las redundantes. La bitácora dice cuáles quedaron fuera y por qué.
- **Capas de límite del curso por cuenta** (L54/L56): (1) por operación: tope 2 %; (2) por día: máximo de **2–3 pérdidas** o **−5 %**, lo que ocurra primero (L47: 5 %, L54: 2–3 perdedoras); (3) por semana: si ya se alcanzó el objetivo, **no abrir por inercia** (objetivo configurable por Ricardo).
- **Protocolo de rachas:** 3 pérdidas seguidas → riesgo ×0.5 hasta recuperar; no se "arregla" el sistema durante la racha (L54).
- **Escalera de drawdown (L56 §10.6, paso 3):** −3 % de la cuenta → ×0.75 · −6 % → ×0.5 · −10 % → solo demo/pausa. *(umbrales propuestos; los defines tú)*.
- **Presupuesto diario de operaciones:** máximo 5 aperturas por cuenta/día (L46: 1–5).

## 7. Cerebro que aprende (determinista y auditable)

Ricardo quiere que el cerebro **aprenda y mejore** (checkpoint del Master) **sin IA en la decisión** (decisión A, 25 sep). La respuesta es un **libro de sombra** + actualización bayesiana:

1. **Cada oportunidad confirmada se registra en el libro, se opere o no**, con todas sus variables, y se **simula hacia adelante** con las mismas reglas de gestión (BE, trailing, −3 %) cuando cierran las velas siguientes. Así hay **muestra fuera de muestra real** para las 200+ combinaciones, no solo para las que algún bot operó.
2. **Cada semana** se actualizan las posteriores (§4.2). Una combinación **sube** de nivel cuando su evidencia fuera de muestra la respalda y **baja o se pausa** cuando se degrada (control secuencial tipo CUSUM/SPRT, el "drift" de `mtb-analyst`).
3. La **IA solo propone reglas** (nuevos cubos, umbrales); **Ricardo las aprueba** y quedan versionadas en `reglas_master`.
4. El resultado se **muestra al alumno** en lenguaje neutro: *"expectativa positiva en el histórico y en lo observado desde que existe el sistema"*, nunca "recomendable".

Esto además contesta el pendiente más antiguo de tu lista: *"monitoreo vivo vs backtest (drift)"*.

## 8. Salidas — reconsideradas como lo haría un trader

**Principio:** las salidas por *razón de señal* ya están donde deben (SL en el broker, TP en la liquidez, BE/trailing en el EA). El Master **no** añade cierres discrecionales sin evidencia. Solo cierra por tres motivos que **no dependen de adivinar la señal**:

| # | Motivo | Cuándo | Evidencia/base |
|---|---|---|---|
| 1 | **Equity** | pérdida del día ≥ 5 % o escalera de drawdown | L47, L54, L56 |
| 2 | **Riesgo agregado** | el calor supera el tope (p. ej. un setup nuevo correlacionado empuja el total) → cierra la posición con **menor expectativa viva** | §6 |
| 3 | **Expectativa** (parada óptima) | el valor esperado de **seguir** < valor de **cerrar ya** | ver abajo |

**Expectativa como parada óptima** *(propuesta, la pieza más avanzada)*: de cada setup histórico se guarda la **trayectoria** (máximo favorable y adverso en R por vela desde el llenado; hoy el backtest no la guarda *(verificado)*). Con eso se estima, para una posición a `+x R` tras `k` velas, `P(llegar al TP antes que al stop actual)`. El Master cierra (o recorta) cuando
`EV_seguir = P·(R_TP − x) − (1−P)·(x − R_stop_actual) < 0`.
Es determinista, usa nuestros propios datos y generaliza tu ejemplo ("si llega al profit calculado, por eso dio expectativa positiva") a todo el recorrido. **Se activa solo tras validarse en sombra** (hasta entonces actúa el plan actual del EA).

**Qué se queda como está (y por qué):**
- **TP** = siguiente liquidez, mínimo 2 R; **SL** en el extremo del barrido; **Swing** sin SL con cierre forzado −3 % y CHoCH inverso: son tus reglas.
- **El EA conserva** BE escalonado, trailing por estructura y expiración de pendientes. *(verificado: ya usa `ORDER_TIME_SPECIFIED`, es decir, la pendiente caduca en el broker aunque el EA o el terminal se caigan.)*

**Mejora de fondo que desbloquea todo lo anterior:** enseñar al backtest la **gestión completa** (BE, trailing, −3 %, parciales) y los **costos**. Es el pendiente "enseñar al backtest" que ya tenías y que permite decidir **con números** las variantes V1–V6 de `estrategias-por-probar.md` en lugar de por intuición.

## 9. Arquitectura técnica (senior-architect, mtb-mt5-expert)

- **Monolito modular** dentro de `mtb-api` (un solo proceso, Postgres): el equipo es de una persona; separar servicios solo aumentaría la superficie de fallo. Módulos: `master/receptor`, `master/evidencia`, `master/seleccion`, `master/despacho`, `master/cierre`.
- **Canal servidor→EA (MT5 no recibe mensajes):** `GET /api/v1/master/pulso` cada **5 s** desde `OnTimer`. Para no golpear la BD en cada pulso, el servidor guarda un **buzón en memoria por licencia** (con respaldo en BD al crear la orden); la respuesta vacía es mínima. Cada orden lleva un **número de secuencia** monótono: el EA confirma el mayor que procesó; reenvío seguro (**al menos una vez** + deduplicación por `orden_id`).
- **Idempotencia y reloj:** el EA no depende de su hora; usa `seq` y `vence_en` calculado por servidor.
- **Modos de falla:**

| Falla | Qué pasa | Por qué es seguro |
|---|---|---|
| Servidor caído | no hay órdenes nuevas | el EA sigue gestionando lo abierto (SL/TP en broker, BE/trailing/−3 % en el EA) |
| EA/terminal caído | sin latido → bot "no vivo" | pendientes caducan en el broker; posiciones con SL/TP |
| Mensaje duplicado | `orden_id` repetido | se ignora |
| Respuesta tardía | `vence_en` pasado | la orden se descarta |
| `/auth` rechaza | lotes no viables o kill switch | se registra `rechazada(motivo)` |

- **Cuenta *netting* vs *hedging* (riesgo no resuelto hoy):** en una cuenta *netting*, dos bots del **mismo símbolo** (p. ej. XAUUSD 1H y 4H) **fusionarían** sus posiciones y se pisarían SL/TP. El EA no lo detecta *(verificado: no usa `ACCOUNT_MARGIN_MODE`)*. El latido debe incluir el modo de margen y el Master aplicar "un bot por símbolo por cuenta" si es *netting*; si XM da *hedging* en estas cuentas, se documenta y se cierra el tema.
- **Datos y carga:** 100 bots ≈ 20 req/s de respuestas mínimas; se mide antes de pasar de ~50 bots. La mayor mejora de **tiempo real** sigue siendo el colector de velas XM (Fase B), no el despachador.
- **Seguridad:** token de licencia por pulso, límite de frecuencia por licencia, el kill switch cierra el buzón al instante, el registro de aceptación es inmutable.

## 10. Experiencia del alumno (aceptación, explicación, entrenamiento)

- **Aceptación:** igual que en la V1 *(Telegram con botones, panel como respaldo, interruptor local en el EA, revocable con `/pausar`, un registro por bot)*; **vale igual en demo y en cuentas propias**. Se sale del trámite legal por decisión de Ricardo; queda un registro técnico de aceptación.
- **El Master explica cada decisión**, incluida la **abstención**, que es lo más educativo: *"No abrí EURUSD 4H: el presupuesto de riesgo de tu cuenta ya está al 5.6 % con dos posiciones de dólar; esta habría sido la tercera."*, *"Reduje a la mitad el riesgo de hoy: llevas 3 pérdidas seguidas (L54)."*
- **Cada mensaje cita la regla** del curso que aplica (L47, L54, L56…): entrena el criterio del alumno en vez de crear dependencia.
- **Lenguaje neutro** (regla vigente): "expectativa positiva en el histórico", intervalos y confianza visibles, sin promesas, con aviso de riesgo.

## 11. Panel Master Trader (propuesta de pantallas; visual tras aprobar el concepto)

Consola admin (y vista simplificada del alumno):
1. **Radar** activo × temporalidad, ahora con **calidad** (expectativa neta con intervalo y semáforo 🟢🟡🔴) en vez de solo "hay/no hay".
2. **Laboratorio de evidencia:** libro de sombra, posterior por celda (activo/TF/dirección/tipo), curva de resultados fuera de muestra, línea base aleatoria, estado "elegible / en observación / pausada".
3. **Presupuesto de riesgo por cuenta** (calor): medidor con las posiciones abiertas, clústeres y espacio disponible.
4. **Bots vivos** (latido, aceptación, modo de margen, versión del EA) y **Órdenes + plan de salida**.
5. **Bitácora de decisiones** (enviar, omitir, reducir, cerrar) con la regla y la lección que la sustenta, más **latencia** de punta a punta.
6. **Control:** modo sombra/activo, kill switch, umbrales editables y versionados.

## 12. Hoja de ruta — evidencia primero, velocidad después

| Fase | Entrega | Qué decide |
|---|---|---|
| **0 · Investigación (días, sin tocar producción)** | (a) costos en R por activo; (b) línea base aleatoria; (c) análisis por **día de la semana, sesión y cercanía de noticias** (¿se cumplen L47/L48/L12 con nuestro detector?); (d) trayectorias MAE/MFE; (e) simulador de gestión V1–V6 en el backtest | **Si hay ventaja real y dónde.** Puede que el resultado sea "solo X combinaciones merecen capital" o incluso "hay que mejorar el detector antes" — y eso también es éxito |
| 1 | Registro de bots (latido) + **aceptación por Telegram/panel** + modo de margen | quién está vivo y quién aceptó |
| 2 | **Receptor + libro de sombra** (todas las oportunidades, operadas o no) | empieza a acumularse muestra fuera de muestra real |
| 3 | **Puntuar + Seleccionar + Dimensionar en sombra** (decide pero no envía) | se compara "lo que habría hecho" contra lo que pasó |
| 4 | **Despacho activo** (canal 5 s, `/auth`, plan de salida) solo a combinaciones elegibles, riesgo ×0.5 al inicio | primeras órdenes reales (demo/cuentas propias) |
| 5 | Reglas de cierre del Master (equity, riesgo, parada óptima) | tras validación en sombra |
| 6 | **Scalping 5 m experimental** | solo si los costos lo permiten (Fase 0) |

Cada fase pasa por el laboratorio `mtb-api-preview` antes de producción y trae pruebas (`demo()` como el resto del repo).

**Piso de 5 m:** *(verificado)* hoy el catálogo empieza en 15 m; no hay nada menor que quitar. Se agrega una validación para que nunca se admita < 5 m. 5 m entra **después** de la Fase 0 porque con ~2 min de retraso de Dukascopy su análisis llega a la mitad de la vela siguiente y porque es donde más pesan los costos.

## 13. Qué cambia respecto a la V1 (resumen)

| | V1 | V2 |
|---|---|---|
| Valor central | velocidad de despacho | **evidencia + presupuesto de riesgo + aprendizaje** |
| Qué se despacha | toda oportunidad con `exp > 0` y `n ≥ 30` | solo lo que supera costos con probabilidad ≥ umbral (posterior encogida) |
| Dirección | igual para compra y venta | posterior **por dirección** |
| Cuentas | cada bot por su lado | **presupuesto y correlación por cuenta**, cola de prioridad |
| Cierres | tesis/equity/riesgo (tesis ≈ duplicaba el SL) | equity, riesgo y **parada óptima** con datos de trayectoria |
| Aprendizaje | "salud del bot" básica | **libro de sombra + actualización bayesiana** continua |
| Lecciones | citadas | **convertidas en reglas** y citadas en cada mensaje |
| Primer paso | construir despacho | **Fase 0: medir si hay ventaja** |

## 14. Decisiones que necesito de ti (menos y más importantes)

1. **¿Aceptas "evidencia primero"?** Es decir, que la Fase 0 (medir costos, línea base aleatoria, día/sesión/noticias, trayectorias, simulador de gestión) vaya **antes** del despacho, y que el Master pueda **no despachar** combinaciones sin evidencia aunque el robot las detecte.
2. **¿El Master puede *reducir* el riesgo y *omitir* oportunidades** (multiplicador ≤ 1 sobre tu 2 %), nunca subirlo? Recomiendo que sí.
3. **Equity:** ¿confirmas que "evergreen" era *equity*?
4. **Presupuesto y capas (§6):** tope de calor 6 %, pérdida del día 5 % o 2–3 perdedoras, 5 aperturas/día, escalera de drawdown 3/6/10 %, racha ×0.5: ¿valen como punto de partida?
5. **Noticias:** no cierran posiciones (tu decisión). ¿Las dejo como **variable a medir** para decidir con datos si conviene no *abrir* cerca de datos fuertes? Recomiendo medir.
6. **Netting/hedging:** necesito confirmar el tipo de cuenta de XM de tus demos (lo puedo leer del terminal en el VPS) antes de la Fase 1.
7. **Alcance inicial del despacho:** solo las 5 demos del VPS, o también demos y cuentas propias de alumnos que acepten.
8. **Scalping 5 m:** después de la Fase 0 (recomendado) o antes.

## 15. Límites y riesgos honestos

- **El Master no crea ventaja.** Si el detector no tiene edge, un orquestador perfecto produce ≈ 0 menos costos. Lo que sí hace es **no regalar capital donde no hay evidencia**, **proteger la cuenta** y **encontrar dónde sí hay** — y si no la hay en ningún lado, **decirlo con datos** (y entonces la mejora será del detector, no del despacho).
- **Muestras pequeñas:** D/S/M nunca tendrán muchos setups; ahí el Master mostrará "evidencia insuficiente" y no despachará (o lo hará con multiplicador mínimo en demo).
- **Las estimaciones de esta propuesta** (costos, tiempos, carga) son a **medir** en la Fase 0/1.
- **Tablas de umbrales** (§6) son puntos de partida de la metodología del curso; se calibran con el libro de sombra.
- **Complejidad:** la V2 es más grande que la V1; por eso la ruta es por fases y cada una entrega valor por sí sola (Fase 0 ya informa decisiones del motor aunque nunca se construya el despacho).
