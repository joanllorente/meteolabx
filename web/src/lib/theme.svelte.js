/**
 * Tema claro u oscuro.
 *
 * Tres estados, no dos: automático —manda el dispositivo y, si no expresa
 * preferencia, la hora local—, claro y oscuro. Con solo dos, la primera vez
 * que alguien probaba el interruptor su elección quedaba clavada para
 * siempre: un ordenador en modo claro seguía viendo MeteoLabX en oscuro a las
 * diez de la mañana, sin manera de devolverle el mando al sistema.
 *
 * El tema lo aplica un script del `<head>` antes de pintar —si no, recargar en
 * oscuro da un fogonazo blanco—; aquí se lee lo que decidió, se ofrece el
 * interruptor y, en automático, se sigue al sistema mientras la página está
 * abierta.
 */
import { nextMode, resolveTheme, themeForHour } from './theme.js';

// Clave propia de la 2.0.0. La anterior guardaba «claro» u «oscuro» en
// cuanto alguien probaba el interruptor —no había vuelta al automático—,
// así que empezar de cero es lo que devuelve el mando al dispositivo.
const KEY = 'mlx-theme-2';

/** Lo que ha elegido esta persona: 'auto', 'light' o 'dark'. */
let mode = $state('auto');
/** Lo que se está viendo: 'light' o 'dark'. */
let theme = $state('dark');

/** Lo que pide el dispositivo, o la hora si no pide nada. */
export function systemTheme() {
  if (typeof window === 'undefined' || !window.matchMedia) return themeForHour(new Date().getHours());
  if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
  if (window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
  return themeForHour(new Date().getHours());
}

function paint(next) {
  theme = next;
  document.documentElement.dataset.theme = next;
  // La barra del navegador en móvil también cambia de color.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', next === 'dark' ? '#080d17' : '#eaeef4');
}

/**
 * Lee el estado al montar y, en automático, se queda atento al sistema.
 *
 * Devuelve la función de limpieza que espera `$effect`: sin ella, cada
 * navegación dejaría un oyente más escuchando el mismo cambio.
 */
export function loadTheme() {
  if (typeof document === 'undefined') return () => {};

  let saved = '';
  try {
    saved = localStorage.getItem(KEY) || '';
  } catch {
    /* almacenamiento bloqueado: se sigue en automático */
  }
  mode = saved === 'light' || saved === 'dark' ? saved : 'auto';
  theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';

  if (typeof window === 'undefined' || !window.matchMedia) return () => {};

  // En automático, cambiar el tema del sistema cambia el de la página sin
  // recargarla; también al cruzar las ocho de la tarde en un equipo sin
  // preferencia, que es cuando la hora manda.
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const follow = () => {
    if (mode === 'auto') paint(systemTheme());
  };
  query.addEventListener('change', follow);
  return () => query.removeEventListener('change', follow);
}

export function currentTheme() {
  return theme;
}

export function currentMode() {
  return mode;
}

/**
 * Pasa al siguiente estado: automático → claro → oscuro → automático.
 *
 * La vuelta al automático es lo que faltaba, y es la que devuelve el mando al
 * dispositivo.
 */
export function cycleTheme() {
  mode = nextMode(mode);
  paint(resolveTheme(mode, systemTheme()));
  try {
    if (mode === 'auto') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, mode);
  } catch {
    /* sin almacenamiento el cambio dura lo que la pestaña */
  }
}
