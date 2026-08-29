#!/usr/bin/env python3
"""
Guarda mapas AROME reales en el almacén local para desarrollar sin descargas.

Copia frames ya calculados de una instancia en marcha —producción por
defecto— al mismo almacén que usa el servidor local, con la disposición de
claves que espera ``forecast_store``. Con la foto guardada, el visor sirve los
mapas desde disco: no hace falta la clave de Météo-France, no se baja ni un
GRIB y una hora se abre en milisegundos en vez de en minutos.

No guarda credenciales: solo se piden endpoints públicos de lectura.

    python scripts/capture_forecast_fixtures.py --list
    python scripts/capture_forecast_fixtures.py --hours 4
    python scripts/capture_forecast_fixtures.py --products updraft-helicity,vv-lfc

Después, el servidor local con la foto y sin clave:

    METEOLABX_FORECAST_PRECOMPUTED_ONLY=true \\
    METEOLABX_FORECAST_CALCULATION_SCOPE=model \\
    ./scripts/run_server.sh
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
from pathlib import Path
import sys

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.services.forecast_store import (  # noqa: E402
    LATEST_MANIFEST_KEY,
    LocalObjectStore,
    frame_key,
    grid_metadata,
    mark_available,
    new_manifest,
    read_json,
    register_run_slot,
    run_manifest_key,
    write_grid,
    write_json,
)


DEFAULT_SOURCE = "https://www.meteolabx.com"
DEFAULT_STORE = "data/forecast_store"
# Un producto por familia de unidades y los dos mapas convectivos nuevos: es
# lo que se toca al trabajar en el visor sin tener que bajar el RUN entero.
DEFAULT_PRODUCTS = (
    "temperature-2m",
    "wind-level",
    "wind-gust",
    "precip-1h",
    "mucape-muli",
    "updraft-helicity",
    "vv-lfc",
)


class CaptureError(RuntimeError):
    """Fallo esperable: fuente inaccesible, RUN inexistente o producto vacío."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_catalog(session: requests.Session, source: str, timeout: float) -> dict:
    response = session.get(f"{source}/v1/forecast/arome/catalog", timeout=timeout)
    if response.status_code != 200:
        raise CaptureError(
            f"El catálogo de {source} responde {response.status_code}."
        )
    return response.json()


def pick_run(catalog: dict, run_iso: str) -> dict:
    """RUN pedido, o el más reciente que tenga frames publicados."""
    runs = catalog.get("runs") or []
    if not runs:
        raise CaptureError("El catálogo no anuncia ninguna pasada.")
    if run_iso:
        for entry in runs:
            if entry.get("run") == run_iso:
                return entry
        disponibles = ", ".join(str(entry.get("run")) for entry in runs)
        raise CaptureError(f"La pasada {run_iso} no está; hay: {disponibles}.")
    for entry in runs:
        if any(item.get("available_times") for item in (entry.get("products") or {}).values()):
            return entry
    raise CaptureError("Ninguna pasada tiene todavía frames publicados.")


def hours_of(product_catalog: dict, limit: int) -> list[str]:
    """Primeras horas realmente disponibles del producto."""
    expected = list(product_catalog.get("valid_times") or [])
    available = set(product_catalog.get("available_times") or [])
    listo = [value for value in expected if value in available]
    return listo[:limit] if limit > 0 else listo


def fetch_frame(
    session: requests.Session,
    source: str,
    *,
    run_iso: str,
    product: str,
    valid_time: str,
    vertical_kind: str,
    level: float,
    timeout: float,
) -> bytes:
    """Rejilla descomprimida, tal como la guarda el worker."""
    params = {
        "product": product,
        "valid_time": valid_time,
        "run": run_iso,
        "revision": "fixtures",
    }
    if product == "wind-level":
        params["vertical_kind"] = vertical_kind
        params["level"] = level
    response = session.get(
        f"{source}/v1/forecast/arome/frames.grid",
        params=params,
        timeout=timeout,
        headers={"Accept": "application/vnd.meteolabx.arome-grid"},
    )
    if response.status_code == 425:
        raise CaptureError("la hora todavía no está publicada")
    if response.status_code != 200:
        raise CaptureError(f"HTTP {response.status_code}")
    # `requests` deshace el Content-Encoding, pero el endpoint puede servir el
    # gzip como cuerpo opaco: se comprueba la firma antes de dar por buena la
    # rejilla, que es lo que `grid_metadata` sabe leer.
    content = response.content
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content


