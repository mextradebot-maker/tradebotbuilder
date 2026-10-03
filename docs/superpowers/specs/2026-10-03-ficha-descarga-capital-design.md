# Ficha de descarga con Capital

**Fecha:** 2026-10-03 · **Estado:** aprobado por Ricardo (sesión 2-3 oct) · **Base:** Fase 2 licencias (`2026-09-26-fase2-licencias-alumnos-design.md`), EA v1.17, catálogo real (`/api/catalogo`)

## Objetivo

Que toda descarga de robot desde el panel `/` (y desde Telegram) pase por una sola ficha que pide qué operar y con cuánto dinero, muestra con datos reales si la combinación es recomendable y las reglas del Master Trader, y entrega el `.ex5` real con licencia y presets ya grabados. Hoy el panel y Telegram entregan el `.mq5` crudo de GitHub (sin token ni presets), que no opera.

## Decisiones

| Tema | Decisión |
|---|---|
| Combinaciones | Cualquier activo (36) × temporalidad (7). Si `viable=false` o no hay datos: aviso rojo con sus números reales y casilla obligatoria "Entiendo el riesgo". |
| REAL sin membresía | Bloqueo: "Necesitas una membresía activa para operar en real" + botón de compra + "Descargar en DEMO". URL de compra = constante `MTB_URL_MEMBRESIA` (**dato pendiente de Ricardo**); vacía → se muestra "Contacta a MexTradeBot para activar tu membresía" sin botón. |
| Monto a operar | Siempre se pregunta. Mínimo $300, sin máximo. DEMO precargado $10,000 (lo que da XM); REAL vacío. Se graba en el `.ex5` (`InpCapital`). El EA opera sobre el **menor** entre monto y balance (lote, freno -3%, R de Swing). |
| Saldo demo de XM | Fuera de alcance: MQL5 no puede cambiar el balance (XM: "balance management has been disabled - demo account"). No hace falta: el EA usa el menor. |
| SL / TP / lote | Se muestran como **solo lectura** "Definidos por el Master Trader, no editables", con montos calculados sobre el monto del alumno (ver tabla de reglas). Botón "Aceptar y descargar". |
| Presets | Compilados dentro del `.ex5` (camino 1). Timeframe derivado de la temporalidad, nunca lo manda el cliente. |
| Telegram | El bot capta y manda al panel; la web registra y entrega. El bot ya no envía archivos. |

## 1. Experiencia del alumno (panel `/`)

1. Los 4 orígenes (Catálogo en Bots en operación, Activos analizados, "Descarga rápida" de Tarjetas, pestaña Personalizado) abren la misma ficha `abrirFicha(simbolo, temporalidad)` con lo que traían preseleccionado.
2. **Paso 1 — qué operar (editable):** Activo (36, con nombre); Temporalidad (7, cada una con tendencia ↗/↘, acierto, expectativa, setups y veredicto de las filas reales del catálogo; "Sin datos" si no hay muestra); Modo DEMO/REAL; Monto a operar.
3. Si no es recomendable: aviso rojo con sus números reales + casilla "Entiendo el riesgo" (obligatoria).
4. Si la licencia DEMO ya está amarrada a una cuenta: "Tu licencia DEMO está amarrada a la cuenta N; el robot solo operará ahí".
5. **Paso 2 — cómo va a operar (solo lectura)**, con el monto M del alumno:

   | Dato | Valor |
   |---|---|
   | Riesgo por operación | 2% = M×0.02 (Swing S/M: 1% provisional, sin SL fijo) |
   | Stop Loss | El del motor SMC en cada setup |
   | Take Profit | Objetivo del motor (respaldo 2R) |
   | Lotaje | Lo calcula el servidor para no arriesgar más del 2% |
   | Cierre forzado | Si la pérdida llega a 3% = M×0.03 |
   | Protección de ganancia | SL a la entrada en 1R, +0.5R en 1.5R, +1R en 2R; luego trailing por estructura |

   Botón **Aceptar y descargar** → "Generando tu robot…" (hasta 2 min) → descarga.
