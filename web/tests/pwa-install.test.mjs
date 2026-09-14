/**
 * Cómo se instala MeteoLabX según el aparato: con «user agents» reales de
 * cada combinación, porque es ahí donde se equivoca una detección.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { INSTALL_METHODS, detectPlatform, installMethod } from '../src/lib/pwa/platform.js';
import { INSTALL_LANGUAGES, installText } from '../src/lib/pwa/install-i18n.js';
import { LANGUAGE_CODES } from '../src/lib/seo/i18n.js';

const UA = {
  iphoneSafari:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1',
  iphoneChrome:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/138.0.7204.119 Mobile/15E148 Safari/604.1',
  iphoneFirefox:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/141.0 Mobile/15E148 Safari/605.1.15',
  iphoneInstagram:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 390.0.0.28.85',
  iphoneGoogleApp:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/378.0.0 Mobile/15E148 Safari/604.1',
  // iPadOS se hace pasar por un Mac.
  ipadSafari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15',
  androidChrome:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36',
  androidTabletChrome:
    'Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
  androidSamsung:
    'Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.36',
  androidFirefox: 'Mozilla/5.0 (Android 14; Mobile; rv:141.0) Gecko/141.0 Firefox/141.0',
  androidWebView:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/139.0.0.0 Mobile Safari/537.36',
  macSafari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15',
  macChrome:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
  macFirefox: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:141.0) Gecko/20100101 Firefox/141.0',
  windowsEdge:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0',
  windowsChrome:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
  windowsFirefox: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0',
  linuxChrome:
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
  chromebook:
    'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36'
};

function caso(ua, touch = 0, state = {}) {
  const platform = detectPlatform({ userAgent: ua, maxTouchPoints: touch });
  return { ...platform, method: installMethod(platform, state) };
}

test('iPhone: Safari y los demás navegadores por el menú de compartir', () => {
  assert.deepEqual(caso(UA.iphoneSafari), { os: 'ios', device: 'mobile', browser: 'safari', inApp: false, method: 'ios-safari' });
  assert.equal(caso(UA.iphoneChrome).browser, 'chrome');
  assert.equal(caso(UA.iphoneChrome).method, 'ios-share');
  assert.equal(caso(UA.iphoneFirefox).method, 'ios-share');
});

test('dentro de Instagram o de la app de Google hay que salir al navegador', () => {
  assert.equal(caso(UA.iphoneInstagram).method, 'open-browser');
  assert.equal(caso(UA.iphoneGoogleApp).method, 'open-browser');
  assert.equal(caso(UA.androidWebView).method, 'open-browser');
});

test('un iPad que se anuncia como Mac se reconoce por la pantalla táctil', () => {
  assert.deepEqual(caso(UA.ipadSafari, 5), { os: 'ipados', device: 'tablet', browser: 'safari', inApp: false, method: 'ios-safari' });
  // El mismo «user agent» sin pantalla táctil es un Mac de verdad.
  assert.deepEqual(caso(UA.macSafari, 0), { os: 'macos', device: 'desktop', browser: 'safari', inApp: false, method: 'mac-safari' });
});

test('Android: móvil o tableta, y cada navegador con su menú', () => {
  assert.deepEqual(caso(UA.androidChrome), { os: 'android', device: 'mobile', browser: 'chrome', inApp: false, method: 'android-menu' });
  assert.equal(caso(UA.androidTabletChrome).device, 'tablet');
  assert.equal(caso(UA.androidSamsung).browser, 'samsung');
  assert.equal(caso(UA.androidSamsung).method, 'android-samsung');
  assert.equal(caso(UA.androidFirefox).method, 'android-firefox');
});

test('escritorio: Chrome y Edge instalan, Firefox no', () => {
  assert.deepEqual(caso(UA.windowsEdge), { os: 'windows', device: 'desktop', browser: 'edge', inApp: false, method: 'desktop-chromium' });
  assert.equal(caso(UA.windowsChrome).method, 'desktop-chromium');
  assert.equal(caso(UA.macChrome).method, 'desktop-chromium');
  assert.equal(caso(UA.linuxChrome).os, 'linux');
  assert.equal(caso(UA.chromebook).os, 'chromeos');
  assert.equal(caso(UA.windowsFirefox).method, 'unsupported');
  assert.equal(caso(UA.macFirefox).method, 'unsupported');
});

test('el diálogo del navegador manda sobre las instrucciones, y dentro de la app no se ofrece nada', () => {
  assert.equal(caso(UA.androidChrome, 5, { canPrompt: true }).method, 'prompt');
  assert.equal(caso(UA.windowsEdge, 0, { canPrompt: true }).method, 'prompt');
  assert.equal(caso(UA.iphoneSafari, 5, { standalone: true }).method, 'installed');
  // Una app que no deja instalar no ofrece diálogo aunque lo pareciera.
  assert.equal(caso(UA.androidWebView, 5, { canPrompt: true }).method, 'open-browser');
});

test('todos los idiomas de la web tienen instrucciones para cada forma de instalar', () => {
  assert.deepEqual([...INSTALL_LANGUAGES].sort(), [...LANGUAGE_CODES].sort());
  const conPasos = INSTALL_METHODS.filter((method) => !['installed', 'prompt'].includes(method));
  for (const language of INSTALL_LANGUAGES) {
    const text = installText(language);
    for (const key of ['title', 'subtitle', 'install', 'how', 'hide', 'dismiss', 'installed']) {
      assert.ok(text[key], `${language}.${key}`);
    }
    for (const method of conPasos) {
      assert.ok(text.steps[method]?.length >= 2, `${language}: ${method}`);
    }
  }
});

test('en iPhone y iPad se dibuja el botón Compartir y el de añadir, en todos los idiomas', () => {
  for (const language of INSTALL_LANGUAGES) {
    for (const method of ['ios-safari', 'ios-share']) {
      const pasos = installText(language).steps[method].join(' ');
      assert.match(pasos, /\{share\}/, `${language}: ${method}`);
      assert.match(pasos, /\{add\}/, `${language}: ${method}`);
    }
  }
});
