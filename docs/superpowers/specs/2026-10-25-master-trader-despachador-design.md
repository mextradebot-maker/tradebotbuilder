# Master Trader — Despachador de oportunidades (diseño v2, para revisión)

**Fecha:** 2026-10-25 · **Versión:** 2 (incorpora las respuestas de Ricardo del 25 oct) · **Estado:** PROPUESTA, nada construido
**Skills usadas:** mtb-trader, mtb-analyst, mtb-mt5-expert, statistical-analyst, ux-copy, brainstorming.
**Marcas:** lo que dijo Ricardo va tal cual; lo que es **mi propuesta/supuesto** está marcado *(propuesta)*; lo **verificado en el código** está marcado *(verificado)*.

---

## 0. Qué cambió respecto a la v1

| Tema | Decisión de Ricardo | Efecto en el diseño |
|---|---|---|
| Naturaleza del Master | Es **orquestador automático**, no gestor de cuentas; es lo que el alumno novato necesita | Se quita todo condicionamiento legal; el alumno solo **acepta la activación** (§6) |
| Cuentas | Igual en **demo y en cuentas propias**, para entrenar al alumno | Una sola ruta; ya no hay etapa "Real" distinta (§9) |
| Abogado | Queda fuera | Se elimina Q1 legal. Se conserva el **registro de aceptación** como bitácora técnica (cuándo, qué texto, quién), no como trámite |
| Temporalidades | Nada menor a **5 min**; scalping desde 5 m; se observará su efectividad | Piso del catálogo = 5 m (§5); no existe 1 m |
| Cierres | El Master **no cierra por noticia**; cierra por **expectativa de ganancia, equity y riesgo** combinados | Propuesta de cierre completa en §8 |

## 1. Intención

Hoy cada bot decide solo, cuando abre su vela, y el Master Trader apenas lo observa. Ricardo quiere que el Master Trader sea el **orquestador automático**: en cuanto el análisis de un activo/temporalidad trae una oportunidad, encuentra los bots vivos que corresponden, comprueba que la temporalidad coincide, les ordena abrir con el riesgo/volumen predefinido y deja lista la salida de cada orden — **con la mínima latencia**. Es la ayuda que necesita el alumno que no es experto.

**Éxito** = (a) cada oportunidad confirmada llega a todos los bots que le corresponden y han sido activados, una sola vez, en segundos; (b) nunca se abre algo que las reglas no permiten; (c) toda decisión (abrir, no abrir, cerrar) queda explicada, y el alumno **aprende** del porqué; (d) las salidas no dependen de que el Master esté en línea.

**Anti-metas:** órdenes duplicadas; abrir con una oportunidad vencida; un bot que opera sin que el sistema lo sepa o sin que el alumno lo haya activado; que una caída del Master deje posiciones sin salida.

## 2. Cómo funciona hoy *(verificado en el código)*

| Pieza | Hoy |
|---|---|
| Análisis | `refresco.py` recalcula `smc_snapshot` cuando cierra una vela de cada activo×temporalidad (34 activos; margen de 2 min por la publicación de Dukascopy; pausa de 5 s entre ciclos). |
| Oportunidad | `/api/setups` aplica el motor SMC v2 (R1–R6) y devuelve `setups_confirmados` con dirección, **entrada (orden límite)**, SL y TP. Solo se calcula **cuando alguien pregunta**; no se registra "apareció una oportunidad". |
| Bot (EA `MexTradeBot_SeguidorSMC` v1.19) | **Consulta él** al abrir cada vela de su temporalidad. Si hay setup, pide permiso y **lotes** a `/api/v1/auth` (regla única `conectividad/riesgo.py`, 2 %) y coloca una orden **límite** con SL/TP. Gestiona breakeven escalonado, trailing, cierre forzado −3 %, expiración (20 velas) y cancelación si toca el TP antes de llenarse. |
| Licencias | Una licencia = una cuenta MT5; guarda `ultimo_contacto`, `ultima_ip`. **Kill switch** global. |
| Telegram | Ya existe: enlace firmado para vincular al alumno (`alumnos.telegram_chat_id`), `POST /api/v1/telegram/enviar` que acepta **teclado con botones** (`reply_markup`), y flujos de n8n que reciben las pulsaciones. |
| Coordinador | Desde el 29 sep **ya no abre operaciones**; solo sincroniza posiciones de las cuentas demo del VPS. |
| Backtest por combinación | `backtest_largo` guarda por activo×temporalidad: `winrate`, `expectativa_r`, nº de setups, etc. Es la base de la "expectativa de ganancia". |
| Temporalidades | `TEMPORALIDADES` en `conectividad/historico.py`: **15m, 30m (inactiva), 1H, 4H, D, Swing S, Swing M**. **No hay nada menor a 15 m**; no existe ni 1 m ni 5 m en el catálogo. |
| Qué NO existe | Registro de bots vivos, canal servidor→bot, evento "oportunidad nueva", plan de salida en el servidor, registro de aceptación del alumno. |

