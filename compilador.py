"""Compilador de robots personalizados — corre en el VPS Windows (lo expone servidor_local.py).

POST /compilar  X-Compilador-Key: <COMPILADOR_KEY>
     {"token": "MTB-XXXXX-XXXXX-XXXXX-XXXXX", "simbolo"?: "XAUUSD", "temporalidad"?: "Intraday 1H", "capital"?: 10000}
  → 200 application/octet-stream: MexTradeBot_SeguidorSMC.ex5 con MTB_LICENSE_TOKEN ya puesto
    y, si vienen los presets, InpSimboloConsulta / InpTemporalidad / InpTF (y InpCapital solo si
    mandan capital: sin él la línea queda en 0, que para el robot significa "usa el balance de la cuenta").
    simbolo y temporalidad van juntos y son obligatorios en cuanto se pida cualquier preset.
  servidor_local.py agrega `X-Compilador-Presets: 1` a toda respuesta: mtb-api no entrega un
  .ex5 con presets pedidos si el VPS sigue con el compilador viejo (que los ignora).

Solo lo llama mtb-api (la clave vive en su .env y en el de este VPS). Token y presets se
validan contra formato exacto / listas cerradas antes de tocar el código fuente, así nada
del request puede inyectarse en el .mq5. MetaEditor: METAEDITOR_PATH o el primero en C:\\Program Files\\*\\.
"""

import glob
import hmac
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

FUENTE = Path(__file__).resolve().parent / "robots" / "MexTradeBot_SeguidorSMC.mq5"
LINEA_TOKEN = 'input string MTB_LICENSE_TOKEN   = "";'
LINEA_SIMBOLO = 'input string InpSimboloConsulta  = "XAUUSD";'
LINEA_TEMPORALIDAD = 'input string InpTemporalidad     = "Intraday 1H";'
LINEA_TF = "input ENUM_TIMEFRAMES InpTF      = PERIOD_H1;"
LINEA_CAPITAL = "input double InpCapital = 0;"
TOKEN_RE = re.compile(r"MTB-([A-HJ-NP-Z2-9]{5}-){3}[A-HJ-NP-Z2-9]{5}")
# Copia de persistencia/migraciones._SIMBOLOS_SEED: el VPS compila sin Postgres (demo() verifica que sigan iguales)
SIMBOLOS = (
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "XAUUSD", "XAGUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD",
    "AUDCAD", "NZDJPY", "CADJPY", "US30", "US100", "US500", "GER40", "UK100",
    "JP225", "XPTUSD", "XPDUSD", "WTIUSD", "BRENTUSD", "BTCUSD", "ETHUSD",
    "XRPUSD", "GOOGL", "NVDA", "META", "WMT",
)
TF_POR_TEMPORALIDAD = {
    "Scalping 15m": "PERIOD_M15", "Scalping 30m": "PERIOD_M30", "Intraday 1H": "PERIOD_H1",
    "Intraday 4H": "PERIOD_H4", "Intraday D": "PERIOD_D1", "Swing (S)": "PERIOD_W1", "Swing (M)": "PERIOD_MN1",
}
CAPITAL_MIN, CAPITAL_MAX = 300.0, 10_000_000.0  # ponytail: tope solo contra números absurdos
_candado = threading.Lock()  # ponytail: una compilación a la vez; MetaEditor tarda ~1-2 s


def validar_presets(simbolo, temporalidad, capital=None) -> tuple[str, str, float | None]:
    """Listas cerradas + capital en rango; ValueError si algo no cuadra. La usa también mtb-api.

    capital=None es válido: el robot se queda con InpCapital = 0, o sea "usa el balance de la cuenta".
    """
    if not isinstance(simbolo, str) or simbolo not in SIMBOLOS:
        raise ValueError("simbolo fuera del catalogo")
    if not isinstance(temporalidad, str) or temporalidad not in TF_POR_TEMPORALIDAD:
        raise ValueError(f"temporalidad debe ser una de {list(TF_POR_TEMPORALIDAD)}")
    if capital is None:
        return simbolo, temporalidad, None
    try:
        capital = float(capital)
    except (TypeError, ValueError):
        raise ValueError("capital debe ser numerico") from None
    if not (math.isfinite(capital) and CAPITAL_MIN <= capital <= CAPITAL_MAX):
        raise ValueError(f"capital debe estar entre {CAPITAL_MIN:.0f} y {CAPITAL_MAX:.0f}")
    return simbolo, temporalidad, round(capital, 2)


