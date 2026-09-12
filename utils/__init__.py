"""
Módulo de utilidades.

Reexportaba también la internacionalización y el almacenamiento del navegador,
que dependían de Streamlit. Se hicieron perezosos para que un ``from
utils.units import ...`` no arrastrase el paquete entero, y al retirar la
interfaz desaparecieron: lo que traduce sin pantalla es ``domain.i18n_catalog``.
"""
from .helpers import (
    html_clean,
    is_nan,
    normalize_text_input,
    coerce_str,
    es_datetime_from_epoch,
    age_string,
    fmt_hpa
)

__all__ = [
    'html_clean',
    'is_nan',
    'normalize_text_input',
    'coerce_str',
    'es_datetime_from_epoch',
    'age_string',
    'fmt_hpa',
]
