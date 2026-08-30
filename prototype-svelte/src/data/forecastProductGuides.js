const ECMWF_OPEN_DATA = {
  label: 'ECMWF · Real-time open data',
  url: 'https://www.ecmwf.int/en/forecasts/datasets/open-data'
};

const MF_API = {
  label: 'Météo-France · API ciblée modèles (WCS/WMS)',
  url: 'https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/854032416/API+Cibl+e+Mod+les'
};

const MF_AROME = {
  label: 'Météo-France · ficha oficial de la API AROME',
  url: 'https://portail-api.meteofrance.fr/web/fr/api/AROME'
};

const NOAA_CAPE = {
  label: 'NOAA/NWS · parámetros convectivos e interpretación de CAPE',
  url: 'https://www.weather.gov/lmk/indices'
};

const NOAA_LI = {
  label: 'NOAA/NWS · definición e interpretación de CAPE y Lifted Index',
  url: 'https://www.weather.gov/mlb/adas_glossary'
};

const SHARPPY = {
  label: 'SHARPpy · implementación pública de params.py',
  url: 'https://github.com/sharppy/SHARPpy/blob/main/sharppy/sharptab/params.py'
};

const THOMPSON_2007 = {
  label: 'Thompson, Mead y Edwards (2007) · capa efectiva y EBWD',
  url: 'https://doi.org/10.1175/WAF969.1'
};

const RKW_1988 = {
  label: 'Rotunno, Klemp y Weisman (1988) · balance cizalladura–cold pool',
  url: 'https://doi.org/10.1175/1520-0469(1988)045%3C0463:ATFSLL%3E2.0.CO;2'
};

const BOLTON_1980 = {
  label: 'Bolton (1980) \u00b7 c\u00e1lculo de la temperatura potencial equivalente',
  url: 'https://doi.org/10.1175/1520-0493(1980)108%3C1046:TCOEPT%3E2.0.CO;2'
};

const METPY = {
  label: 'MetPy \u00b7 equivalent_potential_temperature',
  url: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.equivalent_potential_temperature.html'
};

const NAYLOR_2012 = {
  label: 'Naylor et al. (2012) \u00b7 sensibilidad de la helicidad del ascenso simulada',
  url: 'https://journals.ametsoc.org/view/journals/mwre/140/7/mwr-d-11-00209.1.xml'
};

const LINE_ORIENTATION = {
  label: 'Bluestein y Weisman (2000) · orientación de la cizalladura respecto a la línea de disparo',
  url: 'https://journals.ametsoc.org/view/journals/mwre/128/9/1520-0493_2000_128_3128_tionss_2.0.co_2.xml'
};

const CELL_MOTION = {
  label: 'NOAA/NSSL · advección y propagación del movimiento convectivo',
  url: 'https://www.nssl.noaa.gov/users/brooks/public_html/sls19/abstracts/corfidi3.html'
};

