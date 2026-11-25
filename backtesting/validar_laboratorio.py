"""Validacion de la estrategia SOLO con lo que entrega el laboratorio (velas XM, no Dukascopy):

  /api/setups     -> los setups validos de cada combinacion (entrada, stop, tp, indices, resultado del motor)
  /api/v1/grafica -> las velas cerradas XM de esa serie (las mismas que analizo el motor; mismo indexado)

Con eso mide (1) a que distancia del precio nace cada setup y si se llena, (2) si la estrategia es rentable y (3) si el problema es el
DISENO de la entrada (orden limite que casi nunca se llena) simulando otras entradas con las mismas reglas de SL/TP.

Uso:  MTB_SERVICE_KEY=... python -m backtesting.validar_laboratorio [descarga|analiza|todo|demo]   (MTB_LAB_URL cambia el servidor)
Los datos bajados quedan en backtesting/validar_laboratorio.json (no se versiona).
"""
import concurrent.futures as cf
import json
import os
import statistics as st
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("MTB_LAB_URL", "https://chatbotventas-mtb-api-preview.h0w0dc.easypanel.host")
ARCHIVO = os.path.join(os.path.dirname(__file__), "validar_laboratorio.json")
EXPIRA = 20      # velas que el robot mantiene su orden pendiente (InpVelasExpiracion)
HORIZONTE = 300  # velas maximas para resolver una operacion simulada
TEMPS = ("Scalping 15m", "Intraday 1H", "Intraday 4H", "Intraday D", "Swing (S)", "Swing (M)")


def _get(ruta: str, qs: dict, clave: str):
    r = urllib.request.Request(f"{BASE}{ruta}?{urllib.parse.urlencode(qs)}", headers={"X-MTB-Service-Key": clave, "User-Agent": "mtb-validacion"})
    return json.load(urllib.request.urlopen(r, timeout=120))


