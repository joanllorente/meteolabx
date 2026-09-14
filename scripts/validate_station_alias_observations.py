#!/usr/bin/env python3
"""Validate station alias candidates with same-hour observations."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_station_aliases import DEFAULT_DATABASE, EXCLUDED_CANONICAL_PROVIDERS
from server.config import get_settings
from server.routers.observations import _resolve_provider_fetchers
from server.schemas.observation import TodaySeriesRequest


IEM_HISTORY_URL = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"
CHECK_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_alias_observation_checks (
    check_pk INTEGER PRIMARY KEY,
    alias_pk INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'conflict', 'inconclusive', 'error')),
    matched_hours INTEGER NOT NULL DEFAULT 0,
    compared_values INTEGER NOT NULL DEFAULT 0,
    agreeing_values INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
    FOREIGN KEY (alias_pk) REFERENCES station_aliases(alias_pk) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alias_observation_checks_alias
ON station_alias_observation_checks(alias_pk, checked_at);
"""
# temperature_c 0.3: IEM almacena °F y el ida-y-vuelta °C→°F→°C mete ±0,1-0,3
# de ruido en pares BUFR con décimas; los feeds exactos (FROST↔WMO) casan a
# 0,0-0,1 así que no pierde discriminación. humidity_pct 2.0: la RH de BUFR
# se deriva de T/Td redondeados.
TOLERANCES = {"temperature_c": 0.3, "humidity_pct": 2.0, "wind_kmh": 0.5, "wind_dir_deg": 3.0}
MIN_MATCHED_HOURS = 12
MIN_TEMPERATURE_VALUES = 12
MIN_TOTAL_VALUES = 24
MIN_TEMPERATURE_RATIO = 0.90
MIN_MULTIVARIABLE_RATIO = 0.85

# Paso de cuantización por variable cuando la serie IEM viene redondeada en
# origen: los METAR (redes *_ASOS) publican temperatura en °C ENTEROS y
# viento en nudos enteros. Contra una fuente con décimas (AEMET 18,4 °C vs
# METAR 18) la tolerancia base es inalcanzable aunque sea la MISMA estación:
# se suma medio paso al detectar la rejilla.
QUANTIZATION_STEPS = {
    "temperature_c": (1.0, 0.5),          # °C enteros (METAR) o medios grados
    "wind_kmh": (1.852, 0.926),           # nudos enteros o medios nudos
    "humidity_pct": (1.0,),
    "wind_dir_deg": (10.0,),              # METAR reporta dirección en decenas
}


def _detect_quantization(values: list[float], steps: tuple[float, ...]) -> float:
    """Mayor paso de rejilla al que se ajusta ≥80% de la serie (0 si ninguno)."""
    meaningful = [v for v in values if v is not None]
    if len(meaningful) < 4:
        return 0.0
    for step in sorted(steps, reverse=True):
        fits = sum(
            1 for v in meaningful
            if abs(v / step - round(v / step)) < 0.02
        )
        if fits / len(meaningful) >= 0.8:
            return step
    return 0.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _hourly_source(series: dict[str, Any]) -> dict[int, dict[str, float]]:
    keys = {
        "temperature_c": "temps", "humidity_pct": "humidities",
        "wind_kmh": "winds", "wind_dir_deg": "wind_dirs",
    }
    hourly: dict[int, dict[str, float]] = {}
    for index, raw_epoch in enumerate(series.get("epochs", [])):
        try:
            epoch = int(raw_epoch)
        except (TypeError, ValueError):
            continue
        bucket = epoch // 3600
        row = hourly.get(bucket)
        # Los feeds oficiales pueden publicar cada 10 minutos, mientras que
        # IEM/BUFR conserva el parte de la hora en punto. Elegir la última
        # lectura del bloque (:50) comparaba instantes distintos. Conservamos
        # la lectura más próxima a :00 (normalmente exactamente :00).
        distance_to_hour = epoch - bucket * 3600
        current_distance = (
            int(row["epoch"]) - bucket * 3600 if row is not None else 3601
        )
        if row is None or distance_to_hour < current_distance:
            row = {"epoch": float(epoch)}
            hourly[bucket] = row
            for canonical, source_key in keys.items():
                values = series.get(source_key, [])
                value = _number(values[index]) if isinstance(values, list) and index < len(values) else None
                if value is not None:
                    row[canonical] = value
    return hourly


