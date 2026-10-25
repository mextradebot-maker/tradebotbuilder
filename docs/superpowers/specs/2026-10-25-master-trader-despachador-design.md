# Master Trader — Despachador de oportunidades (diseño para revisión)

**Fecha:** 2026-10-25 · **Estado:** PROPUESTA, nada construido · **Autor:** Claude, con las skills mtb-trader, mtb-analyst, mtb-mt5-expert y brainstorming
**Marcas:** lo que dijo Ricardo va tal cual; lo que es **mi propuesta/supuesto** está marcado *(propuesta)*; lo que **verifiqué en el código** está marcado *(verificado)*.

---

## 1. Intención

Hoy cada bot decide solo, cuando abre su vela, y el Master Trader apenas lo observa. Ricardo quiere que el Master Trader sea el **centro que reacciona**: en cuanto el análisis de un activo/temporalidad trae una oportunidad, encuentra los bots vivos que corresponden, comprueba que la temporalidad coincide, les ordena abrir con el riesgo/volumen predefinido y deja lista la salida de cada orden — **con la mínima latencia posible**.

**Éxito** = (a) cada oportunidad confirmada llega a todos los bots que le corresponden, una sola vez, en segundos; (b) nunca se abre algo que las reglas no permiten; (c) toda decisión (abrir o no abrir) queda explicada en una bitácora; (d) las salidas no dependen de que el Master esté en línea.

**Anti-metas** (fallaría aunque "funcione"): órdenes duplicadas; abrir con una oportunidad ya vencida; un bot que opera sin que el sistema sepa que existe; que una caída del Master deje posiciones sin salida.

## 2. Cómo funciona hoy *(verificado en el código)*

| Pieza | Hoy |
|---|---|
| Análisis | `refresco.py` recalcula `smc_snapshot` cuando cierra una vela de cada activo×temporalidad (34 activos × 6 temporalidades; margen de 2 min por la publicación de Dukascopy; pausa de 5 s entre ciclos). |
| Oportunidad | `/api/setups` aplica el motor SMC v2 (reglas R1–R6) y devuelve `setups_confirmados` con dirección, **entrada (orden límite)**, SL, TP. Solo se calcula **cuando alguien pregunta**; nadie registra "apareció una oportunidad". |
| Bot (EA `MexTradeBot_SeguidorSMC` v1.19) | **Consulta él** al abrir cada vela de su temporalidad (en swing, cada vela D1). Si hay setup: pide permiso y **lotes** a `/api/v1/auth` (regla única `conectividad/riesgo.py`, 2 %) y coloca una orden **límite** con SL/TP. Él mismo gestiona breakeven escalonado, trailing, cierre forzado −3 %, expiración (20 velas) y cancelación si toca el TP antes de llenarse. |
| Licencias | Una licencia = una cuenta MT5; guarda `ultimo_contacto`, `ultima_ip`. Hay **kill switch global**. |
| Coordinador | Desde el 29 sep **ya no abre operaciones**; solo sincroniza posiciones de las 5 cuentas demo del VPS. |
| Qué NO existe | Un registro de bots vivos (cuenta + símbolo + temporalidad + estado), un canal para que el servidor le hable al bot, un evento "oportunidad nueva", ni un plan de salida guardado en el servidor. |

**Limitación técnica clave:** un EA de MT5 **no puede recibir mensajes entrantes**; solo hace peticiones hacia afuera. Por eso "el Master le indica al bot" tiene que ser: *el bot pregunta muy seguido (cada pocos segundos) si tiene órdenes pendientes*. No hay otra forma con MT5. *(propuesta de cómo hacerlo: §6)*

## 3. Lo pedido, punto por punto

| # | Ricardo pidió | Qué falta | Pieza nueva |
|---|---|---|---|
| 1 | Saber en cada actualización qué oportunidades hay por activo×temporalidad (receptor) | Evento al terminar cada refresco | **A. Receptor de oportunidades** |
| 2 | Gestionarlas con mínima latencia; buscar los bots de ese activo | Saber qué bots están vivos y dónde | **B. Registro de bots** + **C. Despachador** |
| 3 | Revisar la temporalidad del bot | Regla de coincidencia | Regla en C |
| 4 | Si coincide, ordenarle abrir con las predefiniciones de riesgo y volumen | Canal servidor→bot y orden con idempotencia | **D. Canal de órdenes** |
| 5 *(el 5 no venía numerado; la lista saltó del 4 al 6)* | — | — | — |
| 6 | Dejar lista la orden de salida de cada apertura | Plan de salida guardado y armado | **E. Plan de salida** |