**Limitación técnica clave:** un EA de MT5 **no puede recibir mensajes entrantes**; solo hace peticiones hacia afuera. El "Master le indica al bot" tiene que ser: *el bot pregunta cada pocos segundos si tiene algo pendiente*.

## 3. Lo pedido, punto por punto

| # | Ricardo pidió | Pieza |
|---|---|---|
| 1 | Saber en cada actualización qué oportunidades hay por activo×temporalidad | **A. Receptor de oportunidades** |
| 2 | Gestionarlas con mínima latencia; buscar los bots del activo | **B. Registro de bots** + **C. Despachador** |
| 3 | Revisar la temporalidad del bot | Regla de coincidencia en C |
| 4 | Si coincide, ordenarle abrir con las predefiniciones de riesgo y volumen | **D. Canal de órdenes** |
| 6 *(la lista saltó del 4 al 6)* | Dejar lista la orden de salida de cada apertura | **E. Plan de salida** |
| nuevo | Cierre automático por expectativa, equity y riesgo | **F. Reglas de cierre** (§8) |
| nuevo | El alumno acepta la activación | **G. Activación por el alumno** (§6) |

## 4. Principios *(propuesta)*

1. **Decisiones deterministas y auditables** (decisión A de Ricardo, 25 sep): reglas en Postgres, sin IA en el bucle. La IA solo propone reglas que Ricardo aprueba.
2. **Una sola fuente de verdad para el riesgo:** `conectividad/riesgo.py`. El Master no calcula lotes por su cuenta.
3. **El bot ejecuta y protege.** SL y TP viven en el broker desde el primer segundo; breakeven/trailing/cierre forzado siguen en el EA. Si el Master cae, las posiciones abiertas se siguen protegiendo.
4. **Idempotencia:** cada oportunidad tiene un id; un bot nunca la ejecuta dos veces.
5. **Falla cerrado:** sin respuesta, licencia dudosa, kill switch o **sin aceptación del alumno** → no se abre.
6. **Todo igual en demo y cuentas propias** (entrenamiento): un solo camino de código.
7. **Explica siempre el porqué** al alumno (mensaje en Telegram con el motivo de cada apertura, omisión y cierre).

## 5. Catálogo de temporalidades: piso de 5 minutos

- **Lo verificado:** hoy el catálogo empieza en **15 m**. No hay nada menor a 5 m que quitar; lo único con "5m" en el código es un texto de prueba (`"Scalping 5m"` en el autodiagnóstico de `carga_historica.py`). **No toqué código.**
- **Regla nueva (propuesta):** el catálogo **no admite** temporalidades menores a 5 m; se deja como validación en `TEMPORALIDADES` para que nadie la agregue por error (1 m queda descartada definitivamente).
- **Scalping 5 m (propuesta, para observar su efectividad):** agregarlo como **experimental**: nueva fila de catálogo (34 combinaciones más), intervalo de 5 min de Dukascopy, el EA acepta `PERIOD_M5`.
- **Dos avisos honestos:**
  1. Con ~2 min de retraso de Dukascopy, el análisis de una vela de 5 m está listo ~2.5 min después de cerrar, es decir, **a la mitad de la vela siguiente**. Las entradas son límite y siguen siendo útiles, pero hay menos margen: la Fase B (colector de velas XM, aún sin desplegar) es lo que lo arregla de verdad.
  2. Carga: +34 refrescos cada 5 min ≈ +34 s de CPU por ciclo *(estimación a ~1 s por combinación, a medir)*.
