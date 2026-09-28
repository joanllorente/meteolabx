/**
 * Líneas de corriente repartidas a distancia pareja.
 *
 * El reparto ingenuo —sembrar en una malla y dibujar un trozo de línea desde
 * cada nodo— se le ve la costura: donde el viento converge las líneas se
 * amontonan hasta tocarse, donde diverge quedan calvas, y todas empiezan y
 * acaban a la misma distancia de su semilla, así que aparecen y desaparecen
 * sin motivo a media pantalla.
 *
 * Aquí se sigue el método de Jobard y Lefer (1997): una línea crece hasta que
 * se superpone a otra ya dibujada, y las semillas siguientes salen de los
 * costados de la que se acaba de trazar, a la separación buscada. El reparto
 * cubre el campo por igual, pero la línea solo se corta cuando de verdad se
 * echa encima de su vecina: si el viento converge, las dos se juntan y esa
 * convergencia se ve, que es información y no un defecto.
 *
 * La vecindad se consulta en una rejilla de cubos del tamaño de la separación:
 * cada punto solo mira los nueve cubos de alrededor, que es lo que permite
 * repartir miles de puntos sin comparar todos contra todos.
 */

/**
 * Fracción de la separación a la que una línea se considera pegada a otra.
 *
 * Es lo único que corta una línea a media pantalla, así que manda en la
 * continuidad del mapa. Con el 55 % clásico, en un flujo que converge las
 * líneas se cortaban a las pocas separaciones y el trazo continuaba unos
 * kilómetros más allá con otra línea: a 300 hPa la mediana era de ocho
 * separaciones. Con el 30 % la mediana pasa de veinte, las líneas cruzan el
 * mapa enteras y donde el viento converge se juntan, que es justo lo que hay
 * que ver.
 */
export const STREAM_TEST_RATIO = 0.3;
/**
 * Largo mínimo de una línea, en veces la separación.
 *
 * Una semilla puede caer en un pasillo ya casi cerrado entre dos líneas y
 * morir a las pocas celdas. Ese rabito no dice nada del viento y encima
 * despista, porque en medio de un flujo intenso parece un jirón de calma: por
 * debajo de este largo la línea se descarta. De 1,5 a 2,5 desaparecen los
 * quince trozos sueltos de la pasada de prueba y la cobertura solo baja del
 * 72 % al 70 %.
 */
export const STREAM_MIN_LENGTH = 2.5;
/**
 * Largo máximo de una línea, en veces la separación.
 *
 * Es una red de seguridad contra un campo que gire sobre sí mismo, no un
 * recorte de presentación: con treinta, a 300 hPa se cortaban nueve de las
 * sesenta y nueve líneas a media pantalla y otra semilla continuaba el mismo
 * trazo un poco más allá, con un hueco en medio que no lo causaba ninguna
 * vecina. Con sesenta no se corta ninguna en el dominio de AROME.
 */
export const STREAM_MAX_LENGTH = 60;
/**
 * Tramo propio que no cuenta para la comprobación de vecindad, en veces la
 * separación. Sin él, una línea que se curva se choca consigo misma y se corta
 * a las pocas celdas; con él puede cerrar un giro completo.
 */
export const STREAM_SELF_SKIP = 2.5;

/**
 * Pasa un vector físico (este, norte) a la dirección que tiene en la rejilla.
 *
 * Las rejillas son de latitud y longitud: una celda mide lo mismo en grados
 * en los dos ejes, pero de este a oeste son cos φ veces menos kilómetros. Un
 * viento de u m/s cruza por tanto más columnas que filas un viento de v m/s
 * igual, y trazarlo con (u, v) tal cual lo giraba hacia el meridiano: a 60°
 * un suroeste salía 18° desviado y las líneas cortaban las isobaras. El módulo
 * se conserva, que es el que dice la intensidad.
 */