## 4. Principios *(propuesta)*

1. **Decisiones deterministas y auditables.** Reglas en Postgres, sin IA en el bucle de decisión (decisión A de Ricardo, 25 sep 2026). La IA solo podría *proponer* reglas que Ricardo aprueba.
2. **Una sola fuente de verdad para el riesgo:** `conectividad/riesgo.py`. El Master no calcula lotes por su cuenta; los obtiene de esa regla.
3. **El bot sigue siendo quien ejecuta y quien protege.** SL y TP viven en el broker desde el segundo cero; breakeven/trailing/cierre forzado siguen en el EA. Si el Master cae, **las posiciones abiertas se siguen protegiendo**. El Master solo decide *abrir* y, en casos puntuales, *cerrar/apagar*.
4. **Idempotencia:** cada oportunidad tiene un id; un bot nunca la ejecuta dos veces.
5. **Falla cerrado:** sin respuesta del servidor, licencia dudosa o kill switch → no se abre.
6. **Se introduce por etapas:** Sombra → Demo → Real (§9).

## 5. Arquitectura

```
cierra vela ─► refresco guarda snapshot ─► A. RECEPTOR detecta setup confirmado
                                              │  (misma función que /api/setups, mismo proceso, sin red extra)
                                              ▼
                              tabla oportunidades  (nueva / vigente / vencida / invalidada)
                                              │
                                              ▼
                         C. DESPACHADOR (evento inmediato, no cron)
              ┌── busca en B. REGISTRO los bots vivos: mismo símbolo
              ├── filtra: misma temporalidad, licencia válida, sin posición/orden, sin orden previa de esta oportunidad
              ├── aplica GUARDIAS (kill switch, modo, noticias, exposición, spread, vigencia)
              └── crea una ORDEN DE APERTURA por bot + su PLAN DE SALIDA (E)
                                              │
                                              ▼
                    D. CANAL: el bot pregunta cada ~3 s (OnTimer) "¿tengo algo?"
                                              │  recibe la orden ─► pide /auth (lotes, regla única) ─► coloca límite con SL/TP
                                              ▼
                              ACK / estado de la orden ─► Bitácora y panel
```

### A. Receptor de oportunidades
- Se ejecuta **inmediatamente después de guardar cada snapshot** (activo×temporalidad), en el mismo hilo de `refresco.py`. Reusa `detectar_setups_v2`; costo estimado ~1 s extra por combinación *(a medir)*.
- Compara contra la oportunidad vigente de esa combinación y emite un evento: **nueva**, **sin cambios**, **invalidada** (el setup ya no existe) o **vencida** (TP tocado antes de llenarse, o pasaron las 20 velas).
- Cada oportunidad lleva: id estable (activo+temporalidad+dirección+entrada+vela de referencia), dirección, entrada, SL, TP, R, vela de referencia (`ref_ts`/`ref_cierre` para el ajuste de precio por broker), vigencia.

### B. Registro de bots vivos (latido)
- El EA, en cada pulso (y en su consulta actual), manda: cuenta, símbolo, temporalidad, modo (demo/real), versión del EA, balance/equity, posición abierta sí/no, orden pendiente sí/no, standby sí/no.
- El servidor mantiene `bots_vivos`; **vivo = latido en los últimos ~15 s** *(propuesta; configurable)*. Esto da el "dónde está cada bot y su estado" que el Master necesita y que hoy no existe.
- **Apagar un bot** = una orden `apagar` por el mismo canal (el EA cancela pendientes, deja de abrir y avisa), además de la revocación de licencia que ya existe.

### C. Despachador — reglas de coincidencia
Para cada oportunidad *nueva*, un bot recibe orden de abrir solo si **todas** se cumplen:
1. Mismo símbolo **y misma temporalidad** (coincidencia exacta; ver pregunta abierta Q3).
2. Latido vigente y modo permitido por la etapa actual (§9).
3. Licencia válida, sin kill switch.
4. Sin posición ni orden pendiente de ese bot (regla actual del EA: una a la vez).
5. No se le envió ya esta oportunidad (idempotencia).
6. Pasa las **guardias** (§8).
Si algo falla, **también se registra** el motivo ("omitido: ya tiene posición", "omitido: noticia en 20 min"…).

