#!/usr/bin/env python3
"""
Construye el inventario de estaciones de Polonia desde IMGW-PIB
(danepubliczne.imgw.pl, datos abiertos sin API key).

IMGW tiene dos mundos que no se solapan del todo:

  - **Tiempo real** (API): ``/api/data/meteo`` es la red telemétrica
    (~790 estaciones automáticas, dato cada 10 min: temperatura, humedad,
    viento, lluvia, temperatura del suelo) con coordenadas, altitud y año de
    fundación. ``/api/data/synop`` son las ~62 sinópticas (horario, con
    presión), identificadas por su indicativo OMM.
  - **Archivo** (``data/dane_pomiarowo_obserwacyjne``): datos diarios
    verificados de las estaciones sinópticas, climatológicas y
    pluviométricas —muchas con observador manual— desde 1951, publicados con
    uno o dos meses de retraso. ``wykaz_stacji.csv`` lista códigos y nombres,
    sin coordenadas.

Clasificación (con el significado que tienen en la app):
  - Automática online: está en la API y alguna variable tiene menos de 6 h.
  - Automática offline: está en la API pero lleva más de 6 h sin datos.
  - Manual (``manual: True``, online): solo está en el archivo y sigue
    publicando en él (últimos meses archivados). Es un observador que lee a
    mano; IMGW publica sus lecturas con uno o dos meses de retraso.
  - Solo histórica (``manual: False``, offline): solo está en el archivo y
    dejó de publicar. Ya no es la estación manual de nadie: es un archivo.
  - Histórico (``has_historical``): toda estación con archivo diario;
    ``archive_start``/``archive_end`` dan el primer y el último día (fin
    ``None`` si sigue en marcha).

El archivo se escanea entero, desde 1951 (antes de 2001 en carpetas de cinco
años). Las del archivo no salen en la
API, así que su ubicación viene de los mapas PDF de IMGW
(``data/imgw_pdf_coords.json``, generado por
``scripts/extract_imgw_pdf_coords.py``; grados y minutos, ~1-2 km) y, si no
están ahí, de geocodificar su nombre con Open-Meteo aceptando solo un
resultado dentro de la cuadrícula de 1° que codifica su código (dígitos 2-3
latitud, 4-5 longitud; se cumple en 786 de las 787 de la API). La procedencia
queda en ``coords_source`` (``imgw_pdf_map`` o ``geocoded_name``).

Uso:
  python3 scripts/build_imgw_inventory.py --cache-dir /tmp/imgw \\
      --output data/data_estaciones_imgw.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import IMGW_PDF_COORDS_PATH, IMGW_STATIONS_PATH

API_URL = "https://danepubliczne.imgw.pl/api/data"
ARCHIVE_URL = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
ARCHIVE_FIRST_YEAR = 2001
ONLINE_MAX_AGE = timedelta(hours=6)
# Un sensor que no reporta en 30 días se da por retirado.
SENSOR_MAX_AGE = timedelta(days=30)
# Una estación del archivo sigue «activa» si publica en los últimos meses
# archivados (el archivo climatológico va unos dos meses por detrás).
ARCHIVE_ACTIVE_MONTHS = 6
STATION_TZ = "Europe/Warsaw"

# Variable de /api/data/meteo → sensor del catálogo.
METEO_SENSORS = {
    "temperatura_powietrza": "thermometer",
    "wilgotnosc_wzgledna": "hygrometer",
    "wiatr_srednia_predkosc": "anemometer",
    "wiatr_predkosc_maksymalna": "anemometer",
    "wiatr_kierunek": "wind_vane",
    "opad_10min": "rain_gauge",
}
ARCHIVE_KIND_SENSORS = {
    "synop": ("thermometer", "hygrometer", "barometer", "anemometer", "wind_vane", "rain_gauge"),
    "klimat": ("thermometer", "rain_gauge"),
    "opad": ("rain_gauge",),
}
NETWORK_BY_KIND = {"synop": "SYNOP", "klimat": "KLIMAT", "opad": "OPAD"}


def _get(url: str, *, timeout: int = 120) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_json(url: str) -> Any:
    return json.loads(_get(url).decode("utf-8"))


def _cached(cache_dir: Path, relative: str) -> Optional[bytes]:
    """Descarga del archivo con caché en disco (los meses cerrados no cambian
    casi nunca; una ejecución repetida no vuelve a bajarlos)."""
    target = cache_dir / relative.replace("/", "__")
    if target.exists():
        return target.read_bytes()
    time.sleep(0.2)
    try:
        payload = _get(f"{ARCHIVE_URL}/{relative}")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    target.write_bytes(payload)
    return payload


def _listing(relative: str) -> List[str]:
    html = _get(f"{ARCHIVE_URL}/{relative}").decode("utf-8", "replace")
    return re.findall(r'href="([^"?/][^"]*)"', html)


def _empty_sensors() -> Dict[str, bool]:
    return {
        "thermometer": False, "hygrometer": False, "barometer": False,
        "anemometer": False, "wind_vane": False, "rain_gauge": False,
        "pyranometer": False, "uv": False,
    }


def _parse_time(text: Any) -> Optional[datetime]:
    """Las marcas de /api/data/meteo van en UTC (comprobado contra el reloj)."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _csv_rows(payload: bytes) -> Iterable[List[str]]:
    from server.services.imgw_climo import read_zip_member

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            # Los ficheros «_t» son medias de términos; basta con el diario.
            if "_d_t_" in info.filename or not info.filename.endswith(".csv"):
                continue
            text = read_zip_member(archive, info).decode("cp1250", "replace")
            yield from csv.reader(io.StringIO(text))


