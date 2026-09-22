"""Informe de una pasada: qué tardó, qué falló y si merece un correo.

El worker ya resumía la pasada en una línea de log (``_log_run_summary``),
pero esa línea se pierde con el despliegue y no dice nada de lo que no llegó
a calcularse. Aquí el manifiesto —que tiene los tiempos por nivel, los frames
publicados y los errores por producto y plazo— se convierte en un informe
guardado junto a la pasada, y se decide si hay algo que contar.

El criterio de «algo que contar» es deliberadamente conservador: una pasada
completa y a su hora no genera correo. Si todo generase correo, el correo que
importa llegaría entre otros tres iguales y no se leería.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from statistics import median
from typing import Any, Iterable, Sequence

from server.services.alerts import Alert, emoji

# Nombres de los niveles del worker, en el orden en que se calculan.
TIER_NAMES = {0: "nativos", 1: "derivados", 2: "convectivos", 3: "DCAPE"}

# Una pasada se da por buena con este porcentaje de frames publicados. No se
# exige el 100 %: Météo-France retira plazos sueltos de su catálogo con cierta
# frecuencia y un hueco aislado no es un fallo del servicio.
COMPLETE_ENOUGH_PERCENT = 98.0

# Errores tolerados sin avisar. Por debajo son reintentos que acabaron
# saliendo; por encima hay algo sistemático.
TOLERATED_ERRORS = 5

# Una pasada que tarda esto por encima de la mediana de las anteriores señala
# un problema aunque termine: memoria apretada, CPU compartida o descargas
# lentas. Es el aviso temprano del OOM que luego mata al worker.
SLOW_RATIO = 1.6

# Cuánto puede rozar el techo de memoria antes de que sea noticia. Por encima
# de esto, el contenedor todavía no ha matado a nadie pero le falta poco.
MEMORY_TIGHT_RATIO = 0.9

# Tarifas de Railway (captura de facturación del proyecto, 09/2026). Se leen
# del entorno para que una subida de precios no obligue a tocar código, y
# porque el mismo informe corre en local, donde no se factura nada.
#
# El egress no entra en el coste de una pasada a propósito: lo que el worker
# mueve son descargas —ingress—, que no se facturan. El egress de la factura
# lo genera el servicio web sirviendo la página, que es otro servicio.
PRICE_DEFAULTS = {
    "memory_gb_min": 0.000231,
    "cpu_vcpu_min": 0.000463,
    "volume_gb_min": 0.00000347,
    "egress_gb": 0.05,
}


def prices() -> dict[str, float]:
    tarifas = {}
    for nombre, defecto in PRICE_DEFAULTS.items():
        try:
            tarifas[nombre] = float(
                os.getenv(f"METEOLABX_PRICE_{nombre.upper()}", "") or defecto
            )
        except ValueError:
            tarifas[nombre] = defecto
    return tarifas


def _resources(manifest: dict[str, Any], duration_min: float | None) -> dict[str, Any]:
    """Memoria, CPU y descargas de la pasada, en unidades legibles."""
    uso = manifest.get("resource_usage") or {}
    muestras = int(uso.get("memory_samples", 0) or 0)
    gb = 1024 ** 3
    media = (int(uso.get("memory_sum_bytes", 0)) / muestras / gb) if muestras else None
    limite = int(uso.get("memory_limit_bytes", 0) or 0)
    pico = int(uso.get("memory_peak_bytes", 0) or 0)
    descargas = uso.get("downloads") or {}
    segundos_descarga = float(descargas.get("seconds", 0.0) or 0.0)
    # Tiempo de reloj con alguna descarga activa. Los manifiestos anteriores a
    # esta cuenta no lo traen: sin él no hay caudal real ni reparto honesto.
    reloj_descarga = float(descargas.get("wall_seconds", 0.0) or 0.0)
    bytes_descargados = int(descargas.get("bytes", 0) or 0)
    return {
        "memory_peak_gb": round(pico / gb, 2) if pico else None,
        "memory_mean_gb": round(media, 2) if media else None,
        "memory_limit_gb": round(limite / gb, 1) if limite else None,
        "memory_headroom": round(pico / limite, 2) if pico and limite else None,
        "memory_samples": muestras,
        "cpu_minutes": round(float(uso.get("cpu_seconds", 0.0)) / 60, 1),
        "downloads": {
            "packages": int(descargas.get("packages", 0) or 0),
            "gb": round(bytes_descargados / gb, 2),
            "minutes": round(segundos_descarga / 60, 1),
            "wall_minutes": round(reloj_descarga / 60, 1) if reloj_descarga else None,
            # Velocidad media de UNA descarga: lo que da cada conexión.
            "mb_s": round(bytes_descargados / 1e6 / segundos_descarga, 1)
            if segundos_descarga else None,
            # Lo que entró al contenedor por segundo de reloj, sumando las que
            # iban a la vez. Es la cifra que dice si hay banda de sobra.
            "throughput_mb_s": round(bytes_descargados / 1e6 / reloj_descarga, 1)
            if reloj_descarga else None,
            # Qué parte de la pasada hubo alguna descarga en marcha. Con la
            # suma de duraciones podía pasar del 100 %.
            "share_of_run": round(reloj_descarga / 60 / duration_min, 2)
            if duration_min and reloj_descarga else None,
        },
    }


def _cost(resources: dict[str, Any], duration_min: float | None) -> dict[str, Any]:
    """Coste aproximado de la pasada, con el desglose a la vista.

    Aproximado de verdad: la memoria se factura por lo que ocupa el contenedor
    entero, y ahí dentro también está la API sirviendo peticiones. Lo que se
    calcula es lo que costó tener el servicio funcionando mientras la pasada
    duraba, que es la cifra con la que se decide si un mapa nuevo sale a
    cuenta.
    """
    tarifas = prices()
    memoria = (resources.get("memory_mean_gb") or 0.0) * (duration_min or 0.0)
    cpu = resources.get("cpu_minutes") or 0.0
    coste_memoria = memoria * tarifas["memory_gb_min"]
    coste_cpu = cpu * tarifas["cpu_vcpu_min"]
    return {
        "memory_gb_min": round(memoria, 1),
        "memory_usd": round(coste_memoria, 4),
        "cpu_vcpu_min": round(cpu, 1),
        "cpu_usd": round(coste_cpu, 4),
        # Las descargas son ingress y no se facturan; se deja explícito para
        # no volver a optimizar el servicio equivocado.
        "egress_usd": 0.0,
        "total_usd": round(coste_memoria + coste_cpu, 4),
        "prices": tarifas,
    }


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def report_key(run_iso: str, *, model: str = "arome") -> str:
    from server.services.forecast_store import run_slug

    prefijo = "" if model == "arome" else f"{model}/"
    return f"forecast/{prefijo}reports/{run_slug(run_iso)}.json"


def _tier_segments(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """Tramos por nivel y duración total en minutos."""
    tiempos = manifest.get("tier_timing") or {}
    arranques = [
        momento
        for momento in (_parse(t.get("first_start")) for t in tiempos.values())
        if momento
    ]
    finales = [
        momento
        for momento in (_parse(t.get("last_start")) for t in tiempos.values())
        if momento
    ]
    if not arranques or not finales:
        return [], None

    inicio = min(arranques)
    # El último arranque no es el final real de la pasada; si el manifiesto
    # guarda cuándo terminó el último trabajo, ese dato es mejor.
    ultimo = _parse(((manifest.get("progress") or {}).get("last_completed") or {}).get("completed_at"))
    fin = max([*finales, ultimo] if ultimo else finales)

    tramos = []
    for clave in sorted(tiempos, key=lambda item: int(item)):
        tramo = tiempos[clave]
        desde = _parse(tramo.get("first_start"))
        # El final del último trabajo, si se apuntó; si no, su arranque.
        hasta = _parse(tramo.get("last_end")) or _parse(tramo.get("last_start"))
        if not desde or not hasta:
            continue
        tramos.append({
            "tier": int(clave),
            "name": TIER_NAMES.get(int(clave), str(clave)),
            "jobs": int(tramo.get("jobs", 0)),
            "start_min": round((desde - inicio).total_seconds() / 60, 1),
            "end_min": round((hasta - inicio).total_seconds() / 60, 1),
            **_occupancy(tramo, (hasta - desde).total_seconds()),
        })
    return tramos, round((fin - inicio).total_seconds() / 60, 1)


def _occupancy(tramo: dict[str, Any], span_s: float) -> dict[str, Any]:
    """Qué parte del tiempo del nivel estuvieron trabajando sus huecos.

    Segundos ocupados entre huecos × duración del nivel. Lo que falta hasta el
    100 % es tiempo parado: el vaciado al final de cada ciclo, la espera a un
    paquete sin publicar o el freno de memoria. Los niveles se solapan en los
    cambios, así que el reparto entre ellos es aproximado; el total no.
    """
    huecos = int(tramo.get("slots", 0) or 0)
    ocupado = float(tramo.get("busy_seconds", 0.0) or 0.0)
    if not huecos or not ocupado or span_s <= 0:
        return {}
    return {
        "slots": huecos,
        "busy_min": round(ocupado / 60, 1),
        "occupancy": round(min(1.0, ocupado / (huecos * span_s)), 2),
    }


def _overall_occupancy(manifest: dict[str, Any]) -> float | None:
    """Ocupación de la pasada entera, con los huecos del nivel más ancho."""
    tiempos = manifest.get("tier_timing") or {}
    huecos = max((int(t.get("slots", 0) or 0) for t in tiempos.values()), default=0)
    ocupado = sum(float(t.get("busy_seconds", 0.0) or 0.0) for t in tiempos.values())
    inicios = [m for m in (_parse(t.get("first_start")) for t in tiempos.values()) if m]
    finales = [m for m in (_parse(t.get("last_end")) for t in tiempos.values()) if m]
    if not huecos or not ocupado or not inicios or not finales:
        return None
    span = (max(finales) - min(inicios)).total_seconds()
    if span <= 0:
        return None
    return round(min(1.0, ocupado / (huecos * span)), 2)


def _errors_by_product(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Errores agrupados por producto, con el mensaje más repetido.

    Un plazo que falla suele fallar en todos sus productos por la misma causa;
    listar los cientos de pares producto/hora no informa más que decir qué
    producto sufre y por qué.
    """
    agrupados = []
    for product, state in sorted((manifest.get("products") or {}).items()):
        errores = (state or {}).get("errors") or {}
        if not errores:
            continue
        mensajes: dict[str, int] = {}
        for mensaje in errores.values():
            corto = str(mensaje).strip().splitlines()[0][:160]
            mensajes[corto] = mensajes.get(corto, 0) + 1
        frecuente = max(mensajes.items(), key=lambda item: item[1])
        agrupados.append({
            "product": product,
            "count": len(errores),
            "message": frecuente[0],
            "hours": sorted(errores)[:3],
        })
    return sorted(agrupados, key=lambda item: item["count"], reverse=True)


