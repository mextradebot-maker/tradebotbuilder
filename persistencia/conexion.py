import os

import psycopg


def get_conn() -> psycopg.Connection:
    # ponytail: nueva conexion por llamada (no hay pool); serverless = una request
    # por proceso, así que el costo es una conexion por snapshot read/write cycle.
    # Agregar psycopg_pool si el P95 de latencia lo justifica.
    return psycopg.connect(os.environ["DATABASE_URL"])
