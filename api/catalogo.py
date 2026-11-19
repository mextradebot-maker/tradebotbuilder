"""GET /api/catalogo — simbolos del catalogo (fecha_inicio) + `activos_todos`: una fila por
(simbolo, temporalidad) con el backtest REAL del motor v2 en la direccion que operaria el robot.

Fuente: backtest_largo si existe (muestra grande), si no el backtest del snapshot de /api/setups.
Sin datos = campos en None y veredicto INSUFICIENTE; nunca se inventan cifras.
"""

N_MIN_VIABLE = 20  # ponytail: muestra minima para "recomendable"; ajustar cuando haya backtests largos de todo
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


def fila(simbolo, temporalidad, tendencia, refrescado_en, bt_snapshot, bt_largo, largo_en) -> dict:
    direccion = "compra" if temporalidad in SWING else tendencia
    largo = (bt_largo or {}).get(direccion) or {}
    usar_largo = bool(largo.get("n_setups"))
    rep = largo if usar_largo else ((bt_snapshot or {}).get(direccion) or {})
    n = rep.get("n_setups") or 0
    exp = rep.get("expectativa_r") if n else None
    viable = n >= N_MIN_VIABLE and exp is not None and exp > 0 and direccion == tendencia
    if n < N_MIN_VIABLE:
        veredicto = "INSUFICIENTE"
    else:
        veredicto = "RECOMENDABLE" if viable else "NO RECOMENDABLE"
    fuente = "backtest largo" if usar_largo else "backtest reciente"
    if n:
        desc = (f"Motor SMC v2, {fuente}: {n} setups en {direccion}, acierto {round((rep.get('winrate') or 0) * 100)}%, "
                f"expectativa {exp:+.2f}R.")
    else:
        desc = "Sin setups suficientes en el historico para medir este activo en esta temporalidad."
    return {
        "simbolo": simbolo, "simbolo_xm": SIMBOLO_XM.get(simbolo), "temporalidad": temporalidad,
        "tendencia": tendencia, "direccion": direccion,
        "winrate": rep.get("winrate") if n else None, "expectativa_r": exp,
        "r_total": rep.get("r_total") if n else None, "n_setups": n,
        "viable": viable, "veredicto": veredicto, "descripcion_larga": desc,
        "fuente": fuente if n else None,
        # actualizado_en = último análisis en vivo (smc_snapshot); el backtest largo va aparte
        "actualizado_en": _iso(refrescado_en), "backtest_en": _iso(largo_en if usar_largo else refrescado_en),
    }


def _vela_medianoche_todos() -> list[dict]:
    """Una fila por candidato de Vela de Medianoche (mismo criterio de nombres que robot_legible), sin
    winrate/expectativa_r: esta estrategia no los tiene. Sin almacen/datos -> [] (no tumba /api/catalogo)."""
    try:
        from motor_smc.vela_medianoche import candidatos as _candidatos
        cands = _candidatos()
    except Exception:
        return []
    return [{
        "simbolo": c["simbolo"], "simbolo_xm": SIMBOLO_XM.get(c["simbolo"]) or "",
        "nombre": NOMBRE_ACTIVO.get(c["simbolo"]) or c["simbolo"],
        "pdh": c["pdh"], "dia_anterior": _iso(c["dia_anterior"]), "ventana_hasta": _iso(c["ventana_hasta"]),
    } for c in cands]


def armar(filas_db) -> list[dict]:
    filas = [fila(*f) for f in filas_db]
    for i, f in enumerate(sorted((f for f in filas if f["viable"]), key=lambda f: -f["expectativa_r"]), 1):
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
    return 200, {"catalogo": entradas, "total": len(entradas), "activos_todos": activos, "robots": [],
                 "vela_medianoche_todos": _vela_medianoche_todos()}


def demo() -> None:
    bt = lambda n, wr, exp: {"n_setups": n, "winrate": wr, "expectativa_r": exp, "r_total": n * exp}
    filas = armar([
        ("EURUSD", "Intraday 1H", "venta", "t1", {"venta": bt(2, 0.0, -1.0)}, None, None),
        ("XAUUSD", "Intraday 4H", "compra", "t2", {"compra": bt(5, 0.6, 0.4)}, {"compra": bt(40, 0.55, 0.3)}, "t3"),
        ("GBPUSD", "Swing (S)", "venta", "t4", {"compra": bt(30, 0.6, 0.5)}, None, None),
        ("USDJPY", "Intraday D", None, "t5", {"compra": bt(50, 0.6, 0.5)}, None, None),
        ("NZDUSD", "Intraday 1H", "compra", "t6", None, None, None),
    ])
    f = {x["simbolo"]: x for x in filas}
    assert f["EURUSD"]["veredicto"] == "INSUFICIENTE" and not f["EURUSD"]["viable"]
    assert f["XAUUSD"]["viable"] and f["XAUUSD"]["n_setups"] == 40 and f["XAUUSD"]["actualizado_en"] == "t2" and f["XAUUSD"]["backtest_en"] == "t3"
    assert f["XAUUSD"]["rank"] == 1 and filas[0]["simbolo"] == "XAUUSD"
    assert f["GBPUSD"]["direccion"] == "compra" and not f["GBPUSD"]["viable"]  # swing bajista: no se recomienda
    assert f["GBPUSD"]["veredicto"] == "NO RECOMENDABLE"
    assert f["USDJPY"]["n_setups"] == 0 and f["USDJPY"]["winrate"] is None  # sin tendencia: sin cifras
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

    # vela_medianoche_todos: una fila por candidato, formato robot_legible-style, sin winrate/expectativa_r
    import datetime as _dt
    import sys as _sys

    from motor_smc import vela_medianoche as _vm
    _orig_candidatos = _vm.candidatos
    try:
        _vm.candidatos = lambda: [{"simbolo": "XAUUSD", "pdh": 2100.5, "dia_anterior": _dt.date(2026, 11, 6),
                                    "ventana_hasta": _dt.datetime(2026, 11, 7, 7, 0, tzinfo=_dt.timezone.utc)}]
        filas_vm = _vela_medianoche_todos()
        assert filas_vm == [{"simbolo": "XAUUSD", "simbolo_xm": "GOLD", "nombre": "Oro", "pdh": 2100.5,
                             "dia_anterior": "2026-11-06", "ventana_hasta": "2026-11-07T07:00:00+00:00"}], filas_vm
        assert "winrate" not in filas_vm[0] and "expectativa_r" not in filas_vm[0]
        # candidatos() lanza -> [] sin tumbar nada
        _vm.candidatos = lambda: (_ for _ in ()).throw(RuntimeError("almacen sin datos"))
        assert _vela_medianoche_todos() == []
    finally:
        _vm.candidatos = _orig_candidatos

    # procesar() incluye vela_medianoche_todos en la respuesta sin tumbar el resto si candidatos() falla
    falso_p = type(_sys)("persistencia")
    falso_p.listar_catalogo = lambda: []
    falso_p.filas_catalogo = lambda: []
    _previo_p = _sys.modules.get("persistencia")
    _sys.modules["persistencia"] = falso_p
    try:
        _vm.candidatos = lambda: (_ for _ in ()).throw(RuntimeError("almacen sin datos"))
        status, body = procesar({})
        assert status == 200 and body["vela_medianoche_todos"] == [], body
    finally:
        _vm.candidatos = _orig_candidatos
        if _previo_p is None:
            del _sys.modules["persistencia"]
        else:
            _sys.modules["persistencia"] = _previo_p
    print("api/catalogo demo OK")


if __name__ == "__main__":
    demo()
