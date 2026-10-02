import test from 'node:test';
import assert from 'node:assert/strict';
// La build ESM: el paquete solo declara su CommonJS como entrada para Node.
import { GIFEncoder, applyPalette, quantize } from 'gifenc/dist/gifenc.esm.js';
import { ANCHO_MAXIMO_GIF, horasDelGif, medidaGif } from '../src/lib/mapGif.js';

test('solo entran las horas calculadas del tramo elegido, en orden', () => {
  assert.deepEqual(horasDelGif([0, 1, 2, 4, 5, 8], 1, 5), [1, 2, 4, 5]);
  // Desde y hasta al revés: el mismo tramo.
  assert.deepEqual(horasDelGif([0, 1, 2, 4, 5, 8], 5, 1), [1, 2, 4, 5]);
  assert.deepEqual(horasDelGif([0, 1, 2], 2, 2), [2]);
});

test('el fotograma se reduce al ancho máximo conservando la proporción', () => {
  assert.deepEqual(medidaGif(800, 600), { ancho: 800, alto: 600 });
  assert.deepEqual(medidaGif(2000, 1000), { ancho: ANCHO_MAXIMO_GIF, alto: 500 });
});

test('gifenc produce un GIF animado válido', () => {
  const gif = GIFEncoder();
  for (const color of [[255, 0, 0], [0, 0, 255]]) {
    const data = new Uint8ClampedArray(4 * 4 * 4);
    for (let i = 0; i < data.length; i += 4) data.set([...color, 255], i);
    const paleta = quantize(data, 256);
    gif.writeFrame(applyPalette(data, paleta), 4, 4, { palette: paleta, delay: 600, repeat: 0 });
  }
  gif.finish();
  const bytes = gif.bytes();
  assert.equal(String.fromCharCode(...bytes.slice(0, 6)), 'GIF89a');
  assert.equal(bytes.at(-1), 0x3b);
});
