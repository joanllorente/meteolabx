/**
 * El frontend no puede cambiar ni una coma de lo que Google ya tiene indexado.
 *
 * `scripts/export_seo_parity_fixture.py` congela la salida del generador
 * Python; aquí se comprueba que los constructores de metadatos en JavaScript
 * la reproducen exactamente. Si alguien retoca un texto en `seo_pages_i18n.py`
 * y no vuelve a exportar, este test lo caza.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { stationMeta, stationLanguages, observationUrl } from '../src/lib/seo/station.js';
import { displayName, sensorLabel, t } from '../src/lib/seo/i18n.js';

const fixture = JSON.parse(
  readFileSync(new URL('./fixtures/seo-parity.generated.json', import.meta.url), 'utf8')
);

test('el nombre visible coincide con _display_name de Python', () => {
  for (const item of fixture.cases) {
    assert.equal(displayName(item.station.name), item.display_name, item.url_slug);
  }
});

test('cada ficha se publica en los mismos idiomas que la estática', () => {
  for (const item of fixture.cases) {
    // El conjunto lo decide el país; el orden, el diccionario de idiomas.
    assert.deepEqual(
      [...stationLanguages(item.station)].sort(),
      [...item.language_codes].sort(),
      item.url_slug
    );
    assert.deepEqual(stationLanguages(item.station), item.alternate_order, item.url_slug);
  }
});

test('título, descripción y entradilla se reproducen carácter a carácter', () => {
  for (const item of fixture.cases) {
    for (const [language, expected] of Object.entries(item.languages)) {
      const meta = stationMeta(item.station, language, item.url_slug);
      const where = `${item.url_slug} · ${language}`;
      assert.equal(meta.title, expected.title, where);
      assert.equal(meta.description, expected.description, where);
      assert.equal(meta.lede, expected.lede, where);
      assert.equal(meta.location, expected.location, where);
      assert.equal(meta.searchName, expected.search_name, where);
    }
  }
});

test('la lista de sensores se traduce igual', () => {
  for (const item of fixture.cases) {
    const keys = Object.keys(item.station.sensors || {});
    for (const [language, expected] of Object.entries(item.languages)) {
      const rendered =
        keys.map((key) => sensorLabel(language, key)).join(', ') ||
        t(language, 'sensor_unknown');
      assert.equal(rendered, expected.sensors, `${item.url_slug} · ${language}`);
    }
  }
});

test('canonical y alternates apuntan a las URLs nuevas', () => {
  for (const item of fixture.cases) {
    for (const language of item.language_codes) {
      const meta = stationMeta(item.station, language, item.url_slug);
      assert.equal(
        meta.canonical,
        `${fixture.site_url}/${language}/observation/${item.url_slug}`
      );
      assert.deepEqual(
        meta.alternates.map((entry) => entry.code),
        item.alternate_order
      );
      assert.equal(meta.xDefault, observationUrl(item.language_codes.includes('es') ? 'es' : item.language_codes[0], item.url_slug));
    }
  }
});

test('los datos estructurados mantienen BreadcrumbList y Place', () => {
  const item = fixture.cases[0];
  const meta = stationMeta(item.station, 'es', item.url_slug);
  const [breadcrumb, place] = meta.structuredData;
  assert.equal(breadcrumb['@type'], 'BreadcrumbList');
  assert.equal(breadcrumb.itemListElement.length, 3);
  assert.equal(breadcrumb.itemListElement[2].item, meta.canonical);
  assert.equal(place['@type'], 'Place');
  assert.equal(place.name, item.languages.es.place_name);
  assert.equal(place.identifier, item.station.station_id);
  assert.equal(place.geo.elevation, item.station.elevation);
});
