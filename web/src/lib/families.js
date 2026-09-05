/**
 * Color por familia de variable. Es el mismo criterio en toda la app: la
 * temperatura siempre naranja, el viento siempre turquesa. Sale del prototipo
 * (`prototype-svelte/src/data.js`), que es donde se decidió la paleta.
 */
export const families = {
  temperature: { color: '#ff8a4c', soft: 'rgba(255,138,76,.14)' },
  humidity: { color: '#2fb8a6', soft: 'rgba(47,184,166,.14)' },
  dewpoint: { color: '#4db6e8', soft: 'rgba(77,182,232,.14)' },
  pressure: { color: '#8b8bff', soft: 'rgba(139,139,255,.14)' },
  wind: { color: '#37c8d6', soft: 'rgba(55,200,214,.14)' },
  precip: { color: '#5b9bff', soft: 'rgba(91,155,255,.14)' },
  thermo: { color: '#b98bff', soft: 'rgba(185,139,255,.14)' },
  radiation: { color: '#f4bb3f', soft: 'rgba(244,187,63,.14)' }
};