- **Decisión (mi propuesta):** agregar 5 m como experimental **después** de la Fase 1, para medir con datos de verdad. Si prefieres que 5 m entre ya, se mueve al principio.

## 6. G. Activación por el alumno (reemplaza a Q1)

**Principio:** el Master es automático, pero **solo despacha a bots que el alumno activó**. La activación es **por bot** (licencia = cuenta + robot) y funciona igual en demo y cuentas propias.

**¿Cómo se le pide? — recomendación: Telegram como canal principal, con panel de respaldo** *(propuesta)*. Razones: ya hay vínculo alumno↔Telegram, el bot ya puede enviar botones y n8n ya recibe pulsaciones.

```
1. El bot manda su primer latido  ──►  aparece en "Bots vivos" como "Pendiente de activación" (no recibe órdenes)
2. Servidor envía por Telegram al alumno vinculado:
        ┌────────────────────────────────────────────────────────────────┐
        │ Master Trader · activar para tu bot                            │
        │ XAUUSD · 1H · cuenta demo 3186…                                │
        │                                                                │
        │ Si lo activas, cuando el análisis detecte una oportunidad en   │
        │ XAUUSD 1H, tu bot la operará solo, con tu riesgo configurado   │
        │ (máx. 2 % por operación), con stop loss y take profit.         │
        │ Te explicaré aquí cada apertura y cada cierre.                 │
        │ Puedes pausarlo cuando quieras con /pausar.                    │
        │ Herramienta educativa: operar implica riesgo de pérdida.       │
        │                                                                │
        │   [ Activar Master Trader ]      [ Ahora no ]                  │
        └────────────────────────────────────────────────────────────────┘
3. Pulsa un botón → n8n recibe → POST /api/v1/master/aceptacion (clave de servicio)
        → se guarda: licencia, chat_id, decisión, versión del texto, fecha/hora
4. Confirmación: "Listo. Master Trader activado para XAUUSD · 1H. Te avisaré aquí cuando abra o cierre algo."
```

- **Si el alumno no tiene Telegram vinculado:** en el panel (sección de sus robots) aparece el mismo aviso con un interruptor "Activar Master Trader" y el botón existente para vincular Telegram.
- **Varios bots:** un solo mensaje con la lista y botón "Activar todos" / activar uno por uno *(propuesta)*.
- **Revocar:** `/pausar` en Telegram o el interruptor del panel; surte efecto en el siguiente pulso (~5 s). Lo ya abierto sigue con su plan de salida.
- **Interruptor local en el EA** (`InpMasterTrader`, por defecto activo): el alumno puede apagarlo desde MT5. El bot opera con el Master solo si **hay aceptación en servidor Y el interruptor local está activo**.
- **Recordatorios:** si dice "Ahora no", no se insiste; en el panel queda la opción. *(propuesta)*
- **Tono de los mensajes** (skill ux-copy, español de México, lenguaje neutro como el resto del proyecto): verbos claros en los botones, sin promesas de rendimiento, siempre con el motivo.

**Mensajes de entrenamiento (propuesta):**
- *Apertura:* "▲ Compra límite XAUUSD · 1H — entrada 2315.50 · SL 2305.00 · TP 2330.00 (1:2.4). **Por qué:** retroceso a un Order Block alcista dentro de tendencia al alza; expectativa histórica de esta combinación: +0.38 R."
- *Omisión (solo si el alumno lo activa en ajustes):* "No abrí EURUSD 4H: ya tienes una posición abierta en este bot."
- *Cierre:* "Cerré XAUUSD 1H en +1.2 R. **Por qué:** se alcanzó la meta calculada por el análisis." / "…en −0.4 R. **Por qué:** la estructura que sostenía la compra se rompió."

## 7. Arquitectura

