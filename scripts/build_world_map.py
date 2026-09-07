#!/usr/bin/env python3
"""Genera el mapa de países ligero que pinta el panel de uso.

El repo ya trae las fronteras de Natural Earth 50m que usa
``stations.country_for_point``, pero son 13 MB: servirlas al navegador para
colorear un panel interno no tiene sentido. Aquí se simplifican los contornos
y se redondean las coordenadas hasta dejar un fichero que pesa poco más de lo
que ocupa una foto, con la forma justa para reconocer cada país.

    python3 scripts/build_world_map.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import data_files

# Grados de tolerancia al simplificar. 0,25 conserva la silueta reconocible de
# cada país y se lleva por delante los recovecos de costa, que a la escala de
# un panel no se distinguen.
TOLERANCIA = 0.25
# Dos decimales son ~1 km en el ecuador: de sobra, y recorta mucho el fichero.
DECIMALES = 2
# Islotes por debajo de esto no se dibujan: a esta escala son un píxel.
AREA_MINIMA = 0.5

DESTINO = ROOT / "web" / "static" / "paises.json"


def _iso2(props: dict) -> str | None:
    for key in ("ISO_A2_EH", "ISO_A2", "iso_a2"):
        value = str(props.get(key, "")).strip().upper()
        if value and value != "-99" and len(value) == 2:
            return value
    return None


def _anillos(geometria, tolerancia: float) -> list:
    """Contornos exteriores simplificados, en [[lon, lat], ...]."""
    from shapely.geometry import MultiPolygon, Polygon

    simplificada = geometria.simplify(tolerancia, preserve_topology=True)
    partes = (
        list(simplificada.geoms)
        if isinstance(simplificada, MultiPolygon)
        else [simplificada] if isinstance(simplificada, Polygon) else []
    )
    salida = []
    for parte in partes:
        if parte.is_empty or parte.area < AREA_MINIMA:
            continue
        puntos = [
            [round(x, DECIMALES), round(y, DECIMALES)]
            for x, y in parte.exterior.coords
        ]
        if len(puntos) >= 4:
            salida.append(puntos)
    return salida


def main() -> int:
    from shapely.geometry import shape

    with open(data_files.COUNTRY_BORDERS_PATH, encoding="utf-8") as handle:
        features = json.load(handle).get("features", [])

    paises: dict[str, dict] = {}
    for feature in features:
        props = feature.get("properties") or {}
        iso = _iso2(props)
        if not iso:
            continue
        anillos = _anillos(shape(feature["geometry"]), TOLERANCIA)
        if not anillos:
            continue
        nombre = str(props.get("NAME_ES") or props.get("NAME") or iso)
        # Un ISO puede venir en varias features (territorios): se acumulan.
        entrada = paises.setdefault(iso, {"name": nombre, "rings": []})
        entrada["rings"].extend(anillos)

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    with open(DESTINO, "w", encoding="utf-8") as handle:
        json.dump(paises, handle, ensure_ascii=False, separators=(",", ":"))

    peso = DESTINO.stat().st_size / 1024
    anillos_total = sum(len(v["rings"]) for v in paises.values())
    print(f"{len(paises)} países · {anillos_total} contornos · {peso:.0f} KB → {DESTINO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
