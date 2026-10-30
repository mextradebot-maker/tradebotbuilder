"""GET /api/catalogo — simbolos del catalogo (fecha_inicio) + `activos_todos`: una fila por
(simbolo, temporalidad) con el backtest REAL del motor v2 en la direccion que operaria el robot.

Fuente de la expectativa: la validacion de laboratorio (tabla validacion_lab, ver validacion_lab.py). El backtest bruto del motor
(backtest_largo o, si no hay, el snapshot de /api/setups) solo se informa en `backtest_motor`. Sin validacion = sin expectativa
y veredicto INSUFICIENTE; nunca se inventan cifras. Ranking: limite inferior del IC 95 % (frena la "maldicion del ganador").
"""

SWING = {"Swing (S)", "Swing (M)"}  # swing solo opera compras

# Nombre del simbolo en el MT5 de XM. SOLO valores verificados; el resto None hasta exportar
# la lista de simbolos del MT5 del VPS (pendiente de Ricardo).
_FOREX = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP",
          "EURJPY", "GBPJPY", "AUDJPY", "EURAUD", "AUDCAD", "NZDJPY", "CADJPY")
# Verificados 3 oct 2026 con exportar_simbolos_xm.py en el VPS (1639 simbolos de XM).
# Los 34 del catalogo tienen nombre XM (AMXL y CEMEXCPO se retiraron el 3 oct 2026: XM solo tiene sus ADR en USD).
SIMBOLO_XM = {
    **{s: s for s in _FOREX},
    "XAUUSD": "GOLD", "XAGUSD": "SILVER", "XPTUSD": "XPTUSD", "XPDUSD": "XPDUSD",
    "US30": "US30Cash", "US100": "US100Cash", "US500": "US500Cash", "GER40": "GER40Cash", "UK100": "UK100Cash", "JP225": "JP225Cash",
    "WTIUSD": "OILCash", "BRENTUSD": "BRENTCash",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD", "XRPUSD": "XRPUSD",
    "GOOGL": "Google", "NVDA": "Nvidia", "META": "Facebook", "WMT": "WalMart",
}

# Nombre legible, copia del catalogo de public/index.html (demo() verifica que sigan iguales).
NOMBRE_ACTIVO = {
    "EURUSD": "Euro / Dólar", "GBPUSD": "Libra / Dólar", "USDJPY": "Dólar / Yen", "AUDUSD": "Dólar australiano / Dólar",
    "USDCAD": "Dólar / Dólar canadiense", "USDCHF": "Dólar / Franco suizo", "NZDUSD": "Dólar neozelandés / Dólar",
    "EURGBP": "Euro / Libra", "EURJPY": "Euro / Yen", "GBPJPY": "Libra / Yen", "AUDJPY": "Dólar australiano / Yen",
    "EURAUD": "Euro / Dólar australiano", "AUDCAD": "Dólar australiano / Dólar canadiense",
    "NZDJPY": "Dólar neozelandés / Yen", "CADJPY": "Dólar canadiense / Yen",
    "XAUUSD": "Oro", "XAGUSD": "Plata", "XPTUSD": "Platino", "XPDUSD": "Paladio",
    "US30": "Dow Jones 30", "US100": "Nasdaq 100", "US500": "S&P 500", "GER40": "DAX 40", "UK100": "FTSE 100",
    "JP225": "Nikkei 225", "WTIUSD": "Petróleo WTI", "BRENTUSD": "Petróleo Brent",
    "BTCUSD": "Bitcoin", "ETHUSD": "Ethereum", "XRPUSD": "XRP",
    "GOOGL": "Alphabet / Google", "NVDA": "NVIDIA", "META": "Meta Platforms", "WMT": "Walmart",
}


def robot_legible(simbolo: str, temporalidad: str) -> dict:
    """{simbolo, temporalidad, nombre, etiqueta: "Oro (XAUUSD) · Swing (S)", simbolo_xm} para botones de descarga."""
    nombre = NOMBRE_ACTIVO.get(simbolo) or simbolo
    return {"simbolo": simbolo, "temporalidad": temporalidad, "nombre": nombre,
            "etiqueta": f"{nombre} ({simbolo}) · {temporalidad}" if nombre != simbolo else f"{simbolo} · {temporalidad}",
            "simbolo_xm": SIMBOLO_XM.get(simbolo) or ""}


def _iso(t):
    return t.isoformat() if hasattr(t, "isoformat") else t


