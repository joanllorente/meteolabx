/**
 * Cónica conforme de Lambert (LCC) para los dominios regionales de ECMWF.
 *
 * Los datos llegan en latitud y longitud, y pintarlos tal cual estira el
 * este-oeste por 1/cos φ: el triple a 70° N. Aquí se reproyecta cada frame a
 * una rejilla regular en km de la LCC que trae en su cabecera. Todo lo que se
 * calcula después —color, isolíneas, centros, vaguadas, líneas de corriente—
 * sigue trabajando en celdas de rejilla como hasta ahora, solo que ahora son
 * celdas cuadradas de verdad; lo que tiene coordenadas geográficas —fronteras,
 * ciudades, ciclones, el cursor— pasa por `frameGeo`.
 *
 * Mismas fórmulas que `server/services/map_projection.py` (Snyder, 1987, §15).
 */

const EARTH_RADIUS_KM = 6371;
const rad = Math.PI / 180;
// Celda de la rejilla reproyectada. Algo más fina que los ~28 km de norte a
// sur de 0,25°, para no perder el detalle este-oeste de las latitudes altas.
export const PROJECTED_CELL_KM = 20;

const wrap = (lon) => ((lon + 180) % 360 + 360) % 360 - 180;

export function lcc({ lon0, lat0, lat1, lat2 }) {
  const signo = lat1 + lat2 > 0 ? 1 : -1;
  const f1 = signo * lat1 * rad;
  const f2 = signo * lat2 * rad;
  const f0 = signo * lat0 * rad;
  const t = (phi) => Math.tan(Math.PI / 4 + phi / 2);
  const n = Math.abs(f1 - f2) < 1e-9
    ? Math.sin(f1)
    : Math.log(Math.cos(f1) / Math.cos(f2)) / Math.log(t(f2) / t(f1));
  const F = Math.cos(f1) * t(f1) ** n / n;
  const rho0 = EARTH_RADIUS_KM * F / t(f0) ** n;
  return {
    n,
    sign: signo,
    lon0,
    /** Ángulo entre el norte geográfico y el eje vertical de la rejilla. */
    theta(lon) {
      return n * wrap(lon - lon0) * rad;
    },
    forward(lon, lat) {
      const theta = n * wrap(lon - lon0) * rad;
      const rho = EARTH_RADIUS_KM * F / t(signo * lat * rad) ** n;
      return [rho * Math.sin(theta), signo * (rho0 - rho * Math.cos(theta))];
    },
    inverse(x, y) {
      const yy = signo * y;
      const rho = Math.hypot(x, rho0 - yy);
      const theta = Math.atan2(x, rho0 - yy);
      const phi = 2 * Math.atan((EARTH_RADIUS_KM * F / rho) ** (1 / n)) - Math.PI / 2;
      return [wrap(lon0 + theta / n / rad), signo * phi / rad];
    }
  };
}

/** Rectángulo de la proyección en km: [xmin, ymin, xmax, ymax]. */
export function projectionRectangle(projection) {
  if (projection.rect) return projection.rect;
  const [x0, y0] = lcc(projection).forward(projection.lon0, projection.lat0);
  const ancho = projection.width_km / 2;
  const alto = projection.height_km / 2;
  return [x0 - ancho, y0 - alto, x0 + ancho, y0 + alto];
}

/**
 * Paso entre coordenadas geográficas y de rejilla para cualquier frame: el de
 * latitud y longitud de siempre o uno reproyectado.
 */
export function frameGeo(frame) {
  if (frame?.lcc) return frame.lcc.geo;
  const [west, south, east, north] = frame.bounds;
  const sx = frame.width / (east - west);
  const sy = frame.height / (north - south);
  return {
    toGrid: (longitude, latitude) => [(longitude - west) * sx, (north - latitude) * sy],
    toGeo: (x, y) => [west + x / sx, north - y / sy]
  };
}

// Tablas de muestreo por forma de rejilla: todas las horas y productos de un
// dominio comparten la misma, así que se calculan una vez.
const tablas = new Map();

function tablaDeMuestreo(projection, bounds, width, height) {
  const clave = JSON.stringify([projection, bounds, width, height]);
  const guardada = tablas.get(clave);
  if (guardada) return guardada;
  const proyeccion = lcc(projection);
  const [xmin, ymin, xmax, ymax] = projectionRectangle(projection);
  const celda = projection.cell_km || PROJECTED_CELL_KM;
  const ancho = Math.round((xmax - xmin) / celda);
  const alto = Math.round((ymax - ymin) / celda);
  const [west, south, east, north] = bounds;
  const dLon = (east - west) / width;
  const dLat = (north - south) / height;
  const origen = new Float32Array(ancho * alto * 2);
  const coseno = new Float32Array(ancho * alto);
  const seno = new Float32Array(ancho * alto);
  const latitud = new Float32Array(ancho * alto);
  for (let fila = 0; fila < alto; fila += 1) {
    const y = ymax - (fila + 0.5) * celda;
    for (let columna = 0; columna < ancho; columna += 1) {
      const indice = fila * ancho + columna;
      const [lon, lat] = proyeccion.inverse(xmin + (columna + 0.5) * celda, y);
      // Posición en la rejilla de origen, con los centros de celda en .0.
      const relativa = west + wrap(lon - west);
      origen[indice * 2] = (relativa - west) / dLon - 0.5;
      origen[indice * 2 + 1] = (north - lat) / dLat - 0.5;
      const theta = proyeccion.theta(lon);
      coseno[indice] = Math.cos(theta);
      seno[indice] = Math.sin(theta);
      latitud[indice] = lat;
    }
  }
  const geo = {
    toGrid(longitude, latitude) {
      const [x, y] = proyeccion.forward(longitude, latitude);
      return [(x - xmin) / celda, (ymax - y) / celda];
    },
    toGeo(x, y) {
      return proyeccion.inverse(xmin + x * celda, ymax - y * celda);
    }
  };
  const tabla = { ancho, alto, origen, coseno, seno, latitud, geo, celda, sign: proyeccion.sign };
  if (tablas.size > 24) tablas.clear();
  tablas.set(clave, tabla);
  return tabla;
}

