"""Conector XM (MT5) — lectura + envío de órdenes.

Requiere una terminal MT5 de XM corriendo/logueada en esta máquina (el
paquete MetaTrader5 es un puente IPC local, no funciona en Vercel/serverless
— ver README). Credenciales via variables de entorno, nunca hardcodeadas:
XM_LOGIN, XM_PASSWORD, XM_SERVER, y opcional XM_MT5_PATH si la terminal no
está en la ruta por defecto. Copia .env.example a .env y llénalo ahí.

Funciones de escritura (abrir_posicion, cerrar_posicion): solo para cuentas
demo de MT5 en el VPS (38.89.76.48). Nunca llamar contra cuentas reales sin
confirmación explícita de Ricardo.
"""

import os
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

MAGIC_MTB = 202600  # ponytail: magic number único para todas las órdenes MexTradeBot; separar por estrategia si se necesita auditoría granular


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


def _filling_mode(simbolo: str) -> int:
    """Devuelve el primer modo de llenado soportado por el símbolo (FOK > IOC > RETURN).

    XM cambia el modo según el servidor — consultarlo evita el retcode 10030.
    """
    info = mt5.symbol_info(simbolo)
    if info is None:
        return mt5.ORDER_FILLING_IOC  # fallback razonable
    if info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    if info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


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
    if not mt5.symbol_select(simbolo, True):
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudo seleccionar el símbolo {simbolo} en Market Watch: [{codigo}] {mensaje}")
    rates = mt5.copy_rates_range(simbolo, timeframe, inicio, fin)
    if rates is None or len(rates) == 0:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudieron obtener velas de {simbolo}: [{codigo}] {mensaje}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]]


def historial_operaciones(dias: int = 60) -> list[dict]:
    """Deals (aperturas y cierres) de los últimos `dias`, vía mt5.history_deals_get().

    Devuelve TODOS los deals crudos (entrada y salida) — quien consuma esto
    filtra según lo que necesite. Para T-05, ver servidor_local.calcular_resumen,
    que se queda solo con los de cierre (DEAL_ENTRY_OUT) para calcular P&L
    realizado por semana, sin duplicar esa lógica aquí.
    """
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=dias)
    deals = mt5.history_deals_get(inicio, fin)
    if deals is None:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudo obtener historial de operaciones: [{codigo}] {mensaje}")
    return [d._asdict() for d in deals]


def abrir_posicion(
    simbolo: str,
    tipo: int,
    lotes: float,
    *,
    sl_precio: float | None = None,
    tp_precio: float | None = None,
    comentario: str = "MexTradeBot",
) -> dict:
    """Abre una orden de mercado. Devuelve el resultado de order_send() como dict.

    tipo: mt5.ORDER_TYPE_BUY o mt5.ORDER_TYPE_SELL
    lotes: volumen en lotes estándar (0.01 = microlote)
    sl_precio / tp_precio: precios absolutos; None = sin SL/TP
    """
    if not mt5.symbol_select(simbolo, True):
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudo seleccionar {simbolo}: [{codigo}] {mensaje}")

    tick = mt5.symbol_info_tick(simbolo)
    if tick is None:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No hay tick para {simbolo}: [{codigo}] {mensaje}")

    precio = tick.ask if tipo == mt5.ORDER_TYPE_BUY else tick.bid
    request: dict = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": simbolo,
        "volume": lotes,
        "type": tipo,
        "price": precio,
        "deviation": 20,
        "magic": MAGIC_MTB,
        "comment": comentario,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(simbolo),
    }
    if sl_precio is not None:
        request["sl"] = sl_precio
    if tp_precio is not None:
        request["tp"] = tp_precio

    resultado = mt5.order_send(request)
    if resultado is None or resultado.retcode != mt5.TRADE_RETCODE_DONE:
        codigo, mensaje = mt5.last_error()
        retcode = resultado.retcode if resultado else None
        raise ConexionXMError(
            f"abrir_posicion({simbolo}) falló — retcode {retcode}, [{codigo}] {mensaje}"
        )
    return resultado._asdict()


def cerrar_posicion(ticket: int) -> dict:
    """Cierra una posición abierta por su ticket. Devuelve el resultado de order_send() como dict."""
    posiciones = mt5.positions_get(ticket=ticket)
    if not posiciones:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"Posición {ticket} no encontrada: [{codigo}] {mensaje}")

    pos = posiciones[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No hay tick para {pos.symbol}: [{codigo}] {mensaje}")

    tipo_cierre = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    precio_cierre = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": tipo_cierre,
        "position": ticket,
        "price": precio_cierre,
        "deviation": 20,
        "magic": pos.magic,
        "comment": "close MexTradeBot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(pos.symbol),
    }

    resultado = mt5.order_send(request)
    if resultado is None or resultado.retcode != mt5.TRADE_RETCODE_DONE:
        codigo, mensaje = mt5.last_error()
        retcode = resultado.retcode if resultado else None
        raise ConexionXMError(
            f"cerrar_posicion(ticket={ticket}) falló — retcode {retcode}, [{codigo}] {mensaje}"
        )
    return resultado._asdict()


def posiciones_abiertas(simbolo: str | None = None) -> list[dict]:
    """Lista posiciones abiertas activas. Filtra por símbolo si se pasa."""
    raw = mt5.positions_get(symbol=simbolo) if simbolo else mt5.positions_get()
    if raw is None:
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"No se pudo obtener posiciones: [{codigo}] {mensaje}")
    return [p._asdict() for p in raw]


def login_cuenta(login: int, password: str, server: str) -> None:
    """Cambia la cuenta activa en la terminal MT5 ya inicializada.

    Usar después de conectar() cuando se necesita operar en una cuenta distinta a la del .env.
    Necesario para el coordinador multi-cuenta (Bloque 3 Master Trader).
    """
    if not mt5.login(login, password=password, server=server):
        codigo, mensaje = mt5.last_error()
        raise ConexionXMError(f"login_cuenta({login}@{server}) falló: [{codigo}] {mensaje}")


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

        ahora = datetime.utcnow()
        # "GOLD" es el nombre real en este servidor XM (no "XAUUSD" — cada broker nombra distinto,
        # verificar con mt5.symbols_get() si cambia de cuenta/servidor)
        ohlc = velas_en_vivo("GOLD", ahora - timedelta(days=5), ahora)
        assert list(ohlc.columns) == ["open", "high", "low", "close", "volume"]
        print(f"  {len(ohlc)} velas GOLD H1 en vivo, última: {ohlc.index[-1]}")

        ops = historial_operaciones(dias=30)
        print(f"  {len(ops)} deals en los últimos 30 días")
    finally:
        desconectar()


if __name__ == "__main__":
    demo()
