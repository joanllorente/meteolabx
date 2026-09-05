/**
 * Las reglas del tema, sin navegador de por medio.
 *
 * Aquí solo está lo que se puede decidir con un número y una cadena: qué toca
 * según la hora y cuál es el siguiente estado del interruptor. Lo que depende
 * del dispositivo —`matchMedia`, el `<html>`, `localStorage`— vive en
 * `theme.svelte.js`.
 */

/**
 * El tema que pide la hora local.
 *
 * Es el último recurso: solo se usa cuando el dispositivo no expresa
 * preferencia. De ocho de la mañana a ocho de la tarde, claro. La misma regla
 * que aplica el script del `<head>` antes de pintar; si una cambia, la otra
 * también.
 */
export function themeForHour(hour) {
  return hour >= 8 && hour < 20 ? 'light' : 'dark';
}

/** Automático → claro → oscuro → automático. */
export function nextMode(mode) {
  return mode === 'auto' ? 'light' : mode === 'light' ? 'dark' : 'auto';
}

/** Lo que se ve, dado lo elegido y lo que pide el sistema. */
export function resolveTheme(mode, system) {
  return mode === 'auto' ? system : mode;
}
