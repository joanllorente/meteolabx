/**
 * Elige tinta blanca o negra según lo que haya debajo.
 *
 * La marca de agua se imprime encima del campo, y el campo cambia de color con
 * la hora, con el producto y con el encuadre: cualquier tono fijo acaba
 * perdiéndose contra un fondo del mismo valor. Vive aparte del componente
 * porque es aritmética de color y así se puede probar.
 */

/** Umbral de luminancia, en 0–255, a partir del cual conviene tinta oscura. */
export const UMBRAL_TINTA = 140;
export const TINTA_OSCURA = '#101820';
export const TINTA_CLARA = '#ffffff';

/** Componentes de un `rgb()` o `rgba()` calculado por el navegador. */
export function colorDeFondo(texto) {
  const partes = String(texto ?? '').match(/[\d.]+/g);
  if (!partes || partes.length < 3) return [255, 255, 255];
  return [Number(partes[0]), Number(partes[1]), Number(partes[2])];
}

/** Color translúcido resuelto contra el fondo que tiene detrás. */
export function mezclaSobre([r, g, b, a], fondo) {
  const alfa = a === undefined ? 1 : a;
  return [
    r * alfa + fondo[0] * (1 - alfa),
    g * alfa + fondo[1] * (1 - alfa),
    b * alfa + fondo[2] * (1 - alfa)
  ];
}

/**
 * Tinta legible sobre la media de unas muestras de color.
 *
 * La luminancia va ponderada: el verde pesa mucho más que el azul, y sin
 * ponderarlo un campo verde claro salía «oscuro» y se rotulaba en blanco.
 */
export function tintaLegible(muestras) {
  if (!muestras?.length) return null;
  let suma = 0;
  for (const [r, g, b] of muestras) suma += 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return suma / muestras.length > UMBRAL_TINTA ? TINTA_OSCURA : TINTA_CLARA;
}
