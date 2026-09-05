/**
 * Credenciales personales de Weather Underground y WeatherLink.
 *
 * Estas dos redes no tienen catálogo público: cada quien consulta SU estación
 * con SU clave. Se guardan en el navegador y solo salen de él para pedir el
 * dato, en la llamada al backend que habla con el proveedor —el backend tiene
 * que verlas, es quien hace esa llamada—. Nunca viajan en la URL ni en cada
 * navegación por el resto del sitio: por eso van aquí y no en una cookie.
 */
const KEY = 'mlx-credentials';

/** Redes con credencial propia. El resto usan las claves del servidor. */
export const PERSONAL_PROVIDERS = ['WU', 'WEATHERLINK'];

export function isPersonalProvider(provider) {
  return PERSONAL_PROVIDERS.includes(String(provider || '').toUpperCase());
}

let credentials = $state({});

/**
 * Dónde se guarda cada credencial.
 *
 * Marcando «recordar» va a `localStorage` y sigue ahí mañana; sin marcar, a
 * `sessionStorage` y desaparece al cerrar la pestaña. Guardar una clave de
 * terceros sin preguntar no es cosa que deba pasar sola.
 */
function stores() {
  try {
    return [localStorage, sessionStorage];
  } catch {
    return [];
  }
}

function read() {
  const merged = {};
  for (const store of stores()) {
    try {
      const raw = store.getItem(KEY);
      const value = raw ? JSON.parse(raw) : null;
      if (value && typeof value === 'object') Object.assign(merged, value);
    } catch {
      /* almacenamiento bloqueado: se sigue sin memoria */
    }
  }
  return merged;
}

/** Relee lo guardado. Se llama al montar: en el servidor no hay dónde mirar. */
export function loadCredentials() {
  credentials = read();
  return credentials;
}

/** Credenciales de una red, o `null`. */
export function credentialsFor(provider) {
  return credentials[String(provider || '').toUpperCase()] || null;
}

export function saveCredentials(provider, value, { remember = false } = {}) {
  const code = String(provider || '').toUpperCase();
  credentials = { ...read(), [code]: value };
  try {
    const target = remember ? localStorage : sessionStorage;
    const other = remember ? sessionStorage : localStorage;
    const current = JSON.parse(target.getItem(KEY) || '{}');
    target.setItem(KEY, JSON.stringify({ ...current, [code]: value }));
    // Que no quede una copia vieja en el otro almacén contradiciendo esta.
    const stale = JSON.parse(other.getItem(KEY) || '{}');
    if (stale[code]) {
      delete stale[code];
      if (Object.keys(stale).length) other.setItem(KEY, JSON.stringify(stale));
      else other.removeItem(KEY);
    }
  } catch {
    // Sin almacenamiento la conexión dura lo que la página, que sigue siendo
    // mejor que no dejar conectarse.
  }
}

/** ¿Está recordada entre sesiones, o solo mientras dure la pestaña? */
export function isRemembered(provider) {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || '{}');
    return Boolean(stored[String(provider || '').toUpperCase()]);
  } catch {
    return false;
  }
}

/** Olvida las de una red, o todas si no se dice cuál. */
export function forgetCredentials(provider = '') {
  const code = String(provider || '').toUpperCase();
  const updated = provider ? { ...read() } : {};
  if (provider) delete updated[code];
  credentials = updated;
  for (const store of stores()) {
    try {
      if (!provider) {
        store.removeItem(KEY);
        continue;
      }
      const stored = JSON.parse(store.getItem(KEY) || '{}');
      delete stored[code];
      if (Object.keys(stored).length) store.setItem(KEY, JSON.stringify(stored));
      else store.removeItem(KEY);
    } catch {
      /* ídem */
    }
  }
}
