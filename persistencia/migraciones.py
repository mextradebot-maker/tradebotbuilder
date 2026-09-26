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
]

# 36 simbolos × 5 temporalidades = 180 pares seeded en la primera migración
_SIMBOLOS_SEED = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "XAUUSD", "XAGUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD",
    "AUDCAD", "NZDJPY", "CADJPY", "US30", "US100", "US500", "GER40", "UK100",
    "JP225", "XPTUSD", "XPDUSD", "WTIUSD", "BRENTUSD", "BTCUSD", "ETHUSD",
    "XRPUSD", "AMXL", "CEMEXCPO", "GOOGL", "NVDA", "META", "WMT",
]
_TEMPORALIDADES_SEED = ["Scalping", "Intraday", "Swing (H)", "Swing (S)", "Swing (M)"]
_FECHA_SEED = "2026-09-20"


# Cuentas confirmadas al 2026-09-21 (5 de 5 completas).
# GOLD = símbolo correcto en XMGlobal-MT5 7/9 (no XAUUSD).
# WTIUSD = Petróleo WTI en XM — confirmar nombre exacto del símbolo en MT5 si falla.
_CUENTAS_SEED = [
    # login,      server,              nombre,                   simbolo,   temporalidad,  pct_riesgo
    (318680674, "XMGlobal-MT5 7", "PETROLEO CRUDO SWING",   "WTIUSD",  "Swing (S)",   0.02),
    (318735437, "XMGlobal-MT5 7", "EUR/USD SCALPING",       "EURUSD",  "Scalping",    0.01),
    (336903105, "XMGlobal-MT5 9", "USD/JPY",                "USDJPY",  "Intraday",    0.01),
    (336903102, "XMGlobal-MT5 9", "ORO INTRADAY",           "GOLD",    "Intraday",    0.02),
    (108460538, "XMGlobal-MT5 5", "ORO SWING",              "GOLD",    "Swing (S)",   0.02),
]


def aplicar() -> None:
    with get_conn() as conn:
        for sql in _SQL:
            conn.execute(sql)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalogo_activos (simbolo, temporalidad, fecha_inicio) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                [(s, t, _FECHA_SEED) for s in _SIMBOLOS_SEED for t in _TEMPORALIDADES_SEED],
            )
            cur.executemany(
                """INSERT INTO cuentas_demo (login, server, nombre, simbolo, temporalidad, pct_riesgo)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                _CUENTAS_SEED,
            )
