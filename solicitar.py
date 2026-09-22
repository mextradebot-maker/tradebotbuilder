"""Registra una petición manual de entrada o salida para el coordinador.

Uso:
  python solicitar.py EURUSD BUY
  python solicitar.py GOLD   SELL 0.02
  python solicitar.py USDJPY CERRAR
  python solicitar.py        lista        ← ver pendientes y recientes
"""
from dotenv import load_dotenv
load_dotenv()

import sys
from persistencia.conexion import get_conn


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].upper()

    if cmd == "LISTA":
        _listar()
        return

    if len(sys.argv) < 3:
        print("Uso: python solicitar.py SIMBOLO DIRECCION [LOTES]")
        sys.exit(1)

    simbolo   = cmd
    direccion = sys.argv[2].upper()
    lotes     = float(sys.argv[3]) if len(sys.argv) > 3 else None

    if direccion not in ("BUY", "SELL", "CERRAR"):
        print(f"Dirección inválida '{direccion}'. Usa BUY, SELL o CERRAR.")
        sys.exit(1)

    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO peticiones_usuario (simbolo, direccion, lotes) VALUES (%s, %s, %s) RETURNING id",
            (simbolo, direccion, lotes),
        ).fetchone()

    suffix = f"  {lotes} lotes" if lotes else ""
    print(f"✓ Petición #{row[0]}: {simbolo} {direccion}{suffix}")
    print("  El coordinador la ejecutará en el próximo ciclo (máx 15 min).")


def _listar() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, simbolo, direccion, lotes, estado, motivo_error, creada_en
               FROM peticiones_usuario
               ORDER BY creada_en DESC LIMIT 20"""
        ).fetchall()
    if not rows:
        print("Sin peticiones.")
        return
    print(f"{'ID':>4}  {'Símbolo':<8}  {'Dir':<6}  {'Lotes':>6}  {'Estado':<10}  {'Notas'}")
    print("-" * 65)
    for r in rows:
        lotes_str = f"{r[3]:.2f}" if r[3] else "auto"
        notas = r[5] or ""
        print(f"{r[0]:>4}  {r[1]:<8}  {r[2]:<6}  {lotes_str:>6}  {r[4]:<10}  {notas}")


if __name__ == "__main__":
    main()
