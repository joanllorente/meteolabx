<script>
  /**
   * Panel interno de uso.
   *
   * Vivía en la barra lateral de Streamlit, y se abría escribiendo
   * `Statics_admin` en el formulario de Weather Underground con la contraseña
   * como clave. Al retirar aquella aplicación se quedaba sin puerta, así que
   * aquí está la suya: una pantalla de solo lectura contra el mismo endpoint,
   * con la contraseña en su cabecera.
   *
   * No se indexa ni se enlaza desde ninguna parte: se llega escribiendo la
   * dirección.
   */
  import { onMount } from 'svelte';

  let password = $state('');
  let data = $state(null);
  let error = $state('');
  let loading = $state(false);

  const KEY = 'mlx-stats-password';

  onMount(() => {
    try {
      const guardada = sessionStorage.getItem(KEY);
      if (guardada) {
        password = guardada;
        consultar();
      }
    } catch {
      /* sin sesión, se escribe cada vez */
    }
  });

  async function consultar(event) {
    event?.preventDefault();
    if (!password.trim()) return;
    loading = true;
    error = '';
    try {
      const respuesta = await fetch('/v1/stats/stations', {
        headers: { 'X-Stats-Password': password.trim() }
      });
      if (respuesta.status === 401) throw new Error('contraseña');
      if (respuesta.status === 404) throw new Error('desactivado');
      if (!respuesta.ok) throw new Error('fallo');
      data = await respuesta.json();
      // Dura lo que la pestaña: recargar no obliga a escribirla otra vez, y
      // cerrarla no la deja puesta en el navegador.
      try {
        sessionStorage.setItem(KEY, password.trim());
      } catch {
        /* ídem */
      }
    } catch (causa) {
      data = null;
      error = String(causa?.message || 'fallo');
    } finally {
      loading = false;
    }
  }

  const fecha = (epoch) =>
    epoch ? new Date(epoch * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const numero = (valor) => new Intl.NumberFormat('es-ES').format(valor || 0);
</script>

<svelte:head>
  <title>Uso interno · MeteoLabX</title>
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<main>
  <h1>Uso interno</h1>

  {#if !data}
    <form onsubmit={consultar}>
      <label>
        <span>Contraseña</span>
        <input type="password" bind:value={password} autocomplete="current-password" />
      </label>
      <button type="submit" disabled={loading}>{loading ? 'Consultando…' : 'Entrar'}</button>
    </form>
    {#if error === 'contraseña'}
      <p class="error">Contraseña incorrecta.</p>
    {:else if error === 'desactivado'}
      <p class="error">Las estadísticas están desactivadas en este servidor.</p>
    {:else if error}
      <p class="error">No se pudieron consultar las estadísticas.</p>
    {/if}
  {:else}
    <section class="totales">
      {#each [
        ['Hoy', data.totals.d1],
        ['7 días', data.totals.d7],
        ['30 días', data.totals.d30],
        ['Desde el inicio', data.totals.total],
        ['Estaciones', data.totals.stations],
        ['Errores (30 d)', data.totals.errors?.d30]
      ] as [etiqueta, valor] (etiqueta)}
        <article><span>{etiqueta}</span><strong>{numero(valor)}</strong></article>
      {/each}
    </section>

    <h2>Origen de las conexiones</h2>
    <section class="totales">
      {#each [
        ['Aplicación', data.totals.sources?.app],
        ['Fichas indexables', data.totals.sources?.seo],
        ['Aplicación anterior', data.totals.sources?.legacy]
      ] as [etiqueta, valores] (etiqueta)}
        <article>
          <span>{etiqueta}</span>
          <strong>{numero(valores?.d30)}</strong>
          <small>{numero(valores?.total)} desde el inicio</small>
        </article>
      {/each}
    </section>

    <h2>Secciones</h2>
    <table>
      <thead><tr><th>Sección</th><th>Hoy</th><th>7 d</th><th>30 d</th><th>Total</th><th>Última</th></tr></thead>
      <tbody>
        {#each data.sections as fila (fila.section)}
          <tr>
            <td>{fila.section}</td>
            <td class="n">{numero(fila.d1)}</td>
            <td class="n">{numero(fila.d7)}</td>
            <td class="n">{numero(fila.d30)}</td>
            <td class="n">{numero(fila.total)}</td>
            <td class="fecha">{fecha(fila.last_epoch)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if data.error_kinds?.length}
      <h2>Tipos de error</h2>
      <table>
        <thead><tr><th>Tipo</th><th>30 d</th><th>Total</th></tr></thead>
        <tbody>
          {#each data.error_kinds as fila (fila.kind)}
            <tr><td>{fila.kind}</td><td class="n">{numero(fila.d30)}</td><td class="n">{numero(fila.total)}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}

    <h2>Estaciones <small>({data.stations.length})</small></h2>
    <table>
      <thead>
        <tr>
          <th>Estación</th><th>Red</th><th>Hoy</th><th>7 d</th><th>30 d</th><th>Total</th>
          <th>Errores (30 d)</th><th>Última visita</th>
        </tr>
      </thead>
      <tbody>
        {#each data.stations as fila (fila.provider + fila.station_id)}
          <tr>
            <td>{fila.name || fila.station_id}</td>
            <td class="red">{fila.provider}</td>
            <td class="n">{numero(fila.d1)}</td>
            <td class="n">{numero(fila.d7)}</td>
            <td class="n">{numero(fila.d30)}</td>
            <td class="n">{numero(fila.total)}</td>
            <td class="n" class:mal={fila.errors?.d30 > 0}>{numero(fila.errors?.d30)}</td>
            <td class="fecha">{fecha(fila.last_epoch)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</main>

<style>
  main { width: min(1180px, calc(100% - 32px)); margin: 32px auto 64px; color: var(--ink); }
  h1 { font-size: 1.4rem; font-weight: 750; margin-bottom: 18px; }
  h2 { font-size: 0.92rem; font-weight: 700; margin: 28px 0 10px; }
  h2 small { color: var(--muted); font-weight: 600; }

  form { display: flex; align-items: flex-end; gap: 10px; }
  label { display: flex; flex-direction: column; gap: 4px; }
  label span { font-size: 0.7rem; font-weight: 650; color: var(--muted); }
  input {
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel-2); color: var(--ink); font: inherit;
  }
  button {
    padding: 9px 16px; border: 0; border-radius: 8px;
    background: var(--accent); color: #fff; font-size: 0.8rem; font-weight: 700;
  }
  .error { margin-top: 12px; font-size: 0.8rem; color: var(--alert-danger-fg); }

  .totales { display: flex; flex-wrap: wrap; gap: 10px; }
  .totales article {
    flex: 1 1 150px; padding: 12px 14px;
    border: 1px solid var(--border); border-radius: 12px; background: var(--card);
  }
  .totales span { display: block; font-size: 0.66rem; color: var(--muted); font-weight: 650; }
  .totales strong { font-size: 1.5rem; font-weight: 740; font-variant-numeric: tabular-nums; }
  .totales small { display: block; font-size: 0.62rem; color: var(--muted); }

  table { width: 100%; border-collapse: collapse; font-size: 0.76rem; }
  th, td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
  th { font-size: 0.66rem; color: var(--muted); font-weight: 650; }
  .n { text-align: right; font-variant-numeric: tabular-nums; }
  .n.mal { color: var(--alert-danger-fg); font-weight: 700; }
  .red { color: var(--muted); }
  .fecha { color: var(--muted); white-space: nowrap; }
</style>
