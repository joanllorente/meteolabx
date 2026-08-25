#!/usr/bin/env python3
"""Visor de cizalladura y SHIP AROME para el nordeste peninsular.

El módulo conserva la posibilidad de ejecutarse como aplicación independiente,
pero su entrada principal se integra en la pestaña Predicción de MeteoLabX.
"""

from __future__ import annotations

import getpass
import base64
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import plotly.graph_objects as go
import rasterio
import requests
import streamlit as st
from dotenv import load_dotenv
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.warp import Resampling, reproject
from shapely import contains_xy, make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union
from zoneinfo import ZoneInfo

try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError:  # La interfaz mostrará cómo instalarlo.
    keyring = None

    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR.parent / ".env")

WCS_BASE = (
    "https://public-api.meteofrance.fr/public/arome/1.0/wcs/"
    "MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS"
)
# El WCS entrega la rejilla nativa completa cuando no se envía un subset
# horizontal. En desarrollo recortamos la petición *antes* de descargar el
# GRIB para que los diagnósticos de perfiles puedan probarse en segundos/minutos
# sobre Cataluña, no sobre las ~804.000 celdas del dominio AROME.
FORECAST_CATALONIA_BBOX = (0.10, 40.45, 3.45, 42.95)
REGIONS_BOUNDARY_URL = (
    "https://mapas.fomento.gob.es/arcgis/rest/services/SIU/"
    "ENTIDADES_TERRITORIALES_EGRN/MapServer/1/query"
)
GRAVITY = 9.80665
SHIP_SCALE = 44_000_000.0
LOCAL_TZ = ZoneInfo("Europe/Madrid")
KEYRING_SERVICE = "arome-cizalladura-catalunya"
KEYRING_ACCOUNT = getpass.getuser()
RETRYABLE_HTTP_CODES = {500, 502, 503, 504}
API_MAX_ATTEMPTS = 4


def forecast_calculation_scope() -> str:
    """Alcance WCS: dominio completo en Railway y Cataluña en local."""
    configured = os.getenv("METEOLABX_FORECAST_CALCULATION_SCOPE", "").strip().lower()
    if configured in {"model", "catalonia"}:
        return configured
    is_railway = any(
        os.getenv(name)
        for name in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID")
    )
    return "model" if is_railway else "catalonia"


PRODUCTS = {
    "CIZ 0–1 km": {"kind": "shear", "depth_m": 1000, "vmax": 26.0},
    "CIZ 0–3 km": {"kind": "shear", "depth_m": 3000, "vmax": 36.0},
    "CIZ 0–6 km": {"kind": "shear", "depth_m": 6000, "vmax": 52.0},
    "SHIP AROME": {"kind": "ship", "depth_m": 6000, "vmax": 5.0},
}

PALETTE = [
    "#3b4cc0",
    "#3288bd",
    "#66c2a5",
    "#abdda4",
    "#e6f598",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d73027",
    "#762a83",
]

PREFIX_CANDIDATES = {
    "height_u": [
        "U_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "WIND_U_COMPONENT__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "U__HEIGHT",
    ],
    "height_v": [
        "V_COMPONENT_OF_WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "WIND_V_COMPONENT__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "V__HEIGHT",
    ],
    "height_wind": ["WIND__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND"],
    "pressure_u": [
        "U_COMPONENT_OF_WIND__ISOBARIC_SURFACE",
        "WIND_U_COMPONENT__ISOBARIC_SURFACE",
        "U__ISOBARIC",
    ],
    "pressure_v": [
        "V_COMPONENT_OF_WIND__ISOBARIC_SURFACE",
        "WIND_V_COMPONENT__ISOBARIC_SURFACE",
        "V__ISOBARIC",
    ],
    "pressure_wind": ["WIND__ISOBARIC_SURFACE"],
    "geopotential": [
        "GEOPOTENTIAL__ISOBARIC_SURFACE",
        "GEOPOTENTIAL_HEIGHT__ISOBARIC_SURFACE",
        "Z__ISOBARIC",
    ],
    "terrain": ["GEOMETRIC_HEIGHT__GROUND_OR_WATER_SURFACE", "ALTITUDE__GROUND"],
    "cape_mu": [
        "CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY__GROUND_OR_WATER_SURFACE",
        "CAPE_INS__GROUND",
    ],
    "cape_ml": ["MEAN_LAYER_CAPE__GROUND_OR_WATER_SURFACE"],
    "height_temperature": [
        "TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "T__HEIGHT",
    ],
    "pressure_temperature": ["TEMPERATURE__ISOBARIC_SURFACE", "T__ISOBARIC"],
    "pressure_dewpoint": [
        "DEW_POINT_TEMPERATURE__ISOBARIC_SURFACE",
        "TD__ISOBARIC",
    ],
    "height_dewpoint": [
        "DEW_POINT_TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
        "TD__HEIGHT",
    ],
    "surface_pressure": ["PRESSURE__GROUND_OR_WATER_SURFACE", "P__GROUND"],
    "precipitation_1h": [
        "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE",
        "PRECIP__GROUND",
    ],
    "wind_gust_1h": [
        "WIND_SPEED_GUST_MAX__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
    ],
    "liquid_precipitation_1h": [
        "TOTAL_WATER_PRECIPITATION__GROUND_OR_WATER_SURFACE",
    ],
    "pressure_relative_humidity": [
        "RELATIVE_HUMIDITY__ISOBARIC_SURFACE",
    ],
    "shortwave_down_1h": [
        "DOWNWARD_SHORT_WAVE_RADIATION_FLUX__GROUND_OR_WATER_SURFACE",
    ],
    "total_cloud_cover": [
        "TOTAL_CLOUD_COVER__GROUND_OR_WATER_SURFACE",
    ],
}


class AromeError(RuntimeError):
    """Error legible para fallos de catálogo, autenticación o cobertura."""


def _load_keychain_token() -> Optional[str]:
    if keyring is None:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except KeyringError:
        return None
    return value.strip() if value else None


def _save_keychain_token(value: str) -> None:
    if keyring is None:
        raise AromeError(
            "Falta la dependencia `keyring`. Ejecuta de nuevo "
            "`python3 -m pip install -r requirements.txt`."
        )
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value.strip())
    except KeyringError as exc:
        raise AromeError(f"No se pudo guardar la clave en el llavero: {exc}") from exc


