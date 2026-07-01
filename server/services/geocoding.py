"""Nominatim geocoding transport owned by the FastAPI backend."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from server.schemas.errors import ProviderError


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
_RATE_LOCK = asyncio.Lock()
_LAST_REQUEST_MONOTONIC = 0.0


async def geocode(
    query: str,
    *,
    accept_language: str = "es,en",
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Return the first Nominatim match while respecting its public rate limit."""
    global _LAST_REQUEST_MONOTONIC

    async with _RATE_LOCK:
        wait_s = 1.05 - (time.monotonic() - _LAST_REQUEST_MONOTONIC)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        try:
            response = await client.get(
                NOMINATIM_SEARCH_URL,
                params={
                    "q": str(query).strip(),
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                    "accept-language": str(accept_language or "es,en"),
                },
                headers={
                    "User-Agent": "MeteoLabX/1.0 (contact: meteolabx@gmail.com)",
                    "Accept": "application/json",
                },
                timeout=12.0,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", provider="NOMINATIM", detail=str(exc), status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error", provider="NOMINATIM", detail=str(exc), status_code=502,
            ) from exc
        finally:
            _LAST_REQUEST_MONOTONIC = time.monotonic()

    if response.status_code == 429:
        raise ProviderError(
            "provider_ratelimit", provider="NOMINATIM", detail="Nominatim rate limit", status_code=429,
        )
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider="NOMINATIM",
            detail=f"Nominatim HTTP {response.status_code}",
            status_code=502,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response", provider="NOMINATIM", detail="Invalid JSON", status_code=502,
        ) from exc
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return {"found": False}

    first = payload[0]
    try:
        lat = float(first.get("lat"))
        lon = float(first.get("lon"))
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "provider_bad_response",
            provider="NOMINATIM",
            detail="Result without valid coordinates",
            status_code=502,
        ) from exc
    return {
        "found": True,
        "lat": lat,
        "lon": lon,
        "display_name": str(first.get("display_name", "") or ""),
    }
