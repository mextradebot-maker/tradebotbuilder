"""GET /api/v1/reporte-setups — setups confirmados vigentes de TODOS los activos (reemplaza el
cálculo de T-01, que analizaba XAUUSD y EURUSD desde cero cada mañana).

No calcula nada: lee los snapshots que refresco.py ya mantiene al día en Postgres
(34 activos × 7 temporalidades). Solo lo llama n8n (T-01) con X-MTB-Service-Key; Claude
redacta el mensaje de Telegram con este JSON.

"Vigente" = EXACTAMENTE lo que el despachador de robots (master/despachador.py) tomaría: pasa su regla única `motivo_descarte`
(conocido hace <= 20 velas Y entrada a <= 10 % del precio actual) y, por combinación, se reporta el mismo setup que el robot
recibiría (el último). Así el reporte de Telegram y la operación no pueden diferir. El precio actual sale del mismo
`_precio_ref` que usa el motor (última vela 15m guardada). Los setups con `resultado` distinto de "sin_llenar" nunca llegan
aquí: el motor ya los quita de `setups_confirmados`.
"""

import os
from datetime import datetime, timezone

_A_COMPRA_VENTA = {"long": "compra", "short": "venta"}


SWING = ("Swing (S)", "Swing (M)")


def estrategias_del_dia(snapshots: list[dict], vigentes: list[dict], vela_medianoche: list[dict] | None) -> dict:
    """Los setups que hay hoy, clasificados por estrategia (lo que el reporte de Telegram presenta, una seccion por estrategia):
    - estructural: el motor SMC; solo Diario y Swing (en 15m/30m/1H/4H no es valida y no se presenta);
    - vela_medianoche: los candidatos de hoy (mismo criterio que la tarjeta del panel; None = no se pudo calcular, se omite);
    - swing_express: activos con tendencia de compra en Swing (S) o (M), el mismo criterio de la tarjeta del panel."""
    estructural = [v for v in vigentes if v["aplica"]]
    express = {}
    for s in snapshots:
        if s["temporalidad"] in SWING and s.get("tendencia_actual") == "compra":
            express.setdefault(s["simbolo"], []).append(s["temporalidad"])
    sal = {
        "estructural": {"valida_en": "Diario y Swing", "n": len(estructural), "setups": estructural},
        "swing_express": {"n": len(express), "activos": [{"simbolo": k, "temporalidades": v} for k, v in sorted(express.items())]},
    }
    if vela_medianoche is not None:
        sal["vela_medianoche"] = {"n": len(vela_medianoche), "candidatos": [
            {"simbolo": c["simbolo"], "nivel_pdh": c["pdh"], "valido_hasta": str(c.get("ventana_hasta"))} for c in vela_medianoche]}
    return sal


