"""
Router de datasets históricos / climogramas.

Cada proveedor tiene un port async puro de su climo en
``server/services/*_climo.py``; el endpoint despacha a su rama en
``_run_async_port`` (hecho: WU, AEMET, METEOCAT, METEOFRANCE,
METEOGALICIA, FROST). El antiguo dispatcher legacy en threadpool
(``utils.historical_dispatch``) ya no se usa desde aquí.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends

from server.config import Settings, get_settings
from server.dependencies.http import get_http_client, get_series_cache
from server.schemas.climo import (
    ClimoChartSeries,
    ClimoHistogramSeries,
    ClimoTemperatureDistribution,
    ClimoWindSeries,
    ClimoDetails,
    ClimoPeriod,
    ClimoMetricRow,
    ClimoSummaryRequest,
    ClimoSummaryResponse,
    ClimoTable,
    ClimoDatasetRequest,
    ClimoDatasetResponse,
    FrostPeriodOptionsRequest,
    FrostPeriodOptionsResponse,
)
from server.schemas.errors import ErrorResponse, ProviderError
from server.services.cache import AsyncTTLCache, make_cache_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/climo", tags=["climo"])

# Proveedores con datos históricos/climogramas.
CLIMO_PROVIDERS = (
    "WU", "AEMET", "METEOCAT", "METEOFRANCE", "METEOGALICIA", "FROST",
    "WEATHERLINK", "IEM", "GEOSPHERE", "SMHI", "ECCC",
)


def _dataset_cache_ttl_s(body: ClimoDatasetRequest) -> float:
    """Mantiene mucho tiempo las consultas cerradas y refresca la actual."""
    requested_ends = [period.end for period in body.periods]
    if body.selected_years:
        requested_ends.extend(date(int(year), 12, 31) for year in body.selected_years)
    if requested_ends and max(requested_ends) < date.today():
        return 30 * 24 * 60 * 60
    return 60 * 60


def _serialize_dataset(dataset: Any) -> Optional[str]:
    """DataFrame → JSON ``orient="table"`` (round-trip con dtypes)."""
    try:
        import pandas as pd
    except Exception:  # pragma: no cover - pandas siempre está
        return None
    if not isinstance(dataset, pd.DataFrame) or dataset.empty:
        return None
    return dataset.reset_index(drop=True).to_json(orient="table", date_format="iso")


async def _run_async_port(
    body: ClimoDatasetRequest, client: httpx.AsyncClient, settings: Settings,
) -> Tuple[Any, Any]:
    if body.provider == "METEOCAT":
        from server.services import meteocat_climo

        return await meteocat_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.meteocat_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
    if body.provider == "METEOGALICIA":
        from server.services import meteogalicia_climo

        dataset = await meteogalicia_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "WU":
        from server.services import wu_climo

        dataset = await wu_climo.fetch_climo_daily_for_periods(
            client,
            body.station_id,
            body.api_key or "",
            [(p.start, p.end) for p in body.periods],
        )
        return dataset, None
    if body.provider == "WEATHERLINK":
        from server.services import weatherlink_climo

        dataset = await weatherlink_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or "",
            body.api_secret or "",
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "GEOSPHERE":
        from server.services import geosphere_climo

        dataset = await geosphere_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "ECCC":
        from server.services import eccc_climo

        dataset = await eccc_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "SMHI":
        from server.services import smhi_climo

        dataset = await smhi_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "IEM":
        from server.services import iem_climo

        dataset = await iem_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "AEMET":
        from server.services import aemet_climo

        dataset = await aemet_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.aemet_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "METEOFRANCE":
        from server.services import meteofrance_climo

        dataset = await meteofrance_climo.fetch_climo_dataset(
            client,
            body.station_id,
            body.api_key or settings.meteofrance_api_key,
            summary_mode=body.summary_mode,
            periods=[(p.start, p.end) for p in body.periods],
            selected_years=[int(y) for y in body.selected_years],
        )
        return dataset, None
    if body.provider == "FROST":
        from server.services import frost_climo

        dataset = await frost_climo.fetch_climo_dataset(
            client,
            body.station_id,
            summary_mode=body.summary_mode,
            selected_months=[int(m) for m in body.selected_months],
            frost_period=body.frost_period,
            frost_periods=list(body.frost_periods),
            client_id=settings.frost_client_id,
            client_secret=settings.frost_client_secret,
        )
        return dataset, None
    raise ProviderError(  # pragma: no cover - el endpoint valida contra CLIMO_PROVIDERS
        "unsupported_provider", provider=body.provider, status_code=400,
    )


def ensure_periods(body: ClimoDatasetRequest) -> None:
    """Rellena ``periods`` a partir de los meses y años pedidos.

    El cliente puede mandar la selección tal cual la hizo el usuario —«agosto
    de 2024, 2025 y 2026»— y dejar que el servidor la convierta en bloques de
    fechas. Es la misma construcción que usa la app actual, así que las dos
    piden exactamente los mismos días. Frost queda fuera: sus periodos son
    normales climáticas («1991/2020»), no rangos de calendario.
    """
    if body.periods or not body.selected_years or body.provider == "FROST":
        return
    from domain.climograms import build_period_specs, clip_periods_to_today

    specs = clip_periods_to_today(
        build_period_specs(body.summary_mode, body.selected_years, body.selected_months)
    )
    body.periods = [
        ClimoPeriod(label=spec.label, start=spec.start, end=spec.end) for spec in specs
    ]


async def _fetch_dataset(
    body: ClimoDatasetRequest,
    settings: Settings,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    try:
        dataset, extremes = await _run_async_port(body, client, settings)
    except ProviderError:
        raise
    except Exception as exc:
        logger.warning(
            "Climo async falló para %s/%s: %s",
            body.provider, body.station_id, exc,
        )
        raise ProviderError(
            "provider_bad_response",
            provider=body.provider,
            detail=f"Climo dispatch failed: {exc}",
            status_code=502,
        ) from exc

    serialized = _serialize_dataset(dataset)
    return {
        "dataset": serialized,
        "extremes": extremes if isinstance(extremes, dict) and extremes else None,
        "has_data": serialized is not None,
    }


@router.post(
    "/dataset",
    response_model=ClimoDatasetResponse,
    summary="Dataset histórico / de climograma",
    description=(
        "Devuelve el dataset diario/mensual/anual que alimenta la pestaña "
        "de Históricos y Climogramas, en el esquema de columnas común "
        "(date, epoch, temp_mean/max/min, wind_mean, gust_max, "
        "precip_total + extras por proveedor), serializado como JSON "
        "``orient='table'`` de pandas. Proveedores: "
        "``WU``, ``AEMET``, ``METEOCAT``, ``METEOFRANCE``, "
        "``METEOGALICIA``, ``FROST``, ``WEATHERLINK``, ``IEM``, "
        "``GEOSPHERE``, ``SMHI`` y ``ECCC``."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Proveedor sin datos históricos."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
    },
)
async def post_climo_dataset(
    body: ClimoDatasetRequest,
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> ClimoDatasetResponse:
    if body.provider not in CLIMO_PROVIDERS:
        raise ProviderError(
            "unsupported_provider",
            provider=body.provider,
            detail=f"Historical dataset not available for provider: {body.provider}",
            status_code=400,
        )

    ensure_periods(body)
    fingerprint = "|".join(
        [
            body.summary_mode,
            ",".join(f"{p.start}:{p.end}" for p in body.periods),
            ",".join(str(y) for y in body.selected_years),
            ",".join(str(m) for m in body.selected_months),
            body.frost_period,
            ",".join(body.frost_periods),
        ]
    )
    key = make_cache_key(
        body.provider,
        f"climo_{fingerprint}",
        body.station_id,
        f"{body.api_key}:{body.api_secret}" if body.api_secret else body.api_key or "server",
    )
    raw = await cache.get_or_fetch(
        key,
        lambda: _fetch_dataset(body, settings, client),
        ttl_s=_dataset_cache_ttl_s(body),
    )
    return ClimoDatasetResponse(**raw)


# ``services.climograms`` traduce las etiquetas con ``utils.i18n.t``, que lee el
# idioma de un estado global heredado de Streamlit. Fuera de Streamlit funciona,
# pero es global al proceso: dos peticiones en idiomas distintos podrían pisarse.
# El candado serializa el tramo que fija idioma y construye tablas, que son
# milisegundos de pandas una vez el dataset está en memoria.
_LANGUAGE_LOCK = threading.Lock()


def _solar_metric_kind(provider: str) -> str:
    """Cada red publica el sol de una forma distinta; el nombre de la métrica cambia."""
    if provider == "METEOCAT":
        return "irradiation"
    if provider == "WEATHERLINK":
        return "irradiance"
    return "sunshine_hours"


def _row_date(rows: list[ClimoMetricRow], key: str) -> str:
    """Fecha de una métrica concreta, o cadena vacía si no vino."""
    return next((row.date for row in rows if row.key == key), "")


def _metric_keys_by_label() -> dict[str, str]:
    """Etiqueta traducida → clave canónica.

    La tabla se construye ya traducida, así que la clave hay que recuperarla
    del catálogo. Se recalcula en cada resumen porque depende del idioma
    activo, y el idioma se fija justo antes bajo ``_LANGUAGE_LOCK``.
    """
    from domain.climograms import METRIC_LABEL_KEYS
    from utils.i18n import t

    # Las dos métricas solares tienen nombre propio según lo que mida la red
    # —irradiación o insolación—, pero son el mismo hito: el año con más sol y
    # el año con menos. Comparten clave para poder emparejarse en una tarjeta.
    aliases = {
        "highest_solar_irradiation_year": "sunniest_year",
        "lowest_solar_irradiation_year": "least_sunny_year",
    }

    keys: dict[str, str] = {}
    for i18n_key in METRIC_LABEL_KEYS.values():
        canonical = str(i18n_key).rsplit(".", 1)[-1]
        label = str(t(i18n_key, default="")).strip()
        if label:
            keys[label] = aliases.get(canonical, canonical)
    return keys


def _rows_from_table(frame, keys_by_label: dict[str, str] | None = None) -> list[ClimoMetricRow]:
    """DataFrame de (métrica, valor[, fecha]) → filas serializables."""
    if frame is None or frame.empty or len(frame.columns) < 2:
        return []
    metric_col, value_col = frame.columns[0], frame.columns[1]
    date_col = frame.columns[2] if len(frame.columns) > 2 else None
    rows: list[ClimoMetricRow] = []
    for _, row in frame.iterrows():
        value = str(row.get(value_col, "") or "").strip()
        # Las filas sin dato se descartan aquí y no en el frontend: son huecos
        # del proveedor, no tarjetas que haya que pintar vacías.
        if value in ("", "-", "—", "nan", "None"):
            continue
        label = str(row.get(metric_col, "") or "").strip()
        rows.append(
            ClimoMetricRow(
                key=(keys_by_label or {}).get(label, ""),
                metric=label,
                value=value,
                date=str(row.get(date_col, "") or "").strip() if date_col is not None else "",
            )
        )
    return rows


def _series(frame, column: str) -> list:
    import pandas as pd

    if frame is None or frame.empty or column not in frame.columns:
        return []
    values = pd.to_numeric(frame[column], errors="coerce")
    return [None if pd.isna(value) else round(float(value), 2) for value in values]


# Columnas de la tabla, en el mismo orden y con las mismas etiquetas que la
# app actual. Se resuelven dentro del candado de idioma, junto al resto.
_TABLE_COLUMNS = ("label", "temp_abs_max", "temp_abs_min", "temp_mean", "precip_total")


def _localized_table(table, body: ClimoSummaryRequest, granularity: str, units: Dict[str, str]):
    """Cabeceras traducidas con su unidad y valores con un decimal."""
    import pandas as pd

    from utils.i18n import t

    if table is None or table.empty:
        return [], []

    temp_unit = units.get("temperature") or "°C"
    precip_unit = units.get("precipitation") or "mm"
    # Mismas claves que ``_historical_chart_scope``: «Día», «Mes», «Año» —o
    # «Periodo climático» cuando la fuente son las normales de Frost.
    if granularity == "daily":
        period_key = "historical.table.period_col.day"
    elif granularity == "monthly":
        period_key = "historical.table.period_col.month"
    elif body.provider == "FROST":
        period_key = "historical.table.period_col.climate_period"
    else:
        period_key = "historical.table.period_col.year"

    def with_unit(label: str, unit: str) -> str:
        return f"{label} ({unit})" if unit else label

    headers = [
        t(period_key),
        with_unit(t("historical.table.columns.temp_abs_max"), temp_unit),
        with_unit(t("historical.table.columns.temp_abs_min"), temp_unit),
        with_unit(t("historical.table.columns.temp_mean"), temp_unit),
        with_unit(t("historical.table.columns.precip"), precip_unit),
    ]

    rows = []
    for _, row in table.iterrows():
        cells = [str(row.get("label", ""))]
        for column in _TABLE_COLUMNS[1:]:
            value = pd.to_numeric(row.get(column), errors="coerce")
            cells.append("—" if pd.isna(value) else f"{float(value):.1f}")
        rows.append(cells)
    return headers, rows


def _build_summary(body: ClimoSummaryRequest, raw: Dict[str, Any]) -> ClimoSummaryResponse:
    import pandas as pd
    from io import StringIO

    from domain import climograms
    from utils.i18n import set_language

    serialized = raw.get("dataset")
    if not serialized:
        return ClimoSummaryResponse(has_data=False, summary_mode=body.summary_mode)

    daily = pd.read_json(StringIO(serialized), orient="table")
    if daily.empty:
        return ClimoSummaryResponse(has_data=False, summary_mode=body.summary_mode)

    units = dict(body.unit_preferences or {})
    solar_kind = _solar_metric_kind(body.provider)
    period_count = len(body.periods)

    with _LANGUAGE_LOCK:
        set_language(body.language)

        extremes = climograms.build_extremes_table(
            daily,
            overrides=raw.get("extremes") or None,
            unit_preferences=units,
            summary_mode=body.summary_mode,
            period_count=period_count,
            include_daily_temperature_extremes=(
                body.summary_mode == "annual"
                and period_count > 1
                and body.provider in {"WU", "IEM"}
            ),
            solar_metric_kind=solar_kind,
        )
        general = climograms.build_general_metrics_table(
            daily, unit_preferences=units, solar_metric_kind=solar_kind
        )

        # Frost sirve normales climáticas, no días sueltos: su climograma va
        # por mes o por año según el modo, sin pasar por la regla general.
        if body.provider == "FROST":
            granularity = "monthly" if body.summary_mode == "monthly" else "yearly"
        else:
            granularity = climograms.resolve_chart_granularity(body.summary_mode, period_count)

        chart = climograms.build_chart_table(daily, granularity, unit_preferences=units)
        wind_chart = climograms.build_wind_chart_table(daily, granularity, unit_preferences=units)
        # El modo mensual de las redes compatibles conserva los días aunque
        # luego el climograma se agrupe por mes. El anual, en cambio, solo
        # trae resúmenes y no debe fingir un histograma diario.
        temperature_distribution = (
            climograms.build_temperature_distribution(
                daily,
                unit_preferences=units,
                shared_bounds=period_count > 1,
            )
            if body.provider != "FROST" and body.summary_mode != "annual"
            else {}
        )
        table = climograms.build_units_table(daily, granularity, unit_preferences=units)

        keys_by_label = _metric_keys_by_label()
        extreme_rows = _rows_from_table(extremes, keys_by_label)
        general_rows = _rows_from_table(general, keys_by_label)

        # Las tarjetas de viento y lluvia enseñan de dónde soplaba y con qué
        # intensidad llovió; eso está en el dataset diario, no en la tabla.
        from domain import historical_details

        details = historical_details.build_details(
            daily,
            overrides=raw.get("extremes") or None,
            unit_preferences=units,
            windiest_day_date=_row_date(extreme_rows, "windiest_day"),
            windiest_month_date=_row_date(extreme_rows, "windiest_month"),
        )

    labels = [str(value) for value in chart["label"]] if not chart.empty else []
    table_columns, table_rows = _localized_table(table, body, granularity, units)
    expected_days = _requested_day_count(body)

    def histogram(name: str) -> ClimoHistogramSeries:
        return ClimoHistogramSeries(**(temperature_distribution.get(name) or {}))

    return ClimoSummaryResponse(
        has_data=True,
        summary_mode=body.summary_mode,
        granularity=granularity,
        general=general_rows,
        extremes=extreme_rows,
        chart=ClimoChartSeries(
            labels=labels,
            temp_mean=_series(chart, "temp_mean"),
            temp_max=_series(chart, "temp_max"),
            temp_min=_series(chart, "temp_min"),
            precip_total=_series(chart, "precip_total"),
        ),
        wind=ClimoWindSeries(
            labels=[str(value) for value in wind_chart["label"]] if not wind_chart.empty else [],
            wind_mean=_series(wind_chart, "wind_mean"),
            gust_max=_series(wind_chart, "gust_max"),
            direction=_series(wind_chart, "dir_deg"),
            direction_kind=str(wind_chart["dir_kind"].iloc[0]) if not wind_chart.empty else "",
            unit=units.get("wind", "km/h"),
        ),
        temperature_distribution=ClimoTemperatureDistribution(
            temp_max=histogram("temp_max"),
            temp_min=histogram("temp_min"),
            temp_mean=histogram("temp_mean"),
            expected_days=expected_days,
            unit=str(temperature_distribution.get("unit") or "°C"),
            bin_width=float(temperature_distribution.get("bin_width") or 2.0),
        ),
        table=ClimoTable(columns=table_columns, rows=table_rows),
        units=units,
        details=ClimoDetails(**details),
        period_count=period_count,
        annual_comparison=body.summary_mode == "annual" and period_count > 1,
        solar_metric_kind=solar_kind,
    )


def _requested_day_count(body: ClimoSummaryRequest) -> int:
    """Número de fechas distintas pedidas, sin inflar periodos solapados."""
    today = date.today()
    spans = sorted(
        (period.start, min(period.end, today))
        for period in body.periods
        if period.start <= min(period.end, today)
    )
    if not spans and body.selected_years:
        spans = sorted(
            (date(int(year), 1, 1), min(date(int(year), 12, 31), today))
            for year in set(body.selected_years)
            if date(int(year), 1, 1) <= today
        )
    if not spans:
        return 0

    merged: List[Tuple[date, date]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum((end - start).days + 1 for start, end in merged)


@router.post(
    "/summary",
    response_model=ClimoSummaryResponse,
    summary="Histórico resumido: tarjetas, climograma y tabla",
    description=(
        "Devuelve la pestaña de Histórico ya resuelta: métricas generales y "
        "extremos con la etiqueta traducida y el valor formateado, las series "
        "del climograma y la tabla completa.\n\n"
        "El cálculo es el mismo de ``services/climograms.py`` que usa la app "
        "actual, así que las dos interfaces enseñan exactamente los mismos "
        "números."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Proveedor sin datos históricos."},
        502: {"model": ErrorResponse, "description": "Error upstream del proveedor."},
    },
)
async def post_climo_summary(
    body: ClimoSummaryRequest,
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> ClimoSummaryResponse:
    if body.provider not in CLIMO_PROVIDERS:
        raise ProviderError(
            "unsupported_provider",
            provider=body.provider,
            detail=f"Historical dataset not available for provider: {body.provider}",
            status_code=400,
        )

    ensure_periods(body)
    dataset_body = ClimoDatasetRequest(**body.model_dump(include=set(ClimoDatasetRequest.model_fields)))
    fingerprint = "|".join(
        [
            body.summary_mode,
            ",".join(f"{p.start}:{p.end}" for p in body.periods),
            ",".join(str(y) for y in body.selected_years),
            ",".join(str(m) for m in body.selected_months),
            body.frost_period,
            ",".join(body.frost_periods),
        ]
    )
    key = make_cache_key(
        body.provider,
        f"climo_{fingerprint}",
        body.station_id,
        f"{body.api_key}:{body.api_secret}" if body.api_secret else body.api_key or "server",
    )
    raw = await cache.get_or_fetch(
        key,
        lambda: _fetch_dataset(dataset_body, settings, client),
        ttl_s=_dataset_cache_ttl_s(dataset_body),
    )
    return await asyncio.to_thread(_build_summary, body, raw)


@router.post(
    "/frost/period-options",
    response_model=FrostPeriodOptionsResponse,
    summary="Periodos de normales climáticas disponibles (Frost)",
    description=(
        "Periodos de normales (p. ej. ``1991/2020``) que la estación Frost "
        "publica con datos, separados en mensual/anual, para poblar el "
        "selector de la pestaña de climogramas."
    ),
    responses={502: {"model": ErrorResponse, "description": "Error upstream del proveedor."}},
)
async def post_frost_period_options(
    body: FrostPeriodOptionsRequest,
    cache: AsyncTTLCache[dict] = Depends(get_series_cache),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> FrostPeriodOptionsResponse:
    from server.services import frost_climo

    async def _fetch() -> Dict[str, Any]:
        return await frost_climo.fetch_period_options(
            client,
            body.station_id,
            client_id=settings.frost_client_id,
            client_secret=settings.frost_client_secret,
        )

    key = make_cache_key("FROST", "frost_period_options", body.station_id, "server")
    raw = await cache.get_or_fetch(key, _fetch)
    return FrostPeriodOptionsResponse(**raw)