def preparar_fuente(fuente: str, token: str, simbolo=None, temporalidad=None, capital=None) -> str:
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("token con formato inválido")
    cambios = {LINEA_TOKEN: f'input string MTB_LICENSE_TOKEN   = "{token}";'}
    if (simbolo, temporalidad, capital) != (None, None, None):
        simbolo, temporalidad, capital = validar_presets(simbolo, temporalidad, capital)
        cambios.update({
            LINEA_SIMBOLO: f'input string InpSimboloConsulta  = "{simbolo}";',
            LINEA_TEMPORALIDAD: f'input string InpTemporalidad     = "{temporalidad}";',
            LINEA_TF: f"input ENUM_TIMEFRAMES InpTF      = {TF_POR_TEMPORALIDAD[temporalidad]};",
        })
        if capital is not None:  # sin capital, InpCapital = 0 → el robot usa el balance de la cuenta
            cambios[LINEA_CAPITAL] = f"input double InpCapital = {capital:.2f};"
    for vieja, nueva in cambios.items():
        if fuente.count(vieja) != 1:
            raise ValueError(f"el .mq5 no tiene exactamente una vez la línea esperada: {vieja}")
        fuente = fuente.replace(vieja, nueva)
    return fuente


def metaeditor() -> str:
    ruta = os.environ.get("METAEDITOR_PATH") or next(iter(sorted(glob.glob(r"C:\Program Files\*\MetaEditor64.exe"))), "")
    if not ruta or not os.path.exists(ruta):
        raise RuntimeError("MetaEditor64.exe no encontrado (configura METAEDITOR_PATH)")
    return ruta


