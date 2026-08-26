from pathlib import Path
from zipfile import ZipFile, ZIP_STORED


SOURCE = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos.odt"
)
OUTPUT = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_EBWD_direccion.odt"
)

ANCHOR = (
    '<text:p text:style-name="Standard"><text:span text:style-name="T10">'
    "Como referencia operativa, el entorno se vuelve progresivamente más favorable para "
    "supercélulas cuando la EBWD entra aproximadamente en el intervalo de "
    '</text:span><text:span text:style-name="T11">25 a 40 kt</text:span>'
    '<text:span text:style-name="T10"> o lo supera. </text:span></text:p>'
)

ADDITION_TEXT = (
    "La flecha de EBWD muestra la orientación del cambio del viento entre la base y el techo "
    "efectivos; no es la dirección del viento ni el movimiento de la tormenta. Su efecto "
    "depende de la orientación relativa: perpendicular a una frontera favorece que las células "
    "se separen y permanezcan más discretas, mientras paralela aumenta sus interacciones y la "
    "evolución lineal. En una línea, la componente perpendicular ayuda más a sostener la "
    "regeneración frontal y la paralela transporta y reorganiza células a lo largo del eje. "
    "Para valorar la rotación y el potencial supercelular debe combinarse con el movimiento de "
    "la tormenta, la SRH y la hodógrafa."
)

ADDITION = f'<text:p text:style-name="P13">{ADDITION_TEXT}</text:p>'


def main():
    with ZipFile(SOURCE, "r") as source_zip:
        entries = source_zip.infolist()
        content = source_zip.read("content.xml").decode("utf-8")

        if ADDITION_TEXT in content:
            raise SystemExit("El párrafo ya existe en el documento de origen.")
        if content.count(ANCHOR) != 1:
            raise SystemExit("No se encontró de forma inequívoca el punto de inserción.")

        updated_content = content.replace(ANCHOR, ANCHOR + ADDITION, 1).encode("utf-8")

        with ZipFile(OUTPUT, "w") as output_zip:
            for entry in entries:
                data = updated_content if entry.filename == "content.xml" else source_zip.read(entry.filename)
                if entry.filename == "mimetype":
                    entry.compress_type = ZIP_STORED
                output_zip.writestr(entry, data)

    print(OUTPUT)


if __name__ == "__main__":
    main()
