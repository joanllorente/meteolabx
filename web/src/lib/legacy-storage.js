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
const HECHA = 'mlx-legacy-imported';

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

function leerJson(clave) {
  const crudo = leer(clave);
  if (!crudo) return null;
  try {
    return JSON.parse(crudo);
  } catch {
    // La interfaz anterior guardaba algunos valores como texto pelado.
    return crudo;
  }
}

function escribir(clave, valor) {
  try {
    localStorage.setItem(clave, JSON.stringify(valor));
  } catch {
    /* sin almacenamiento no hay nada que migrar */
  }
}

function vacia(clave) {
  const valor = leerJson(clave);
  if (valor === null || valor === '') return true;
  return Array.isArray(valor) ? valor.length === 0 : Object.keys(valor).length === 0;
}

/** Altitud: la interfaz anterior la guardaba como texto. */
function altitud(clave) {
  const valor = Number(String(leerJson(clave) ?? '').replace(',', '.'));
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
  const antiguos = leerJson(ANTIGUAS.favoritos);
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
    const wuKey = String(leerJson(ANTIGUAS.wuKey) || '').trim();
    const wuStation = String(leerJson(ANTIGUAS.wuStation) || '').trim();
    if (wuKey && wuStation) {
      credenciales.WU = {
        stationId: wuStation.toUpperCase(),
        apiKey: wuKey,
        elevation: altitud(ANTIGUAS.wuAltitud)
      };
    }
    const wlKey = String(leerJson(ANTIGUAS.wlKey) || '').trim();
    const wlSecret = String(leerJson(ANTIGUAS.wlSecret) || '').trim();
    if (wlKey && wlSecret) {
      credenciales.WEATHERLINK = {
        stationId: String(leerJson(ANTIGUAS.wlStation) || '').trim(),
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
  const objetivo = String(leerJson(ANTIGUAS.autoconexion) || '').trim();
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
