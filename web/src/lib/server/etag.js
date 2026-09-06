/**
 * ETags de las páginas que Googlebot revisita.
 *
 * Las fichas de observación son ~300.000 URLs y el rastreador vuelve a todas
 * mucho más de lo que cambian: la mayoría de estaciones publica cada 10-60
 * minutos y bastantes llevan días sin emitir. Sin validador, cada revisita se
 * paga con el HTML entero; con él, la que no ha cambiado se resuelve en un 304
 * vacío y ese presupuesto de rastreo se va a las páginas que aún no conoce.
 *
 * El ETag es débil a propósito: no promete que el byte sea idéntico, solo que
 * el contenido lo es. La semilla lleva la versión de la app porque el marcado
 * también cambia al desplegar, no solo el dato.
 */
import app from '$lib/i18n/app-i18n.generated.js';

const BUILD = String(app.app_version || '0');

/** FNV-1a de 32 bits: sin dependencias y de sobra para distinguir versiones. */
function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(36);
}

/**
 * ETag débil a partir de lo que hace única a la respuesta.
 *
 * Se le añade la longitud de la semilla porque dos entradas distintas con el
 * mismo hash de 32 bits dejan de colisionar en cuanto difieren en tamaño.
 */
export function contentEtag(...parts) {
  const seed = [BUILD, ...parts].map((part) => String(part ?? '')).join(' ');
  return `W/"${fnv1a(seed)}-${seed.length.toString(36)}"`;
}

/**
 * Lo que distingue una observación de la siguiente.
 *
 * Normalmente basta el instante de la medición. Cuando el proveedor falla, la
 * ficha se sirve igual explicando por qué, y ahí lo que identifica al
 * contenido es el motivo: así una estación que pasa de dar dato a no darlo
 * cambia de ETag en vez de quedarse congelada en la última medición buena.
 *
 * Devuelve `null` cuando el dato viene sin marca de tiempo utilizable. Ahí no
 * hay forma de saber si ha cambiado, y un validador que se queda quieto sobre
 * contenido que se mueve es peor que no tener ninguno: la ficha se sirve sin
 * ETag y se revalida como hasta ahora.
 */
export function observationVersion(observation) {
  if (!observation) return 'none';
  const failure = observation.unavailable;
  if (failure) return `off ${failure.status ?? ''} ${failure.code ?? ''}`;
  const epoch = Number(observation.epoch);
  return Number.isFinite(epoch) && epoch > 0 ? String(epoch) : null;
}

/** Un ETag sin el prefijo `W/` ni las comillas, para compararlos como débiles. */
function bare(tag) {
  return tag.trim().replace(/^W\//, '').replace(/^"|"$/g, '');
}

/**
 * ¿La copia que dice tener el cliente sigue valiendo?
 *
 * `If-None-Match` admite una lista y el comodín `*`. La comparación es débil
 * —la que corresponde a un GET condicional— así que `W/"x"` y `"x"` casan.
 */
export function matchesEtag(header, etag) {
  if (!header || !etag) return false;
  const candidates = String(header).split(',').map(bare).filter(Boolean);
  return candidates.includes('*') || candidates.includes(bare(etag));
}
