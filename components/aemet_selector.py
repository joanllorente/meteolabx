"""Compatibilidad legacy: reexporta el selector genérico de estaciones."""

from .station_selector import render_station_selector, show_provider_connection_status


# Mantener nombres antiguos para no romper imports existentes.
def render_aemet_selector():
    render_station_selector()


def show_aemet_connection_status():
    show_provider_connection_status()
