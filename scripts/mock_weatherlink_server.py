#!/usr/bin/env python3
"""
Tiny local WeatherLink v2 mock for testing MeteoLabX without Davis hardware.

Usage:
  python3 scripts/mock_weatherlink_server.py

Then start Streamlit with:
  WEATHERLINK_BASE_URL=http://127.0.0.1:8899/v2 python3 -m streamlit run meteolabx.py
"""

from __future__ import annotations

import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8899
DEFAULT_STATION_ID = "374964"
DEFAULT_STATION_NAME = "Davis Mock"


def _station_id_value(station_id: str) -> int | str:
    station_id = str(station_id or "").strip()
    return int(station_id) if station_id.isdigit() else station_id


def build_station(station_id: str = DEFAULT_STATION_ID, station_name: str = DEFAULT_STATION_NAME) -> dict[str, Any]:
    return {
        "station_id": _station_id_value(station_id),
        "station_id_uuid": f"mock-{station_id}",
        "station_name": station_name,
        "latitude": 41.3710,
        "longitude": 2.1280,
        "elevation": 39,
        "time_zone": "Europe/Madrid",
    }


def _fahrenheit(celsius: float) -> float:
    return (float(celsius) * 9.0 / 5.0) + 32.0


def build_current_payload(station_id: str = DEFAULT_STATION_ID, *, epoch: int | None = None) -> dict[str, Any]:
    now = int(epoch if epoch is not None else time.time())
    phase = (now % 3600) / 3600.0
    temp_c = 24.0 + (math.sin(phase * math.tau) * 1.4)
    dew_c = 16.5 + (math.cos(phase * math.tau) * 0.9)
    humidity = 58.0 + (math.cos(phase * math.tau) * 4.0)
    wind_mph = 4.2 + (math.sin(phase * math.tau) * 1.1)
    gust_mph = wind_mph + 2.4

    return {
        "station_id": _station_id_value(station_id),
        "generated_at": now,
        "sensors": [
            {
                "sensor_type": 45,
                "data_structure_type": 10,
                "data": [
                    {
                        "ts": now,
                        "temp": round(_fahrenheit(temp_c), 2),
                        "temp_hi": round(_fahrenheit(temp_c + 1.2), 2),
                        "temp_lo": round(_fahrenheit(temp_c - 2.1), 2),
                        "hum": round(humidity, 1),
                        "hum_hi": round(humidity + 5.0, 1),
                        "hum_lo": round(humidity - 7.0, 1),
                        "dew_point": round(_fahrenheit(dew_c), 2),
                        "wind_speed_last": round(wind_mph, 2),
                        "wind_speed_hi_last_10_min": round(gust_mph, 2),
                        "wind_speed_hi": round(gust_mph + 1.5, 2),
                        "wind_dir_last": 195,
                        "rainfall_daily_mm": 1.2,
                        "rain_rate_last_mm": 0.4,
                        "solar_rad": 598,
                        "uv_index": 2.3,
                        "heat_index": round(_fahrenheit(temp_c + 0.3), 2),
                        "wind_chill": round(_fahrenheit(temp_c - 0.1), 2),
                        "wet_bulb": round(_fahrenheit(19.2), 2),
                    }
                ],
            },
            {
                "sensor_type": 242,
                "data_structure_type": 12,
                "data": [
                    {
                        "ts": now,
                        "bar_absolute": 29.515,
                        "bar_sea_level": 29.61,
                    }
                ],
            },
        ],
    }


