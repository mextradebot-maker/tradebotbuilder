"""GET /api/v1/reporte-setups — setups confirmados vigentes de TODOS los activos (reemplaza el
cálculo de T-01, que analizaba XAUUSD y EURUSD desde cero cada mañana).

No calcula nada: lee los snapshots que refresco.py ya mantiene al día en Postgres
(36 activos × 5 temporalidades). Solo lo llama n8n (T-01) con X-MTB-Service-Key; Claude
redacta el mensaje de Telegram con este JSON.

"Vigente" = conocido (indice_conocido; si no viene, confirmado) en las últimas VELAS_VIGENCIA velas de su temporalidad: la misma
ventana en que el robot mantiene su orden pendiente (InpVelasExpiracion del EA).
"""

import os
from datetime import datetime, timezone

VELAS_VIGENCIA = 20
_A_COMPRA_VENTA = {"long": "compra", "short": "venta"}


def setups_vigentes(snapshots: list[dict], max_velas: int = VELAS_VIGENCIA) -> dict:
    """snapshots: [{simbolo, temporalidad, respuesta, tendencia_actual, refrescado_en}] → reporte."""
    vigentes, sin_setup, tendencias = [], 0, {}
    for s in snapshots:
        r = s.get("respuesta") or {}
        tendencias.setdefault(s["simbolo"], {})[s["temporalidad"]] = s.get("tendencia_actual")
        velas = int(r.get("velas") or 0)
        encontrados = []
        for st in r.get("setups_confirmados") or []:
            hace = velas - 1 - int(st.get("indice_conocido", st["indice_confirmacion"]))
            if 0 <= hace <= max_velas:
                encontrados.append({
                    "simbolo": s["simbolo"],
                    "temporalidad": s["temporalidad"],
                    "direccion": _A_COMPRA_VENTA.get(st["direccion"], st["direccion"]),
                    "entrada": round(float(st["entrada"]), 5),
                    "stop": round(float(st["stop"]), 5),
                    "tp": round(float(st["tp"]), 5) if st.get("tp") is not None else None,
                    "tipo": st.get("tipo"),
                    "velas_desde_confirmacion": hace,
                    "order_block_confluente": bool(st.get("order_block_confluente")),
                    "liquidez_confluente": bool(st.get("liquidez_confluente")),
                })
        if encontrados:
            vigentes.append(min(encontrados, key=lambda x: x["velas_desde_confirmacion"]))  # el más reciente
        else:
            sin_setup += 1
    refrescos = [s["refrescado_en"] for s in snapshots if s.get("refrescado_en")]
    return {
        "generado_en": datetime.now(timezone.utc),
        "combinaciones": len(snapshots),
        "con_setup_vigente": len(vigentes),
        "sin_setup": sin_setup,
        "snapshot_mas_viejo": min(refrescos) if refrescos else None,
        "vigentes": sorted(vigentes, key=lambda x: (x["velas_desde_confirmacion"], x["simbolo"])),
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


def procesar_reporte(headers: dict) -> tuple[int, dict]:
    from api.licencias import _clave_ok, _h

    if not os.environ.get("MTB_SERVICE_KEY"):
        return 503, {"error": "MTB_SERVICE_KEY no configurada"}
    if not _clave_ok(_h(headers, "X-MTB-Service-Key"), "MTB_SERVICE_KEY"):
        return 401, {"error": "se requiere X-MTB-Service-Key"}
    try:
        return 200, setups_vigentes(_leer_snapshots())
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
        snap("XAUUSD", "Intraday", 500, [st(400), st(495), st(490)]),   # vigentes: hace 4 y hace 9 → el de hace 4
        snap("EURUSD", "Scalping", 300, [st(100, "short", 1.0845, 1.0852)], "venta"),  # hace 199 → viejo
        snap("USDJPY", "Swing (H)", 200, []),
    ])
    assert rep["combinaciones"] == 3 and rep["con_setup_vigente"] == 1 and rep["sin_setup"] == 2, rep
    v = rep["vigentes"][0]
    assert (v["simbolo"], v["direccion"], v["velas_desde_confirmacion"]) == ("XAUUSD", "compra", 4), v
    assert rep["tendencias"]["EURUSD"]["Scalping"] == "venta"
    assert rep["conteo_por_temporalidad"]["Intraday"] == {"compra": 1, "venta": 0, "sin_definir": 0}
    assert setups_vigentes([])["con_setup_vigente"] == 0
    # v2: la vigencia cuenta desde indice_conocido (cuando el setup ya era visible), y salen tp/tipo
    v2 = {**st(100), "indice_conocido": 495, "tp": 2660.0, "tipo": "reversion"}
    rv = setups_vigentes([snap("XAUUSD", "Intraday", 500, [v2])])["vigentes"][0]
    assert (rv["velas_desde_confirmacion"], rv["tp"], rv["tipo"]) == (4, 2660.0, "reversion"), rv
    assert v["tp"] is None and v["tipo"] is None  # setups viejos sin tp/tipo

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
