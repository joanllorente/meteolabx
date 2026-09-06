/**
 * Traduce la respuesta de `/v1/observations/current/processed` a lo que
 * pintan los componentes del prototipo.
 *
 * El prototipo se diseñó contra este mismo contrato —sus ocho tarjetas de
 * termodinámica y sus siete de radiación son literalmente los campos que
 * devuelve el pipeline—, así que aquí no hay lógica meteorológica: solo
 * formateo, traducción y descarte de lo que la estación no mide.
 *
 * Regla general: una magnitud sin dato no se inventa ni se pinta a cero. La
 * tarjeta desaparece; si no queda ninguna, desaparece la sección entera.
 */
import { cardinal, skyClarity, isNumber, num, pressureTrend, rainIntensity, stationTime, uvCategory } from '$lib/format.js';
import { dayChart, solarChart } from './series.js';
import { heatAlert, heatRisk, wetBulbRisk } from './heat-alert.js';
import { cardTooltip } from '$lib/i18n/card-tooltip.js';
import { cardinals, ui } from '$lib/i18n/ui.js';
import {
  convertRadiationEnergy,
  convertSeries,
  convertUnit,
  normalizeUnitPreferences,
  radiationEnergyLabel,
  unitLabel
} from '$lib/units.js';

/** El valor si es un número utilizable; `null` si el sensor no lo dio. */
function valid(value) {
  return isNumber(value) ? value : null;
}

/**
 * Rosa de los vientos del día a partir de la serie de rumbos.
 *
 * Las calmas se cuentan aparte y no entran en el reparto por sectores: con
 * viento flojo el rumbo que publica la veleta es ruido, y meterlo dentro
 * inventaría una dirección dominante que no existe.
 */
const CALM_KMH = 2;

/**
 * Calma: lo que la tarjeta enseña como 0 km/h.
 *
 * Por debajo de medio km/h una veleta no arranca —su umbral típico ronda el
 * kilómetro por hora—, así que el rumbo que llega ahí no es una medida del
 * momento: es la veleta clavada donde sopló por última vez. Ni la tarjeta ni
 * la gráfica del día lo dibujan.
 */
const CALM_WIND_KMH = 0.5;

function windRose(series, language) {
  const dirs = series?.wind_dirs || [];
  const speeds = series?.winds || [];
  const sectors = new Array(16).fill(0);
  let calm = 0;
  let total = 0;

  for (let index = 0; index < dirs.length; index += 1) {
    const direction = valid(dirs[index]);
    const speed = valid(speeds[index]);
    if (direction === null) continue;
    total += 1;
    if (speed !== null && speed < CALM_KMH) {
      calm += 1;
      continue;
    }
    sectors[Math.round((((direction % 360) + 360) % 360) / 22.5) % 16] += 1;
  }

  const active = total - calm;
  if (active < 6) return null;

  const names = cardinals(language);
  const data = sectors.map((count, index) => ({
    dir: names[index],
    pct: (count / active) * 100
  }));
  const dominant = data.reduce((best, item) => (item.pct > best.pct ? item : best), data[0]);
  return {
    data,
    cardinals: [names[0], names[4], names[8], names[12]],
    stats: {
      dominant: dominant.dir,
      frequency: `${num(dominant.pct, { language, decimals: 0 })} %`,
      samples: num(total, { language, decimals: 0 }),
      calm: `${num((calm / total) * 100, { language, decimals: 0 })} %`
    }
  };
}

/**
 * Sección completa, o ninguna tarjeta.
 *
 * Una sección con huecos intermedios —irradiancia sí, índice UV no, dosis
 * sí— parece rota: da la impresión de que falta algo por cargar. O están
 * todas sus tarjetas, aunque alguna diga «—» porque la red no mide eso, o no
 * está la sección. Sin ningún dato la sección entera sobra.
 */
function section(cards, { keepEmpty = false } = {}) {
  if (keepEmpty) return cards;
  return cards.some((card) => card.value !== '—') ? cards : [];
}

/**
 * Tarjeta de `MetricCard`.
 *
 * Devuelve `null` cuando la estación no mide esa magnitud: una tarjeta a cero
 * mentiría. La excepción es el panel sin estación conectada (`placeholder`),
 * donde el esqueleto completo con rayas es justamente lo que hay que enseñar.
 */
