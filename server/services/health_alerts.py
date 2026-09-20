"""Vigilante de salud del servicio: detecta el fallo y avisa.

Cubre los tres fallos que ya nos han pasado y que nadie descubrió a tiempo:

1. **El worker se queda sin memoria.** El contenedor lo mata (-9) y el
   manifiesto de la pasada se congela a medias. El propio worker no puede
   avisar de su muerte, así que quien vigila es el backend: vive en el mismo
   contenedor, lee el mismo volumen y sobrevive al OOM del otro proceso.
2. **El backend deja de salir a internet.** Pasó el 16/09/2026: ``/v1/health``
   seguía contestando —el healthcheck de Railway no reinicia nada— mientras
   las estaciones no cargaban. ``egress_watchdog`` ya lo detecta y reinicia;
   lo que faltaba era que quedase constancia en algún sitio que se mire.
3. **Los proveedores de estaciones dejan de contestar.** El ranking reintenta
   con backoff y sigue su vida, de modo que un proveedor caído durante horas
   solo se nota mirando el mapa.

Un cuarto caso, el resumen diario, no es un fallo: es la prueba de que el
vigilante sigue vivo. Sin él, el silencio es ambiguo —todo va bien o el
vigilante también se cayó—.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Sequence

from server.services.alerts import Alert, emoji, flush_pending, send
from server.services.run_report import build_report, render_text

logger = logging.getLogger(__name__)

DIGEST_STATE_KEY = "forecast/alerts/digest.json"

# Minutos sin que el manifiesto se mueva antes de dar la pasada por atascada.
# Un trabajo de DCAPE puede tardar bastantes minutos y el heartbeat solo se
# escribe al empezar cada trabajo, así que el margen es amplio: lo que se
# busca es el proceso muerto, no el proceso lento.
DEFAULT_STALL_MINUTES = 45.0

# Proveedores caídos a la vez que delatan un problema propio y no ajeno. Que
# falle AEMET es de todos los días; que fallen cinco a la vez es que el que
# no sale a internet somos nosotros.
TRANSVERSAL_FAILURES = 4

# Rachas seguidas antes de avisar de un proveedor concreto. Con la cadencia
# horaria del ranking y su backoff, seis rachas son varias horas caído.
PROVIDER_STREAK = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(f"METEOLABX_{name}", "") or default)
    except ValueError:
        return default


def stall_minutes() -> float:
    return _float_env("RUN_STALL_MINUTES", DEFAULT_STALL_MINUTES)


def digest_hour() -> int:
    """Hora UTC del resumen diario. Negativa → sin resumen."""
    try:
        return int(os.getenv("METEOLABX_ALERT_DIGEST_HOUR_UTC", "6"))
    except ValueError:
        return 6


# --- 1. Pasadas atascadas -------------------------------------------------

def stalled_alert(manifest: dict[str, Any], *, now: datetime | None = None) -> Alert | None:
    """Aviso si la pasada lleva demasiado tiempo sin avanzar."""
    if not manifest or str(manifest.get("status")) == "complete":
        return None
    momento = now or _now()
    latido = _parse(manifest.get("worker_heartbeat_at")) or _parse(manifest.get("updated_at"))
    if latido is None:
        return None
    parado = (momento - latido).total_seconds() / 60
    if parado < stall_minutes():
        return None

    run = str(manifest.get("run", "?"))
    progress = manifest.get("progress") or {}
    activos = progress.get("active_jobs") or []
    detalle = ", ".join(
        f"{job.get('product', '?')} {str(job.get('valid_time', ''))[11:16]}"
        for job in activos[:4]
    )
    cuerpo = [
        f"La pasada {run} lleva {parado:.0f} min sin avanzar.",
        f"Publicado: {progress.get('frames_available', 0)}/{progress.get('frames_total', 0)}"
        f" frames ({progress.get('percent', 0)} %).",
        f"Último latido del worker: {latido.isoformat()}.",
    ]
    if detalle:
        cuerpo.append(f"Trabajos que quedaron abiertos: {detalle}.")
    cuerpo.append("")
    cuerpo.append(
        "La causa más probable es que el contenedor matara al worker por memoria: "
        "el proceso muere sin escribir nada y el manifiesto se queda como estaba. "
        "Railway reinicia el servicio y la pasada se retoma sola; si se repite turno "
        "tras turno, hay que bajar METEOLABX_FORECAST_WORKERS o subir la memoria."
    )
    return Alert(
        key=f"forecast/run-stalled/{run}",
        subject=f"{emoji('fail')} MeteoLabX · pasada {run[:13]}Z parada desde hace {parado:.0f} min",
        body="\n".join(cuerpo),
        severity="fail",
        details={"run": run, "stalled_minutes": round(parado, 1)},
    )


# --- 2. Salida a internet atascada ---------------------------------------

def egress_alert(*, restarting: bool, pool: dict[str, Any] | None = None) -> Alert:
    estado = f" · pool: {pool}" if pool else ""
    if restarting:
        cuerpo = (
            "El cliente HTTP compartido del backend ha dejado de salir a internet "
            "mientras una conexión independiente sí llega: el proceso está atascado.\n"
            "Se ha mandado reiniciar el servicio, así que las estaciones deberían "
            "volver en un par de minutos sin que tengas que hacer nada.\n\n"
            "Es el mismo cuadro del 16/09/2026: /v1/health seguía contestando —por eso "
            "Railway no reiniciaba solo— mientras el mapa y las fichas quedaban vacíos. "
            "Si se repite a menudo, el disparador suele ser una avalancha de rastreadores."
            f"{estado}"
        )
        return Alert(
            key="backend/egress-stuck",
            subject=f"{emoji('fail')} MeteoLabX · el backend no salía a internet; reiniciando",
            body=cuerpo,
            severity="fail",
            # Corto: si el atasco vuelve en la hora siguiente, eso es una avería
            # distinta de un episodio aislado y hay que verlo.
            min_interval_s=3600.0,
        )
    return Alert(
        key="backend/egress-network",
        subject=f"{emoji('warn')} MeteoLabX · sin salida a internet (parece corte de red)",
        body=(
            "Ni el cliente compartido ni una conexión independiente llegan a internet. "
            "No se reinicia nada: con la red caída, reiniciar no arregla nada y solo "
            "vaciaría las cachés.\n"
            "Las observaciones en vivo estarán sin actualizar mientras dure."
            f"{estado}"
        ),
        severity="warn",
    )


# --- 3. Proveedores de estaciones ----------------------------------------

def provider_alert(
    failure_counts: dict[str, int],
    *,
    attempted: Sequence[str] = (),
) -> Alert | None:
    """Aviso si los proveedores del ranking dejan de contestar.

    Distingue dos cuadros que piden reacciones distintas: varios proveedores
    caídos a la vez apunta a nuestra red, y uno solo con racha larga apunta a
    ese proveedor.
    """
    caidos = {name: streak for name, streak in failure_counts.items() if streak >= 2}
    if len(caidos) >= TRANSVERSAL_FAILURES:
        nombres = ", ".join(sorted(caidos)[:10])
        return Alert(
            key="stations/providers-down",
            subject=f"{emoji('fail')} MeteoLabX · {len(caidos)} proveedores de estaciones sin contestar",
            body=(
                f"No contestan {len(caidos)} de {len(attempted) or '?'} proveedores: {nombres}.\n\n"
                "Que fallen varios a la vez rara vez es cosa suya: lo habitual es que el "
                "problema esté de nuestro lado —salida a internet atascada, DNS o pool de "
                "conexiones agotado—. El mapa y el ranking se estarán sirviendo con datos "
                "viejos o vacíos.\n"
                "Si además llega un aviso de salida a internet, es el mismo fallo visto "
                "desde dos sitios."
            ),
            severity="fail",
            min_interval_s=3600.0,
        )

    largos = {name: streak for name, streak in failure_counts.items() if streak >= PROVIDER_STREAK}
    if largos:
        detalle = ", ".join(f"{name} ({streak} rachas)" for name, streak in sorted(largos.items()))
        return Alert(
            key="stations/provider-streak/" + ",".join(sorted(largos)),
            subject=f"{emoji('warn')} MeteoLabX · proveedor caído: {sorted(largos)[0]}",
            body=(
                f"Llevan fallando sin parar: {detalle}.\n\n"
                "El ranking sigue funcionando con el resto y reintenta con backoff, así que "
                "no hay nada urgente que hacer: sus estaciones aparecerán con datos viejos "
                "hasta que el proveedor vuelva."
            ),
            severity="warn",
        )
    return None


def check_providers(failure_counts: dict[str, int], attempted: Sequence[str] = ()) -> None:
    """Punto de entrada desde el ranking. No propaga fallos ni bloquea."""
    try:
        aviso = provider_alert(failure_counts, attempted=attempted)
        if aviso is not None:
            send(aviso, store=_store())
    except Exception:
        logger.warning("alertas: falló la revisión de proveedores", exc_info=True)


# --- 4. Resumen diario ----------------------------------------------------

def digest_alert(manifests: Sequence[dict[str, Any]], *, now: datetime | None = None) -> Alert:
    momento = now or _now()
    informes = [build_report(manifest, previous=manifests) for manifest in manifests]
    malas = [informe for informe in informes if informe["severity"] != "ok"]
    cuerpo = [
        f"Resumen de las últimas pasadas · {momento.strftime('%d/%m/%Y %H:%M')} UTC",
        "",
    ]
    for informe in informes:
        marca = emoji(informe["severity"])
        duracion = f"{informe['duration_min']:.0f} min" if informe.get("duration_min") else "—"
        cuerpo.append(
            f"{marca} {informe['run'][:13]}Z · {duracion} · {informe['percent']} % · "
            f"{informe['error_count']} errores"
        )
    if malas:
        cuerpo.append("")
        cuerpo.append("Detalle de las que no salieron limpias:")
        for informe in malas:
            cuerpo.append("")
            cuerpo.append(render_text(informe))
    peor = "fail" if any(i["severity"] == "fail" for i in informes) else (
        "warn" if malas else "ok"
    )
    return Alert(
        key=f"digest/{momento.strftime('%Y-%m-%d')}",
        subject=(
            f"{emoji(peor)} MeteoLabX · resumen diario: "
            f"{len(informes) - len(malas)}/{len(informes)} pasadas limpias"
        ),
        body="\n".join(cuerpo),
        severity="info",
        # Una vez al día y punto; la clave ya lleva la fecha.
        min_interval_s=12 * 3600.0,
    )


# --- Bucle ----------------------------------------------------------------

def _store() -> Any:
    from server.services.forecast_store import get_forecast_store

    return get_forecast_store()


def _tick() -> None:
    """Una revisión completa. Síncrona: la llama el bucle en otro hilo."""
    from server.services.forecast_store import (
        LATEST_MANIFEST_KEY,
        read_json,
        retained_manifests,
    )

    store = _store()
    # Lo primero, los avisos que no pudieron salir en su momento: el aviso de
    # «no salgo a internet» se detecta justo cuando el correo no sale, y el
    # reinicio que lo arregla es el que le devuelve la voz.
    flush_pending(store)
    manifest = read_json(store, LATEST_MANIFEST_KEY) or {}

    aviso = stalled_alert(manifest)
    if aviso is not None:
        send(aviso, store=store)

    hora = digest_hour()
    if hora < 0:
        return
    ahora = _now()
    if ahora.hour != hora:
        return
    # El resumen se manda una vez al día aunque el bucle pase muchas veces
    # dentro de esa hora; la marca vive en el volumen para que un reinicio no
    # lo repita.
    estado = read_json(store, DIGEST_STATE_KEY) or {}
    if str(estado.get("last_date")) == ahora.strftime("%Y-%m-%d"):
        return
    manifiestos = retained_manifests(store)
    if not manifiestos:
        return
    if send(digest_alert(manifiestos, now=ahora), store=store):
        from server.services.forecast_store import write_json

        write_json(store, DIGEST_STATE_KEY, {"last_date": ahora.strftime("%Y-%m-%d")})


async def health_loop(*, interval_s: float = 300.0) -> None:
    """Revisión periódica. Se cancela con el lifespan del backend."""
    while True:
        await asyncio.sleep(max(60.0, interval_s))
        try:
            # En otro hilo: leer el manifiesto es E/S de disco o de S3 y no
            # debe congelar las peticiones web mientras dura.
            await asyncio.to_thread(_tick)
        except Exception:
            logger.warning("alertas: falló la revisión de salud", exc_info=True)
