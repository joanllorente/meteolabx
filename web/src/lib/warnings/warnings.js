/**
 * Avisos a partir de AROME: lógica pura de la pestaña.
 *
 * Todavía no hay backend. La pestaña solo existe en desarrollo y se alimenta
 * de `demoWarnings`, que imita la forma que tendrá la respuesta de la API:
 * un aviso por zona, día y fenómeno, con su nivel, su evolución hora a hora
 * en hora local de la zona, el pico previsto y los umbrales que lo disparan.
 *
 * Las zonas de la demostración son las regiones admin-1 de Natural Earth que
 * caen en el dominio (`demo-zones.json`); las definitivas serán NUTS3.
 */

export const HAZARDS = ['temperature', 'wind', 'rain', 'storms'];

/** Límites de la rejilla de AROME: los mismos que `AROME_MODEL_GRID_BOUNDS`. */
export const AROME_BOUNDS = { west: -12.0125, south: 37.4875, east: 16.0125, north: 55.4125 };

export const LEVEL_COLORS = { 1: '#f5c542', 2: '#f08a3c', 3: '#e0403a' };

/** Familia de unidades de cada fenómeno, para convertir el pico y los umbrales. */
export const HAZARD_UNIT = { temperature: 'temperature', wind: 'wind', rain: 'precip', storms: '' };

/** Huso de cada país del dominio: el cronograma va en hora local de la zona. */
const COUNTRY_TIME_ZONE = { PT: 'Europe/Lisbon', GB: 'Europe/London', IE: 'Europe/Dublin' };

export function countryTimeZone(country) {
  return COUNTRY_TIME_ZONE[country] || 'Europe/Paris';
}

/** Fecha ISO (`2026-09-13`) de `date` más `offset` días, en hora local. */
export function isoDay(date, offset = 0) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate() + offset);
  const month = String(day.getMonth() + 1).padStart(2, '0');
  return `${day.getFullYear()}-${month}-${String(day.getDate()).padStart(2, '0')}`;
}

/** Nivel de cada hora: `base` entre `from` y `to`, `peak` en el tramo central. */
function hours(from, to, base, peak = base, peakFrom = from, peakTo = to) {
  return Array.from({ length: 24 }, (_, hour) => {
    if (hour < from || hour > to) return 0;
    return hour >= peakFrom && hour <= peakTo ? peak : base;
  });
}

/**
 * Tres días de avisos inventados, fechados a partir de `now`.
 *
 * Se fechan al vuelo para que la demostración siempre caiga en «hoy» y
 * «mañana»: con fechas fijas, la pestaña estaría vacía al día siguiente.
 */