export function gridDirection(frame, u, v, y) {
  // Sin límites geográficos no hay latitud con la que corregir; y un frame
  // reproyectado a la LCC ya tiene celdas cuadradas y el viento girado.
  if (!frame.bounds || frame.lcc) return { u, v, magnitude: Math.hypot(u, v) };
  const [west, south, east, north] = frame.bounds;
  const dLon = (east - west) / frame.width;
  const dLat = (north - south) / frame.height;
  const latitud = (north - y * dLat) * Math.PI / 180;
  const gu = u / (dLon * Math.max(0.05, Math.cos(latitud)));
  const gv = v / dLat;
  const magnitude = Math.hypot(u, v);
  const escala = magnitude / (Math.hypot(gu, gv) || 1);
  return { u: gu * escala, v: gv * escala, magnitude };
}

/**
 * Interpola el vector sin exigir que el campo escalar del mapa tenga dato.
 * En productos como la velocidad vertical en el NCL, el viento de 10 m sigue
 * estando definido incluso donde no se puede calcular el diagnóstico vertical.
 */
export function sampleVectorField(frame, x, y) {
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
  if (indexes.some((index) => !Number.isFinite(frame.u[index]) || !Number.isFinite(frame.v[index]))) return null;
  const weights = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
  const u = indexes.reduce((sum, index, i) => sum + frame.u[index] * weights[i], 0);
  const v = indexes.reduce((sum, index, i) => sum + frame.v[index] * weights[i], 0);
  const vector = gridDirection(frame, u, v, y + 0.5);
  return vector.magnitude >= .35 ? vector : null;
}

/** Rejilla de cubos para preguntar «¿hay algún punto cerca?» en tiempo constante. */
function makeGrid(cell) {
  const buckets = new Map();
  // Clave numérica: con cadenas, construir y comparar el texto de cada cubo
  // se llevaba buena parte del reparto. El desplazamiento admite índices
  // negativos, que salen cuando el encuadre desborda la rejilla.
  const cubo = (cx, cy) => (cx + 32768) * 65536 + (cy + 32768);
  const key = (x, y) => cubo(Math.floor(x / cell), Math.floor(y / cell));
  return {
    add(x, y) {
      const clave = key(x, y);
      const cubo = buckets.get(clave);
      if (cubo) cubo.push(x, y);
      else buckets.set(clave, [x, y]);
    },
    /** ¿Hay algún punto a menos de `distance` de (x, y)? */
    near(x, y, distance) {
      const limite = distance * distance;
      const cx = Math.floor(x / cell);
      const cy = Math.floor(y / cell);
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const puntos = buckets.get(cubo(cx + dx, cy + dy));
          if (!puntos) continue;
          for (let index = 0; index < puntos.length; index += 2) {
            const ex = puntos[index] - x;
            const ey = puntos[index + 1] - y;
            if (ex * ex + ey * ey < limite) return true;
          }
        }
      }
      return false;
    }
  };
}

/**
 * Traza las líneas de corriente del campo que devuelve `sample`.
 *
 * `sample(x, y)` da `{u, v}` en coordenadas de rejilla —con `v` hacia el
 * norte, como el viento— o `null` donde no hay dato. `separation` es la
 * distancia buscada entre líneas vecinas y `step` el paso de integración.
 */