def _delete_keychain_token() -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except PasswordDeleteError:
        pass
    except KeyringError as exc:
        raise AromeError(f"No se pudo borrar la clave del llavero: {exc}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> Optional[str]:
    for child in element.iter():
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _parse_run(coverage_id: str) -> Optional[datetime]:
    match = re.search(r"___(\d{4}-\d{2}-\d{2}T\d{2}\.\d{2}\.\d{2}Z)", coverage_id)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H.%M.%SZ").replace(
        tzinfo=timezone.utc
    )


def _parse_coverage_period(coverage_id: str) -> Optional[str]:
    match = re.search(r"Z_(P(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?)$", coverage_id)
    return match.group(1) if match else None


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _credential_headers(token: str) -> Dict[str, str]:
    """Return the authentication headers expected by the Meteo-France portal.

    Long-lived API keys are JWTs whose payload contains
    ``"token_type": "apiKey"``.  Unlike OAuth2 access tokens, the AROME
    gateway expects those keys in the ``apikey`` header, without ``Bearer``.
    Decoding here is only used to select the header; it does not attempt to
    validate the JWT signature.
    """
    payload: Dict[str, object] = {}
    try:
        encoded_payload = token.split(".")[1]
        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        )
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    token_type = str(payload.get("token_type", "")).lower()
    if token_type == "apikey":
        return {"apikey": token, "Accept": "*/*"}
    return {"Authorization": f"Bearer {token}", "Accept": "*/*"}


def _wait_for_api_request_slot(interval: float | None = None) -> None:
    """Escalona GetCoverage entre todos los procesos del contenedor."""
    delay_between_requests = max(
        0.1,
        float(
            interval
            if interval is not None
            else os.getenv("METEOLABX_AROME_REQUEST_INTERVAL_S", "0.5")
        ),
    )
    lock_path = Path(
        os.getenv(
            "METEOLABX_AROME_REQUEST_THROTTLE_FILE",
            "/tmp/meteolabx-arome-request-throttle",
        )
    )
    try:
        import fcntl
    except ImportError:  # pragma: no cover - producción y desarrollo son Unix
        time.sleep(delay_between_requests)
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
            handle.write(str(time.monotonic() + delay_between_requests))
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@st.cache_data(ttl=900, show_spinner=False, max_entries=256)
def _api_get(
    url: str,
    params: Tuple[Tuple[str, str], ...],
    _token: str,
) -> Tuple[bytes, str]:
    response = None
    last_connection_error: Optional[requests.RequestException] = None
    for attempt in range(API_MAX_ATTEMPTS):
        try:
            if url.rstrip("/").endswith("GetCoverage"):
                _wait_for_api_request_slot()
            response = requests.get(
                url,
                params=list(params),
                headers=_credential_headers(_token),
                timeout=90,
            )
            last_connection_error = None
        except requests.RequestException as exc:
            last_connection_error = exc
            if attempt == API_MAX_ATTEMPTS - 1:
                break
            time.sleep(1.5 * (2**attempt))
            continue

        if (
            response.status_code in RETRYABLE_HTTP_CODES
            and attempt < API_MAX_ATTEMPTS - 1
        ):
            time.sleep(1.5 * (2**attempt))
            continue
        break

    if response is None:
        raise AromeError(
            "No se pudo conectar con Météo-France después de varios intentos: "
            f"{last_connection_error}"
        ) from last_connection_error

    if response.status_code >= 400:
        if response.status_code in (401, 403):
            raise AromeError(
                "Météo-France rechazó la credencial o la suscripción AROME "
                f"(HTTP {response.status_code})."
            )
        if response.status_code in RETRYABLE_HTTP_CODES:
            raise AromeError(
                "Météo-France está temporalmente fuera de servicio "
                f"(HTTP {response.status_code}) después de {API_MAX_ATTEMPTS} "
                "intentos. Espera un momento y vuelve a probar."
            )
        detail = response.text[:500].replace("\n", " ").strip()
        raise AromeError(
            f"La API de AROME devolvió HTTP {response.status_code}: {detail}"
        )

    content_type = response.headers.get("Content-Type", "").lower()
    if response.content.lstrip().startswith(b"<ExceptionReport"):
        raise AromeError(response.text[:700].replace("\n", " "))
    return response.content, content_type


