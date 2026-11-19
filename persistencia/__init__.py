import os

from .snapshot import leer_snapshot, escribir_snapshot, resumen_snapshots, filas_catalogo
from .tendencias import leer_ultima_tendencia, registrar_cambio_tendencia
from .catalogo import leer_fecha_inicio, registrar_simbolo, listar_catalogo
from . import migraciones as _m

# Los procesos auxiliares del VPS (coordinador, motor, colector, servidor local) ponen
# MTB_SIN_MIGRACIONES=1: solo la API migra. Cada ALTER TABLE ... IF NOT EXISTS pide bloqueo
# exclusivo aunque no cambie nada, y con varios procesos arrancando a la vez daba deadlock.
if os.environ.get("MTB_SIN_MIGRACIONES") != "1":
    _m.aplicar()
