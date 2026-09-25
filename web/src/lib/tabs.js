/**
 * Las pestañas de la aplicación.
 *
 * Todas son ya rutas de este frontend: ninguna vuelve a Streamlit. La única
 * que se sale del patrón es Predicción, que es un SPA estático servido desde
 * `static/forecast/`.
 */
import { ui } from '$lib/i18n/ui.js';

/**
 * Predicción se lleva el idioma en la ruta y la estación en la consulta.
 *
 * Es un SPA aparte: no comparte estado con este frontend, y sin esos dos datos
 * su barra saldría en español y con las pestañas de estación apagadas, que es
 * justo la discontinuidad que se quería evitar.
 */
export function forecastHref(language, slug = '') {
  const params = new URLSearchParams();
  if (slug) params.set('slug', slug);
  // Marca de procedencia para el panel de uso: sin ella, entrar por la pestaña
  // y teclear la dirección contaban igual («forecast.direct»).
  params.set('from', 'app');
  return `/${language}/forecast?${params.toString()}`;
}

/**
 * Avisos está en construcción: solo aparece con `vite dev`.
 *
 * `import.meta.env.DEV` lo sustituye Vite al compilar: en el build de
 * producción vale `false` y la barra no la enseña. Fuera de Vite —los tests
 * con `node --test`— `import.meta.env` no existe y cuenta como producción.
 */
const WARNINGS_ENABLED = import.meta.env !== undefined && import.meta.env.DEV === true;

export function appTabs({ language, slug = '', observationPath = '', warnings = WARNINGS_ENABLED }) {
  const tabs = [
    {
      id: 'observation',
      label: ui(language, 'tab_observation'),
      icon: 'LayoutDashboard',
      // Las redes sin ficha indexable se identifican por red e id: su panel
      // no cuelga de un slug, pero se conecta igual.
      href: slug ? `/${language}/observation/${slug}` : observationPath || '/'
    },
    { id: 'map', label: ui(language, 'tab_map'), icon: 'Map', href: `/${language}/map` },
    { id: 'forecast', label: ui(language, 'tab_forecast'), symbol: '∂', href: forecastHref(language, slug), external: true },
    { id: 'ranking', label: ui(language, 'tab_ranking'), icon: 'Trophy', href: `/${language}/ranking` }
  ];
  if (warnings) {
    tabs.push({ id: 'warnings', label: ui(language, 'tab_warnings'), icon: 'TriangleAlert', href: `/${language}/warnings` });
  }
  return tabs;
}

/**
 * Vistas de una estación, agrupadas dentro de Observación.
 *
 * La estación se identifica por su slug o, cuando no tiene —las redes con
 * credencial propia y las que repiten nombres a millares—, por red e
 * identificador. Antes solo se contemplaba el slug y la barra desaparecía
 * entera al conectar una estación propia: quedaba la ficha suelta, sin
 * tendencias ni histórico, aunque las tres vistas existan igual para ella.
 */
export function observationTabs({ language, slug = '', provider = '', stationId = '' }) {
  const key =
    slug ||
    (provider && stationId
      ? `${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}`
      : '');
  if (!key) return [];
  return [
    { id: 'observation', label: ui(language, 'tab_current'), icon: 'Activity', href: `/${language}/observation/${key}` },
    { id: 'trends', label: ui(language, 'tab_trend'), icon: 'TrendingUp', href: `/${language}/trends/${key}` },
    { id: 'historical', label: ui(language, 'tab_historical'), icon: 'History', href: `/${language}/historical/${key}` }
  ];
}

/** Datos de la cinta superior: los mismos en todas las pestañas. */
export function stationStripe(station, meta) {
  return {
    provider: meta.provider,
    name: meta.name,
    place: meta.location,
    id: station.station_id,
    elevation: Number.isFinite(station.elevation) ? station.elevation : null,
    lat: station.lat?.toFixed(4),
    lon: station.lon?.toFixed(4)
  };
}
