"""Motor SMC — Bloque 4.

Lee velas de MT5 por cada par activo en cuentas_demo,
calcula tendencia (HH/HL vs LH/LL) + ATR,
y hace upsert en smc_snapshot.

Ejecutar antes de coordinador.py (o en paralelo con mayor frecuencia).
"""
from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5
from persistencia.conexion import get_conn
from conectividad.xm import conectar, desconectar

TIMEFRAMES = {
    "Scalping":  mt5.TIMEFRAME_M15,
    "Intraday":  mt5.TIMEFRAME_H1,
    "Swing (H)": mt5.TIMEFRAME_H4,
    "Swing (S)": mt5.TIMEFRAME_D1,
    "Swing (M)": mt5.TIMEFRAME_W1,
}
N_CANDLES = 100
SWING_LOOKBACK = 3
ATR_PERIOD = 14


def _atr(rates) -> float:
    trs = []
    for i in range(1, len(rates)):
        h, l, pc = rates[i]["high"], rates[i]["low"], rates[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    period_trs = trs[-ATR_PERIOD:] if len(trs) >= ATR_PERIOD else trs
    return sum(period_trs) / len(period_trs) if period_trs else 0.0


def _swings(rates):
    highs = [r["high"] for r in rates]
    lows  = [r["low"]  for r in rates]
    sh, sl = [], []
    lb = SWING_LOOKBACK
    for i in range(lb, len(rates) - lb):
        if highs[i] == max(highs[i - lb : i + lb + 1]):
            sh.append(highs[i])
        if lows[i] == min(lows[i - lb : i + lb + 1]):
            sl.append(lows[i])
    return sh, sl


def _tendencia(sh, sl) -> str | None:
    if len(sh) < 2 or len(sl) < 2:
        return None
    hh = sh[-1] > sh[-2]
    hl = sl[-1] > sl[-2]
    lh = sh[-1] < sh[-2]
    ll = sl[-1] < sl[-2]
    if hh and hl:
        return "alcista"
    if lh and ll:
        return "bajista"
    return None


def _analizar(simbolo: str, temporalidad: str) -> dict | None:
    tf = TIMEFRAMES.get(temporalidad)
    if tf is None:
        return None
    mt5.symbol_select(simbolo, True)
    rates = mt5.copy_rates_from_pos(simbolo, tf, 0, N_CANDLES)
    if rates is None or len(rates) < 20:
        err = mt5.last_error()
        print(f"  {simbolo}/{temporalidad}: sin velas MT5 — {err}")
        return None

    sh, sl = _swings(rates)
    tendencia = _tendencia(sh, sl)
    atr = _atr(rates)
    vol = sum(r["tick_volume"] for r in rates[-20:]) / 20

    return {
        "tendencia_actual": tendencia,
        "atr": atr,
        "volumen_promedio": vol,
        "ultimo_timestamp": int(rates[-1]["time"]),
    }


def ciclo() -> None:
    with get_conn() as conn:
        pares = conn.execute(
            "SELECT DISTINCT simbolo, temporalidad FROM cuentas_demo WHERE activa = true"
        ).fetchall()

    print(f"Motor SMC: analizando {len(pares)} pares")
    conectar()
    try:
        for simbolo, temporalidad in pares:
            r = _analizar(simbolo, temporalidad)
            if r is None:
                continue
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO smc_snapshot
                           (simbolo, temporalidad, ultimo_timestamp,
                            tendencia_actual, volumen_promedio, atr, refrescado_en)
                       VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, now())
                       ON CONFLICT (simbolo, temporalidad) DO UPDATE SET
                           ultimo_timestamp = EXCLUDED.ultimo_timestamp,
                           tendencia_actual = EXCLUDED.tendencia_actual,
                           volumen_promedio = EXCLUDED.volumen_promedio,
                           atr              = EXCLUDED.atr,
                           refrescado_en    = now()""",
                    (simbolo, temporalidad, r["ultimo_timestamp"],
                     r["tendencia_actual"], r["volumen_promedio"], r["atr"]),
                )
            print(f"  {simbolo}/{temporalidad}: tendencia={r['tendencia_actual']}  ATR={r['atr']:.5f}")
    finally:
        desconectar()
    print("Motor SMC: ciclo completado")


if __name__ == "__main__":
    ciclo()


def demo():
    rates = [
        {"high": 1.10, "low": 1.08, "close": 1.09},
        {"high": 1.11, "low": 1.09, "close": 1.10},
        {"high": 1.09, "low": 1.07, "close": 1.08},
        {"high": 1.12, "low": 1.10, "close": 1.11},
        {"high": 1.10, "low": 1.08, "close": 1.09},
        {"high": 1.13, "low": 1.11, "close": 1.12},
        {"high": 1.11, "low": 1.09, "close": 1.10},
    ]
    sh, sl = _swings(rates)
    assert _atr(rates) > 0
    print(f"demo: sh={sh} sl={sl} tendencia={_tendencia(sh, sl)}")
    print("motor_smc.demo() OK")
