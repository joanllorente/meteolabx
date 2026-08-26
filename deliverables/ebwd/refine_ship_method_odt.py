from pathlib import Path
from zipfile import ZipFile, ZIP_STORED


SOURCE = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_SHIP_revisado.odt"
)
OUTPUT = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_SHIP_revisado_metodo_SHARPpy.odt"
)


METHOD_SECTION = """<text:h text:style-name="P12" text:outline-level="2">Cómo se obtiene</text:h>
<text:p text:style-name="P13">MeteoLabX obtiene SHIP mediante la función sharppy.sharptab.params.ship de SHARPpy, aplicada a cada punto del mapa. Le proporciona la MUCAPE y la humedad de la parcela más inestable, el gradiente térmico 700-500 hPa, la temperatura a 500 hPa, la cizalladura geométrica entre superficie y 6 km y la altura AGL del nivel de 0 °C. De este modo, el diagnóstico sigue la formulación meteorológica operativa de SHARPpy y no una aproximación distinta creada para la visualización.</text:p>
<text:p text:style-name="P18">SHIP_0 = −(MUCAPE · r_MU · Γ_700-500 · T_500 · BWD_0-6) / 42 000 000</text:p>
<text:p text:style-name="P13">La función limita internamente la influencia excesiva de algunos ingredientes y aplica reductores cuando la CAPE, el gradiente 700-500 hPa o la altura de congelación quedan por debajo de sus intervalos de referencia. El resultado final es adimensional y no puede ser negativo.</text:p>"""


def main():
    with ZipFile(SOURCE, "r") as source_zip:
        entries = source_zip.infolist()
        content = source_zip.read("content.xml").decode("utf-8")

        ship_pos = content.find("SHIP · Significant Hail Parameter")
        method_pos = content.find("Cómo se obtiene", ship_pos)
        notes_pos = content.find("Notas y limitaciones", method_pos)
        if min(ship_pos, method_pos, notes_pos) < 0:
            raise SystemExit("No se pudo localizar el bloque de método de SHIP.")

        start = content.rfind("<text:h", ship_pos, method_pos)
        end = content.rfind("<text:h", method_pos, notes_pos)
        if start < 0 or end <= start:
            raise SystemExit("No se pudo delimitar el bloque de método de SHIP.")

        updated = (content[:start] + METHOD_SECTION + content[end:]).encode("utf-8")

        with ZipFile(OUTPUT, "w") as output_zip:
            for entry in entries:
                data = updated if entry.filename == "content.xml" else source_zip.read(entry.filename)
                if entry.filename == "mimetype":
                    entry.compress_type = ZIP_STORED
                output_zip.writestr(entry, data)

    print(OUTPUT)


if __name__ == "__main__":
    main()
