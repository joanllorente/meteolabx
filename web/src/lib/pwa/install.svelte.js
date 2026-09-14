/**
 * Estado compartido de la instalación de la PWA.
 *
 * Chrome y Edge avisan de que la web se puede instalar con
 * `beforeinstallprompt`, una sola vez y al cargar la página, esté donde esté
 * el visitante. Si solo lo escuchara la tarjeta de la ficha, quien entra por
 * el mapa y luego abre una estación ya se lo habría perdido. Por eso lo
 * captura el layout y la tarjeta lo lee de aquí.
 */
import { deviceKind, recordPwaEvent, statsExcluded } from '$lib/stats.js';

import { detectPlatform, resolveInstallMethod } from './platform.js';

const LAUNCH_RECORDED = 'mlx-pwa-launch-recorded';
// Instalada desde este navegador. No hay forma estándar de preguntarlo después,
// así que se apunta en el momento en que el navegador lo confirma.
const INSTALLED = 'mlx-pwa-installed';

export const pwa = $state({
  ready: false,
  platform: null,
  standalone: false,
  /** @type {any} */
  deferredPrompt: null,
  installedNow: false,
  knownInstalled: false
});

export function currentMethod() {
  return resolveInstallMethod(pwa.platform, {
    canPrompt: Boolean(pwa.deferredPrompt),
    standalone: pwa.standalone,
    knownInstalled: pwa.knownInstalled,
    installedNow: pwa.installedNow
  });
}

function rememberInstalled(installed) {
  pwa.knownInstalled = installed;
  try {
    if (installed) localStorage.setItem(INSTALLED, String(Math.floor(Date.now() / 1000)));
    else localStorage.removeItem(INSTALLED);
  } catch {
    /* sin almacenamiento, vale solo para esta visita */
  }
}

/**
 * Marca de «ya contado» y envío, juntos.
 *
 * Con `?stats=off` no se envía nada, y tampoco se deja la marca: si después
 * se vuelve a contar ese navegador, sus eventos tienen que poder registrarse.
 */
export function recordOnce(storageKey, event, context = pwaContext()) {
  if (statsExcluded()) return false;
  try {
    if (localStorage.getItem(storageKey)) return false;
    localStorage.setItem(storageKey, String(Math.floor(Date.now() / 1000)));
  } catch {
    // Sin almacenamiento no se puede saber si es la primera vez: no se cuenta.
    return false;
  }
  recordPwaEvent(event, context);
  return true;
}

/** Contexto que acompaña a cada evento de las estadísticas. */
export function pwaContext() {
  const platform = pwa.platform || { os: 'other', device: 'desktop', browser: 'other' };
  return { os: platform.os, device: platform.device, browser: platform.browser, method: currentMethod() || '' };
}

function isStandalone() {
  try {
    return (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.matchMedia('(display-mode: fullscreen)').matches ||
      window.matchMedia('(display-mode: minimal-ui)').matches ||
      // Safari en iPhone y iPad no conoce `display-mode` en versiones viejas.
      navigator.standalone === true
    );
  } catch {
    return false;
  }
}

let initialized = false;

/** Se llama una vez, desde el layout. */
export function initPwa() {
  if (initialized || typeof window === 'undefined') return;
  initialized = true;

  const platform = detectPlatform({
    userAgent: navigator.userAgent,
    maxTouchPoints: navigator.maxTouchPoints || 0,
    uaDataPlatform: navigator.userAgentData?.platform || ''
  });
  // El tipo de dispositivo sale del puntero, como en las visitas, para que las
  // dos estadísticas digan lo mismo de un mismo aparato. Solo si se puede
  // medir: sin `matchMedia` vale la deducción por el «user agent».
  try {
    platform.device = deviceKind({
      coarse: window.matchMedia('(pointer: coarse)').matches,
      width: Math.min(window.screen?.width || 0, window.innerWidth || Infinity)
    });
  } catch {
    /* se queda la del user agent */
  }
  pwa.platform = platform;
  pwa.standalone = isStandalone();
  try {
    pwa.knownInstalled = Boolean(localStorage.getItem(INSTALLED));
  } catch {
    pwa.knownInstalled = false;
  }
  // Chrome puede pasar la misma pestaña a ventana de app al instalar; la
  // tarjeta tiene que desaparecer en ese momento, no en la siguiente carga.
  try {
    window
      .matchMedia('(display-mode: standalone)')
      .addEventListener('change', () => (pwa.standalone = isStandalone()));
  } catch {
    /* navegadores sin `change` en matchMedia: se mira al cargar */
  }

  window.addEventListener('beforeinstallprompt', (event) => {
    // Sin esto Chrome enseña su propia barra en móvil; el botón de la ficha
    // lo lanza cuando la persona lo pide.
    event.preventDefault();
    pwa.deferredPrompt = event;
    // Si vuelve a ofrecer instalar, es que ya no está instalada.
    if (pwa.knownInstalled) rememberInstalled(false);
  });

  window.addEventListener('appinstalled', () => {
    pwa.deferredPrompt = null;
    pwa.installedNow = true;
    rememberInstalled(true);
    recordPwaEvent('installed', pwaContext());
  });

  // Primera apertura de la app instalada. Es la única instalación que se ve
  // en iPhone y iPad, donde Safari no avisa de nada al añadirla; se cuenta en
  // todas las plataformas para que la cifra sea comparable.
  if (pwa.standalone) recordOnce(LAUNCH_RECORDED, 'launched');

  pwa.ready = true;
}

/** Lanza el diálogo del navegador y cuenta lo que se decide. */
export async function promptInstall() {
  const event = pwa.deferredPrompt;
  if (!event) return null;
  const context = pwaContext();
  pwa.deferredPrompt = null;
  try {
    await event.prompt();
    const choice = await event.userChoice;
    const accepted = choice?.outcome === 'accepted';
    recordPwaEvent(accepted ? 'prompt_accepted' : 'prompt_dismissed', context);
    // `appinstalled` suele llegar enseguida, pero no siempre: la marca va ya.
    if (accepted) rememberInstalled(true);
    return accepted;
  } catch {
    return null;
  }
}