export function evenlySpacedStreamlines({
  sample,
  bounds,
  separation,
  step = separation / 4,
  testRatio = STREAM_TEST_RATIO,
  maxLength = STREAM_MAX_LENGTH * separation,
  minLength = separation * STREAM_MIN_LENGTH,
  // Holgado: con 400 el viento de superficie, que se parte en muchas líneas
  // cortas, gastaba el cupo antes de llegar a los bordes y el mapa ampliado
  // se quedaba con zonas enteras en blanco, distintas según el zoom.
  maxLines = 1500
}) {
  const grid = makeGrid(separation);
  const testDistance = separation * testRatio;
  const lines = [];
  const semillas = [];

  const dentro = (x, y) => x >= bounds.west && x <= bounds.east
    && y >= bounds.north && y <= bounds.south;

  /** Un paso de Runge-Kutta de segundo orden sobre el campo normalizado. */
  const avanzar = (x, y, sentido) => {
    const inicio = sample(x, y);
    if (!inicio) return null;
    const m1 = Math.hypot(inicio.u, inicio.v);
    if (!(m1 > 0)) return null;
    const medioX = x + sentido * step * 0.5 * inicio.u / m1;
    const medioY = y - sentido * step * 0.5 * inicio.v / m1;
    const medio = sample(medioX, medioY);
    if (!medio) return null;
    const m2 = Math.hypot(medio.u, medio.v);
    if (!(m2 > 0)) return null;
    const nx = x + sentido * step * medio.u / m2;
    const ny = y - sentido * step * medio.v / m2;
    // El punto de llegada también tiene que tener dato: con el paso medio a
    // las puertas de un hueco, la línea se metía dentro.
    return sample(nx, ny) ? [nx, ny] : null;
  };

  /**
   * Crece la línea desde la semilla en un sentido.
   *
   * La vecindad se mira en dos rejillas: la de todas las líneas ya trazadas y
   * la de esta misma, que recibe sus puntos con retraso. Lo segundo deja que
   * una línea cierre un giro completo sin cortarse contra el tramo que acaba
   * de dibujar, y que sus dos mitades no se estorben junto a la semilla.
   */
  const crecer = (seedX, seedY, sentido, propia) => {
    const puntos = [];
    const espera = [];
    const atrasados = Math.max(1, Math.round(separation * STREAM_SELF_SKIP / step));
    let x = seedX;
    let y = seedY;
    let recorrido = 0;
    for (;;) {
      const siguiente = avanzar(x, y, sentido);
      if (!siguiente) break;
      const [nx, ny] = siguiente;
      if (!dentro(nx, ny)) break;
      if (grid.near(nx, ny, testDistance)) break;
      if (propia.near(nx, ny, testDistance)) break;
      recorrido += Math.hypot(nx - x, ny - y);
      if (recorrido > maxLength) break;
      puntos.push([nx, ny]);
      espera.push([nx, ny]);
      while (espera.length > atrasados) {
        const [vx, vy] = espera.shift();
        propia.add(vx, vy);
      }
      x = nx;
      y = ny;
    }
    return puntos;
  };

  const trazar = (seedX, seedY) => {
    if (!dentro(seedX, seedY)) return null;
    if (!sample(seedX, seedY)) return null;
    if (grid.near(seedX, seedY, separation)) return null;
    // Los puntos propios viven en su rejilla hasta que la línea se acepta: si
    // entraran en la común, la mitad de ida frenaría a la de vuelta.
    const propia = makeGrid(separation);
    const atras = crecer(seedX, seedY, -1, propia);
    const adelante = crecer(seedX, seedY, 1, propia);
    const puntos = [...atras.reverse(), [seedX, seedY], ...adelante];
    let largo = 0;
    for (let index = 1; index < puntos.length; index += 1) {
      largo += Math.hypot(puntos[index][0] - puntos[index - 1][0], puntos[index][1] - puntos[index - 1][1]);
    }
    if (largo < minLength) return null;
    for (const [px, py] of puntos) grid.add(px, py);
    lines.push({ points: puntos, length: largo });
    // Semillas para la siguiente línea: a un costado y a otro de esta.
    for (let index = 0; index < puntos.length; index += 1) {
      const previo = puntos[Math.max(0, index - 1)];
      const posterior = puntos[Math.min(puntos.length - 1, index + 1)];
      const dx = posterior[0] - previo[0];
      const dy = posterior[1] - previo[1];
      const norma = Math.hypot(dx, dy);
      if (!(norma > 0)) continue;
      const nx = -dy / norma;
      const ny = dx / norma;
      const [px, py] = puntos[index];
      semillas.push([px + nx * separation, py + ny * separation]);
      semillas.push([px - nx * separation, py - ny * separation]);
    }
    return puntos;
  };

  // La primera línea arranca en el centro del encuadre; desde ahí el reparto
  // se propaga solo hacia los bordes.
  const centro = [(bounds.west + bounds.east) / 2, (bounds.north + bounds.south) / 2];
  if (!trazar(centro[0], centro[1])) {
    // Un centro sin dato —mar fuera del dominio, una calma— no puede dejar el
    // mapa vacío: se barre en rejilla hasta encontrar por dónde empezar.
    const paso = separation * 2;
    buscar: for (let y = bounds.north + paso / 2; y < bounds.south; y += paso) {
      for (let x = bounds.west + paso / 2; x < bounds.east; x += paso) {
        if (trazar(x, y)) break buscar;
      }
    }
  }

  // Cola con índice, no con `shift()`: cada línea aceptada deja dos semillas
  // por punto, decenas de miles con el mapa ampliado, y `shift()` recoloca el
  // array entero en cada vuelta. A ×2 el reparto tardaba casi un segundo.
  let siguiente = 0;
  const propagar = () => {
    for (; siguiente < semillas.length && lines.length < maxLines; siguiente += 1) {
      const [x, y] = semillas[siguiente];
      trazar(x, y);
    }
  };
  propagar();

  // Las semillas solo nacen al lado de líneas aceptadas. Donde el viento
  // converge o se encalma, las líneas salen cortas y se descartan, y lo que
  // hay detrás no llega a recibir ninguna: quedaban zonas enteras vacías. Un
  // barrido en rejilla busca esos huecos y vuelve a propagar desde cada uno.
  const paso = separation * 2;
  for (let y = bounds.north + paso / 2; y < bounds.south && lines.length < maxLines; y += paso) {
    for (let x = bounds.west + paso / 2; x < bounds.east && lines.length < maxLines; x += paso) {
      if (trazar(x, y)) propagar();
    }
  }
  return lines;
}

