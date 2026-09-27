# Fase 2 · Paso 1 — Amarre al primer uso + "liberar cuenta" · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Una licencia puede nacer sin cuenta MT5 y se amarra sola a la cuenta del primer robot que se autoriza; el admin puede liberarla desde `/master.html`.

**Architecture:** `licencias.cuenta` pasa a nullable. La decisión pura `evaluar()` acepta licencias sin amarrar; `verificar()` fija la cuenta con un `UPDATE … WHERE cuenta IS NULL` (seguro ante dos robots simultáneos). Nueva acción admin `liberar`. El panel permite emitir sin cuenta y muestra "sin amarrar" / botón "Liberar cuenta".

**Tech Stack:** Python 3.12, psycopg 3, Postgres (servicio `chatbotventas_mextradebot-db`), HTML/JS vanilla en `public/master.html`. Pruebas con el patrón del repo: `demo()` con `assert` en cada módulo (`python -m <modulo>`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-26-fase2-licencias-alumnos-design.md`.
- Tokens solo como SHA-256 en BD; todo falla cerrado.
- 1 token = 1 cuenta MT5 una vez amarrada; DEMO solo en demo, REAL solo en real, VIP ambas.
- Despliegue: push a `main` → "Implementar" en EasyPanel `chatbotventas/mtb-api` (las migraciones corren al primer uso de `persistencia`).
- Comandos desde `C:\Proyectos\MexTradeBot\tradebotbuilder` con `PYTHONUTF8=1` y `.venv/Scripts/python.exe`.

---

### Task 1: Licencias sin amarrar en BD y en la decisión

**Files:**
- Modify: `persistencia/migraciones.py` (lista `_SQL`, al final del Bloque 7)
- Modify: `persistencia/licencias.py` (`evaluar`, `verificar`, `emitir`, nueva `liberar`, `demo`)

**Interfaces:**
- Produces: `evaluar(lic, cuenta, modo, robot, kill_switch, ahora) -> (bool, str)` — acepta `lic["cuenta"] is None`; `verificar(...)` igual firma, amarra; `emitir(cliente, cuenta: int | None, tipo, robot, vigencia_dias)`; `liberar(licencia_id: int) -> None`.

- [ ] **Step 1: Tests que fallan en `demo()`** — agregar al final de `persistencia/licencias.py::demo()`, antes del `print`:

```python
    libre = {**base, "cuenta": None}
    assert evaluar(libre, 318680674, "real", "seguidor-smc", False, ahora) == (True, "ok")  # se amarra
    assert evaluar({**libre, "tipo": "demo"}, 318680674, "real", "seguidor-smc", False, ahora)[1] == "licencia_demo_en_cuenta_real"
    assert evaluar({**libre, "revocada_en": ahora}, 1, "real", "seguidor-smc", False, ahora)[1] == "revocada"
```

- [ ] **Step 2: Verificar que falla**

Run: `.venv/Scripts/python.exe -c "import importlib.util,sys,types; p=types.ModuleType('persistencia'); p.__path__=['persistencia']; sys.modules['persistencia']=p; s=importlib.util.spec_from_file_location('persistencia.licencias','persistencia/licencias.py'); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); m.demo()"`
Expected: `AssertionError` (hoy devuelve `cuenta_no_coincide`).

- [ ] **Step 3: Implementación**

En `evaluar`, reemplazar:
```python
    if lic["cuenta"] != cuenta:
        return False, "cuenta_no_coincide"
```
por:
```python
    if lic["cuenta"] is not None and lic["cuenta"] != cuenta:
        return False, "cuenta_no_coincide"  # None = sin amarrar: verificar() la fija a esta cuenta
```

En `verificar`, después de `ok, motivo = evaluar(...)` y antes del `if lic:`:
```python
        if ok and lic["cuenta"] is None:
            amarrada = conn.execute(
                "UPDATE licencias SET cuenta = %s WHERE id = %s AND cuenta IS NULL", (cuenta, lic["id"])
            ).rowcount
            if amarrada:
                lic["cuenta"] = cuenta
                _evento_admin(conn, lic["id"], cuenta, "amarrada_primer_uso")
            else:  # otro robot la amarró en el mismo instante: re-evaluar contra la cuenta ya fijada
                lic["cuenta"] = conn.execute("SELECT cuenta FROM licencias WHERE id = %s", (lic["id"],)).fetchone()[0]
                ok, motivo = evaluar(lic, cuenta, modo, robot, False, ahora)
```

En `emitir`, cambiar firma y validación:
```python
def emitir(cliente: str, cuenta: int | None, tipo: str, robot: str, vigencia_dias: int | None) -> tuple[str, dict]:
    """Crea la licencia. cuenta=None → se amarra al primer robot que la use. Devuelve (token_en_claro, licencia)."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo debe ser uno de {sorted(TIPOS)}")
    if not cliente.strip() or (cuenta is not None and cuenta <= 0):
        raise ValueError("cliente obligatorio y cuenta, si se da, debe ser positiva")
```

Nueva función después de `reautorizar`:
```python
def liberar(licencia_id: int) -> None:
    """Quita el amarre: el próximo robot que se autorice con este token la vuelve a amarrar."""
    with get_conn() as conn:
        conn.execute("UPDATE licencias SET cuenta = NULL WHERE id = %s", (licencia_id,))
        _evento_admin(conn, licencia_id, None, "cuenta_liberada")
```

En `persistencia/migraciones.py`, agregar al final de `_SQL`:
```python
    # ── Fase 2: licencias sin amarrar (se amarran al primer uso) ──────────
    """
    ALTER TABLE licencias ALTER COLUMN cuenta DROP NOT NULL
    """,
```

- [ ] **Step 4: Verificar que pasa** — mismo comando del Step 2. Expected: `licencias.demo() OK`.

- [ ] **Step 5: Commit**
```bash
git add persistencia/licencias.py persistencia/migraciones.py
git commit -m "Licencias sin cuenta: se amarran al primer uso; liberar()"
```

### Task 2: Acción admin `liberar` y emisión sin cuenta en la API

**Files:**
- Modify: `api/licencias.py` (`procesar_admin`, docstring, `demo`)

**Interfaces:**
- Consumes: `licencias.emitir(cliente, cuenta|None, …)`, `licencias.liberar(id)` (Task 1).
- Produces: `POST /api/v1/licencias {"accion":"liberar","id":N}`; `{"accion":"emitir", "cuenta": null|""|N, …}`.

- [ ] **Step 1: Test que falla** — en `api/licencias.py::demo()`, dentro del `try`, después de los asserts de admin con clave inválida:

```python
        import types as _t
        llamadas = []
        falso = _t.SimpleNamespace(
            liberar=lambda i: llamadas.append(("liberar", i)),
            emitir=lambda c, cu, t, r, d: (llamadas.append(("emitir", cu)) or ("MTB-T", {"id": 1})),
        )
        import sys as _s
        _s.modules["persistencia"] = _t.SimpleNamespace(licencias=falso)
        _s.modules["persistencia.licencias"] = falso
        h = {"X-Admin-Key": "adm"}
        assert procesar_admin("POST", {"accion": "liberar", "id": 7}, h) == (200, {"ok": True})
        assert procesar_admin("POST", {"accion": "emitir", "cliente": "x", "cuenta": "", "tipo": "demo", "robot": "r"}, h)[0] == 200
        assert llamadas == [("liberar", 7), ("emitir", None)], llamadas
        _s.modules.pop("persistencia"); _s.modules.pop("persistencia.licencias")
```

- [ ] **Step 2: Verificar que falla** — Run: `.venv/Scripts/python.exe -m api.licencias` → Expected: falla (`accion debe ser …` o `int('')`).

- [ ] **Step 3: Implementación** — en `procesar_admin`, reemplazar el bloque `emitir` y agregar `liberar`:

```python
        if accion == "emitir":
            cuenta = int(datos["cuenta"]) if str(datos.get("cuenta") or "").strip() else None
            token, lic = licencias.emitir(str(datos.get("cliente", "")), cuenta,
                                          str(datos.get("tipo", "")).lower(), str(datos.get("robot", "")), dias)
            return 200, {"token": token, "licencia": lic,
                         "aviso": "Guarda el token ahora: no se puede volver a mostrar."}
        if accion == "liberar":
            licencias.liberar(int(datos["id"]))
            return 200, {"ok": True}
```
y el mensaje final: `"accion debe ser emitir | revocar | reautorizar | liberar | kill_switch"`. En el docstring del módulo agregar la línea `{"accion": "liberar", "id"}` y `"cuenta"` opcional en `emitir`.

- [ ] **Step 4: Verificar que pasa** — Run: `.venv/Scripts/python.exe -m api.licencias` → `api.licencias.demo() OK`.

- [ ] **Step 5: Commit**
```bash
git add api/licencias.py
git commit -m "API admin: emitir sin cuenta y accion liberar"
```

### Task 3: Panel `/master.html` — emitir sin cuenta y "Liberar cuenta"

**Files:**
- Modify: `public/master.html` (formulario de emisión, `mtbRenderTabla`, `mtbBindEventos`)

**Interfaces:**
- Consumes: acciones `emitir` (cuenta opcional) y `liberar` (Task 2).

- [ ] **Step 1: Formulario** — el input `licCuenta`: quitar `required`, `placeholder="Vacío = se amarra al primer uso"`; etiqueta "Número de cuenta MT5 (opcional)". En el submit: `cuenta: document.getElementById('licCuenta').value.trim() || null`.

- [ ] **Step 2: Tabla** — en `mtbRenderTabla`, columna cuenta:
```javascript
'<td class="p-3"><div class="font-mono">' + (l.cuenta ? mtbEsc(l.cuenta) : '<span class="text-amber-300">sin amarrar</span>') + '</div><div class="text-[11px] text-slate-400 uppercase">' + mtbEsc(l.tipo) + '</div></td>'
```
y en la columna Acción, cuando `est === 'activa' && l.cuenta`, anteponer:
```javascript
'<button type="button" class="mtb-btn text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 px-2.5 py-1 rounded-lg mr-1" data-liberar="' + l.id + '">Liberar cuenta</button>'
```

- [ ] **Step 3: Evento** — en el listener de `nodeTableBody` de `mtbBindEventos`:
```javascript
    if (b.dataset.liberar && confirm('¿Liberar la cuenta? El próximo robot que se conecte con este token quedará amarrado a su cuenta.')) {
      mtbAccion({ accion: 'liberar', id: Number(b.dataset.liberar) });
    }
```

- [ ] **Step 4: Verificación en navegador** — servidor local con BD en memoria (`scratchpad/dev_server.py` + `lic.liberar`) → emitir sin cuenta → fila "sin amarrar"; emitir con cuenta → botón "Liberar cuenta" → confirmar → fila "sin amarrar". Sin errores de consola.

- [ ] **Step 5: Commit**
```bash
git add public/master.html
git commit -m "master.html: emitir sin cuenta y liberar cuenta"
```

### Task 4: Despliegue y verificación en producción

- [ ] **Step 1:** `git push origin main` (autorizado por Ricardo para el flujo de la Fase 2).
- [ ] **Step 2:** EasyPanel `chatbotventas/mtb-api` → Implementar; esperar `/salud` 200.
- [ ] **Step 3:** Prueba real: emitir desde `/master.html` una licencia DEMO **sin cuenta**; `POST https://mextradebot.com.mx/api/v1/auth` con ese token, `account` A, `mode: demo` → 200 y la fila muestra la cuenta A; repetir con `account` B → 403 `cuenta_no_coincide`; "Liberar cuenta" → con B → 200. Revocar la licencia de prueba al final.
