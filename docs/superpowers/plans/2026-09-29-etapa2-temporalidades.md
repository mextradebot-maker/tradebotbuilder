# Etapa 2 — Migración de temporalidades (plan)

> Ejecutar con superpowers:subagent-driven-development, una tarea por agente, revisión después de cada una.

**Spec:** `docs/superpowers/specs/2026-09-28-motor-smc-v2-design.md` (sección Etapa 2) + decisiones del 29 sep abajo.

## Decisiones de Ricardo (29 sep 2026)

- **Opción A de identificación:** un solo nombre por combinación, con la vela incluida, en la columna `temporalidad` que ya existe (sin cambio de estructura en BD):

| Nombre canónico | Vela | Mayor | Días | Swing length | Recalcula | Solo compras | Riesgo |
|---|---|---|---|---|---|---|---|
| `Scalping 15m` | 15m | 1H | 60 | 8 | cierre de cada vela 15m | no | SL clásico |
| `Scalping 30m` | 30m | 1H | 120 | 8 | cierre de cada vela 30m | no | SL clásico |
| `Intraday 1H` | 1H | D | 365 | 10 | cierre de cada vela 1H | no | SL clásico |
| `Intraday 4H` | 4H | D | 365 | 10 | cierre de cada vela 4H | no | SL clásico |
| `Intraday D` | D | S | 1095 | 10 | diario 08:00 México (14:00 UTC) | no | SL clásico |
| `Swing (S)` | S | M | 1095 | 5 | diario 08:00 México | sí | Swing (sin SL, CHoCH, máx 2%) |
| `Swing (M)` | M | M | 2555 | 5 | diario 08:00 México | sí | Swing |

- **Alias** (se resuelven a la entrada de toda API y del EA; nunca se guardan): `Scalping`→`Scalping 15m`, `Intraday`→`Intraday 1H`, `Swing (H)`→`Intraday 4H`, `Swing`→`Intraday 4H`.
- Swing = solo `Swing (S)` y `Swing (M)` (riesgo y solo-compras).
- `Scalping 30m` entra al catálogo con `activo = false` hasta medir en producción, después de la optimización (T2), que cabe en la CPU.
- mtb-api pasa a 1 CPU / 1.5 GB (lo cambia Ricardo en EasyPanel). `REFRESCO_WORKERS` sigue en 1.
- n8n "MTB Analisis Diario de Mercado" pasa a 09:00, 13:00 y 00:00 hora de México.
- Master Trader: Scalping→`Scalping 15m`, Intraday→`Intraday 1H`, Swing (S) sin cambio.

## Global Constraints

