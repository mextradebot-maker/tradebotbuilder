"""Cuenta del motor (paper trading): el motor opera lo que sugiere, sin forzar nada, para medir en vivo si la estrategia gana.

Reglas (las de la estrategia, no se tocan):
  - Solo los setups que el reporte presenta como vigentes (estructural: Diario y Swing; pasan api/reporte.setups_vigentes).
  - Orden LIMITE en la entrada exacta del setup, con SU stop y SU take profit (nunca entrada a mercado ni niveles distintos).
  - La orden vive EXPIRA velas de su temporalidad (igual que el robot, InpVelasExpiracion): si el precio no toca la entrada, expira.
  - Si el precio llega al TP antes de tocar la entrada, se cancela (la oportunidad paso). Si toca la entrada y luego el stop y el tp en la
    misma vela, cuenta el stop primero (lo conservador).
Resultado en R (1 R = distancia entrada-stop del setup); se guarda bruto y el resumen descuenta un costo supuesto por operacion.

La SALIDA es la del robot (MexTradeBot_SeguidorSMC.mq5), no un stop fijo:
  - Swing (S)/(M): SIN stop loss y SIN cierre forzado (decision de Ricardo, 25 nov 2026: el -3 % del capital es muy corto para swing;
    se espera que aguante el drawdown). Solo sale por el TP del motor. La orden queda "llena" mientras el precio no lo alcance.
    El EA v1.25 ya lo quita para Swing (SMC); Swing Express conserva el freno. Falta compilar y desplegar el robot en el VPS.
  - Diario (y el resto): stop del motor + escalera de breakeven: a +1 R el SL pasa a la entrada, a +1.5 R a +0.5 R, a +2 R a +1 R.
    (El trailing por estructura desde +2 R no se simula: solo mejoraria salidas ya protegidas.)
La cuenta de referencia es Ultra Low Micro (lotes minimos chicos): aqui todo va en R, sin lotes.

Se registra y resuelve una vez por reporte (cada vez que se pide /api/v1/reporte-setups con MOTOR_CUENTA=1 o ?cuenta_motor=1): para velas diarias,
semanales y mensuales una vez al dia basta. Es idempotente: repetir no duplica ordenes (clave unica por setup).
"""

from datetime import datetime, timedelta, timezone

TEMPORALIDADES_VALIDAS = ("Intraday D", "Swing (S)", "Swing (M)")
SWING = ("Swing (S)", "Swing (M)")
ESCALERA = ((1.0, 0.0), (1.5, 0.5), (2.0, 1.0))  # (cuando el precio llega a +x R, el SL pasa a +y R); no aplica a Swing
EXPIRA = 20          # velas
MERCADO = ("Intraday D",)   # entrada A MERCADO (v1.27 del robot): entra en la apertura de la vela siguiente, con el stop y TP del setup
COSTO_PCT = 0.015    # supuesto: spread + comision, % del precio por operacion
ABIERTOS = ("pendiente", "llena")


def clave(s: dict) -> str:
    return f"{s['direccion']}|{float(s['entrada']):.8g}|{float(s['stop']):.8g}"


def resolver_orden(o: dict, velas: list) -> tuple[str, float | None, int | None]:
    """Sigue UNA orden con las velas cerradas posteriores a su creacion: velas = [(abre, max, min, cierra)].
    Devuelve (estado, r, indice_de_llenado). Estados: pendiente | llena | gano | perdio | empate | cancelada_tp | expirada.
    Salida como el robot (ver arriba): Swing sin stop (solo TP); el resto con escalera de breakeven. En una misma vela el stop va
    antes que el TP, y la escalera solo sube despues de cerrar la vela (lo conservador)."""
    largo = o["direccion"] in ("compra", "long")
    e, sl, tp = o["entrada"], o["stop"], o["tp"]
    if o.get("temporalidad") in MERCADO:
        if not velas:
            return "pendiente", None, None
        e = velas[0][0]   # como el robot: a mercado, ahora; si el precio ya paso el stop o el TP, no entra
        if not ((sl < e < tp) if largo else (tp < e < sl)):
            return "expirada", 0.0, None
    riesgo = abs(e - sl)
    swing = o.get("temporalidad") in SWING
    sl_r = None if swing else -1.0   # donde esta el SL, en R respecto a la entrada (None = sin stop)
    mejor = 0.0                      # mejor ganancia flotante vista, en R
    sgn = 1 if largo else -1
    estado, llena_en = "pendiente", None
    for j, (_, h, l, _) in enumerate(velas):
        if estado == "pendiente":
            if j >= EXPIRA:
                return "expirada", 0.0, None
            if (l <= e) if largo else (h >= e):
                estado, llena_en = "llena", j
            elif (h >= tp) if largo else (l <= tp):
                return "cancelada_tp", 0.0, None
            else:
                continue
        if sl_r is not None:
            nivel_sl = e + sgn * sl_r * riesgo
            if (l <= nivel_sl) if largo else (h >= nivel_sl):
                return ("gano" if sl_r > 0 else "perdio" if sl_r < 0 else "empate"), sl_r, llena_en
        if (h >= tp) if largo else (l <= tp):
            return "gano", abs(tp - e) / riesgo, llena_en
        if sl_r is not None and j > (llena_en or 0):  # desde la vela siguiente a la del llenado: escalera de breakeven
            mejor = max(mejor, ((h - e) if largo else (e - l)) / riesgo)
            for a, b in ESCALERA:
                if mejor >= a and sl_r < b:
                    sl_r = b
    if estado == "pendiente" and len(velas) >= EXPIRA:
        return "expirada", 0.0, None
    return estado, None, llena_en


