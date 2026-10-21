"""Avisos del servidor a Slack (#motor-trading). Sin SLACK_BOT_TOKEN no hace nada.

Nunca lanza ni bloquea la peticion: envia en un hilo con timeout corto y traga cualquier error.
`avisar(texto, clave=..., cada_s=...)`: con clave, no repite el mismo aviso antes de `cada_s` segundos
(evita inundar el canal si algo falla en bucle). No incluir correos ni nombres de alumnos en el texto.
Variables: SLACK_BOT_TOKEN (xoxb-..., EasyPanel), SLACK_CANAL_MOTOR (id del canal, opcional).
"""

import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

URL = "https://slack.com/api/chat.postMessage"
CANAL_MOTOR = "C0CAP9DTC83"  # #motor-trading
_ultimo: dict[str, float] = {}
_candado = threading.Lock()


def _toca(clave: str | None, cada_s: int, ahora: float) -> bool:
    if not clave or cada_s <= 0:
        return True
    with _candado:
        if ahora - _ultimo.get(clave, float("-inf")) < cada_s:
            return False
        _ultimo[clave] = ahora
        return True


def _enviar(token: str, canal: str, texto: str) -> None:
    try:
        req = urllib.request.Request(
            URL, data=json.dumps({"channel": canal, "text": texto}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            if not json.loads(r.read()).get("ok"):
                log.warning("slack_avisos: Slack rechazo el aviso")
    except Exception as e:  # nunca afecta al servidor
        log.warning("slack_avisos: no se pudo enviar (%s)", type(e).__name__)


def avisar(texto: str, clave: str | None = None, cada_s: int = 0, canal: str | None = None) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token or not texto:
        return
    if not _toca(clave, cada_s, time.monotonic()):
        return
    destino = canal or os.environ.get("SLACK_CANAL_MOTOR", "").strip() or CANAL_MOTOR
    threading.Thread(target=_enviar, args=(token, destino, texto[:500]), daemon=True).start()


def demo() -> None:
    global _enviar
    llamadas = []
    real, _enviar = _enviar, lambda t, c, x: llamadas.append((t, c, x))
    os.environ.pop("SLACK_BOT_TOKEN", None)
    try:
        avisar("sin token")
        assert not llamadas  # sin token no hace nada
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-prueba"
        avisar("uno")
        avisar("falla", clave="k", cada_s=60)
        avisar("falla otra vez", clave="k", cada_s=60)  # throttle: no sale
        avisar("otro canal", canal="C123")
        time.sleep(0.2)
        assert [(c, x) for _, c, x in llamadas] == [(CANAL_MOTOR, "uno"), (CANAL_MOTOR, "falla"), ("C123", "otro canal")], llamadas
        assert _toca("k", 60, time.monotonic() + 61)  # pasado el tiempo, vuelve a tocar
        assert _toca(None, 60, 0)  # sin clave nunca se limita
    finally:
        _enviar = real
        os.environ.pop("SLACK_BOT_TOKEN", None)
    print("slack_avisos.demo() OK")


if __name__ == "__main__":
    demo()
