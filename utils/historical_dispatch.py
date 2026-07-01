"""
Dispatcher de datasets históricos / climogramas del frontend.

El frontend es backend-only: ``fetch_historical_dataset`` delega
siempre en ``POST /v1/climo/dataset`` (vía
``fetch_climo_dataset_via_api_strict``) y propaga ``BackendApiError`` si el
backend falla; el caller (``tabs/historical.py``) ya lo traduce a un
    mensaje de error en la UI.
"""

from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_historical_dataset(
    *,
    provider_id: str,
    station_id: str,
    api_key,
    summary_mode: str,
    periods,
    selected_years,
    selected_months,
    frost_selected_period: str,
    frost_selected_periods,
    api_secret: str = "",
):
    """
    Dataset histórico del proveedor vía backend ``/v1/climo/dataset``.

    Lanza ``BackendApiError`` si el backend falla.
    """
    from utils.api_client import fetch_climo_dataset_via_api_strict

    provider_id = str(provider_id or "").strip().upper()
    df, extremes = fetch_climo_dataset_via_api_strict(
        provider_id,
        station_id,
        api_key=str(api_key or ""),
        api_secret=str(api_secret or ""),
        summary_mode=summary_mode,
        periods=periods,
        selected_years=selected_years,
        selected_months=selected_months,
        frost_period=frost_selected_period,
        frost_periods=frost_selected_periods,
    )
    if df is not None:
        return df, extremes
    # El renderer trabaja siempre con DataFrame, también cuando no hay datos.
    import pandas as _pd

    return _pd.DataFrame(), extremes