/** Celda más cercana: para campos por categorías, que no se promedian. */
function muestrearCercana(campo, width, height, gx, gy) {
  if (gx < -0.5 || gy < -0.5 || gx > width - 0.5 || gy > height - 0.5) return NaN;
  const cx = Math.max(0, Math.min(width - 1, Math.round(gx)));
  const cy = Math.max(0, Math.min(height - 1, Math.round(gy)));
  return campo[cy * width + cx];
}

/** Bilineal; si falta alguna esquina, la celda más cercana. */
function muestrear(campo, width, height, gx, gy) {
  if (gx < -0.5 || gy < -0.5 || gx > width - 0.5 || gy > height - 0.5) return NaN;
  const x0 = Math.max(0, Math.min(width - 2, Math.floor(gx)));
  const y0 = Math.max(0, Math.min(height - 2, Math.floor(gy)));
  const fx = Math.max(0, Math.min(1, gx - x0));
  const fy = Math.max(0, Math.min(1, gy - y0));
  const a = campo[y0 * width + x0];
  const b = campo[y0 * width + x0 + 1];
  const c = campo[(y0 + 1) * width + x0];
  const d = campo[(y0 + 1) * width + x0 + 1];
  if (Number.isFinite(a) && Number.isFinite(b) && Number.isFinite(c) && Number.isFinite(d)) {
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  }
  const cx = Math.max(0, Math.min(width - 1, Math.round(gx)));
  const cy = Math.max(0, Math.min(height - 1, Math.round(gy)));
  return campo[cy * width + cx];
}

/**
 * LCC a medida de un recuadro de latitud y longitud.
 *
 * Meridiano central y paralelo de origen en el centro del recuadro, y los dos
 * paralelos estándar a un sexto de cada borde, que es donde el error de escala
 * queda más repartido. El rectángulo pasa por los puntos medios de los lados:
 * en una LCC los paralelos son arcos, y el recuadro de latitud y longitud se
 * convierte en un abanico; su rectángulo envolvente tendría las esquinas fuera
 * y el inscrito se comería los extremos.
 *
 * Con `dataBounds`, los lados que se salen de los datos se meten hasta que el
 * rectángulo queda entero dentro: una zona pegada al borde del modelo no deja
 * esquinas vacías.
 */
export function projectionForBox([west, south, east, north], { dataBounds = null, inside = null, cellKm } = {}) {
  const lon0 = (west + east) / 2;
  const lat0 = (south + north) / 2;
  const base = {
    lon0, lat0, lat1: south + (north - south) / 6, lat2: north - (north - south) / 6
  };
  const proyeccion = lcc(base);
  let xmin = proyeccion.forward(west, lat0)[0];
  let xmax = proyeccion.forward(east, lat0)[0];
  let ymin = proyeccion.forward(lon0, south)[1];
  let ymax = proyeccion.forward(lon0, north)[1];
  if (dataBounds || inside) {
    const [dw, ds, de, dn] = dataBounds || [-180, -90, 180, 90];
    const dentro = ([lon, lat]) => lon >= dw && lon <= de && lat >= ds && lat <= dn
      && (!inside || inside(lon, lat));
    // Fracción de cada lado que se sale de los datos. En cada paso se mete
    // solo el lado que más se sale: si una zona pasa del borde este, basta con
    // mover el lado este, y los de arriba y abajo —que solo se salen por esa
    // esquina— quedan dentro en cuanto se mueve.
    const fuera = (fijo, desde, hasta, vertical) => {
      let cuenta = 0;
      for (let i = 0; i <= 40; i += 1) {
        const t = desde + (hasta - desde) * i / 40;
        if (!dentro(vertical ? proyeccion.inverse(fijo, t) : proyeccion.inverse(t, fijo))) cuenta += 1;
      }
      return cuenta / 41;
    };
    const paso = Math.min(xmax - xmin, ymax - ymin) / 400;
    for (let vuelta = 0; vuelta < 4000 && xmax - xmin > paso && ymax - ymin > paso; vuelta += 1) {
      const lados = [
        [fuera(ymin, xmin, xmax, false), () => { ymin += paso; }],
        [fuera(ymax, xmin, xmax, false), () => { ymax -= paso; }],
        [fuera(xmin, ymin, ymax, true), () => { xmin += paso; }],
        [fuera(xmax, ymin, ymax, true), () => { xmax -= paso; }]
      ];
      const [peor, mover] = lados.reduce((a, b) => (b[0] > a[0] ? b : a));
      if (peor === 0) break;
      mover();
    }
  }
  // Una zona que no toca los datos se queda en nada: sin proyección propia.
  const celda = cellKm || PROJECTED_CELL_KM;
  if ((xmax - xmin) / celda < 8 || (ymax - ymin) / celda < 8) return null;
  return { ...base, rect: [xmin, ymin, xmax, ymax], ...(cellKm ? { cell_km: cellKm } : {}) };
}

