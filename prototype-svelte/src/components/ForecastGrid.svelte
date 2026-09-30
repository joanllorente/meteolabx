<script>
  import { Locate, Minus, Plus } from '@lucide/svelte';
  import {
    LUT_SIZE, anchorFraction, bandOfValue, bandPosition, defaultPalette,
    paletteStop, precipitationPalette, divergingPalette, thetaEPalette, windPalette,
  } from '../lib/palettes.js';
  import { contourLines, stepLevels } from '../lib/contours.js';
  import { CITY_DETAIL_ZOOM, placeCities } from '../lib/cityPlacement.js';
  import { troughAxes as detectTroughAxes, troughAxesLonLat as detectTroughAxesLonLat, troughAxesProjected as detectTroughAxesProjected } from '../lib/troughs.js';
  import { frameGeo } from '../lib/projection.js';
  import {
    CENTRE_PROMINENCE_HPA, pressureCentres as detectPressureCentres,
  } from '../lib/pressureCentres.js';
  import {
    STREAM_FADE, arrowHeadsPath, evenlySpacedStreamlines, fadeSegments, gridDirection, pathsByOpacity,
    sampleVectorField, streamlineArrows
  } from '../lib/streamlines.js';
  import { colorDeFondo, mezclaSobre, tintaLegible } from '../lib/ink.js';
  import { LAYERS, layerPreferences, toggleLayer } from '../lib/layerPreferences.svelte.js';
  import { forecastLayerLabel, forecastText } from '../lib/forecast-i18n.js';
  import { precipitationType } from '../data/precipitationTypes.js';

  // `formatProbe` trae ya la unidad elegida en la leyenda; sin ella se cae a la
  // que manda el backend en la cabecera del frame. `scaleBreaks` y `zeroFloor`
  // vienen del producto: con clases, el ráster deja de escalarse linealmente.
  // `scaleAnchors` hace lo mismo sin romper el degradado: reparte la rampa por
  // tramos en vez de a partes iguales por grado.
  let {
    frame, productLabel, language = 'es', resetKey = 0, formatProbe = null,
    colorPalette = '', vectorMinMagnitude = .5, vectorScaleMagnitude = 0,
    scaleBreaks = null, scaleAnchors = null, zeroFloor = 0,
    displayMin = null, displayMax = null, contourStep = 0, contourLayerId = 'isotherms', formatContour = null,
    nationalBoundariesOnly = false, overlayStep = 0, overlayMajorStep = 0,
    cityLabels = false,
    troughAxes = false, overlayLabel = '',
    // Viento dibujado como líneas de corriente en vez de flechas sueltas: se
    // ve el flujo entero y, con él, dónde converge.
    flowLines = false, flowMinMagnitude = 0,
    pressureCentres = false, multipleSolutions = false, overlaySmoothing = 4, overlayLayerLabel = '',
    // Ciclones con nombre: posición de la baja del modelo en el campo de
    // presión de esta hora, la misma en todos los mapas.
    stormLabels = [],
    onprofileclick = null,
    onink = null,
    // Encuadre guardado fuera del componente: `{ key, zoom, panX, panY }`.
    // La vista desmonta el mapa mientras llega el frame de la hora siguiente,
    // y sin esto cada cambio de hora devolvía el zoom al 100 %.
    savedView = null,
    onviewchange = null,
  } = $props();

  // Isotermas notables: la del cero es la que separa nieve de lluvia y helada
  // de no helada, así que va la primera y más gruesa; las decenas ordenan la
  // lectura del resto sin competir con ella.
  // Capas que este mapa puede ofrecer, y cuáles están encendidas ahora mismo.
  const availableLayers = $derived(LAYERS.filter((capa) => (
    capa.id === 'isotherms' || capa.id === 'snowContours' || capa.id === 'freezingContours'
      ? contourStep > 0 && capa.id === contourLayerId
      : capa.id === 'isohypses' ? overlayStep > 0
      : capa.id === 'troughs' ? troughAxes && Boolean(frame.overlay)
      : capa.id === 'cities' ? cityLabels
      : capa.id === 'multipleSolutions' ? multipleSolutions && Boolean(frame.overlay)
      : capa.id === 'liContours' ? showsIndexContours
      : pressureCentres && Boolean(frame.overlay)
  // La capa superpuesta se llama distinto según el campo: isohipsas en un
  // mapa de geopotencial e isobaras en uno de presión. El nombre alternativo
  // va por su propia clave de traducción, que si no el rótulo del panel se
  // queda en «isohipsas» en cuanto el idioma tiene traducción propia: el
  // respaldo solo entra cuando no la hay.
  )).map((capa) => (
    // El nombre de la capa superpuesta se traduce por su clave: antes todo lo
    // que declaraba un nombre propio salía como «Isobaras», también las
    // isentrópicas de la frontogénesis.
    capa.id === 'isohypses' && overlayLayerLabel
      ? { ...capa, labelKey: overlayLayerLabel === 'Isentrópicas' ? 'isentropes' : 'isobars', label: overlayLayerLabel }
      : capa
  )));
  const showValueContours = $derived(contourStep > 0 && layerPreferences[contourLayerId]);
  const showIsohypses = $derived(overlayStep > 0 && layerPreferences.isohypses);
  // Isopletas del índice superpuesto sin paso propio: el LI de los mapas de
  // CAPE. Son lo único que dibuja esa rama, así que su casilla sale solo ahí.
  const showsIndexContours = $derived(Boolean(frame.overlay) && !multipleSolutions && !(overlayStep > 0));
  const showLiContours = $derived(showsIndexContours && layerPreferences.liContours);

  // Ancho de rejilla con el que se ajustaron los rótulos: el dominio nativo de
  // AROME.
  const LABEL_REFERENCE_WIDTH = 1121;
  /**
   * Escala de todo lo que se midió en celdas y se mira en píxeles.
   *
   * La capa vectorial usa como unidad la celda del modelo, así que un modelo
   * con menos columnas la dibuja todo más grande: los 11 px del CSS son 11
   * celdas, y una celda de ECMWF ocupa en pantalla el doble que una de AROME.
   * Los rótulos salían del tamaño de un titular y el hueco de 230 unidades que
   * se reserva alrededor de cada uno —el 46 % del ancho de ese mapa— dejaba
   * casi todas las isobaras sin etiquetar. Referenciarlo al ancho de AROME
   * deja su aspecto intacto y hace que cualquier otro modelo mida lo mismo.
   */
  const labelScale = $derived(frame.width / LABEL_REFERENCE_WIDTH);
  // Ancho con que se pinta el mapa, en píxeles de pantalla.
  let surfaceWidth = $state(0);
  // Tamaño de los rótulos: celdas de la rejilla por píxel de pantalla, con un
  // poco de aumento. `labelScale` sirve para la geometría —separación de
  // isolíneas, anillos mínimos—, pero con él el texto encogía con el ancho
  // pintado: en Lambert el dominio de AROME es casi cuadrado, a la misma
  // altura se pinta al 60 % de ancho y las letras salían un 40 % más
  // pequeñas. Así miden lo que dice su CSS en cualquier proyección.
  const TEXT_ENLARGEMENT = 1.15;
  const textScale = $derived(
    surfaceWidth > 0 ? frame.width / surfaceWidth * TEXT_ENLARGEMENT : labelScale
  );

  /**
   * Tamaño de celda del modelo, en km.
   *
   * La detección de centros razona en kilómetros —200 de radio, 300 de
   * separación— y los convierte a celdas con esto. Estaba fijo en el 2,5 de
   * AROME, así que en una rejilla de 0,25° cada radio salía diez veces más
   * grande de lo que decía: un centro tenía que ganarle al entorno en 2.000 km
   * a la redonda y el mapa entero se quedaba en un solo anticiclón relativo.
   *
   * Se calcula con la misma regla con la que se fijó aquel 2,5 —un grado, unos
   * 100 km—, de modo que AROME sigue dando exactamente 2,5 y no se mueve nada
   * de lo que ya estaba ajustado sobre él.
   */
  // El respaldo evita una división por cero si un frame llegara sin límites:
  // la detección los usa como divisor y saldrían radios infinitos.
  // Un frame reproyectado a la LCC ya trae su celda en km.
  const cellKm = $derived(
    frame.cellKm || (frame.bounds?.[3] - frame.bounds?.[1]) / frame.height * 100 || 2.5
  );
  const geo = $derived(frameGeo(frame));
  // El engrosado previo buscaba bloques de unos 10 km, que es lo que valían
  // las cuatro celdas de AROME. Donde la celda ya mide más, no hay nada que
  // engrosar.
  const centreBlock = $derived(Math.max(1, Math.round(10 / cellKm)));
  const showTroughs = $derived(troughAxes && layerPreferences.troughs);
  const showCities = $derived(cityLabels && layerPreferences.cities);
  // El catálogo de ciudades son 160 kB: se trae aparte y solo cuando hace
  // falta, que hay mapas que no rotulan ninguna y no tienen por qué pagarlo
  // en la carga inicial.
  let cityCatalogue = $state([]);
  $effect(() => {
    if (!showCities || cityCatalogue.length) return;
    import('../data/cityLabels.js').then((modulo) => {
      cityCatalogue = modulo.CITY_LABELS;
    });
  });
  // Los núcleos pequeños, aparte y solo con el zoom a fondo. Van detrás del
  // catálogo principal: el orden decide quién gana cuando dos se pisan.
  let cityDetail = $state([]);
  $effect(() => {
    if (!showCities || viewZoom < CITY_DETAIL_ZOOM || cityDetail.length) return;
    import('../data/cityLabelsDetail.js').then((modulo) => {
      cityDetail = modulo.CITY_LABELS_DETAIL;
    });
  });
  const cityRows = $derived(cityDetail.length ? [...cityCatalogue, ...cityDetail] : cityCatalogue);
  const showCentres = $derived(pressureCentres && layerPreferences.centres);
  const showMultipleSolutions = $derived(multipleSolutions && layerPreferences.multipleSolutions);
  const profileHint = $derived(({
    es: 'Pulsa para ver el perfil', ca: 'Prem per veure el perfil',
    en: 'Click to view the profile', fr: 'Cliquer pour voir le profil',
    de: 'Klicken für das Profil', it: 'Clicca per vedere il profilo',
    pt: 'Clica para ver o perfil'
  })[language] || 'Pulsa para ver el perfil');

  const EMPHASISED_LEVELS = [0, 10, 20, 30];

  function emphasis(level) {
    if (level === 0) return 2;
    return EMPHASISED_LEVELS.includes(level) ? 1 : 0;
  }

  // El buffer de ImageData es RGBA en orden de memoria; escribirlo como Uint32
  // exige conocer el orden de bytes de la máquina para componer la paleta.
  const littleEndian = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
  const lutCache = new Map();
  let layer;
  let surface;
  let raster;
  let multipleRaster = $state();
  let hover = $state(null);
  // Hueco que necesita la tarjeta del cursor, con su separación: en píxeles.
  const TOOLTIP_ROOM = { alto: 125, ancho: 200 };
  let zoom = $state(1);
  let panX = $state(0);
  let panY = $state(0);
  let dragging = $state(false);
  let dragStart = null;
  // Encuadre asentado: alimenta los cálculos caros (flechas, streamlines) y
  // solo sigue al gesto cuando este se detiene.
  let viewZoom = $state(1);
  let viewPanX = $state(0);
  let viewPanY = $state(0);
  let dragFrame = 0;
  let settleTimer = 0;
  let pendingPan = null;
  const activePointers = new Map();
  let pinchDistance = 0;

  function settleViewport() {
    window.clearTimeout(settleTimer);
    settleTimer = 0;
    viewZoom = zoom;
    viewPanX = panX;
    viewPanY = panY;
  }

  function scheduleSettle() {
    window.clearTimeout(settleTimer);
    settleTimer = window.setTimeout(settleViewport, 160);
  }

  function packColor(red, green, blue, alpha) {
    return littleEndian
      ? ((alpha << 24) | (blue << 16) | (green << 8) | red) >>> 0
      : ((red << 24) | (green << 16) | (blue << 8) | alpha) >>> 0;
  }

  /** Paleta interpolada a 256 entradas: evita releer los hex por píxel. */
  function paletteLut(palette, alpha) {
    const key = `${palette[0]}|${alpha}`;
    const cached = lutCache.get(key);
    if (cached) return cached;
    const lut = new Uint32Array(LUT_SIZE);
    for (let index = 0; index < LUT_SIZE; index += 1) {
      const [red, green, blue] = paletteStop(palette, index);
      lut[index] = packColor(red, green, blue, alpha);
    }
    lutCache.set(key, lut);
    return lut;
  }

  /**
   * Un color por clase, repartidos por toda la paleta.
   *
   * La rampa se muestrea en tantos puntos como clases haya, así que las clases
   * conservan el orden y el contraste de la paleta continua sin que haya que
   * mantener una lista de colores aparte.
   */
  function bandColors(palette, count, alpha) {
    const lut = paletteLut(palette, alpha);
    const bands = new Uint32Array(count);
    for (let index = 0; index < count; index += 1) {
      bands[index] = lut[bandPosition(index, count)];
    }
    return bands;
  }

  // Recuadro de la esquina inferior izquierda del mapa, que es donde va la
  // marca de agua: 14 px desde el borde izquierdo y 13 desde el de abajo, con
  // el alto de sus dos líneas y el logo.
  const MARCA = { izquierda: 14, abajo: 13, ancho: 210, alto: 30 };

  /**
   * Color legible sobre lo que haya debajo de la marca de agua.
   *
   * La marca se imprime encima del campo, y el campo cambia de color con la
   * hora, con el producto y con el encuadre: un tono fijo se pierde tarde o
   * temprano contra un fondo del mismo valor. Se mide lo que hay debajo y se
   * devuelve blanco o negro, que es lo que más contraste da contra cualquier
   * cosa.
   *
   * Se muestrea el propio ráster porque es el color que se ve. Donde no llega
   * —con el mapa sin ampliar la esquina cae fuera de la rejilla— manda el
   * fondo del contenedor, y donde el campo es translúcido se mezclan los dos.
   */
  function tintaDeLaMarca() {
    const area = layer?.parentElement;
    if (!raster || !area) return null;
    const areaCaja = area.getBoundingClientRect();
    const rasterCaja = raster.getBoundingClientRect();
    if (!rasterCaja.width || !rasterCaja.height) return null;
    const fondo = colorDeFondo(getComputedStyle(area).backgroundColor);

    const muestras = [];
    for (let fila = 0; fila < 3; fila += 1) {
      for (let columna = 0; columna < 7; columna += 1) {
        const x = areaCaja.left + MARCA.izquierda + (columna + 0.5) * MARCA.ancho / 7;
        const y = areaCaja.bottom - MARCA.abajo - MARCA.alto + (fila + 0.5) * MARCA.alto / 3;
        muestras.push(colorEnPantalla(x, y, rasterCaja, fondo));
      }
    }
    return tintaLegible(muestras);
  }

  /** Color del ráster en un punto de la pantalla, mezclado con el fondo. */
  function colorEnPantalla(x, y, rasterCaja, fondo) {
    if (x < rasterCaja.left || x >= rasterCaja.right || y < rasterCaja.top || y >= rasterCaja.bottom) {
      return fondo;
    }
    const columna = Math.floor((x - rasterCaja.left) / rasterCaja.width * raster.width);
    const fila = Math.floor((y - rasterCaja.top) / rasterCaja.height * raster.height);
    try {
      const [r, g, b, a] = raster.getContext('2d').getImageData(columna, fila, 1, 1).data;
      return mezclaSobre([r, g, b, a / 255], fondo);
    } catch {
      return fondo;
    }
  }

  function renderGrid() {
    if (!frame || !raster) return;
    const { width, height, values } = frame;
    raster.width = width;
    raster.height = height;
    const context = raster.getContext('2d', { alpha: true });
    context.imageSmoothingEnabled = false;
    const pixels = context.createImageData(width, height);
    const canvas32 = new Uint32Array(pixels.data.buffer);
    if (frame.product === 'precip-type') {
      const colors = new Map();
      for (let index = 0; index < values.length; index += 1) {
        const type = precipitationType(values[index]);
        // Donde no precipita no se pinta: con el gris de «sin precipitación»
        // encima, el mapa entero quedaba de un color y no se veía dónde acaba
        // la tierra y empieza el mar. El globo del cursor lo sigue diciendo.
        if (!type || type.code === 0) continue;
        if (!colors.has(type.code)) {
          const hex = type.color.slice(1);
          colors.set(type.code, packColor(
            parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16),
            parseInt(hex.slice(4, 6), 16), 235
          ));
        }
        canvas32[index] = colors.get(type.code);
      }
      context.putImageData(pixels, 0, 0);
      return;
    }
    const isPrecipitation = frame.product === 'precip-1h';
    const last = LUT_SIZE - 1;
    const palette = colorPalette === 'diverging' ? divergingPalette
      : colorPalette === 'theta-e' ? thetaEPalette
      : colorPalette === 'wind' ? windPalette
      : isPrecipitation || scaleBreaks?.length
      ? precipitationPalette
      : defaultPalette;
    const lut = paletteLut(palette, 235);
    // Precipitación: escala logarítmica para no aplastar las lluvias débiles.
    const logScale = isPrecipitation ? last / Math.log1p(frame.vmax) : 0;
    const breaks = scaleBreaks?.length ? scaleBreaks : null;
    const bands = breaks ? bandColors(palette, breaks.length + 1, 235) : null;
    // El rango de color es de presentación, igual que la paleta o las clases:
    // lo fija el producto y la cabecera del frame solo hace de respaldo. Si
    // mandara la cabecera, cambiar una escala no se vería hasta que la pasada
    // siguiente reemplazara todos los frames guardados, y mientras tanto la
    // leyenda estaría rotulando una escala que el mapa no usa.
    // El suelo solo se aplica si el producto pide dejar el cero sin pintar. Un
    // umbral de 0 «por defecto» descartaría el campo entero de cualquier mapa
    // con valores negativos: la temperatura de 500 hPa, la velocidad vertical
    // en el NCL, la helicidad o el CIN no tienen ni una celda positiva.
    const floor = zeroFloor > 0 ? zeroFloor : -Infinity;
    const low = Number.isFinite(displayMin) ? displayMin : frame.vmin;
    const high = Number.isFinite(displayMax) ? displayMax : frame.vmax;
    const linearScale = last / (high - low || 1);
    const anchors = !breaks && scaleAnchors?.length > 1 ? scaleAnchors : null;
    for (let index = 0; index < values.length; index += 1) {
      const value = values[index];
      if (!Number.isFinite(value)) continue;
      if (value < floor) continue;
      if (bands) {
        canvas32[index] = bands[bandOfValue(value, breaks)];
      } else if (isPrecipitation) {
        if (value < .05) continue;
        const slot = Math.log1p(value) * logScale;
        canvas32[index] = lut[slot > last ? last : slot < 0 ? 0 : slot | 0];
      } else if (anchors) {
        canvas32[index] = lut[(anchorFraction(value, anchors) * last) | 0];
      } else {
        const slot = (value - low) * linearScale;
        canvas32[index] = lut[slot > last ? last : slot < 0 ? 0 : slot | 0];
      }
    }
    context.putImageData(pixels, 0, 0);
  }

  function renderMultipleSolutions() {
    if (!multipleSolutions || !multipleRaster || !frame.overlay) return;
    const { width, height } = frame;
    multipleRaster.width = width;
    multipleRaster.height = height;
    const context = multipleRaster.getContext('2d', { alpha: true });
    const pixels = context.createImageData(width, height);
    for (let index = 0; index < frame.overlay.length; index += 1) {
      if (!(frame.overlay[index] >= 0.5)) continue;
      const x = index % width;
      const y = Math.floor(index / width);
      const offset = index * 4;
      pixels.data[offset] = 255;
      pixels.data[offset + 1] = 174;
      pixels.data[offset + 2] = 42;
      pixels.data[offset + 3] = (x + y) % 7 < 2 ? 205 : 75;
    }
    context.putImageData(pixels, 0, 0);
  }

  function makeBoundaryPaths() {
    if (!frame.boundaries?.length) return [];
    const paths = [];
    // El servidor recorta costas y fronteras a un recuadro de latitud y
    // longitud, y el recorte cierra cada polígono por el borde del recuadro.
    // En latitud y longitud ese cierre caía justo en el borde del mapa; en una
    // LCC el recuadro es un abanico que cruza la vista, y los cierres salían
    // como diagonales sueltas. Los tramos que corren a lo largo del borde del
    // recorte —sus dos extremos en el mismo lado— no se dibujan.
    let oeste = Infinity, este = -Infinity, sur = Infinity, norte = -Infinity;
    for (const region of frame.boundaries) {
      for (const ring of region.rings) {
        for (const [longitude, latitude] of ring) {
          if (longitude < oeste) oeste = longitude;
          if (longitude > este) este = longitude;
          if (latitude < sur) sur = latitude;
          if (latitude > norte) norte = latitude;
        }
      }
    }
    const tolerancia = 1e-6;
    const enBorde = ([lonA, latA], [lonB, latB]) => (
      (Math.abs(lonA - oeste) < tolerancia && Math.abs(lonB - oeste) < tolerancia)
      || (Math.abs(lonA - este) < tolerancia && Math.abs(lonB - este) < tolerancia)
      || (Math.abs(latA - sur) < tolerancia && Math.abs(latB - sur) < tolerancia)
      || (Math.abs(latA - norte) < tolerancia && Math.abs(latB - norte) < tolerancia)
    );
    const punto = ([longitude, latitude]) => {
      const [x, y] = geo.toGrid(longitude, latitude);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    };
    for (const region of frame.boundaries) {
      // Los mapas con isolíneas propias se quedan solo con costas y fronteras
      // nacionales: sobre un campo ya cruzado de isotermas, las divisiones
      // interiores compiten con ellas y no aportan nada a la lectura.
      if (nationalBoundariesOnly && (region.level || 'country') !== 'country') continue;
      for (const ring of region.rings) {
        if (ring.length < 2) continue;
        // El anillo cerrado, tramo a tramo, empezando un trazo nuevo después
        // de cada tramo de borde que se salta.
        const cerrado = ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1] ? ring : [...ring, ring[0]];
        let trazo = '';
        let abierto = false;
        for (let i = 0; i < cerrado.length - 1; i += 1) {
          if (enBorde(cerrado[i], cerrado[i + 1])) {
            abierto = false;
            continue;
          }
          if (!abierto) trazo += `M${punto(cerrado[i])}`;
          trazo += `L${punto(cerrado[i + 1])}`;
          abierto = true;
        }
        if (trazo) paths.push({ path: trazo, level: region.level || 'country' });
      }
    }
    // Las divisiones interiores se dibujan primero —de la más fina a la más
    // gruesa— para que en un límite compartido mande la de más rango: la
    // frontera nacional conserva su grosor y color en costas y límites
    // internacionales, y la de comunidad no queda tapada por la de provincia.
    const orden = { admin2: 0, admin1: 1, country: 2 };
    return paths.sort((left, right) => (orden[left.level] ?? 1) - (orden[right.level] ?? 1));
  }

  function visibleSourceBounds(margen = .3) {
    const renderedWidth = surface?.clientWidth || frame.width;
    const renderedHeight = surface?.clientHeight || frame.height;
    // Se usa el encuadre asentado, no el del gesto en curso: integrar las
    // líneas de corriente en cada pointermove bloquea el hilo principal.
    const userPanX = viewPanX * frame.width / renderedWidth;
    const userPanY = viewPanY * frame.height / renderedHeight;
    const centerX = frame.width / 2;
    const centerY = frame.height / 2;
    const sourceX = (screenX) => centerX + (screenX - userPanX - centerX) / viewZoom;
    const sourceY = (screenY) => centerY + (screenY - userPanY - centerY) / viewZoom;
    // El margen mantiene glifos ya calculados fuera de cuadro, de modo que el
    // arrastre no descubre zonas vacías antes de que el encuadre se asiente.
    const marginX = frame.width * margen / viewZoom;
    const marginY = frame.height * margen / viewZoom;
    return {
      west: Math.max(0, sourceX(0) - marginX),
      east: Math.min(frame.width, sourceX(frame.width) + marginX),
      north: Math.max(0, sourceY(0) - marginY),
      south: Math.min(frame.height, sourceY(frame.height) + marginY)
    };
  }

  function clusteredVector(west, north, east, south) {
    const firstColumn = Math.max(0, Math.floor(west));
    const lastColumn = Math.min(frame.width - 1, Math.ceil(east));
    const firstRow = Math.max(0, Math.floor(north));
    const lastRow = Math.min(frame.height - 1, Math.ceil(south));
    const sampleStride = Math.max(1, Math.floor(Math.max(east - west, south - north) / 6));
    let sumU = 0;
    let sumV = 0;
    let sumX = 0;
    let sumY = 0;
    let count = 0;
    for (let row = firstRow; row <= lastRow; row += sampleStride) {
      for (let column = firstColumn; column <= lastColumn; column += sampleStride) {
        const index = row * frame.width + column;
        const u = frame.u[index];
        const v = frame.v[index];
        if (!Number.isFinite(frame.values[index]) || !Number.isFinite(u) || !Number.isFinite(v)) continue;
        sumU += u;
        sumV += v;
        sumX += column + .5;
        sumY += row + .5;
        count += 1;
      }
    }
    if (!count) return null;
    // La media se hace con las componentes físicas; solo el ángulo que se
    // dibuja pasa a la rejilla, corregido por la latitud del grupo.
    const x = sumX / count;
    const y = sumY / count;
    const { u, v, magnitude } = gridDirection(frame, sumU / count, sumV / count, y);
    if (magnitude < vectorMinMagnitude) return null;
    return { x, y, angle: Math.atan2(-v, u) * 180 / Math.PI, magnitude };
  }

  function makeArrowGlyphs() {
    if (!frame.u || !frame.v || flowLines) return { arrows: [], path: '' };
    const arrows = [];
    // Mantiene una densidad visual estable tanto en el dominio AROME completo
    // como al ampliar una región: unas 26 agrupaciones a lo ancho del visor.
    const baseStep = Math.max(4, frame.width / 26);
    const sourceStep = baseStep / viewZoom;
    const bounds = visibleSourceBounds();
    const firstColumn = Math.floor(bounds.west / sourceStep) * sourceStep;
    const firstRow = Math.floor(bounds.north / sourceStep) * sourceStep;
    for (let top = firstRow; top < bounds.south; top += sourceStep) {
      for (let left = firstColumn; left < bounds.east; left += sourceStep) {
        const vector = clusteredVector(left, top, left + sourceStep, top + sourceStep);
        if (vector) arrows.push(vector);
      }
    }
    const length = Math.max(3.8, baseStep * .58);
    const arrowPath = (size) => {
      const half = size / 2;
      const head = Math.min(size * .3, length * .3);
      return `M${(-half).toFixed(2)},0L${half.toFixed(2)},0M${(half - head).toFixed(2)},${(-head * .62).toFixed(2)}L${half.toFixed(2)},0L${(half - head).toFixed(2)},${(head * .62).toFixed(2)}`;
    };
    // Donde el módulo dice algo —vectores Q—, la flecha crece con él; el
    // viento, en cambio, ya lleva la intensidad en el color.
    if (vectorScaleMagnitude > 0) {
      for (const arrow of arrows) {
        arrow.path = arrowPath(length * Math.max(.3, Math.min(1, arrow.magnitude / vectorScaleMagnitude)));
      }
    }
    return { arrows, path: arrowPath(length) };
  }

  function sampleVector(x, y) {
    const muestra = sampleVectorField(frame, x, y);
    // Un umbral propio corta las líneas donde el viento no llega: en el jet
    // stream quedan solo sobre el jet, donde está el color.
    return muestra && muestra.magnitude >= flowMinMagnitude ? muestra : null;
  }



  const boundaryPaths = $derived(makeBoundaryPaths());

  /**
   * Relleno de tierra, debajo del campo.
   *
   * En los mapas que solo colorean donde pasa algo —precipitación, nieve,
   * tipo de precipitación— el resto del ráster es transparente, y tierra y mar
   * salían del mismo gris: sin costa a la vista no se sabía dónde acababa el
   * continente. Se rellenan los países con un tono cálido y neutro que no usa
   * ninguna paleta, así que no se confunde con un valor del campo.
   *
   * A diferencia de las fronteras, aquí sí hacen falta los cierres por el
   * borde del recorte: sin ellos el polígono no encierra nada. En una LCC ese
   * borde es un paralelo que se curva, así que los tramos largos se parten
   * para que el cierre siga la curva y no corte el mapa en diagonal.
   */
  function makeLandPath() {
    if (!frame.boundaries?.length) return '';
    let trazo = '';
    const punto = (longitude, latitude) => {
      const [x, y] = geo.toGrid(longitude, latitude);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    };
    for (const region of frame.boundaries) {
      if ((region.level || 'country') !== 'country') continue;
      for (const ring of region.rings) {
        if (ring.length < 3) continue;
        trazo += `M${punto(ring[0][0], ring[0][1])}`;
        for (let i = 1; i <= ring.length; i += 1) {
          const [lonA, latA] = ring[i - 1];
          const [lonB, latB] = ring[i % ring.length];
          const pasos = Math.min(40, Math.ceil(Math.max(Math.abs(lonB - lonA), Math.abs(latB - latA)) / 0.25));
          for (let paso = 1; paso <= pasos; paso += 1) {
            const t = paso / pasos;
            trazo += `L${punto(lonA + (lonB - lonA) * t, latA + (latB - latA) * t)}`;
          }
        }
        trazo += 'Z';
      }
    }
    return trazo;
  }
  const landPath = $derived(makeLandPath());

  /**
   * Celdas con dato, como máscara de la tierra.
   *
   * No todos los campos cubren el dominio entero: el tipo de precipitación de
   * AROME, por ejemplo, deja sin dato una franja al este y otra al oeste. Ahí
   * el ráster es transparente, y sin recortar asomaba la tierra en beige donde
   * el modelo no dice nada. Con la máscara, fuera del campo vuelve a verse el
   * fondo liso. Si todas las celdas tienen dato no hace falta.
   */
  function makeDataMask() {
    const { width, height, values } = frame;
    if (!values?.length || typeof document === 'undefined') return '';
    let huecos = false;
    for (let index = 0; index < values.length; index += 1) {
      if (!Number.isFinite(values[index])) { huecos = true; break; }
    }
    if (!huecos) return '';
    const lienzo = document.createElement('canvas');
    lienzo.width = width;
    lienzo.height = height;
    const contexto = lienzo.getContext('2d');
    const pixels = contexto.createImageData(width, height);
    const blanco32 = new Uint32Array(pixels.data.buffer);
    for (let index = 0; index < values.length; index += 1) {
      if (Number.isFinite(values[index])) blanco32[index] = 0xffffffff;
    }
    contexto.putImageData(pixels, 0, 0);
    return lienzo.toDataURL('image/png');
  }
  const dataMask = $derived(landPath ? makeDataMask() : '');
  const uid = $props.id();
  const maskId = `land-mask-${uid}`;
  const clipId = `land-clip-${uid}`;
  // Provincias, départements y demás solo con el zoom alto: con el mapa
  // entero son una maraña que tapa el campo, y a partir de aquí la pantalla
  // cubre media comunidad y ya no queda ninguna referencia entre fronteras.
  // Se filtra al pintar, no al construir los trazos, para no rehacerlos en
  // cada cambio de zoom.
  const ADMIN2_ZOOM = 4;
  const visibleBoundaryPaths = $derived(
    viewZoom >= ADMIN2_ZOOM ? boundaryPaths : boundaryPaths.filter((item) => item.level !== 'admin2')
  );

  function makeStreamlinePaths() {
    if (!frame.u || !frame.v || !flowLines) {
      return { particles: '', layers: [], arrows: '' };
    }
    // Separación entre líneas vecinas: el reparto la respeta por su cuenta,
    // así que esto fija la densidad del mapa y no dónde empieza cada línea.
    const separacion = Math.max(4.5, frame.width / 55) / viewZoom;
    const lineas = evenlySpacedStreamlines({
      sample: sampleVector,
      bounds: visibleSourceBounds(),
      separation: separacion,
      step: separacion / 4
    });
    const trazo = (puntos) => `M${puntos.map(([px, py]) => `${px.toFixed(2)},${py.toFixed(2)}`).join('L')}`;
    // En píxeles de pantalla a zoom 1; en la rejilla, dividido por el zoom.
    const markerSize = Math.max(2.4, separacion * viewZoom * 0.16);
    // Pocos trazos grandes en vez de un nodo por tramo y por flecha: con miles
    // de nodos el mapa no se podía ni arrastrar.
    return {
      // Las líneas enteras, para la animación de partículas.
      particles: lineas.map((linea) => trazo(linea.points)).join(''),
      // Y partidas en tramos con su tinta, para que las puntas se desvanezcan
      // en vez de cortarse en seco a media pantalla.
      layers: pathsByOpacity(
        lineas.flatMap((linea) => fadeSegments(linea.points, { fade: separacion * STREAM_FADE })),
        trazo
      ),
      // Una flecha cada dos separaciones y media: bastantes para seguir el
      // sentido sin que dos caigan sobre el mismo tramo de línea.
      arrows: arrowHeadsPath(
        lineas.flatMap((linea) => streamlineArrows(linea.points, separacion * 2.5)),
        markerSize / viewZoom
      )
    };
  }

  const arrowGlyphs = $derived(makeArrowGlyphs());
  const streamlineData = $derived(makeStreamlinePaths());
  // El contorno del índice superpuesto va crudo, como estaba: es una línea de
  // referencia sobre un campo ya suave, no el dibujo principal del mapa.
  const contourPaths = $derived.by(() => {
    if (!frame.overlay || multipleSolutions) return [];
    if (overlayStep > 0) {
      return contourLines(frame.overlay, {
        width: frame.width,
        height: frame.height,
        levels: stepLevels(frame.overlay, overlayStep),
        // Suavizado de dibujo configurado por producto y resolución.
        // El campo original sigue disponible para las consultas y centros.
        sigma: overlaySmoothing,
        // En celdas, todos: un anillo o un tramo mínimo que valen para 2,5 km
        // piden diez veces más recorrido real en una rejilla de 25.
        minRingArea: 40 * labelScale * labelScale,
        // La mitad que antes: la simplificación es de dibujo, no de dato, y
        // desde que la isolínea se traza como curva los vértices de más no
        // ensucian nada —la acercan al contorno calculado— mientras que los
        // de menos dejaban cuerdas rectas de veinte celdas entre esquinas.
        // En rejillas globales, escalar solo por el ancho retenía vértices
        // casi coincidentes y sus pequeños ganchos entre curvas Bézier.
        tolerance: overlaySmoothing > 0
          ? (pressureCentres ? Math.max(0.35, 0.8 * labelScale) : 0.8 * labelScale)
          : 0.4 * labelScale,
        labelMinLength: 90 * labelScale,
        labelSpacing: 70 * labelScale
      });
    }
    return contourLines(frame.overlay, {
      width: frame.width,
      height: frame.height,
      levels: [-10, -8, -6, -4, -2, 0, 2, 4],
      sigma: 0,
      minRingArea: 0,
      tolerance: 0,
      // Rotuladas: sin el número no se sabe si una línea es el −2 o el −6.
      // Solo en los tramos largos, que el campo crudo deja muchos pedazos.
      labelMinLength: 60 * labelScale,
      labelSpacing: 70 * labelScale
    });
  });

  /**
   * Grosor de las isohipsas según el encuadre.
   *
   * Fijo no sirve: el que se lee con el dominio entero en pantalla se
   * convierte en un chorizo al ampliar, y el que queda fino al ampliar obliga
   * a mirar con lupa de lejos. Se estrecha conforme se acerca, y la línea
   * principal mantiene siempre su ventaja sobre la secundaria.
   */
  const overlayWidth = $derived.by(() => {
    const cerca = Math.min(1, Math.log2(Math.max(1, viewZoom)) / 2);
    return {
      normal: 0.95 - 0.3 * cerca,
      fuerte: 1.45 - 0.45 * cerca
    };
  });

  // Ejes de vaguada y depresiones cerradas del campo superpuesto. Solo donde
  // el producto lo pide: es un análisis de escala sinóptica y en 850 hPa, con
  // el relieve metido en el campo, no dice lo mismo.
  const troughs = $derived(
    troughAxes && frame.overlay
      // Con el paso de las isohipsas dibujadas: una baja rodeada por una de
      // ellas es circulación cerrada aunque sea somera, y no lleva eje.
      // En ECMWF el campo se reproyecta antes a una rejilla de 25 km reales:
      // su dominio llega a 75° N, donde una celda de 0,25° es cuatro veces más
      // alta que ancha, y el detector, que mide en celdas, veía las ondas del
      // norte aplastadas. AROME sigue como estaba ajustado.
      ? (frame.lcc && cellKm >= 10
        ? detectTroughAxesProjected(frame.overlay, {
            width: frame.width, height: frame.height, cellKm,
            latitude: frame.lcc.latitude, sign: frame.lcc.sign, contourStep: overlayStep
          })
        : cellKm >= 10
        ? detectTroughAxesLonLat(frame.overlay, {
            width: frame.width, height: frame.height, bounds: frame.bounds,
            contourStep: overlayStep
          })
        : detectTroughAxes(frame.overlay, {
            width: frame.width, height: frame.height, contourStep: overlayStep
          }))
      : { axes: [], lows: [] }
  );

  /**
   * Trazo del eje como curva, no como poligonal.
   *
   * Catmull-Rom pasada a Bézier: la línea pasa por todos los vértices y llega
   * a cada uno con la pendiente del anterior al siguiente, así que no quedan
   * esquinas entre isohipsa e isohipsa.
   */
  /**
   * Bajas y anticiclones del campo superpuesto.
   *
   * El criterio físico es estable al ampliar: el zoom no añade mínimos
   * débiles ni cambia la clasificación del mismo campo.
   */
  const centres = $derived(
    showCentres && frame.overlay
      ? detectPressureCentres(frame.overlay, {
          width: frame.width,
          height: frame.height,
          cellKm,
          block: centreBlock,
          prominenceHpa: CENTRE_PROMINENCE_HPA
        })
      : []
  );

  const storms = $derived.by(() => {
    if (!stormLabels?.length) return [];
    return stormLabels
      .map((storm) => {
        const [x, y] = geo.toGrid(storm.longitude, storm.latitude);
        return { ...storm, x, y, text: storm.stage === 'tropical' ? storm.name : `ex-${storm.name}` };
      })
      .filter((storm) => storm.x >= 0 && storm.y >= 0 && storm.x <= frame.width && storm.y <= frame.height);
  });

  function axisPath(axis) {
    if (axis.length < 3) {
      return `M${axis.map((punto) => `${punto.x.toFixed(1)},${punto.y.toFixed(1)}`).join('L')}`;
    }
    let trazo = `M${axis[0].x.toFixed(1)},${axis[0].y.toFixed(1)}`;
    for (let index = 0; index < axis.length - 1; index += 1) {
      const previo = axis[Math.max(0, index - 1)];
      const desde = axis[index];
      const hasta = axis[index + 1];
      const siguiente = axis[Math.min(axis.length - 1, index + 2)];
      const c1x = desde.x + (hasta.x - previo.x) / 6;
      const c1y = desde.y + (hasta.y - previo.y) / 6;
      const c2x = hasta.x - (siguiente.x - desde.x) / 6;
      const c2y = hasta.y - (siguiente.y - desde.y) / 6;
      trazo += `C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${hasta.x.toFixed(1)},${hasta.y.toFixed(1)}`;
    }
    return trazo;
  }

  function isMajorOverlay(level) {
    if (!(overlayMajorStep > 0)) return false;
    // El nivel viene de un múltiplo exacto del paso, pero el redondeo del
    // trazado deja restos: se compara con holgura.
    return Math.abs(level / overlayMajorStep - Math.round(level / overlayMajorStep)) < 1e-6;
  }
  // Isolíneas del propio campo, discontinuas y en marrón cálido para no
  // confundirlas con las fronteras ni con el contorno del índice superpuesto.
  const valueContours = $derived(
    contourStep > 0
      ? contourLines(frame.values, {
          width: frame.width,
          height: frame.height,
          levels: stepLevels(frame.values, contourStep)
        })
      : []
  );

  /**
   * Trazo discontinuo que se nota a cualquier escala.
   *
   * El patrón va en píxeles de pantalla —el trazo no se escala—, así que uno
   * fino se lee como línea continua cuando el mapa está entero en el visor: no
   * hay bastante recorrido en pantalla para que el ojo distinga los huecos.
   * Se parte de una raya larga y se acorta al ampliar, que es cuando sobra
   * longitud y una raya corta ya se aprecia.
   */
  const contourDash = $derived.by(() => {
    const cerca = Math.min(1, Math.log2(Math.max(1, viewZoom)) / 2);
    const raya = 7.4 - 2.6 * cerca;
    const hueco = 4.4 - 1.5 * cerca;
    return {
      normal: `${raya.toFixed(2)} ${hueco.toFixed(2)}`,
      fuerte: `${(raya * 1.15).toFixed(2)} ${hueco.toFixed(2)}`,
      cero: `${(raya * 1.4).toFixed(2)} ${(hueco * 0.95).toFixed(2)}`
    };
  });

  function dashOf(level) {
    const grado = emphasis(level);
    return grado === 2 ? contourDash.cero : grado === 1 ? contourDash.fuerte : contourDash.normal;
  }

  /**
   * Etiquetas de las isolíneas, con separación entre ellas.
   *
   * Se colocan sobre el encuadre asentado y solo dentro de lo que se está
   * mirando: al ampliar aparecen más, porque cabe más rótulo por pantalla. El
   * orden empieza por las líneas destacadas, de modo que si el sitio escasea
   * las que sobreviven son las de 0, 10, 20 y 30.
   */
  /**
   * Reparte todos los rótulos del mapa en un solo sistema.
   *
   * Isotermas e isohipsas comparten sitio, así que tienen que repartírselo
   * juntas: con un reparto por familia, cada una evitaba sus propios rótulos y
   * los dos acababan impresos uno encima del otro.
   *
   * Primero una etiqueta por línea, para que ninguna se quede sin nombre
   * mientras otra acumula seis; después se rellena con lo que quepa. El orden
   * lo marca la prioridad, de modo que si el sitio escasea sobreviven las
   * líneas que más dicen: isohipsa principal, isoterma destacada, y el resto.
   * Cada línea empieza a probar por un punto distinto porque los candidatos
   * van en orden de barrido y arrancar todas por el primero amontonaría los
   * rótulos en el norte del mapa.
   */
  function placeLabels(groups, max = 52) {
    const candidatas = [];
    for (const group of groups) {
      for (const contour of group.contours) {
        candidatas.push({ ...group, level: contour.level, anchors: contour.anchors });
      }
    }
    if (!candidatas.length) return [];
    const bounds = visibleSourceBounds();
    candidatas.sort((izquierda, derecha) => derecha.priority(derecha.level) - izquierda.priority(izquierda.level));
    const puestas = [];
    const cuenta = new Map();

    const intentar = (candidata, orden, tope) => {
      const { level, anchors, kind, format, gapX, gapY } = candidata;
      const clave = `${kind}:${level}`;
      if (!anchors?.length) return;
      if ((cuenta.get(clave) || 0) >= tope || puestas.length >= max) return;
      const inicio = Math.floor(anchors.length * orden) % anchors.length;
      for (let paso = 0; paso < anchors.length; paso += 1) {
        const [x, y] = anchors[(inicio + paso) % anchors.length];
        if (x < bounds.west || x > bounds.east || y < bounds.north || y > bounds.south) continue;
        // El hueco exigido es la media de lo que pide cada uno: «560 dam» ocupa
        // bastante más que «10°C» y no puede medirse con la misma vara.
        const choca = puestas.some((item) => (
          Math.abs(item.x - x) < (item.gapX + gapX) / 2
          && Math.abs(item.y - y) < (item.gapY + gapY) / 2
        ));
        if (choca) continue;
        puestas.push({ x, y, level, kind, gapX, gapY, text: format(level) });
        cuenta.set(clave, (cuenta.get(clave) || 0) + 1);
        return;
      }
    };

    candidatas.forEach((candidata, index) =>
      intentar(candidata, index / candidatas.length, 1)
    );
    for (let vuelta = 2; vuelta <= 5; vuelta += 1) {
      candidatas.forEach((candidata, index) =>
        intentar(candidata, (index + vuelta * 0.37) / candidatas.length, vuelta)
      );
    }
    return puestas;
  }

  const mapLabels = $derived.by(() => {
    const groups = [];
    if (overlayStep > 0 && showIsohypses && formatContour) {
      groups.push({
        kind: 'height',
        contours: contourPaths,
        format: (level) => `${level} ${frame.overlay_unit || ''}`.trim(),
        // Un rótulo de isohipsa es más largo y pide más aire alrededor.
        gapX: 230 * textScale / viewZoom,
        gapY: 118 * textScale / viewZoom,
        priority: (level) => (isMajorOverlay(level) ? 3 : 1)
      });
    }
    // Isolíneas del índice superpuesto (el LI de los mapas de CAPE). Pesan
    // más cuanto más inestable: el −6 importa más que el +2.
    if (showLiContours) {
      groups.push({
        kind: 'index',
        contours: contourPaths,
        format: (level) => (level < 0 ? `−${Math.abs(level)}` : `${level}`),
        gapX: 110 * textScale / viewZoom,
        gapY: 70 * textScale / viewZoom,
        priority: (level) => (level <= -4 ? 2 : level < 0 ? 1 : 0)
      });
    }
    if (showValueContours && formatContour) {
      groups.push({
        kind: 'value',
        contours: valueContours,
        format: formatContour,
        gapX: 170 * textScale / viewZoom,
        gapY: 92 * textScale / viewZoom,
        priority: (level) => (emphasis(level) > 0 ? 2 : 0)
      });
    }
    return placeLabels(groups);
  });

  const cities = $derived.by(() => (
    showCities
      ? placeCities({
          catalogue: cityRows,
          frame,
          bounds: visibleSourceBounds(),
          visible: visibleSourceBounds(0),
          viewZoom,
          labelScale: textScale,
          format: (value) => (
            // En el tipo de precipitación, donde no precipita va solo el
            // nombre: «Sin precipitación» bajo cada ciudad es ruido.
            frame.product === 'precip-type' && Math.round(value) === 0 ? ''
              : formatProbe ? formatProbe(value) : `${value.toFixed(1)} ${frame.unit || ''}`.trim()
          )
        })
      : []
  ));

  function inspect(event) {
    if (!frame || !surface || !layer) return;
    const rect = surface.getBoundingClientRect();
    const screenX = (event.clientX - rect.left) / rect.width * frame.width;
    const screenY = (event.clientY - rect.top) / rect.height * frame.height;
    const userPanX = panX * frame.width / rect.width;
    const userPanY = panY * frame.height / rect.height;
    const sourceX = frame.width / 2 + (screenX - userPanX - frame.width / 2) / zoom;
    const sourceY = frame.height / 2 + (screenY - userPanY - frame.height / 2) / zoom;
    if (sourceX < 0 || sourceY < 0 || sourceX >= frame.width || sourceY >= frame.height) {
      hover = null;
      return;
    }
    const column = Math.floor(sourceX);
    const row = Math.floor(sourceY);
    const value = frame.values[row * frame.width + column];
    if (!Number.isFinite(value)) {
      hover = null;
      return;
    }
    const [longitude, latitude] = geo.toGeo(column + .5, row + .5);
    const layerRect = layer.getBoundingClientRect();
    hover = {
      value,
      overlay: frame.overlay?.[row * frame.width + column],
      longitude,
      latitude,
      x: event.clientX - layerRect.left,
      y: event.clientY - layerRect.top,
      // Cerca del borde superior o del derecho la tarjeta no cabe en su sitio
      // de siempre —arriba a la derecha del cursor— y la recortaba el marco
      // del mapa: se da la vuelta hacia donde sí hay hueco.
      below: event.clientY - layerRect.top < TOOLTIP_ROOM.alto,
      left: layerRect.right - event.clientX < TOOLTIP_ROOM.ancho
    };
  }

  function setZoom(nextZoom, clientX, clientY, { settle = true } = {}) {
    if (!surface) return;
    const next = Math.max(1, Math.min(8, nextZoom));
    const rect = surface.getBoundingClientRect();
    const cursorX = (clientX ?? rect.left + rect.width / 2) - rect.left;
    const cursorY = (clientY ?? rect.top + rect.height / 2) - rect.top;
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const factor = next / zoom;
    panX = cursorX - centerX - (cursorX - centerX - panX) * factor;
    panY = cursorY - centerY - (cursorY - centerY - panY) * factor;
    zoom = next;
    if (next === 1) panX = panY = 0;
    hover = null;
    // El zoom cambia la densidad de glifos: con los botones o el doble clic
    // se rehacen de inmediato. La rueda y el trackpad mandan decenas de
    // eventos por gesto, y rehacerlos en cada uno —las streamlines son 50 ms
    // de cálculo y más de un mega de trazos— bloqueaba el hilo principal: el
    // zoom iba a saltos y Safari, sin respuesta a tiempo, desplazaba la página.
    if (settle) settleViewport();
    else scheduleSettle();
  }

  function vectorTransform(escala = zoom, desplazamientoX = panX, desplazamientoY = panY) {
    const renderedWidth = surface?.clientWidth || frame.width;
    const renderedHeight = surface?.clientHeight || frame.height;
    const userPanX = desplazamientoX * frame.width / renderedWidth;
    const userPanY = desplazamientoY * frame.height / renderedHeight;
    const centerX = frame.width / 2;
    const centerY = frame.height / 2;
    return `translate(${userPanX} ${userPanY}) translate(${centerX} ${centerY}) scale(${escala}) translate(${-centerX} ${-centerY})`;
  }

  /**
   * Lo que se ha movido el mapa desde que se trazaron las streamlines.
   *
   * Las streamlines son decenas de miles de puntos: volver a rasterizarlas en
   * cada paso del zoom era lo que lo hacía ir a saltos. Se dibujan con el
   * encuadre asentado y, mientras dura el gesto, se desplaza y escala la capa
   * ya pintada con CSS, como el ráster. Al asentarse se vuelven a trazar
   * nítidas y esta transformación vuelve a ser la identidad.
   */
  const streamGesture = $derived.by(() => {
    const k = zoom / viewZoom;
    return {
      moving: k !== 1 || panX !== viewPanX || panY !== viewPanY,
      transform: `translate(${panX - k * viewPanX}px, ${panY - k * viewPanY}px) scale(${k})`
    };
  });

  function zoomWithWheel(event) {
    event.preventDefault();
    // Proporcional al giro: un trackpad manda muchos eventos pequeños, y con
    // un 22 % fijo por evento el zoom iba a trompicones. Un paso de ratón
    // (100 px) sigue siendo más o menos ese 22 %.
    const pixeles = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 800 : 1);
    const factor = Math.min(1.6, Math.max(1 / 1.6, Math.exp(-pixeles * 0.002)));
    setZoom(zoom * factor, event.clientX, event.clientY, { settle: false });
  }

  function beginDrag(event) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    surface.setPointerCapture(event.pointerId);
    hover = null;
    if (activePointers.size >= 2) {
      const [first, second] = [...activePointers.values()];
      pinchDistance = Math.hypot(second.x - first.x, second.y - first.y);
      dragging = false;
      dragStart = null;
      return;
    }
    dragging = true;
    dragStart = { x: event.clientX, y: event.clientY, panX, panY };
  }

  function movePointer(event) {
    if (activePointers.has(event.pointerId)) {
      activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    if (activePointers.size >= 2) {
      const [first, second] = [...activePointers.values()];
      const distance = Math.hypot(second.x - first.x, second.y - first.y);
      const centerX = (first.x + second.x) / 2;
      const centerY = (first.y + second.y) / 2;
      if (pinchDistance > 0 && distance > 0) {
        setZoom(zoom * distance / pinchDistance, centerX, centerY, { settle: false });
      }
      pinchDistance = distance;
      return;
    }
    if (!dragging || !dragStart) {
      inspect(event);
      return;
    }
    // Los eventos de puntero llegan más rápido que el refresco de pantalla:
    // sin este agrupado se recalcula el encuadre varias veces por fotograma.
    pendingPan = {
      x: dragStart.panX + event.clientX - dragStart.x,
      y: dragStart.panY + event.clientY - dragStart.y
    };
    if (dragFrame) return;
    dragFrame = requestAnimationFrame(() => {
      dragFrame = 0;
      if (!pendingPan) return;
      panX = pendingPan.x;
      panY = pendingPan.y;
      scheduleSettle();
    });
  }

  function endDrag(event) {
    const clicked = activePointers.size === 1 && dragStart
      && Math.hypot(event.clientX - dragStart.x, event.clientY - dragStart.y) < 6;
    activePointers.delete(event.pointerId);
    pinchDistance = 0;
    dragging = false;
    dragStart = null;
    pendingPan = null;
    if (dragFrame) {
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
    }
    settleViewport();
    if (surface?.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    if (clicked && showMultipleSolutions && onprofileclick) {
      inspect(event);
      if (hover?.overlay >= 0.5) {
        onprofileclick({ latitude: hover.latitude, longitude: hover.longitude });
      }
    }
    /* Si queda un dedo tras terminar el pellizco, puede continuar desplazando
       el mapa sin tener que levantarlo y volverlo a apoyar. */
    if (activePointers.size === 1) {
      const remaining = [...activePointers.values()][0];
      dragging = true;
      dragStart = { x: remaining.x, y: remaining.y, panX, panY };
    }
  }

  function resetView() {
    zoom = 1;
    panX = 0;
    panY = 0;
    // Encuadre europeo para las pasadas antiguas de Europa guardadas con dominio atlántico;
    // los demás dominios se ven enteros.
    if (frame.forecast_model === 'ecmwf' && !frame.lcc && (!frame.domain || frame.domain === 'europe') && frame.bounds[0] < -60) {
      const [west, south, east, north] = frame.bounds;
      zoom = 1.7;
      panX = (.5 - (5 - west) / (east - west)) * (surface?.clientWidth || frame.width) * zoom;
      panY = (.5 - (north - 53) / (north - south)) * (surface?.clientHeight || frame.height) * zoom;
    }
    hover = null;
    settleViewport();
  }

  $effect(() => {
    frame;
    scaleBreaks;
    scaleAnchors;
    zeroFloor;
    renderGrid();
  });
  $effect(() => {
    frame;
    renderMultipleSolutions();
  });

  // La tinta de la marca de agua se mide después de pintar, en el fotograma
  // siguiente: si se calculara dentro del mismo efecto se leerían los píxeles
  // del frame anterior, y la marca iría siempre una hora por detrás del fondo
  // que tiene debajo.
  $effect(() => {
    frame;
    viewZoom;
    viewPanX;
    viewPanY;
    if (!onink) return;
    const pendiente = requestAnimationFrame(() => {
      const tinta = tintaDeLaMarca();
      if (tinta) onink(tinta);
    });
    return () => cancelAnimationFrame(pendiente);
  });

  // Solo se reencuadra cuando cambia de verdad el mapa mostrado. El efecto se
  // reevalúa también cuando llega un frame equivalente —al refrescarse el
  // catálogo, por ejemplo—, y reiniciar ahí devolvía el zoom del usuario a 1.
  let lastViewportKey = '';
  $effect(() => {
    const key = [
      resetKey,
      productLabel,
      frame.width,
      frame.height,
      frame.bounds?.join(',') || JSON.stringify(frame.projection)
    ].join('|');
    if (key === lastViewportKey) return;
    const firstMount = lastViewportKey === '';
    lastViewportKey = key;
    // Al volver a montarse para el mismo mapa —otra hora del mismo producto,
    // pasada y nivel— se recupera el encuadre en vez de reencuadrar.
    if (firstMount && savedView?.key === key) {
      zoom = savedView.zoom;
      panX = savedView.panX;
      panY = savedView.panY;
      hover = null;
      settleViewport();
      return;
    }
    resetView();
  });

  // Cada cambio de encuadre se comunica hacia arriba para poder restaurarlo.
  $effect(() => {
    const view = { zoom, panX, panY };
    if (lastViewportKey) onviewchange?.({ key: lastViewportKey, ...view });
  });

  $effect(() => () => {
    window.clearTimeout(settleTimer);
    if (dragFrame) cancelAnimationFrame(dragFrame);
  });
</script>

<div class="grid-layer" bind:this={layer} style:--grid-ratio={frame.width / frame.height}>
  <div
    class="map-surface"
    bind:this={surface}
    bind:clientWidth={surfaceWidth}
    role="application"
    aria-label={forecastText(language, 'interactiveMap', { product: productLabel })}
    class:dragging
    class:profile-target={showMultipleSolutions && hover?.overlay >= 0.5}
    onwheel={zoomWithWheel}
    onpointerdown={beginDrag}
    onpointermove={movePointer}
    onpointerup={endDrag}
    onpointercancel={endDrag}
    ondblclick={(event) => setZoom(zoom * 1.7, event.clientX, event.clientY)}
    onpointerleave={() => (hover = null)}
  >
    {#if landPath}
      <svg class="land-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} preserveAspectRatio="none" aria-hidden="true">
        <g transform={vectorTransform()}>
          <!-- Las fronteras son las del dominio del modelo, pero un campo puede
               llegar recortado: fuera de su rejilla la tierra no tiene nada
               que separar y asomaría en beige junto al campo. -->
          <clipPath id={clipId}>
            <rect x="0" y="0" width={frame.width} height={frame.height} />
          </clipPath>
          {#if dataMask}
            <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={frame.width} height={frame.height}>
              <image href={dataMask} x="0" y="0" width={frame.width} height={frame.height} preserveAspectRatio="none" style="image-rendering:pixelated" />
            </mask>
          {/if}
          <path class="land" d={landPath} clip-path={`url(#${clipId})`} mask={dataMask ? `url(#${maskId})` : undefined} />
        </g>
      </svg>
    {/if}
    <canvas
      class="grid-raster"
      bind:this={raster}
      style:transform={`translate(${panX}px, ${panY}px) scale(${zoom})`}
      aria-hidden="true"
    ></canvas>
    {#if multipleSolutions}
      <canvas
        class="grid-raster multiple-raster"
        bind:this={multipleRaster}
        style:transform={`translate(${panX}px, ${panY}px) scale(${zoom})`}
        style:display={showMultipleSolutions ? 'block' : 'none'}
        aria-hidden="true"
      ></canvas>
    {/if}
    {#if streamlineData.layers.length}
      <svg
        class="vector-overlay stream-overlay"
        class:moving={streamGesture.moving}
        style:transform={streamGesture.transform}
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <g transform={vectorTransform(viewZoom, viewPanX, viewPanY)}>
          {#each streamlineData.layers as capa}
            <path class="streamline-halo" d={capa.d} style={`opacity:${capa.opacity.toFixed(2)}`} />
            <path class="streamline" d={capa.d} style={`opacity:${capa.opacity.toFixed(2)}`} />
          {/each}
          {#if streamlineData.particles}<path class="stream-particle" d={streamlineData.particles} />{/if}
          {#if streamlineData.arrows}
            <path class="stream-direction-halo" d={streamlineData.arrows} />
            <path class="stream-direction" d={streamlineData.arrows} />
          {/if}
        </g>
      </svg>
    {/if}
    <svg class="vector-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} preserveAspectRatio="none" aria-hidden="true">
      <g transform={vectorTransform()}>
        {#each arrowGlyphs.arrows || [] as arrow}
          <g transform={`translate(${arrow.x.toFixed(2)} ${arrow.y.toFixed(2)}) rotate(${arrow.angle.toFixed(2)}) scale(${(1 / zoom).toFixed(5)})`}>
            <path class="vector-arrow-halo" d={arrow.path || arrowGlyphs.path} />
            <path class="vector-arrow" d={arrow.path || arrowGlyphs.path} />
          </g>
        {/each}
        {#each showValueContours ? valueContours : [] as contour}
          <path
            class="value-contour-halo"
            class:strong={emphasis(contour.level) > 0}
            style:stroke-dasharray={dashOf(contour.level)}
            d={contour.path}
          />
          <path
            class="value-contour"
            class:strong={emphasis(contour.level) === 1}
            class:zero={emphasis(contour.level) === 2}
            style:stroke-dasharray={dashOf(contour.level)}
            d={contour.path}
          />
        {/each}
        {#each (overlayStep > 0 ? !showIsohypses : !showLiContours) ? [] : contourPaths as contour}
          {#if overlayStep > 0}
            <path class="height-contour-halo" style:stroke-width={overlayWidth.fuerte + 1.1} d={contour.path} />
            <path
              class="height-contour"
              class:major={isMajorOverlay(contour.level)}
              style:stroke-width={isMajorOverlay(contour.level) ? overlayWidth.fuerte : overlayWidth.normal}
              d={contour.path}
            />
          {:else}
            <path class:zero-contour={contour.level === 0} class="scalar-contour" d={contour.path} />
          {/if}
        {/each}
        {#each showTroughs ? troughs.axes : [] as axis}
          <path class="trough-axis-halo" d={axisPath(axis)} />
          <path class="trough-axis" d={axisPath(axis)} />
        {/each}
        {#each centres as centre}
          <g transform={`translate(${centre.x.toFixed(1)} ${centre.y.toFixed(1)}) scale(${(textScale / zoom).toFixed(5)})`}>
            <text class="centre-letter" class:relative={!centre.main} text-anchor="middle" dominant-baseline="central">{centre.type === 'low'
              ? (centre.main ? 'B' : 'b')
              : (centre.main ? 'A' : 'a')}</text>
            <text class="centre-value" y={centre.main ? 17 : 14} text-anchor="middle" dominant-baseline="central">{Math.round(centre.value)}</text>
          </g>
        {/each}
        {#each storms as storm}
          <g transform={`translate(${storm.x.toFixed(1)} ${storm.y.toFixed(1)}) scale(${(textScale / zoom).toFixed(5)})`}>
            {#if showCentres}
              <!-- En el mapa de presión la baja ya lleva su B y su valor. -->
              <text class="storm-name" y="31" text-anchor="middle" dominant-baseline="central">{storm.text}</text>
            {:else}
              <circle class="storm-mark" r="5" />
              <text class="storm-name" y="15" text-anchor="middle" dominant-baseline="central">{storm.text}</text>
            {/if}
          </g>
        {/each}
        {#each showTroughs ? troughs.lows : [] as low}
          <g transform={`translate(${low.x.toFixed(1)} ${low.y.toFixed(1)}) scale(${(textScale / zoom).toFixed(5)})`}>
            <text class="centre-letter" text-anchor="middle" dominant-baseline="central">B</text>
            <text class="centre-value" y="17" text-anchor="middle" dominant-baseline="central">{Math.round(low.minimum)}</text>
          </g>
        {/each}
        {#each mapLabels as label}
          <g transform={`translate(${label.x.toFixed(1)} ${label.y.toFixed(1)}) scale(${(textScale / zoom).toFixed(5)})`}>
            <text
              class={label.kind === 'height' ? 'height-label' : label.kind === 'index' ? 'index-label' : 'contour-label'}
              class:major={label.kind === 'height' && isMajorOverlay(label.level)}
              class:strong={label.kind === 'value' && emphasis(label.level) > 0}
              text-anchor="middle"
              dominant-baseline="central"
            >{label.text}</text>
          </g>
        {/each}
        {#each visibleBoundaryPaths as boundary}
          <path
            class="region-boundary"
            class:admin-boundary={boundary.level === 'admin1'}
            class:admin2-boundary={boundary.level === 'admin2'}
            d={boundary.path}
          />
        {/each}
        {#each cities as city}
          <g transform={`translate(${city.x.toFixed(1)} ${city.y.toFixed(1)}) scale(${(textScale / zoom).toFixed(5)})`}>
            <circle class="city-dot" r="1.9" />
            <text class="city-name" text-anchor="middle" y="-5.5">{city.name}</text>
            {#if city.text}<text class="city-value" text-anchor="middle" y="12">{city.text}</text>{/if}
          </g>
        {/each}
      </g>
    </svg>
  </div>
  {#if hover}
    <div class="grid-tooltip" class:below={hover.below} class:left={hover.left} style:left={`${hover.x}px`} style:top={`${hover.y}px`}>
      <strong>{productLabel}</strong>
      <span>{formatProbe ? formatProbe(hover.value) : `${hover.value.toFixed(frame.product === 'ship' ? 2 : 1)} ${frame.unit}`}</span>
      {#if showMultipleSolutions && hover.overlay >= 0.5}<span class="overlay-value">{forecastLayerLabel(language, 'multipleSolutions')} · {profileHint}</span>
      {:else if !multipleSolutions && Number.isFinite(hover.overlay)}<span class="overlay-value">{overlayLabel ? `${overlayLabel} ` : ''}{hover.overlay.toFixed(1)} {frame.overlay_unit}</span>{/if}
      <small>{hover.latitude.toFixed(3)}° N · {hover.longitude.toFixed(3)}° E</small>
    </div>
  {/if}
  {#if availableLayers.length}
    <div class="layer-panel" role="group" aria-label={forecastText(language, 'layers')}>
      {#each availableLayers as capa}
        <label>
          <input
            type="checkbox"
            checked={layerPreferences[capa.id]}
            onchange={() => toggleLayer(capa.id)}
          />
          <span>{forecastLayerLabel(language, capa.labelKey || capa.id, capa.label)}</span>
        </label>
      {/each}
    </div>
  {/if}
  <div class="zoom-controls" aria-label={forecastText(language, 'zoom')}>
    <button type="button" onclick={() => setZoom(zoom * 1.35)} aria-label={forecastText(language, 'zoomIn')}><Plus size={15} /></button>
    <button type="button" onclick={() => setZoom(zoom / 1.35)} aria-label={forecastText(language, 'zoomOut')} disabled={zoom <= 1}><Minus size={15} /></button>
    <button type="button" onclick={resetView} aria-label={forecastText(language, 'resetView')}><Locate size={15} /></button>
    <span>{Math.round(zoom * 100)}%</span>
  </div>
</div>

<style>
  /* Sin margen interior: el visor adopta la proporción del dominio, así que
     el mapa lo llena entero. */
  .grid-layer{position:absolute;inset:0;z-index:4;display:grid;place-items:center;pointer-events:none}
  /* El tema oscurece la interfaz, no el papel cartográfico. Los campos
     discontinuos dejan el cero transparente (precipitación, reflectividad,
     nieve…); sin este fondo heredaban el azul casi negro del visor y costas,
     fronteras y zonas sin fenómeno se fundían con él. */
  .map-surface{position:relative;max-width:100%;max-height:100%;width:auto;height:100%;aspect-ratio:var(--grid-ratio);filter:drop-shadow(0 12px 24px rgba(0,0,0,.24));pointer-events:auto;cursor:grab;touch-action:none}
  .map-surface.dragging{cursor:grabbing}
  /* Durante el gesto las partículas se paran: animarlas obliga a repintar
     todas las líneas en cada fotograma, además de moverlas. */
  /* Durante el gesto las partículas se esconden: son un trazo con todas las
     líneas y habría que volver a rasterizarlo a cada escala. */
  .map-surface.dragging .stream-particle{animation-play-state:paused}
  .stream-overlay.moving .stream-particle{display:none}
  .stream-overlay{transform-origin:center;will-change:transform}
  .map-surface.profile-target:not(.dragging){cursor:pointer}
  .vector-overlay{position:absolute;inset:0;display:block;width:100%;height:100%}
  .land-overlay{position:absolute;inset:0;display:block;width:100%;height:100%;overflow:visible;pointer-events:none}
  .land{fill:#e8e4da;stroke:none}
  /* El raster se compone en GPU: el encuadre no vuelve a rasterizar la malla. */
  .grid-raster{position:absolute;inset:0;display:block;width:100%;height:100%;image-rendering:pixelated;transform-origin:center;will-change:transform}
  .multiple-raster{pointer-events:none}
  .vector-overlay{overflow:visible;pointer-events:none}
  .vector-arrow,.vector-arrow-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .vector-arrow-halo{stroke:rgba(239,247,250,.62);stroke-width:1.9}
  .vector-arrow{stroke:rgba(7,13,18,.94);stroke-width:1.02}
  .streamline,.streamline-halo,.stream-particle,.stream-direction,.stream-direction-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  /* Trazo fino: las líneas van a una separación pareja y con un grosor de
     dos píxeles el mapa se convertía en una maraña de tuberías. */
  .streamline-halo{stroke:rgba(238,247,250,.5);stroke-width:1.9}
  .streamline{stroke:rgba(5,14,20,.82);stroke-width:.95}
  .stream-particle{stroke:rgba(190,232,250,.85);stroke-width:1;stroke-dasharray:1.25 11.75;animation:stream-flow 1.25s linear infinite}
  .stream-direction-halo{stroke:rgba(238,247,250,.85);stroke-width:2.4}
  .stream-direction{stroke:rgba(5,14,20,.95);stroke-width:1.35}
  .scalar-contour{fill:none;stroke:rgba(9,13,18,.82);stroke-width:.62;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  /* Marrón cálido, no negro: las fronteras ya son negras y el trazo del
     índice superpuesto también. El halo claro las mantiene legibles sobre los
     dos extremos de la paleta, que son azul y granate oscuros. */
  .value-contour,.value-contour-halo{fill:none;stroke-linecap:butt;vector-effect:non-scaling-stroke}
  .value-contour-halo{stroke:rgba(255,247,235,.5);stroke-width:2.4}
  .value-contour-halo.strong{stroke-width:3}
  .value-contour{stroke:rgba(74,54,40,.92);stroke-width:1.05}
  .value-contour.strong{stroke-width:1.45}
  .value-contour.zero{stroke:rgba(58,40,28,.96);stroke-width:1.8}
  /* Isohipsas: continuas y en gris azulado frío, para que no se confundan con
     las isotermas discontinuas ni con las fronteras. */
  .height-contour,.height-contour-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .height-contour-halo{stroke:rgba(250,252,255,.45)}
  .height-contour{stroke:rgba(28,44,66,.88)}
  .height-contour.major{stroke:rgba(16,30,50,.95)}
  /* Eje de vaguada: blanco, grueso y discontinuo, que es como se traza a mano
     sobre un mapa isobárico. El halo oscuro lo sostiene sobre los tonos
     claros de la paleta, donde un blanco a secas se perdería. */
  .trough-axis,.trough-axis-halo{fill:none;stroke-linecap:butt;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  /* Discontinua blanca con un contorno negro fino alrededor de cada trazo, no
     una línea negra con blanco encima: el contorno lleva los mismos huecos que
     el blanco y cada trazo suyo sobresale 0,7 px por los dos extremos (11+1,4
     de trazo, 7-1,4 de hueco, adelantado 0,7), que es el borde que falta en
     las puntas con `butt`. */
  .trough-axis-halo{stroke:rgba(10,18,28,.9);stroke-width:4.4;stroke-dasharray:12.4 5.6;stroke-dashoffset:.7}
  .trough-axis{stroke:#fff;stroke-width:3;stroke-dasharray:11 7}
  /* Centros de presión y bajas cerradas de 500 hPa, con el mismo diseño: la
     letra grande y el valor en pequeño debajo, los dos en blanco con perfil
     oscuro para que se lean sobre cualquier tono. */
  .centre-letter,.centre-value{fill:#fff;stroke:rgba(10,18,28,.6);paint-order:stroke;pointer-events:none}
  .centre-letter{stroke-width:3.4px;font-size:19px;font-weight:800}
  .centre-value{stroke-width:2.6px;font-size:10.5px;font-weight:700}
  /* Los relativos van en minúscula y algo más discretos, como en los mapas de
     AEMET: están, pero no compiten con el centro principal. */
  .centre-letter.relative{font-size:16px;stroke-width:3px}
  .storm-name{fill:#ffd166;stroke:rgba(10,18,28,.7);stroke-width:2.8px;paint-order:stroke;font-size:11.5px;font-weight:800;font-style:italic;pointer-events:none}
  .storm-mark{fill:none;stroke:#ffd166;stroke-width:2.2px;pointer-events:none}
  .height-label{fill:rgba(20,34,54,.96);stroke:rgba(252,253,255,.85);stroke-width:2.6px;paint-order:stroke;font-size:11px;font-weight:700;pointer-events:none}
  .height-label.major{font-size:12px;font-weight:800}
  .layer-panel{position:absolute;right:10px;top:48px;z-index:15;display:flex;flex-direction:column;gap:3px;padding:7px 9px;border:1px solid rgba(255,255,255,.15);border-radius:8px;background:rgba(5,14,22,.78);backdrop-filter:blur(8px);pointer-events:auto}
  .layer-panel label{display:flex;align-items:center;gap:6px;color:rgba(235,244,251,.82);font-size:.55rem;line-height:1;cursor:pointer;white-space:nowrap}
  .layer-panel label:hover{color:#fff}
  .layer-panel input{width:12px;height:12px;margin:0;accent-color:#68bdf1;cursor:pointer}
  /* Como la isolínea: tinta oscura con halo claro, que se lee sobre toda la
     rampa del CAPE, del gris del cero al granate del máximo. */
  .index-label{fill:rgba(9,13,18,.94);stroke:rgba(250,252,253,.86);stroke-width:2.4px;paint-order:stroke;font-size:11px;font-weight:750;font-variant-numeric:tabular-nums;pointer-events:none}
  .contour-label{fill:rgba(58,42,30,.96);stroke:rgba(255,250,242,.82);stroke-width:2.6px;paint-order:stroke;font-size:12px;font-weight:700;letter-spacing:.01em;pointer-events:none}
  .contour-label.strong{font-size:13px}
  .scalar-contour.zero-contour{stroke:#f5f8fa;stroke-width:.9}
  @keyframes stream-flow{to{stroke-dashoffset:-13.05}}
  @media(prefers-reduced-motion:reduce){.stream-particle{display:none}}
  .city-dot{fill:rgba(12,20,28,.9);stroke:rgba(255,255,255,.9);stroke-width:.9px;pointer-events:none}
  .city-name{fill:#fff;stroke:rgba(9,16,24,.72);stroke-width:2.6px;paint-order:stroke;font-size:10.5px;font-weight:700;letter-spacing:.01em;pointer-events:none}
  .city-value{fill:#fff;stroke:rgba(9,16,24,.78);stroke-width:3px;paint-order:stroke;font-size:12px;font-weight:800;pointer-events:none}
  .region-boundary{fill:none;stroke:#0b0f12;stroke-width:.7;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .region-boundary.admin-boundary{stroke:rgba(11,15,18,.54);stroke-width:.32}.region-boundary.admin2-boundary{stroke:rgba(11,15,18,.32);stroke-width:.26}
  .grid-tooltip{position:absolute;z-index:12;display:flex;flex-direction:column;gap:2px;min-width:142px;padding:8px 9px;transform:translate(12px,calc(-100% - 10px));border:1px solid rgba(255,255,255,.16);border-radius:8px;color:#eef6fa;background:rgba(5,14,22,.9);box-shadow:0 8px 24px rgba(0,0,0,.3);backdrop-filter:blur(8px);pointer-events:none}
  .grid-tooltip.below{transform:translate(12px,16px)}.grid-tooltip.left{transform:translate(calc(-100% - 12px),calc(-100% - 10px))}.grid-tooltip.below.left{transform:translate(calc(-100% - 12px),16px)}
  .grid-tooltip strong{font-size:.59rem}.grid-tooltip span{color:#8ed1ff;font-size:.72rem;font-weight:720}.grid-tooltip small{color:rgba(235,244,251,.6);font-size:.5rem}
  .grid-tooltip .overlay-value{color:#f4d58a;font-size:.62rem}
  .zoom-controls{position:absolute;right:10px;top:10px;z-index:15;display:grid;grid-template-columns:30px 30px 30px auto;align-items:center;gap:4px;pointer-events:auto}
  .zoom-controls button{display:grid;place-items:center;width:30px;height:30px;border:1px solid rgba(255,255,255,.15);border-radius:7px;color:#dceaf2;background:rgba(5,14,22,.76);backdrop-filter:blur(8px)}
  .zoom-controls button:hover{background:rgba(31,60,79,.9)}.zoom-controls button:disabled{opacity:.38}
  .zoom-controls span{min-width:38px;padding:5px 6px;border-radius:6px;color:rgba(235,244,251,.72);background:rgba(5,14,22,.66);font-size:.5rem;text-align:center}
</style>