@st.cache_data(ttl=86400, show_spinner=False)
def _load_forecast_regions_geojson() -> dict:
    params = {
        "where": "CodINE IN ('02','09','10')",
        "outFields": "NAMEUNIT,CodINE",
        "returnGeometry": "true",
        "outSR": "4326",
        "maxAllowableOffset": "0.004",
        "geometryPrecision": "5",
        "f": "geojson",
    }
    response = requests.get(REGIONS_BOUNDARY_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not data.get("features"):
        raise AromeError("El servicio cartográfico no devolvió las tres comunidades.")
    return data


# Alias temporal para la vista Streamlit original y consumidores existentes.
_load_catalonia_geojson = _load_forecast_regions_geojson


@dataclass(frozen=True)
class CoverageMetadata:
    axes: Dict[str, Tuple[float, ...]]
    units: Dict[str, str]
    begin: Optional[datetime]
    end: Optional[datetime]

    def vertical_axis(self) -> Optional[str]:
        for name in self.axes:
            if name.lower() not in {"long", "lon", "latitude", "lat", "time"}:
                return name
        return None

    def valid_times(self, run: datetime) -> List[datetime]:
        offsets = self.axes.get("time", ())
        if offsets:
            return [run + timedelta(seconds=float(value)) for value in offsets]
        if self.begin and self.end:
            result = []
            value = self.begin
            while value <= self.end:
                result.append(value)
                value += timedelta(hours=1)
            return result
        return [run]


@dataclass
class RasterField:
    data: np.ndarray
    transform: rasterio.Affine
    crs: CRS
    bounds: Tuple[float, float, float, float]
    units: str = ""
    vector_u: Optional[np.ndarray] = None
    vector_v: Optional[np.ndarray] = None
    overlay: Optional[np.ndarray] = None
    overlay_units: str = ""


class CoverageCatalog:
    def __init__(self, xml_bytes: bytes):
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise AromeError("La respuesta GetCapabilities no es XML válido.") from exc

        self.by_prefix: Dict[str, Dict[datetime, str]] = {}
        self.by_prefix_period: Dict[str, Dict[datetime, Dict[str, str]]] = {}
        self.titles: Dict[str, str] = {}
        for summary in root.iter():
            if _local_name(summary.tag) != "CoverageSummary":
                continue
            coverage_id = _child_text(summary, "CoverageId")
            if not coverage_id or "___" not in coverage_id:
                continue
            prefix = coverage_id.split("___", 1)[0]
            run = _parse_run(coverage_id)
            if run is None:
                continue
            self.by_prefix.setdefault(prefix, {})[run] = coverage_id
            period = _parse_coverage_period(coverage_id)
            if period:
                self.by_prefix_period.setdefault(prefix, {}).setdefault(run, {})[
                    period
                ] = coverage_id
            self.titles[prefix] = _child_text(summary, "Title") or prefix

        if not self.by_prefix:
            raise AromeError("GetCapabilities no contiene coberturas AROME utilizables.")

    @property
    def prefixes(self) -> Sequence[str]:
        return tuple(self.by_prefix)

    def resolve(self, kind: str) -> str:
        for candidate in PREFIX_CANDIDATES[kind]:
            if candidate in self.by_prefix:
                return candidate

        if kind in {"height_u", "height_v"}:
            combined = self.resolve_optional("height_wind")
            if combined:
                return combined
        if kind in {"pressure_u", "pressure_v"}:
            combined = self.resolve_optional("pressure_wind")
            if combined:
                return combined

        if kind == "geopotential":
            for prefix in self.prefixes:
                if "GEOPOTENTIAL" in prefix and "ISOBARIC_SURFACE" in prefix:
                    return prefix
        if kind == "terrain":
            for prefix in self.prefixes:
                if "GEOMETRIC_HEIGHT" in prefix and "GROUND_OR_WATER_SURFACE" in prefix:
                    return prefix

        desired_level = (
            "SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND"
            if kind.startswith("height")
            else "ISOBARIC_SURFACE"
        )
        component = "U_COMPONENT" if kind.endswith("_u") else "V_COMPONENT"
        for prefix in self.prefixes:
            if desired_level in prefix and component in prefix and "GUST" not in prefix:
                return prefix

        available = ", ".join(sorted(self.prefixes))
        raise AromeError(
            f"No se encontró la cobertura necesaria ({kind}). "
            f"Coberturas anunciadas: {available}"
        )

    def resolve_optional(self, kind: str) -> Optional[str]:
        for candidate in PREFIX_CANDIDATES[kind]:
            if candidate in self.by_prefix:
                return candidate
        return None

    def runs_for(self, prefix: str, period: Optional[str] = None) -> set[datetime]:
        if period is None:
            return set(self.by_prefix.get(prefix, {}))
        return {
            run
            for run, variants in self.by_prefix_period.get(prefix, {}).items()
            if period in variants
        }

    def coverage_id(
        self, prefix: str, run: datetime, period: Optional[str] = None
    ) -> str:
        try:
            if period is not None:
                return self.by_prefix_period[prefix][run][period]
            return self.by_prefix[prefix][run]
        except KeyError as exc:
            suffix = f" con acumulación {period}" if period else ""
            raise AromeError(
                f"No existe {prefix}{suffix} para el run {_iso_utc(run)}."
            ) from exc

    def latest_common_run(self, prefixes: Iterable[str]) -> datetime:
        unique = set(prefixes)
        runs: Optional[set] = None
        for prefix in unique:
            current = set(self.by_prefix.get(prefix, {}))
            runs = current if runs is None else runs & current
        if not runs:
            raise AromeError("No hay un run común para todas las variables requeridas.")
        return max(runs)


class AromeWCS:
    def __init__(self, token: str):
        self.token = token.strip()
        if not self.token:
            raise AromeError("Falta la clave de la API de Météo-France.")

    def capabilities(self) -> CoverageCatalog:
        params = (
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("language", "fre"),
        )
        content, _ = _api_get(f"{WCS_BASE}/GetCapabilities", params, self.token)
        return CoverageCatalog(content)

    def describe(self, coverage_id: str) -> CoverageMetadata:
        params = (
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageID", coverage_id),
        )
        content, _ = _api_get(f"{WCS_BASE}/DescribeCoverage", params, self.token)
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise AromeError("DescribeCoverage no devolvió XML válido.") from exc

        axes: Dict[str, Tuple[float, ...]] = {}
        units: Dict[str, str] = {}
        begin: Optional[datetime] = None
        end: Optional[datetime] = None

        for element in root.iter():
            if _local_name(element.tag) == "EnvelopeWithTimePeriod":
                labels = element.attrib.get("axisLabels", "").split()
                uoms = element.attrib.get("uomLabels", "").split()
                units.update(dict(zip(labels, uoms)))
                begin_text = _child_text(element, "beginPosition")
                end_text = _child_text(element, "endPosition")
                if begin_text:
                    begin = datetime.fromisoformat(begin_text.replace("Z", "+00:00"))
                if end_text:
                    end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))

            if _local_name(element.tag) != "GeneralGridAxis":
                continue
            axis_name = _child_text(element, "gridAxesSpanned")
            coefficient_text = _child_text(element, "coefficients") or ""
            values: List[float] = []
            for item in coefficient_text.split():
                try:
                    values.append(float(item))
                except ValueError:
                    pass
            if axis_name:
                axes[axis_name] = tuple(values)
                for child in element.iter():
                    if _local_name(child.tag) == "offsetVector":
                        labels = child.attrib.get("axisLabels", "").split()
                        uoms = child.attrib.get("uomLabels", "").split()
                        units.update(dict(zip(labels, uoms)))

        return CoverageMetadata(axes=axes, units=units, begin=begin, end=end)

    def get_field(
        self,
        catalog: CoverageCatalog,
        prefix: str,
        run: datetime,
        valid_time: Optional[datetime],
        vertical_target: Optional[float],
        vertical_kind: Optional[str],
        component: Optional[str] = None,
        period: Optional[str] = None,
    ) -> RasterField:
        coverage_id = catalog.coverage_id(prefix, run, period=period)
        metadata = self.describe(coverage_id)
        params: List[Tuple[str, str]] = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("coverageid", coverage_id),
        ]

        if "time" in metadata.axes:
            selected_time = valid_time or metadata.valid_times(run)[0]
            params.append(("subset", f"time({_iso_utc(selected_time)})"))

        vertical_axis = metadata.vertical_axis()
        if vertical_axis and vertical_target is not None:
            actual_value = _nearest_vertical_value(
                metadata, vertical_axis, vertical_target, vertical_kind or "height"
            )
            params.append(("subset", f"{vertical_axis}({_format_number(actual_value)})"))

        if forecast_calculation_scope() == "catalonia":
            west, south, east, north = FORECAST_CATALONIA_BBOX
            params.extend(
                [
                    ("subset", f"lat({south},{north})"),
                    ("subset", f"long({west},{east})"),
                ]
            )
        params.append(("format", "application/wmo-grib"))
        content, content_type = _api_get(
            f"{WCS_BASE}/GetCoverage", tuple(params), self.token
        )
        if "xml" in content_type or content.lstrip().startswith(b"<"):
            detail = content[:800].decode("utf-8", errors="replace").replace("\n", " ")
            raise AromeError(f"GetCoverage devolvió XML en vez de GRIB2: {detail}")
        return _read_raster(content, component=component)


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _nearest_vertical_value(
    metadata: CoverageMetadata,
    axis_name: str,
    target: float,
    kind: str,
) -> float:
    values = metadata.axes.get(axis_name, ())
    unit = metadata.units.get(axis_name, "").lower()
    if not values:
        if kind == "pressure" and ("pa" in unit and "hpa" not in unit):
            return target * 100.0
        return target

    array = np.asarray(values, dtype=float)
    comparable = array.copy()
    pressure_unit_is_pa = "pa" in unit and "hpa" not in unit
    if kind == "pressure" and (
        np.nanmax(np.abs(array)) > 2000 or pressure_unit_is_pa
    ):
        comparable = array / 100.0
    index = int(np.nanargmin(np.abs(comparable - target)))
    if abs(comparable[index] - target) > (2 if kind == "pressure" else 5):
        raise AromeError(
            f"El nivel {target:g} ({kind}) no está disponible en {axis_name}."
        )
    return float(array[index])


