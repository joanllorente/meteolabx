/**
 * La raíz del sitio no lleva idioma en la URL y hay que decidirlo. El
 * navegador manda una lista con prioridades, no un idioma suelto: leerla
 * entera es lo que hace que un visitante ruso con inglés de segunda reciba
 * inglés en vez del idioma por defecto.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const SUPPORTED = ['es', 'ca', 'en', 'fr', 'it', 'pt'];
const { negotiateLanguage, parseAcceptLanguage } = await import('../src/lib/server/language.js');

const negotiate = (header) => negotiateLanguage(header, SUPPORTED);

test('se respeta el orden de preferencia del navegador', () => {
  assert.equal(negotiate('ca,es;q=0.9,en;q=0.8'), 'ca');
  assert.equal(negotiate('en-GB,en;q=0.9'), 'en');
});

test('la q manda sobre el orden de escritura', () => {
  assert.equal(negotiate('es;q=0.5,fr;q=0.9'), 'fr');
});

test('las variantes regionales cuentan como su idioma', () => {
  assert.equal(negotiate('pt-BR'), 'pt');
  assert.equal(negotiate('es-419,es-MX;q=0.9'), 'es');
});

test('un navegador en ruso con inglés de segunda recibe inglés', () => {
  assert.equal(negotiate('ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'), 'en');
});

test('sin ningún idioma del sitio se cae al inglés, no al castellano', () => {
  // Quien no pide ninguno de los seis viene de fuera de su ámbito.
  assert.equal(negotiate('ru-RU,ru;q=0.9'), 'en');
  assert.equal(negotiate(''), 'en');
  assert.equal(negotiate(null), 'en');
});

test('q=0 significa «este no»', () => {
  assert.equal(negotiate('es;q=0,fr;q=0.4'), 'fr');
});

test('parseAcceptLanguage ordena por calidad', () => {
  assert.deepEqual(
    parseAcceptLanguage('es;q=0.5,fr,it;q=0.8').map((entry) => entry.tag),
    ['fr', 'it', 'es']
  );
});