```
cierra vela ─► refresco guarda snapshot ─► A. RECEPTOR detecta setup confirmado
                                              │  (misma función que /api/setups, mismo proceso, sin red extra)
                                              ▼
                              tabla oportunidades  (nueva / vigente / vencida / invalidada)
                                              │
                                              ▼
                         C. DESPACHADOR (evento inmediato, no cron)
              ┌── busca en B. REGISTRO los bots vivos: mismo símbolo
              ├── filtra: misma temporalidad · activado por el alumno · licencia válida · sin posición/orden ·
              │           sin orden previa de esta oportunidad
              ├── aplica GUARDIAS (kill switch, expectativa positiva, exposición, spread, vigencia)
              └── crea una ORDEN DE APERTURA por bot + su PLAN DE SALIDA (E)
                                              │
                                              ▼
                    D. CANAL: el bot pregunta cada 5 s (OnTimer) "¿tengo algo?"
                                              │  recibe la orden ─► pide /auth (lotes, regla única) ─► coloca límite con SL/TP
                                              ▼
                         ACK ─► bitácora ─► mensaje al alumno (Telegram)
                                              │
        F. REGLAS DE CIERRE evalúan cada posición abierta en cada actualización del análisis y cada pulso (§8)
```

### A. Receptor de oportunidades
- Se ejecuta **justo después de guardar cada snapshot**, en el mismo hilo de `refresco.py`. Reusa `detectar_setups_v2`; ~1 s extra por combinación *(a medir)*.
- Emite: **nueva**, **sin cambios**, **invalidada** o **vencida** (TP tocado antes de llenarse, o 20 velas).
- Cada oportunidad lleva: id estable, dirección, entrada, SL, TP, R, `ref_ts`/`ref_cierre` (para el ajuste de precio por broker) y su **expectativa histórica** (de `backtest_largo`).

### B. Registro de bots vivos (latido)
- El EA manda en cada pulso: cuenta, símbolo, temporalidad, modo, versión del EA, balance/equity, posición sí/no, pendiente sí/no, standby sí/no.
- **Vivo = latido en los últimos ~20 s** *(con pulso de 5 s; configurable)*. Da el "dónde está cada bot y su estado".
- **Apagar un bot** = orden `apagar` por el mismo canal (cancela pendientes, deja de abrir); la posición abierta sigue las reglas de §8.

### C. Despachador — reglas de coincidencia
Un bot recibe orden de abrir solo si **todas** se cumplen: (1) mismo símbolo **y misma temporalidad**; (2) latido vigente; (3) **activado por el alumno**; (4) licencia válida, sin kill switch; (5) sin posición ni pendiente; (6) no se le envió ya esta oportunidad; (7) pasa las guardias (§8.4). Cada omisión queda registrada con su motivo.

### D. Canal de órdenes (servidor → bot)
- `GET /api/v1/master/pulso` (token de licencia) cada **5 s** desde `OnTimer`: respuesta mínima si no hay nada, o la orden pendiente. *(Elegí 5 s: es ~1.7 % de la vela más corta, 5 m; menos carga que 3 s; ver §10.)*
- La orden trae `orden_id`, `oportunidad_id`, dirección, entrada, SL, TP, vigencia y plan de salida. **No trae lotes**: el bot los pide a `/api/v1/auth` justo antes de colocar (balance y especificaciones frescas; cálculo en un solo sitio).
- `POST /api/v1/master/ack`: `recibida` → `colocada(ticket)` / `rechazada(motivo)` / `llenada` / `cancelada` / `cerrada(resultado)`. Sin ACK en X s → `sin_respuesta` y alerta.

### E. Plan de salida (la "orden de salida lista")
Se crea **con** la orden de apertura, se guarda y se arma al colocar. Capas:
1. **Broker (inmediata, sin depender de nadie):** SL y TP. *(Swing S/M sin SL por regla de Ricardo: su freno es el cierre forzado −3 %.)*
2. **EA (cada tick):** escalera de breakeven (1R→entrada, 1.5R→+0.5R, 2R→+1R), trailing por estructura desde 2R, cierre forzado −3 %, expiración, cancelación si toca TP antes de llenarse; Swing: CHoCH inverso.
3. **Master (por el canal):** `cerrar` / `cancelar` / `apagar` según las **reglas de cierre de §8**, nunca por noticias.

## 8. F. Propuesta de cierre — expectativa, equity y riesgo