def stored_levels(entry: dict) -> dict[str, list[float]]:
    """Niveles del viento que anuncia una entrada del catálogo."""
    levels = entry.get("levels") or {}
    return {
        "height": [float(value) for value in (levels.get("height") or [])],
        "isobaric": [float(value) for value in (levels.get("isobaric") or [])],
    }


def hours_on_disk(
    store: LocalObjectStore,
    run_iso: str,
    product: str,
    hours: list[str],
    *,
    scope: str,
    levels: dict[str, list[float]],
) -> list[str]:
    """Filtra unas horas a las que de verdad tienen su rejilla guardada."""
    if product == "wind-level":
        combinaciones = [
            (kind, value) for kind, values in levels.items() for value in values
        ] or [("height", 10.0)]
    else:
        combinaciones = [(None, None)]
    presentes = []
    for valid_time in hours:
        for kind, value in combinaciones:
            key = frame_key(
                run_iso, product, valid_time,
                scope=scope, vertical_kind=kind, level=value,
            )
            if store.exists(key):
                presentes.append(valid_time)
                break
    return presentes


def build_manifest(
    store: LocalObjectStore,
    run_entry: dict,
    captured: dict[str, list[str]],
    *,
    scope: str,
    vertical_kind: str,
    level: float,
    reset: bool = False,
) -> dict:
    """Manifiesto de la foto: solo anuncia las horas que están en disco.

    El visor lee las horas del manifiesto, así que recortarlo evita el caso
    más molesto de una foto parcial: un deslizador lleno de plazos que al
    pulsarlos responden 425.

    Se suma a lo que ya hubiera guardado de la misma pasada en vez de
    sustituirlo: bajar dos horas más de un producto no puede dejar sin índice a
    los otros seis que ya estaban en disco. Lo anterior se contrasta contra el
    almacén, así que borrar ficheros a mano también los retira del manifiesto.
    """
    run_iso = str(run_entry.get("run"))
    anterior = None if reset else read_json(store, run_manifest_key(run_iso))
    if anterior and str(anterior.get("run")) != run_iso:
        anterior = None
    previos: dict[str, list[str]] = {}
    niveles: dict[str, dict[str, list[float]]] = {}
    for product, state in ((anterior or {}).get("products") or {}).items():
        horas = list(state.get("available_times") or [])
        if not horas:
            continue
        entry_previa = ((anterior or {}).get("catalog_products") or {}).get(product) or {}
        niveles[product] = stored_levels(entry_previa)
        en_disco = hours_on_disk(
            store, run_iso, product, horas,
            scope=str(anterior.get("calculation_scope") or scope),
            levels=niveles[product],
        )
        if en_disco:
            previos[product] = en_disco

    combinado: dict[str, list[str]] = {
        product: sorted(set(horas)) for product, horas in previos.items()
    }
    for product, horas in captured.items():
        combinado[product] = sorted(set(combinado.get(product, [])) | set(horas))
        if product == "wind-level":
            acumulados = niveles.get(product) or {"height": [], "isobaric": []}
            acumulados[vertical_kind] = sorted(set(acumulados[vertical_kind]) | {level})
            niveles[product] = acumulados

    catalog_products: dict[str, dict] = {}
    for product, times in combinado.items():
        entry = deepcopy((run_entry.get("products") or {}).get(product) or {})
        if not entry:
            entry = deepcopy(((anterior or {}).get("catalog_products") or {}).get(product) or {})
        entry["run"] = run_iso
        entry["valid_times"] = list(times)
        entry.pop("available_times", None)
        entry.pop("available_until", None)
        entry.pop("publishing", None)
        if product == "wind-level":
            # Solo se han bajado unos niveles: ofrecer los demás sería ofrecer 425.
            entry["levels"] = niveles.get(product) or {"height": [level], "isobaric": []}
        catalog_products[product] = entry

    captured = combinado
    todas = sorted({value for times in captured.values() for value in times})
    manifest = new_manifest(
        run_iso,
        todas,
        scope=scope,
        catalog_products=catalog_products,
    )
    for product, times in captured.items():
        for value in times:
            mark_available(manifest, product, value)
    total = sum(len(times) for times in captured.values())
    manifest["status"] = "complete"
    manifest["updated_at"] = _now()
    manifest["worker_heartbeat_at"] = _now()
    manifest["progress"] = {
        "frames_available": total,
        "frames_total": total,
        "percent": 100.0,
        "error_count": 0,
        "current_job": None,
        "active_jobs": [],
        "last_completed": None,
    }
    return manifest


