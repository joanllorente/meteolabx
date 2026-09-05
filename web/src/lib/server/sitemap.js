/**
 * Sitemaps de las fichas de observación.
 *
 * El generador estático escribía un `sitemap.xml` con las 300.000 URLs de las
 * fichas `.html`. Esas URLs ahora redirigen, así que el sitemap lo publica el
 * frontend nuevo y apunta directamente a `/{idioma}/observation/{slug}`. El
 * formato es el mismo que ya usaba producción cuando superaba el límite:
 * un índice y tramos planos de `<loc>`, sin alternates —con este volumen, el
 * bloque de alternates repetido en cada entrada multiplicaba por seis el peso
 * del fichero sin que Google sacara nada nuevo de él.
 *
 * El catálogo se pide una vez y se guarda en memoria: son 49.000 estaciones y
 * cambian cuando se reconstruye el catálogo, no entre visitas.
 */
import { fetchIndexableCatalog } from './api.js';
import seo from '$lib/seo/seo-i18n.generated.js';
import { SITE_URL, languageCodesForCountry } from '$lib/seo/i18n.js';
import { observationUrl } from '$lib/seo/station.js';

const PROVIDER_COUNTRIES = seo.provider_countries;

export const SITEMAP_URL_LIMIT = 50_000;
const CATALOG_PAGE_SIZE = 25_000;
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;

/** Las mismas dos URLs sueltas que ya listaba el generador estático. */
export const STATIC_URLS = [`${SITE_URL}/`, `${SITE_URL}/forecast`];

/**
 * Sitemap que sigue generando Python con los directorios, los índices de red
 * y las páginas de ciudad. Vive en el servicio antiguo y el índice lo enlaza
 * para no dejarlo fuera mientras esas páginas no se migren.
 */
export const LEGACY_DIRECTORY_SITEMAP = `${SITE_URL}/directories-sitemap.xml`;

let cache = null;

function xmlEscape(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function urlset(urls) {
  const entries = urls.map((url) => `  <url><loc>${xmlEscape(url)}</loc></url>`).join('\n');
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    `${entries}\n</urlset>\n`
  );
}

async function loadObservationUrls(fetchImpl) {
  const urls = [];
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const page = await fetchIndexableCatalog(
      { offset, limit: CATALOG_PAGE_SIZE },
      { fetch: fetchImpl, timeoutMs: 60_000 }
    );
    total = page.total;
    if (!page.count) break;
    for (const station of page.stations) {
      // Cada ficha existe solo en los idiomas de su país, igual que antes.
      for (const language of languageCodesForCountry(
        station.catalog_country || countryForProvider(station.provider)
      )) {
        urls.push(observationUrl(language, station.url_slug));
      }
    }
    offset += page.count;
  }
  return urls;
}

/** Fallback cuando el catálogo no trae país: lo decide la red, como en Python. */
function countryForProvider(provider) {
  return PROVIDER_COUNTRIES[String(provider || '').toUpperCase()] || '';
}

/** Tramos ya renderizados, listos para servir. */
export async function sitemapChunks(fetchImpl) {
  if (cache && Date.now() - cache.at < CACHE_TTL_MS) return cache.chunks;
  const urls = await loadObservationUrls(fetchImpl);
  const chunks = [];
  for (let offset = 0; offset < urls.length; offset += SITEMAP_URL_LIMIT) {
    chunks.push(urlset(urls.slice(offset, offset + SITEMAP_URL_LIMIT)));
  }
  cache = { at: Date.now(), chunks, count: urls.length };
  return chunks;
}

export function staticSitemap() {
  return urlset(STATIC_URLS);
}

export async function sitemapIndex(fetchImpl) {
  const chunks = await sitemapChunks(fetchImpl);
  const names = [
    `${SITE_URL}/sitemap-static.xml`,
    LEGACY_DIRECTORY_SITEMAP,
    ...chunks.map((_, index) => `${SITE_URL}/sitemap-observation-${index + 1}.xml`)
  ];
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    names.map((url) => `  <sitemap><loc>${xmlEscape(url)}</loc></sitemap>`).join('\n') +
    '\n</sitemapindex>\n'
  );
}

/** Se vacía la caché al reconstruir el catálogo (`POST /sitemap/refresh`). */
export function resetSitemapCache() {
  cache = null;
}
