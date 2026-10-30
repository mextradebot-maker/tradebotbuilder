"""Validacion de laboratorio de cada (simbolo, temporalidad): decide cuales expectativas positivas son confiables.

Va DESPUES del analisis tecnico: toma los setups validos que ya guardo `backtest_largo` (backtest_largo.resultado.setups) y
vuelve a simularlos como opera el robot, que el backtest del motor no hace:
  1. una operacion a la vez por robot (la orden pendiente tambien ocupa al robot);
  2. costo de spread restado en R (spread / distancia al stop); swing solo opera compras;
  3. historial partido en dos mitades: tiene que ser positivo en AMBAS, con minimo N_MIN operaciones.
Resultado en la tabla `validacion_lab` (el catalogo /api/catalogo lo lee). Un hilo lo recalcula cada vez que backtest_largo
guarda un resultado nuevo (o cambia este archivo o los spreads): proceso continuo, no una prueba unica.
VALIDACION_LAB_ACTIVA=0 lo apaga (app.py). Solo escribe en `validacion_lab`: no toca backtest_largo ni nada de produccion.
"""

import hashlib
import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

N_MIN = 30  # operaciones minimas para validar
EXPIRACION = 20  # = backtesting.backtest.VELAS_EXPIRACION_ORDEN = InpVelasExpiracion del EA
SWING = {"Swing (S)", "Swing (M)"}  # swing solo opera compras
RESUELTOS = ("gano", "perdio", "invalidado")
PAUSA_COMBO_S = 2
PAUSA_OCIOSA_S = 300
PAUSA_ERROR_S = 3600

# Spread de ida como fraccion del precio. SUPUESTO (ilustrativo, igual que docs/experimento-walkforward/wf.py) hasta tener el
# muestreo real de XM (diag_vps.py resumen): al crear spreads_pct.json {"EURUSD": 0.00007, ...} junto a este archivo se usa ese valor
# (y como cambia VERSION, todo se revalida solo).
SPREAD_SUPUESTO = {
    "EURUSD": 7e-5, "GBPUSD": 9e-5, "USDJPY": 8e-5, "AUDUSD": 9e-5, "USDCAD": 1e-4, "USDCHF": 1e-4, "NZDUSD": 1.1e-4,
    "EURGBP": 9e-5, "EURJPY": 9e-5, "GBPJPY": 1.3e-4, "AUDJPY": 1.1e-4, "EURAUD": 1.4e-4, "AUDCAD": 1.3e-4,
    "NZDJPY": 1.3e-4, "CADJPY": 1.3e-4, "XAUUSD": 1.5e-4, "XAGUSD": 6e-4, "XPTUSD": 1.2e-3, "XPDUSD": 1.5e-3,
    "US30": 5e-5, "US100": 6e-5, "US500": 6e-5, "GER40": 6e-5, "UK100": 8e-5, "JP225": 1.2e-4,
    "WTIUSD": 4e-4, "BRENTUSD": 4e-4, "BTCUSD": 5e-4, "ETHUSD": 8e-4, "XRPUSD": 1.5e-3,
    "GOOGL": 6e-4, "NVDA": 6e-4, "META": 6e-4, "WMT": 6e-4,
}
SPREAD_DEFECTO = 5e-4  # simbolo sin spread conocido: conservador
_MEDIDOS = Path(__file__).with_name("spreads_pct.json")


def _spreads() -> tuple[dict, str]:
    if _MEDIDOS.exists():
        return {**SPREAD_SUPUESTO, **json.loads(_MEDIDOS.read_text())}, "medido"
    return SPREAD_SUPUESTO, "supuesto"


def _version() -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    if _MEDIDOS.exists():
        h.update(_MEDIDOS.read_bytes())
    return h.hexdigest()[:16]


def _sim(velas, s) -> tuple[str, float, int | None, int]:
    """Igual que backtesting.backtest._simular_v2_uno, pero devuelve ademas (vela de llenado, ultima vela que ocupa al robot).
    El demo() comprueba que da los mismos resultados que simular_v2."""
    long = s["direccion"] == "long"
    entrada, stop, tp, extremo = s["entrada"], s["stop"], s["tp"], s["zona_extremo"]
    riesgo = abs(entrada - stop)
    conocido = int(s["indice_conocido"])
    lleno = None
    for p in range(conocido + 1, len(velas)):
        alto, bajo, cierre = velas[p]
        toca_stop = bajo <= stop if long else alto >= stop
        if lleno is None:
            if p > conocido + EXPIRACION:
                return "expirado", 0.0, None, p - 1
            if not (bajo <= entrada if long else alto >= entrada):
                if alto >= tp if long else bajo <= tp:
                    return "cancelado_tp", 0.0, None, p
                continue
            lleno = p
            if toca_stop:
                return "perdio", -1.0, p, p
            if (cierre < extremo) if long else (cierre > extremo):
                return "invalidado", float((cierre - entrada) / riesgo if long else (entrada - cierre) / riesgo), p, p
            continue
        if toca_stop:
            return "perdio", -1.0, lleno, p
        if alto >= tp if long else bajo <= tp:
            return "gano", float(abs(tp - entrada) / riesgo), lleno, p
    return ("sin_llenar" if lleno is None else "sin_resolver"), 0.0, lleno, len(velas) - 1


