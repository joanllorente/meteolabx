"""
Servicio para interactuar con AEMET OpenData API
"""
import requests
import streamlit as st
import time
from typing import Dict, Optional, List, Any, Tuple
from datetime import datetime, timedelta, timezone, date
from urllib.parse import quote
import re
import pandas as pd

# API Key de AEMET
AEMET_API_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXRlb2xhYnhAZ21haWwuY29tIiwianRpIjoiNTdkMzE1MjYtMTk4My00YzNiLTgzNjAtYTdkZWJmMmIxMDFhIiwiaXNzIjoiQUVNRVQiLCJpYXQiOjE3NzAyNDQ1OTEsInVzZXJJZCI6IjU3ZDMxNTI2LTE5ODMtNGMzYi04MzYwLWE3ZGViZjJiMTAxYSIsInJvbGUiOiIifQ.GvliQHY3f94N691sU0ExhMHZxbTiGn2BCe-bIA22K8c"

BASE_URL = "https://opendata.aemet.es/opendata/api"

CLIMO_DAILY_SCHEMA = [
    "date",
    "epoch",
    "temp_mean",
    "temp_max",
    "temp_min",
    "wind_mean",
    "gust_max",
    "precip_total",
]

CLIMO_EXTRA_SCHEMA = [
    "solar_mean",
    "solar_hours",       # horas de sol diarias (endpoint diario AEMET, campo 'sol')
    "precip_max_24h",
    "rain_days",
    "temp_abs_max",
    "temp_abs_max_date",
    "temp_abs_min",
    "temp_abs_min_date",
    "gust_abs_max_date",
    "precip_max_24h_date",
    "tropical_nights",   # noches tropicales del mes (nt_30 de AEMET mensual)
    "frost_nights",      # noches de helada del mes (nt_00 de AEMET mensual)
]


def _parse_num(value):
    """Parseo robusto de números AEMET (coma decimal, vacíos, 'Ip', paréntesis…).

    AEMET devuelve campos como ta_max='37.4(27)', p_max='29.0(09)',
    q_min='984.2(09/nov)', w_racha='99/21.1(07)' donde el paréntesis
    indica el día/mes de ocurrencia. Este parser extrae solo la parte numérica.
    """
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return float("nan")
    try:
        s = str(value).strip()
        if not s:
            return float("nan")
        # Quitar parte entre paréntesis: "37.4(27)" → "37.4", "984.2(09/nov)" → "984.2"
        paren_idx = s.find("(")
        if paren_idx > 0:
            s = s[:paren_idx].strip()
        # w_racha usa "dir/velocidad": "99/21.1" → tomar la última parte
        if "/" in s:
            s = s.rsplit("/", 1)[-1].strip()
        s = s.replace(",", ".")
        if s.lower() in {"ip", "nan", "none", "--", "-"}:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


_MONTH_ABBR_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _extract_paren_date(raw_value: Any, period_str: Any) -> Optional[str]:
    """Extrae fecha desde valores AEMET con paréntesis.

    Ejemplos:
        _extract_paren_date("37.4(27)", "2010-8")       → "2010-08-27"
        _extract_paren_date("37.4(27/ago)", "2010-13")   → "2010-08-27"
        _extract_paren_date("-2.0(27/dic)", "2010-13")   → "2010-12-27"
    """
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    p_start = s.find("(")
    p_end = s.find(")")
    if p_start < 0 or p_end <= p_start + 1:
        return None
    paren = s[p_start + 1:p_end].strip()

    # Año desde la cadena de periodo ("2010-8", "2010-13", etc.)
    period = str(period_str or "").strip()
    if len(period) < 4 or not period[:4].isdigit():
        return None
    year = int(period[:4])

    # Mes: puede venir del periodo o del paréntesis
    month: Optional[int] = None
    if "-" in period:
        mp = period.split("-", 1)[1]
        if mp.isdigit():
            mm = int(mp)
            if 1 <= mm <= 12:
                month = mm

    # Parsear paréntesis: "27" (solo día) o "27/ago" (día/mes)
    if "/" in paren:
        parts = paren.split("/", 1)
        day_str = parts[0].strip()
        month_str = parts[1].strip().lower()
        month = _MONTH_ABBR_ES.get(month_str, month)
    else:
        day_str = paren

    try:
        day = int(day_str)
    except ValueError:
        return None

    if month is None or day < 1 or day > 31:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_epoch_any(fint_str: str) -> Optional[int]:
    """Parsea timestamps en varios formatos habituales de AEMET."""
    if not fint_str:
        return None

    raw = str(fint_str).strip()
    clean = raw.replace("UTC", "").replace("Z", "").strip()
    clean = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', clean)

    # Intento ISO
    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass

    # Formatos alternativos observados en integraciones AEMET
    patterns = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ]
    for pattern in patterns:
        try:
            dt = datetime.strptime(clean, pattern).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue

    return None


def _extract_timestamp(record: Dict) -> Optional[str]:
    """Obtiene timestamp aunque la clave cambie según endpoint/estación."""
    if not isinstance(record, dict):
        return None

    record_ci = {str(k).lower(): v for k, v in record.items()}

    for key in ("Fecha", "fecha", "fint", "FINT", "fhora", "FHORA"):
        value = record.get(key)
        if value is None:
            value = record_ci.get(str(key).lower())
        if value is not None and value != "":
            return str(value)

    # Fallback por patrón de nombre de campo
    for key, value in record_ci.items():
        if value is None or value == "":
            continue
        if "fecha" in key or "fint" in key or "hora" in key:
            return str(value)
    return None


def _aemet_first_non_empty(record: Dict[str, Any], keys: List[str]):
    """Devuelve el primer campo no vacío ignorando mayúsculas/minúsculas."""
    record_ci = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = record.get(key)
        if value is None:
            value = record_ci.get(str(key).lower())
        if value is not None and value != "":
            return value
    return None


def _aemet_first_by_patterns(record: Dict[str, Any], keys: List[str], patterns: List[str]):
    """Busca primero por claves conocidas y luego por patrón de nombre de campo."""
    value = _aemet_first_non_empty(record, keys)
    if value is not None and value != "":
        return value

    for k, v in record.items():
        if v is None or v == "":
            continue
        lk = str(k).lower()
        if any(p in lk for p in patterns):
            return v
    return None


def _parse_wind_dir_deg(value) -> float:
    """Parsea dirección de viento a grados, aceptando numérico y cardinal (ES/EN)."""
    num = _parse_num(value)
    if num == num:
        return num % 360.0

    if value is None:
        return float("nan")

    s = str(value).strip().upper()
    if not s:
        return float("nan")
    if s in {"CALMA", "CALM", "VARIABLE", "VRB"}:
        return 0.0

    # En español se usa O para Oeste.
    s_norm = s.replace("O", "W")

    cardinal_16 = {
        "N": 0.0,
        "NNE": 22.5,
        "NE": 45.0,
        "ENE": 67.5,
        "E": 90.0,
        "ESE": 112.5,
        "SE": 135.0,
        "SSE": 157.5,
        "S": 180.0,
        "SSW": 202.5,
        "SW": 225.0,
        "WSW": 247.5,
        "W": 270.0,
        "WNW": 292.5,
        "NW": 315.0,
        "NNW": 337.5,
    }

    return cardinal_16.get(s_norm, float("nan"))


def _fetch_aemet_opendata_list(
    endpoint: str,
    label: str,
    api_key: Optional[str] = None,
) -> Optional[List[Dict]]:
    """Patrón OpenData de 2 pasos: endpoint -> URL temporal -> lista JSON."""
    key = str(api_key or AEMET_API_KEY).strip()
    headers = {"api_key": key}
    last_step1_exc: Optional[Exception] = None
    response = None
    for _att in range(3):
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            response.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_step1_exc = exc
            response = None
            time.sleep(2)
    if response is None:
        raise last_step1_exc or RuntimeError("No se pudo conectar con AEMET OpenData")

    result = response.json()
    print(
        f"📡 [AEMET API:{label}] Estado: {result.get('estado')} - "
        f"{result.get('descripcion', 'N/A')}"
    )

    if result.get("estado") != 200:
        print(f"❌ [AEMET API:{label}] Error en respuesta: {result}")
        return None

    datos_url = result.get("datos")
    if not datos_url:
        print(f"❌ [AEMET API:{label}] No hay URL de datos en respuesta")
        return None

    print(f"⬇️ [AEMET API:{label}] Descargando datos desde: {datos_url[:80]}...")
    last_exc: Optional[Exception] = None
    data_response = None
    for _attempt in range(3):
        try:
            data_response = requests.get(datos_url, timeout=60)
            data_response.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            data_response = None
            time.sleep(2)
    if data_response is None:
        raise last_exc or RuntimeError("No se pudo descargar datos de AEMET")

    try:
        data = data_response.json()
    except Exception:
        data = data_response.content.decode("latin-1")
        import json
        data = json.loads(data)

    if isinstance(data, list):
        print(f"✅ [AEMET API:{label}] Descargados {len(data)} registros")
        return data

    print(f"❌ [AEMET API:{label}] Datos no son lista: {type(data)}")
    return None


