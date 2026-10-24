"""Colector de velas de XM (MT5) -> servidor. Corre en el VPS Windows, junto a la terminal MT5 de XM.

Cada 1 s mira, por activo del catalogo y por serie base (15m, 1H), si cerro una vela nueva en el MT5 y, en cuanto
la ve (segundos), la manda a POST /api/v1/velas-xm. El servidor la guarda y el refresco recalcula el analisis al
instante, sin esperar los ~2 min de publicacion de Dukascopy. Sin esto todo sigue funcionando por Dukascopy: si el
colector se detiene, el refresco normal toma el relevo solo.

Solo 15m y 1H (de ahi salen 30m y 4H). Diario, semanal y mensual siguen viniendo de Dukascopy: XM abre su vela
diaria a otra hora y mezclar fuentes cambiaria esas estructuras.

Uso (VPS, desde C:\\MTB):  python colector_velas.py            # corre para siempre
                           python colector_velas.py --seco     # imprime lo que mandaria, sin enviar
                           python colector_velas.py --una-vez  # una sola pasada y termina
Variables (.env): MTB_SERVICE_KEY (la misma del servidor), MTB_API_URL (por defecto https://mextradebot.com.mx),
COLECTOR_MT5_PATH (por defecto C:\\MT5\\cuenta1\\terminal64.exe; se conecta a la terminal ya logueada, sin credenciales).
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

SERIES = {"15m": 900, "1H": 3600}  # serie base -> segundos de una vela
VELAS_POR_ENVIO = 3  # las ultimas 3 cerradas: la nueva y 2 anteriores por si alguna se corrigio
TOLERANCIA_S = 5
REINTENTO_RAPIDO_S = 2  # mientras se espera una vela que acaba de cerrar
REINTENTO_LENTO_S = 60  # pasados 2 min sin vela nueva (mercado cerrado): preguntar cada minuto
VENTANA_RAPIDA_S = 120
UA = "MTB-Colector/1.0"  # Cloudflare bloquea el User-Agent por defecto de urllib


def _domingo(anio: int, mes: int, n: int) -> int:
    """Dia del mes del n-esimo domingo."""
    primero = datetime(anio, mes, 1).weekday()  # lunes=0 ... domingo=6
    return 1 + (6 - primero) % 7 + 7 * (n - 1)


def horario_verano_us(utc: datetime) -> bool:
    """Horario de verano de EE. UU.: 2.º domingo de marzo 07:00 UTC -> 1.er domingo de noviembre 06:00 UTC."""
    ini = datetime(utc.year, 3, _domingo(utc.year, 3, 2), 7)
    fin = datetime(utc.year, 11, _domingo(utc.year, 11, 1), 6)
    return ini <= utc.replace(tzinfo=None) < fin


def offset_regla(utc: datetime) -> int:
    """Segundos que el reloj de servidor de XM va por delante de UTC: GMT+2 en invierno, GMT+3 con horario de verano US."""
    return 10800 if horario_verano_us(utc) else 7200


def offset_observado(tick_time: int, ahora_s: float) -> int | None:
    """Offset (s) visto en el ultimo tick: tick.time (reloj del servidor) - reloj UTC real, a horas completas.
    None si el tick no es reciente o el resultado no es un offset plausible (+1 h a +4 h)."""
    d = tick_time - ahora_s
    o = round(d / 3600) * 3600
    return o if 3600 <= o <= 14400 and abs(d - o) <= 300 else None


def cerradas(rates, paso_s: int, offset_s: int, ahora_s: float, ultimo: int | None) -> tuple[list[dict], int | None]:
    """rates: filas (time, open, high, low, close, tick_volume) del MT5 con la ultima vela CERRADA al final (se pidio
    desde la posicion 1). Devuelve (velas a enviar, ts de la ultima), vacio si no hay una vela nueva respecto a `ultimo`.
    Descarta lo que no esta alineado a la serie o no ha cerrado: señal de que el offset de reloj esta mal."""
    velas = []
    for r in rates:
        ts = int(r[0]) - offset_s
        if ts % paso_s or ts + paso_s > ahora_s + TOLERANCIA_S or ahora_s - ts > 2 * 86400:
            logging.error("vela descartada ts=%s (offset %s s): no alineada, abierta o antigua", ts, offset_s)
            continue
        velas.append({"ts": ts, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
    if not velas or (ultimo is not None and velas[-1]["ts"] <= ultimo):
        return [], ultimo
    return velas, velas[-1]["ts"]


class Colector:
    """mt5: modulo MetaTrader5 (o un doble en pruebas). enviar(simbolo, serie, velas) -> True si el servidor la acepto."""

    def __init__(self, mt5, simbolos: dict, enviar, reloj=time.time):
        self.mt5, self.simbolos, self.enviar, self.reloj = mt5, simbolos, enviar, reloj
        self.ultimo: dict = {}  # (simbolo, serie) -> ts de la ultima vela enviada
        self.proximo: dict = {}  # (simbolo, serie) -> proximo intento (epoch s)
        self.offset = None
        self.offset_en = 0.0
        self._tf = {"15m": mt5.TIMEFRAME_M15, "1H": mt5.TIMEFRAME_H1}

    def calibrar(self, ahora_s: float) -> int:
        """Offset del reloj del servidor: el observado en un tick de BTCUSD (opera 24/7); si no, la regla GMT+2/+3."""
        if self.offset is None or ahora_s - self.offset_en > 3600:
            obs = None
            tick = self.mt5.symbol_info_tick(self.simbolos.get("BTCUSD", "BTCUSD"))
            if tick is not None:
                obs = offset_observado(int(tick.time), ahora_s)
            regla = offset_regla(datetime.fromtimestamp(ahora_s, timezone.utc).replace(tzinfo=None))
            if obs is not None and obs != regla:
                logging.warning("offset de servidor observado %s s distinto de la regla %s s: se usa el observado", obs, regla)
            self.offset, self.offset_en = (obs if obs is not None else regla), ahora_s
        return self.offset

    def pasada(self) -> int:
        """Una vuelta a todos los activos y series. Devuelve cuantas series enviadas."""
        ahora = self.reloj()
        off = self.calibrar(ahora)
        enviadas = 0
        for api, xm in self.simbolos.items():
            for serie, paso in SERIES.items():
                k = (api, serie)
                cierre = int(ahora // paso) * paso  # ultima vela cerrada debe terminar aqui
                if self.ultimo.get(k, 0) >= cierre - paso or ahora < self.proximo.get(k, 0):
                    continue
                rates = self.mt5.copy_rates_from_pos(xm, self._tf[serie], 1, VELAS_POR_ENVIO)
                velas, nuevo = cerradas(rates if rates is not None else [], paso, off, ahora, self.ultimo.get(k))
                if velas and nuevo >= cierre - paso:
                    if self.enviar(api, serie, velas):
                        self.ultimo[k] = nuevo
                        enviadas += 1
                    else:
                        self.proximo[k] = ahora + REINTENTO_RAPIDO_S
                    continue
                # sin vela nueva: mercado cerrado o aun no llega; rapido durante 2 min tras el cierre, luego cada minuto
                self.proximo[k] = ahora + (REINTENTO_RAPIDO_S if ahora - cierre < VENTANA_RAPIDA_S else REINTENTO_LENTO_S)
        return enviadas


def _enviar_http(url: str, clave: str):
    import requests

    def enviar(simbolo, serie, velas) -> bool:
        try:
            r = requests.post(url, json={"simbolo": simbolo, "serie": serie, "velas": velas},
                              headers={"X-MTB-Service-Key": clave, "User-Agent": UA}, timeout=10)
        except requests.RequestException as e:
            logging.warning("envio %s %s fallo: %s", simbolo, serie, e)
            return False
        if r.status_code != 200:
            logging.warning("envio %s %s -> %s %s", simbolo, serie, r.status_code, r.text[:200])
        return r.status_code == 200 or 400 <= r.status_code < 500  # un 4xx no se arregla reintentando
    return enviar


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    seco, una_vez = "--seco" in sys.argv, "--una-vez" in sys.argv
    from dotenv import load_dotenv
    load_dotenv()
    import MetaTrader5 as mt5
    from api.catalogo import SIMBOLO_XM

    ruta = os.environ.get("COLECTOR_MT5_PATH", r"C:\MT5\cuenta1\terminal64.exe")
    if not mt5.initialize(path=ruta, portable=True):
        raise SystemExit(f"No se pudo conectar a la terminal MT5 {ruta}: {mt5.last_error()}")
    for xm in SIMBOLO_XM.values():
        mt5.symbol_select(xm, True)  # el simbolo debe estar en Market Watch para recibir velas
    if seco:
        def enviar(simbolo, serie, velas):
            print(simbolo, serie, [(datetime.fromtimestamp(v["ts"], timezone.utc).strftime("%m-%d %H:%M"), v["c"]) for v in velas])
            return True
    else:
        clave = os.environ.get("MTB_SERVICE_KEY")
        if not clave:
            raise SystemExit("Falta MTB_SERVICE_KEY en .env")
        enviar = _enviar_http(os.environ.get("MTB_API_URL", "https://mextradebot.com.mx") + "/api/v1/velas-xm", clave)
    c = Colector(mt5, dict(SIMBOLO_XM), enviar)
    logging.info("colector de velas activo: %d activos, series %s%s", len(c.simbolos), list(SERIES), " (modo seco)" if seco else "")
    while True:
        try:
            n = c.pasada()
            if n:
                logging.info("%d series enviadas", n)
        except Exception:
            logging.exception("pasada fallo")
        if una_vez:
            return
        time.sleep(1)


def demo() -> None:
    import types
    u = timezone.utc
    # horario de verano US 2026: 8 mar - 1 nov
    assert offset_regla(datetime(2026, 10, 24)) == 10800 and offset_regla(datetime(2026, 11, 1, 5, 59)) == 10800
    assert offset_regla(datetime(2026, 11, 1, 6)) == 7200 and offset_regla(datetime(2026, 3, 8, 6, 59)) == 7200
    assert offset_regla(datetime(2026, 3, 8, 7)) == 10800 and offset_regla(datetime(2026, 1, 15)) == 7200
    assert offset_observado(1000 + 10800 + 3, 1000) == 10800 and offset_observado(1000 + 7200 - 60, 1000) == 7200
    assert offset_observado(1000 + 3000, 1000) is None and offset_observado(1000 + 10800 + 600, 1000) is None

    ahora = datetime(2026, 10, 24, 7, 0, 3, tzinfo=u).timestamp()  # 15m: acaba de cerrar 06:45
    off = 10800
    srv = lambda h, m: int(datetime(2026, 10, 24, h, m, tzinfo=u).timestamp()) + off  # hora del reloj del servidor
    rates15 = [(srv(6, 15), 1.1, 1.2, 1.0, 1.15, 10), (srv(6, 30), 1.15, 1.2, 1.1, 1.18, 11), (srv(6, 45), 1.18, 1.3, 1.1, 1.2, 12)]
    velas, ult = cerradas(rates15, 900, off, ahora, None)
    assert len(velas) == 3 and ult == int(datetime(2026, 10, 24, 6, 45, tzinfo=u).timestamp()) and velas[-1]["c"] == 1.2
    assert cerradas(rates15, 900, off, ahora, ult) == ([], ult)  # nada nuevo
    assert cerradas(rates15, 900, 7200, ahora, None)[0] == []  # offset equivocado: nada alineado/cerrado -> se descarta
    assert cerradas([(srv(7, 0), 1, 1, 1, 1, 1)], 900, off, ahora, None)[0] == []  # vela que aun no cierra

    class MT5:
        TIMEFRAME_M15, TIMEFRAME_H1 = 15, 60

        def __init__(self): self.llamadas, self.hay_nueva = [], True

        def symbol_info_tick(self, s): return types.SimpleNamespace(time=int(ahora) + off + 1)

        def copy_rates_from_pos(self, s, tf, pos, n):
            self.llamadas.append((s, tf, pos, n))
            if not self.hay_nueva:
                return rates15[:2] if tf == 15 else []
            if tf == 15:
                return rates15
            return [(int(datetime(2026, 10, 24, 5, 0, tzinfo=u).timestamp()) + off, 1.1, 1.2, 1.0, 1.15, 5),
                    (int(datetime(2026, 10, 24, 6, 0, tzinfo=u).timestamp()) + off, 1.1, 1.2, 1.0, 1.16, 6)]

    mt5, reloj, enviados, ok = MT5(), [ahora], [], [True]
    col = Colector(mt5, {"EURUSD": "EURUSD", "BTCUSD": "BTCUSD"}, lambda s, se, v: enviados.append((s, se, len(v))) or ok[0], lambda: reloj[0])
    assert col.pasada() == 4 and len(enviados) == 4 and ("EURUSD", "1H", 2) in enviados and ("BTCUSD", "15m", 3) in enviados
    assert col.offset == off  # observado = regla
    n = len(mt5.llamadas)
    assert col.pasada() == 0 and len(mt5.llamadas) == n  # ya enviado: ni siquiera consulta al MT5
    # siguiente vela de 15m (07:15 + 3 s): aun no llega -> reintenta rapido y luego cada minuto
    reloj[0] = datetime(2026, 10, 24, 7, 15, 3, tzinfo=u).timestamp()
    mt5.hay_nueva = False
    assert col.pasada() == 0
    n = len(mt5.llamadas); reloj[0] += 1
    assert col.pasada() == 0 and len(mt5.llamadas) == n  # espera REINTENTO_RAPIDO_S antes de preguntar otra vez
    reloj[0] += 2
    assert col.pasada() == 0 and len(mt5.llamadas) > n
    reloj[0] = datetime(2026, 10, 24, 7, 20, tzinfo=u).timestamp()  # >2 min: ritmo lento
    col.pasada(); n = len(mt5.llamadas); reloj[0] += 10
    col.pasada(); assert len(mt5.llamadas) == n
    # envio rechazado (red): no se marca enviado y se reintenta
    col2 = Colector(MT5(), {"EURUSD": "EURUSD"}, lambda *a: False, lambda: ahora)
    assert col2.pasada() == 0 and not col2.ultimo
    print("colector_velas.demo() OK")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
