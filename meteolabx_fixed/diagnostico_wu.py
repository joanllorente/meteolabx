"""
Script de diagnóstico para Weather Underground
Muestra TODOS los campos que devuelve la API para tu estación
"""
import requests
import json
import sys

def diagnostico_wu(station_id, api_key):
    """
    Muestra todos los campos disponibles de Weather Underground
    """
    
    # Endpoint actual (observations/current)
    url_current = "https://api.weather.com/v2/pws/observations/current"
    
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal"
    }
    
    print("=" * 80)
    print("DIAGNÓSTICO DE WEATHER UNDERGROUND")
    print("=" * 80)
    print(f"\nEstación: {station_id}")
    print(f"Endpoint: {url_current}\n")
    
    try:
        r = requests.get(url_current, params=params, timeout=15)
        
        if r.status_code != 200:
            print(f"❌ Error HTTP {r.status_code}")
            print(r.text)
            return
        
        data = r.json()
        
        # Guardar respuesta completa en archivo
        with open('wu_response_complete.json', 'w') as f:
            json.dump(data, f, indent=2)
        print("✅ Respuesta completa guardada en: wu_response_complete.json\n")
        
        # Analizar estructura
        print("=" * 80)
        print("ESTRUCTURA DE LA RESPUESTA:")
        print("=" * 80)
        
        obs = data.get("observations", [{}])[0]
        metric = obs.get("metric", {})
        
        print("\n📊 CAMPOS EN observations[0]:")
        print("-" * 80)
        for key in sorted(obs.keys()):
            if key != "metric":
                value = obs[key]
                print(f"  {key:30s} = {value}")
        
        print("\n📊 CAMPOS EN observations[0].metric:")
        print("-" * 80)
        for key in sorted(metric.keys()):
            value = metric[key]
            print(f"  {key:30s} = {value}")
        
        # Buscar extremos
        print("\n" + "=" * 80)
        print("BÚSQUEDA DE EXTREMOS DIARIOS:")
        print("=" * 80)
        
        # Posibles nombres para temperatura
        temp_fields = [
            "tempHigh", "tempMax", "tempDaily", "dailyHighTemp",
            "tempLow", "tempMin", "dailyLowTemp",
        ]
        
        # Posibles nombres para humedad
        rh_fields = [
            "humidityHigh", "humidityMax", "humidityDaily",
            "humidityLow", "humidityMin"
        ]
        
        # Posibles nombres para viento
        wind_fields = [
            "windGustHigh", "windGustMax", "windGustDaily",
            "maxWindSpeed", "dailyMaxGust"
        ]
        
        print("\n🌡️  Temperatura:")
        found_temp = False
        for field in temp_fields:
            if field in obs:
                print(f"  ✅ obs['{field}'] = {obs[field]}")
                found_temp = True
            if field in metric:
                print(f"  ✅ metric['{field}'] = {metric[field]}")
                found_temp = True
        if not found_temp:
            print("  ❌ No se encontraron extremos de temperatura")
        
        print("\n💧 Humedad:")
        found_rh = False
        for field in rh_fields:
            if field in obs:
                print(f"  ✅ obs['{field}'] = {obs[field]}")
                found_rh = True
            if field in metric:
                print(f"  ✅ metric['{field}'] = {metric[field]}")
                found_rh = True
        if not found_rh:
            print("  ❌ No se encontraron extremos de humedad")
        
        print("\n💨 Viento:")
        found_wind = False
        for field in wind_fields:
            if field in obs:
                print(f"  ✅ obs['{field}'] = {obs[field]}")
                found_wind = True
            if field in metric:
                print(f"  ✅ metric['{field}'] = {metric[field]}")
                found_wind = True
        if not found_wind:
            print("  ❌ No se encontraron extremos de viento")
        
        # Sugerencia
        print("\n" + "=" * 80)
        print("💡 PRÓXIMOS PASOS:")
        print("=" * 80)
        
        if not (found_temp and found_rh and found_wind):
            print("""
Este endpoint (/observations/current) NO proporciona extremos diarios.

PWS Monitor probablemente usa uno de estos otros endpoints:

1. /observations/all/1day
   → Resumen del día con máximos/mínimos
   
2. /observations/hourly/1day  
   → Datos por hora (calcular extremos)

3. Endpoint privado de WU Dashboard
   → Solo accesible desde la app oficial

RECOMENDACIÓN:
Probar endpoint /observations/all/1day añadiendo al final de la URL:
  ?stationId=TU_ESTACION&format=json&units=m&apiKey=TU_KEY&numericPrecision=decimal

O calcular extremos localmente guardando histórico en session_state.
""")
        else:
            print("""
✅ Este endpoint SÍ proporciona extremos diarios!

Los campos encontrados arriba deberían funcionar.
Verifica que el código esté buscando exactamente esos nombres.
""")
        
        print("\n" + "=" * 80)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("  SCRIPT DE DIAGNÓSTICO - WEATHER UNDERGROUND")
    print("=" * 80 + "\n")
    
    # Pedir credenciales
    station_id = input("Station ID: ").strip()
    api_key = input("API Key: ").strip()
    
    if not station_id or not api_key:
        print("\n❌ Debes proporcionar Station ID y API Key")
        sys.exit(1)
    
    diagnostico_wu(station_id, api_key)
    
    print("\n✅ Diagnóstico completado")
    print("📄 Revisa el archivo: wu_response_complete.json")
    print()
