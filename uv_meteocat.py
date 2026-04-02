import json
import requests
from datetime import datetime, timedelta, timezone

API_KEY = "rZwBPl5kv05CS7NEgk9wcaqd0FFimA2f9y6ISDa2"

# Archivo que ya me has pasado con metadatos de estaciones
STATIONS_FILE = "data_estaciones_meteocat.json"

# Variable UV en Meteocat
UV_VAR_CODE = 39

# Cuántos días hacia atrás mirar para detectar estaciones con UV
LOOKBACK_DAYS = 14

BASE_URL = "https://api.meteo.cat/xema/v1"


def load_stations(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        stations = json.load(f)

    station_map = {}
    for s in stations:
        station_map[s["codi"]] = {
            "nom": s.get("nom"),
            "municipi": (s.get("municipi") or {}).get("nom"),
            "comarca": (s.get("comarca") or {}).get("nom"),
            "provincia": (s.get("provincia") or {}).get("nom"),
            "latitud": (s.get("coordenades") or {}).get("latitud"),
            "longitud": (s.get("coordenades") or {}).get("longitud"),
            "altitud": s.get("altitud"),
            "tipus": s.get("tipus"),
        }
    return station_map


def fetch_uv_for_day(date_obj: datetime) -> list:
    yyyy = date_obj.strftime("%Y")
    mm = date_obj.strftime("%m")
    dd = date_obj.strftime("%d")

    url = f"{BASE_URL}/variables/mesurades/{UV_VAR_CODE}/{yyyy}/{mm}/{dd}"
    headers = {
        "X-Api-Key": API_KEY,
        "Accept": "application/json",
    }

    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_station_codes(payload) -> set:
    """
    Intenta adaptarse a varios formatos posibles del JSON.
    """
    codes = set()

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue

            # Caso típico: cada elemento ya lleva el código de estación
            for key in ("codiEstacio", "codi_estacio", "estacio", "codi"):
                if key in item and isinstance(item[key], str):
                    # Ojo: "codi" a veces podría ser el código de la variable.
                    # Si viene acompañado de 'lectures', suele no ser estación.
                    if key == "codi" and "lectures" in item:
                        continue
                    codes.add(item[key])

            # Si existe un bloque anidado de estación
            est = item.get("estacio")
            if isinstance(est, dict):
                if isinstance(est.get("codi"), str):
                    codes.add(est["codi"])

    return codes


def active_station(station: dict) -> bool:
    """
    Filtra estaciones activas según el campo 'estats'.
    En tus metadatos aparece codi=2 con dataFi=null para activas.
    """
    # Si no hay info, no bloqueamos
    return True


def main():
    station_map = load_stations(STATIONS_FILE)
    uv_station_codes = set()

    today = datetime.now(timezone.utc)

    print(f"Buscando estaciones con UV en los últimos {LOOKBACK_DAYS} días...\n")

    for i in range(LOOKBACK_DAYS):
        day = today - timedelta(days=i)
        try:
            payload = fetch_uv_for_day(day)
            codes = extract_station_codes(payload)
            if codes:
                print(f"{day.strftime('%Y-%m-%d')}: {len(codes)} estaciones con datos UV")
            uv_station_codes.update(codes)
        except requests.HTTPError as e:
            print(f"{day.strftime('%Y-%m-%d')}: error HTTP -> {e}")
        except Exception as e:
            print(f"{day.strftime('%Y-%m-%d')}: error -> {e}")

    print("\n==============================")
    print(f"Total estaciones detectadas con UV: {len(uv_station_codes)}")
    print("==============================\n")

    rows = []
    for code in sorted(uv_station_codes):
        meta = station_map.get(code, {})
        rows.append({
            "codi": code,
            "nom": meta.get("nom", "Desconeguda"),
            "municipi": meta.get("municipi"),
            "comarca": meta.get("comarca"),
            "provincia": meta.get("provincia"),
            "altitud": meta.get("altitud"),
            "latitud": meta.get("latitud"),
            "longitud": meta.get("longitud"),
            "tipus": meta.get("tipus"),
        })

    # Mostrar por pantalla
    for r in rows:
        print(
            f"{r['codi']:>3} | {r['nom']} | "
            f"{r['municipi'] or '-'} | {r['comarca'] or '-'} | {r['provincia'] or '-'}"
        )

    # Guardar JSON
    with open("estaciones_meteocat_uv.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print("\nGuardado en: estaciones_meteocat_uv.json")


if __name__ == "__main__":
    main()