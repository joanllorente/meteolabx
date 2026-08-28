"""Adaptador entre los diagnósticos AROME y la API del visor Svelte."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
import hashlib
import json
import logging
import math
import os
import resource
import sys
from pathlib import Path
import shutil
import struct
import threading
import tempfile
import time
from typing import Any, Callable, Iterator

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, mapping, shape

from server.services.arome_packages import (
    IP3_ELEMENTS,
    SURFACE_ELEMENTS,
    read_isobaric_extras,
    read_surface_fields,
    AromePackageError,
    discard_packages_before,
    ensure_package,
    read_isobaric_profile,
)
from server.services.meteofrance_auth import MeteoFranceAuthError
from server.services.convective_diagnostics import (
    downdraft_cape,
    diagnose_convection,
    effective_bulk_wind_difference,
    freezing_level_m,
    hypsometric_height_profile_m,
    interpolate_profile_at_height,
    pressure_weighted_layer_mean,
    significant_hail_parameter_sharppy,
)

from tabs.arome_forecast import (
    AromeError,
    AromeWCS,
    LOCAL_TZ,
    PALETTE,
    RasterField,
    _align,
    _catalonia_geometry,
    _compute_shear,
    _compute_ship,
    _get_uv_height,
    _load_forecast_regions_geojson,
    _mask_to_catalonia,
    _resolved_prefixes,
    _wait_for_api_request_slot,
    forecast_calculation_scope,
)



logger = logging.getLogger("meteolabx.arome_forecast")


# El resolvedor importado admite tanto identificadores WCS largos como abreviados.
PRODUCTS = {
    "temperature-2m": {
        "kind": "native", "prefix_kind": "height_temperature",
        "level": 2.0, "vertical_kind": "height", "value_mode": "temperature_c",
        "vmin": -12.0, "vmax": 42.0, "unit": "°C",
    },
    "temperature-850": {
        "kind": "native", "prefix_kind": "pressure_temperature",
        "level": 850.0, "vertical_kind": "pressure", "value_mode": "temperature_c",
        "vmin": -18.0, "vmax": 30.0, "unit": "°C",
    },
    "temperature-500": {
        "kind": "native", "prefix_kind": "pressure_temperature",
        "level": 500.0, "vertical_kind": "pressure", "value_mode": "temperature_c",
        "vmin": -38.0, "vmax": -4.0, "unit": "°C",
    },
    "shear-01": {"kind": "shear", "depth_m": 1000, "vmax": 26.0, "unit": "m/s"},
    "shear-03": {"kind": "shear", "depth_m": 3000, "vmax": 36.0, "unit": "m/s"},
    "shear-06": {"kind": "shear", "depth_m": 6000, "vmax": 52.0, "unit": "m/s"},
    "ebwd": {"kind": "convective", "diagnostic": "ebwd", "vmax": 50.0, "unit": "m/s"},
    "ship": {"kind": "convective", "diagnostic": "ship", "vmax": 5.0, "unit": ""},
    "mucape-muli": {
        "kind": "convective",
        "diagnostic": "mucape",
        "vmax": 3500.0,
        "unit": "J/kg",
    },
    "mlcape-mlli": {
        "kind": "convective",
        "diagnostic": "mlcape",
        "vmax": 3500.0,
        "unit": "J/kg",
    },
    "sbcape-sbli": {
        "kind": "convective",
        "diagnostic": "sbcape",
        "vmax": 3500.0,
        "unit": "J/kg",
    },
    "dcape": {
        "kind": "convective",
        "diagnostic": "dcape",
        "vmax": 1800.0,
        "unit": "J/kg",
    },
    "ordinary-cell-motion": {
        "kind": "convective",
        "diagnostic": "ordinary_cell_motion",
        "vmax": 35.0,
        "unit": "m/s",
    },
    "mu-ecape": {
        "kind": "native",
        "prefix_kind": "cape_mu",
        "vmax": 3500.0,
        "unit": "J/kg",
    },
    "ml-ecape": {
        "kind": "native",
        "prefix_kind": "cape_ml",
        "vmax": 3500.0,
        "unit": "J/kg",
    },
    "precip-1h": {
        "kind": "native",
        "prefix_kind": "precipitation_1h",
        "period": "PT1H",
        "vmax": 60.0,
        "unit": "mm",
    },
    "accumulated-precip": {
        "kind": "native", "prefix_kind": "precipitation_1h",
        "period": "PT1H", "value_mode": "nonnegative",
        "accumulate_from_run": True,
        "vmax": 150.0, "unit": "mm",
    },
    "wind-gust": {
        "kind": "native", "prefix_kind": "wind_gust_1h",
        "period": "PT1H", "level": 10.0, "vertical_kind": "height",
        "value_mode": "nonnegative", "vmax": 45.0, "unit": "m/s",
    },
    "relative-humidity-700": {
        "kind": "native", "prefix_kind": "pressure_relative_humidity",
        "level": 700.0, "vertical_kind": "pressure", "value_mode": "percent",
        "vmax": 100.0, "unit": "%",
    },
    "shortwave-down": {
        "kind": "native", "prefix_kind": "shortwave_down_1h",
        "period": "PT1H", "value_mode": "nonnegative",
        # Aunque DescribeCoverage anuncia W/m², GetCoverage PT1H entrega la
        # energía integrada de la hora. Convertimos J/m² a flujo medio W/m².
        "scale": 1.0 / 3600.0,
        "vmax": 1000.0, "unit": "W/m²",
    },
    "vertical-totals": {
        # Vertical Totals: T850 - T500. Mide el gradiente termico del entorno
        # sin depender de que parcela se elija, que es lo que lo hace util al
        # lado de los CAPE. Ambos niveles vienen en IP1, asi que no cuesta
        # ninguna descarga nueva.
        "kind": "level_difference",
        "prefix_kind": "pressure_temperature",
        "lower_level": 850.0,
        "upper_level": 500.0,
        "vmin": 18.0, "vmax": 34.0, "unit": "°C",
    },
    "cloud-cover": {
        "kind": "native", "prefix_kind": "total_cloud_cover",
        # AROME no publica nubosidad total en el instante de la pasada, aunque
        # la cobertura no declare periodo como hacen lluvia o racha.
        "starts_at_hour": 1,
        "value_mode": "percent", "vmax": 100.0, "unit": "%",
    },
    "wind-level": {
        "kind": "wind",
        "vmax": 55.0,
        "unit": "m/s",
    },
}

# EURW1S40: centros 37,5–55,4°N y 12°W–16°E, paso 0,025°.
# Los bounds incluyen media celda exterior: 1121 × 717 = 803.757 celdas.
AROME_MODEL_GRID_SHAPE = (717, 1121)
AROME_MODEL_GRID_BOUNDS = (-12.0125, 37.4875, 16.0125, 55.4125)


def _boundary_payload(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce el GeoJSON oficial a anillos exteriores aptos para Canvas."""
    result: list[dict[str, Any]] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") == "Polygon":
            polygons = [coordinates]
        elif geometry.get("type") == "MultiPolygon":
            polygons = coordinates
        else:
            continue
        rings = []
        for polygon in polygons:
            if not polygon:
                continue
            rings.append(
                [[round(float(lon), 5), round(float(lat), 5)] for lon, lat in polygon[0]]
            )
        if rings:
            properties = feature.get("properties") or {}
            result.append({
                "name": properties.get("NAMEUNIT") or properties.get("ADMIN") or "",
                "level": properties.get("boundary_level", "country"),
                "rings": rings,
            })
    return result


def domain_boundaries(scope: str = "") -> list[dict[str, Any]]:
    """Fronteras del dominio AROME, servidas aparte de los frames.

    Son idénticas para todos los mapas, así que el visor las pide una vez y
    las reutiliza en lugar de recibirlas dentro de cada frame.
    """
    return _domain_boundary_payload(
        AROME_MODEL_GRID_BOUNDS, scope or forecast_calculation_scope()
    )


def _domain_boundary_payload(
    bounds: tuple[float, float, float, float], scope: str
) -> list[dict[str, Any]]:
    """Anillos de frontera del dominio, reutilizados entre procesos.

    Recortar los GeoJSON completos cuesta ~110 MB y algo más de un segundo, y
    da siempre el mismo resultado para unos límites dados: apenas 1 MB de
    anillos. Cada trabajo del worker es un proceso nuevo, así que el resultado
    se deja en disco y los siguientes solo leen eso.
    """
    return _boundary_payload_from_disk(bounds, scope)


