"""
Servicio para interactuar con AEMET OpenData API
"""
import requests
import streamlit as st
import time
from typing import Dict, Optional, List, Any
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import re

# API Key de AEMET
AEMET_API_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXRlb2xhYnhAZ21haWwuY29tIiwianRpIjoiNTdkMzE1MjYtMTk4My00YzNiLTgzNjAtYTdkZWJmMmIxMDFhIiwiaXNzIjoiQUVNRVQiLCJpYXQiOjE3NzAyNDQ1OTEsInVzZXJJZCI6IjU3ZDMxNTI2LTE5ODMtNGMzYi04MzYwLWE3ZGViZjJiMTAxYSIsInJvbGUiOiIifQ.GvliQHY3f94N691sU0ExhMHZxbTiGn2BCe-bIA22K8c"

BASE_URL = "https://opendata.aemet.es/opendata/api"


def _parse_num(value):
    """Parseo robusto de números AEMET (coma decimal, vacíos, 'Ip', etc.)."""
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
        s = s.replace(",", ".")
        if s.lower() in {"ip", "nan", "none", "--", "-"}:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


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


def _fetch_aemet_opendata_list(endpoint: str, label: str) -> Optional[List[Dict]]:
    """Patrón OpenData de 2 pasos: endpoint -> URL temporal -> lista JSON."""
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
        p_msl = _parse_num(_aemet_first_non_empty(row, ["pres_nmar", "PRES_NMAR", "pnm", "PNM", "pressure", "PRESSURE"]))
        p_station = _parse_num(_aemet_first_non_empty(row, ["pres", "PRES"]))
        p = p_msl if p_msl == p_msl else p_station

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
