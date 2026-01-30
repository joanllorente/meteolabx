"""
Módulo API
"""
from .weather_underground import (
    WuError,
    fetch_wu_current,
    fetch_wu_current_session_cached,
    fetch_daily_timeseries
)

__all__ = [
    'WuError',
    'fetch_wu_current',
    'fetch_wu_current_session_cached',
    'fetch_daily_timeseries',
]
