/**
 * Textos y URLs SEO, calcados del generador estático que ya está indexado.
 *
 * Todo lo que hay aquí replica `scripts/build_seo_pages.py`. Los literales no
 * se escriben a mano: llegan en `seo-i18n.generated.json`, que exporta
 * `scripts/export_seo_i18n.py` desde el módulo Python. Si un texto cambia,
 * cambia en Python y se vuelve a exportar; nunca al revés.
 */
import data from './seo-i18n.generated.js';

export const SITE_URL = data.site_url;
export const DEFAULT_LANGUAGE = data.default_language;
export const LANGUAGE_CODES = data.language_order;
export const LANGUAGES = data.languages;
export const PROVIDER_LABELS = data.provider_labels;

/** Idiomas en los que existe la ficha de una estación, según su país. */
export function languageCodesForCountry(country) {
  return data.languages_by_country[String(country || '').toUpperCase()] || ['en', 'es'];
}

export function isLanguage(code) {
  return Object.hasOwn(LANGUAGES, code);
}

export function providerLabel(provider) {
  const key = String(provider || '').toUpperCase();
  return PROVIDER_LABELS[key] || provider || '';
}

export function countryLabel(country, language) {
  const code = String(country || '').trim().toUpperCase();
  if (!code) return '';
  return data.country_labels[code]?.[language] || code;
}

/**
 * `LanguageSpec.t()`: interpola `{clave}` con los valores dados.
 *
 * Python usa `str.format`, que también acepta `{{` literales; en estos textos
 * no aparecen, así que basta con la sustitución simple.
 */
export function t(language, key, values = {}) {
  const template = LANGUAGES[language]?.text?.[key];
  if (template === undefined) return '';
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.hasOwn(values, name) ? String(values[name]) : match
  );
}

export function sensorLabel(language, sensorKey) {
  return LANGUAGES[language]?.sensors?.[sensorKey] || sensorKey;
}

/**
 * `_display_name`: limpia espacios y suaviza los inventarios en mayúsculas.
 *
 * Los catálogos oficiales mezclan `BARCELONA  DRASSANES` con `Barcelona - Zona
 * Universitària`; sin esto el `<h1>` y el `<title>` de media web irían
 * gritando. Equivale a `str.title()` de Python: cada tramo de letras se
 * capitaliza y el resto pasa a minúscula.
 */
export function displayName(value) {
  const clean = String(value ?? '').split(/\s+/).filter(Boolean).join(' ');
  if (!clean) return '';
  const hasLetters = /\p{L}/u.test(clean);
  if (hasLetters && clean === clean.toUpperCase()) {
    return clean.replace(
      /\p{L}+/gu,
      (word) => word[0].toUpperCase() + word.slice(1).toLowerCase()
    );
  }
  return clean;
}

function stationKey(provider, stationId) {
  return `${String(provider || '').toUpperCase()}|${String(stationId || '').toUpperCase()}`;
}

/** Alias de búsqueda: «Observatori Fabra» en vez del código de la red. */
export function stationSearchName(station, language) {
  const alias = data.station_search_names[stationKey(station.provider, station.station_id)];
  return alias?.[language] || displayName(station.name);
}

/** «Barcelona, Cataluña, España» — el mismo orden y deduplicado que en Python. */
export function stationLocationLabel(station, language) {
  const localized =
    data.station_location_names[stationKey(station.provider, station.station_id)]?.[language];
  if (localized) return localized;
  const parts = [];
  for (const value of [
    station.locality,
    station.region,
    countryLabel(stationCountry(station), language)
  ]) {
    const clean = String(value ?? '').trim();
    if (clean && !parts.includes(clean)) parts.push(clean);
  }
  return parts.join(', ');
}

/**
 * País efectivo de la estación.
 *
 * El generador estático confía primero en la red (`PROVIDER_COUNTRIES`)
 * porque en el catálogo hay filas con el país vacío.
 */
export function stationCountry(station) {
  const provider = String(station.provider || '').toUpperCase();
  const fromCatalog = String(station.country || '').trim().toUpperCase();
  return fromCatalog || data.provider_countries[provider] || '';
}