def registrar(repo, setups: list[dict], ahora: datetime) -> int:
    """Una orden por cada setup vigente de las temporalidades validas (los de intradia NUNCA entran). Idempotente."""
    nuevas = 0
    for s in setups:
        if s.get("temporalidad") not in TEMPORALIDADES_VALIDAS or s.get("aplica") is False:
            continue
        if s.get("direccion") not in ("compra", "venta") or not s.get("entrada") or not s.get("stop") or not s.get("tp"):
            continue
        if s["temporalidad"] in SWING and s["direccion"] == "venta":
            continue  # Swing solo compra
        e, sl = float(s["entrada"]), float(s["stop"])
        if e == sl:
            continue
        if repo.crear({"simbolo": s["simbolo"], "temporalidad": s["temporalidad"], "direccion": s["direccion"], "entrada": e,
                       "stop": sl, "tp": float(s["tp"]), "riesgo_pct": abs(e - sl) / e * 100, "clave": clave(s), "creada_en": ahora}):
            nuevas += 1
    return nuevas


def resolver(repo, velas_desde, ahora: datetime) -> int:
    """Actualiza las ordenes abiertas. velas_desde(simbolo, temporalidad, desde) -> [(abre, max, min, cierra)] de las velas CERRADAS
    cuya apertura es posterior a `desde` (la vela en curso al crear la orden no cuenta). Devuelve cuantas cambiaron."""
    cambios = 0
    for o in repo.abiertas():
        try:
            velas = velas_desde(o["simbolo"], o["temporalidad"], o["creada_en"])
        except Exception:
            continue  # sin velas hoy: se reintenta en el siguiente reporte
        estado, r, llena = resolver_orden(o, velas)
        if estado != o["estado"]:
            repo.actualizar(o["id"], estado, r, ahora if estado in ("gano", "perdio", "empate", "cancelada_tp", "expirada") else None,
                            ahora if estado in ("llena", "gano", "perdio", "empate") and o["estado"] == "pendiente" else None)
            cambios += 1
    return cambios


def resumen(repo) -> dict:
    filas = repo.todas()
    cerradas = [f for f in filas if f["estado"] in ("gano", "perdio", "empate")]
    neto = [f["r"] - COSTO_PCT / f["riesgo_pct"] for f in cerradas if f["r"] is not None]
    por = {}
    for f in filas:
        por[f["estado"]] = por.get(f["estado"], 0) + 1
    return {"ordenes": len(filas), "por_estado": por, "operaciones_cerradas": len(cerradas),
            "ganadas": sum(1 for f in cerradas if f["estado"] == "gano"), "perdidas": sum(1 for f in cerradas if f["estado"] == "perdio"),
            "empates": sum(1 for f in cerradas if f["estado"] == "empate"),
            "r_bruto": round(sum(f["r"] for f in cerradas if f["r"] is not None), 2), "r_neto": round(sum(neto), 2),
            "costo_supuesto_pct": COSTO_PCT,
            "abiertas": [{"simbolo": f["simbolo"], "temporalidad": f["temporalidad"], "direccion": f["direccion"], "estado": f["estado"]}
                         for f in filas if f["estado"] in ABIERTOS][:15]}


class MemRepo:
    def __init__(self):
        self.f, self.n = [], 0

    def crear(self, o):
        if any(x["simbolo"] == o["simbolo"] and x["temporalidad"] == o["temporalidad"] and x["clave"] == o["clave"] for x in self.f):
            return False
        self.n += 1
        self.f.append({**o, "id": self.n, "estado": "pendiente", "r": None})
        return True

    def abiertas(self):
        return [x for x in self.f if x["estado"] in ABIERTOS]

    def actualizar(self, id_, estado, r, cerrada_en, llena_en):
        x = next(x for x in self.f if x["id"] == id_)
        x["estado"], x["r"] = estado, r

    def todas(self):
        return list(self.f)