def _choose_band(dataset: rasterio.DatasetReader, component: Optional[str]) -> int:
    if dataset.count == 1 or not component:
        return 1
    component = component.lower()
    patterns = {
        "u": ("ugrd", "u-component", "u component", "eastward", "zonal"),
        "v": ("vgrd", "v-component", "v component", "northward", "merid"),
    }[component]
    for index in range(1, dataset.count + 1):
        tags = " ".join(f"{key}={value}" for key, value in dataset.tags(index).items())
        description = dataset.descriptions[index - 1] or ""
        text = f"{description} {tags}".lower()
        if any(pattern in text for pattern in patterns):
            return index
    raise AromeError(f"El GRIB2 no permite identificar la componente {component.upper()}.")


def _read_raster(content: bytes, component: Optional[str] = None) -> RasterField:
    try:
        with MemoryFile(content) as memory_file:
            with memory_file.open() as dataset:
                band = _choose_band(dataset, component)
                data = dataset.read(band).astype(np.float64)
                nodata = dataset.nodata
                if nodata is not None:
                    data[np.isclose(data, nodata)] = np.nan
                data[~np.isfinite(data)] = np.nan
                tags = dataset.tags(band)
                units = (
                    (dataset.units[band - 1] if dataset.units else None)
                    or tags.get("GRIB_UNIT")
                    or tags.get("units")
                    or ""
                )
                crs = dataset.crs or CRS.from_epsg(4326)
                bounds = (
                    float(dataset.bounds.left),
                    float(dataset.bounds.bottom),
                    float(dataset.bounds.right),
                    float(dataset.bounds.top),
                )
                return RasterField(data, dataset.transform, crs, bounds, str(units))
    except rasterio.errors.RasterioError as exc:
        raise AromeError("No se pudo leer el GRIB2 devuelto por AROME.") from exc


def _same_grid(first: RasterField, second: RasterField) -> bool:
    return (
        first.data.shape == second.data.shape
        and first.crs == second.crs
        and np.allclose(tuple(first.transform), tuple(second.transform), atol=1e-9)
    )


