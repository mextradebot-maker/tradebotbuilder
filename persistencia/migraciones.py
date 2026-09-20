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
]


def aplicar() -> None:
    with get_conn() as conn:
        for sql in _SQL:
            conn.execute(sql)