class RepoPg:
    COLS = "id, simbolo, temporalidad, direccion, entrada, stop, tp, riesgo_pct, estado, r, creada_en"

    def _filas(self, sql, par=()):
        from persistencia.conexion import get_conn

        with get_conn() as c:
            cols = self.COLS.split(", ")
            return [dict(zip(cols, f)) for f in c.execute(sql, par).fetchall()]

    def crear(self, o):
        from persistencia.conexion import get_conn

        with get_conn() as c:
            return c.execute(
                """INSERT INTO cuenta_motor (simbolo, temporalidad, direccion, entrada, stop, tp, riesgo_pct, clave, creada_en)
                   VALUES (%(simbolo)s, %(temporalidad)s, %(direccion)s, %(entrada)s, %(stop)s, %(tp)s, %(riesgo_pct)s, %(clave)s, %(creada_en)s)
                   ON CONFLICT (simbolo, temporalidad, clave) DO NOTHING""", o).rowcount == 1

    def abiertas(self):
        return self._filas(f"SELECT {self.COLS} FROM cuenta_motor WHERE estado IN ('pendiente', 'llena') ORDER BY id")

    def actualizar(self, id_, estado, r, cerrada_en, llena_en):
        from persistencia.conexion import get_conn

        with get_conn() as c:
            c.execute("UPDATE cuenta_motor SET estado = %s, r = %s, cerrada_en = COALESCE(%s, cerrada_en), llena_en = COALESCE(%s, llena_en) WHERE id = %s",
                      (estado, r, cerrada_en, llena_en, id_))

    def todas(self):
        return self._filas(f"SELECT {self.COLS} FROM cuenta_motor ORDER BY id")


def velas_xm(simbolo: str, temporalidad: str, desde: datetime) -> list:
    """Velas cerradas de la temporalidad posteriores a `desde`, del almacen (el mismo que usa el motor)."""
    import pandas as pd

    from conectividad import TEMPORALIDADES, obtener_velas
    from motor_smc.reglas import DURACION_VELA

    cfg = TEMPORALIDADES[temporalidad]
    fin = datetime.now(timezone.utc)
    ohlc = obtener_velas(simbolo, desde - timedelta(days=45), fin, intervalo=cfg["intervalo"])
    if ohlc is None or ohlc.empty:
        return []
    if ohlc.index[-1] + DURACION_VELA[cfg["vela"]] > pd.Timestamp(fin):  # la vela en formacion no esta cerrada
        ohlc = ohlc.iloc[:-1]
    ohlc = ohlc[ohlc.index > pd.Timestamp(desde)]  # solo velas que abren despues de crear la orden
    return [(r.open, r.high, r.low, r.close) for r in ohlc.itertuples()]


def correr(setups: list[dict], repo=None, velas_desde=velas_xm, ahora: datetime | None = None) -> dict:
    """Registra los setups de hoy, resuelve lo abierto y devuelve el resumen. Nunca propaga un fallo al reporte."""
    ahora = ahora or datetime.now(timezone.utc)
    repo = repo or RepoPg()
    nuevas = registrar(repo, setups, ahora)
    cambios = resolver(repo, velas_desde, ahora)
    return {**resumen(repo), "nuevas_hoy": nuevas, "actualizadas_hoy": cambios}