@lru_cache(maxsize=4)
def _boundary_payload_from_disk(
    bounds: tuple[float, float, float, float], scope: str
) -> list[dict[str, Any]]:
    firma = hashlib.sha1(
        f"{scope}|{'|'.join(f'{value:.4f}' for value in bounds)}".encode()
    ).hexdigest()[:16]
    cache_path = _boundary_cache_dir() / f"boundaries-{firma}.json"
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    payload = _boundary_payload(
        _model_boundary_geojson(_load_forecast_regions_geojson(), bounds)
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Se escribe aparte y se renombra para que otro proceso no lea un
        # fichero a medias.
        temporal = cache_path.with_suffix(f".{os.getpid()}.tmp")
        temporal.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporal.replace(cache_path)
    except OSError:
        # Sin caché el resultado es el mismo, solo más lento.
        pass
    return payload


def _boundary_cache_dir() -> Path:
    configured = os.getenv("METEOLABX_FORECAST_BOUNDARY_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "meteolabx-boundaries"


def _catalonia_only_geojson(geojson: dict[str, Any]) -> dict[str, Any]:
    """Conserva Cataluña (CodINE 09) del GeoJSON de comunidades."""
    features = [
        feature
        for feature in geojson.get("features", ())
        if str((feature.get("properties") or {}).get("CodINE", "")).zfill(2) == "09"
    ]
    if not features:
        raise AromeError("No se encontró Cataluña en el contorno administrativo.")
    return {"type": "FeatureCollection", "features": features}


@lru_cache(maxsize=1)
def _country_boundaries_geojson() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "ne_50m_admin_0_countries.geojson"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _admin1_boundaries_geojson() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "ne_50m_admin_1_states_provinces.geojson"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _model_boundary_geojson(
    regions_geojson: dict[str, Any], bounds: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Fronteras nacionales y divisiones administrativas visibles en el dominio."""
    viewport = box(*bounds)
    features: list[dict[str, Any]] = []
    for feature in _country_boundaries_geojson().get("features", ()):
        geometry = shape(feature.get("geometry") or {})
        if geometry.is_empty or not geometry.intersects(viewport):
            continue
        # Natural Earth 1:10m: conservar detalle suficiente para que costas y
        # fronteras sigan siendo suaves incluso al zoom máximo del visor.
        clipped = geometry.intersection(viewport).simplify(0.0012, preserve_topology=True)
        if clipped.is_empty:
            continue
        properties = feature.get("properties") or {}
        features.append({
            "type": "Feature",
            "properties": {
                "ADMIN": properties.get("ADMIN", ""),
                "boundary_level": "country",
            },
            "geometry": mapping(clipped),
        })
    for feature in _admin1_boundaries_geojson().get("features", ()):
        geometry = shape(feature.get("geometry") or {})
        if geometry.is_empty or not geometry.intersects(viewport):
            continue
        clipped = geometry.intersection(viewport)
        if clipped.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": feature.get("properties") or {},
            "geometry": mapping(clipped),
        })
    if not features:
        features.extend(regions_geojson.get("features", ()))
    return {"type": "FeatureCollection", "features": features}


def _place_local_array_in_model_grid(
    array: np.ndarray,
    source_bounds: tuple[float, float, float, float],
) -> np.ndarray:
    """Sitúa un recorte WCS en EURW1S40 sin calcular las celdas exteriores."""
    target_height, target_width = AROME_MODEL_GRID_SHAPE
    target_west, _target_south, _target_east, target_north = AROME_MODEL_GRID_BOUNDS
    source = np.asarray(array)
    source_height, source_width = source.shape
    west, _south, _east, north = source_bounds
    resolution = 0.025
    column_start = int(round((west - target_west) / resolution))
    row_start = int(round((target_north - north) / resolution))
    column_end = column_start + source_width
    row_end = row_start + source_height

    target = np.full((target_height, target_width), np.nan, dtype=source.dtype)
    target_column_start = max(0, column_start)
    target_row_start = max(0, row_start)
    target_column_end = min(target_width, column_end)
    target_row_end = min(target_height, row_end)
    if target_column_start >= target_column_end or target_row_start >= target_row_end:
        raise AromeError("El recorte local queda fuera del dominio AROME EURW1S40.")
    source_column_start = target_column_start - column_start
    source_row_start = target_row_start - row_start
    target[target_row_start:target_row_end, target_column_start:target_column_end] = source[
        source_row_start:source_row_start + (target_row_end - target_row_start),
        source_column_start:source_column_start + (target_column_end - target_column_start),
    ]
    return target


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AromeError("La hora válida no tiene formato ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _convective_prefixes(catalog) -> dict[str, str]:
    prefixes = {
        "height_temperature": catalog.resolve("height_temperature"),
        "height_dewpoint": catalog.resolve("height_dewpoint"),
        "surface_pressure": catalog.resolve("surface_pressure"),
        "pressure_temperature": catalog.resolve("pressure_temperature"),
        "pressure_dewpoint": catalog.resolve("pressure_dewpoint"),
        "height_u": catalog.resolve("height_u"),
        "height_v": catalog.resolve("height_v"),
        "pressure_u": catalog.resolve("pressure_u"),
        "pressure_v": catalog.resolve("pressure_v"),
    }
    terrain = catalog.resolve_optional("terrain")
    if terrain:
        prefixes["terrain"] = terrain
    return prefixes


def _main_cycle_runs(runs: set[datetime]) -> set[datetime]:
    """Conserva exclusivamente los cuatro ciclos operativos 00/06/12/18Z."""
    return {candidate for candidate in runs if candidate.hour % 6 == 0}


def _product_context(
    token: str,
    product_id: str,
    vertical_kind: str = "height",
    run_iso: str = "",
):
    config = PRODUCTS.get(product_id)
    if config is None:
        raise AromeError(f"Producto de predicción desconocido: {product_id}")
    client = AromeWCS(token)
    catalog = client.capabilities()
    if config["kind"] in {"native", "level_difference"}:
        # La diferencia entre dos niveles usa un solo campo, como los nativos:
        # lo que cambia es que se pide dos veces, a alturas distintas.
        prefixes = {"field": catalog.resolve(str(config["prefix_kind"]))}
    elif config["kind"] == "convective":
        prefixes = _convective_prefixes(catalog)
    elif config["kind"] == "wind":
        if vertical_kind not in {"height", "isobaric"}:
            raise AromeError("El tipo de nivel de viento no es válido.")
        stem = "height" if vertical_kind == "height" else "pressure"
        prefixes = {
            "u": catalog.resolve(f"{stem}_u"),
            "v": catalog.resolve(f"{stem}_v"),
        }
        if vertical_kind == "isobaric":
            prefixes["surface_pressure"] = catalog.resolve("surface_pressure")
    else:
        prefixes = _resolved_prefixes(
            catalog, int(config["depth_m"]), str(config["kind"])
        )
    required = [prefix for key, prefix in prefixes.items() if key != "terrain"]
    period = config.get("period")
    common_runs = catalog.runs_for(required[0], period)
    for prefix in required[1:]:
        common_runs &= catalog.runs_for(prefix)
    if not common_runs:
        raise AromeError("No hay un run común para todas las variables requeridas.")

    # La API WCS 0,025° también anuncia ciclos intermedios 03/09/15/21Z.
    # MeteoLabX publica el producto operativo de cuatro RUN diarios para no
    # mezclar esas coberturas con los ciclos principales 00/06/12/18Z.
    common_runs = _main_cycle_runs(common_runs)
    if not common_runs:
        raise AromeError("Todavía no hay un RUN principal 00/06/12/18Z disponible.")

    if run_iso:
        requested_run = _parse_time(run_iso)
        if requested_run not in common_runs:
            raise AromeError("El RUN solicitado ya no está disponible en Météo-France.")
        reference = catalog.coverage_id(required[0], requested_run, period=period)
        requested_times = client.describe(reference).valid_times(requested_run)
        if not requested_times:
            raise AromeError("El RUN solicitado no contiene horas disponibles.")
        return config, client, catalog, prefixes, requested_run, requested_times

    # Durante la publicación, un RUN nuevo puede aparecer con solo H+00.
    # Priorizamos la más reciente que ya tenga un horizonte útil; si ninguna
    # alcanza el mínimo, conservamos la última como fallback transparente.
    #
    # El mínimo decide cuánto se tarda en empezar: el nivel 0 se pasa casi una
    # hora esperando a que el modelo publique, así que adoptar antes la pasada
    # solapa esa espera con el trabajo en vez de encadenarlos. Bajarlo también
    # compromete antes: si Météo-France retirase una pasada a medio publicar,
    # se habría calculado sobre ella. Por eso es configurable.
    run = max(common_runs)
    times = []
    for candidate in sorted(common_runs, reverse=True):
        reference = catalog.coverage_id(required[0], candidate, period=period)
        candidate_times = client.describe(reference).valid_times(candidate)
        if not times:
            run, times = candidate, candidate_times
        if len(candidate_times) >= MINIMUM_RUN_HOURS:
            run, times = candidate, candidate_times
            break
    if not times:
        raise AromeError("El run más reciente no contiene horas disponibles.")
    return config, client, catalog, prefixes, run, times


def _wind_levels(client, catalog, run: datetime) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for kind, prefix_kind in (("height", "height_u"), ("isobaric", "pressure_u")):
        prefix = catalog.resolve(prefix_kind)
        available_runs = catalog.runs_for(prefix)
        selected_run = run if run in available_runs else max(available_runs)
        metadata = client.describe(catalog.coverage_id(prefix, selected_run))
        axis = metadata.vertical_axis()
        result[kind] = sorted(float(value) for value in metadata.axes.get(axis or "", ()))
    return result


def catalog_payload(token: str) -> dict[str, Any]:
    """Catálogo con TTL de un minuto durante la publicación progresiva."""
    refresh_s = max(5, int(os.getenv("METEOLABX_FORECAST_CATALOG_REFRESH_S", "60")))
    return _catalog_payload_cached(token, int(time.time() // refresh_s))


@lru_cache(maxsize=16)
def _catalog_payload_cached(token: str, _minute_bucket: int) -> dict[str, Any]:
    """Devuelve pasadas y horas disponibles para los productos conectados."""
    products: dict[str, Any] = {}
    unavailable_products: dict[str, str] = {}
    for product_id in PRODUCTS:
        try:
            config, client, coverage_catalog, _, run, times = _product_context(
                token, product_id
            )
        except AromeError as exc:
            # Las coberturas del WCS pueden aparecer/desaparecer durante la
            # publicación. Un producto opcional no debe derribar el catálogo
            # completo ni bloquear los otros mapas conectados.
            unavailable_products[product_id] = str(exc)
            continue
        products[product_id] = {
            "run": run.isoformat().replace("+00:00", "Z"),
            "run_local": run.astimezone(LOCAL_TZ).isoformat(),
            "valid_times": [value.isoformat().replace("+00:00", "Z") for value in times],
            "vmax": config["vmax"],
            "unit": config["unit"],
        }
        if config["kind"] == "wind":
            products[product_id]["levels"] = _wind_levels(
                client, coverage_catalog, run
            )
    return {
        "model": "AROME France",
        "resolution": "0,025°",
        "domain": {
            "label": "Dominio nativo AROME France",
            "calculation_scope": forecast_calculation_scope(),
            "local_crop": forecast_calculation_scope() == "catalonia",
        },
        "products": products,
        "unavailable_products": unavailable_products,
    }


def _hex_rgb(value: str) -> np.ndarray:
    value = value.lstrip("#")
    return np.asarray([int(value[index:index + 2], 16) for index in (0, 2, 4)])


def _rgba_field(values: np.ndarray, vmax: float) -> np.ndarray:
    valid = np.isfinite(values)
    normalized = np.clip(np.nan_to_num(values, nan=0.0) / vmax, 0.0, 1.0)
    palette = np.stack([_hex_rgb(color) for color in PALETTE]).astype(float)
    scaled = normalized * (len(palette) - 1)
    lower = np.floor(scaled).astype(int)
    upper = np.minimum(lower + 1, len(palette) - 1)
    fraction = (scaled - lower)[..., None]
    rgb = palette[lower] * (1.0 - fraction) + palette[upper] * fraction
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[..., :3] = rgb.astype(np.uint8)
    rgba[..., 3] = np.where(valid, 224, 0).astype(np.uint8)
    return rgba


def _draw_vectors(image: Image.Image, field, output_size: tuple[int, int]) -> None:
    if field.vector_u is None or field.vector_v is None:
        return
    magnitude = np.hypot(field.vector_u, field.vector_v)
    valid = np.isfinite(field.data) & np.isfinite(magnitude) & (magnitude >= 0.5)
    rows, cols = np.nonzero(valid)
    step = max(1, int(round(field.data.shape[1] / 18)))
    sampled = (rows % step == step // 2) & (cols % step == step // 2)
    rows, cols = rows[sampled], cols[sampled]
    width, height = output_size
    source_height, source_width = field.data.shape
    draw = ImageDraw.Draw(image)
    arrow_length = max(12.0, width / 48.0)
    for row, col in zip(rows.tolist(), cols.tolist()):
        east = float(field.vector_u[row, col] / magnitude[row, col])
        north = float(field.vector_v[row, col] / magnitude[row, col])
        x = (col + 0.5) * width / source_width
        y = (row + 0.5) * height / source_height
        pixel_x = max(0, min(width - 1, int(round(x))))
        pixel_y = max(0, min(height - 1, int(round(y))))
        if image.getpixel((pixel_x, pixel_y))[3] == 0:
            continue
        dx, dy = east * arrow_length, -north * arrow_length
        start = (x - dx / 2, y - dy / 2)
        tip = (x + dx / 2, y + dy / 2)
        draw.line([start, tip], fill=(8, 17, 26, 205), width=2)
        angle = np.arctan2(dy, dx)
        for wing in (-2.55, 2.55):
            endpoint = (
                tip[0] + arrow_length * 0.34 * np.cos(angle + wing),
                tip[1] + arrow_length * 0.34 * np.sin(angle + wing),
            )
            draw.line([tip, endpoint], fill=(8, 17, 26, 205), width=2)


def _draw_boundary(image: Image.Image, field, geometry) -> None:
    west, south, east, north = field.bounds
    width, height = image.size
    draw = ImageDraw.Draw(image)
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for polygon in polygons:
        points = [
            (
                (lon - west) / (east - west) * width,
                (north - lat) / (north - south) * height,
            )
            for lon, lat in polygon.exterior.coords
        ]
        draw.line(points, fill=(232, 240, 247, 220), width=3, joint="curve")


def _render_png(field, vmax: float) -> bytes:
    regions_geojson = _load_forecast_regions_geojson()
    if forecast_calculation_scope() == "catalonia":
        visible_geojson = _catalonia_only_geojson(regions_geojson)
        geometry = _catalonia_geometry(visible_geojson)
        values = _mask_to_catalonia(field, geometry)
    else:
        visible_geojson = _model_boundary_geojson(regions_geojson, field.bounds)
        geometry = _catalonia_geometry(visible_geojson)
        values = np.asarray(field.data, dtype=float)
    raw = Image.fromarray(_rgba_field(values, vmax), mode="RGBA")
    output_size = (960, 680)
    image = raw.resize(output_size, Image.Resampling.BILINEAR)
    _draw_vectors(image, field, output_size)
    _draw_boundary(image, field, geometry)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _as_kelvin(values: np.ndarray, units: str) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    finite = data[np.isfinite(data)]
    if finite.size and ("c" in units.lower() or float(np.nanmedian(finite)) < 150.0):
        return data + 273.15
    return data


def _as_hpa(values: np.ndarray, units: str) -> np.ndarray:
    data = np.asarray(values, dtype=float)
    finite = data[np.isfinite(data)]
    if finite.size and (("pa" in units.lower() and "hpa" not in units.lower()) or float(np.nanmedian(finite)) > 2_000):
        return data / 100.0
    return data


def _pressure_levels(client, catalog, prefix: str, run: datetime) -> list[float]:
    metadata = client.describe(catalog.coverage_id(prefix, run))
    axis = metadata.vertical_axis()
    levels = [float(value) for value in metadata.axes.get(axis or "", ())]
    unit = metadata.units.get(axis or "", "").lower()
    if levels and (("pa" in unit and "hpa" not in unit) or max(levels) > 2_000):
        levels = [value / 100.0 for value in levels]
    return sorted((value for value in levels if 100.0 <= value <= 1_000.0), reverse=True)


def _wait_for_profile_request_slot() -> None:
    """Limita globalmente WCS incluso con varios perfiles en procesos distintos."""
    interval = max(
        0.1,
        float(os.getenv("METEOLABX_AROME_PROFILE_REQUEST_INTERVAL_S", "1.25")),
    )
    _wait_for_api_request_slot(interval)


# Filas de rejilla que se diagnostican de una vez. El perfil completo ocupa
# ~1 GB y los temporales del cálculo varias veces más: trocear por bandas
# recorta el pico sin cambiar el resultado, porque cada celda es independiente
# de sus vecinas en la vertical.
# Bandas que se calculan a la vez dentro de un mismo perfil. Numpy suelta el
# GIL en las operaciones grandes, así que los hilos escalan de verdad; por
# encima de tres deja de mejorar y cada uno suma su propio pico de memoria.
CONVECTIVE_THREADS = max(
    1, int(os.getenv("METEOLABX_FORECAST_CONVECTIVE_THREADS", "3"))
)


CONVECTIVE_STRIPE_ROWS = int(
    os.getenv("METEOLABX_FORECAST_CONVECTIVE_STRIPE_ROWS", "128")
)
# DCAPE es el único que no tolera bandas estrechas: su selección de capa de
# origen a través de SHARPpy cambia según cómo se particione la rejilla, y
# sólo por encima de ~120 filas devuelve lo mismo que la rejilla entera. De ahí
# las 128 de arriba: comprobado sobre 384x1121, da el mismo DCAPE bit a bit que
# con 192 y ahorra 435 MB por perfil, que es lo que más aprieta cuando hay
# varios a la vez. Los
# otros trece son celda a celda, así que cuando DCAPE queda fuera —su propio
# turno lo calcula aparte— se puede bajar mucho: medido sobre una rejilla de
# 384x1121, pasar de 192 a 64 filas recorta 1.089 MB del pico en el mismo
# tiempo y con resultados idénticos hasta el último bit.
CONVECTIVE_STRIPE_ROWS_WITHOUT_DCAPE = int(
    os.getenv("METEOLABX_FORECAST_CONVECTIVE_STRIPE_ROWS_NO_DCAPE", "64")
)


def _convective_outputs(
    pressure: np.ndarray,
    temperature: np.ndarray,
    dewpoint: np.ndarray,
    u_profile: np.ndarray,
    v_profile: np.ndarray,
    terrain: np.ndarray,
    surface_u: np.ndarray,
    surface_v: np.ndarray,
    levels: list[float],
    include_dcape: bool = True,
    only_dcape: bool = False,
) -> dict[str, np.ndarray]:
    """Diagnósticos convectivos de un bloque de filas de la rejilla.

    `only_dcape` calcula el descenso y nada más. DCAPE va en su propio nivel,
    detrás de los otros trece, y por el camino se rehacían las tres parcelas
    —MU, ML y SB— que ese nivel anterior ya había calculado: medido sobre una
    banda de 192x1121, 14,8 s de parcelas para llegar a un DCAPE que cuesta
    12,3. El resultado es idéntico; lo que se ahorra es el trabajo repetido.
    """
    shape = terrain.shape
    height = hypsometric_height_profile_m(pressure, temperature, dewpoint, terrain)
    if only_dcape:
        vacio = np.full(shape, np.nan)
        return {
            "dcape": downdraft_cape(pressure, temperature, dewpoint, height),
            **{
                nombre: vacio.copy()
                for nombre in (
                    "mucape", "muli", "mlcape", "mlli", "sbcape", "sbli",
                    "cell_u", "cell_v", "cell_speed",
                    "ebwd", "ebwd_u", "ebwd_v", "ship",
                )
            },
        }
    diagnostics = diagnose_convection(
        pressure, temperature, dewpoint, height, include_dcape=include_dcape
    )

    cell_u = pressure_weighted_layer_mean(
        pressure,
        u_profile,
        diagnostics.ml_lcl_pressure_hpa,
        diagnostics.ml_equilibrium_pressure_hpa,
    )
    cell_v = pressure_weighted_layer_mean(
        pressure,
        v_profile,
        diagnostics.ml_lcl_pressure_hpa,
        diagnostics.ml_equilibrium_pressure_hpa,
    )

    ebwd, ebwd_u, ebwd_v = effective_bulk_wind_difference(
        height,
        u_profile,
        v_profile,
        diagnostics.effective_base_height_m,
        diagnostics.mu_equilibrium_height_m,
    )
    six_km_height = terrain + 6_000.0
    u_6km = interpolate_profile_at_height(height, u_profile, six_km_height)
    v_6km = interpolate_profile_at_height(height, v_profile, six_km_height)
    shear_0_6 = np.hypot(u_6km - surface_u, v_6km - surface_v)

    index_700 = levels.index(700.0) + 1
    index_500 = levels.index(500.0) + 1
    layer_depth = height[index_500] - height[index_700]
    lapse_rate = np.divide(
        (temperature[index_700] - temperature[index_500]) * 1_000.0,
        layer_depth,
        out=np.full(shape, np.nan),
        where=layer_depth > 0,
    )
    freezing_agl = freezing_level_m(temperature, height)
    ship = significant_hail_parameter_sharppy(
        diagnostics.mucape,
        diagnostics.mu_mixing_ratio_gkg,
        lapse_rate,
        temperature[index_500] - 273.15,
        shear_0_6,
        freezing_agl,
    )

    return {
        "mucape": diagnostics.mucape,
        "muli": diagnostics.muli,
        "mlcape": diagnostics.mlcape,
        "mlli": diagnostics.mlli,
        "sbcape": diagnostics.sbcape,
        "sbli": diagnostics.sbli,
        "dcape": diagnostics.dcape,
        "cell_u": cell_u,
        "cell_v": cell_v,
        "cell_speed": np.hypot(cell_u, cell_v),
        "ebwd": ebwd,
        "ebwd_u": ebwd_u,
        "ebwd_v": ebwd_v,
        "ship": ship,
    }


# DCAPE era el único que pedía el rocío isobárico al WCS: 24 peticiones por
# hora, 864 por pasada, más que todo el resto junto. Medido contra el modelo
# sobre la misma pasada y hora, el rocío derivado de la humedad del paquete se
# desvía 0,006 K de media y mueve el DCAPE un 0,18 %, con un 0,3 % de celdas
# saltando de capa de origen —la misma sensibilidad que ya tiene al troceado
# de la rejilla—. A 0 se deriva y DCAPE deja de depender del WCS.
DCAPE_EXACT_DEWPOINT = os.getenv(
    "METEOLABX_FORECAST_DCAPE_EXACT_DEWPOINT", "1"
).strip().lower() not in {"0", "false", "no"}


# Plazos publicados que se le exigen a una pasada para adoptarla.
MINIMUM_RUN_HOURS = max(
    1, int(os.getenv("METEOLABX_AROME_MINIMUM_RUN_HOURS", "6"))
)


PROFILE_SPILL_ENABLED = os.getenv(
    "METEOLABX_FORECAST_PROFILE_SPILL", "1"
).strip().lower() not in {"0", "false", "no"}


def _is_memory_backed(path: Path) -> bool:
    """Indica si la ruta vive en un sistema de archivos en RAM.

    Volcar a un tmpfs no libera nada: los bytes siguen en memoria, sólo que
    contabilizados de otra forma. Ante la duda se responde que sí, para no
    empeorar las cosas creyendo que se mejoran.
    """
    mounts = Path("/proc/mounts")
    if not mounts.exists():  # macOS y demás: no se vuelca.
        return True
    try:
        target = path.resolve()
        best, memoria = -1, True
        for linea in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
            partes = linea.split()
            if len(partes) < 3:
                continue
            punto, tipo = Path(partes[1]), partes[2]
            if punto == target or punto in target.parents:
                if len(str(punto)) > best:
                    best, memoria = len(str(punto)), tipo in {"tmpfs", "ramfs"}
        return memoria
    except OSError:
        return True


@contextlib.contextmanager
def _profiles_spilled_to_disk(
    profiles: list[np.ndarray],
) -> Iterator[list[np.ndarray]]:
    """Aparta los perfiles apilados al disco y los sirve mapeados.

    El diagnóstico recorre la rejilla por bandas de filas, que en un perfil
    apilado son trozos contiguos: leerlos desde un fichero mapeado no cuesta
    tiempo medible, porque el cálculo lo domina la CPU y no el acceso. A cambio,
    ese giga deja de ser memoria anónima —la que el núcleo no puede recuperar y
    que acaba en un OOM— y pasa a ser caché de fichero, que sí puede soltar
    cuando hay presión.

    La lista se vacía: quien llama debe soltar sus propias referencias antes,
    o los perfiles seguirán ocupando memoria además del fichero.
    """
    destino = Path(tempfile.gettempdir())
    if not PROFILE_SPILL_ENABLED or _is_memory_backed(destino):
        yield profiles
        return
    carpeta = None
    mapeados: list[np.ndarray] = []
    try:
        carpeta = Path(tempfile.mkdtemp(prefix="meteolabx-perfil-", dir=destino))
        for indice, array in enumerate(profiles):
            ruta = carpeta / f"{indice}.f8"
            escritura = np.memmap(ruta, dtype=array.dtype, mode="w+", shape=array.shape)
            escritura[:] = array
            escritura.flush()
            del escritura
            mapeados.append(
                np.memmap(ruta, dtype=array.dtype, mode="r", shape=array.shape)
            )
    except OSError as error:
        # Sin espacio o sin permisos: se sigue en memoria, que siempre funciona.
        logger.warning("No se han podido volcar los perfiles al disco: %s", error)
        mapeados.clear()
        if carpeta is not None:
            shutil.rmtree(carpeta, ignore_errors=True)
        yield profiles
        return
    try:
        profiles.clear()
        yield mapeados
    finally:
        mapeados.clear()
        shutil.rmtree(carpeta, ignore_errors=True)


def _convective_outputs_in_stripes(
    pressure: np.ndarray,
    temperature: np.ndarray,
    dewpoint: np.ndarray,
    u_profile: np.ndarray,
    v_profile: np.ndarray,
    terrain: np.ndarray,
    surface_u: np.ndarray,
    surface_v: np.ndarray,
    levels: list[float],
    stripe_rows: int | None = None,
    include_dcape: bool = True,
    only_dcape: bool = False,
) -> dict[str, np.ndarray]:
    """Encadena el diagnóstico por bandas de filas y recompone la rejilla."""
    rows = terrain.shape[0]
    if stripe_rows is not None:
        step = stripe_rows
    elif include_dcape:
        step = CONVECTIVE_STRIPE_ROWS
    else:
        step = CONVECTIVE_STRIPE_ROWS_WITHOUT_DCAPE
    arguments = (
        pressure, temperature, dewpoint, u_profile, v_profile,
        terrain, surface_u, surface_v, levels, include_dcape, only_dcape,
    )
    if step <= 0 or step >= rows:
        return _convective_outputs(*arguments)

    # Cada banda se vuelca sobre el resultado final y se descarta. Acumularlas
    # todas para concatenarlas al final mantenía dos copias completas de los
    # catorce campos en el momento de mayor consumo.
    merged: dict[str, np.ndarray] = {}
    reserva = threading.Lock()

    def una_banda(band: slice) -> None:
        stripe = _convective_outputs(
            pressure[:, band],
            temperature[:, band],
            dewpoint[:, band],
            u_profile[:, band],
            v_profile[:, band],
            terrain[band],
            surface_u[band],
            surface_v[band],
            levels,
            include_dcape,
            only_dcape,
        )
        for name, values in stripe.items():
            with reserva:
                if name not in merged:
                    merged[name] = np.empty(
                        (rows, *values.shape[1:]), dtype=values.dtype
                    )
            # Cada banda escribe en sus propias filas, así que fuera del
            # cerrojo no hay dos hilos tocando lo mismo.
            merged[name][band] = values
        stripe.clear()

    bandas = [
        slice(start, min(start + step, rows)) for start in range(0, rows, step)
    ]
    if CONVECTIVE_THREADS <= 1 or len(bandas) == 1:
        for band in bandas:
            una_banda(band)
        return merged

    # Las bandas son independientes y numpy suelta el GIL en las operaciones
    # grandes, así que se calculan a la vez. Medido sobre 384x1121: tres hilos
    # bajan el DCAPE de 24,3 a 9,2 s, con el mismo resultado, a cambio de
    # 743 MB. Por encima de tres deja de mejorar.
    with ThreadPoolExecutor(
        max_workers=CONVECTIVE_THREADS, thread_name_prefix="arome-banda"
    ) as bandas_a_la_vez:
        list(bandas_a_la_vez.map(una_banda, bandas))
    return merged


# Viento a 10 m de la hora en curso. Las tres cizalladuras parten del mismo
# campo, así que compartirlo evita repetir su descarga una vez por producto.
# Se guarda una sola hora: el worker agrupa los tres productos por hora y cada
# entrada ocupa dos rejillas completas.
_SURFACE_WIND_CACHE: dict[tuple[str, str], tuple[RasterField, RasterField]] = {}


def _surface_wind_10m(
    client: AromeWCS,
    catalog: Any,
    prefixes: dict[str, str],
    run: datetime,
    valid_time: datetime,
) -> tuple[RasterField, RasterField]:
    """Viento a 10 m reutilizable. No modificar los campos devueltos."""
    key = (run.isoformat(), valid_time.isoformat())
    cached = _SURFACE_WIND_CACHE.get(key)
    if cached is not None:
        return cached
    fields = _get_uv_height(client, catalog, prefixes, run, valid_time, 10.0)
    _SURFACE_WIND_CACHE.clear()
    _SURFACE_WIND_CACHE[key] = fields
    return fields


# Cada entrada retiene siete campos del dominio completo (~45 MB). Un trabajo
# del worker resuelve una sola hora, así que basta con no perder la que se
# está usando; guardar treinta y dos era retener más de un giga sin necesidad.
def _shear_levels_from_package(
    reference: RasterField, run: datetime, valid_time: datetime, levels_hpa: tuple[float, ...]
) -> dict[float, dict[str, RasterField]] | None:
    """Niveles isobáricos para la cizalladura 0-6 km, desde el paquete GRIB.

    Son los mismos que ya se bajan para el perfil convectivo, así que leerlos
    del fichero evita 18 peticiones por hora de predicción.
    """
    campos = _isobaric_fields_from_package(reference, run, valid_time, list(levels_hpa))
    if not campos:
        return None
    disponibles = {
        nivel: {
            "u": campos["u"][nivel],
            "v": campos["v"][nivel],
            "geopotential": campos["geopotential"][nivel],
        }
        for nivel in levels_hpa
        if nivel in campos["u"] and nivel in campos["geopotential"]
    }
    return disponibles or None


def _dewpoint_from_relative_humidity_c(
    temperature_c: np.ndarray, relative_humidity_pct: np.ndarray
) -> np.ndarray:
    """Punto de rocío en °C a partir de temperatura y humedad relativa.

    Misma formulación de Magnus que `saturation_vapor_pressure_hpa`, para que
    el rocío derivado sea coherente con el resto de la termodinámica.

    Se usa solo en el bloque de trece diagnósticos: DCAPE emplea el rocío que
    publica el modelo, porque su selección de capa de origen es sensible a
    diferencias mínimas y la derivación lo desplazaba en un 0,8 % de celdas.
    """
    saturation = 6.112 * np.exp(
        17.67 * temperature_c / (temperature_c + 243.5)
    )
    vapour = np.clip(relative_humidity_pct, 0.01, 100.0) / 100.0 * saturation
    logarithm = np.log(np.maximum(vapour, 1e-6) / 6.112)
    return 243.5 * logarithm / (17.67 - logarithm)


def _packages_available() -> bool:
    """Si conviene servir el perfil desde los paquetes GRIB.

    Solo en el dominio completo: en local el WCS entrega el recorte catalán y
    el paquete siempre trae la rejilla entera, así que no son intercambiables.
    """
    if forecast_calculation_scope() != "model":
        return False
    if not os.getenv("METEOLABX_METEOFRANCE_APPLICATION_ID", "").strip():
        return False
    return os.getenv("METEOLABX_AROME_USE_PACKAGES", "1").strip().lower() not in {
        "0", "false", "no"
    }


def _isobaric_fields_from_package(
    reference: RasterField,
    run: datetime,
    valid_time: datetime,
    levels: list[float],
) -> dict[str, dict[float, RasterField]] | None:
    """Temperatura, viento y geopotencial del paquete isobárico.

    Devuelve None si el paquete todavía no está publicado, para que quien
    llame siga por el camino del WCS: durante las primeras horas de una pasada
    el bloque aún no existe.
    """
    if not _packages_available():
        return None
    try:
        path = ensure_package("IP1", run, valid_time)
        profile, geometria = read_isobaric_profile(path, valid_time, levels)
    except (AromePackageError, MeteoFranceAuthError) as exc:
        logger.info("Paquete IP1 no disponible, se usa el WCS: %s", exc)
        return None

    # Con la geometría del propio paquete: cuando coincide con la referencia,
    # _align la reconoce y devuelve los datos sin tocarlos, y cuando no, los
    # recorta bien en vez de reinterpretarlos sobre una rejilla ajena.
    common = geometria
    unidades = {
        "temperature": "C",
        "relative_humidity": "%",
        "u": "m/s",
        "v": "m/s",
        "geopotential": "m^2/s^2",
    }
    salida: dict[str, dict[float, RasterField]] = {}
    for nombre, unidad in unidades.items():
        salida[nombre] = {
            nivel: RasterField(valores, *common, unidad)
            for nivel, valores in profile[nombre].items()
        }
    return salida


def _surface_fields_from_package(
    reference: RasterField,
    run: datetime,
    valid_time: datetime,
) -> dict[str, RasterField] | None:
    """Rocío, presión y viento de superficie de los paquetes SP1 y SP2.

    Ahorra cuatro descargas WCS por hora, cada una con su turno en el
    estrangulador. Devuelve None si algún paquete todavía no está publicado,
    para que quien llame siga por el camino de siempre.
    """
    if not _packages_available():
        return None
    campos: dict[str, RasterField] = {}
    for paquete, elementos in SURFACE_ELEMENTS.items():
        try:
            path = ensure_package(paquete, run, valid_time)
            leidos, geometria = read_surface_fields(path, valid_time, elementos)
        except (AromePackageError, MeteoFranceAuthError) as exc:
            logger.info("Paquete %s no disponible, se usa el WCS: %s", paquete, exc)
            return None
        # Con la geometría del propio paquete; alinearlos con la referencia es
        # cosa de quien llama, igual que con cualquier campo del WCS.
        for nombre, (valores, unidad) in leidos.items():
            campos[nombre] = RasterField(valores, *geometria, unidad)
    esperados = {
        nombre for elementos in SURFACE_ELEMENTS.values()
        for nombre, _ in elementos.values()
    }
    if esperados - set(campos):
        logger.info(
            "Los paquetes de superficie no traen %s para %s; se usa el WCS.",
            ", ".join(sorted(esperados - set(campos))),
            valid_time.isoformat(),
        )
        return None
    return campos


def _isobaric_extras_from_package(
    run: datetime,
    valid_time: datetime,
    levels: list[float],
    campos_pedidos: tuple[str, ...] = (),
) -> tuple[dict[str, dict[float, RasterField]], tuple[Any, Any, Any]] | None:
    """Rocío y velocidad vertical isobáricos, del paquete IP3.

    El rocío es el único campo por el que DCAPE seguía pidiendo al WCS: 24
    peticiones por hora, más de la mitad de lo que tardaba. La velocidad
    vertical viene en el mismo paquete y no cuesta nada más.

    Devuelve None si el paquete no está o no trae lo que se espera, para que
    quien llame siga por el camino de siempre.
    """
    if not _packages_available():
        return None
    try:
        path = ensure_package("IP3", run, valid_time)
        # Leer un elemento que nadie va a usar son 150 MB por hora tirados: el
        # perfil convectivo sólo necesita el rocío.
        buscar = (
            {nombre: IP3_ELEMENTS[nombre] for nombre in campos_pedidos}
            if campos_pedidos
            else IP3_ELEMENTS
        )
        campos, geometria = read_isobaric_extras(path, valid_time, levels, buscar)
    except (AromePackageError, MeteoFranceAuthError) as exc:
        logger.info("Paquete IP3 no disponible: %s", exc)
        return None
    unidades = {"dewpoint": "C", "vertical_velocity": "m/s"}
    salida: dict[str, dict[float, RasterField]] = {}
    for nombre, niveles in campos.items():
        if not niveles:
            continue
        salida[nombre] = {
            nivel: RasterField(valores, *geometria, unidades.get(nombre, ""))
            for nivel, valores in niveles.items()
        }
    return (salida, geometria) if salida else None


@lru_cache(maxsize=2)
def _convective_frames(
    token: str,
    valid_time_iso: str,
    run_iso: str = "",
    exact_dewpoint: bool = True,
    include_dcape: bool | None = None,
    only_dcape: bool = False,
) -> tuple[dict[str, RasterField], datetime]:
    """Descarga un perfil común y reutiliza todos sus diagnósticos convectivos.

    `exact_dewpoint` pide el rocío isobárico al WCS, nivel a nivel; sin él se
    deriva de la humedad del paquete. `include_dcape` decide si se calcula
    DCAPE, que es lo único que necesitaba ese rocío exacto. Van separados
    porque medido contra el modelo el rocío derivado se desvía 0,006 K y mueve
    el DCAPE un 0,18 %, así que se puede calcular sin pagar 24 peticiones por
    hora.
    """
    if include_dcape is None:
        include_dcape = exact_dewpoint
    _, client, catalog, prefixes, run, times = _product_context(
        token, "ship", run_iso=run_iso
    )
    valid_time = _parse_time(valid_time_iso)
    if valid_time not in times:
        raise AromeError("La hora solicitada no está disponible en la última pasada.")

    # Sin repartir el tiempo entre fases no hay forma de saber si una hora se
    # va en traer los datos o en diagnosticarlos, y las dos se arreglan por
    # caminos distintos. Es una línea de log por hora de predicción.
    fases: dict[str, float] = {}
    reloj = time.monotonic()

    reference = client.get_field(
        catalog,
        prefixes["height_temperature"],
        run,
        valid_time,
        2.0,
        "height",
    )
    surface_temperature = _as_kelvin(reference.data, reference.units)
    levels = _pressure_levels(client, catalog, prefixes["pressure_temperature"], run)
    if 500.0 not in levels or 700.0 not in levels:
        raise AromeError("El perfil AROME no contiene los niveles 500 y 700 hPa necesarios para SHIP.")

    def fetch_surface(name: str):
        if name == "dewpoint":
            return client.get_field(catalog, prefixes["height_dewpoint"], run, valid_time, 2.0, "height")
        if name == "pressure":
            return client.get_field(catalog, prefixes["surface_pressure"], run, valid_time, None, None)
        if name == "u":
            return client.get_field(catalog, prefixes["height_u"], run, valid_time, 10.0, "height", component="u")
        if name == "v":
            return client.get_field(catalog, prefixes["height_v"], run, valid_time, 10.0, "height", component="v")
        if name == "terrain":
            terrain_prefix = prefixes.get("terrain")
            if not terrain_prefix:
                return None
            terrain_runs = catalog.by_prefix[terrain_prefix]
            terrain_run = run if run in terrain_runs else max(terrain_runs)
            return client.get_field(catalog, terrain_prefix, terrain_run, None, None, None)
        raise KeyError(name)

    def fetch_level(variable: str, level_hpa: float):
        prefix = prefixes[f"pressure_{variable}"]
        component = variable if variable in {"u", "v"} else None
        return client.get_field(
            catalog,
            prefix,
            run,
            valid_time,
            level_hpa,
            "pressure",
            component=component,
        )

    # De IP1 salen temperatura, viento y geopotencial en altura. El rocío
    # isobárico, que sólo necesita DCAPE, viaja en IP3 junto a la velocidad
    # vertical: con él no hace falta pedir un solo campo por niveles al WCS.
    package_levels = _isobaric_fields_from_package(reference, run, valid_time, levels)
    package_levels_usado = bool(package_levels)
    extras = (
        _isobaric_extras_from_package(run, valid_time, levels, ("dewpoint",))
        if package_levels and exact_dewpoint
        else None
    )
    package_dewpoint = (extras[0].get("dewpoint") if extras else None) or None
    rocio_de_ip3 = bool(package_dewpoint)
    if package_levels:
        # Con el rocío derivado de la humedad de IP1 —o el exacto de IP3— no
        # hace falta pedir nada al WCS por niveles: 24 descargas menos por hora.
        level_variables = (
            ("dewpoint",) if exact_dewpoint and not package_dewpoint else ()
        )
    else:
        level_variables = ("temperature", "dewpoint", "u", "v")

    # Rocío, presión y viento de superficie salen de SP1/SP2 cuando están
    # publicados: son cuatro descargas WCS menos por hora, cada una con su
    # turno en el estrangulador. El terreno no viaja en los paquetes y la
    # temperatura a 2 m es la referencia, así que esas dos siguen igual.
    surface_package = _surface_fields_from_package(reference, run, valid_time)
    surface_package_usado = bool(surface_package)

    fetched: dict[tuple[str, float | None], RasterField | None] = {}
    tasks: dict[Any, tuple[str, float | None]] = {}
    def throttled(function, *args):
        # La API ciblée WCS limita campos 2D y aplica cuota. Espaciar los
        # inicios evita ráfagas HTTP 429 mientras se prepara el backend de
        # paquetes GRIB2 multimensaje para producción.
        _wait_for_profile_request_slot()
        return function(*args)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="arome-profile") as executor:
        pendientes = ("terrain",) if surface_package else (
            "dewpoint", "pressure", "u", "v", "terrain"
        )
        for name in pendientes:
            tasks[executor.submit(throttled, fetch_surface, name)] = (name, None)
        for level_hpa in levels:
            for variable in level_variables:
                tasks[executor.submit(throttled, fetch_level, variable, level_hpa)] = (variable, level_hpa)
        for future in as_completed(tasks):
            fetched[tasks[future]] = future.result()

    if surface_package:
        surface_dewpoint_field = surface_package["surface_dewpoint"]
        surface_pressure_field = surface_package["surface_pressure"]
        surface_u_field = surface_package["surface_u"]
        surface_v_field = surface_package["surface_v"]
    else:
        surface_dewpoint_field = fetched[("dewpoint", None)]
        surface_pressure_field = fetched[("pressure", None)]
        surface_u_field = fetched[("u", None)]
        surface_v_field = fetched[("v", None)]
    if not all((surface_dewpoint_field, surface_pressure_field, surface_u_field, surface_v_field)):
        raise AromeError("Faltan campos de superficie para el perfil convectivo.")

    fases["traer"] = time.monotonic() - reloj
    reloj = time.monotonic()

    surface_dewpoint = _as_kelvin(_align(reference, surface_dewpoint_field), surface_dewpoint_field.units)
    surface_pressure = _as_hpa(_align(reference, surface_pressure_field), surface_pressure_field.units)
    surface_u = _align(reference, surface_u_field)
    surface_v = _align(reference, surface_v_field)
    terrain_field = fetched.get(("terrain", None))
    terrain = _align(reference, terrain_field) if terrain_field is not None else np.zeros_like(surface_temperature)
    terrain = np.where(np.isfinite(terrain), terrain, 0.0)

    shape = surface_temperature.shape
    pressure_layers = [surface_pressure]
    temperature_layers = [surface_temperature]
    dewpoint_layers = [surface_dewpoint]
    u_layers = [surface_u]
    v_layers = [surface_v]
    for level_hpa in levels:
        if package_levels:
            temperature_field = package_levels["temperature"][level_hpa]
            u_field = package_levels["u"][level_hpa]
            v_field = package_levels["v"][level_hpa]
        else:
            temperature_field = fetched[("temperature", level_hpa)]
            u_field = fetched[("u", level_hpa)]
            v_field = fetched[("v", level_hpa)]
        if package_dewpoint and level_hpa in package_dewpoint:
            dewpoint_field = package_dewpoint[level_hpa]
        elif package_levels and not exact_dewpoint:
            humedad = package_levels["relative_humidity"][level_hpa]
            dewpoint_field = RasterField(
                _dewpoint_from_relative_humidity_c(
                    np.asarray(temperature_field.data, dtype=float),
                    np.asarray(humedad.data, dtype=float),
                ),
                temperature_field.transform,
                temperature_field.crs,
                temperature_field.bounds,
                "C",
            )
        else:
            dewpoint_field = fetched[("dewpoint", level_hpa)]
        assert temperature_field and dewpoint_field and u_field and v_field
        temperature = _as_kelvin(_align(reference, temperature_field), temperature_field.units)
        dewpoint = _as_kelvin(_align(reference, dewpoint_field), dewpoint_field.units)
        u_value = _align(reference, u_field)
        v_value = _align(reference, v_field)
        fixed_pressure = np.full(shape, level_hpa, dtype=float)
        below_ground = fixed_pressure >= surface_pressure
        pressure_layers.append(np.where(below_ground, surface_pressure, fixed_pressure))
        temperature_layers.append(np.where(below_ground, surface_temperature, temperature))
        dewpoint_layers.append(np.where(below_ground, surface_dewpoint, dewpoint))
        u_layers.append(np.where(below_ground, surface_u, u_value))
        v_layers.append(np.where(below_ground, surface_v, v_value))

    pressure = np.stack(pressure_layers)
    temperature = np.stack(temperature_layers)
    dewpoint = np.minimum(np.stack(dewpoint_layers), temperature)
    u_profile = np.stack(u_layers)
    v_profile = np.stack(v_layers)

    # Los campos descargados y las capas sueltas ya viven dentro de los
    # perfiles apilados. Mantenerlos vivos durante el diagnóstico duplicaba el
    # volumen completo en memoria, y el proceso moría por falta de ella.
    pressure_layers = temperature_layers = dewpoint_layers = None
    u_layers = v_layers = None
    surface_dewpoint_field = surface_pressure_field = None
    surface_u_field = surface_v_field = terrain_field = None
    fetched.clear()
    # Los campos del paquete tambien: son cinco elementos por cada uno de los
    # 24 niveles, y su contenido ya esta dentro de los perfiles apilados.
    if package_levels:
        for campos in package_levels.values():
            campos.clear()
        package_levels = None
    if surface_package:
        surface_package.clear()
        surface_package = None
    # El rocío de IP3 ya está dentro de los perfiles apilados. Mantenerlo vivo
    # durante el diagnóstico eran 150 MB por perfil, y DCAPE es el que menos
    # margen tiene.
    if package_dewpoint:
        package_dewpoint.clear()
    package_dewpoint = extras = None

    # Los perfiles apilados rondan el giga y sólo se leen. Apartarlos al disco
    # los saca de la memoria que el núcleo no puede recuperar; hay que soltar
    # las referencias locales o seguirían ocupando sitio además del fichero.
    fases["montar"] = time.monotonic() - reloj
    reloj = time.monotonic()

    apilados = [pressure, temperature, dewpoint, u_profile, v_profile]
    del pressure, temperature, dewpoint, u_profile, v_profile
    with _profiles_spilled_to_disk(apilados) as perfiles:
        apilados = None
        outputs = _convective_outputs_in_stripes(
            *perfiles,
            terrain, surface_u, surface_v, levels,
            include_dcape=include_dcape,
            # Cuando sólo se pide DCAPE no hay que rehacer las tres parcelas:
            # de eso se encargó el nivel anterior.
            only_dcape=only_dcape,
        )
        perfiles = None
    fases["diagnosticar"] = time.monotonic() - reloj
    # El pico del propio proceso, que es el dato que falta: la memoria que se
    # registra al lanzar mide antes de que el perfil crezca, así que nunca ve
    # el máximo. Con seis a la vez, la diferencia entre uno y otro es de más
    # del doble.
    pico_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024 if sys.platform == "linux" else 1024**2
    )
    logger.info(
        "Perfil convectivo %s: traer %.0f s, montar %.0f s, diagnosticar %.0f s "
        "(paquete isobárico: %s, rocío isobárico: %s, superficie: %s, DCAPE: %s"
        ", pico %.1f GB).",
        valid_time_iso,
        fases["traer"], fases["montar"], fases["diagnosticar"],
        "sí" if package_levels_usado else "no",
        "IP3" if rocio_de_ip3 else ("derivado" if package_levels_usado else "WCS"),
        "sí" if surface_package_usado else "no",
        "sí" if include_dcape else "no",
        pico_mb / 1024,
    )
    common = (reference.transform, reference.crs, reference.bounds)
    frames = {
        "mucape-muli": RasterField(
            outputs["mucape"],
            *common,
            "J/kg",
            overlay=outputs["muli"],
            overlay_units="°C",
        ),
        "mlcape-mlli": RasterField(
            outputs["mlcape"],
            *common,
            "J/kg",
            overlay=outputs["mlli"],
            overlay_units="°C",
        ),
        "sbcape-sbli": RasterField(
            outputs["sbcape"],
            *common,
            "J/kg",
            overlay=outputs["sbli"],
            overlay_units="°C",
        ),
        "dcape": RasterField(outputs["dcape"], *common, "J/kg"),
        "ordinary-cell-motion": RasterField(
            outputs["cell_speed"],
            *common,
            "m/s",
            vector_u=outputs["cell_u"],
            vector_v=outputs["cell_v"],
        ),
        "ebwd": RasterField(outputs["ebwd"], *common, "m/s", vector_u=outputs["ebwd_u"], vector_v=outputs["ebwd_v"]),
        "ship": RasterField(outputs["ship"], *common, ""),
    }
    return frames, run


# Guarda campos sin serializar, a 6,4 MB cada uno.
def _level_difference_field(
    client, catalog, prefixes, config, run: datetime, valid_time: datetime
) -> RasterField:
    """Diferencia de un campo entre dos niveles isobáricos.

    Es lo que necesita el Vertical Totals: T850 menos T500. Los dos niveles
    viajan en IP1, que ya se descarga para los perfiles, así que sólo se acude
    al WCS cuando el paquete todavía no está publicado.
    """
    bajo = float(config["lower_level"])
    alto = float(config["upper_level"])
    paquete = _isobaric_fields_from_package(None, run, valid_time, [bajo, alto])
    temperaturas = (paquete or {}).get("temperature") or {}
    if bajo in temperaturas and alto in temperaturas:
        campo_bajo, campo_alto = temperaturas[bajo], temperaturas[alto]
    else:
        campo_bajo = client.get_field(
            catalog, prefixes["field"], run, valid_time, bajo, "pressure"
        )
        campo_alto = client.get_field(
            catalog, prefixes["field"], run, valid_time, alto, "pressure"
        )
    # En kelvin o en grados la diferencia es la misma; se normaliza para no
    # depender de en cuál venga cada uno.
    valores = _as_kelvin(
        np.asarray(campo_bajo.data, dtype=float), campo_bajo.units
    ) - _as_kelvin(_align(campo_bajo, campo_alto), campo_alto.units)
    return RasterField(
        valores,
        campo_bajo.transform,
        campo_bajo.crs,
        campo_bajo.bounds,
        str(config["unit"]),
    )


@lru_cache(maxsize=12)
def _computed_frame(
    token: str,
    product_id: str,
    valid_time_iso: str,
    vertical_kind: str = "height",
    level: float = 10.0,
    run_iso: str = "",
):
    """Calcula un campo y conserva la matriz nativa en memoria."""
    reloj = time.monotonic()
    config, client, catalog, prefixes, run, times = _product_context(
        token, product_id, vertical_kind, run_iso
    )
    catalogo_s = time.monotonic() - reloj
    if catalogo_s > 3.0:
        # El catálogo se rehace en cada proceso aislado: si pesa, se nota 242
        # veces por pasada y conviene verlo separado de la descarga del campo.
        logger.info(
            "Catálogo del WCS para %s: %.1f s.", product_id, catalogo_s
        )
    valid_time = _parse_time(valid_time_iso)
    if valid_time not in times:
        raise AromeError("La hora solicitada no está disponible en la última pasada.")
    if config["kind"] == "convective":
        # Solo DCAPE necesita el rocío que publica el modelo; los otros trece
        # se resuelven con el derivado del paquete y no esperan esas descargas.
        frames, diagnostic_run = _convective_frames(
            token,
            valid_time_iso,
            run_iso,
            exact_dewpoint=product_id == "dcape" and DCAPE_EXACT_DEWPOINT,
            include_dcape=product_id == "dcape",
            only_dcape=product_id == "dcape",
        )
        field = frames[product_id]
        run = diagnostic_run
    elif config["kind"] == "level_difference":
        field = _level_difference_field(
            client, catalog, prefixes, config, run, valid_time
        )
    elif config["kind"] == "wind":
        vertical_mode = "height" if vertical_kind == "height" else "pressure"
        u_field = client.get_field(
            catalog, prefixes["u"], run, valid_time, level, vertical_mode, component="u"
        )
        v_field = client.get_field(
            catalog, prefixes["v"], run, valid_time, level, vertical_mode, component="v"
        )
        from tabs.arome_forecast import _align

        vector_v = _align(u_field, v_field)
        u_field.vector_u = np.asarray(u_field.data, dtype=float)
        u_field.vector_v = vector_v
        u_field.data = np.hypot(u_field.vector_u, vector_v)
        if vertical_kind == "isobaric":
            surface_pressure = client.get_field(
                catalog,
                prefixes["surface_pressure"],
                run,
                valid_time,
                None,
                None,
            )
            pressure_data = _align(u_field, surface_pressure)
            finite_pressure = pressure_data[np.isfinite(pressure_data)]
            if finite_pressure.size and float(np.nanmedian(finite_pressure)) > 2_000:
                pressure_data = pressure_data / 100.0
            above_ground = pressure_data >= float(level)
            u_field.data = np.where(above_ground, u_field.data, np.nan)
            u_field.vector_u = np.where(above_ground, u_field.vector_u, np.nan)
            u_field.vector_v = np.where(above_ground, u_field.vector_v, np.nan)
        u_field.units = "m/s"
        field = u_field
    elif config["kind"] == "native":
        native_level = config.get("level")
        native_vertical_kind = config.get("vertical_kind")
        field_times = [valid_time]
        if config.get("accumulate_from_run"):
            # TOTAL_PRECIPITATION PT1H es un incremento horario. Para el
            # acumulado del RUN sumamos H+01..H+n sobre la misma rejilla; H+00
            # no pertenece al periodo de predicción iniciado por esta pasada.
            field_times = [value for value in times if run < value <= valid_time]
            if not field_times:
                field_times = [valid_time]
        field = client.get_field(
            catalog,
            prefixes["field"],
            run,
            field_times[0],
            float(native_level) if native_level is not None else None,
            str(native_vertical_kind) if native_vertical_kind else None,
            period=str(config["period"]) if config.get("period") else None,
        )
        if config.get("accumulate_from_run"):
            from tabs.arome_forecast import _align

            accumulated = np.maximum(np.asarray(field.data, dtype=float), 0.0)
            if valid_time <= run:
                accumulated = np.zeros_like(accumulated)
            else:
                for increment_time in field_times[1:]:
                    increment = client.get_field(
                        catalog,
                        prefixes["field"],
                        run,
                        increment_time,
                        float(native_level) if native_level is not None else None,
                        str(native_vertical_kind) if native_vertical_kind else None,
                        period=str(config["period"]),
                    )
                    accumulated += np.maximum(_align(field, increment), 0.0)
            field.data = accumulated
        values = np.asarray(field.data, dtype=float)
        value_mode = str(config.get("value_mode", "nonnegative"))
        if value_mode == "temperature_c":
            values = _as_kelvin(values, field.units) - 273.15
        elif value_mode == "percent":
            finite_values = values[np.isfinite(values)]
            if finite_values.size and float(np.nanmax(np.abs(finite_values))) <= 1.5:
                values = values * 100.0
            values = np.clip(values, 0.0, 100.0)
        else:
            values = np.maximum(values, 0.0)
        values = values * float(config.get("scale", 1.0))
        field.data = values
        field.units = str(config["unit"])
    else:
        # Mismo reparto que llevan los perfiles y los mapas nativos: sin él, el
        # nivel 1 era el único tramo de la pasada del que no se sabía en qué se
        # iba el tiempo.
        reloj_ciz = time.monotonic()
        base_uv = _surface_wind_10m(client, catalog, prefixes, run, valid_time)
        # La de 0-6 km interpola sobre niveles isobáricos, que ya vienen en el
        # paquete; las de 0-1 y 0-3 usan niveles de altura y siguen por el WCS.
        isobaric_levels = None
        if int(config["depth_m"]) == 6000:
            isobaric_levels = _shear_levels_from_package(
                base_uv[0], run, valid_time, (500.0, 450.0, 400.0, 350.0, 300.0, 250.0)
            )
        traer_ciz = time.monotonic() - reloj_ciz
        reloj_ciz = time.monotonic()
        field = _compute_shear(
            client,
            catalog,
            prefixes,
            run,
            valid_time,
            int(config["depth_m"]),
            base_uv=base_uv,
            isobaric_levels=isobaric_levels,
        )
        logger.info(
            "Cizalladura %s %s: traer %.1f s, calcular %.1f s (niveles del "
            "paquete: %s).",
            product_id, valid_time_iso, traer_ciz, time.monotonic() - reloj_ciz,
            "sí" if isobaric_levels else "no",
        )
    finite = field.data[np.isfinite(field.data)]
    maximum = float(np.nanmax(finite)) if finite.size else float("nan")
    headers = {
        "X-AROME-Run": run.isoformat().replace("+00:00", "Z"),
        "X-AROME-Valid-Time": valid_time.isoformat().replace("+00:00", "Z"),
        "X-AROME-Max": f"{maximum:.3f}",
        "X-AROME-Unit": str(config["unit"]),
    }
    if config["kind"] == "wind":
        headers["X-AROME-Level"] = f"{level:g}"
        headers["X-AROME-Level-Type"] = vertical_kind
    return field, config, headers


@lru_cache(maxsize=32)
def frame_png(
    token: str,
    product_id: str,
    valid_time_iso: str,
    vertical_kind: str = "height",
    level: float = 10.0,
    run_iso: str = "",
) -> tuple[bytes, dict[str, str]]:
    """Render PNG de compatibilidad para clientes sin Canvas interactivo."""
    field, config, headers = _computed_frame(
        token, product_id, valid_time_iso, vertical_kind, level, run_iso
    )
    return _render_png(field, float(config["vmax"])), headers


GRID_FORMAT_VERSION = 3
QUANTIZATION_LEVELS = 4096
MAX_QUANTIZATION_CODE = 65534


def _quantization_step(span: float) -> float:
    """Mayor paso 1/2/5·10^k que divide el rango del producto en ≥4096 niveles.

    Al reducir el número de códigos distintos el plano de bytes altos queda casi
    constante, y ahí está la ganancia frente a Float32, cuya mantisa es
    prácticamente ruido incompresible. Medido sobre un frame real de viento:
    un tercio del tamaño, con el valor del tooltip intacto en el 97,6 % de las
    celdas y un error máximo de 0,007 m/s.
    """
    if not np.isfinite(span) or span <= 0:
        return 1.0
    target = span / QUANTIZATION_LEVELS
    base = 10.0 ** math.floor(math.log10(target))
    for factor in (5.0, 2.0, 1.0):
        if factor * base <= target:
            return factor * base
    return base


def _quantize_array(array: np.ndarray) -> tuple[bytes, dict[str, Any]]:
    """Codifica a uint16 con planos de byte separados; 0 marca «sin dato».

    Cada matriz se escala por su propio rango: el overlay (índice de elevación)
    no comparte magnitud con el escalar que acompaña, y heredar su paso
    deformaría los contornos.

    Separar el byte alto del bajo agrupa los bytes suaves y deja el ruido de
    baja magnitud en un bloque aparte, que gzip comprime mucho mejor que la
    secuencia intercalada.
    """
    finite = np.isfinite(array)
    if not finite.any():
        codes = np.zeros(array.shape, dtype="<u2")
        return codes.tobytes(), {"offset": 0.0, "step": 1.0}
    offset = float(np.nanmin(array))
    span = float(np.nanmax(array)) - offset
    step = _quantization_step(span)
    # Un rango muy amplio no cabe en 16 bits con el paso preferido.
    if span / step > MAX_QUANTIZATION_CODE - 1:
        step = span / (MAX_QUANTIZATION_CODE - 1)
    codes = np.zeros(array.shape, dtype="<u2")
    codes[finite] = 1 + np.round((array[finite] - offset) / step).astype("<u2")
    # Se emiten los bytes altos y luego los bajos, en ese orden explícito, para
    # que el visor no dependa del orden de bytes de la máquina que sirvió.
    high = (codes >> 8).astype("u1")
    low = (codes & 0xFF).astype("u1")
    return high.tobytes(order="C") + low.tobytes(order="C"), {
        "offset": offset,
        "step": step,
    }


# Ya serializado y comprimido: barato de guardar, pero tampoco sin límite.
@lru_cache(maxsize=32)
def frame_grid(
    token: str,
    product_id: str,
    valid_time_iso: str,
    vertical_kind: str = "height",
    level: float = 10.0,
    run_iso: str = "",
) -> tuple[bytes, dict[str, str]]:
    """Serializa la rejilla nativa: cabecera JSON + matrices uint16 cuantizadas."""
    # El mismo reparto que llevan los perfiles convectivos. Sin él no se sabe
    # si una hora de mapa nativo se va en pedir el dato o en prepararlo, y son
    # 242 trabajos por pasada: el nivel 0 entero ronda la media hora.
    reloj = time.monotonic()
    field, config, headers = _computed_frame(
        token, product_id, valid_time_iso, vertical_kind, level, run_iso
    )
    traer = time.monotonic() - reloj
    reloj = time.monotonic()
    cuerpo = _serialize_grid(product_id, field, config, headers)
    # Sólo los que de verdad traen un campo: los convectivos y las
    # cizalladuras pasan por aquí, pero su tiempo ya lo reparte su propia
    # traza, y llamarlos «nativos» hacía leer 170 s de perfil como si fuera
    # una descarga.
    if (PRODUCTS.get(product_id) or {}).get("kind") in {"native", "level_difference"}:
        logger.info(
            "Mapa %s %s: traer %.1f s, serializar %.1f s.",
            product_id, valid_time_iso, traer, time.monotonic() - reloj,
        )
    return cuerpo, headers


def accumulated_precip_series(
    token: str,
    valid_times: tuple[str, ...],
    run_iso: str = "",
    stored_increment: Callable[[str], np.ndarray | None] | None = None,
) -> Iterator[tuple[str, bytes, dict[str, str]]]:
    """Acumulado de precipitación de varias horas con una descarga por hora.

    Resolver cada hora por separado obliga a rebajar de nuevo todos los
    incrementos anteriores, lo que hace el número de peticiones cuadrático
    (1.326 para una pasada de 51 horas en lugar de 51). Aquí se recorren las
    horas en orden llevando la suma acumulada.

    El resultado es el mismo que el del camino por hora: se recortan los
    negativos incremento a incremento y todos se alinean sobre la rejilla del
    primer campo, igual que hacía `_computed_frame`.

    `stored_increment` permite recuperar una hora ya calculada en vez de
    volver a pedirla: el mapa horario de lluvia sale del mismo campo del WCS y
    se publica antes, así que cuando llega el acumulado esas horas ya están en
    disco. La primera siempre se descarga, porque de ella salen la rejilla y la
    proyección sobre las que se alinea el resto.
    """
    product_id = "accumulated-precip"
    config, client, catalog, prefixes, run, times = _product_context(
        token, product_id, run_iso=run_iso
    )
    requested = {_parse_time(value) for value in valid_times}
    increments = [value for value in times if value > run and value in requested]
    if not increments:
        return
    # La serie necesita cada hora desde la pasada, aunque no todas se publiquen.
    horizon = max(increments)
    ordered = [value for value in times if run < value <= horizon]

    reference: RasterField | None = None
    accumulated: np.ndarray | None = None
    reutilizados = 0
    for valid_time in ordered:
        guardado = None
        if reference is not None and stored_increment is not None:
            guardado = stored_increment(
                valid_time.isoformat().replace("+00:00", "Z")
            )
            # Sólo sirve si cubre la misma rejilla; si no, se descarga.
            if guardado is not None and guardado.shape != reference.data.shape:
                guardado = None
        if guardado is not None:
            reutilizados += 1
            accumulated = accumulated + np.maximum(guardado, 0.0)
        else:
            increment = client.get_field(
                catalog,
                prefixes["field"],
                run,
                valid_time,
                None,
                None,
                period=str(config["period"]),
            )
            if reference is None:
                reference = increment
                accumulated = np.maximum(np.asarray(increment.data, dtype=float), 0.0)
            else:
                accumulated = accumulated + np.maximum(_align(reference, increment), 0.0)
        if valid_time not in requested:
            continue
        frame = RasterField(
            accumulated * float(config.get("scale", 1.0)),
            reference.transform,
            reference.crs,
            reference.bounds,
            str(config["unit"]),
        )
        finite = frame.data[np.isfinite(frame.data)]
        headers = {
            "X-AROME-Run": run.isoformat().replace("+00:00", "Z"),
            "X-AROME-Valid-Time": valid_time.isoformat().replace("+00:00", "Z"),
            "X-AROME-Max": f"{float(np.nanmax(finite)) if finite.size else float('nan'):.3f}",
            "X-AROME-Unit": str(config["unit"]),
        }
        yield (
            headers["X-AROME-Valid-Time"],
            _serialize_grid(product_id, frame, config, headers),
            headers,
        )
    if reutilizados:
        logger.info(
            "Acumulado: %d de %d horas reutilizadas del mapa horario ya "
            "publicado, sin volver a pedirlas.",
            reutilizados, len(ordered),
        )


def stored_grid_values(content: bytes) -> np.ndarray | None:
    """Deshace el empaquetado de un frame guardado y devuelve su escalar.

    Es el inverso de `_serialize_grid` para el caso simple —un solo array, sin
    vectores ni overlay—, que es el de los productos nativos. Devuelve None si
    el frame no encaja en ese caso, para que quien llame vuelva a descargarlo
    en vez de interpretar mal unos bytes.
    """
    if len(content) < 4:
        return None
    try:
        largo = struct.unpack("<I", content[:4])[0]
        metadatos = json.loads(content[4 : 4 + largo])
    except (struct.error, ValueError):
        return None
    if metadatos.get("encoding") != "u16-planes":
        return None
    nombres = metadatos.get("array_order") or []
    if "value" not in nombres:
        # value_source «hypot»: el escalar se reconstruye de u y v, no está.
        return None
    indice = nombres.index("value")
    alto_px, ancho = int(metadatos["height"]), int(metadatos["width"])
    plano = alto_px * ancho
    inicio = 4 + largo + indice * plano * 2
    if len(content) < inicio + plano * 2:
        return None
    altos = np.frombuffer(content, dtype="u1", count=plano, offset=inicio)
    bajos = np.frombuffer(content, dtype="u1", count=plano, offset=inicio + plano)
    codigos = (altos.astype("<u2") << 8) | bajos
    escala = metadatos["arrays"][indice]
    valores = np.full(plano, np.nan, dtype=float)
    # El código 0 marca «sin dato»; el resto van desplazados en uno.
    vivos = codigos > 0
    valores[vivos] = (
        float(escala["offset"]) + (codigos[vivos] - 1) * float(escala["step"])
    )
    return valores.reshape(alto_px, ancho)


def _serialize_grid(
    product_id: str,
    field: RasterField,
    config: dict[str, Any],
    headers: dict[str, str],
) -> bytes:
    """Empaqueta un campo ya calculado en el formato de rejilla del visor."""
    calculation_scope = forecast_calculation_scope()
    if calculation_scope == "catalonia":
        geometry = _catalonia_geometry(
            _catalonia_only_geojson(_load_forecast_regions_geojson())
        )
        values = np.asarray(_mask_to_catalonia(field, geometry), dtype="<f4")
        boundary_bounds = AROME_MODEL_GRID_BOUNDS
    else:
        # El dominio completo no necesita los GeoJSON en memoria: sus fronteras
        # se sirven ya recortadas desde la caché.
        boundary_bounds = tuple(float(value) for value in field.bounds)
        values = np.asarray(field.data, dtype="<f4")
    inside = np.isfinite(values)
    arrays = [values]
    has_vectors = field.vector_u is not None and field.vector_v is not None
    if has_vectors:
        vector_u = np.where(inside, field.vector_u, np.nan).astype("<f4")
        vector_v = np.where(inside, field.vector_v, np.nan).astype("<f4")
        arrays.extend((vector_u, vector_v))
    has_overlay = field.overlay is not None
    if has_overlay:
        overlay = np.where(inside, field.overlay, np.nan).astype("<f4")
        arrays.append(overlay)

    output_bounds = tuple(float(value) for value in field.bounds)
    if calculation_scope == "catalonia":
        arrays = [
            _place_local_array_in_model_grid(array, output_bounds)
            for array in arrays
        ]
        values = arrays[0]
        output_bounds = AROME_MODEL_GRID_BOUNDS

    height, width = values.shape
    west, south, east, north = output_bounds
    # Cuando el escalar es el módulo del vector, el visor lo reconstruye y así
    # se ahorra un tercio del cuerpo sin ninguna pérdida.
    names = [
        "value",
        *(["u", "v"] if has_vectors else []),
        *(["overlay"] if has_overlay else []),
    ]
    value_source = None
    if has_vectors:
        finite = np.isfinite(arrays[0])
        modulus = np.hypot(arrays[1], arrays[2])
        if np.allclose(arrays[0][finite], modulus[finite], rtol=0, atol=1e-4):
            value_source = "hypot"
            arrays = arrays[1:]
            names = names[1:]

    encoded_arrays = []
    body_chunks = []
    for name, array in zip(names, arrays):
        chunk, scale = _quantize_array(array)
        body_chunks.append(chunk)
        encoded_arrays.append({"name": name, **scale})

    metadata = {
        "version": GRID_FORMAT_VERSION,
        "encoding": "u16-planes",
        "arrays": encoded_arrays,
        "value_source": value_source,
        "product": product_id,
        "width": width,
        "height": height,
        "bounds": [west, south, east, north],
        "vmin": float(config.get("vmin", 0.0)),
        "vmax": float(config["vmax"]),
        "unit": str(config["unit"]),
        "run": headers["X-AROME-Run"],
        "valid_time": headers["X-AROME-Valid-Time"],
        "maximum": float(headers["X-AROME-Max"]),
        "vertical_kind": headers.get("X-AROME-Level-Type"),
        "level": float(headers["X-AROME-Level"]) if "X-AROME-Level" in headers else None,
        "calculation_scope": calculation_scope,
        # Las fronteras ya no viajan aquí: eran los mismos 293 KB repetidos en
        # cada frame, un cuarto del volumen y del tráfico. El visor las pide
        # una vez por dominio y las reutiliza.
        "boundary_scope": calculation_scope,
        "has_vectors": has_vectors,
        "has_overlay": has_overlay,
        "overlay_unit": field.overlay_units if has_overlay else None,
        "array_order": names,
    }
    encoded_header = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    body = bytearray(struct.pack("<I", len(encoded_header)))
    body.extend(encoded_header)
    for chunk in body_chunks:
        body.extend(chunk)
    return bytes(body)