def _parse_aemet_basic_series(data_list: Optional[List[Dict]]) -> Dict[str, Any]:
    """Parsea serie AEMET a formato homogéneo para tendencias."""
    if not data_list:
        return {
            "epochs": [],
            "temps": [],
            "humidities": [],
            "pressures": [],
            "has_data": False,
        }

    rows = []
    for row in data_list:
        if not isinstance(row, dict):
            continue

        ts = _extract_timestamp(row)
        ep = _parse_epoch_any(ts) if ts else None
        if ep is None:
            continue

        temp = _parse_num(_aemet_first_non_empty(row, ["TA", "ta", "TPRE", "tpre", "T", "t", "TEMP", "temp"]))
        rh = _parse_num(_aemet_first_non_empty(row, ["hr", "HR", "hrel", "HREL", "humidity", "HUMIDITY"]))
        p_station = _parse_num(_aemet_first_non_empty(row, ["pres", "PRES"]))
        p_msl = _parse_num(_aemet_first_non_empty(row, ["pres_nmar", "PRES_NMAR", "pnm", "PNM", "pressure", "PRESSURE"]))
        # Priorizar presión de estación (absoluta) para cálculos termodinámicos.
        p = p_station if p_station == p_station else p_msl

        if temp != temp and rh != rh and p != p:
            continue

        rows.append((ep, temp, rh, p))

    if not rows:
        return {
            "epochs": [],
            "temps": [],
            "humidities": [],
            "pressures": [],
            "has_data": False,
        }

    rows.sort(key=lambda x: x[0])
    dedup = {}
    for ep, temp, rh, p in rows:
        dedup[ep] = (temp, rh, p)

    epochs = sorted(dedup.keys())
    temps = [float(dedup[ep][0]) for ep in epochs]
    humidities = [float(dedup[ep][1]) for ep in epochs]
    pressures = [float(dedup[ep][2]) for ep in epochs]

    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": humidities,
        "pressures": pressures,
        "has_data": len(epochs) > 0,
    }


@st.cache_data(ttl=600)  # Caché de 10 minutos (AEMET actualiza ~cada 30 min)
def fetch_aemet_station_data(idema: str) -> Optional[Dict]:
    """
    Obtiene datos actuales de una estación AEMET
    
    AEMET usa un patrón de 2 pasos:
    1. Llamar al endpoint → devuelve URL temporal
    2. Llamar a la URL temporal → devuelve datos
    
    Args:
        idema: ID de la estación (ej: "0201X")
        
    Returns:
        Diccionario con datos de la estación o None si falla
    """
    try:
        # Paso 1: Obtener URL de datos
        endpoint = f"{BASE_URL}/observacion/convencional/datos/estacion/{idema}"
        headers = {"api_key": AEMET_API_KEY}
        
        response = requests.get(endpoint, headers=headers, timeout=15)
        response.raise_for_status()
        
        result = response.json()
        
        if result.get("estado") != 200:
            st.warning(f"⚠️ AEMET no respondió correctamente: {result.get('descripcion', 'desconocido')}")
            return None
        
        datos_url = result.get("datos")
        if not datos_url:
            st.warning("⚠️ AEMET no devolvió URL de datos de la estación.")
            return None
        
        # Paso 2: Descargar datos desde URL temporal (servidor muy lento)
        # Aumentar timeout a 60 segundos
        data_response = requests.get(datos_url, timeout=60)
        data_response.raise_for_status()
        
        # AEMET usa latin-1 encoding
        try:
            data = data_response.json()
        except:
            # Intentar con latin-1 si UTF-8 falla
            data = data_response.content.decode('latin-1')
            import json
            data = json.loads(data)
        
        # Devolver el ÚLTIMO elemento (datos más recientes)
        # AEMET devuelve lista ordenada cronológicamente, el último es el más nuevo
        if isinstance(data, list) and len(data) > 0:
            return data[-1]  # Último elemento (más reciente)
        elif isinstance(data, dict):
            return data
        else:
            return None
            
    except requests.exceptions.Timeout:
        st.warning("⏱️ La estación o el servidor de AEMET no responde a tiempo. Inténtalo de nuevo en unos minutos.")
        # No cachear errores - lanzar excepción para que streamlit no guarde en caché
        st.cache_data.clear()
        return None
    except requests.exceptions.RequestException as e:
        st.warning(f"⚠️ No se pudo contactar con AEMET ahora mismo (red/servidor). Detalle: {e}")
        st.cache_data.clear()
        return None
    except Exception as e:
        st.error(f"❌ Error inesperado: {e}")
        st.cache_data.clear()
        return None


def parse_aemet_data(raw_data: Dict) -> Dict:
    """
    Parsea datos crudos de AEMET al formato de MeteoLabX
    
    Campos de AEMET:
    - ta: Temperatura (°C)
    - hr: Humedad relativa (%)
    - pres: Presión a nivel de estación (hPa)
    - pres_nmar: Presión a nivel del mar (hPa)
    - vv: Velocidad del viento (m/s)
    - dv: Dirección del viento (grados)
    - vmax: Racha máxima (m/s)
    - prec: Precipitación (mm)
    - alt: Altitud (m)
    - fint: Fecha/hora (ISO 8601)
    
    Args:
        raw_data: Datos crudos de AEMET
        
    Returns:
        Diccionario con datos en formato MeteoLabX (campos en MAYÚSCULAS)
    """
    record_ci = {str(k).lower(): v for k, v in raw_data.items()}

    def field(*keys):
        for key in keys:
            value = raw_data.get(key)
            if value is None:
                value = record_ci.get(str(key).lower())
            if value is not None and value != "":
                return value
        return None

    # Conversión de m/s a km/h
    def ms_to_kmh(ms):
        num = _parse_num(ms)
        return num * 3.6 if num == num else float("nan")
    
    # Convertir timestamp ISO de AEMET a epoch
    fint_str = field("fint", "FINT", "Fecha", "fecha", "fhora")
    
    if fint_str:
        epoch = _parse_epoch_any(fint_str)
        if epoch is not None:
            dt_local = datetime.fromtimestamp(epoch)
            print(f"✅ [AEMET] Timestamp OK: '{fint_str}' → epoch={epoch} → local={dt_local}")
        else:
            print(f"❌ [AEMET] Error parseando timestamp '{fint_str}'")
            epoch = int(time.time())
            print(f"⚠️ [AEMET] Usando timestamp actual como fallback: {epoch}")
    else:
        print(f"⚠️ [AEMET] Campo 'fint' no encontrado en datos, usando hora actual")
        epoch = int(time.time())
    
    # Max/min temperatura - AEMET los da cuando están disponibles
    tamax = _parse_num(field("tamax", "TAMAX"))
    tamin = _parse_num(field("tamin", "TAMIN"))
    
    return {
        # Temperatura - MAYÚSCULA para compatibilidad
        "Tc": _parse_num(field("ta", "TA", "t", "T", "temp", "TEMP", "tpre", "TPRE")),
        "temp_max": tamax,
        "temp_min": tamin,
        
        # Humedad - MAYÚSCULA para compatibilidad
        "RH": _parse_num(field("hr", "HR", "hrel", "HREL")),
        "rh": _parse_num(field("hr", "HR", "hrel", "HREL")),  # Duplicado por si acaso
        "rh_max": None,  # AEMET no provee
        "rh_min": None,  # AEMET no provee
        
        # Presión
        "p_hpa": _parse_num(field("pres_nmar", "PRES_NMAR", "pnm", "PNM")),  # Presión a nivel del mar
        "p_station": _parse_num(field("pres", "PRES")),                      # Presión a nivel de estación
        
        # Viento
        "wind": ms_to_kmh(field("vv", "VV", "ff", "FF", "viento")),
        "wind_speed_kmh": ms_to_kmh(field("vv", "VV", "ff", "FF", "viento")),
        "wind_dir_deg": _parse_wind_dir_deg(field("dv", "DV", "dd", "DD", "dir", "DIR", "dir_viento", "direccion_viento")),
        "gust": ms_to_kmh(field("vmax", "VMAX", "fx", "FX", "racha", "RACHA")),
        "gust_max": ms_to_kmh(field("vmax", "VMAX", "fx", "FX", "racha", "RACHA")),
        
        # Precipitación
        "precip_total": _parse_num(field("prec", "PREC", "precip", "PR", "pr", "lluvia")),
        
        # Metadatos
        "elevation": _parse_num(field("alt", "ALT", "elev", "ELEV")),
        "epoch": epoch,  # Timestamp convertido de ISO
        "fint": field("fint", "FINT", "Fecha", "fecha", "fhora"),  # Timestamp de AEMET
        "lat": _parse_num(field("lat", "LAT")),
        "lon": _parse_num(field("lon", "LON")),
        "ubi": field("ubi", "UBI") or "",
        "idema": field("idema", "IDEMA") or "",
        
        # Punto de rocío - se calcula, no usar dato de AEMET
        "Td": float("nan"),
        
        # Campos no disponibles en AEMET (valores NaN)
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
    }


