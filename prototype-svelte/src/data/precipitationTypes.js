// Códigos del diagnóstico PTYPE de AROME. Los códigos 2 y 4 no están
// documentados; el backend normaliza aquí las variantes 193 y 20x/213.
export const PRECIPITATION_TYPES = [
  { code: 0, color: '#8795a3', es: 'Sin precipitación', ca: 'Sense precipitació', en: 'No precipitation', fr: 'Pas de précipitation', de: 'Kein Niederschlag', it: 'Nessuna precipitazione', pt: 'Sem precipitação' },
  { code: 1, color: '#1479b8', es: 'Lluvia', ca: 'Pluja', en: 'Rain', fr: 'Pluie', de: 'Regen', it: 'Pioggia', pt: 'Chuva' },
  { code: 3, color: '#0b8f91', es: 'Precipitación engelante', ca: 'Precipitació gelant', en: 'Freezing precipitation', fr: 'Précipitations verglaçantes', de: 'Gefrierender Niederschlag', it: 'Precipitazione gelicida', pt: 'Precipitação gelante' },
  { code: 5, color: '#bba4e8', es: 'Nieve seca', ca: 'Neu seca', en: 'Dry snow', fr: 'Neige sèche', de: 'Trockener Schnee', it: 'Neve asciutta', pt: 'Neve seca' },
  { code: 6, color: '#855cc7', es: 'Nieve húmeda', ca: 'Neu humida', en: 'Wet snow', fr: 'Neige humide', de: 'Nasser Schnee', it: 'Neve bagnata', pt: 'Neve húmida' },
  { code: 7, color: '#7c83d1', es: 'Lluvia y nieve', ca: 'Pluja i neu', en: 'Rain and snow', fr: 'Pluie et neige', de: 'Regen und Schnee', it: 'Pioggia e neve', pt: 'Chuva e neve' },
  { code: 8, color: '#6970bf', es: 'Gránulos de hielo', ca: 'Grànuls de gel', en: 'Ice pellets', fr: 'Granules de glace', de: 'Eiskörner', it: 'Granuli di ghiaccio', pt: 'Grânulos de gelo' },
  { code: 9, color: '#4b61aa', es: 'Nieve granulada', ca: 'Neu granulada', en: 'Snow pellets', fr: 'Grésil', de: 'Graupel', it: 'Neve tonda', pt: 'Neve granulada' },
  { code: 10, color: '#304887', es: 'Granizo', ca: 'Calamarsa', en: 'Hail', fr: 'Grêle', de: 'Hagel', it: 'Grandine', pt: 'Granizo' },
  { code: 11, color: '#73b9d9', es: 'Llovizna', ca: 'Plugim', en: 'Drizzle', fr: 'Bruine', de: 'Nieselregen', it: 'Pioviggine', pt: 'Chuvisco' },
  { code: 12, color: '#59b9a7', es: 'Llovizna engelante', ca: 'Plugim gelant', en: 'Freezing drizzle', fr: 'Bruine verglaçante', de: 'Gefrierender Nieselregen', it: 'Pioviggine gelicida', pt: 'Chuvisco gelante' }
];

const byCode = new Map(PRECIPITATION_TYPES.map((type) => [type.code, type]));

export function precipitationType(value) {
  if (!Number.isFinite(value)) return undefined;
  const code = Math.round(value);
  return Math.abs(value - code) < 0.05 ? byCode.get(code) : undefined;
}

export function precipitationTypeLabel(type, language = 'es') {
  return type?.[language] || type?.es || '';
}