def evaluar(simbolo: str, temporalidad: str, ohlc: pd.DataFrame, setups: pd.DataFrame) -> dict:
    """`setups`: setups validos del motor v2 con indice_conocido sobre `ohlc`. Devuelve el dict que se guarda."""
    spreads, fuente = _spreads()
    spread = spreads.get(simbolo, SPREAD_DEFECTO)
    v = setups[setups["valido"].astype(bool)].sort_values("indice_conocido")
    if temporalidad in SWING:
        v = v[v["direccion"] == "long"]
    velas = ohlc[["high", "low", "close"]].to_numpy()
    ops, libre, llenadas = [], -1, 0  # libre: ultima vela en que el robot sigue ocupado
    for _, s in v.iterrows():
        res, r, lleno, fin = _sim(velas, s)
        if int(s["indice_conocido"]) + 1 <= libre:
            continue  # robot ocupado: el EA no toma este setup
        libre = fin
        if lleno is not None:
            llenadas += 1
        if res in RESUELTOS:
            riesgo_pct = abs(s["entrada"] - s["stop"]) / s["entrada"]
            ops.append((ohlc.index[int(s["indice_conocido"])], r, r - spread / riesgo_pct))
    n = len(ops)
    out = {"n": n, "oportunidades": int(len(v)), "llenadas": llenadas, "spread_pct": spread, "spread_fuente": fuente,
           "nota": ("Swing: el robot opera sin SL (cierre forzado -3 %); la simulacion usa el stop del setup como unidad de riesgo."
                    if temporalidad in SWING else None)}
    if n == 0:
        return {**out, "winrate": None, "r_bruto": None, "r_neto": None, "mitad_1": None, "mitad_2": None, "ic95_bajo": None,
                "r_total_neto": 0.0, "estado": "insuficiente", "validado": False,
                "motivo": f"Sin operaciones resueltas (minimo {N_MIN})."}
    bruto = np.array([o[1] for o in ops])
    neto = np.array([o[2] for o in ops])
    h = n // 2
    m1, m2 = (float(neto[:h].mean()), float(neto[h:].mean())) if h else (None, None)
    se = float(neto.std(ddof=1) / math.sqrt(n)) if n > 1 else None
    r_neto = float(neto.mean())
    ic_bajo = r_neto - 1.96 * se if se is not None else None
    if n < N_MIN:
        estado, motivo = "insuficiente", f"Muestra insuficiente: {n} operaciones (minimo {N_MIN})."
    elif r_neto <= 0:
        estado, motivo = "no_validado", "Expectativa neta negativa despues de spread y una operacion a la vez."
    elif m1 <= 0 or m2 <= 0:
        estado, motivo = "no_validado", "Positiva en total pero no en las dos mitades del historial: no es estable."
    else:
        estado, motivo = "validado", "Positiva neta de spread y en las dos mitades del historial."
    return {**out, "winrate": float((bruto > 0).mean()), "r_bruto": float(bruto.mean()), "r_neto": r_neto, "mitad_1": m1,
            "mitad_2": m2, "ic95_bajo": ic_bajo, "r_total_neto": float(neto.sum()), "estado": estado,
            "validado": estado == "validado", "motivo": motivo}