def _align(reference: RasterField, field: RasterField) -> np.ndarray:
    if _same_grid(reference, field):
        return field.data
    destination = np.full(reference.data.shape, np.nan, dtype=np.float64)
    reproject(
        source=field.data,
        destination=destination,
        src_transform=field.transform,
        src_crs=field.crs,
        dst_transform=reference.transform,
        dst_crs=reference.crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return destination


def _grid_lon_lat(field: RasterField) -> Tuple[np.ndarray, np.ndarray]:
    if field.crs.to_epsg() not in (None, 4326) and not field.crs.is_geographic:
        raise AromeError("La cobertura no está en una rejilla geográfica EPSG:4326.")
    height, width = field.data.shape
    cols = np.arange(width, dtype=float) + 0.5
    rows = np.arange(height, dtype=float) + 0.5
    lon = field.transform.c + cols * field.transform.a
    lat = field.transform.f + rows * field.transform.e
    return np.meshgrid(lon, lat)


def _height_from_geopotential(values: np.ndarray, units: str) -> np.ndarray:
    unit_text = units.lower().replace(" ", "")
    median = float(np.nanmedian(np.abs(values)))
    if "m^2" in unit_text or "m2" in unit_text or median > 20000:
        return values / GRAVITY
    return values


def _interpolate_at_height(
    heights: np.ndarray,
    values: np.ndarray,
    target: np.ndarray,
) -> np.ndarray:
    """Interpola values[level,y,x] a target[y,x] usando heights[level,y,x]."""
    order = np.argsort(heights, axis=0)
    sorted_h = np.take_along_axis(heights, order, axis=0)
    sorted_v = np.take_along_axis(values, order, axis=0)
    upper = np.sum(sorted_h < target[None, :, :], axis=0)
    upper = np.clip(upper, 1, sorted_h.shape[0] - 1)
    lower = upper - 1
    h0 = np.take_along_axis(sorted_h, lower[None, :, :], axis=0)[0]
    h1 = np.take_along_axis(sorted_h, upper[None, :, :], axis=0)[0]
    v0 = np.take_along_axis(sorted_v, lower[None, :, :], axis=0)[0]
    v1 = np.take_along_axis(sorted_v, upper[None, :, :], axis=0)[0]
    fraction = np.divide(
        target - h0,
        h1 - h0,
        out=np.full_like(target, np.nan, dtype=float),
        where=np.abs(h1 - h0) > 1e-6,
    )
    result = v0 + fraction * (v1 - v0)
    valid = (target >= sorted_h[0]) & (target <= sorted_h[-1])
    result[~valid] = np.nan
    return result


def _resolved_prefixes(
    catalog: CoverageCatalog, depth_m: int, product_kind: str = "shear"
) -> Dict[str, str]:
    result = {
        "height_u": catalog.resolve("height_u"),
        "height_v": catalog.resolve("height_v"),
    }
    if depth_m == 6000:
        result.update(
            {
                "pressure_u": catalog.resolve("pressure_u"),
                "pressure_v": catalog.resolve("pressure_v"),
                "geopotential": catalog.resolve("geopotential"),
            }
        )
        terrain = catalog.resolve_optional("terrain")
        if terrain:
            result["terrain"] = terrain
    if product_kind == "ship":
        result.update(
            {
                "cape_mu": catalog.resolve("cape_mu"),
                "pressure_temperature": catalog.resolve("pressure_temperature"),
                "height_dewpoint": catalog.resolve("height_dewpoint"),
                "surface_pressure": catalog.resolve("surface_pressure"),
            }
        )
    return result


def _get_uv_height(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
    height_m: float,
) -> Tuple[RasterField, RasterField]:
    u = client.get_field(
        catalog,
        prefixes["height_u"],
        run,
        valid_time,
        height_m,
        "height",
        component="u",
    )
    v = client.get_field(
        catalog,
        prefixes["height_v"],
        run,
        valid_time,
        height_m,
        "height",
        component="v",
    )
    return u, v


def _compute_shear(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
    depth_m: int,
    base_uv: Optional[Tuple[RasterField, RasterField]] = None,
) -> RasterField:
    """Cizalladura entre 10 m y `depth_m`.

    `base_uv` permite compartir el viento de superficie entre las tres
    profundidades: es el mismo campo para las tres y descargarlo una vez por
    producto multiplicaba las peticiones al WCS.
    """
    base_u, base_v = base_uv or _get_uv_height(
        client, catalog, prefixes, run, valid_time, 10.0
    )
    u0 = base_u.data
    v0 = _align(base_u, base_v)

    if depth_m in (1000, 3000):
        top_u, top_v = _get_uv_height(
            client, catalog, prefixes, run, valid_time, float(depth_m)
        )
        ut = _align(base_u, top_u)
        vt = _align(base_u, top_v)
    else:
        terrain = np.zeros_like(u0)
        terrain_prefix = prefixes.get("terrain")
        if terrain_prefix:
            terrain_runs = catalog.by_prefix[terrain_prefix]
            terrain_run = run if run in terrain_runs else max(terrain_runs)
            terrain_field = client.get_field(
                catalog,
                terrain_prefix,
                terrain_run,
                None,
                None,
                None,
            )
            terrain = _align(base_u, terrain_field)
            terrain = np.where(np.isfinite(terrain), terrain, 0.0)

        pressure_levels_hpa = (500, 450, 400, 350, 300, 250)
        u_levels: List[np.ndarray] = []
        v_levels: List[np.ndarray] = []
        z_levels: List[np.ndarray] = []
        for pressure in pressure_levels_hpa:
            u_field = client.get_field(
                catalog,
                prefixes["pressure_u"],
                run,
                valid_time,
                pressure,
                "pressure",
                component="u",
            )
            v_field = client.get_field(
                catalog,
                prefixes["pressure_v"],
                run,
                valid_time,
                pressure,
                "pressure",
                component="v",
            )
            z_field = client.get_field(
                catalog,
                prefixes["geopotential"],
                run,
                valid_time,
                pressure,
                "pressure",
            )
            u_levels.append(_align(base_u, u_field))
            v_levels.append(_align(base_u, v_field))
            z_values = _align(base_u, z_field)
            z_levels.append(_height_from_geopotential(z_values, z_field.units))

        heights = np.stack(z_levels)
        target = terrain + 6000.0
        ut = _interpolate_at_height(heights, np.stack(u_levels), target)
        vt = _interpolate_at_height(heights, np.stack(v_levels), target)

    shear = np.hypot(ut - u0, vt - v0)
    shear[~np.isfinite(shear)] = np.nan
    return RasterField(
        shear,
        base_u.transform,
        base_u.crs,
        base_u.bounds,
        "m/s",
        vector_u=ut - u0,
        vector_v=vt - v0,
    )


def _to_celsius(values: np.ndarray, units: str) -> np.ndarray:
    text = units.lower().strip()
    if text in {"k", "kelvin"} or float(np.nanmedian(values)) > 150:
        return values - 273.15
    return values


def _to_hpa(values: np.ndarray, units: str) -> np.ndarray:
    text = units.lower().replace(" ", "")
    if ("pa" in text and "hpa" not in text) or float(np.nanmedian(values)) > 2000:
        return values / 100.0
    return values


def _mixing_ratio_from_dewpoint(
    dewpoint_c: np.ndarray, pressure_hpa: np.ndarray
) -> np.ndarray:
    vapor_pressure = 6.112 * np.exp(17.67 * dewpoint_c / (dewpoint_c + 243.5))
    return np.divide(
        621.97 * vapor_pressure,
        pressure_hpa - vapor_pressure,
        out=np.full_like(dewpoint_c, np.nan, dtype=float),
        where=pressure_hpa > vapor_pressure,
    )


def _ship_formula(
    mucape: np.ndarray,
    mixing_ratio_gkg: np.ndarray,
    temperature_700_c: np.ndarray,
    temperature_500_c: np.ndarray,
    height_700_m: np.ndarray,
    height_500_m: np.ndarray,
    shear_0_6_ms: np.ndarray,
) -> np.ndarray:
    lapse_rate = np.divide(
        (temperature_700_c - temperature_500_c) * 1000.0,
        height_500_m - height_700_m,
        out=np.full_like(mucape, np.nan, dtype=float),
        where=(height_500_m - height_700_m) > 0,
    )
    ship = (
        np.maximum(mucape, 0.0)
        * np.maximum(mixing_ratio_gkg, 0.0)
        * np.maximum(lapse_rate, 0.0)
        * np.maximum(-temperature_500_c, 0.0)
        * np.maximum(shear_0_6_ms, 0.0)
        / SHIP_SCALE
    )
    ship[~np.isfinite(ship)] = np.nan
    return ship


def _compute_ship(
    client: AromeWCS,
    catalog: CoverageCatalog,
    prefixes: Dict[str, str],
    run: datetime,
    valid_time: datetime,
) -> RasterField:
    mucape_field = client.get_field(
        catalog, prefixes["cape_mu"], run, valid_time, None, None
    )
    dewpoint_field = client.get_field(
        catalog,
        prefixes["height_dewpoint"],
        run,
        valid_time,
        2.0,
        "height",
    )
    pressure_field = client.get_field(
        catalog, prefixes["surface_pressure"], run, valid_time, None, None
    )

    temperature_fields = {
        level: client.get_field(
            catalog,
            prefixes["pressure_temperature"],
            run,
            valid_time,
            float(level),
            "pressure",
        )
        for level in (700, 500)
    }
    geopotential_fields = {
        level: client.get_field(
            catalog,
            prefixes["geopotential"],
            run,
            valid_time,
            float(level),
            "pressure",
        )
        for level in (700, 500)
    }
    shear_field = _compute_shear(
        client, catalog, prefixes, run, valid_time, depth_m=6000
    )

    dewpoint_c = _to_celsius(
        _align(mucape_field, dewpoint_field), dewpoint_field.units
    )
    pressure_hpa = _to_hpa(
        _align(mucape_field, pressure_field), pressure_field.units
    )
    mixing_ratio = _mixing_ratio_from_dewpoint(dewpoint_c, pressure_hpa)
    temperature_700_c = _to_celsius(
        _align(mucape_field, temperature_fields[700]),
        temperature_fields[700].units,
    )
    temperature_500_c = _to_celsius(
        _align(mucape_field, temperature_fields[500]),
        temperature_fields[500].units,
    )
    height_700_m = _height_from_geopotential(
        _align(mucape_field, geopotential_fields[700]),
        geopotential_fields[700].units,
    )
    height_500_m = _height_from_geopotential(
        _align(mucape_field, geopotential_fields[500]),
        geopotential_fields[500].units,
    )
    shear = _align(mucape_field, shear_field)
    ship = _ship_formula(
        mucape_field.data,
        mixing_ratio,
        temperature_700_c,
        temperature_500_c,
        height_700_m,
        height_500_m,
        shear,
    )
    return RasterField(
        ship, mucape_field.transform, mucape_field.crs, mucape_field.bounds, ""
    )


def _catalonia_geometry(geojson: dict):
    geometries = [
        make_valid(shape(feature["geometry"])) for feature in geojson["features"]
    ]
    return unary_union(geometries)


def _mask_to_catalonia(field: RasterField, geometry) -> np.ndarray:
    lon, lat = _grid_lon_lat(field)
    inside = contains_xy(geometry, lon, lat)
    return np.where(inside, field.data, np.nan)


def _colorscale() -> List[List[object]]:
    return [[index / (len(PALETTE) - 1), color] for index, color in enumerate(PALETTE)]


def _geometry_lines(geometry) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    polygons: Iterable[Polygon]
    if isinstance(geometry, Polygon):
        polygons = [geometry]
    elif isinstance(geometry, MultiPolygon):
        polygons = geometry.geoms
    else:
        polygons = [part for part in geometry.geoms if isinstance(part, Polygon)]
    lons: List[Optional[float]] = []
    lats: List[Optional[float]] = []
    for polygon in polygons:
        coords = list(polygon.exterior.coords)
        lons.extend([point[0] for point in coords] + [None])
        lats.extend([point[1] for point in coords] + [None])
    return lons, lats


def _grid_cells_geojson(
    field: RasterField, valid: np.ndarray
) -> Tuple[dict, List[str]]:
    """Convierte las celdas válidas de la rejilla en polígonos contiguos."""
    height, width = field.data.shape
    transform = field.transform
    if abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12:
        raise AromeError("La rejilla AROME rotada no se puede representar en el mapa.")

    x_edges = transform.c + np.arange(width + 1, dtype=float) * transform.a
    y_edges = transform.f + np.arange(height + 1, dtype=float) * transform.e
    rows, cols = np.nonzero(valid)
    features = []
    locations: List[str] = []

    for row, col in zip(rows.tolist(), cols.tolist()):
        west, east = sorted((float(x_edges[col]), float(x_edges[col + 1])))
        south, north = sorted((float(y_edges[row]), float(y_edges[row + 1])))
        cell_id = f"{row}-{col}"
        locations.append(cell_id)
        features.append(
            {
                "type": "Feature",
                "id": cell_id,
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [west, south],
                            [east, south],
                            [east, north],
                            [west, north],
                            [west, south],
                        ]
                    ],
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}, locations