### 8.1 Idea
Cada posición abierta se evalúa con **tres semáforos** que se combinan con una tabla fija (determinista y explicable). El Master **no cierra por noticias**.

> **Una duda para confirmar:** en tu mensaje escribiste "expectativa de ganancia, **el evergreen** y el riesgo". Lo interpreto como **equity** (el capital vivo de la cuenta, con las pérdidas/ganancias flotantes). Si querías decir otra cosa, dímelo y lo ajusto.

### 8.2 Los tres factores

**① Expectativa de ganancia** *(¿la razón por la que abrimos sigue en pie?)*
- **Al abrir:** solo se despacha si la combinación tiene `expectativa_r > 0` en su backtest **y** muestra suficiente (n ≥ 30 setups). Con n pequeño la expectativa no es fiable: por ejemplo, 15 ganadas de 30 (50 %) tiene un intervalo del 95 % de **33 % a 67 %**, y con TP en 2R el punto de equilibrio es 33 %, o sea, no se distingue del azar; con 55 de 100 el intervalo baja a 45–64 %. *(Estadística: Wilson; 🟢 confiable n ≥ 100, 🟡 indicativo 30–99, 🔴 no despachar < 30.)*
- **Durante la posición:** la **tesis** (el setup que la originó) se evalúa en cada actualización:
  - 🟢 **vigente:** la estructura que la sostiene sigue intacta.
  - 🟡 **debilitada:** el precio avanzó en contra más de 0.5 R sin romper la estructura, o la posición lleva más de **1×** la duración típica de esa combinación sin progreso (mediana del backtest).
  - 🔴 **invalidada:** la estructura se rompió (cierre más allá del nivel que sostenía el setup / CHoCH contrario confirmado), o lleva más de **2×** la duración típica con progreso < 0.5 R.
- **Meta cumplida:** el TP calculado por el análisis es la salida natural; ya vive en el broker.

**② Equity** *(¿cuánto capital estamos arriesgando hoy?)*
- 🟢 normal.
- 🟡 pérdida del día ≥ **3 %** del capital: el Master **deja de abrir** en esa cuenta ese día.
- 🔴 pérdida del día ≥ **5 %**, o una posición con pérdida flotante ≥ **3 %** (el cierre forzado que ya existe): cierre.
*(Umbrales: propuesta basada en tus reglas de 2 % por operación y −3 % forzado; los confirmas tú.)*

**③ Riesgo agregado** *(¿cuántas apuestas simultáneas hay?)*
- Riesgo abierto = suma del riesgo (en % del capital) de las posiciones abiertas de la cuenta, **contando una sola vez** las que son la misma apuesta (p. ej. varios pares con USD en la misma dirección).
- 🟢 ≤ 4 % · 🟡 4–6 % (no abrir más) · 🔴 > 6 % (cerrar la peor posición hasta quedar dentro).

### 8.3 Cómo se combinan (tabla de decisión, de arriba abajo, gana la primera que aplique)

| # | Condición | Acción del Master |
|---|---|---|
| 1 | Equity 🔴 | **Cerrar** (la cuenta primero) |
| 2 | Expectativa 🔴 **y** progreso < +1R | **Cerrar** ("la tesis ya no existe") |
| 3 | Expectativa 🔴 **y** Riesgo ≥ 🟡 | **Cerrar** |
| 4 | Riesgo 🔴 | **Cerrar la posición con peor expectativa viva** hasta volver a 🟡 |
| 5 | Expectativa 🔴 **y** progreso ≥ +1R | **No cerrar**: la escalera de breakeven/trailing ya protege la ganancia; se deja correr |
| 6 | Cualquier 🟡 | **Vigilar y no abrir más** en esa cuenta; avisar al alumno |
| 7 | Todo 🟢 | Mantener; la salida natural es el TP |

Ejemplos: *"cierro XAUUSD 1H en −0.4 R porque la estructura se rompió"* (regla 2); *"cierro la posición más débil porque el riesgo abierto llegó a 6.8 %"* (regla 4); *"se alcanzó la meta calculada por el análisis"* (TP, regla 7).

