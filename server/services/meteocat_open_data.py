"""Cliente de Dades Obertes de la Generalitat para observaciones XEMA.

El dataset Socrata ``nzvn-apee`` publica las mismas mediciones de periodo que
XEMA, sin consumir la cuota mensual de la API de Meteocat. Las fechas vienen
en UTC, sin sufijo de zona, y etiquetan el inicio del periodo.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from server.schemas.errors import ProviderError


PROVIDER = "METEOCAT"
BASE_URL = "https://analisi.transparenciacatalunya.cat"
MEASURED_DATASET = "nzvn-apee"
RESOURCE_URL = f"{BASE_URL}/resource/{MEASURED_DATASET}.json"
PAGE_SIZE = 50_000

logger = logging.getLogger(__name__)

VarMap = Dict[int, List[Tuple[int, float]]]
StationVarMaps = Dict[str, VarMap]


def _socrata_datetime(dt: datetime) -> str:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_epoch(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _parse_rows(rows: Any) -> StationVarMaps:
    out: StationVarMaps = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = str(row.get("codi_estacio") or "").strip().upper()
        try:
            variable = int(row.get("codi_variable"))
            value = float(row.get("valor_lectura"))
        except (TypeError, ValueError):
            continue
        epoch = _row_epoch(row.get("data_lectura"))
        if not station or epoch is None:
            continue
        out.setdefault(station, {}).setdefault(variable, []).append((epoch, value))
    for variables in out.values():
        for samples in variables.values():
            samples.sort()
    return out


def _merge_maps(target: StationVarMaps, incoming: StationVarMaps) -> None:
    for station, variables in incoming.items():
        for variable, samples in variables.items():
            target.setdefault(station, {}).setdefault(variable, []).extend(samples)


def _error_from_status(status_code: int, detail: str) -> ProviderError:
    if status_code == 429:
        return ProviderError(
            "provider_ratelimit", provider=PROVIDER, detail=detail, status_code=429,
        )
    return ProviderError(
        "provider_bad_response", provider=PROVIDER, detail=detail, status_code=502,
    )


async def fetch_variable_maps(
    start_utc: datetime,
    end_utc: datetime,
    variable_codes: List[int],
    *,
    station_id: str = "",
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
) -> StationVarMaps:
    """Descarga mediciones XEMA planas y las agrupa por estación/variable."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)

    codes = sorted({int(code) for code in variable_codes})
    clauses = [
        f"data_lectura >= '{_socrata_datetime(start_utc)}'",
        f"data_lectura <= '{_socrata_datetime(end_utc)}'",
        # Los códigos van ENTRECOMILLADOS: en este dataset `codi_variable` es
        # una columna de texto, no numérica, y Socrata rechaza la consulta
        # entera con «Type mismatch for #IN, is number» si se le pasan números.
        # El fallback fallaba siempre por esto, así que al agotar la cuota de
        # XEMA se propagaba el 429 en vez de servir por Dades Obertes.
        f"codi_variable in ({','.join(chr(39) + str(code) + chr(39) for code in codes)})",
    ]
    station = str(station_id or "").strip().upper()
    if station:
        safe_station = station.replace("'", "''")
        clauses.append(f"codi_estacio = '{safe_station}'")

    result: StationVarMaps = {}
    offset = 0
    try:
        while True:
            params = {
                "$select": "codi_estacio,codi_variable,data_lectura,valor_lectura",
                "$where": " AND ".join(clauses),
                "$order": "data_lectura,codi_estacio,codi_variable",
                "$limit": str(PAGE_SIZE),
                "$offset": str(offset),
            }
            try:
                response = await client.get(RESOURCE_URL, params=params, timeout=timeout_s)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    "provider_timeout", provider=PROVIDER,
                    detail=f"Dades Obertes timeout: {exc}", status_code=504,
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderError(
                    "provider_network_error", provider=PROVIDER,
                    detail=f"Dades Obertes network error: {exc}", status_code=502,
                ) from exc

            if response.status_code >= 400:
                raise _error_from_status(
                    response.status_code,
                    f"Dades Obertes HTTP {response.status_code}: {response.text[:300]}",
                )
            try:
                rows = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "provider_bad_response", provider=PROVIDER,
                    detail="Dades Obertes devolvió JSON inválido", status_code=502,
                ) from exc
            if not isinstance(rows, list):
                raise ProviderError(
                    "provider_bad_response", provider=PROVIDER,
                    detail="Dades Obertes no devolvió una lista", status_code=502,
                )
            _merge_maps(result, _parse_rows(rows))
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    finally:
        if owns_client:
            await client.aclose()

    # Una página posterior podría repetir el límite de la anterior si el
    # dataset se actualiza durante la descarga. Deduplicamos por instante.
    for variables in result.values():
        for code, samples in variables.items():
            variables[code] = sorted(dict(samples).items())
    return result


