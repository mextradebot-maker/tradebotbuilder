# Master Trader — despachador V3 (construido, 29 oct 2026)

Estado: **código en la rama `master-despachador-build` (47bd984) y fusionado en `lab-solo-ricardo` (01fcb10). NO está en main ni en producción.** El interruptor `MASTER_ACTIVO` está apagado por defecto: sin él, nada cambia para los robots de hoy.

## Qué se construyó

| Pieza | Archivo | Qué hace |
|---|---|---|
| Receptor de oportunidades | `refresco.py` (`_avisar_master`) + `master/despachador.py` (`recibir_oportunidades`) | En cada refresco del análisis, si hay setup confirmado, crea una orden por cada robot **activado** de ese activo y temporalidad. Repetir el mismo setup no duplica (`UNIQUE licencia_id, clave`). Solo corre con `MASTER_ACTIVO=1`. |
| Pulso del robot | `api/master.py` `POST /api/v1/master/pulso` | El robot pregunta cada `InpPulsoSeg` s (mínimo 2) con su estado; recibe a lo más UNA orden con lotes ya calculados. Reentrega idempotente por `orden_id` hasta que el robot confirma o expira (5 min). |
| Compuerta de riesgo | `master/riesgo.py` | Reglas de la mejora del 29 oct + protocolo de racha del diseño V2 (abajo). Los lotes siguen saliendo de `conectividad/riesgo.dimensionar` (regla única, máx 2 %). |
| Robot v1.20 | `robots/MexTradeBot_SeguidorSMC.mq5` | `InpModoMaster=false` por defecto = comportamiento idéntico a v1.19. Con `true`: no consulta `/api/setups`, usa `OnTimer` + `WebRequest` al pulso, y sigue gestionando BE, trailing y cierre forzado en cada tick. Compila con 0 errores y 0 advertencias (MetaEditor local). |
| Admin | `GET/POST /api/v1/master/admin` (`X-Admin-Key`) | Estado (robots vivos, órdenes, frenos), `activar`/`desactivar` un robot, `reanudar` una cuenta con freno. |
| Tablas | `persistencia/migraciones.py` | `master_robots`, `master_ordenes`, `master_cuentas` (aditivas, `IF NOT EXISTS`). |

## Cómo entra la mejora del 29 oct (`codigo/mtb_tools`)

Se tomó la idea, no el código (`riesgo.py` usa archivos locales y el paquete MetaTrader5; el despachador vive en el servidor):

| Regla de la mejora | En el despachador |
|---|---|
| Tope de pérdida diaria 3 % | Bloquea entradas del día si el resultado realizado de hoy llega a −3 % del balance (el robot manda `pnl_hoy`). Se reinicia solo al día siguiente. |
| Freno tras 5 pérdidas seguidas | Freno duro (`master_cuentas.halt`): solo una persona lo levanta (`reanudar`). |
| Freno por drawdown 15 % | Igual, medido contra el pico de equity que guarda el servidor. |
| Riesgo abierto total | Suma del riesgo de lo abierto + lo nuevo ≤ 4 %; máx. 5 posiciones. |
| R:R mínimo | Configurable, **apagado** (el motor SMC ya trae su TP). |
| 1 % por trade | **No se adopta**: la regla única del proyecto es 2 % máximo (Ricardo, 29 sep). Queda 2 %, 1 % en Swing S/M. |
| SL obligatorio | No aplica a Swing S/M (por diseño no llevan SL). |
| Racha ×0.5 (diseño V2) | Desde 3 pérdidas seguidas el riesgo de la orden se reduce a la mitad. |

El simulador Monte Carlo (`simulador.py trades`) **no entra al despachador**: es la puerta previa a activar un robot (T-03). Antes de activar un robot nuevo para el Master se le pasa su backtest por ese simulador.

Respuesta a la pendiente 1 de ese checkpoint: el MT5 con los robots corre en el **VPS Windows** (`38.89.76.48`, terminales `C:\MT5\cuenta1..5`), no en la PC. El servidor de la API es Linux (EasyPanel) y no necesita MetaTrader5: los robots mandan sus datos en el pulso. El alias `XAUUSD`↔`GOLD` ya existe en `api/catalogo.SIMBOLO_XM`.

## Lo que NO se construyó (siguiente fase)

1. **Aceptación del alumno por Telegram.** Hoy un robot se activa por admin (`activar`). Falta el flujo "una vez por robot, automático por defecto, asistido opcional" (infra de Telegram ya existe).
2. **Cierre por Master** (expectativa, equity, riesgo). El robot sigue con sus reglas (BE, trailing, −3 % forzado). El Master todavía no ordena cerrar.
3. **Panel "Despacho"** en la SPA (hoy solo `GET /api/v1/master/admin`).
4. **Fase 0** (costos reales, línea base aleatoria, MAE/MFE): sigue pendiente; el muestreo de spreads del VPS corre.
5. Prueba de extremo a extremo con un robot real (ver plan).

## Plan de prueba (laboratorio → una demo)

1. Desplegar `lab-solo-ricardo` en `mtb-api-preview` (EasyPanel). El laboratorio **comparte la BD de producción**; las tablas nuevas son aditivas y con `MASTER_ACTIVO` apagado no entrega nada.
2. Variables del laboratorio para probar: `MASTER_ACTIVO=1`, `MTB_ADMIN_KEY` y `MTB_SERVICE_KEY` **propias del laboratorio** (valores de prueba, no los de producción).
3. Compilar v1.20 en el VPS (`git pull` en `C:\MTB`, MetaEditor), copiar a **una sola** demo (propuesta: `cuenta2`, EUR/USD Scalping 15m), poner `InpModoMaster=true` y `InpMasterUrl` apuntando al laboratorio.
4. `POST /api/v1/master/admin {"accion":"activar", ...}` para esa licencia; esperar un setup confirmado (o inyectarlo con `POST /api/v1/master/oportunidad`); verificar: pulso vivo → orden entregada → orden pendiente en MT5 → `colocada` en `master_ordenes`.
5. Con el OK de Ricardo: merge a main, deploy de producción con `MASTER_ACTIVO` apagado, luego encender.

## Pruebas automáticas

```
python -m master.riesgo        # compuerta
python -m master.despachador   # recepción, entrega, idempotencia, expiración, frenos, lotes
python -m api.master           # endpoints (auth, interruptor, admin)
```
Verificadas junto con `api.licencias`, `compilador`, `refresco` y `conectividad.riesgo` (sin regresiones). Falta ejecutar las migraciones contra Postgres real (primer arranque del laboratorio).
