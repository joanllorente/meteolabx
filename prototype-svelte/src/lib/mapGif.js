/**
 * Animación GIF del visor: un fotograma por hora, compuesto igual que el PNG.
 *
 * Cada fotograma lleva su propia paleta de 256 colores. Una paleta común para
 * toda la animación obligaría a tener todos los fotogramas en memoria antes de
 * cuantizar —medio centenar de lienzos de un millón de píxeles—, y como los
 * colores del mapa salen siempre de la misma escala, de una hora a otra apenas
 * cambian.
 */
// La build ESM: el paquete solo declara su CommonJS como entrada para Node.
import { GIFEncoder, applyPalette, quantize } from 'gifenc/dist/gifenc.esm.js';

/** Ancho máximo de la animación: más allá el fichero crece sin ganar nada. */
export const ANCHO_MAXIMO_GIF = 1000;

/** Milisegundos de cada hora y de la última, que se queda un poco más. */
export const RETARDO_GIF = 600;
export const RETARDO_FINAL_GIF = 1600;

/** Tamaño del fotograma: el del lienzo, reducido si pasa del máximo. */
export function medidaGif(ancho, alto, maximo = ANCHO_MAXIMO_GIF) {
  if (ancho <= maximo) return { ancho, alto };
  return { ancho: maximo, alto: Math.round(alto * maximo / ancho) };
}

/**
 * Horas que entran en la animación, de `desde` a `hasta` ambas incluidas,
 * entre las que ya están calculadas. Si vienen al revés, se ordenan.
 */
export function horasDelGif(listas, desde, hasta) {
  const [inicio, fin] = desde <= hasta ? [desde, hasta] : [hasta, desde];
  return listas.filter((indice) => indice >= inicio && indice <= fin);
}

export function nuevoGif() {
  const gif = GIFEncoder();
  let fotogramas = 0;
  let medida = null;
  const reductor = document.createElement('canvas');
  return {
    get fotogramas() {
      return fotogramas;
    },
    /** Añade un lienzo como fotograma; todos salen al tamaño del primero. */
    anade(lienzo, retardo = RETARDO_GIF) {
      medida ||= medidaGif(lienzo.width, lienzo.height);
      reductor.width = medida.ancho;
      reductor.height = medida.alto;
      const ctx = reductor.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(lienzo, 0, 0, medida.ancho, medida.alto);
      const { data } = ctx.getImageData(0, 0, medida.ancho, medida.alto);
      const paleta = quantize(data, 256);
      const indices = applyPalette(data, paleta);
      gif.writeFrame(indices, medida.ancho, medida.alto, {
        palette: paleta,
        delay: retardo,
        // Bucle infinito; solo cuenta en el primer fotograma.
        repeat: 0
      });
      fotogramas += 1;
    },
    termina() {
      gif.finish();
      return new Blob([gif.bytes()], { type: 'image/gif' });
    }
  };
}