def demo() -> None:
    V = lambda *x: [(1, h, l, 1) for h, l in x]  # velas (max, min)
    o = {"direccion": "compra", "entrada": 100.0, "stop": 90.0, "tp": 130.0}
    # llena en la vela 1 (min 99), luego sube al tp: gana 3R
    assert resolver_orden(o, V((105, 101), (104, 99), (131, 110))) == ("gano", 3.0, 1)
    # el precio se va al tp sin tocar la entrada: cancelada, no se persigue
    assert resolver_orden(o, V((105, 101), (131, 102)))[0] == "cancelada_tp"
    # toca la entrada y luego el stop: pierde 1R
    assert resolver_orden(o, V((104, 99), (101, 89))) == ("perdio", -1.0, 0)
    # en la misma vela toca stop y tp: cuenta el stop (lo conservador)
    assert resolver_orden(o, V((131, 89))) == ("perdio", -1.0, 0)
    # llena y sigue abierta
    assert resolver_orden(o, V((104, 99), (110, 105))) == ("llena", None, 0)
    # sin tocar nada durante EXPIRA velas: expira; antes de eso sigue pendiente
    assert resolver_orden(o, V(*[(110, 105)] * 5))[0] == "pendiente"
    assert resolver_orden(o, V(*[(110, 105)] * EXPIRA))[0] == "expirada"
    # venta: espejo
    v = {"direccion": "venta", "entrada": 100.0, "stop": 110.0, "tp": 70.0}
    assert resolver_orden(v, V((101, 95), (105, 69))) == ("gano", 3.0, 0)

    # --- salida como el robot ---
    sw = {"direccion": "compra", "entrada": 100.0, "stop": 90.0, "tp": 130.0, "temporalidad": "Swing (M)"}
    # Swing: sin stop y sin cierre forzado. Aguanta caidas de -5 R y sigue abierta
    assert resolver_orden(sw, V((104, 99), (101, 50), (110, 40)))[0] == "llena"
    # ... y llega a su TP aunque haya pasado por -5 R (un stop de 1 R lo habria sacado)
    assert resolver_orden(sw, V((104, 99), (101, 50), (131, 90))) == ("gano", 3.0, 0)
    # Swing de venta: espejo
    sv = {"direccion": "venta", "entrada": 100.0, "stop": 110.0, "tp": 70.0, "temporalidad": "Swing (S)"}
    assert resolver_orden(sv, V((101, 95), (160, 99), (99, 69))) == ("gano", 3.0, 0)
    # Diario: entra A MERCADO en la apertura de la primera vela (aqui 100); escalera de breakeven.
    di = {**sw, "temporalidad": "Intraday D"}
    VM = lambda *x: [(a, h, l, 1) for a, h, l in x]  # velas (abre, max, min)
    # a +1 R el SL pasa a la entrada: si luego vuelve, sale en 0 (empate), no en -1 R
    assert resolver_orden(di, VM((100, 104, 99), (100, 110, 101), (100, 105, 99.5))) == ("empate", 0.0, 0)
    # a +2 R el SL pasa a +1 R: vuelve y sale ganando 1 R
    assert resolver_orden(di, VM((100, 104, 99), (100, 121, 101), (100, 110, 109))) == ("gano", 1.0, 0)
    # la vela de entrada no cuenta para la escalera (no se sabe en que orden paso): si en esa vela llego a +1 R y cae, pierde -1 R
    assert resolver_orden(di, VM((100, 112, 99), (100, 101, 89))) == ("perdio", -1.0, 0)
    # a mercado no espera retroceso: entra en la apertura aunque la entrada del setup (100) no se toque nunca, y llega al TP
    assert resolver_orden(di, VM((108, 131, 107))) == ("gano", abs(130 - 108) / abs(108 - 90), 0)
    # si la apertura ya paso el stop o el TP del setup, no entra
    assert resolver_orden(di, VM((135, 140, 134)))[0] == "expirada" and resolver_orden(di, VM((85, 95, 80)))[0] == "expirada"
    assert resolver_orden(di, [])[0] == "pendiente"

    ahora = datetime(2026, 11, 25, tzinfo=timezone.utc)
    rp = MemRepo()
    setups = [{"simbolo": "EURJPY", "temporalidad": "Swing (M)", "direccion": "compra", "entrada": 164.89, "stop": 150.98, "tp": 187.94, "aplica": True},
              {"simbolo": "EURGBP", "temporalidad": "Scalping 15m", "direccion": "venta", "entrada": 0.8371, "stop": 0.8378, "tp": 0.8351, "aplica": False},
              {"simbolo": "CADJPY", "temporalidad": "Intraday D", "direccion": "venta", "entrada": 111.97, "stop": 113.41, "tp": 110.15}]
    assert registrar(MemRepo(), [{"simbolo": "USDCHF", "temporalidad": "Swing (M)", "direccion": "venta", "entrada": 0.89, "stop": 0.93, "tp": 0.76}], ahora) == 0
    assert registrar(rp, setups, ahora) == 2, rp.f  # el intradia NUNCA entra
    assert registrar(rp, setups, ahora) == 0       # idempotente
    assert {x["simbolo"] for x in rp.f} == {"EURJPY", "CADJPY"} and rp.f[0]["entrada"] == 164.89  # entrada exacta del setup
    pr = {"EURJPY": V((170, 164), (190, 168)), "CADJPY": [(112, 113, 111, 112)]}  # EURJPY llena y llega al tp; CADJPY llena y sigue abierta
    assert resolver(rp, lambda s, t, d: pr[s], ahora) == 2
    res = resumen(rp)
    assert res["operaciones_cerradas"] == 1 and res["ganadas"] == 1 and res["r_bruto"] == round(abs(187.94 - 164.89) / abs(164.89 - 150.98), 2)
    assert res["por_estado"] == {"gano": 1, "llena": 1} and res["abiertas"][0]["simbolo"] == "CADJPY"
    assert res["r_neto"] <= res["r_bruto"]
    # un fallo de velas no rompe: la orden queda como esta
    assert resolver(rp, lambda s, t, d: 1 / 0, ahora) == 0
    print("cuenta_motor.demo() OK")


if __name__ == "__main__":
    demo()
