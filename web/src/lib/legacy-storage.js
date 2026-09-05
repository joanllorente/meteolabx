/**
 * Lo que quedó guardado por la interfaz anterior.
 *
 * El dominio es el mismo, así que en el navegador de quien ya usaba MeteoLabX
 * siguen ahí sus favoritos, sus credenciales y la estación que abría al
 * entrar. Lo que cambió son las claves, de modo que sin esta traducción esa
 * gente se encontraría la lista de favoritos vacía y el formulario en blanco,
 * con sus datos intactos y nadie leyéndolos.
 *
 * Se ejecuta una vez por navegador y solo rellena lo que esté vacío: nunca
 * pisa algo elegido en la interfaz nueva. Las unidades y la calibración de
 * sensores no aparecen aquí porque comparten clave y formato: esas se
 * conservan solas.
 */
/**
 * El centinela lleva un 2: la primera versión leía las claves antiguas como si
 * guardaran el valor pelado y no traía nada, pero se marcaba igual y no
 * volvía a intentarlo. Cambiar el nombre da una segunda pasada a quien ya
 * entró con la versión rota.
 */
const HECHA = 'mlx-legacy-imported-2';

/** Valor con el que la interfaz anterior marcaba «esto ya no está». */
const OLVIDADO = '__MLX_FORGOTTEN__';

const ANTIGUAS = {
  favoritos: 'meteolabx_favorites',
  wuStation: 'meteolabx_active_station',
  wuKey: 'meteolabx_active_key',
  wuAltitud: 'meteolabx_active_z',
  wlKey: 'meteolabx_weatherlink_api_key',
  wlSecret: 'meteolabx_weatherlink_api_secret',
  wlStation: 'meteolabx_weatherlink_station',
  wlAltitud: 'meteolabx_weatherlink_z',
  autoconexion: 'meteolabx_auto_connect_target'
};

const NUEVAS = {
  favoritos: 'mlx-favourites',
  credenciales: 'mlx-credentials',
  autoconexion: 'mlx-autoconnect'
};

function leer(clave) {
  try {
    return localStorage.getItem(clave) || '';
  } catch {
    return '';
  }
}

/**
 * Lee una clave antigua y le quita el envoltorio del puente de Streamlit.
 *
 * El componente que escribía en `localStorage` no guardaba el valor a secas:
 * lo metía dentro de un objeto con la propia clave como campo, de modo que
 * `meteolabx_favorites` contiene `{"meteolabx_favorites":"[…]"}` y no la
 * lista. Encima el valor de dentro es texto —Python serializaba antes de
 * pasárselo—, así que hay que parsear dos veces. Leerlo como si fuera la
 * lista devolvía un objeto, `Array.isArray` decía que no y la migración se
 * marcaba como hecha sin haber traído un solo favorito.
 */
function leerAntigua(clave) {
  const crudo = leer(clave);
  if (!crudo) return null;

  let valor;
  try {
    valor = JSON.parse(crudo);
  } catch {
    // La interfaz anterior guardaba algunos valores como texto pelado.
    valor = crudo;
  }

  if (valor && typeof valor === 'object' && !Array.isArray(valor)) {
    const claves = Object.keys(valor);
    if (Object.prototype.hasOwnProperty.call(valor, clave)) valor = valor[clave];
    else if (claves.length === 1) valor = valor[claves[0]];
  }

  if (typeof valor === 'string') {
    if (valor === OLVIDADO) return null;
    const texto = valor.trim();
    if (texto.startsWith('[') || texto.startsWith('{')) {
      try {
        return JSON.parse(texto);
      } catch {
        return valor;
      }
    }
  }

  return valor;
}

function escribir(clave, valor) {
  try {
    localStorage.setItem(clave, JSON.stringify(valor));
  } catch {
    /* sin almacenamiento no hay nada que migrar */
  }
}

/**
 * Lee una clave que las dos interfaces comparten —las unidades y la
 * calibración de sensores—, quitando el envoltorio del puente si lo lleva.
 *
 * Estas dos no se migran, se leen en el sitio: la clave y el contenido son los
 * mismos. Lo que no era el mismo es la cáscara, y sin quitarla la interfaz
 * nueva encontraba un objeto con una sola clave donde esperaba las
 * preferencias, y volvía a los valores de fábrica.
 */
export function readSharedLegacyKey(clave) {
  const valor = leerAntigua(clave);
  return valor && typeof valor === 'object' && !Array.isArray(valor) ? valor : null;
}

/**
 * Lo que guarda la interfaz nueva, sin desenvolver nada: aquí el valor es lo
 * que se escribió, y desenvolver un objeto de un solo campo —unas credenciales
 * con solo WU, por ejemplo— confundiría el contenido con el envoltorio.
 */
