"""Compilador de robots personalizados — corre en el VPS Windows (lo expone servidor_local.py).

POST /compilar  X-Compilador-Key: <COMPILADOR_KEY>   {"token": "MTB-XXXXX-XXXXX-XXXXX-XXXXX"}
  → 200 application/octet-stream: MexTradeBot_SeguidorSMC.ex5 con MTB_LICENSE_TOKEN ya puesto.

Solo lo llama mtb-api (la clave vive en su .env y en el de este VPS). El token se valida
contra el formato exacto antes de tocar el código fuente, así nada del request puede
inyectarse en el .mq5. MetaEditor: METAEDITOR_PATH o el primero en C:\\Program Files\\*\\.
"""

import glob
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

FUENTE = Path(__file__).resolve().parent / "robots" / "MexTradeBot_SeguidorSMC.mq5"
LINEA_TOKEN = 'input string MTB_LICENSE_TOKEN   = "";'
TOKEN_RE = re.compile(r"MTB-([A-HJ-NP-Z2-9]{5}-){3}[A-HJ-NP-Z2-9]{5}")
_candado = threading.Lock()  # ponytail: una compilación a la vez; MetaEditor tarda ~1-2 s


def preparar_fuente(fuente: str, token: str) -> str:
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("token con formato inválido")
    if fuente.count(LINEA_TOKEN) != 1:
        raise ValueError("el .mq5 no tiene la línea de MTB_LICENSE_TOKEN esperada")
    return fuente.replace(LINEA_TOKEN, f'input string MTB_LICENSE_TOKEN   = "{token}";')


def metaeditor() -> str:
    ruta = os.environ.get("METAEDITOR_PATH") or next(iter(sorted(glob.glob(r"C:\Program Files\*\MetaEditor64.exe"))), "")
    if not ruta or not os.path.exists(ruta):
        raise RuntimeError("MetaEditor64.exe no encontrado (configura METAEDITOR_PATH)")
    return ruta


def compilar(token: str) -> bytes:
    fuente = preparar_fuente(FUENTE.read_text(encoding="utf-8"), token)
    with _candado, tempfile.TemporaryDirectory() as tmp:
        mq5 = Path(tmp) / "MexTradeBot_SeguidorSMC.mq5"
        log = Path(tmp) / "compilar.log"
        mq5.write_text(fuente, encoding="utf-8")
        subprocess.run([metaeditor(), f"/compile:{mq5}", f"/log:{log}"], timeout=120, check=False)
        texto_log = log.read_text(encoding="utf-16", errors="replace") if log.exists() else ""
        ex5 = mq5.with_suffix(".ex5")
        if " 0 errors" not in texto_log or not ex5.exists():
            ultimo = texto_log.strip().splitlines()[-1] if texto_log.strip() else "sin log"
            raise RuntimeError(f"compilación falló: {ultimo}")
        return ex5.read_bytes()


def procesar_http(headers: dict, cuerpo: bytes) -> tuple[int, str, bytes]:
    """(status, content_type, body). Falla cerrado sin COMPILADOR_KEY."""
    esperada = os.environ.get("COMPILADOR_KEY", "")
    recibida = next((v for k, v in headers.items() if k.lower() == "x-compilador-key"), "") or ""

    def error(status: int, msg: str) -> tuple[int, str, bytes]:
        return status, "application/json", json.dumps({"error": msg}).encode()

    if not esperada:
        return error(503, "COMPILADOR_KEY no configurada")
    if not hmac.compare_digest(recibida.encode(), esperada.encode()):
        return error(401, "clave de compilador inválida")
    try:
        token = str(json.loads(cuerpo or b"{}").get("token", ""))
        return 200, "application/octet-stream", compilar(token)
    except (ValueError, json.JSONDecodeError) as e:
        return error(400, str(e))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return error(500, str(e))


def demo() -> None:
    token = "MTB-ABCDE-FGHJK-LMNPQ-RS234"
    fuente = FUENTE.read_text(encoding="utf-8")
    assert f'MTB_LICENSE_TOKEN   = "{token}";' in preparar_fuente(fuente, token)
    for malo in ['MTB-ABCDE-FGHJK-LMNPQ-RS23"', "MTB-ABCD0-FGHJK-LMNPQ-RS234", "x"]:
        try:
            preparar_fuente(fuente, malo)
            raise AssertionError(f"aceptó token inválido {malo!r}")
        except ValueError:
            pass

    previa = os.environ.pop("COMPILADOR_KEY", None)
    try:
        assert procesar_http({}, b"{}")[0] == 503
        os.environ["COMPILADOR_KEY"] = "k"
        assert procesar_http({"X-Compilador-Key": "otra"}, b"{}")[0] == 401
        assert procesar_http({"x-compilador-key": "k"}, b'{"token": "malo"}')[0] == 400
        try:
            metaeditor()
        except RuntimeError:
            print("compilador.demo() OK (sin MetaEditor en esta máquina: se omite la compilación real)")
            return
        st, tipo, ex5 = procesar_http({"X-Compilador-Key": "k"}, json.dumps({"token": token}).encode())
        assert st == 200 and tipo == "application/octet-stream" and len(ex5) > 10_000, (st, ex5[:200])
    finally:
        os.environ.pop("COMPILADOR_KEY", None)
        if previa is not None:
            os.environ["COMPILADOR_KEY"] = previa
    print(f"compilador.demo() OK — .ex5 real de {len(ex5):,} bytes")


if __name__ == "__main__":
    demo()
