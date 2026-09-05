/** Opciones y conversiones puras desde las unidades canónicas del backend. */
export const defaultUnitPreferences = {
  temperature: 'c', wind: 'kmh', pressure: 'hpa', precip: 'mm', radiation: 'wm2'
};

export const unitOptions = {
  temperature: { k: 'K', c: '°C', f: '°F' },
  wind: { kmh: 'km/h', ms: 'm/s', mph: 'mph', kt: 'kt' },
  pressure: { hpa: 'hPa', mmhg: 'mmHg', inhg: 'inHg' },
  precip: { mm: 'mm', in: 'in' },
  radiation: { wm2: 'W/m²', mjm2: 'MJ/m²', kwhm2: 'kWh/m²' }
};

export function normalizeUnitPreferences(raw) {
  const normalized = { ...defaultUnitPreferences };
  for (const [family, options] of Object.entries(unitOptions)) {
    const candidate = String(raw?.[family] || '').toLowerCase();
    if (options[candidate]) normalized[family] = candidate;
  }
  return normalized;
}

export function unitLabel(family, preferences = defaultUnitPreferences) {
  const selected = preferences?.[family] || defaultUnitPreferences[family];
  return unitOptions[family]?.[selected] || '';
}

function numeric(value) {
  // `Number(null)` y `Number('')` valen cero, y esa cortesía de JavaScript
  // convertía «no hay dato» en «cero»: sin estación conectada, la ficha
  // enseñaba un punto de rocío de 0,0 °C y una presión de 0,0 hPa como si
  // alguien los hubiera medido.
  if (value === null || value === undefined || value === '') return NaN;
  const result = Number(value);
  return Number.isFinite(result) ? result : NaN;
}

export function convertUnit(value, family, preferences = defaultUnitPreferences, { delta = false } = {}) {
  const number = numeric(value);
  if (!Number.isFinite(number)) return value;
  const selected = preferences?.[family] || defaultUnitPreferences[family];
  if (family === 'temperature') {
    if (selected === 'f') return number * 9 / 5 + (delta ? 0 : 32);
    if (selected === 'k') return number + (delta ? 0 : 273.15);
  }
  if (family === 'wind') {
    if (selected === 'ms') return number / 3.6;
    if (selected === 'mph') return number * 0.6213711922;
    if (selected === 'kt') return number * 0.5399568035;
  }
  if (family === 'pressure') {
    if (selected === 'mmhg') return number * 0.750061683;
    if (selected === 'inhg') return number * 0.0295299831;
  }
  if (family === 'precip' && selected === 'in') return number / 25.4;
  if (family === 'radiation') {
    if (selected === 'mjm2') return number * 0.0036;
    if (selected === 'kwhm2') return number / 1000;
  }
  return number;
}

export function convertRadiationEnergy(value, preferences = defaultUnitPreferences) {
  const number = numeric(value);
  if (!Number.isFinite(number)) return value;
  return preferences?.radiation === 'kwhm2' ? number / 3.6 : number;
}

export function radiationEnergyLabel(preferences = defaultUnitPreferences) {
  return preferences?.radiation === 'kwhm2' ? 'kWh/m²' : 'MJ/m²';
}

export function convertSeries(values, family, preferences = defaultUnitPreferences, options) {
  return (values || []).map((value) =>
    Number.isFinite(value) ? convertUnit(value, family, preferences, options) : value
  );
}
