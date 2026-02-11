"""
Utilidades para trabajar con estaciones AEMET
"""
import json
import math
from typing import List, Dict, Tuple


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcula distancia en km entre dos coordenadas usando fórmula de Haversine
    
    Args:
        lat1, lon1: Coordenadas del primer punto
        lat2, lon2: Coordenadas del segundo punto
        
    Returns:
        Distancia en kilómetros
    """
    R = 6371  # Radio de la Tierra en km
    
    # Convertir a radianes
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Diferencias
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    # Fórmula de Haversine
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    return R * c


def load_stations(filepath: str = 'estaciones_aemet_clean.json') -> List[Dict]:
    """
    Carga el inventario de estaciones desde JSON
    
    Args:
        filepath: Ruta al archivo JSON
        
    Returns:
        Lista de estaciones
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    return data['estaciones']


def find_nearest_station(lat: float, lon: float, stations: List[Dict], 
                         max_results: int = 5, max_distance_km: float = None) -> List[Tuple[Dict, float]]:
    """
    Encuentra las estaciones más cercanas a una ubicación
    
    Args:
        lat, lon: Coordenadas de búsqueda
        stations: Lista de estaciones
        max_results: Número máximo de resultados
        max_distance_km: Distancia máxima en km (None = sin límite)
        
    Returns:
        Lista de tuplas (estación, distancia_km) ordenadas por distancia
    """
    results = []
    
    for station in stations:
        s_lat = station.get('lat')
        s_lon = station.get('lon')
        
        if s_lat is None or s_lon is None:
            continue
        
        distance = haversine_distance(lat, lon, s_lat, s_lon)
        
        # Filtrar por distancia máxima si se especifica
        if max_distance_km is not None and distance > max_distance_km:
            continue
        
        results.append((station, distance))
    
    # Ordenar por distancia
    results.sort(key=lambda x: x[1])
    
    return results[:max_results]


def find_station_by_id(idema: str, stations: List[Dict]) -> Dict:
    """
    Busca una estación por su ID
    
    Args:
        idema: ID de la estación (ej: "0201X")
        stations: Lista de estaciones
        
    Returns:
        Diccionario con datos de la estación o None si no existe
    """
    for station in stations:
        if station['idema'] == idema:
            return station
    return None


def search_stations_by_name(query: str, stations: List[Dict], max_results: int = 10) -> List[Dict]:
    """
    Busca estaciones por nombre (búsqueda parcial, case-insensitive)
    
    Args:
        query: Texto a buscar
        stations: Lista de estaciones
        max_results: Número máximo de resultados
        
    Returns:
        Lista de estaciones que coinciden
    """
    query_lower = query.lower()
    results = []
    
    for station in stations:
        nombre = station.get('nombre', '').lower()
        if query_lower in nombre:
            results.append(station)
            
            if len(results) >= max_results:
                break
    
    return results


def filter_stations_by_province(province: str, stations: List[Dict]) -> List[Dict]:
    """
    Filtra estaciones por provincia
    
    Args:
        province: Nombre de la provincia
        stations: Lista de estaciones
        
    Returns:
        Lista de estaciones en esa provincia
    """
    province_lower = province.lower()
    return [
        s for s in stations 
        if s.get('provincia', '').lower() == province_lower
    ]


# Ejemplo de uso
if __name__ == "__main__":
    # Cargar estaciones
    stations = load_stations('estaciones_aemet_clean.json')
    print(f"✅ Cargadas {len(stations)} estaciones\n")
    
    # Ejemplo 1: Buscar por coordenadas (Barcelona)
    print("🔍 Ejemplo 1: Buscar cerca de Barcelona")
    nearest = find_nearest_station(41.3851, 2.1734, stations, max_results=3)
    for station, distance in nearest:
        print(f"  • {station['idema']} - {station['nombre']} ({distance:.1f} km)")
    
    # Ejemplo 2: Buscar por nombre
    print("\n🔍 Ejemplo 2: Buscar por nombre 'MADRID'")
    madrid_stations = search_stations_by_name("MADRID", stations, max_results=5)
    for s in madrid_stations:
        print(f"  • {s['idema']} - {s['nombre']}")
    
    # Ejemplo 3: Buscar por ID
    print("\n🔍 Ejemplo 3: Buscar por ID '0201X'")
    station = find_station_by_id("0201X", stations)
    if station:
        print(f"  • {station['nombre']} - {station['lat']}, {station['lon']}")