def scan_archive(cache_dir: Path, *, today: date) -> Dict[str, Dict[str, Any]]:
    """code → {name, kinds{kind: (primer día, último día)}} de todo el archivo."""
    stations: Dict[str, Dict[str, Any]] = {}

    def _ingest(kind: str, payload: Optional[bytes]) -> None:
        if not payload:
            return
        for row in _csv_rows(payload):
            if len(row) < 5 or not row[0].strip().isdigit():
                continue
            try:
                day = date(int(row[2]), int(row[3]), int(row[4]))
            except ValueError:
                continue
            code = row[0].strip()
            record = stations.setdefault(code, {"name": row[1].strip(), "kinds": {}})
            first, last = record["kinds"].get(kind, (day, day))
            record["kinds"][kind] = (min(first, day), max(last, day))
            if day >= last:
                record["name"] = row[1].strip() or record["name"]

    from concurrent.futures import ThreadPoolExecutor

    # Antes de 2001: carpetas de cinco años. klimat y opad, un ZIP por año con
    # todas las estaciones; synop, un ZIP por estación con los cinco años.
    batches: List[Tuple[str, List[Tuple[str, str]]]] = []
    for kind in ("klimat", "opad", "synop"):
        folders = sorted({
            name.rstrip("/") for name in _listing(f"dobowe/{kind}/")
            if re.fullmatch(r"\d{4}_\d{4}/", name) and int(name[5:9]) < ARCHIVE_FIRST_YEAR
        })
        for folder in folders:
            names = [name for name in _listing(f"dobowe/{kind}/{folder}/") if name.endswith(".zip")]
            batches.append((f"{kind} {folder}", [(kind, f"dobowe/{kind}/{folder}/{name}") for name in names]))
    for year in range(ARCHIVE_FIRST_YEAR, today.year + 1):
        files = [
            (kind, f"dobowe/{kind}/{year}/{year}_{month:02d}_{suffix}.zip")
            for kind, suffix in (("klimat", "k"), ("opad", "o"))
            for month in range(1, 13)
            if date(year, month, 1) <= today
        ]
        # Sinópticas: un fichero por estación y año, salvo el año en curso, que
        # va por meses con todas juntas.
        try:
            names = [name for name in _listing(f"dobowe/synop/{year}/") if name.endswith(".zip")]
        except HTTPError:
            names = []
        files += [("synop", f"dobowe/synop/{year}/{name}") for name in names]
        batches.append((str(year), files))

    for label, files in batches:
        print(f"  archivo {label}…", flush=True)
        # El servidor tarda unos segundos por fichero: cuatro a la vez.
        with ThreadPoolExecutor(max_workers=4) as pool:
            payloads = list(pool.map(lambda item: _cached(cache_dir, item[1]), files))
        for (kind, relative), payload in zip(files, payloads):
            try:
                _ingest(kind, payload)
            except zipfile.BadZipFile:
                # Una descarga cortada deja un ZIP roto en caché: se repite una
                # vez y, si el del servidor también lo está, se salta.
                (cache_dir / relative.replace("/", "__")).unlink(missing_ok=True)
                try:
                    _ingest(kind, _cached(cache_dir, relative))
                except zipfile.BadZipFile:
                    print(f"  aviso: ZIP corrupto en el servidor, se omite {relative}")
    return stations