### 8.4 Guardias de apertura (resumen)
Ya vigentes y respetadas sin duplicar: 2 % por operación; no abrir si el SL cuesta > 2 %; cierre forzado −3 %; una posición a la vez por bot. Nuevas: **expectativa positiva con n ≥ 30**; vigencia (el precio no tocó ya el TP ni pasó la entrada); spread máximo; exposición (§8.2-③); pérdida del día (§8.2-②); kill switch; aceptación del alumno.
*Pregunta menor:* la **ventana de noticias** como guarda de **apertura** (no cerrar, solo no abrir en la hora de un dato fuerte) estaba en la v1; como dijiste que no se cierra por noticia, **la quito** salvo que me pidas conservarla.

### 8.5 Salud del bot (drift)
Con los umbrales de mtb-analyst el Master **pausa nuevas aperturas** de un bot si: Profit Factor < 1.0, drawdown > 15 %, win rate < 35 % en 20+ operaciones, o 5+ pérdidas seguidas (esta última solo alerta). Requiere registrar resultados reales de cada bot.

## 9. Etapas de implantación *(propuesta)*

La diferencia entre demo y cuenta propia desaparece (mismo flujo). Quedan dos etapas, porque siempre conviene validar primero:
1. **Sombra:** el Master detecta, empareja y **registra lo que habría ordenado y cerrado**, sin enviar nada. Valida reglas y mide latencia.
2. **Activo:** envía órdenes a los bots **activados por el alumno**, en demo o cuenta propia, con las reglas de §8.

## 10. Latencia — presupuesto honesto *(estimaciones; hay que medirlas)*

| Tramo | Hoy | Con el diseño |
|---|---|---|
| Cierre de vela → snapshot listo | hasta ~2 min (publicación Dukascopy) + ciclo ≤ 5 s | igual; **segundos con el colector XM (Fase B, sin desplegar)** |
| Snapshot → oportunidad registrada | no se registra | ~1 s |
| Oportunidad → bot se entera | solo al abrir su vela | ≤ 5 s (promedio ~2.5 s) |
| Bot → orden colocada | `/auth` + orden ≈ 0.3–1 s | igual |
| **Total tras el snapshot** | hasta una vela | **≈ 4–7 s** |

- Las entradas son **órdenes límite**: la latencia no cambia el precio; el riesgo es que el precio toque el nivel **antes** de que la orden exista. Por eso se revisa la vigencia antes de enviar.
- El mayor ahorro no está en el despachador sino en **cuándo se refresca el análisis** (Fase B).
- Carga: con N bots y pulso de 5 s son N/5 peticiones por segundo; 100 bots ≈ 20 req/s de respuestas mínimas. Se mide antes de pasar de ~50 bots.

## 11. Modelo de datos *(propuesta; tablas nuevas)*

- `oportunidades` — id, simbolo, temporalidad, direccion, entrada, sl, tp, r, ref_ts, expectativa_r, n_muestra, estado, detectada_en, vence_en.
- `bots_vivos` — licencia_id, cuenta, simbolo, temporalidad, modo, version_ea, balance, equity, tiene_posicion, tiene_pendiente, standby, ultimo_latido.
- `aceptaciones_master` — licencia_id, chat_id, decision (activar/pausar/rechazar), version_texto, canal (telegram/panel), registrado_en. *(Bitácora técnica de quién activó qué y cuándo.)*
- `ordenes_master` — id, oportunidad_id, licencia_id, tipo (abrir/cerrar/apagar/cancelar), estado, ticket, creada_en, ack_en, motivo, plan_salida jsonb.
- `decisiones_master` — oportunidad_id, licencia_id, decision (enviar/omitir/cerrar/mantener), regla (§8.3), motivo, ts.
- `reglas_master` — umbrales y modo (sombra/activo), con versión y quién los cambió.

## 12. Sección del panel Master Trader *(propuesta; el diseño visual lo haremos con las skills de diseño)*

Consola admin del Master Trader, cinco bloques; más una vista simple para el alumno:

