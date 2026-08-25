<script>
  import { Locate, Minus, Plus } from '@lucide/svelte';

  let { frame, productLabel, resetKey = 0 } = $props();

  const defaultPalette = ['#3b4cc0','#3288bd','#66c2a5','#abdda4','#e6f598','#fee08b','#fdae61','#f46d43','#d73027','#762a83'];
  const precipitationPalette = ['#28465f','#2f6f8e','#369aa1','#58bd91','#9bd275','#d7dc69','#f2c55a','#ed914c','#df6262','#b44f88'];
  const LUT_SIZE = 256;
  // El buffer de ImageData es RGBA en orden de memoria; escribirlo como Uint32
  // exige conocer el orden de bytes de la máquina para componer la paleta.
  const littleEndian = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
  const lutCache = new Map();
  let layer;
  let surface;
  let raster;
  let hover = $state(null);
  let zoom = $state(1);
  let panX = $state(0);
  let panY = $state(0);
  let dragging = $state(false);
  let dragStart = null;
  // Encuadre asentado: alimenta los cálculos caros (flechas, streamlines) y
  // solo sigue al gesto cuando este se detiene.
  let viewZoom = $state(1);
  let viewPanX = $state(0);
  let viewPanY = $state(0);
  let dragFrame = 0;
  let settleTimer = 0;
  let pendingPan = null;

  function settleViewport() {
    window.clearTimeout(settleTimer);
    settleTimer = 0;
    viewZoom = zoom;
    viewPanX = panX;
    viewPanY = panY;
  }

  function scheduleSettle() {
    window.clearTimeout(settleTimer);
    settleTimer = window.setTimeout(settleViewport, 160);
  }

  function rgb(hex) {
    return [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
  }

  function packColor(red, green, blue, alpha) {
    return littleEndian
      ? ((alpha << 24) | (blue << 16) | (green << 8) | red) >>> 0
      : ((red << 24) | (green << 16) | (blue << 8) | alpha) >>> 0;
  }

  /** Paleta interpolada a 256 entradas: evita releer los hex por píxel. */
  function paletteLut(palette, alpha) {
    const key = `${palette[0]}|${alpha}`;
    const cached = lutCache.get(key);
    if (cached) return cached;
    const channels = palette.map(rgb);
    const lut = new Uint32Array(LUT_SIZE);
    for (let index = 0; index < LUT_SIZE; index += 1) {
      const position = index / (LUT_SIZE - 1) * (palette.length - 1);
      const lower = Math.floor(position);
      const upper = Math.min(lower + 1, palette.length - 1);
      const fraction = position - lower;
      const a = channels[lower];
      const b = channels[upper];
      lut[index] = packColor(
        Math.round(a[0] + (b[0] - a[0]) * fraction),
        Math.round(a[1] + (b[1] - a[1]) * fraction),
        Math.round(a[2] + (b[2] - a[2]) * fraction),
        alpha
      );
    }
    lutCache.set(key, lut);
    return lut;
  }

  function renderGrid() {
    if (!frame || !raster) return;
    const { width, height, values } = frame;
    raster.width = width;
    raster.height = height;
    const context = raster.getContext('2d', { alpha: true });
    context.imageSmoothingEnabled = false;
    const pixels = context.createImageData(width, height);
    const canvas32 = new Uint32Array(pixels.data.buffer);
    const isPrecipitation = frame.product === 'precip-1h';
    const lut = paletteLut(isPrecipitation ? precipitationPalette : defaultPalette, 235);
    const last = LUT_SIZE - 1;
    // Precipitación: escala logarítmica para no aplastar las lluvias débiles.
    const logScale = isPrecipitation ? last / Math.log1p(frame.vmax) : 0;
    const linearScale = last / (frame.vmax - frame.vmin || 1);
    for (let index = 0; index < values.length; index += 1) {
      const value = values[index];
      if (!Number.isFinite(value)) continue;
      if (isPrecipitation) {
        if (value < .05) continue;
        const slot = Math.log1p(value) * logScale;
        canvas32[index] = lut[slot > last ? last : slot < 0 ? 0 : slot | 0];
      } else {
        const slot = (value - frame.vmin) * linearScale;
        canvas32[index] = lut[slot > last ? last : slot < 0 ? 0 : slot | 0];
      }
    }
    context.putImageData(pixels, 0, 0);
  }

  function makeBoundaryPaths() {
    if (!frame.boundaries?.length) return [];
    const [west, south, east, north] = frame.bounds;
    const paths = [];
    for (const region of frame.boundaries) {
      for (const ring of region.rings) {
        const points = ring.map(([longitude, latitude]) => [
          (longitude - west) / (east - west) * frame.width,
          (north - latitude) / (north - south) * frame.height
        ]);
        if (points.length) {
          paths.push({
            path: `M${points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join('L')}Z`,
            level: region.level || 'country'
          });
        }
      }
    }
    // Las divisiones interiores se dibujan primero para que la frontera nacional
    // conserve exactamente su grosor y color en costas y límites internacionales.
    return paths.sort((left, right) =>
      (left.level === 'country' ? 1 : 0) - (right.level === 'country' ? 1 : 0)
    );
  }

  function visibleSourceBounds() {
    const renderedWidth = surface?.clientWidth || frame.width;
    const renderedHeight = surface?.clientHeight || frame.height;
    // Se usa el encuadre asentado, no el del gesto en curso: integrar las
    // líneas de corriente en cada pointermove bloquea el hilo principal.
    const userPanX = viewPanX * frame.width / renderedWidth;
    const userPanY = viewPanY * frame.height / renderedHeight;
    const centerX = frame.width / 2;
    const centerY = frame.height / 2;
    const sourceX = (screenX) => centerX + (screenX - userPanX - centerX) / viewZoom;
    const sourceY = (screenY) => centerY + (screenY - userPanY - centerY) / viewZoom;
    // El margen mantiene glifos ya calculados fuera de cuadro, de modo que el
    // arrastre no descubre zonas vacías antes de que el encuadre se asiente.
    const marginX = frame.width * .3 / viewZoom;
    const marginY = frame.height * .3 / viewZoom;
    return {
      west: Math.max(0, sourceX(0) - marginX),
      east: Math.min(frame.width, sourceX(frame.width) + marginX),
      north: Math.max(0, sourceY(0) - marginY),
      south: Math.min(frame.height, sourceY(frame.height) + marginY)
    };
  }

  function clusteredVector(west, north, east, south) {
    const firstColumn = Math.max(0, Math.floor(west));
    const lastColumn = Math.min(frame.width - 1, Math.ceil(east));
    const firstRow = Math.max(0, Math.floor(north));
    const lastRow = Math.min(frame.height - 1, Math.ceil(south));
    const sampleStride = Math.max(1, Math.floor(Math.max(east - west, south - north) / 6));
    let sumU = 0;
    let sumV = 0;
    let sumX = 0;
    let sumY = 0;
    let count = 0;
    for (let row = firstRow; row <= lastRow; row += sampleStride) {
      for (let column = firstColumn; column <= lastColumn; column += sampleStride) {
        const index = row * frame.width + column;
        const u = frame.u[index];
        const v = frame.v[index];
        if (!Number.isFinite(frame.values[index]) || !Number.isFinite(u) || !Number.isFinite(v)) continue;
        sumU += u;
        sumV += v;
        sumX += column + .5;
        sumY += row + .5;
        count += 1;
      }
    }
    if (!count) return null;
    const u = sumU / count;
    const v = sumV / count;
    const magnitude = Math.hypot(u, v);
    if (magnitude < .5) return null;
    return { x: sumX / count, y: sumY / count, angle: Math.atan2(-v, u) * 180 / Math.PI };
  }

  function makeArrowGlyphs() {
    if (!frame.u || !frame.v || frame.product === 'wind-level') return { arrows: [], path: '' };
    const arrows = [];
    // Mantiene una densidad visual estable tanto en el dominio AROME completo
    // como al ampliar una región: unas 26 agrupaciones a lo ancho del visor.
    const baseStep = Math.max(4, frame.width / 26);
    const sourceStep = baseStep / viewZoom;
    const bounds = visibleSourceBounds();
    const firstColumn = Math.floor(bounds.west / sourceStep) * sourceStep;
    const firstRow = Math.floor(bounds.north / sourceStep) * sourceStep;
    for (let top = firstRow; top < bounds.south; top += sourceStep) {
      for (let left = firstColumn; left < bounds.east; left += sourceStep) {
        const vector = clusteredVector(left, top, left + sourceStep, top + sourceStep);
        if (vector) arrows.push(vector);
      }
    }
    const length = Math.max(3.8, baseStep * .58);
    const half = length / 2;
    const head = length * .3;
    return {
      arrows,
      path: `M${(-half).toFixed(2)},0L${half.toFixed(2)},0M${(half - head).toFixed(2)},${(-head * .62).toFixed(2)}L${half.toFixed(2)},0L${(half - head).toFixed(2)},${(head * .62).toFixed(2)}`
    };
  }

  function sampleVector(x, y) {
    if (x < 0 || y < 0 || x >= frame.width - 1 || y >= frame.height - 1) return null;
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const tx = x - x0;
    const ty = y - y0;
    const indexes = [
      y0 * frame.width + x0,
      y0 * frame.width + x0 + 1,
      (y0 + 1) * frame.width + x0,
      (y0 + 1) * frame.width + x0 + 1
    ];
    if (indexes.some((index) => !Number.isFinite(frame.values[index]) || !Number.isFinite(frame.u[index]) || !Number.isFinite(frame.v[index]))) return null;
    const weights = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
    const u = indexes.reduce((sum, index, i) => sum + frame.u[index] * weights[i], 0);
    const v = indexes.reduce((sum, index, i) => sum + frame.v[index] * weights[i], 0);
    const magnitude = Math.hypot(u, v);
    return magnitude >= .35 ? { u, v, magnitude } : null;
  }

  function integrateStream(seedX, seedY, direction, spatialStep, maxSteps) {
    const points = [];
    let x = seedX;
    let y = seedY;
    for (let step = 0; step < maxSteps; step += 1) {
      const first = sampleVector(x, y);
      if (!first) break;
      const halfX = x + direction * spatialStep * .5 * first.u / first.magnitude;
      const halfY = y - direction * spatialStep * .5 * first.v / first.magnitude;
      const middle = sampleVector(halfX, halfY);
      if (!middle) break;
      x += direction * spatialStep * middle.u / middle.magnitude;
      y -= direction * spatialStep * middle.v / middle.magnitude;
      if (!sampleVector(x, y)) break;
      points.push([x, y]);
    }
    return points;
  }

  function makeStreamlinePaths() {
    if (!frame.u || !frame.v || frame.product !== 'wind-level') return { paths: [], markerPath: '' };
    const paths = [];
    const baseSeedStep = Math.max(4.5, frame.width / 25);
    const seedStep = baseSeedStep / viewZoom;
    const spatialStep = Math.max(.16, seedStep / 9);
    const bounds = visibleSourceBounds();
    const firstX = Math.floor(bounds.west / seedStep) * seedStep + seedStep * .5;
    const firstY = Math.floor(bounds.north / seedStep) * seedStep + seedStep * .5;
    for (let y = firstY; y < bounds.south; y += seedStep) {
      for (let x = firstX; x < bounds.east; x += seedStep) {
        if (!sampleVector(x, y)) continue;
        const points = [
          ...integrateStream(x, y, -1, spatialStep, 15).reverse(),
          [x, y],
          ...integrateStream(x, y, 1, spatialStep, 15)
        ];
        if (points.length < 10) continue;
        const middleIndex = Math.floor(points.length / 2);
        const previous = points[Math.max(0, middleIndex - 1)];
        const middle = points[middleIndex];
        const next = points[Math.min(points.length - 1, middleIndex + 1)];
        paths.push({
          path: `M${points.map(([px, py]) => `${px.toFixed(2)},${py.toFixed(2)}`).join('L')}`,
          marker: {
            x: middle[0],
            y: middle[1],
            angle: Math.atan2(next[1] - previous[1], next[0] - previous[0]) * 180 / Math.PI
          }
        });
      }
    }
    const markerSize = Math.max(3.2, baseSeedStep * .22);
    return {
      paths,
      markerPath: `M${(-markerSize).toFixed(2)},${(-markerSize * .62).toFixed(2)}L0,0L${(-markerSize).toFixed(2)},${(markerSize * .62).toFixed(2)}`
    };
  }

  function makeContourPaths() {
    if (!frame.overlay) return [];
    const levels = [-10, -8, -6, -4, -2, 0, 2, 4];
    const cases = {
      1: [[3, 0]], 2: [[0, 1]], 3: [[3, 1]], 4: [[1, 2]],
      5: [[3, 0], [1, 2]], 6: [[0, 2]], 7: [[3, 2]], 8: [[2, 3]],
      9: [[0, 2]], 10: [[0, 1], [2, 3]], 11: [[1, 2]], 12: [[3, 1]],
      13: [[0, 1]], 14: [[3, 0]]
    };
    const contours = [];
    const interpolate = (a, b, threshold) => Math.max(0, Math.min(1, (threshold - a) / (b - a || 1e-6)));
    for (const level of levels) {
      const segments = [];
      for (let row = 0; row < frame.height - 1; row += 1) {
        for (let column = 0; column < frame.width - 1; column += 1) {
          const topLeft = frame.overlay[row * frame.width + column];
          const topRight = frame.overlay[row * frame.width + column + 1];
          const bottomLeft = frame.overlay[(row + 1) * frame.width + column];
          const bottomRight = frame.overlay[(row + 1) * frame.width + column + 1];
          if (![topLeft, topRight, bottomLeft, bottomRight].every(Number.isFinite)) continue;
          const code = (topLeft >= level ? 1 : 0) | (topRight >= level ? 2 : 0) | (bottomRight >= level ? 4 : 0) | (bottomLeft >= level ? 8 : 0);
          if (!cases[code]) continue;
          const points = [
            [column + interpolate(topLeft, topRight, level), row],
            [column + 1, row + interpolate(topRight, bottomRight, level)],
            [column + interpolate(bottomLeft, bottomRight, level), row + 1],
            [column, row + interpolate(topLeft, bottomLeft, level)]
          ];
          for (const [first, second] of cases[code]) {
            segments.push(`M${points[first][0].toFixed(2)},${points[first][1].toFixed(2)}L${points[second][0].toFixed(2)},${points[second][1].toFixed(2)}`);
          }
        }
      }
      if (segments.length) contours.push({ level, path: segments.join('') });
    }
    return contours;
  }

  const boundaryPaths = $derived(makeBoundaryPaths());
  const arrowGlyphs = $derived(makeArrowGlyphs());
  const streamlineData = $derived(makeStreamlinePaths());
  const contourPaths = $derived(makeContourPaths());

  function inspect(event) {
    if (!frame || !surface || !layer) return;
    const rect = surface.getBoundingClientRect();
    const screenX = (event.clientX - rect.left) / rect.width * frame.width;
    const screenY = (event.clientY - rect.top) / rect.height * frame.height;
    const userPanX = panX * frame.width / rect.width;
    const userPanY = panY * frame.height / rect.height;
    const sourceX = frame.width / 2 + (screenX - userPanX - frame.width / 2) / zoom;
    const sourceY = frame.height / 2 + (screenY - userPanY - frame.height / 2) / zoom;
    if (sourceX < 0 || sourceY < 0 || sourceX >= frame.width || sourceY >= frame.height) {
      hover = null;
      return;
    }
    const column = Math.floor(sourceX);
    const row = Math.floor(sourceY);
    const value = frame.values[row * frame.width + column];
    if (!Number.isFinite(value)) {
      hover = null;
      return;
    }
    const [west, south, east, north] = frame.bounds;
    const longitude = west + (column + .5) / frame.width * (east - west);
    const latitude = north - (row + .5) / frame.height * (north - south);
    const layerRect = layer.getBoundingClientRect();
    hover = {
      value,
      overlay: frame.overlay?.[row * frame.width + column],
      longitude,
      latitude,
      x: event.clientX - layerRect.left,
      y: event.clientY - layerRect.top
    };
  }

  function setZoom(nextZoom, clientX, clientY) {
    if (!surface) return;
    const next = Math.max(1, Math.min(8, nextZoom));
    const rect = surface.getBoundingClientRect();
    const cursorX = (clientX ?? rect.left + rect.width / 2) - rect.left;
    const cursorY = (clientY ?? rect.top + rect.height / 2) - rect.top;
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const factor = next / zoom;
    panX = cursorX - centerX - (cursorX - centerX - panX) * factor;
    panY = cursorY - centerY - (cursorY - centerY - panY) * factor;
    zoom = next;
    if (next === 1) panX = panY = 0;
    hover = null;
    // El zoom cambia la densidad de glifos: conviene rehacerlos de inmediato.
    settleViewport();
  }

  function vectorTransform() {
    const renderedWidth = surface?.clientWidth || frame.width;
    const renderedHeight = surface?.clientHeight || frame.height;
    const userPanX = panX * frame.width / renderedWidth;
    const userPanY = panY * frame.height / renderedHeight;
    const centerX = frame.width / 2;
    const centerY = frame.height / 2;
    return `translate(${userPanX} ${userPanY}) translate(${centerX} ${centerY}) scale(${zoom}) translate(${-centerX} ${-centerY})`;
  }

  function zoomWithWheel(event) {
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.22 : 1 / 1.22), event.clientX, event.clientY);
  }

  function beginDrag(event) {
    if (event.button !== 0) return;
    dragging = true;
    dragStart = { x: event.clientX, y: event.clientY, panX, panY };
    surface.setPointerCapture(event.pointerId);
    hover = null;
  }

  function movePointer(event) {
    if (!dragging || !dragStart) {
      inspect(event);
      return;
    }
    // Los eventos de puntero llegan más rápido que el refresco de pantalla:
    // sin este agrupado se recalcula el encuadre varias veces por fotograma.
    pendingPan = {
      x: dragStart.panX + event.clientX - dragStart.x,
      y: dragStart.panY + event.clientY - dragStart.y
    };
    if (dragFrame) return;
    dragFrame = requestAnimationFrame(() => {
      dragFrame = 0;
      if (!pendingPan) return;
      panX = pendingPan.x;
      panY = pendingPan.y;
      scheduleSettle();
    });
  }

  function endDrag(event) {
    dragging = false;
    dragStart = null;
    pendingPan = null;
    if (dragFrame) {
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
    }
    settleViewport();
    if (surface?.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
  }

  function resetView() {
    zoom = 1;
    panX = 0;
    panY = 0;
    hover = null;
    settleViewport();
  }

  $effect(() => {
    frame;
    renderGrid();
  });

  $effect(() => {
    resetKey;
    productLabel;
    frame.width;
    frame.height;
    frame.bounds.join(',');
    resetView();
  });

  $effect(() => () => {
    window.clearTimeout(settleTimer);
    if (dragFrame) cancelAnimationFrame(dragFrame);
  });
</script>

<div class="grid-layer" bind:this={layer} style:--grid-ratio={`${frame.width}/${frame.height}`}>
  <div
    class="map-surface"
    bind:this={surface}
    role="application"
    aria-label={`Mapa interactivo de ${productLabel}`}
    class:dragging
    onwheel={zoomWithWheel}
    onpointerdown={beginDrag}
    onpointermove={movePointer}
    onpointerup={endDrag}
    onpointercancel={endDrag}
    ondblclick={(event) => setZoom(zoom * 1.7, event.clientX, event.clientY)}
    onpointerleave={() => (hover = null)}
  >
    <canvas
      class="grid-raster"
      bind:this={raster}
      style:transform={`translate(${panX}px, ${panY}px) scale(${zoom})`}
      aria-hidden="true"
    ></canvas>
    <svg class="vector-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} aria-hidden="true">
      <g transform={vectorTransform()}>
        {#each streamlineData.paths as streamline}
          <path class="streamline-halo" d={streamline.path} />
          <path class="streamline" d={streamline.path} />
          <path class="stream-particle" d={streamline.path} />
          <g transform={`translate(${streamline.marker.x.toFixed(2)} ${streamline.marker.y.toFixed(2)}) rotate(${streamline.marker.angle.toFixed(2)}) scale(${(1 / zoom).toFixed(5)})`}>
            <path class="stream-direction-halo" d={streamlineData.markerPath} />
            <path class="stream-direction" d={streamlineData.markerPath} />
          </g>
        {/each}
        {#each arrowGlyphs.arrows || [] as arrow}
          <g transform={`translate(${arrow.x.toFixed(2)} ${arrow.y.toFixed(2)}) rotate(${arrow.angle.toFixed(2)}) scale(${(1 / zoom).toFixed(5)})`}>
            <path class="vector-arrow-halo" d={arrowGlyphs.path} />
            <path class="vector-arrow" d={arrowGlyphs.path} />
          </g>
        {/each}
        {#each contourPaths as contour}
          <path class:zero-contour={contour.level === 0} class="scalar-contour" d={contour.path} />
        {/each}
        {#each boundaryPaths as boundary}
          <path
            class="region-boundary"
            class:admin-boundary={boundary.level === 'admin1'}
            d={boundary.path}
          />
        {/each}
      </g>
    </svg>
  </div>
  {#if hover}
    <div class="grid-tooltip" style:left={`${hover.x}px`} style:top={`${hover.y}px`}>
      <strong>{productLabel}</strong>
      <span>{hover.value.toFixed(frame.product === 'ship' ? 2 : 1)} {frame.unit}</span>
      {#if Number.isFinite(hover.overlay)}<span class="overlay-value">LI {hover.overlay.toFixed(1)} {frame.overlay_unit}</span>{/if}
      <small>{hover.latitude.toFixed(3)}° N · {hover.longitude.toFixed(3)}° E</small>
    </div>
  {/if}
  <div class="zoom-controls" aria-label="Controles de zoom">
    <button type="button" onclick={() => setZoom(zoom * 1.35)} aria-label="Acercar mapa"><Plus size={15} /></button>
    <button type="button" onclick={() => setZoom(zoom / 1.35)} aria-label="Alejar mapa" disabled={zoom <= 1}><Minus size={15} /></button>
    <button type="button" onclick={resetView} aria-label="Restablecer encuadre"><Locate size={15} /></button>
    <span>{Math.round(zoom * 100)}%</span>
  </div>
</div>

<style>
  .grid-layer{position:absolute;inset:4% 6%;z-index:4;display:grid;place-items:center;pointer-events:none}
  .map-surface{position:relative;max-width:100%;max-height:100%;width:auto;height:100%;aspect-ratio:var(--grid-ratio);filter:drop-shadow(0 12px 24px rgba(0,0,0,.24));pointer-events:auto;cursor:grab;touch-action:none}
  .map-surface.dragging{cursor:grabbing}
  .vector-overlay{position:absolute;inset:0;display:block;width:100%;height:100%}
  /* El raster se compone en GPU: el encuadre no vuelve a rasterizar la malla. */
  .grid-raster{position:absolute;inset:0;display:block;width:100%;height:100%;image-rendering:pixelated;transform-origin:center;will-change:transform}
  .vector-overlay{overflow:visible;pointer-events:none}
  .vector-arrow,.vector-arrow-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .vector-arrow-halo{stroke:rgba(239,247,250,.62);stroke-width:1.9}
  .vector-arrow{stroke:rgba(7,13,18,.94);stroke-width:1.02}
  .streamline,.streamline-halo,.stream-particle,.stream-direction,.stream-direction-halo{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .streamline-halo{stroke:rgba(238,247,250,.58);stroke-width:2.75}
  .streamline{stroke:rgba(5,14,20,.9);stroke-width:1.48}
  .stream-particle{stroke:rgba(190,232,250,.88);stroke-width:1.55;stroke-dasharray:1.25 11.75;animation:stream-flow 1.25s linear infinite}
  .stream-direction-halo{stroke:rgba(238,247,250,.9);stroke-width:3.55}
  .stream-direction{stroke:rgba(5,14,20,.98);stroke-width:2.05}
  .scalar-contour{fill:none;stroke:rgba(9,13,18,.82);stroke-width:.62;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .scalar-contour.zero-contour{stroke:#f5f8fa;stroke-width:.9}
  @keyframes stream-flow{to{stroke-dashoffset:-13.05}}
  @media(prefers-reduced-motion:reduce){.stream-particle{display:none}}
  .region-boundary{fill:none;stroke:#0b0f12;stroke-width:.7;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
  .region-boundary.admin-boundary{stroke:rgba(11,15,18,.54);stroke-width:.32}
  .grid-tooltip{position:absolute;z-index:12;display:flex;flex-direction:column;gap:2px;min-width:142px;padding:8px 9px;transform:translate(12px,calc(-100% - 10px));border:1px solid rgba(255,255,255,.16);border-radius:8px;color:#eef6fa;background:rgba(5,14,22,.9);box-shadow:0 8px 24px rgba(0,0,0,.3);backdrop-filter:blur(8px);pointer-events:none}
  .grid-tooltip strong{font-size:.59rem}.grid-tooltip span{color:#8ed1ff;font-size:.72rem;font-weight:720}.grid-tooltip small{color:rgba(235,244,251,.6);font-size:.5rem}
  .grid-tooltip .overlay-value{color:#f4d58a;font-size:.62rem}
  .zoom-controls{position:absolute;right:calc(-6% + 4px);top:calc(-4% + 4px);z-index:15;display:grid;grid-template-columns:30px 30px 30px auto;align-items:center;gap:4px;pointer-events:auto}
  .zoom-controls button{display:grid;place-items:center;width:30px;height:30px;border:1px solid rgba(255,255,255,.15);border-radius:7px;color:#dceaf2;background:rgba(5,14,22,.76);backdrop-filter:blur(8px)}
  .zoom-controls button:hover{background:rgba(31,60,79,.9)}.zoom-controls button:disabled{opacity:.38}
  .zoom-controls span{min-width:38px;padding:5px 6px;border-radius:6px;color:rgba(235,244,251,.72);background:rgba(5,14,22,.66);font-size:.5rem;text-align:center}
</style>
