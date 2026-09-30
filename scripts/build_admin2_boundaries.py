"""Genera las subdivisiones de segundo nivel que el visor dibuja con zoom alto.

Las regiones que ya se dibujan (comunidades autónomas, régions, Länder…) salen
de `ne_50m_admin_1_states_provinces.geojson`. Con el zoom a fondo la pantalla
cubre media comunidad y entre frontera y frontera no queda ninguna referencia:
esta capa añade el nivel de debajo —provincias, départements, province— donde
Natural Earth 1:10 m lo trae.

    curl -sSLO https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip
    unzip -q ne_10m_admin_1_states_provinces.zip -d ne10a1
    python3 scripts/build_admin2_boundaries.py ne10a1/ne_10m_admin_1_states_provinces.shp

Natural Earth es de dominio público.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import box, mapping

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "data" / "ne_10m_admin_2_europe.geojson"

# Dominio de AROME con algo de holgura. ECMWF no dibuja divisiones interiores.
VENTANA = (-12.5, 37.0, 16.5, 56.0)

# Solo los países donde el 1:10 m baja un nivel respecto a las regiones que ya
# se dibujan. En Alemania, Suiza, Austria o Países Bajos trae las mismas, y en
# Eslovenia baja hasta el municipio: 160 recintos en un país de 20.000 km².
PAISES = {
    "Spain": "provincia",
    "France": "département",
    "Italy": "provincia",
    "Portugal": "distrito",
    "Belgium": "provincia",
    "Ireland": "condado",
    "United Kingdom": "condado",
}

# Unos 300 m: al zoom máximo del visor un píxel son 400 m, así que no se ve la
# diferencia. Con 0,002 las fronteras del visor pesaban 50 kB más comprimidas
# sin nada que enseñar a cambio.
SIMPLIFICACION = 0.003


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    capa = gpd.read_file(sys.argv[1])
    ventana = box(*VENTANA)
    features = []
    for fila in capa[capa["admin"].isin(PAISES)].itertuples():
        geometria = fila.geometry
        if geometria is None or geometria.is_empty or not geometria.intersects(ventana):
            continue
        recorte = shapely.set_precision(
            geometria.intersection(ventana).simplify(SIMPLIFICACION, preserve_topology=True),
            1e-5,
        )
        if recorte.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "NAMEUNIT": fila.name,
                "COUNTRY": fila.admin,
                "boundary_level": "admin2",
            },
            "geometry": mapping(recorte),
        })
    DESTINO.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    tamano = DESTINO.stat().st_size / 1024
    print(f"{len(features)} recintos en {DESTINO.relative_to(RAIZ)} ({tamano:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
