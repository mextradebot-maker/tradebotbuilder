"""Corrida real del motor v2 contra Dukascopy — spec 2026-09-28, Pruebas punto 4.

Imprime, por símbolo × perfil, cuántos candidatos hay de cada tipo, cuántos elimina cada
regla y cuántos quedan válidos, más el backtest de los válidos. No exige un número de
setups: sirve para ver si alguna regla deja un perfil en cero ANTES de cerrar la Etapa 1.

Uso: PYTHONIOENCODING=utf-8 .venv/Scripts/python -m backtesting.embudo_v2 [SIMBOLO ...]
"""

import sys
from datetime import datetime, timedelta, timezone

from api.setups import DIAS_POR_TEMPORALIDAD, PERFIL_A_VELAS_V2, VELA_A_INTERVALO
from backtesting.backtest import backtest_v2
from conectividad import TEMPORALIDADES, obtener_velas
from motor_smc.setups_v2 import detectar_setups_v2, embudo

PERFILES = tuple(TEMPORALIDADES)


def correr(simbolo: str, perfil: str) -> dict:
    vela, vela_mayor = PERFIL_A_VELAS_V2[perfil]
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=DIAS_POR_TEMPORALIDAD[perfil])
    ohlc = obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela])
    ohlc_mayor = ohlc if vela_mayor == vela else obtener_velas(simbolo, inicio, fin, intervalo=VELA_A_INTERVALO[vela_mayor])
    setups = detectar_setups_v2(ohlc, ohlc_mayor, vela, vela_mayor)
    invalidos_sin_razon = setups[~setups["valido"].astype(bool) & (setups["razon_descarte"] == "")]
    assert invalidos_sin_razon.empty, f"{simbolo} {perfil}: descartes sin razón"
    return {"velas": len(ohlc), "embudo": embudo(setups), "backtests": backtest_v2(ohlc, setups)}


def main(simbolos: list[str]) -> None:
    print(f"{'símbolo':8} {'perfil':10} {'tipo':13} {'cand':>5} {'R1':>4} {'R2':>4} {'R3':>4} {'R4':>4} {'R5':>4} {'R6':>4} {'válidos':>8} {'n bt':>5} {'exp R':>6}")
    for simbolo in simbolos:
        for perfil in PERFILES:
            r = correr(simbolo, perfil)
            for tipo, e in r["embudo"].items():
                d = e["descartados_por_regla"]
                bts = [r["backtests"][tipo][cv] for cv in ("compra", "venta")]
                n_bt = sum(b["n_setups"] for b in bts)
                exp = [b["expectativa_r"] for b in bts if b.get("expectativa_r") is not None]
                exp_txt = f"{sum(exp) / len(exp):.2f}" if exp else "-"
                print(f"{simbolo:8} {perfil:10} {tipo:13} {e['candidatos']:>5} {d['R1']:>4} {d['R2']:>4} {d['R3']:>4} "
                      f"{d['R4']:>4} {d['R5']:>4} {d['R6']:>4} {e['validos']:>8} {n_bt:>5} {exp_txt:>6}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["XAUUSD", "EURUSD"])