export const forecastProductGuides = {
  'z500-mslp': {
    what: 'Altura geopotencial de 500 hPa en color y presión al nivel del mar en isobaras, sobre el Atlántico y Europa. Es el par clásico del análisis sinóptico: la altura de la superficie de 500 hPa dibuja la onda que dirige el tiempo a varios días vista, y la presión en superficie dice dónde acaba apoyándose.',
    interpretation: [
      'Los valores altos son dorsal —aire cálido y una columna dilatada, tiempo estable— y los bajos, vaguada o depresión en altura. Lo que importa no es tanto el valor como la forma: dónde se curva la onda y hacia dónde avanza.',
      'Las isobaras se leen encima, no aparte. Un mínimo de geopotencial justo sobre una baja en superficie es un sistema maduro y vertical, ya sin apenas recorrido; desplazado al oeste de ella, es un sistema todavía en desarrollo.',
      'El gradiente entre isohipsas es proporcional al viento en 500 hPa: donde se aprietan está la corriente en chorro, y con ella la banda por donde viajan las borrascas.',
      'A +144 h el mapa no es un pronóstico de detalle sino de patrón. Sirve para ver si se instala una dorsal o entra una vaguada, no para decidir a qué hora llueve.'
    ],
    method: 'Dos mensajes por plazo del open data de ECMWF, leídos por rango de bytes del GRIB2 global de 0,25° con el índice que el propio ECMWF publica al lado. La altura geopotencial de 500 hPa se pasa de metros geopotenciales a decámetros y la presión al nivel del mar de pascales a hectopascales; el recorte al dominio euroatlántico se hace al leer, no después.',
    equations: [
      { label: 'Altura geopotencial en decámetros', latex: String.raw`Z_{500}[\mathrm{dam}]=\frac{Z_{500}[\mathrm{gpm}]}{10}` },
      { label: 'Presión al nivel del mar', latex: String.raw`p_{\mathrm{mar}}[\mathrm{hPa}]=\frac{p_{\mathrm{mar}}[\mathrm{Pa}]}{100}` }
    ],
    steps: [
      'Índice `.index` del plazo: una línea JSON por mensaje, con desplazamiento y longitud.',
      'Descarga parcial de los dos mensajes: gh en 500 hPa y msl en superficie.',
      'Recorte a la ventana euroatlántica y empaquetado en el mismo formato de rejilla que usan los mapas de AROME.'
    ],
    sources: [ECMWF_OPEN_DATA]
  },

  'temperature-2m': {
    what: 'Temperatura del aire prevista a 2 m sobre la superficie del modelo. Es el campo de referencia para describir el ambiente térmico próximo al suelo, pero no equivale a la temperatura de la piel del terreno.',
    interpretation: [
      'Los máximos y mínimos permiten seguir calentamiento diurno, enfriamiento nocturno, heladas y episodios de calor. Los gradientes compactos suelen delimitar brisas, frentes, inversiones o contrastes tierra–mar.',
      'La topografía, el uso del suelo y la mezcla de la capa límite condicionan mucho este campo. Valles estrechos, laderas, núcleos urbanos y piscinas frías pueden diferir de una celda de 2,5 km; debe interpretarse como temperatura representativa de la celda, no como lectura de estación.'
    ],
    method: 'AROME publica TEMPERATURE en el nivel de altura específica de 2 m. MeteoLabX selecciona la hora válida y la celda WCS, conserva la rejilla y convierte a grados Celsius cuando la cobertura llega en Kelvin.',
    equations: [
      { label: 'Conversión aplicada cuando la unidad nativa es Kelvin', latex: String.raw`T_{2\,\mathrm m}[{}^\circ\mathrm C]=T_{2\,\mathrm m}[\mathrm K]-273.15` }
    ],
    steps: [
      'Coverage nativo: TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND.',
      'Nivel seleccionado: 2 m; no se interpola entre horas ni se suaviza la rejilla.',
      'La paleta modifica solo la representación, nunca el valor consultado de la celda.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'vv-lfc': {
    what: 'Velocidad vertical del modelo en el nivel de convección libre de la parcela de capa mezclada, en m/s. Positiva hacia arriba.',
    interpretation: [
      'Responde a la pregunta que los mapas de inestabilidad no contestan: si el ascenso llega hasta donde la parcela se vuelve flotante. Alcanzado el NCL la convección se dispara sola; por debajo, la convergencia se embotella bajo la inversión y no pasa nada.',
      'Por eso complementa a los CAPE en lugar de repetirlos: aquéllos dicen cuánta energía hay disponible y éste, si algo la va a liberar. Un valor alto sobre una zona con CAPE apreciable señala dónde y cuándo.',
      'AROME resuelve parcialmente la convección, así que en celdas ya desarrolladas este campo recoge la propia corriente ascendente y no solo el forzamiento previo. Como detector de dónde el modelo está convectando sigue siendo útil, pero conviene saber que no siempre es la causa.',
      'Las flechas son el viento de 10 m: indican qué está forzando el ascenso —brisa, línea de convergencia, relieve— y hacia dónde se propagaría lo que se dispare.'
    ],
    method: 'Velocidad vertical geométrica de los niveles isobáricos del paquete IP3, interpolada linealmente a la altura del NCL. El NCL sale de la parcela de capa mezclada de los 100 hPa inferiores, la misma con la que se calcula el MLCAPE.',
    equations: [
      { label: 'Interpolación al NCL', latex: String.raw`w_{\mathrm{NCL}}=w_k+\frac{z_{\mathrm{NCL}}-z_k}{z_{k+1}-z_k}\,(w_{k+1}-w_k)` }
    ],
    steps: [
      'Velocidad vertical en niveles isobáricos del paquete IP3, que ya se descarga para el rocío del DCAPE.',
      'Altura del nivel de convección libre de la parcela ML100, sobre el terreno.',
      'Sin valor donde la parcela no llega a ganar flotabilidad: no hay nivel al que mirar.'
    ],
    sources: [MF_AROME, MF_API]
  },
  'updraft-helicity': {
    what: 'Helicidad de la corriente ascendente entre 2 y 5 km sobre el terreno, en m\u00b2/s\u00b2. Diagnostica la rotaci\u00f3n que el propio modelo genera dentro de una corriente ascendente: combina la velocidad vertical con la vorticidad vertical entre 2.000 y 5.000 metros sobre el terreno.',
    interpretation: [
      'Los valores positivos indican que el ascenso y la rotaci\u00f3n cicl\u00f3nica coinciden, que en el hemisferio norte es la se\u00f1al habitual de una superc\u00e9lula derecha. Los negativos, rotaci\u00f3n anticicl\u00f3nica acompa\u00f1ando al ascenso: puede corresponder a una superc\u00e9lula izquierda, pero el signo por s\u00ed solo no lo demuestra.',
      'Mide coincidencia, no intensidad: una corriente ascendente fuerte sin rotaci\u00f3n da poca UH, y una zona con vorticidad pero sin ascenso, tambi\u00e9n.',
      'Una UH elevada identifica un mesocicl\u00f3n simulado de niveles medios. No significa autom\u00e1ticamente tornado ni tiempo severo en superficie, as\u00ed que conviene leerla junto al MLCAPE, la helicidad relativa, la cizalladura de la capa efectiva, la velocidad vertical en el NCL y la evoluci\u00f3n entre horas.',
      'A diferencia de CAPE o cizalladura, no describe el ambiente sino lo que el modelo est\u00e1 generando: aparece donde AROME ya ha desarrollado la tormenta, no antes. Por eso complementa a los campos de entorno y no los sustituye.',
      'Como orientaci\u00f3n para modelos de 2\u20134 km, alrededor de 25\u201350 m\u00b2/s\u00b2 puede se\u00f1alar rotaci\u00f3n organizada, 75\u2013150 una se\u00f1al fuerte y por encima de 150 una rotaci\u00f3n muy intensa. Son rangos que dependen del modelo, de la resoluci\u00f3n y de la acumulaci\u00f3n temporal \u2014los estudios han usado umbrales de 50 a 150 seg\u00fan uno y otro\u2014, de modo que para AROME piden calibraci\u00f3n propia.'
    ],
    method: 'Vorticidad vertical de cada nivel isob\u00e1rico calculada con las distancias horizontales en metros \u2014la longitud se corrige por el coseno de la latitud\u2014, multiplicada por la velocidad vertical geom\u00e9trica de IP3 e integrada por trapecios entre 2.000 y 5.000 m sobre el terreno, con los extremos interpolados.',
    equations: [
      { label: 'Vorticidad vertical', latex: String.raw`\zeta=\frac{\partial v}{\partial x}-\frac{\partial u}{\partial y}` },
      { label: 'Helicidad del ascenso', latex: String.raw`\mathrm{UH}_{2-5}=\int_{2000}^{5000} w\,\zeta\;\mathrm{d}z` }
    ],
    steps: [
      'Presi\u00f3n, temperatura, humedad y viento horizontal de los niveles isob\u00e1ricos de IP1, y velocidad vertical geom\u00e9trica de IP3.',
      'Altura de cada nivel reconstruida con la ecuaci\u00f3n hipsom\u00e9trica, menos el terreno: la capa va sobre el suelo, no sobre el mar.',
      'Vorticidad vertical en el plano, con los grados pasados a metros y la distancia longitudinal corregida por el coseno de la latitud. Las filas de la rejilla bajan de norte a sur, y ese signo es el que le da el suyo a \u2202u/\u2202y.',
      'Producto de la velocidad vertical por la vorticidad en cada nivel, interpolado exactamente en 2.000 y 5.000 m.',
      'Integraci\u00f3n por trapecios de todos los tramos comprendidos en esa capa.',
      'El troceado por bandas lee una fila de halo a cada lado para que las derivadas no dejen costuras en las uniones.',
      'Sin valor donde la columna no cubre la capa entera o le falta alguno de los niveles de en medio: un valor parcial se leer\u00eda como rotaci\u00f3n d\u00e9bil cuando es falta de datos.'
    ],
    sources: [MF_AROME, MF_API, NAYLOR_2012]
  },
  reflectivity: {
    what: 'Reflectividad m\u00e1xima simulada de la columna, en dBZ: la lectura que dar\u00eda un radar si los hidrometeoros del modelo fueran los reales. De todos los niveles se queda con el valor mayor, que es lo que ve un radar al barrer.',
    interpretation: [
      'Es el mapa m\u00e1s directo para ver d\u00f3nde llueve y c\u00f3mo: no da una cantidad acumulada sino la estructura del momento, y ah\u00ed se distingue una banda estratiforme de un tren de c\u00e9lulas o de una l\u00ednea organizada.',
      'Como orientaci\u00f3n: por debajo de 20 dBZ es lluvia d\u00e9bil o llovizna; entre 20 y 35, lluvia moderada; por encima de 40 hay convecci\u00f3n y por encima de 50, n\u00facleos con posible granizo. Son los mismos \u00f3rdenes que en un radar de verdad, pero calculados, no medidos.',
      'AROME resuelve parcialmente la convecci\u00f3n, as\u00ed que la posici\u00f3n exacta de cada c\u00e9lula es orientativa: lo fiable es el tipo de estructura y la zona, no que el n\u00facleo caiga en un pueblo concreto.',
      'Sin color por debajo de 5 dBZ. En una hora corriente nueve d\u00e9cimas partes del dominio no tienen eco, y pintarlas taparía lo poco que importa.'
    ],
    method: 'Campo nativo de AROME, sin c\u00e1lculo propio: MeteoLabX solo lo recorta al dominio y lo sirve. La escala va por clases de 5 dBZ, como la de un radar, en vez de un degradado continuo.',
    equations: [],
    steps: [
      'Cobertura REFLECTIVITY_MAX_DBZ sobre la superficie, una por hora.',
      'Clases de 5 en 5 dBZ hasta 70; por debajo de 5 no se pinta.'
    ],
    sources: [MF_AROME, MF_API]
  },
  'mslp-theta-e-850': {
    what: 'Temperatura potencial equivalente en 850 hPa, en \u00b0C, con la presi\u00f3n al nivel del mar en isobaras y sus centros marcados. La theta-e resume en un solo n\u00famero el calor y la humedad que trae el aire, y se conserva tanto si la masa sube seca como si condensa: por eso identifica a la masa misma y no al term\u00f3metro de un momento.',
    interpretation: [
      'Es el mapa de masas de aire. Una lengua de theta-e alta que avanza sobre valores m\u00e1s bajos es advecci\u00f3n c\u00e1lida y h\u00fameda; el gradiente apretado entre dos zonas marca el frente mejor que la temperatura sola, porque una masa seca y otra h\u00fameda pueden estar al mismo grado y no ser la misma cosa.',
      'Se lee junto a las isobaras: el aire va casi paralelo a ellas, as\u00ed que ellas dicen de d\u00f3nde viene la masa que la theta-e describe. Una baja al oeste con isobaras del sur trae la lengua c\u00e1lida por delante.',
      'El nivel de 850 hPa se elige porque queda por encima del rozamiento y del ciclo diario de la superficie, pero a\u00fan dentro del aire que alimenta la convecci\u00f3n.',
      'Sin valor donde la presi\u00f3n en superficie no llega a 850 hPa: ah\u00ed ese nivel est\u00e1 bajo tierra y el modelo publica una extrapolaci\u00f3n que no es aire de ninguna parte. Es lo que deja en blanco los Alpes y buena parte de la meseta.'
    ],
    method: 'Theta-e de Bolton (1980) con la implementaci\u00f3n de MetPy, sobre la temperatura y el rocío nativos de 850 hPa. El roc\u00edo se recorta a la temperatura para absorber las sobresaturaciones num\u00e9ricas del modelo, la presi\u00f3n se toma constante en 850 hPa y el c\u00e1lculo va en kelvin; solo el mapa se pasa a grados. La presi\u00f3n al nivel del mar viaja en la capa superpuesta del mismo frame.',
    equations: [
      { label: 'Presi\u00f3n de vapor', latex: String.raw`e=6{,}112\exp\!\left(\frac{17{,}67\,T_d}{T_d+243{,}5}\right)` },
      { label: 'Raz\u00f3n de mezcla', latex: String.raw`r=\frac{0{,}622\,e}{p-e}` },
      { label: 'Temperatura del NCA', latex: String.raw`T_L=\left[\frac{1}{T_d-56}+\frac{\ln(T/T_d)}{800}\right]^{-1}+56` },
      { label: 'Theta-e', latex: String.raw`\theta_e=T\left(\frac{1000}{p-e}\right)^{\kappa}\left(\frac{T}{T_L}\right)^{0{,}28r}\exp\!\left[\left(\frac{3036}{T_L}-1{,}78\right)r(1+0{,}448r)\right]` }
    ],
    steps: [
      'Temperatura y roc\u00edo isob\u00e1ricos de 850 hPa, presi\u00f3n en superficie y presi\u00f3n al nivel del mar: cuatro coberturas por hora, porque ninguna lo da hecho.',
      'El roc\u00edo nunca por encima de la temperatura, y la presi\u00f3n constante en 850 hPa.',
      'Theta-e por MetPy, no por la funci\u00f3n compartida de los diagn\u00f3sticos convectivos: aquella mezcla dos variantes de Bolton y se desv\u00eda entre 0,02 y 0,19 K, poco pero sin motivo, y cambiarla movería MUCAPE, MULI y SHIP a la vez.',
      'Isobaras cada 4 hPa sobre el campo sin suavizar, con una de cada cinco marcada.',
      'Bajas y anticiclones: el campo se suaviza a 40 km solo para buscarlos, tienen que ser el extremo en 200 km a la redonda y ganarle al entorno 2,5 hPa. Dos del mismo signo a menos de 300 km son el mismo. Al ampliar se rebaja el list\u00f3n y aparecen los secundarios.'
    ],
    sources: [MF_AROME, MF_API, BOLTON_1980, METPY]
  },
  'srh-01': {
    what: 'Helicidad relativa a la tormenta entre el suelo y 1.000 m sobre el terreno, en m²/s². Mide el área que el hodógrafo barre alrededor del vector de movimiento de la tormenta: cuánto giro puede heredar una corriente ascendente del entorno en el que se forma.',
    interpretation: [
      'Es la capa que más se asocia con la tornadogénesis. Valores por encima de 100 m²/s² ya son favorables y por encima de 150 son significativos, siempre acompañados de inestabilidad y de una base nubosa baja.',
      'El signo importa: positivo indica giro ciclónico y negativo, anticiclónico. Un SRH alto sin CAPE describe un entorno cizallado pero sin tormentas; conviene leerlo junto a los mapas de CAPE y al de cizalladura 0–6 km.',
      'Las flechas son el movimiento estimado de la supercélula derecha, no el viento: indican hacia dónde se desplazaría la tormenta a la que se refiere esa helicidad.'
    ],
    method: 'Integral del hodógrafo entre 0 y 1.000 m AGL, restando el movimiento Bunkers 2000 right mover. Se recorren todos los niveles del perfil, no solo los extremos, y el límite superior se interpola. Las alturas son sobre el terreno y los niveles isobáricos subterráneos quedan excluidos. No se filtra por CAPE: es un campo cinemático del entorno.',
    equations: [
      { label: 'Helicidad relativa', latex: String.raw`\mathrm{SRH}=\sum_i\left[(u_{i+1}-C_u)(v_i-C_v)-(u_i-C_u)(v_{i+1}-C_v)\right]` },
      { label: 'Movimiento de Bunkers', latex: String.raw`\mathbf{C}_R=\overline{\mathbf{V}}_{0-6}+7{,}5\,\frac{(\Delta v,\,-\Delta u)}{|\Delta \mathbf{V}|}` }
    ],
    steps: [
      'Perfil de viento del paquete isobárico, con el viento de 10 m como base.',
      'Movimiento Bunkers: viento medio 0–6 km desviado 7,5 m/s perpendicular a la cizalladura entre las capas 0–0,5 y 5,5–6 km.',
      'Las medias van pesadas por espesor y no por presión, como especifica Bunkers et al. (2000): allí se comprueba que ponderar por presión no reduce el error. MetPy integra esas mismas capas en coordenada de presión, de modo que su movimiento sale algo distinto —en un hodógrafo de prueba, 1,1 m/s por componente— sin que ninguna de las dos formulaciones esté mal.',
      'Sin valor donde el perfil no alcanza los 6 km: la desviación de Bunkers no queda definida.'
    ],
    sources: [MF_AROME, MF_API]
  },
  'srh-03': {
    what: 'Helicidad relativa a la tormenta entre el suelo y 3.000 m sobre el terreno, en m²/s². Misma magnitud que la de 0–1 km, sobre la capa que abarca el grueso de la corriente ascendente.',
    interpretation: [
      'Es la capa habitual para valorar el potencial de rotación de una supercélula. Por encima de 150 m²/s² el entorno favorece supercélulas y por encima de 300 la rotación es marcada.',
      'Comparar 0–3 con 0–1 km dice dónde está el giro: si el de 0–1 es proporcionalmente alto, la cizalladura se concentra cerca del suelo, que es la configuración asociada a tornados.',
      'Las flechas son el movimiento estimado de la supercélula derecha, el mismo que se resta para calcular la helicidad.'
    ],
    method: 'Integral del hodógrafo entre 0 y 3.000 m AGL, restando el movimiento Bunkers 2000 right mover. Se recorren todos los niveles del perfil, no solo los extremos, y el límite superior se interpola. Las alturas son sobre el terreno y los niveles isobáricos subterráneos quedan excluidos. No se filtra por CAPE: es un campo cinemático del entorno.',
    equations: [
      { label: 'Helicidad relativa', latex: String.raw`\mathrm{SRH}=\sum_i\left[(u_{i+1}-C_u)(v_i-C_v)-(u_i-C_u)(v_{i+1}-C_v)\right]` },
      { label: 'Movimiento de Bunkers', latex: String.raw`\mathbf{C}_R=\overline{\mathbf{V}}_{0-6}+7{,}5\,\frac{(\Delta v,\,-\Delta u)}{|\Delta \mathbf{V}|}` }
    ],
    steps: [
      'Perfil de viento del paquete isobárico, con el viento de 10 m como base.',
      'Movimiento Bunkers: viento medio 0–6 km desviado 7,5 m/s perpendicular a la cizalladura entre las capas 0–0,5 y 5,5–6 km.',
      'Las medias van pesadas por espesor y no por presión, como especifica Bunkers et al. (2000). MetPy integra esas mismas capas en presión y obtiene un movimiento algo distinto; la helicidad, con el mismo movimiento de partida, coincide entre ambas hasta la última cifra.',
      'Sin valor donde el perfil no alcanza los 6 km: la desviación de Bunkers no queda definida.'
    ],
    sources: [MF_AROME, MF_API]
  },
  'vertical-totals': {
    what: 'Vertical Totals: diferencia de temperatura entre las superficies isobáricas de 850 y 500 hPa. Es la mitad térmica del índice Total Totals y mide el gradiente vertical de la troposfera baja y media.',
    interpretation: [
      'Describe el entorno, no una parcela concreta. Ahí está su valor junto a los CAPE: donde MUCAPE y MLCAPE discrepan porque la capa de origen es dudosa, el VT no tiene esa ambigüedad, porque no depende de qué parcela se elija.',
      'Valores en torno a 26 °C indican un gradiente suficiente para convección; por encima de 30 °C el gradiente es marcado. Un VT alto con poca humedad en niveles bajos apunta al ambiente de reventones secos, donde el aire descendente se enfría poco por evaporación pero acelera por el gradiente.',
      'No incorpora humedad: por sí solo no distingue una atmósfera inestable y húmeda de una inestable y seca. Conviene leerlo junto a la humedad de niveles bajos o al DCAPE.'
    ],
    method: 'Resta directa de dos niveles isobáricos del mismo campo de temperatura. Ambos viajan en el paquete GRIB IP1, que ya se descarga para los perfiles convectivos, así que no añade ninguna petición.',
    equations: [
      { label: 'Vertical Totals', latex: String.raw`\mathrm{VT}=T_{850}-T_{500}` }
    ],
    steps: [
      'Temperatura en 850 y 500 hPa del paquete isobárico IP1.',
      'Diferencia en kelvin, equivalente a la diferencia en grados Celsius.',
      'Sin valor donde p_s < 850 hPa: la superficie isobárica queda bajo tierra.'
    ],
    sources: [MF_AROME, MF_API]
  },
  'temperature-850': {
    what: 'Temperatura del aire sobre la superficie isobárica de 850 hPa. Representa la masa de aire de la baja troposfera, normalmente por encima de gran parte de la influencia térmica inmediata del suelo.',
    interpretation: [
      'Es útil para seguir advecciones cálidas o frías y comparar masas de aire. Un gradiente intenso junto al viento de 850 hPa señala transporte térmico, pero no determina por sí solo la temperatura a 2 m.',
      'Combinada con humedad, espesor y perfil vertical ayuda a evaluar cota de nieve o estabilidad. Donde la presión de superficie sea inferior a 850 hPa —relieve elevado— la superficie isobárica queda bajo tierra y el valor no tiene interpretación atmosférica física.'
    ],
    method: 'Campo TEMPERATURE nativo sobre nivel isobárico, extraído mediante WCS en 850 hPa. MeteoLabX únicamente selecciona el nivel y convierte Kelvin a Celsius cuando procede.',
    equations: [
      { label: 'Conversión de unidad', latex: String.raw`T_{850}[{}^\circ\mathrm C]=T_{850}[\mathrm K]-273.15` }
    ],
    steps: [
      'Coverage nativo: TEMPERATURE__ISOBARIC_SURFACE.',
      'Subconjunto vertical: p = 850 hPa.',
      'Debe ignorarse o enmascararse donde p_s < 850 hPa.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'temperature-500': {
    what: 'Temperatura del aire en 500 hPa, una referencia de la troposfera media situada aproximadamente entre 5 y 6 km de altitud según el estado de la columna.',
    interpretation: [
      'Bolsas frías, vaguadas y depresiones en altura aparecen como mínimos térmicos. Aire más frío en 500 hPa sobre una capa baja cálida y húmeda aumenta el gradiente térmico vertical y puede incrementar la flotabilidad.',
      'No es un mapa de tormentas: para convección hacen falta simultáneamente humedad, inestabilidad de parcela, forzamiento y un entorno de viento adecuado. También conviene analizar geopotencial y advección, no solo la temperatura.'
    ],
    method: 'Campo TEMPERATURE nativo sobre superficie isobárica, solicitado en 500 hPa. MeteoLabX conserva el dato de la celda y normaliza la unidad a grados Celsius.',
    equations: [
      { label: 'Conversión de unidad', latex: String.raw`T_{500}[{}^\circ\mathrm C]=T_{500}[\mathrm K]-273.15` }
    ],
    steps: [
      'Coverage nativo: TEMPERATURE__ISOBARIC_SURFACE.',
      'Subconjunto vertical: p = 500 hPa.',
      'No se deduce CAPE ni gradiente vertical a partir de este mapa aislado.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'wind-level': {
    what: 'Viento horizontal en el nivel seleccionado. Los colores representan velocidad y las streamlines siguen la dirección hacia la que se desplaza el aire.',
    interpretation: [
      'En niveles sobre terreno permite localizar canalizaciones, jets de baja cota, convergencias y aceleraciones orográficas. En niveles isobáricos muestra la circulación sinóptica y la posición relativa de dorsales, vaguadas y corrientes en chorro.',
      'La aproximación o separación de streamlines sugiere convergencia o divergencia horizontal, pero no la cuantifica: para ello deben calcularse derivadas espaciales. En un nivel isobárico se ocultan las celdas donde p_s < p_nivel porque el nivel estaría bajo el terreno.'
    ],
    method: 'MeteoLabX descarga las componentes U y V nativas de AROME en altura geométrica AGL o nivel isobárico, las alinea en la misma rejilla y calcula el módulo. Las streamlines se integran bilinealmente sobre U/V y se redensifican con el zoom; no alteran el campo.',
    equations: [
      { label: 'Velocidad horizontal mostrada en colores', latex: String.raw`|\vec V|=\sqrt{u^2+v^2}` },
      { label: 'Campo direccional seguido por las streamlines', latex: String.raw`\frac{d\vec x}{ds}=\frac{\vec V(\vec x)}{|\vec V(\vec x)|}` }
    ],
    steps: [
      'Coverages nativos: componentes U y V en el tipo de nivel elegido.',
      'Alineación de V sobre la cuadrícula de U; módulo calculado celda a celda.',
      'Para isobáricos: máscara p_s ≥ p_nivel.',
      'La orientación es meteorológica del flujo; no son trayectorias temporales de partículas.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'wind-gust': {
    what: 'Máxima racha de viento a 10 m prevista dentro de la hora que termina en la hora válida del mapa.',
    interpretation: [
      'Resalta máximos breves que el viento medio no muestra: pasos frontales, mezcla turbulenta, canalización por el relieve y posibles rachas convectivas.',
      'Es un máximo subhorario parametrizado por el modelo, no una velocidad sostenida ni una observación. En tormentas pequeñas puede haber errores de posición e intensidad; conviene contrastarlo con DCAPE, precipitación, reflectividad y evolución de las células.'
    ],
    method: 'Campo nativo WIND_SPEED_GUST_MAX de AROME, nivel 10 m y periodo PT1H. MeteoLabX no reconstruye la racha: selecciona directamente el máximo publicado para ese intervalo.',
    equations: [
      { label: 'Significado temporal del campo', latex: String.raw`G_{1h}(t)=\max_{\tau\in(t-1\,h,\,t]}|\vec V_{10m}(\tau)|` }
    ],
    steps: [
      'Coverage nativo: WIND_SPEED_GUST_MAX__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND_PT1H.',
      'Nivel: 10 m; periodo: la hora precedente.',
      'El visor conserva m/s; la paleta no modifica el máximo.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'shear-01': {
    what: 'La cizalladura vectorial 0–1 km representa el cambio del vector viento entre la superficie y 1 km de altura. Su magnitud indica cuánto cambia el viento y su dirección muestra hacia dónde se produce ese cambio.',
    interpretation: [
      'Valores altos de CIZ1 indican una mayor cizalladura y, por tanto, una mayor vorticidad horizontal en niveles bajos, que puede ser inclinada y transformada en rotación vertical por las corrientes ascendentes. Esto favorece la organización convectiva y la rotación de bajo nivel, especialmente en supercélulas y sistemas convectivos organizados.',
      'En supercélulas, una cizalladura fuerte y favorablemente orientada respecto al movimiento de la célula puede aumentar la cantidad de vorticidad streamwise ingerida por la corriente ascendente, incrementando la SRH 0–1 km y favoreciendo la rotación del mesociclón de bajos niveles.',
      'En líneas de tormentas, una componente de cizalladura de bajos niveles perpendicular a la línea suele favorecer más su mantenimiento, ya que puede compensar la circulación asociada a la piscina de aire frío y mantener las nuevas corrientes ascendentes cerca del frente de racha.',
      'Cuando la cizalladura es más paralela al eje de la línea, favorece en mayor medida la propagación y regeneración de células a lo largo del propio eje del sistema, en lugar de reforzar tanto la regeneración frontal perpendicular a la línea.',
      'CIZ aproximadamente perpendicular a la línea: favorece una regeneración más frontal, cerca del borde de avance, y puede ayudar a mantener una línea compacta cuando la circulación asociada a la piscina fría y la cizalladura ambiental están razonablemente equilibradas.'
    ],
    method: 'Diagnóstico íntegramente calculado por MeteoLabX a partir de las componentes U/V nativas de AROME a 10 m y 1.000 m AGL para la misma hora. Ambas rejillas se alinean espacialmente sobre la del nivel de 10 m y se calcula la diferencia vectorial entre ambos niveles.',
    equations: [
      { label: 'Vector de cizalladura 0–1 km', latex: String.raw`\Delta\vec V_{0-1}=\vec V_{1000\,m}-\vec V_{10\,m}` },
      { label: 'Magnitud representada', latex: String.raw`\mathrm{CIZ}_{0-1}=\sqrt{(u_{1000}-u_{10})^2+(v_{1000}-v_{10})^2}` },
      { label: 'Relación aproximada con vorticidad horizontal', latex: String.raw`\vec\omega_h\approx\hat{k}\times\frac{\partial\vec V}{\partial z}` }
    ],
    steps: [
      'La dirección de las flechas corresponde a la orientación del vector diferencia, no a la dirección del viento ni al desplazamiento de las tormentas.',
      'Los colores y el valor mostrado al puntero conservan la resolución completa de la rejilla; las flechas pueden agrupar varias celdas únicamente para mejorar la visualización.',
      'Valores elevados de CIZ0–1 indican un cambio intenso del viento en el primer kilómetro de la atmósfera y una mayor disponibilidad de vorticidad horizontal.'
    ],
    sources: [RKW_1988, LINE_ORIENTATION, MF_API]
  },

  'shear-03': {
    what: 'La cizalladura vectorial 0–3 km representa el cambio del vector viento entre la superficie y 3 km de altura. Su magnitud indica cuánto cambia el viento en la baja troposfera y su dirección muestra la orientación de ese cambio.',
    interpretation: [
      'Valores altos de CIZ3 indican una variación importante del viento durante los primeros 3 km de la atmósfera y, por tanto, una mayor disponibilidad de vorticidad horizontal. Favorecen una convección más organizada, con mayor capacidad para mantener estructuras multicelulares, líneas convectivas, QLCS y, cuando el perfil completo es favorable, supercélulas.',
      'En líneas de tormentas, la componente de CIZ3 perpendicular al eje de la línea es especialmente importante. Una cizalladura perpendicular adecuada puede compensar la circulación generada por la piscina fría y mantener las nuevas corrientes ascendentes cerca del frente de racha, favoreciendo la persistencia y organización de la línea. No obstante, una cizalladura cada vez mayor no implica necesariamente una línea cada vez más intensa: el mantenimiento óptimo depende del equilibrio entre la cizalladura ambiental y la intensidad de la piscina.',
      'Cuando CIZ3 es principalmente paralela a la línea, aporta menos al equilibrio frontal con el cold pool. Puede influir en la propagación, sucesión y regeneración de células a lo largo del eje del sistema y en la distribución de la precipitación, pero por sí sola no determina la dirección de movimiento de la línea.'
    ],
    method: 'Diagnóstico íntegramente calculado por MeteoLabX a partir de las componentes U/V a 10 m y 3.000 m AGL para la misma hora. Tras alinear espacialmente ambas rejillas sobre la del nivel inferior, se calcula la diferencia vectorial entre los dos niveles.',
    equations: [
      { label: 'Vector y magnitud', latex: String.raw`\Delta\vec V_{0-3}=\vec V_{3000\,m}-\vec V_{10\,m},\qquad \mathrm{CIZ}_{0-3}=|\Delta\vec V_{0-3}|` },
      { label: 'Componentes', latex: String.raw`|\Delta\vec V|=\sqrt{(\Delta u)^2+(\Delta v)^2}` }
    ],
    steps: [
      'La dirección de las flechas corresponde a la orientación del vector diferencia, no a la dirección del viento ni al desplazamiento de las tormentas.',
      'Los colores y el valor mostrado al puntero conservan la resolución completa de la rejilla; las flechas pueden agrupar varias celdas únicamente para mejorar la visualización.',
      'Valores elevados de CIZ0–3 indican un cambio intenso del viento durante los primeros 3 km y una mayor disponibilidad de vorticidad horizontal para interactuar con las corrientes ascendentes.'
    ],
    sources: [RKW_1988, LINE_ORIENTATION, MF_API]
  },

  'shear-06': {
    what: 'La cizalladura vectorial 0–6 km representa el cambio del vector viento entre la superficie y 6 km de altura. Su magnitud mide cuánto cambia el viento a través de una capa profunda de la troposfera y su dirección muestra la orientación de ese cambio. Es un indicador fundamental de la capacidad del ambiente para organizar y mantener convección profunda.',
    interpretation: [
      'Valores altos de CIZ6 favorecen la separación espacial entre la corriente ascendente, la descendente y la precipitación, reduciendo su interferencia y permitiendo tormentas más organizadas y duraderas. Una CIZ6 débil suele asociarse a células pulsantes o poco persistentes; valores moderados o fuertes favorecen multicélulas organizadas, líneas convectivas y supercélulas, siempre que exista suficiente inestabilidad.',
      'En supercélulas, la cizalladura profunda facilita la inclinación de la vorticidad horizontal y ayuda a mantener una corriente ascendente rotatoria separada de la precipitación. CIZ6 es especialmente útil para estimar la organización y persistencia de la convección profunda, pero no garantiza por sí sola que una tormenta se convierta en supercélula.',
      'Una cizalladura profunda intensa también favorece la separación de los miembros desviados a derecha e izquierda durante procesos de storm splitting. La trayectoria y la intensidad de cada miembro dependen de la hodógrafa, del viento medio y de su propagación dinámica, no de la dirección de CIZ6 de forma aislada.',
      'En líneas convectivas, la componente de CIZ6 perpendicular al eje de la línea puede ayudar a separar las corrientes ascendentes de la precipitación y favorecer la organización profunda del sistema. Una componente más paralela influye en mayor medida en la propagación y regeneración de células a lo largo del eje. Para analizar específicamente el equilibrio entre la piscina fría y la cizalladura en el frente de racha, la capa 0–3 km suele ser más representativa que CIZ6 completa.'
    ],
    method: 'Diagnóstico íntegramente calculado por MeteoLabX a partir de las componentes U/V nativas de AROME a 10 m y 6.000 m AGL para la misma hora. Tras alinear espacialmente ambas rejillas sobre la del nivel inferior, se calcula la diferencia vectorial entre los dos niveles.',
    equations: [
      { label: 'Vector de cizalladura 0–6 km', latex: String.raw`\Delta\vec V_{0-6}=\vec V_{6000\,m}-\vec V_{10\,m}` },
      { label: 'Magnitud representada', latex: String.raw`\mathrm{CIZ}_{0-6}=\sqrt{(u_{6000}-u_{10})^2+(v_{6000}-v_{10})^2}` },
      { label: 'Relación aproximada con vorticidad horizontal', latex: String.raw`\vec\omega_h\approx\hat{k}\times\frac{\partial\vec V}{\partial z}` }
    ],
    steps: [
      'La dirección de las flechas corresponde a la orientación del vector diferencia, no a la dirección del viento ni al desplazamiento de las tormentas.',
      'Los colores y el valor mostrado al puntero conservan la resolución completa de la rejilla; las flechas pueden agrupar varias celdas únicamente para mejorar la visualización.',
      'El cálculo no incorpora por sí mismo CAPE, movimiento de tormenta, SRH, curvatura de la hodógrafa, profundidad efectiva de la tormenta ni intensidad del cold pool. CIZ6 debe interpretarse junto con estos factores.'
    ],
    sources: [LINE_ORIENTATION, MF_API, NOAA_CAPE]
  },

  ebwd: {
    what: 'La EBWD (Effective Bulk Wind Difference) mide cuánto cambia el viento, como vector, dentro de la mitad inferior de la profundidad efectiva de la tormenta. El cálculo empieza en la base de la capa de entrada que realmente puede alimentar la convección y termina a mitad de camino hacia el nivel de equilibrio de la parcela más inestable. Por eso no usa siempre una capa fija desde superficie hasta 6 km. Frente a CIZ6, EBWD se adapta tanto a la altura del inflow como a la profundidad de la tormenta. La diferencia es especialmente útil en convección elevada y en tormentas más someras o más profundas de lo habitual.',
    interpretation: [
      'Los colores expresan la magnitud de la EBWD: cuanto mayor es el valor, mayor es el cambio del viento dentro de la capa efectiva. En presencia de inestabilidad y un mecanismo de disparo, una EBWD mayor suele favorecer tormentas más organizadas y persistentes porque ayuda a separar la corriente ascendente de la descendente y de la precipitación.',
      'Como referencia operativa, el entorno se vuelve progresivamente más favorable para supercélulas cuando la EBWD entra aproximadamente en el intervalo de 25 a 40 kt o lo supera.',
      'La flecha de EBWD muestra la orientación del cambio del viento entre la base y el techo efectivos; no es la dirección del viento ni el movimiento de la tormenta. Su efecto depende de la orientación relativa: perpendicular a una frontera favorece que las células se separen y permanezcan más discretas, mientras paralela aumenta sus interacciones y la evolución lineal. En una línea, la componente perpendicular ayuda más a sostener la regeneración frontal y la paralela transporta y reorganiza células a lo largo del eje.',
      'Si ninguna parcela cumple los criterios de CAPE y CIN, la capa efectiva no existe y EBWD queda sin definir.'
    ],
    method: 'MeteoLabX calcula el diagnóstico directamente a partir del perfil termodinámico y U/V de AROME, siguiendo la definición de capa efectiva de Thompson et al. (2007); no llama a SHARPpy para obtener EBWD. El resultado es sensible a la resolución vertical y a la interpolación entre niveles.',
    equations: [
      { label: 'Criterio de entrada efectiva', latex: String.raw`\mathrm{CAPE}_{parcela}\ge100\ \mathrm{J\,kg^{-1}},\qquad \mathrm{CIN}_{parcela}\ge-250\ \mathrm{J\,kg^{-1}}` },
      { label: 'Techo de la capa EBWD', latex: String.raw`z_{top}=z_{base}+\tfrac12\left(z_{EL,MU}-z_{base}\right)` },
      { label: 'Vector efectivo', latex: String.raw`\mathrm{EBWD}=\left|\vec V(z_{top})-\vec V(z_{base})\right|` }
    ],
    steps: [
      'Construir el perfil. MeteoLabX combina la superficie con los niveles isobáricos de AROME para obtener temperatura, humedad, altura y componentes U/V en una misma columna.',
      'Encontrar la base efectiva. Se prueban parcelas desde la superficie hasta 500 hPa. La primera que cumple CAPE ≥ 100 J/kg y CIN ≥ −250 J/kg fija la base de la capa.',
      'Situar el techo efectivo. Se toma el punto medio, en altura, entre esa base y el nivel de equilibrio de la parcela más inestable.',
      'Calcular la diferencia vectorial. U y V se interpolan linealmente en la base y en el techo; después se resta el vector de la base al del techo y se calcula su módulo.'
    ],
    sources: [THOMPSON_2007, SHARPPY]
  },

  'precip-1h': {
    what: 'Es la precipitación total acumulada durante la hora inmediatamente anterior a la hora válida del mapa. Incluye lluvia y todas las demás fases de precipitación expresadas como equivalente de agua líquida.',
    interpretation: [
      'El mapa muestra la cantidad acumulada en una hora, no la severidad de la tormenta ni su intensidad instantánea. El total depende tanto de cuánto precipita el sistema como del tiempo que permanece sobre cada punto.',
      'Por ello, una célula relativamente débil pero lenta o estacionaria puede dejar acumulaciones horarias altas. En cambio, una tormenta muy intensa o severa que se desplaza rápidamente puede producir acumulaciones pequeñas en cada punto, aunque genere granizo, viento fuerte o actividad eléctrica intensa.',
      'La precipitación convectiva tiene un error importante de fase y posición. Una celda prevista pocos kilómetros fuera de su lugar puede producir un error local grande aunque el patrón meteorológico general sea correcto. El mapa no debe interpretarse como una medición puntual exacta.',
      'Este campo no diagnostica severidad convectiva: no informa directamente de granizo, rachas, rayos, rotación ni organización. Tampoco distingue la fase que alcanzó el suelo, porque todas se convierten a equivalente líquido.'
    ],
    method: 'Es un campo WCS nativo de AROME: TOTAL_PRECIPITATION con sufijo temporal PT1H. MeteoLabX selecciona la hora solicitada, limita a cero cualquier valor negativo y aplica la equivalencia de agua 1 kg/m² = 1 mm.',
    equations: [
      { label: 'Acumulación del intervalo', latex: String.raw`P_{1h}(t)=\int_{t-1h}^{t} R(\tau)\,d\tau` },
      { label: 'Equivalencia de agua', latex: String.raw`1\ \mathrm{kg\,m^{-2}}=1\ \mathrm{mm}` }
    ],
    steps: [
      'Seleccionar el campo nativo. La cobertura es TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE_PT1H.',
      'Asignar el intervalo. El mapa válido a la hora t representa exclusivamente el intervalo (t−1 h, t].',
      'Mostrar la acumulación. Los valores se expresan en milímetros y no se suman con las horas vecinas en este producto.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'accumulated-precip': {
    what: 'Suma de la precipitación horaria desde el inicio del RUN hasta la hora válida seleccionada, celda por celda y en equivalente de agua.',
    interpretation: [
      'Muestra la huella total del episodio prevista por un mismo RUN y ayuda a localizar máximos persistentes u orográficos. Al avanzar la línea temporal nunca debería disminuir en una celda.',
      'Acumula también los errores de intensidad y posición de cada hora. Comparar acumulados de RUN distintos requiere indicar claramente el intervalo, porque no comparten necesariamente la misma ventana temporal.'
    ],
    method: 'Diagnóstico MLX: suma de todos los campos TOTAL_PRECIPITATION PT1H disponibles entre H+01 y H+n. H+00 se fija a cero porque no pertenece al periodo posterior al inicio del RUN.',
    equations: [
      { label: 'Suma discreta sobre la rejilla', latex: String.raw`P_{acum}(H+n,\,i,j)=\sum_{k=1}^{n}\max\!\left[P_{1h}(H+k,\,i,j),0\right]` }
    ],
    steps: [
      'Descarga y alineación de cada incremento PT1H del mismo RUN.',
      'Recorte de pequeños valores negativos de codificación a cero.',
      'Suma Float por celda; no se interpola espacialmente ni entre horas.',
      'Incluye lluvia y nieve como equivalente líquido del campo TOTAL_PRECIPITATION.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'relative-humidity-700': {
    what: 'Humedad relativa del aire sobre la superficie isobárica de 700 hPa, expresada respecto a la saturación a la temperatura del propio nivel.',
    interpretation: [
      'Bandas húmedas ayudan a localizar nubes medias, ascenso y alimentación de sistemas; intrusiones secas pueden favorecer evaporación, corrientes descendentes o erosión nubosa cuando coinciden con precipitación.',
      'La humedad relativa depende fuertemente de la temperatura: un descenso no implica necesariamente pérdida de vapor. No sustituye al agua precipitable ni describe toda la columna. Sobre relieve con p_s < 700 hPa el nivel estaría bajo tierra.'
    ],
    method: 'Campo RELATIVE_HUMIDITY nativo de AROME en 700 hPa. MeteoLabX lo convierte a porcentaje cuando la cobertura usa fracción 0–1 y limita el resultado a 0–100 %.',
    equations: [
      { label: 'Definición física de referencia', latex: String.raw`RH=100\,\frac{e}{e_s(T)}\ \%` },
      { label: 'Normalización aplicada si AROME entrega fracción', latex: String.raw`RH_{\%}=100\,RH_{0-1}` }
    ],
    steps: [
      'Coverage nativo: RELATIVE_HUMIDITY__ISOBARIC_SURFACE.',
      'Nivel: 700 hPa.',
      'MeteoLabX no recalcula e ni e_s: usa el RH publicado.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'shortwave-down': {
    what: 'Flujo medio horario de radiación solar de onda corta que llega hacia abajo a la superficie, suma de componente directa y difusa.',
    interpretation: [
      'Los máximos siguen insolación, altura solar y cielos despejados; descensos locales suelen señalar nubosidad, niebla, aerosoles o sombra orográfica representada por el modelo. Es útil para energía solar y balance superficial.',
      'El valor mostrado es medio de una hora, no irradiancia instantánea. Cerca de amanecer y ocaso el promedio puede diferir mucho del máximo dentro del intervalo; por la noche debe aproximarse a cero.'
    ],
    method: 'AROME publica DOWNWARD_SHORT_WAVE_RADIATION_FLUX con periodo PT1H. Aunque el metadato pueda anunciar W/m², la cobertura descargada contiene energía integrada en J/m²; MeteoLabX divide por 3.600 s para obtener el flujo medio horario.',
    equations: [
      { label: 'Conversión de energía acumulada a flujo medio', latex: String.raw`\overline{F}_{SW\downarrow}=\frac{E_{SW\downarrow,\,PT1H}}{3600\ \mathrm s}` },
      { label: 'Descomposición física', latex: String.raw`F_{SW\downarrow}=F_{directa}+F_{difusa}` }
    ],
    steps: [
      'Coverage nativo: DOWNWARD_SHORT_WAVE_RADIATION_FLUX_PT1H.',
      'Valores negativos se limitan a cero.',
      'Única transformación MLX: multiplicación por 1/3600.'
    ],
    sources: [MF_AROME, MF_API]
  },

  'mu-ecape': {
    what: 'MU-ECAPE es la CAPE nativa de AROME asociada a la parcela más inestable de las capas bajas. El producto del modelo incluye sus propios efectos de dilución o arrastre, es decir, la mezcla de aire ambiental con la parcela mientras asciende. MeteoLabX usa el nombre MU-ECAPE para distinguir este campo de su MUCAPE convencional, calculada sin arrastre.',
    interpretation: [
      'El mapa busca la parte del entorno con mayor flotabilidad potencial. Por eso puede mostrar inestabilidad elevada aunque la superficie sea estable. Un valor alto indica que, incluso después de la dilución representada por AROME, queda una cantidad importante de energía disponible para acelerar una corriente ascendente.',
      'La comparación más útil es con MUCAPE MLX y con ML-ECAPE. Si MU-ECAPE supera claramente a ML-ECAPE, la capa más inestable puede estar elevada o ser poco representativa del promedio de la capa baja. Si MU-ECAPE es mucho menor que MUCAPE MLX, el producto de AROME está representando una reducción importante de la flotabilidad por dilución.'
    ],
    method: 'MeteoLabX descarga el campo nativo CONVECTIVE_AVAILABLE_POTENTIAL_ENERGY publicado por AROME, conserva su valor en J/kg y lo representa. No reconstruye la parcela, no recalcula la energía y no llama a SHARPpy.',
    equations: [
      { label: 'Forma física general de una CAPE con parcela diluida', latex: String.raw`\mathrm{ECAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p}^{(entr)}-T_{v,e}}{T_{v,e}}\,dz` }
    ],
    steps: [
      'Origen del dato. La selección de la parcela MU, su trayectoria y el arrastre pertenecen al producto nativo de AROME.',
      'Tratamiento MeteoLabX. El valor se descarga y se muestra sin correcciones ni combinación con el Lifted Index de MLX, que se calcula sin arrastre.',
      'La API pública no documenta la formulación exacta de la temperatura virtual de la parcela diluida, la tasa de arrastre, el cierre ni el procedimiento preciso de selección de la parcela. Por ello, MU-ECAPE no debe compararse uno a uno con MUCAPE MLX como si solo cambiara una constante conocida.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'ml-ecape': {
    what: 'ML-ECAPE es la CAPE nativa de AROME para una parcela representativa de una capa baja mezclada, con la dilución o arrastre incluidos por el propio producto del modelo. MeteoLabX la etiqueta así para diferenciarla de MLCAPE MLX, que usa una parcela de capa mezclada convencional sin arrastre.',
    interpretation: [
      'Al representar un promedio de la capa baja, ML-ECAPE suele ser menos sensible que una parcela superficial a máximos muy locales de temperatura o humedad. Representa mejor la inestabilidad media disponible para las tormentas que se alimentan del aire de la capa límite.',
      'Debe leerse junto con MU-ECAPE. Valores parecidos sugieren que la capa baja mezclada representa bien la parcela más favorable; una MU-ECAPE claramente mayor puede señalar una capa elevada más inestable o una franja especialmente cálida y húmeda que el promedio ML suaviza. La comparación con MLCAPE MLX ayuda a apreciar la reducción asociada al producto con arrastre.'
    ],
    method: 'MeteoLabX descarga el campo nativo MEAN_LAYER_CAPE publicado por AROME y lo muestra en J/kg sin reconstruir la parcela ni modificar la energía.',
    equations: [
      { label: 'Forma física general', latex: String.raw`\mathrm{ML\!\!-\!ECAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p,ML}^{(entr)}-T_{v,e}}{T_{v,e}}\,dz` }
    ],
    steps: [
      'Origen del dato. La selección de la capa ML, la profundidad de mezcla y el arrastre son internos del campo AROME.',
      'Tratamiento MeteoLabX. El campo se descarga y se representa sin correcciones posteriores.',
      'La API pública no documenta la profundidad exacta de mezcla ni el esquema de arrastre, por lo que no debe suponerse que ML-ECAPE usa exactamente los 100 hPa inferiores empleados por la MLCAPE convencional de MeteoLabX.',
      'El campo expresa energía potencial; para valorar si esa energía puede realizarse y qué tipo de tormenta podría producir, debe combinarse con CIN, forzamiento, humedad, cizalladura y estructura vertical.'
    ],
    sources: [MF_AROME, MF_API, NOAA_CAPE]
  },

  'mucape-muli': {
    what: 'MUCAPE es la CAPE convencional, sin arrastre, de la parcela más inestable de los 300 hPa inferiores del perfil. MeteoLabX la representa mediante colores y dibuja en isolíneas el MULI, el Lifted Index calculado para exactamente la misma parcela MU.',
    interpretation: [
      'MUCAPE identifica la mayor flotabilidad potencial, aunque la parcela de origen esté elevada. MULI describe la flotabilidad de esa parcela a 500 hPa: un valor negativo significa que la parcela llega más cálida que el ambiente. MUCAPE alta y MULI muy negativo refuerzan la señal de inestabilidad, pero no garantizan que exista disparo ni que la parcela pueda superar la inhibición.',
      'Como el ascenso no incluye arrastre, carga de agua ni mezcla lateral, MUCAPE funciona como un límite superior idealizado de la energía de la corriente ascendente. Conviene compararla con MU-ECAPE para apreciar la reducción representada por AROME y con CIN para valorar si la parcela puede alcanzar el nivel de libre convección (LFC).'
    ],
    method: 'MeteoLabX aplica su algoritmo vectorizado de parcela. Selecciona el máximo de temperatura potencial equivalente de Bolton entre la superficie y 300 hPa por encima, eleva la parcela en seco hasta el LCL y pseudoadiabáticamente después. La flotabilidad usa temperatura virtual y la energía se integra por trapecios. No llama a params.cape de SHARPpy.',
    equations: [
      { label: 'Selección MU', latex: String.raw`p_{MU}=\operatorname*{arg\,max}_{p_s-300\le p\le p_s}\theta_e(p)` },
      { label: 'Energía positiva', latex: String.raw`\mathrm{MUCAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p}-T_{v,e}}{T_{v,e}}\,dz` },
      { label: 'Lifted Index de la misma parcela', latex: String.raw`\mathrm{MULI}=T_e(500\,hPa)-T_{p,MU}(500\,hPa)` }
    ],
    steps: [
      'Seleccionar la parcela MU. Se busca el máximo θe dentro de los 300 hPa inferiores.',
      'Elevar la parcela. El ascenso es seco hasta el LCL y pseudoadiabático por encima, con el condensado eliminado y sin entrainment.',
      'Integrar la CAPE. Se suman en altura las capas con flotabilidad virtual positiva; las alturas proceden de integración hipsométrica.',
      'Calcular MULI. Se resta la temperatura de la parcela MU a la ambiental en 500 hPa.'
    ],
    sources: [NOAA_CAPE, NOAA_LI, SHARPPY]
  },

  'mlcape-mlli': {
    what: 'MLCAPE es la CAPE sin arrastre de una parcela representativa de los 100 hPa inferiores. MeteoLabX mezcla esa capa mediante la temperatura potencial y la razón de mezcla medias. Los colores muestran MLCAPE y las isolíneas muestran el MLLI de la misma parcela.',
    interpretation: [
      'MLCAPE suaviza picos muy locales de temperatura o humedad y suele representar mejor una capa límite bien mezclada. Es apropiada para convección que ingiere un espesor de aire, no solo las condiciones del primer nivel cercano a 2 m.',
      'MLLI negativo refuerza la señal de inestabilidad a 500 hPa. Una SBCAPE mucho mayor que MLCAPE puede revelar una capa superficial cálida o húmeda extremadamente fina; una MUCAPE claramente mayor que MLCAPE puede indicar que la capa más inestable está elevada.'
    ],
    method: 'MeteoLabX promedia θ y la razón de mezcla r en los 100 hPa inferiores, reconstruye temperatura y punto de rocío a la presión de superficie y eleva la parcela con el mismo esquema pseudoadiabático, virtual y sin arrastre utilizado para MU.',
    equations: [
      { label: 'Propiedades de la parcela ML100', latex: String.raw`\bar\theta=\frac{1}{\Delta p}\int_{p_s-100}^{p_s}\theta\,dp,\qquad \bar r=\frac{1}{\Delta p}\int_{p_s-100}^{p_s}r\,dp` },
      { label: 'Energía y LI', latex: String.raw`\mathrm{MLCAPE}=\int_{LFC}^{EL}B\,dz,\qquad \mathrm{MLLI}=T_e(500)-T_{p,ML}(500)` }
    ],
    steps: [
      'Definir la capa ML100. Se toman los 100 hPa situados inmediatamente sobre la presión de superficie.',
      'Mezclar sus propiedades. Se promedian θ y r y se reconstruyen T y Td de la parcela a p_s.',
      'Elevar e integrar. La parcela asciende seca al LCL y pseudoadiabáticamente al EL; la CAPE usa flotabilidad virtual.',
      'Calcular MLLI. Las isolíneas usan la temperatura a 500 hPa de exactamente la misma parcela ML.'
    ],
    sources: [NOAA_CAPE, NOAA_LI, SHARPPY]
  },

  'sbcape-sbli': {
    what: 'SBCAPE es la CAPE sin arrastre de una parcela que parte de las condiciones de superficie del perfil, construidas con temperatura y punto de rocío cercanos a 2 m. Los colores muestran SBCAPE y las isolíneas el SBLI de esa misma parcela.',
    interpretation: [
      'Es especialmente sensible al ciclo diurno, las brisas, los frentes de racha y las piscinas frías. Resulta útil para convección claramente enraizada en superficie, aunque puede exagerar una capa cálida o húmeda demasiado delgada.',
      'SBCAPE elevada con SBLI negativo indica flotabilidad potencial de una parcela superficial, pero no asegura que venza la inhibición. Si SBCAPE disminuye mientras MUCAPE permanece alta, la inestabilidad puede haberse elevado por encima de una capa superficial estable.'
    ],
    method: 'MeteoLabX inserta T y Td de superficie como primer nivel y eleva esa parcela con el mismo esquema vectorizado pseudoadiabático, de temperatura virtual y sin arrastre usado en las demás CAPE MLX.',
    equations: [
      { label: 'Energía superficial', latex: String.raw`\mathrm{SBCAPE}=\int_{LFC}^{EL} g\,\frac{T_{v,p,SFC}-T_{v,e}}{T_{v,e}}\,dz` },
      { label: 'Índice superficial', latex: String.raw`\mathrm{SBLI}=T_e(500\,hPa)-T_{p,SFC}(500\,hPa)` }
    ],
    steps: [
      'Fijar el origen. Se usan la presión de superficie y T/Td del primer nivel.',
      'Elevar la parcela. El ascenso es seco hasta el LCL y pseudoadiabático por encima.',
      'Calcular energía e índice. La CAPE se integra en altura y el LI se evalúa en 500 hPa.',
      'Representar el resultado. SBCAPE se muestra en colores y SBLI en isolíneas.'
    ],
    sources: [NOAA_CAPE, NOAA_LI]
  },

  dcape: {
    what: 'DCAPE mide la energía que podría acelerar hacia abajo una parcela de aire. Puede entenderse como el equivalente descendente de la CAPE: mientras la CAPE suma la flotabilidad positiva que impulsa una corriente ascendente, la DCAPE suma la flotabilidad negativa que puede impulsar una corriente descendente. Cuando lluvia, granizo o nieve caen por aire no saturado, parte del agua se evapora o sublima. Estos cambios de fase consumen calor y enfrían el aire que rodea a los hidrometeoros. El aire enfriado se vuelve más denso que el ambiente y esa diferencia de densidad produce una fuerza descendente. La parcela se calienta por compresión al bajar, pero en una capa baja con fuerte gradiente térmico el ambiente puede calentarse hacia el suelo aún más deprisa. Cuando el núcleo descendente llega al suelo, se expande horizontalmente, forma el frente de racha y puede producir un reventón.',
    interpretation: [
      'Una DCAPE alta suele aparecer cuando existe aire relativamente seco en niveles bajos o medios y un descenso marcado de la temperatura con la altura. La combinación permite mucho enfriamiento por evaporación y mantiene fría la parcela durante el descenso, por lo que señala entornos favorables para reventones.',
      'Como escala idealizada, si toda la energía se transformara en velocidad vertical, la corriente descendente podría aproximarse mediante w ≈ √(2 · DCAPE).',
      'Esta relación no calcula la racha en superficie. Parte de la energía se pierde por mezcla, rozamiento y evaporación incompleta, y el viento observado depende también de cuánto momento transporta la tormenta desde niveles altos y de cómo se organiza el flujo al alcanzar el suelo.',
      'Una DCAPE baja tampoco descarta viento dañino.'
    ],
    method: 'MeteoLabX reproduce el procedimiento SPC/SHARPpy y busca una parcela especialmente favorable al enfriamiento dentro de los 400 hPa inferiores del perfil. La implementación usa temperatura ordinaria, como params.dcape de SHARPpy, y una integración trapezoidal. Si SHARPpy no está disponible, MeteoLabX obtiene la temperatura de la parcela mediante la inversión vectorizada de θe saturada.',
    equations: [
      { label: 'Selección de la capa de origen', latex: String.raw`p_0=p_{base,\,\min\overline{\theta_e}}-50\,hPa` },
      { label: 'Integración de la flotabilidad negativa', latex: String.raw`\mathrm{DCAPE}=-R_d\int_{p_s}^{p_0}\left(T_e-T_p\right)d\ln p` },
      { label: 'Escala idealizada de velocidad vertical', latex: String.raw`w_{ideal}\approx\sqrt{2\,\mathrm{DCAPE}}` }
    ],
    steps: [
      'Buscar el aire de origen. Se prueban capas móviles de 100 hPa y se elige la que tiene menor temperatura potencial equivalente media.',
      'Situar la parcela. La parcela parte del centro de esa capa de 100 hPa.',
      'Representar el enfriamiento inicial. Su temperatura se lleva al bulbo húmedo, como aproximación al enfriamiento producido al evaporarse precipitación hasta alcanzar la saturación.',
      'Hacerla descender. La parcela baja pseudoadiabáticamente hasta la superficie y se compara en cada nivel con la temperatura ambiental.',
      'Sumar la flotabilidad negativa. La diferencia térmica se integra durante todo el descenso, con temperatura ordinaria y sin corrección de temperatura virtual; una parcela más fría que el ambiente y una capa más profunda producen una DCAPE mayor.'
    ],
    sources: [SHARPPY]
  },

  'ordinary-cell-motion': {
    what: 'Es una estimación de la componente advectiva del movimiento de una célula convectiva ordinaria. Se calcula con el viento medio ponderado por presión dentro de la nube de una parcela ML100, entre su LCL y su EL.',
    interpretation: [
      'Los colores muestran la velocidad estimada y las líneas de corriente la dirección de traslación por el flujo medio. Sirve para anticipar hacia dónde se desplazaría una célula no supercelular y cuánto tiempo podría permanecer sobre una zona.',
      'No incluye propagación por nuevos desarrollos, cold pools, splitting de supercélulas, interacción con fronteras ni anclaje orográfico.'
    ],
    method: 'MeteoLabX calcula primero el LCL y el EL de la parcela ML100. Después integra U y V por trapecios sobre presión, recorta cada capa isobárica al intervalo nuboso y divide por la profundidad de presión.',
    equations: [
      { label: 'Vector de movimiento ordinario', latex: String.raw`\vec C_{cel}=\frac{1}{p_{LCL}-p_{EL}}\int_{p_{EL}}^{p_{LCL}}\vec V(p)\,dp` },
      { label: 'Velocidad mostrada en colores', latex: String.raw`C_{cel}=\sqrt{C_u^2+C_v^2}` }
    ],
    steps: [
      'Definir la nube. LCL y EL proceden de la parcela ML100 calculada por MeteoLabX.',
      'Construir el perfil de viento. U/V proceden de la superficie y los niveles isobáricos de AROME.',
      'Promediar por presión. Se calcula el solape exacto de cada capa con el intervalo LCL–EL y se integra linealmente por trapecios.',
      'Representar el vector. La magnitud aparece en colores y la dirección mediante streamlines.',
      'No es Bunkers ni Corfidi y no representa supercélulas o sistemas. Describe únicamente la advección por el viento medio dentro de la nube.'
    ],
    sources: [CELL_MOTION, NOAA_CAPE]
  },

  ship: {
    what: 'SHIP resume hasta qué punto el ambiente podría permitir que una tormenta produzca granizo muy grande. No busca detectar cualquier granizada: se diseñó para reconocer entornos asociados a granizo significativo, aproximadamente de 5 cm o más en su contexto original de Estados Unidos. El índice combina varios requisitos que deben coincidir: una corriente ascendente capaz de sostener las piedras, humedad que aporte agua superenfriada, una zona fría donde el granizo pueda crecer y suficiente cizalladura para que la tormenta permanezca organizada. Si uno de estos ingredientes es claramente desfavorable, el resultado disminuye.',
    interpretation: [
      'MUCAPE representa la energía disponible para acelerar la corriente ascendente. Una corriente intensa puede mantener el granizo suspendido durante más tiempo y permitir que continúe creciendo.',
      'La humedad de la parcela MU favorece que la tormenta produzca abundante agua líquida superenfriada. El gradiente 700–500 hPa y la temperatura a 500 hPa describen una capa media fría y con fuerte descenso térmico. La cizalladura 0–6 km ayuda a separar la corriente ascendente de la precipitación, y la altura de 0 °C sitúa las zonas de congelación y fusión.',
      'Un valor alto significa que varios ingredientes favorables coinciden en el mismo lugar y momento. Si se desarrolla una tormenta que aprovecha esa parcela y logra organizarse, el ambiente permite un crecimiento eficiente del granizo.',
      'SHIP no representa el tamaño previsto de las piedras, la cantidad de granizo ni la probabilidad de que granice en un punto. Un valor bajo tampoco descarta granizo severo.',
      'Los umbrales proceden del contexto operativo del SPC de Estados Unidos y no están calibrados para Europa. Conviene utilizarlos como orientación relativa y no como categorías universales de riesgo.'
    ],
    method: 'MeteoLabX obtiene SHIP mediante la función sharppy.sharptab.params.ship de SHARPpy. Para cada punto del mapa prepara los ingredientes de la parcela más inestable y del perfil ambiental, y aplica la formulación operativa.',
    equations: [
      { label: 'Núcleo de la formulación SHARPpy/SPC', latex: String.raw`\mathrm{SHIP}_0=-\frac{\mathrm{MUCAPE}\;r_{MU}\;\Gamma_{700-500}\;T_{500}\;\mathrm{BWD}_{0-6}}{42\,000\,000}` },
      { label: 'Factores reductores', latex: String.raw`\mathrm{SHIP}=\max(0,\mathrm{SHIP}_0)\,f_C\,f_\Gamma\,f_F` },
      { label: 'Definición de los reductores', latex: String.raw`f_C=\min\!\left(1,\frac{\mathrm{MUCAPE}}{1300}\right),\quad f_\Gamma=\min\!\left(1,\frac{\Gamma_{700-500}}{5.8}\right),\quad f_F=\min\!\left(1,\frac{z_{0^\circ C}}{2400}\right)` }
    ],
    steps: [
      'Preparar la parcela MU. Se toman la MUCAPE MLX y la razón de mezcla de la misma parcela más inestable, para que energía y humedad describan el mismo aire de origen.',
      'Construir los campos verticales. A partir del perfil AROME se calculan el gradiente térmico 700–500 hPa, la temperatura a 500 hPa, la BWD geométrica entre superficie y 6 km y la altura AGL del nivel de 0 °C.',
      'Evaluar SHIP con SHARPpy. MeteoLabX entrega estos ingredientes a sharppy.sharptab.params.ship, que los combina celda a celda mediante la formulación SPC.',
      'Aplicar los límites operativos. La función restringe la humedad MU al intervalo 11–13,6 g/kg y la BWD 0–6 km a 7–27 m/s, y aplica a T500 el límite de −5,5 °C.',
      'Reducir y cerrar el resultado. Una MUCAPE inferior a 1300 J/kg, un gradiente 700–500 hPa menor de 5,8 °C/km o un nivel de 0 °C por debajo de 2400 m AGL reducen progresivamente SHIP. El valor final es adimensional y se trunca a cero. SHIP no usa EBWD en esta formulación.'
    ],
    sources: [SHARPPY]
  },

  'cloud-cover': {
    what: 'Fracción total de la celda cubierta por nubes en cualquier nivel de la columna atmosférica, expresada como porcentaje.',
    interpretation: [
      'Valores altos indican cielo ampliamente cubierto y suelen reducir radiación solar; gradientes marcan bordes de sistemas nubosos. Un 100 % no informa de espesor, base, techo, fase ni precipitación.',
      'La nubosidad total puede estar dominada por una capa alta delgada o por estratos bajos densos, situaciones meteorológicamente distintas. Debe combinarse con niveles de nubosidad, humedad, precipitación y radiación.'
    ],
    method: 'Campo nativo TOTAL_CLOUD_COVER de AROME. MeteoLabX usa el valor publicado; si la cobertura llega como fracción 0–1, la multiplica por 100 y limita el resultado al intervalo físico 0–100 %.',
    equations: [
      { label: 'Normalización de unidad cuando procede', latex: String.raw`C_{total}[\%]=100\,C_{total}[0,1]` }
    ],
    steps: [
      'Coverage nativo: TOTAL_CLOUD_COVER.',
      'No se reconstruye sumando nubes bajas, medias y altas.',
      'La regla interna de solapamiento de capas pertenece al modelo y no se deduce de la API.'
    ],
    sources: [MF_AROME, MF_API]
  }
};