def is_aemet_connection() -> bool:
    """Verifica si la conexión actual es a AEMET"""
    return st.session_state.get("connection_type") == "AEMET"


def get_aemet_data() -> Optional[Dict]:
    """
    Obtiene y parsea datos de AEMET de la estación conectada
    
    Returns:
        Diccionario con datos parseados o None
    """
    if not is_aemet_connection():
        return None
    
    idema = st.session_state.get("aemet_station_id")
    if not idema:
        return None
    
    raw_data = fetch_aemet_station_data(idema)
    if not raw_data:
        return None
    
    return parse_aemet_data(raw_data)


@st.cache_data(ttl=600)  # Caché de 10 minutos
def fetch_aemet_daily_timeseries(idema: str) -> Optional[List[Dict]]:
    """
    Obtiene serie temporal AEMET priorizando endpoints diezminutales.

    Orden de consulta principal (3 endpoints diezminutales):
    1) /observacion/convencional/diezminutal/datos/estacion/{idema}
    2) /observacion/convencional/diezminutal/datos/fecha/{hoy}/estacion/{idema}
    3) /observacion/convencional/diezminutal/datos/fecha/{ayer}/estacion/{idema}

    Solo si no hay serie útil diezminutal se usa /observacion/convencional/todas como último recurso.
    """
    def fetch_from_endpoint(endpoint: str, label: str) -> Optional[List[Dict]]:
        """Patrón OpenData de 2 pasos: endpoint -> URL temporal -> lista JSON."""
        try:
            headers = {"api_key": AEMET_API_KEY}
            response = requests.get(endpoint, headers=headers, timeout=15)
            response.raise_for_status()

            result = response.json()
            print(
                f"📡 [AEMET API:{label}] Estado: {result.get('estado')} - "
                f"{result.get('descripcion', 'N/A')}"
            )

            if result.get("estado") != 200:
                print(f"❌ [AEMET API:{label}] Error en respuesta: {result}")
                return None

            datos_url = result.get("datos")
            if not datos_url:
                print(f"❌ [AEMET API:{label}] No hay URL de datos en respuesta")
                return None

            print(f"⬇️ [AEMET API:{label}] Descargando datos desde: {datos_url[:80]}...")
            data_response = requests.get(datos_url, timeout=60)
            data_response.raise_for_status()

            try:
                data = data_response.json()
            except Exception:
                data = data_response.content.decode("latin-1")
                import json
                data = json.loads(data)

            if isinstance(data, list):
                print(f"✅ [AEMET API:{label}] Descargados {len(data)} registros")
                return data

            print(f"❌ [AEMET API:{label}] Datos no son lista: {type(data)}")
            return None
        except Exception as e:
            print(f"⚠️ [AEMET API:{label}] Error consultando endpoint: {e}")
            return None

    def series_stats(data: Optional[List[Dict]]) -> Dict[str, float]:
        """Métricas de calidad, incluyendo cadencia para priorizar diezminutal real."""
        stats: Dict[str, float] = {
            "ts_valid": 0,
            "temp_valid": 0,
            "wind_valid": 0,
            "wind_nonzero": 0,
            "gust_valid": 0,
            "dir_valid": 0,
            "wind_dir_valid": 0,
            "dir_unique": 0,
            "latest_epoch": 0,
            "median_step_min": 999.0,
            "step_10m_ratio": 0.0,
        }
        if not data:
            return stats

        epochs: List[int] = []
        dir_bins = set()

        for row in data:
            if not isinstance(row, dict):
                continue

            ts = _extract_timestamp(row)
            ep = _parse_epoch_any(ts) if ts else None
            if ep is None:
                continue

            stats["ts_valid"] += 1
            epochs.append(int(ep))
            if ep > stats["latest_epoch"]:
                stats["latest_epoch"] = ep

            ta = _parse_num(_aemet_first_non_empty(row, ["TA", "ta", "TPRE", "tpre", "T", "t", "TEMP", "temp"]))
            vv = _parse_num(_aemet_first_by_patterns(
                row,
                ["VV", "vv", "FF", "ff", "VVIENTO", "v_viento", "viento", "vel_viento", "velocidad_viento"],
                ["vv", "ff", "viento", "vel_viento", "velocidad_viento", "wind"],
            ))
            vmax = _parse_num(_aemet_first_by_patterns(
                row,
                ["VMAX", "vmax", "FX", "fx", "RACHA", "racha", "racha_max", "v_racha", "vmax10m", "windgust"],
                ["vmax", "racha", "fx", "gust"],
            ))
            dv = _parse_wind_dir_deg(_aemet_first_by_patterns(
                row,
                ["DV", "dv", "DD", "dd", "dir_viento", "direccion_viento", "DIR", "dir", "winddir"],
                ["dv", "dd", "dir", "direccion", "winddir"],
            ))

            has_t = ta == ta
            has_w = vv == vv
            has_g = vmax == vmax
            has_d = dv == dv

            if has_t:
                stats["temp_valid"] += 1
            if has_w:
                stats["wind_valid"] += 1
                if vv > 0.3:
                    stats["wind_nonzero"] += 1
            if has_g:
                stats["gust_valid"] += 1
            if has_d:
                stats["dir_valid"] += 1

            speed_ref = float("nan")
            if has_w and has_g:
                speed_ref = max(vv, vmax)
            elif has_w:
                speed_ref = vv
            elif has_g:
                speed_ref = vmax

            if has_d and speed_ref == speed_ref and speed_ref > 0.3:
                stats["wind_dir_valid"] += 1
                idx = int((dv + 11.25) // 22.5) % 16
                dir_bins.add(idx)

        if len(epochs) >= 2:
            epochs_sorted = sorted(set(epochs))
            diffs_min: List[float] = []
            for i in range(1, len(epochs_sorted)):
                d_sec = epochs_sorted[i] - epochs_sorted[i - 1]
                if d_sec <= 0:
                    continue
                d_min = d_sec / 60.0
                if d_min <= 240.0:
                    diffs_min.append(d_min)

            if diffs_min:
                s = sorted(diffs_min)
                mid = len(s) // 2
                stats["median_step_min"] = s[mid] if len(s) % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0
                stats["step_10m_ratio"] = sum(1 for d in s if 5.0 <= d <= 15.0) / len(s)

        stats["dir_unique"] = len(dir_bins)
        return stats

    def add_candidate(candidates: List[tuple], label: str, data: Optional[List[Dict]], kind: str) -> None:
        stats = series_stats(data)
        print(
            f"ℹ️ [AEMET API] Calidad {label}: "
            f"ts={int(stats['ts_valid'])}, temp={int(stats['temp_valid'])}, "
            f"wind={int(stats['wind_valid'])}, wind_nz={int(stats['wind_nonzero'])}, "
            f"dir={int(stats['dir_valid'])}, wind_dir={int(stats['wind_dir_valid'])}, dir_u={int(stats['dir_unique'])}, "
            f"step_med={stats['median_step_min']:.1f}min, step10m={stats['step_10m_ratio']:.2f}, "
            f"latest={int(stats['latest_epoch'])}"
        )
        if data:
            candidates.append((label, data, stats, kind))

    try:
        print(f"🔄 [AEMET API] Solicitando serie temporal para {idema}...")
        candidates: List[tuple] = []

        # Endpoint 1: por estación (diezminutal)
        endpoint_station = f"{BASE_URL}/observacion/convencional/diezminutal/datos/estacion/{idema}"
        add_candidate(candidates, "station", fetch_from_endpoint(endpoint_station, "station"), "10m")

        # Endpoints 2 y 3: por fecha (hoy y ayer) diezminutal
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        yesterday = (now_utc - timedelta(days=1)).date()

        date_payloads: List[List[Dict]] = []
        for day, day_label in ((today, "date_today"), (yesterday, "date_yesterday")):
            fecha = f"{day.isoformat()}T00:00:00UTC"
            fecha_encoded = quote(fecha, safe="")
            endpoint_date = (
                f"{BASE_URL}/observacion/convencional/diezminutal/"
                f"datos/fecha/{fecha_encoded}/estacion/{idema}"
            )
            day_data = fetch_from_endpoint(endpoint_date, day_label)
            add_candidate(candidates, day_label, day_data, "10m")
            if day_data:
                date_payloads.append(day_data)

        # Candidato combinado hoy+ayer para maximizar continuidad
        if date_payloads:
            dedup = {}
            for rows in date_payloads:
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ts = _extract_timestamp(row)
                    rid = row.get("idema", idema)
                    key = f"{rid}|{ts}"
                    dedup[key] = row
            merged = list(dedup.values())
            add_candidate(candidates, "date_merged", merged, "10m")

        # Fallback no diezminutal solo si no hay candidatos 10m útiles
        ten_min_candidates = [c for c in candidates if c[3] == "10m" and c[2]["ts_valid"] > 0]
        if not ten_min_candidates:
            print("⚠️ [AEMET API] Sin diezminutal útil, intento fallback /observacion/convencional/todas")
            endpoint_all = f"{BASE_URL}/observacion/convencional/todas"
            data_all = fetch_from_endpoint(endpoint_all, "all24h")
            if data_all:
                target_id = str(idema).strip().upper()
                filtered = []
                for row in data_all:
                    if not isinstance(row, dict):
                        continue
                    row_id = str(row.get("idema", "")).strip().upper()
                    if row_id == target_id:
                        filtered.append(row)
                add_candidate(candidates, "all24h", filtered, "fallback")

        if not candidates:
            return None

        ten_min_candidates = [c for c in candidates if c[3] == "10m" and c[2]["ts_valid"] > 0]
        pool = ten_min_candidates if ten_min_candidates else candidates

        priority = {
            "station": 4,
            "date_today": 3,
            "date_yesterday": 2,
            "date_merged": 1,
            "all24h": 0,
        }

        def score(item: tuple):
            label, _data, stt, _kind = item
            median_step = stt["median_step_min"]
            cadence_closeness = -abs(median_step - 10.0) if median_step < 900 else -999.0
            return (
                stt["step_10m_ratio"],
                cadence_closeness,
                stt["wind_nonzero"],
                stt["dir_unique"],
                stt["wind_dir_valid"],
                stt["wind_valid"],
                stt["dir_valid"],
                stt["gust_valid"],
                stt["ts_valid"],
                stt["latest_epoch"],
                stt["temp_valid"],
                priority.get(label, 0),
                len(_data),
            )

        source, data_best, best_stats, best_kind = max(pool, key=score)
        print(
            f"✅ [AEMET API] Fuente elegida: {source} ({best_kind}) "
            f"(ts={int(best_stats['ts_valid'])}, temp={int(best_stats['temp_valid'])}, "
            f"wind={int(best_stats['wind_valid'])}, wind_nz={int(best_stats['wind_nonzero'])}, "
            f"dir={int(best_stats['dir_valid'])}, wind_dir={int(best_stats['wind_dir_valid'])}, dir_u={int(best_stats['dir_unique'])}, "
            f"step_med={best_stats['median_step_min']:.1f}min, step10m={best_stats['step_10m_ratio']:.2f}, "
            f"latest={int(best_stats['latest_epoch'])}, registros={len(data_best)})"
        )
        return data_best

    except Exception as e:
        print(f"❌ [AEMET API] Error obteniendo serie temporal: {e}")
        import traceback
        traceback.print_exc()
        return None


@st.cache_data(ttl=600)
def fetch_aemet_all24h_station_series(idema: str) -> Dict[str, Any]:
    """Serie de últimas 24h para una estación concreta usando endpoint global /todas."""
    try:
        endpoint_all = f"{BASE_URL}/observacion/convencional/todas"
        data_all = _fetch_aemet_opendata_list(endpoint_all, "all24h_station_series")
        if not data_all:
            return {
                "epochs": [],
                "temps": [],
                "humidities": [],
                "pressures": [],
                "has_data": False,
            }

        target_id = str(idema).strip().upper()
        filtered = []
        for row in data_all:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("idema", "")).strip().upper()
            if row_id == target_id:
                filtered.append(row)

        parsed = _parse_aemet_basic_series(filtered)
        if not parsed.get("has_data", False):
            return parsed

        now_epoch = int(time.time())
        min_epoch = now_epoch - (24 * 3600)
        rows = [
            (ep, t, rh, p)
            for ep, t, rh, p in zip(
                parsed["epochs"],
                parsed["temps"],
                parsed["humidities"],
                parsed["pressures"],
            )
            if min_epoch <= ep <= now_epoch + 3600
        ]

        if not rows:
            return {
                "epochs": [],
                "temps": [],
                "humidities": [],
                "pressures": [],
                "has_data": False,
            }

        return {
            "epochs": [r[0] for r in rows],
            "temps": [r[1] for r in rows],
            "humidities": [r[2] for r in rows],
            "pressures": [r[3] for r in rows],
            "has_data": True,
        }

    except Exception as e:
        print(f"❌ [AEMET API] Error obteniendo serie 24h por estación: {e}")
        return {
            "epochs": [],
            "temps": [],
            "humidities": [],
            "pressures": [],
            "has_data": False,
        }