function leerNueva(clave) {
  const crudo = leer(clave);
  if (!crudo) return null;
  try {
    return JSON.parse(crudo);
  } catch {
    return crudo;
  }
}

function vacia(clave) {
  const valor = leerNueva(clave);
  if (valor === null || valor === '') return true;
  return Array.isArray(valor) ? valor.length === 0 : Object.keys(valor).length === 0;
}

/** Altitud: la interfaz anterior la guardaba como texto. */
function altitud(clave) {
  const valor = Number(String(leerAntigua(clave) ?? '').replace(',', '.'));
  return Number.isFinite(valor) ? valor : null;
}

/**
 * Traduce un favorito antiguo.
 *
 * El formato viejo identifica la estación por red e identificador; el nuevo,
 * por la URL de su ficha. Cuando la estación tiene ficha indexable se guarda
 * su slug, y si no —Netatmo, Windy, las propias— la ruta por red e
 * identificador, que es la que abre el panel igual.
 */
async function traducirFavorito(entrada, language, fetchImpl) {
  const provider = String(entrada?.provider_id || entrada?.kind || '').trim().toUpperCase();
  const stationId = String(entrada?.station_id || '').trim();
  if (!provider || !stationId || provider === 'PROVIDER') return null;

  const name = String(entrada?.station_name || entrada?.name || stationId).trim();
  const path = `/${language}/observation/${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}`;

  // Las estaciones propias no están en el catálogo: no tienen slug que buscar.
  if (provider === 'WU' || provider === 'WEATHERLINK') {
    return { slug: '', path, name, provider };
  }

  const payload = await fetchImpl(
    `/v1/stations/url-slug?${new URLSearchParams({ provider, station_id: stationId })}`
  )
    .then((respuesta) => (respuesta.ok ? respuesta.json() : null))
    .catch(() => null);

  return payload?.url_slug
    ? { slug: payload.url_slug, path: '', name, provider }
    : { slug: '', path, name, provider };
}

/**
 * Importa lo guardado por la interfaz anterior. Devuelve qué se trajo, para
 * poder contarlo en una prueba.
 */
export async function importLegacyStorage(language = 'es', fetchImpl = fetch) {
  if (typeof localStorage === 'undefined') return null;
  if (leer(HECHA)) return null;

  const traido = { favoritos: 0, credenciales: [], autoconexion: '' };

  // 1. Favoritos.
  const antiguos = leerAntigua(ANTIGUAS.favoritos);
  if (Array.isArray(antiguos) && antiguos.length && vacia(NUEVAS.favoritos)) {
    const traducidos = [];
    for (const entrada of antiguos) {
      const favorito = await traducirFavorito(entrada, language, fetchImpl);
      if (favorito) traducidos.push(favorito);
    }
    if (traducidos.length) {
      escribir(NUEVAS.favoritos, traducidos);
      traido.favoritos = traducidos.length;
    }
  }

  // 2. Credenciales. Se guardan recordadas, que es lo que eran: quien las
  //    tenía en el navegador ya había aceptado conservarlas.
  if (vacia(NUEVAS.credenciales)) {
    const credenciales = {};
    const wuKey = String(leerAntigua(ANTIGUAS.wuKey) || '').trim();
    const wuStation = String(leerAntigua(ANTIGUAS.wuStation) || '').trim();
    if (wuKey && wuStation) {
      credenciales.WU = {
        stationId: wuStation.toUpperCase(),
        apiKey: wuKey,
        elevation: altitud(ANTIGUAS.wuAltitud)
      };
    }
    const wlKey = String(leerAntigua(ANTIGUAS.wlKey) || '').trim();
    const wlSecret = String(leerAntigua(ANTIGUAS.wlSecret) || '').trim();
    if (wlKey && wlSecret) {
      credenciales.WEATHERLINK = {
        stationId: String(leerAntigua(ANTIGUAS.wlStation) || '').trim(),
        apiKey: wlKey,
        apiSecret: wlSecret,
        elevation: altitud(ANTIGUAS.wlAltitud)
      };
    }
    if (Object.keys(credenciales).length) {
      escribir(NUEVAS.credenciales, credenciales);
      traido.credenciales = Object.keys(credenciales);
    }
  }

  // 3. La estación que se abría al entrar.
  const objetivo = String(leerAntigua(ANTIGUAS.autoconexion) || '').trim();
  if (objetivo && vacia(NUEVAS.autoconexion)) {
    escribir(NUEVAS.autoconexion, objetivo);
    traido.autoconexion = objetivo;
  }

  try {
    localStorage.setItem(HECHA, '1');
  } catch {
    /* si no se puede marcar, se reintentará: rellenar lo vacío no hace daño */
  }
  return traido;
}
