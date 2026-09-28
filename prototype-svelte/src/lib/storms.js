/**
 * Nombre de los ciclones tropicales sobre las bajas del modelo.
 *
 * El modelo no sabe cómo se llama nada: el nombre viene del aviso del NHC,
 * que da la posición actual del ciclón y la prevista hasta cinco días. En cada
 * hora válida se busca la baja del campo de presión del modelo más cercana a
 * esa posición, y el nombre se pone allí, en la baja del modelo y no en el
 * punto del aviso: el mapa enseña lo que prevé el modelo, y el nombre tiene
 * que caer encima de su ciclón aunque vaya adelantado o retrasado respecto al
 * NHC. Sin baja cerca, o más allá del último punto del aviso, no hay nombre.
 */

import { pressureCentres, CENTRE_PROMINENCE_HPA } from './pressureCentres.js';

const RADIO_TIERRA_KM = 6371;
// Distancia máxima entre la baja del modelo y la posición del aviso. Crece con
// el plazo porque crece el error de ambos: unos 70 km al día sobre la base.
const RADIO_BASE_KM = 300;
const RADIO_POR_DIA_KM = 70;
const RADIO_MAXIMO_KM = 650;
// Horas antes del primer punto del aviso en las que aún se busca el ciclón:
// una pasada del modelo suele ser algo anterior al último aviso.
const MARGEN_PREVIO_H = 12;
// Un mínimo que el detector de centros no marca —una depresión tropical
// pequeña— se acepta si queda al menos esto por debajo de su entorno.
const HUNDIMIENTO_MINIMO_HPA = 2;

export function distanceKm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * RADIO_TIERRA_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Posición del aviso a una hora válida, interpolada entre sus puntos. */
export function stormPosition(storm, validIso) {
  const track = storm?.track || [];
  if (!track.length) return null;
  const t = Date.parse(validIso);
  const tiempos = track.map((punto) => Date.parse(punto.time));
  if (t < tiempos[0]) {
    const horas = (tiempos[0] - t) / 3.6e6;
    return horas <= MARGEN_PREVIO_H ? { ...track[0], hoursOff: horas } : null;
  }
  if (t > tiempos[tiempos.length - 1]) return null;
  for (let i = 0; i < track.length - 1; i += 1) {
    if (t > tiempos[i + 1]) continue;
    const f = (t - tiempos[i]) / (tiempos[i + 1] - tiempos[i] || 1);
    const a = track[i];
    const b = track[i + 1];
    return {
      latitude: a.latitude + (b.latitude - a.latitude) * f,
      longitude: a.longitude + (b.longitude - a.longitude) * f,
      stage: f < 0.5 ? a.stage : b.stage,
      hoursOff: 0
    };
  }
  return { ...track[track.length - 1], hoursOff: 0 };
}

function radioBusqueda(storm, validIso, hoursOff) {
  const inicio = Date.parse(storm.track[0].time);
  const dias = Math.max(0, (Date.parse(validIso) - inicio) / 8.64e7);
  return Math.min(RADIO_MAXIMO_KM, RADIO_BASE_KM + RADIO_POR_DIA_KM * dias + 25 * hoursOff);
}