def descargar(clave: str) -> dict:
    cat = json.load(urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/catalogo", headers={"User-Agent": "mtb-validacion"}), timeout=60))
    combos = sorted({(a["simbolo"], a["temporalidad"]) for a in cat["activos_todos"]})

    def uno(c):
        try:
            s = _get("/api/setups", {"simbolo": c[0], "temporalidad": c[1]}, clave)
            g = _get("/api/v1/grafica", {"simbolo": c[0], "temporalidad": c[1], "limite": 6000}, clave)
            return "|".join(c), {"n": s.get("velas"), "ref": s.get("ref_cierre"), "setups": [x for x in s.get("setups") or [] if x.get("valido")],
                                 "velas": [[v["o"], v["h"], v["l"], v["c"]] for v in g["velas"]]}
        except Exception as e:
            return "|".join(c), {"error": f"{type(e).__name__}: {e}"}
    with cf.ThreadPoolExecutor(4) as ex:
        datos = dict(ex.map(uno, combos))
    json.dump(datos, open(ARCHIVO, "w"))
    return datos


def sim_limite(v, k0, lado, entrada, stop, tp, expira=EXPIRA):
    """Orden limite puesta al conocerse el setup (vela k0): se llena si el precio la toca antes de `expira` velas; se cancela si toca el
    TP antes. Devuelve (estado, R): gano|perdio|cancelado_tp|expirado|abierto. En una misma vela el stop va antes que el TP."""
    riesgo = abs(entrada - stop)
    lleno = False
    for j in range(k0 + 1, min(len(v), k0 + 1 + HORIZONTE)):
        _, h, l, _ = v[j]
        if not lleno:
            if j > k0 + expira:
                return "expirado", 0.0
            if (l <= entrada) if lado == "long" else (h >= entrada):
                lleno = True
            elif (h >= tp) if lado == "long" else (l <= tp):
                return "cancelado_tp", 0.0
            else:
                continue
        if (l <= stop) if lado == "long" else (h >= stop):
            return "perdio", -1.0
        if (h >= tp) if lado == "long" else (l <= tp):
            return "gano", abs(tp - entrada) / riesgo
    return "abierto", 0.0


def sim_mercado(v, k0, lado, stop, tp):
    """Entrada a mercado al cierre de la vela donde se conoce el setup, con el mismo SL y TP."""
    c = v[k0][3]
    if not (stop < c < tp if lado == "long" else tp < c < stop):
        return "invalido", 0.0
    riesgo = abs(c - stop)
    for j in range(k0 + 1, min(len(v), k0 + 1 + HORIZONTE)):
        _, h, l, _ = v[j]
        if (l <= stop) if lado == "long" else (h >= stop):
            return "perdio", -1.0
        if (h >= tp) if lado == "long" else (l <= tp):
            return "gano", abs(tp - c) / riesgo
    return "abierto", 0.0


def alineado(d: dict) -> tuple[int, int]:
    """(coinciden, probados): zona_extremo debe caer dentro de la vela indice_zona; si no, velas y setups no estan alineados."""
    ok = tot = 0
    off = d["n"] - len(d["velas"])  # el grafico trae las ultimas velas; el setup cuenta desde el inicio de la serie del motor
    for s in d["setups"]:
        i = s.get("indice_zona")
        if i is None or s.get("zona_extremo") is None or not 0 <= i - off < len(d["velas"]):
            continue
        _, h, l, _ = d["velas"][i - off]
        tot += 1
        ok += l * 0.9995 <= s["zona_extremo"] <= h * 1.0005
    return ok, tot


def filas(datos: dict) -> list[dict]:
    out = []
    for clave, d in datos.items():
        if "error" in d or not d.get("velas"):
            continue
        sim, temp = clave.split("|")
        off = d["n"] - len(d["velas"])
        v = d["velas"]
        for s in d["setups"]:
            k0 = s.get("indice_conocido")
            if k0 is None or k0 - off < 0 or not s.get("tp"):
                continue
            k = k0 - off
            lado, e, sl, tp = s["direccion"], s["entrada"], s["stop"], s["tp"]
            if abs(e - sl) == 0 or k >= len(v) - 1 or (temp in ("Swing (S)", "Swing (M)") and lado == "short"):
                continue  # Swing solo compra
            medio = (v[k][3] + e) / 2
            sirve = sl < medio < tp if lado == "long" else tp < medio < sl
            out.append({"sim": sim, "temp": temp, "motor": s.get("resultado"), "tipo": s.get("tipo"), "lado": lado,
                        "riesgo_pct": abs(e - sl) / e * 100, "dist_r": abs(e - v[k][3]) / abs(e - sl), "dist_pct": abs(e - v[k][3]) / v[k][3] * 100,
                        "limite": sim_limite(v, k, lado, e, sl, tp),
                        "medio": sim_limite(v, k, lado, medio, sl, tp) if sirve else ("invalido", 0.0),
                        "mercado": sim_mercado(v, k, lado, sl, tp)})
    return out


def _cerradas(x, variante):
    return [r[variante] for r in x if r[variante][0] in ("gano", "perdio")]


def resumen(x: list[dict], variante: str, costo_r=0.0) -> str:
    c = _cerradas(x, variante)
    if not c:
        return "sin operaciones"
    n, g = len(c), sum(1 for e, _ in c if e == "gano")
    bruta = sum(R for _, R in c) / n
    return f"{n:4} ops ({100 * n / len(x):3.0f}% de los setups)  win {100 * g / n:3.0f}%  R medio {bruta:+.3f}  neto@{costo_r:g}R {bruta - costo_r:+.3f}  total {sum(R for _, R in c):+7.1f}R"


def analiza(datos: dict) -> None:
    pares = [alineado(d) for d in datos.values() if "velas" in d]
    ok, tot = sum(a for a, _ in pares), sum(b for _, b in pares)
    print(f"alineacion velas/setups: {ok}/{tot} zonas coinciden con su vela ({100 * ok / max(tot, 1):.0f}%)   combos con error: {sum('error' in d for d in datos.values())}")
    x = filas(datos)
    print(f"setups simulados: {len(x)}")
    comp = [(r["motor"], r["limite"][0]) for r in x if r["motor"] in ("gano", "perdio", "cancelado_tp", "expirado")]
    print(f"el simulador reproduce al motor (mismo resultado): {100 * sum(a == b for a, b in comp) / max(len(comp), 1):.0f}% de {len(comp)}")
    for nombre, var in (("LIMITE en la entrada actual (diseno actual)", "limite"), ("LIMITE a mitad de camino entre precio y entrada", "medio"),
                        ("MERCADO al conocerse el setup", "mercado")):
        print(f"\n{nombre}")
        for c in (0, 0.05, 0.10):
            print("   " + resumen(x, var, c))
    print("\nPOR TEMPORALIDAD, R medio bruto (operaciones):   limite  |  medio  |  mercado")
    for t in TEMPS:
        y = [r for r in x if r["temp"] == t]

        def f(var):
            c = _cerradas(y, var)
            return f"{sum(R for _, R in c) / len(c):+.2f} ({len(c):3})" if c else "    -     "
        print(f"  {t:13} n={len(y):4}   {f('limite')} | {f('medio')} | {f('mercado')}")
    print("\nLLENADO Y RESULTADO SEGUN DISTANCIA AL PRECIO AL DETECTAR (veces el riesgo):")
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 1e9)):
        y = [r for r in x if lo <= r["dist_r"] < hi]
        ll = [r for r in y if r["limite"][0] in ("gano", "perdio")]
        print(f"  {lo:g}-{hi:g}R: n={len(y):4} llenadas {100 * len(ll) / max(len(y), 1):3.0f}%  R medio de las llenadas {sum(r['limite'][1] for r in ll) / max(len(ll), 1):+.2f}")
    print("\nDISTANCIA AL PRECIO AL DETECTAR, mediana / p90 (y riesgo mediano del setup):")
    for t in TEMPS:
        d = sorted(r["dist_pct"] for r in x if r["temp"] == t)
        if d:
            print(f"  {t:13} {st.median(d):6.2f}% / {d[int(0.9 * (len(d) - 1))]:6.2f}%   riesgo {st.median(r['riesgo_pct'] for r in x if r['temp'] == t):.2f}%")
    print("\nPOR TIPO (limite): ops, R medio")
    for tipo in sorted({r["tipo"] for r in x}, key=str):
        c = _cerradas([r for r in x if r["tipo"] == tipo], "limite")
        if c:
            print(f"  {str(tipo):13} {len(c):4} ops  R medio {sum(R for _, R in c) / len(c):+.3f}")


