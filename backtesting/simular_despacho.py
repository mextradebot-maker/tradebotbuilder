"""Prueba de despacho de extremo a extremo, SIN base de datos: ¿el Master manda a los robots lo que dice el reporte, en sus niveles?

Toma los datos reales del laboratorio (/api/setups de cada combinacion, los mismos que ve refresco.py), pone un robot activado
simulado en CADA combinacion (activo x temporalidad) y corre el despachador REAL (master.despachador: vigentes -> recibir_oportunidades
-> pulso con la compuerta de riesgo) sobre un repositorio en memoria. Compara lo que el despachador entrega con lo que el reporte presenta.

Uso:  MTB_SERVICE_KEY=... python -m backtesting.simular_despacho      (MTB_LAB_URL cambia el servidor)
Nada se escribe en Postgres: se puede correr en el laboratorio las veces que haga falta.
"""
import concurrent.futures as cf
import json
import os
import sys

from backtesting.validar_laboratorio import BASE, _get
from master.despachador import MemRepo, pulso, recibir_oportunidades, vigentes

# Aqui se miran NIVELES, no lotes: capital enorme para que la compuerta de riesgo no oculte nada (las especificaciones de contrato de cada
# par cambian la perdida por lote; los lotes reales dependen de la cuenta y los calcula la calculadora de capital del panel).
EST = {"balance": 1e9, "equity": 1e9, "pnl_hoy": 0, "racha": 0, "riesgo_abierto_pct": 0.0, "posiciones": 0, "pendientes": 0,
       "riesgo_pct": 2.0, "tick_size": 0.00001, "tick_value": 1.0, "vol_min": 0.01, "vol_max": 100.0, "vol_step": 0.01}
INTRADIA = ("Scalping 15m", "Scalping 30m", "Intraday 1H", "Intraday 4H")


def bajar(clave: str) -> dict:
    cat = json.load(__import__("urllib.request", fromlist=["urlopen"]).urlopen(
        __import__("urllib.request", fromlist=["Request"]).Request(f"{BASE}/api/catalogo", headers={"User-Agent": "mtb-despacho"}), timeout=60))
    combos = sorted({(a["simbolo"], a["temporalidad"]) for a in cat["activos_todos"]})

    def uno(c):
        try:
            return c, _get("/api/setups", {"simbolo": c[0], "temporalidad": c[1]}, clave)
        except Exception as e:
            return c, {"error": str(e)}
    with cf.ThreadPoolExecutor(4) as ex:
        return dict(ex.map(uno, combos))


def simular(cuerpos: dict) -> list[dict]:
    """cuerpos: {(simbolo, temporalidad): respuesta de /api/setups}. Devuelve las ordenes que el Master entrega a un robot por combinacion."""
    activados = [(1000 + i, s, t, 5000 + i) for i, (s, t) in enumerate(cuerpos)]
    repo = MemRepo(activados)
    for (s, t), b in cuerpos.items():
        conf = vigentes(b.get("setups_confirmados") or [], b.get("velas"), b.get("ref_cierre"), t)  # el mismo camino de refresco._avisar_master
        if conf:
            recibir_oportunidades(repo, s, t, conf, b.get("ref_ts"), b.get("ref_cierre"))
    salida = []
    for lic, s, t, cuenta in activados:
        r = pulso(repo, lic, cuenta, s, t, dict(EST))
        if r.get("orden"):
            o = r["orden"]
            salida.append({"simbolo": s, "temporalidad": t, "direccion": o["direccion"], "entrada": o["entrada"], "stop": o["stop"], "tp": o["tp"],
                           "lotes": o["lotes"]})
        elif r.get("estado") not in ("sin_orden",):
            salida.append({"simbolo": s, "temporalidad": t, "bloqueada": r.get("estado"), "motivos": r.get("motivos")})
    return salida


def comparar(ordenes: list[dict], reporte: dict) -> None:
    rep = {(v["simbolo"], v["temporalidad"]): v for v in reporte["estrategias"]["estructural"]["setups"]}
    ent = {(o["simbolo"], o["temporalidad"]): o for o in ordenes if "entrada" in o}
    print(f"el reporte presenta {len(rep)} setups; el despachador entregaria {len(ent)} ordenes a robots simulados (uno por combinacion)\n")
    print("EN EL REPORTE:")
    for k, v in rep.items():
        o = ent.get(k)
        if o is None:
            print(f"  {k[0]:8}{k[1]:12} -> el despachador NO la entrega  <-- DIFERENCIA")
            continue
        igual = all(abs(float(o[c]) - float(v[c])) <= 1e-4 * max(1.0, abs(float(v[c]))) for c in ("entrada", "stop", "tp"))
        print(f"  {k[0]:8}{k[1]:12} entrada {o['entrada']:<10g} stop {o['stop']:<10g} tp {o['tp']:<10g} {'= igual al reporte' if igual else '<-- NIVELES DISTINTOS'}")
    print("\nENTREGA EL DESPACHADOR PERO EL REPORTE NO LA MUESTRA:")
    extra = [k for k in ent if k not in rep]
    for k in extra:
        o = ent[k]
        print(f"  {k[0]:8}{k[1]:14}{o['direccion']:7} {'(intradia: estrategia no valida)' if k[1] in INTRADIA else ''}")
    if not extra:
        print("  ninguna")
    bloq = [o for o in ordenes if "bloqueada" in o]
    if bloq:
        print(f"\nordenes frenadas por la compuerta de riesgo: {len(bloq)}", [(o['simbolo'], o['temporalidad'], o['motivos']) for o in bloq][:3])


def demo() -> None:
    cuerpo = {"velas": 400, "ref_cierre": 1.0, "ref_ts": 1, "setups_confirmados": [
        {"direccion": "long", "entrada": 0.999, "stop": 0.995, "tp": 1.02, "indice_conocido": 398, "indice_confirmacion": 390}]}
    lejos = {"velas": 400, "ref_cierre": 2.0, "ref_ts": 1, "setups_confirmados": [
        {"direccion": "long", "entrada": 0.999, "stop": 0.995, "tp": 1.02, "indice_conocido": 398, "indice_confirmacion": 390}]}
    venta_swing = {"velas": 80, "ref_cierre": 1.0, "ref_ts": 1, "setups_confirmados": [
        {"direccion": "short", "entrada": 1.001, "stop": 1.05, "tp": 0.9, "indice_conocido": 79, "indice_confirmacion": 75}]}
    o = simular({("AAA", "Intraday D"): cuerpo, ("BBB", "Intraday D"): lejos, ("CCC", "Swing (M)"): venta_swing, ("DDD", "Intraday D"): {}})
    k = {(x["simbolo"], x["temporalidad"]): x for x in o}
    assert set(k) == {("AAA", "Intraday D")}, k          # lejos del precio y venta de Swing: no se entregan
    assert (k[("AAA", "Intraday D")]["entrada"], k[("AAA", "Intraday D")]["stop"], k[("AAA", "Intraday D")]["tp"]) == (0.999, 0.995, 1.02)
    print("simular_despacho.demo() OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        clave = os.environ["MTB_SERVICE_KEY"]
        cuerpos = {c: b for c, b in bajar(clave).items() if "error" not in b}
        reporte = _get("/api/v1/reporte-setups", {}, clave)
        comparar(simular(cuerpos), reporte)
