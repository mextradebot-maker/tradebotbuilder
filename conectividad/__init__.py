from .historico import (obtener_velas, SIMBOLOS, TEMPORALIDAD_A_INTERVALO, TIPOS_SOLO_ALCISTA,
                        TEMPORALIDADES, ALIAS_TEMPORALIDAD, resolver_temporalidad)

__all__ = ["obtener_velas", "SIMBOLOS", "TEMPORALIDAD_A_INTERVALO", "TIPOS_SOLO_ALCISTA",
           "TEMPORALIDADES", "ALIAS_TEMPORALIDAD", "resolver_temporalidad"]

# conectividad.xm NO se importa aca: requiere MetaTrader5 (Windows-only, ver
# xm.py) — importarlo aqui rompería `from conectividad import obtener_velas`
# en cualquier entorno sin Windows (ej. el build de Vercel). Usar
# `from conectividad.xm import conectar` directo donde haga falta.