def describe(catalog: dict) -> str:
    lineas = []
    for entry in catalog.get("runs") or []:
        productos = entry.get("products") or {}
        listos = {
            product: len(meta.get("available_times") or [])
            for product, meta in productos.items()
        }
        con_datos = {product: total for product, total in listos.items() if total}
        lineas.append(
            f"{entry.get('run')} · {entry.get('status')} · "
            f"{len(con_datos)}/{len(productos)} productos con horas publicadas"
        )
        for product, total in sorted(con_datos.items()):
            lineas.append(f"    {product:<22} {total} h")
    return "\n".join(lineas) or "El catálogo no anuncia ninguna pasada."


def capture(arguments: argparse.Namespace) -> int:
    source = arguments.source.rstrip("/")
    session = requests.Session()
    catalog = fetch_catalog(session, source, arguments.timeout)

    if arguments.list:
        print(describe(catalog))
        return 0

    run_entry = pick_run(catalog, arguments.run)
    run_iso = str(run_entry.get("run"))
    productos_fuente = run_entry.get("products") or {}
    if arguments.all:
        pedidos = sorted(productos_fuente)
    else:
        pedidos = [item.strip() for item in arguments.products.split(",") if item.strip()]

    desconocidos = [product for product in pedidos if product not in productos_fuente]
    if desconocidos:
        raise CaptureError(
            "La pasada no publica: " + ", ".join(desconocidos)
            + ". Usa --list para ver lo que hay."
        )

    scope = str(
        (run_entry.get("publication") or {}).get("calculation_scope")
        or (catalog.get("publication") or {}).get("calculation_scope")
        or "model"
    )
    store = LocalObjectStore(Path(arguments.store).resolve())
    print(f"Fuente   {source}")
    print(f"Pasada   {run_iso} · alcance {scope}")
    print(f"Almacén  {store.root}")

    capturado: dict[str, list[str]] = {}
    fallos: list[str] = []
    bytes_totales = 0
    reutilizadas = 0
    descargadas = 0
    for product in pedidos:
        horas = hours_of(productos_fuente[product], arguments.hours)
        if not horas:
            fallos.append(f"{product}: sin horas publicadas")
            continue
        guardadas: list[str] = []
        for valid_time in horas:
            key_previa = frame_key(
                run_iso, product, valid_time,
                scope=scope,
                vertical_kind=arguments.vertical_kind,
                level=arguments.level,
            )
            if not arguments.force and store.exists(key_previa):
                # Ya está en disco: repetir la descarga solo gasta ancho de
                # banda de la instancia de la que se copia.
                guardadas.append(valid_time)
                reutilizadas += 1
                continue
            try:
                content = fetch_frame(
                    session,
                    source,
                    run_iso=run_iso,
                    product=product,
                    valid_time=valid_time,
                    vertical_kind=arguments.vertical_kind,
                    level=arguments.level,
                    timeout=arguments.timeout,
                )
            except (CaptureError, requests.RequestException) as error:
                fallos.append(f"{product} {valid_time}: {error}")
                continue
            metadata = grid_metadata(content)
            # El frame manda sobre el catálogo: si viniera de otra pasada o con
            # otro alcance, guardarlo bajo esta clave dejaría la foto mintiendo.
            if str(metadata.get("run") or run_iso) != run_iso:
                fallos.append(
                    f"{product} {valid_time}: la rejilla es de {metadata.get('run')}"
                )
                continue
            key = frame_key(
                run_iso,
                product,
                valid_time,
                scope=str(metadata.get("calculation_scope") or scope),
                vertical_kind=arguments.vertical_kind,
                level=arguments.level,
            )
            write_grid(store, key, content)
            guardadas.append(valid_time)
            descargadas += 1
            bytes_totales += len(content)
        if guardadas:
            capturado[product] = guardadas
            print(f"  {product:<22} {len(guardadas):>2} h")

    if not capturado:
        raise CaptureError(
            "No se ha guardado ningún frame."
            + ("\n  " + "\n  ".join(fallos) if fallos else "")
        )

    manifest = build_manifest(
        store,
        run_entry,
        capturado,
        scope=scope,
        vertical_kind=arguments.vertical_kind,
        level=arguments.level,
        reset=arguments.reset,
    )
    write_json(store, run_manifest_key(run_iso), manifest)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    try:
        register_run_slot(store, manifest)
    except ValueError:
        # Una pasada fuera de los turnos 00/06/12/18 no tiene ranura; el visor
        # la sigue viendo por el manifiesto `latest`.
        pass

    en_foto = sum(
        len(entry.get("valid_times") or [])
        for entry in manifest["catalog_products"].values()
    )
    print(
        f"\n{descargadas} frames descargados "
        f"({bytes_totales / 1024 / 1024:.0f} MB sin comprimir)"
        + (f", {reutilizadas} ya estaban en disco" if reutilizadas else "")
    )
    print(
        f"La foto anuncia {en_foto} horas de "
        f"{len(manifest['catalog_products'])} productos en {store.root}"
    )
    for aviso in fallos:
        print(f"  aviso · {aviso}")
    print(
        "\nPara servirlos sin clave de AROME:\n"
        f"  METEOLABX_FORECAST_STORE_PATH={store.root} \\\n"
        "  METEOLABX_FORECAST_PRECOMPUTED_ONLY=true \\\n"
        f"  METEOLABX_FORECAST_CALCULATION_SCOPE={scope} \\\n"
        "  ./scripts/run_server.sh"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copia frames AROME reales al almacén local de desarrollo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Instancia de la que copiar.")
    parser.add_argument("--store", default=DEFAULT_STORE, help="Carpeta del almacén local.")
    parser.add_argument("--run", default="", help="Pasada ISO; por defecto la más reciente con datos.")
    parser.add_argument(
        "--products",
        default=",".join(DEFAULT_PRODUCTS),
        help="Productos separados por coma.",
    )
    parser.add_argument("--all", action="store_true", help="Todos los productos de la pasada.")
    parser.add_argument("--hours", type=int, default=6, help="Horas por producto; 0 = todas.")
    parser.add_argument("--vertical-kind", default="height", choices=("height", "isobaric"))
    parser.add_argument("--level", type=float, default=10.0, help="Nivel del viento a capturar.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Segundos por petición.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Vuelve a descargar aunque la rejilla ya esté guardada.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Empieza una foto nueva en vez de sumarse a la guardada.",
    )
    parser.add_argument("--list", action="store_true", help="Enseña lo que ofrece la fuente y sale.")
    arguments = parser.parse_args()
    try:
        return capture(arguments)
    except CaptureError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de red: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
