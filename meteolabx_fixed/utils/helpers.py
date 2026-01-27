"""
Funciones auxiliares generales
"""
import textwrap
from datetime import datetime


def html_clean(s: str) -> str:
    """Limpia y dedenta HTML"""
    return textwrap.dedent(s).strip()


def is_nan(x):
    """Verifica si un valor es NaN"""
    return x != x


def normalize_text_input(value) -> str:
    """Normaliza entrada de texto a string"""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def es_datetime_from_epoch(epoch: int) -> str:
    """Convierte epoch a datetime"""
    dt = datetime.fromtimestamp(epoch)
    return dt.strftime("%d-%m-%Y %H:%M:%S")


def age_string(epoch: int) -> str:
    """Calcula la edad de un dato desde epoch"""
    import time
    diff_s = int(time.time() - epoch)
    if diff_s < 60:
        return f"{diff_s}s"
    elif diff_s < 3600:
        return f"{diff_s // 60}m"
    else:
        return f"{diff_s // 3600}h {(diff_s % 3600) // 60}m"


def fmt_hpa(x, decimals=1):
    """Formatea presión en hPa"""
    if is_nan(x):
        return "—"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.{decimals}f}"
