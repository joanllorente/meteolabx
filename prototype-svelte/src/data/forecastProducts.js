import { forecastProductGuides } from './forecastProductGuides.js';

export const forecastCategories = [
  { id: 'temperature', label: 'Temperatura' },
  { id: 'precipitation', label: 'Precipitación' },
  { id: 'dynamics', label: 'Dinámica atmosférica' },
  { id: 'convection', label: 'Convección' },
  { id: 'humidity', label: 'Humedad' },
  { id: 'clouds', label: 'Nubosidad' },
  { id: 'radiation', label: 'Radiación' }
];

const allForecastProducts = [
  {
    id: 'temperature-2m', category: 'temperature', label: 'Temperatura a 2 m', short: 'T 2 m', kind: 'native',
    unit: '°C', min: -8, max: 42, palette: 'temperature', accent: '#ff8a5b', vectors: false,
    description: 'Temperatura del aire prevista a dos metros sobre el terreno. Permite seguir contrastes térmicos, entradas marítimas y la evolución diurna.',
    method: 'Campo AROME en nivel de altura específica de 2 m; la API entrega el valor numérico y sus horas válidas.',
    coverage: 'TEMPERATURE · altura 2 m'
  },
  {
    id: 'temperature-850', category: 'temperature', label: 'Temperatura y geopotencial 850 hPa', short: 'T/Z 850 hPa', kind: 'native',
    unit: '°C', min: -24, max: 36, palette: 'temperature', accent: '#ed7f61', vectors: false,
    contourStep: 2, nationalBoundariesOnly: true,
    // Lo que enseña el mapa, que ya no es solo su categoría: en la cabecera se
    // lee «Temperatura · Geopotencial» y no «Temperatura» a secas.
    contents: 'Temperatura · Geopotencial',
    // Sin nombre para la capa superpuesta: en un mapa que se llama «temperatura
    // y geopotencial», un valor en dam solo puede ser una cosa. Los CAPE sí lo
    // llevan, porque ahí conviven tres índices distintos.
    // Isohipsas del geopotencial de 850 hPa, que viajan en la capa superpuesta
    // del propio frame. Cada 3 dam, y una de cada dos más marcada.
    overlayStep: 3, overlayMajorStep: 6,
    description: 'Temperatura en la superficie isobárica de 850 hPa, útil para reconocer masas de aire por encima de la capa superficial.',
    method: 'Campo AROME de temperatura sobre superficie isobárica, seleccionado en 850 hPa mediante DescribeCoverage.',
    coverage: 'TEMPERATURE · 850 hPa'
  },
  {
    id: 'temperature-500', category: 'temperature', label: 'Temperatura y geopotencial 500 hPa', short: 'T/Z 500 hPa', kind: 'native',
    unit: '°C', min: -42, max: -2, palette: 'temperature', accent: '#bc6ed0', vectors: false,
    contourStep: 2, nationalBoundariesOnly: true,
    contents: 'Temperatura · Geopotencial',
    overlayStep: 4, overlayMajorStep: 8,
    // Ejes de vaguada sobre el geopotencial de 500 hPa, que es el nivel donde
    // la onda se lee sin que el relieve la enmascare.
    troughAxes: true,
    description: 'Temperatura prevista en la superficie isobárica de 500 hPa, representativa de la troposfera media y útil para valorar el aire frío en altura.',
    method: 'Campo AROME TEMPERATURE sobre superficie isobárica, seleccionado en 500 hPa mediante DescribeCoverage.',
    coverage: 'TEMPERATURE · 500 hPa'
  },
  {
    id: 'wet-bulb-2m', category: 'temperature', label: 'Temperatura de bulbo húmedo', short: 'Tw 2 m', kind: 'native',
    unit: '°C', min: -8, max: 30, palette: 'humidity', accent: '#45c4c6', vectors: false,
    description: 'Temperatura de bulbo húmedo cerca de superficie. Ayuda a estimar enfriamiento evaporativo y transiciones del tipo de precipitación.',
    method: 'Campo nativo WET_BULB_TEMPERATURE en nivel de altura específica.', coverage: 'WET BULB TEMPERATURE · altura 2 m'
  },
  {
    id: 'wind-level', category: 'dynamics', label: 'Viento por niveles', short: 'Viento', kind: 'native',
    unit: 'm/s', min: 0, max: 55, palette: 'wind', accent: '#4db6e8', vectors: true,
    description: 'Velocidad y dirección del viento en alturas sobre el terreno o superficies isobáricas.',
    method: 'Magnitud calculada a partir de las componentes U/V nativas del nivel seleccionado; las flechas muestran la dirección.',
    coverage: 'U/V · altura geométrica o nivel isobárico'
  },
  {
    id: 'wind-gust', category: 'dynamics', label: 'Racha máxima horaria a 10 m', short: 'Racha máx. 10 m', kind: 'native',
    unit: 'm/s', min: 0, max: 45, palette: 'wind', accent: '#62a9f5', vectors: false,
    description: 'Racha máxima prevista durante la hora, útil para localizar aceleraciones por relieve, frentes y convección.',
    method: 'Campo nativo WIND_SPEED_GUST_MAX de AROME en altura específica de 10 m y periodo PT1H.', coverage: 'WIND SPEED GUST MAX · 10 m · 1 h'
  },
  {
    id: 'shear-01', category: 'dynamics', label: 'Cizalladura 0–1 km', short: 'CIZ 0–1 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 26, palette: 'shear', accent: '#57b6ff', vectors: true,
    description: 'Cizalladura vectorial entre el viento a 10 m y 1.000 m sobre el terreno. Describe el cambio de viento en la capa más baja.',
    method: '√[(u₁₀₀₀ − u₁₀)² + (v₁₀₀₀ − v₁₀)²]. Las flechas muestran el vector diferencia.',
    coverage: 'Diagnóstico MeteoLabX · U/V 10 y 1.000 m'
  },
  {
    id: 'shear-03', category: 'dynamics', label: 'Cizalladura 0–3 km', short: 'CIZ 0–3 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 36, palette: 'shear', accent: '#7d8cff', vectors: true,
    description: 'Cizalladura vectorial entre el viento a 10 m y 3.000 m, relevante para la organización de la convección.',
    method: '√[(u₃₀₀₀ − u₁₀)² + (v₃₀₀₀ − v₁₀)²]. Las rejillas se alinean antes de operar.',
    coverage: 'Diagnóstico MeteoLabX · U/V 10 y 3.000 m'
  },
  {
    id: 'shear-06', category: 'dynamics', label: 'Cizalladura 0–6 km', short: 'CIZ 0–6 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 52, palette: 'shear', accent: '#b87cff', vectors: true,
    description: 'Cizalladura profunda entre 10 m y 6 km sobre el terreno, un ingrediente importante para la organización de tormentas.',
    method: 'U/V se interpolan a terreno + 6.000 m entre niveles isobáricos antes de calcular el vector diferencia.',
    coverage: 'Diagnóstico MeteoLabX · U/V + geopotencial'
  },
  {
    id: 'ebwd', category: 'dynamics', label: 'Cizalladura efectiva (EBWD)', short: 'EBWD', kind: 'derived',
    unit: 'm/s', min: 0, max: 50, palette: 'shear', accent: '#8d75ff', vectors: true,
    description: 'Diferencia vectorial del viento sobre la mitad inferior de la profundidad efectiva de la tormenta.',
    method: 'Thompson et al. (2007): base de la capa con CAPE ≥ 100 J/kg y CIN ≥ −250 J/kg hasta el 50 % de la distancia al EL de la parcela MU.',
    coverage: 'Diagnóstico MeteoLabX · perfil termodinámico y U/V AROME'
  },
  {
    id: 'precip-1h', category: 'precipitation', label: 'Precipitación en 1 hora', short: 'Precip. 1 h', kind: 'native',
    unit: 'mm', min: 0, max: 60, palette: 'precipitation', accent: '#38a8ad', vectors: false,
    description: 'Precipitación total prevista durante la hora que termina en la hora válida seleccionada.',
    method: 'Campo WCS nativo TOTAL_PRECIPITATION con periodo de acumulación PT1H. Incluye precipitación líquida y sólida en equivalente de agua.',
    coverage: 'TOTAL PRECIPITATION · superficie · PT1H'
  },
  {
    id: 'accumulated-precip', category: 'precipitation', label: 'Precipitación acumulada', short: 'Precip. acumulada', kind: 'derived',
    unit: 'mm', min: 0, max: 400, palette: 'precipitation', accent: '#479be5', vectors: false,
    // Clases en mm, no una rampa continua: un acumulado reparte casi todas sus
    // celdas por debajo de los 20 mm, y en escala lineal hasta el máximo esas
    // salen todas del mismo azul. `zeroFloor` deja el cero sin pintar para que
    // lo acumulado se lea sobre el fondo en vez de sobre una capa de color.
    scaleBreaks: [1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 400],
    zeroFloor: 0.05,
    description: 'Precipitación total acumulada desde el inicio de la pasada hasta la hora válida seleccionada.',
    method: 'MeteoLabX suma celda a celda los campos horarios TOTAL_PRECIPITATION PT1H comprendidos entre H+01 y la hora seleccionada. Incluye precipitación líquida y sólida en equivalente de agua.',
    coverage: 'Diagnóstico MeteoLabX · suma TOTAL PRECIPITATION PT1H desde el RUN'
  },
  {
    id: 'snow-precip', category: 'precipitation', label: 'Precipitación de nieve', short: 'Nieve', kind: 'native',
    unit: 'kg/m²', min: 0, max: 40, palette: 'snow', accent: '#b7dcec', vectors: false,
    description: 'Cantidad de precipitación prevista en forma de nieve.',
    method: 'Campo nativo TOTAL_SNOW_PRECIPITATION sobre la superficie.', coverage: 'TOTAL SNOW PRECIPITATION · superficie'
  },
  {
    id: 'precip-type', category: 'precipitation', label: 'Tipo de precipitación', short: 'Tipo precip.', kind: 'native',
    unit: 'clase', min: 0, max: 8, palette: 'ptype', accent: '#83a8ef', vectors: false,
    description: 'Tipo de precipitación más frecuente durante una hora: lluvia, nieve, aguanieve u otras clases publicadas por el modelo.',
    method: 'Campo categórico nativo PRECIPITATION_TYPE_60_MIN; la leyenda usará las clases del catálogo.',
    coverage: 'PRECIPITATION TYPE · 60 min'
  },
  {
    id: 'relative-humidity-700', category: 'humidity', label: 'Humedad relativa a 700 hPa', short: 'HR 700 hPa', kind: 'native',
    unit: '%', min: 0, max: 100, palette: 'humidity', accent: '#43bfaf', vectors: false,
    description: 'Humedad relativa en niveles medios, útil para reconocer bandas húmedas e intrusiones secas.',
    method: 'Campo nativo RELATIVE_HUMIDITY en la superficie isobárica de 700 hPa.', coverage: 'RELATIVE HUMIDITY · 700 hPa'
  },
  {
    id: 'precipitable-water', category: 'humidity', label: 'Agua precipitable', short: 'PWAT', kind: 'native',
    unit: 'kg/m²', min: 0, max: 55, palette: 'humidity', accent: '#3db9bc', vectors: false,
    description: 'Contenido integrado de vapor de agua en toda la columna atmosférica.',
    method: 'Campo nativo PRECIPITABLE_WATER sobre la superficie.', coverage: 'PRECIPITABLE WATER · columna'
  },
  {
    id: 'boundary-layer', category: 'dynamics', label: 'Altura de la capa límite', short: 'Capa límite', kind: 'native',
    unit: 'm', min: 0, max: 3500, palette: 'boundary', accent: '#d2a75d', vectors: false,
    description: 'Altura prevista de la capa límite planetaria, vinculada a la mezcla vertical de la baja atmósfera.',
    method: 'Campo nativo PLANETARY_BOUNDARY_LAYER_HEIGHT de AROME.', coverage: 'PLANETARY BOUNDARY LAYER HEIGHT'
  },
  {
    id: 'shortwave-down', category: 'radiation', label: 'Radiación solar descendente', short: 'Solar ↓', kind: 'native',
    unit: 'W/m²', min: 0, max: 1000, palette: 'radiation', accent: '#f3be4f', vectors: false,
    description: 'Flujo de onda corta descendente que alcanza la superficie, incluyendo la componente directa y difusa.',
    method: 'Campo DOWNWARD_SHORT_WAVE_RADIATION_FLUX PT1H. AROME entrega la energía integrada de la hora; MeteoLabX divide entre 3.600 para mostrar el flujo medio en W/m².', coverage: 'DOWNWARD SHORT WAVE RADIATION FLUX · PT1H → W/m²'
  },
  {
    id: 'direct-shortwave', category: 'radiation', label: 'Radiación solar directa', short: 'Solar directa', kind: 'native',
    unit: 'W/m²', min: 0, max: 1000, palette: 'radiation', accent: '#f29a47', vectors: false,
    description: 'Componente directa del flujo solar descendente prevista en superficie.',
    method: 'Campo nativo DOWNWARD_DIRECT_SHORT_WAVE_RADIATION_FLUX.', coverage: 'DIRECT SHORT WAVE RADIATION FLUX'
  },
  {
    id: 'longwave-down', category: 'radiation', label: 'Radiación térmica descendente', short: 'Onda larga ↓', kind: 'native',
    unit: 'W/m²', min: 150, max: 500, palette: 'longwave', accent: '#db7d83', vectors: false,
    description: 'Flujo de radiación térmica descendente emitido por la atmósfera y las nubes hacia la superficie.',
    method: 'Campo nativo DOWNWARD_LONG_WAVE_RADIATION_FLUX.', coverage: 'DOWNWARD LONG WAVE RADIATION FLUX'
  },
  {
    id: 'mu-ecape', category: 'convection', label: 'MU-ECAPE', short: 'MU-ECAPE', kind: 'native',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#f0b44f', vectors: false,
    description: 'CAPE con arrastre de la parcela más inestable en las capas bajas publicada por AROME.',
    method: 'Campo nativo CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY de AROME. El algoritmo exacto de arrastre no se reproduce fuera del modelo.',
    coverage: 'AROME · parcela MU con arrastre'
  },
  {
    id: 'ml-ecape', category: 'convection', label: 'ML-ECAPE', short: 'ML-ECAPE', kind: 'native',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#ef985d', vectors: false,
    description: 'CAPE con arrastre de una parcela representativa de la capa baja publicada por AROME.',
    method: 'Campo nativo MEAN_LAYER_CAPE de AROME. El algoritmo exacto de arrastre no se reproduce fuera del modelo.',
    coverage: 'AROME · parcela ML con arrastre'
  },
  {
    id: 'mucape-muli', category: 'convection', label: 'MUCAPE + MULI', short: 'MUCAPE · MULI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#ed8d61', vectors: false,
    description: 'MUCAPE convencional en colores, con el Lifted Index de la misma parcela MU representado mediante isolíneas.',
    method: 'MeteoLabX calcula MUCAPE y MULI sin arrastre sobre el mismo perfil termodinámico AROME.',
    coverage: 'Diagnóstico MeteoLabX · MUCAPE + isolíneas MULI', overlay: 'MULI'
  },
  {
    id: 'mlcape-mlli', category: 'convection', label: 'MLCAPE + MLLI', short: 'MLCAPE · MLLI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#e7816a', vectors: false,
    description: 'MLCAPE convencional en colores, con el Lifted Index de la misma parcela ML representado mediante isolíneas.',
    method: 'MeteoLabX calcula MLCAPE y MLLI sin arrastre sobre la misma parcela media del perfil AROME.',
    coverage: 'Diagnóstico MeteoLabX · MLCAPE + isolíneas MLLI', overlay: 'MLLI'
  },
  {
    id: 'sbcape-sbli', category: 'convection', label: 'SBCAPE + SBLI', short: 'SBCAPE · SBLI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#e97973', vectors: false,
    description: 'CAPE de la parcela superficial en colores, con su índice Lifted representado mediante isolíneas.',
    method: 'MeteoLabX calcula SBCAPE y SBLI a partir del perfil termodinámico y las condiciones superficiales de AROME.',
    coverage: 'Diagnóstico MeteoLabX · SBCAPE + isolíneas SBLI', overlay: 'SBLI'
  },
  {
    id: 'dcape', category: 'convection', label: 'DCAPE', short: 'DCAPE', kind: 'derived',
    unit: 'J/kg', min: 0, max: 1800, palette: 'convection', accent: '#df6d7f', vectors: false,
    description: 'Energía potencial disponible para corrientes descendentes, útil para valorar el potencial de reventones convectivos.',
    method: 'Diagnóstico MeteoLabX preparado a partir del descenso pseudoadiabático de una parcela representativa de niveles medios.',
    coverage: 'Diagnóstico MeteoLabX · perfil termodinámico AROME'
  },
  {
    id: 'ordinary-cell-motion', category: 'convection', label: 'Movimiento de células ordinarias', short: 'Movimiento celular', kind: 'derived',
    unit: 'm/s', min: 0, max: 35, palette: 'wind', accent: '#d96f91', vectors: true,
    description: 'Movimiento estimado de células convectivas ordinarias a partir del viento medio ponderado por presión dentro de la nube.',
    method: 'C⃗cel = (pLCL − pEL)⁻¹ ∫[pEL,pLCL] V⃗(p) dp. MeteoLabX usa el LCL y EL de la parcela de capa mezclada ML100; los colores muestran la velocidad y las streamlines la dirección.',
    coverage: 'Diagnóstico MeteoLabX · viento medio ML100 LCL–EL'
  },
  {
    id: 'cin', category: 'convection', label: 'Inhibición convectiva', short: 'CIN', kind: 'native',
    unit: 'J/kg', min: -400, max: 0, palette: 'convection', accent: '#d69b56', vectors: false,
    description: 'Energía que se opone al ascenso libre de una parcela y puede mantener inhibida la convección.',
    method: 'Campo nativo CONVECTIVE_INHIBITION sobre la superficie.', coverage: 'CONVECTIVE INHIBITION'
  },
  {
    id: 'reflectivity', category: 'convection', label: 'Reflectividad máxima', short: 'Reflectividad', kind: 'native',
    unit: 'dBZ', min: 0, max: 70, palette: 'reflectivity', accent: '#ef6f76', vectors: false,
    description: 'Reflectividad máxima simulada para visualizar áreas precipitantes y núcleos convectivos.',
    method: 'Campo nativo REFLECTIVITY_MAX_DBZ sobre la superficie.', coverage: 'REFLECTIVITY MAX · dBZ'
  },
  {
    id: 'lightning-density', category: 'convection', label: 'Densidad de rayos en 3 h', short: 'Rayos 3 h', kind: 'native',
    unit: 'rayos/km²', min: 0, max: 8, palette: 'lightning', accent: '#ecdb58', vectors: false,
    description: 'Densidad media de descargas eléctricas prevista por AROME durante tres horas.',
    method: 'Campo nativo AVERAGE_LIGHTNING_STRIKE_DENSITY_OVER_3HOURS.', coverage: 'LIGHTNING STRIKE DENSITY · 3 h'
  },
  {
    id: 'ship', category: 'convection', label: 'SHIP', short: 'SHIP', kind: 'derived',
    unit: '', min: 0, max: 5, palette: 'hail', accent: '#f07086', vectors: false,
    description: 'Significant Hail Parameter para identificar entornos favorables a granizo de tamaño significativo.',
    method: 'Formulación operacional SHARPpy/SPC: MUCAPE, razón de mezcla MU, gradiente 700–500, T500, BWD superficie–6 km y factores reductores.',
    coverage: 'Diagnóstico MeteoLabX · formulación SPC sobre perfiles AROME'
  },
  {
    id: 'cloud-cover', category: 'clouds', label: 'Nubosidad total', short: 'Nubosidad total', kind: 'native',
    unit: '%', min: 0, max: 100, palette: 'clouds', accent: '#a8b8c9', vectors: false,
    description: 'Fracción total de cielo cubierto prevista por el modelo en todo el espesor atmosférico.',
    method: 'Campo nativo TOTAL_CLOUD_COVER, convertido a porcentaje cuando sus unidades son fraccionarias.', coverage: 'TOTAL CLOUD COVER'
  },
  {
    id: 'low-cloud-cover', category: 'clouds', label: 'Nubosidad baja', short: 'Nubes bajas', kind: 'native',
    unit: '%', min: 0, max: 100, palette: 'clouds', accent: '#91aabe', vectors: false,
    description: 'Cobertura nubosa del estrato inferior prevista por AROME.',
    method: 'Campo nativo LOW_CLOUD_COVER sobre la superficie.', coverage: 'LOW CLOUD COVER'
  },
  {
    id: 'high-cloud-cover', category: 'clouds', label: 'Nubosidad alta', short: 'Nubes altas', kind: 'native',
    unit: '%', min: 0, max: 100, palette: 'clouds', accent: '#c3b8d9', vectors: false,
    description: 'Cobertura nubosa del estrato superior prevista por AROME.',
    method: 'Campo nativo HIGH_CLOUD_COVER sobre la superficie.', coverage: 'HIGH CLOUD COVER'
  },
  {
    id: 'vertical-totals', category: 'convection', label: 'Vertical Totals', short: 'VT', kind: 'derived',
    // Grados de diferencia entre dos niveles, no una temperatura: pasarlo a °F
    // con el desplazamiento de 32 daría un número sin significado.
    unit: '°C', unitFixed: true, min: 18, max: 34, palette: 'shear', accent: '#e0a458', vectors: false,
    description: 'Diferencia de temperatura entre 850 y 500 hPa. Mide el gradiente térmico del entorno sin depender de qué parcela se elija, así que no comparte las ambigüedades de los CAPE. Valores altos con poca humedad en niveles bajos señalan el ambiente de reventones secos.',
    method: 'T850 menos T500, ambos del paquete isobárico IP1 que ya se descarga para los perfiles.',
    coverage: 'TEMPERATURE · 850 y 500 hPa'
  },
  {
    id: 'mslp-theta-e-850', category: 'dynamics', label: 'θₑ 850 hPa y presión al nivel del mar', short: 'θₑ 850 · MSLP', kind: 'derived',
    unit: '°C', min: -10, max: 60, palette: 'temperature', accent: '#5ac8a8', vectors: false,
    contents: 'Masas de aire · Presión',
    // Isobaras cada 4 hPa, una de cada cinco marcada, y sin suavizar: mover la
    // línea la separaría del campo. El geopotencial sí se suaviza; la presión
    // al nivel del mar, no.
    overlayStep: 4, overlayMajorStep: 20, overlaySmoothing: 0, overlay: '',
    overlayLayerLabel: 'Isobaras',
    pressureCentres: true,
    description: 'Temperatura potencial equivalente en 850 hPa, en color, con la presión al nivel del mar en isobaras y los centros de acción marcados. Es el mapa de masas de aire: la theta-e resume en un número el calor y la humedad que trae el aire, y se conserva cuando sube o baja.',
    method: 'Theta-e de Bolton (1980) calculada con MetPy sobre la temperatura y el rocío nativos de 850 hPa, con el rocío recortado a la temperatura. Se enmascara donde la presión en superficie no llega a 850 hPa, es decir donde ese nivel queda bajo tierra.',
    coverage: 'AROME · T y Td isobáricos · presión en superficie · MSLP'
  },
  {
    id: 'srh-01', category: 'convection', label: 'Helicidad relativa 0–1 km', short: 'SRH 0–1', kind: 'derived',
    unit: 'm²/s²', min: -200, max: 500, palette: 'shear', accent: '#c084fc', vectors: true,
    description: 'Helicidad relativa a la tormenta en el primer kilómetro, referida al movimiento de la supercélula derecha de Bunkers. Mide el giro que una corriente ascendente puede heredar del entorno, y es el nivel que más se asocia con la tornadogénesis.',
    method: 'Integral del hodógrafo entre 0 y 1.000 m sobre el terreno, restando el movimiento Bunkers 2000 right mover. Sale del mismo perfil de viento que los demás diagnósticos convectivos.',
    coverage: 'Perfil de viento AROME · 0–1 km AGL'
  },
  {
    id: 'srh-03', category: 'convection', label: 'Helicidad relativa 0–3 km', short: 'SRH 0–3', kind: 'derived',
    unit: 'm²/s²', min: -300, max: 600, palette: 'shear', accent: '#a855f7', vectors: true,
    description: 'Helicidad relativa a la tormenta en los tres primeros kilómetros, referida al movimiento de la supercélula derecha de Bunkers. Es la capa habitual para valorar el potencial de rotación de una supercélula.',
    method: 'Integral del hodógrafo entre 0 y 3.000 m sobre el terreno, restando el movimiento Bunkers 2000 right mover. Sale del mismo perfil de viento que los demás diagnósticos convectivos.',
    coverage: 'Perfil de viento AROME · 0–3 km AGL'
  },
  {
    id: 'vv-lfc', category: 'convection', label: 'Velocidad vertical en el NCL', short: 'w en NCL', kind: 'derived',
    unit: 'm/s', min: -5, max: 10, palette: 'shear', accent: '#4ade80', vectors: true,
    description: 'Velocidad vertical del modelo interpolada al nivel de convección libre de la parcela de capa mezclada. Un ascenso que alcanza ese nivel dispara la convección; el que se queda por debajo se embotella bajo la inversión, y una convergencia en superficie no distingue esos dos casos.',
    method: 'Velocidad vertical geométrica del paquete isobárico IP3, interpolada a la altura del NCL que calcula la parcela ML100. Las flechas son el viento de 10 m.',
    coverage: 'IP3 · velocidad vertical en niveles isobáricos'
  },
  {
    id: 'updraft-helicity', category: 'convection', label: 'Helicidad de la corriente ascendente 2–5 km', short: 'UH 2–5', kind: 'derived',
    unit: 'm²/s²', min: -50, max: 250, palette: 'shear', accent: '#f472b6', vectors: false,
    description: 'Diagnóstico de la rotación que el propio modelo genera dentro de una corriente ascendente: integra el producto de la velocidad vertical por la vorticidad vertical entre 2 y 5 km sobre el terreno. Mide cuánto coinciden el ascenso y el giro, así que separa una tormenta rotatoria de otra que sólo sube con fuerza: es el rastro que deja una supercélula en un modelo que resuelve la convección.',
    method: 'Vorticidad vertical de cada nivel isobárico con las distancias en metros, multiplicada por la velocidad vertical de IP3 e integrada por trapecios entre 2.000 y 5.000 m AGL.',
    coverage: 'IP1 · viento y geopotencial · IP3 · velocidad vertical'
  },
];

// Selección inicial deliberadamente corta. El catálogo completo queda listo para
// incorporar nuevos mapas cuando se decida qué variables formarán el producto.
const initialProductIds = [
  'vertical-totals',
  'mslp-theta-e-850',
  'srh-01',
  'srh-03',
  'vv-lfc',
  'updraft-helicity',
  'temperature-2m',
  'temperature-850',
  'temperature-500',
  'wind-level',
  'wind-gust',
  'shear-01',
  'shear-03',
  'shear-06',
  'ebwd',
  'precip-1h',
  'accumulated-precip',
  'relative-humidity-700',
  'shortwave-down',
  'mu-ecape',
  'ml-ecape',
  'mucape-muli',
  'mlcape-mlli',
  'sbcape-sbli',
  'dcape',
  'ordinary-cell-motion',
  'ship',
  'cloud-cover'
];

export const forecastProducts = initialProductIds.map((id) => {
  const product = allForecastProducts.find((item) => item.id === id);
  return { ...product, guide: forecastProductGuides[id] };
});

export const forecastCatalogSummary = {
  selectedNative: forecastProducts.filter((item) => item.kind === 'native').length,
  selectedDerived: forecastProducts.filter((item) => item.kind === 'derived').length,
  approximateNativeTotal: 35
};
