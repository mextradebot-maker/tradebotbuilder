from .historico import obtener_velas, SIMBOLOS

__all__ = ["obtener_velas", "SIMBOLOS"]

# conectividad.xm NO se importa aca: requiere MetaTrader5 (Windows-only, ver
# xm.py) — importarlo aqui rompería `from conectividad import obtener_velas`
# en cualquier entorno sin Windows (ej. el build de Vercel). Usar
# `from conectividad.xm import conectar` directo donde haga falta.
