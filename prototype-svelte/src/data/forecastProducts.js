import { forecastProductGuides } from './forecastProductGuides.js';

// Modelos conectados. El identificador es también el trozo de ruta de la API
// —`/v1/forecast/<id>/…`— y el namespace del almacén, así que separar un
// modelo del otro se hace en un solo sitio.
const connectedForecastModels = [
  {
    id: 'arome',
    label: 'AROME 0,025°',
    short: 'AROME',
    origin: 'Météo-France',
    domain: 'Francia y entorno · 2,5 km',
    horizon: '+51 h'
  },
  {
    // El mismo AROME, acoplado e inicializado con el IFS del CEPPM en vez de
    // con ARPEGE. Publica exactamente los mismos mapas —`products`— sobre el
    // mismo dominio, porque sale del mismo cálculo.
    id: 'arome-ifs',
    label: 'AROME-IFS 0,025°',
    short: 'AROME-IFS',
    origin: 'Météo-France · IFS',
    domain: 'Francia y entorno · 2,5 km',
    horizon: '+51 h',
    products: 'arome'
  },
  {
    id: 'ecmwf',
    label: 'ECMWF IFS 0,25°',
    short: 'ECMWF',
    origin: 'ECMWF · open data',
    domain: 'Europa y Atlántico oriental · 25 km',
    horizon: '+144 h'
  }
];

// Todos públicos. Sus rutas de la API siguen dependiendo de
// METEOLABX_ENABLE_ECMWF y METEOLABX_ENABLE_AROME_IFS en el servicio: sin
// ellas, el modelo aparece en la barra pero su catálogo responde 404.
export const forecastModels = connectedForecastModels;

export const DEFAULT_FORECAST_MODEL = 'arome';

/**
 * De qué modelo toma sus mapas y sus zonas. Casi siempre es él mismo;
 * AROME-IFS usa los de AROME en vez de repetir la lista.
 */
export function productFamily(modelId = DEFAULT_FORECAST_MODEL) {
  return connectedForecastModels.find((model) => model.id === modelId)?.products || modelId;
}

/**
 * Dominios de cada modelo.
 *
 * Los de ECMWF son recortes que calcula el servidor: cada uno tiene sus
 * propios mapas. Los de AROME son zonas del mismo dominio que el visor recorta
 * del mapa ya descargado (`bounds`), así que cambiar de zona no pide nada.
 * El primero de cada lista es el de entrada.
 */
export const forecastDomains = {
  arome: [
    { id: 'full', label: 'Dominio completo' },
    { id: 'iberia', label: 'Península Ibérica', bounds: [-10, 37.5, 4.6, 44.2] },
    { id: 'france', label: 'Francia', bounds: [-5.5, 41.2, 10, 51.3] },
    { id: 'italy', label: 'Italia', bounds: [6.4, 37.5, 16, 47.2] },
    { id: 'alps', label: 'Suiza y Austria', bounds: [5.8, 45.6, 16, 48.9] },
    { id: 'british-isles', label: 'Islas Británicas', bounds: [-11, 49.8, 2.1, 55.4] },
    { id: 'benelux-germany', label: 'Benelux y Alemania', bounds: [2.4, 47.2, 15.2, 55.2] }
  ],
  ecmwf: [
    { id: 'europe', label: 'Europa y Atlántico', server: true },
    { id: 'middle-east', label: 'Oriente Medio y Asia central', server: true },
    { id: 'north-america', label: 'Norteamérica', server: true },
    { id: 'east-asia', label: 'Asia oriental y Pacífico', server: true },
    { id: 'south-america', label: 'Sudamérica', server: true },
    { id: 'australia', label: 'Australia y Nueva Zelanda', server: true }
  ]
};

export function domainsForModel(modelId = DEFAULT_FORECAST_MODEL) {
  return forecastDomains[productFamily(modelId)] || [];
}

export const forecastCategories = [
  { id: 'temperature', label: 'Temperatura' },
  { id: 'precipitation', label: 'Precipitación' },
  { id: 'dynamics', label: 'Viento y dinámica' },
  { id: 'instability', label: 'Inestabilidad' },
  { id: 'shear', label: 'Cizalladura y helicidad' },
  { id: 'forcing', label: 'Forzamiento y cinemática' },
  { id: 'severe', label: 'Tiempo severo' },
  { id: 'clouds', label: 'Nubes y humedad' }
];