### D. Canal de órdenes (servidor → bot)
- `GET /api/v1/master/pulso` (token de licencia) cada ~3 s desde `OnTimer`: devuelve `{}` casi vacío si no hay nada (respuesta mínima, sin cálculo) o la orden pendiente de ese bot.
- La orden trae: `orden_id`, `oportunidad_id`, dirección, entrada, SL, TP, vigencia y el **plan de salida**. **No trae lotes**: el bot los pide a `/api/v1/auth` justo antes de colocar, con su balance y especificaciones reales del símbolo (así la regla única del 2 % se aplica con datos frescos y el cálculo no se duplica).
- `POST /api/v1/master/ack`: el bot confirma `recibida` → `colocada(ticket)` / `rechazada(motivo)` (ej. error 10016 stops muy cerca) / `llenada` / `cancelada` / `cerrada(resultado)`. Sin ACK en X s, el despachador marca la orden `sin_respuesta` y alerta.
- El EA conserva sus protecciones actuales (ajuste de precio por broker, expiración, −3 %).

### E. Plan de salida (la "orden de salida lista")
Se crea **junto con la orden de apertura** y se guarda en el servidor; el EA lo recibe y lo arma en el mismo acto de colocar. Tres capas, de la más rápida a la más flexible:
1. **En el broker (inmediata, no depende de nadie):** SL y TP de la orden. *(En Swing S/M no hay SL por regla de Ricardo; su freno es el cierre forzado −3 %.)*
2. **En el EA (cada tick):** escalera de breakeven (1R→entrada, 1.5R→+0.5R, 2R→+1R), trailing por estructura desde 2R, cierre forzado −3 %, expiración de la pendiente (20 velas), cancelar si toca TP antes de llenarse; Swing: salida por CHoCH inverso.
3. **Del Master (discrecional, por el canal):** `cerrar` o `apagar` ante eventos que el EA no ve: noticia de alto impacto, kill switch, oportunidad invalidada antes de llenarse (cancelar la pendiente), drift de rentabilidad (§8).
El plan de salida queda registrado por orden para que el panel muestre "qué pasará con cada orden".

## 6. Latencia — presupuesto honesto *(estimaciones, hay que medirlas)*

| Tramo | Hoy | Con el diseño |
|---|---|---|
| Cierre de vela → snapshot listo | hasta ~2 min (margen de publicación Dukascopy) + ciclo ≤5 s | igual; **~segundos con el colector de velas XM (Fase B), que sigue sin desplegarse** |
| Snapshot → oportunidad registrada | no se registra; el EA la descubre al preguntar (tolera snapshot de hasta 10 min) | ~1 s (inmediato, mismo proceso) |
| Oportunidad → bot se entera | el bot pregunta solo al abrir su vela | ≤ ~3 s (intervalo del pulso; promedio ~1.5 s) |
| Bot → orden colocada | `/auth` + orden ≈ 0.3–1 s | igual |
| **Total tras el snapshot** | variable: hasta una vela completa | **≈ 3–6 s** |

Dos aclaraciones que importan:
- **Las entradas son órdenes límite** (en un Order Block/FVG), no órdenes a mercado. La latencia no cambia el precio de entrada; el riesgo real es que el precio **toque el nivel antes de que la orden exista** (se pierde ese llenado — hoy el backtest ya contempla "cancelado_tp"). Por eso el despachador revisa la vigencia contra el precio actual antes de enviar.
- El mayor ahorro de latencia **no está en el despachador** sino en cuándo se refresca el análisis (Fase B). Conviene medir el tramo real de punta a punta antes de prometer cifras.

Carga: con N bots y pulso cada 3 s son N/3 peticiones por segundo; 100 bots ≈ 33 req/s de respuestas mínimas. El servidor es `http.server` con hilos; se mide antes de pasar de ~50 bots (si hace falta: pulso cada 5 s o cabecera `If-None-Match`).

