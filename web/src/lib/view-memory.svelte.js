/**
 * Lo último que se estaba mirando en cada pestaña.
 *
 * Los filtros del mapa y la selección del ranking viajan en la URL —así una
 * vista concreta se puede enlazar—, pero los enlaces de la barra apuntan a la
 * pestaña pelada. Al ir a otra pestaña y volver, el trabajo de filtrar se
 * perdía: veinte países elegidos y las casillas marcadas, de vuelta al
 * principio.
 *
 * Se recuerda solo mientras dure la pestaña del navegador: es el contexto de
 * un rato de trabajo, no una preferencia que deba sobrevivir a mañana.
 */
const KEY = 'mlx-view-search';

let searches = $state({});

function read() {
  try {
    const raw = sessionStorage.getItem(KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

/** Relee lo guardado. Al montar: en el servidor no hay dónde mirar. */
export function loadViewSearches() {
  searches = read();
  return searches;
}

/** La consulta recordada de una vista, o cadena vacía. */
export function viewSearch(view) {
  return String(searches[view] || '');
}

/**
 * Recuerda con qué filtros se está mirando una vista.
 *
 * Una consulta vacía también se guarda: quitar todos los filtros es una
 * decisión, y volver a la pestaña no debería resucitar los de antes.
 */
export function rememberViewSearch(view, search) {
  const value = String(search || '');
  if (searches[view] === value) return;
  searches = { ...searches, [view]: value };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(searches));
  } catch {
    /* sin almacenamiento, cada visita empieza de cero */
  }
}