def _hourly_iem(rows: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    hourly: dict[int, dict[str, float]] = {}
    for item in rows:
        try:
            epoch = _epoch(item.get("utc_valid", ""))
        except (TypeError, ValueError):
            continue
        bucket = epoch // 3600
        row: dict[str, float] = {"epoch": float(epoch)}
        tmpf = _number(item.get("tmpf"))
        if tmpf is not None:
            row["temperature_c"] = (tmpf - 32.0) * 5.0 / 9.0
        for source_key, canonical, factor in (
            ("relh", "humidity_pct", 1.0),
            ("sknt", "wind_kmh", 1.852),
            ("drct", "wind_dir_deg", 1.0),
        ):
            value = _number(item.get(source_key))
            if value is not None:
                row[canonical] = value * factor
        current = hourly.get(bucket)
        if current is None or epoch >= current["epoch"]:
            hourly[bucket] = row
    return hourly


def _effective_tolerances(iem: dict[int, dict[str, float]]) -> dict[str, float]:
    tolerances = dict(TOLERANCES)
    for variable, steps in QUANTIZATION_STEPS.items():
        values = [row[variable] for row in iem.values() if variable in row]
        step = _detect_quantization(values, steps)
        if step:
            tolerances[variable] = TOLERANCES[variable] + step / 2.0
    return tolerances


def _compare_at_offset(
    source: dict[int, dict[str, float]],
    iem: dict[int, dict[str, float]],
    offset_hours: int,
    tolerances: dict[str, float],
) -> tuple[list[dict[str, Any]], int]:
    comparisons = []
    matched_hours = 0
    for hour in sorted(set(source) & {h - offset_hours for h in iem}):
        iem_row = iem[hour + offset_hours]
        matched_hours += 1
        for variable, tolerance in tolerances.items():
            if variable not in source[hour] or variable not in iem_row:
                continue
            left, right = source[hour][variable], iem_row[variable]
            difference = abs(left - right)
            if variable == "wind_dir_deg":
                difference = min(difference, 360.0 - difference)
            comparisons.append({
                "hour_epoch": hour * 3600, "variable": variable,
                "source": round(left, 3), "iem": round(right, 3),
                "difference": round(difference, 3), "agrees": difference <= tolerance,
            })
    return comparisons, matched_hours


def compare_hourly(source: dict[int, dict[str, float]], iem: dict[int, dict[str, float]]) -> dict[str, Any]:
    # Se prueban desfases de ±1 h además del alineado directo: algunas
    # series (p.ej. los partes BUFR de estaciones AEMET) llegan con el
    # sello horario corrido una hora (huso/DST del emisor), lo que hacía
    # que la MISMA estación puntuara como "conflict" — coincidencia exacta
    # de noche (temperatura plana) y desvíos de ~1 °C con la rampa diurna.
    # Una estación realmente distinta no se rescata con un desfase: sus
    # diferencias no son un corrimiento temporal limpio.
    tolerances = _effective_tolerances(iem)
    best_offset = 0
    comparisons: list[dict[str, Any]] = []
    matched_hours = 0
    best_ratio = -1.0
    for offset in (0, -1, 1):
        offset_comparisons, offset_hours = _compare_at_offset(source, iem, offset, tolerances)
        if not offset_comparisons:
            continue
        offset_ratio = sum(bool(row["agrees"]) for row in offset_comparisons) / len(offset_comparisons)
        # Preferir el alineado directo salvo mejora clara (evita elegir un
        # desfase por ruido cuando ambos empatan).
        margin = 0.0 if offset == 0 else 0.15
        if offset_ratio > best_ratio + margin:
            best_ratio = offset_ratio
            best_offset = offset
            comparisons = offset_comparisons
            matched_hours = offset_hours
    return _summarize_comparisons(
        comparisons, matched_hours=matched_hours, hour_offset=best_offset,
        tolerances=tolerances,
    )


def _summarize_comparisons(
    comparisons: list[dict[str, Any]], *, matched_hours: int | None = None,
    hour_offset: int | None = None, tolerances: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Classify comparisons, deduplicating hours accumulated across runs."""
    unique = {
        (int(row["hour_epoch"]), str(row["variable"])): row
        for row in comparisons
        if "hour_epoch" in row and "variable" in row
    }
    comparisons = list(unique.values())
    observed_hours = len({int(row["hour_epoch"]) for row in comparisons})
    matched_hours = observed_hours if matched_hours is None else min(matched_hours, observed_hours)
    agreeing = sum(bool(row["agrees"]) for row in comparisons)
    total = len(comparisons)
    ratio = agreeing / total if total else 0.0
    temperature = [row for row in comparisons if row["variable"] == "temperature_c"]
    temperature_ratio = sum(row["agrees"] for row in temperature) / len(temperature) if temperature else 0.0
    variables = {row["variable"] for row in comparisons}
    strong_temperature = (
        len(temperature) >= MIN_TEMPERATURE_VALUES
        and temperature_ratio >= MIN_TEMPERATURE_RATIO
    )
    strong_multivariable = (
        total >= MIN_TOTAL_VALUES and len(variables) >= 2
        and ratio >= MIN_MULTIVARIABLE_RATIO
    )
    if matched_hours >= MIN_MATCHED_HOURS and (strong_temperature or strong_multivariable):
        status = "confirmed"
    elif matched_hours >= MIN_MATCHED_HOURS and total >= MIN_TOTAL_VALUES and ratio <= 0.25 and (
        not temperature or temperature_ratio <= 0.25
    ):
        status = "conflict"
    else:
        status = "inconclusive"
    return {
        "status": status, "matched_hours": matched_hours, "compared_values": total,
        "agreeing_values": agreeing, "agreement_ratio": round(ratio, 3),
        "temperature_agreement_ratio": round(temperature_ratio, 3),
        "hour_offset": hour_offset,
        "tolerances": {key: round(value, 3) for key, value in (tolerances or TOLERANCES).items()},
        "comparisons": comparisons,
    }


async def _source_series(provider: str, station_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    body = TodaySeriesRequest(provider=provider, station_id=station_id)
    _secret, _current, fetch_series = _resolve_provider_fetchers(body, client, get_settings())
    value = await fetch_series()
    return value if isinstance(value, dict) else {}


async def _retry_async(factory, *, retries: int, delay_seconds: float):
    """Retry transient provider/rate-limit failures with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            return await factory()
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                raise
            await asyncio.sleep(delay_seconds * (2 ** attempt))
    raise last_error or RuntimeError("retry failed")


async def _iem_rows(
    network: str, station_id: str, dates: set[str], client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    rows = []
    for date in sorted(dates):
        response = await client.get(
            IEM_HISTORY_URL,
            params={"network": network, "station": station_id, "date": date},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows.extend(item for item in payload.get("data", []) if isinstance(item, dict))
    return rows


async def _validate_candidate(
    candidate: sqlite3.Row, client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
    retries: int = 2, retry_delay: float = 2.0,
) -> tuple[int, dict[str, Any]]:
    async with semaphore:
        try:
            series = await _retry_async(
                lambda: _source_series(candidate["provider"], candidate["source_station_id"], client),
                retries=retries, delay_seconds=retry_delay,
            )
            dates = _dates_for_series(series, candidate["iem_timezone"])
            iem_rows = await _retry_async(
                lambda: _iem_rows(
                    candidate["network_code"], candidate["iem_station_id"], dates, client,
                ),
                retries=retries, delay_seconds=retry_delay,
            ) if dates else []
            details = compare_hourly(_hourly_source(series), _hourly_iem(iem_rows))
        except Exception as exc:
            details = {
                "status": "error", "matched_hours": 0, "compared_values": 0,
                "agreeing_values": 0, "error": f"{type(exc).__name__}: {exc}",
            }
        return int(candidate["alias_pk"]), details


def _dates_for_series(series: dict[str, Any], tz_name: str) -> set[str]:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dates = set()
    for epoch in series.get("epochs", []):
        try:
            dates.add(datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(tz).date().isoformat())
        except (TypeError, ValueError, OSError):
            pass
    return dates


def _accumulate_previous_checks(
    connection: sqlite3.Connection, alias_pk: int, current: dict[str, Any],
) -> dict[str, Any]:
    """Combine distinct hourly evidence from earlier inconclusive runs."""
    if current.get("status") == "error":
        return current
    comparisons = list(current.get("comparisons") or [])
    rows = connection.execute(
        """
        SELECT details_json FROM station_alias_observation_checks
        WHERE alias_pk = ? AND status IN ('confirmed', 'conflict', 'inconclusive')
        ORDER BY check_pk
        """,
        (alias_pk,),
    ).fetchall()
    for row in rows:
        try:
            previous = json.loads(row["details_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        comparisons.extend(previous.get("comparisons") or [])
    result = _summarize_comparisons(comparisons)
    result["sampled_dates"] = sorted({
        datetime.fromtimestamp(int(row["hour_epoch"]), tz=timezone.utc).date().isoformat()
        for row in result["comparisons"]
    })
    result["accumulated_runs"] = len(rows) + 1
    return result


def _candidates(
    connection: sqlite3.Connection, limit: int, provider: str,
    retry_inconclusive: bool = False, include_secure: bool = False,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    provider_clause = ""
    if provider:
        provider_clause = " AND source.provider = ?"
        params.append(provider.upper())
    params.append(limit)
    # 'error' también se reintenta: un 429/timeout transitorio del proveedor
    # no debe excluir el par para siempre.
    pending_clause = (
        "(latest.check_pk IS NULL OR latest.status IN ('inconclusive', 'error'))"
        if retry_inconclusive else "latest.check_pk IS NULL"
    )
    methods = "'inventory_probable', 'inventory_ambiguous'"
    if include_secure:
        methods = "'inventory_secure', 'inventory_probable', 'inventory_ambiguous'"
    excluded_providers = ", ".join(
        f"'{provider}'" for provider in EXCLUDED_CANONICAL_PROVIDERS
    )
    return connection.execute(
        f"""
        SELECT a.alias_pk, source.provider, source.station_id AS source_station_id,
               iem.network_code, iem.station_id AS iem_station_id,
               COALESCE(iem.timezone, 'UTC') AS iem_timezone
        FROM station_aliases a
        JOIN stations source ON source.station_pk = a.canonical_station_pk
        JOIN stations iem ON iem.station_pk = a.station_pk
        LEFT JOIN station_alias_observation_checks latest
          ON latest.check_pk = (
              SELECT MAX(c.check_pk) FROM station_alias_observation_checks c
              WHERE c.alias_pk = a.alias_pk
          )
        WHERE a.reviewed = 0 AND a.method IN ({methods})
          AND source.provider NOT IN ({excluded_providers})
          AND {pending_clause} {provider_clause}
        ORDER BY CASE a.method
                   WHEN 'inventory_secure' THEN 0
                   WHEN 'inventory_probable' THEN 1
                   ELSE 2
                 END,
                 a.confidence DESC, a.alias_pk
        LIMIT ?
        """,
        params,
    ).fetchall()


async def validate_batch(
    database: Path, *, limit: int = 10, provider: str = "",
    retry_inconclusive: bool = False, concurrency: int = 1,
    include_secure: bool = False,
    retries: int = 2, retry_delay: float = 2.0,
) -> dict[str, int]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(CHECK_SCHEMA)
    candidates = _candidates(connection, limit, provider, retry_inconclusive, include_secure)
    counts = {"confirmed": 0, "conflict": 0, "inconclusive": 0, "error": 0}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(headers={"User-Agent": "MeteoLabX alias validation"}) as client:
        tasks = [
            _validate_candidate(candidate, client, semaphore, retries, retry_delay)
            for candidate in candidates
        ]
        for task in asyncio.as_completed(tasks):
            alias_pk, details = await task
            details = _accumulate_previous_checks(connection, alias_pk, details)
            status = details["status"]
            counts[status] += 1
            connection.execute(
                """
                INSERT INTO station_alias_observation_checks(
                    alias_pk, checked_at, status, matched_hours,
                    compared_values, agreeing_values, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alias_pk, datetime.now(timezone.utc).isoformat(), status,
                    details["matched_hours"], details["compared_values"],
                    details["agreeing_values"], json.dumps(details, separators=(",", ":")),
                ),
            )
            if status in {"confirmed", "conflict"}:
                alias = connection.execute(
                    "SELECT confidence, evidence_json FROM station_aliases WHERE alias_pk = ?",
                    (alias_pk,),
                ).fetchone()
                evidence = json.loads(alias["evidence_json"])
                evidence["observation_comparison"] = details
                confidence = (
                    max(float(alias["confidence"]), 0.995)
                    if status == "confirmed"
                    else min(float(alias["confidence"]), 0.20)
                )
                connection.execute(
                    """
                    UPDATE station_aliases
                    SET method = ?, confidence = ?, evidence_json = ?
                    WHERE alias_pk = ? AND reviewed = 0
                    """,
                    (
                        f"observation_{status}", confidence,
                        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                        alias_pk,
                    ),
                )
            connection.commit()
    connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--provider", default="")
    parser.add_argument("--retry-inconclusive", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument(
        "--include-secure",
        action="store_true",
        help="También procesa candidatos inventory_secure sin evidencia observacional.",
    )
    args = parser.parse_args()
    counts = asyncio.run(validate_batch(
        args.database, limit=max(1, args.limit), provider=args.provider,
        retry_inconclusive=args.retry_inconclusive, concurrency=max(1, args.concurrency),
        include_secure=args.include_secure,
        retries=max(0, args.retries), retry_delay=max(0.0, args.retry_delay),
    ))
    print("Observation validation:", " ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
