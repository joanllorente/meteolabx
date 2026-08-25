// ─────────────────────────────────────────────────────────────────────────
// Datos HARDCODEADOS para el prototipo visual.
// Escenario: tarde de verano en Barcelona (21/07/2026, ~19:40).
// Nada de esto viene del backend — son valores de ejemplo plausibles.
// ─────────────────────────────────────────────────────────────────────────

export const station = {
  provider: 'Weather Underground',
  providerId: 'WU',
  name: 'ILHOSP26',
  place: "L'Hospitalet de Llobregat",
  id: 'ILHOSP26',
  alt: 39,
  lat: 41.371,
  lon: 2.128,
  updated: '21/07/2026 · 19:41:08',
  ago: 'hace 22 s'
};

export const favorites = [
  { id: 'ILHOSP26', name: 'ILHOSP26', provider: 'WU', place: "L'Hospitalet", active: true },
  { id: 'fabra', name: 'Barcelona / Fabra', provider: 'AEMET', place: 'Observatori Fabra' },
  { id: 'raval', name: 'Barcelona · el Raval', provider: 'Meteocat', place: 'Raval' },
  { id: 'igueldo', name: 'Donostia / Igeldo', provider: 'Euskalmet', place: 'Igeldo' }
];

// Colores por familia de variable — usados en toda la app.
export const families = {
  temperature: { color: '#ff8a4c', soft: 'rgba(255,138,76,.14)' },
  humidity:    { color: '#2fb8a6', soft: 'rgba(47,184,166,.14)' },
  dewpoint:    { color: '#4db6e8', soft: 'rgba(77,182,232,.14)' },
  pressure:    { color: '#8b8bff', soft: 'rgba(139,139,255,.14)' },
  wind:        { color: '#37c8d6', soft: 'rgba(55,200,214,.14)' },
  precip:      { color: '#5b9bff', soft: 'rgba(91,155,255,.14)' },
  thermo:      { color: '#b98bff', soft: 'rgba(185,139,255,.14)' },
  radiation:   { color: '#f4bb3f', soft: 'rgba(244,187,63,.14)' }
};

// ── OBSERVACIÓN ────────────────────────────────────────────────────────────
export const observed = [
  {
    key: 'temperature', family: 'temperature', icon: 'Thermometer',
    title: 'Temperatura', value: '29.4', unit: '°C',
    sub: [
      { label: 'Sensación térmica', value: '32.1 °C' },
      { label: 'Heat index', value: '33.0 °C', chip: { text: 'Aviso de calor', tone: 'warn' } }
    ]
  },
  {
    key: 'humidity', family: 'humidity', icon: 'Droplets',
    title: 'Humedad relativa', value: '68', unit: '%',
    sub: [{ label: 'Presión de vapor', value: '27.4 hPa' }]
  },
  {
    key: 'dewpoint', family: 'dewpoint', icon: 'Droplet',
    title: 'Punto de rocío', value: '22.8', unit: '°C',
    sub: [{ label: 'Bulbo húmedo', value: '24.6 °C', chip: { text: 'Condiciones potenciales', tone: 'note' } }]
  },
  {
    key: 'pressure', family: 'pressure', icon: 'Gauge',
    title: 'Presión', value: '1014.2', unit: 'hPa',
    sub: [
      { label: 'Tendencia', value: 'Subiendo', arrow: 'up' },
      { label: 'Δ3h', value: '+0.8 hPa' },
      { label: 'MSL', value: '1018.9 hPa' }
    ]
  },
  {
    key: 'wind', family: 'wind', icon: 'Wind',
    title: 'Viento', value: '14', unit: 'km/h',
    windDir: 155, windCard: 'SSE',
    sub: [
      { label: 'Racha', value: '27 km/h' },
      { label: 'Dirección', value: 'SSE · 155°' }
    ]
  },
  {
    key: 'precip', family: 'precip', icon: 'CloudRain',
    title: 'Precipitación hoy', value: '1.2', unit: 'mm',
    sub: [
      { label: 'Instantánea', value: '0.0 mm/h', chip: { text: 'Sin precipitación', tone: 'note' } },
      { label: 'Intensidad 5 min', value: '0.0 mm/h' },
      { label: 'Intensidad 10 min', value: '0.0 mm/h' }
    ]
  }
];

