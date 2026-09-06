/**
 * Estadísticas internas de uso.
 *
 * Cuentan qué estaciones se consultan y qué falla al consultarlas: es lo que
 * alimenta el panel interno y lo único que hay para saber si una red lleva
 * días caída. No llevan identificadores, ni sesión, ni IP —eso lo dice la
 * ventana de privacidad— y nunca deben estorbar: cada envío va por su cuenta
 * y su fallo se ignora.
 *
 * Lo enviaba la aplicación anterior; al retirarla, las conexiones, los errores
 * y las entradas a cada pestaña dejaron de contarse y el panel se quedaba
 * enseñando solo las aperturas de ficha.
 */
/**
 * Interruptor para no contarse a uno mismo.
 *
 * Probar la aplicación —abrir la misma estación cinco veces para ver cómo va
 * de rápida, saltar entre pestañas— ensucia las cifras del panel. Con
 * `?stats=off` en cualquier URL este navegador deja de registrar nada, y con
 * `?stats=on` vuelve a hacerlo. La decisión vive en `localStorage`: no viaja
 * al servidor, no es una cuenta ni una cookie, y hay que repetirla en cada
 * navegador o dispositivo desde el que se pruebe.
 */
const OPT_OUT = 'mlx-stats-off';

/** Qué hacer con el parámetro de la URL y lo que ya había guardado. */
export function resolveOptOut(parametro, guardado) {
  if (parametro === 'off') return { excluido: true, guardar: true };
  if (parametro === 'on') return { excluido: false, guardar: false };
  return { excluido: guardado === '1', guardar: guardado === '1' };
}

export function statsExcluded() {
  if (typeof localStorage === 'undefined' || typeof location === 'undefined') return false;
  try {
    const parametro = new URLSearchParams(location.search).get('stats');
    const { excluido, guardar } = resolveOptOut(parametro, localStorage.getItem(OPT_OUT));
    if (guardar) localStorage.setItem(OPT_OUT, '1');
    else localStorage.removeItem(OPT_OUT);
    return excluido;
  } catch {
    // Navegador sin almacenamiento: se cuenta, como siempre.
    return false;
  }
}

/** Enciende o apaga el registro en este navegador. Lo usa el panel interno. */
export function setStatsExcluded(excluido) {
  try {
    if (excluido) localStorage.setItem(OPT_OUT, '1');
    else localStorage.removeItem(OPT_OUT);
  } catch {
    /* sin almacenamiento no hay nada que recordar */
  }
}

function send(path, body) {
  if (typeof fetch !== 'function') return;
  if (statsExcluded()) return;
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    // Sobrevive a la navegación: si la visita se registra justo al saltar de
    // página, el navegador no la cancela a medias.
    keepalive: true
  }).catch(() => {});
}

/**
 * Buscadores que reconocemos por su dominio. Google tiene un dominio por
 * país (`google.es`, `google.it`…), de ahí la comprobación aparte.
 */
const BUSCADORES = [
  'bing.com',
  'duckduckgo.com',
  'ecosia.org',
  'search.yahoo.com',
  'search.brave.com',
  'qwant.com',
  'startpage.com',
  'yandex.com',
  'yandex.ru',
  'baidu.com',
  'seznam.cz',
  'naver.com',
  'mojeek.com'
];

const esGoogle = (host) => host === 'google.com' || /^google\.[a-z.]+$/.test(host);

/**
 * De dónde llegó quien abre una ficha.
 *
 * `interna` la pone el enrutador cuando el salto ocurre dentro de la
 * aplicación; el resto sale del referente que da el navegador. Ojo con
 * `directa`: ahí caen la barra de direcciones y los marcadores, pero también
 * WhatsApp, el correo y cualquier sitio que no mande referente. Es «no se
 * sabe», no «escribió la URL».
 *
 * Del referente se guarda solo el dominio, nunca la URL completa: el panel
 * quiere saber qué sitios enlazan, no qué páginas lee nadie.
 */
