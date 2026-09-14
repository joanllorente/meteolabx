#!/usr/bin/env python3
"""
Extrae las coordenadas de las estaciones de IMGW de sus «mapas de contenido»
(``mapa_zawartosci_{synop,klimat,opad}.pdf``).

Son el único listado público con la ubicación de las estaciones que solo
existen en el archivo —manuales o cerradas—: ``wykaz_stacji.csv`` no trae
coordenadas y la API solo cubre la red telemétrica. Cada fila da el código
corto de la estación, su nombre, longitud y latitud en grados y minutos (a
veces con segundos) y el río de la cuenca. IMGW avisa de que son aproximadas;
con minutos enteros el error ronda 1-2 km, que basta para el mapa.

El código corto se traduce al de 9 dígitos con ``wykaz_stacji.csv``.

Necesita ``pypdf``, que no es dependencia de la aplicación: el resultado se
guarda en ``data/imgw_pdf_coords.json`` y es lo que lee
``scripts/build_imgw_inventory.py``.

Uso:
  pip install --target /tmp/pylibs pypdf
  PYTHONPATH=/tmp/pylibs python3 scripts/extract_imgw_pdf_coords.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import IMGW_PDF_COORDS_PATH

ARCHIVE_URL = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
KINDS = ("synop", "klimat", "opad")

# Código corto, nombre y a continuación la tanda de enteros de las coordenadas
# (4 = grados y minutos; 6 = con segundos) antes del nombre del río.
ROW = re.compile(r"^\s*(\d{1,5})\s+(.+?)\s+((?:\d{1,2}\s+){3}\d{1,2}(?:\s+\d{1,2}\s+\d{1,2})?)\s+(\D.*)$")


def _get(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=300) as response:
        return response.read()


def parse_coordinates(numbers: str) -> Optional[Tuple[float, float]]:
    """``"17 31 57 53 42 55"`` (lon DMS, lat DMS) o ``"17 58 53 06"`` (DM, DM)
    → (lat, lon)."""
    parts = [int(part) for part in numbers.split()]
    if len(parts) == 6:
        lon = parts[0] + parts[1] / 60 + parts[2] / 3600
        lat = parts[3] + parts[4] / 60 + parts[5] / 3600
    elif len(parts) == 4:
        lon = parts[0] + parts[1] / 60
        lat = parts[2] + parts[3] / 60
    else:
        return None
    # Polonia: 49-55 °N, 14-24,2 °E. Fuera de ahí la fila está mal leída.
    if not (48.5 <= lat <= 55.2 and 13.8 <= lon <= 24.5):
        return None
    return round(lat, 5), round(lon, 5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=IMGW_PDF_COORDS_PATH)
    args = parser.parse_args()

    from pypdf import PdfReader

    wykaz = _get(f"{ARCHIVE_URL}/wykaz_stacji.csv").decode("cp1250")
    by_short: Dict[str, str] = {}
    for row in csv.reader(io.StringIO(wykaz)):
        if len(row) >= 3 and row[2].strip():
            by_short[row[2].strip()] = row[0].strip()

    stations: Dict[str, Dict[str, object]] = {}
    for kind in KINDS:
        reader = PdfReader(io.BytesIO(_get(f"{ARCHIVE_URL}/mapa_zawartosci_{kind}.pdf")))
        parsed = skipped = 0
        for page in reader.pages:
            for line in (page.extract_text() or "").splitlines():
                match = ROW.match(line)
                if not match:
                    continue
                short, name, numbers, _rest = match.groups()
                coords = parse_coordinates(numbers)
                code = by_short.get(short)
                if coords is None or code is None:
                    skipped += 1
                    continue
                stations.setdefault(code, {
                    "name": name.strip(),
                    "lat": coords[0],
                    "lon": coords[1],
                    "precision": "dms" if len(numbers.split()) == 6 else "dm",
                    "kinds": [],
                })["kinds"].append(kind)
                parsed += 1
        print(f"{kind}: {parsed} filas con coordenadas, {skipped} descartadas")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"{ARCHIVE_URL}/mapa_zawartosci_*.pdf",
        "note": "Coordenadas aproximadas según IMGW (grados y minutos).",
        "stations": stations,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Guardadas {len(stations)} estaciones en {args.output}")


if __name__ == "__main__":
    main()