6. **Pantalla final (texto aprobado):**
   > **Instala tu robot**
   > 1. Copia el archivo a **MQL5\Experts** en el MT5 de tu VPS.
   > 2. En MT5 ve a Herramientas → Opciones → Expert Advisors: permite **WebRequest** a `https://mextradebot.com.mx` y activa **Trading algorítmico**.
   > 3. Ponlo en la gráfica **{simbolo_xm}**, temporalidad **{TF}**.
   > 4. **Verifica** en MexTradeBot_SeguidorSMC (F7 → Entradas) que aparezcan estos datos. Ya vienen cargados; si alguno no coincide, corrígelo: Token (botón Copiar), Símbolo, Temporalidad, Timeframe, Monto a operar.

   Si el símbolo no tiene nombre XM verificado: "Busca {nombre} en XM (Ver → Símbolos) y abre su gráfica".

## 2. Piezas y contrato

### 2.1 EA v1.18 (`robots/MexTradeBot_SeguidorSMC.mq5`)
- Nuevo `input double InpCapital = 0; // Monto a operar (USD); 0 = balance de la cuenta`.
- `CapitalEfectivo()` = `InpCapital > 0 ? MathMin(InpCapital, balance) : balance`. La usan los 3 lugares que hoy leen el balance: `AutorizarOrden` (lo manda a `/api/v1/auth`), `CerrarPorPerdidaMaxima` (-3%) y `MoverBreakeven` (R de Swing).
- `OnInit` imprime el monto efectivo. Se compila y se versiona el `.ex5`.

### 2.2 Compilador (`compilador.py` + `servidor_local.py`, VPS)
- `POST /compilar {"token", "simbolo"?, "temporalidad"?, "capital"?}`. Sin presets = comportamiento actual (solo token).
- Con presets: `simbolo` en la lista cerrada de 36, `temporalidad` en las 7, `capital` numérico ≥ 300. Reemplaza por línea exacta (como el token) `InpSimboloConsulta`, `InpTemporalidad`, `InpTF` (PERIOD_M15/M30/H1/H4/D1/W1/MN1 derivado) e `InpCapital`; cada línea debe existir exactamente una vez o falla. Nada fuera de las listas llega al código fuente (anti-inyección).
- Toda respuesta del compilador nuevo lleva `X-Compilador-Presets: 1`.

### 2.3 API (mtb-api)
- `GET /api/v1/mi-robot?simbolo&temporalidad&modo=demo|real&capital` (cookie `mtb_token`):
  - 401 `sesion` · 400 `{error}` datos inválidos · 402 `membresia_requerida` (REAL sin licencia `real`/`vip` vigente) · 403 `licencia_no_vigente` · 503 `generador_actualizando` (el compilador no respondió `X-Compilador-Presets: 1`) · 502 compilador caído.
  - 200: `.ex5` con `Content-Disposition` `MexTradeBot_{SIMBOLO}_{TF}.ex5` y cabeceras `X-MTB-Token`, `X-MTB-Cuenta` (cuenta amarrada o vacío), `X-MTB-Simbolo-XM`.
  - DEMO usa/crea la licencia DEMO de registro (`asegurar_licencia_demo`).
  - Se conserva `?id=N` (lo usa "Mis robots" de `/alumnos`).
- `GET /api/v1/mis-licencias` incluye por licencia `tipo`, `estado` y `cuenta` (la ficha sabe antes de compilar si hay REAL y si la DEMO está amarrada).
- `/api/catalogo`: cada fila de `activos_todos` incluye `simbolo_xm` (o `null`).
- Tabla de nombres XM: solo valores verificados (pares forex = mismo nombre, XAUUSD→GOLD, WTIUSD→OILCash, BTCUSD, ETHUSD); el resto `null` hasta exportar la lista de símbolos del MT5 del VPS (**pendiente de Ricardo**).
- Telegram:
  - `POST /api/v1/telegram/enlace` (`X-MTB-Service-Key`) `{chat_id, simbolo?, temporalidad?}` → `{url}` = `https://mextradebot.com.mx/#ficha/{SIMBOLO}/{temporalidad}?tg={chat_id}.{exp}.{firma}`; firma = HMAC-SHA256 con clave derivada de `MTB_TOKEN_SECRET`, vigencia 7 días.
  - `POST /api/v1/vincular-telegram` (cookie) `{tg}` → verifica firma y vigencia; guarda `alumnos.telegram_chat_id`. Firma inválida → 400 y la ficha sigue normal.
  - Migración: `ALTER TABLE alumnos ADD COLUMN IF NOT EXISTS telegram_chat_id bigint`.