@st.cache_data(ttl=3600)
def fetch_aemet_hourly_7day_series(idema: str) -> Dict[str, Any]:
    """Serie horaria de 7 días para tendencias sinópticas."""
    try:
        now_utc = datetime.now(timezone.utc)
        ini_utc = now_utc - timedelta(days=7)

        candidates = [
            (
                ini_utc.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                now_utc.strftime("%Y-%m-%dT%H:%M:%SUTC"),
                "hourly7d:sec",
            ),
            (
                ini_utc.strftime("%Y-%m-%dT%H:%MUTC"),
                now_utc.strftime("%Y-%m-%dT%H:%MUTC"),
                "hourly7d:min",
            ),
        ]

        for fecha_ini, fecha_fin, label in candidates:
            fecha_ini_encoded = quote(fecha_ini, safe="")
            fecha_fin_encoded = quote(fecha_fin, safe="")
            endpoint = (
                f"{BASE_URL}/valores/climatologicos/horarios/datos/"
                f"fechaini/{fecha_ini_encoded}/fechafin/{fecha_fin_encoded}/estacion/{idema}"
            )
            data = _fetch_aemet_opendata_list(endpoint, label)
            parsed = _parse_aemet_basic_series(data)
            if parsed.get("has_data", False):
                return parsed

        return {
            "epochs": [],
            "temps": [],
            "humidities": [],
            "pressures": [],
            "has_data": False,
        }

    except Exception as e:
        print(f"❌ [AEMET API] Error obteniendo serie horaria 7d: {e}")
        return {
            "epochs": [],
            "temps": [],
            "humidities": [],
            "pressures": [],
            "has_data": False,
        }


def _empty_climo_dataframe(include_extras: bool = True) -> pd.DataFrame:
    columns = CLIMO_DAILY_SCHEMA + (CLIMO_EXTRA_SCHEMA if include_extras else [])
    return pd.DataFrame(columns=columns)