```
┌ RADAR ─────────────────────────────────────────────────────────────────┐
│ Cuadrícula activo × temporalidad. Celda: sin oportunidad / vigente      │
│ (▲ ▼) / despachada / vencida. Confianza estadística: 🟢 🟡 🔴          │
├ BOTS VIVOS ────────────────────────┬ POSICIONES Y SEMÁFOROS ───────────┤
│ cuenta · símbolo · TF · activado   │ por posición: Expectativa ● Equity ● │
│ sí/no · latido · [Apagar]          │ Riesgo ●  · plan de salida · regla  │
├ BITÁCORA ──────────────────────────┴───────────────────────────────────┤
│ "XAUUSD 1H ▲ → 3 bots: 2 enviadas, 1 omitida (ya tenía posición)"       │
│ latencia: detección → entrega → colocada (ms)                           │
├ CONTROL ───────────────────────────────────────────────────────────────┤
│ Modo [Sombra | Activo] · Kill switch · Umbrales (§8) editables          │
└─────────────────────────────────────────────────────────────────────────┘
Vista del alumno: sus bots, interruptor "Master Trader", y la lista de "qué hizo y por qué".
```

## 13. Fuera de alcance

Cambiar el motor SMC o R1–R6; que el Master calcule lotes; IA en la decisión; cerrar por noticias; reescribir el EA (solo se **añade**: latido, pulso, ACK, apagar, interruptor); temporalidades menores a 5 m.

## 14. Fases de construcción *(propuesta, cada una verificable)*

| Fase | Entrega | Verificación |
|---|---|---|
| 0 | Medir latencia real cierre→snapshot y carga de `/api/setups` | informe con números |
| 1 | Registro de bots (latido) + vista "Bots vivos" + **aceptación por Telegram/panel** | los bots demo aparecen vivos; la pulsación de "Activar" queda registrada |
| 2 | Receptor (A) + `oportunidades` + radar | coincide con `/api/setups` |
| 3 | Despachador en **Sombra** + bitácora + semáforos de §8 calculados | decisiones registradas, cero órdenes |
| 4 | Canal (D) + EA nuevo + plan de salida en **Activo** | una oportunidad real llega a un bot activado y se coloca con SL/TP en < 7 s |
| 5 | Reglas de cierre del Master (§8.3) y salud del bot | pruebas por cada fila de la tabla |
| 6 | Scalping 5 m experimental | 34 series nuevas, medición de efectividad |

Cada fase pasa por el laboratorio `mtb-api-preview` antes de producción y trae pruebas automáticas (`demo()` como el resto del proyecto).

## 15. Preguntas abiertas para Ricardo

1. **"Evergreen"**: ¿lo leí bien como *equity*? (§8.1)
2. **Umbrales de §8.2** — 3 %/5 % de pérdida del día, 4 %/6 % de riesgo abierto, 1×/2× duración típica: ¿valen como punto de partida?
3. **Tabla de decisión (§8.3):** ¿estás de acuerdo con la regla 5 (si la tesis se invalida pero ya hay +1R, se deja correr con la escalera)?
4. **Activación:** ¿Telegram principal + panel de respaldo + interruptor en el EA? ¿Un mensaje por bot o uno con todos?
5. **Mensajes al alumno:** ¿quieres también avisos de **omisión** ("no abrí porque…") o solo aperturas y cierres?
6. **Scalping 5 m:** ¿entra después de la Fase 1 (propuesta) o primero?
7. **Ventana de noticias** como bloqueo de **apertura**: ¿la quito del todo (propuesta) o la conservo?
8. **Mínimo de muestra** para despachar una combinación (n ≥ 30): ¿lo subimos a 100 para exigir 🟢?

## 16. Riesgos que veo

- **Latencia dominada por la publicación de datos**, no por el despachador (§10): sin la Fase B la mejora es menor de lo esperado, y más para 5 m.
- **Carga** del servidor con muchos bots haciendo pulso.
- **Doble ejecución** si falla el ACK: `orden_id` idempotente + "una posición a la vez".
- **Cerrar por reglas** puede sacar una operación que luego habría ganado: la tabla es explícita y auditable, y el modo **Sombra** permite medir cuántas veces pasaría antes de activarlo.
- **Reinstalar el EA** (.ex5 nuevo) en cada terminal: se versiona y se migra por tandas.
- **Evidencia estadística débil** en combinaciones con pocos setups (§8.2): por eso el filtro n ≥ 30.