function card({
  tooltip = '', title, value, unit, icon, language, decimals = 1, chip = null, sub = [],
  family = '', preferences = null, delta = false, radiationEnergy = false,
  // Hay tarjetas cuyo detalle vale por sí mismo aunque no haya número: la
  // claridad del cielo no se mide de noche, y precisamente entonces lo que
  // interesa es en qué crepúsculo se está y a qué hora salió y se puso el Sol.
  keepDetails = false }) {
  const shownValue = radiationEnergy
    ? convertRadiationEnergy(value, preferences)
    : family
      ? convertUnit(value, family, preferences, { delta })
      : value;
  const shownUnit = radiationEnergy
    ? radiationEnergyLabel(preferences)
    : family
      ? unitLabel(family, preferences)
      : unit;
  if (!isNumber(shownValue)) {
    // Con una raya: la red no publica esa magnitud, y decirlo es más honesto
    // que hacer desaparecer la tarjeta y dejar el hueco.
    return {
      title, value: '—', unit: shownUnit, icon, family: 'thermo',
      sub: keepDetails ? sub.filter(Boolean) : [],
      ...(keepDetails && chip ? { chip } : {}),
      help: cardTooltip(tooltip || title, language)
    };
  }
  return {
    title,
    value: num(shownValue, {
      language,
      decimals:
        family === 'radiation' && preferences?.radiation !== 'wm2'
          ? 2
          : family === 'precip' && preferences?.precip === 'in' && decimals === 1
            ? 2
            : decimals
    }),
    unit: shownUnit,
    icon,
    family: 'thermo',
    ...(chip ? { chip } : {}),
    sub: sub.filter(Boolean),
    // La explicación de la variable, la misma que enseña la app actual.
    help: cardTooltip(tooltip || title, language)
  };
}