### 2.4 Front (`public/index.html`, copia idéntica en `index.html`)
- Ficha modal única; los 4 orígenes la abren. La pestaña Personalizado abre la ficha (desaparecen sus campos SL/TP/lotaje editables). Se elimina `descargarArchivo` (webhook n8n `panel-alumnos-robots`).
- Estadísticas por temporalidad desde todas las filas del catálogo (no el arreglo deduplicado).
- Deep link `#ficha/{SIMBOLO}/{temporalidad}?tg=…`: sin sesión, el registro muestra "Crea tu cuenta gratis para descargar tu robot"; al aceptar la sesión se abre la ficha con lo elegido y, si hay `tg`, se llama una vez a `vincular-telegram` (errores se ignoran).

## 3. Telegram (n8n T-04 `1TXBqNpJxe8b5DoF`)
- Las 3 ramas de envío (Rápido, Catálogo, Personalizado: `Descargar EA …` + `Telegram Enviar Robot …`) se reemplazan por: `POST /api/v1/telegram/enlace` → mensaje con botón "Abrir ficha de descarga".
- Exploración (catálogo, tendencias, glosario) se queda igual.
- Niveles: registrado gratis = DEMO; membresía = REAL; sin registro no hay descarga por ningún canal. Un alumno = un correo = un Telegram.
- Hallazgo de seguridad: el nodo "Telegram Enviar Form" tiene el token del bot en texto plano en la URL. Mover a credencial n8n y regenerar con @BotFather (**Ricardo**).

## 4. Errores

| Situación | El alumno ve |
|---|---|
| VPS no compila / tarda | "No pudimos generar tu robot, intenta en unos minutos" (indicador hasta 2 min). Nada se descarga. |
| Compilador sin presets (VPS sin actualizar) | "El generador de robots se está actualizando, intenta más tarde". |
| Sesión vencida | Login; al entrar vuelve a la ficha con lo llenado. |
| REAL sin membresía | Bloqueo de la decisión "REAL sin membresía". |
| Monto < $300 / datos inválidos | Botón deshabilitado; API y compilador revalidan. |
| Catálogo no carga | Ficha funciona; "Sin datos"; se trata como no recomendable. |
| Licencia vencida/revocada | "Tu licencia no está vigente. Escríbenos para revisarla." |
| DEMO amarrada | Aviso con el número de cuenta. |
| Firma de Telegram inválida | Se ignora el `chat_id`; la ficha abre normal. |

## 5. Pruebas
- `demo()` con asserts (estilo del repo): compilador (válidos, inválidos, inyección `XAUUSD"; …`, línea faltante), API `mi-robot` (DEMO, REAL con/sin membresía, monto bajo, sin sesión, compilador viejo → 503), enlace/firma Telegram (válida, alterada, vencida).
- EA compila con 0 errores en MetaEditor.
- Ficha en navegador con respuestas simuladas: recomendable, no recomendable + casilla, REAL bloqueado, monto bajo, error del generador, deep link antes y después de sesión.
- Punta a punta en producción (Ricardo, tras actualizar el VPS): alumno de prueba descarga DEMO, F7 muestra token/símbolo/temporalidad/timeframe/monto y el EA imprime "inicializado".

## Despliegue
1. Push a main + deploy de mtb-api (EasyPanel). La ficha queda visible; descargas responden "generador actualizando" hasta el paso 2.
2. **VPS (Ricardo):** `cd C:\MTB; git pull; Stop-ScheduledTask MTB-ServidorLocal; Start-ScheduledTask MTB-ServidorLocal`.
3. n8n T-04 (con respaldo previo).
4. Las 5 cuentas Master Trader no necesitan cambio (InpCapital = 0 → balance). Si se actualizan a v1.18: copiar el `.ex5` NO recarga el EA; reiniciar terminales `C:\MT5\cuentaN` (nunca la de `C:\Program Files\XMGlobal MetaTrader 5`).

## Pendientes de Ricardo
- URL de compra de membresía.
- Exportar lista de símbolos XM del MT5 del VPS para completar la tabla de nombres.
- Regenerar el token del bot de Telegram y moverlo a credencial n8n.

## Fuera de alcance
- Que el robot cambie el saldo demo de XM.
- Entrega directa del `.ex5` por Telegram.
- Que el backtest simule la escalera.
