"""Conector XM (MT4/MT5) — Plan de construcción, Paso 1b.

Solo lectura por ahora: conexión, info de cuenta y velas en vivo. Envío de
órdenes queda deliberadamente fuera de este módulo — es un paso aparte que
necesita sus propias salvaguardas antes de tocarlo.

Requiere una terminal MT5 de XM corriendo/logueada en esta máquina (el
paquete MetaTrader5 es un puente IPC local, no funciona en Vercel/serverless
— ver README). Credenciales via variables de entorno, nunca hardcodeadas:
XM_LOGIN, XM_PASSWORD, XM_SERVER, y opcional XM_MT5_PATH si la terminal no
está en la ruta por defecto. Copia .env.example a .env y llénalo ahí.
"""

import os

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


class ConexionXMError(RuntimeError):
    pass


def _credenciales() -> dict:
    login, password, server = (os.environ.get(k) for k in ("XM_LOGIN", "XM_PASSWORD", "XM_SERVER"))
    faltantes = [k for k, v in [("XM_LOGIN", login), ("XM_PASSWORD", password), ("XM_SERVER", server)] if not v]
    if faltantes:
        raise ConexionXMError(f"Faltan variables de entorno: {', '.join(faltantes)} (ver .env.example)")

    kwargs = {"login": int(login), "password": password, "server": server}
    path = os.environ.get("XM_MT5_PATH")
    if path:
        kwargs["path"] = path
    return kwargs


def conectar() -> None:
    if not mt5.initialize(**_credenciales()):
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudo conectar a MT5: [{codigo}] {mensaje}")


def desconectar() -> None:
    mt5.shutdown()


def info_cuenta() -> dict:
    cuenta = mt5.account_info()
    if cuenta is None:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No hay cuenta conectada: [{codigo}] {mensaje}")
    return cuenta._asdict()


def velas_en_vivo(simbolo: str, inicio, fin, timeframe=mt5.TIMEFRAME_H1) -> pd.DataFrame:
    """Igual formato que conectividad.historico.obtener_velas — compatible con motor_smc.analizar()."""
    rates = mt5.copy_rates_range(simbolo, timeframe, inicio, fin)
    if rates is None or len(rates) == 0:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudieron obtener velas de {simbolo}: [{codigo}] {mensaje}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]]


def demo() -> None:
    try:
        conectar()
    except ConexionXMError as e:
        print(f"conectividad.xm.demo() SIN CORRER — {e}")
        print("Copia .env.example a .env, llena tus credenciales de XM y vuelve a correr.")
        return

    try:
        cuenta = info_cuenta()
        assert "login" in cuenta and "balance" in cuenta
        print(f"conectividad.xm.demo() OK — cuenta {cuenta['login']} en {cuenta['server']}, balance {cuenta['balance']} {cuenta['currency']}")

        from datetime import datetime, timedelta

        ahora = datetime.utcnow()
        ohlc = velas_en_vivo("XAUUSD", ahora - timedelta(days=5), ahora)
        assert list(ohlc.columns) == ["open", "high", "low", "close", "volume"]
        print(f"  {len(ohlc)} velas XAUUSD H1 en vivo, última: {ohlc.index[-1]}")
    finally:
        desconectar()


if __name__ == "__main__":
    demo()