def _missing_products(manifest: dict[str, Any]) -> list[str]:
    """Productos sin un solo frame publicado: el fallo que más se nota."""
    vacios = []
    for product, state in sorted((manifest.get("products") or {}).items()):
        if not ((state or {}).get("available_times")):
            vacios.append(product)
    return vacios


def previous_durations(manifests: Iterable[dict[str, Any]], *, run_iso: str) -> list[float]:
    duraciones = []
    for manifest in manifests:
        if str(manifest.get("run")) == run_iso:
            continue
        _, duracion = _tier_segments(manifest)
        if duracion:
            duraciones.append(duracion)
    return duraciones


def build_report(
    manifest: dict[str, Any],
    *,
    previous: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Informe de una pasada, con veredicto.

    ``previous`` son las pasadas conservadas en el volumen (hasta tres útiles,
    las de los otros turnos). Sirven para la única comparación que se puede
    hacer sin guardar histórico aparte: si esta pasada tardó mucho más que las
    suyas recientes.
    """
    run_iso = str(manifest.get("run", ""))
    progress = manifest.get("progress") or {}
    tramos, duracion = _tier_segments(manifest)
    errores = _errors_by_product(manifest)
    vacios = _missing_products(manifest)
    total_errores = sum(item["count"] for item in errores)
    percent = float(progress.get("percent", 0.0) or 0.0)
    status = str(manifest.get("status", "publishing"))
    duraciones_previas = previous_durations(previous, run_iso=run_iso)
    mediana = round(median(duraciones_previas), 1) if duraciones_previas else None
    recursos = _resources(manifest, duracion)
    coste = _cost(recursos, duracion)
    fallos = dict(manifest.get("failure_kinds") or {})

    problemas: list[str] = []
    severidad = "ok"
    # Una pasada con todos sus frames y sin un solo error está terminada,
    # diga lo que diga la etiqueta. El estado lo fija el worker y ha llegado a
    # quedarse en «publicando» por su cuenta: avisar entonces de que «no
    # terminó» manda a mirar una pasada que está entera, que es la forma más
    # rápida de que dejen de leerse estos correos.
    terminada = status == "complete" or (percent >= 99.95 and total_errores == 0)
    if not terminada:
        problemas.append(
            f"La pasada no llegó a completarse: sigue en «{status}» con {percent:.1f} % publicado."
        )
        severidad = "fail"
    if vacios:
        problemas.append(
            "Sin ningún frame publicado: " + ", ".join(vacios[:6])
            + (f" (y {len(vacios) - 6} más)" if len(vacios) > 6 else "")
        )
        severidad = "fail"
    if percent < COMPLETE_ENOUGH_PERCENT and terminada:
        problemas.append(f"Publicado solo el {percent:.1f} % de los frames esperados.")
        severidad = "fail" if severidad == "fail" else "warn"
    if total_errores > TOLERATED_ERRORS:
        problemas.append(
            f"{total_errores} errores de cálculo, el grueso en «{errores[0]['product']}»: "
            f"{errores[0]['message']}"
        )
        severidad = "fail" if severidad == "fail" else "warn"
    if fallos.get("killed"):
        problemas.append(
            f"{fallos['killed']} trabajos murieron sin devolver resultado: es el "
            "contenedor matándolos por memoria, no un error de cálculo."
        )
        severidad = "fail"
    if recursos.get("memory_headroom") and recursos["memory_headroom"] >= MEMORY_TIGHT_RATIO:
        problemas.append(
            f"La memoria llegó a {recursos['memory_peak_gb']} GB de "
            f"{recursos['memory_limit_gb']} ({recursos['memory_headroom'] * 100:.0f} % del techo)."
        )
        severidad = "fail" if severidad == "fail" else "warn"
    if duracion and mediana and duracion > mediana * SLOW_RATIO:
        problemas.append(
            f"Tardó {duracion:.0f} min frente a los {mediana:.0f} habituales "
            f"({duracion / mediana:.1f}×): conviene mirar la memoria del contenedor."
        )
        severidad = "fail" if severidad == "fail" else "warn"

    return {
        "version": 1,
        "run": run_iso,
        "model": manifest.get("forecast_model", "arome"),
        "model_label": manifest.get("model", ""),
        "status": status,
        "scope": manifest.get("calculation_scope", "model"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_min": duracion,
        "previous_median_min": mediana,
        "frames_available": int(progress.get("frames_available", 0) or 0),
        "frames_total": int(progress.get("frames_total", 0) or 0),
        "percent": round(percent, 1),
        "error_count": total_errores,
        "tiers": tramos,
        "occupancy": _overall_occupancy(manifest),
        "resources": recursos,
        "failures": fallos,
        "cost": coste,
        "errors": errores,
        "empty_products": vacios,
        "issues": problemas,
        "severity": severidad,
    }


def render_text(report: dict[str, Any]) -> str:
    """El informe en texto plano, que es también el cuerpo del correo."""
    run = report.get("run", "?")
    lineas = [f"Pasada {run} · {report.get('model_label') or report.get('model')}"]

    duracion = report.get("duration_min")
    mediana = report.get("previous_median_min")
    if duracion:
        comparacion = f" (habitual: {mediana:.0f} min)" if mediana else ""
        lineas.append(f"Duración: {duracion:.0f} min{comparacion}")
    lineas.append(
        f"Frames: {report.get('frames_available', 0)}/{report.get('frames_total', 0)}"
        f" · {report.get('percent', 0)} % · estado «{report.get('status')}»"
    )

    if report.get("issues"):
        lineas.append("")
        lineas.append("Qué va mal:")
        lineas.extend(f"  · {problema}" for problema in report["issues"])

    if report.get("tiers"):
        lineas.append("")
        titulo = "Reparto por nivel"
        if report.get("occupancy") is not None:
            titulo += f" (ocupación total {report['occupancy'] * 100:.0f} %)"
        lineas.append(titulo + ":")
        for tramo in report["tiers"]:
            ocupacion = (
                f" · ocupación {tramo['occupancy'] * 100:.0f} % de {tramo['slots']} huecos"
                if tramo.get("occupancy") is not None else ""
            )
            lineas.append(
                f"  · {tramo['name']:<12} {tramo['jobs']:>4} trabajos "
                f"{tramo['start_min']:.0f}-{tramo['end_min']:.0f} min" + ocupacion
            )

    recursos = report.get("resources") or {}
    coste = report.get("cost") or {}
    if recursos.get("memory_mean_gb") or recursos.get("cpu_minutes"):
        lineas.append("")
        lineas.append("Recursos:")
        if recursos.get("memory_peak_gb"):
            techo = (
                f" de {recursos['memory_limit_gb']} GB"
                if recursos.get("memory_limit_gb") else ""
            )
            lineas.append(
                f"  · {'memoria':<10} pico {recursos['memory_peak_gb']} GB{techo}"
                f" · media {recursos.get('memory_mean_gb')} GB"
            )
        if recursos.get("cpu_minutes"):
            lineas.append(f"  · {'CPU':<10} {recursos['cpu_minutes']:.0f} vCPU-min")
        descargas = recursos.get("downloads") or {}
        if descargas.get("packages"):
            reparto = (
                f" ({descargas['share_of_run'] * 100:.0f} % de la pasada)"
                if descargas.get("share_of_run") else ""
            )
            if descargas.get("wall_minutes"):
                # Minutos de reloj y caudal real; la velocidad por descarga,
                # aparte, porque la suma de duraciones no es tiempo de reloj.
                lineas.append(
                    f"  · {'GRIB':<10} {descargas['packages']} paquetes · "
                    f"{descargas['gb']} GB en {descargas['wall_minutes']:.0f} min de reloj"
                    + (f" a {descargas['throughput_mb_s']} MB/s"
                       if descargas.get("throughput_mb_s") else "")
                    + reparto
                )
                if descargas.get("mb_s"):
                    lineas.append(
                        f"  · {'':<10} {descargas['mb_s']} MB/s por descarga"
                    )
            else:
                lineas.append(
                    f"  · {'GRIB':<10} {descargas['packages']} paquetes · "
                    f"{descargas['gb']} GB en {descargas['minutes']:.0f} min"
                    + (f" a {descargas['mb_s']} MB/s por descarga"
                       if descargas.get("mb_s") else "")
                )

    if report.get("failures"):
        reparto = {
            "killed": "matados por memoria", "timeout": "agotaron su tiempo",
            "provider": "rechazados por el proveedor", "other": "otros",
        }
        detalle = " · ".join(
            f"{reparto.get(clase, clase)} {cuantos}"
            for clase, cuantos in sorted(report["failures"].items(),
                                         key=lambda item: item[1], reverse=True)
        )
        lineas.append(f"  · {'caídos':<10} {detalle}")

    if coste.get("total_usd") is not None:
        lineas.append("")
        lineas.append(
            f"Coste aproximado: ${coste['total_usd']:.2f}"
            f" (memoria ${coste['memory_usd']:.2f} · CPU ${coste['cpu_usd']:.2f};"
            " las descargas son ingress y no se facturan)"
        )

    if report.get("errors"):
        lineas.append("")
        lineas.append("Errores por producto:")
        for error in report["errors"][:8]:
            lineas.append(f"  · {error['product']} ({error['count']}): {error['message']}")

    return "\n".join(lineas)


def email_level() -> str:
    """Qué pasadas escriben correo: ``problems`` (por defecto) o ``all``.

    El informe se guarda en las dos: lo que cambia es si además llega al
    buzón. Empezar en ``problems`` evita acostumbrarse a borrar cuatro correos
    diarios sin leerlos, que es como se pierde el que sí importaba; ``all``
    tiene sentido las primeras semanas, para ver qué es normal en este
    servicio antes de fiarse de los umbrales.
    """
    valor = str(os.getenv("METEOLABX_ALERT_EMAIL_LEVEL", "problems") or "").strip().lower()
    return "all" if valor in {"all", "todo", "siempre"} else "problems"


def alert_for_report(report: dict[str, Any]) -> Alert | None:
    """El aviso que merece esta pasada, o ninguno si salió bien y no toca."""
    run = str(report.get("run", "?"))
    if report.get("severity") == "ok":
        if email_level() != "all":
            return None
        duracion = report.get("duration_min")
        return Alert(
            key=f"forecast/run-report/{run}",
            subject=(
                f"{emoji('ok')} MeteoLabX · pasada {run[:13]}Z completa"
                + (f" en {duracion:.0f} min" if duracion else "")
            ),
            body=render_text(report),
            severity="info",
            details={"run": run},
        )
    gravedad = report.get("severity", "warn")
    titular = report["issues"][0] if report.get("issues") else "revisar la pasada"
    return Alert(
        # La clave lleva la pasada: cada pasada avisa como mucho una vez, y un
        # problema que se repite turno tras turno sí vuelve a escribir, que es
        # lo que distingue un tropiezo de una avería.
        key=f"forecast/run-report/{run}",
        subject=f"{emoji(gravedad)} MeteoLabX · pasada {run[:13]}Z: {titular[:80]}",
        body=render_text(report),
        severity=gravedad,
        details={"run": run},
    )


def save_report(store: Any, report: dict[str, Any]) -> None:
    from server.services.forecast_store import write_json

    write_json(
        store,
        report_key(str(report["run"]), model=str(report.get("model", "arome"))),
        report,
    )