def compilar(token: str, simbolo=None, temporalidad=None, capital=None) -> bytes:
    fuente = preparar_fuente(FUENTE.read_text(encoding="utf-8"), token, simbolo, temporalidad, capital)
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
        datos = json.loads(cuerpo or b"{}")
        if not isinstance(datos, dict):
            raise ValueError("se esperaba un objeto JSON")
        presets = (datos.get("simbolo"), datos.get("temporalidad"), datos.get("capital"))
        return 200, "application/octet-stream", compilar(str(datos.get("token", "")), *presets)
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

    # presets: las 4 lineas cambian, el comentario de cada una se conserva
    con = preparar_fuente(fuente, token, "WTIUSD", "Swing (S)", "12345.678")
    assert 'InpSimboloConsulta  = "WTIUSD";      // Simbolo' in con
    assert 'InpTemporalidad     = "Swing (S)"; //' in con
    assert "InpTF      = PERIOD_W1;     //" in con and "input double InpCapital = 12345.68; //" in con
    assert preparar_fuente(fuente, token) == fuente.replace(LINEA_TOKEN, f'input string MTB_LICENSE_TOKEN   = "{token}";')

    # presets sin capital (lo que pide el boton "Robot" del admin): simbolo y TF si, InpCapital
    # intacto en 0 para que el robot calcule con el balance real de la cuenta
    sin_capital = preparar_fuente(fuente, token, "XAUUSD", "Swing (S)")
    assert 'InpSimboloConsulta  = "XAUUSD";' in sin_capital and "InpTF      = PERIOD_W1;" in sin_capital
    assert 'InpTemporalidad     = "Swing (S)";' in sin_capital and LINEA_CAPITAL in sin_capital
    assert validar_presets("XAUUSD", "Swing (S)") == ("XAUUSD", "Swing (S)", None)
    assert preparar_fuente(fuente.replace(LINEA_CAPITAL, "//"), token, "XAUUSD", "Swing (S)")  # sin capital no la exige
    for t, tf in TF_POR_TEMPORALIDAD.items():
        assert f"InpTF      = {tf};" in preparar_fuente(fuente, token, "EURUSD", t, 300)
    malos = [
        ('XAUUSD"; input int x=1; //', "Intraday 1H", 1000),   # inyeccion en simbolo
        ("XAUUSD\n#property x", "Intraday 1H", 1000),
        ("xauusd", "Intraday 1H", 1000), ("GOLD", "Intraday 1H", 1000),
        ("XAUUSD", 'Intraday 1H"; //', 1000), ("XAUUSD", "Intraday\n1H", 1000), ("XAUUSD", "Intraday", 1000),
        ("XAUUSD", "Intraday 1H", 299.99), ("XAUUSD", "Intraday 1H", "1e400"), ("XAUUSD", "Intraday 1H", "nan"),
        ("XAUUSD", "Intraday 1H", "1000; //"), ("XAUUSD", "Intraday 1H", 10_000_001),
        (["XAUUSD"], "Intraday 1H", 1000), ("XAUUSD", None, 1000), ("XAUUSD", None, None), (None, "Intraday 1H", None),
    ]
    for s, t, c in malos:
        try:
            preparar_fuente(fuente, token, s, t, c)
            raise AssertionError(f"aceptó presets inválidos {(s, t, c)!r}")
        except ValueError:
            pass
    for linea in (LINEA_SIMBOLO, LINEA_TEMPORALIDAD, LINEA_TF, LINEA_CAPITAL):
        for rota in (fuente.replace(linea, "//"), fuente + "\n" + linea):  # falta o repetida
            try:
                preparar_fuente(rota, token, "XAUUSD", "Intraday 1H", 1000)
                raise AssertionError(f"compiló sin la línea única {linea!r}")
            except ValueError:
                pass
        assert preparar_fuente(fuente.replace(linea, "//"), token)  # sin presets esas líneas no importan

    # las listas cerradas siguen iguales a las semillas del catalogo (sin importar persistencia: aplica migraciones)
    import ast
    arbol = ast.parse((FUENTE.parent.parent / "persistencia" / "migraciones.py").read_text(encoding="utf-8"))
    semillas = {n.targets[0].id: ast.literal_eval(n.value) for n in arbol.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_SIMBOLOS_SEED", "_TEMPORALIDADES_SEED")}
    assert list(SIMBOLOS) == semillas["_SIMBOLOS_SEED"] and len(SIMBOLOS) == 34
    assert list(TF_POR_TEMPORALIDAD) == semillas["_TEMPORALIDADES_SEED"]

    previa = os.environ.pop("COMPILADOR_KEY", None)
    try:
        assert procesar_http({}, b"{}")[0] == 503
        os.environ["COMPILADOR_KEY"] = "k"
        assert procesar_http({"X-Compilador-Key": "otra"}, b"{}")[0] == 401
        assert procesar_http({"x-compilador-key": "k"}, b'{"token": "malo"}')[0] == 400
        assert procesar_http({"x-compilador-key": "k"}, b"[1]")[0] == 400
        malo = {"token": token, "simbolo": 'XAUUSD"', "temporalidad": "Intraday 1H", "capital": 500}
        assert procesar_http({"x-compilador-key": "k"}, json.dumps(malo).encode())[0] == 400
        parcial = {"token": token, "simbolo": "XAUUSD"}  # presets incompletos: no se compila a medias
        assert procesar_http({"x-compilador-key": "k"}, json.dumps(parcial).encode())[0] == 400
        solo_tf = {"token": token, "temporalidad": "Intraday 1H"}
        assert procesar_http({"x-compilador-key": "k"}, json.dumps(solo_tf).encode())[0] == 400
        try:
            metaeditor()
        except RuntimeError:
            print("compilador.demo() OK (sin MetaEditor en esta máquina: se omite la compilación real)")
            return
        st, tipo, ex5 = procesar_http({"X-Compilador-Key": "k"}, json.dumps({"token": token}).encode())
        assert st == 200 and tipo == "application/octet-stream" and len(ex5) > 10_000, (st, ex5[:200])
        presets = {"token": token, "simbolo": "WTIUSD", "temporalidad": "Swing (S)", "capital": 10000}
        st, tipo, ex5p = procesar_http({"X-Compilador-Key": "k"}, json.dumps(presets).encode())
        assert st == 200 and len(ex5p) > 10_000 and ex5p != ex5, (st, ex5p[:200])
        sin_cap = {"token": token, "simbolo": "WTIUSD", "temporalidad": "Swing (S)"}
        st, tipo, ex5s = procesar_http({"X-Compilador-Key": "k"}, json.dumps(sin_cap).encode())
        assert st == 200 and len(ex5s) > 10_000, (st, ex5s[:200])
    finally:
        os.environ.pop("COMPILADOR_KEY", None)
        if previa is not None:
            os.environ["COMPILADOR_KEY"] = previa
    print(f"compilador.demo() OK — .ex5 real de {len(ex5):,} bytes")


if __name__ == "__main__":
    demo()
