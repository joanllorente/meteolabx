/**
 * Definición de cada tarjeta, la que sale al posarse en el interrogante.
 *
 * El catálogo lo exporta `scripts/export_card_tooltips.py` desde `locales/`,
 * que es de donde lo lee la app actual: los textos son los mismos en las dos
 * interfaces.
 *
 * Las claves del catálogo están en español aunque el texto esté traducido, y
 * no siempre coinciden entre idiomas: el corpus español las guarda con la
 * abreviatura del título («presion absoluta», «humedad especifica q») y los
 * traducidos sin ella. Por eso, igual que en la app actual, se busca primero
 * la clave exacta y después por prefijo.
 */
import DEFINITIONS from './card-tooltips.generated.js';

// Tarjetas cuyo título no es el de la definición que las explica.
const ALIASES = {
  'temp bulbo humedo': 'temperatura de bulbo humedo',
  'temp virtual': 'temperatura virtual',
  'temp equivalente': 'temperatura equivalente',
  'temp potencial': 'temperatura potencial',
  'base nube lcl': 'nivel de condensacion por ascenso',
  irradiancia: 'radiacion solar',
  'evapotranspiracion hoy': 'evapotranspiracion',
  'balance hidrico hoy': 'balance hidrico'
};

/** Sin acentos, sin signos y en minúsculas: como las claves del catálogo. */
export function normalizeKey(text) {
  return String(text ?? '')
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** El corpus guarda «Temperatura: indica…»; la definición empieza en minúscula. */
function capitalize(text) {
  return String(text)
    .split('\n')
    .map((line) => {
      const body = line.startsWith('- ') ? line.slice(2).trim() : line.trim();
      if (!body) return line;
      const upper = body[0].toUpperCase() + body.slice(1);
      return line.startsWith('- ') ? `- ${upper}` : upper;
    })
    .join('\n');
}

function lookup(catalogue, key) {
  if (!catalogue || !key) return '';
  if (catalogue[key]) return catalogue[key];
  const prefixed = Object.keys(catalogue).find((candidate) => candidate.startsWith(key));
  return prefixed ? catalogue[prefixed] : '';
}

/**
 * Texto de ayuda de una tarjeta, o cadena vacía si no hay definición.
 *
 * Si el idioma no la tiene traducida se cae al español antes que a nada:
 * media explicación es mejor que ninguna.
 */
export function cardTooltip(key, language = 'es') {
  const normalized = normalizeKey(key);
  const canonical = ALIASES[normalized] || normalized;
  const text =
    lookup(DEFINITIONS[language], canonical) ||
    lookup(DEFINITIONS[language], normalized) ||
    lookup(DEFINITIONS.es, canonical) ||
    lookup(DEFINITIONS.es, normalized);
  return text ? capitalize(text) : '';
}
