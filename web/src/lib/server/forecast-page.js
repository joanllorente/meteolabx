/**
 * Páginas indexables de Predicción.
 *
 * El visor es un SPA aparte (`static/forecast/`) y durante mucho tiempo vivió
 * en una sola URL, `/forecast`: para un buscador era una página casi vacía,
 * con un párrafo escondido, y ninguno de sus mapas tenía dirección propia.
 * Ahora cada mapa la tiene —`/es/forecast/ebwd`— y este módulo entrega en ella
 * el mismo HTML del visor con lo que Google necesita ya escrito: título,
 * descripción, canonical, `hreflang`, datos estructurados y la guía completa
 * del mapa (qué representa, cómo se interpreta, cómo se calcula y fuentes).
 *
 * El visor, al montarse, sustituye ese contenido por la interfaz, que enseña
 * la misma guía. `/{idioma}/forecast` es el índice: todos los mapas enlazados
 * por categoría.
 *
 * Solo se indexan los idiomas con guía propia. Los demás se sirven igual
 * —la interfaz está traducida—, pero con `noindex`: sin su explicación serían
 * páginas de poco valor que Google acabaría descartando.
 */
import { SITE_URL } from '$lib/seo/i18n.js';
import data from './forecast-seo.generated.js';

export const FORECAST_SEGMENT = 'forecast';
// Idiomas en los que existe la guía. El índice solo tiene introducción en
// estos, así que solo estos se indexan también ahí.
export const HUB_LANGUAGES = ['es', 'en', 'fr'];
const DEFAULT_LANGUAGE = 'es';
const DESCRIPTION_LENGTH = 158;

const PRODUCTS = new Map(data.products.map((product) => [product.id, product]));

const HUB_INTRO = {
  es: 'Mapas horarios del modelo AROME a 2,5 km hasta 51 horas: temperatura en superficie y en altura, viento y rachas, precipitación y tipo de precipitación, cota de nieve, nubosidad y radiación, y los diagnósticos convectivos que calcula MeteoLabX —CAPE y ECAPE, cizalladura, helicidad, DCAPE, SHIP, STP o SCP— para analizar tormentas y entornos de supercélulas. Cada mapa explica qué representa, cómo se interpreta y cómo se calcula.',
  en: 'Hourly maps from the AROME model at 2.5 km out to 51 hours: surface and upper-air temperature, wind and gusts, precipitation and precipitation type, snow level, cloud cover and radiation, plus the convective diagnostics computed by MeteoLabX —CAPE and ECAPE, shear, helicity, DCAPE, SHIP, STP and SCP— to analyse thunderstorms and supercell environments. Every map explains what it shows, how to read it and how it is calculated.',
  fr: 'Cartes horaires du modèle AROME à 2,5 km jusqu’à 51 heures : température en surface et en altitude, vent et rafales, précipitations et type de précipitations, limite pluie-neige, nébulosité et rayonnement, ainsi que les diagnostics convectifs calculés par MeteoLabX —CAPE et ECAPE, cisaillement, hélicité, DCAPE, SHIP, STP ou SCP— pour analyser les orages et les environnements de supercellules. Chaque carte explique ce qu’elle représente, comment la lire et comment elle est calculée.'
};

// Los textos que el visor no tiene: los de la página estática.
const PAGE_TEXT = {
  es: { related: 'Otros mapas de {category}', model: 'Modelo {model}', equation: 'Ecuación', coverage: 'Campo del modelo' },
  en: { related: 'More {category} maps', model: '{model} model', equation: 'Equation', coverage: 'Model field' },
  fr: { related: 'Autres cartes : {category}', model: 'Modèle {model}', equation: 'Équation', coverage: 'Champ du modèle' }
};

const OG_LOCALES = { es: 'es_ES', ca: 'ca_ES', en: 'en_GB', de: 'de_DE', fr: 'fr_FR', it: 'it_IT', pt: 'pt_PT' };

export function forecastProduct(id) {
  return PRODUCTS.get(id) || null;
}

export function forecastPath(language, productId = '') {
  const base = `/${language}/${FORECAST_SEGMENT}`;
  return productId ? `${base}/${encodeURIComponent(productId)}` : base;
}

/** Idiomas en los que la página de un mapa (o el índice, sin mapa) se indexa. */
export function indexableLanguages(product = null) {
  if (!product) return HUB_LANGUAGES;
  return data.languages.filter((language) => product.guides[language]);
}

/** Todas las URL indexables, para el sitemap. */
export function forecastSitemapUrls() {
  const urls = HUB_LANGUAGES.map((language) => `${SITE_URL}${forecastPath(language)}`);
  for (const product of data.products) {
    for (const language of indexableLanguages(product)) {
      urls.push(`${SITE_URL}${forecastPath(language, product.id)}`);
    }
  }
  return urls;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match);
}

function pageText(language, key, values = {}) {
  return fill((PAGE_TEXT[language] || PAGE_TEXT.en)[key], values);
}

