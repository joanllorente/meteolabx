/**
 * URLs canónicas, metadatos y datos estructurados de la ficha de estación.
 *
 * Las páginas estáticas vivían en `/{idioma}/{directorio}/{red}/{slug}.html`.
 * Las nuevas viven en `/{idioma}/observation/{slug}` y las antiguas redirigen
 * con 301 (ver `src/hooks.server.js`). Lo que NO cambia es el contenido de
 * `<title>`, `description` y JSON-LD: si cambiaran, se tiraría el
 * posicionamiento que ya existe.
 */
import {
  DEFAULT_LANGUAGE,
  LANGUAGES,
  LANGUAGE_CODES,
  SITE_URL,
  displayName,
  languageCodesForCountry,
  providerLabel,
  stationCountry,
  stationLocationLabel,
  stationSearchName,
  t
} from './i18n.js';

export const OBSERVATION_SEGMENT = 'observation';

export function observationPath(language, slug) {
  return `/${language}/${OBSERVATION_SEGMENT}/${slug}`;
}

export function observationUrl(language, slug) {
  return `${SITE_URL}${observationPath(language, slug)}`;
}

/** Directorio de estaciones de un idioma (sigue siendo la página estática). */
export function directoryPath(language) {
  return `/${language}/${LANGUAGES[language].directory_slug}.html`;
}

/** Índice de una red dentro de un idioma (también estático todavía). */
export function providerPath(provider, language) {
  const slug = String(provider || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return `/${language}/${LANGUAGES[language].directory_slug}/${slug}.html`;
}

/**
 * Idiomas en los que se publica la ficha, en el orden que llevan hoy los
 * `<link rel="alternate">`.
 */
export function stationLanguages(station) {
  const available = new Set(languageCodesForCountry(stationCountry(station)));
  return LANGUAGE_CODES.filter((code) => available.has(code));
}

/**
 * Idioma principal de la ficha: el primero que el país declara.
 *
 * No es lo mismo que el primero de `stationLanguages`, que va en el orden del
 * diccionario global (castellano primero). Una estación de Wyoming pedida en
 * catalán tiene que caer en la inglesa, no en la castellana.
 */
export function primaryLanguage(station) {
  return languageCodesForCountry(stationCountry(station))[0] || DEFAULT_LANGUAGE;
}

export function stationAlternates(station, slug) {
  return stationLanguages(station).map((code) => ({
    code,
    url: observationUrl(code, slug)
  }));
}

/**
 * Todo lo que necesita `<svelte:head>`: títulos, canonical, alternates y los
 * dos bloques JSON-LD (BreadcrumbList y Place) de la ficha original.
 */
export function stationMeta(station, language, slug) {
  const name = displayName(station.name);
  const searchName = stationSearchName(station, language);
  const provider = providerLabel(station.provider);
  const location = stationLocationLabel(station, language) || t(language, 'fallback_location');
  const canonical = observationUrl(language, slug);
  const alternates = stationAlternates(station, slug);
  const xDefault =
    alternates.find((item) => item.code === DEFAULT_LANGUAGE)?.url ||
    alternates[0]?.url ||
    canonical;

  const breadcrumb = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    inLanguage: language,
    itemListElement: [
      {
        '@type': 'ListItem',
        position: 1,
        name: t(language, 'stations'),
        item: `${SITE_URL}${directoryPath(language)}`
      },
      {
        '@type': 'ListItem',
        position: 2,
        name: provider,
        item: `${SITE_URL}${providerPath(station.provider, language)}`
      },
      { '@type': 'ListItem', position: 3, name, item: canonical }
    ]
  };

  const place = {
    '@context': 'https://schema.org',
    '@type': 'Place',
    name: `${t(language, 'station_type')} ${name}`,
    url: canonical,
    identifier: station.station_id,
    inLanguage: language,
    geo: {
      '@type': 'GeoCoordinates',
      latitude: station.lat,
      longitude: station.lon,
      ...(station.elevation === null || station.elevation === undefined
        ? {}
        : { elevation: station.elevation })
    }
  };
  if (searchName !== name) place.alternateName = searchName;

  return {
    name,
    searchName,
    provider,
    location,
    canonical,
    alternates,
    xDefault,
    ogLocale: LANGUAGES[language].og_locale,
    ogLocaleAlternates: LANGUAGE_CODES.filter((code) => code !== language).map(
      (code) => LANGUAGES[code].og_locale
    ),
    title: t(language, 'station_title', { name: searchName, provider }),
    description: t(language, 'station_description', {
      name: searchName,
      provider,
      location
    }),
    lede: t(language, 'station_lede', { name: searchName, provider, location }),
    structuredData: [breadcrumb, place],
    ogImage: `${SITE_URL}/og-image.png?v=12`
  };
}
