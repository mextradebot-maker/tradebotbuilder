"""Membresías pagadas en Hotmart → licencias del panel.

Niveles (decisión de Ricardo, 2026-10-04):
- sin membresía (Gratis): 3 robots DEMO distintos, nada en real.
- trader: DEMO ilimitado + 5 robots REAL distintos (licencia `real`).
- vip: todo ilimitado (licencia `vip`, cualquier robot y cuenta demo o real).

Un "robot" = símbolo + temporalidad: volver a bajar el mismo no gasta cupo.
La licencia de la membresía es UNA por correo y nivel (origen 'hotmart'); al pausar
se revoca y al reanudar se reautoriza, así el robot que el alumno ya instaló vuelve
a operar sin descargarlo otra vez.
"""

from datetime import datetime, timezone

from .conexion import get_conn
from .licencias import ROBOT_ALUMNOS, ROBOT_TODOS, VIGENCIA_DEMO_DIAS, emitir, licencias_de_correo

NIVELES = ("trader", "vip")
TIPO_POR_NIVEL = {"trader": "real", "vip": "vip"}
LIMITES = {  # robots distintos por modo; None = ilimitado
    None: {"demo": 3, "real": 0},
    "trader": {"demo": None, "real": 5},
    "vip": {"demo": None, "real": None},
}


def registrar_evento(evento_id: str, evento: str, correo: str | None, accion: str, payload: dict) -> bool:
    """False si ese id ya se procesó (Hotmart reintenta)."""
    import json

    with get_conn() as conn:
        return bool(conn.execute(
            """INSERT INTO hotmart_eventos (id, evento, correo, accion, payload) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (evento_id, evento, correo, accion, json.dumps(payload)),
        ).rowcount)


def olvidar_evento(evento_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM hotmart_eventos WHERE id = %s", (evento_id,))


def _guardar(conn, correo, nivel, estado, vence_en, suscriptor, producto, evento) -> None:
    conn.execute(
        """INSERT INTO membresias (correo, nivel, estado, vence_en, suscriptor, producto, ultimo_evento)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (correo) DO UPDATE SET nivel = EXCLUDED.nivel, estado = EXCLUDED.estado,
               vence_en = EXCLUDED.vence_en, suscriptor = COALESCE(EXCLUDED.suscriptor, membresias.suscriptor),
               producto = COALESCE(EXCLUDED.producto, membresias.producto),
               ultimo_evento = EXCLUDED.ultimo_evento, actualizada_en = now()""",
        (correo, nivel, estado, vence_en, suscriptor, producto, evento),
    )


def activar(correo: str, nombre: str, nivel: str, suscriptor: str | None = None, producto: str | None = None,
            evento: str = "") -> dict:
    """Pago aprobado: deja vigente la licencia del nivel y revoca la del otro nivel (cambio de plan)."""
    if nivel not in NIVELES:
        raise ValueError(f"nivel debe ser uno de {NIVELES}")
    correo = correo.strip().lower()
    tipo = TIPO_POR_NIVEL[nivel]
    propias = [l for l in licencias_de_correo(correo) if l["origen"] == "hotmart"]
    lic = next((l for l in propias if l["tipo"] == tipo), None)
    if lic is None:
        lic = emitir(nombre or correo, None, tipo, ROBOT_TODOS if tipo == "vip" else ROBOT_ALUMNOS, None,
                     correo=correo, origen="hotmart")[1]
    with get_conn() as conn:
        _guardar(conn, correo, nivel, "activa", None, suscriptor, producto, evento)
        conn.execute("UPDATE licencias SET revocada_en = NULL, expira_en = NULL WHERE id = %s", (lic["id"],))
        conn.execute(
            "UPDATE licencias SET revocada_en = now() WHERE correo = %s AND origen = 'hotmart' AND tipo <> %s AND revocada_en IS NULL",
            (correo, tipo),
        )
    return lic


def suspender(correo: str, estado: str, vence_en: datetime | None = None, evento: str = "",
              nivel: str | None = None) -> bool:
    """Reembolso, contracargo o cobro fallido → 'pausada' (corte inmediato).
    Cancelación → 'cancelada' y la licencia sigue hasta `vence_en` (lo ya pagado); sin fecha, corte inmediato.
    Con `nivel`, solo si la membresía es de ese nivel: un pago de OXXO vencido al intentar subir
    a VIP no le corta el Trader que sí está pagado.
    False si no hubo nada que suspender."""
    if estado not in ("pausada", "cancelada"):
        raise ValueError("estado debe ser pausada o cancelada")
    correo = correo.strip().lower()
    ahora = datetime.now(timezone.utc)
    if vence_en is not None and vence_en <= ahora:
        vence_en = None
    with get_conn() as conn:
        row = conn.execute("SELECT nivel FROM membresias WHERE correo = %s", (correo,)).fetchone()
        if row is None or (nivel and row[0] != nivel):
            return False
        _guardar(conn, correo, row[0], estado, vence_en, None, None, evento)
        if vence_en is None:
            conn.execute("UPDATE licencias SET revocada_en = now() WHERE correo = %s AND origen = 'hotmart' AND revocada_en IS NULL",
                         (correo,))
        else:
            conn.execute("UPDATE licencias SET expira_en = %s WHERE correo = %s AND origen = 'hotmart' AND revocada_en IS NULL",
                         (vence_en, correo))
    return True


def nivel_de(correo: str) -> str | None:
    """Nivel vigente: membresía activa, o cancelada que aún no llega a su fecha de corte."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT nivel FROM membresias WHERE correo = %s
               AND (estado = 'activa' OR (estado = 'cancelada' AND vence_en > now()))""",
            (correo.strip().lower(),),
        ).fetchone()
    return row[0] if row else None


def robots_descargados(correo: str, modo: str) -> set[tuple[str, str]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT simbolo, temporalidad FROM descargas WHERE correo = %s AND modo = %s",
                            (correo.strip().lower(), modo)).fetchall()
    return {(r[0], r[1]) for r in rows}


def robots_de_correos(correos) -> list[dict]:
    """Robots ya bajados desde la ficha, más reciente primero: [{correo, modo, simbolo, temporalidad, creada_en}].

    Una sola consulta para varios correos (panel admin) o uno (Mis robots del alumno).
    """
    correos = sorted({str(c).strip().lower() for c in correos if c})
    if not correos:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT correo, modo, simbolo, temporalidad, creada_en FROM descargas WHERE correo = ANY(%s) ORDER BY creada_en DESC",
            (correos,)).fetchall()
    return [{"correo": r[0], "modo": r[1], "simbolo": r[2], "temporalidad": r[3], "creada_en": r[4]} for r in rows]


def registrar_descarga(correo: str, modo: str, simbolo: str, temporalidad: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO descargas (correo, modo, simbolo, temporalidad) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                     (correo.strip().lower(), modo, simbolo, temporalidad))


def renovar_demo(licencia_id: int) -> None:
    """Miembros: su DEMO de registro no caduca a los 90 días mientras paguen."""
    with get_conn() as conn:
        conn.execute("UPDATE licencias SET expira_en = now() + make_interval(days => %s) WHERE id = %s AND revocada_en IS NULL",
                     (VIGENCIA_DEMO_DIAS, licencia_id))