const allForecastProducts = [
  {
    id: 'ecmwf-temperature-850', model: 'ecmwf', category: 'temperature',
    label: 'Temperatura y geopotencial 850 hPa', short: 'T/Z 850 hPa', kind: 'native',
    unit: '°C', min: -24, max: 36, palette: 'temperature', accent: '#ed7f61', vectors: false,
    contourStep: 2, nationalBoundariesOnly: true, contents: 'Temperatura · Geopotencial',
    overlayStep: 3, overlayMajorStep: 6, overlaySmoothing: 1,
    description: 'Temperatura en 850 hPa con las isohipsas de ese nivel: el mapa clásico de masas de aire a varios días vista.',
    method: 'Temperatura y altura geopotencial de ECMWF en 850 hPa. Se ocultan los puntos donde ese nivel queda bajo el relieve.',
    coverage: 'ECMWF IFS 0,25° · t y gh 850 hPa'
  },
  {
    id: 'ecmwf-temperature-500', model: 'ecmwf', category: 'temperature',
    label: 'Temperatura y geopotencial 500 hPa', short: 'T/Z 500 hPa', kind: 'native',
    unit: '°C', min: -42, max: -2, palette: 'temperature', accent: '#bc6ed0', vectors: false,
    contourStep: 2, nationalBoundariesOnly: true, contents: 'Temperatura · Geopotencial',
    overlayStep: 6, overlayMajorStep: 12, overlaySmoothing: 1,
    // Ejes de vaguada sobre el geopotencial de 500 hPa, como en AROME.
    troughAxes: true,
    description: 'Temperatura en 500 hPa con las isohipsas de ese nivel y los ejes de vaguada: aire frío en altura, DANAs y la onda que dirige el tiempo.',
    method: 'Temperatura y altura geopotencial de ECMWF en 500 hPa. Los ejes de vaguada se detectan sobre el geopotencial.',
    coverage: 'ECMWF IFS 0,25° · t y gh 500 hPa'
  },
  {
    id: 'ecmwf-precip-6h', model: 'ecmwf', category: 'precipitation',
    label: 'Precipitación en 6 horas y presión al nivel del mar', short: 'Precip. 6 h · MSLP', kind: 'native',
    unit: 'mm', min: 0, max: 60, palette: 'precipitation', accent: '#38a8ad', vectors: false,
    // Clases en mm, como el acumulado de AROME; por debajo de 0,1 mm no se
    // pinta, para que se vea dónde no llueve.
    scaleBreaks: [0.5, 1, 2, 5, 10, 15, 20, 30, 40, 60], zeroFloor: 0.1,
    overlayStep: 4, overlayMajorStep: 20, overlaySmoothing: 2, overlay: '',
    overlayLayerLabel: 'Isobaras', pressureCentres: true,
    nationalBoundariesOnly: true, contents: 'Precipitación · Presión',
    description: 'Precipitación total de las 6 horas que terminan en la hora seleccionada, con la presión al nivel del mar en isobaras y los centros de acción. Desde la +6 hasta seis días vista.',
    method: 'Diferencia entre la precipitación acumulada de ECMWF en la hora válida y 6 horas antes. Incluye lluvia y nieve en equivalente de agua.',
    coverage: 'ECMWF IFS 0,25° · tp · MSLP'
  },
  {
    id: 'ecmwf-precip-accumulated', model: 'ecmwf', category: 'precipitation',
    label: 'Precipitación acumulada', short: 'Precip. acumulada', kind: 'derived',
    // La misma escala que el acumulado de AROME, para poder compararlos.
    unit: 'mm', min: 0, max: 800, palette: 'precipitation-extended', paletteSplit: 400, accent: '#479be5', vectors: false,
    scaleBreaks: [1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 400, 500, 600, 800],
    zeroFloor: 0.05,
    nationalBoundariesOnly: true,
    description: 'Precipitación caída entre dos horas elegidas con los deslizadores A y B, hasta seis días vista.',
    method: 'Diferencia entre la precipitación acumulada de ECMWF en B y en A. Incluye lluvia y nieve en equivalente de agua.',
    coverage: 'ECMWF IFS 0,25° · tp acumulada desde la pasada'
  },
  {
    id: 'ecmwf-precipitable-water', model: 'ecmwf', category: 'clouds',
    label: 'Agua precipitable', short: 'PWAT', kind: 'native',
    unit: 'kg/m²', min: 0, max: 70, palette: 'humidity', accent: '#3db9bc', vectors: false,
    nationalBoundariesOnly: true,
    description: 'Vapor de agua contenido en toda la columna atmosférica, expresado como el espesor de agua que daría si se condensara entero.',
    method: 'Campo nativo tcwv del IFS: vapor de agua integrado en toda la columna. 1 kg/m² equivale a 1 mm de agua.',
    coverage: 'ECMWF IFS 0,25° · tcwv'
  },
  {
    id: 'ecmwf-eady-850-500', model: 'ecmwf', category: 'dynamics',
    label: 'Tasa de crecimiento de Eady 850-500 hPa', short: 'Eady 850-500', kind: 'derived',
    unit: 'día⁻¹', min: 0.3, max: 2.5, palette: 'shear', accent: '#c46bd1', vectors: false,
    // Por debajo de 0,3 día⁻¹ no se pinta: queda la zona baroclina, donde las
    // borrascas pueden crecer, y no un fondo de color en todo el mapa.
    zeroFloor: 0.3,
    overlayStep: 6, overlayMajorStep: 12, overlaySmoothing: 1, overlay: '',
    nationalBoundariesOnly: true, contents: 'Crecimiento baroclino · Geopotencial',
    description: 'Lo rápido que podría crecer una borrasca en la capa 850-500 hPa, según la cizalladura del viento y la estabilidad, con las isohipsas de 500 hPa. Marca las zonas propicias para la ciclogénesis.',
    method: 'Tasa de Eady con la cizalladura entre 850 y 500 hPa y la estabilidad de esa capa, de ECMWF, suavizadas unos 40 km. Se oculta donde 850 hPa queda a menos de unos 500 m del suelo.',
    coverage: 'ECMWF IFS 0,25° · u, v, t y gh 850 y 500 hPa · presión en superficie'
  },
  {
    id: 'ecmwf-jet-300', model: 'ecmwf', category: 'dynamics',
    label: 'Jet stream en 300 hPa', short: 'Jet 300', kind: 'native',
    unit: 'm/s', min: 10, max: 75, palette: 'wind', accent: '#4db6e8', vectors: true, flowLines: true,
    // Por debajo de 10 m/s ni color ni líneas de corriente: queda el jet y,
    // con él, la forma de la onda, sin necesidad de isohipsas.
    zeroFloor: 10, flowMinMagnitude: 10,
    nationalBoundariesOnly: true, contents: 'Viento',
    description: 'Velocidad y dirección del viento en 300 hPa a partir de 10 m/s: el jet stream que guía las borrascas.',
    method: 'Componentes U y V de ECMWF en 300 hPa; la velocidad es su módulo.',
    coverage: 'ECMWF IFS 0,25° · u y v 300 hPa'
  },
  {
    id: 'ecmwf-frontogenesis-850', model: 'ecmwf', category: 'forcing',
    label: 'Frontogénesis a 850 hPa', short: 'Frontogénesis 850', kind: 'derived',
    unit: 'K/100 km/3 h', min: -4, max: 4, palette: 'diverging', accent: '#e8826b', vectors: false,
    // Casi todo el campo está por debajo de 1: esa franja se lleva el 60 % de
    // la rampa para que se vean también los frentes débiles.
    scaleAnchors: [[-4, 0], [-1, .2], [-.2, .4], [0, .5], [.2, .6], [1, .8], [4, 1]], scaleTicks: [-4, -1, 0, 1, 4],
    // Isentrópicas de 850 hPa cada 2 K, con un suavizado de dibujo de dos
    // celdas para que no tiemblen.
    overlayStep: 2, overlayMajorStep: 10, overlaySmoothing: 2, overlay: '',
    overlayLayerLabel: 'Isentrópicas',
    nationalBoundariesOnly: true, contents: 'Frontogénesis · Temperatura potencial',
    description: 'Dónde el viento en 850 hPa está intensificando (rojo) o debilitando (azul) los frentes. Las líneas son isentrópicas: unen puntos con la misma temperatura potencial, cada 2 K, y donde se aprietan hay un frente.',
    method: 'Frontogénesis cinemática de Petterssen con el viento y la temperatura potencial de ECMWF en 850 hPa, suavizados unos 40 km.',
    coverage: 'ECMWF IFS 0,25° · t, u y v 850 hPa · presión en superficie'
  },
  {
    id: 'ecmwf-omega-700', model: 'ecmwf', category: 'forcing',
    label: 'Velocidad vertical a 700 hPa', short: '−ω 700', kind: 'native',
    unit: 'Pa/s', min: -1.5, max: 1.5, palette: 'diverging', accent: '#7aa2f7', vectors: false,
    // Casi todo el campo está por debajo de 0,5 Pa/s: esa franja se lleva el
    // 60 % de la rampa para que se lea el ascenso sinóptico, no solo el
    // convectivo.
    scaleAnchors: [[-1.5, 0], [-.5, .2], [-.1, .4], [0, .5], [.1, .6], [.5, .8], [1.5, 1]], scaleTicks: [-1.5, -.5, 0, .5, 1.5],
    overlayStep: 3, overlayMajorStep: 12, overlaySmoothing: 1, overlay: '',
    nationalBoundariesOnly: true, contents: 'Velocidad vertical · Geopotencial',
    description: 'Movimiento vertical del aire en 700 hPa según ECMWF, con las isohipsas de ese nivel. Rojo: ascenso; azul: descenso.',
    method: 'Velocidad vertical ω de ECMWF en 700 hPa, cambiada de signo para que el ascenso sea positivo y suavizada unos 40 km. Se oculta donde ese nivel queda bajo el relieve.',
    coverage: 'ECMWF IFS 0,25° · w y gh 700 hPa · presión en superficie'
  },
  {
    id: 'relative-vorticity-500', model: 'ecmwf', category: 'dynamics',
    label: 'Vorticidad relativa a 500 hPa', short: 'Vorticidad 500', kind: 'derived',
    unit: '10⁻⁵ s⁻¹', min: -30, max: 30, palette: 'diverging', accent: '#7aa2f7', vectors: false,
    // La mayor parte del campo está entre −10 y 10: esa franja se lleva el
    // 60 % de la rampa para que se lean también las vaguadas débiles.
    scaleAnchors: [[-30, 0], [-10, .2], [-3, .4], [0, .5], [3, .6], [10, .8], [30, 1]], scaleTicks: [-30, -10, 0, 10, 30],
    nationalBoundariesOnly: true, contents: 'Vorticidad relativa · Geopotencial',
    overlayStep: 6, overlayMajorStep: 12, overlaySmoothing: 1, overlay: '',
    description: 'Rotación del viento a 500 hPa: valores positivos ciclónicos y negativos anticiclónicos en el hemisferio norte.',
    method: 'Vorticidad relativa que publica ECMWF a 500 hPa, sin sumar Coriolis, con un suavizado ligero de unos 28 km.',
    coverage: 'ECMWF IFS · vorticidad y geopotencial 500 hPa · presión en superficie'
  },
  {
    id: 'q-vectors-700', model: 'ecmwf', category: 'forcing',
    label: 'Vectores Q y forzamiento vertical a 700 hPa', short: 'Q · −2∇·Q 700', kind: 'derived',
    unit: '10⁻¹⁷ m kg⁻¹ s⁻¹', min: -4, max: 4, palette: 'diverging', accent: '#7aa2f7', vectors: true,
    // |Q| típico: mediana ~2·10⁻¹³ y percentil 97 ~10⁻¹². Por debajo de 10⁻¹³
    // la flecha no dice nada; a partir de 10⁻¹² alcanza su longitud máxima.
    vectorMinMagnitude: 1e-13, vectorScaleMagnitude: 1e-12,
    scaleAnchors: [[-4, 0], [-1.5, .15], [-.3, .38], [0, .5], [.3, .62], [1.5, .85], [4, 1]], scaleTicks: [-4, -1.5, 0, 1.5, 4],
    overlayStep: 3, overlayMajorStep: 12, overlaySmoothing: 1, overlay: '',
    nationalBoundariesOnly: true, contents: 'Vectores Q · Forzamiento vertical · Geopotencial',
    description: 'Vectores Q a 700 hPa con el forzamiento vertical que producen en color e isohipsas de 700 hPa. Rojo: forzamiento de ascenso; azul: de descenso.',
    method: 'Q a partir del viento geostrófico y la temperatura a 700 hPa, suavizados antes a escala sinóptica (unos 165 km). El color es −2∇·Q.',
    coverage: 'ECMWF IFS · T/Z 700 hPa · presión en superficie'
  },
  {
    id: 'ecmwf-mslp-theta-e-850', model: 'ecmwf', category: 'dynamics',
    label: 'θₑ 850 hPa y presión al nivel del mar', short: 'θₑ 850 · MSLP', kind: 'derived',
    unit: '°C', min: -10, max: 90, palette: 'theta-e', accent: '#5ac8a8', vectors: false,
    // Hasta 60 °C, los diez colores de siempre con el mismo reparto (9 de los
    // 13 tramos de la paleta); de 60 a 90, la cola tropical. En el trópico la
    // θe en 850 hPa anda por 65–80 °C y el núcleo de un huracán pasa de 85, y
    // con la escala anterior todo eso salía del mismo color.
    scaleAnchors: [[-10, 0], [60, 9 / 13], [90, 1]], scaleTicks: [-10, 10, 30, 50, 70, 90],
    contents: 'Masas de aire · Presión',
    // Isobaras cada 4 hPa y una de cada cinco marcada, con un suavizado de
    // dibujo de dos celdas: a 25 km el campo ya es suave y más filtro movería
    // la línea respecto al dato.
    overlayStep: 4, overlayMajorStep: 20, overlaySmoothing: 2, overlay: '',
    overlayLayerLabel: 'Isobaras',
    pressureCentres: true,
    nationalBoundariesOnly: true,
    description: 'Temperatura potencial equivalente en 850 hPa, en color, con la presión al nivel del mar en isobaras y los centros de acción marcados. Es el mapa de masas de aire y frentes, hasta seis días vista.',
    method: 'Theta-e de Bolton (1980) calculada con MetPy sobre la temperatura y la humedad específica de ECMWF en 850 hPa. Se oculta donde ese nivel queda bajo el relieve.',
    coverage: 'ECMWF IFS 0,25° · t y q 850 hPa · presión en superficie · MSLP'
  },
  {
    id: 'temperature-2m', category: 'temperature', label: 'Temperatura a 2 m', short: 'T 2 m', kind: 'native',
    unit: '°C', min: -25, max: 45, palette: 'temperature', accent: '#ff8a5b', vectors: false,
    // La escala llega a −25 para no recortar las noches de invierno en los
    // Alpes, pero repartir setenta grados a partes iguales dejaría sin
    // contraste la franja de 0 a 30 °C, que es donde está casi todo el campo
    // casi todo el año. Los nodos le dan a esos treinta grados el 58 % de la
    // rampa y comprimen las colas, que se visitan un par de veces por invierno.
    scaleAnchors: [[-25, 0], [-10, 0.1], [0, 0.22], [10, 0.42], [20, 0.62], [30, 0.8], [40, 0.94], [45, 1]],
    scaleTicks: [-25, -10, 0, 10, 20, 30, 45],
    cityLabels: true,
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
    method: 'Temperatura de AROME en el nivel de 850 hPa, con el geopotencial de ese nivel en isohipsas.',
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
    method: 'Temperatura de AROME en el nivel de 500 hPa, con el geopotencial de ese nivel en isohipsas.',
    coverage: 'TEMPERATURE · 500 hPa'
  },
  {
    id: 'freezing-level', category: 'temperature', label: 'Altura de la iso 0 °C', short: 'Iso 0 °C', kind: 'derived',
    unit: 'm', unitFixed: true, min: 0, max: 5000, palette: 'temperature', accent: '#a5d4f2', vectors: false,
    contourStep: 250, contourLayerId: 'freezingContours', multipleSolutions: true,
    description: 'Altitud sobre el nivel del mar a la que el aire cruza los 0 °C. Con inversiones puede haber varias isoceros; se muestra la más alta.',
    method: 'Perfil de temperatura desde 2 m hasta los niveles de presión de AROME, con el cruce de 0 °C interpolado entre niveles. La capa «Zonas con varias soluciones» marca las columnas que lo cruzan más de una vez.',
    coverage: 'Diagnóstico MeteoLabX · temperatura/geopotencial del perfil'
  },
  {
    id: 'snow-level', category: 'precipitation', label: 'Cota de nieve', short: 'Cota nieve', kind: 'derived',
    unit: 'm', unitFixed: true, min: 0, max: 3500, palette: 'temperature', accent: '#8bcce8', vectors: false,
    multipleSolutions: true, contourStep: 250, contourLayerId: 'snowContours',
    description: 'Altitud a partir de la cual la precipitación prevista caería como nieve, donde el bulbo húmedo cruza los 0,5 °C. Solo donde hay precipitación; con inversiones se muestra el cruce más alto.',
    method: 'Bulbo húmedo de cada nivel a partir de temperatura, humedad y presión, con el cruce de 0,5 °C interpolado entre la superficie y los niveles de presión de AROME. La capa «Zonas con varias soluciones» marca las columnas con más de un cruce.',
    coverage: 'Diagnóstico MeteoLabX · perfil T/HR/geopotencial · precipitación 1 h'
  },
  {
    id: 'wet-bulb-2m', category: 'temperature', label: 'Temperatura de bulbo húmedo', short: 'Tw 2 m', kind: 'native',
    unit: '°C', min: -8, max: 30, palette: 'humidity', accent: '#45c4c6', vectors: false,
    description: 'Temperatura de bulbo húmedo cerca de superficie. Ayuda a estimar enfriamiento evaporativo y transiciones del tipo de precipitación.',
    method: 'Campo nativo WET_BULB_TEMPERATURE en nivel de altura específica.', coverage: 'WET BULB TEMPERATURE · altura 2 m'
  },
  {
    id: 'wind-level', category: 'dynamics', label: 'Viento por niveles', short: 'Viento', kind: 'native',
    unit: 'm/s', min: 0, max: 55, palette: 'wind', accent: '#4db6e8', vectors: true, flowLines: true,
    description: 'Velocidad y dirección del viento en alturas sobre el terreno o superficies isobáricas.',
    method: 'Magnitud calculada a partir de las componentes U/V nativas del nivel seleccionado; las flechas muestran la dirección.',
    coverage: 'U/V · altura geométrica o nivel isobárico'
  },
  {
    id: 'wind-gust', category: 'dynamics', label: 'Racha máxima horaria a 10 m', short: 'Racha máx. 10 m', kind: 'native',
    unit: 'm/s', min: 0, max: 45, palette: 'wind', accent: '#62a9f5', vectors: false,
    cityLabels: true,
    description: 'Racha máxima prevista durante la hora, útil para localizar aceleraciones por relieve, frentes y convección.',
    method: 'Racha máxima de AROME (WIND_SPEED_GUST_MAX) a 10 m durante la hora anterior.', coverage: 'WIND SPEED GUST MAX · 10 m · 1 h'
  },
  {
    id: 'shear-01', category: 'shear', label: 'Cizalladura 0–1 km', short: 'CIZ 0–1 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 26, palette: 'shear', accent: '#57b6ff', vectors: true,
    description: 'Cizalladura vectorial entre el viento a 10 m y 1.000 m sobre el terreno. Describe el cambio de viento en la capa más baja.',
    method: '√[(u₁₀₀₀ − u₁₀)² + (v₁₀₀₀ − v₁₀)²]. Las flechas muestran el vector diferencia.',
    coverage: 'Diagnóstico MeteoLabX · U/V 10 y 1.000 m'
  },
  {
    id: 'shear-03', category: 'shear', label: 'Cizalladura 0–3 km', short: 'CIZ 0–3 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 36, palette: 'shear', accent: '#7d8cff', vectors: true,
    description: 'Cizalladura vectorial entre el viento a 10 m y 3.000 m, relevante para la organización de la convección.',
    method: '√[(u₃₀₀₀ − u₁₀)² + (v₃₀₀₀ − v₁₀)²]. Las rejillas se alinean antes de operar.',
    coverage: 'Diagnóstico MeteoLabX · U/V 10 y 3.000 m'
  },
  {
    id: 'shear-06', category: 'shear', label: 'Cizalladura 0–6 km', short: 'CIZ 0–6 km', kind: 'derived',
    unit: 'm/s', min: 0, max: 52, palette: 'shear', accent: '#b87cff', vectors: true,
    description: 'Cizalladura profunda entre 10 m y 6 km sobre el terreno, un ingrediente importante para la organización de tormentas.',
    method: 'U/V se interpolan a terreno + 6.000 m entre niveles isobáricos antes de calcular el vector diferencia.',
    coverage: 'Diagnóstico MeteoLabX · U/V + geopotencial'
  },
  {
    id: 'ebwd', category: 'shear', label: 'Cizalladura efectiva (EBWD)', short: 'EBWD', kind: 'derived',
    unit: 'm/s', min: 0, max: 50, palette: 'shear', accent: '#8d75ff', vectors: true,
    description: 'Diferencia vectorial del viento sobre la mitad inferior de la profundidad efectiva de la tormenta.',
    method: 'Thompson et al. (2007): base de la capa con CAPE ≥ 100 J/kg y CIN ≥ −250 J/kg hasta el 50 % de la distancia al EL de la parcela MU.',
    coverage: 'Diagnóstico MeteoLabX · perfil termodinámico y U/V AROME'
  },
  {
    id: 'precip-1h', category: 'precipitation', label: 'Precipitación en 1 hora', short: 'Precip. 1 h', kind: 'native',
    // Hasta 140 mm/h y con el tramo hacia el blanco: 60 se quedaba corto en los
    // núcleos convectivos del Mediterráneo. Por debajo de 60, los colores de
    // siempre (`paletteSplit`).
    unit: 'mm', min: 0, max: 140, palette: 'precipitation-extended', paletteSplit: 60, accent: '#38a8ad', vectors: false,
    cityLabels: true,
    description: 'Precipitación total prevista durante la hora que termina en la hora válida seleccionada.',
    method: 'Precipitación total de AROME (TOTAL_PRECIPITATION) acumulada en una hora. Incluye precipitación líquida y sólida en equivalente de agua.',
    coverage: 'TOTAL PRECIPITATION · 1 h'
  },
  {
    id: 'accumulated-precip', category: 'precipitation', label: 'Precipitación acumulada', short: 'Precip. acumulada', kind: 'derived',
    unit: 'mm', min: 0, max: 800, palette: 'precipitation-extended', paletteSplit: 400, accent: '#479be5', vectors: false,
    // Clases en mm, no una rampa continua: un acumulado reparte casi todas sus
    // celdas por debajo de los 20 mm, y en escala lineal hasta el máximo esas
    // salen todas del mismo azul. `zeroFloor` deja el cero sin pintar para que
    // lo acumulado se lea sobre el fondo en vez de sobre una capa de color.
    // Hasta 800 mm: un episodio largo en el Mediterráneo pasa de los 400.
    scaleBreaks: [1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 400, 500, 600, 800],
    zeroFloor: 0.05,
    cityLabels: true,
    description: 'Precipitación total acumulada desde el inicio de la pasada hasta la hora válida seleccionada.',
    method: 'MeteoLabX suma celda a celda la precipitación de cada hora (TOTAL_PRECIPITATION) entre H+01 y la hora seleccionada. Incluye precipitación líquida y sólida en equivalente de agua.',
    coverage: 'Diagnóstico MeteoLabX · suma de TOTAL PRECIPITATION desde el RUN'
  },
  {
    id: 'snow-precip', category: 'precipitation', label: 'Precipitación de nieve', short: 'Nieve', kind: 'native',
    unit: 'kg/m²', min: 0, max: 40, palette: 'snow', accent: '#b7dcec', vectors: false,
    description: 'Cantidad de precipitación prevista en forma de nieve.',
    method: 'Campo nativo TOTAL_SNOW_PRECIPITATION sobre la superficie.', coverage: 'TOTAL SNOW PRECIPITATION · superficie'
  },
  {
    id: 'precip-type', category: 'precipitation', label: 'Tipo de precipitación', short: 'Tipo precip.', kind: 'native',
    unit: 'clase', min: 0, max: 12, palette: 'ptype', accent: '#83a8ef', vectors: false, cityLabels: true,
    description: 'Qué llegaría al suelo durante la hora seleccionada: lluvia, llovizna, nieve seca o húmeda, aguanieve, precipitación engelante, gránulos de hielo, nieve granulada o granizo.',
    method: 'Diagnóstico de tipo de precipitación que publica AROME para cada hora. Las variantes intermitentes y la nieve pegajosa se agrupan con su tipo principal.',
    coverage: 'PRECIPITATION TYPE · 60 min'
  },
  {
    id: 'relative-humidity-700', category: 'clouds', label: 'Humedad relativa a 700 hPa', short: 'HR 700 hPa', kind: 'native',
    unit: '%', min: 0, max: 100, palette: 'humidity', accent: '#43bfaf', vectors: false,
    description: 'Humedad relativa en niveles medios, útil para reconocer bandas húmedas e intrusiones secas.',
    method: 'Campo nativo RELATIVE_HUMIDITY en la superficie isobárica de 700 hPa.', coverage: 'RELATIVE HUMIDITY · 700 hPa'
  },
  {
    id: 'precipitable-water', category: 'clouds', label: 'Agua precipitable', short: 'PWAT', kind: 'native',
    // La misma escala que el de ECMWF, para poder compararlos.
    unit: 'kg/m²', min: 0, max: 70, palette: 'humidity', accent: '#3db9bc', vectors: false,
    description: 'Vapor de agua contenido en toda la columna atmosférica, expresado como el espesor de agua que daría si se condensara entero.',
    method: 'Campo nativo PRECIPITABLE_WATER de AROME: vapor de agua integrado desde la superficie hasta el tope del modelo. 1 kg/m² equivale a 1 mm de agua.', coverage: 'PRECIPITABLE WATER · columna'
  },
  {
    id: 'boundary-layer', category: 'dynamics', label: 'Altura de la capa límite', short: 'Capa límite', kind: 'native',
    unit: 'm', min: 0, max: 3500, palette: 'boundary', accent: '#d2a75d', vectors: false,
    description: 'Altura prevista de la capa límite planetaria, vinculada a la mezcla vertical de la baja atmósfera.',
    method: 'Campo nativo PLANETARY_BOUNDARY_LAYER_HEIGHT de AROME.', coverage: 'PLANETARY BOUNDARY LAYER HEIGHT'
  },
  {
    id: 'direct-shortwave', category: 'clouds', label: 'Radiación solar directa', short: 'Solar directa', kind: 'native',
    unit: 'W/m²', min: 0, max: 1000, palette: 'radiation', accent: '#f29a47', vectors: false,
    description: 'Componente directa del flujo solar descendente prevista en superficie.',
    method: 'Campo nativo DOWNWARD_DIRECT_SHORT_WAVE_RADIATION_FLUX.', coverage: 'DIRECT SHORT WAVE RADIATION FLUX'
  },
  {
    id: 'longwave-down', category: 'clouds', label: 'Radiación térmica descendente', short: 'Onda larga ↓', kind: 'native',
    unit: 'W/m²', min: 150, max: 500, palette: 'longwave', accent: '#db7d83', vectors: false,
    description: 'Flujo de radiación térmica descendente emitido por la atmósfera y las nubes hacia la superficie.',
    method: 'Campo nativo DOWNWARD_LONG_WAVE_RADIATION_FLUX.', coverage: 'DOWNWARD LONG WAVE RADIATION FLUX'
  },
  {
    id: 'mu-ecape', category: 'instability', label: 'MU-ECAPE', short: 'MU-ECAPE', kind: 'native',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#f0b44f', vectors: false,
    description: 'CAPE con arrastre de la parcela más inestable en las capas bajas publicada por AROME.',
    method: 'Campo nativo CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY de AROME. El algoritmo exacto de arrastre no se reproduce fuera del modelo.',
    coverage: 'AROME · parcela MU con arrastre'
  },
  {
    id: 'ml-ecape', category: 'instability', label: 'ML-ECAPE', short: 'ML-ECAPE', kind: 'native',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#ef985d', vectors: false,
    description: 'CAPE con arrastre de una parcela representativa de la capa baja publicada por AROME.',
    method: 'Campo nativo MEAN_LAYER_CAPE de AROME. El algoritmo exacto de arrastre no se reproduce fuera del modelo.',
    coverage: 'AROME · parcela ML con arrastre'
  },
  {
    id: 'mucape-muli', category: 'instability', label: 'MUCAPE + MULI', short: 'MUCAPE · MULI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#ed8d61', vectors: false,
    description: 'MUCAPE convencional en colores, con el Lifted Index de la misma parcela MU representado mediante isolíneas.',
    method: 'MeteoLabX calcula MUCAPE y MULI sin arrastre sobre el mismo perfil termodinámico AROME.',
    coverage: 'Diagnóstico MeteoLabX · MUCAPE + isolíneas MULI', overlay: 'MULI'
  },
  {
    id: 'mlcape-mlli', category: 'instability', label: 'MLCAPE + MLLI', short: 'MLCAPE · MLLI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#e7816a', vectors: false,
    description: 'MLCAPE convencional en colores, con el Lifted Index de la misma parcela ML representado mediante isolíneas.',
    method: 'MeteoLabX calcula MLCAPE y MLLI sin arrastre sobre la misma parcela media del perfil AROME.',
    coverage: 'Diagnóstico MeteoLabX · MLCAPE + isolíneas MLLI', overlay: 'MLLI'
  },
  {
    id: 'sbcape-sbli', category: 'instability', label: 'SBCAPE + SBLI', short: 'SBCAPE · SBLI', kind: 'derived',
    unit: 'J/kg', min: 0, max: 3500, palette: 'convection', accent: '#e97973', vectors: false,
    description: 'CAPE de la parcela superficial en colores, con su índice Lifted representado mediante isolíneas.',
    method: 'MeteoLabX calcula SBCAPE y SBLI a partir del perfil termodinámico y las condiciones superficiales de AROME.',
    coverage: 'Diagnóstico MeteoLabX · SBCAPE + isolíneas SBLI', overlay: 'SBLI'
  },
  {
    id: 'dcape', category: 'severe', label: 'DCAPE', short: 'DCAPE', kind: 'derived',
    unit: 'J/kg', min: 0, max: 1800, palette: 'convection', accent: '#df6d7f', vectors: false,
    description: 'Energía potencial disponible para corrientes descendentes, útil para valorar el potencial de reventones convectivos.',
    method: 'Diagnóstico MeteoLabX preparado a partir del descenso pseudoadiabático de una parcela representativa de niveles medios.',
    coverage: 'Diagnóstico MeteoLabX · perfil termodinámico AROME'
  },
  {
    id: 'ordinary-cell-motion', category: 'forcing', label: 'Movimiento de células ordinarias', short: 'Movimiento celular', kind: 'derived',
    unit: 'm/s', min: 0, max: 35, palette: 'wind', accent: '#d96f91', vectors: true,
    description: 'Movimiento estimado de células convectivas ordinarias a partir del viento medio ponderado por presión dentro de la nube.',
    method: 'C⃗cel = (pLCL − pEL)⁻¹ ∫[pEL,pLCL] V⃗(p) dp. MeteoLabX usa el LCL y EL de la parcela de capa mezclada ML100; los colores muestran la velocidad y las streamlines la dirección.',
    coverage: 'Diagnóstico MeteoLabX · viento medio ML100 LCL–EL'
  },
  {
    id: 'cin', category: 'instability', label: 'Inhibición convectiva', short: 'CIN', kind: 'native',
    unit: 'J/kg', min: -400, max: 0, palette: 'convection', accent: '#d69b56', vectors: false,
    description: 'Energía que se opone al ascenso libre de una parcela y puede mantener inhibida la convección.',
    method: 'Campo nativo CONVECTIVE_INHIBITION sobre la superficie.', coverage: 'CONVECTIVE INHIBITION'
  },
  {
    id: 'reflectivity', category: 'precipitation', label: 'Reflectividad simulada MAX', short: 'Reflectividad MAX', kind: 'native',
    unit: 'dBZ', min: 0, max: 70, palette: 'reflectivity', accent: '#ef6f76', vectors: false,
    // Clases de 5 dBZ, como un radar, y sin eco por debajo de 5: en una hora
    // corriente nueve décimas partes del dominio están limpias y pintarlas de
    // azul taparía justo lo que se busca.
    scaleBreaks: [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70],
    zeroFloor: 5,
    description: 'Reflectividad máxima simulada en la columna, en dBZ. Es la lectura de radar que tendría el modelo si sus hidrometeoros fueran los reales: enseña dónde llueve, con qué intensidad y con qué estructura.',
    method: 'Campo nativo REFLECTIVITY_MAX_DBZ de AROME sobre la superficie, sin más cálculo.',
    coverage: 'AROME · REFLECTIVITY MAX DBZ · superficie'
  },
  {
    id: 'reflectivity-cappi-1500', category: 'precipitation', label: 'Reflectividad simulada CAPPI 1,5 km', short: 'CAPPI 1,5 km', kind: 'derived',
    unit: 'dBZ', min: 0, max: 70, palette: 'reflectivity', accent: '#e8606b', vectors: false,
    // La misma escala que la MAX, para que se puedan comparar de un vistazo.
    scaleBreaks: [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70],
    zeroFloor: 5,
    description: 'Reflectividad simulada a 1.500 m sobre el nivel del mar, en dBZ, como el CAPPI de un radar. A diferencia de la MAX, corta la columna a una altitud fija: enseña la precipitación cerca del suelo y no el núcleo más intenso de la tormenta.',
    method: 'MeteoLabX interpola la reflectividad de AROME entre los niveles de presión que encierran 1.500 m, con su altura sacada del geopotencial. Sin valor donde el terreno supera esa altitud.',
    coverage: 'Diagnóstico MeteoLabX · reflectividad y geopotencial en niveles de presión'
  },
  {
    id: 'lightning-density', category: 'severe', label: 'Densidad de rayos en 3 h', short: 'Rayos 3 h', kind: 'native',
    unit: 'rayos/km²', min: 0, max: 8, palette: 'lightning', accent: '#ecdb58', vectors: false,
    description: 'Densidad media de descargas eléctricas prevista por AROME durante tres horas.',
    method: 'Campo nativo AVERAGE_LIGHTNING_STRIKE_DENSITY_OVER_3HOURS.', coverage: 'LIGHTNING STRIKE DENSITY · 3 h'
  },
  {
    id: 'ship', category: 'severe', label: 'SHIP', short: 'SHIP', kind: 'derived',
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
    id: 'vertical-totals', category: 'instability', label: 'Vertical Totals', short: 'VT', kind: 'derived',
    // Grados de diferencia entre dos niveles, no una temperatura: pasarlo a °F
    // con el desplazamiento de 32 daría un número sin significado.
    unit: '°C', unitFixed: true, min: 18, max: 34, palette: 'shear', accent: '#e0a458', vectors: false,
    description: 'Diferencia de temperatura entre 850 y 500 hPa. Mide el gradiente térmico del entorno sin depender de qué parcela se elija, así que no comparte las ambigüedades de los CAPE. Valores altos con poca humedad en niveles bajos señalan el ambiente de reventones secos.',
    method: 'Temperatura de AROME en 850 hPa menos la de 500 hPa.',
    coverage: 'TEMPERATURE · 850 y 500 hPa'
  },
  {
    id: 'mslp-theta-e-850', category: 'dynamics', label: 'θₑ 850 hPa y presión al nivel del mar', short: 'θₑ 850 · MSLP', kind: 'derived',
    unit: '°C', min: -10, max: 60, palette: 'temperature', accent: '#5ac8a8', vectors: false,
    contents: 'Masas de aire · Presión',
    // Isobaras cada 4 hPa, una de cada cinco marcada, suavizadas a escala
    // sinóptica (σ de 20 celdas, unos 50 km). Sin suavizar se enroscaban: la
    // reducción al nivel del mar bajo el relieve de AROME dibuja rizos
    // alrededor de cada sierra que no son circulación, y un mapa de masas de
    // aire lee la presión a la escala de los frentes, no de los valles.
    overlayStep: 4, overlayMajorStep: 20, overlaySmoothing: 20, overlay: '',
    overlayLayerLabel: 'Isobaras',
    pressureCentres: true,
    description: 'Temperatura potencial equivalente en 850 hPa, en color, con la presión al nivel del mar en isobaras y los centros de acción marcados. Es el mapa de masas de aire: la theta-e resume en un número el calor y la humedad que trae el aire, y se conserva cuando sube o baja. Por eso permite identificar los frentes, que aparecen como franjas estrechas donde la theta-e cambia bruscamente entre dos masas de aire.',
    method: 'Theta-e de Bolton (1980) calculada con MetPy sobre la temperatura y el rocío nativos de 850 hPa, con el rocío recortado a la temperatura. Se enmascara donde la presión en superficie no llega a 850 hPa, es decir donde ese nivel queda bajo tierra.',
    coverage: 'AROME · T y Td isobáricos · presión en superficie · MSLP'
  },
  {
    id: 'srh-01', category: 'shear', label: 'Helicidad relativa 0–1 km', short: 'SRH 0–1', kind: 'derived',
    unit: 'm²/s²', min: -200, max: 500, palette: 'shear', accent: '#c084fc', vectors: true,
    description: 'Helicidad relativa a la tormenta en el primer kilómetro, referida al movimiento de la supercélula derecha de Bunkers. Mide el giro que una corriente ascendente puede heredar del entorno, y es el nivel que más se asocia con la tornadogénesis.',
    method: 'Integral del hodógrafo entre 0 y 1.000 m sobre el terreno, restando el movimiento Bunkers 2000 right mover. Sale del mismo perfil de viento que los demás diagnósticos convectivos.',
    coverage: 'Perfil de viento AROME · 0–1 km AGL'
  },
  {
    id: 'esrh', category: 'shear', label: 'Helicidad efectiva', short: 'ESRH', kind: 'derived',
    unit: 'm²/s²', min: -300, max: 600, palette: 'shear', accent: '#a855f7', vectors: true,
    description: 'Helicidad relativa a Bunkers derecho integrada en la capa efectiva de alimentación, incluso cuando es elevada.',
    method: 'Primera capa continua de parcelas con CAPE ≥ 100 J/kg y CIN ≥ −250 J/kg. Se reutilizan los perfiles, las parcelas y Bunkers; las flechas muestran el movimiento de la tormenta.',
    coverage: 'Diagnóstico MeteoLabX · capa efectiva AROME'
  },
  {
    id: 'stp', category: 'severe', label: 'STP efectivo (con CIN)', short: 'STP efectivo', kind: 'derived',
    unit: '', min: 0, max: 10, palette: 'hail', accent: '#ef476f', vectors: false,
    // Por debajo de 0,5 el STP no dice nada: el producto de los cinco factores
    // deja décimas en medio mar abierto y pintarlas cubre el dominio de azul
    // sin que haya entorno tornádico en ninguna de esas celdas.
    zeroFloor: 0.5,
    description: 'Índice de entorno favorable a tornados significativos: STP de capa efectiva con inhibición, referido a Bunkers derecho.',
    method: 'Combina MLCAPE, MLCIN, altura del LCL mezclado sobre el terreno, ESRH y EBWD. Se anula cuando la base efectiva está elevada.',
    coverage: 'Diagnóstico MeteoLabX · STP efectivo con CIN · formulación SPC'
  },
  {
    id: 'scp', category: 'severe', label: 'Supercell Composite Parameter', short: 'SCP', kind: 'derived',
    unit: '', min: 0, max: 20, palette: 'hail', accent: '#f07086', vectors: false,
    // El umbral clásico del SCP es 1; por debajo el índice sólo recoge restos
    // de CAPE y helicidad que no organizan nada, así que se deja sin pintar.
    zeroFloor: 1,
    description: 'Índice compuesto del entorno favorable a supercélulas derechas, combinando MUCAPE, helicidad efectiva y EBWD.',
    method: '(MUCAPE / 1000) × (ESRH / 50) × factor EBWD: cero por debajo de 10 m/s, EBWD / 20 entre 10 y 20 m/s y uno por encima.',
    coverage: 'Diagnóstico MeteoLabX · formulación SPC sobre perfiles AROME'
  },
  {
    id: 'srh-03', category: 'shear', label: 'Helicidad relativa 0–3 km', short: 'SRH 0–3', kind: 'derived',
    unit: 'm²/s²', min: -300, max: 600, palette: 'shear', accent: '#a855f7', vectors: true,
    description: 'Helicidad relativa a la tormenta en los tres primeros kilómetros, referida al movimiento de la supercélula derecha de Bunkers. Es la capa habitual para valorar el potencial de rotación de una supercélula.',
    method: 'Integral del hodógrafo entre 0 y 3.000 m sobre el terreno, restando el movimiento Bunkers 2000 right mover. Sale del mismo perfil de viento que los demás diagnósticos convectivos.',
    coverage: 'Perfil de viento AROME · 0–3 km AGL'
  },
  {
    id: 'vv-lfc', category: 'forcing', label: 'Velocidad vertical en el NCL', short: 'w en NCL', kind: 'derived',
    unit: 'm/s', min: -5, max: 10, palette: 'shear', accent: '#4ade80', vectors: true,
    // El viento de 10 m va en líneas de corriente: es lo que enseña dónde
    // convergen las brisas, que es lo que fuerza el ascenso que pinta el mapa.
    flowLines: true,
    description: 'Velocidad vertical del modelo interpolada al nivel de convección libre de la parcela de capa mezclada, con el viento de 10 m en líneas de corriente. Un ascenso que alcanza ese nivel dispara la convección; el que se queda por debajo se embotella bajo la inversión, y una convergencia en superficie no distingue esos dos casos. Donde las líneas de corriente se juntan o chocan hay convergencia en superficie, que es lo que suele forzar el ascenso.',
    method: 'Velocidad vertical geométrica de AROME en los niveles de presión, interpolada a la altura del NCL de la parcela de capa mezclada (ML100). Las líneas de corriente son el viento de 10 m.',
    coverage: 'Diagnóstico MeteoLabX · velocidad vertical en niveles de presión · viento 10 m'
  },
  {
    id: 'updraft-helicity', category: 'severe', label: 'Helicidad de la corriente ascendente 2–5 km', short: 'UH 2–5', kind: 'derived',
    unit: 'm²/s²', min: -50, max: 250, palette: 'shear', accent: '#f472b6', vectors: false,
    description: 'Diagnóstico de la rotación que el propio modelo genera dentro de una corriente ascendente: integra el producto de la velocidad vertical por la vorticidad vertical entre 2 y 5 km sobre el terreno. Mide cuánto coinciden el ascenso y el giro, así que separa una tormenta rotatoria de otra que sólo sube con fuerza: es el rastro que deja una supercélula en un modelo que resuelve la convección.',
    method: 'Vorticidad vertical de cada nivel isobárico con las distancias en metros, multiplicada por la velocidad vertical geométrica e integrada por trapecios entre 2.000 y 5.000 m AGL.',
    coverage: 'Diagnóstico MeteoLabX · viento, geopotencial y velocidad vertical en niveles de presión'
  },
];