## 7. Modelo de datos *(propuesta; tablas nuevas)*

- `oportunidades` — id, simbolo, temporalidad, direccion, entrada, sl, tp, r, ref_ts, estado (nueva/vigente/invalidada/vencida), detectada_en, vence_en, snapshot_ref.
- `bots_vivos` — licencia_id, cuenta, simbolo, temporalidad, modo, version_ea, balance, equity, tiene_posicion, tiene_pendiente, standby, ultimo_latido (índice por símbolo+temporalidad).
- `ordenes_master` — id, oportunidad_id, licencia_id, tipo (abrir/cerrar/apagar), estado (creada/entregada/colocada/llenada/cancelada/cerrada/rechazada/sin_respuesta), ticket, creada_en, entregada_en, ack_en, motivo, plan_salida jsonb.
- `decisiones_master` — oportunidad_id, licencia_id (nullable), decision (enviar/omitir), motivo, regla, ts. (Extiende la idea de `log_coordinador` a todos los bots.)
- `reglas_master` — reglas deterministas editables (guardias, modos, límites, ventanas de noticias) con versión.

## 8. Guardias de riesgo *(propuesta, a confirmar con Ricardo)*

Ya vigentes y que el Master **respeta sin duplicar**: 2 % por operación; no abrir si el SL cuesta > 2 %; cierre forzado −3 %; breakeven/trailing; una posición a la vez por bot.
Nuevas, a nivel Master:
- **Kill switch** (ya existe) y **modo** por etapa (§9).
- **Noticias:** no despachar en la ventana de un evento de alto impacto (hay `conectividad/calendario.py`).
- **Vigencia:** no enviar si el precio ya tocó el TP, pasó la entrada o el setup se invalidó.
- **Exposición por cuenta:** tope de riesgo abierto total (p. ej. máx. 3 operaciones o 6 % simultáneas) y **correlación** (varios pares con USD en la misma dirección cuentan como una apuesta) *(valores por definir)*.
- **Pérdida diaria por cuenta** (p. ej. −5 %) que pausa los despachos del día.
- **Salud del bot (drift)**, con los umbrales de mtb-analyst: pausar si Profit Factor < 1.0, drawdown > 15 %, 5+ pérdidas seguidas (alerta) o win rate < 35 % en 20+ operaciones; requiere que el Master registre resultados reales (hoy solo hay datos de las cuentas demo del coordinador).
- **Spread** máximo permitido al despachar.

## 9. Etapas de implantación

1. **Sombra:** el Master detecta, empareja y **registra lo que habría ordenado**, sin enviar nada. Sirve para validar reglas y medir latencia sin riesgo.
2. **Demo:** envía órdenes solo a bots en cuentas **demo** (las 5 del VPS y demos de alumnos que lo acepten).
3. **Real:** solo con decisión explícita de Ricardo y la revisión legal (ver Q1). El interruptor de etapa vive en `reglas_master`.

## 10. Sección del panel Master Trader *(propuesta de pantallas; el diseño visual se hace con las skills de diseño tras aprobar el concepto)*

Una vista "Despacho" en la consola admin del Master Trader (no en el panel de alumnos), dividida en cinco bloques:

```
┌ RADAR ─────────────────────────────────────────────────────────────────┐
│ Cuadrícula activo × temporalidad (34 × 6). Celda: sin oportunidad /     │
│ vigente (compra ▲ / venta ▼) / despachada / vencida. Clic = detalle.    │
├ BOTS VIVOS ────────────────────────┬ ÓRDENES EN CURSO ─────────────────┤
│ cuenta · símbolo · TF · modo ·     │ oportunidad → bots → estado        │
│ latido · estado · [Apagar]         │ (entregada/colocada/llenada/…)     │
│ semáforo: vivo / sin latido /      │ + PLAN DE SALIDA de cada una:      │
│ standby / revocado                 │ SL · TP · escalera · cierre forzado │
├ BITÁCORA ──────────────────────────┴───────────────────────────────────┤
│ "XAUUSD 1H ▲ → 3 bots: 2 enviadas, 1 omitida (ya tenía posición)"       │
│ latencia: detección → entrega → colocada (ms)                           │
├ CONTROL ───────────────────────────────────────────────────────────────┤
│ Etapa [Sombra|Demo|Real] · Kill switch · Guardias (valores editables)   │
└─────────────────────────────────────────────────────────────────────────┘
```
Principios de diseño: el estado y los semáforos se entienden sin leer; cada número de latencia es visible; las acciones destructivas (apagar, kill switch) piden confirmación; el panel funciona en claro/oscuro como el resto.

