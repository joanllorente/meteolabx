"""Campo mundial de temperatura local/regional renderizado como PNG RGBA.

Combina tres gaussianas normalizadas: una local que conserva los extremos de
las estaciones próximas, otra intermedia que une grupos dispersos y una
regional que rellena zonas de baja densidad. Antes de interpolar, las lecturas
se agregan espacialmente para que la densidad de una red no deforme el campo.
Las instantáneas proceden del refresh horario del ranking. Las celdas sin
ninguna estación cercana quedan transparentes, así los océanos y los desiertos
sin datos no se inventan valores.
"""

from __future__ import annotations

import gzip
import io
import json
from functools import lru_cache
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np


# lat_min, lon_min, lat_max, lon_max — tierra habitada (sin interior antártico).
FIELD_BBOX = (-60.0, -180.0, 85.0, 180.0)
CELL_DEG = 0.1
# Radio del campo regional, en celdas (100 celdas ≈ 1.100 km). Solo rellena
# huecos; nunca se suma directamente al peso local porque miles de estaciones
# lejanas terminarían diluyendo los extremos cercanos.
INFLUENCE_CELLS = 100
# El campo regional/intermedio conserva bloques de 0,4° × 0,4°: evita que una
# red con cientos de sensores pese cientos de veces más que otra región. El
# detalle local usa bloques de 0,1° para no borrar máximos y mínimos legítimos
# en redes densas como Meteocat.
SPATIAL_AGGREGATION_CELLS = 4
LOCAL_SPATIAL_AGGREGATION_CELLS = 1
LOCAL_INFLUENCE_CELLS = 10
MEDIUM_INFLUENCE_CELLS = 36
KERNEL_SIGMA_LOCAL = 1.5       # ~17 km
KERNEL_SIGMA_MEDIUM = 10.0     # ~110 km
KERNEL_SIGMA_REGIONAL = 40.0   # ~450 km
REGIONAL_KERNEL_GAIN = 0.01
# Una estación aislada matiza el campo intermedio, pero no debe crear un punto
# de color saturado. El detalle fino gana peso progresivamente y solo domina
# donde existe una red local densa.
LOCAL_SINGLE_STATION_SHARE = 0.0
LOCAL_DENSE_WEIGHT = 4.0
LOCAL_COHERENCE_FLOOR = 0.50
LOCAL_ANOMALY_FLOOR_C = 0.75
LOCAL_ANOMALY_FULL_C = 2.00
MEDIUM_CONFIDENCE_WEIGHT = 1.0
# Peso mínimo para pintar una celda (~ninguna estación a menos de ~600 km).
MIN_WEIGHT = 0.003
FIELD_ALGORITHM_VERSION = 5

# Rampa de color estilo mapa sinóptico clásico (°C → RGB).
COLOR_SCALE_VERSION = 2
COLOR_STOPS: Sequence[Tuple[float, Tuple[int, int, int]]] = (
    (-20.0, (98, 22, 146)),
    (-10.0, (52, 122, 235)),
    (0.0, (88, 176, 245)),
    (5.0, (130, 215, 235)),
    (10.0, (110, 205, 125)),
    (15.0, (200, 225, 80)),
    (20.0, (250, 210, 50)),
    (25.0, (248, 158, 38)),
    (30.0, (238, 92, 28)),
    (35.0, (205, 32, 22)),
    (40.0, (150, 8, 32)),
    (46.0, (96, 2, 58)),
)
FIELD_ALPHA = 255

# El PNG mundial de 0,1 grados es suficiente a escala continental, pero al
# ampliarlo sobre una isla cada píxel ocupa muchos píxeles de pantalla. Para
# vistas cercanas se genera un recorte del viewport a resolución de pantalla y
# se vuelve a rasterizar la costa 1:10m sobre ese recorte.
VIEWPORT_MASK_SUPERSAMPLE = 2
VIEWPORT_MIN_SIZE = 256
VIEWPORT_MAX_SIZE = 2048
# Una sola textura mundial, suficientemente densa para que la costa 1:10m no
# se deshaga al acercar el mapa. Se genera una vez por actualización y nunca
# durante pan/zoom.
GLOBAL_RENDER_SIZE = (7200, 2900)

