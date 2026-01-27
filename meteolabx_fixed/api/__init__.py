"""
Módulo API
"""
from .weather_underground import (
    WuError,
    fetch_wu_current,
    fetch_wu_current_session_cached
)

__all__ = [
    'WuError',
    'fetch_wu_current',
    'fetch_wu_current_session_cached',
]
