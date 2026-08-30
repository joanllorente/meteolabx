/**
 * Exporta el visor a PNG tal y como se está viendo.
 *
 * No se rasteriza el DOM: se compone sobre un canvas leyendo la geometría real
 * de cada pieza con `getBoundingClientRect`, que ya trae aplicadas las
 * transformaciones. Así el encuadre del usuario —su zoom y su arrastre— sale
 * exacto sin tener que sacar ese estado del componente, y los controles no hay
 * que esconderlos: simplemente no se pintan, de modo que la exportación no
 * provoca ningún parpadeo en pantalla.
 *
 * Lo que entra: la barra del título con el mapa y la hora, el campo, las
 * isolíneas y sus rótulos, la marca de agua y la leyenda. Lo que no: botones de
 * pantalla completa, de zoom, casillas de capas, selector de unidades y el
 * globo del cursor.
 */

/** Propiedades que hay que llevarse al SVG serializado. */
const ESTILOS_SVG = [
  'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin',
  'opacity', 'vector-effect', 'paint-order', 'display', 'visibility',
  'font-family', 'font-size', 'font-weight', 'font-style', 'letter-spacing',
  'text-anchor', 'dominant-baseline'
];

function caja(elemento, origen) {
  const r = elemento.getBoundingClientRect();
  return { x: r.left - origen.left, y: r.top - origen.top, width: r.width, height: r.height };
}

function radios(estilo, altura) {
  const valor = parseFloat(estilo.borderTopLeftRadius) || 0;
  return Math.min(valor, altura / 2);
}

function rectangulo(ctx, { x, y, width, height }, radio) {
  ctx.beginPath();
  if (radio > 0 && ctx.roundRect) ctx.roundRect(x, y, width, height, radio);
  else ctx.rect(x, y, width, height);
}

/** Fondo y borde de un elemento, con sus esquinas redondeadas. */
function pintaFondo(ctx, elemento, origen) {
  const estilo = getComputedStyle(elemento);
  const rect = caja(elemento, origen);
  if (!rect.width || !rect.height) return rect;
  const radio = radios(estilo, rect.height);
  const fondo = estilo.backgroundColor;
  if (fondo && fondo !== 'rgba(0, 0, 0, 0)' && fondo !== 'transparent') {
    ctx.fillStyle = fondo;
    rectangulo(ctx, rect, radio);
    ctx.fill();
  }
  pintaBordes(ctx, estilo, rect, radio);
  return rect;
}

/**
 * Bordes, lado a lado.
 *
 * No todos son un marco: la cabecera del visor solo lleva la línea de abajo, y
 * dibujarla como recuadro completo le habría puesto un cerco que no tiene.
 */
function pintaBordes(ctx, estilo, rect, radio) {
  const lados = ['Top', 'Right', 'Bottom', 'Left'].map((lado) => ({
    ancho: estilo[`border${lado}Style`] === 'none' ? 0 : parseFloat(estilo[`border${lado}Width`]) || 0,
    color: estilo[`border${lado}Color`],
    lado
  }));
  if (!lados.some((item) => item.ancho > 0)) return;
  const uniforme = lados.every(
    (item) => item.ancho === lados[0].ancho && item.color === lados[0].color
  );
  if (uniforme) {
    const ancho = lados[0].ancho;
    ctx.strokeStyle = lados[0].color;
    ctx.lineWidth = ancho;
    rectangulo(ctx, {
      x: rect.x + ancho / 2, y: rect.y + ancho / 2,
      width: rect.width - ancho, height: rect.height - ancho
    }, Math.max(0, radio - ancho / 2));
    ctx.stroke();
    return;
  }
  const trazos = {
    Top: [rect.x, rect.y, rect.x + rect.width, rect.y],
    Bottom: [rect.x, rect.y + rect.height, rect.x + rect.width, rect.y + rect.height],
    Left: [rect.x, rect.y, rect.x, rect.y + rect.height],
    Right: [rect.x + rect.width, rect.y, rect.x + rect.width, rect.y + rect.height]
  };
  for (const { ancho, color, lado } of lados) {
    if (ancho <= 0) continue;
    const [x1, y1, x2, y2] = trazos[lado];
    const medio = ancho / 2;
    const dx = lado === 'Left' ? medio : lado === 'Right' ? -medio : 0;
    const dy = lado === 'Top' ? medio : lado === 'Bottom' ? -medio : 0;
    ctx.strokeStyle = color;
    ctx.lineWidth = ancho;
    ctx.beginPath();
    ctx.moveTo(x1 + dx, y1 + dy);
    ctx.lineTo(x2 + dx, y2 + dy);
    ctx.stroke();
  }
}

/**
 * Texto de un elemento de una sola línea, centrado en su caja.
 *
 * La caja ya la ha medido el navegador con la fuente real, así que centrar en
 * vertical y arrancar por la izquierda reproduce la línea sin tener que
 * replicar la maquetación.
 */