export function observationModel(payload, station, language, rawPreferences = null) {
  // Sin payload no hay estación conectada: el panel se pinta entero, con
  // todas sus tarjetas a raya, en vez de encogerse a la mitad.
  const placeholder = !payload;
  const observation = payload?.observation ?? {};
  const derivatives = payload?.derivatives ?? {};
  const extremes = payload?.daily_extremes ?? {};
  const series = payload?.series ?? null;
  const timeZone = station.tz || 'UTC';
  const available = !payload?.unavailable && isNumber(observation.epoch);
  const preferences = normalizeUnitPreferences(rawPreferences);
  const temperatureUnit = unitLabel('temperature', preferences);
  const windUnit = unitLabel('wind', preferences);
  const pressureUnit = unitLabel('pressure', preferences);
  const precipUnit = unitLabel('precip', preferences);
  const radiationUnit = unitLabel('radiation', preferences);
  const familyDecimals = (family, requested = 1) => {
    if (family === 'radiation' && preferences.radiation !== 'wm2') return 2;
    if (family === 'precip' && preferences.precip === 'in' && requested === 1) return 2;
    return requested;
  };
  const formatFamily = (value, family, decimals = 1, options = {}) =>
    num(convertUnit(value, family, preferences, options), {
      language,
      decimals: familyDecimals(family, decimals)
    });
  const valueWithUnit = (value, family, decimals = 1, suffix = '') =>
    isNumber(value)
      ? `${formatFamily(value, family, decimals)} ${unitLabel(family, preferences)}${suffix}`
      : null;

  const trend = pressureTrend(derivatives.dp3, language);
  const rate = valid(derivatives.inst_mm_h) ?? valid(observation.precip_rate);
  const dewPoint = valid(observation.Td) ?? valid(derivatives.Td_calc);

  const calm = isNumber(observation.wind) && observation.wind < CALM_WIND_KMH;

  /**
   * Ultravioleta solo cuando hay Sol sobre el horizonte.
   *
   * Bajo el horizonte no llega radiación ultravioleta solar: lo que el sensor
   * siga publicando es su deriva de cero. Porto de Marín, a las 22:00 y con el
   * Sol a 11° bajo el horizonte, daba 0,024 UVI —cero al redondear, pero 0,6
   * mW/m² al multiplicar por los 25 mW/m² que vale un punto de índice—, así
   * que la tarjeta enseñaba una irradiancia eritematosa de noche cerrada.
   *
   * La dosis del día no se toca: es un acumulado de las horas de Sol, y sigue
   * siendo cierto a medianoche.
   */
  const sunAboveHorizon =
    !isNumber(series?.solar_altitude) || series.solar_altitude > 0;
  const uvIndex = isNumber(derivatives.uv) ? (sunAboveHorizon ? derivatives.uv : 0) : null;
  const erythemalIrradiance = isNumber(derivatives.erythemal_irradiance_mw_m2)
    ? (sunAboveHorizon ? derivatives.erythemal_irradiance_mw_m2 : 0)
    : null;

  // Todo el día, de 00:00 a 24:00, rellenándose según avanza. Antes el eje
  // crecía con la serie y a media mañana la jornada parecía durar tres horas.
  const dayOptions = { language, timeZone };
  const temperatureSpark = dayChart(series, ['temps'], dayOptions);

  const thermo = section([
    card({ tooltip: 'humedad especifica', title: ui(language, 'specific_humidity'), value: derivatives.q_gkg, unit: 'g/kg', icon: 'Droplets', language }),
    card({ tooltip: 'humedad absoluta', title: ui(language, 'absolute_humidity'), value: derivatives.rho_v_gm3, unit: 'g/m³', icon: 'Droplets', language }),
    card({ tooltip: 'temperatura virtual', title: ui(language, 'virtual_temperature'), value: derivatives.Tv, unit: '°C', icon: 'Thermometer', language, family: 'temperature', preferences }),
    card({ tooltip: 'temperatura equivalente', title: ui(language, 'equivalent_temperature'), value: derivatives.Te, unit: '°C', icon: 'Thermometer', language, family: 'temperature', preferences }),
    card({ tooltip: 'temperatura potencial', title: ui(language, 'potential_temperature'), value: derivatives.theta, unit: '°C', icon: 'Thermometer', language, family: 'temperature', preferences }),
    card({ tooltip: 'densidad del aire', title: ui(language, 'air_density'), value: derivatives.rho, unit: 'kg/m³', icon: 'Box', language, decimals: 3  }),
    card({ tooltip: 'nivel de condensacion por ascenso', title: ui(language, 'lcl'), value: derivatives.lcl, unit: 'm', icon: 'CloudFog', language, decimals: 0  }),
    card({ tooltip: 'velocidad del sonido', title: ui(language, 'sound_speed'), value: derivatives.sound_speed_ms, unit: 'm/s', icon: 'AudioLines', language })
  ], { keepEmpty: placeholder });

  const sunrise = stationTime(series?.sunrise_epoch, { language, timeZone });
  const sunset = stationTime(series?.sunset_epoch, { language, timeZone });
  const energyToday = isNumber(derivatives.solar_energy_today_wh_m2)
    // Wh/m² → MJ/m², que es como se publica la integral diaria.
    ? derivatives.solar_energy_today_wh_m2 * 0.0036
    : null;

  // El estado del cielo que acompaña al porcentaje, o el tramo de crepúsculo
  // cuando el sol ya no da para medirlo.
  const skyState = skyClarity(derivatives.clarity, series?.solar_altitude, language);

  /**
   * ¿Mide esta estación algo de radiación?
   *
   * La sección entera cuelga de dos sensores: el piranómetro y el de
   * ultravioleta. Sin ninguno de los dos no hay nada que enseñar —la altura
   * del Sol se calcula con la posición y la hora, y sola no justifica una
   * sección— y la ficha se llenaba de rayas. Sin estación conectada sí se
   * pinta, porque ahí el esqueleto es lo que se está enseñando.
   */
  const hasRadiationSensors =
    placeholder || isNumber(derivatives.solar_rad) || isNumber(derivatives.uv);

  const radiation = !hasRadiationSensors ? [] : section([
    card({
      tooltip: 'radiacion solar',
      title: ui(language, 'irradiance'), value: derivatives.solar_rad, unit: 'W/m²',
      icon: 'Sun', language, decimals: 0, family: 'radiation', preferences,
      sub: [isNumber(energyToday) && {
        label: ui(language, 'energy_today'),
        value: `${num(convertRadiationEnergy(energyToday, preferences), { language })} ${radiationEnergyLabel(preferences)}`
      }]
    }),
    card({
      tooltip: 'indice uv',
      title: ui(language, 'uv_index'), value: uvIndex, unit: 'UV', icon: 'SunMedium',
      // Con un decimal, como la app actual. El índice es la irradiancia
      // eritematosa dividida por 25 mW/m², así que redondearlo a entero
      // enfrentaba un «0 UV» con los «0,6 mW/m²» de su propio subtítulo:
      // dos formas de decir lo mismo que parecían contradecirse.
      language, decimals: 1,
      chip: isNumber(uvIndex) ? { text: uvCategory(uvIndex, language), tone: uvIndex >= 6 ? 'warn' : 'note' } : null,
      // Con un decimal, como la app actual: la irradiancia eritematosa se
      // mueve en fracciones de mW/m², y redondear a enteros convertía un 0,6
      // de noche cerrada en un «1 mW/m²» junto a un índice UV de 0.
      sub: [isNumber(erythemalIrradiance) && { label: ui(language, 'erythemal_irradiance'), value: `${num(erythemalIrradiance, { language, decimals: 1 })} mW/m²` }]
    }),
    card({
      tooltip: 'dosis eritematica',
      title: ui(language, 'erythemal_dose'),
      // En SED, como la app actual: es la unidad con la que se lee una dosis
      // —una SED enrojece una piel clara—. Los julios van debajo.
      value: derivatives.erythemal_dose_today_sed,
      unit: 'SED', icon: 'Sun', language, decimals: 2,
      sub: [
        isNumber(derivatives.erythemal_dose_today_j_m2) && {
          label: ui(language, 'erythemal_dose_energy'),
          value: `${num(derivatives.erythemal_dose_today_j_m2, { language, decimals: 0 })} J/m²`
        }
      ]
    }),
    card({
      tooltip: 'evapotranspiracion',
      title: ui(language, 'evapotranspiration_today'), value: derivatives.et0, unit: 'mm',
      icon: 'Sprout', language, decimals: 2, family: 'precip', preferences,
      sub: [{ label: ui(language, 'fao_note'), value: '' }]
    }),
    card({
      tooltip: 'claridad del cielo',
      title: ui(language, 'clarity'),
      // En porcentaje, que es como se lee: 98 % de la radiación de un cielo
      // limpio, no 0,98.
      //
      // Con el Sol bajo no hay claridad que medir —es lo que dice
      // `skyState.measurable`—, así que ni número ni unidad: un «— %» daría a
      // entender que la estación ha dejado de publicar. Lo que queda es el
      // estado (los tres crepúsculos y la noche cerrada) y las horas de orto
      // y ocaso, que es justo lo que se busca a esas horas.
      value: skyState.measurable ? derivatives.clarity * 100 : null,
      unit: skyState.measurable ? '%' : '', icon: 'CloudSun', language, decimals: 0,
      chip: skyState.label ? { text: skyState.label, tone: 'note' } : null,
      sub: [sunrise && sunset && { label: `${ui(language, 'sunrise')} ${sunrise} · ${ui(language, 'sunset')} ${sunset}`, value: '' }],
      keepDetails: true
    }),
    card({
      tooltip: 'altura del sol',
      title: ui(language, 'sun_altitude'), value: series?.solar_altitude, unit: '°',
      icon: 'Sunrise', language,
      sub: [isNumber(series?.solar_altitude_max) && { label: ui(language, 'culmination'), value: `${num(series.solar_altitude_max, { language })}°` }]
    }),
    card({
      tooltip: 'balance hidrico',
      title: ui(language, 'water_balance'), value: derivatives.balance, unit: 'mm',
      icon: 'Scale', language, decimals: 2, family: 'precip', preferences,
      sub: [{ label: ui(language, 'deficit'), value: '' }]
    })
  ], { keepEmpty: placeholder });

  const names = cardinals(language);

  const rose = windRose(series, language);
  if (rose) {
    rose.stats.calmThreshold = `<${formatFamily(
      CALM_KMH,
      'wind',
      preferences.wind === 'kmh' ? 0 : 1
    )} ${windUnit}`;
  }

  return {
    available,
    // N/E/S/O para la brújula del viento; en inglés la última es W.
    roseCardinals: [names[0], names[4], names[8], names[12]],
    measuredAt: stationTime(observation.epoch, { language, timeZone }),
    timeZone,
    timestamp: isNumber(observation.epoch) && observation.epoch > 0
      ? new Date(observation.epoch * 1000).toISOString()
      : '',

    temperature: {
      value: formatFamily(observation.Tc, 'temperature'),
      unit: temperatureUnit,
      feelsLike: isNumber(observation.feels_like) ? formatFamily(observation.feels_like, 'temperature') : null,
      heatIndex: isNumber(observation.heat_index) ? formatFamily(observation.heat_index, 'temperature') : null,
      windChill: isNumber(observation.wind_chill) ? formatFamily(observation.wind_chill, 'temperature') : null,
      alert: heatAlert(derivatives, language),
      // El riesgo se dice desde los 40 °C de índice de calor; el aviso
      // largo, solo desde los 45.
      risk: heatRisk(derivatives, language),
      // Extremos del día para la esquina de la tarjeta: sin unidad, que ya
      // la lleva el valor grande justo debajo.
      extremes: {
        max: isNumber(extremes.temp_max) ? formatFamily(extremes.temp_max, 'temperature') : null,
        min: isNumber(extremes.temp_min) ? formatFamily(extremes.temp_min, 'temperature') : null
      },
      // La sparkline no tiene ejes: los huecos del futuro solo la aplanarían.
      spark: temperatureSpark
        ? convertSeries(temperatureSpark.data[0].filter(isNumber), 'temperature', preferences)
        : null
    },
    humidity: {
      value: num(observation.RH, { language, decimals: 0 }),
      vapourPressure: valueWithUnit(derivatives.e, 'pressure'),
      extremes: {
        max: isNumber(extremes.rh_max) ? num(extremes.rh_max, { language, decimals: 0 }) : null,
        min: isNumber(extremes.rh_min) ? num(extremes.rh_min, { language, decimals: 0 }) : null
      }
    },
    dewPoint: {
      value: formatFamily(dewPoint, 'temperature'),
      unit: temperatureUnit,
      wetBulb: valueWithUnit(derivatives.Tw, 'temperature'),
      // El estado del bulbo húmedo, con las mismas palabras que la aplicación
      // actual —«condiciones extremas»— en vez de una etiqueta propia.
      risk: wetBulbRisk(derivatives, language)
    },
    wind: {
      // Con un decimal: en km/h los enteros esconden la diferencia entre una
      // brisa de 3,4 y una de 4,4, que es justo donde se mueve casi siempre.
      value: formatFamily(observation.wind, 'wind'),
      unit: windUnit,
      gust: valueWithUnit(observation.gust, 'wind'),
      degrees: calm ? null : valid(observation.wind_dir_deg),
      cardinal: calm ? '' : cardinal(observation.wind_dir_deg, language),
      // Tres casos que no se pueden decir igual:
      //
      // En calma la veleta no gira, se queda clavada donde sopló por última
      // vez, y el rumbo que publica es un recuerdo, no una medida: ni se
      // escribe ni se dibuja la aguja. Sin veleta tampoco hay rumbo, pero eso
      // no es calma: es que no se mide. Y con viento, el rumbo es el rumbo.
      direction: calm
        ? ui(language, 'calm')
        : isNumber(observation.wind_dir_deg)
          ? `${cardinal(observation.wind_dir_deg, language)} · ${num(observation.wind_dir_deg, { language, decimals: 0 })}°`
          : '—',
      // Racha máxima de la jornada, no la del último parte: es el dato que se
      // busca cuando ha soplado fuerte hace un rato.
      // Del viento solo el máximo: un mínimo diario de viento es casi siempre
      // cero y no dice nada.
      extremes: {
        max: isNumber(extremes.gust_max) ? formatFamily(extremes.gust_max, 'wind') : null,
        min: null
      }
    },
    precipitation: {
      value: formatFamily(observation.precip_total, 'precip'),
      unit: precipUnit,
      rate: valueWithUnit(rate, 'precip', preferences.precip === 'in' ? 2 : 1, '/h'),
      label: rainIntensity(rate, language),
      rate5: valueWithUnit(derivatives.r5_mm_h, 'precip', preferences.precip === 'in' ? 2 : 1, '/h'),
      rate10: valueWithUnit(derivatives.r10_mm_h, 'precip', preferences.precip === 'in' ? 2 : 1, '/h')
    },
    pressure: {
      value: formatFamily(valid(derivatives.p_msl) ?? valid(observation.p_hpa), 'pressure'),
      unit: pressureUnit,
      absolute: valueWithUnit(derivatives.p_abs, 'pressure'),
      delta3h: isNumber(derivatives.dp3)
        ? `${derivatives.dp3 > 0 ? '+' : ''}${formatFamily(derivatives.dp3, 'pressure')} ${pressureUnit}`
        : null,
      trend
    },
    // De noche —o en una estación sin sensor UV— el titular de la tarjeta es
    // la irradiancia. Encabezarla con una raya no informaba de nada.
    radiationTile: isNumber(uvIndex)
      ? {
          present: true,
          title: ui(language, 'uv_index'),
          value: num(uvIndex, { language, decimals: 0 }),
          unit: uvCategory(uvIndex, language),
          footLabel: isNumber(derivatives.solar_rad) ? ui(language, 'irradiance') : null,
          footValue: isNumber(derivatives.solar_rad)
            ? `${formatFamily(derivatives.solar_rad, 'radiation', preferences.radiation === 'wm2' ? 0 : 2)} ${radiationUnit}`
            : null
        }
      : isNumber(derivatives.solar_rad)
        ? {
            present: true,
            title: ui(language, 'irradiance'),
            value: formatFamily(derivatives.solar_rad, 'radiation', preferences.radiation === 'wm2' ? 0 : 2),
            unit: radiationUnit,
            footLabel: null,
            footValue: null
          }
        : { present: false },

    thermo,
    radiation,
    charts: {
      temperature: temperatureSpark && {
        ...temperatureSpark,
        data: temperatureSpark.data.map((values) => convertSeries(values, 'temperature', preferences))
      },
      vapour: (() => {
        const built = dayChart(series, ['vapor_pressures', 'saturation_pressures'], dayOptions);
        return built && { ...built, data: built.data.map((values) => convertSeries(values, 'pressure', preferences)) };
      })(),
      precipitation: (() => {
        const built = dayChart(series, ['precips'], dayOptions);
        return built && { ...built, data: built.data.map((values) => convertSeries(values, 'precip', preferences)) };
      })(),
      wind: (() => {
        const built = dayChart(series, ['winds', 'gusts'], dayOptions);
        return built && { ...built, data: built.data.map((values) => convertSeries(values, 'wind', preferences)) };
      })(),
      windDirection: (() => {
        const built = dayChart(series, ['winds', 'gusts', 'wind_dirs'], dayOptions);
        if (!built) return null;
        // Los rumbos de las calmas se borran aquí, con la serie de viento
        // todavía en km/h: pasado el conversor, el umbral ya no valdría. Lo
        // que quedaba si no era una fila de puntos alineados en el rumbo en el
        // que la veleta se quedó parada, leídos como si fueran viento.
        const [speeds, gusts, degrees] = built.data;
        const rumbos = degrees.map((value, index) =>
          isNumber(speeds[index]) && speeds[index] < CALM_WIND_KMH ? null : value
        );
        // Un día entero en calma deja la serie sin un solo rumbo: entonces no
        // hay dirección que ofrecer, y el interruptor de la leyenda tampoco.
        if (!rumbos.some(isNumber)) return null;
        return { ...built, data: [speeds, gusts, rumbos] };
      })(),
      irradiance: (() => {
        const built = solarChart(series, dayOptions);
        return built && { ...built, data: built.data.map((values) => convertSeries(values, 'radiation', preferences)) };
      })()
    },
    units: { temperature: temperatureUnit, wind: windUnit, pressure: pressureUnit, precip: precipUnit, radiation: radiationUnit },
    rose,
    warnings: (payload?.warnings || []).map((item) => item.code)
  };
}