MapBounds = Tuple[float, float, float, float]  # west, south, east, north


def _grid_shape(cell_deg: float) -> Tuple[int, int]:
    lat_min, lon_min, lat_max, lon_max = FIELD_BBOX
    return (
        int(round((lat_max - lat_min) / cell_deg)),
        int(round((lon_max - lon_min) / cell_deg)),
    )


# Máscara de tierra rasterizada (una vez por proceso y tamaño de celda).
_LAND_MASK_CACHE: dict = {}


# Factor de supermuestreo de la máscara de tierra: se rasteriza x4 y se
# promedia a la rejilla → cobertura fraccional 0..1 por celda. El borde del
# campo sigue así el perfil REAL de la costa (antialiasing) en vez del
# escalonado de la celda de 0,1°.
_LAND_MASK_SUPERSAMPLE = 4


def _land_mask(cell_deg: float) -> np.ndarray:
    """Cobertura de tierra (0..1 por celda) rasterizando las fronteras de
    Natural Earth con supermuestreo (polígonos exteriores en blanco, lagos/
    huecos en negro)."""
    cached = _LAND_MASK_CACHE.get(cell_deg)
    if cached is not None:
        return cached

    import json

    from PIL import Image, ImageDraw

    import data_files

    lat_min, lon_min, lat_max, lon_max = FIELD_BBOX
    rows, cols = _grid_shape(cell_deg)
    scale = _LAND_MASK_SUPERSAMPLE
    fine_cell = cell_deg / scale
    image = Image.new("1", (cols * scale, rows * scale), 0)
    draw = ImageDraw.Draw(image)

    def _to_pixels(ring):
        return [
            ((lon - lon_min) / fine_cell, (lat_max - lat) / fine_cell)
            for lon, lat in ring
        ]

    try:
        features = json.loads(
            data_files.COUNTRY_BORDERS_PATH.read_text(encoding="utf-8")
        ).get("features", [])
    except (OSError, ValueError):
        coverage = np.ones((rows, cols), dtype=np.float32)
        _LAND_MASK_CACHE[cell_deg] = coverage
        return coverage

    for feature in features:
        geometry = (feature or {}).get("geometry") or {}
        kind = geometry.get("type")
        polygons = (
            [geometry.get("coordinates", [])] if kind == "Polygon"
            else geometry.get("coordinates", []) if kind == "MultiPolygon"
            else []
        )
        for polygon in polygons:
            for index, ring in enumerate(polygon):
                if len(ring) < 3:
                    continue
                draw.polygon(_to_pixels(ring), fill=0 if index else 1)

    fine = np.asarray(image, dtype=np.float32)
    coverage = fine.reshape(rows, scale, cols, scale).mean(axis=(1, 3))
    _LAND_MASK_CACHE[cell_deg] = coverage
    return coverage


def _gaussian_kernel(
    radius_cells: int,
    sigma_cells: float,
    *,
    gain: float = 1.0,
) -> np.ndarray:
    """Kernel gaussiano circular para uno de los dos campos normalizados."""
    span = np.arange(-radius_cells, radius_cells + 1, dtype=np.float32)
    yy, xx = np.meshgrid(span, span, indexing="ij")
    dist2 = yy ** 2 + xx ** 2
    weights = float(gain) * np.exp(-dist2 / (2.0 * float(sigma_cells) ** 2))
    weights[dist2 > radius_cells ** 2] = 0.0
    return weights.astype(np.float32, copy=False)


