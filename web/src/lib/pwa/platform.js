/**
 * Qué sistema, dispositivo y navegador tiene el visitante, y cómo se instala
 * la PWA en esa combinación.
 *
 * Instalar una web no es igual en todas partes: Chrome y Edge ofrecen un
 * diálogo propio que se puede lanzar desde un botón; Safari no, y hay que
 * decir dónde está «Añadir a pantalla de inicio»; Firefox de escritorio no
 * instala nada. Aquí solo se decide; los textos van en `install-i18n.js`.
 *
 * Se lee el «user agent» porque es lo único que distingue un Safari de un
 * Chrome en iPhone, y porque iPadOS se hace pasar por un Mac.
 */

/**
 * Apps que abren enlaces en su propio navegador, donde no se puede instalar.
 * `GSA` es la app de Google en iPhone: pasa por Safari pero no añade nada.
 */
const IN_APP = /GSA\/|FBAN|FBAV|FB_IAB|Instagram|Line\/|MicroMessenger|musical_ly|TikTok|LinkedInApp|Snapchat|Pinterest|; wv\)/;

/**
 * @param {{ userAgent?: string, maxTouchPoints?: number, uaDataPlatform?: string }} hints
 * @returns {{ os: string, device: 'mobile' | 'tablet' | 'desktop', browser: string, inApp: boolean }}
 */
export function detectPlatform({ userAgent = '', maxTouchPoints = 0, uaDataPlatform = '' } = {}) {
  const ua = String(userAgent);
  const hint = String(uaDataPlatform).toLowerCase();

  let os = 'other';
  // iPadOS 13+ se anuncia como Mac de escritorio; lo delata la pantalla táctil.
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1)) os = 'ipados';
  else if (/iPhone|iPod/.test(ua)) os = 'ios';
  else if (/Android/.test(ua) || hint === 'android') os = 'android';
  else if (/CrOS/.test(ua) || hint === 'chrome os' || hint === 'chromeos') os = 'chromeos';
  else if (/Windows/.test(ua) || hint === 'windows') os = 'windows';
  else if (/Macintosh|Mac OS X/.test(ua) || hint === 'macos') os = 'macos';
  else if (/Linux/.test(ua) || hint === 'linux') os = 'linux';

  let browser = 'other';
  if (/EdgA?\/|EdgiOS\/|Edg\//.test(ua)) browser = 'edge';
  else if (/SamsungBrowser\//.test(ua)) browser = 'samsung';
  else if (/OPR\/|OPiOS\/|OPT\//.test(ua)) browser = 'opera';
  else if (/FxiOS\/|Firefox\//.test(ua)) browser = 'firefox';
  else if (/CriOS\/|Chrome\/|Chromium\//.test(ua)) browser = 'chrome';
  else if (/Safari\//.test(ua) && ['ios', 'ipados', 'macos'].includes(os)) browser = 'safari';

  const device =
    os === 'ios' ? 'mobile'
    : os === 'ipados' ? 'tablet'
    : os === 'android' ? (/Mobile/.test(ua) ? 'mobile' : 'tablet')
    : os === 'other' && /Mobile/.test(ua) ? 'mobile'
    : 'desktop';

  return { os, device, browser, inApp: IN_APP.test(ua) };
}

export const INSTALL_METHODS = [
  'installed',
  'prompt',
  'ios-safari',
  'ios-share',
  'android-menu',
  'android-samsung',
  'android-firefox',
  'mac-safari',
  'desktop-chromium',
  'open-browser',
  'unsupported'
];

/**
 * Cómo se instala en esta combinación.
 *
 * - `prompt`: el navegador ha ofrecido su diálogo (`beforeinstallprompt`) y
 *   basta un botón. Manda sobre todo lo demás, porque es lo más fiable.
 * - El resto son instrucciones: dónde está la opción en ese navegador.
 *
 * @param {ReturnType<typeof detectPlatform>} platform
 * @param {{ canPrompt?: boolean, standalone?: boolean }} state
 */
export function installMethod(platform, { canPrompt = false, standalone = false } = {}) {
  if (standalone) return 'installed';
  if (platform.inApp) return 'open-browser';
  if (canPrompt) return 'prompt';
  const { os, browser } = platform;
  if (os === 'ios' || os === 'ipados') {
    // Desde iOS 16.4 Chrome, Edge y Firefox también añaden a la pantalla de
    // inicio desde su menú de compartir; antes solo Safari.
    return browser === 'safari' ? 'ios-safari' : 'ios-share';
  }
  if (os === 'android') {
    if (browser === 'firefox') return 'android-firefox';
    if (browser === 'samsung') return 'android-samsung';
    return 'android-menu';
  }
  if (os === 'macos' && browser === 'safari') return 'mac-safari';
  if (['chrome', 'edge', 'opera'].includes(browser)) return 'desktop-chromium';
  // Firefox de escritorio y lo que no reconocemos: no instalan webs.
  return 'unsupported';
}

/**
 * Forma de instalar teniendo en cuenta lo que se sabe de este navegador.
 *
 * `knownInstalled` es la marca que deja una instalación hecha desde aquí:
 * sin ella, quien instala en Chrome y sigue entrando por la pestaña normal
 * vería la tarjeta ofreciéndole instalar otra vez en cada ficha. Si el
 * navegador vuelve a ofrecer instalar (`canPrompt`), es que ya no lo está:
 * la desinstaló, y la marca no vale.
 *
 * `installedNow` es la instalación recién hecha en esta visita, que se
 * confirma en la tarjeta en vez de hacerla desaparecer de golpe.
 */
export function resolveInstallMethod(
  platform,
  { canPrompt = false, standalone = false, knownInstalled = false, installedNow = false } = {}
) {
  if (!platform) return null;
  if (installedNow) return 'installed';
  return installMethod(platform, {
    canPrompt,
    standalone: standalone || (knownInstalled && !canPrompt)
  });
}
