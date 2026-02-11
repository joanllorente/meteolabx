#!/usr/bin/env python3
"""
Script para construir inventario de estaciones AEMET
Versión simple: usa solo /todas con timeout muy largo
"""
import requests
import json
import time
from typing import List, Dict

# API Key de AEMET
API_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXRlb2xhYnhAZ21haWwuY29tIiwianRpIjoiNTdkMzE1MjYtMTk4My00YzNiLTgzNjAtYTdkZWJmMmIxMDFhIiwiaXNzIjoiQUVNRVQiLCJpYXQiOjE3NzAyNDQ1OTEsInVzZXJJZCI6IjU3ZDMxNTI2LTE5ODMtNGMzYi04MzYwLWE3ZGViZjJiMTAxYSIsInJvbGUiOiIifQ.GvliQHY3f94N691sU0ExhMHZxbTiGn2BCe-bIA22K8c"

BASE_URL = "https://opendata.aemet.es/opendata/api"


def fetch_stations_slow():
    """
    Obtiene estaciones usando /todas con descarga incremental
    Usa read() en chunks para evitar timeout completo
    """
    print("=" * 60)
    print("🏗️  CONSTRUCCIÓN DE INVENTARIO AEMET")
    print("=" * 60)
    
    # Paso 1: Obtener URL de datos
    print("\n📡 Paso 1: Obteniendo URL de descarga...")
    url = f"{BASE_URL}/observacion/convencional/todas"
    headers = {"api_key": API_KEY}
    
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    result = response.json()
    
    if result["estado"] != 200:
        raise Exception(f"Error API: {result.get('descripcion')}")
    
    datos_url = result["datos"]
    print(f"✅ URL obtenida: {datos_url[:60]}...")
    
    # Paso 2: Descargar datos en chunks (evitar timeout)
    print("\n⏳ Paso 2: Descargando datos...")
    print("   (Esto puede tardar 2-5 minutos - el servidor es LENTO)")
    print("   Mostrando progreso cada 10 segundos...\n")
    
    start_time = time.time()
    
    # Usar stream=True para descargar en chunks
    response = requests.get(datos_url, stream=True, timeout=300)
    response.raise_for_status()
    
    # Leer chunks y mostrar progreso
    chunks = []
    bytes_downloaded = 0
    last_update = time.time()
    
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            chunks.append(chunk)
            bytes_downloaded += len(chunk)
            
            # Mostrar progreso cada 10 segundos
            if time.time() - last_update > 10:
                elapsed = time.time() - start_time
                kb = bytes_downloaded / 1024
                print(f"   📥 {kb:.1f} KB descargados ({elapsed:.0f}s)...", flush=True)
                last_update = time.time()
    
    # Combinar chunks y parsear JSON
    data = b''.join(chunks)
    elapsed = time.time() - start_time
    print(f"\n✅ Descarga completada: {len(data)/1024:.1f} KB en {elapsed:.0f}s")
    
    print("\n🔍 Paso 3: Parseando JSON...")
    # AEMET usa latin-1 (iso-8859-1) en lugar de utf-8
    try:
        stations_data = json.loads(data.decode('utf-8'))
    except UnicodeDecodeError:
        print("   ⚠️  UTF-8 falló, intentando con latin-1...")
        stations_data = json.loads(data.decode('latin-1'))
    
    return stations_data


def extract_station_info(raw_data: List[Dict]) -> List[Dict]:
    """Extrae información relevante de cada estación"""
    stations = []
    
    for item in raw_data:
        station = {
            "idema": item.get("idema"),
            "nombre": item.get("ubi", "").strip(),
            "provincia": item.get("prov", "").strip(),
            "lat": item.get("lat"),
            "lon": item.get("lon"),
            "alt": item.get("alt"),
        }
        
        # Validar campos mínimos
        if station["idema"] and station["lat"] and station["lon"]:
            stations.append(station)
    
    return stations


def save_inventory(stations: List[Dict], filename: str = "estaciones_aemet.json"):
    """Guarda el inventario en JSON"""
    stations_sorted = sorted(stations, key=lambda x: (x.get("provincia", ""), x.get("nombre", "")))
    
    output = {
        "version": "1.0",
        "fecha_generacion": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_estaciones": len(stations_sorted),
        "fuente": "AEMET OpenData",
        "estaciones": stations_sorted
    }
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Inventario guardado: {filename}")
    print(f"📊 Total estaciones: {len(stations_sorted)}")


def print_sample(stations: List[Dict]):
    """Muestra ejemplo de estaciones"""
    print(f"\n📋 Primeras 5 estaciones:")
    for s in stations[:5]:
        print(f"  • {s['idema']} - {s['nombre']} ({s['provincia']})")
        print(f"    {s['lat']}, {s['lon']} | {s['alt']}m")


def main():
    try:
        # Descargar datos (lento pero funcional)
        raw_data = fetch_stations_slow()
        
        # Procesar
        print("🔄 Procesando estaciones...")
        stations = extract_station_info(raw_data)
        
        # Mostrar muestra
        print_sample(stations)
        
        # Guardar
        save_inventory(stations)
        
        print("\n" + "=" * 60)
        print("✅ INVENTARIO COMPLETADO")
        print("=" * 60)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelado por el usuario")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()