_ROMAN = re.compile(r"^[IVX]+$")


def display_name(name: str) -> str:
    """«WARSZAWA-OBSERWATORIUM II» → «Warszawa-Obserwatorium II». Los romanos y
    las siglas cortas (UJ, MŁK) se quedan en mayúsculas; ``str.title()`` los
    dejaba en «Ii» y «Uj»."""
    def _word(token: str) -> str:
        acronym = token.isalpha() and token.isupper() and (
            len(token) <= 2 or (len(token) == 3 and not re.search(r"[AEIOUYĄĘÓ]", token))
        )
        if _ROMAN.match(token) or acronym:
            return token
        return token[:1].upper() + token[1:].lower()

    return re.sub(r"[^\s\-.()]+", lambda match: _word(match.group(0)), name.strip())


def _name_variants(name: str) -> List[str]:
    """«KRAKÓW-BALICE» → [«KRAKÓW-BALICE», «KRAKÓW»]; se quitan paréntesis."""
    base = re.sub(r"\(.*?\)", " ", name).strip()
    variants = [base]
    for separator in ("-", " "):
        head = base.split(separator)[0].strip()
        if head and head not in variants and len(head) >= 3:
            variants.append(head)
    return variants


def _box_from_code(code: str) -> Optional[Tuple[int, int]]:
    try:
        return int(code[1:3]), int(code[3:5])
    except (ValueError, IndexError):
        return None


def geocode_station(code: str, name: str) -> Optional[Dict[str, Any]]:
    """Coordenadas aproximadas: el primer topónimo polaco con ese nombre que
    cae en la cuadrícula de 1° del código de la estación."""
    box = _box_from_code(code)
    if box is None:
        return None
    lat_floor, lon_floor = box
    for variant in _name_variants(name):
        time.sleep(0.15)
        try:
            payload = _get_json(
                f"{GEOCODING_URL}?name={quote(variant.title())}&count=30&countryCode=PL&language=pl"
            )
        except Exception:
            continue
        matches = [
            result for result in payload.get("results") or []
            if math.floor(result.get("latitude", 0)) == lat_floor
            and math.floor(result.get("longitude", 0)) == lon_floor
        ]
        if matches:
            best = matches[0]
            return {
                "lat": round(float(best["latitude"]), 5),
                "lon": round(float(best["longitude"]), 5),
                "elev": best.get("elevation"),
                "ambiguous": len(matches) > 1,
                "matched_name": best.get("name"),
            }
    return None


def _elevation(lat: float, lon: float) -> Optional[float]:
    try:
        payload = _get_json(f"{ELEVATION_URL}?latitude={lat}&longitude={lon}")
        value = float((payload.get("elevation") or [None])[0])
    except Exception:
        return None
    return value if value == value else None


def _synop_by_code(synop: List[Dict[str, Any]]) -> Dict[str, str]:
    """Código IMGW de 9 dígitos → indicativo OMM (12295 → …295)."""
    wmo = {str(item.get("id_stacji") or "").strip()[-3:]: str(item.get("id_stacji")).strip() for item in synop}
    return wmo