def demo() -> None:
    sube = [[1, 1.0, 1.0, 1.0]] * 3 + [[1, 1.2, 0.99, 1.1]] + [[1, 3.2, 1.1, 3.0]]  # k0=2: limite 1.0 se toca en la vela 3, tp 3.0 en la 4
    assert sim_limite(sube, 2, "long", 1.0, 0.5, 3.0) == ("gano", 4.0), sim_limite(sube, 2, "long", 1.0, 0.5, 3.0)
    huye = [[1, 1.1, 1.05, 1.1]] * 3 + [[1, 3.1, 1.1, 3.0]]  # nunca baja a 1.0 y llega al tp: se cancela
    assert sim_limite(huye, 2, "long", 1.0, 0.5, 3.0)[0] == "cancelado_tp"
    stop = [[1, 1, 1, 1]] * 3 + [[1, 1.1, 0.4, 0.5]]
    assert sim_limite(stop, 2, "long", 1.0, 0.5, 3.0) == ("perdio", -1.0)
    m = sim_mercado(huye, 2, "long", 0.5, 3.0)
    assert m[0] == "gano" and abs(m[1] - (3.0 - 1.1) / 0.6) < 1e-9, m
    assert sim_limite([[1, 1, 1, 1]] * 40, 2, "short", 1.5, 2.0, 0.5)[0] == "expirado"
    print("validar_laboratorio.demo() OK")


# ---- Comparacion ANTES (todas las temporalidades) vs DESPUES (solo Diario + Swing), por ano y fuera de muestra ----
DIAS = {"Scalping 15m": 60, "Intraday 1H": 365, "Intraday 4H": 365, "Intraday D": 1095, "Swing (S)": 1095, "Swing (M)": 2555}
FOCO = ("Intraday D", "Swing (S)", "Swing (M)")
COSTO_PCT = 0.015  # supuesto: spread + comision ~0.015 % del precio por operacion


