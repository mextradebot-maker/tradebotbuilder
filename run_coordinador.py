"""Script de entrada para ejecutar un ciclo del Agente Coordinador del Master Trader."""

import sys
import os

# Asegurar import de módulos locales
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coordinador.coordinador import ejecutar_ciclo_coordinacion

if __name__ == "__main__":
    try:
        ejecutar_ciclo_coordinacion()
    except Exception as e:
        print(f"Error al ejecutar ciclo del coordinador: {e}", file=sys.stderr)
        sys.exit(1)
