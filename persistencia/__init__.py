from .snapshot import leer_snapshot, escribir_snapshot
from .tendencias import leer_ultima_tendencia, registrar_cambio_tendencia
from . import migraciones as _m

_m.aplicar()