def fila(simbolo, temporalidad, tendencia, refrescado_en, bt_snapshot, bt_largo, largo_en, validacion=None, validado_en=None) -> dict:
    """Una fila por (simbolo, temporalidad). La expectativa que se muestra es SOLO la de la validacion de laboratorio
    (validacion_lab.py: una operacion a la vez, spread, dos mitades, minimo de operaciones). Sin validacion (todavia no
    corre) no hay expectativa ni recomendacion: el backtest bruto del motor queda aparte en `backtest_motor`."""
    direccion = "compra" if temporalidad in SWING else tendencia
    largo = (bt_largo or {}).get(direccion) or {}
    rep = largo if largo.get("n_setups") else ((bt_snapshot or {}).get(direccion) or {})
    bruto = {"n_setups": rep.get("n_setups") or 0, "expectativa_r": rep.get("expectativa_r") if rep.get("n_setups") else None,
             "fuente": "backtest largo" if largo.get("n_setups") else "backtest reciente"}
    v = validacion or {}
    estado = v.get("estado") or "pendiente"
    veredicto = {"validado": "RECOMENDABLE", "no_validado": "NO RECOMENDABLE"}.get(estado, "INSUFICIENTE")
    if v:
        n = v["n"]
        desc = (f"Laboratorio: {n} operaciones simuladas como las opera el robot (una a la vez, spread {v['spread_fuente']}"
                f" {v['spread_pct'] * 100:.3f} %)")
        if v.get("r_neto") is not None:
            desc += (f", acierto {round((v.get('winrate') or 0) * 100)}%, expectativa neta {v['r_neto']:+.2f}R"
                     f" (mitades {v['mitad_1']:+.2f}R / {v['mitad_2']:+.2f}R). ")
        else:
            desc += ". "
        desc += v["motivo"]
    else:
        n = 0
        desc = "Pendiente de validar en el laboratorio: hasta entonces no hay expectativa confiable para este activo."
    return {
        "simbolo": simbolo, "simbolo_xm": SIMBOLO_XM.get(simbolo), "temporalidad": temporalidad,
        "tendencia": tendencia, "direccion": direccion,
        "winrate": v.get("winrate"), "expectativa_r": v.get("r_neto"), "r_total": v.get("r_total_neto") if v else None, "n_setups": n,
        "viable": bool(v.get("validado")), "veredicto": veredicto, "descripcion_larga": desc,
        "fuente": "validacion de laboratorio" if v else None, "estado_validacion": estado,
        "r_bruto": v.get("r_bruto"), "mitad_1": v.get("mitad_1"), "mitad_2": v.get("mitad_2"), "ic95_bajo": v.get("ic95_bajo"),
        "oportunidades": v.get("oportunidades"), "llenadas": v.get("llenadas"), "nota_validacion": v.get("nota"),
        "backtest_motor": bruto, "validado_en": _iso(validado_en),
        # actualizado_en = último análisis en vivo (smc_snapshot); el backtest largo va aparte
        "actualizado_en": _iso(refrescado_en), "backtest_en": _iso(largo_en if bruto["n_setups"] and bruto["fuente"] == "backtest largo" else refrescado_en),
    }


def armar(filas_db) -> list[dict]:
    filas = [fila(*f) for f in filas_db]
    for i, f in enumerate(sorted((f for f in filas if f["viable"]), key=lambda f: -f["ic95_bajo"]), 1):
        f["rank"] = i
    return sorted(filas, key=lambda f: (f.get("rank") or 10**6, f["simbolo"], f["temporalidad"]))


def procesar(_payload: dict) -> tuple[int, dict]:
    try:
        import persistencia as _p
    except Exception:
        return 503, {"error": "base de datos no disponible"}
    try:
        entradas = _p.listar_catalogo()
        activos = armar(_p.filas_catalogo())
    except Exception as e:
        return 500, {"error": str(e)}
    return 200, {"catalogo": entradas, "total": len(entradas), "activos_todos": activos, "robots": []}