export function demoWarnings(now = new Date()) {
  const [today, tomorrow, after] = [0, 1, 2].map((offset) => isoDay(now, offset));
  const run = new Date(now);
  run.setUTCHours(run.getUTCHours() >= 15 ? 12 : run.getUTCHours() >= 9 ? 6 : 0, 0, 0, 0);

  const warnings = [
    { zone: 'FR-Occitanie', day: today, hazard: 'rain', level: 3, hourly: hours(3, 18, 2, 3, 7, 13), peak: 180, period: '24h', thresholds: [45, 90, 150], runs: [3, 3] },
    { zone: 'FR-Auvergne-Rhône-Alpes', day: today, hazard: 'rain', level: 2, hourly: hours(6, 20, 1, 2, 10, 14), peak: 95, period: '24h', thresholds: [42, 85, 145], runs: [2, 3] },
    { zone: "FR-Provence-Alpes-Côte-d'Azur", day: tomorrow, hazard: 'wind', level: 1, hourly: hours(10, 22, 1), peak: 90, thresholds: [85, 105, 125], runs: [2, 3] },
    { zone: 'IT-Liguria', day: today, hazard: 'storms', level: 2, hourly: hours(13, 21, 1, 2, 15, 18), peak: 2600, thresholds: null, runs: [3, 3] },
    { zone: 'IT-Piemonte', day: today, hazard: 'storms', level: 1, hourly: hours(14, 20, 1), peak: 1900, thresholds: null, runs: [2, 3] },
    { zone: 'CH-Tesino', day: tomorrow, hazard: 'rain', level: 2, hourly: hours(12, 23, 1, 2, 15, 19), peak: 85, period: '12h', thresholds: [35, 70, 110], runs: [2, 3] },
    { zone: 'ES-Aragón', day: today, hazard: 'wind', level: 1, hourly: hours(11, 19, 1), peak: 80, thresholds: [75, 92, 110], runs: [3, 3] },
    { zone: 'ES-Extremadura', day: today, hazard: 'temperature', level: 1, hourly: hours(13, 19, 1), peak: 40, thresholds: [39, 41, 43], runs: [3, 3] },
    { zone: 'PT-Alentejo', day: today, hazard: 'temperature', level: 2, hourly: hours(12, 19, 1, 2, 14, 17), peak: 42, thresholds: [39, 41, 44], runs: [3, 3] },
    { zone: 'GB-South West', day: tomorrow, hazard: 'wind', level: 1, hourly: hours(4, 13, 1), peak: 85, thresholds: [80, 98, 118], runs: [2, 3] },
    { zone: 'DE-Baviera', day: tomorrow, hazard: 'storms', level: 1, hourly: hours(14, 22, 1), peak: 1500, thresholds: null, runs: [1, 2] },
    { zone: 'AT-Tirol', day: after, hazard: 'storms', level: 1, hourly: hours(13, 20, 1), peak: 1200, thresholds: null, runs: null }
  ];

  return {
    model: 'arome',
    run: run.toISOString(),
    days: [
      { date: today, source: 'arome' },
      { date: tomorrow, source: 'arome' },
      { date: after, source: 'ecmwf' }
    ],
    warnings
  };
}

/** País de una zona: el prefijo ISO de su identificador. */
export function zoneCountry(zone) {
  return String(zone).split('-')[0];
}

/**
 * Avisos que pasan los filtros de la pestaña.
 *
 * `names` traduce el identificador de zona a su nombre, que es por lo que se
 * busca; sin él se busca por el identificador.
 */
export function filterWarnings(warnings, { day = '', hazard = 'all', country = 'all', query = '', names = {} } = {}) {
  const needle = normalise(query);
  return warnings.filter((warning) => {
    if (day && warning.day !== day) return false;
    if (hazard !== 'all' && warning.hazard !== hazard) return false;
    if (country !== 'all' && zoneCountry(warning.zone) !== country) return false;
    if (needle && !normalise(names[warning.zone] || warning.zone).includes(needle)) return false;
    return true;
  });
}

/** Sin mayúsculas ni tildes: «occitanie» encuentra «Occitanie», «aragon» a «Aragón». */
function normalise(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim();
}

/** Nivel más alto de cada zona entre los avisos dados. */
export function zoneLevels(warnings) {
  const levels = {};
  for (const warning of warnings) {
    levels[warning.zone] = Math.max(levels[warning.zone] || 0, warning.level);
  }
  return levels;
}

/**
 * Avisos agrupados por país: primero el país con el aviso más grave y, dentro
 * de cada uno, de más a menos grave.
 */
export function groupByCountry(warnings, names = {}) {
  const groups = new Map();
  for (const warning of warnings) {
    const country = zoneCountry(warning.zone);
    if (!groups.has(country)) groups.set(country, []);
    groups.get(country).push(warning);
  }
  const byName = (a, b) => (names[a.zone] || a.zone).localeCompare(names[b.zone] || b.zone);
  return [...groups.entries()]
    .map(([country, items]) => ({
      country,
      level: Math.max(...items.map((item) => item.level)),
      warnings: items.sort((a, b) => b.level - a.level || byName(a, b))
    }))
    .sort((a, b) => b.level - a.level || a.country.localeCompare(b.country));
}

/** Primera y última hora con aviso, o `null` si no hay ninguna. */
export function activeSpan(hourly) {
  const first = hourly.findIndex((level) => level > 0);
  if (first < 0) return null;
  return [first, hourly.length - 1 - [...hourly].reverse().findIndex((level) => level > 0)];
}

/** Identificador estable de un aviso: una zona puede tener varios en el mismo día. */
export function warningKey(warning) {
  return `${warning.zone}|${warning.day}|${warning.hazard}`;
}
