# Almacén de velas + historial largo — diseño

**Fecha:** 2026-09-29 · **Estado:** aprobado por Ricardo (pendiente 2 del checkpoint 28 sep, después de la Etapa 2).

## Por qué

1. **Lentitud del refresco (medida en producción 29 sep):** un recálculo de Scalping 15m tarda ~45 s y el
   detector solo ~1 s; el resto es volver a descargar de Dukascopy los 60 días completos en cada vela
   (registros: `Start Date 2026-07-31 … End Date 2026-09-29`). CPU del contenedor ~0 % mientras tanto.
2. **Validación estadística:** con las ventanas actuales cada combinación tiene 1–11 operaciones en el
   backtest; hace falta historia de años, que no se puede descargar completa en cada cálculo.

No existe hoy ninguna tabla de velas (revisado en la BD de producción `mextradebot`: solo tablas de la app).

## Decisiones (Ricardo)

Historia a guardar (opción A):

| Temporalidad | Historia del backtest largo |
|---|---|
| 15m, 30m | 3 años |
| 1H | 8 años |
| 4H, D | 15 años |
| S, M | toda la disponible en Dukascopy |

## Diseño

### 1. Series base y derivadas
Se almacenan solo tres series por símbolo, descargadas de Dukascopy:

| Serie guardada | Profundidad | Deriva |
|---|---|---|
| `15m` | 3 años | `30m` (2 velas 15m) |
| `1H` | 15 años (para poder armar 4H de 15 años) | `4H` (4 velas 1H, alineadas a 00/04/08/12/16/20 UTC) |
| `D` | toda la disponible | `S` (semana desde lunes, `W-MON`) y `M` (mes, `MS`), como ya hace `obtener_velas` |

La agregación usa open=primero, high=máx, low=mín, close=último, volume=suma, solo con velas completas del
bloque (una vela derivada incompleta —la de la vela en curso— se marca/descarta igual que hoy la vela en formación).

### 2. Tabla
```sql
CREATE TABLE IF NOT EXISTS velas (
  simbolo text NOT NULL, serie text NOT NULL, ts timestamptz NOT NULL,
  open double precision, high double precision, low double precision, close double precision, volume double precision,
  PRIMARY KEY (simbolo, serie, ts)
);
CREATE TABLE IF NOT EXISTS velas_carga (          -- progreso de la carga histórica (reanudable)
  simbolo text NOT NULL, serie text NOT NULL,
  desde timestamptz,        -- vela más antigua ya guardada
  hasta timestamptz,        -- vela más reciente ya guardada
  completa boolean NOT NULL DEFAULT false,         -- historia objetivo alcanzada
  actualizado_en timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (simbolo, serie)
);
```
Estimado 1–2 GB para 36 símbolos (disco del VPS: 55 GB libres).

### 3. Lectura: `obtener_velas` pasa a leer del almacén
- Nueva función `velas_almacen(simbolo, serie, inicio, fin)` en `conectividad` (o módulo nuevo `almacen`).
- `obtener_velas(simbolo, inicio, fin, intervalo)`: si el almacén tiene la serie base cubriendo `[inicio, fin]`
  → **actualización incremental** (pedir a Dukascopy solo desde `hasta` hasta `fin`, insertar con
  `ON CONFLICT DO UPDATE` —la última vela puede corregirse—), luego leer del almacén y agregar si hace falta.
  Si no la cubre (carga histórica aún en curso) → comportamiento actual (descarga directa), sin romper nada.
- La API, el refresco, el backtest y n8n no cambian su contrato.

### 4. Carga histórica en segundo plano
- Hilo en `app.py` (como el refresco) con prioridad baja, apagable con `ALMACEN_CARGA_ACTIVA=0`.
- Por símbolo y serie base, descarga hacia atrás en bloques (15m: 1 mes; 1H: 6 meses; D: 5 años), guardando
  después de cada bloque y actualizando `velas_carga` → reanudable tras reinicios.
- Cede el paso al refresco (no corre un bloque mientras el refresco tiene pendientes de Scalping/Intraday).
- Se detiene por serie al llegar a la profundidad objetivo o cuando Dukascopy ya no devuelve datos (inicio de la historia).

### 5. Backtest largo semanal
- Nuevo cálculo `backtest_largo(simbolo, temporalidad)`: lee del almacén la historia objetivo de la tabla de
  arriba, corre `detectar_setups_v2` + `backtest_v2`, y guarda el resultado en una tabla
  `backtest_largo(simbolo, temporalidad, calculado_en, desde, hasta, resultado jsonb)`.
- Se recalcula **una vez por semana** (domingo, mercado cerrado) en segundo plano, solo para combinaciones con
  la historia completa en el almacén.
- `/api/setups` sigue sirviendo `backtests` de la ventana corta del snapshot y agrega `backtest_largo` (leído de
  la tabla; `null` si aún no existe). Informativo: no cambia qué se confirma (decisión vigente: confirmar = reglas).
- `/api/backtest` y el Análisis Diario de n8n: usar el backtest largo cuando exista (más estadística), si no el corto.

## Fuera de alcance
Optimización del stop (pendiente 3, después de tener el backtest largo). Cambios de reglas.

## Pruebas
- Agregación 15m→30m, 1H→4H, D→S/M contra `obtener_velas` directo de Dukascopy (mismas velas).
- Incremental: con almacén parcial, solo se piden las velas nuevas; resultado idéntico a la descarga directa.
- Carga reanudable: interrumpir y reanudar no duplica ni deja huecos (PK + `velas_carga`).
- Validación en Postgres 17 local (clúster temporal) antes de desplegar.
- Medir en producción el tiempo de un refresco de Scalping antes/después.
