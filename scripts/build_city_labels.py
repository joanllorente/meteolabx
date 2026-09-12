"""Genera el catálogo de ciudades que rotula el visor de predicción.

La fuente es `cities5000` de GeoNames (CC BY 4.0; la atribución va en el pie
del visor). Natural Earth, que era la fuente anterior, solo trae 845 núcleos
en toda la ventana europea: con el zoom a fondo el mapa cubre una comarca y se
quedaba sin nombres que poner.

    curl -sLO https://download.geonames.org/export/dump/cities5000.zip
    unzip -q cities5000.zip
    python3 scripts/build_city_labels.py cities5000.txt

El volcado es un TSV sin cabecera; de sus diecinueve columnas se usan el
nombre, la coordenada, el código de entidad y la población.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "prototype-svelte" / "src" / "data" / "cityLabels.js"

# Ventana de trabajo: cubre el dominio de AROME con holgura y el recorte
# europeo de ECMWF que se enseña junto a él.
OESTE, ESTE, SUR, NORTE = -16.0, 24.0, 33.0, 59.0
POBLACION_MINIMA = 15_000

# Entidades de población de GeoNames. Se quedan fuera las secciones de ciudad
# (PPLX), que son barrios con nombre propio y llenarían el área metropolitana
# de rótulos que compiten con el de la ciudad, y las despobladas (PPLQ, PPLH).
ENTIDADES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLG"}

# Criba de vecindad para los núcleos pequeños: una ciudad por cuadro de 0,1°,
# unos once kilómetros. Sin ella, una conurbación gasta veinte entradas del
# catálogo en núcleos que a esa distancia nunca caben dos en pantalla. Las de
# más de 70.000 y las capitales se libran: ahí los vecinos son ciudades por
# derecho propio —Getafe y Leganés no son un duplicado de Madrid—.
CUADRO_CRIBA = 0.1

# Rango de rótulo: a qué nivel de detalle entra cada ciudad. El 1 es lo que se
# ve con el mapa entero y el 6 lo que aparece con el zoom a fondo, cuando la
# pantalla cubre una comarca y las capitales de provincia ya no bastan.
def rango(poblacion: float, capital: bool) -> int:
    if poblacion >= 700_000 or (capital and poblacion >= 250_000):
        return 1
    if capital or poblacion >= 350_000:
        return 2
    if poblacion >= 150_000:
        return 3
    if poblacion >= 70_000:
        return 4
    if poblacion >= 35_000:
        return 5
    return 6


# Natural Earth rotula en inglés y con alguna marca de control colada en el
# nombre. Se traduce lo que en castellano tiene nombre propio asentado; el
# resto se queda con el endónimo, que es lo que pone la señal de la carretera.
EN_CASTELLANO = {
    "Seville": "Sevilla", "La Coruña": "A Coruña", "Castello": "Castelló",
    "London": "Londres", "Lisbon": "Lisboa", "Paris": "París",
    "Bordeaux": "Burdeos", "Marseille": "Marsella", "Nice": "Niza",
    "Strasbourg": "Estrasburgo", "Monaco": "Mónaco",
    "Munich": "Múnich", "München": "Múnich", "Cologne": "Colonia",
    "Köln": "Colonia", "Frankfurt am Main": "Fráncfort",
    "Nuremberg": "Núremberg", "Nürnberg": "Núremberg", "Dresden": "Dresde",
    "Copenhagen": "Copenhague", "Antwerp": "Amberes",
    "Nürnberg": "Núremberg", "Frankfurt": "Fráncfort", "Hamburg": "Hamburgo",
    "Berlin": "Berlín", "Zürich": "Zúrich", "Geneva": "Ginebra",
    "Genève": "Ginebra", "Basel": "Basilea", "Bern": "Berna",
    "Brussels": "Bruselas", "Bruxelles": "Bruselas", "Antwerpen": "Amberes",
    "Gent": "Gante", "Liège": "Lieja", "The Hague": "La Haya",
    "Rotterdam": "Róterdam", "Milan": "Milán", "Turin": "Turín",
    "Rome": "Roma", "Naples": "Nápoles", "Florence": "Florencia",
    "Venice": "Venecia", "Venezia": "Venecia", "Genoa": "Génova",
    "Genova": "Génova", "Padova": "Padua", "Bologna": "Bolonia",
    "Milano": "Milán", "Torino": "Turín", "Firenze": "Florencia",
    "Napoli": "Nápoles",
    "Vatican City": "Ciudad del Vaticano", "Valletta": "La Valeta",
    "København": "Copenhague", "Gothenburg": "Gotemburgo",
    "Stockholm": "Estocolmo", "Göteborg": "Gotemburgo",
    "Vienna": "Viena", "Prague": "Praga", "Warsaw": "Varsovia",
    "Kraków": "Cracovia", "Ljubljana": "Liubliana", "Belgrade": "Belgrado",
    "Bucharest": "Bucarest", "Sofia": "Sofía", "Athens": "Atenas",
    "Thessaloniki": "Salónica", "Istanbul": "Estambul", "İzmir": "Esmirna",
    "Chișinău": "Chisináu", "Tunis": "Túnez", "Tripoli": "Trípoli",
    "Banghazi": "Bengasi", "Alexandria": "Alejandría", "Oran": "Orán",
    "Tangier": "Tánger", "Marrakesh": "Marrakech", "Algiers": "Argel",
    "Fes": "Fez", "Meknes": "Mequinez", "Tétouan": "Tetuán",
    "Constantine": "Constantina", "Kaliningrad": "Kaliningrado",
    "Wien": "Viena", "Praha": "Praga", "Warszawa": "Varsovia",
    "Lisbon": "Lisboa", "Bruxelles": "Bruselas", "Den Haag": "La Haya",
    "'s-Gravenhage": "La Haya", "Genève": "Ginebra", "Zürich": "Zúrich",
    "Edinburgh": "Edimburgo", "Dublin": "Dublín",
    "Luxembourg": "Luxemburgo", "Andorra": "Andorra la Vella",
}


# Coletillas administrativas que GeoNames arrastra del registro civil alemán y
# suizo: en un mapa del tiempo sobran, y ocupan sitio de rótulo.
PREFIJOS = (
    "Universitäts- und Hansestadt ", "Landeshauptstadt ", "Hansestadt ",
    "Stadt ",
)

# Rótulo que no cabe: por encima de esto son fracciones y agregados que
# GeoNames encadena con guiones —«Rosignano Solvay-Castiglioncello»— y que
# ocuparían media pantalla para nombrar un núcleo de veinte mil habitantes.
# Solo se les exige a los rangos pequeños: una ciudad grande se rotula aunque
# el nombre sea largo.
NOMBRE_MAXIMO = 24


def limpia(texto: str) -> str:
    """Deja el nombre en lo que se puede escribir sobre un mapa.

    GeoNames trae el nombre oficial completo: con la coletilla administrativa
    delante, con la desambiguación entre paréntesis —«Halle (Saale)»— y, en
    los territorios bilingües, con las dos versiones separadas por barra. De
    las dos se queda la segunda, que es la castellana en el País Vasco, que es
    de donde salen casi todos los casos.
    """
    nombre = "".join(
        caracter for caracter in texto.strip()
        if unicodedata.category(caracter) != "Cf"
    )
    for prefijo in PREFIJOS:
        if nombre.startswith(prefijo):
            nombre = nombre[len(prefijo):]
    if " / " in nombre:
        nombre = nombre.split(" / ")[-1]
    if nombre.endswith(")") and "(" in nombre:
        nombre = nombre[:nombre.rindex("(")].strip()
    return nombre.strip()


def registros(volcado: Path) -> list[dict[str, str]]:
    """Filas del volcado de GeoNames, con las columnas que hacen falta."""
    filas = []
    with volcado.open(encoding="utf-8") as fichero:
        for linea in fichero:
            partes = linea.rstrip("\n").split("\t")
            if len(partes) < 15:
                continue
            filas.append({
                "name": partes[1],
                "latitude": partes[4],
                "longitude": partes[5],
                "fcode": partes[7],
                "population": partes[14],
            })
    return filas


def ciudades(volcado: Path) -> list[list]:
    candidatas = []
    for fila in registros(volcado):
        if fila["fcode"] not in ENTIDADES:
            continue
        longitud = float(fila["longitude"])
        latitud = float(fila["latitude"])
        if not (OESTE <= longitud <= ESTE and SUR <= latitud <= NORTE):
            continue
        poblacion = float(fila["population"] or 0)
        capital = fila["fcode"] == "PPLC"
        if poblacion < POBLACION_MINIMA and not capital:
            continue
        nombre = limpia(fila["name"])
        if not nombre:
            continue
        # Los distritos de París y Marsella vienen numerados —«Paris 15
        # Vaugirard»— y con población de ciudad mediana: sin esto, la capital
        # aparece veinte veces sobre su propia mancha.
        if any(caracter.isdigit() for caracter in nombre):
            continue
        if len(nombre) > NOMBRE_MAXIMO and poblacion < 150_000:
            continue
        candidatas.append((poblacion, capital, nombre, latitud, longitud))

    # De mayor a menor: el orden del fichero decide quién se queda el sitio
    # cuando dos rótulos se pisan, y ahí debe ganar la ciudad que más gente
    # reconoce, no la que llegue antes en el volcado.
    candidatas.sort(key=lambda fila: -fila[0])
    ocupados = set()
    elegidas = []
    for poblacion, capital, nombre, latitud, longitud in candidatas:
        nivel = rango(poblacion, capital)
        if nivel > 4:
            cuadro = (round(latitud / CUADRO_CRIBA), round(longitud / CUADRO_CRIBA))
            if cuadro in ocupados:
                continue
            ocupados.add(cuadro)
        elegidas.append([
            EN_CASTELLANO.get(nombre, nombre),
            round(latitud, 3),
            round(longitud, 3),
            nivel,
        ])
    elegidas.sort(key=lambda ciudad: ciudad[3])
    return elegidas


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    lista = ciudades(Path(sys.argv[1]))
    filas = ",\n  ".join(json.dumps(ciudad, ensure_ascii=False) for ciudad in lista)
    DESTINO.write_text(
        "/**\n"
        " * Ciudades que el visor puede rotular sobre el mapa.\n"
        " *\n"
        " * Generado por `scripts/build_city_labels.py` a partir de\n"
        " * `ne_10m_populated_places_simple` de Natural Earth (dominio público).\n"
        " * No se edita a mano: se vuelve a generar.\n"
        " *\n"
        " * Cada fila es `[nombre, latitud, longitud, rango]`. El rango es el nivel\n"
        " * de detalle desde el que la ciudad merece rótulo: 1 son las que se ven\n"
        " * con el mapa entero y 6 las que solo aparecen con el zoom a fondo.\n"
        " */\n\n"
        f"export const CITY_LABELS = [\n  {filas}\n];\n",
        encoding="utf-8",
    )
    print(f"{len(lista)} ciudades en {DESTINO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
