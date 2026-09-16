/**
 * Reparto de los rótulos de ciudad sobre el mapa.
 *
 * Vive fuera del componente porque es la parte que se puede equivocar sola: a
 * qué nivel de zoom entra cada ciudad, dónde cae en la rejilla y cuál gana
 * cuando dos nombres se pisan.
 */

/**
 * Nivel de detalle según lo ampliado que esté el mapa.
 *
 * Con el dominio entero en pantalla ya entran las ciudades de tercer nivel,
 * pero solo las que quedan lejos unas de otras (ver `cityRoom`): sin ellas el
 * interior de la península, el sur de Francia o Alemania quedaban en blanco
 * entre capital y capital. Cada salto de zoom reparte el doble de superficie
 * por pantalla, así que va entrando la tanda siguiente. Los cortes no son
 * potencias de dos porque el reparto de sitio ya frena por su cuenta: esto
 * fija cuándo una ciudad merece rótulo, no cuántos caben.
 */
export function cityRank(zoomLevel) {
  if (zoomLevel < 2.8) return 3;
  if (zoomLevel < 3.8) return 4;
  if (zoomLevel < 5) return 5;
  return 6;
}

/**
 * Holgura que se exigen entre sí las ciudades secundarias.
 *
 * Las grandes (rango 1) se rotulan con el hueco justo. Las demás, con el mapa
 * lejos, piden más aire entre ellas: así rellenan los vacíos del mapa en vez
 * de apelotonarse alrededor del Ruhr o de Londres. La holgura se desvanece al
 * ampliar y a partir de 2,8 aumentos ya no pesa.
 */
export function cityRoom(zoomLevel) {
  return 1 + 0.8 * Math.min(1, Math.max(0, (2.8 - zoomLevel) / 1.8));
}

/**
 * Ciudades que caben en el encuadre, con el valor del campo en su celda.
 *
 * El valor sale de la celda que contiene la ciudad, sin interpolar: es el
 * mismo número que daría el globo del cursor puesto encima, y con celdas de
 * 2,5 km promediar vecinas emborronaría justo lo que distingue una costa de su
 * interior.
 *
 * Una ciudad sin valor no se rotula. El dominio no es un rectángulo lleno
 * —AROME deja esquinas sin datos y el recorte de un modelo no coincide con el
 * del catálogo de ciudades—, y un nombre suelto sobre un hueco en blanco no
 * dice nada.
 *
 * El reparto va por orden de importancia, que es el del catálogo: si dos
 * rótulos se pisan sobrevive el de la ciudad mayor.
 */
export function placeCities({
  catalogue, frame, bounds, viewZoom, labelScale, format, max = 60
}) {
  if (!frame?.bounds) return [];
  const [west, south, east, north] = frame.bounds;
  const limite = cityRank(viewZoom);
  // Los rótulos no se escalan con el zoom, así que el hueco que piden medido
  // en celdas encoge a medida que se amplía: es lo que hace que quepan más.
  const escala = labelScale / viewZoom;
  const altura = 34 * escala;
  const holgura = cityRoom(viewZoom);
  const puestas = [];
  for (const [name, latitude, longitude, rank] of catalogue) {
    if (rank > limite) continue;
    if (puestas.length >= max) break;
    const x = (longitude - west) / (east - west) * frame.width;
    const y = (north - latitude) / (north - south) * frame.height;
    if (x < bounds.west || x > bounds.east || y < bounds.north || y > bounds.south) continue;
    const column = Math.floor(x);
    const row = Math.floor(y);
    if (column < 0 || row < 0 || column >= frame.width || row >= frame.height) continue;
    const value = frame.values[row * frame.width + column];
    if (!Number.isFinite(value)) continue;
    // El nombre manda en el ancho: «Villanueva de la Serena» pide sitio muy
    // distinto de «Vic», y el valor de debajo siempre es más corto.
    const anchura = (name.length * 6.4 + 16) * escala;
    const secundaria = rank > 1;
    const choca = puestas.some((puesta) => {
      const aire = secundaria && puesta.secundaria ? holgura : 1;
      return Math.abs(puesta.x - x) < (puesta.anchura + anchura) / 2 * aire
        && Math.abs(puesta.y - y) < altura * aire;
    });
    if (choca) continue;
    puestas.push({ name, x, y, anchura, secundaria, text: format(value) });
  }
  return puestas;
}