## 11. Fuera de alcance

- Cambiar el motor SMC o sus reglas R1–R6.
- Que el Master calcule lotes por su cuenta.
- IA en la decisión de operar.
- Reescribir el EA (solo se **añade**: latido, pulso, ACK, apagar). El EA actual sigue funcionando solo si el Master no existe.
- Abrir cuentas reales (etapa 3) hasta que Ricardo lo decida.

## 12. Fases de construcción *(propuesta, cada una verificable)*

| Fase | Entrega | Cómo se verifica |
|---|---|---|
| 0 | Medir hoy: latencia real cierre-vela → snapshot, y carga de `/api/setups` | números reales en un informe |
| 1 | Registro de bots: el EA manda latido; tabla `bots_vivos`; vista "Bots vivos" | los 5 bots demo aparecen vivos; matar uno lo marca sin latido |
| 2 | Receptor (A) + tabla `oportunidades` + radar | cada refresco genera eventos; coincide con lo que dice `/api/setups` |
| 3 | Despachador en **Sombra** (C) + bitácora | decisiones registradas, cero órdenes enviadas |
| 4 | Canal (D) + EA nuevo (pulso/ACK) + plan de salida (E) en **Demo** | una oportunidad real llega a un bot demo y se coloca con SL/TP en < 6 s |
| 5 | Guardias de exposición, noticias, drift; apagar bot | pruebas por regla |
| 6 | Etapa Real | solo con aprobación explícita |

Cada fase pasa por previsualización (laboratorio `mtb-api-preview`) antes de producción, y por pruebas automáticas de las reglas (`demo()` como el resto del proyecto).

## 13. Decisiones que necesito de Ricardo

**Q1 (la más importante) — Alcance legal/consentimiento.** Hoy cada alumno corre un software con reglas públicas y decide si lo enciende. Que **un servidor central le ordene abrir** posiciones en la cuenta del alumno se parece más a gestión/señales personalizadas. Propuesta: la etapa Real exige consentimiento explícito del alumno ("permito que el Master Trader envíe órdenes a mi bot") y revisión de abogado; mientras tanto solo cuentas propias y demos. ¿Estás de acuerdo?
**Q2 — Intervalo del pulso.** 3 s (propuesta) vs 5 s vs 1 s. Más rápido = más carga y más peticiones de cada alumno.
**Q3 — Coincidencia de temporalidad.** ¿Estricta (el bot de 1H solo recibe oportunidades de 1H, como pediste) o también aceptar una oportunidad de temporalidad mayor como confirmación de una menor? Propuesta: **estricta** por ahora.
**Q4 — ¿A qué bots?** Solo los 5 demo del VPS al inicio, o también demos de alumnos desde la etapa 2.
**Q5 — Guardias nuevas:** ¿topes de exposición/pérdida diaria/spread como en §8 o prefieres definirlos tú?
**Q6 — Plan de salida discrecional:** ¿el Master puede cerrar posiciones abiertas por noticia, o solo cancelar pendientes y apagar bots?
**Q7 — Apagar:** ¿"apagar" cancela pendientes y deja correr las posiciones abiertas con su plan de salida (propuesta), o las cierra?

## 14. Riesgos que veo

- **Latencia dominada por la publicación de datos**, no por el despachador (§6): sin la Fase B la mejora real es menor de lo que se espera.
- **Carga del servidor** con muchos bots haciendo pulso; se mide en la Fase 0/1.
- **Doble ejecución** si falla el ACK: se mitiga con `orden_id` idempotente y con la regla "una posición a la vez" del EA.
- **Divergencia de precio entre Dukascopy y el broker**: ya se maneja con el ajuste del EA; el Master no debe despachar si ese ajuste excede el límite.
- **El EA hay que reinstalarlo** en cada terminal (nuevo .ex5): afecta a las cuentas demo del VPS y a alumnos; requiere versionar y migrar con cuidado.
- **Cumplimiento** (Q1).