def setups_vigentes(snapshots: list[dict], precios: dict | None = None, vela_medianoche: list[dict] | None = None,
                    operacion: dict | None = None) -> dict:
    """snapshots: [{simbolo, temporalidad, respuesta, tendencia_actual, refrescado_en}] → reporte.
    precios: {simbolo: (ref_ts, ref_cierre)} (el de api.setups._precio_ref); sin él no se puede medir la distancia al precio.
    vela_medianoche: candidatos de motor_smc.vela_medianoche.candidatos() (None = se omite esa estrategia).
    operacion: lo que ocurre de verdad (_leer_operacion): despachador, robots activados y ordenes. None = no se pudo leer: el reporte dice
    "sin dato", nunca "no"."""
    from api.catalogo import AVISO_SIN_VENTAJA, SIN_VENTAJA
    from master.despachador import MAX_DISTANCIA_PCT_SWING, MAX_DISTANCIA_R, MAX_EDAD_VELAS, MAX_ZONA_VELAS, motivo_descarte, vigentes as vigentes_motor

    precios = precios or {}
    vigentes, sin_setup, tendencias = [], 0, {}
    descartados = {"resuelto": 0, "solo_compras": 0, "edad": 0, "zona_vieja": 0, "distancia": 0}
    sin_precio = 0
    for s in snapshots:
        r = s.get("respuesta") or {}
        tendencias.setdefault(s["simbolo"], {})[s["temporalidad"]] = s.get("tendencia_actual")
        velas = int(r.get("velas") or 0)
        ref_ts, ref_cierre = precios.get(s["simbolo"]) or (None, None)
        confirmados = r.get("setups_confirmados") or []
        for st in confirmados:
            m = motivo_descarte(st, velas, ref_cierre, s["temporalidad"])
            if m:
                descartados[m] += 1
        ok = vigentes_motor(confirmados, velas, ref_cierre, s["temporalidad"])
        if not ok:
            sin_setup += 1
            continue
        st = ok[-1]  # el mismo que recibe el robot (recibir_oportunidades toma el último)
        hace = velas - 1 - int(st.get("indice_conocido", st["indice_confirmacion"]))
        sin_precio += ref_cierre is None
        aplica = s["temporalidad"] not in SIN_VENTAJA
        rb = ((operacion or {}).get("robots") or {}).get((s["simbolo"], s["temporalidad"]), {"activos": 0, "vivos": 0}) if operacion else None
        vigentes.append({
            "robots_activos": None if rb is None else rb["activos"],  # None = sin dato; 0 = nadie lo opera: el setup NO se convierte en orden
            "robots_vivos": None if rb is None else rb["vivos"],
            "aplica": aplica,  # False = intradia: sin ventaja demostrada; el mensaje debe decirlo, no presentarlo como oportunidad
            "aviso": None if aplica else AVISO_SIN_VENTAJA,
            "simbolo": s["simbolo"],
            "temporalidad": s["temporalidad"],
            "direccion": _A_COMPRA_VENTA.get(st["direccion"], st["direccion"]),
            "entrada": round(float(st["entrada"]), 5),
            "stop": round(float(st["stop"]), 5),
            "tp": round(float(st["tp"]), 5) if st.get("tp") is not None else None,
            "tipo": st.get("tipo"),
            "velas_desde_confirmacion": hace,
            "precio_actual": round(float(ref_cierre), 5) if ref_cierre else None,
            "distancia_pct": round(abs(float(st["entrada"]) - float(ref_cierre)) / float(ref_cierre) * 100, 2) if ref_cierre else None,
            "distancia_r": round(abs(float(st["entrada"]) - float(ref_cierre)) / abs(float(st["entrada"]) - float(st["stop"])), 2) if ref_cierre and st["entrada"] != st["stop"] else None,
            "order_block_confluente": bool(st.get("order_block_confluente")),
            "liquidez_confluente": bool(st.get("liquidez_confluente")),
        })
    refrescos = [s["refrescado_en"] for s in snapshots if s.get("refrescado_en")]
    return {
        "generado_en": datetime.now(timezone.utc),
        "criterio": f"conocido hace <= {MAX_EDAD_VELAS} velas, zona no mas vieja que el tope de su temporalidad {MAX_ZONA_VELAS} y entrada a <= {MAX_DISTANCIA_R:g} veces su riesgo del precio actual (en swing ademas <= {MAX_DISTANCIA_PCT_SWING:g}%) (el mismo del despachador de robots)",
        "combinaciones": len(snapshots),
        "con_setup_vigente": len(vigentes),
        "sin_setup": sin_setup,
        "aviso_estrategia": AVISO_SIN_VENTAJA,  # va en todo reporte: los setups con aplica=false (intradia) no son oportunidades
        "descartados": descartados,  # setups que el motor aun ve "sin llenar" pero ya no son operables
        "vigentes_sin_precio_actual": sin_precio,  # sin precio de referencia no se pudo medir la distancia
        "snapshot_mas_viejo": min(refrescos) if refrescos else None,
        "vigentes": sorted(vigentes, key=lambda x: (x["velas_desde_confirmacion"], x["simbolo"])),
        "operacion": None if operacion is None else {
            "despachador_encendido": operacion["master_activo"],
            "robots_activos": operacion["robots_total"],
            "ordenes_24h": operacion["ordenes_24h"],
            "ordenes_30d": {**operacion["ordenes_30d"], "ultima": str(operacion["ordenes_30d"]["ultima"]) if operacion["ordenes_30d"]["ultima"] else None},
        },
        "estrategias": estrategias_del_dia(snapshots, sorted(vigentes, key=lambda x: (x["velas_desde_confirmacion"], x["simbolo"])), vela_medianoche),
        "conteo_por_temporalidad": _conteos(tendencias),  # Claude redacta, no cuenta (contaba mal)
        "tendencias": tendencias,
    }


