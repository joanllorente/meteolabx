/**
 * La estación con la que se está trabajando.
 *
 * Conectar no es cargar una página: es elegir una estación y moverse por la
 * aplicación con ella. Pero el mapa y el ranking no cuelgan de ninguna, así
 * que al pasar por ahí las pestañas perdían el slug y, al volver, Observación
 * llevaba al panel vacío: parecía que te habías desconectado.
 *
 * Aquí se recuerda cuál es, en `localStorage`, para que la barra siga
 * apuntando a ella mientras se navega. No guarda datos de la estación ni los
 * vuelve a pedir: solo el enlace, que es lo que se perdía.
 */
const KEY = 'mlx-connection';

/** Estación conectada, o `null`. Se lee al hidratar. */
let connection = $state(null);

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && (value.slug || value.path) ? value : null;
  } catch {
    // Navegación privada o almacenamiento bloqueado: sin memoria, pero la
    // aplicación funciona igual.
    return null;
  }
}

/** Carga la conexión guardada. Se llama al montar cualquier página. */
export function loadConnection() {
  connection = read();
}

/** La conexión actual, para construir los enlaces de las pestañas. */
export function currentConnection() {
  return connection;
}

/**
 * Recuerda la estación que se está viendo.
 *
 * `slug` es el de las redes con ficha indexable; `path` la ruta completa de
 * observación para las que no lo tienen (IEM, Netatmo, Windy), donde la
 * estación se identifica por red e identificador.
 */
export function rememberConnection({ slug = '', path = '', name = '', provider = '' }) {
  const value = { slug, path, name, provider };
  connection = value;
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    /* sin almacenamiento no hay nada que hacer, y tampoco pasa nada */
  }
}

/** Olvida la estación: es lo que hace el botón de desconectar. */
export function forgetConnection() {
  connection = null;
  spendAutoconnect();
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ídem */
  }
}

/**
 * ¿Toca abrir sola la estación de siempre?
 *
 * La autoconexión es para cuando se abre la web, no para cada vuelta a la
 * portada: sin esta marca, «Desconectar» llevaba a la portada y la portada
 * volvía a conectar la misma estación, de modo que no había forma de quedarse
 * desconectado.
 *
 * Vive en `sessionStorage`, así que dura lo que la pestaña: mañana, al abrir
 * de nuevo, la estación vuelve a salir sola.
 */
const AUTOCONNECT_SPENT = 'meteolabx_autoconnect_spent';

export function autoconnectPending() {
  try {
    return sessionStorage.getItem(AUTOCONNECT_SPENT) !== '1';
  } catch {
    // Sin almacenamiento no hay memoria de sesión: más vale no insistir que
    // dejar a alguien sin poder desconectarse.
    return false;
  }
}

export function spendAutoconnect() {
  try {
    sessionStorage.setItem(AUTOCONNECT_SPENT, '1');
  } catch {
    /* sin almacenamiento, la autoconexión simplemente no se repite */
  }
}