def operaciones(datos: dict, variante: str) -> list[dict]:
    """Operaciones cerradas simuladas con su fecha aproximada (dias atras) y R neto de costo."""
    ops = []
    for clave, d in datos.items():
        if "error" in d or not d.get("velas"):
            continue
        sim, temp = clave.split("|")
        v, n = d["velas"], d["n"]
        off = n - len(v)
        for s in d["setups"]:
            k0 = s.get("indice_conocido")
            if k0 is None or k0 - off < 0 or not s.get("tp"):
                continue
            k = k0 - off
            lado, e, sl, tp = s["direccion"], s["entrada"], s["stop"], s["tp"]
            if abs(e - sl) == 0 or k >= len(v) - 1 or (temp in ("Swing (S)", "Swing (M)") and lado == "short"):
                continue  # Swing solo compra
            abierta = False
            if variante == "robot":
                from cuenta_motor import resolver_orden

                est, R, _ = resolver_orden({"direccion": lado, "entrada": e, "stop": sl, "tp": tp, "temporalidad": temp},
                                           [tuple(x) for x in v[k + 1:]])
                if est == "llena":  # sigue abierta: a precio de mercado con la ultima vela
                    abierta, est = True, "gano" if (v[-1][3] - e) * (1 if lado == "long" else -1) > 0 else "perdio"
                    R = (v[-1][3] - e) * (1 if lado == "long" else -1) / abs(e - sl)
                elif est == "empate":
                    est = "perdio"  # R = 0: cuenta como operacion cerrada sin ganancia
            elif variante == "limite":
                est, R = sim_limite(v, k, lado, e, sl, tp)
            elif variante == "mercado":
                est, R = sim_mercado(v, k, lado, sl, tp)
            else:
                medio = (v[k][3] + e) / 2
                est, R = sim_limite(v, k, lado, medio, sl, tp) if (sl < medio < tp if lado == "long" else tp < medio < sl) else ("invalido", 0.0)
            if est in ("gano", "perdio"):
                riesgo_pct = abs(e - sl) / e * 100
                ops.append({"temp": temp, "sim": sim, "dias_atras": DIAS[temp] * (n - 1 - k0) / n, "R": R - COSTO_PCT / riesgo_pct,
                            "abierta": abierta})
    return ops


def metricas(ops: list[dict], anios: dict) -> dict:
    """anios: {temporalidad: anos de historia usados}. R/ano suma cada temporalidad sobre SU propia ventana."""
    if not ops:
        return {"n": 0}
    orden = sorted(ops, key=lambda o: -o["dias_atras"])  # de la mas antigua a la mas reciente
    eq = pico = dd = racha = peor = 0.0
    for o in orden:
        eq += o["R"]
        pico = max(pico, eq)
        dd = max(dd, pico - eq)
        racha = racha + 1 if o["R"] < 0 else 0
        peor = max(peor, racha)
    por_anio = sum(sum(o["R"] for o in ops if o["temp"] == t) / a for t, a in anios.items())
    ops_anio = sum(sum(1 for o in ops if o["temp"] == t) / a for t, a in anios.items())
    return {"n": len(ops), "abiertas": sum(1 for o in ops if o.get("abierta")),
            "win": 100 * sum(o["R"] > 0 for o in ops) / len(ops), "R_op": sum(o["R"] for o in ops) / len(ops),
            "R_anio": por_anio, "ops_anio": ops_anio, "dd": dd, "racha": peor}


def linea(nombre: str, m: dict) -> str:
    if not m["n"]:
        return f"  {nombre:34} sin operaciones"
    return (f"  {nombre:34} {m['ops_anio']:5.0f} ops/ano  win {m['win']:3.0f}%  R/op {m['R_op']:+.3f}  "
            f"R/ANO {m['R_anio']:+6.1f}  caida maxima {m['dd']:4.1f}R  peor racha {m['racha']:.0f} perdidas")