def _conteos(tendencias: dict) -> dict:
    c = {}
    for por_temp in tendencias.values():
        for temp, d in por_temp.items():
            c.setdefault(temp, {"compra": 0, "venta": 0, "sin_definir": 0})
            c[temp][d if d in ("compra", "venta") else "sin_definir"] += 1
    return c


def _leer_operacion() -> dict | None:
    """Lo que el despachador hace de verdad: encendido o no, robots activados (y vivos) por activo y temporalidad, ordenes de 24 h.
    Mismo criterio que master.despachador.RepoPg.robots_activados. None si no se puede leer (el reporte lo dice como "sin dato")."""
    try:
        from persistencia.conexion import get_conn

        with get_conn() as conn:
            rob = conn.execute(
                """SELECT m.simbolo, m.temporalidad, count(*), count(*) FILTER (WHERE m.visto_en > now() - interval '60 seconds')
                   FROM master_robots m JOIN licencias l ON l.id = m.licencia_id
                   WHERE m.activo AND l.revocada_en IS NULL AND (l.expira_en IS NULL OR l.expira_en > now())
                     AND m.estrategia NOT IN ('swing_express', 'vela_medianoche')
                   GROUP BY m.simbolo, m.temporalidad""").fetchall()
            ords = conn.execute(
                """SELECT simbolo, temporalidad, direccion, estado FROM master_ordenes
                   WHERE creada_en > now() - interval '24 hours' AND estrategia NOT IN ('swing_express', 'vela_medianoche')
                   ORDER BY creada_en DESC LIMIT 30""").fetchall()
            h30 = conn.execute(
                """SELECT estado, count(*), max(creada_en) FROM master_ordenes
                   WHERE creada_en > now() - interval '30 days' AND estrategia NOT IN ('swing_express', 'vela_medianoche')
                   GROUP BY estado""").fetchall()
    except Exception:
        return None
    return {"master_activo": os.environ.get("MASTER_ACTIVO") == "1",
            "robots": {(r[0], r[1]): {"activos": int(r[2]), "vivos": int(r[3])} for r in rob},
            "robots_total": sum(int(r[2]) for r in rob),
            "ordenes_24h": [{"simbolo": o[0], "temporalidad": o[1], "direccion": o[2], "estado": o[3]} for o in ords],
            # historia de 30 dias: ¿el Master ha mandado senales alguna vez? (por estado y fecha de la ultima orden creada)
            "ordenes_30d": {"total": sum(int(r[1]) for r in h30), "por_estado": {r[0]: int(r[1]) for r in h30},
                            "ultima": max((r[2] for r in h30), default=None)}}


