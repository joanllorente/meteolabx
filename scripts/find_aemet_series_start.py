#!/usr/bin/env python3
"""Probe AEMET OpenData to find the earliest daily date for stations.

The script is intentionally conservative: it caches step-1/year and daily
range results, sleeps between requests, and treats timeouts as inconclusive
instead of "no data".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = "https://opendata.aemet.es/opendata/api"
DEFAULT_CACHE = Path("/private/tmp/aemet_series_start_probe_cache.json")


def _load_env_key() -> str:
    key = os.getenv("METEOLABX_AEMET_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("METEOLABX_AEMET_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")


def _fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 90) -> Any:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


class Probe:
    def __init__(self, api_key: str, cache_path: Path, sleep_s: float):
        self.api_key = api_key
        self.cache_path = cache_path
        self.cache = _load_cache(cache_path)
        self.sleep_s = float(sleep_s)
        self.calls = 0

    def _request_step1(self, key: str, path: str) -> dict[str, Any]:
        cached = self.cache.get(key)
        if isinstance(cached, dict) and cached.get("stage") != "transient":
            return cached
        if self.calls:
            time.sleep(self.sleep_s)
        self.calls += 1
        url = f"{BASE_URL}{path}"
        try:
            payload = _fetch_json(url, headers={"api_key": self.api_key})
        except HTTPError as exc:
            result = {"ok": False, "stage": "transient", "error": "HTTPError", "code": exc.code}
        except (TimeoutError, URLError) as exc:
            result = {"ok": False, "stage": "transient", "error": type(exc).__name__, "detail": str(exc)[:180]}
        except Exception as exc:
            result = {"ok": False, "stage": "transient", "error": type(exc).__name__, "detail": str(exc)[:180]}
        else:
            if not isinstance(payload, dict):
                result = {"ok": False, "stage": "bad_response", "detail": "step1 not object"}
            else:
                estado = payload.get("estado")
                if estado == 200 and payload.get("datos"):
                    result = {"ok": True, "estado": 200, "datos": payload.get("datos")}
                elif estado == 404:
                    result = {"ok": False, "stage": "no_data", "estado": 404, "descripcion": payload.get("descripcion")}
                elif estado == 429:
                    result = {"ok": False, "stage": "transient", "estado": 429, "descripcion": payload.get("descripcion")}
                else:
                    result = {"ok": False, "stage": "other", "estado": estado, "descripcion": payload.get("descripcion")}
        self.cache[key] = result
        _save_cache(self.cache_path, self.cache)
        return result

    def year_has_data(self, station: str, year: int) -> bool | None:
        path = (
            "/valores/climatologicos/mensualesanuales/datos/"
            f"anioini/{year:04d}/aniofin/{year:04d}/estacion/{station}"
        )
        result = self._request_step1(f"year:{station}:{year:04d}", path)
        if result.get("ok"):
            return True
        if result.get("stage") == "no_data":
            return False
        return None

    def daily_range_dates(self, station: str, start: date, end: date) -> list[str] | None:
        path = (
            "/valores/climatologicos/diarios/datos/"
            f"fechaini/{start.isoformat()}T00%3A00%3A00UTC/"
            f"fechafin/{end.isoformat()}T23%3A59%3A59UTC/"
            f"estacion/{station}"
        )
        key = f"daily:{station}:{start.isoformat()}:{end.isoformat()}"
        cached = self.cache.get(key)
        if isinstance(cached, dict) and cached.get("stage") != "transient":
            result = cached
        else:
            step1 = self._request_step1(f"step1:{key}", path)
            if not step1.get("ok"):
                result = step1
            else:
                if self.calls:
                    time.sleep(self.sleep_s)
                self.calls += 1
                try:
                    payload = _fetch_json(str(step1["datos"]), timeout=120)
                except HTTPError as exc:
                    result = {"ok": False, "stage": "transient", "error": "HTTPError", "code": exc.code}
                except (TimeoutError, URLError) as exc:
                    result = {"ok": False, "stage": "transient", "error": type(exc).__name__, "detail": str(exc)[:180]}
                except Exception as exc:
                    result = {"ok": False, "stage": "transient", "error": type(exc).__name__, "detail": str(exc)[:180]}
                else:
                    if isinstance(payload, list):
                        dates = sorted(
                            str(row.get("fecha", "")).strip()
                            for row in payload
                            if isinstance(row, dict) and str(row.get("fecha", "")).strip()
                        )
                        result = {"ok": True, "dates": dates}
                    else:
                        result = {"ok": False, "stage": "bad_response", "detail": "step2 not list"}
            self.cache[key] = result
            _save_cache(self.cache_path, self.cache)
        if result.get("ok"):
            return list(result.get("dates") or [])
        if result.get("stage") == "no_data":
            return []
        return None


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1).replace(day=1) - date.resolution


def find_first_year(probe: Probe, station: str, known_year: int, min_year: int) -> int | None:
    known = known_year
    current = known_year - 1
    last_true = known_year
    first_false = None
    step = 1
    while current >= min_year:
        status = probe.year_has_data(station, current)
        print(f"{station} year {current}: {status}", flush=True)
        if status is True:
            last_true = current
            step *= 2
            current = known_year - step
            continue
        if status is False:
            first_false = current
            break
        return None
    if first_false is None:
        return last_true if current < min_year else None

    low = first_false + 1
    high = last_true
    while low < high:
        mid = (low + high) // 2
        status = probe.year_has_data(station, mid)
        print(f"{station} year {mid}: {status}", flush=True)
        if status is True:
            high = mid
        elif status is False:
            low = mid + 1
        else:
            return None
    return low


def find_first_day(probe: Probe, station: str, year: int) -> str | None:
    for month in range(1, 13):
        start = date(year, month, 1)
        end = _last_day(year, month)
        dates = probe.daily_range_dates(station, start, end)
        print(f"{station} {year}-{month:02d}: {None if dates is None else (dates[:1], dates[-1:], len(dates))}", flush=True)
        if dates is None:
            return None
        if dates:
            return dates[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stations", nargs="+", help="station specs like 9981A:1947")
    parser.add_argument("--min-year", type=int, default=1900)
    parser.add_argument("--sleep", type=float, default=35.0)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    key = _load_env_key()
    if not key:
        print("Missing METEOLABX_AEMET_API_KEY", file=sys.stderr)
        return 2
    probe = Probe(key, args.cache, args.sleep)
    for spec in args.stations:
        station, _, year_txt = spec.partition(":")
        known_year = int(year_txt) if year_txt else 1950
        station = station.strip().upper()
        print(f"== {station} known_year={known_year} ==", flush=True)
        first_year = find_first_year(probe, station, known_year, args.min_year)
        print(f"{station} first_year={first_year}", flush=True)
        if first_year is not None:
            first_day = find_first_day(probe, station, first_year)
            print(f"{station} first_day={first_day}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