/**
 * Puntos de una línea donde poner una punta de flecha, con su ángulo.
 *
 * Una sola flecha por línea deja tramos largos sin saber hacia dónde van, y
 * una por vértice las amontona: se reparten cada `spacing` de recorrido.
 */
export function streamlineArrows(points, spacing) {
  const marcas = [];
  if (points.length < 3 || !(spacing > 0)) return marcas;
  let recorrido = 0;
  let siguiente = spacing * 0.5;
  for (let index = 1; index < points.length; index += 1) {
    const [x0, y0] = points[index - 1];
    const [x1, y1] = points[index];
    recorrido += Math.hypot(x1 - x0, y1 - y0);
    if (recorrido < siguiente) continue;
    const previo = points[Math.max(0, index - 2)];
    const posterior = points[Math.min(points.length - 1, index + 1)];
    marcas.push({
      x: x1,
      y: y1,
      angle: Math.atan2(posterior[1] - previo[1], posterior[0] - previo[0]) * 180 / Math.PI
    });
    siguiente += spacing;
  }
  return marcas;
}



/**
 * Junta en un solo trazo los tramos que comparten opacidad.
 *
 * Cada tramo era su propio `<path>`, y con su halo al lado: miles de nodos
 * SVG que el navegador volvía a rasterizar en cada paso del gesto, y el mapa
 * se arrastraba. Las opacidades son pocas —las de los escalones del
 * desvanecido—, así que todo el mapa cabe en unos pocos trazos.
 */
export function pathsByOpacity(segments, toPath) {
  const capas = new Map();
  for (const tramo of segments) {
    const opacidad = Math.round(tramo.opacity * 100) / 100;
    const capa = capas.get(opacidad);
    const d = toPath(tramo.points);
    if (capa) capa.push(d);
    else capas.set(opacidad, [d]);
  }
  return [...capas].sort(([a], [b]) => a - b).map(([opacity, trazos]) => ({ opacity, d: trazos.join('') }));
}

/**
 * Todas las puntas de flecha en un solo trazo, en coordenadas de la rejilla.
 *
 * `size` es el tamaño en unidades de rejilla: quien llama lo divide por el
 * zoom asentado para que en pantalla midan siempre lo mismo. Durante un gesto
 * crecen o encogen con el mapa hasta que se vuelve a asentar, que es lo que
 * cuesta no tener un nodo con su propia transformación por cada flecha.
 */
