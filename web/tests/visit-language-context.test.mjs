import test from 'node:test';
import assert from 'node:assert/strict';
import { visitLanguageContext } from '../src/lib/stats.js';

test('contrasta idioma del navegador con URL sin enviar estación ni filtros', () => {
  assert.deepEqual(visitLanguageContext({ languages: ['es-ES', 'es', 'en'] }, { pathname: '/fr/observation/navalvillar', search: '?secret=private' }),
    { browser_languages: 'es-ES,es,en', url_language: 'fr' });
});
test('sin navigator.languages usa language y no inventa datos ausentes', () => {
  assert.deepEqual(visitLanguageContext({ language: 'it-IT' }, { pathname: '/es/map' }), { browser_languages: 'it-IT', url_language: 'es' });
  assert.deepEqual(visitLanguageContext({}, {}), { browser_languages: '', url_language: '' });
});
test('solo envía etiquetas de idioma válidas y limita el número', () => {
  assert.deepEqual(visitLanguageContext({ languages: ['es', 'es', 'secret@example.com', '/private', 'pt-BR'] }, { pathname: '/stats' }),
    { browser_languages: 'es,pt-BR', url_language: '' });
});
