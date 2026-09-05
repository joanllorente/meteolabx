/**
 * Favoritos y autoconexión, en el navegador.
 *
 * En Streamlit esto vive en `session_state` y se pierde al cerrar; aquí va a
 * `localStorage`, que sobrevive. Se guarda lo justo para reconstruir un
 * enlace —slug, nombre y red—, nunca la posición de nadie.
 *
 * Todo acceso va envuelto: en navegación privada o con las cookies
 * bloqueadas, `localStorage` lanza en vez de devolver vacío, y una lista de
 * favoritos no puede tumbar la página.
 */

const FAVOURITES_KEY = 'mlx-favourites';
const AUTOCONNECT_KEY = 'mlx-autoconnect';

/**
 * La lista, compartida por toda la aplicación.
 *
 * Antes cada componente leía `localStorage` por su cuenta y se quedaba con
 * la foto del momento en que se montó: guardabas una estación desde el mapa
 * y el menú de la barra seguía diciendo que no había ninguna. Con un estado
 * único, guardar en un sitio se ve en todos.
 */
let favourites = $state([]);

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* sin almacenamiento no hay nada que hacer, y tampoco pasa nada */
  }
}

/**
 * `[{ slug, path, name, provider }]`, en el orden en que se guardaron.
 *
 * `slug` es el de las redes con ficha indexable y `path` la ruta completa de
 * las que no lo tienen —IEM, Netatmo, Windy—: esas también se guardan, que
 * son justo las que uno tiene al lado de casa.
 */
function readFavourites() {
  const stored = read(FAVOURITES_KEY, []);
  if (!Array.isArray(stored)) return [];
  return stored.filter((item) => item?.slug || item?.path);
}

/** Relee del almacenamiento. Se llama al montar: en el servidor no existe. */
export function loadFavourites() {
  favourites = readFavourites();
  return favourites;
}

export function listFavourites() {
  return favourites;
}

/** Identidad de un favorito: su slug si lo tiene, o su ruta. */
export function favouriteKey(station) {
  return station?.slug || station?.path || '';
}

export function isFavourite(key) {
  return Boolean(key) && favourites.some((item) => favouriteKey(item) === key);
}

/** Añade o quita según esté; devuelve si quedó guardada. */
export function toggleFavourite({ slug = '', path = '', name = '', provider = '' }) {
  const key = slug || path;
  if (!key) return false;
  // Se relee antes de tocar: otra pestaña puede haber cambiado la lista.
  const current = readFavourites();
  const without = current.filter((item) => favouriteKey(item) !== key);
  if (without.length !== current.length) {
    write(FAVOURITES_KEY, without);
    favourites = without;
    return false;
  }
  const updated = [...without, { slug, path, name, provider }];
  write(FAVOURITES_KEY, updated);
  favourites = updated;
  return true;
}

/**
 * Añade el favorito, o actualiza el que ya esté.
 *
 * `toggleFavourite` no sirve aquí: al guardar la estación propia hay que
 * dejarla guardada, no alternar. Y su nombre no se conoce hasta que el
 * proveedor responde, así que después se refresca.
 */
export function upsertFavourite({ slug = '', path = '', name = '', provider = '' }) {
  const key = slug || path;
  if (!key) return;
  const current = readFavourites();
  const previous = current.find((item) => favouriteKey(item) === key);
  const entry = {
    slug,
    path,
    name: name || previous?.name || '',
    provider: provider || previous?.provider || ''
  };
  const updated = previous
    ? current.map((item) => (favouriteKey(item) === key ? entry : item))
    : [...current, entry];
  write(FAVOURITES_KEY, updated);
  favourites = updated;
}

/** Ruta a la que lleva un favorito, con el idioma de quien mira. */
export function favouriteHref(favourite, language) {
  if (favourite?.slug) return `/${language}/observation/${favourite.slug}`;
  return favourite?.path || '/';
}

/** Estación que se abre sola al entrar, o cadena vacía. */
export function autoconnectSlug() {
  const stored = read(AUTOCONNECT_KEY, '');
  return typeof stored === 'string' ? stored : '';
}

export function setAutoconnect(slug) {
  write(AUTOCONNECT_KEY, slug || null);
}
