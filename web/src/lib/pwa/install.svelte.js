/**
 * Estado compartido de la instalación de la PWA.
 *
 * Chrome y Edge avisan de que la web se puede instalar con
 * `beforeinstallprompt`, una sola vez y al cargar la página, esté donde esté
 * el visitante. Si solo lo escuchara la tarjeta de la ficha, quien entra por
 * el mapa y luego abre una estación ya se lo habría perdido. Por eso lo
 * captura el layout y la tarjeta lo lee de aquí.
 */
import { deviceKind, recordPwaEvent } from '$lib/stats.js';

import { detectPlatform, installMethod } from './platform.js';

const LAUNCH_RECORDED = 'mlx-pwa-launch-recorded';

export const pwa = $state({
  ready: false,
  platform: null,
  standalone: false,
  /** @type {any} */
  deferredPrompt: null,
  installedNow: false
});

export function currentMethod() {
  if (!pwa.platform) return null;
  if (pwa.installedNow) return 'installed';
  return installMethod(pwa.platform, {
    canPrompt: Boolean(pwa.deferredPrompt),
    standalone: pwa.standalone
  });
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
  });

  window.addEventListener('appinstalled', () => {
    pwa.deferredPrompt = null;
    pwa.installedNow = true;
    recordPwaEvent('installed', pwaContext());
  });

  // Primera apertura de la app instalada. Es la única instalación que se ve
  // en iPhone y iPad, donde Safari no avisa de nada al añadirla; se cuenta en
  // todas las plataformas para que la cifra sea comparable.
  if (pwa.standalone) {
    try {
      if (!localStorage.getItem(LAUNCH_RECORDED)) {
        localStorage.setItem(LAUNCH_RECORDED, String(Math.floor(Date.now() / 1000)));
        recordPwaEvent('launched', pwaContext());
      }
    } catch {
      /* sin almacenamiento no se puede saber si es la primera: no se cuenta */
    }
  }

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
    return accepted;
  } catch {
    return null;
  }
}
