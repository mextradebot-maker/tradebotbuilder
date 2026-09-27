# Fase 2 — Licencias automáticas para alumnos (multicanal)

**Fecha:** 2026-09-26 · **Estado:** aprobado por Ricardo · **Base:** Fase 1 (cerebro de licencias en `mtb-api`, https://mextradebot.com.mx)

## Objetivo

Que un alumno reciba y use su robot sin que Ricardo intervenga: licencia DEMO al registrarse,
licencia REAL al pagar por **cualquier canal**, revocación automática al cancelar, robot `.ex5`
personalizado con el token adentro. Ricardo solo atiende excepciones desde `/master.html`.

## Decisiones

| Tema | Decisión |
|---|---|
| Amarre a la cuenta MT5 | Automático al primer uso: la licencia nace sin cuenta; la 1ª autorización fija `cuenta` y modo demo/real para siempre. "Liberar cuenta" desde `/master.html`. |
| Identidad del alumno | Correo electrónico, igual en todos los canales. |
| Canales de pago | Multicanal. `mtb-api` solo recibe eventos normalizados; cada canal (Hotmart, Mercado Pago, Stripe, PayPal, manual…) es un workflow n8n que traduce su aviso. |
| Robot | `.ex5` compilado por alumno con `MTB_LICENSE_TOKEN` embebido, en el VPS Windows (MetaEditor), vía `servidor_local.py` (se actualiza con `git pull`). |
| Entrega | Panel "Mis robots" (descarga con sesión del alumno) y Telegram (T-04) con el mismo `.ex5`. |

## Modelo de datos (Postgres `mextradebot`)

- `licencias`: `cuenta` pasa a **nullable** (sin amarrar). Nuevo `correo` (dueño) y `origen` (`registro` | `membresia` | `admin`).
- `membresias` (nueva): `id, correo, canal, referencia, estado (pendiente|activa|cancelada|reembolsada), vigencia_hasta, licencia_id, creada_en, actualizada_en`, único `(canal, referencia)` para que un evento repetido no duplique.
- Evaluación: si `cuenta IS NULL` en la 1ª autorización válida → se fija `cuenta` y, para DEMO/REAL, se valida el modo reportado por el terminal.

## Flujos

1. **Registro** (n8n "Panel Alumnos - Registro") → `POST /api/v1/alumnos/licencia-demo {correo}` (`X-MTB-Service-Key`) → crea DEMO sin amarrar. Si hay membresía `pendiente` con ese correo, también emite la REAL.
2. **Pago en cualquier canal** → workflow n8n del canal → `POST /api/v1/membresias/evento {correo, canal, referencia, evento, vigencia_dias}`:
   - `alta` / `renovacion` → membresía activa; emite REAL (o extiende `expira_en` de la existente).
   - `cancelacion` / `reembolso` → revoca la REAL ligada.
   - Correo sin alumno registrado → membresía `pendiente`.
3. **Manual** → botón "Dar membresía" en `/master.html` → mismo evento con `canal: manual`.
4. **Descarga** → panel "Mis robots" → `GET /api/v1/mi-robot?tipo=demo|real` (sesión del alumno, cookie de n8n validada contra `alumnos.session_token`) → `mtb-api` pide al compilador del VPS Windows (`X-Compilador-Key`) → devuelve `.ex5`. Se cachea por licencia para no recompilar.
5. **Telegram (T-04)** → misma descarga con la clave de servicio en lugar de sesión.
6. **Avisos** a Telegram de Ricardo: licencia emitida, membresía nueva/cancelada, rechazo por `cuenta_no_coincide` (posible robot compartido).

## Seguridad

- Endpoints de alumnos: sesión del panel; endpoints de integración: `X-MTB-Service-Key`; compilador: clave propia, solo alcanzable desde `mtb-api`.
- El token embebido no sirve fuera de la cuenta amarrada. Tokens en BD solo como SHA-256 (como en Fase 1).
- Eventos de pago idempotentes por `(canal, referencia)`.

## Orden de construcción (cada paso queda funcionando solo)

1. Amarre al primer uso + "liberar cuenta" en `/master.html`.
2. Compilador en VPS Windows + descarga en el panel ("Mis robots").
3. Licencia DEMO al registrarse.
4. Endpoint de membresías + botón manual + primer canal de pago.
5. T-04 por Telegram + avisos.

## Fuera de alcance

Cerebro de reglas de operación (opción A, fase siguiente); precisión de ejecución del EA; `www` y T-01.
