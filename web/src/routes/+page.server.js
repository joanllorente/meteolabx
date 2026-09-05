import { geocode, fetchStationsNear } from '$lib/server/api.js';
import { LANGUAGES } from '$lib/seo/i18n.js';
import { negotiateLanguage } from '$lib/server/language.js';

/**
 * La portada: el panel sin ninguna estación conectada.
 *
 * La búsqueda se resuelve en el servidor a partir de `?q=`, así que funciona
 * escribiendo la URL a mano, sin JavaScript y para un buscador. Si además hay
 * `?lat=`/`?lon=` —los rellena el botón de geolocalización— se salta el
 * geocodificador y se va directo al catálogo.
 */
export async function load({ url, params, request, fetch, setHeaders }) {
  const query = (url.searchParams.get('q') || '').trim().slice(0, 120);
  // `/ca`, `/en`… lo dicen en la ruta. La raíz no, y ahí se negocia con la
  // lista de idiomas del navegador: es lo que espera quien llega de fuera, y
  // deja la URL limpia para que sea la versión x-default del sitio.
  const language =
    params?.lang || negotiateLanguage(request.headers.get('accept-language'), Object.keys(LANGUAGES));

  // Ojo con `Number(null)`: vale 0, que es un número perfectamente finito y
  // además una coordenada válida en el Golfo de Guinea. Sin comprobar que el
  // parámetro existe, la portada buscaba siempre estaciones en (0, 0).
  const coordinates = readCoordinates(url.searchParams);

  // Las redes de particulares —Netatmo, Windy— son la mayoría de lo que hay
  // cerca en ciudad: se enseñan, y quien no las quiera las apaga.
  const hideAmateur = url.searchParams.get('sin-particulares') === 'si';

  let place = null;
  let results = [];
  let failed = false;

  // «41.38, 2.17» escrito en la caja es un punto, no un topónimo: se resuelve
  // sin pasar por el geocodificador.
  const typedPoint = parseCoordinates(query);

  if (coordinates || typedPoint || query) {
    try {
      const point =
        coordinates ?? typedPoint ?? (await resolvePlace(query, language, fetch));
      if (point) {
        place = point.label;
        const payload = await fetchStationsNear(
          { ...point, radiusKm: 25, limit: 12, hideAmateur },
          { fetch }
        );
        results = payload.stations;
      }
    } catch {
      // La portada tiene que pintar igual aunque el geocodificador o el
      // catálogo fallen: se enseña el estado vacío con el aviso.
      failed = true;
    }
  }

  setHeaders({ 'cache-control': 'public, max-age=120' });
  return {
    language, query, place, results, failed, hideAmateur,
    searched: Boolean(query || coordinates)
  };
}

/** «41.38, 2.17» o «41.38 2.17» → punto. Devuelve null si no lo parece. */
function parseCoordinates(text) {
  const match = String(text || '').match(
    /^\s*(-?\d{1,3}(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/
  );
  if (!match) return null;
  const lat = Number(match[1].replace(',', '.'));
  const lon = Number(match[2].replace(',', '.'));
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon, label: `${lat}, ${lon}` };
}

function readCoordinates(params) {
  if (!params.has('lat') || !params.has('lon')) return null;
  const lat = Number(params.get('lat'));
  const lon = Number(params.get('lon'));
  const usable =
    Number.isFinite(lat) && Number.isFinite(lon) &&
    Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
  return usable ? { lat, lon } : null;
}

async function resolvePlace(query, language, fetch) {
  const match = await geocode(query, { language }, { fetch });
  if (!match?.found) return null;
  return { lat: match.lat, lon: match.lon, label: match.display_name };
}