async def fetch_station_day_var_map(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> VarMap:
    """Todas las variables usadas por la ficha durante el día civil catalán."""
    from server.services import meteocat

    now_local = (now or datetime.now(tz=meteocat.CAT_TZ)).astimezone(meteocat.CAT_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    maps = await fetch_variable_maps(
        start_local,
        now_local,
        sorted(meteocat.RELEVANT_VARIABLES),
        station_id=station_id,
        client=client,
        timeout_s=timeout_s,
    )
    return maps.get(str(station_id).strip().upper(), {})


# =====================================================================
# Resúmenes diarios (dataset 7bvh-jvq2)
# =====================================================================

DAILY_DATASET = "7bvh-jvq2"
DAILY_RESOURCE_URL = f"{BASE_URL}/resource/{DAILY_DATASET}.json"
# Sin cuota, pero un bloque de veinte años en una sola consulta tarda 26 s y
# topa con el límite de página; uno por año, de seis en seis, se queda en 1,6.
DAILY_CONCURRENCY = 6

DailyValues = Dict[int, Dict[str, float]]


async def fetch_daily_values(
    station_id: str,
    start: date,
    end: date,
    variable_codes: List[int],
    *,
    client: httpx.AsyncClient,
    timeout_s: float = 30.0,
) -> DailyValues:
    """Resúmenes diarios de una estación: ``{codi_variable: {YYYY-MM-DD: valor}}``.

    Es la misma tabla, con los mismos códigos (1000, 1001, 1300, 1503…), que
    los endpoints ``estadistics/diaris`` de XEMA, pero sin partirse en meses
    ni gastar cuota: cualquier rango de fechas devuelve días.

    Se descartan las lecturas marcadas «No representatiu». Algunos días traen
    ``nom_variable`` mal codificado; no son duplicados sino días que no están
    con el nombre bueno, así que se agrupa por fecha y código, nunca por nombre.
    """
    station = str(station_id or "").strip().upper()
    codes = sorted({int(code) for code in variable_codes})
    if not station or not codes or end < start:
        return {}
    clauses = [
        f"codi_estacio = '{station.replace(chr(39), chr(39) * 2)}'",
        f"data_lectura >= '{start.isoformat()}T00:00:00'",
        f"data_lectura <= '{end.isoformat()}T00:00:00'",
        # Texto, no número: igual que en el dataset semihorario.
        f"codi_variable in ({','.join(chr(39) + str(code) + chr(39) for code in codes)})",
    ]
    out: DailyValues = {}
    offset = 0
    while True:
        params = {
            "$select": "data_lectura,codi_variable,valor,estat",
            "$where": " AND ".join(clauses),
            "$order": "data_lectura,codi_variable",
            "$limit": str(PAGE_SIZE),
            "$offset": str(offset),
        }
        try:
            response = await client.get(DAILY_RESOURCE_URL, params=params, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", provider=PROVIDER,
                detail=f"Dades Obertes (diaris) timeout: {exc}", status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error", provider=PROVIDER,
                detail=f"Dades Obertes (diaris) network error: {exc}", status_code=502,
            ) from exc
        if response.status_code >= 400:
            raise _error_from_status(
                response.status_code,
                f"Dades Obertes (diaris) HTTP {response.status_code}: {response.text[:300]}",
            )
        try:
            rows = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response", provider=PROVIDER,
                detail="Dades Obertes (diaris) devolvió JSON inválido", status_code=502,
            ) from exc
        if not isinstance(rows, list):
            raise ProviderError(
                "provider_bad_response", provider=PROVIDER,
                detail="Dades Obertes (diaris) no devolvió una lista", status_code=502,
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("estat") or "").strip().lower() == "no representatiu":
                continue
            day = str(row.get("data_lectura") or "")[:10]
            try:
                code = int(row.get("codi_variable"))
                value = float(row.get("valor"))
            except (TypeError, ValueError):
                continue
            if len(day) != 10 or value != value:
                continue
            out.setdefault(code, {})[day] = value
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out
