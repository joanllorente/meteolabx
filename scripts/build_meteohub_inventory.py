#!/usr/bin/env python3
"""
Build a reconstructed MeteoHub Italia observations station inventory.

MeteoHub does not expose a simple station catalog with sensor capabilities.
For public observations, the practical path is to query /api/observations with
onlyStations=true for each observation product and merge the stations found.

Usage:
  python3 scripts/build_meteohub_inventory.py --check-config

  python3 scripts/build_meteohub_inventory.py \
      --networks dpcn-lazio \
      --output data/data_estaciones_meteohub_it.json

  python3 scripts/build_meteohub_inventory.py \
      --output data/data_estaciones_meteohub_it.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://meteohub.agenziaitaliameteo.it"
DEFAULT_OUTPUT = str(ROOT_DIR / "data" / "data_estaciones_meteohub_it.json")
DEFAULT_CACHE = str(ROOT_DIR / "data" / "meteohub_inventory_cache.json")
DEFAULT_MAX_REQUESTS = 200
DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class ProductSpec:
    capability: str
    code: str
    label: str


PRODUCT_SPECS: Tuple[ProductSpec, ...] = (
    ProductSpec("temperature", "B12101", "Temperature / dry-bulb temperature"),
    ProductSpec("relative_humidity", "B13003", "Relative humidity"),
    ProductSpec("pressure", "B10004", "Pressure"),
    ProductSpec("wind_speed", "B11002", "Wind speed"),
    ProductSpec("wind_direction", "B11001", "Wind direction"),
    ProductSpec("precipitation", "B13011", "Total precipitation"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cache(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        payload = _load_json_file(cache_path)
    except Exception:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("responses"), dict):
        return dict(payload["responses"])
    return {}


def _save_cache(path: Optional[str], responses: Dict[str, Any]) -> None:
    if not path:
        return
    _save_json_file(
        Path(path),
        {
            "version": 1,
            "generated_at": _now_iso(),
            "responses": responses,
        },
    )


def _request_json(
    url: str,
    *,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> Any:
    last_error: Optional[BaseException] = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
    }

    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} for {url}: {details[:500]}") from exc
        except URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"Network error for {url}: {exc}") from exc
        except TimeoutError as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"Timeout for {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON for {url}: {exc}") from exc

        time.sleep(max(0.0, retry_sleep) * (attempt + 1))

    raise RuntimeError(f"Request failed for {url}: {last_error}")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _status_url(base_url: str) -> str:
    return _join_url(base_url, "/api/status")


def _datasets_url(base_url: str) -> str:
    return _join_url(base_url, "/api/datasets") + "?licenceSpecs=true"


def _observations_url(
    base_url: str,
    *,
    network_id: str,
    product_code: str,
    start_date: str,
    end_date: str,
    license_group: str,
) -> str:
    q = (
        f"reftime: >={start_date} 00:00,<={end_date} 23:59;"
        f"license:{license_group};"
        f"product:{product_code}"
    )
    params = {
        "q": q,
        "networks": network_id,
        "onlyStations": "true",
    }
    return _join_url(base_url, "/api/observations") + "?" + urlencode(params)


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(str(value).strip())


def _date_range(end: date, days_back: int) -> Tuple[str, str]:
    days = max(0, int(days_back))
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "si", "sí"}
    return bool(value)


def _load_public_observation_networks(datasets: Any, license_group: str) -> List[Dict[str, Any]]:
    if not isinstance(datasets, list):
        return []
    networks: List[Dict[str, Any]] = []
    for item in datasets:
        if not isinstance(item, dict):
            continue
        if str(item.get("category") or "").strip().upper() != "OBS":
            continue
        if not _as_bool(item.get("is_public")):
            continue
        if license_group and str(item.get("group_license") or "").strip() != license_group:
            continue
        network_id = str(item.get("id") or "").strip()
        if not network_id:
            continue
        networks.append(item)
    networks.sort(key=lambda item: str(item.get("id") or ""))
    return networks


def _split_csv(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _select_networks(
    all_networks: List[Dict[str, Any]],
    requested: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    wanted = {item.lower() for item in _split_csv(requested) if item.lower() != "all"}
    selected = [
        network
        for network in all_networks
        if not wanted or str(network.get("id") or "").strip().lower() in wanted
    ]
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return selected


def _select_products(requested: str) -> List[ProductSpec]:
    wanted = {item.lower() for item in _split_csv(requested) if item.lower() != "all"}
    selected = [
        spec
        for spec in PRODUCT_SPECS
        if not wanted or spec.capability.lower() in wanted or spec.code.lower() in wanted
    ]
    return selected


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _details_name(details: Any) -> str:
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            if str(detail.get("var") or "").strip() == "B01019":
                value = str(detail.get("val") or "").strip()
                if value:
                    return value
        for detail in details:
            if isinstance(detail, dict):
                value = str(detail.get("val") or "").strip()
                if value:
                    return value
    return ""


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "station"


def _station_key(network: str, lat: float, lon: float, name: str) -> str:
    return f"{network}|{lat:.5f}|{lon:.5f}|{_slug(name)}"


def _iter_station_records(payload: Any) -> Iterator[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        stat = item.get("stat")
        if not isinstance(stat, dict):
            continue
        lat = _to_float(stat.get("lat"))
        lon = _to_float(stat.get("lon"))
        network = str(stat.get("net") or "").strip()
        if lat is None or lon is None or not network:
            continue
        name = _details_name(stat.get("details")) or f"{network} {lat:.5f},{lon:.5f}"
        yield {
            "network": network,
            "lat": lat,
            "lon": lon,
            "name": name,
            "details": stat.get("details") if isinstance(stat.get("details"), list) else [],
            "raw_stat": stat,
        }


def _empty_capabilities() -> Dict[str, bool]:
    return {spec.capability: False for spec in PRODUCT_SPECS}


def _new_station(record: Dict[str, Any], dataset: Dict[str, Any], observed_start: str, observed_end: str) -> Dict[str, Any]:
    network = str(record.get("network") or "").strip()
    name = str(record.get("name") or "").strip()
    lat = float(record.get("lat"))
    lon = float(record.get("lon"))
    station_id = _station_key(network, lat, lon, name)
    network_name = str(dataset.get("name") or network).strip()
    return {
        "id": station_id,
        "source_id": station_id,
        "name": name,
        "network": network,
        "network_name": network_name,
        "lat": lat,
        "lon": lon,
        "elev": None,
        "altitude": None,
        "country": "Italy",
        "country_code": "IT",
        "provider": "METEOHUB_IT",
        "source": "MeteoHub Italia /api/observations",
        "inventory_method": "observations_onlystations_by_product",
        "observed_start": observed_start,
        "observed_end": observed_end,
        "active_now": True,
        "capabilities": _empty_capabilities(),
        "products": [],
        "product_labels": {},
        "license": dataset.get("license"),
        "license_description": dataset.get("license_description"),
        "license_url": dataset.get("license_url"),
        "group_license": dataset.get("group_license"),
        "attribution": dataset.get("attribution"),
        "attribution_description": dataset.get("attribution_description"),
        "attribution_url": dataset.get("attribution_url"),
        "raw_details": record.get("details") or [],
    }


def _upsert_station(
    stations_by_key: Dict[str, Dict[str, Any]],
    record: Dict[str, Any],
    product: ProductSpec,
    dataset_by_network: Dict[str, Dict[str, Any]],
    observed_start: str,
    observed_end: str,
) -> None:
    network = str(record.get("network") or "").strip()
    lat = float(record.get("lat"))
    lon = float(record.get("lon"))
    name = str(record.get("name") or "").strip()
    key = _station_key(network, lat, lon, name)
    station = stations_by_key.get(key)
    if station is None:
        station = _new_station(
            record,
            dataset_by_network.get(network, {}),
            observed_start,
            observed_end,
        )
        stations_by_key[key] = station

    capabilities = station.setdefault("capabilities", _empty_capabilities())
    capabilities[product.capability] = True
    products = station.setdefault("products", [])
    if product.code not in products:
        products.append(product.code)
    labels = station.setdefault("product_labels", {})
    labels[product.code] = product.label


def _finalize_stations(stations_by_key: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    stations = list(stations_by_key.values())
    for station in stations:
        capabilities = station.setdefault("capabilities", _empty_capabilities())
        for spec in PRODUCT_SPECS:
            capabilities.setdefault(spec.capability, False)
            station[f"has_{spec.capability}"] = bool(capabilities.get(spec.capability))
        station["has_wind"] = bool(
            capabilities.get("wind_speed") or capabilities.get("wind_direction")
        )
        station["products"] = sorted(str(code) for code in station.get("products", []))

    stations.sort(
        key=lambda station: (
            str(station.get("network") or ""),
            str(station.get("name") or ""),
            float(station.get("lat") or 0.0),
            float(station.get("lon") or 0.0),
        )
    )
    return stations


def _capability_counts(stations: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {spec.capability: 0 for spec in PRODUCT_SPECS}
    counts["wind"] = 0
    for station in stations:
        capabilities = station.get("capabilities") if isinstance(station, dict) else {}
        if not isinstance(capabilities, dict):
            continue
        for spec in PRODUCT_SPECS:
            if capabilities.get(spec.capability):
                counts[spec.capability] += 1
        if capabilities.get("wind_speed") or capabilities.get("wind_direction"):
            counts["wind"] += 1
    return counts


def build_inventory(args: argparse.Namespace) -> List[Dict[str, Any]]:
    base_url = str(args.base_url).rstrip("/")
    end = _parse_date(args.date)
    observed_start, observed_end = _date_range(end, args.days_back)
    cache = _load_cache(args.cache)
    datasets = _request_json(
        _datasets_url(base_url),
        timeout=args.timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    all_networks = _load_public_observation_networks(datasets, args.license_group)
    selected_networks = _select_networks(all_networks, args.networks, args.limit_networks)
    selected_products = _select_products(args.products)
    dataset_by_network = {
        str(network.get("id") or "").strip(): network
        for network in all_networks
        if str(network.get("id") or "").strip()
    }

    if not selected_networks:
        raise RuntimeError("No MeteoHub observation networks selected.")
    if not selected_products:
        raise RuntimeError("No MeteoHub products selected.")

    print("MeteoHub Italia observation inventory scan")
    print(f"Networks: {len(selected_networks)} | products: {len(selected_products)}")
    print(f"Date window: {observed_start} 00:00 -> {observed_end} 23:59")
    print(f"Request cap: {args.max_requests} non-cached calls")

    stations_by_key: Dict[str, Dict[str, Any]] = {}
    api_calls = 0
    cache_hits = 0
    failures = 0
    total_jobs = len(selected_networks) * len(selected_products)
    completed_jobs = 0
    stop = False

    for network in selected_networks:
        network_id = str(network.get("id") or "").strip()
        for product in selected_products:
            cache_key = (
                f"observations:{observed_start}:{observed_end}:"
                f"{args.license_group}:{network_id}:{product.code}"
            )
            payload = cache.get(cache_key)
            if payload is not None:
                cache_hits += 1
            else:
                if api_calls >= int(args.max_requests):
                    print(f"Max requests reached: {args.max_requests}")
                    stop = True
                    break
                url = _observations_url(
                    base_url,
                    network_id=network_id,
                    product_code=product.code,
                    start_date=observed_start,
                    end_date=observed_end,
                    license_group=args.license_group,
                )
                try:
                    payload = _request_json(
                        url,
                        timeout=args.timeout,
                        retries=args.retries,
                        retry_sleep=args.retry_sleep,
                    )
                except RuntimeError as exc:
                    failures += 1
                    if args.strict:
                        raise
                    print(f"Warning: skipped {network_id}/{product.code}: {exc}")
                    payload = None
                else:
                    api_calls += 1
                    cache[cache_key] = payload
                    _save_cache(args.cache, cache)
                    if args.sleep > 0:
                        time.sleep(float(args.sleep))

            if payload is not None:
                for record in _iter_station_records(payload):
                    _upsert_station(
                        stations_by_key,
                        record,
                        product,
                        dataset_by_network,
                        observed_start,
                        observed_end,
                    )

            completed_jobs += 1
            if completed_jobs % max(1, int(args.progress_every)) == 0:
                print(
                    f"{completed_jobs}/{total_jobs} jobs | "
                    f"stations={len(stations_by_key)} | "
                    f"api_calls={api_calls} | cache_hits={cache_hits}"
                )

        if stop:
            break

    _save_cache(args.cache, cache)
    stations = _finalize_stations(stations_by_key)
    output_path = Path(args.output).resolve()
    _save_json_file(output_path, stations)

    counts = _capability_counts(stations)
    print(f"Inventory saved: {output_path}")
    print(f"Total reconstructed stations: {len(stations)}")
    print(f"API calls this run: {api_calls} | cache hits: {cache_hits} | failures: {failures}")
    print(
        "Capabilities: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    print("Note: this is reconstructed from recent /api/observations responses, not an official station catalog.")
    return stations


def check_config(args: argparse.Namespace) -> int:
    base_url = str(args.base_url).rstrip("/")
    status = _request_json(
        _status_url(base_url),
        timeout=args.timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    datasets = _request_json(
        _datasets_url(base_url),
        timeout=args.timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    networks = _load_public_observation_networks(datasets, args.license_group)
    selected_networks = _select_networks(networks, args.networks, args.limit_networks)
    selected_products = _select_products(args.products)
    end = _parse_date(args.date)
    observed_start, observed_end = _date_range(end, args.days_back)

    print("MeteoHub config check")
    print(f"Status: {status}")
    print(f"Base URL: {base_url}")
    print(f"Observation networks: {len(networks)}")
    print(f"Selected networks: {len(selected_networks)}")
    print("Networks: " + ", ".join(str(item.get("id")) for item in selected_networks[:50]))
    print(
        "Products: "
        + ", ".join(f"{spec.capability}:{spec.code}" for spec in selected_products)
    )
    print(f"Date window: {observed_start} 00:00 -> {observed_end} 23:59")
    print(f"Max API calls per run: {args.max_requests}")
    print(f"Cache path: {Path(args.cache).resolve() if args.cache else '(disabled)'}")
    print(f"Output path: {Path(args.output).resolve()}")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a reconstructed MeteoHub Italia observations station inventory."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument(
        "--networks",
        default="all",
        help="Comma-separated network ids, or all. Example: dpcn-lazio,open-trentino",
    )
    parser.add_argument(
        "--products",
        default="all",
        help="Comma-separated capabilities/codes, or all. Example: temperature,wind_speed,B13011",
    )
    parser.add_argument(
        "--license-group",
        default="CCBY_COMPLIANT",
        help="MeteoHub license group filter used in dataset and observation queries.",
    )
    parser.add_argument(
        "--date",
        default="",
        help="UTC date YYYY-MM-DD. Defaults to today in UTC.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=0,
        help="Include previous days in the reftime window. Public archive may require login.",
    )
    parser.add_argument("--limit-networks", type=int, default=None)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=1.5)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    if args.check_config:
        return check_config(args)
    build_inventory(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