def _parse_aemet_climo_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    if len(raw) >= 7 and raw[4] == "-" and raw[5:7].isdigit():
        return f"{raw[:7]}-01"
    if len(raw) == 4 and raw.isdigit():
        return f"{raw}-01-01"
    try:
        dt = pd.to_datetime(raw, errors="raise")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_precip_aemet(value: Any) -> float:
    if value is None:
        return float("nan")
    raw = str(value).strip().lower()
    if raw == "ip":
        return 0.0
    return _parse_num(value)


def _parse_month_key(value: Any) -> Optional[str]:
    """Parsea 'YYYY-M' o 'YYYY-MM' a 'YYYY-MM'. Descarta mes 13 (resumen anual AEMET)."""
    raw = str(value or "").strip()
    if len(raw) < 6 or not raw[:4].isdigit() or raw[4] != "-":
        return None
    month_part = raw[5:]
    if not month_part.isdigit():
        return None
    mm = int(month_part)
    if mm < 1 or mm > 12:
        return None
    return f"{raw[:4]}-{mm:02d}"


def _parse_year_key(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    return None


def _aemet_climo_num(record: Dict[str, Any], keys: List[str], patterns: List[str]) -> float:
    value = _aemet_first_by_patterns(record, keys, patterns)
    return _parse_num(value)


def _aemet_climo_date_field(record: Dict[str, Any], keys: List[str], patterns: List[str]) -> Optional[str]:
    value = _aemet_first_by_patterns(record, keys, patterns)
    return _parse_aemet_climo_date(value)


def _aemet_daily_record_to_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None

    day_txt = _parse_aemet_climo_date(_aemet_first_non_empty(record, ["fecha", "Fecha", "fint", "FINT"]))
    if not day_txt:
        return None

    temp_mean = _aemet_climo_num(record, ["tmed", "TMED", "tm", "TM"], ["tmed", "tmedia", "temp_media"])
    temp_max = _aemet_climo_num(record, ["tmax", "TMAX", "tamax", "TAMAX"], ["tmax", "tamax"])
    temp_min = _aemet_climo_num(record, ["tmin", "TMIN", "tamin", "TAMIN"], ["tmin", "tamin"])
    wind_mean = _aemet_climo_num(record, ["velmedia", "VELMEDIA", "vv", "VV"], ["velmedia", "vv", "viento"])
    gust_max = _aemet_climo_num(record, ["racha", "RACHA", "vmax", "VMAX"], ["racha", "vmax", "gust"])
    precip_total = _parse_precip_aemet(_aemet_first_by_patterns(record, ["prec", "PREC", "pp", "PP"], ["prec", "pp"]))
    # 'sol': horas de sol del día (disponible en estaciones con piranómetro/heliógrafo)
    solar_hours = _aemet_climo_num(record, ["sol", "SOL", "insolacion", "INSOLACION"], ["sol", "insol"])

    if pd.isna(temp_mean) and not pd.isna(temp_max) and not pd.isna(temp_min):
        temp_mean = (float(temp_max) + float(temp_min)) / 2.0

    try:
        epoch = float(pd.Timestamp(day_txt).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        epoch = float("nan")

    if not pd.isna(wind_mean):
        wind_mean = float(wind_mean) * 3.6
    if not pd.isna(gust_max):
        gust_max = float(gust_max) * 3.6
    if not pd.isna(precip_total):
        precip_total = max(0.0, float(precip_total))

    return {
        "date": day_txt,
        "epoch": epoch,
        "temp_mean": float(temp_mean) if not pd.isna(temp_mean) else float("nan"),
        "temp_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "wind_mean": float(wind_mean) if not pd.isna(wind_mean) else float("nan"),
        "gust_max": float(gust_max) if not pd.isna(gust_max) else float("nan"),
        "precip_total": float(precip_total) if not pd.isna(precip_total) else float("nan"),
        "solar_hours": float(solar_hours) if not pd.isna(solar_hours) else float("nan"),
    }


def _normalize_climo_daily_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_climo_dataframe(include_extras=False)
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    if frame.empty:
        return _empty_climo_dataframe(include_extras=False)

    output_cols = CLIMO_DAILY_SCHEMA + ["solar_hours"]
    for col in output_cols:
        if col not in frame.columns:
            frame[col] = float("nan")

    numeric_cols = ["epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total", "solar_hours"]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)

    frame = (
        frame.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return frame[output_cols]


@st.cache_data(ttl=1800)
def fetch_aemet_climo_daily_period(
    idema: str,
    start_date: date,
    end_date: date,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    station = str(idema).strip().upper()
    if not station:
        return _empty_climo_dataframe(include_extras=False)

    fecha_ini = f"{start_date.strftime('%Y-%m-%d')}T00:00:00UTC"
    fecha_fin = f"{end_date.strftime('%Y-%m-%d')}T23:59:59UTC"
    fecha_ini_encoded = quote(fecha_ini, safe="")
    fecha_fin_encoded = quote(fecha_fin, safe="")
    endpoint = (
        f"{BASE_URL}/valores/climatologicos/diarios/datos/"
        f"fechaini/{fecha_ini_encoded}/fechafin/{fecha_fin_encoded}/estacion/{station}"
    )

    payload = _fetch_aemet_opendata_list(endpoint, "climo_daily", api_key=api_key)
    rows: List[Dict[str, Any]] = []
    for record in payload or []:
        row = _aemet_daily_record_to_row(record)
        if row:
            rows.append(row)
    return _normalize_climo_daily_rows(rows)


def _iter_date_chunks(start_date: date, end_date: date, max_days: int = 150):
    """Divide un rango de fechas en trozos de max_days días (AEMET limita a ~6 meses)."""
    cursor = start_date
    delta = timedelta(days=max_days - 1)
    while cursor <= end_date:
        chunk_end = min(cursor + delta, end_date)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def fetch_aemet_climo_daily_for_periods(
    idema: str,
    periods: List[Any],
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    if not periods:
        return _empty_climo_dataframe(include_extras=False)

    chunks: List[pd.DataFrame] = []
    for period in periods:
        start = getattr(period, "start")
        end = getattr(period, "end")
        # AEMET limita el endpoint diario a 6 meses: dividimos en trozos de 150 días.
        for chunk_start, chunk_end in _iter_date_chunks(start, end, max_days=150):
            chunk = fetch_aemet_climo_daily_period(
                idema=idema,
                start_date=chunk_start,
                end_date=chunk_end,
                api_key=api_key,
            )
            if not chunk.empty:
                chunks.append(chunk)

    if not chunks:
        return _empty_climo_dataframe(include_extras=False)

    all_days = pd.concat(chunks, ignore_index=True)
    all_days["date"] = pd.to_datetime(all_days["date"], errors="coerce").dt.normalize()
    all_days = (
        all_days.sort_values(["date", "epoch"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    # Incluir solar_hours si está presente (estaciones con piranómetro/heliógrafo)
    output_cols = CLIMO_DAILY_SCHEMA + (["solar_hours"] if "solar_hours" in all_days.columns else [])
    return all_days[output_cols]


@st.cache_data(ttl=1800)
def fetch_aemet_climo_monthlyannual_raw(
    idema: str,
    year_start: int,
    year_end: int,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    station = str(idema).strip().upper()
    if not station:
        return []

    y0 = int(min(year_start, year_end))
    y1 = int(max(year_start, year_end))
    endpoint = (
        f"{BASE_URL}/valores/climatologicos/mensualesanuales/datos/"
        f"anioini/{y0:04d}/aniofin/{y1:04d}/estacion/{station}"
    )
    payload = _fetch_aemet_opendata_list(endpoint, "climo_monthlyannual", api_key=api_key)
    return payload or []


def _aemet_monthlyannual_to_metrics(record: Dict[str, Any]) -> Dict[str, Any]:
    # Contexto de periodo para extracción de fechas de los paréntesis
    period_str = str(_aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"]) or "")

    temp_mean = _aemet_climo_num(record, ["tm_mes", "TM_MES", "tm", "TM", "tmed", "TMED"], ["tm_mes", "tmed", "temp_media"])
    temp_max = _aemet_climo_num(record, ["tm_max", "TM_MAX", "txm", "TXM"], ["tm_max", "txm"])
    temp_min = _aemet_climo_num(record, ["tm_min", "TM_MIN", "tnm", "TNM"], ["tm_min", "tnm"])
    wind_mean = _aemet_climo_num(record, ["w_med", "W_MED", "velmedia", "VELMEDIA", "vmedia", "VMEDIA"], ["w_med", "velmedia", "vmedia"])
    gust_max = _aemet_climo_num(record, ["w_racha", "W_RACHA", "racha", "RACHA", "vmax", "VMAX"], ["w_racha", "racha", "vmax"])
    precip_total = _parse_precip_aemet(_aemet_first_by_patterns(record, ["p_mes", "P_MES", "prec", "PREC", "pp", "PP"], ["p_mes", "prec", "pp"]))
    rain_days = _aemet_climo_num(record, ["n_llu", "N_LLU", "diaslluvia", "DIASLLUVIA", "n_dias_lluvia"], ["n_llu", "dias", "lluv"])
    precip_max_24h = _aemet_climo_num(record, ["p_max", "P_MAX", "prec_max_24h", "PREC_MAX_24H", "pmax24", "PMAX24"], ["p_max", "max24", "pmax24"])

    # Horas de sol (campo 'e' en el endpoint mensual = décimas de hora; 'sol' en algunas variantes)
    solar_mean = _aemet_climo_num(record, ["sol", "SOL", "insolacion", "INSOLACION"], ["sol", "insol"])
    if pd.isna(solar_mean):
        # Campo 'e' = horas de sol en décimas → convertir a horas
        e_val = _aemet_climo_num(record, ["e", "E"], [])
        if not pd.isna(e_val) and e_val >= 0:
            solar_mean = e_val / 10.0

    # Noches tropicales (nt_30): número de noches del mes con mínima ≥ 20 °C
    tropical_nights = _aemet_climo_num(record, ["nt_30", "NT_30", "noches_tropicales"], ["nt_30"])
    # Noches de helada (nt_00): número de noches del mes con mínima ≤ 0 °C
    frost_nights = _aemet_climo_num(record, ["nt_00", "NT_00", "noches_helada", "noches_frost"], ["nt_00"])

    # ta_max/ta_min: valor numérico + fecha dentro del paréntesis
    raw_ta_max = _aemet_first_by_patterns(record, ["ta_max", "TA_MAX", "tmax_abs", "TMAX_ABS"], ["ta_max", "tmax_abs"])
    raw_ta_min = _aemet_first_by_patterns(record, ["ta_min", "TA_MIN", "tmin_abs", "TMIN_ABS"], ["ta_min", "tmin_abs"])
    raw_gust = _aemet_first_by_patterns(record, ["w_racha", "W_RACHA", "racha", "RACHA"], ["w_racha", "racha"])
    raw_p_max = _aemet_first_by_patterns(record, ["p_max", "P_MAX"], ["p_max"])

    temp_abs_max = _parse_num(raw_ta_max)
    temp_abs_min = _parse_num(raw_ta_min)

    if pd.isna(temp_mean) and not pd.isna(temp_max) and not pd.isna(temp_min):
        temp_mean = (float(temp_max) + float(temp_min)) / 2.0
    if not pd.isna(wind_mean):
        wind_mean = float(wind_mean) * 3.6
    if not pd.isna(gust_max):
        gust_max = float(gust_max) * 3.6
    if not pd.isna(precip_total):
        precip_total = max(0.0, float(precip_total))
    # NO fallback: si AEMET no proporciona ta_max/ta_min el valor queda como NaN
    # y se mostrará "—" en la tabla. Mejor no mostrar nada que mostrar un valor
    # incorrecto (p.ej. la media de máximas en lugar de la máxima absoluta real).

    # Extraer fechas desde los paréntesis: "37.4(27)" → 27 del mes, "37.4(27/ago)" → 27/ago
    ta_max_date = _extract_paren_date(raw_ta_max, period_str)
    ta_min_date = _extract_paren_date(raw_ta_min, period_str)
    gust_date = _extract_paren_date(raw_gust, period_str)
    p_max_date = _extract_paren_date(raw_p_max, period_str)

    return {
        "temp_mean": float(temp_mean) if not pd.isna(temp_mean) else float("nan"),
        "temp_max": float(temp_max) if not pd.isna(temp_max) else float("nan"),
        "temp_min": float(temp_min) if not pd.isna(temp_min) else float("nan"),
        "wind_mean": float(wind_mean) if not pd.isna(wind_mean) else float("nan"),
        "gust_max": float(gust_max) if not pd.isna(gust_max) else float("nan"),
        "precip_total": float(precip_total) if not pd.isna(precip_total) else float("nan"),
        "solar_mean": float(solar_mean) if not pd.isna(solar_mean) else float("nan"),
        "precip_max_24h": float(precip_max_24h) if not pd.isna(precip_max_24h) else float("nan"),
        "rain_days": float(rain_days) if not pd.isna(rain_days) else float("nan"),
        "temp_abs_max": float(temp_abs_max) if not pd.isna(temp_abs_max) else float("nan"),
        "temp_abs_min": float(temp_abs_min) if not pd.isna(temp_abs_min) else float("nan"),
        "temp_abs_max_date": ta_max_date,
        "temp_abs_min_date": ta_min_date,
        "gust_abs_max_date": gust_date,
        "precip_max_24h_date": p_max_date,
        "tropical_nights": float(tropical_nights) if not pd.isna(tropical_nights) else float("nan"),
        "frost_nights": float(frost_nights) if not pd.isna(frost_nights) else float("nan"),
    }


def fetch_aemet_climo_monthly_for_year(
    idema: str,
    year: int,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    yy = int(year)
    payload = fetch_aemet_climo_monthlyannual_raw(idema, yy, yy, api_key=api_key)
    rows: List[Dict[str, Any]] = []
    annual_record: Optional[Dict[str, Any]] = None  # resumen anual AEMET (periodo YYYY-13)

    for record in payload:
        if not isinstance(record, dict):
            continue
        raw_date = _aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"])
        month_key = _parse_month_key(raw_date)
        if not month_key:
            # _parse_month_key devuelve None para mes 13 (resumen anual) → capturarlo
            year_key = _parse_year_key(raw_date)
            if year_key == yy:
                annual_record = record
            continue
        if not month_key.startswith(f"{yy:04d}-"):
            continue

        day_txt = f"{month_key}-01"
        try:
            epoch = float(pd.Timestamp(day_txt).replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            epoch = float("nan")
        metrics = _aemet_monthlyannual_to_metrics(record)
        rows.append({"date": day_txt, "epoch": epoch, **metrics})

    if not rows:
        return _empty_climo_dataframe(include_extras=True)

    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total",
        "solar_mean", "precip_max_24h", "rain_days", "temp_abs_max", "temp_abs_min",
        "tropical_nights", "frost_nights",
    ]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    frame["tropical_nights"] = frame["tropical_nights"].clip(lower=0)
    frame["frost_nights"] = frame["frost_nights"].clip(lower=0)

    # Usar el resumen anual de AEMET (ta_max/ta_min del año completo) para corregir
    # los extremos absolutos cuando los registros mensuales no tienen ta_max/ta_min
    # o cuando el valor anual es más extremo que el máximo de los mensuales.
    if annual_record is not None and not frame.empty:
        ann = _aemet_monthlyannual_to_metrics(annual_record)
        ann_abs_max = ann.get("temp_abs_max", float("nan"))
        ann_abs_min = ann.get("temp_abs_min", float("nan"))
        ann_abs_max_date = ann.get("temp_abs_max_date")
        ann_abs_min_date = ann.get("temp_abs_min_date")
        ann_gust = ann.get("gust_max", float("nan"))
        ann_gust_date = ann.get("gust_abs_max_date")

        abs_max_s = pd.to_numeric(frame["temp_abs_max"], errors="coerce")
        if not pd.isna(ann_abs_max) and (
            abs_max_s.isna().all() or float(ann_abs_max) >= float(abs_max_s.max(skipna=True))
        ):
            proxy = abs_max_s if abs_max_s.notna().any() else pd.to_numeric(frame["temp_max"], errors="coerce")
            idx = proxy.idxmax() if proxy.notna().any() else frame.index[0]
            frame.loc[idx, "temp_abs_max"] = float(ann_abs_max)
            if ann_abs_max_date:
                frame.loc[idx, "temp_abs_max_date"] = ann_abs_max_date

        abs_min_s = pd.to_numeric(frame["temp_abs_min"], errors="coerce")
        if not pd.isna(ann_abs_min) and (
            abs_min_s.isna().all() or float(ann_abs_min) <= float(abs_min_s.min(skipna=True))
        ):
            proxy = abs_min_s if abs_min_s.notna().any() else pd.to_numeric(frame["temp_min"], errors="coerce")
            idx = proxy.idxmin() if proxy.notna().any() else frame.index[0]
            frame.loc[idx, "temp_abs_min"] = float(ann_abs_min)
            if ann_abs_min_date:
                frame.loc[idx, "temp_abs_min_date"] = ann_abs_min_date

        gust_s = pd.to_numeric(frame["gust_max"], errors="coerce")
        if not pd.isna(ann_gust) and (
            gust_s.isna().all() or float(ann_gust) >= float(gust_s.max(skipna=True))
        ):
            idx = gust_s.idxmax() if gust_s.notna().any() else frame.index[0]
            frame.loc[idx, "gust_max"] = float(ann_gust)
            if ann_gust_date:
                frame.loc[idx, "gust_abs_max_date"] = ann_gust_date

    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA]


def fetch_aemet_climo_yearly_for_years(
    idema: str,
    years: List[int],
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    valid_years = sorted({int(y) for y in years})
    if not valid_years:
        return _empty_climo_dataframe(include_extras=True)

    # AEMET limita a 36 meses (~3 años) por petición: dividimos en bloques.
    monthly_metrics: Dict[Tuple[int, int], Dict[str, Any]] = {}
    annual_metrics: Dict[int, Dict[str, Any]] = {}
    for chunk_start in range(min(valid_years), max(valid_years) + 1, 3):
        chunk_end = min(chunk_start + 2, max(valid_years))
        payload = fetch_aemet_climo_monthlyannual_raw(
            idema=idema,
            year_start=chunk_start,
            year_end=chunk_end,
            api_key=api_key,
        )
        for record in payload:
            if not isinstance(record, dict):
                continue
            raw_date = _aemet_first_non_empty(record, ["fecha", "Fecha", "periodo", "PERIODO"])
            month_key = _parse_month_key(raw_date)
            if month_key:
                yy = int(month_key[:4])
                mm = int(month_key[5:7])
                monthly_metrics[(yy, mm)] = _aemet_monthlyannual_to_metrics(record)
                continue
            year_key = _parse_year_key(raw_date)
            if year_key is not None:
                annual_metrics[int(year_key)] = _aemet_monthlyannual_to_metrics(record)

    rows: List[Dict[str, Any]] = []
    for yy in valid_years:
        day_txt = f"{yy:04d}-01-01"
        try:
            epoch = float(pd.Timestamp(day_txt).replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            epoch = float("nan")

        metrics = annual_metrics.get(int(yy))
        if metrics is None:
            month_rows = [monthly_metrics[(yy, mm)] for mm in range(1, 13) if (yy, mm) in monthly_metrics]
            if month_rows:
                month_df = pd.DataFrame(month_rows)
                metrics = {
                    "temp_mean": float(pd.to_numeric(month_df["temp_mean"], errors="coerce").mean()),
                    "temp_max": float(pd.to_numeric(month_df["temp_max"], errors="coerce").mean()),
                    "temp_min": float(pd.to_numeric(month_df["temp_min"], errors="coerce").mean()),
                    "wind_mean": float(pd.to_numeric(month_df["wind_mean"], errors="coerce").mean()),
                    "gust_max": float(pd.to_numeric(month_df["gust_max"], errors="coerce").max()),
                    "precip_total": float(pd.to_numeric(month_df["precip_total"], errors="coerce").sum(min_count=1)),
                    "solar_mean": float(pd.to_numeric(month_df["solar_mean"], errors="coerce").mean()),
                    "precip_max_24h": float(pd.to_numeric(month_df["precip_max_24h"], errors="coerce").max()),
                    "rain_days": float(pd.to_numeric(month_df["rain_days"], errors="coerce").sum(min_count=1)),
                    "temp_abs_max": float(pd.to_numeric(month_df["temp_abs_max"], errors="coerce").max()),
                    "temp_abs_min": float(pd.to_numeric(month_df["temp_abs_min"], errors="coerce").min()),
                    "temp_abs_max_date": None,
                    "temp_abs_min_date": None,
                    "gust_abs_max_date": None,
                    "precip_max_24h_date": None,
                    # Sumar noches tropicales/helada de los 12 meses para obtener el total anual
                    "tropical_nights": float(pd.to_numeric(month_df.get("tropical_nights", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)),
                    "frost_nights": float(pd.to_numeric(month_df.get("frost_nights", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)),
                }
            else:
                metrics = {}

        # El registro anual AEMET (YYYY-13) suele tener solo tm_max/tm_min
        # (medias anuales) pero NO ta_max/ta_min (absolutos reales).
        # Los registros mensuales SÍ los tienen → cruzar y corregir.
        if metrics:
            avail_months = [monthly_metrics[(yy, mm)] for mm in range(1, 13) if (yy, mm) in monthly_metrics]
            if avail_months:
                mdf = pd.DataFrame(avail_months)

                # temp_abs_max: mejor valor entre registro anual y máx. de mensuales
                m_abs_max_s = pd.to_numeric(mdf["temp_abs_max"], errors="coerce")
                if m_abs_max_s.notna().any():
                    idx_best = int(m_abs_max_s.idxmax())
                    m_best_max = float(m_abs_max_s.iloc[idx_best])
                    cur_max = metrics.get("temp_abs_max", float("nan"))
                    if pd.isna(cur_max) or m_best_max > float(cur_max):
                        metrics["temp_abs_max"] = m_best_max
                        m_date = avail_months[idx_best].get("temp_abs_max_date")
                        if m_date:
                            metrics["temp_abs_max_date"] = m_date

                # temp_abs_min: mejor valor entre registro anual y mín. de mensuales
                m_abs_min_s = pd.to_numeric(mdf["temp_abs_min"], errors="coerce")
                if m_abs_min_s.notna().any():
                    idx_best = int(m_abs_min_s.idxmin())
                    m_best_min = float(m_abs_min_s.iloc[idx_best])
                    cur_min = metrics.get("temp_abs_min", float("nan"))
                    if pd.isna(cur_min) or m_best_min < float(cur_min):
                        metrics["temp_abs_min"] = m_best_min
                        m_date = avail_months[idx_best].get("temp_abs_min_date")
                        if m_date:
                            metrics["temp_abs_min_date"] = m_date

                # gust_max: mejor valor
                m_gust_s = pd.to_numeric(mdf["gust_max"], errors="coerce")
                if m_gust_s.notna().any():
                    idx_best = int(m_gust_s.idxmax())
                    m_best_gust = float(m_gust_s.iloc[idx_best])
                    cur_gust = metrics.get("gust_max", float("nan"))
                    if pd.isna(cur_gust) or m_best_gust > float(cur_gust):
                        metrics["gust_max"] = m_best_gust
                        m_date = avail_months[idx_best].get("gust_abs_max_date")
                        if m_date:
                            metrics["gust_abs_max_date"] = m_date

                # precip_max_24h: mejor valor
                m_prec24_s = pd.to_numeric(mdf["precip_max_24h"], errors="coerce")
                if m_prec24_s.notna().any():
                    idx_best = int(m_prec24_s.idxmax())
                    m_best_prec = float(m_prec24_s.iloc[idx_best])
                    cur_prec = metrics.get("precip_max_24h", float("nan"))
                    if pd.isna(cur_prec) or m_best_prec > float(cur_prec):
                        metrics["precip_max_24h"] = m_best_prec
                        m_date = avail_months[idx_best].get("precip_max_24h_date")
                        if m_date:
                            metrics["precip_max_24h_date"] = m_date

                # tropical_nights / frost_nights: sumar de mensuales si el anual no las tiene
                for night_col in ("tropical_nights", "frost_nights"):
                    if night_col in mdf.columns:
                        m_night_s = pd.to_numeric(mdf[night_col], errors="coerce")
                        if m_night_s.notna().any():
                            cur_val = metrics.get(night_col, float("nan"))
                            if pd.isna(cur_val):
                                metrics[night_col] = float(m_night_s.sum(min_count=1))

        rows.append({"date": day_txt, "epoch": epoch, **metrics})

    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).copy()
    for col in CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA:
        if col not in frame.columns:
            frame[col] = float("nan")
    numeric_cols = [
        "epoch", "temp_mean", "temp_max", "temp_min", "wind_mean", "gust_max", "precip_total",
        "solar_mean", "precip_max_24h", "rain_days", "temp_abs_max", "temp_abs_min",
        "tropical_nights", "frost_nights",
    ]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["precip_total"] = frame["precip_total"].clip(lower=0)
    frame["precip_max_24h"] = frame["precip_max_24h"].clip(lower=0)
    frame["rain_days"] = frame["rain_days"].clip(lower=0)
    frame["tropical_nights"] = frame["tropical_nights"].clip(lower=0)
    frame["frost_nights"] = frame["frost_nights"].clip(lower=0)
    return frame.sort_values("date").reset_index(drop=True)[CLIMO_DAILY_SCHEMA + CLIMO_EXTRA_SCHEMA]


def get_aemet_daily_charts() -> tuple:
    """
    Obtiene datos históricos del día para gráficos
    
    Returns:
        (
            epochs, temps, humidities, pressures,
            winds, gusts, wind_dirs, precip_totals
        ) o listas vacías si falla
    """
    if not is_aemet_connection():
        print("⚠️ [AEMET Charts] No hay conexión AEMET")
        return [], [], [], [], [], [], [], []
    
    idema = st.session_state.get("aemet_station_id")
    if not idema:
        print("⚠️ [AEMET Charts] No hay station ID")
        return [], [], [], [], [], [], [], []
    
    print(f"📊 [AEMET Charts] Obteniendo datos del día para estación {idema}")
    
    # Obtener serie temporal del día
    data_list = fetch_aemet_daily_timeseries(idema)
    if not data_list:
        print("❌ [AEMET Charts] fetch_aemet_daily_timeseries devolvió None o lista vacía")
        return [], [], [], [], [], [], [], []
    
    print(f"📦 [AEMET Charts] Recibidos {len(data_list)} registros")
    
    # Debug: mostrar campos del primer registro
    if len(data_list) > 0:
        campos = list(data_list[0].keys())
        print(f"🔑 [AEMET Charts] Campos disponibles en registro: {campos}")
        
        # Debug: mostrar valores de temperatura del primer registro
        primer = data_list[0]
        print(f"🌡️ [AEMET Charts] Valores temperatura primer registro:")
        print(f"   - TA: {primer.get('TA')}")
        print(f"   - ta: {primer.get('ta')}")
        print(f"   - TPRE: {primer.get('TPRE')}")
        print(f"   - TPR: {primer.get('TPR')}")
    
    epochs = []
    temps = []
    humidities = []
    pressures = []
    winds = []
    gusts = []
    wind_dirs = []
    precip_totals = []
    
    # Parsear cada registro
    
    errores = 0
    timestamps_ejemplo = []
    
    for i, record in enumerate(data_list):
        record_ci = {str(k).lower(): v for k, v in record.items()}

        def first_non_empty(keys):
            for key in keys:
                value = record.get(key)
                if value is None and isinstance(key, str):
                    value = record_ci.get(key.lower())
                if value is not None and value != "":
                    return value
            return None

        # Timestamp - endpoint diezminutal usa 'Fecha' (mayúscula)
        fint_str = first_non_empty(["Fecha", "fecha", "fint", "FINT", "fhora"])
        if not fint_str:
            fint_str = _extract_timestamp(record)
        if not fint_str:
            errores += 1
            if errores <= 3:
                print(f"⚠️ [AEMET Charts] Registro #{i}: sin campo de fecha")
            continue
        
        # Guardar primeros 3 timestamps para debug
        if len(timestamps_ejemplo) < 3:
            timestamps_ejemplo.append(fint_str)
            
        epoch = _parse_epoch_any(fint_str)
        if epoch is None:
            errores += 1
            if errores <= 3:  # Solo mostrar primeros 3 errores
                print(f"⚠️ [AEMET Charts] Error #{i}: timestamp no parseable '{fint_str}'")
            continue
        
        # Temperatura - diezminutal usa 'TA' (temperatura del aire)
        ta = first_non_empty(["TA", "ta", "TPRE", "T", "t", "TEMP", "temp"])
        if ta is not None:
            temp_val = _parse_num(ta)
            if temp_val == temp_val:
                temps.append(temp_val)
                epochs.append(epoch)
            else:
                temps.append(float("nan"))
                epochs.append(epoch)
        else:
            temps.append(float("nan"))
            epochs.append(epoch)
        
        # Humedad - diezminutal NO tiene humedad en todos los registros
        # Solo hay humedad en observaciones horarias (sincronizadas)
        hr = first_non_empty(["hr", "HR", "hrel", "HREL"])  # Puede no existir
        if hr is not None:
            rh_val = _parse_num(hr)
            if rh_val == rh_val:
                humidities.append(rh_val)
            else:
                humidities.append(float("nan"))
        else:
            humidities.append(float("nan"))
        
        # Presión - diezminutal usa 'PRES' (presión de estación)
        pres = first_non_empty(["PRES", "pres", "pres_nmar", "pnm", "PNM"])
        if pres is not None:
            pres_val = _parse_num(pres)
            if pres_val == pres_val:
                pressures.append(pres_val)
            else:
                pressures.append(float("nan"))
        else:
            pressures.append(float("nan"))

        # Viento medio (normalmente VV/FF en m/s), convertir a km/h.
        vv = _aemet_first_by_patterns(
            record,
            ["VV", "vv", "FF", "ff", "VVIENTO", "v_viento", "viento", "vel_viento", "velocidad_viento", "wind"],
            ["vv", "ff", "viento", "vel_viento", "velocidad_viento", "wind"],
        )
        vv_val = _parse_num(vv)
        winds.append(vv_val * 3.6 if vv_val == vv_val else float("nan"))

        # Racha máxima (normalmente VMAX/FX en m/s), convertir a km/h.
        vmax = _aemet_first_by_patterns(
            record,
            ["VMAX", "vmax", "FX", "fx", "RACHA", "racha", "racha_max", "v_racha", "vmax10m", "windgust"],
            ["vmax", "racha", "fx", "gust"],
        )
        vmax_val = _parse_num(vmax)
        gusts.append(vmax_val * 3.6 if vmax_val == vmax_val else float("nan"))

        # Dirección del viento (numérica o cardinal ES/EN).
        dv = _aemet_first_by_patterns(
            record,
            ["DV", "dv", "DD", "dd", "dir_viento", "direccion_viento", "DIR", "dir", "winddir"],
            ["dv", "dd", "dir", "direccion", "winddir"],
        )
        dv_val = _parse_wind_dir_deg(dv)
        wind_dirs.append(dv_val if dv_val == dv_val else float("nan"))

        # Precipitación acumulada/total reportada por el registro (mm)
        prec = first_non_empty(["prec", "PREC", "PR", "pr", "lluvia"])
        if prec is not None:
            prec_val = _parse_num(prec)
            if prec_val == prec_val:
                precip_totals.append(prec_val)
            else:
                precip_totals.append(float("nan"))
        else:
            precip_totals.append(float("nan"))
    
    print(f"✅ [AEMET Charts] Procesados: {len(epochs)} puntos, {errores} errores")
    if errores > 0 and len(timestamps_ejemplo) > 0:
        print(f"📋 [AEMET Charts] Ejemplos de timestamps recibidos:")
        for ts in timestamps_ejemplo:
            print(f"   - '{ts}'")
    if len(epochs) == 0:
        return [], [], [], [], [], [], [], []

    # Ordenar cronológicamente todas las series y recortar a ventana reciente
    rows = sorted(
        zip(epochs, temps, humidities, pressures, winds, gusts, wind_dirs, precip_totals),
        key=lambda r: r[0]
    )

    # Mantener datos de las últimas 72h para tolerar desfases en algunos endpoints.
    now_epoch = int(time.time())
    min_epoch = now_epoch - (72 * 3600)
    rows = [r for r in rows if min_epoch <= r[0] <= now_epoch + 3600]

    if len(rows) == 0:
        return [], [], [], [], [], [], [], []

    (
        epochs,
        temps,
        humidities,
        pressures,
        winds,
        gusts,
        wind_dirs,
        precip_totals,
    ) = map(list, zip(*rows))

    print(f"📈 [AEMET Charts] Rango ordenado: {datetime.fromtimestamp(epochs[0])} → {datetime.fromtimestamp(epochs[-1])}")
    return epochs, temps, humidities, pressures, winds, gusts, wind_dirs, precip_totals
