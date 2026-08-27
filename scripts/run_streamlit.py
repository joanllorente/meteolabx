#!/usr/bin/env python3
"""Arranca Streamlit añadiendo la entrada pública limpia ``/forecast``.

Streamlit sirve correctamente archivos bajo ``/forecast/...``, pero sus reglas
genéricas alternan entre añadir y quitar la barra final cuando ese path es un
directorio. Registramos antes de ellas un handler muy pequeño que entrega el
``index.html`` del visor Svelte sin redirigir ni cambiar la URL del navegador.
"""

from __future__ import annotations

from pathlib import Path
import os
import re
import sys

from tornado.httpclient import AsyncHTTPClient, HTTPError, HTTPRequest
from tornado.routing import PathMatches, Rule
from tornado.web import RequestHandler

from streamlit.web import cli
from streamlit.web.server.server import Server


class ForecastIndexHandler(RequestHandler):
    """Entrega ``/forecast`` y consolida la variante con barra final."""

    def initialize(self, index_path: str) -> None:
        self._index_path = Path(index_path)

    def set_default_headers(self) -> None:
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache")

    async def get(self) -> None:
        if self.request.path == "/forecast/":
            self.redirect("/forecast", permanent=True)
            return
        if not self._index_path.is_file():
            self.send_error(404)
            return
        self.write(self._index_path.read_bytes())

    async def head(self) -> None:
        if self.request.path == "/forecast/":
            self.redirect("/forecast", permanent=True)
            return
        if not self._index_path.is_file():
            self.send_error(404)
            return
        self.set_header("Content-Length", str(self._index_path.stat().st_size))


class ForecastApiProxyHandler(RequestHandler):
    """Expone la API AROME interna bajo el mismo origen que el visor."""

    async def _proxy(self) -> None:
        backend = os.getenv("METEOLABX_API_URL", "http://127.0.0.1:8000").rstrip("/")
        target = f"{backend}{self.request.uri}"
        headers = {
            "Accept": self.request.headers.get("Accept", "*/*"),
            "Accept-Encoding": self.request.headers.get("Accept-Encoding", "gzip"),
        }
        if content_type := self.request.headers.get("Content-Type"):
            headers["Content-Type"] = content_type
        request = HTTPRequest(
            target,
            method=self.request.method,
            headers=headers,
            body=self.request.body if self.request.method == "POST" else None,
            request_timeout=180,
            follow_redirects=False,
            # Los grids ya están comprimidos en el volumen. Tornado los
            # descomprimiría por defecto y dejaría el Content-Length original,
            # rompiendo la respuesta del proxy. Se reenvían intactos para que
            # sea el navegador quien los descomprima automáticamente.
            decompress_response=False,
        )
        try:
            response = await AsyncHTTPClient().fetch(request, raise_error=False)
        except HTTPError as exc:
            self.set_status(502)
            self.finish({"detail": f"Forecast API no disponible: {exc}"})
            return

        self.set_status(response.code)
        for header in (
            "Content-Type",
            "Content-Length",
            "Content-Encoding",
            "Cache-Control",
            "ETag",
            "Vary",
            "X-AROME-Run",
            "X-AROME-Valid-Time",
            "X-MeteoLabX-Precomputed",
        ):
            if value := response.headers.get(header):
                self.set_header(header, value)
        if self.request.method == "HEAD":
            self.finish()
        else:
            self.finish(response.body)

    async def get(self) -> None:
        await self._proxy()

    async def head(self) -> None:
        await self._proxy()


class PublicStatsProxyHandler(ForecastApiProxyHandler):
    """Permite registrar eventos anónimos validados desde el mismo origen."""

    def check_xsrf_cookie(self) -> None:
        # Solo se exponen endpoints de telemetría anónima con cuerpos validados
        # y sin datos de usuario.
        return None

    async def post(self) -> None:
        await self._proxy()


def install_forecast_route() -> None:
    original_create_app = Server._create_app

    def create_app_with_forecast(self: Server):
        app = original_create_app(self)
        from scripts.install_forecast_frontend import streamlit_static_dir

        index_path = streamlit_static_dir() / "forecast" / "index.html"
        route = Rule(
            PathMatches(re.compile(r"^/forecast/?$")),
            ForecastIndexHandler,
            {"index_path": str(index_path)},
        )
        api_route = Rule(
            PathMatches(re.compile(r"^/v1/forecast/arome(?:/.*)?$")),
            ForecastApiProxyHandler,
        )
        stats_route = Rule(
            PathMatches(re.compile(r"^/v1/stats/(?:section|seo-view)$")),
            PublicStatsProxyHandler,
        )
        # ``wildcard_router`` contiene las rutas declaradas por Streamlit. La
        # entrada debe ir antes de su StaticFileHandler y de Add/RemoveSlash.
        app.wildcard_router.rules.insert(0, route)
        app.wildcard_router.rules.insert(0, api_route)
        app.wildcard_router.rules.insert(0, stats_route)
        return app

    Server._create_app = create_app_with_forecast


def main() -> int:
    install_forecast_route()
    sys.argv = ["streamlit", "run", *sys.argv[1:]]
    cli.main(prog_name="streamlit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
