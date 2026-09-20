from .conexion import get_conn

_SQL = [
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
        id               bigserial   PRIMARY KEY,
        simbolo          text        NOT NULL,
        temporalidad     text        NOT NULL,
        fecha_cambio     timestamptz NOT NULL DEFAULT now(),
        tendencia_nueva  text,
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


def aplicar() -> None:
    with get_conn() as conn:
        for sql in _SQL:
            conn.execute(sql)
        conn.executemany(
            "INSERT INTO catalogo_activos (simbolo, temporalidad, fecha_inicio) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(s, t, _FECHA_SEED) for s in _SIMBOLOS_SEED for t in _TEMPORALIDADES_SEED],
        )