def _leer_snapshots() -> list[dict]:
    from persistencia.conexion import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.simbolo, s.temporalidad, s.estructura_smc, s.tendencia_actual, s.refrescado_en
               FROM smc_snapshot s
               JOIN catalogo_activos c ON c.simbolo = s.simbolo AND c.temporalidad = s.temporalidad AND c.activo
               ORDER BY s.simbolo, s.temporalidad"""
        ).fetchall()
    return [{"simbolo": r[0], "temporalidad": r[1], "respuesta": r[2], "tendencia_actual": r[3], "refrescado_en": r[4]}
            for r in rows]


def procesar_reporte(headers: dict, qs: dict | None = None) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h

    if not os.environ.get("MTB_SERVICE_KEY"):
        return 503, {"error": "MTB_SERVICE_KEY no configurada"}
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    try:
        snaps = _leer_snapshots()
        from api.setups import _precio_ref  # el mismo precio de referencia que ve el motor al despachar

        try:  # la Vela de Medianoche necesita el almacen de velas; si falla, el reporte sigue sin esa estrategia
            from motor_smc.vela_medianoche import candidatos

            vm = candidatos()
        except Exception:
            vm = None
        rep = setups_vigentes(snaps, {sim: _precio_ref(sim) for sim in {s["simbolo"] for s in snaps}}, vm, _leer_operacion())
        rep["cuenta_motor"] = None  # None = la cuenta del motor no esta encendida (MOTOR_CUENTA=1 o ?cuenta_motor=1 en la peticion)
        if os.environ.get("MOTOR_CUENTA") == "1" or (qs or {}).get("cuenta_motor") == "1":
            try:  # el motor "opera" exactamente lo que este reporte presenta como vigente (cuenta_motor.py)
                import cuenta_motor

                rep["cuenta_motor"] = cuenta_motor.correr(rep["estrategias"]["estructural"]["setups"])
            except Exception as e:
                rep["cuenta_motor"] = {"error": type(e).__name__}
        return 200, rep
    except Exception as e:  # BD caída
        return 503, {"error": f"snapshots no disponibles: {type(e).__name__}"}


def demo() -> None:
    def snap(sim, temp, velas, confirmados, tend="compra"):
        return {"simbolo": sim, "temporalidad": temp, "tendencia_actual": tend,
                "refrescado_en": datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
                "respuesta": {"velas": velas, "setups_confirmados": confirmados}}

    st = lambda idx, d="long", e=2642.5, s=2635.0: {"indice_confirmacion": idx, "direccion": d, "entrada": e, "stop": s,
                                                     "order_block_confluente": True, "liquidez_confluente": False}
    rep = setups_vigentes([
        snap("XAUUSD", "Intraday 1H", 500, [st(400), st(490), st(495)]),   # en orden cronologico; vigentes hace 9 y hace 4 → el ultimo (hace 4), como el robot
        snap("EURUSD", "Scalping 15m", 300, [st(100, "short", 1.0845, 1.0852)], "venta"),  # hace 199 → viejo
        snap("USDJPY", "Intraday 4H", 200, []),
    ])
    assert rep["combinaciones"] == 3 and rep["con_setup_vigente"] == 1 and rep["sin_setup"] == 2, rep
    v = rep["vigentes"][0]
    assert (v["simbolo"], v["direccion"], v["velas_desde_confirmacion"]) == ("XAUUSD", "compra", 4), v
    assert rep["tendencias"]["EURUSD"]["Scalping 15m"] == "venta"
    assert rep["conteo_por_temporalidad"]["Intraday 1H"] == {"compra": 1, "venta": 0, "sin_definir": 0}
    assert setups_vigentes([])["con_setup_vigente"] == 0
    # v2: la vigencia cuenta desde indice_conocido (cuando el setup ya era visible), y salen tp/tipo
    v2 = {**st(480), "indice_conocido": 495, "tp": 2660.0, "tipo": "reversion"}
    rv = setups_vigentes([snap("XAUUSD", "Intraday 1H", 500, [v2])])["vigentes"][0]
    assert (rv["velas_desde_confirmacion"], rv["tp"], rv["tipo"]) == (4, 2660.0, "reversion"), rv
    assert v["tp"] is None and v["tipo"] is None  # setups viejos sin tp/tipo
    # un setup ya llenado/cancelado/expirado no está vigente aunque esté dentro de la ventana
    resuelto = {**v2, "resultado": "gano"}
    assert setups_vigentes([snap("XAUUSD", "Intraday 1H", 500, [resuelto])])["con_setup_vigente"] == 0
    pendiente = {**v2, "resultado": "sin_llenar"}
    assert setups_vigentes([snap("XAUUSD", "Intraday 1H", 500, [pendiente])])["con_setup_vigente"] == 1

    # intradia: aparece, pero marcado como sin ventaja demostrada
    ri = setups_vigentes([snap("EURGBP", "Scalping 15m", 400, [{**st(395, "long", 0.8700, 0.8690), "indice_conocido": 396}]),
                          snap("CADJPY", "Intraday D", 400, [{**st(395, "short", 111.97, 112.2), "indice_conocido": 396}])],
                         {"EURGBP": (1, 0.8702), "CADJPY": (1, 111.6)})
    ap = {v["simbolo"]: v["aplica"] for v in ri["vigentes"]}
    assert ap == {"EURGBP": False, "CADJPY": True} and "no valida para esta temporalidad" in ri["aviso_estrategia"], ri["vigentes"]
    assert [v["aviso"] for v in ri["vigentes"] if v["simbolo"] == "CADJPY"] == [None]
    # estrategias del dia: estructural solo Diario/Swing, swing express por tendencia, vela de medianoche opcional
    snaps_e = [snap("XAUUSD", "Swing (M)", 400, [], "compra"), snap("XAUUSD", "Swing (S)", 400, [], "compra"), snap("EURUSD", "Swing (S)", 400, [], "venta"),
               snap("BTCUSD", "Swing (M)", 400, [], "compra"), snap("EURGBP", "Scalping 15m", 400, [{**st(395, "long", 0.8700, 0.8690), "indice_conocido": 396}], "venta")]
    re_ = setups_vigentes(snaps_e, {"EURGBP": (1, 0.8702)}, [{"simbolo": "ETHUSD", "pdh": 2100.5, "ventana_hasta": datetime(2026, 11, 7, 7, tzinfo=timezone.utc)}])
    es = re_["estrategias"]
    assert es["estructural"]["n"] == 0 and es["estructural"]["setups"] == [], es  # el unico vigente es de 15m: no se presenta
    assert [a["simbolo"] for a in es["swing_express"]["activos"]] == ["BTCUSD", "XAUUSD"] and es["swing_express"]["activos"][1]["temporalidades"] == ["Swing (M)", "Swing (S)"], es
    assert es["vela_medianoche"]["n"] == 1 and es["vela_medianoche"]["candidatos"][0]["nivel_pdh"] == 2100.5, es
    assert "vela_medianoche" not in setups_vigentes(snaps_e, None, None)["estrategias"]  # sin dato: se omite, no se inventa
    # Swing solo compra: una venta de Swing no se presenta (el robot nunca la tomaria)
    sw = setups_vigentes([snap("USDCHF", "Swing (M)", 400, [{**st(395, "short", 0.89, 0.93), "indice_conocido": 396, "tp": 0.76}], "venta")], {"USDCHF": (1, 0.8361)})
    assert sw["con_setup_vigente"] == 0 and sw["descartados"]["solo_compras"] == 1, sw
    # reporte honesto: cada setup dice si algun robot lo opera; sin dato no se afirma nada
    op = {"master_activo": False, "robots": {("EURJPY", "Swing (M)"): {"activos": 1, "vivos": 0}}, "robots_total": 1,
          "ordenes_24h": [{"simbolo": "XAUUSD", "temporalidad": "Swing (S)", "direccion": "compra", "estado": "pendiente"}],
          "ordenes_30d": {"total": 3, "por_estado": {"expirada": 2, "pendiente": 1}, "ultima": None}}
    sn = [snap("EURJPY", "Swing (M)", 400, [{**st(395, "long", 164.0, 150.0), "indice_conocido": 396, "tp": 190.0}]),
          snap("CADJPY", "Intraday D", 400, [{**st(395, "short", 111.9, 113.4), "indice_conocido": 396, "tp": 110.0}], "venta")]
    pp = {"EURJPY": (1, 165.0), "CADJPY": (1, 111.6)}
    ro = setups_vigentes(sn, pp, None, op)
    rb = {v["simbolo"]: (v["robots_activos"], v["robots_vivos"]) for v in ro["vigentes"]}
    assert rb == {"EURJPY": (1, 0), "CADJPY": (0, 0)}, rb  # CADJPY: ningun robot -> no se convierte en orden
    assert ro["operacion"]["despachador_encendido"] is False and ro["operacion"]["robots_activos"] == 1 and len(ro["operacion"]["ordenes_24h"]) == 1
    assert ro["operacion"]["ordenes_30d"]["total"] == 3 and ro["operacion"]["ordenes_30d"]["ultima"] is None
    rs = setups_vigentes(sn, pp, None, None)
    assert rs["operacion"] is None and all(v["robots_activos"] is None for v in rs["vigentes"])  # sin dato: None, no 0
    # MISMA regla que el robot: una zona vieja de oro (entrada 1905, precio 3883) NO es vigente aunque tenga < 20 velas
    oro_m = {**st(52, "long", 1905.4, 1750.9), "indice_conocido": 79}
    rep = setups_vigentes([snap("XAUUSD", "Swing (M)", 83, [oro_m]), snap("EURGBP", "Scalping 15m", 400, [{**st(395, "long", 0.8700, 0.8690), "indice_conocido": 396}])],
                          {"XAUUSD": (1, 3883.74), "EURGBP": (1, 0.8702)})
    assert rep["con_setup_vigente"] == 1 and rep["descartados"]["zona_vieja"] == 1, rep
    assert rep["vigentes"][0]["simbolo"] == "EURGBP" and rep["vigentes"][0]["distancia_pct"] == 0.02, rep["vigentes"]
    # sin precio de referencia no se puede medir la distancia: se reporta como el motor (no filtra) pero queda marcado
    oro_cerca = {**oro_m, "indice_confirmacion": 75}  # zona reciente pero lejos: solo la distancia lo descarta
    assert setups_vigentes([snap("XAUUSD", "Swing (M)", 83, [oro_cerca])], {"XAUUSD": (1, 3883.74)})["descartados"]["distancia"] == 1
    sp = setups_vigentes([snap("XAUUSD", "Swing (M)", 83, [oro_cerca])])
    assert sp["con_setup_vigente"] == 1 and sp["vigentes_sin_precio_actual"] == 1 and sp["vigentes"][0]["distancia_pct"] is None, sp
    # con varios setups, el reporte da el MISMO que recibe el robot (el ultimo que pasa el filtro), no otro
    from master.despachador import vigentes as _v
    cs = [{**st(i, "long", 2642.5 + i, 2635.0), "indice_conocido": i} for i in (490, 495, 498)]
    assert setups_vigentes([snap("XAUUSD", "Intraday 1H", 500, cs)])["vigentes"][0]["entrada"] == _v(cs, 500, None)[-1]["entrada"]

    previa = os.environ.pop("MTB_SERVICE_KEY", None)
    try:
        assert procesar_reporte({})[0] == 503
        os.environ["MTB_SERVICE_KEY"] = "svc"
        assert procesar_reporte({"X-MTB-Service-Key": "otra"})[0] == 401
    finally:
        os.environ.pop("MTB_SERVICE_KEY", None)
        if previa is not None:
            os.environ["MTB_SERVICE_KEY"] = previa
    print("api.reporte.demo() OK")


if __name__ == "__main__":
    demo()
