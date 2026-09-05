/**
 * Descarga de una gráfica como PNG.
 *
 * Las gráficas son SVG en línea, y su aspecto vive en hojas de estilo del
 * documento: clases de Svelte y variables de tema —`var(--ink)`,
 * `var(--grid-line)`—. Un SVG serializado se convierte en un documento
 * aislado que no ve nada de eso, así que sale en negro sobre transparente.
 * Por eso aquí se clona, se le fijan los estilos ya calculados y se le pinta
 * un fondo antes de rasterizar: lo que se descarga es lo que se ve.
 *
 * Sin dependencias: no hay librería de exportación, solo canvas.
 */

// Lo que hay que fijar para que el dibujo sobreviva fuera del documento.
const PAINTED = [
  'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin', 'opacity',
  'font-family', 'font-size', 'font-weight', 'font-variant-numeric',
  'letter-spacing', 'text-anchor', 'dominant-baseline', 'paint-order'
];

/** Píxeles por unidad del viewBox. Dos bastan para que el texto no pixele. */
const SCALE = 2;

function inlineStyles(source, clone) {
  const originals = [source, ...source.querySelectorAll('*')];
  const copies = [clone, ...clone.querySelectorAll('*')];
  originals.forEach((node, index) => {
    const copy = copies[index];
    if (!copy) return;
    const computed = getComputedStyle(node);
    let declaration = '';
    for (const property of PAINTED) {
      const value = computed.getPropertyValue(property);
      // `none` se copia igual que cualquier otro valor: en un trazo, `fill:
      // none` es justo lo que evita que la línea salga rellena de negro.
      if (value) declaration += `${property}:${value};`;
    }
    if (declaration) copy.setAttribute('style', declaration);
  });
}

/** Dimensiones del viewBox: son las del dibujo, sin depender del zoom. */
function boxOf(svg) {
  const box = svg.viewBox?.baseVal;
  if (box?.width && box?.height) return { width: box.width, height: box.height };
  const rect = svg.getBoundingClientRect();
  return { width: rect.width || 620, height: rect.height || 200 };
}

/**
 * Copia lista para rasterizar: estilos fijados, fondo opaco y tamaño real.
 *
 * El fondo se toma del panel donde vive la gráfica, así que un PNG bajado en
 * modo oscuro sale oscuro y en claro, claro.
 */
function standalone(svg, background) {
  const { width, height } = boxOf(svg);
  const clone = svg.cloneNode(true);
  inlineStyles(svg, clone);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('width', String(width));
  clone.setAttribute('height', String(height));

  const backdrop = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  backdrop.setAttribute('x', '0');
  backdrop.setAttribute('y', '0');
  backdrop.setAttribute('width', String(width));
  backdrop.setAttribute('height', String(height));
  backdrop.setAttribute('fill', background);
  clone.insertBefore(backdrop, clone.firstChild);

  return { markup: new XMLSerializer().serializeToString(clone), width, height };
}

/** Color de fondo heredado: el primer ancestro que pinte algo. */
function backgroundOf(element) {
  let node = element.parentElement;
  while (node) {
    const colour = getComputedStyle(node).backgroundColor;
    if (colour && colour !== 'transparent' && !colour.startsWith('rgba(0, 0, 0, 0')) {
      return colour;
    }
    node = node.parentElement;
  }
  return '#ffffff';
}

/** `Tendencia de θe · Can Bruixa` → `tendencia-de-θe-can-bruixa`. */
export function fileSlug(text) {
  return (
    String(text || 'grafica')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80) || 'grafica'
  );
}

/**
 * Rasteriza el SVG y lo ofrece como descarga.
 *
 * Devuelve `false` si el navegador no ha podido pintarlo, para que quien
 * llame decida qué contar. No lanza: una descarga que falla no puede tumbar
 * la página.
 */
export async function downloadChartPng(svg, name) {
  if (!svg || typeof document === 'undefined') return false;
  const { markup, width, height } = standalone(svg, backgroundOf(svg));
  const source = URL.createObjectURL(
    new Blob([markup], { type: 'image/svg+xml;charset=utf-8' })
  );
  try {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error('svg_no_rasterizable'));
      image.src = source;
    });

    const canvas = document.createElement('canvas');
    canvas.width = Math.round(width * SCALE);
    canvas.height = Math.round(height * SCALE);
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!blob) return false;

    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${fileSlug(name)}.png`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // El objeto se libera cuando el navegador ya ha empezado la descarga.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return true;
  } catch {
    return false;
  } finally {
    URL.revokeObjectURL(source);
  }
}
