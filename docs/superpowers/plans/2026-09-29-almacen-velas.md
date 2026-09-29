# Almacén de velas + historial largo — plan

> Ejecutar con superpowers:subagent-driven-development. Spec: `docs/superpowers/specs/2026-09-29-almacen-velas-historial-largo-design.md`.

## Global Constraints
- Repo `C:\Proyectos\MexTradeBot\tradebotbuilder`, rama `feat/almacen-velas` (desde `main`). No push hasta A5.
- Comandos: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m <modulo>` desde la raíz (Git Bash). Tests = `demo()` con asserts, RED → GREEN.
- Postgres de prueba: clúster temporal con `"/c/Program Files/PostgreSQL/17/bin/initdb.exe"` + `pg_ctl` en puerto 55432
  dentro de `.superpowers/sdd/pgtest` (usuario `postgres`, auth trust), `DATABASE_URL=postgresql://postgres@localhost:55432/postgres`.
  Apagarlo al terminar (`pg_ctl stop`). Importar `persistencia` corre las migraciones.
- Series base guardadas: `15m`, `1H`, `D`. Derivadas: `30m`←15m, `4H`←1H, `S`/`M`←D. Profundidad objetivo de carga:
  15m 3 años, 1H 15 años, D toda la historia. Ventanas del backtest largo por temporalidad: 15m/30m 3 años, 1H 8 años, 4H/D 15 años, S/M toda.
- Contratos que no cambian: respuesta de `/api/setups` (`setups_confirmados` última llave, `_forma_ea`), `obtener_velas` misma firma y mismo resultado.
- Commits en español terminando con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### A1 — Tablas y módulo `almacen`
Migraciones idempotentes para `velas` y `velas_carga` (SQL del spec §2) en `persistencia/migraciones.py`.
Módulo nuevo `conectividad/almacen.py`: `guardar(simbolo, serie, df)` (upsert por lotes, `ON CONFLICT DO UPDATE`),
`leer(simbolo, serie, inicio, fin) -> DataFrame` (mismas columnas/índice UTC que `obtener_velas`),
`agregar(df_base, serie_destino) -> DataFrame` (30m, 4H, S=`W-MON`, M=`MS`; solo bloques completos salvo el último, marcado igual que hoy),
`rango(simbolo, serie) -> (desde, hasta) | None`. Tests: agregación contra velas de Dukascopy directas (30m, 4H) y
contra la agregación actual de `obtener_velas` (S, M); guardar/leer ida y vuelta en Postgres local; upsert corrige la última vela.

### A2 — `obtener_velas` incremental
En `conectividad/historico.py::obtener_velas`: mapear el intervalo pedido a su serie base; si `velas_carga` cubre
`inicio`, pedir a Dukascopy solo `(hasta - 1 vela, fin]`, guardar, leer del almacén `[inicio, fin]` y agregar si hace falta.
Si no cubre `inicio` o no hay BD → comportamiento actual. Test de equivalencia: para XAUUSD 15m/30m/1H/4H/D/S/M en una
ventana fija, la salida con almacén == salida directa (tolerancia 0 en OHLC). Medir el tiempo de la segunda llamada.

### A3 — Carga histórica en segundo plano
Módulo `carga_historica.py`: por símbolo del catálogo activo y serie base, descarga hacia atrás en bloques
(15m: 1 mes; 1H: 6 meses; D: 5 años), guarda, actualiza `velas_carga`; marca `completa` al llegar a la profundidad
objetivo o cuando Dukascopy devuelve vacío. Hilo en `app.py` apagable con `ALMACEN_CARGA_ACTIVA=0`; no corre un
bloque si `refresco` tiene pendientes de 15m/30m/1H (exponer una función en refresco para consultarlo) y duerme entre
bloques. Tests offline con Dukascopy simulado: reanudable sin duplicados ni huecos; se detiene en vacío/objetivo.

### A4 — Backtest largo semanal
Tabla `backtest_largo(simbolo, temporalidad, calculado_en, desde, hasta, resultado jsonb, PK(simbolo, temporalidad))`.
Función que lee del almacén la ventana larga de la temporalidad, corre `detectar_setups_v2` + `backtest_v2` y guarda
`backtest_v2(...)` completo (+ `embudo`). Programación: domingo (mercado cerrado) en segundo plano, solo combinaciones
activas cuya serie base esté `completa`. `/api/setups` agrega `backtest_largo` (antes de `setups_confirmados`, que sigue
siendo la última llave) y `/api/backtest` devuelve el largo si existe (misma forma `compra/venta` del `total`) con
`"fuente": "largo" | "corto"`. Tests offline con BD local.

### A5 — Despliegue
Revisión final de rama, fusión, push, deploy (POST `EASYPANEL_DEPLOY_MTB_API`), verificar tablas creadas, medir refresco
Scalping antes/después, vigilar el avance de la carga (`velas_carga`) y la RAM del contenedor.
