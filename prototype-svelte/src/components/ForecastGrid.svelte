<script>
  import { Locate, Minus, Plus } from '@lucide/svelte';
  import {
    LUT_SIZE, bandOfValue, bandPosition, defaultPalette, paletteStop,
    precipitationPalette,
  } from '../lib/palettes.js';
  import { contourLines, stepLevels } from '../lib/contours.js';
  import { troughAxes as detectTroughAxes } from '../lib/troughs.js';
  import {
    CENTRE_PROMINENCE_HPA, pressureCentres as detectPressureCentres,
  } from '../lib/pressureCentres.js';
  import { LAYERS, layerPreferences, toggleLayer } from '../lib/layerPreferences.svelte.js';

  // `formatProbe` trae ya la unidad elegida en la leyenda; sin ella se cae a la
  // que manda el backend en la cabecera del frame. `scaleBreaks` y `zeroFloor`
  // vienen del producto: con clases, el ráster deja de escalarse linealmente.
  let {
    frame, productLabel, resetKey = 0, formatProbe = null,
    scaleBreaks = null, zeroFloor = 0,
    displayMin = null, displayMax = null, contourStep = 0, formatContour = null,
    nationalBoundariesOnly = false, overlayStep = 0, overlayMajorStep = 0,
    troughAxes = false, overlayLabel = '',
    pressureCentres = false, overlaySmoothing = 4, overlayLayerLabel = '',
  } = $props();

  // Isotermas notables: la del cero es la que separa nieve de lluvia y helada
  // de no helada, así que va la primera y más gruesa; las decenas ordenan la
  // lectura del resto sin competir con ella.
  // Capas que este mapa puede ofrecer, y cuáles están encendidas ahora mismo.
  const availableLayers = $derived(LAYERS.filter((capa) => (
    capa.id === 'isotherms' ? contourStep > 0
      : capa.id === 'isohypses' ? overlayStep > 0
      : capa.id === 'troughs' ? troughAxes && Boolean(frame.overlay)
      : pressureCentres && Boolean(frame.overlay)
  // La capa superpuesta se llama distinto según el campo: isohipsas en un
  // mapa de geopotencial e isobaras en uno de presión.
  )).map((capa) => (
    capa.id === 'isohypses' && overlayLayerLabel
      ? { ...capa, label: overlayLayerLabel }
      : capa
  )));
  const showIsotherms = $derived(contourStep > 0 && layerPreferences.isotherms);
  const showIsohypses = $derived(overlayStep > 0 && layerPreferences.isohypses);
  const showTroughs = $derived(troughAxes && layerPreferences.troughs);
  const showCentres = $derived(pressureCentres && layerPreferences.centres);

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
  let hover = $state(null);
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

  function renderGrid() {
    if (!frame || !raster) return;
    const { width, height, values } = frame;
    raster.width = width;
    raster.height = height;
    const context = raster.getContext('2d', { alpha: true });
    context.imageSmoothingEnabled = false;
    const pixels = context.createImageData(width, height);
    const canvas32 = new Uint32Array(pixels.data.buffer);
    const isPrecipitation = frame.product === 'precip-1h';
    const last = LUT_SIZE - 1;
    const palette = isPrecipitation || scaleBreaks?.length
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
      } else {
        const slot = (value - low) * linearScale;
        canvas32[index] = lut[slot > last ? last : slot < 0 ? 0 : slot | 0];
      }
    }
    context.putImageData(pixels, 0, 0);
  }

  function makeBoundaryPaths() {
    if (!frame.boundaries?.length) return [];
    const [west, south, east, north] = frame.bounds;
    const paths = [];
    for (const region of frame.boundaries) {
      // Los mapas con isolíneas propias se quedan solo con costas y fronteras
      // nacionales: sobre un campo ya cruzado de isotermas, las divisiones
      // interiores compiten con ellas y no aportan nada a la lectura.
      if (nationalBoundariesOnly && (region.level || 'country') !== 'country') continue;
      for (const ring of region.rings) {
        const points = ring.map(([longitude, latitude]) => [
          (longitude - west) / (east - west) * frame.width,
          (north - latitude) / (north - south) * frame.height
        ]);
        if (points.length) {
          paths.push({
            path: `M${points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join('L')}Z`,
            level: region.level || 'country'
          });
        }
      }
    }
    // Las divisiones interiores se dibujan primero para que la frontera nacional
    // conserve exactamente su grosor y color en costas y límites internacionales.
    return paths.sort((left, right) =>
      (left.level === 'country' ? 1 : 0) - (right.level === 'country' ? 1 : 0)
    );
  }

  function visibleSourceBounds() {
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
    const marginX = frame.width * .3 / viewZoom;
    const marginY = frame.height * .3 / viewZoom;
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
    const u = sumU / count;
    const v = sumV / count;
    const magnitude = Math.hypot(u, v);
    if (magnitude < .5) return null;
    return { x: sumX / count, y: sumY / count, angle: Math.atan2(-v, u) * 180 / Math.PI };
  }

  function makeArrowGlyphs() {
    if (!frame.u || !frame.v || frame.product === 'wind-level') return { arrows: [], path: '' };
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
    const half = length / 2;
    const head = length * .3;
    return {
      arrows,
      path: `M${(-half).toFixed(2)},0L${half.toFixed(2)},0M${(half - head).toFixed(2)},${(-head * .62).toFixed(2)}L${half.toFixed(2)},0L${(half - head).toFixed(2)},${(head * .62).toFixed(2)}`
    };
  }

  function sampleVector(x, y) {
    if (x < 0 || y < 0 || x >= frame.width - 1 || y >= frame.height - 1) return null;
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const tx = x - x0;
    const ty = y - y0;
    const indexes = [
      y0 * frame.width + x0,
      y0 * frame.width + x0 + 1,
      (y0 + 1) * frame.width + x0,
      (y0 + 1) * frame.width + x0 + 1
    ];
    if (indexes.some((index) => !Number.isFinite(frame.values[index]) || !Number.isFinite(frame.u[index]) || !Number.isFinite(frame.v[index]))) return null;
    const weights = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
    const u = indexes.reduce((sum, index, i) => sum + frame.u[index] * weights[i], 0);
    const v = indexes.reduce((sum, index, i) => sum + frame.v[index] * weights[i], 0);
    const magnitude = Math.hypot(u, v);
    return magnitude >= .35 ? { u, v, magnitude } : null;
  }

  function integrateStream(seedX, seedY, direction, spatialStep, maxSteps) {
    const points = [];
    let x = seedX;
    let y = seedY;
    for (let step = 0; step < maxSteps; step += 1) {
      const first = sampleVector(x, y);
      if (!first) break;
      const halfX = x + direction * spatialStep * .5 * first.u / first.magnitude;
      const halfY = y - direction * spatialStep * .5 * first.v / first.magnitude;
      const middle = sampleVector(halfX, halfY);
      if (!middle) break;
      x += direction * spatialStep * middle.u / middle.magnitude;
      y -= direction * spatialStep * middle.v / middle.magnitude;
      if (!sampleVector(x, y)) break;
      points.push([x, y]);
    }
    return points;
  }

  function makeStreamlinePaths() {
    if (!frame.u || !frame.v || frame.product !== 'wind-level') return { paths: [], markerPath: '' };
    const paths = [];
    const baseSeedStep = Math.max(4.5, frame.width / 25);
    const seedStep = baseSeedStep / viewZoom;
    const spatialStep = Math.max(.16, seedStep / 9);
    const bounds = visibleSourceBounds();
    const firstX = Math.floor(bounds.west / seedStep) * seedStep + seedStep * .5;
    const firstY = Math.floor(bounds.north / seedStep) * seedStep + seedStep * .5;
    for (let y = firstY; y < bounds.south; y += seedStep) {
      for (let x = firstX; x < bounds.east; x += seedStep) {
        if (!sampleVector(x, y)) continue;
        const points = [
          ...integrateStream(x, y, -1, spatialStep, 15).reverse(),
          [x, y],
          ...integrateStream(x, y, 1, spatialStep, 15)
        ];
        if (points.length < 10) continue;
        const middleIndex = Math.floor(points.length / 2);
        const previous = points[Math.max(0, middleIndex - 1)];
        const middle = points[middleIndex];
        const next = points[Math.min(points.length - 1, middleIndex + 1)];
        paths.push({
          path: `M${points.map(([px, py]) => `${px.toFixed(2)},${py.toFixed(2)}`).join('L')}`,
          marker: {
            x: middle[0],
            y: middle[1],
            angle: Math.atan2(next[1] - previous[1], next[0] - previous[0]) * 180 / Math.PI
          }
        });
      }
    }
    const markerSize = Math.max(3.2, baseSeedStep * .22);
    return {
      paths,
      markerPath: `M${(-markerSize).toFixed(2)},${(-markerSize * .62).toFixed(2)}L0,0L${(-markerSize).toFixed(2)},${(markerSize * .62).toFixed(2)}`
    };
  }

  const boundaryPaths = $derived(makeBoundaryPaths());
  const arrowGlyphs = $derived(makeArrowGlyphs());
  const streamlineData = $derived(makeStreamlinePaths());
  // El contorno del índice superpuesto va crudo, como estaba: es una línea de
  // referencia sobre un campo ya suave, no el dibujo principal del mapa.
  const contourPaths = $derived.by(() => {
    if (!frame.overlay) return [];
    if (overlayStep > 0) {
      // Isohipsas: el geopotencial es un campo mucho más suave que la
      // temperatura, así que basta con medio sigma y con tirar los anillos
      // pequeños; no hace falta la limpieza entera.
      return contourLines(frame.overlay, {
        width: frame.width,
        height: frame.height,
        levels: stepLevels(frame.overlay, overlayStep),
        // El geopotencial es liso de verdad: los dientes que salían no eran
        // meteorología, sino el escalón de la cuantización uint16 del frame.
        // Con más sigma y más tolerancia la isohipsa queda como dibujada a
        // mano y no se pierde nada del campo.
        // Las isobaras van sin suavizar: mover la línea la separaría del dato.
        // El geopotencial sí se suaviza, que es liso de verdad y sus dientes
        // vienen del escalón de la cuantización.
        sigma: overlaySmoothing,
        minRingArea: 40,
        tolerance: overlaySmoothing > 0 ? 1.6 : 0.8,
        labelMinLength: 90,
        labelSpacing: 70
      });
    }
    return contourLines(frame.overlay, {
      width: frame.width,
      height: frame.height,
      levels: [-10, -8, -6, -4, -2, 0, 2, 4],
      sigma: 0,
      minRingArea: 0,
      tolerance: 0,
      labelMinLength: Infinity
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
      ? detectTroughAxes(frame.overlay, { width: frame.width, height: frame.height })
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
   * La prominencia exigida baja al ampliar: con el mapa entero solo interesan
   * los centros sinópticos, y de cerca sí aporta ver los secundarios.
   */
  const centres = $derived(
    showCentres && frame.overlay
      ? detectPressureCentres(frame.overlay, {
          width: frame.width,
          height: frame.height,
          prominenceHpa: Math.max(1, CENTRE_PROMINENCE_HPA / Math.min(2.5, viewZoom))
        })
      : []
  );

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
        gapX: 230 / viewZoom,
        gapY: 118 / viewZoom,
        priority: (level) => (isMajorOverlay(level) ? 3 : 1)
      });
    }
    if (showIsotherms && formatContour) {
      groups.push({
        kind: 'value',
        contours: valueContours,
        format: formatContour,
        gapX: 170 / viewZoom,
        gapY: 92 / viewZoom,
        priority: (level) => (emphasis(level) > 0 ? 2 : 0)
      });
    }
    return placeLabels(groups);
  });

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
    const [west, south, east, north] = frame.bounds;
    const longitude = west + (column + .5) / frame.width * (east - west);
    const latitude = north - (row + .5) / frame.height * (north - south);
    const layerRect = layer.getBoundingClientRect();
    hover = {
      value,
      overlay: frame.overlay?.[row * frame.width + column],
      longitude,
      latitude,
      x: event.clientX - layerRect.left,
      y: event.clientY - layerRect.top
    };
  }

  function setZoom(nextZoom, clientX, clientY) {
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
    // El zoom cambia la densidad de glifos: conviene rehacerlos de inmediato.
    settleViewport();
  }

  function vectorTransform() {
    const renderedWidth = surface?.clientWidth || frame.width;
    const renderedHeight = surface?.clientHeight || frame.height;
    const userPanX = panX * frame.width / renderedWidth;
    const userPanY = panY * frame.height / renderedHeight;
    const centerX = frame.width / 2;
    const centerY = frame.height / 2;
    return `translate(${userPanX} ${userPanY}) translate(${centerX} ${centerY}) scale(${zoom}) translate(${-centerX} ${-centerY})`;
  }

  function zoomWithWheel(event) {
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.22 : 1 / 1.22), event.clientX, event.clientY);
  }

  function beginDrag(event) {
    if (event.button !== 0) return;
    dragging = true;
    dragStart = { x: event.clientX, y: event.clientY, panX, panY };
    surface.setPointerCapture(event.pointerId);
    hover = null;
  }

  function movePointer(event) {
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
    dragging = false;
    dragStart = null;
    pendingPan = null;
    if (dragFrame) {
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
    }
    settleViewport();
    if (surface?.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
  }

  function resetView() {
    zoom = 1;
    panX = 0;
    panY = 0;
    hover = null;
    settleViewport();
  }

  $effect(() => {
    frame;
    scaleBreaks;
    zeroFloor;
    renderGrid();
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
      frame.bounds.join(',')
    ].join('|');
    if (key === lastViewportKey) return;
    lastViewportKey = key;
    resetView();
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
    role="application"
    aria-label={`Mapa interactivo de ${productLabel}`}
    class:dragging
    onwheel={zoomWithWheel}
    onpointerdown={beginDrag}
    onpointermove={movePointer}
    onpointerup={endDrag}
    onpointercancel={endDrag}
    ondblclick={(event) => setZoom(zoom * 1.7, event.clientX, event.clientY)}
    onpointerleave={() => (hover = null)}
  >
    <canvas
      class="grid-raster"
      bind:this={raster}
      style:transform={`translate(${panX}px, ${panY}px) scale(${zoom})`}
      aria-hidden="true"
    ></canvas>
    <svg class="vector-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} preserveAspectRatio="none" aria-hidden="true">
      <g transform={vectorTransform()}>
        {#each streamlineData.paths as streamline}
          <path class="streamline-halo" d={streamline.path} />
          <path class="streamline" d={streamline.path} />
          <path class="stream-particle" d={streamline.path} />
          <g transform={`translate(${streamline.marker.x.toFixed(2)} ${streamline.marker.y.toFixed(2)}) rotate(${streamline.marker.angle.toFixed(2)}) scale(${(1 / zoom).toFixed(5)})`}>
            <path class="stream-direction-halo" d={streamlineData.markerPath} />
            <path class="stream-direction" d={streamlineData.markerPath} />
          </g>
        {/each}
        {#each arrowGlyphs.arrows || [] as arrow}
          <g transform={`translate(${arrow.x.toFixed(2)} ${arrow.y.toFixed(2)}) rotate(${arrow.angle.toFixed(2)}) scale(${(1 / zoom).toFixed(5)})`}>
            <path class="vector-arrow-halo" d={arrowGlyphs.path} />
            <path class="vector-arrow" d={arrowGlyphs.path} />
          </g>
        {/each}
        {#each showIsotherms ? valueContours : [] as contour}
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
        {#each (overlayStep > 0 && !showIsohypses) ? [] : contourPaths as contour}
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
          <g transform={`translate(${centre.x.toFixed(1)} ${centre.y.toFixed(1)}) scale(${(1 / zoom).toFixed(5)})`}>
            <text
              class="pressure-centre"
              class:relative={!centre.main}
              text-anchor="middle"
              dominant-baseline="central"
            >
              <tspan class="symbol">{centre.type === 'low'
                ? (centre.main ? 'B' : 'b')
                : (centre.main ? 'A' : 'a')}</tspan>
              <tspan dx="3">{Math.round(centre.value)}</tspan>
            </text>
          </g>
        {/each}
        {#each showTroughs ? troughs.lows : [] as low}
          <g transform={`translate(${low.x.toFixed(1)} ${low.y.toFixed(1)}) scale(${(1 / zoom).toFixed(5)})`}>
            <text class="closed-low" text-anchor="middle" dominant-baseline="central">B</text>
          </g>
        {/each}
        {#each mapLabels as label}
          <g transform={`translate(${label.x.toFixed(1)} ${label.y.toFixed(1)}) scale(${(1 / zoom).toFixed(5)})`}>
            <text
              class={label.kind === 'height' ? 'height-label' : 'contour-label'}
              class:major={label.kind === 'height' && isMajorOverlay(label.level)}
              class:strong={label.kind === 'value' && emphasis(label.level) > 0}
              text-anchor="middle"
              dominant-baseline="central"
            >{label.text}</text>
          </g>
        {/each}
        {#each boundaryPaths as boundary}
          <path
            class="region-boundary"
            class:admin-boundary={boundary.level === 'admin1'}
            d={boundary.path}
          />
        {/each}
      </g>
    </svg>
  </div>
  {#if hover}
    <div class="grid-tooltip" style:left={`${hover.x}px`} style:top={`${hover.y}px`}>
      <strong>{productLabel}</strong>
      <span>{formatProbe ? formatProbe(hover.value) : `${hover.value.toFixed(frame.product === 'ship' ? 2 : 1)} ${frame.unit}`}</span>
      {#if Number.isFinite(hover.overlay)}<span class="overlay-value">{overlayLabel ? `${overlayLabel} ` : ''}{hover.overlay.toFixed(1)} {frame.overlay_unit}</span>{/if}
      <small>{hover.latitude.toFixed(3)}° N · {hover.longitude.toFixed(3)}° E</small>
    </div>
  {/if}
  {#if availableLayers.length}
    <div class="layer-panel" role="group" aria-label="Capas del mapa">
      {#each availableLayers as capa}
        <label>
          <input
            type="checkbox"
            checked={layerPreferences[capa.id]}
            onchange={() => toggleLayer(capa.id)}
          />
          <span>{capa.label}</span>
        </label>
      {/each}
    </div>
  {/if}
  <div class="zoom-controls" aria-label="Controles de zoom">
    <button type="button" onclick={() => setZoom(zoom * 1.35)} aria-label="Acercar mapa"><Plus size={15} /></button>
    <button type="button" onclick={() => setZoom(zoom / 1.35)} aria-label="Alejar mapa" disabled={zoom <= 1}><Minus size={15} /></button>
    <button type="button" onclick={resetView} aria-label="Restablecer encuadre"><Locate size={15} /></button>
    <span>{Math.round(zoom * 100)}%</span>
  </div>
</div>

<style>
  .grid-layer{position:absolute;inset:4% 6%;z-index:4;display:grid;place-items:center;pointer-events:none}
  .map-surface{position:relative;max-width:100%;max-height:100%;width:auto;height:100%;aspect-ratio:var(--grid-ratio);filter:drop-shadow(0 12px 24px rgba(0,0,0,.24));pointer-events:auto;cursor:grab;touch-action:none}
  .map-surface.dragging{cursor:grabbing}
  .vector-overlay{position:absolute;inset:0;display:block;width:100%;height:100%}
  /* El raster se compone en GPU: el encuadre no vuelve a rasterizar la malla. */
  .grid-raster{position:absolute;inset:0;display:block;width:100%;height:100%;image-rendering:pixelated;transform-origin:center;will-change:transform}
  .vector-overlay{overflow:visible;pointer-events:none}
  .vector-arrow,.vector-arrow-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .vector-arrow-halo{stroke:rgba(239,247,250,.62);stroke-width:1.9}
  .vector-arrow{stroke:rgba(7,13,18,.94);stroke-width:1.02}
  .streamline,.streamline-halo,.stream-particle,.stream-direction,.stream-direction-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .streamline-halo{stroke:rgba(238,247,250,.58);stroke-width:2.75}
  .streamline{stroke:rgba(5,14,20,.9);stroke-width:1.48}
  .stream-particle{stroke:rgba(190,232,250,.88);stroke-width:1.55;stroke-dasharray:1.25 11.75;animation:stream-flow 1.25s linear infinite}
  .stream-direction-halo{stroke:rgba(238,247,250,.9);stroke-width:3.55}
  .stream-direction{stroke:rgba(5,14,20,.98);stroke-width:2.05}
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
  .trough-axis,.trough-axis-halo{fill:none;stroke-linecap:butt;stroke-linejoin:round;vector-effect:non-scaling-stroke;stroke-dasharray:11 7}
  .trough-axis-halo{stroke:rgba(10,18,28,.5);stroke-width:6.2}
  .trough-axis{stroke:rgba(255,255,255,.97);stroke-width:3.4}
  /* Centro de presión: la letra manda y el valor la acompaña, los dos en
     blanco con perfil oscuro para que se lean sobre cualquier tono. */
  .pressure-centre{fill:#fff;stroke:rgba(10,18,28,.68);stroke-width:3.2px;paint-order:stroke;font-size:13px;font-weight:750;pointer-events:none}
  .pressure-centre .symbol{font-size:18px;font-weight:900}
  /* Los relativos van en minúscula y algo más discretos, como en los mapas de
     AEMET: están, pero no compiten con el centro principal. */
  .pressure-centre.relative{font-size:11px;fill:rgba(255,255,255,.92)}
  .pressure-centre.relative .symbol{font-size:15px;font-weight:800}
  .closed-low{fill:#fff;stroke:rgba(10,18,28,.6);stroke-width:3.4px;paint-order:stroke;font-size:19px;font-weight:800}
  .height-label{fill:rgba(20,34,54,.96);stroke:rgba(252,253,255,.85);stroke-width:2.6px;paint-order:stroke;font-size:11px;font-weight:700;pointer-events:none}
  .height-label.major{font-size:12px;font-weight:800}
  .layer-panel{position:absolute;right:calc(-6% + 4px);top:calc(-4% + 42px);z-index:15;display:flex;flex-direction:column;gap:3px;padding:7px 9px;border:1px solid rgba(255,255,255,.15);border-radius:8px;background:rgba(5,14,22,.78);backdrop-filter:blur(8px);pointer-events:auto}
  .layer-panel label{display:flex;align-items:center;gap:6px;color:rgba(235,244,251,.82);font-size:.55rem;line-height:1;cursor:pointer;white-space:nowrap}
  .layer-panel label:hover{color:#fff}
  .layer-panel input{width:12px;height:12px;margin:0;accent-color:#68bdf1;cursor:pointer}
  .contour-label{fill:rgba(58,42,30,.96);stroke:rgba(255,250,242,.82);stroke-width:2.6px;paint-order:stroke;font-size:12px;font-weight:700;letter-spacing:.01em;pointer-events:none}
  .contour-label.strong{font-size:13px}
  .scalar-contour.zero-contour{stroke:#f5f8fa;stroke-width:.9}
  @keyframes stream-flow{to{stroke-dashoffset:-13.05}}
  @media(prefers-reduced-motion:reduce){.stream-particle{display:none}}
  .region-boundary{fill:none;stroke:#0b0f12;stroke-width:.7;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .region-boundary.admin-boundary{stroke:rgba(11,15,18,.54);stroke-width:.32}
  .grid-tooltip{position:absolute;z-index:12;display:flex;flex-direction:column;gap:2px;min-width:142px;padding:8px 9px;transform:translate(12px,calc(-100% - 10px));border:1px solid rgba(255,255,255,.16);border-radius:8px;color:#eef6fa;background:rgba(5,14,22,.9);box-shadow:0 8px 24px rgba(0,0,0,.3);backdrop-filter:blur(8px);pointer-events:none}
  .grid-tooltip strong{font-size:.59rem}.grid-tooltip span{color:#8ed1ff;font-size:.72rem;font-weight:720}.grid-tooltip small{color:rgba(235,244,251,.6);font-size:.5rem}
  .grid-tooltip .overlay-value{color:#f4d58a;font-size:.62rem}
  .zoom-controls{position:absolute;right:calc(-6% + 4px);top:calc(-4% + 4px);z-index:15;display:grid;grid-template-columns:30px 30px 30px auto;align-items:center;gap:4px;pointer-events:auto}
  .zoom-controls button{display:grid;place-items:center;width:30px;height:30px;border:1px solid rgba(255,255,255,.15);border-radius:7px;color:#dceaf2;background:rgba(5,14,22,.76);backdrop-filter:blur(8px)}
  .zoom-controls button:hover{background:rgba(31,60,79,.9)}.zoom-controls button:disabled{opacity:.38}
  .zoom-controls span{min-width:38px;padding:5px 6px;border-radius:6px;color:rgba(235,244,251,.72);background:rgba(5,14,22,.66);font-size:.5rem;text-align:center}
</style>
