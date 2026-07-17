"""Pestañas de MeteoLabX cargadas bajo demanda."""

from __future__ import annotations

from importlib import import_module


_LAZY_EXPORTS = {
    "build_observation_context": ("tabs.observation", "build_observation_context"),
    "render_observation_tab": ("tabs.observation", "render_observation_tab"),
    "render_trends_tab": ("tabs.trends", "render_trends_tab"),
    "render_historical_tab": ("tabs.historical", "render_historical_tab"),
    "render_map_tab": ("tabs.map", "render_map_tab"),
    "handle_rank_connect_query": ("tabs.ranking", "handle_rank_connect_query"),
    "render_ranking_tab": ("tabs.ranking", "render_ranking_tab"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "build_observation_context",
    "render_observation_tab",
    "render_trends_tab",
    "render_historical_tab",
    "render_map_tab",
    "handle_rank_connect_query",
    "render_ranking_tab",
]
