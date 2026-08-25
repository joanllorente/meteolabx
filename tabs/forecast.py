"""Pestaña del prototipo de predicción numérica."""

from __future__ import annotations

import streamlit as st


def render_forecast_tab(ctx: dict) -> None:
    """Muestra el prototipo AROME integrado en MeteoLabX."""
    section_title = ctx["section_title"]
    t = ctx["t"]

    section_title(t("forecast.section_title"))
    st.caption(t("forecast.intro"))
    from tabs.arome_forecast import render_arome_forecast

    render_arome_forecast(embedded=True)
