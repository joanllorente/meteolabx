from pathlib import Path
from zipfile import ZipFile, ZIP_STORED


SOURCE = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_SHIP_revisado_metodo_SHARPpy.odt"
)
OUTPUT = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_SHIP_revisado_algoritmo.odt"
)


METHOD_SECTION = """<text:h text:style-name="P12" text:outline-level="2">Cómo se obtiene</text:h>
<text:p text:style-name="P13">MeteoLabX obtiene SHIP mediante la función sharppy.sharptab.params.ship de SHARPpy. Para cada punto del mapa prepara los ingredientes de la parcela más inestable y del perfil ambiental, y aplica la formulación operativa siguiente:</text:p>
<text:p text:style-name="P18">SHIP_0 = −(MUCAPE · r_MU · Γ_700-500 · T_500 · BWD_0-6) / 42 000 000</text:p>
<text:p text:style-name="P18">SHIP = max(0, SHIP_0) · f_C · f_Γ · f_F</text:p>
<text:list text:style-name="WWNum18">
<text:list-item text:start-value="1"><text:p text:style-name="P24"><text:span text:style-name="T11">Preparar la parcela MU. </text:span><text:span text:style-name="T10">Se toman la MUCAPE MLX y la razón de mezcla de la misma parcela más inestable, para que energía y humedad describan el mismo aire de origen.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Construir los campos verticales. </text:span><text:span text:style-name="T10">A partir del perfil AROME se calculan el gradiente térmico 700-500 hPa, la temperatura a 500 hPa, la BWD geométrica entre superficie y 6 km y la altura AGL del nivel de 0 °C.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Evaluar SHIP con SHARPpy. </text:span><text:span text:style-name="T10">MeteoLabX entrega estos ingredientes a sharppy.sharptab.params.ship, que los combina celda a celda mediante la formulación SPC.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Aplicar los límites operativos. </text:span><text:span text:style-name="T10">La función restringe la humedad MU al intervalo 11-13,6 g/kg y la BWD 0-6 km a 7-27 m/s, y aplica a T500 el límite de −5,5 °C. Así se evita que un solo ingrediente extremo domine el índice.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Reducir y cerrar el resultado. </text:span><text:span text:style-name="T10">Una MUCAPE inferior a 1300 J/kg, un gradiente 700-500 hPa menor de 5,8 °C/km o un nivel de 0 °C por debajo de 2400 m AGL reducen progresivamente SHIP. El valor final es adimensional y se trunca a cero.</text:span></text:p></text:list-item>
</text:list>"""


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