# ----------------------------------------------------------------------------------------------- persistencia / hilo
def guardar(simbolo: str, temporalidad: str, resultado: dict, backtest_en: datetime) -> None:
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO validacion_lab (simbolo, temporalidad, validado_en, backtest_en, version, resultado)
               VALUES (%s, %s, now(), %s, %s, %s::jsonb)
               ON CONFLICT (simbolo, temporalidad) DO UPDATE SET validado_en = now(), backtest_en = EXCLUDED.backtest_en,
                 version = EXCLUDED.version, resultado = EXCLUDED.resultado""",
            (simbolo, temporalidad, backtest_en, _version(), json.dumps(resultado, allow_nan=False)))


def pendientes(backtests: dict, hechos: dict, version: str) -> list[tuple[str, str]]:
    """Combos a (re)validar. `backtests`: {combo: calculado_en} de backtest_largo; `hechos`: {combo: (backtest_en, version)}.
    Los que nunca se validaron primero, luego los de backtest mas nuevo o version distinta, el mas viejo antes."""
    sin = sorted(c for c in backtests if c not in hechos)
    viejos = sorted((c for c in backtests if c in hechos and (hechos[c][1] != version or hechos[c][0] < backtests[c])),
                    key=lambda c: hechos[c][0])
    return sin + viejos


def validar(simbolo: str, temporalidad: str) -> None:
    import backtest_largo
    previo = backtest_largo.leer_previo(simbolo, temporalidad)
    if not previo or not previo.get("setups"):
        raise ValueError("backtest largo sin setups guardados")
    ohlc = backtest_largo.velas_de(simbolo, temporalidad)[0]
    setups = backtest_largo._de_guardados(ohlc, previo["setups"])
    if setups is None:
        raise ValueError("las velas cambiaron respecto al backtest largo; se espera al siguiente completo")
    guardar(simbolo, temporalidad, evaluar(simbolo, temporalidad, ohlc, setups), backtest_largo.ultimos()[(simbolo, temporalidad)][0])


def _bucle(dormir=time.sleep) -> None:
    import backtest_largo
    from persistencia.conexion import get_conn
    vetados: dict = {}
    dormir(150)
    while True:
        pausa = PAUSA_OCIOSA_S
        try:
            ahora = datetime.now(timezone.utc)
            for c in [c for c, h in vetados.items() if h <= ahora]:
                del vetados[c]
            with get_conn() as conn:
                hechos = {(s, t): (b, v) for s, t, b, v in conn.execute("SELECT simbolo, temporalidad, backtest_en, version FROM validacion_lab")}
            cola = [c for c in pendientes({c: u[0] for c, u in backtest_largo.ultimos().items()}, hechos, _version()) if c not in vetados]
            if cola:
                c = cola[0]
                t0 = time.monotonic()
                try:
                    validar(*c)
                    log.info("validacion lab %s %s lista en %.1f s (%d pendientes)", *c, time.monotonic() - t0, len(cola) - 1)
                except Exception as e:
                    vetados[c] = ahora + timedelta(seconds=PAUSA_ERROR_S)
                    log.warning("validacion lab %s %s fallo: %s: %s", *c, type(e).__name__, e)
                pausa = PAUSA_COMBO_S
        except Exception:
            log.exception("ciclo de validacion lab fallo")
            pausa = 60
        dormir(pausa)


def iniciar_en_segundo_plano() -> threading.Thread:
    hilo = threading.Thread(target=_bucle, name="validacion-lab", daemon=True)
    hilo.start()
    return hilo


# ----------------------------------------------------------------------------------------------- pruebas
def demo() -> None:
    """Sin red ni BD: velas sinteticas. Comprueba que la simulacion iguala a backtest.simular_v2 y las reglas del veredicto."""
    from backtesting.backtest import simular_v2

    rng = np.random.default_rng(7)
    n = 3000
    cierre = 100 + np.cumsum(rng.normal(0, 0.3, n))
    ohlc = pd.DataFrame({"open": cierre, "high": cierre + rng.uniform(0.05, 0.5, n), "low": cierre - rng.uniform(0.05, 0.5, n),
                         "close": cierre}, index=pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))
    filas = []
    for i in range(10, n - 60, 25):  # un setup cada 25 velas, mezcla de lados
        long = (i // 25) % 2 == 0
        px = float(ohlc["close"].iloc[i])
        d = 0.8
        filas.append({"tipo": "continuacion", "direccion": "long" if long else "short", "entrada": px, "stop": px - d if long else px + d,
                      "tp": px + 2 * d if long else px - 2 * d, "zona_extremo": px - 0.9 * d if long else px + 0.9 * d,
                      "indice_conocido": i, "valido": True})
    setups = pd.DataFrame(filas)
    oficial = simular_v2(ohlc, setups).reset_index(drop=True)
    velas = ohlc[["high", "low", "close"]].to_numpy()
    for k, s in setups.iterrows():
        res, r, _, _ = _sim(velas, s)
        assert res == oficial.loc[k, "resultado"] and abs(r - oficial.loc[k, "r"]) < 1e-9, (k, res, oficial.loc[k, ["resultado", "r"]].tolist())
    e = evaluar("EURUSD", "Scalping 15m", ohlc, setups)
    assert e["oportunidades"] == len(setups) and 0 < e["n"] <= e["llenadas"] <= e["oportunidades"], e
    assert e["r_neto"] < e["r_bruto"], e  # el spread siempre resta
    assert evaluar("EURUSD", "Swing (S)", ohlc, setups)["oportunidades"] == int((setups["direccion"] == "long").sum())
    # veredictos con series armadas a mano: se prueban con el mismo corte de mitades y N_MIN
    ok = evaluar("EURUSD", "Scalping 15m", ohlc, setups.iloc[:0].assign(valido=[]))
    assert ok["estado"] == "insuficiente" and not ok["validado"]
    # pendientes: nunca validados primero, luego backtest mas nuevo o version distinta
    t0, t1 = datetime(2026, 1, 1), datetime(2026, 1, 2)
    bt = {("A", "x"): t1, ("B", "x"): t1, ("C", "x"): t1, ("D", "x"): t1}
    hechos = {("A", "x"): (t1, "v"), ("B", "x"): (t0, "v"), ("C", "x"): (t1, "otra")}
    assert pendientes(bt, hechos, "v") == [("D", "x"), ("B", "x"), ("C", "x")], pendientes(bt, hechos, "v")
    print(f"validacion_lab.demo() OK — {e['n']} operaciones sinteticas, R bruto {e['r_bruto']:+.2f} -> neto {e['r_neto']:+.2f}, estado {e['estado']}")


if __name__ == "__main__":
    demo()