def _add_kernel(
    weight_sum: np.ndarray,
    value_sum: np.ndarray,
    kernel: np.ndarray,
    *,
    row: int,
    col: int,
    value: float,
    radius_cells: int,
) -> None:
    """Añade una estación recortando el kernel en los bordes de la rejilla."""
    rows, cols = weight_sum.shape
    size = 2 * radius_cells + 1
    row_0 = max(0, row - radius_cells)
    row_1 = min(rows, row + radius_cells + 1)
    col_0 = max(0, col - radius_cells)
    col_1 = min(cols, col + radius_cells + 1)
    window = kernel[
        row_0 - (row - radius_cells): size - ((row + radius_cells + 1) - row_1),
        col_0 - (col - radius_cells): size - ((col + radius_cells + 1) - col_1),
    ]
    weight_sum[row_0:row_1, col_0:col_1] += window
    value_sum[row_0:row_1, col_0:col_1] += window * value


def _smoothstep(values: np.ndarray) -> np.ndarray:
    """Transición 0..1 continua, con pendiente nula en ambos extremos."""
    clipped = np.clip(values, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _aggregate_station_points(
    points: Iterable[Tuple[float, float, float]],
    *,
    cell_deg: float,
    block_size_cells: Optional[int] = None,
) -> list[tuple[int, int, float]]:
    """Resume espacialmente las estaciones conservando todas las lecturas.

    La temperatura representativa es la mediana de cada bloque y la posición,
    la media de las celdas de sus estaciones. La confianza posterior depende
    de cuántos bloques distintos rodean el píxel, no de cuántos proveedores
    duplican una misma ubicación.
    """
    _lat_min, lon_min, lat_max, _lon_max = FIELD_BBOX
    rows, cols = _grid_shape(cell_deg)
    bins: dict[tuple[int, int], tuple[float, float, list[float]]] = {}
    block_size = max(1, int(
        SPATIAL_AGGREGATION_CELLS
        if block_size_cells is None
        else block_size_cells
    ))
    for lat, lon, temp in points:
        row = int((lat_max - float(lat)) / cell_deg)
        col = int((float(lon) - lon_min) / cell_deg)
        if not (0 <= row < rows and 0 <= col < cols):
            continue
        key = (row // block_size, col // block_size)
        bucket = bins.get(key)
        if bucket is None:
            bins[key] = (float(row), float(col), [float(temp)])
            continue
        row_sum, col_sum, temperatures = bucket
        temperatures.append(float(temp))
        bins[key] = (row_sum + row, col_sum + col, temperatures)

    aggregated: list[tuple[int, int, float]] = []
    for row_sum, col_sum, temperatures in bins.values():
        count = len(temperatures)
        aggregated.append((
            int(round(row_sum / count)),
            int(round(col_sum / count)),
            float(np.median(temperatures)),
        ))
    return aggregated


def interpolate_grid(
    points: Iterable[Tuple[float, float, float]],
    *,
    cell_deg: float = CELL_DEG,
    radius_cells: int = INFLUENCE_CELLS,
) -> Tuple[np.ndarray, np.ndarray]:
    """Interpola ``(lat, lon, temp)`` con campos local y regional.

    Ambos campos se normalizan por separado y se mezclan según el soporte
    local. Devuelve ``(temp, mask)`` con la fila 0 en el norte.
    """
    _lat_min, lon_min, lat_max, _lon_max = FIELD_BBOX
    rows, cols = _grid_shape(cell_deg)
    regional_weight = np.zeros((rows, cols), dtype=np.float32)
    regional_value = np.zeros((rows, cols), dtype=np.float32)
    medium_weight = np.zeros((rows, cols), dtype=np.float32)
    medium_value = np.zeros((rows, cols), dtype=np.float32)
    local_weight = np.zeros((rows, cols), dtype=np.float32)
    local_value = np.zeros((rows, cols), dtype=np.float32)
    local_abs_weight = np.zeros((rows, cols), dtype=np.float32)
    local_abs_value = np.zeros((rows, cols), dtype=np.float32)
    regional_radius = max(1, int(radius_cells))
    medium_radius = min(MEDIUM_INFLUENCE_CELLS, regional_radius)
    local_radius = min(LOCAL_INFLUENCE_CELLS, regional_radius)
    regional_kernel = _gaussian_kernel(
        regional_radius,
        KERNEL_SIGMA_REGIONAL,
        gain=REGIONAL_KERNEL_GAIN,
    )
    medium_kernel = _gaussian_kernel(medium_radius, KERNEL_SIGMA_MEDIUM)
    local_kernel = _gaussian_kernel(local_radius, KERNEL_SIGMA_LOCAL)

    # Materializar una vez: el campo regional/intermedio y el detalle local
    # parten de las mismas observaciones, pero con agregaciones distintas.
    source_points = list(points)
    regional_points = _aggregate_station_points(
        source_points,
        cell_deg=cell_deg,
        block_size_cells=SPATIAL_AGGREGATION_CELLS,
    )
    local_points = _aggregate_station_points(
        source_points,
        cell_deg=cell_deg,
        block_size_cells=LOCAL_SPATIAL_AGGREGATION_CELLS,
    )

    for row, col, temp in regional_points:
        _add_kernel(
            regional_weight,
            regional_value,
            regional_kernel,
            row=row,
            col=col,
            value=temp,
            radius_cells=regional_radius,
        )
        _add_kernel(
            medium_weight,
            medium_value,
            medium_kernel,
            row=row,
            col=col,
            value=temp,
            radius_cells=medium_radius,
        )
    # Alfa fraccional: cobertura de tierra (costa con antialiasing) donde el
    # peso llega al mínimo; 0 donde no hay estaciones cerca o es mar.
    mask = np.where(regional_weight >= MIN_WEIGHT, _land_mask(cell_deg), 0.0)
    regional_temp = np.divide(
        regional_value,
        np.maximum(regional_weight, np.float32(1e-9)),
    )
    medium_temp = np.divide(
        medium_value,
        np.maximum(medium_weight, np.float32(1e-9)),
    )
    medium_confidence = _smoothstep(
        medium_weight / np.float32(MEDIUM_CONFIDENCE_WEIGHT),
    )
    medium_base = (
        medium_confidence * medium_temp
        + (1.0 - medium_confidence) * regional_temp
    )

    # El detalle local se interpola como ANOMALÍA respecto al campo
    # intermedio, no como otra temperatura absoluta. Esto conserva máximos y
    # mínimos respaldados por varias estaciones sin reintroducir los puntos
    # aislados de redes muy densas. La coherencia mide cuánto coinciden en el
    # signo las anomalías vecinas: valores discordantes se cancelan.
    for row, col, station_temp in local_points:
        anomaly = float(station_temp) - float(medium_base[row, col])
        _add_kernel(
            local_weight,
            local_value,
            local_kernel,
            row=row,
            col=col,
            value=anomaly,
            radius_cells=local_radius,
        )
        _add_kernel(
            local_abs_weight,
            local_abs_value,
            local_kernel,
            row=row,
            col=col,
            value=abs(anomaly),
            radius_cells=local_radius,
        )
    local_anomaly = np.divide(
        local_value,
        np.maximum(local_weight, np.float32(1e-9)),
    )
    local_abs_anomaly = np.divide(
        local_abs_value,
        np.maximum(local_abs_weight, np.float32(1e-9)),
    )
    anomaly_coherence = np.divide(
        np.abs(local_anomaly),
        np.maximum(local_abs_anomaly, np.float32(1e-9)),
    )
    coherence_confidence = _smoothstep(
        (anomaly_coherence - np.float32(LOCAL_COHERENCE_FLOOR))
        / np.float32(1.0 - LOCAL_COHERENCE_FLOOR),
    )
    amplitude_confidence = _smoothstep(
        (np.abs(local_anomaly) - np.float32(LOCAL_ANOMALY_FLOOR_C))
        / np.float32(LOCAL_ANOMALY_FULL_C - LOCAL_ANOMALY_FLOOR_C),
    )
    single_station_support = (
        np.clip(local_weight, 0.0, 1.0)
        * np.float32(LOCAL_SINGLE_STATION_SHARE)
    )
    dense_support = _smoothstep(
        (local_weight - 1.0) / np.float32(LOCAL_DENSE_WEIGHT - 1.0),
    )
    local_confidence = np.clip(
        single_station_support
        + (1.0 - np.float32(LOCAL_SINGLE_STATION_SHARE)) * dense_support,
        0.0,
        1.0,
    ) * coherence_confidence * amplitude_confidence
    temp = medium_base + local_confidence * local_anomaly
    return temp, mask


def colorize(
    temp: np.ndarray,
    mask: np.ndarray,
    *,
    color_stops: Sequence[Tuple[float, Tuple[int, int, int]]] = COLOR_STOPS,
    band_size: float = 1.0,
) -> np.ndarray:
    """Aplica la rampa de color. ``mask`` puede ser booleana o cobertura
    fraccional 0..1 (alfa proporcional → costa con antialiasing). Devuelve
    un array RGBA uint8."""
    stops = np.array([stop for stop, _ in color_stops])
    channels = np.array([rgb for _, rgb in color_stops], dtype=float)
    # Bandas discretas (1 °C por defecto): aplanan el ruido de la
    # interpolación y el campo se ve nítido también con zoom.
    step = max(0.01, float(band_size))
    clipped = np.floor(np.clip(temp, stops[0], stops[-1]) / step) * step
    rgba = np.zeros(temp.shape + (4,), dtype=np.uint8)
    for channel in range(3):
        rgba[..., channel] = np.interp(clipped, stops, channels[:, channel]).astype(np.uint8)
    rgba[..., 3] = np.clip(
        np.asarray(mask, dtype=float) * FIELD_ALPHA, 0, 255,
    ).astype(np.uint8)
    return rgba


def _to_mercator_rows(
    rgba: np.ndarray,
    *,
    south: Optional[float] = None,
    north: Optional[float] = None,
) -> np.ndarray:
    """Remuestrea las filas (equirectangulares, lineales en latitud) a
    espaciado Web Mercator: ``BitmapLayer`` estira la textura linealmente en
    Mercator entre sus bounds, así que sin esto las formas salen desplazadas
    en latitud (peor cuanto más al norte)."""
    import math

    lat_min, _lon_min, lat_max, _lon_max = FIELD_BBOX
    if south is not None:
        lat_min = float(south)
    if north is not None:
        lat_max = float(north)
    rows = rgba.shape[0]

    def _mercator_y(lat_deg: float) -> float:
        return math.log(math.tan(math.pi / 4.0 + math.radians(lat_deg) / 2.0))

    y_top = _mercator_y(lat_max)
    y_bottom = _mercator_y(lat_min)
    # Latitud de cada fila de SALIDA (lineal en Y de Mercator, de norte a sur).
    y_values = y_top + (np.arange(rows) + 0.5) / rows * (y_bottom - y_top)
    lats = np.degrees(2.0 * np.arctan(np.exp(y_values)) - np.pi / 2.0)
    source_rows = np.clip(
        ((lat_max - lats) / (lat_max - lat_min) * rows).astype(int), 0, rows - 1,
    )
    return rgba[source_rows]


@lru_cache(maxsize=1)
def _high_res_land_geometries() -> tuple:
    """Carga una sola vez los polígonos de tierra Natural Earth 1:10m."""
    from shapely.geometry import shape

    import data_files

    path = data_files.LAND_BORDERS_HIGH_RES_PATH
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            features = json.load(handle).get("features", [])
        geometries = tuple(
            shape(feature.get("geometry"))
            for feature in features
            if isinstance(feature, dict) and feature.get("geometry")
        )
        if geometries:
            return geometries
    except (OSError, ValueError, TypeError):
        pass

    # Fallback para instalaciones antiguas que aún no tengan el fichero 1:10m.
    try:
        features = json.loads(
            data_files.COUNTRY_BORDERS_PATH.read_text(encoding="utf-8")
        ).get("features", [])
        return tuple(
            shape(feature.get("geometry"))
            for feature in features
            if isinstance(feature, dict) and feature.get("geometry")
        )
    except (OSError, ValueError, TypeError):
        return ()


def _viewport_land_mask(
    bounds: MapBounds,
    width: int,
    height: int,
    *,
    supersample: Optional[int] = None,
) -> "Image.Image":
    """Rasteriza tierra 1:10m en el viewport, con antialiasing subpixel."""
    from PIL import Image, ImageDraw
    from shapely.geometry import box

    west, south, east, north = bounds
    scale = max(1, int(
        VIEWPORT_MASK_SUPERSAMPLE if supersample is None else supersample
    ))
    raster_width = int(width) * scale
    raster_height = int(height) * scale
    image = Image.new("L", (raster_width, raster_height), 0)
    draw = ImageDraw.Draw(image)
    viewport = box(west, south, east, north)

    def _pixels(coords):
        return [
            (
                (float(lon) - west) / (east - west) * raster_width,
                (north - float(lat)) / (north - south) * raster_height,
            )
            for lon, lat in coords
        ]

    def _draw_polygon(polygon) -> None:
        if polygon.is_empty:
            return
        exterior = _pixels(polygon.exterior.coords)
        if len(exterior) >= 3:
            draw.polygon(exterior, fill=255)
        for interior in polygon.interiors:
            hole = _pixels(interior.coords)
            if len(hole) >= 3:
                draw.polygon(hole, fill=0)

    for geometry in _high_res_land_geometries():
        min_x, min_y, max_x, max_y = geometry.bounds
        if max_x < west or min_x > east or max_y < south or min_y > north:
            continue
        if west <= min_x and south <= min_y and east >= max_x and north >= max_y:
            clipped = geometry
        else:
            try:
                clipped = geometry.intersection(viewport)
            except Exception:
                clipped = geometry
        if clipped.geom_type == "Polygon":
            _draw_polygon(clipped)
        elif clipped.geom_type == "MultiPolygon":
            for polygon in clipped.geoms:
                _draw_polygon(polygon)

    if scale > 1:
        image = image.resize((int(width), int(height)), Image.Resampling.LANCZOS)
    return image


def _validated_viewport(
    bounds: MapBounds,
    width: int,
    height: int,
) -> tuple[MapBounds, int, int]:
    west, south, east, north = (float(value) for value in bounds)
    field_south, field_west, field_north, field_east = FIELD_BBOX
    if not (
        field_west <= west < east <= field_east
        and field_south <= south < north <= field_north
    ):
        raise ValueError("temperature field viewport is outside FIELD_BBOX")
    width = int(width)
    height = int(height)
    if not (VIEWPORT_MIN_SIZE <= width <= VIEWPORT_MAX_SIZE):
        raise ValueError("temperature field viewport width is out of range")
    if not (VIEWPORT_MIN_SIZE <= height <= VIEWPORT_MAX_SIZE):
        raise ValueError("temperature field viewport height is out of range")
    return (west, south, east, north), width, height


def render_grid_png(
    temp: np.ndarray,
    mask: np.ndarray,
    *,
    bounds: Optional[MapBounds] = None,
    width: int = 1600,
    height: int = 1000,
    color_stops: Sequence[Tuple[float, Tuple[int, int, int]]] = COLOR_STOPS,
    band_size: float = 1.0,
) -> bytes:
    """Renderiza una rejilla ya interpolada.

    Sin ``bounds`` conserva el PNG mundial histórico. Con ``bounds`` crea un
    raster del tamaño del viewport y aplica la costa 1:10m en esa misma
    resolución; de ese modo BitmapLayer deja de ampliar una máscara mundial de
    solo 0,1 grados por píxel.
    """
    from PIL import Image, ImageChops, ImageFilter

    if bounds is None:
        rgba = _to_mercator_rows(
            colorize(temp, mask, color_stops=color_stops, band_size=band_size),
        )
    else:
        bounds, width, height = _validated_viewport(bounds, width, height)
        west, south, east, north = bounds
        rows, cols = temp.shape
        field_south, field_west, field_north, field_east = FIELD_BBOX
        source_box = (
            (west - field_west) / (field_east - field_west) * cols,
            (field_north - north) / (field_north - field_south) * rows,
            (east - field_west) / (field_east - field_west) * cols,
            (field_north - south) / (field_north - field_south) * rows,
        )

        colored = colorize(
            temp, np.asarray(mask) > 0,
            color_stops=color_stops, band_size=band_size,
        )
        rgb_source = Image.fromarray(colored[..., :3])
        # La máscara mundial 1:50m puede omitir una isla pequeña aunque haya
        # datos alrededor. Se dilata solo el soporte de datos; la costa 1:10m
        # aplicada después vuelve a recortar con precisión todo el océano.
        support_source = Image.fromarray(
            np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8),
        ).filter(ImageFilter.MaxFilter(5))
        rgb = rgb_source.transform(
            (width, height),
            Image.Transform.EXTENT,
            source_box,
            resample=Image.Resampling.BILINEAR,
        )
        support = support_source.transform(
            (width, height),
            Image.Transform.EXTENT,
            source_box,
            resample=Image.Resampling.NEAREST,
        )
        alpha = ImageChops.multiply(
            support,
            _viewport_land_mask(bounds, width, height),
        )
        image = Image.merge("RGBA", (*rgb.split(), alpha))
        rgba = _to_mercator_rows(
            np.asarray(image),
            south=south,
            north=north,
        )

    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_global_grid_png(
    temp: np.ndarray,
    mask: np.ndarray,
    *,
    width: int = GLOBAL_RENDER_SIZE[0],
    height: int = GLOBAL_RENDER_SIZE[1],
    color_stops: Sequence[Tuple[float, Tuple[int, int, int]]] = COLOR_STOPS,
    band_size: float = 1.0,
    preserve_mask_alpha: bool = False,
) -> bytes:
    """Textura mundial cacheable con costa 1:10m.

    A diferencia del antiguo recorte por viewport, esta operación no depende
    de la cámara. El navegador conserva la misma textura durante todos los
    movimientos y solo la sustituye cuando cambia el ciclo meteorológico.
    """
    from PIL import Image, ImageChops, ImageFilter

    width = int(width)
    height = int(height)
    if not (2048 <= width <= 8192 and 1024 <= height <= 4096):
        raise ValueError("global temperature field size is out of range")

    colored = colorize(
        temp, np.asarray(mask) > 0,
        color_stops=color_stops, band_size=band_size,
    )
    rgb_source = Image.fromarray(colored[..., :3])
    support_values = (
        np.clip(np.asarray(mask, dtype=float) * 255.0, 0, 255).astype(np.uint8)
        if preserve_mask_alpha
        else np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8)
    )
    support_source = Image.fromarray(support_values).filter(ImageFilter.MaxFilter(5))
    rgb = rgb_source.resize((width, height), Image.Resampling.BILINEAR)
    support = support_source.resize(
        (width, height),
        Image.Resampling.BILINEAR if preserve_mask_alpha else Image.Resampling.NEAREST,
    )
    alpha = ImageChops.multiply(
        support,
        _viewport_land_mask(
            (-180.0, -60.0, 180.0, 85.0),
            width,
            height,
            # A 7200 px ya hay subpixel suficiente; x2 duplicaría más de
            # 80 millones de píxeles solo para la máscara temporal.
            supersample=1,
        ),
    )
    image = Image.merge("RGBA", (*rgb.split(), alpha))
    rgba = _to_mercator_rows(np.asarray(image))

    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", compress_level=4)
    return buffer.getvalue()


def render_field_png(
    points: Iterable[Tuple[float, float, float]],
    *,
    cell_deg: float = CELL_DEG,
    radius_cells: int = INFLUENCE_CELLS,
    bounds: Optional[MapBounds] = None,
    width: int = 1600,
    height: int = 1000,
) -> bytes:
    temp, mask = interpolate_grid(points, cell_deg=cell_deg, radius_cells=radius_cells)
    return render_grid_png(
        temp,
        mask,
        bounds=bounds,
        width=width,
        height=height,
    )
