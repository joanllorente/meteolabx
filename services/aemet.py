"""
Servicio para interactuar con AEMET OpenData API
"""
import requests
import streamlit as st
import time
from typing import Dict, Optional, List
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
        "wind_dir_deg": _parse_num(field("dv", "DV", "dd", "DD", "dir", "DIR")),
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
    Obtiene serie temporal del día actual (observaciones cada 10 minutos)
    
    Args:
        idema: ID de la estación
        
    Returns:
        Lista de observaciones del día o None si falla
    """
    def fetch_from_endpoint(endpoint: str, label: str) -> Optional[List[Dict]]:
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

    def series_stats(data: Optional[List[Dict]]) -> Dict[str, int]:
        """
        Métricas de calidad para elegir mejor fuente:
        - ts_valid: timestamps parseables
        - temp_valid: temperatura parseable
        - latest_epoch: timestamp más reciente
        """
        stats = {"ts_valid": 0, "temp_valid": 0, "latest_epoch": 0}
        if not data:
            return stats

        for row in data:
            if not isinstance(row, dict):
                continue
            row_ci = {str(k).lower(): v for k, v in row.items()}
            ts = _extract_timestamp(row)
            ep = _parse_epoch_any(ts)
            if ep is None:
                continue

            stats["ts_valid"] += 1
            if ep > stats["latest_epoch"]:
                stats["latest_epoch"] = ep

            ta = (
                row.get("TA")
                or row.get("ta")
                or row.get("TPRE")
                or row.get("T")
                or row.get("t")
                or row.get("TEMP")
                or row.get("temp")
                or row_ci.get("ta")
                or row_ci.get("tpre")
                or row_ci.get("temp")
            )
            ta_val = _parse_num(ta)
            if ta_val == ta_val:
                stats["temp_valid"] += 1

        return stats

    try:
        print(f"🔄 [AEMET API] Solicitando serie temporal para {idema}...")

        candidates: List[tuple] = []

        # Intento 1: endpoint por estación (suele devolver la serie más reciente)
        endpoint_station = f"{BASE_URL}/observacion/convencional/diezminutal/datos/estacion/{idema}"
        data_station = fetch_from_endpoint(endpoint_station, "station")
        stats_station = series_stats(data_station)
        print(
            "ℹ️ [AEMET API] Calidad station: "
            f"ts={stats_station['ts_valid']}, temp={stats_station['temp_valid']}, "
            f"latest={stats_station['latest_epoch']}"
        )
        if data_station:
            candidates.append(("station", data_station, stats_station))

        # Fallback 1: endpoint por fecha para hoy y ayer (mejora cobertura en estaciones problemáticas)
        print("⚠️ [AEMET API] Fallback por fecha: intentando hoy y ayer")
        now_utc = datetime.now(timezone.utc)
        days = [now_utc.date(), (now_utc - timedelta(days=1)).date()]
        collected: List[Dict] = []

        for day in days:
            # Formato habitual en AEMET OpenData para {fecha}
            fecha = f"{day.isoformat()}T00:00:00UTC"
            fecha_encoded = quote(fecha, safe="")
            endpoint_date = (
                f"{BASE_URL}/observacion/convencional/diezminutal/"
                f"datos/fecha/{fecha_encoded}/estacion/{idema}"
            )
            data_by_date = fetch_from_endpoint(endpoint_date, f"date:{day.isoformat()}")
            if data_by_date:
                collected.extend(data_by_date)

        # Deduplicar por timestamp+idema para evitar solapes entre días
        dedup = {}
        for row in collected:
            if not isinstance(row, dict):
                continue
            ts = _extract_timestamp(row)
            rid = row.get("idema", idema)
            key = f"{rid}|{ts}"
            dedup[key] = row
        merged = list(dedup.values())
        print(f"✅ [AEMET API] Fallback por fecha devolvió {len(merged)} registros únicos")
        stats_merged = series_stats(merged)
        print(
            "ℹ️ [AEMET API] Calidad fallback fecha: "
            f"ts={stats_merged['ts_valid']}, temp={stats_merged['temp_valid']}, "
            f"latest={stats_merged['latest_epoch']}"
        )
        if merged:
            candidates.append(("date", merged, stats_merged))

        # Fallback 2: últimas 24h de todas las estaciones y filtrar por idema
        print("⚠️ [AEMET API] Fallback final: endpoint /observacion/convencional/todas")
        endpoint_all = f"{BASE_URL}/observacion/convencional/todas"
        data_all = fetch_from_endpoint(endpoint_all, "all24h")
        if not data_all:
            return None

        filtered = []
        target_id = str(idema).strip().upper()
        for row in data_all:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("idema", "")).strip().upper()
            if row_id == target_id:
                filtered.append(row)

        print(f"✅ [AEMET API] Fallback all24h devolvió {len(filtered)} registros para {idema}")
        stats_all = series_stats(filtered)
        print(
            "ℹ️ [AEMET API] Calidad fallback all24h: "
            f"ts={stats_all['ts_valid']}, temp={stats_all['temp_valid']}, "
            f"latest={stats_all['latest_epoch']}"
        )
        if filtered:
            candidates.append(("all24h", filtered, stats_all))

        if candidates:
            # Elegir mejor fuente priorizando primero que haya temperatura válida.
            temp_candidates = [c for c in candidates if c[2]["temp_valid"] > 0]
            pool = temp_candidates if temp_candidates else candidates

            # Dentro del pool: frescura real y luego calidad.
            priority = {"station": 3, "date": 2, "all24h": 1}
            source, data_best, best_stats = max(
                pool,
                key=lambda t: (
                    t[2]["latest_epoch"],
                    t[2]["temp_valid"],
                    t[2]["ts_valid"],
                    priority.get(t[0], 0),
                    len(t[1]),
                ),
            )
            print(
                f"✅ [AEMET API] Fuente elegida: {source} "
                f"(latest={best_stats['latest_epoch']}, ts={best_stats['ts_valid']}, "
                f"temp={best_stats['temp_valid']}, registros={len(data_best)})"
            )
            return data_best

        return None

    except Exception as e:
        print(f"❌ [AEMET API] Error obteniendo serie temporal: {e}")
        import traceback
        traceback.print_exc()
        return None


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

        # Viento medio - diezminutal usa VV (m/s), convertir a km/h
        vv = first_non_empty([
            "VV", "vv",
            "FF", "ff",
            "VVIENTO", "v_viento", "viento", "vel_viento", "velocidad_viento"
        ])
        if vv is not None:
            vv_val = _parse_num(vv)
            if vv_val == vv_val:
                winds.append(vv_val * 3.6)
            else:
                winds.append(float("nan"))
        else:
            winds.append(float("nan"))

        # Racha máxima - diezminutal usa VMAX (m/s), convertir a km/h
        vmax = first_non_empty([
            "VMAX", "vmax",
            "FX", "fx",
            "RACHA", "racha", "racha_max", "v_racha", "vmax10m"
        ])
        if vmax is not None:
            vmax_val = _parse_num(vmax)
            if vmax_val == vmax_val:
                gusts.append(vmax_val * 3.6)
            else:
                gusts.append(float("nan"))
        else:
            gusts.append(float("nan"))

        # Dirección del viento en grados
        dv = first_non_empty(["DV", "dv", "DD", "dd", "dir_viento", "direccion_viento"])
        if dv is not None:
            dv_val = _parse_num(dv)
            if dv_val == dv_val:
                wind_dirs.append(dv_val)
            else:
                wind_dirs.append(float("nan"))
        else:
            wind_dirs.append(float("nan"))

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
