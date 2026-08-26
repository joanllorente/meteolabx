from pathlib import Path
from zipfile import ZipFile, ZIP_STORED


SOURCE = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_DCape_revisado.odt"
)
OUTPUT = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_SHIP_revisado.odt"
)


SHIP_SECTION = """<text:h text:style-name="P11" text:outline-level="1">SHIP · Significant Hail Parameter</text:h>
<text:h text:style-name="P12" text:outline-level="2">Qué representa</text:h>
<text:p text:style-name="P13">SHIP resume hasta qué punto el ambiente podría permitir que una tormenta produzca granizo muy grande. No busca detectar cualquier granizada: se diseñó para reconocer entornos asociados a granizo significativo, aproximadamente de 5 cm o más en su contexto original de Estados Unidos.</text:p>
<text:p text:style-name="P13">El índice combina varios requisitos que deben coincidir. Hace falta una corriente ascendente capaz de sostener las piedras, humedad que aporte agua superenfriada, una zona fría donde el granizo pueda crecer y suficiente cizalladura para que la tormenta permanezca organizada. Si uno de estos ingredientes es claramente desfavorable, el resultado disminuye.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Por qué interviene cada ingrediente</text:h>
<text:list text:style-name="WWNum18">
<text:list-item text:start-value="1"><text:p text:style-name="P24"><text:span text:style-name="T11">MUCAPE. </text:span><text:span text:style-name="T10">Representa la energía disponible para acelerar la corriente ascendente. Una corriente intensa puede mantener el granizo suspendido durante más tiempo y permitir que continúe creciendo.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Humedad de la parcela MU. </text:span><text:span text:style-name="T10">Una mayor razón de mezcla favorece que la tormenta produzca abundante agua líquida superenfriada, que se congela sobre los embriones de granizo.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Gradiente 700-500 hPa y temperatura a 500 hPa. </text:span><text:span text:style-name="T10">Una capa media fría y con fuerte descenso de temperatura con la altura refuerza la inestabilidad y favorece una zona de crecimiento del granizo profunda y activa.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Cizalladura 0-6 km. </text:span><text:span text:style-name="T10">Ayuda a separar la corriente ascendente de la precipitación y a mantener una tormenta organizada y duradera, aumentando el tiempo disponible para el crecimiento.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P24"><text:span text:style-name="T11">Altura de 0 °C. </text:span><text:span text:style-name="T10">Sitúa verticalmente las zonas de congelación y fusión. La formulación reduce el índice cuando este nivel es muy bajo, una situación frecuente en ambientes fríos y de convección poco profunda para los que SHIP no fue optimizado.</text:span></text:p></text:list-item>
</text:list>
<text:h text:style-name="P12" text:outline-level="2">Cómo interpretar el mapa</text:h>
<text:p text:style-name="P13">Un valor alto significa que varios ingredientes favorables coinciden en el mismo lugar y momento. Si se desarrolla una tormenta que aprovecha esa parcela y logra organizarse, el ambiente permite un crecimiento eficiente del granizo. Las zonas donde SHIP aumenta rápidamente suelen ser más informativas que un número aislado.</text:p>
<text:p text:style-name="P13">SHIP no representa el tamaño previsto de las piedras, la cantidad de granizo ni la probabilidad de que granice en un punto. Tampoco asegura que se forme una tormenta: no incluye el mecanismo de disparo, la inhibición que debe superarse ni todos los procesos internos de la célula.</text:p>
<text:p text:style-name="P13">Un valor bajo tampoco descarta granizo severo. Una supercélula puede aprovechar pequeñas zonas de aire más favorable que el modelo no resuelva, una parcela distinta de la elegida o procesos de crecimiento que un índice compuesto simplifica. Por ello debe compararse con MUCAPE, BWD o EBWD, altura de 0 °C, modo convectivo y, cuando estén disponibles, reflectividad y observaciones.</text:p>
<text:p text:style-name="P13">Los umbrales proceden del contexto operativo del SPC de Estados Unidos y no están calibrados para Europa. Conviene utilizarlos como orientación relativa y no como categorías universales de riesgo.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Cómo se obtiene</text:h>
<text:p text:style-name="P13">MeteoLabX calcula SHIP en cada punto combinando la MUCAPE y la humedad de la parcela más inestable con el gradiente térmico 700-500 hPa, la temperatura a 500 hPa, la cizalladura geométrica entre superficie y 6 km y la altura AGL del nivel de 0 °C:</text:p>
<text:p text:style-name="P18">SHIP_0 = −(MUCAPE · r_MU · Γ_700-500 · T_500 · BWD_0-6) / 42 000 000</text:p>
<text:p text:style-name="P18">SHIP = max(0, SHIP_0) · f_C · f_Γ · f_F</text:p>
<text:p text:style-name="P13">La formulación limita la influencia excesiva de algunos ingredientes: restringe la humedad MU al intervalo 11-13,6 g/kg, la BWD 0-6 km a 7-27 m/s y la temperatura a 500 hPa a un máximo de −5,5 °C. Además, reduce el resultado cuando la MUCAPE es inferior a 1300 J/kg, el gradiente 700-500 hPa es menor de 5,8 °C/km o el nivel de 0 °C está por debajo de 2400 m AGL.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Notas y limitaciones</text:h>
<text:p text:style-name="P13">La MUCAPE usada es el diagnóstico MLX sin arrastre y la cizalladura es la BWD geométrica superficie-6 km, no la EBWD. SHIP resume la coincidencia de ingredientes ambientales; no reproduce la trayectoria real de cada piedra, el reciclaje dentro de la corriente ascendente, la cantidad de agua superenfriada ni la microfísica de una tormenta concreta.</text:p>
<text:p text:style-name="P14">Diagnóstico MeteoLabX · formulación SPC sobre perfiles AROME</text:p>"""


def main():
    with ZipFile(SOURCE, "r") as source_zip:
        entries = source_zip.infolist()
        content = source_zip.read("content.xml").decode("utf-8")

        title = "SHIP · Significant Hail Parameter"
        next_title = "Precipitación en 1 hora"
        title_pos = content.find(title)
        if title_pos < 0:
            raise SystemExit("No se encontró la sección SHIP.")
        start = content.rfind("<text:h", 0, title_pos)
        next_pos = content.find(next_title, title_pos)
        if next_pos < 0:
            raise SystemExit("No se encontró el final de la sección SHIP.")
        end = content.rfind("<text:h", title_pos, next_pos)
        if start < 0 or end <= start:
            raise SystemExit("No se pudo delimitar la sección SHIP.")

        updated = (content[:start] + SHIP_SECTION + content[end:]).encode("utf-8")

        with ZipFile(OUTPUT, "w") as output_zip:
            for entry in entries:
                data = updated if entry.filename == "content.xml" else source_zip.read(entry.filename)
                if entry.filename == "mimetype":
                    entry.compress_type = ZIP_STORED
                output_zip.writestr(entry, data)

    print(OUTPUT)


if __name__ == "__main__":
    main()