- Repo `C:\Proyectos\MexTradeBot\tradebotbuilder`, rama `feat/etapa2-temporalidades` (crear desde `main`). No push hasta la tarea de despliegue.
- Comandos: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m <modulo>` desde la raíz (Git Bash). Pruebas = `demo()` con asserts, RED → GREEN.
- Contrato del EA: `setups_confirmados` última llave; `_forma_ea` en todo return con temporalidad.
- Commits en español, terminan con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### T1 — Mapa único de temporalidades + alias
**Archivos:** `conectividad/historico.py` (fuente única), `conectividad/__init__.py`, `api/setups.py`, `api/tendencia.py`, `api/backtest.py`, `api/mejor_indicador.py`, `api/reporte.py`, `conectividad/riesgo.py`, `backtesting/embudo_v2.py`, `backtesting/calibrar_swing_length.py`.
- En `conectividad/historico.py`: `TEMPORALIDADES: dict[str, dict]` con las llaves `vela, vela_mayor, intervalo (dukascopy), dias, swing_length, diaria (bool), solo_compras (bool), es_swing (bool)` para los 7 nombres de la tabla; `ALIAS_TEMPORALIDAD`; `resolver_temporalidad(nombre) -> str` (canónico; `ValueError` si no existe). Derivar de ahí, para compatibilidad: `TEMPORALIDAD_A_INTERVALO` (canónicos + alias), `TIPOS_SOLO_ALCISTA = {"Swing (S)", "Swing (M)"}`.
- `api/setups.py`: `DIAS_POR_TEMPORALIDAD`, `SWING_LENGTH_POR_TEMPORALIDAD`, `PERFIL_A_VELAS_V2`, `VELA_A_INTERVALO` se derivan del mapa (no duplicar valores). `procesar` resuelve el alias al entrar y responde con el nombre canónico (`"temporalidad"`) y guarda el snapshot bajo el canónico.
- `api/tendencia.py`, `api/backtest.py`, `api/mejor_indicador.py`: resolver alias al entrar; listas de valores válidos = los 7 canónicos (+ alias aceptados).
- `conectividad/riesgo.py`: `TEMPORALIDADES_SWING = {"Swing (S)", "Swing (M)"}`; `es_swing` resuelve alias.
- `backtesting/embudo_v2.py`: `PERFILES` = los 7 canónicos (30m incluido).
- Tests: `resolver_temporalidad` para los 7 + 4 alias + uno inválido; `procesar` offline (monkeypatch de `obtener_velas`/`motor_v2`) con `temporalidad="Swing (H)"` responde `"Intraday 4H"`; `riesgo.es_swing("Swing (H)") is False`, `es_swing("Swing (S)") is True`; demos existentes siguen pasando.

### T2 — Optimización del detector (obligatoria antes de activar 30m)
**Archivos:** `motor_smc/setups_v2.py`, `motor_smc/reglas.py`.
- Medición de referencia en local: `detectar_setups_v2` para XAUUSD `Scalping 15m` e `Intraday 1H` (fechas fijas 2026-07-01 a 2026-09-25), tiempo y salida guardada (pickle en `.superpowers/sdd/`).
- Objetivo: bajar el tiempo del detector al menos a la mitad. El costo dominante es R4/R5 corriendo `smc.swing_highs_lows` + `smc.bos_choch` sobre la temporalidad mayor recortada, casi un recorte distinto por candidato.
- Estrategia permitida: limitar el recorte de la temporalidad mayor a una ventana fija de las últimas N velas cerradas (N constante, p. ej. 300) para R4 y R5, en vez de toda la historia; u otra que mantenga la anti-anticipación (solo velas mayores cerradas antes de `indice_conocido`).
- **Equivalencia:** comparar la salida contra la referencia. Si hay diferencias, listar exactamente qué candidatos cambian de `valido` y por qué regla, y reportarlo como DONE_WITH_CONCERNS (Ricardo decide). Si es idéntica, DONE.
- Reportar tiempos antes/después.

### T3 — Refresco por vela, diario 08:00 México y tope de tiempo por ciclo
**Archivos:** `refresco.py` (y `poblar_snapshots.py` si usa nombres viejos).
- `inicio_vela` por vela (15m, 30m, 1H, 4H) leyendo el mapa; los de `diaria=True` se recalculan una vez al día a las **14:00 UTC** (08:00 México; México no tiene horario de verano desde 2022). Constante `HORA_REFRESCO_DIARIO = 14` con comentario.
- `PRIORIDAD` = orden por vela: 15m, 30m, 1H, 4H, D, S, M.
- **Tope de tiempo por ciclo:** constante `PRESUPUESTO_CICLO_S = 300`. El ciclo procesa pendientes en orden de prioridad y se detiene al pasar el presupuesto (no interrumpe un cálculo en curso); lo pendiente queda para el siguiente ciclo, donde lo nuevo de mayor prioridad pasa al frente.
- Solo combinaciones con `activo = true` en el catálogo (ya es así).
- Tests (offline): `inicio_vela` para cada vela; `necesita_refresco` diario antes/después de las 14:00 UTC; el ciclo respeta prioridad y presupuesto (con `procesar` sustituido por una función que duerme poco y un presupuesto pequeño).

### T4 — Migración de datos en Postgres (idempotente, al arrancar)
**Archivos:** `persistencia/migraciones.py`.
- Agregar sentencias idempotentes al final de `_SQL`:
  1. `catalogo_activos`: para cada símbolo con filas viejas, insertar las 7 canónicas (`ON CONFLICT DO NOTHING`), heredando `fecha_inicio` de la fila vieja equivalente (Scalping→Scalping 15m y 30m; Intraday→Intraday 1H y D; Swing (H)→Intraday 4H; Swing (S)/(M) iguales; si no hay equivalente, la mínima del símbolo). `Scalping 30m` con `activo = false`. Luego borrar las filas con nombres viejos (`Scalping`, `Intraday`, `Swing (H)`).
  2. `cuentas_demo`: `UPDATE ... SET temporalidad = 'Scalping 15m' WHERE temporalidad = 'Scalping'`; ídem `Intraday`→`Intraday 1H`, `Swing (H)`→`Intraday 4H`.
  3. `smc_snapshot`: borrar filas con nombres viejos (`Scalping`, `Intraday`, `Swing (H)`, `Swing`, `''`).
  4. `historico_tendencias`, `posiciones_abiertas`, `historial_posiciones`, `log_coordinador`: NO se tocan (histórico).
- Verificar que `aplicar()` es seguro de correr repetidamente (segunda corrida no cambia nada). Test: si hay forma offline, validar el SQL con un Postgres local no disponible → al menos revisar que cada sentencia sea idempotente y documentarlo; reportarlo.

### T5 — EA
**Archivos:** `robots/MexTradeBot_SeguidorSMC.mq5`.
- `InpTemporalidad` default `"Intraday 1H"`, comentario con los 7 nombres (los viejos siguen aceptados por la API).
- Al iniciar (`OnInit`): si la temporalidad (canónica o alias) no corresponde a `InpTF` (Scalping 15m↔M15, Scalping 30m↔M30, Intraday 1H↔H1, Intraday 4H↔H4, Intraday D↔D1, Swing (S)↔W1, Swing (M)↔MN1; alias: Scalping→M15, Intraday→H1, Swing (H)/Swing→H4), imprimir el error y devolver `INIT_PARAMETERS_INCORRECT`.
- Compilar en la PC: `"/c/Program Files/XM Global MT5/MetaEditor64.exe" "/compile:<ruta win>" "/log:<ruta win>"` sobre una copia en `.superpowers/sdd/ea/`; log UTF-16; exigir `0 errors`.

### T6 — Páginas (catálogo y panel de alumnos)
**Archivos:** `index.html`, `public/index.html`, `plantillas/panel-alumnos.html`.
- Selectores de temporalidad: los 7 nombres canónicos (default `Intraday 1H`).
- `index.html` ~L2251 `periods` y ~L2269 detección por texto: mapear a los canónicos (H4 → `Intraday 4H`, D1 → `Intraday D`, M30 → `Scalping 30m`).
- Datos de ejemplo (`DEFAULT_CATALOGO_DATA`, demo del panel): cambiar `Swing (H)`→`Intraday 4H`, `Scalping`→`Scalping 15m`, `Intraday`→`Intraday 1H`.
- Verificar en el navegador integrado (abrir el archivo local o servir con `python -m http.server`) que los selectores muestran las 7 opciones sin errores de consola.

### T7 — n8n
- **MTB Analisis Diario de Mercado** (`Vm8jfl3DblDjjy31`): nodo "Expandir Temporalidades" → los 7 canónicos con sus días (60, 120, 365, 365, 1095, 1095, 2555); nodo "Consultar Backtest por Temporalidad" → mismo mapa de días; lógica "esSwing" → solo `Swing (S)`/`Swing (M)`; cron a 09:00, 13:00 y 00:00 **hora de México** (revisar la zona horaria del workflow/instancia y ajustar la expresión para que sea México).
- **T-04** (`1TXBqNpJxe8b5DoF`) y **T-01** (`RQIWLyZAhUBtr55m`): revisar referencias a los nombres viejos y actualizarlas a los canónicos; los `callback_data` viejos siguen funcionando por alias en la API.
- Usar el MCP de n8n (leer SDK/tipos antes de editar). Guardar como borrador, validar, publicar solo si valida. Reportar cada cambio.

### T8 — Despliegue y verificación
- Revisión final de la rama; fusión a `main`; push; despliegue con POST a `EASYPANEL_DEPLOY_MTB_API` (archivo de claves).
- Verificar: `/api/setups` con cada nombre canónico y cada alias (con `X-MTB-Service-Key`); catálogo = 252 filas (30m inactivas); tiempo real del refresco a la hora en punto; reporte T-01.
- Activar `Scalping 30m` solo si el tiempo medido cabe (Ricardo decide).
- `git pull` en el VPS Windows (comando para Ricardo) para que `/compilar` use el EA nuevo.
