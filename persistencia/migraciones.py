import re

from .conexion import get_conn

_SQL = [
    # ── Bloque 1: señales SMC ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS smc_snapshot (
        simbolo           text        NOT NULL,
        temporalidad      text        NOT NULL,
        ultimo_timestamp  timestamptz,
        estructura_smc    jsonb,
        tendencia_actual  text,
        volumen_promedio  numeric,
        atr               numeric,
        refrescado_en     timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (simbolo, temporalidad)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historico_tendencias (
        id                bigserial   PRIMARY KEY,
        simbolo           text        NOT NULL,
        temporalidad      text        NOT NULL,
        fecha_cambio      timestamptz NOT NULL DEFAULT now(),
        tendencia_nueva   text,
        tendencia_anterior text
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hist_tend_simbolo_temp_fecha
        ON historico_tendencias (simbolo, temporalidad, fecha_cambio DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS catalogo_activos (
        simbolo       text    NOT NULL,
        temporalidad  text    NOT NULL,
        fecha_inicio  date    NOT NULL,
        activo        boolean NOT NULL DEFAULT true,
        PRIMARY KEY (simbolo, temporalidad)
    )
    """,

    # ── Bloque 2: configuración de cuentas demo ───────────────────────────
    """
    CREATE TABLE IF NOT EXISTS cuentas_demo (
        login         bigint   PRIMARY KEY,
        server        text     NOT NULL,
        nombre        text     NOT NULL,
        simbolo       text     NOT NULL,
        temporalidad  text     NOT NULL,
        pct_riesgo    numeric  NOT NULL DEFAULT 0.01,
        activa        boolean  NOT NULL DEFAULT true
    )
    """,

    # ── Bloque 3: posiciones activas del coordinador ──────────────────────
    """
    CREATE TABLE IF NOT EXISTS posiciones_abiertas (
        ticket             bigint      PRIMARY KEY,
        login              bigint      NOT NULL REFERENCES cuentas_demo(login),
        simbolo            text        NOT NULL,
        temporalidad       text        NOT NULL,
        direccion          text        NOT NULL,
        lotes              numeric     NOT NULL,
        precio_entrada     numeric     NOT NULL,
        tiene_sl           boolean     NOT NULL DEFAULT false,
        sl_precio          numeric,
        tendencia_apertura text,
        abierta_en         timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pos_abiertas_login
        ON posiciones_abiertas (login)
    """,

    # ── Bloque 4: historial de trades cerrados ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS historial_posiciones (
        id             bigserial   PRIMARY KEY,
        ticket         bigint      NOT NULL,
        login          bigint      NOT NULL,
        simbolo        text        NOT NULL,
        temporalidad   text        NOT NULL,
        direccion      text        NOT NULL,
        lotes          numeric     NOT NULL,
        precio_entrada numeric     NOT NULL,
        precio_cierre  numeric     NOT NULL,
        profit_usd     numeric     NOT NULL,
        razon_cierre   text        NOT NULL,
        abierta_en     timestamptz NOT NULL,
        cerrada_en     timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hist_pos_login_fecha
        ON historial_posiciones (login, cerrada_en DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hist_pos_simbolo_fecha
        ON historial_posiciones (simbolo, cerrada_en DESC)
    """,

    # ── Bloque 5: auditoría de decisiones del coordinador ─────────────────
    """
    CREATE TABLE IF NOT EXISTS log_coordinador (
        id            bigserial   PRIMARY KEY,
        login         bigint,
        simbolo       text,
        temporalidad  text,
        accion        text        NOT NULL,
        motivo        text,
        lotes         numeric,
        detalle       jsonb,
        registrado_en timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_log_coord_login_fecha
        ON log_coordinador (login, registrado_en DESC)
    """,

    # Bloque 6 (peticiones_usuario / solicitar.py) retirado 2026-09-26: las
    # operaciones manuales se abren directo en MT5 y el coordinador las adopta.

    # ── Bloque 7: cerebro de licencias (persistencia/licencias.py) ────────
    """
    CREATE TABLE IF NOT EXISTS licencias (
        id               bigserial   PRIMARY KEY,
        token_hash       text        NOT NULL UNIQUE,
        token_prefijo    text        NOT NULL,
        cliente          text        NOT NULL,
        cuenta           bigint      NOT NULL,
        tipo             text        NOT NULL CHECK (tipo IN ('demo', 'real', 'vip')),
        robot            text        NOT NULL,
        expira_en        timestamptz,
        revocada_en      timestamptz,
        creada_en        timestamptz NOT NULL DEFAULT now(),
        ultimo_contacto  timestamptz,
        ultima_ip        text,
        ultimo_resultado text
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_licencias_cuenta ON licencias (cuenta)
    """,
    """
    CREATE TABLE IF NOT EXISTS licencias_eventos (
        id            bigserial   PRIMARY KEY,
        licencia_id   bigint      REFERENCES licencias(id),
        cuenta        bigint,
        resultado     text        NOT NULL,
        motivo        text,
        ip            text,
        detalle       jsonb,
        registrado_en timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lic_eventos_fecha ON licencias_eventos (registrado_en DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS kill_switch (
        id          boolean     PRIMARY KEY DEFAULT true CHECK (id),
        activo      boolean     NOT NULL DEFAULT false,
        motivo      text,
        cambiado_en timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    INSERT INTO kill_switch (id) VALUES (true) ON CONFLICT DO NOTHING
    """,

    # ── Fase 2: licencias sin amarrar (se amarran al primer uso) ──────────
    """
    ALTER TABLE licencias ALTER COLUMN cuenta DROP NOT NULL
    """,
    # semilla del token derivado (persistencia/licencias.derivar_token): permite recompilar robots
    """
    ALTER TABLE licencias ADD COLUMN IF NOT EXISTS semilla text
    """,
    # licencias de alumnos: dueño por correo; 1 DEMO automática por alumno (persistencia/licencias.asegurar_licencia_demo)
    """
    ALTER TABLE licencias ADD COLUMN IF NOT EXISTS correo text
    """,
    """
    ALTER TABLE licencias ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'admin'
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_licencia_demo_alumno ON licencias (correo)
        WHERE tipo = 'demo' AND origen = 'registro'
    """,
    # ── Etapa 2: temporalidades canonicas (idempotente: se corre en cada arranque) ──
    # 1) catalogo: 7 canonicas por simbolo con filas viejas, heredando fecha_inicio
    #    (Scalping 30m entra inactiva); sin filas viejas no hace nada.
    """
    INSERT INTO catalogo_activos (simbolo, temporalidad, fecha_inicio, activo)
    WITH m(nuevo, viejo, activo) AS (VALUES
        ('Scalping 15m', 'Scalping',  true),
        ('Scalping 30m', 'Scalping',  false),
        ('Intraday 1H',  'Intraday',  true),
        ('Intraday 4H',  'Swing (H)', true),
        ('Intraday D',   'Intraday',  true),
        ('Swing (S)',    'Swing (S)', true),
        ('Swing (M)',    'Swing (M)', true))
    SELECT s.simbolo, m.nuevo, COALESCE(v.fecha_inicio, s.fecha_min), m.activo AND COALESCE(v.activo, true)
    FROM (SELECT simbolo, MIN(fecha_inicio) AS fecha_min FROM catalogo_activos
          WHERE simbolo IN (SELECT simbolo FROM catalogo_activos
                            WHERE temporalidad IN ('Scalping', 'Intraday', 'Swing (H)'))
          GROUP BY simbolo) s
    CROSS JOIN m
    LEFT JOIN catalogo_activos v ON v.simbolo = s.simbolo AND v.temporalidad = m.viejo
    WHERE true
    ON CONFLICT DO NOTHING
    """,
    """
    DELETE FROM catalogo_activos WHERE temporalidad IN ('Scalping', 'Intraday', 'Swing (H)')
    """,
    # 2) cuentas demo: nombre viejo -> canonico
    """
    UPDATE cuentas_demo SET temporalidad = 'Scalping 15m' WHERE temporalidad = 'Scalping'
    """,
    """
    UPDATE cuentas_demo SET temporalidad = 'Intraday 1H' WHERE temporalidad = 'Intraday'
    """,
    """
    UPDATE cuentas_demo SET temporalidad = 'Intraday 4H' WHERE temporalidad = 'Swing (H)'
    """,
    # 3) snapshots con nombres viejos (se regeneran bajo el canonico en el refresco).
    #    historico_tendencias, posiciones_abiertas, historial_posiciones y log_coordinador NO se tocan.
    """
    DELETE FROM smc_snapshot WHERE temporalidad IN ('Scalping', 'Intraday', 'Swing (H)', 'Swing', '')
    """,
]

# 36 simbolos × 7 temporalidades = 252 pares (Scalping 30m inactiva)
_SIMBOLOS_SEED = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "XAUUSD", "XAGUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD",
    "AUDCAD", "NZDJPY", "CADJPY", "US30", "US100", "US500", "GER40", "UK100",
    "JP225", "XPTUSD", "XPDUSD", "WTIUSD", "BRENTUSD", "BTCUSD", "ETHUSD",
    "XRPUSD", "AMXL", "CEMEXCPO", "GOOGL", "NVDA", "META", "WMT",
]
_TEMPORALIDADES_SEED = ["Scalping 15m", "Scalping 30m", "Intraday 1H", "Intraday 4H", "Intraday D", "Swing (S)", "Swing (M)"]
_TEMPORALIDADES_INACTIVAS = {"Scalping 30m"}  # activar a mano tras medir tiempos en produccion
_FECHA_SEED = "2026-09-20"


# Cuentas confirmadas al 2026-09-21 (5 de 5 completas).
# GOLD = símbolo correcto en XMGlobal-MT5 7/9 (no XAUUSD).
# WTIUSD = Petróleo WTI en XM — confirmar nombre exacto del símbolo en MT5 si falla.
_CUENTAS_SEED = [
    # login,      server,              nombre,                   simbolo,   temporalidad,  pct_riesgo
    (318680674, "XMGlobal-MT5 7", "PETROLEO CRUDO SWING",   "WTIUSD",  "Swing (S)",   0.02),
    (318735437, "XMGlobal-MT5 7", "EUR/USD SCALPING",       "EURUSD",  "Scalping 15m", 0.01),
    (336903105, "XMGlobal-MT5 9", "USD/JPY",                "USDJPY",  "Intraday 1H", 0.01),
    (336903102, "XMGlobal-MT5 9", "ORO INTRADAY",           "GOLD",    "Intraday 1H", 0.02),
    (108460538, "XMGlobal-MT5 5", "ORO SWING",              "GOLD",    "Swing (S)",   0.02),
]


def aplicar() -> None:
    with get_conn() as conn:
        # serializa arranques concurrentes (padre + workers): el bloqueo se suelta al commit
        conn.execute("SELECT pg_advisory_xact_lock(7204042901)")
        for sql in _SQL:
            conn.execute(sql)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalogo_activos (simbolo, temporalidad, fecha_inicio, activo) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(s, t, _FECHA_SEED, t not in _TEMPORALIDADES_INACTIVAS) for s in _SIMBOLOS_SEED for t in _TEMPORALIDADES_SEED],
            )
            cur.executemany(
                """INSERT INTO cuentas_demo (login, server, nombre, simbolo, temporalidad, pct_riesgo)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                _CUENTAS_SEED,
            )


def demo() -> None:
    """Simula las sentencias de la migracion Etapa 2 en sqlite (sin Postgres): 2 corridas, mismo resultado."""
    import sqlite3
    import sys

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE catalogo_activos (simbolo text, temporalidad text, fecha_inicio text, activo boolean default 1, PRIMARY KEY (simbolo, temporalidad))")
    db.execute("CREATE TABLE cuentas_demo (login int primary key, temporalidad text)")
    db.execute("CREATE TABLE smc_snapshot (simbolo text, temporalidad text)")
    db.execute("CREATE TABLE historico_tendencias (temporalidad text)")
    for i, t in enumerate(["Scalping", "Intraday", "Swing (H)", "Swing (S)", "Swing (M)"]):
        db.execute("INSERT INTO catalogo_activos VALUES ('EURUSD', ?, ?, 1)", (t, f"2026-09-0{i + 1}"))
    db.execute("INSERT INTO catalogo_activos VALUES ('GOLD', 'Swing (S)', '2026-08-01', 1)")  # solo Swing: hereda la minima
    db.execute("INSERT INTO catalogo_activos VALUES ('GOLD', 'Intraday', '2026-08-15', 1)")
    db.executemany("INSERT INTO cuentas_demo VALUES (?, ?)", [(1, "Scalping"), (2, "Intraday"), (3, "Swing (H)"), (4, "Swing (S)")])
    db.executemany("INSERT INTO smc_snapshot VALUES ('EURUSD', ?)", [(t,) for t in ("Scalping", "Swing", "", "Intraday 1H")])
    db.execute("INSERT INTO historico_tendencias VALUES ('Scalping')")
    nuevas = _SQL[-6:]
    assert "INSERT INTO catalogo_activos" in nuevas[0] and "smc_snapshot" in nuevas[-1]
    tablas = {"catalogo_activos", "cuentas_demo", "smc_snapshot"}
    for sql in nuevas:
        assert set(re.findall(r"(?:INTO|FROM|UPDATE|JOIN)\s+(\w+)", sql)) <= tablas | {"m"}, sql
    def estado():
        return [db.execute(q).fetchall() for q in (
            "SELECT * FROM catalogo_activos ORDER BY 1, 2", "SELECT * FROM cuentas_demo ORDER BY 1",
            "SELECT * FROM smc_snapshot ORDER BY 2", "SELECT * FROM historico_tendencias")]
    for _ in range(2):
        ant = estado()
        for sql in nuevas:
            db.execute(sql)
        ult = estado()
    assert ant == ult, "segunda corrida cambio datos"
    cat, cuentas, snap, hist = ult
    canon = ["Intraday 1H", "Intraday 4H", "Intraday D", "Scalping 15m", "Scalping 30m", "Swing (M)", "Swing (S)"]
    for sim in ("EURUSD", "GOLD"):
        filas = [f for f in cat if f[0] == sim]
        assert sorted(f[1] for f in filas) == canon, filas
        assert [f[3] for f in filas if f[1] == "Scalping 30m"] == [0] * 1 or not [f[3] for f in filas if f[1] == "Scalping 30m"][0]
        assert all(f[3] for f in filas if f[1] != "Scalping 30m")
    fi = {(f[0], f[1]): f[2] for f in cat}
    assert fi[("EURUSD", "Scalping 15m")] == fi[("EURUSD", "Scalping 30m")] == "2026-09-01"
    assert fi[("EURUSD", "Intraday 1H")] == fi[("EURUSD", "Intraday D")] == "2026-09-02"
    assert fi[("EURUSD", "Intraday 4H")] == "2026-09-03"
    assert fi[("GOLD", "Scalping 15m")] == "2026-08-01"  # sin equivalente -> minima del simbolo
    assert fi[("GOLD", "Intraday 1H")] == "2026-08-15"
    assert [c[1] for c in cuentas] == ["Scalping 15m", "Intraday 1H", "Intraday 4H", "Swing (S)"]
    assert [s[1] for s in snap] == ["Intraday 1H"] and hist == [("Scalping",)]
    # las semillas ya no reintroducen nombres viejos
    assert not {"Scalping", "Intraday", "Swing (H)"} & (set(_TEMPORALIDADES_SEED) | {c[4] for c in _CUENTAS_SEED})
    from conectividad.historico import TEMPORALIDADES
    assert list(TEMPORALIDADES) == _TEMPORALIDADES_SEED
    print("persistencia.migraciones.demo() OK", file=sys.stdout)