export function arrowHeadsPath(arrows, size) {
  const ala = size * 0.62;
  let d = '';
  for (const { x, y, angle } of arrows) {
    const radianes = angle * Math.PI / 180;
    const cos = Math.cos(radianes);
    const sin = Math.sin(radianes);
    // Las dos alas, giradas con la línea: (−size, ±ala) en el sistema de la flecha.
    const ax = x - size * cos + ala * sin;
    const ay = y - size * sin - ala * cos;
    const bx = x - size * cos - ala * sin;
    const by = y - size * sin + ala * cos;
    d += `M${ax.toFixed(2)},${ay.toFixed(2)}L${x.toFixed(2)},${y.toFixed(2)}L${bx.toFixed(2)},${by.toFixed(2)}`;
  }
  return d;
}

/** Largo del desvanecido de cada punta, en veces la separación. */
export const STREAM_FADE = 1.6;
/** Tramos en que se parte cada punta para desvanecerla. */
export const STREAM_FADE_STEPS = 3;

/**
 * Parte una línea en tramos con su opacidad, desvanecida por las dos puntas.
 *
 * Una línea de corriente empieza y acaba donde le estorba su vecina, no donde
 * el viento deje de soplar, así que un corte limpio a media pantalla se lee
 * como un jirón suelto. Bajando la tinta en las puntas, la línea se va en vez
 * de cortarse, y el mapa se lee como un flujo continuo.
 *
 * Devuelve tramos consecutivos que comparten vértice, de modo que dibujados
 * uno tras otro forman la línea entera sin costuras.
 */
export function fadeSegments(points, {
  fade,
  steps = STREAM_FADE_STEPS,
  minOpacity = 0.12
} = {}) {
  if (points.length < 2) return [];
  const largos = [0];
  for (let index = 1; index < points.length; index += 1) {
    largos.push(largos[index - 1] + Math.hypot(
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1]
    ));
  }
  const total = largos[largos.length - 1];
  // En una línea corta, el desvanecido no puede comerse más de la mitad por
  // punta: se reparte lo que hay.
  const punta = Math.min(fade, total / 2);
  if (!(punta > 0)) return [{ points, opacity: 1 }];

  /** Opacidad en una distancia dada, con las dos rampas. */
  const tinta = (distancia) => {
    const desdeElBorde = Math.min(distancia, total - distancia);
    if (desdeElBorde >= punta) return 1;
    return minOpacity + (1 - minOpacity) * (desdeElBorde / punta);
  };

  // Cortes: los de las dos rampas más el cuerpo entero de una pieza.
  const cortes = [0];
  for (let paso = 1; paso <= steps; paso += 1) cortes.push((punta * paso) / steps);
  for (let paso = steps; paso >= 0; paso -= 1) cortes.push(total - (punta * paso) / steps);

  const tramos = [];
  let indice = 0;
  for (let corte = 1; corte < cortes.length; corte += 1) {
    const desde = cortes[corte - 1];
    const hasta = cortes[corte];
    if (!(hasta > desde)) continue;
    const trozo = [puntoEn(points, largos, desde)];
    while (indice < points.length && largos[indice] <= desde) indice += 1;
    while (indice < points.length && largos[indice] < hasta) {
      trozo.push(points[indice]);
      indice += 1;
    }
    trozo.push(puntoEn(points, largos, hasta));
    tramos.push({ points: trozo, opacity: tinta((desde + hasta) / 2) });
  }
  return tramos;
}

/** Punto de la línea a una distancia dada del principio. */
function puntoEn(points, largos, distancia) {
  if (distancia <= 0) return points[0];
  const total = largos[largos.length - 1];
  if (distancia >= total) return points[points.length - 1];
  let index = 1;
  while (index < largos.length && largos[index] < distancia) index += 1;
  const tramo = largos[index] - largos[index - 1];
  const fraccion = tramo > 0 ? (distancia - largos[index - 1]) / tramo : 0;
  return [
    points[index - 1][0] + (points[index][0] - points[index - 1][0]) * fraccion,
    points[index - 1][1] + (points[index][1] - points[index - 1][1]) * fraccion
  ];
}
