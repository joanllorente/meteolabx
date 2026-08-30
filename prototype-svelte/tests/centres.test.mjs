/** La medida de cierre de un centro de presión. */

import assert from 'node:assert/strict';
import test from 'node:test';

import { closureDepth } from '../src/lib/pressureCentres.js';

/**
 * Perfil en 1D metido en una banda, con paredes altas arriba y abajo para que
 * la inundación no se escape por el borde antes de tiempo.
 *
 *   980  990 …sube hasta 1000 en el collado… baja al mínimo de 992 y vuelve a subir
 *
 * Desde el 992 el agua tiene que remontar el collado de 1000 para encontrar el
 * 990 de la izquierda, así que el cierre es de 8 hPa.
 */
function banda() {
  const perfil = [
    980, 990, 995, 998, 1000, 999, 998, 997, 996, 993,
    992, 993, 996, 997, 998, 1000, 1002, 1003, 1004, 1005, 1006
  ];
  const ancho = perfil.length;
  const alto = 5;
  const campo = new Float32Array(ancho * alto);
  for (let fila = 0; fila < alto; fila += 1) {
    for (let columna = 0; columna < ancho; columna += 1) {
      campo[fila * ancho + columna] = fila === 0 || fila === alto - 1 ? 1010 : perfil[columna];
    }
  }
  return { campo, ancho, alto };
}

test('el cierre mide hasta dónde subió el agua, no el último paso', () => {
  // El nivel se asignaba con cada celda que salía del montón, y salen celdas
  // por debajo del frente: al destapar la bajada del collado el nivel caía y
  // la profundidad devuelta era la de ese último paso. Una borrasca con miles
  // de celdas cerradas se anunciaba con centésimas de hectopascal y se
  // descartaba por poco marcada.
  const { campo, ancho, alto } = banda();
  const cierre = closureDepth(campo, ancho, alto, { x: 10, y: 2 }, -1);
  assert.equal(cierre.open, false, 'debería cerrar contra terreno más hondo');
  assert.ok(
    Math.abs(cierre.depth - 8) < 1e-6,
    `cierre de ${cierre.depth.toFixed(2)} hPa en vez de los 8 del collado`
  );
});

test('un mínimo sin collado que remontar apenas cierra', () => {
  // Control: si el terreno baja nada más salir, el cierre es pequeño de verdad.
  const { campo, ancho, alto } = banda();
  const cierre = closureDepth(campo, ancho, alto, { x: 19, y: 2 }, 1);
  assert.ok(cierre.depth < 8, `cierre de ${cierre.depth.toFixed(2)} hPa`);
});
