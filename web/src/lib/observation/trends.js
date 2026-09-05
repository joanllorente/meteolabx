/**
 * Los gráficos de la pestaña de tendencias.
 *
 * Las cuatro de la aplicación actual, en su orden: θe, razón de mezcla,
 * componentes del viento y presión. Solo tendencias: la evolución de
 * temperatura y punto de rocío ya está en el panel de observación.
 *
 * Los textos traen también un título para una tendencia de presión de vapor,
 * pero esa no se pinta en la app actual (`vapor_pressure` no aparece ni una
 * vez en `tabs/trends.py`): es un texto huérfano, no un gráfico que falte.
 *
 * Cada uno se descarta solo si la estación no publica lo que necesita —hay
 * redes con termómetro y poco más—, igual que hace Streamlit comprobando
 * `has_humidity_series`, `has_barometer_series` y compañía. Un gráfico con
 * los ejes puestos y la caja vacía miente sobre lo que mide la estación.
 */
import app from '$lib/i18n/app-i18n.generated.js';
import { families } from '$lib/families.js';
import { t } from '$lib/seo/i18n.js';
import { symmetricRange } from './scale.js';
import { chart, dayChart } from './series.js';
import { convertSeries, convertUnit, normalizeUnitPreferences, unitLabel } from '$lib/units.js';

export function trendsModel(series, station, language, { span = 'day', preferences: rawPreferences = null } = {}) {
  const options = { language, timeZone: station.tz || 'UTC', span };
  const preferences = normalizeUnitPreferences(rawPreferences);
  const texts = app.trends?.[language] || app.trends?.es || { charts: {}, tooltips: {} };
  const charts = texts.charts || {};
  const tooltips = texts.tooltips || {};

  const definitions = [
    {
      key: 'theta_e',
      title: charts.theta_e_title,
      axis: charts.theta_e_axis,
      help: tooltips.theta_e,
      zero: true,
      // ±20 K/h de suelo, el mismo que fija la app actual.
      minimumAbs: 20,
      fields: ['theta_e_trends'],
      colors: [families.thermo.color],
      labels: ['dθe/dt'],
      intervalField: 'theta_e_interval_minutes',
      family: 'temperature',
      delta: true
    },
    {
      key: 'mixing',
      title: charts.mixing_ratio_title,
      axis: charts.mixing_ratio_axis,
      help: tooltips.mixing_ratio,
      zero: true,
      minimumAbs: 5,
      fields: ['mixing_ratio_trends'],
      colors: [families.humidity.color],
      labels: ['dr/dt'],
      intervalField: 'mixing_ratio_interval_minutes'
    },
    {
      key: 'wind',
      title: charts.uv_title,
      axis: charts.uv_axis,
      help: tooltips.uv,
      zero: true,
      fields: ['wind_u', 'wind_v'],
      colors: [families.wind.color, families.radiation.color],
      labels: [charts.uv_u, charts.uv_v],
      family: 'wind'
    },
    {
      key: 'pressure',
      title: charts.pressure_title,
      axis: charts.pressure_axis,
      zero: true,
      fields: ['pressure_trends'],
      colors: [families.pressure.color],
      labels: ['dp/dt'],
      intervalField: 'pressure_interval_minutes',
      family: 'pressure',
      delta: true
    }
  ];

  return definitions
    .map((definition) => {
      // «Hoy» se dibuja sobre el eje fijo de 24 horas, igual que las gráficas
      // de observación: si no, el gráfico crece durante el día y a las ocho de
      // la mañana parece que la jornada entera cabe en dos horas.
      const built =
        span === 'day'
          ? dayChart(series, definition.fields, options)
          : chart(series, definition.fields, options);
      if (!built) return null;
      const minutes = definition.intervalField ? series?.[definition.intervalField] : 0;
      const converted = definition.family
        ? built.data.map((values) => convertSeries(values, definition.family, preferences, { delta: definition.delta }))
        : built.data;
      const flat = converted.flat();
      return {
        key: definition.key,
        title: definition.title,
        axis: definition.family
          ? definition.axis.replace(
              /\([^)]*\)/,
              `(${unitLabel(definition.family, preferences)}${definition.delta ? '/h' : ''})`
            )
          : definition.axis,
        help: definition.help || '',
        zero: Boolean(definition.zero),
        labels: built.labels,
        epochs: built.epochs,
        nowIndex: built.nowIndex,
        minutes: minutes || 0,
        range: definition.minimumAbs
          ? symmetricRange(
              flat,
              convertUnit(definition.minimumAbs, definition.family, preferences, {
                delta: Boolean(definition.delta)
              })
            )
          : null,
        series: converted.map((data, index) => ({
          data,
          color: definition.colors[index],
          label: definition.labels[index]
        }))
      };
    })
    .filter(Boolean);
}
