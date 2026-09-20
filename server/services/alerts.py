"""Avisos por correo cuando algo va mal en producción.

Hasta ahora todo lo que iba mal terminaba en el log de Railway: el atasco de
salida a internet del 16/09/2026, una pasada que no llega a completarse, un
proveedor de estaciones caído. El log solo se mira cuando ya has notado el
fallo por otro camino —y, además, solo guarda el despliegue activo—, así que
un problema de madrugada se descubre por la mañana y sin rastro de cómo
empezó.

Este módulo es el único sitio que manda correo. Los vigilantes construyen un
``Alert`` y lo entregan aquí; aquí se decide si toca enviarlo, se envía y se
apunta que se envió.

Dos decisiones que importan:

- **Nunca propaga un fallo.** Un aviso es información sobre el servicio, no
  parte de él: si Resend no contesta, el worker tiene que seguir calculando y
  el backend sirviendo. Todo error se registra y se traga.
- **Antirrepetición por clave.** Un proveedor caído lo sigue estando en el
  ciclo siguiente, y un RUN atascado sigue atascado un minuto después. Sin
  freno, el primer fallo llenaría el buzón y el aviso dejaría de leerse. Cada
  aviso lleva una clave estable y no se repite hasta pasado el intervalo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ALERTS_STATE_KEY = "forecast/alerts/state.json"
PENDING_KEY = "forecast/alerts/pending.json"

# Avisos en espera y cuánto se guardan. El caso que justifica la cola es el
# atasco de salida: el correo que cuenta que no hay internet tampoco puede
# salir por internet. Se apunta, el proceso se reinicia y el vigilante lo
# manda cuando la red vuelve. Pasado un día ya no informa de nada útil.
MAX_PENDING = 10
PENDING_TTL_H = 24
RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT_S = 15.0

# Correo del responsable del servicio. Es el destinatario por defecto porque
# este backend tiene un solo operador; cualquier otro despliegue lo cambia con
# METEOLABX_ALERT_EMAIL_TO sin tocar código.
DEFAULT_RECIPIENT = "joan.llorente@protonmail.com"
DEFAULT_SENDER = "MeteoLabX <alertas@meteolabx.com>"

# Ventana por defecto antes de repetir el mismo aviso: seis horas. Es más que
# el ciclo del ranking y menos que el hueco entre pasadas AROME, así que un
# fallo persistente avisa cuatro veces al día como mucho.
DEFAULT_MIN_INTERVAL_S = 6 * 3600.0

# El asunto se lee en la lista del buzón, muchas veces desde el móvil y sin
# abrirlo: el emoji es el único dato que llega ahí sin esfuerzo. Verde todo en
# orden, amarillo hay algo que mirar sin prisa, rojo el servicio está dando
# mal servicio ahora mismo. Vive aquí para que los tres vigilantes no acaben
# usando símbolos distintos para lo mismo.
SEVERITY_EMOJI = {"info": "✅", "ok": "✅", "warn": "⚠️", "fail": "🔴"}


def emoji(severity: str) -> str:
    return SEVERITY_EMOJI.get(str(severity), "⚠️")


@dataclass(frozen=True)
class Alert:
    """Un aviso listo para enviar.

    ``key`` identifica el problema, no el instante: dos detecciones del mismo
    fallo comparten clave para que la segunda no vuelva a escribir. Incluye lo
    que distingue un episodio de otro —la pasada, el proveedor— y nada que
    cambie a cada comprobación.
    """

    key: str
    subject: str
    body: str
    severity: str = "warn"  # "info" | "warn" | "fail"
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S
    details: dict[str, Any] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _setting(name: str, default: str = "") -> str:
    """Lee la configuración sin arrastrar ``Settings`` hasta el worker.

    El worker corre como proceso aparte y no monta el backend; leer del
    entorno directamente evita importar medio ``server`` para tres cadenas.
    """
    return str(os.getenv(f"METEOLABX_{name}", default) or "").strip()


def recipient() -> str:
    return _setting("ALERT_EMAIL_TO", DEFAULT_RECIPIENT)


def _api_key() -> str:
    return _setting("RESEND_API_KEY")


def enabled() -> bool:
    """Si hay por dónde enviar.

    Sin clave de API no se envía nada, pero el aviso no se pierde: sale por el
    log con su severidad. Así el vigilante funciona igual en local y en los
    tests, donde no hay credenciales ni debe haberlas.
    """
    flag = _setting("ALERT_EMAIL_ENABLED").lower()
    if flag in {"0", "false", "no"}:
        return False
    return bool(_api_key() and recipient())


def _load_state(store: Any) -> dict[str, Any]:
    if store is None:
        return {}
    from server.services.forecast_store import read_json

    try:
        return read_json(store, ALERTS_STATE_KEY) or {}
    except Exception:
        logger.warning("alertas: no se pudo leer el estado de envíos", exc_info=True)
        return {}


def _save_state(store: Any, state: dict[str, Any]) -> None:
    if store is None:
        return
    from server.services.forecast_store import write_json

    try:
        write_json(store, ALERTS_STATE_KEY, state)
    except Exception:
        logger.warning("alertas: no se pudo guardar el estado de envíos", exc_info=True)


def _recently_sent(state: dict[str, Any], alert: Alert, now: datetime) -> bool:
    previous = _parse(str((state.get("sent") or {}).get(alert.key, "")))
    if previous is None:
        return False
    return now - previous < timedelta(seconds=max(0.0, alert.min_interval_s))


def _html(alert: Alert) -> str:
    """Cuerpo HTML mínimo.

    Nada de plantillas ni CSS elaborado: estos correos se leen en diagonal a
    las siete de la mañana y lo único que importa es que el asunto y las
    primeras líneas digan qué pasa. El texto plano va aparte y es el mismo.
    """
    lineas = [
        f"<p style='margin:0 0 12px'><strong>{_escape(alert.subject)}</strong></p>",
        "<pre style='font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;"
        "white-space:pre-wrap;margin:0'>",
        _escape(alert.body),
        "</pre>",
    ]
    return "".join(lineas)


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _post(alert: Alert) -> bool:
    payload = {
        "from": _setting("ALERT_EMAIL_FROM", DEFAULT_SENDER),
        "to": [recipient()],
        "subject": alert.subject,
        "text": alert.body,
        "html": _html(alert),
    }
    try:
        with httpx.Client(timeout=SEND_TIMEOUT_S) as client:
            response = client.post(
                RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {_api_key()}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("alertas: no se pudo enviar «%s» (%s)", alert.subject, exc)
        return False
    if response.status_code >= 300:
        logger.warning(
            "alertas: el servicio de correo rechazó «%s» (%d): %s",
            alert.subject, response.status_code, response.text[:300],
        )
        return False
    return True


def send(alert: Alert, *, store: Any = None, force: bool = False) -> bool:
    """Envía el aviso si toca. Devuelve si salió un correo.

    ``store`` es el almacén de predicción, que es el único sitio persistente
    compartido entre el worker y el backend: sin él la antirrepetición vive
    solo en memoria y un reinicio la olvida, que es justo lo que pasa cuando
    el contenedor se queda sin memoria y arranca de nuevo.
    """
    now = _now()
    state = _load_state(store)
    if not force and _recently_sent(state, alert, now):
        logger.debug("alertas: «%s» ya avisado hace poco; no se repite", alert.key)
        return False

    nivel = logging.ERROR if alert.severity == "fail" else logging.WARNING
    logger.log(nivel, "ALERTA %s · %s", alert.key, alert.subject)

    if not enabled():
        logger.info(
            "alertas: sin correo configurado (METEOLABX_RESEND_API_KEY); "
            "el aviso queda solo en el log"
        )
        return False

    if not _post(alert):
        # El envío falló, no el aviso: se guarda para mandarlo cuando se
        # pueda. Es lo único que salva al aviso de atasco de red, que se
        # detecta justo cuando el correo no puede salir.
        _queue(store, alert, now)
        return False

    # Solo se apunta lo que llegó a enviarse: si el correo falló, el próximo
    # ciclo tiene que volver a intentarlo en vez de dar el fallo por avisado.
    state.setdefault("sent", {})[alert.key] = _iso(now)
    state["last_sent_at"] = _iso(now)
    _save_state(store, state)
    return True


def _queue(store: Any, alert: Alert, now: datetime) -> None:
    if store is None:
        return
    pendientes = _pending(store)
    if any(item.get("key") == alert.key for item in pendientes):
        return
    pendientes.append({
        "key": alert.key,
        "subject": alert.subject,
        "body": alert.body,
        "severity": alert.severity,
        "queued_at": _iso(now),
    })
    _save_pending(store, pendientes[-MAX_PENDING:])


def _pending(store: Any) -> list[dict[str, Any]]:
    if store is None:
        return []
    from server.services.forecast_store import read_json

    try:
        return list((read_json(store, PENDING_KEY) or {}).get("alerts") or [])
    except Exception:
        return []


def _save_pending(store: Any, pendientes: list[dict[str, Any]]) -> None:
    from server.services.forecast_store import write_json

    try:
        write_json(store, PENDING_KEY, {"version": 1, "alerts": pendientes})
    except Exception:
        logger.warning("alertas: no se pudo guardar la cola de pendientes", exc_info=True)


def flush_pending(store: Any) -> int:
    """Intenta mandar los avisos que quedaron sin salir. Devuelve cuántos salieron.

    Lo llama el vigilante de salud en cada vuelta. Un aviso caducado se tira
    sin enviarlo: a las veinticuatro horas, un atasco de red que ya se resolvió
    solo sirve para confundir.
    """
    pendientes = _pending(store)
    if not pendientes:
        return 0
    if not enabled():
        return 0

    ahora = _now()
    quedan: list[dict[str, Any]] = []
    enviados = 0
    for item in pendientes:
        encolado = _parse(str(item.get("queued_at", "")))
        if encolado and ahora - encolado > timedelta(hours=PENDING_TTL_H):
            continue
        espera = ahora - encolado if encolado else None
        cuerpo = str(item.get("body", ""))
        if espera is not None:
            cuerpo += (
                f"\n\n(Este aviso se detectó hace {espera.total_seconds() / 60:.0f} min "
                "y no pudo enviarse entonces: el correo tampoco salía.)"
            )
        alerta = Alert(
            key=str(item.get("key", "pendiente")),
            subject=str(item.get("subject", "MeteoLabX")),
            body=cuerpo,
            severity=str(item.get("severity", "warn")),
        )
        if _post(alerta):
            enviados += 1
            state = _load_state(store)
            state.setdefault("sent", {})[alerta.key] = _iso(ahora)
            _save_state(store, state)
        else:
            quedan.append(item)
    _save_pending(store, quedan)
    return enviados
