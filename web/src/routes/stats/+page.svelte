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

  import { setStatsExcluded, statsExcluded } from '$lib/stats.js';

  let password = $state('');
  let data = $state(null);
  let error = $state('');
  let loading = $state(false);

  const KEY = 'mlx-stats-password';

  // Si este navegador ya está fuera del recuento, el panel lo dice y deja
  // volver atrás sin tener que recordar el parámetro de la URL.
  let excluido = $state(false);

  function alternarExclusion() {
    excluido = !excluido;
    setStatsExcluded(excluido);
  }

  onMount(() => {
    excluido = statsExcluded();
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

  // Nombres legibles de las secciones. El identificador que se guarda es
  // estable (y así debe seguir, para no partir el histórico), pero leer
  // «forecast.streamlit» en una tabla no dice gran cosa.
  const NOMBRES_SECCION = {
    observation: 'Observación',
    trends: 'Tendencias',
    historical: 'Histórico',
    'map.stations': 'Mapa · estaciones',
    'map.temperature': 'Mapa · temperatura',
    'map.wind': 'Mapa · viento',
    'map.precipitation': 'Mapa · precipitación',
    'forecast.app': 'Predicción · desde la web',
    'forecast.streamlit': 'Predicción · desde Streamlit (retirado)',
    'forecast.direct': 'Predicción · entrada directa',
    ranking: 'Ranking'
  };
  const nombreSeccion = (id) => NOMBRES_SECCION[id] || id;

  // Columnas de la tabla de estaciones: etiqueta, valor con el que se ordena y
  // si es texto (se ordena alfabéticamente y empieza ascendente) o número.
  const COLUMNAS = [
    { clave: 'nombre', etiqueta: 'Estación', texto: true, valor: (f) => f.name || f.station_id || '' },
    { clave: 'red', etiqueta: 'Red', texto: true, valor: (f) => f.provider || '' },
    { clave: 'd1', etiqueta: 'Hoy', valor: (f) => f.d1 || 0 },
    { clave: 'd7', etiqueta: '7 d', valor: (f) => f.d7 || 0 },
    { clave: 'd30', etiqueta: '30 d', valor: (f) => f.d30 || 0 },
    { clave: 'total', etiqueta: 'Total', valor: (f) => f.total || 0 },
    { clave: 'errores', etiqueta: 'Errores (30 d)', valor: (f) => f.errors?.d30 || 0 },
    { clave: 'visita', etiqueta: 'Última visita', valor: (f) => f.last_epoch || 0 }
  ];

  // Sin columna elegida se respeta el orden que manda el servidor.
  let orden = $state(null);

  function ordenarPor(columna) {
    if (orden?.clave === columna.clave) {
      // Tercer clic: se vuelve al orden del servidor.
      orden = orden.ascendente === columna.texto ? { clave: columna.clave, ascendente: !orden.ascendente } : null;
    } else {
      orden = { clave: columna.clave, ascendente: !!columna.texto };
    }
  }

  // Detalle de una estación: la fila abierta y lo que ha respondido la API.
  // Se cachea por estación para que cerrar y volver a abrir no vuelva a pedirlo.
  let abierta = $state('');
  let detalles = $state({});

  const claveDe = (fila) => `${fila.provider}|${fila.station_id}`;

  async function alternarDetalle(fila) {
    const clave = claveDe(fila);
    if (abierta === clave) {
      abierta = '';
      return;
    }
    abierta = clave;
    if (detalles[clave]?.datos) return;
    detalles = { ...detalles, [clave]: { cargando: true, error: '', datos: null } };
    try {
      const parametros = new URLSearchParams({
        provider: fila.provider,
        station_id: fila.station_id
      });
      const respuesta = await fetch(`/v1/stats/station?${parametros}`, {
        headers: { 'X-Stats-Password': password.trim() }
      });
      if (!respuesta.ok) throw new Error('fallo');
      const datos = await respuesta.json();
      detalles = { ...detalles, [clave]: { cargando: false, error: '', datos } };
    } catch {
      detalles = { ...detalles, [clave]: { cargando: false, error: 'fallo', datos: null } };
    }
  }

  const FUENTES = { app: 'Aplicación', seo: 'Ficha indexable', legacy: 'Aplicación anterior' };
  const IDIOMAS = {
    es: 'Castellano',
    ca: 'Catalán',
    en: 'Inglés',
    fr: 'Francés',
    it: 'Italiano',
    pt: 'Portugués'
  };
  // Las visitas anteriores a que se registrara el idioma lo llevan vacío.
  const idioma = (codigo) => IDIOMAS[codigo] || (codigo ? codigo : '—');

  const ENTRADAS = {
    search: 'Buscador',
    external: 'Enlace externo',
    internal: 'Dentro de la aplicación',
    direct: 'Directa o desconocida'
  };
  const entrada = (codigo) => ENTRADAS[codigo] || (codigo ? codigo : '—');

  const DISPOSITIVOS = { mobile: 'Móvil', tablet: 'Tableta', desktop: 'Escritorio' };
  const dispositivo = (codigo) => DISPOSITIVOS[codigo] || (codigo ? codigo : '—');

  const colacion = new Intl.Collator('es-ES', { sensitivity: 'base', numeric: true });

  const estaciones = $derived.by(() => {
    const filas = data?.stations ?? [];
    if (!orden) return filas;
    const columna = COLUMNAS.find((c) => c.clave === orden.clave);
    if (!columna) return filas;
    const signo = orden.ascendente ? 1 : -1;
    return [...filas].sort((a, b) => {
      const x = columna.valor(a);
      const y = columna.valor(b);
      const cmp = columna.texto ? colacion.compare(x, y) : x - y;
      return cmp * signo;
    });
  });
</script>

<svelte:head>
  <title>Uso interno · MeteoLabX</title>
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<main>
  <h1>Uso interno</h1>

  <p class="exclusion">
    {excluido
      ? 'Este navegador no cuenta en las estadísticas.'
      : 'Este navegador cuenta en las estadísticas.'}
    <button type="button" onclick={alternarExclusion}>
      {excluido ? 'Volver a contarlo' : 'Dejar de contarlo'}
    </button>
  </p>

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
            <td title={fila.section}>{nombreSeccion(fila.section)}</td>
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
          {#each COLUMNAS as columna (columna.clave)}
            <th class:num={!columna.texto} aria-sort={orden?.clave === columna.clave
              ? (orden.ascendente ? 'ascending' : 'descending')
              : 'none'}>
              <button type="button" onclick={() => ordenarPor(columna)}>
                {columna.etiqueta}
                <span class="flecha" aria-hidden="true"
                  >{orden?.clave === columna.clave ? (orden.ascendente ? '▲' : '▼') : ''}</span>
              </button>
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each estaciones as fila (claveDe(fila))}
          {@const clave = claveDe(fila)}
          <tr class:abierta={abierta === clave}>
            <td>
              <button type="button" class="nombre" onclick={() => alternarDetalle(fila)}
                aria-expanded={abierta === clave}>{fila.name || fila.station_id}</button>
            </td>
            <td class="red">{fila.provider}</td>
            <td class="n">{numero(fila.d1)}</td>
            <td class="n">{numero(fila.d7)}</td>
            <td class="n">{numero(fila.d30)}</td>
            <td class="n">{numero(fila.total)}</td>
            <td class="n" class:mal={fila.errors?.d30 > 0}>{numero(fila.errors?.d30)}</td>
            <td class="fecha">{fecha(fila.last_epoch)}</td>
          </tr>
          {#if abierta === clave}
            {@const detalle = detalles[clave]}
            <tr class="detalle">
              <td colspan={COLUMNAS.length}>
                {#if detalle?.cargando}
                  <p class="aviso">Consultando…</p>
                {:else if detalle?.error}
                  <p class="aviso error">No se pudo consultar el detalle de la estación.</p>
                {:else if detalle?.datos}
                  {@const d = detalle.datos}
                  <div class="ficha">
                    <p class="identificador">{d.provider} · {d.station_id}</p>

                    <div class="resumen">
                      {#each [
                        ['Visitas', d.visits],
                        ['Errores', d.errors],
                        ['Fichas indexables', d.seo_views]
                      ] as [etiqueta, bloque] (etiqueta)}
                        <article>
                          <span>{etiqueta}</span>
                          <strong>{numero(bloque?.total)}</strong>
                          <small>
                            {numero(bloque?.d1)} hoy · {numero(bloque?.d7)} en 7 d ·
                            {numero(bloque?.d30)} en 30 d
                          </small>
                          <small>Último: {fecha(bloque?.last_epoch)}</small>
                        </article>
                      {/each}
                    </div>

                    <div class="columnas">
                      <section>
                        <h3>Visitas por entrada</h3>
                        {#if d.visits_by_entry?.some((fila) => fila.entry)}
                          <table>
                            <thead><tr><th>Entrada</th><th>30 d</th><th>Total</th></tr></thead>
                            <tbody>
                              {#each d.visits_by_entry as fila (fila.entry)}
                                <tr>
                                  <td>{entrada(fila.entry)}</td>
                                  <td class="n">{numero(fila.d30)}</td>
                                  <td class="n">{numero(fila.total)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin entrada registrada todavía.</p>
                        {/if}

                        {#if d.referrers?.length}
                          <h3>Quién enlaza</h3>
                          <table>
                            <thead><tr><th>Dominio</th><th>30 d</th><th>Total</th><th>Último</th></tr></thead>
                            <tbody>
                              {#each d.referrers as fila (fila.domain)}
                                <tr>
                                  <td>{fila.domain}</td>
                                  <td class="n">{numero(fila.d30)}</td>
                                  <td class="n">{numero(fila.total)}</td>
                                  <td class="fecha">{fecha(fila.last_epoch)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {/if}

                        <h3>Visitas por versión</h3>
                        <table>
                          <thead><tr><th>Versión</th><th>30 d</th><th>Total</th></tr></thead>
                          <tbody>
                            {#each Object.entries(FUENTES) as [fuente, etiqueta] (fuente)}
                              <tr>
                                <td>{etiqueta}</td>
                                <td class="n">{numero(d.visits_by_source?.[fuente]?.d30)}</td>
                                <td class="n">{numero(d.visits_by_source?.[fuente]?.total)}</td>
                              </tr>
                            {/each}
                          </tbody>
                        </table>

                        <h3>Visitas por dispositivo</h3>
                        {#if d.visits_by_device?.some((fila) => fila.device)}
                          <table>
                            <thead><tr><th>Dispositivo</th><th>30 d</th><th>Total</th></tr></thead>
                            <tbody>
                              {#each d.visits_by_device as fila (fila.device)}
                                <tr>
                                  <td>{dispositivo(fila.device)}</td>
                                  <td class="n">{numero(fila.d30)}</td>
                                  <td class="n">{numero(fila.total)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin dispositivo registrado todavía.</p>
                        {/if}

                        <h3>Visitas por idioma mostrado</h3>
                        {#if d.visits_by_language?.some((fila) => fila.language)}
                          <table>
                            <thead><tr><th>Idioma</th><th>30 d</th><th>Total</th></tr></thead>
                            <tbody>
                              {#each d.visits_by_language as fila (fila.language)}
                                <tr>
                                  <td>{idioma(fila.language)}</td>
                                  <td class="n">{numero(fila.d30)}</td>
                                  <td class="n">{numero(fila.total)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin idioma registrado todavía.</p>
                        {/if}

                        <h3>Errores por tipo</h3>
                        {#if d.error_kinds?.length}
                          <table>
                            <thead><tr><th>Tipo</th><th>30 d</th><th>Total</th><th>Último</th></tr></thead>
                            <tbody>
                              {#each d.error_kinds as tipo (tipo.kind)}
                                <tr>
                                  <td>{tipo.kind}</td>
                                  <td class="n">{numero(tipo.d30)}</td>
                                  <td class="n">{numero(tipo.total)}</td>
                                  <td class="fecha">{fecha(tipo.last_epoch)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin errores registrados.</p>
                        {/if}
                      </section>

                      <section>
                        <h3>Últimos errores</h3>
                        {#if d.recent_errors?.length}
                          <table>
                            <thead><tr><th>Cuándo</th><th>Tipo</th><th>Código</th></tr></thead>
                            <tbody>
                              {#each d.recent_errors as evento, i (evento.epoch + '|' + i)}
                                <tr>
                                  <td class="fecha">{fecha(evento.epoch)}</td>
                                  <td>{evento.kind}</td>
                                  <td class="n">{evento.status_code ?? '—'}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin errores registrados.</p>
                        {/if}

                        <h3>Últimas visitas</h3>
                        <p class="aviso">El idioma mostrado no indica el país del visitante. Las visitas antiguas no incluyen la comprobación del navegador.</p>
                        {#if d.recent_visits?.length}
                          <table>
                            <thead>
                              <tr><th>Cuándo</th><th>Idioma mostrado</th><th>Comprobación</th><th>Dispositivo</th><th>Entrada</th></tr>
                            </thead>
                            <tbody>
                              {#each d.recent_visits as evento, i (evento.epoch + '|' + i)}
                                <tr>
                                  <td class="fecha">{fecha(evento.epoch)}</td>
                                  <td>{idioma(evento.language)}</td>
                                  <td>
                                    {#if evento.url_language || evento.browser_languages || evento.request_languages}
                                      <details>
                                        <summary>Ver idiomas</summary>
                                        <div>URL: {idioma(evento.url_language)}</div>
                                        <div>Navegador: {evento.browser_languages || '—'}</div>
                                        <div>Petición del registro: {evento.request_languages || '—'}</div>
                                        <div>Elección guardada: {evento.saved_language ? idioma(evento.saved_language) : 'Ninguna'}</div>
                                      </details>
                                    {:else}
                                      Sin datos de diagnóstico
                                    {/if}
                                  </td>
                                  <td>{dispositivo(evento.device)}</td>
                                  <td>{evento.referrer_domain || entrada(evento.entry)}</td>
                                </tr>
                              {/each}
                            </tbody>
                          </table>
                        {:else}
                          <p class="aviso">Sin visitas registradas.</p>
                        {/if}
                      </section>
                    </div>
                  </div>
                {/if}
              </td>
            </tr>
          {/if}
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

  .exclusion {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-bottom: 18px; font-size: 0.74rem; color: var(--muted);
  }
  .exclusion button {
    padding: 5px 10px; background: var(--panel-2); color: var(--ink);
    border: 1px solid var(--border); font-size: 0.72rem; font-weight: 650;
    cursor: pointer;
  }

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
  th button {
    display: inline-flex; align-items: baseline; gap: 4px;
    padding: 0; border: 0; border-radius: 0; background: none;
    color: inherit; font: inherit; cursor: pointer;
  }
  th button:hover { color: var(--ink); }
  th.num { text-align: right; }
  th.num button { flex-direction: row-reverse; }
  th .flecha { font-size: 0.6em; }
  .n { text-align: right; font-variant-numeric: tabular-nums; }
  .n.mal { color: var(--alert-danger-fg); font-weight: 700; }
  .red { color: var(--muted); }
  .fecha { color: var(--muted); white-space: nowrap; }

  .nombre {
    padding: 0; border: 0; border-radius: 0; background: none;
    color: inherit; font: inherit; text-align: left; cursor: pointer;
  }
  .nombre:hover { color: var(--accent); }
  /* Seguir una fila de punta a punta en tablas de seis columnas y decenas de
     filas es incómodo sin una guía; el sombreado al pasar el ratón la marca.
     Va solo en el cuerpo: las cabeceras no son filas de datos. Y se declara
     ANTES de `tr.abierta` para que la fila desplegada conserve su fondo. */
  tbody tr:hover { background: var(--fila-hover); }
  tr.abierta,
  tr.abierta:hover { background: var(--panel-2); }
  tr.abierta .nombre { font-weight: 700; }

  td[colspan] { padding: 0; }
  .ficha {
    padding: 14px 10px 20px;
    background: var(--panel-2);
    border-left: 2px solid var(--accent);
  }
  .identificador { font-size: 0.7rem; color: var(--muted); margin-bottom: 10px; }
  .resumen { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .resumen article {
    flex: 1 1 180px; padding: 10px 12px;
    border: 1px solid var(--border); border-radius: 10px; background: var(--card);
  }
  .resumen span { display: block; font-size: 0.64rem; color: var(--muted); font-weight: 650; }
  .resumen strong { font-size: 1.15rem; font-weight: 720; font-variant-numeric: tabular-nums; }
  .resumen small { display: block; font-size: 0.62rem; color: var(--muted); }
  .columnas { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
  .columnas section { flex: 1 1 320px; min-width: 0; }
  .ficha h3 { font-size: 0.7rem; font-weight: 700; margin: 0 0 6px; }
  .ficha h3 + table { margin-bottom: 14px; }
  .ficha table + h3 { margin-top: 4px; }
  .aviso { font-size: 0.72rem; color: var(--muted); margin-bottom: 14px; }
  .aviso.error { color: var(--alert-danger-fg); }
</style>
