"""Adaptador entre los diagnósticos AROME y la API del visor Svelte."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import threading
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, mapping, shape

from server.services.convective_diagnostics import (
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
    _load_forecast_regions_geojson,
    _mask_to_catalonia,
    _resolved_prefixes,
    forecast_calculation_scope,
)


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
    "cloud-cover": {
        "kind": "native", "prefix_kind": "total_cloud_cover",
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
    if config["kind"] == "native":
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
    run = max(common_runs)
    times = []
    for candidate in sorted(common_runs, reverse=True):
        reference = catalog.coverage_id(required[0], candidate, period=period)
        candidate_times = client.describe(reference).valid_times(candidate)
        if not times:
            run, times = candidate, candidate_times
        if len(candidate_times) >= 12:
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


_PROFILE_THROTTLE_LOCK = threading.Lock()


def _wait_for_profile_request_slot() -> None:
    """Limita globalmente WCS incluso con varios perfiles en procesos distintos."""
    interval = max(
        0.1,
        float(os.getenv("METEOLABX_AROME_PROFILE_REQUEST_INTERVAL_S", "1.1")),
    )
    lock_path = Path(
        os.getenv(
            "METEOLABX_AROME_PROFILE_THROTTLE_FILE",
            "/tmp/meteolabx-arome-profile-throttle",
        )
    )
    with _PROFILE_THROTTLE_LOCK:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Railway y desarrollo son Unix
            time.sleep(interval)
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="ascii") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw_next = handle.read().strip()
                next_request = float(raw_next) if raw_next else 0.0
                delay = next_request - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                handle.seek(0)
                handle.truncate()
                handle.write(str(time.monotonic() + interval))
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=32)
def _convective_frames(
    token: str, valid_time_iso: str, run_iso: str = ""
) -> tuple[dict[str, RasterField], datetime]:
    """Descarga un perfil común y reutiliza todos sus diagnósticos convectivos."""
    _, client, catalog, prefixes, run, times = _product_context(
        token, "ship", run_iso=run_iso
    )
    valid_time = _parse_time(valid_time_iso)
    if valid_time not in times:
        raise AromeError("La hora solicitada no está disponible en la última pasada.")

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

    fetched: dict[tuple[str, float | None], RasterField | None] = {}
    tasks: dict[Any, tuple[str, float | None]] = {}
    def throttled(function, *args):
        # La API ciblée WCS limita campos 2D y aplica cuota. Espaciar los
        # inicios evita ráfagas HTTP 429 mientras se prepara el backend de
        # paquetes GRIB2 multimensaje para producción.
        _wait_for_profile_request_slot()
        return function(*args)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="arome-profile") as executor:
        for name in ("dewpoint", "pressure", "u", "v", "terrain"):
            tasks[executor.submit(throttled, fetch_surface, name)] = (name, None)
        for level_hpa in levels:
            for variable in ("temperature", "dewpoint", "u", "v"):
                tasks[executor.submit(throttled, fetch_level, variable, level_hpa)] = (variable, level_hpa)
        for future in as_completed(tasks):
            fetched[tasks[future]] = future.result()

    surface_dewpoint_field = fetched[("dewpoint", None)]
    surface_pressure_field = fetched[("pressure", None)]
    surface_u_field = fetched[("u", None)]
    surface_v_field = fetched[("v", None)]
    if not all((surface_dewpoint_field, surface_pressure_field, surface_u_field, surface_v_field)):
        raise AromeError("Faltan campos de superficie para el perfil convectivo.")

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
        temperature_field = fetched[("temperature", level_hpa)]
        dewpoint_field = fetched[("dewpoint", level_hpa)]
        u_field = fetched[("u", level_hpa)]
        v_field = fetched[("v", level_hpa)]
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
    height = hypsometric_height_profile_m(pressure, temperature, dewpoint, terrain)
    diagnostics = diagnose_convection(pressure, temperature, dewpoint, height)

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
    cell_speed = np.hypot(cell_u, cell_v)

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

    common = (reference.transform, reference.crs, reference.bounds)
    frames = {
        "mucape-muli": RasterField(
            diagnostics.mucape,
            *common,
            "J/kg",
            overlay=diagnostics.muli,
            overlay_units="°C",
        ),
        "mlcape-mlli": RasterField(
            diagnostics.mlcape,
            *common,
            "J/kg",
            overlay=diagnostics.mlli,
            overlay_units="°C",
        ),
        "sbcape-sbli": RasterField(
            diagnostics.sbcape,
            *common,
            "J/kg",
            overlay=diagnostics.sbli,
            overlay_units="°C",
        ),
        "dcape": RasterField(diagnostics.dcape, *common, "J/kg"),
        "ordinary-cell-motion": RasterField(
            cell_speed,
            *common,
            "m/s",
            vector_u=cell_u,
            vector_v=cell_v,
        ),
        "ebwd": RasterField(ebwd, *common, "m/s", vector_u=ebwd_u, vector_v=ebwd_v),
        "ship": RasterField(ship, *common, ""),
    }
    return frames, run


@lru_cache(maxsize=96)
def _computed_frame(
    token: str,
    product_id: str,
    valid_time_iso: str,
    vertical_kind: str = "height",
    level: float = 10.0,
    run_iso: str = "",
):
    """Calcula un campo y conserva la matriz nativa en memoria."""
    config, client, catalog, prefixes, run, times = _product_context(
        token, product_id, vertical_kind, run_iso
    )
    valid_time = _parse_time(valid_time_iso)
    if valid_time not in times:
        raise AromeError("La hora solicitada no está disponible en la última pasada.")
    if config["kind"] == "convective":
        frames, diagnostic_run = _convective_frames(token, valid_time_iso, run_iso)
        field = frames[product_id]
        run = diagnostic_run
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
        field = _compute_shear(
            client, catalog, prefixes, run, valid_time, int(config["depth_m"])
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


@lru_cache(maxsize=96)
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


@lru_cache(maxsize=96)
def frame_grid(
    token: str,
    product_id: str,
    valid_time_iso: str,
    vertical_kind: str = "height",
    level: float = 10.0,
    run_iso: str = "",
) -> tuple[bytes, dict[str, str]]:
    """Serializa la rejilla nativa: cabecera JSON + matrices Float32 LE."""
    field, config, headers = _computed_frame(
        token, product_id, valid_time_iso, vertical_kind, level, run_iso
    )
    regions_geojson = _load_forecast_regions_geojson()
    calculation_scope = forecast_calculation_scope()
    if calculation_scope == "catalonia":
        catalonia_geojson = _catalonia_only_geojson(regions_geojson)
        geometry = _catalonia_geometry(catalonia_geojson)
        values = np.asarray(_mask_to_catalonia(field, geometry), dtype="<f4")
        visible_geojson = _model_boundary_geojson(
            regions_geojson, AROME_MODEL_GRID_BOUNDS
        )
    else:
        visible_geojson = _model_boundary_geojson(regions_geojson, field.bounds)
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
    metadata = {
        "version": 1,
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
        "boundaries": _boundary_payload(visible_geojson),
        "has_vectors": has_vectors,
        "has_overlay": has_overlay,
        "overlay_unit": field.overlay_units if has_overlay else None,
        "array_order": [
            "value",
            *(["u", "v"] if has_vectors else []),
            *(["overlay"] if has_overlay else []),
        ],
    }
    encoded_header = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    body = bytearray(struct.pack("<I", len(encoded_header)))
    body.extend(encoded_header)
    for array in arrays:
        body.extend(array.tobytes(order="C"))
    return bytes(body), headers
