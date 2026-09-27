# Fase 2 · Paso 2 — Robot personalizado (.ex5 con token embebido) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desde `/master.html` descargar, para cualquier licencia nueva, un `.ex5` compilado con su `MTB_LICENSE_TOKEN` ya puesto.

**Architecture:** Tokens derivados: `token = f(HMAC-SHA256(MTB_TOKEN_SECRET, semilla))`, con `semilla` aleatoria por licencia guardada en BD (la BD sigue sin tokens en claro). `mtb-api` regenera el token y lo manda al compilador del VPS Windows (`servidor_local.py` → `compilador.py`, MetaEditor por línea de comandos) a través del túnel `https://demo-status.mextradebot.com.mx/compilar`, protegido con `X-Compilador-Key`. El panel descarga el binario con la clave de admin.

**Tech Stack:** Python 3.12 stdlib (hmac, hashlib, subprocess, tempfile), MetaEditor64 (XM Global MT5), Cloudflare Tunnel existente, EasyPanel `mtb-api`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-26-fase2-licencias-alumnos-design.md`. Ajuste: la descarga del alumno ("Mis robots") se construye en el Paso 3; aquí solo descarga admin.
- Falla cerrado: sin `MTB_TOKEN_SECRET` no se emite; sin `COMPILADOR_KEY` el compilador responde 503.
- Llamadas HTTP de Python a dominios detrás de Cloudflare llevan `User-Agent` propio (Cloudflare bloquea `Python-urllib`, error 1010).
- Tokens `MTB-XXXXX-XXXXX-XXXXX-XXXXX`, alfabeto `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`; el compilador rechaza cualquier otro formato (evita inyección en el `.mq5`).
- Pruebas: `demo()` con `assert` por módulo (`python -m <modulo>`).

---

### Task 1: Tokens derivados (semilla + secreto)

**Files:** Modify `persistencia/licencias.py`, `persistencia/migraciones.py`.

**Interfaces — Produces:** `derivar_token(semilla: str, secreto: str) -> str`; `token_de_licencia(licencia_id: int) -> str`; `emitir()` guarda `semilla`; columna `licencias.semilla text`.

- [ ] Test en `demo()`: `derivar_token("ab"*16, "s")` es determinista, tiene formato `^MTB-([A-Z2-9]{5}-){3}[A-Z2-9]{5}$` sin `0/O/1/I`, y cambia con otra semilla o secreto.
- [ ] Implementar: `derivar_token` toma los primeros 100 bits del HMAC en 20 grupos de 5 bits → `_ALFABETO`. `emitir`: `semilla = secrets.token_hex(16)`, `token = derivar_token(semilla, _secreto())`, INSERT incluye `semilla`. `_secreto()` lee `MTB_TOKEN_SECRET` o lanza `ValueError("MTB_TOKEN_SECRET no configurado")`. `token_de_licencia` lee `semilla` (si es NULL: `ValueError("licencia sin semilla (anterior a Paso 2): emite una nueva")`). Migración: `ALTER TABLE licencias ADD COLUMN IF NOT EXISTS semilla text`.
- [ ] `python -m` del demo aislado → OK. Commit.

### Task 2: Compilador (Windows)

**Files:** Create `compilador.py`; Modify `servidor_local.py` (`do_POST` + respuesta binaria).

**Interfaces — Produces:** `POST /compilar` con `X-Compilador-Key` y `{"token": "..."}` → `200 application/octet-stream` (.ex5) | 400 token inválido | 401 clave | 503 sin `COMPILADOR_KEY` | 500 error de compilación.

- [ ] Tests en `compilador.demo()`: `preparar_fuente` sustituye exactamente la línea `input string MTB_LICENSE_TOKEN   = "";`; rechaza tokens con comillas/formato inválido; `procesar_http` sin env → 503, clave mala → 401. Si hay MetaEditor en la máquina: compilación real → `.ex5` > 10 KB.
- [ ] Implementar `metaeditor()` (env `METAEDITOR_PATH` o `C:\Program Files\*\MetaEditor64.exe`), `compilar(token)` en `tempfile.TemporaryDirectory`, log UTF-16 debe contener `0 errors`, `threading.Lock`.
- [ ] `servidor_local.py`: `do_POST` → ruta `/compilar` → `compilador.procesar_http(dict(self.headers), cuerpo)`.
- [ ] Demo OK en esta PC (tiene MetaEditor). Commit.

### Task 3: Descarga admin en mtb-api + panel

**Files:** Modify `api/licencias.py` (`robot_de_licencia`), `api/analizar.py` (ruta binaria `GET /api/v1/licencias/robot?id=N`), `public/master.html` (botón "Robot").

**Interfaces — Consumes:** `token_de_licencia` (Task 1), `POST {COMPILADOR_URL}` (Task 2). **Produces:** `robot_de_licencia(licencia_id) -> bytes`.

- [ ] Test en `api.licencias.demo()` con compilador y `token_de_licencia` falsos: devuelve los bytes; sin `COMPILADOR_URL` → error claro.
- [ ] Implementar; el router responde `application/octet-stream` con `Content-Disposition: attachment; filename="MexTradeBot_<prefijo>.ex5"`; errores en JSON.
- [ ] Panel: botón "Robot" en licencias activas → `fetch` con `X-Admin-Key` → blob → descarga. Verificar en navegador con servidor de prueba. Commit.

### Task 4: Despliegue

- [ ] Generar `MTB_TOKEN_SECRET` y `COMPILADOR_KEY` (se agregan al archivo de claves privado). EasyPanel `mtb-api` → Entorno: `MTB_TOKEN_SECRET`, `COMPILADOR_KEY`, `COMPILADOR_URL=https://demo-status.mextradebot.com.mx/compilar` (portapapeles, sin pasar por el chat).
- [ ] Push `main` → deploy (URL de activación de EasyPanel).
- [ ] VPS Windows (Ricardo): `git pull`, agregar `COMPILADOR_KEY` al `.env` con PowerShell, reiniciar la tarea `MTB-ServidorLocal`.
- [ ] Prueba: emitir licencia de prueba → "Robot" → `.ex5` descargado (> 10 KB) → revocar licencia de prueba.