function pintaTexto(ctx, elemento, origen) {
  const texto = (elemento.textContent || '').trim();
  if (!texto) return;
  const estilo = getComputedStyle(elemento);
  const rect = caja(elemento, origen);
  ctx.save();
  ctx.font = `${estilo.fontStyle} ${estilo.fontWeight} ${estilo.fontSize}/${estilo.fontSize} ${estilo.fontFamily}`;
  ctx.fillStyle = estilo.color;
  if ('letterSpacing' in ctx) ctx.letterSpacing = estilo.letterSpacing === 'normal' ? '0px' : estilo.letterSpacing;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(texto, rect.x, rect.y + rect.height / 2);
  ctx.restore();
}

function cargaImagen(url) {
  return new Promise((resolve, reject) => {
    const imagen = new Image();
    imagen.onload = () => resolve(imagen);
    imagen.onerror = () => reject(new Error('No se pudo preparar una capa del mapa.'));
    imagen.src = url;
  });
}

/**
 * Serializa la capa vectorial con sus estilos dentro.
 *
 * Las reglas CSS del componente no viajan con el SVG —quedan en la hoja de
 * estilos de la página—, así que cada nodo se lleva copiados los valores que
 * el navegador ya ha calculado para él. El viewBox se amplía del recuadro del
 * SVG al del mapa entero: al ampliar, las isolíneas se salen de su caja y en
 * pantalla siguen viéndose, porque el recorte lo hace el contenedor.
 */
/**
 * viewBox que lleva la capa vectorial de su propio recuadro al del mapa entero.
 *
 * El SVG está encajado dentro del área del mapa con un margen alrededor, pero
 * dibuja fuera de su caja: al ampliar, las isolíneas se salen y en pantalla se
 * siguen viendo porque quien recorta es el contenedor. Si se serializara con su
 * viewBox original, la imagen cortaría justo en el borde del recuadro y el PNG
 * saldría con un marco vacío que en pantalla no está.
 *
 * Como el mapeo es lineal —`preserveAspectRatio="none"`—, basta con convertir a
 * unidades de rejilla el rectángulo del área.
 */
export function viewBoxAmpliado(svgRect, areaRect, anchoUsuario, altoUsuario) {
  const porPixelX = anchoUsuario / svgRect.width;
  const porPixelY = altoUsuario / svgRect.height;
  return [
    (areaRect.x - svgRect.x) * porPixelX,
    (areaRect.y - svgRect.y) * porPixelY,
    areaRect.width * porPixelX,
    areaRect.height * porPixelY
  ];
}

