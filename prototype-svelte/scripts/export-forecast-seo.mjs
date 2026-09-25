#!/usr/bin/env node
/**
 * Exporta los mapas de Predicción y sus guías al servidor web.
 *
 * Cada mapa tiene una página indexable —`/es/forecast/ebwd`— que SvelteKit
 * renderiza en el servidor con la guía del mapa. El servicio web se construye
 * con `web/` como contexto de Docker y no ve este proyecto, así que los datos
 * viajan en un fichero generado, como el resto de `*.generated.js`.
 *
 * Solo sale lo público: los mapas de los modelos visibles en el build de
 * producción (sin `VITE_ENABLE_ECMWF`, AROME) y, por idioma, las guías que
 * existen de verdad. Una guía sin traducir no se exporta en ese idioma: la
 * página correspondiente se sirve, pero no se indexa.
 *
 *   node scripts/export-forecast-seo.mjs
 */
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runnerImport } from 'vite';

const ROOT = resolve(import.meta.dirname, '..');
const OUTPUT = resolve(ROOT, '../web/src/lib/server/forecast-seo.generated.js');
const LANGUAGES = ['es', 'ca', 'en', 'de', 'fr', 'it', 'pt'];
// Idiomas con guías propias. El castellano es el original y va siempre.
const GUIDE_LANGUAGES = ['es', 'en', 'fr'];
const TEXT_KEYS = ['pageTitle', 'title', 'subtitle', 'what', 'interpretation', 'calculation', 'sources', 'maps'];

// `forecastProducts.js` usa `import.meta.env`, que solo existe dentro de Vite.
const load = async (path) => (await runnerImport(resolve(ROOT, path))).module;
const { forecastCategories, forecastModels, productsForModel } = await load('src/data/forecastProducts.js');
const i18n = await load('src/lib/forecast-i18n.js');
const translated = {
  en: (await load('src/data/forecastProductGuides.en.js')).default,
  fr: (await load('src/data/forecastProductGuides.fr.js')).default
};

function guideFields(guide) {
  if (!guide?.what || !guide?.interpretation?.length) return null;
  return {
    what: guide.what,
    interpretation: guide.interpretation,
    method: guide.method || '',
    equations: (guide.equations || []).map(({ label, latex }) => ({ label, latex })),
    steps: guide.steps || [],
    sources: (guide.sources || []).map(({ label, url }) => ({ label, url }))
  };
}

const products = [];
for (const model of forecastModels) {
  const base = productsForModel(model.id);
  const localized = Object.fromEntries(
    LANGUAGES.map((language) => [language, i18n.localizedForecastProducts(base, language)])
  );
  base.forEach((product, index) => {
    const guides = {};
    for (const language of GUIDE_LANGUAGES) {
      const guide = language === 'es'
        ? product.guide
        : translated[language]?.[product.id] && { ...product.guide, ...translated[language][product.id] };
      const fields = guideFields(guide);
      if (fields) guides[language] = fields;
    }
    products.push({
      id: product.id,
      model: model.id,
      modelLabel: model.short,
      category: product.category,
      kind: product.kind,
      coverage: product.coverage || '',
      labels: Object.fromEntries(LANGUAGES.map((language) => [language, localized[language][index].label])),
      guides
    });
  });
}

const data = {
  languages: LANGUAGES,
  text: Object.fromEntries(LANGUAGES.map((language) => [
    language,
    Object.fromEntries(TEXT_KEYS.map((key) => [key, i18n.forecastText(language, key)]))
  ])),
  categories: Object.fromEntries(LANGUAGES.map((language) => [
    language,
    i18n.localizedForecastCategories(forecastCategories, language).map(({ id, label }) => ({ id, label }))
  ])),
  products
};

writeFileSync(
  OUTPUT,
  '// Generado por prototype-svelte/scripts/export-forecast-seo.mjs. No editar a mano.\n' +
    `export default ${JSON.stringify(data, null, 1)};\n`
);
console.log(`${products.length} mapas → ${OUTPUT}`);
