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

  let cuarentena = $state(null);
  let prediccion = $state(null);
  let instalacion = $state(null);

  /** Días completos que lleva marcada, para saber si es crónica o de un rato. */
  const duracion = (fila) => {
    const dias = Number(fila?.days_total || 0);
    if (dias > 1) return `${dias} días`;
    const desde = Number(fila?.first_seen || 0);
    const hasta = Number(fila?.last_seen || 0);
    const horas = desde && hasta ? Math.round((hasta - desde) / 3600) : 0;
    return horas >= 1 ? `${horas} h` : 'menos de 1 h';
  };

  const MOTIVOS = {
    frozen: 'lleva horas sin variar',
    impossible: 'imposible para su latitud y época',
    range: 'máxima y mínima incompatibles',
    isolated_peak: 'racha máxima aislada',
    sustained_mismatch: 'racha que su viento medio desmiente',
    world_record: 'por encima del récord mundial',
    intensity: 'intensidad de lluvia implausible',
    unreported: 'lluvia que ningún parte confirma'
  };
  const motivo = (fila) => {
    // Las marcas de lluvia de la ficha no llevan `reason`, pero sus parámetros
    // dicen de cuál se trata: sin esto salían en blanco.
    const params = fila?.params || {};
    const deducido = params.reports != null ? 'unreported' : params.minutes != null ? 'intensity' : '';
    const clave = params.reason || deducido || Object.keys(fila?.reasons || {})[0] || '';
    return MOTIVOS[clave] || clave || '—';
  };
  const VARIABLES = { temperature: 'Temperatura', rain: 'Precipitación', wind: 'Viento (racha)' };

  /** Ficha de la estación, para ir a mirarla sin buscarla a mano. */
  const fichaDe = (fila) =>
    `/es/observation/${encodeURIComponent(fila.provider)}/${encodeURIComponent(fila.station_id)}`;

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
      // La cuarentena vive en memoria del backend, no en la base de uso, así
      // que va en su propia llamada. Que falle no debe tumbar el panel.
      cuarentena = await fetch('/v1/stats/quarantine', {
        headers: { 'X-Stats-Password': password.trim() }
      })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
      // Los mapas de predicción, igual: su propia llamada, y un fallo solo
      // deja vacía su pestaña.
      prediccion = await fetch('/v1/stats/forecast-maps', {
        headers: { 'X-Stats-Password': password.trim() }
      })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
      instalacion = await fetch('/v1/stats/pwa', {
        headers: { 'X-Stats-Password': password.trim() }
      })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
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
  const MOTIVOS_IDIOMA = { saved: 'Elección guardada al cargar', browser: 'Idioma solicitado al cargar', url: 'URL: la carga no pidió un idioma compatible', fallback: 'Idioma de reserva' };
  const CLIENTES = { googlebot: 'Se identifica como Googlebot', bingbot: 'Se identifica como Bingbot', other_bot: 'Se identifica como automatización', unidentified: 'No identificado (no confirma que sea una persona)' };


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

  // Contornos de países, generados por scripts/build_world_map.py desde las
  // fronteras de Natural Earth que ya trae el repo (13 MB → 148 KB). Se cargan
  // solo cuando hace falta pintar el mapa, no en cada visita al panel.
  let paises = $state(null);
  $effect(() => {
    if (paises || !data) return;
    fetch('/paises.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((valor) => (paises = valor))
      .catch(() => (paises = null));
  });

  // Rótulo flotante del mapa. El `<title>` de SVG también funciona, pero es el
  // tooltip del sistema: tarda casi un segundo en salir y rompe el recorrido
  // cuando vas pasando de un país a otro.
  let rotulo = $state(null);
  const mostrarPais = (evento, codigo, nombre) => {
    const caja = evento.currentTarget.ownerSVGElement.getBoundingClientRect();
    rotulo = {
      texto: nombre,
      visitas: visitasPorPais[codigo] || 0,
      x: evento.clientX - caja.left,
      y: evento.clientY - caja.top
    };
  };

  /** Contornos → atributo `d` de un path SVG, en proyección equirectangular. */
  const trazo = (rings) =>
    rings
      .map((ring) => 'M' + ring.map(([lon, lat]) => `${180 + lon} ${90 - lat}`).join('L') + 'Z')
      .join(' ');

  // Pestañas del panel. La cuarentena tiene su propia vista: es otra pregunta
  // —qué sensores no me creo— y estorbaba entre las tablas de uso.
  const PESTANAS = [
    { id: 'uso', etiqueta: 'Uso' },
    { id: 'estaciones', etiqueta: 'Estaciones' },
    { id: 'prediccion', etiqueta: 'Predicción' },
    { id: 'instalacion', etiqueta: 'Instalación' },
    { id: 'cuarentena', etiqueta: 'Cuarentena' }
  ];

  // Las categorías del visor de predicción, con sus nombres del menú.
  const CATEGORIAS_PREDICCION = {
    temperature: 'Temperatura',
    precipitation: 'Precipitación',
    dynamics: 'Dinámica atmosférica',
    convection: 'Convección',
    humidity: 'Humedad',
    clouds: 'Nubosidad',
    radiation: 'Radiación'
  };
  const nombreCategoria = (id) => CATEGORIAS_PREDICCION[id] || id || '—';
  const MODELOS_PREDICCION = { arome: 'AROME', ecmwf: 'ECMWF' };

  // Instalación de la app. Los identificadores son los de `platform.js`.
  const EVENTOS_PWA = {
    offered: 'Tarjeta enseñada',
    instructions: 'Instrucciones abiertas',
    prompt_accepted: 'Diálogo aceptado',
    prompt_dismissed: 'Diálogo rechazado',
    installed: 'Instalación confirmada',
    launched: 'Primera apertura instalada',
    dismissed: '«Ahora no»'
  };
  const NOMBRES_PWA = {
    mobile: 'Móvil', tablet: 'Tableta', desktop: 'Escritorio',
    ios: 'iOS', ipados: 'iPadOS', android: 'Android', macos: 'macOS', windows: 'Windows',
    linux: 'Linux', chromeos: 'ChromeOS',
    safari: 'Safari', chrome: 'Chrome', edge: 'Edge', firefox: 'Firefox', samsung: 'Samsung Internet',
    opera: 'Opera', other: 'Otro',
    prompt: 'Botón con diálogo del navegador', 'ios-safari': 'Instrucciones · Safari en iOS',
    'ios-share': 'Instrucciones · otro navegador en iOS', 'android-menu': 'Instrucciones · menú de Android',
    'android-samsung': 'Instrucciones · Samsung Internet', 'android-firefox': 'Instrucciones · Firefox Android',
    'mac-safari': 'Instrucciones · Safari en Mac', 'desktop-chromium': 'Instrucciones · Chrome/Edge escritorio',
    'open-browser': 'Dentro de otra app', unsupported: 'Navegador sin instalación', installed: 'Ya instalada'
  };
  const nombrePwa = (valor) => NOMBRES_PWA[valor] || valor || 'Sin dato';
  const totalPwa = (evento, ventana = 'total') =>
    instalacion?.events?.find((fila) => fila.event === evento)?.[ventana] || 0;

  // Barra de cada fila, relativa al mapa más visto en 30 días.
  const maximoPrediccion = $derived(
    Math.max(1, ...(prediccion?.maps || []).map((fila) => fila.d30))
  );
  let pestana = $state('uso');

  // Paginación: 500 filas de golpe hacían la página inmanejable.
  const POR_PAGINA = 50;
  let pagina = $state(1);
  // Al reordenar o cambiar de datos se vuelve al principio: seguir en la
  // página 7 de otra ordenación no significa nada.
  $effect(() => {
    void orden;
    void data;
    pagina = 1;
  });

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

  const paginas = $derived(Math.max(1, Math.ceil(estaciones.length / POR_PAGINA)));
  const paginaActual = $derived(Math.min(pagina, paginas));
  const estacionesPagina = $derived(
    estaciones.slice((paginaActual - 1) * POR_PAGINA, paginaActual * POR_PAGINA)
  );

  // Reparto por país, para el mapa y su leyenda.
  const porPais = $derived(data?.by_country ?? []);
  const visitasPorPais = $derived(
    Object.fromEntries(porPais.map((fila) => [fila.country, fila.visits || 0]))
  );
  const visitasMaximas = $derived(Math.max(1, ...porPais.map((f) => f.visits || 0)));

  // Escala logarítmica: con España en 195 visitas y el resto por debajo de 10,
  // una escala lineal dejaba a todos los demás del mismo color que el fondo.
  const intensidad = (visitas) =>
    visitas > 0 ? Math.log1p(visitas) / Math.log1p(visitasMaximas) : 0;

  /** Cinco tramos de color, del más claro al más saturado. */
  const COLORES = ['#1b3a5c', '#245782', '#2c74a8', '#3e8ed0', '#6fb2e8'];
  const colorPais = (codigo) => {
    const visitas = visitasPorPais[codigo] || 0;
    if (!visitas) return 'var(--panel-2)';
    return COLORES[Math.min(COLORES.length - 1, Math.floor(intensidad(visitas) * COLORES.length))];
  };

  // Cortes de la leyenda, deshaciendo la escala logarítmica.
  const tramos = $derived(
    COLORES.map((color, i) => ({
      color,
      desde: Math.max(1, Math.round(Math.expm1((i / COLORES.length) * Math.log1p(visitasMaximas)))),
      hasta: Math.round(Math.expm1(((i + 1) / COLORES.length) * Math.log1p(visitasMaximas)))
    }))
  );
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

    <nav class="pestanas" aria-label="Secciones del panel">
      {#each PESTANAS as p (p.id)}
        <button
          type="button"
          class:activa={pestana === p.id}
          aria-current={pestana === p.id ? 'page' : undefined}
          onclick={() => (pestana = p.id)}
        >
          {p.etiqueta}{#if p.id === 'cuarentena' && cuarentena?.active?.length}
            <span class="cuenta">{cuarentena.active.length}</span>
          {/if}
        </button>
      {/each}
    </nav>

    {#if pestana === 'uso'}
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

    <h2>A qué países se conectan <small>({porPais.length})</small></h2>
    {#if porPais.length}
      {#if paises}
        <figure class="mapa" onmouseleave={() => (rotulo = null)}>
          <svg viewBox="0 0 360 180" role="img" aria-label="Conexiones por país">
            <rect x="0" y="0" width="360" height="180" class="oceano" />
            {#each Object.entries(paises) as [codigo, pais] (codigo)}
              <path
                d={trazo(pais.rings)}
                fill={colorPais(codigo)}
                class="pais"
                class:visitado={visitasPorPais[codigo] > 0}
                onmousemove={(e) => mostrarPais(e, codigo, pais.name)}
              />
            {/each}
          </svg>
          {#if rotulo}
            <div class="rotulo" style="left: {rotulo.x}px; top: {rotulo.y}px">
              <strong>{rotulo.texto}</strong>
              <span>{rotulo.visitas ? `${numero(rotulo.visitas)} conexiones` : 'sin conexiones'}</span>
            </div>
          {/if}
        </figure>
        <div class="leyenda">
          <span>Conexiones</span>
          {#each tramos as tramo (tramo.color)}
            <i style="background: {tramo.color}"></i>
            <small>{tramo.desde === tramo.hasta ? tramo.desde : `${tramo.desde}–${tramo.hasta}`}</small>
          {/each}
        </div>
      {:else}
        <p class="vacio">Cargando el mapa…</p>
      {/if}
      <table>
        <thead><tr><th>País</th><th>Conexiones</th></tr></thead>
        <tbody>
          {#each porPais.slice(0, 12) as fila (fila.country)}
            <tr>
              <td>{paises?.[fila.country]?.name || fila.country}</td>
              <td class="n">{numero(fila.visits)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <p class="vacio">Todavía no hay visitas con país resuelto.</p>
    {/if}

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

    {/if}

    {#if pestana === 'prediccion'}
    {#if prediccion}
      <section class="totales">
        {#each [
          ['Hoy', prediccion.totals.d1],
          ['7 días', prediccion.totals.d7],
          ['30 días', prediccion.totals.d30],
          ['Desde el inicio', prediccion.totals.total],
          ['Mapas distintos', prediccion.totals.maps]
        ] as [etiqueta, valor] (etiqueta)}
          <article><span>{etiqueta}</span><strong>{numero(valor)}</strong></article>
        {/each}
      </section>
      <p class="nota">
        Cuenta cada mapa elegido en el menú del visor una vez por carga de página:
        volver a él mientras se compara no suma. El primero que pone un cambio de
        modelo no cuenta.
      </p>

      {#if prediccion.maps.length}
        <h2>Mapas más vistos <small>({prediccion.maps.length})</small></h2>
        <table>
          <thead>
            <tr>
              <th>Mapa</th><th>Modelo</th><th>Categoría</th>
              <th class="num">Hoy</th><th class="num">7 d</th><th class="num">30 d</th>
              <th class="num">Total</th><th>Último</th>
            </tr>
          </thead>
          <tbody>
            {#each prediccion.maps as fila (`${fila.model}|${fila.product}`)}
              <tr>
                <td title={fila.product}>
                  {fila.label || fila.product}
                  <span class="barra" style="width: {(fila.d30 / maximoPrediccion) * 100}%"></span>
                </td>
                <td class="red">{MODELOS_PREDICCION[fila.model] || fila.model}</td>
                <td class="red">{nombreCategoria(fila.category)}</td>
                <td class="n">{numero(fila.d1)}</td>
                <td class="n">{numero(fila.d7)}</td>
                <td class="n">{numero(fila.d30)}</td>
                <td class="n">{numero(fila.total)}</td>
                <td class="fecha">{fecha(fila.last_epoch)}</td>
              </tr>
            {/each}
          </tbody>
        </table>

        <h2>Por categoría</h2>
        <table>
          <thead><tr><th>Categoría</th><th class="num">30 d</th><th class="num">Total</th></tr></thead>
          <tbody>
            {#each prediccion.categories as fila (fila.category)}
              <tr>
                <td>{nombreCategoria(fila.category)}</td>
                <td class="n">{numero(fila.d30)}</td>
                <td class="n">{numero(fila.total)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <p class="vacio">Todavía no se ha registrado ningún mapa de predicción.</p>
      {/if}
    {:else}
      <p class="vacio">No se pudieron consultar los mapas de predicción.</p>
    {/if}
    {/if}

    {#if pestana === 'instalacion'}
    {#if instalacion}
      <section class="totales">
        {#each [
          ['Instalaciones (30 d)', totalPwa('launched', 'd30')],
          ['Instalaciones totales', totalPwa('launched')],
          ['Confirmadas por el navegador', totalPwa('installed')],
          ['Tarjeta enseñada', totalPwa('offered')]
        ] as [etiqueta, valor] (etiqueta)}
          <article><span>{etiqueta}</span><strong>{numero(valor)}</strong></article>
        {/each}
      </section>
      <p class="nota">
        Una instalación se cuenta la primera vez que se abre la app instalada: en iPhone y
        iPad es la única señal, porque Safari no avisa al añadirla. «Confirmadas por el
        navegador» solo existe en Chrome, Edge y compañía, y va aparte para no contar dos
        veces. La tarjeta se cuenta una vez por navegador.
      </p>

      <h2>Embudo</h2>
      <table>
        <thead><tr><th>Evento</th><th class="num">Hoy</th><th class="num">7 d</th><th class="num">30 d</th><th class="num">Total</th></tr></thead>
        <tbody>
          {#each instalacion.events as fila (fila.event)}
            <tr>
              <td title={fila.event}>{EVENTOS_PWA[fila.event] || fila.event}</td>
              <td class="n">{numero(fila.d1)}</td>
              <td class="n">{numero(fila.d7)}</td>
              <td class="n">{numero(fila.d30)}</td>
              <td class="n">{numero(fila.total)}</td>
            </tr>
          {/each}
        </tbody>
      </table>

      {#each [
        ['Por dispositivo', instalacion.by_device],
        ['Por sistema', instalacion.by_os],
        ['Por navegador', instalacion.by_browser]
      ] as [titulo, filas] (titulo)}
        <h2>{titulo}</h2>
        {#if filas.length}
          <table>
            <thead>
              <tr>
                <th></th><th class="num">Instalaciones (30 d)</th><th class="num">Instalaciones</th>
                <th class="num">Confirmadas</th><th class="num">Tarjeta enseñada</th>
              </tr>
            </thead>
            <tbody>
              {#each filas as fila (fila.value)}
                <tr>
                  <td>{nombrePwa(fila.value)}</td>
                  <td class="n">{numero(fila.launched_d30)}</td>
                  <td class="n">{numero(fila.launched)}</td>
                  <td class="n">{numero(fila.installed)}</td>
                  <td class="n">{numero(fila.offered)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <p class="vacio">Sin datos todavía.</p>
        {/if}
      {/each}

      <h2>Por forma de instalar</h2>
      {#if instalacion.by_method.length}
        <table>
          <thead>
            <tr>
              <th>Qué se le ofreció</th><th class="num">Enseñada</th><th class="num">Instrucciones</th>
              <th class="num">Diálogo aceptado</th><th class="num">Diálogo rechazado</th><th class="num">«Ahora no»</th>
            </tr>
          </thead>
          <tbody>
            {#each instalacion.by_method as fila (fila.value)}
              <tr>
                <td>{nombrePwa(fila.value)}</td>
                <td class="n">{numero(fila.offered)}</td>
                <td class="n">{numero(fila.instructions)}</td>
                <td class="n">{numero(fila.prompt_accepted)}</td>
                <td class="n">{numero(fila.prompt_dismissed)}</td>
                <td class="n">{numero(fila.dismissed)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <p class="vacio">Sin datos todavía.</p>
      {/if}
    {:else}
      <p class="vacio">No se pudieron consultar las instalaciones.</p>
    {/if}
    {/if}

    {#if pestana === 'cuarentena'}
    {#if cuarentena}
      <h2>
        En cuarentena
        <small>({cuarentena.active?.length || 0})</small>
      </h2>
      {#if cuarentena.active?.length}
        <table>
          <thead>
            <tr>
              <th>Estación</th><th>Red</th><th>Variable</th>
              <th>Motivo</th><th>Lleva</th><th>Desde</th>
            </tr>
          </thead>
          <tbody>
            {#each cuarentena.active as fila (fila.provider + fila.station_id + fila.variable + fila.day)}
              <tr>
                <td>
                  <a href={fichaDe(fila)} target="_blank" rel="noopener">
                    {fila.name || fila.station_id}
                  </a>
                  {#if fila.name}<small class="id">{fila.station_id}</small>{/if}
                </td>
                <td>{fila.provider}</td>
                <td>{VARIABLES[fila.variable] || fila.variable}</td>
                <td>{motivo(fila)}</td>
                <td>{duracion(fila)}</td>
                <td class="fecha">{fecha(fila.first_seen)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <p class="vacio">Ninguna estación en cuarentena ahora mismo.</p>
      {/if}

      <!-- El historial incluye las que ya salieron: una que reaparece cada
           pocos días no se ve en la tabla de arriba, pero aquí acumula. -->
      {#if cuarentena.history?.length}
        <h3 class="sub">Han pasado por cuarentena ({cuarentena.history.length})</h3>
        <table>
          <thead>
            <tr><th>Estación</th><th>Red</th><th>Variable</th><th>Días</th><th>Última vez</th></tr>
          </thead>
          <tbody>
            {#each cuarentena.history as fila (fila.provider + fila.station_id + fila.variable)}
              <tr>
                <td>
                  <a href={fichaDe(fila)} target="_blank" rel="noopener">
                    {fila.name || fila.station_id}
                  </a>
                </td>
                <td>{fila.provider}</td>
                <td>{VARIABLES[fila.variable] || fila.variable}</td>
                <td class="n">{fila.days_total}</td>
                <td class="fecha">{fecha(fila.last_seen)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
    {/if}

    {#if pestana === 'estaciones'}
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

    <h2>
      Estaciones
      <small>
        ({numero(estaciones.length)}{#if paginas > 1}
          · {(paginaActual - 1) * POR_PAGINA + 1}–{Math.min(paginaActual * POR_PAGINA, estaciones.length)}
        {/if})
      </small>
    </h2>
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
        {#each estacionesPagina as fila (claveDe(fila))}
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
                                        <div>Navegador al registrar: {evento.browser_languages || '—'}</div>
                                        <div>Petición que cargó la página: {evento.language_reason ? (evento.page_request_languages || 'Sin idioma enviado') : 'No registrada'}</div>
                                        <div>Motivo: {MOTIVOS_IDIOMA[evento.language_reason] || 'No registrado'}</div>
                                        <div>Cliente del registro: {CLIENTES[evento.request_client] || 'No registrado'}</div>
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

    {#if paginas > 1}
      <nav class="paginador" aria-label="Páginas de estaciones">
        <button type="button" onclick={() => (pagina = Math.max(1, paginaActual - 1))}
          disabled={paginaActual <= 1}>‹ Anterior</button>
        <span>Página {paginaActual} de {paginas}</span>
        <button type="button" onclick={() => (pagina = Math.min(paginas, paginaActual + 1))}
          disabled={paginaActual >= paginas}>Siguiente ›</button>
      </nav>
    {/if}
    {/if}
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
  .vacio { margin: 0 0 18px; font-size: 0.78rem; color: var(--muted); }
  td .id { display: block; font-size: 0.68rem; color: var(--muted); }
  td a { color: var(--accent); text-decoration: none; }
  td a:hover { text-decoration: underline; }

  .pestanas { display: flex; gap: 6px; margin: 0 0 20px; flex-wrap: wrap; }
  .nota { margin: 10px 0 0; font-size: 0.7rem; color: var(--muted); }
  td .barra {
    display: block; height: 3px; margin-top: 4px; border-radius: 2px;
    background: var(--accent); opacity: 0.55; min-width: 2px;
  }
  .pestanas button {
    padding: 7px 14px; border: 1px solid var(--border); border-radius: 999px;
    background: var(--panel-2); color: var(--ink-2);
    font: inherit; font-size: 0.78rem; font-weight: 650; cursor: pointer;
  }
  .pestanas button:hover { color: var(--ink); }
  .pestanas button.activa {
    background: var(--accent); border-color: var(--accent); color: #fff;
  }
  .pestanas .cuenta {
    margin-left: 6px; padding: 1px 6px; border-radius: 999px;
    background: var(--chip-warn-bg); color: var(--chip-warn-fg); font-size: 0.72rem;
  }

  .mapa { margin: 0 0 16px; position: relative; }
  .rotulo {
    position: absolute; transform: translate(12px, -50%); pointer-events: none;
    padding: 6px 10px; border: 1px solid var(--border-2); border-radius: var(--r-sm);
    background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
    font-size: 0.74rem; white-space: nowrap; z-index: 2;
  }
  .rotulo strong { display: block; font-weight: 700; }
  .rotulo span { color: var(--muted); }
  .mapa svg {
    width: 100%; height: auto; display: block;
    border: 1px solid var(--border); border-radius: var(--r-sm);
  }
  .mapa .oceano { fill: var(--panel-2); }
  .mapa .pais { stroke: var(--border-2); stroke-width: 0.15; fill: var(--panel); }
  .mapa .pais.visitado { stroke: var(--border-2); stroke-width: 0.2; }
  .mapa .pais:hover { stroke: var(--ink); stroke-width: 0.5; }

  .leyenda {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin: -6px 0 18px; font-size: 0.72rem; color: var(--muted);
  }
  .leyenda span { margin-right: 4px; font-weight: 650; }
  .leyenda i { width: 22px; height: 10px; border-radius: 2px; display: inline-block; }
  .leyenda small { margin-right: 6px; }

  .paginador {
    display: flex; align-items: center; justify-content: center; gap: 14px;
    margin: 14px 0 4px; font-size: 0.78rem; color: var(--muted);
  }
  .paginador button {
    padding: 6px 12px; border: 1px solid var(--border); border-radius: var(--r-sm);
    background: var(--panel-2); color: var(--ink); font: inherit; cursor: pointer;
  }
  .paginador button:disabled { opacity: 0.4; cursor: default; }
  h3.sub { margin: 22px 0 8px; font-size: 0.82rem; color: var(--ink-2); }
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
