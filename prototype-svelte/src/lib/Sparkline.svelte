<script>
  // Mini-gráfica en línea, sin ejes. Puramente decorativa.
  let { data = [], color = 'var(--accent)', width = 96, height = 30, fill = true } = $props();

  const min = $derived(Math.min(...data));
  const max = $derived(Math.max(...data));
  const span = $derived(max - min || 1);
  const pts = $derived(
    data.map((v, i) => {
      const x = (i / (data.length - 1)) * (width - 2) + 1;
      const y = height - 3 - ((v - min) / span) * (height - 6);
      return [x, y];
    })
  );
  const line = $derived(pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' '));
  const area = $derived(`${line} L${width - 1} ${height} L1 ${height} Z`);
</script>

<svg {width} {height} viewBox="0 0 {width} {height}" fill="none" aria-hidden="true">
  {#if fill}
    <path d={area} fill={color} opacity="0.12" />
  {/if}
  <path d={line} stroke={color} stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
  <circle cx={pts[pts.length - 1]?.[0]} cy={pts[pts.length - 1]?.[1]} r="2.4" fill={color} />
</svg>