export const thermo = [
  { title: 'Humedad específica', value: '14.8', unit: 'g/kg', icon: 'Droplets' },
  { title: 'Humedad absoluta', value: '19.6', unit: 'g/m³', icon: 'Droplets' },
  { title: 'Temp. virtual', value: '30.6', unit: '°C', icon: 'Thermometer' },
  { title: 'Temp. equivalente', value: '67.4', unit: '°C', icon: 'Thermometer' },
  { title: 'Temp. potencial', value: '29.7', unit: '°C', icon: 'Thermometer' },
  { title: 'Densidad del aire', value: '1.162', unit: 'kg/m³', icon: 'Box' },
  { title: 'Base nube LCL', value: '640', unit: 'm', icon: 'CloudFog' },
  { title: 'Velocidad del sonido', value: '349.8', unit: 'm/s', icon: 'AudioLines' }
];

export const radiation = [
  { title: 'Irradiancia', value: '712', unit: 'W/m²', icon: 'Sun', sub: [{ label: 'Energía hoy', value: '21.4 MJ/m²' }] },
  { title: 'Índice UV', value: '7', unit: 'UV', icon: 'SunMedium', chip: { text: 'Alto', tone: 'warn' }, sub: [{ label: 'Irradiancia eritematosa', value: '168 mW/m²' }] },
  { title: 'Dosis eritemática', value: '3.2', unit: 'kJ/m²', icon: 'Sun', sub: [{ label: 'Energía eritemática', value: '3200 J/m²' }] },
  { title: 'Evapotranspiración hoy', value: '4.8', unit: 'mm', icon: 'Sprout', sub: [{ label: 'FAO-56 Penman-Monteith', value: '' }] },
  { title: 'Claridad del cielo', value: '0.82', unit: '', icon: 'CloudSun', sub: [{ label: 'Orto 06:34 · Ocaso 21:18', value: '' }] },
  { title: 'Altura del Sol', value: '11.6', unit: '°', icon: 'Sunrise', sub: [{ label: 'Culminación', value: '61.4° · 14:12' }] },
  { title: 'Balance hídrico hoy', value: '-4.8', unit: 'mm', icon: 'Scale', sub: [{ label: 'Déficit', value: 'ET0 − lluvia' }] }
];

// ── TENDENCIAS ────────────────────────────────────────────────────────────
// Series a lo largo del día (cada punto ~1 h desde 00:00).
export const hours = ['00','02','04','06','08','10','12','14','16','18','19'];
export const trendSeries = {
  temperature: [22.1, 21.3, 20.8, 21.6, 24.2, 27.1, 29.0, 30.4, 30.9, 29.8, 29.4],
  dewpoint:    [19.8, 19.6, 19.4, 19.9, 20.8, 21.6, 22.1, 22.6, 22.9, 22.9, 22.8],
  pressure_dt: [-0.2, -0.4, -0.5, -0.3, 0.1, 0.4, 0.5, 0.3, 0.6, 0.7, 0.8],
  theta_e_dt:  [0.1, -0.1, -0.2, 0.3, 0.9, 1.2, 0.8, 0.4, -0.2, -0.5, -0.3],
  mixing_dt:   [0.0, -0.1, -0.1, 0.1, 0.4, 0.5, 0.3, 0.2, 0.1, -0.1, 0.0],
  wind_u:      [-3, -2, -1, 2, 6, 9, 11, 12, 10, 7, 6],
  wind_v:      [1, 0, -1, -2, -4, -6, -8, -9, -7, -5, -4]
};