const cache = new WeakMap();

/**
 * El frame reproyectado a una LCC: la de su cabecera o la que se le pase.
 *
 * El viento se gira además al eje de la rejilla: en la LCC los meridianos
 * convergen, y el norte de cada punto se inclina θ = n·(λ − λ₀) respecto al
 * vertical del mapa. Con `nearest`, los valores se toman de la celda más
 * cercana en vez de interpolarse: un tipo de precipitación no tiene término
 * medio entre lluvia y nieve.
 */
export function projectFrame(frame, projection = frame?.projection, { nearest = false } = {}) {
  if (!projection || !frame?.bounds || frame.lcc) return frame;
  const clave = `${JSON.stringify(projection)}|${nearest}`;
  let porProyeccion = cache.get(frame);
  const guardado = porProyeccion?.get(clave);
  if (guardado) return guardado;
  const tabla = tablaDeMuestreo(projection, frame.bounds, frame.width, frame.height);
  const { ancho, alto, origen, coseno, seno, sign } = tabla;
  const total = ancho * alto;
  const proyectar = (campo, cercana = false) => {
    if (!campo) return campo;
    const leer = cercana ? muestrearCercana : muestrear;
    const salida = new Float32Array(total);
    for (let i = 0; i < total; i += 1) {
      salida[i] = leer(campo, frame.width, frame.height, origen[i * 2], origen[i * 2 + 1]);
    }
    return salida;
  };
  const resultado = {
    ...frame,
    width: ancho,
    height: alto,
    values: proyectar(frame.values, nearest),
    overlay: proyectar(frame.overlay),
    u: null,
    v: null,
    latlonBounds: frame.bounds,
    bounds: null,
    projection,
    cellKm: tabla.celda,
    lcc: { geo: tabla.geo, latitude: tabla.latitud, sign }
  };
  if (frame.u && frame.v) {
    const u = proyectar(frame.u);
    const v = proyectar(frame.v);
    for (let i = 0; i < total; i += 1) {
      const este = u[i];
      const norte = v[i];
      u[i] = este * coseno[i] - sign * norte * seno[i];
      v[i] = sign * este * seno[i] + norte * coseno[i];
    }
    resultado.u = u;
    resultado.v = v;
  }
  if (!porProyeccion) {
    porProyeccion = new Map();
    cache.set(frame, porProyeccion);
  }
  porProyeccion.set(clave, resultado);
  return resultado;
}

/**
 * Dominio con dato real de AROME, en su propia LCC.
 *
 * AROME calcula en una rejilla Lambert, y Météo-France la interpola a
 * latitud y longitud para los datos abiertos: fuera de la rejilla original el
 * recuadro de 0,025° viene vacío, y lo que tiene dato es un trapecio. En esta
 * LCC —la del recuadro, que casi coincide con la del modelo— ese trapecio
 * vuelve a ser un rectángulo, medido sobre los frames: es el que se enseña,
 * sin franjas vacías a los lados.
 */
export const AROME_LCC = {
  lon0: 2, lat0: 46.45, lat1: 40.475, lat2: 52.425,
  rect: [-897, -937, 900, 990],
  cell_km: 2.5
};
const AROME_BOUNDS = [-12.0125, 37.4875, 16.0125, 55.4125];

function dentroDeArome(lon, lat) {
  const [x, y] = lcc(AROME_LCC).forward(lon, lat);
  const [xmin, ymin, xmax, ymax] = AROME_LCC.rect;
  return x >= xmin && x <= xmax && y >= ymin && y <= ymax;
}

/**
 * LCC con la que se pinta un frame de AROME: la del dominio con dato si el
 * frame es el dominio entero, o una a medida de la zona, metida dentro de ese
 * dominio. Un frame recortado —en local, solo Cataluña— usa su propio recuadro.
 */
export function aromeProjection(frameBounds, zoneBounds = null) {
  const completo = frameBounds.every((valor, i) => Math.abs(valor - AROME_BOUNDS[i]) < 0.05);
  const propio = completo ? AROME_LCC : projectionForBox(frameBounds, { cellKm: AROME_LCC.cell_km });
  if (!zoneBounds) return propio;
  return projectionForBox(zoneBounds, {
    dataBounds: frameBounds,
    inside: completo ? dentroDeArome : null,
    cellKm: AROME_LCC.cell_km
  }) || propio;
}