// Selección inicial deliberadamente corta. El catálogo completo queda listo para
// incorporar nuevos mapas cuando se decida qué variables formarán el producto.
const initialProductIds = [
  'temperature-2m',
  'temperature-850',
  'temperature-500',
  'freezing-level',
  'precip-1h',
  'accumulated-precip',
  'precip-type',
  'snow-level',
  'reflectivity',
  'reflectivity-cappi-1500',
  'ecmwf-mslp-theta-e-850',
  'ecmwf-temperature-850',
  'ecmwf-temperature-500',
  'relative-vorticity-500',
  'q-vectors-700',
  'ecmwf-precip-6h',
  'ecmwf-precip-accumulated',
  'ecmwf-eady-850-500',
  'ecmwf-jet-300',
  'ecmwf-frontogenesis-850',
  'ecmwf-omega-700',
  'ecmwf-precipitable-water',
  'mslp-theta-e-850',
  'wind-level',
  'wind-gust',
  'mucape-muli',
  'mlcape-mlli',
  'sbcape-sbli',
  'mu-ecape',
  'ml-ecape',
  'vertical-totals',
  'shear-01',
  'shear-03',
  'shear-06',
  'ebwd',
  'srh-01',
  'srh-03',
  'esrh',
  'vv-lfc',
  'ordinary-cell-motion',
  'stp',
  'scp',
  'ship',
  'dcape',
  'updraft-helicity',
  'cloud-cover',
  'relative-humidity-700',
  'precipitable-water'
];

export const forecastProducts = initialProductIds.map((id) => {
  const product = allForecastProducts.find((item) => item.id === id);
  // Sin `model` declarado, el mapa es de AROME: es de donde vienen todos menos
  // los que se han ido añadiendo después.
  return { model: DEFAULT_FORECAST_MODEL, ...product, guide: forecastProductGuides[id] };
});

export function productsForModel(modelId = DEFAULT_FORECAST_MODEL) {
  const family = productFamily(modelId);
  return forecastProducts.filter((item) => item.model === family);
}

export function catalogSummaryFor(modelId = DEFAULT_FORECAST_MODEL) {
  const products = productsForModel(modelId);
  return {
    total: products.length,
    selectedNative: products.filter((item) => item.kind === 'native').length,
    selectedDerived: products.filter((item) => item.kind === 'derived').length
  };
}

export const forecastCatalogSummary = catalogSummaryFor(DEFAULT_FORECAST_MODEL);