// ── OBSERVACIÓN · sección "Gráficos" (intradía de hoy) ─────────────────────
// Mismas gráficas que la app real: temperatura, presión de vapor (e vs e_s),
// precipitación acumulada, viento+rachas+dirección, rosa de viento e
// irradiancia (medida vs teórica de cielo despejado).
export const obsHours = ['00', '02', '04', '06', '08', '10', '12', '14', '16', '18', '19:41'];
export const obsNowIndex = 10;
export const obsCharts = {
  temperature: [22.1, 21.3, 20.8, 21.6, 24.2, 27.1, 29.0, 30.4, 30.9, 29.8, 29.4],
  vapor_e:  [19.9, 19.6, 19.4, 20.1, 22.4, 24.8, 26.1, 27.0, 27.6, 27.5, 27.4],
  vapor_es: [26.6, 25.3, 24.6, 25.7, 30.3, 36.0, 40.1, 43.4, 44.6, 41.9, 40.9],
  precip:   [0, 0, 0, 0, 0, 0.4, 0.9, 1.1, 1.2, 1.2, 1.2],
  wind_speed: [6, 5, 4, 5, 8, 11, 13, 14, 12, 10, 14],
  wind_gust:  [11, 9, 8, 10, 15, 21, 24, 27, 23, 19, 27],
  wind_dir:   [205, 210, 235, 300, 140, 150, 160, 155, 150, 145, 155],
  irr_measured:    [0, 0, 0, 42, 225, 520, 690, 712, 560, 300, 118],
  irr_theoretical: [0, 0, 0, 58, 262, 560, 742, 760, 605, 345, 150]
};
// Rosa de viento de hoy (16 sectores) + estadísticos que muestra la app.
export const obsWindRose = [
  { dir: 'N', pct: 3 }, { dir: 'NNE', pct: 2 }, { dir: 'NE', pct: 3 }, { dir: 'ENE', pct: 4 },
  { dir: 'E', pct: 6 }, { dir: 'ESE', pct: 9 }, { dir: 'SE', pct: 12 }, { dir: 'SSE', pct: 18 },
  { dir: 'S', pct: 14 }, { dir: 'SSW', pct: 9 }, { dir: 'SW', pct: 6 }, { dir: 'WSW', pct: 3 },
  { dir: 'W', pct: 2 }, { dir: 'WNW', pct: 2 }, { dir: 'NW', pct: 3 }, { dir: 'NNW', pct: 4 }
];
export const obsWindRoseStats = { dominant: 'SSE', frequency: '18 %', samples: 268, calm: '6 %' };

// ── HISTÓRICO — Climograma mensual (Barcelona, año típico) ─────────────────
export const months = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
export const climogram = {
  tmax: [13.4, 14.6, 16.9, 18.5, 21.8, 25.6, 28.7, 29.1, 26.2, 22.3, 17.2, 14.1],
  tmin: [6.1, 6.9, 8.7, 10.6, 14.1, 18.0, 21.0, 21.4, 18.6, 14.9, 9.9, 7.0],
  precip: [41, 39, 42, 49, 52, 38, 27, 61, 84, 91, 58, 51]
};

// Rosa de viento — % por rumbo (16 sectores).
export const windRose = [
  { dir: 'N', pct: 4 }, { dir: 'NNE', pct: 3 }, { dir: 'NE', pct: 5 }, { dir: 'ENE', pct: 6 },
  { dir: 'E', pct: 9 }, { dir: 'ESE', pct: 8 }, { dir: 'SE', pct: 11 }, { dir: 'SSE', pct: 13 },
  { dir: 'S', pct: 9 }, { dir: 'SSW', pct: 6 }, { dir: 'SW', pct: 5 }, { dir: 'WSW', pct: 4 },
  { dir: 'W', pct: 3 }, { dir: 'WNW', pct: 2 }, { dir: 'NW', pct: 4 }, { dir: 'NNW', pct: 8 }
];

