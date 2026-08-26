from pathlib import Path
from zipfile import ZipFile, ZIP_STORED


SOURCE = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_EBWD_direccion.odt"
)
OUTPUT = Path(__file__).with_name(
    "MeteoLabX_explicaciones_parametros_meteorologicos_DCAPE_revisado.odt"
)


DCAPE_SECTION = """<text:h text:style-name="P11" text:outline-level="1">DCAPE · Energía potencial de corrientes descendentes</text:h>
<text:h text:style-name="P12" text:outline-level="2">Qué representa</text:h>
<text:p text:style-name="P13">DCAPE mide la energía que podría acelerar hacia abajo una parcela de aire. Puede entenderse como el equivalente descendente de la CAPE: mientras la CAPE suma la flotabilidad positiva que impulsa una corriente ascendente, la DCAPE suma la flotabilidad negativa que puede impulsar una corriente descendente.</text:p>
<text:p text:style-name="P13">El valor describe el potencial termodinámico del ambiente, no una corriente descendente ya existente. Para que ese potencial se materialice debe haber precipitación o hielo que permita enfriar y cargar el aire descendente.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Física paso a paso</text:h>
<text:list text:style-name="WWNum16">
<text:list-item text:start-value="1"><text:p text:style-name="P22"><text:span text:style-name="T11">Enfriamiento. </text:span><text:span text:style-name="T10">Cuando lluvia, granizo o nieve caen por aire no saturado, parte del agua se evapora o sublima. Estos cambios de fase consumen calor y enfrían el aire que rodea a los hidrometeoros.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Flotabilidad negativa. </text:span><text:span text:style-name="T10">El aire enfriado se vuelve más denso que el ambiente. Esa diferencia de densidad produce una fuerza descendente: cuanto más fría permanezca la parcela respecto a su entorno, mayor será su aceleración potencial.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Aceleración durante el descenso. </text:span><text:span text:style-name="T10">La parcela se calienta por compresión al bajar, pero en una capa baja con fuerte gradiente térmico el ambiente puede calentarse hacia el suelo aún más deprisa. La parcela continúa relativamente fría y la flotabilidad negativa se acumula a lo largo de una mayor profundidad.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Salida en superficie. </text:span><text:span text:style-name="T10">Cuando el núcleo descendente llega al suelo, ya no puede continuar hacia abajo y se expande horizontalmente. Esa expansión forma el frente de racha y puede producir un reventón.</text:span></text:p></text:list-item>
</text:list>
<text:h text:style-name="P12" text:outline-level="2">Cómo interpretar el valor</text:h>
<text:p text:style-name="P13">Una DCAPE alta suele aparecer cuando existe aire relativamente seco en niveles bajos o medios y un descenso marcado de la temperatura con la altura. La combinación permite mucho enfriamiento por evaporación y mantiene fría la parcela durante el descenso, por lo que señala <text:span text:style-name="T11">entornos favorables para reventones</text:span>.</text:p>
<text:p text:style-name="P13">Como escala idealizada, si toda la energía se transformara en velocidad vertical, la corriente descendente podría aproximarse mediante:</text:p>
<text:p text:style-name="P18">w_ideal ≈ √(2 · DCAPE)</text:p>
<text:p text:style-name="P13">Esta relación no calcula la racha en superficie. Parte de la energía se pierde por mezcla, rozamiento y evaporación incompleta, y el viento observado depende también de cuánto momento transporta la tormenta desde niveles altos y de cómo se organiza el flujo al alcanzar el suelo.</text:p>
<text:p text:style-name="P13">Una DCAPE baja tampoco descarta viento dañino: una tormenta organizada puede producirlo mediante carga de precipitación, transferencia de momento, un cold pool intenso o la combinación de varias corrientes descendentes.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Cómo lo calcula MeteoLabX</text:h>
<text:p text:style-name="P13">MeteoLabX reproduce el procedimiento SPC/SHARPpy y busca una parcela especialmente favorable al enfriamiento dentro de los 400 hPa inferiores del perfil:</text:p>
<text:list text:style-name="WWNum16">
<text:list-item text:start-value="1"><text:p text:style-name="P22"><text:span text:style-name="T11">Buscar el aire de origen. </text:span><text:span text:style-name="T10">Se prueban capas móviles de 100 hPa y se elige la que tiene menor temperatura potencial equivalente media. Una θe baja identifica aire relativamente frío y seco, capaz de alcanzar una temperatura baja al saturarse.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Situar la parcela. </text:span><text:span text:style-name="T10">La parcela parte del centro de esa capa de 100 hPa:</text:span></text:p></text:list-item>
</text:list>
<text:p text:style-name="P18">p_0 = p_base,min(θ̄_e) − 50 hPa</text:p>
<text:list text:style-name="WWNum16">
<text:list-item text:start-value="3"><text:p text:style-name="P22"><text:span text:style-name="T11">Representar el enfriamiento inicial. </text:span><text:span text:style-name="T10">Su temperatura se lleva al bulbo húmedo, como aproximación al enfriamiento producido al evaporarse precipitación hasta alcanzar la saturación.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Hacerla descender. </text:span><text:span text:style-name="T10">La parcela baja pseudoadiabáticamente hasta la superficie y se compara en cada nivel con la temperatura ambiental.</text:span></text:p></text:list-item>
<text:list-item><text:p text:style-name="P22"><text:span text:style-name="T11">Sumar la flotabilidad negativa. </text:span><text:span text:style-name="T10">La diferencia térmica se integra durante todo el descenso. Una parcela más fría que el ambiente y una capa más profunda producen una DCAPE mayor:</text:span></text:p></text:list-item>
</text:list>
<text:p text:style-name="P18">DCAPE = −R_d ∫[p_s, p_0] (T_e − T_p) d ln p</text:p>
<text:p text:style-name="P13">La implementación usa temperatura ordinaria, como params.dcape de SHARPpy, y una integración trapezoidal. Si SHARPpy no está disponible, MeteoLabX obtiene la temperatura de la parcela mediante la inversión vectorizada de θe saturada.</text:p>
<text:h text:style-name="P12" text:outline-level="2">Notas y limitaciones</text:h>
<text:p text:style-name="P13">DCAPE no contiene la cantidad real de precipitación, la carga de hidrometeoros, el arrastre de aire ambiental, la organización de la tormenta ni la transferencia completa del momento al suelo. Un valor alto fuera de una zona con precipitación puede no producir ninguna racha. Debe interpretarse junto con reflectividad o precipitación, humedad, gradiente térmico de niveles bajos, viento en altura y estructura convectiva.</text:p>
<text:p text:style-name="P14">Diagnóstico MeteoLabX · perfil termodinámico AROME</text:p>"""


def main():
    with ZipFile(SOURCE, "r") as source_zip:
        entries = source_zip.infolist()
        content = source_zip.read("content.xml").decode("utf-8")

        title = "DCAPE · Energía potencial de corrientes descendentes"
        next_title = "Movimiento de células ordinarias"
        title_pos = content.find(title)
        if title_pos < 0:
            raise SystemExit("No se encontró la sección DCAPE.")
        start = content.rfind("<text:h", 0, title_pos)
        next_pos = content.find(next_title, title_pos)
        if next_pos < 0:
            raise SystemExit("No se encontró el final de la sección DCAPE.")
        end = content.rfind("<text:h", title_pos, next_pos)
        if start < 0 or end <= start:
            raise SystemExit("No se pudo delimitar la sección DCAPE.")

        updated = (content[:start] + DCAPE_SECTION + content[end:]).encode("utf-8")

        with ZipFile(OUTPUT, "w") as output_zip:
            for entry in entries:
                data = updated if entry.filename == "content.xml" else source_zip.read(entry.filename)
                if entry.filename == "mimetype":
                    entry.compress_type = ZIP_STORED
                output_zip.writestr(entry, data)

    print(OUTPUT)


if __name__ == "__main__":
    main()