def swing_exposicion(datos: dict) -> None:
    """Swing no lleva stop ni cierre forzado: lo que importa es CUANTO drawdown flotante aguanta antes de llegar al TP (o al fin de los
    datos). Peor excursion adversa en R del setup y en % del precio de entrada, por posicion llenada."""
    filas_ = []
    for clave, d in datos.items():
        sim, temp = clave.split("|")
        if temp not in ("Swing (S)", "Swing (M)") or "velas" not in d:
            continue
        v, off = d["velas"], d["n"] - len(d["velas"])
        for s in d["setups"]:
            k0 = s.get("indice_conocido")
            if k0 is None or k0 - off < 0 or not s.get("tp") or s["entrada"] == s["stop"]:
                continue
            k, e, sl, tp, largo = k0 - off, s["entrada"], s["stop"], s["tp"], s["direccion"] == "long"
            if k >= len(v) - 1 or not largo:  # Swing solo compra
                continue
            lleno, peor, fin = False, 0.0, "abierta"
            for j in range(k + 1, len(v)):
                _, h, l, _ = v[j]
                if not lleno:
                    if j > k + 20:
                        break
                    if (l <= e) if largo else (h >= e):
                        lleno = True
                    elif (h >= tp) if largo else (l <= tp):
                        break
                    else:
                        continue
                peor = max(peor, (e - l) if largo else (h - e))
                if (h >= tp) if largo else (l <= tp):
                    fin = "tp"
                    break
            if lleno:
                filas_.append({"sim": sim, "temp": temp, "fin": fin, "mae_r": peor / abs(e - sl), "mae_pct": peor / e * 100,
                               "ultimo_r": ((v[-1][3] - e) if largo else (e - v[-1][3])) / abs(e - sl)})
    print(f"  posiciones de Swing llenadas: {len(filas_)}  (llegaron al TP: {sum(f['fin'] == 'tp' for f in filas_)}, siguen abiertas: {sum(f['fin'] == 'abierta' for f in filas_)})")
    for etiqueta, grupo in (("llegaron al TP", [f for f in filas_ if f["fin"] == "tp"]), ("siguen abiertas", [f for f in filas_ if f["fin"] == "abierta"])):
        if grupo:
            mr = sorted(f["mae_r"] for f in grupo)
            mp = sorted(f["mae_pct"] for f in grupo)
            q = lambda xs, p_: xs[int(p_ * (len(xs) - 1))]
            print(f"  {etiqueta:16} n={len(grupo):2}  peor drawdown flotante: mediana {q(mr, .5):4.1f} R / {q(mp, .5):4.1f} %   p90 {q(mr, .9):4.1f} R / {q(mp, .9):4.1f} %   max {mr[-1]:4.1f} R / {mp[-1]:4.1f} %")
    ab = [f for f in filas_ if f["fin"] == "abierta"]
    if ab:
        print("  las que siguen abiertas, hoy: " + ", ".join(f"{f['sim']} {f['temp'][-2]} {f['ultimo_r']:+.1f}R" for f in ab))


