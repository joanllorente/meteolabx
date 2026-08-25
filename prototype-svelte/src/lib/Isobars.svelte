<script>
  // Motivo decorativo de isobaras (líneas de contorno) — identidad MeteoLabx.
  // Curvas sinusoidales apiladas, muy tenues. Puramente ornamental.
  let { lines = 9 } = $props();

  function wave(i) {
    const y = 40 + i * 42;
    const amp = 26 + (i % 3) * 10;
    const k = 0.9 + (i % 4) * 0.15;
    let d = `M-40 ${y}`;
    for (let x = 0; x <= 1240; x += 40) {
      const yy = y + Math.sin((x / 200) * k + i * 0.7) * amp;
      d += ` L${x} ${yy.toFixed(1)}`;
    }
    return d;
  }
</script>

<svg class="isobars" viewBox="0 0 1200 460" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  {#each Array(lines) as _, i}
    <path d={wave(i)} fill="none" stroke="var(--isobar)" stroke-width="1.4" />
  {/each}
</svg>

<style>
  .isobars { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
</style>