function uiText(language, key) {
  return data.text[language]?.[key] || data.text[DEFAULT_LANGUAGE][key] || '';
}

/** Recorta en la última palabra entera que cabe, para la meta descripción. */
export function summarize(text, limit = DESCRIPTION_LENGTH) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  if (clean.length <= limit) return clean;
  const cut = clean.slice(0, limit - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > limit * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.–—-]+$/, '')}…`;
}

function firstSentence(text) {
  const match = String(text || '').match(/^.+?[.!?](?=\s|$)/);
  return match ? match[0] : String(text || '');
}

/** Guía del mapa en el idioma pedido; sin traducción, la castellana. */
function guideFor(product, language) {
  return product.guides[language] || product.guides[DEFAULT_LANGUAGE] || null;
}

function categoryLabel(language, categoryId) {
  const categories = data.categories[language] || data.categories[DEFAULT_LANGUAGE];
  return categories.find((item) => item.id === categoryId)?.label || '';
}

function head({ language, title, description, path, indexable, alternates, jsonLd }) {
  const url = `${SITE_URL}${path}`;
  const lines = [
    `<title>${escapeHtml(title)}</title>`,
    `<meta name="description" content="${escapeHtml(description)}" />`,
    `<meta name="robots" content="${indexable ? 'index, follow, max-image-preview:large' : 'noindex, follow'}" />`,
    `<link rel="canonical" href="${escapeHtml(url)}" />`
  ];
  if (indexable) {
    for (const code of alternates) {
      lines.push(`<link rel="alternate" hreflang="${code}" href="${escapeHtml(SITE_URL + alternatePath(path, code))}" />`);
    }
    if (alternates.includes(DEFAULT_LANGUAGE)) {
      lines.push(`<link rel="alternate" hreflang="x-default" href="${escapeHtml(SITE_URL + alternatePath(path, DEFAULT_LANGUAGE))}" />`);
    }
  }
  lines.push(
    `<meta property="og:url" content="${escapeHtml(url)}" />`,
    `<meta property="og:title" content="${escapeHtml(title)}" />`,
    `<meta property="og:description" content="${escapeHtml(description)}" />`,
    `<meta property="og:locale" content="${OG_LOCALES[language] || OG_LOCALES.es}" />`,
    `<meta property="og:image" content="${SITE_URL}/og-image.png" />`,
    `<script type="application/ld+json">${JSON.stringify(jsonLd).replaceAll('<', '\\u003c')}</script>`
  );
  return lines.map((line) => `    ${line}`).join('\n');
}

function alternatePath(path, language) {
  return path.replace(/^\/[^/]+/, `/${language}`);
}

function breadcrumb(items) {
  return {
    '@type': 'BreadcrumbList',
    itemListElement: items.map(([name, path], index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name,
      item: `${SITE_URL}${path}`
    }))
  };
}

function productArticle(product, language) {
  const guide = guideFor(product, language);
  const label = product.labels[language] || product.labels[DEFAULT_LANGUAGE];
  const hub = forecastPath(language);
  const related = data.products.filter((item) => item.category === product.category && item.id !== product.id);
  const parts = [
    `<nav aria-label="breadcrumb"><a href="/${language}">MeteoLabX</a> › <a href="${hub}">${escapeHtml(uiText(language, 'title'))}</a> › ${escapeHtml(label)}</nav>`,
    `<h1>${escapeHtml(label)}</h1>`,
    `<p>${escapeHtml(pageText(language, 'model', { model: product.modelLabel }))} · ${escapeHtml(categoryLabel(language, product.category))}</p>`
  ];
  if (guide) {
    parts.push(`<h2>${escapeHtml(uiText(language, 'what'))}</h2>`, `<p>${escapeHtml(guide.what)}</p>`);
    parts.push(
      `<h2>${escapeHtml(uiText(language, 'interpretation'))}</h2>`,
      `<ul>${guide.interpretation.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
    );
    parts.push(`<h2>${escapeHtml(uiText(language, 'calculation'))}</h2>`);
    if (guide.method) parts.push(`<p>${escapeHtml(guide.method)}</p>`);
    for (const equation of guide.equations) {
      parts.push(`<p>${escapeHtml(equation.label || pageText(language, 'equation'))}: <code>${escapeHtml(equation.latex)}</code></p>`);
    }
    if (guide.steps.length) parts.push(`<ol>${guide.steps.map((step) => `<li>${escapeHtml(step)}</li>`).join('')}</ol>`);
    // El campo del modelo solo está escrito en castellano.
    if (product.coverage && language === DEFAULT_LANGUAGE) parts.push(`<p>${escapeHtml(pageText(language, 'coverage'))}: <code>${escapeHtml(product.coverage)}</code></p>`);
    if (guide.sources.length) {
      parts.push(
        `<h2>${escapeHtml(uiText(language, 'sources'))}</h2>`,
        `<ul>${guide.sources.map((source) => `<li><a href="${escapeHtml(source.url)}" rel="noreferrer">${escapeHtml(source.label)}</a></li>`).join('')}</ul>`
      );
    }
  }
  if (related.length) {
    parts.push(
      `<h2>${escapeHtml(pageText(language, 'related', { category: categoryLabel(language, product.category).toLocaleLowerCase(language) }))}</h2>`,
      `<ul>${related.map((item) => `<li><a href="${forecastPath(language, item.id)}">${escapeHtml(item.labels[language] || item.labels[DEFAULT_LANGUAGE])}</a></li>`).join('')}</ul>`
    );
  }
  return parts.join('\n');
}