def _vector_arrow_lines(
    field: RasterField, geometry
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    if field.vector_u is None or field.vector_v is None:
        return [], []

    lon_grid, lat_grid = _grid_lon_lat(field)
    inside = contains_xy(geometry, lon_grid, lat_grid)
    magnitude = np.hypot(field.vector_u, field.vector_v)
    valid = inside & np.isfinite(magnitude) & (magnitude >= 0.5)
    step = max(1, int(round(field.data.shape[1] / 18)))
    rows, cols = np.nonzero(valid)
    sampled = (rows % step == step // 2) & (cols % step == step // 2)
    rows = rows[sampled]
    cols = cols[sampled]

    arrow_length = 0.10
    head_length = arrow_length * 0.32
    head_angle = np.deg2rad(150.0)
    lons: List[Optional[float]] = []
    lats: List[Optional[float]] = []

    for row, col in zip(rows.tolist(), cols.tolist()):
        center_lon = float(lon_grid[row, col])
        center_lat = float(lat_grid[row, col])
        east = float(field.vector_u[row, col] / magnitude[row, col])
        north = float(field.vector_v[row, col] / magnitude[row, col])
        lon_factor = max(float(np.cos(np.deg2rad(center_lat))), 0.2)

        start_lon = center_lon - 0.5 * arrow_length * east / lon_factor
        start_lat = center_lat - 0.5 * arrow_length * north
        tip_lon = center_lon + 0.5 * arrow_length * east / lon_factor
        tip_lat = center_lat + 0.5 * arrow_length * north

        for sign in (-1.0, 1.0):
            angle = sign * head_angle
            wing_east = east * np.cos(angle) - north * np.sin(angle)
            wing_north = east * np.sin(angle) + north * np.cos(angle)
            wing_lon = tip_lon + head_length * wing_east / lon_factor
            wing_lat = tip_lat + head_length * wing_north
            lons.extend([tip_lon, wing_lon, None])
            lats.extend([tip_lat, wing_lat, None])

        lons.extend([start_lon, tip_lon, None])
        lats.extend([start_lat, tip_lat, None])

    return lons, lats


def _map_title(label: str, valid_time: Optional[datetime]) -> str:
    if valid_time is None:
        return label
    local = valid_time.astimezone(LOCAL_TZ)
    return f"{label} · {local:%d/%m %H:%M} {local.tzname()}"


def _make_figure(
    field: RasterField,
    geometry,
    label: str,
    vmax: float,
    valid_time: Optional[datetime] = None,
    animation_fields: Optional[Sequence[Tuple[datetime, RasterField]]] = None,
    unit: str = "m/s",
    hover_label: str = "Cizalladura",
) -> go.Figure:
    values = _mask_to_catalonia(field, geometry)
    valid = np.isfinite(values)
    cells_geojson, locations = _grid_cells_geojson(field, valid)
    arrow_lon, arrow_lat = _vector_arrow_lines(field, geometry)
    has_vectors = bool(arrow_lon)
    boundary_lon, boundary_lat = _geometry_lines(geometry)

    figure = go.Figure()
    figure.add_trace(
        go.Choroplethmap(
            geojson=cells_geojson,
            locations=locations,
            z=values[valid],
            zmin=0,
            zmax=vmax,
            colorscale=_colorscale(),
            showscale=True,
            marker={"opacity": 0.88, "line": {"width": 0}},
            colorbar={
                "title": {"text": unit or hover_label},
                "thickness": 16,
                "len": 0.72,
                "x": 0.99,
                "y": 0.5,
            },
            hovertemplate=(
                f"{hover_label}: %{{z:.2f}}"
                + (f" {unit}" if unit else "")
                + "<extra></extra>"
            ),
            name=label,
            showlegend=False,
        )
    )
    if has_vectors:
        figure.add_trace(
            go.Scattermap(
                lon=arrow_lon,
                lat=arrow_lat,
                mode="lines",
                line={"color": "#111827", "width": 1.35},
                opacity=0.78,
                hoverinfo="skip",
                name="Dirección del vector de cizalladura",
                showlegend=False,
            )
        )
    figure.add_trace(
        go.Scattermap(
            lon=boundary_lon,
            lat=boundary_lat,
            mode="lines",
            line={"color": "#171717", "width": 2},
            hoverinfo="skip",
            name="Límites autonómicos",
            showlegend=False,
        )
    )
    figure.update_layout(
        title={
            "text": _map_title(label, valid_time),
            "x": 0.01,
            "xanchor": "left",
        },
        map={
            "style": "carto-positron",
            "center": {"lon": 0.65, "lat": 40.40},
            "zoom": 5.7,
        },
        height=690,
        margin={"l": 0, "r": 0, "t": 52, "b": 0},
        uirevision="nordeste-free-zoom-v1",
    )

    available_frames = sorted(animation_fields or [], key=lambda item: item[0])
    if len(available_frames) >= 2:
        frames = []
        steps = []
        active = 0
        for index, (frame_time, frame_field) in enumerate(available_frames):
            frame_name = frame_time.isoformat()
            local_frame_time = frame_time.astimezone(LOCAL_TZ)
            if valid_time == frame_time:
                active = index
            aligned = _align(field, frame_field)
            frame_values = aligned[valid]
            frame_data = [go.Choroplethmap(z=frame_values)]
            frame_traces = [0]
            if has_vectors:
                frame_arrow_lon, frame_arrow_lat = _vector_arrow_lines(
                    frame_field, geometry
                )
                frame_data.append(
                    go.Scattermap(lon=frame_arrow_lon, lat=frame_arrow_lat)
                )
                frame_traces.append(1)
            frames.append(
                go.Frame(
                    name=frame_name,
                    traces=frame_traces,
                    data=frame_data,
                    layout=go.Layout(
                        title={"text": _map_title(label, frame_time)}
                    ),
                )
            )
            steps.append(
                {
                    "label": f"{local_frame_time:%d/%m %H:%M}",
                    "method": "animate",
                    "args": [
                        [frame_name],
                        {
                            "mode": "immediate",
                            "frame": {"duration": 0, "redraw": True},
                            "transition": {"duration": 0},
                        },
                    ],
                }
            )

        figure.frames = frames
        figure.update_layout(
            height=750,
            margin={"l": 0, "r": 0, "t": 52, "b": 82},
            updatemenus=[
                {
                    "type": "buttons",
                    "direction": "left",
                    "x": 0.0,
                    "y": -0.075,
                    "showactive": False,
                    "buttons": [
                        {
                            "label": "▶",
                            "method": "animate",
                            "args": [
                                None,
                                {
                                    "fromcurrent": True,
                                    "mode": "immediate",
                                    "frame": {"duration": 750, "redraw": True},
                                    "transition": {"duration": 120},
                                },
                            ],
                        },
                        {
                            "label": "❚❚",
                            "method": "animate",
                            "args": [
                                [None],
                                {
                                    "mode": "immediate",
                                    "frame": {"duration": 0, "redraw": False},
                                    "transition": {"duration": 0},
                                },
                            ],
                        },
                    ],
                }
            ],
            sliders=[
                {
                    "active": active,
                    "x": 0.11,
                    "len": 0.87,
                    "y": -0.07,
                    "pad": {"t": 0, "b": 0},
                    "currentvalue": {"prefix": "Horas cargadas · "},
                    "steps": steps,
                }
            ],
        )
    return figure


def _format_slider_time(value: datetime, run: datetime) -> str:
    horizon = int((value - run).total_seconds() // 3600)
    local = value.astimezone(LOCAL_TZ)
    return f"H+{horizon:02d} · {local:%d/%m %H:%M} {local.tzname()}"


def _step_forecast_time(
    widget_key: str, times: Sequence[datetime], direction: int
) -> None:
    current = st.session_state.get(widget_key, times[0])
    try:
        index = list(times).index(current)
    except ValueError:
        index = 0
    st.session_state[widget_key] = times[
        max(0, min(len(times) - 1, index + direction))
    ]


def render_arome_forecast(*, embedded: bool = True) -> None:
    """Renderiza el visor AROME dentro de MeteoLabX o como app independiente."""
    if not embedded:
        st.set_page_config(
            page_title="Cizalladura AROME · Nordeste peninsular",
            page_icon="🌬️",
            layout="wide",
        )
        st.title("Cizalladura AROME · Nordeste peninsular")

    env_token = (
        os.getenv("METEOLABX_AROME_API_KEY")
        or os.getenv("AROME_API_KEY")
    )
    saved_token = None if env_token else _load_keychain_token()
    token = env_token or saved_token

    if saved_token:
        st.sidebar.success("Clave cargada automáticamente del llavero.")
        if st.sidebar.button("Cambiar clave", use_container_width=True):
            try:
                _delete_keychain_token()
            except AromeError as exc:
                st.sidebar.error(str(exc))
            else:
                st.rerun()

    if not token:
        entered_token = st.sidebar.text_input(
            "Nueva clave de Météo-France",
            type="password",
            help="Se guardará en el llavero seguro del sistema.",
        )
        if entered_token and st.sidebar.button(
            "Guardar y continuar", type="primary", use_container_width=True
        ):
            try:
                _save_keychain_token(entered_token)
            except AromeError as exc:
                st.sidebar.error(str(exc))
            else:
                st.rerun()

    if not token:
        st.info(
            "Introduce una clave nueva una sola vez. La aplicación la guardará "
            "en el llavero seguro y la cargará automáticamente en adelante."
        )
        st.stop()

    product_name = st.radio(
        "Mapa",
        options=list(PRODUCTS),
        horizontal=True,
        label_visibility="collapsed",
        key="arome_forecast_product",
    )
    config = PRODUCTS[product_name]
    product_kind = str(config["kind"])
    depth_m = int(config["depth_m"])

    try:
        client = AromeWCS(token)
        with st.spinner("Consultando el catálogo AROME…"):
            catalog = client.capabilities()
            prefixes = _resolved_prefixes(catalog, depth_m, product_kind)
            required_for_run = [
                prefix for key, prefix in prefixes.items() if key != "terrain"
            ]
            run = catalog.latest_common_run(required_for_run)
            reference_coverage = catalog.coverage_id(prefixes["height_u"], run)
            times = client.describe(reference_coverage).valid_times(run)

        if not times:
            raise AromeError("El run más reciente no contiene horas disponibles.")

        slider_key = f"forecast_time_{run:%Y%m%d%H}"
        if st.session_state.get(slider_key) not in times:
            st.session_state[slider_key] = times[0]
        selected_index = times.index(st.session_state[slider_key])

        previous_col, slider_col, next_col = st.columns(
            [1.4, 12, 1.4], vertical_alignment="bottom"
        )
        with previous_col:
            st.button(
                "← 1 h",
                key=f"previous_{run:%Y%m%d%H}",
                help="Cargar la hora anterior",
                use_container_width=True,
                disabled=selected_index == 0,
                on_click=_step_forecast_time,
                args=(slider_key, tuple(times), -1),
            )
        with slider_col:
            valid_time = st.select_slider(
                "Hora prevista",
                options=times,
                format_func=lambda value: _format_slider_time(value, run),
                key=slider_key,
            )
        with next_col:
            st.button(
                "1 h →",
                key=f"next_{run:%Y%m%d%H}",
                help="Cargar la hora siguiente",
                use_container_width=True,
                disabled=selected_index == len(times) - 1,
                on_click=_step_forecast_time,
                args=(slider_key, tuple(times), 1),
            )

        horizon = int((valid_time - run).total_seconds() // 3600)
        local_run = run.astimezone(LOCAL_TZ)
        local_valid = valid_time.astimezone(LOCAL_TZ)
        st.caption(
            f"AROME 0,025° · pasada {local_run:%d/%m/%Y %H:%M} "
            f"{local_run.tzname()} · válido {local_valid:%d/%m/%Y %H:%M} "
            f"{local_valid.tzname()} · H+{horizon:02d}"
        )

        run_cache_key = run.isoformat()
        if st.session_state.get("arome_field_cache_run") != run_cache_key:
            st.session_state["arome_field_cache_run"] = run_cache_key
            st.session_state["arome_field_cache"] = {}
        field_cache = st.session_state.setdefault("arome_field_cache", {})
        field_key = (product_name, valid_time.isoformat())

        with st.spinner(f"Calculando {product_name} para el dominio…"):
            if field_key not in field_cache:
                if product_kind == "ship":
                    field_cache[field_key] = _compute_ship(
                        client, catalog, prefixes, run, valid_time
                    )
                else:
                    field_cache[field_key] = _compute_shear(
                        client, catalog, prefixes, run, valid_time, depth_m
                    )
            field = field_cache[field_key]
            boundary_geojson = _load_catalonia_geojson()
            boundary = _catalonia_geometry(boundary_geojson)
            animation_fields = [
                (time_value, field_cache[(product_name, time_value.isoformat())])
                for time_value in times
                if (product_name, time_value.isoformat()) in field_cache
            ]
            figure = _make_figure(
                field,
                boundary,
                (
                    "SHIP AROME · MUCAPE con arrastre"
                    if product_kind == "ship"
                    else f"{product_name} · magnitud vectorial"
                ),
                float(config["vmax"]),
                valid_time=valid_time,
                animation_fields=animation_fields,
                unit="" if product_kind == "ship" else "m/s",
                hover_label="SHIP" if product_kind == "ship" else "Cizalladura",
            )

        loaded_count = len(animation_fields)
        if loaded_count == 1:
            st.caption(
                "1 hora cargada · usa las flechas para cargar otra; después "
                "aparecerán reproducción y animación bajo el mapa."
            )
        else:
            st.caption(
                f"{loaded_count} horas cargadas · usa ▶ y ❚❚ bajo el mapa "
                "para reproducirlas sin nuevas consultas."
            )

        st.plotly_chart(
            figure,
            use_container_width=True,
            config={"scrollZoom": True, "displaylogo": False},
            key=f"shear_map_{product_name}_{run:%Y%m%d%H}",
        )
        if product_kind == "ship":
            st.caption(
                "SHIP AROME usa la MUCAPE con arrastre publicada por AROME, "
                "la razón de mezcla aproximada con el punto de rocío a 2 m, "
                "el gradiente 700–500 hPa, T500 y la cizalladura 0–6 km. Es "
                "una adaptación y no el SHIP SPC idéntico."
            )
        else:
            st.caption(
                "La cizalladura es |Vₕ−V₁₀ₘ| en m/s. Para 0–6 km, el viento "
                "superior se interpola a terreno + 6000 m usando niveles "
                "isobáricos y geopotencial. Las flechas indican la dirección "
                "del vector Vₕ−V₁₀ₘ."
            )
    except AromeError as exc:
        st.error(str(exc))
        st.stop()
    except requests.RequestException as exc:
        st.error(f"No se pudo cargar la cartografía oficial: {exc}")
        st.stop()


def _launch_streamlit() -> int:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        "--server.headless=false",
        "--",
        "--streamlit-child",
    ]
    return subprocess.call(command, cwd=str(APP_DIR))


if __name__ == "__main__":
    if "--streamlit-child" in sys.argv:
        render_arome_forecast(embedded=False)
    else:
        raise SystemExit(_launch_streamlit())