def build_inventory(cache_dir: Path, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    meteo = _get_json(f"{API_URL}/meteo/")
    synop = _get_json(f"{API_URL}/synop/")
    wmo_by_suffix = _synop_by_code(synop)

    print("Escaneando el archivo diario…", flush=True)
    archive = scan_archive(cache_dir, today=now_utc.date())
    latest_archive_day = max(
        (last for record in archive.values() for _first, last in record["kinds"].values()),
        default=now_utc.date(),
    )
    active_since = latest_archive_day - timedelta(days=30 * ARCHIVE_ACTIVE_MONTHS)

    rows: List[Dict[str, Any]] = []
    seen = set()
    for item in meteo:
        code = str(item.get("kod_stacji") or "").strip()
        if not code or code in seen or not item.get("lat") or not item.get("lon"):
            continue
        seen.add(code)
        sensors = _empty_sensors()
        freshest: Optional[datetime] = None
        for field, sensor in METEO_SENSORS.items():
            if item.get(field) is None:
                continue
            stamp = _parse_time(item.get(f"{field}_data"))
            if stamp is None:
                continue
            freshest = stamp if freshest is None or stamp > freshest else freshest
            if now_utc - stamp <= SENSOR_MAX_AGE:
                sensors[sensor] = True
        kinds = (archive.get(code) or {}).get("kinds", {})
        suffix = code[-3:] if code.startswith("3") else ""
        wmo_id = wmo_by_suffix.get(suffix, "") if suffix else ""
        if wmo_id:
            sensors["barometer"] = True
        network = "SYNOP" if (wmo_id or "synop" in kinds) else (
            "KLIMAT" if "klimat" in kinds else ("OPAD" if "opad" in kinds else "TELEMETRIA")
        )
        extras = ["soil_temperature"] if item.get("temperatura_gruntu") is not None else []
        archive_start = min((first for first, _last in kinds.values()), default=None)
        rows.append({
            "id": code,
            "source_id": code,
            "name": display_name(str(item.get("nazwa_stacji") or code)),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "elev": float(item["wysokosc_npm"]) if item.get("wysokosc_npm") not in (None, "") else None,
            "altitude": float(item["wysokosc_npm"]) if item.get("wysokosc_npm") not in (None, "") else None,
            "tz": STATION_TZ,
            "country": "Polonia",
            "country_code": "PL",
            "region": "",
            "founded": int(item["rok_zalozenia_stacji"]) if str(item.get("rok_zalozenia_stacji") or "").isdigit() else None,
            "network": network,
            "wmo_id": wmo_id,
            "manual": False,
            "realtime": True,
            "active_now": freshest is not None and now_utc - freshest <= ONLINE_MAX_AGE,
            "archive_active": any(last >= active_since for _first, last in kinds.values()),
            "has_historical": bool(kinds),
            "archive_kinds": sorted(kinds),
            "archive_start": archive_start.isoformat() if archive_start else None,
            # En marcha: la serie llega hasta hoy (el archivo, más el poller).
            "archive_end": None if (
                freshest is not None and now_utc - freshest <= ONLINE_MAX_AGE
            ) else (max(last for _first, last in kinds.values()).isoformat() if kinds else None),
            "extra_sensors": extras,
            "coords_source": "imgw_api",
            "provider": "IMGW",
            "source": f"{API_URL}/meteo",
            "sensors": sensors,
        })

    print("Ubicando estaciones del archivo…", flush=True)
    try:
        pdf_coords = json.loads(IMGW_PDF_COORDS_PATH.read_text(encoding="utf-8"))["stations"]
    except (OSError, ValueError, KeyError):
        print("  aviso: sin data/imgw_pdf_coords.json; todo se geocodificará")
        pdf_coords = {}
    unresolved = []
    ambiguous = 0
    from_pdf = 0
    for code, record in sorted(archive.items()):
        if code in seen:
            continue
        kinds = record["kinds"]
        pdf = pdf_coords.get(code)
        box = _box_from_code(code)
        # Un código reutilizado o una fila mal leída del PDF manda la estación
        # lejos: si no cae en la cuadrícula de su código, no se usa. El margen
        # cubre el redondeo a minutos de las que están justo en el borde (17°00').
        if pdf and box and not (
            box[0] - 0.05 <= pdf["lat"] < box[0] + 1.05
            and box[1] - 0.05 <= pdf["lon"] < box[1] + 1.05
        ):
            pdf = None
        if pdf:
            geo = {"lat": pdf["lat"], "lon": pdf["lon"], "elev": None, "ambiguous": False,
                   "source": "imgw_pdf_map"}
            from_pdf += 1
        else:
            geo = geocode_station(code, record["name"])
            if geo is None:
                unresolved.append((code, record["name"]))
                continue
            geo["source"] = "geocoded_name"
            ambiguous += int(geo["ambiguous"])
        elev = geo.get("elev")
        if elev is None:
            elev = _elevation(geo["lat"], geo["lon"])
        kind = "synop" if "synop" in kinds else ("klimat" if "klimat" in kinds else "opad")
        sensors = _empty_sensors()
        for kind_name in kinds:
            for sensor in ARCHIVE_KIND_SENSORS[kind_name]:
                sensors[sensor] = True
        first = min(first for first, _last in kinds.values())
        last = max(last for _first, last in kinds.values())
        rows.append({
            "id": code,
            "source_id": code,
            "name": display_name(record["name"]),
            "lat": geo["lat"],
            "lon": geo["lon"],
            "elev": elev,
            "altitude": elev,
            "tz": STATION_TZ,
            "country": "Polonia",
            "country_code": "PL",
            "region": "",
            "network": NETWORK_BY_KIND[kind],
            "wmo_id": "",
            # Manual solo mientras el observador siga publicando; cerrada es
            # un archivo, no una estación manual.
            "manual": last >= active_since,
            "realtime": False,
            "active_now": last >= active_since,
            "archive_active": last >= active_since,
            "has_historical": True,
            "archive_kinds": sorted(kinds),
            "archive_start": first.isoformat(),
            "archive_end": None if last >= active_since else last.isoformat(),
            "archive_last_day": last.isoformat(),
            "extra_sensors": [],
            "coords_source": geo["source"],
            "coords_ambiguous": geo["ambiguous"],
            "provider": "IMGW",
            "source": f"{ARCHIVE_URL}/dobowe",
            "sensors": sensors,
        })
        seen.add(code)

    rows.sort(key=lambda row: (row["manual"], row["id"]))
    print(f"  del mapa PDF: {from_pdf}; geocodificadas: {sum(1 for r in rows if r.get('coords_source') == 'geocoded_name')}")
    print(f"  sin ubicar: {len(unresolved)}; geocodificadas ambiguas: {ambiguous}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=IMGW_STATIONS_PATH)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    rows = build_inventory(args.cache_dir)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "IMGW",
        "source": "https://danepubliczne.imgw.pl",
        "license": "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy",
        "stations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    auto = [r for r in rows if r["realtime"]]
    archive_only = [r for r in rows if not r["realtime"]]
    print(f"Guardadas {len(rows)} estaciones IMGW en {args.output}")
    print(f"  automáticas online:  {sum(1 for r in auto if r['active_now'])}")
    print(f"  automáticas offline: {sum(1 for r in auto if not r['active_now'])}")
    print(f"  manuales (siguen publicando en el archivo): {sum(1 for r in archive_only if r['manual'])}")
    print(f"  solo históricas (cerradas):                 {sum(1 for r in archive_only if not r['manual'])}")
    for network in ("SYNOP", "KLIMAT", "OPAD", "TELEMETRIA"):
        print(f"  red {network:10} {sum(1 for r in rows if r['network'] == network):5}")
    for sensor in _empty_sensors():
        print(f"  {sensor:12} {sum(1 for r in rows if r['sensors'].get(sensor)):5}")


if __name__ == "__main__":
    main()
