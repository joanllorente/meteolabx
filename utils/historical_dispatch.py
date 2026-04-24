"""
Dispatcher común para datasets históricos por proveedor.
"""

from __future__ import annotations


def _fetch_frost_historical_dataset(*, station_id, summary_mode, selected_months, frost_selected_period, frost_selected_periods, get_frost_service):
    frost_service = get_frost_service()
    frost_fetchers = {
        "monthly": lambda: frost_service.fetch_frost_climo_monthly_for_period(
            station_id=station_id,
            period=frost_selected_period,
            months=selected_months,
            client_id=frost_service.FROST_CLIENT_ID,
            client_secret=frost_service.FROST_CLIENT_SECRET,
        ),
        "annual": lambda: frost_service.fetch_frost_climo_yearly_for_periods(
            station_id=station_id,
            periods=frost_selected_periods,
            client_id=frost_service.FROST_CLIENT_ID,
            client_secret=frost_service.FROST_CLIENT_SECRET,
        ),
    }
    return frost_fetchers[summary_mode]()


def _fetch_generic_historical_dataset(*, provider_id, station_id, api_key, summary_mode, periods, selected_years, get_aemet_service, get_meteofrance_service, get_meteogalicia_service):
    provider_id = str(provider_id or "").strip().upper()
    historical_fetchers = {
        "AEMET": {
            "daily_periods": lambda: get_aemet_service().fetch_aemet_climo_daily_for_periods(
                idema=station_id,
                periods=periods,
                api_key=api_key,
            ),
            "monthly_year": lambda year: get_aemet_service().fetch_aemet_climo_monthly_for_year(
                idema=station_id,
                year=year,
                api_key=api_key,
            ),
            "annual_years": lambda years: get_aemet_service().fetch_aemet_climo_yearly_for_years(
                idema=station_id,
                years=years,
                api_key=api_key,
            ),
        },
        "METEOFRANCE": {
            "daily_periods": lambda: get_meteofrance_service().fetch_meteofrance_climo_daily_for_periods(
                station_id=station_id,
                periods=periods,
                api_key=api_key,
            ),
            "monthly_year": lambda year: get_meteofrance_service().fetch_meteofrance_climo_monthly_for_year(
                station_id=station_id,
                year=year,
                api_key=api_key,
            ),
            "annual_years": lambda years: get_meteofrance_service().fetch_meteofrance_climo_yearly_for_years(
                station_id=station_id,
                years=years,
                api_key=api_key,
            ),
        },
        "METEOGALICIA": {
            "daily_periods": lambda: get_meteogalicia_service().fetch_mgalicia_climo_daily_for_periods(
                station_id=station_id,
                periods=periods,
            ),
            "monthly_year": lambda year: get_meteogalicia_service().fetch_mgalicia_climo_monthly_for_year(
                station_id=station_id,
                year=year,
            ),
            "annual_years": lambda years: get_meteogalicia_service().fetch_mgalicia_climo_yearly_for_years(
                station_id=station_id,
                years=years,
            ),
        },
    }
    config = historical_fetchers.get(provider_id)
    if not config:
        return None
    if summary_mode == "monthly":
        return config["daily_periods"]()
    if len(selected_years) == 1:
        return config["monthly_year"](int(selected_years[0]))
    return config["annual_years"]([int(year) for year in selected_years])


def _fetch_meteocat_historical_dataset(*, station_id, summary_mode, periods, selected_years, get_meteocat_service):
    meteocat_service = get_meteocat_service()
    extremes_overrides = None
    if summary_mode == "annual" and len(selected_years) > 1:
        daily_df = meteocat_service.fetch_meteocat_annual_history_for_years(
            station_code=station_id,
            years=[int(year) for year in selected_years],
        )
    elif summary_mode == "annual" and len(selected_years) == 1:
        selected_year = int(selected_years[0])
        daily_df = meteocat_service.fetch_meteocat_monthly_history_for_year(
            station_code=station_id,
            year=selected_year,
        )
        extremes_overrides = meteocat_service.fetch_meteocat_daily_extremes_for_year(
            station_code=station_id,
            year=selected_year,
        )
    elif summary_mode == "monthly":
        daily_df = (
            meteocat_service.fetch_meteocat_daily_history_for_periods(
                station_code=station_id,
                periods=periods,
            ) if len(periods) == 1
            else meteocat_service.fetch_meteocat_monthly_history_for_periods(
                station_code=station_id,
                periods=periods,
            )
        )
        extremes_overrides = meteocat_service.fetch_meteocat_daily_extremes_for_periods(
            station_code=station_id,
            periods=periods,
        )
    else:
        daily_df = meteocat_service.fetch_meteocat_daily_history_for_periods(
            station_code=station_id,
            periods=periods,
        )
    return daily_df, extremes_overrides


def fetch_historical_dataset(
    *,
    provider_id: str,
    climograms_service,
    station_id: str,
    api_key,
    summary_mode: str,
    periods,
    selected_years,
    selected_months,
    frost_selected_period: str,
    frost_selected_periods,
    get_frost_service,
    get_meteocat_service,
    get_aemet_service,
    get_meteofrance_service,
    get_meteogalicia_service,
):
    provider_id = str(provider_id or "").strip().upper()
    if provider_id == "WU":
        return climograms_service.fetch_wu_daily_history_for_periods(
            station_id=station_id,
            api_key=api_key,
            periods=periods,
        ), None

    if provider_id == "FROST":
        return _fetch_frost_historical_dataset(
            station_id=station_id,
            summary_mode=summary_mode,
            selected_months=selected_months,
            frost_selected_period=frost_selected_period,
            frost_selected_periods=frost_selected_periods,
            get_frost_service=get_frost_service,
        ), None

    generic_dataset = _fetch_generic_historical_dataset(
        provider_id=provider_id,
        station_id=station_id,
        api_key=api_key,
        summary_mode=summary_mode,
        periods=periods,
        selected_years=selected_years,
        get_aemet_service=get_aemet_service,
        get_meteofrance_service=get_meteofrance_service,
        get_meteogalicia_service=get_meteogalicia_service,
    )
    if generic_dataset is not None:
        return generic_dataset, None

    return _fetch_meteocat_historical_dataset(
        station_id=station_id,
        summary_mode=summary_mode,
        periods=periods,
        selected_years=selected_years,
        get_meteocat_service=get_meteocat_service,
    )

