"""GET /api/v1/estado-carga — avance de la carga historica (velas_carga) y del backtest largo, para diagnostico.

Protegido con X-MTB-Service-Key (MTB_SERVICE_KEY). Solo lectura. Por serie base: desde/hasta cubiertos,
completa, ultima sincronizacion y profundidad objetivo de carga_historica; por combinacion: cuando se calculo
el backtest largo y con cuantas velas.
"""

from datetime import datetime, timezone


def _iso(t):
    return t.isoformat() if hasattr(t, "isoformat") else t


def _leer_db() -> tuple[list, list]:
    from persistencia.conexion import get_conn
    with get_conn() as conn:
        carga = conn.execute("SELECT simbolo, serie, desde, hasta, completa, actualizado_en FROM velas_carga "
                             "ORDER BY simbolo, serie").fetchall()
        largo = conn.execute("SELECT simbolo, temporalidad, calculado_en, velas FROM backtest_largo "
                             "ORDER BY simbolo, temporalidad").fetchall()
    return carga, largo


def armar(carga: list, largo: list, ahora: datetime) -> dict:
    import carga_historica as ch
    series = []
    for simbolo, serie, desde, hasta, completa, sinc in carga:
        obj = ch.OBJETIVO.get(serie)
        series.append({"simbolo": simbolo, "serie": serie, "desde": _iso(desde), "hasta": _iso(hasta),
                       "completa": bool(completa), "sincronizado": _iso(sinc),
                       "objetivo_desde": _iso(ahora - obj) if obj is not None else None})
    resumen = {}
    for s in series:
        r = resumen.setdefault(s["serie"], {"registradas": 0, "completas": 0})
        r["registradas"] += 1
        r["completas"] += s["completa"]
    largos = [{"simbolo": s, "temporalidad": t, "calculado_en": _iso(c), "velas": v} for s, t, c, v in largo]
    ultimo = max((x["calculado_en"] for x in largos if x["calculado_en"]), default=None)
    return {"ahora": _iso(ahora), "resumen_series": resumen, "series": series,
            "backtest_largo": {"total": len(largos), "ultimo_calculo": ultimo, "combos": largos}}


def procesar(headers: dict, leer=_leer_db) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    try:
        carga, largo = leer()
    except Exception as e:
        return 503, {"error": f"base de datos no disponible: {e}"}
    return 200, armar(carga, largo, datetime.now(timezone.utc))


def demo() -> None:
    import os
    os.environ["MTB_SERVICE_KEY"] = "sk"
    t = datetime(2026, 10, 4, tzinfo=timezone.utc)
    carga = [("EURUSD", "1H", t.replace(year=2020), t, False, t), ("EURUSD", "D", t.replace(year=2003), t, True, t)]
    largo = [("EURUSD", "Intraday D", t, 5000)]
    assert procesar({}, lambda: (carga, largo))[0] == 401
    assert procesar({"X-MTB-Service-Key": "otra"}, lambda: (carga, largo))[0] == 401
    assert procesar({"x-mtb-service-key": "sk"}, lambda: 1 / 0)[0] == 503
    st, b = procesar({"x-mtb-service-key": "sk"}, lambda: (carga, largo))
    assert st == 200 and b["resumen_series"] == {"1H": {"registradas": 1, "completas": 0}, "D": {"registradas": 1, "completas": 1}}
    s = {x["serie"]: x for x in b["series"]}
    assert s["D"]["objetivo_desde"] is None and s["1H"]["objetivo_desde"] and s["1H"]["desde"].startswith("2020")
    assert b["backtest_largo"] == {"total": 1, "ultimo_calculo": t.isoformat(),
                                   "combos": [{"simbolo": "EURUSD", "temporalidad": "Intraday D", "calculado_en": t.isoformat(), "velas": 5000}]}
    print("api.estado_carga.demo() OK")


if __name__ == "__main__":
    demo()