function hubArticle(language) {
  const parts = [
    `<h1>${escapeHtml(uiText(language, 'title'))}</h1>`,
    `<p>${escapeHtml(HUB_INTRO[language] || uiText(language, 'subtitle'))}</p>`
  ];
  const categories = data.categories[language] || data.categories[DEFAULT_LANGUAGE];
  for (const category of categories) {
    const products = data.products.filter((item) => item.category === category.id);
    if (!products.length) continue;
    parts.push(
      `<h2>${escapeHtml(category.label)}</h2>`,
      `<ul>${products.map((item) => {
        const guide = guideFor(item, language);
        const summary = guide ? ` — ${escapeHtml(firstSentence(guide.what))}` : '';
        return `<li><a href="${forecastPath(language, item.id)}">${escapeHtml(item.labels[language] || item.labels[DEFAULT_LANGUAGE])}</a>${summary}</li>`;
      }).join('')}</ul>`
    );
  }
  return parts.join('\n');
}

/** Metadatos y artículo de una URL de Predicción, sin plantilla. */
export function forecastPageContent(language, product = null) {
  const path = forecastPath(language, product?.id);
  const alternates = indexableLanguages(product);
  const indexable = alternates.includes(language);
  const siteTitle = uiText(language, 'pageTitle');
  const hubCrumb = [uiText(language, 'title'), forecastPath(language)];
  if (!product) {
    const description = summarize(HUB_INTRO[language] || uiText(language, 'subtitle'));
    return {
      path, indexable, alternates, title: siteTitle, description,
      article: hubArticle(language),
      jsonLd: {
        '@context': 'https://schema.org',
        '@graph': [
          { '@type': 'CollectionPage', name: siteTitle, description, url: `${SITE_URL}${path}`, inLanguage: language },
          breadcrumb([['MeteoLabX', `/${language}`], hubCrumb])
        ]
      }
    };
  }
  const label = product.labels[language] || product.labels[DEFAULT_LANGUAGE];
  const guide = guideFor(product, language);
  const title = `${label} · ${siteTitle}`;
  const description = summarize(guide?.what || uiText(language, 'subtitle'));
  return {
    path, indexable, alternates, title, description,
    article: productArticle(product, language),
    jsonLd: {
      '@context': 'https://schema.org',
      '@graph': [
        {
          '@type': 'WebPage', name: title, headline: label, description,
          url: `${SITE_URL}${path}`, inLanguage: language,
          isPartOf: { '@type': 'WebSite', name: 'MeteoLabX', url: SITE_URL }
        },
        breadcrumb([['MeteoLabX', `/${language}`], hubCrumb, [label, path]])
      ]
    }
  };
}

// Lo que el HTML del visor trae de serie y aquí se reescribe por URL.
const REPLACED_HEAD = [
  /\s*<title>[\s\S]*?<\/title>/,
  /\s*<meta name="description"[^>]*>/,
  /\s*<meta name="robots"[^>]*>/,
  /\s*<link rel="canonical"[^>]*>/,
  /\s*<meta property="og:(?:url|title|description|locale|image)"[^>]*>/g
];
const INTRO = /<main class="forecast-static-intro">[\s\S]*?<\/main>/;

/**
 * El HTML del visor con los metadatos y la guía de esta URL.
 *
 * `template` es `static/forecast/index.html` tal como lo instala el build del
 * visor. Si alguna vez deja de traer el bloque de introducción, la página se
 * sirve igual pero sin artículo: mejor eso que un 500.
 */
export function renderForecastPage(template, language, product = null) {
  const content = forecastPageContent(language, product);
  let html = template.replace(/<html lang="[^"]*"/, `<html lang="${language}"`);
  for (const pattern of REPLACED_HEAD) html = html.replace(pattern, '');
  html = html.replace('</head>', `${head({ language, ...content })}\n  </head>`);
  html = html.replace(INTRO, () => `<main class="forecast-static-intro">\n${content.article}\n      </main>`);
  return html;
}

/**
 * La misma caché corta que tenía `/forecast`: el visor se reinstala con cada
 * build y el HTML apunta a bundles con hash que desaparecen al desplegar.
 */
export function forecastHtmlResponse(html) {
  return new Response(html, {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'cache-control': 'public, max-age=0, must-revalidate'
    }
  });
}
