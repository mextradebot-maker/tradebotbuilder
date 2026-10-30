"""Compuerta de riesgo del Master Trader (servidor).

Une las reglas de la mejora de trading del 29 oct (codigo/mtb_tools/riesgo.py: tope de perdida diaria, riesgo abierto,
frenos por racha y por drawdown) con el protocolo de racha del diseno V2 (mitad de riesgo tras 3 perdidas seguidas).
Los lotes siguen saliendo de conectividad.riesgo.dimensionar (regla unica, 2 % maximo): aqui solo se decide SI se abre
y con que fraccion del riesgo. Una salida nunca pasa por aqui. Un freno (halt) solo lo levanta una persona
(POST /api/v1/master/admin accion reanudar).

est (lo manda el robot en cada pulso, salvo pico_equity y halt que guarda el servidor):
    balance, equity, pico_equity, pnl_hoy (USD realizado hoy), racha (perdidas seguidas), riesgo_abierto_pct, posiciones, halt
"""

CFG = {
    "perdida_dia_max_pct": 3.0,    # perdida realizada hoy que cierra la puerta hasta manana
    "riesgo_abierto_max_pct": 4.0, # suma del riesgo de lo abierto + lo nuevo
    "max_posiciones": 5,
    "racha_reduce": 3,             # desde aqui el riesgo se multiplica por factor_racha
    "factor_racha": 0.5,
    "racha_pausa": 5,              # freno duro
    "dd_pausa_pct": 15.0,          # freno duro: caida desde el pico de equity
    "min_rr": 0.0,                 # 0 = apagado (el motor SMC ya trae su TP); >0 exige R:R minimo con SL
}


def evaluar(est: dict, riesgo_pct: float, rr: float | None = None, cfg: dict | None = None) -> dict:
    """-> {"ok", "factor", "motivos", "halt"}; halt = texto si ESTA evaluacion levanta un freno duro (se persiste)."""
    c = {**CFG, **(cfg or {})}
    motivos, halt = [], None
    bal = float(est.get("balance") or 0)
    if bal <= 0:
        return {"ok": False, "factor": 0.0, "motivos": ["balance invalido"], "halt": None}
    if est.get("halt"):
        motivos.append(f"FRENO activo: {est['halt']} (lo levanta una persona)")
    pico = float(est.get("pico_equity") or 0)
    eq = float(est.get("equity") or bal)
    if pico > 0:
        dd = (pico - eq) / pico * 100
        if dd >= c["dd_pausa_pct"]:
            halt = f"drawdown {dd:.1f} % desde el pico (tope {c['dd_pausa_pct']} %)"
    racha = int(est.get("racha") or 0)
    if racha >= c["racha_pausa"] and not halt:
        halt = f"{racha} perdidas seguidas (tope {c['racha_pausa']})"
    if halt:
        motivos.append("FRENO: " + halt)
    if float(est.get("pnl_hoy") or 0) <= -bal * c["perdida_dia_max_pct"] / 100:
        motivos.append(f"perdida del dia {float(est['pnl_hoy']):.2f} llego al tope {c['perdida_dia_max_pct']} %")
    if int(est.get("posiciones") or 0) >= c["max_posiciones"]:
        motivos.append(f"{est['posiciones']} posiciones abiertas (tope {c['max_posiciones']})")
    factor = c["factor_racha"] if racha >= c["racha_reduce"] else 1.0
    if float(est.get("riesgo_abierto_pct") or 0) + riesgo_pct * factor > c["riesgo_abierto_max_pct"] + 1e-9:
        motivos.append(f"riesgo abierto {float(est.get('riesgo_abierto_pct') or 0):.2f} % + nuevo pasa {c['riesgo_abierto_max_pct']} %")
    if c["min_rr"] and rr is not None and rr < c["min_rr"]:
        motivos.append(f"R:R {rr:.2f} menor al minimo {c['min_rr']}")
    return {"ok": not motivos, "factor": factor, "motivos": motivos, "halt": halt}


def demo() -> None:
    base = {"balance": 10000, "equity": 10000, "pico_equity": 10000, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0, "posiciones": 0}
    r = evaluar(base, 2.0)
    assert r["ok"] and r["factor"] == 1.0 and r["halt"] is None, r
    r = evaluar({**base, "racha": 3}, 2.0)
    assert r["ok"] and r["factor"] == 0.5, r  # mitad de riesgo tras 3 perdidas
    r = evaluar({**base, "racha": 5}, 2.0)
    assert not r["ok"] and r["halt"] and "5 perdidas" in r["halt"], r
    r = evaluar({**base, "equity": 8400}, 2.0)
    assert not r["ok"] and "drawdown 16.0" in r["halt"], r
    r = evaluar({**base, "equity": 8600}, 2.0)
    assert r["ok"], r  # 14 % todavia pasa
    r = evaluar({**base, "pnl_hoy": -300}, 2.0)
    assert not r["ok"] and r["halt"] is None, r  # tope diario: bloquea hoy, no deja freno
    r = evaluar({**base, "halt": "x"}, 2.0)
    assert not r["ok"] and "FRENO activo" in r["motivos"][0], r
    r = evaluar({**base, "riesgo_abierto_pct": 3.0}, 2.0)
    assert not r["ok"], r
    r = evaluar({**base, "riesgo_abierto_pct": 2.0}, 2.0)
    assert r["ok"], r  # 2 + 2 = 4, cabe justo
    r = evaluar(base, 2.0, rr=1.0, cfg={"min_rr": 1.5})
    assert not r["ok"], r
    assert evaluar({**base, "balance": 0}, 2.0)["ok"] is False
    print("master.riesgo.demo() OK")


if __name__ == "__main__":
    demo()
