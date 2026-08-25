#!/usr/bin/env python3
"""Arranca Streamlit añadiendo la entrada pública limpia ``/forecast``.

Streamlit sirve correctamente archivos bajo ``/forecast/...``, pero sus reglas
genéricas alternan entre añadir y quitar la barra final cuando ese path es un
directorio. Registramos antes de ellas un handler muy pequeño que entrega el
``index.html`` del visor Svelte sin redirigir ni cambiar la URL del navegador.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

from tornado.routing import PathMatches, Rule
from tornado.web import RequestHandler

from streamlit.web import cli
from streamlit.web.server.server import Server


class ForecastIndexHandler(RequestHandler):
    """Entrega el entrypoint Svelte conservando exactamente ``/forecast``."""

    def initialize(self, index_path: str) -> None:
        self._index_path = Path(index_path)

    def set_default_headers(self) -> None:
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache")

    async def get(self) -> None:
        if not self._index_path.is_file():
            self.send_error(404)
            return
        self.write(self._index_path.read_bytes())

    async def head(self) -> None:
        if not self._index_path.is_file():
            self.send_error(404)
            return
        self.set_header("Content-Length", str(self._index_path.stat().st_size))


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
        # ``wildcard_router`` contiene las rutas declaradas por Streamlit. La
        # entrada debe ir antes de su StaticFileHandler y de Add/RemoveSlash.
        app.wildcard_router.rules.insert(0, route)
        return app

    Server._create_app = create_app_with_forecast


def main() -> int:
    install_forecast_route()
    sys.argv = ["streamlit", "run", *sys.argv[1:]]
    cli.main(prog_name="streamlit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