/** Mínimo del campo dentro del radio, si se hunde lo bastante respecto al borde. */
function minimoLocal(frame, lat, lon, radioKm) {
  const [west, south, east, north] = frame.bounds;
  const dLon = (east - west) / frame.width;
  const dLat = (north - south) / frame.height;
  const campo = frame.overlay;
  let mejor = null;
  let borde = 0;
  let nBorde = 0;
  const filas = Math.ceil(radioKm / (dLat * 111));
  const fila0 = Math.round((north - lat) / dLat - 0.5);
  for (let fila = Math.max(0, fila0 - filas); fila <= Math.min(frame.height - 1, fila0 + filas); fila += 1) {
    const latCelda = north - (fila + 0.5) * dLat;
    const cols = Math.ceil(radioKm / (dLon * 111 * Math.max(0.2, Math.cos(latCelda * Math.PI / 180))));
    const col0 = Math.round((lon - west) / dLon - 0.5);
    for (let col = Math.max(0, col0 - cols); col <= Math.min(frame.width - 1, col0 + cols); col += 1) {
      const valor = campo[fila * frame.width + col];
      if (!Number.isFinite(valor)) continue;
      const d = distanceKm(lat, lon, latCelda, west + (col + 0.5) * dLon);
      if (d > radioKm) continue;
      if (d > radioKm * 0.8) { borde += valor; nBorde += 1; }
      if (!mejor || valor < mejor.value) mejor = { x: col + 0.5, y: fila + 0.5, value: valor };
    }
  }
  if (!mejor || !nBorde || borde / nBorde - mejor.value < HUNDIMIENTO_MINIMO_HPA) return null;
  return mejor;
}

/**
 * Ciclones con nombre en un frame de presión al nivel del mar.
 *
 * Devuelve la posición geográfica de la baja del modelo, para poder pintarla
 * sobre cualquier otro mapa de la misma hora y dominio.
 */
export function nameStorms(mslpFrame, storms, validIso) {
  if (!mslpFrame?.overlay || !mslpFrame.bounds || !storms?.length) return [];
  const [west, south, east, north] = mslpFrame.bounds;
  const dLon = (east - west) / mslpFrame.width;
  const dLat = (north - south) / mslpFrame.height;
  const aGeo = (punto) => ({ longitude: west + punto.x * dLon, latitude: north - punto.y * dLat });
  const cellKm = (north - south) / mslpFrame.height * 100 || 2.5;
  const bajas = pressureCentres(mslpFrame.overlay, {
    width: mslpFrame.width,
    height: mslpFrame.height,
    cellKm,
    block: Math.max(1, Math.round(10 / cellKm)),
    prominenceHpa: CENTRE_PROMINENCE_HPA
  }).filter((centro) => centro.type === 'low').map((centro) => ({ ...centro, ...aGeo(centro) }));

  const nombrados = [];
  const usadas = new Set();
  for (const storm of storms) {
    const posicion = stormPosition(storm, validIso);
    if (!posicion) continue;
    const radio = radioBusqueda(storm, validIso, posicion.hoursOff || 0);
    // Fuera del dominio, con un margen del propio radio.
    const margenLat = radio / 111;
    if (posicion.latitude < south - margenLat || posicion.latitude > north + margenLat) continue;
    if (posicion.longitude < west - 2 * margenLat || posicion.longitude > east + 2 * margenLat) continue;

    let elegida = null;
    let distancia = Infinity;
    for (const baja of bajas) {
      if (usadas.has(baja)) continue;
      const d = distanceKm(posicion.latitude, posicion.longitude, baja.latitude, baja.longitude);
      if (d <= radio && d < distancia) { elegida = baja; distancia = d; }
    }
    if (elegida) usadas.add(elegida);
    else {
      const minimo = minimoLocal(mslpFrame, posicion.latitude, posicion.longitude, radio);
      if (minimo) elegida = { ...minimo, ...aGeo(minimo) };
    }
    if (!elegida) continue;
    nombrados.push({
      id: storm.id,
      name: storm.name,
      classification: storm.classification,
      stage: posicion.stage || 'tropical',
      latitude: elegida.latitude,
      longitude: elegida.longitude,
      pressure: elegida.value
    });
  }
  return nombrados;
}

/** ¿Algún ciclón del aviso cae en este dominio y a esta hora? */
export function stormsNear(storms, bounds, validIso) {
  if (!storms?.length || !bounds) return false;
  const [west, south, east, north] = bounds;
  return storms.some((storm) => {
    const p = stormPosition(storm, validIso);
    return p && p.latitude >= south - 5 && p.latitude <= north + 5
      && p.longitude >= west - 10 && p.longitude <= east + 10;
  });
}