def compara_enfoque(datos: dict) -> None:
    anios = {t: DIAS[t] / 365 for t in DIAS}
    print("COSTOS descontados: 0.015 % del precio por operacion. R/ANO = suma de R por ano sobre las 34 activos de cada temporalidad.\n")
    for var, nom in (("limite", "entrada LIMITE (la actual)"), ("mercado", "entrada A MERCADO"), ("medio", "entrada LIMITE A MITAD")):
        ops = operaciones(datos, var)
        print(f"== {nom}")
        print(linea("ANTES: todas las temporalidades", metricas(ops, anios)))
        print(linea("DESPUES: solo Diario + Swing S/M", metricas([o for o in ops if o["temp"] in FOCO], {t: anios[t] for t in FOCO})))
        print(linea("  lo que se descarta (15m/1H/4H)", metricas([o for o in ops if o["temp"] not in FOCO], {t: anios[t] for t in DIAS if t not in FOCO})))
        print()
    print("== FUERA DE MUESTRA: se ELIGE con la mitad antigua de cada serie y se PRUEBA en la mitad reciente (entrada limite)")
    for var in ("limite", "mercado"):
        ops = operaciones(datos, var)
        elegidas = []
        for t in DIAS:
            ant = [o for o in ops if o["temp"] == t and o["dias_atras"] > DIAS[t] / 2]
            if len(ant) >= 10 and sum(o["R"] for o in ant) / len(ant) > 0:
                elegidas.append(t)
        rec = [o for o in ops if o["dias_atras"] <= DIAS[o["temp"]] / 2]
        mitad = {t: anios[t] / 2 for t in DIAS}
        print(f" entrada {var}: con la mitad antigua habria elegido {elegidas}")
        print(linea("  mitad reciente, TODAS", metricas(rec, mitad)))
        print(linea("  mitad reciente, solo lo elegido", metricas([o for o in rec if o["temp"] in elegidas], {t: mitad[t] for t in elegidas})))
        print(linea("  mitad reciente, solo Diario+Swing", metricas([o for o in rec if o["temp"] in FOCO], {t: mitad[t] for t in FOCO})))
    print("\n== COMO OPERAN LOS ROBOTS (Swing sin stop ni cierre forzado, solo TP; Diario con escalera de breakeven; abiertas a precio de mercado)")
    ops = operaciones(datos, "robot")
    print(linea("TODAS las temporalidades", metricas(ops, anios)))
    print(linea("Diario + Swing S/M", metricas([o for o in ops if o["temp"] in FOCO], {t: anios[t] for t in FOCO})))
    for t in FOCO:
        m = metricas([o for o in ops if o["temp"] == t], {t: anios[t]})
        print(linea(f"  {t}", m) + (f"  ({m['abiertas']} abiertas)" if m["n"] else ""))
    print("\n== SWING: DRAWDOWN QUE AGUANTA (sin stop ni cierre forzado)")
    swing_exposicion(datos)
    print("\n== POR TEMPORALIDAD (entrada limite, neto): ops/ano y R/ano")
    ops = operaciones(datos, "limite")
    for t in DIAS:
        m = metricas([o for o in ops if o["temp"] == t], {t: anios[t]})
        print(linea(f"  {t}", m))


def resumen_publico(datos: dict, fecha: str) -> dict:
    """Lo que muestra el panel en 'Mis validaciones': por temporalidad, setups analizados y resultado simulado (neto de costo)
    con la entrada actual (limite) y a mercado. Se guarda en public/validacion-smc.json (python -m backtesting.validar_laboratorio publica)."""
    x = filas(datos)
    por = {}
    for var in ("robot", "mercado"):
        for o in operaciones(datos, var):
            por.setdefault((o["temp"], var), []).append(o["R"])
    salida = []
    for t in TEMPS:
        def b(var):
            r = por.get((t, var), [])
            return {"ops": len(r), "acierto": round(sum(1 for v in r if v > 0) / len(r), 3) if r else None,
                    "r_op": round(sum(r) / len(r), 3) if r else None}
        lim, mer = b("robot"), b("mercado")
        valida = t in FOCO
        salida.append({"temporalidad": t, "setups": sum(1 for r in x if r["temp"] == t), "robot": lim, "mercado": mer, "valida": valida,
                       "muestra_limitada": valida and min(lim["ops"], mer["ops"]) < 30})
    return {"fecha": fecha, "estrategia": "SMC / Estructural (motor v2)", "fuente": "velas XM, 34 activos, setups validos del motor",
            "costo_pct": COSTO_PCT, "salida": "como opera el robot: Swing sin stop (solo TP), Diario con escalera de breakeven; "
            "posiciones abiertas a precio de mercado", "filas": salida}


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if modo == "demo":
        demo()
    else:
        datos = descargar(os.environ["MTB_SERVICE_KEY"]) if modo in ("descarga", "todo") else json.load(open(ARCHIVO))
        if modo in ("analiza", "todo"):
            analiza(datos)
        if modo in ("enfoque", "todo"):
            compara_enfoque(datos)
        if modo == "publica":
            import datetime
            destino = os.path.join(os.path.dirname(__file__), "..", "public", "validacion-smc.json")
            json.dump(resumen_publico(datos, datetime.date.today().isoformat()), open(destino, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("escrito", os.path.normpath(destino))
