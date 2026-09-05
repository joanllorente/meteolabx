/**
 * Las guías de cada mapa, traducidas.
 *
 * Son textos largos —qué representa el campo, cómo se lee, con qué se calcula
 * y de dónde sale—, así que viven en un fichero por idioma que se carga solo
 * cuando hace falta: sumarlos todos al arranque engordaría el visor en medio
 * mega para que cada visitante use una sexta parte.
 *
 * Mientras un idioma no tenga su traducción, la guía se enseña en castellano
 * con un aviso. Antes se sustituía por una frase genérica —«DCAPE es un campo
 * de predicción del modelo seleccionado»— y quien no leía español se quedaba
 * sin la explicación entera: sin interpretación, sin método, sin ecuaciones y
 * sin fuentes.
 */
const GUIDE_LOADERS = {
  en: () => import('../data/forecastProductGuides.en.js')
};

let loadedGuides = $state({});

export function loadForecastGuides(language) {
  const loader = GUIDE_LOADERS[language];
  if (!loader || loadedGuides[language]) return;
  loader()
    .then((module) => {
      loadedGuides = { ...loadedGuides, [language]: module.default || module.forecastProductGuides };
    })
    .catch(() => {
      // Sin traducción disponible se sigue viendo la castellana con su aviso.
    });
}

export function localizedForecastGuide(guide, product, language) {
  if (language === 'es') return guide;
  const traducida = loadedGuides[language]?.[product.id];
  if (traducida) return { ...guide, ...traducida };
  // Castellano con aviso: incompleto, pero es la explicación de verdad.
  return { ...guide, untranslated: true };
}