def build_historic_payload(
    station_id: str = DEFAULT_STATION_ID,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    end = int(end_ts if end_ts is not None else time.time())
    start = int(start_ts if start_ts is not None else end - 6 * 3600)
    rows: list[dict[str, Any]] = []
    step_seconds = 5 * 60
    first = start + step_seconds
    for ts in range(first, end + 1, step_seconds):
        phase = (ts % 86400) / 86400.0
        temp_c = 20.0 + 4.5 * math.sin((phase - 0.25) * math.tau)
        dew_c = 15.0 + 1.8 * math.sin((phase - 0.18) * math.tau)
        humidity = 62.0 - 10.0 * math.sin((phase - 0.25) * math.tau)
        wind_mph = 3.0 + 2.0 * abs(math.sin(phase * math.tau))
        gust_mph = wind_mph + 2.8
        rows.append(
            {
                "ts": ts,
                "arch_int": step_seconds,
                "temp_last": round(_fahrenheit(temp_c), 2),
                "temp_hi": round(_fahrenheit(temp_c + 0.4), 2),
                "temp_lo": round(_fahrenheit(temp_c - 0.5), 2),
                "hum_last": round(humidity, 1),
                "hum_hi": round(humidity + 1.5, 1),
                "hum_lo": round(humidity - 1.5, 1),
                "dew_point_last": round(_fahrenheit(dew_c), 2),
                "wind_speed_avg": round(wind_mph, 2),
                "wind_speed_hi": round(gust_mph, 2),
                "wind_dir_of_prevail": 190,
                "rainfall_mm": 0.0,
                "bar": 29.61,
                "bar_absolute": 29.515,
                "solar_rad_avg": max(0, round(700 * math.sin(max(0.0, phase) * math.pi), 1)),
                "uv_index_avg": max(0, round(4.0 * math.sin(max(0.0, phase) * math.pi), 1)),
            }
        )

    return {
        "station_id": _station_id_value(station_id),
        "generated_at": end,
        "sensors": [
            {
                "sensor_type": 45,
                "data_structure_type": 11,
                "data": rows,
            }
        ],
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _clean_path(path: str) -> list[str]:
    parts = [part for part in str(path or "").split("/") if part]
    if parts and parts[0].lower() == "v2":
        parts = parts[1:]
    return parts


class WeatherLinkMockHandler(BaseHTTPRequestHandler):
    server_version = "MeteoLabXWeatherLinkMock/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)

    def _credentials_ok(self) -> bool:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        api_key = str(query.get("api-key", [""])[0] or "").strip()
        api_secret = str(self.headers.get("X-Api-Secret", "") or "").strip()
        if not api_key or not api_secret:
            return False
        return api_key.lower() != "reject" and api_secret.lower() != "reject"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = _clean_path(parsed.path)
        if parts == ["health"]:
            _json_response(self, 200, {"ok": True})
            return

        if not self._credentials_ok():
            _json_response(self, 401, {"error": "mock unauthorized"})
            return

        station_id = str(getattr(self.server, "station_id", DEFAULT_STATION_ID))
        station_name = str(getattr(self.server, "station_name", DEFAULT_STATION_NAME))

        if parts == ["stations"]:
            _json_response(
                self,
                200,
                {
                    "generated_at": int(time.time()),
                    "stations": [build_station(station_id, station_name)],
                },
            )
            return

        if len(parts) == 2 and parts[0] == "current":
            requested_station_id = str(parts[1]).strip()
            if requested_station_id not in {station_id, f"mock-{station_id}"}:
                _json_response(self, 404, {"error": "mock station not found"})
                return
            _json_response(self, 200, build_current_payload(station_id))
            return

        if len(parts) == 2 and parts[0] == "historic":
            requested_station_id = str(parts[1]).strip()
            if requested_station_id not in {station_id, f"mock-{station_id}"}:
                _json_response(self, 404, {"error": "mock station not found"})
                return
            query = parse_qs(parsed.query)
            end_ts = int(float(query.get("end-timestamp", [time.time()])[0]))
            start_ts = int(float(query.get("start-timestamp", [end_ts - 6 * 3600])[0]))
            _json_response(self, 200, build_historic_payload(station_id, start_ts=start_ts, end_ts=end_ts))
            return

        _json_response(self, 404, {"error": "mock endpoint not found", "path": parsed.path})


def make_server(host: str, port: int, *, station_id: str, station_name: str, quiet: bool) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, int(port)), WeatherLinkMockHandler)
    server.station_id = str(station_id)  # type: ignore[attr-defined]
    server.station_name = str(station_name)  # type: ignore[attr-defined]
    server.quiet = bool(quiet)  # type: ignore[attr-defined]
    return server


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local WeatherLink v2 mock server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host. Default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port. Default: {DEFAULT_PORT}")
    parser.add_argument("--station-id", default=DEFAULT_STATION_ID, help=f"Mock station id. Default: {DEFAULT_STATION_ID}")
    parser.add_argument("--station-name", default=DEFAULT_STATION_NAME, help=f"Mock station name. Default: {DEFAULT_STATION_NAME}")
    parser.add_argument("--quiet", action="store_true", help="Suppress request logs.")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    server = make_server(
        args.host,
        args.port,
        station_id=args.station_id,
        station_name=args.station_name,
        quiet=args.quiet,
    )
    print(f"WeatherLink mock listening on http://{args.host}:{args.port}/v2")
    print("Use any non-empty API key and secret. Use 'reject' to simulate 401.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping WeatherLink mock.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
