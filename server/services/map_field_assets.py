"""Pregenera las texturas de los mapas de valores para Streamlit.

FastAPI y Streamlit viven en el mismo contenedor de Railway. El job del
ranking puede escribir por tanto las dos mitades WebGL de cada campo en
``static/`` y publicar después un manifiesto atómico. El navegador consume
esos archivos directamente; ningún usuario paga la interpolación ni el ciclo
PNG mundial -> descarga -> recorte -> recompresión.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable


logger = logging.getLogger(__name__)
# Pillow registra cada bloque PNG a nivel DEBUG. Es útil al depurar imágenes
# corruptas, pero ensucia el progreso normal de la pregeneración del ranking.
logging.getLogger("PIL.PngImagePlugin").setLevel(logging.WARNING)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
MANIFEST_PATH = STATIC_DIR / "map_field_assets.json"
MANIFEST_VERSION = 1
FIELD_BOUNDS = (-180.0, -60.0, 180.0, 85.0)
HALF_BOUNDS = (
    (-180.0, -60.0, 0.0, 85.0),
    (0.0, -60.0, 180.0, 85.0),
)

_BUILD_LOCK = threading.Lock()


def _temperature_png(points: Iterable[tuple[float, float, float]]) -> bytes:
    from server.services.temperature_field import interpolate_grid, render_global_grid_png

    values, mask = interpolate_grid(points)
    return render_global_grid_png(
        values.astype("float32", copy=False),
        mask.astype("float16", copy=False),
    )


def _wind_png(points: Iterable[tuple[float, float, float]]) -> bytes:
    from server.services.temperature_field import interpolate_grid, render_global_grid_png
    from server.services.wind_field import BAND_SIZE_KMH, COLOR_STOPS

    values, mask = interpolate_grid(points)
    return render_global_grid_png(
        values.astype("float32", copy=False),
        mask.astype("float16", copy=False),
        color_stops=COLOR_STOPS,
        band_size=BAND_SIZE_KMH,
    )


def _precipitation_png(points: Iterable[tuple[float, float, float]]) -> bytes:
    from server.services.precipitation_field import (
        BAND_SIZE_MM,
        COLOR_STOPS,
        interpolate_precipitation_grid,
    )
    from server.services.temperature_field import render_global_grid_png

    values, mask = interpolate_precipitation_grid(points)
    return render_global_grid_png(
        values.astype("float32", copy=False),
        mask.astype("float16", copy=False),
        color_stops=COLOR_STOPS,
        band_size=BAND_SIZE_MM,
        preserve_mask_alpha=True,
    )


def _mode_specs(store: Any) -> tuple[dict[str, Any], ...]:
    from server.services.precipitation_field import (
        COLOR_SCALE_VERSION as precipitation_palette,
        FIELD_ALGORITHM_VERSION as precipitation_algorithm,
    )
    from server.services.temperature_field import (
        COLOR_SCALE_VERSION as temperature_palette,
        FIELD_ALGORITHM_VERSION as temperature_algorithm,
    )
    from server.services.wind_field import (
        COLOR_SCALE_VERSION as wind_palette,
        FIELD_ALGORITHM_VERSION as wind_algorithm,
    )

    return (
        {
            "mode": "temperature",
            "label": "temperatura",
            "prefix": "temperature_field",
            "identity_prefix": "field",
            "algorithm": temperature_algorithm,
            "palette": temperature_palette,
            "points": store.current_temperature_points,
            "renderer": _temperature_png,
        },
        {
            "mode": "wind",
            "label": "viento",
            "prefix": "wind_field",
            "identity_prefix": "wind-field",
            "algorithm": wind_algorithm,
            "palette": wind_palette,
            "points": store.current_wind_points,
            "renderer": _wind_png,
        },
        {
            "mode": "precipitation",
            "label": "precipitación",
            "prefix": "precipitation_field",
            "identity_prefix": "precipitation-field",
            "algorithm": precipitation_algorithm,
            "palette": precipitation_palette,
            "points": store.current_precipitation_points,
            "renderer": _precipitation_png,
        },
    )


def _read_manifest(path: Path | None = None) -> dict[str, Any]:
    path = MANIFEST_PATH if path is None else path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _asset_is_ready(asset: Any, *, version: str, algorithm: int, palette: int) -> bool:
    if not isinstance(asset, dict):
        return False
    if (
        str(asset.get("version") or "") != version
        or int(asset.get("algorithm") or -1) != int(algorithm)
        or int(asset.get("palette") or -1) != int(palette)
    ):
        return False
    tiles = asset.get("tiles")
    if not isinstance(tiles, list) or len(tiles) != 2:
        return False
    return all(
        isinstance(tile, dict)
        and Path(str(tile.get("file") or "")).name == str(tile.get("file") or "")
        and (STATIC_DIR / str(tile.get("file") or "")).is_file()
        for tile in tiles
    )


def _write_split_tiles(
    png: bytes,
    *,
    prefix: str,
    digest: str,
) -> list[dict[str, Any]]:
    from PIL import Image

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    midpoint = image.width // 2
    crop_boxes = (
        (0, 0, midpoint, image.height),
        (midpoint, 0, image.width, image.height),
    )
    tiles: list[dict[str, Any]] = []
    for index, (crop_box, bounds) in enumerate(zip(crop_boxes, HALF_BOUNDS)):
        name = f"{prefix}_{digest}_{index}.png"
        target = STATIC_DIR / name
        if not target.is_file():
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{prefix}.", suffix=".tmp", dir=STATIC_DIR,
            )
            os.close(fd)
            try:
                image.crop(crop_box).save(tmp_name, format="PNG", compress_level=4)
                os.replace(tmp_name, target)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        tiles.append({"file": name, "bounds": list(bounds)})
    return tiles


def _cleanup_old_assets(active_files: set[str], *, max_age_s: int = 3600) -> None:
    cutoff = time.time() - max(60, int(max_age_s))
    for pattern in (
        "temperature_field_*.png",
        "wind_field_*.png",
        "precipitation_field_*.png",
    ):
        for path in STATIC_DIR.glob(pattern):
            try:
                if path.name not in active_files and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                pass


def build_map_field_assets(store: Any) -> dict[str, Any]:
    """Genera una versión completa de los campos y publica su manifiesto.

    Se llama desde un ``asyncio.to_thread`` del job del ranking. El lock evita
    duplicar el trabajo si un arranque y un refresco llegan a solaparse.
    """
    with _BUILD_LOCK:
        updated_at = getattr(store, "updated_at", None)
        version = updated_at.isoformat() if updated_at is not None else ""
        if not version:
            return {}

        previous = _read_manifest()
        previous_fields = previous.get("fields") if isinstance(previous.get("fields"), dict) else {}
        fields: dict[str, Any] = dict(previous_fields)
        started = time.perf_counter()

        for spec in _mode_specs(store):
            mode = str(spec["mode"])
            algorithm = int(spec["algorithm"])
            palette = int(spec["palette"])
            existing = fields.get(mode)
            if _asset_is_ready(
                existing,
                version=version,
                algorithm=algorithm,
                palette=palette,
            ):
                continue

            points = list(spec["points"]())
            if not points:
                logger.info(
                    "🗺️ Mapa %s sin datos · se conserva la versión anterior",
                    spec.get("label", mode),
                )
                continue
            identity = (
                f"{spec['identity_prefix']}-{algorithm}:"
                f"palette-{palette}:{version}"
            )
            digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
            mode_started = time.perf_counter()
            png = spec["renderer"](points)
            tiles = _write_split_tiles(
                png,
                prefix=str(spec["prefix"]),
                digest=digest,
            )
            fields[mode] = {
                "version": version,
                "algorithm": algorithm,
                "palette": palette,
                "point_count": len(points),
                "tiles": tiles,
            }
            logger.info(
                "🗺️ Mapa %s OK · %s estaciones · %.2f s",
                spec.get("label", mode),
                f"{len(points):,}".replace(",", "."),
                time.perf_counter() - mode_started,
            )

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "updated_at": version,
            "fields": fields,
        }
        _atomic_json_write(MANIFEST_PATH, manifest)
        active_files = {
            str(tile.get("file") or "")
            for asset in fields.values()
            if isinstance(asset, dict)
            for tile in asset.get("tiles", [])
            if isinstance(tile, dict)
        }
        _cleanup_old_assets(active_files)
        logger.info(
            "🗺️ Mapas publicados OK · %.2f s en total",
            time.perf_counter() - started,
        )
        return manifest