export function classifyEntry(referrer, host, { interna = false } = {}) {
  if (interna) return { kind: 'internal', domain: '' };
  let origen = '';
  try {
    origen = new URL(String(referrer || '')).hostname.toLowerCase().replace(/^www\./, '');
  } catch {
    origen = '';
  }
  if (!origen) return { kind: 'direct', domain: '' };
  const propio = String(host || '').toLowerCase().replace(/^www\./, '');
  if (origen === propio) return { kind: 'internal', domain: '' };
  const buscador = esGoogle(origen) || BUSCADORES.some((s) => origen === s || origen.endsWith(`.${s}`));
  return { kind: buscador ? 'search' : 'external', domain: origen.slice(0, 120) };
}

/**
 * Móvil, tableta o escritorio.
 *
 * Se mira el puntero, no el ancho ni el «user agent»: un dedo es un puntero
 * grueso y un ratón uno fino, y eso no cambia al girar la pantalla ni al
 * estrechar la ventana. El ancho solo separa el móvil de la tableta.
 */
export function deviceKind({ coarse = false, width = 0 } = {}) {
  if (!coarse) return 'desktop';
  return width && width >= 768 ? 'tablet' : 'mobile';
}

function currentDevice() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return '';
  try {
    return deviceKind({
      coarse: window.matchMedia('(pointer: coarse)').matches,
      width: Math.min(window.screen?.width || 0, window.innerWidth || Infinity)
    });
  } catch {
    return '';
  }
}

/**
 * Alguien ha abierto la ficha de una estación.
 *
 * `language` es el idioma declarado por el componente, no la nacionalidad
 * del visitante. Se contrasta con la URL y la lista de idiomas del navegador.
 */
export function recordVisit({
  provider,
  stationId,
  name = '',
  source = 'app',
  language = '',
  decision = null,
  entry = null
}) {
  if (!provider || !stationId) return;
  send('/v1/stats/visit', {
    provider,
    station_id: stationId,
    name,
    source,
    language,
    ...visitLanguageContext(),
    page_request_languages: decision?.requestLanguages || '',
    language_reason: decision?.reason || '',
    entry: entry?.kind || '',
    referrer_domain: entry?.domain || '',
    device: currentDevice()
  });
}

/**
 * La consulta a esa estación falló.
 *
 * `kind` es la categoría que ya distingue la interfaz —tiempo agotado,
 * credenciales rechazadas, red incomunicada— y no el mensaje completo: el
 * registro cuenta clases de fallo, no textos.
 */
export function recordConnectionError({ provider, stationId, name = '', kind, status = null }) {
  if (!provider || !stationId || !kind) return;
  send('/v1/stats/error', {
    provider,
    station_id: stationId,
    name,
    error_kind: kind,
    ...(Number.isInteger(status) && status >= 100 && status <= 599 ? { status_code: status } : {})
  });
}

/** Entrada a una pestaña. Las del mapa llevan su capa: `map.temperature`. */
export function recordSection(section) {
  if (!section) return;
  send('/v1/stats/section', { section });
}

/** Apertura de una ficha indexable, con el idioma en el que se leyó. */
export function recordSeoView({ provider, stationId, name = '', language = '' }) {
  if (!provider || !stationId) return;
  send('/v1/stats/seo-view', { provider, station_id: stationId, name, language });
}

/** Solo etiquetas de idioma; nunca se envía la URL completa ni sus filtros. */
export function visitLanguageContext(browser = globalThis.navigator, url = globalThis.location) {
  const valid = (value) => /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(String(value || ''));
  const languages = browser?.languages?.length ? browser.languages : [browser?.language];
  const urlLanguage = String(url?.pathname || '').split('/')[1] || '';
  return {
    browser_languages: [...new Set(Array.from(languages || []).filter(valid))].slice(0, 6).join(',').slice(0, 200),
    url_language: ['es', 'ca', 'en', 'fr', 'it', 'pt'].includes(urlLanguage) ? urlLanguage : ''
  };
}