export const historicalTable = [
  { metric: 'Temperatura media', value: '18.4 °C' },
  { metric: 'Tmáx media', value: '20.6 °C' },
  { metric: 'Tmín media', value: '13.1 °C' },
  { metric: 'Tmáx absoluta', value: '35.2 °C · 12 ago' },
  { metric: 'Tmín absoluta', value: '1.8 °C · 18 ene' },
  { metric: 'Precipitación anual', value: '633 mm' },
  { metric: 'Días de lluvia', value: '74' },
  { metric: 'Racha máxima', value: '86 km/h · WNW' }
];

// ── MAPA — estaciones (posiciones relativas 0..100 sobre el lienzo) ─────────
export const mapProviders = [
  { id: 'WU', name: 'Weather Underground', color: '#ff8a4c', count: 41230, near: true },
  { id: 'AEMET', name: 'AEMET', color: '#e8484f', count: 812, near: true },
  { id: 'METEOCAT', name: 'Meteocat', color: '#f4bb3f', count: 187, near: true },
  { id: 'METEOFRANCE', name: 'Météo-France', color: '#4db6e8', count: 554, near: false },
  { id: 'NETATMO', name: 'Netatmo', color: '#2fb8a6', count: 63140, near: true }
];

export const mapStations = [
  { x: 46, y: 52, provider: 'WU', name: 'ILHOSP26', t: 29.4, active: true },
  { x: 52, y: 44, provider: 'AEMET', name: 'Barcelona / Fabra', t: 27.9 },
  { x: 55, y: 49, provider: 'METEOCAT', name: 'el Raval', t: 30.1 },
  { x: 39, y: 61, provider: 'NETATMO', name: 'Gavà platja', t: 28.6 },
  { x: 62, y: 39, provider: 'METEOCAT', name: 'Badalona', t: 29.7 },
  { x: 44, y: 33, provider: 'AEMET', name: 'Sabadell', t: 31.2 },
  { x: 68, y: 58, provider: 'NETATMO', name: 'El Masnou', t: 28.2 },
  { x: 30, y: 47, provider: 'WU', name: 'ICORNE12', t: 30.4 },
  { x: 58, y: 66, provider: 'NETATMO', name: 'Castelldefels', t: 28.9 },
  { x: 50, y: 57, provider: 'WU', name: 'IBARCE441', t: 29.9 }
];

export const sensorFilters = [
  { id: 'thermometer', label: 'Termómetro' },
  { id: 'hygrometer', label: 'Higrómetro' },
  { id: 'barometer', label: 'Barómetro' },
  { id: 'anemometer', label: 'Anemómetro' },
  { id: 'wind_vane', label: 'Veleta' },
  { id: 'rain_gauge', label: 'Pluviómetro' },
  { id: 'pyranometer', label: 'Piranómetro' },
  { id: 'uv', label: 'UV' }
];

// ── RANKING ────────────────────────────────────────────────────────────────
export const rankingMetrics = [
  { id: 'tmax', label: 'Temp. máxima', unit: '°C', icon: 'Thermometer' },
  { id: 'tmin', label: 'Temp. mínima', unit: '°C', icon: 'Thermometer' },
  { id: 'gust', label: 'Racha de viento', unit: 'km/h', icon: 'Wind' },
  { id: 'rain', label: 'Lluvia', unit: 'mm', icon: 'CloudRain' }
];