async function capaVectorial(svg, areaRect, origen) {
  const svgRect = caja(svg, origen);
  if (!svgRect.width || !svgRect.height) return null;
  const [, , anchoUsuario, altoUsuario] = svg.getAttribute('viewBox').split(/\s+/).map(Number);
  const clon = svg.cloneNode(true);
  const fuente = svg.querySelectorAll('*');
  const copia = clon.querySelectorAll('*');
  for (let indice = 0; indice < fuente.length; indice += 1) {
    const estilo = getComputedStyle(fuente[indice]);
    copia[indice].setAttribute(
      'style',
      ESTILOS_SVG.map((propiedad) => `${propiedad}:${estilo.getPropertyValue(propiedad)}`).join(';')
    );
  }
  clon.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clon.setAttribute('width', areaRect.width);
  clon.setAttribute('height', areaRect.height);
  clon.setAttribute('preserveAspectRatio', 'none');
  clon.setAttribute(
    'viewBox',
    viewBoxAmpliado(svgRect, areaRect, anchoUsuario, altoUsuario).join(' ')
  );
  const texto = new XMLSerializer().serializeToString(clon);
  return cargaImagen(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(texto)}`);
}

/** Paradas de color de un `linear-gradient`, en el orden en que las declara. */
export function coloresDelDegradado(fondo) {
  return String(fondo || '').match(/rgba?\([^)]*\)|#[0-9a-f]{3,8}\b/gi) || [];
}

/** Barra de color continua: se rehace del degradado que declara el CSS. */
function pintaDegradado(ctx, elemento, origen) {
  const estilo = getComputedStyle(elemento);
  const rect = caja(elemento, origen);
  const colores = coloresDelDegradado(estilo.backgroundImage);
  if (!colores.length) return;
  const degradado = ctx.createLinearGradient(rect.x, rect.y, rect.x + rect.width, rect.y);
  colores.forEach((color, indice) => {
    degradado.addColorStop(colores.length > 1 ? indice / (colores.length - 1) : 0, color);
  });
  ctx.fillStyle = degradado;
  rectangulo(ctx, rect, radios(estilo, rect.height));
  ctx.fill();
}

/** Recorre un elemento pintando fondos y textos de todo lo que cuelga de él. */
function pintaRama(ctx, raiz, origen, excluir = []) {
  if (!raiz || excluir.some((selector) => raiz.matches?.(selector))) return;
  const hijos = [...raiz.children].filter(
    (hijo) => !excluir.some((selector) => hijo.matches(selector))
  );
  pintaFondo(ctx, raiz, origen);
  if (raiz.tagName === 'IMG') {
    const rect = caja(raiz, origen);
    ctx.save();
    rectangulo(ctx, rect, radios(getComputedStyle(raiz), rect.height));
    ctx.clip();
    const opacidad = Number(getComputedStyle(raiz).opacity);
    ctx.globalAlpha = Number.isFinite(opacidad) ? opacidad : 1;
    try {
      ctx.drawImage(raiz, rect.x, rect.y, rect.width, rect.height);
    } catch {
      // Un logo que no haya cargado no debe tumbar la exportación entera.
    }
    ctx.restore();
    return;
  }
  if (!hijos.length) {
    if (getComputedStyle(raiz).backgroundImage.includes('gradient')) pintaDegradado(ctx, raiz, origen);
    pintaTexto(ctx, raiz, origen);
    return;
  }
  const estilo = getComputedStyle(raiz);
  // Las bandas de la leyenda viven dentro de una caja con las esquinas
  // redondeadas y `overflow:hidden`: sin recortar, la primera y la última
  // sobresaldrían por las puntas.
  const recorta = estilo.overflow === 'hidden' && parseFloat(estilo.borderTopLeftRadius) > 0;
  if (recorta) {
    const rect = caja(raiz, origen);
    ctx.save();
    rectangulo(ctx, rect, radios(estilo, rect.height));
    ctx.clip();
  }
  for (const hijo of hijos) pintaRama(ctx, hijo, origen, excluir);
  if (recorta) ctx.restore();
}

/**
 * Compone el PNG y lo descarga.
 *
 * `tarjeta` es la tarjeta entera del visor: cabecera más mapa.
 */
export async function exportarMapaPng(tarjeta, { nombre = 'mapa', escala = 2 } = {}) {
  const origen = tarjeta.getBoundingClientRect();
  const cabecera = tarjeta.querySelector('.map-head');
  const area = tarjeta.querySelector('.forecast-map');
  const raster = area?.querySelector('canvas.grid-raster');
  const vector = area?.querySelector('svg.vector-overlay');
  const superficie = area?.querySelector('.map-surface');
  if (!area || !raster) throw new Error('El mapa todavía no está en pantalla.');

  // La imagen acaba donde acaba el mapa. La tarjeta sigue hacia abajo con el
  // deslizador de horas, que es un control: recortando ahí no queda la franja
  // en blanco de una fila que en el PNG no pinta nada.
  const areaCaja = caja(area, origen);
  const lienzo = document.createElement('canvas');
  lienzo.width = Math.round(origen.width * escala);
  lienzo.height = Math.round((areaCaja.y + areaCaja.height) * escala);
  const ctx = lienzo.getContext('2d');
  ctx.scale(escala, escala);

  // Fondo de la tarjeta y cabecera, sin los botones.
  pintaFondo(ctx, tarjeta, origen);
  if (cabecera) pintaRama(ctx, cabecera, origen, ['.map-actions']);

  const areaRect = pintaFondo(ctx, area, origen);
  ctx.save();
  rectangulo(ctx, areaRect, 0);
  ctx.clip();

  // La hoja del mapa puede llevar fondo propio y recortar lo que se sale de
  // ella; si lo hace, el PNG tiene que hacer lo mismo o enseñaría un encuadre
  // que en pantalla no se ve.
  if (superficie) {
    const hoja = pintaFondo(ctx, superficie, origen);
    if (getComputedStyle(superficie).overflow === 'hidden') {
      rectangulo(ctx, hoja, 0);
      ctx.clip();
    }
  }

  // El campo. Su caja ya viene transformada por el encuadre del usuario, así
  // que basta con dibujarlo donde el navegador lo está poniendo.
  const rasterRect = caja(raster, origen);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(raster, rasterRect.x, rasterRect.y, rasterRect.width, rasterRect.height);
  ctx.imageSmoothingEnabled = true;

  if (vector) {
    const imagen = await capaVectorial(vector, areaRect, origen);
    if (imagen) ctx.drawImage(imagen, areaRect.x, areaRect.y, areaRect.width, areaRect.height);
  }

  const marca = area.querySelector('.map-watermark');
  if (marca) pintaRama(ctx, marca, origen);
  const leyenda = area.querySelector('.legend');
  if (leyenda) pintaRama(ctx, leyenda, origen, ['.unit-picker']);
  ctx.restore();

  const blob = await new Promise((resolve) => lienzo.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('El navegador no ha podido generar el PNG.');
  const url = URL.createObjectURL(blob);
  const enlace = document.createElement('a');
  enlace.href = url;
  enlace.download = `${nombre}.png`;
  enlace.click();
  // Se libera al final del ciclo, cuando la descarga ya tiene el blob.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
