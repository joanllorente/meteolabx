"""Frontend-neutral errors raised while calling the MeteoLabX API."""

from __future__ import annotations


class BackendApiError(Exception):
    """Stable error category returned by FastAPI or its upstream providers."""

    def __init__(self, kind: str, status_code: int | None = None, detail: str = ""):
        self.kind = str(kind or "http")
        self.status_code = status_code
        self.detail = str(detail or "")
        super().__init__(self.kind)