export const ranking = {
  tmax: [
    { name: 'Écija', provider: 'AEMET', place: 'Sevilla', v: 42.6 },
    { name: 'Montoro', provider: 'AEMET', place: 'Córdoba', v: 42.1 },
    { name: 'Andújar', provider: 'AEMET', place: 'Jaén', v: 41.7 },
    { name: 'Badajoz / Talavera', provider: 'AEMET', place: 'Badajoz', v: 41.2 },
    { name: 'Xàtiva', provider: 'AEMET', place: 'València', v: 40.8 },
    { name: 'Lleida', provider: 'Meteocat', place: 'Segrià', v: 40.3 },
    { name: 'Murcia', provider: 'AEMET', place: 'Murcia', v: 40.1 },
    { name: 'Zaragoza', provider: 'AEMET', place: 'Zaragoza', v: 39.6 },
    { name: 'Toledo', provider: 'AEMET', place: 'Toledo', v: 39.2 },
    { name: 'Móra la Nova', provider: 'Meteocat', place: 'Ribera d\'Ebre', v: 38.9 }
  ],
  tmin: [
    { name: 'Puerto de Navacerrada', provider: 'AEMET', place: 'Madrid', v: 12.4 },
    { name: 'Molina de Aragón', provider: 'AEMET', place: 'Guadalajara', v: 13.1 },
    { name: 'Núria', provider: 'Meteocat', place: 'Ripollès', v: 13.6 },
    { name: 'Reinosa', provider: 'AEMET', place: 'Cantabria', v: 14.0 },
    { name: 'Calamocha', provider: 'AEMET', place: 'Teruel', v: 14.3 },
    { name: 'Vielha', provider: 'Meteocat', place: 'Val d\'Aran', v: 14.8 },
    { name: 'Ávila', provider: 'AEMET', place: 'Ávila', v: 15.1 },
    { name: 'Soria', provider: 'AEMET', place: 'Soria', v: 15.4 },
    { name: 'Burgos / Villafría', provider: 'AEMET', place: 'Burgos', v: 15.9 },
    { name: 'León / Virgen', provider: 'AEMET', place: 'León', v: 16.2 }
  ],
  gust: [
    { name: 'Bagà', provider: 'Meteocat', place: 'Berguedà', v: 94 },
    { name: 'Portbou', provider: 'Meteocat', place: 'Alt Empordà', v: 88 },
    { name: 'Fanjac', provider: 'AEMET', place: 'Girona', v: 83 },
    { name: 'Punta Galea', provider: 'Euskalmet', place: 'Bizkaia', v: 79 },
    { name: 'Cap de Creus', provider: 'AEMET', place: 'Girona', v: 76 },
    { name: 'Estaca de Bares', provider: 'MeteoGalicia', place: 'A Coruña', v: 74 },
    { name: 'Tarifa', provider: 'AEMET', place: 'Cádiz', v: 71 },
    { name: 'Alto de Orduña', provider: 'Euskalmet', place: 'Bizkaia', v: 68 },
    { name: 'Izaña', provider: 'AEMET', place: 'Tenerife', v: 66 },
    { name: 'Delta de l\'Ebre', provider: 'Meteocat', place: 'Baix Ebre', v: 63 }
  ],
  rain: [
    { name: 'Cangas de Onís', provider: 'AEMET', place: 'Asturias', v: 38.4 },
    { name: 'Bielsa', provider: 'AEMET', place: 'Huesca', v: 31.2 },
    { name: 'Vall de Boí', provider: 'Meteocat', place: 'Alta Ribagorça', v: 27.8 },
    { name: 'Oviedo', provider: 'AEMET', place: 'Asturias', v: 24.1 },
    { name: 'Santander', provider: 'AEMET', place: 'Cantabria', v: 21.6 },
    { name: 'Hondarribia', provider: 'Euskalmet', place: 'Gipuzkoa', v: 19.9 },
    { name: 'Ripoll', provider: 'Meteocat', place: 'Ripollès', v: 17.2 },
    { name: 'Lugo', provider: 'MeteoGalicia', place: 'Lugo', v: 14.8 },
    { name: 'Pamplona', provider: 'AEMET', place: 'Navarra', v: 12.3 },
    { name: 'Vielha', provider: 'Meteocat', place: 'Val d\'Aran', v: 10.7 }
  ]
};

export const providerColors = {
  WU: '#ff8a4c', AEMET: '#e8484f', Meteocat: '#f4bb3f', METEOCAT: '#f4bb3f',
  Euskalmet: '#2fb8a6', MeteoGalicia: '#4db6e8', Netatmo: '#2fb8a6', NETATMO: '#2fb8a6'
};