def demo() -> None:
    bt = lambda n, wr, exp: {"n_setups": n, "winrate": wr, "expectativa_r": exp, "r_total": n * exp}
    val = lambda estado, n, r, m1, m2, ic: {"estado": estado, "validado": estado == "validado", "n": n, "winrate": 0.4, "r_neto": r, "r_bruto": r + 0.1,
                                            "mitad_1": m1, "mitad_2": m2, "ic95_bajo": ic, "r_total_neto": n * r, "oportunidades": 3 * n,
                                            "llenadas": n, "spread_pct": 0.0001, "spread_fuente": "supuesto", "motivo": "m", "nota": None}
    filas = armar([
        ("EURUSD", "Intraday 1H", "venta", "t1", {"venta": bt(2, 0.0, -1.0)}, None, None),
        ("XAUUSD", "Intraday 4H", "compra", "t2", {"compra": bt(5, 0.6, 0.4)}, {"compra": bt(40, 0.55, 0.3)}, "t3",
         val("validado", 45, 0.30, 0.2, 0.4, 0.25), "t7"),
        ("GBPUSD", "Swing (S)", "venta", "t4", {"compra": bt(30, 0.6, 0.5)}, None, None, val("no_validado", 40, -0.2, -0.1, -0.3, -0.4), "t8"),
        ("USDJPY", "Intraday D", None, "t5", {"compra": bt(50, 0.6, 0.5)}, None, None),
        ("NZDUSD", "Intraday 1H", "compra", "t6", None, None, None),
        ("GBPJPY", "Intraday 1H", "compra", "t6", None, None, None, val("validado", 32, 0.5, 0.6, 0.4, 0.05), "t9"),
        ("AUDJPY", "Scalping 15m", "compra", "t6", None, None, None, val("insuficiente", 12, 0.9, 0.9, 0.9, 0.1), "t9"),
    ])
    f = {x["simbolo"]: x for x in filas}
    assert f["EURUSD"]["veredicto"] == "INSUFICIENTE" and not f["EURUSD"]["viable"] and f["EURUSD"]["expectativa_r"] is None  # sin validar: sin cifra
    assert f["EURUSD"]["estado_validacion"] == "pendiente" and f["EURUSD"]["backtest_motor"]["n_setups"] == 2
    assert f["USDJPY"]["backtest_motor"]["n_setups"] == 0 and f["USDJPY"]["viable"] is False
    assert f["XAUUSD"]["viable"] and f["XAUUSD"]["veredicto"] == "RECOMENDABLE" and f["XAUUSD"]["n_setups"] == 45
    assert f["XAUUSD"]["expectativa_r"] == 0.30 and f["XAUUSD"]["validado_en"] == "t7" and f["XAUUSD"]["backtest_motor"]["n_setups"] == 40
    assert f["GBPJPY"]["rank"] == 2 and f["XAUUSD"]["rank"] == 1 and filas[0]["simbolo"] == "XAUUSD"  # rank por limite inferior del IC, no por R
    assert f["GBPUSD"]["direccion"] == "compra" and not f["GBPUSD"]["viable"] and f["GBPUSD"]["veredicto"] == "NO RECOMENDABLE"
    assert f["AUDJPY"]["veredicto"] == "INSUFICIENTE" and not f["AUDJPY"]["viable"] and "rank" not in f["AUDJPY"]  # 0.9R con 12 operaciones no cuenta
    assert f["NZDUSD"]["expectativa_r"] is None and f["NZDUSD"]["fuente"] is None
    assert f["XAUUSD"]["simbolo_xm"] == "GOLD" and f["EURUSD"]["simbolo_xm"] == "EURUSD"
    assert len(SIMBOLO_XM) == 34 and SIMBOLO_XM["US500"] == "US500Cash" and SIMBOLO_XM.get("AMXL") is None
    from pathlib import Path

    from compilador import SIMBOLOS

    assert set(NOMBRE_ACTIVO) == set(SIMBOLOS), set(NOMBRE_ACTIVO) ^ set(SIMBOLOS)
    index = (Path(__file__).resolve().parent.parent / "public" / "index.html").read_text(encoding="utf-8")
    for simbolo, nombre in NOMBRE_ACTIVO.items():  # mismo nombre que el catalogo de la web
        assert f"['{simbolo}','{nombre}']" in index, (simbolo, nombre)
    assert robot_legible("XAUUSD", "Swing (S)") == {"simbolo": "XAUUSD", "temporalidad": "Swing (S)", "nombre": "Oro",
                                                    "etiqueta": "Oro (XAUUSD) · Swing (S)", "simbolo_xm": "GOLD"}
    assert robot_legible("ZZZ", "Intraday 1H")["etiqueta"] == "ZZZ · Intraday 1H"
    print("api/catalogo demo OK")


if __name__ == "__main__":
    demo()
