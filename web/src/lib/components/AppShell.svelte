<script>
  import { onMount, setContext } from 'svelte';
  /**
   * El armazón de la aplicación: barra superior, pestañas y cinta de estación.
   *
   * Es el mismo del prototipo. La pestaña de observación ya no lleva a ningún
   * sitio —esta página *es* el panel—; el resto apuntan todavía a la app
   * antigua y llevan una marca para que se note qué queda por migrar.
   */
  import Isobars from './Isobars.svelte';
  import NavigationProgress from './NavigationProgress.svelte';
  import ConnectMyStation from './ConnectMyStation.svelte';
  import UnitPreferences from './UnitPreferences.svelte';
  import StationMenu from './StationMenu.svelte';
  import LanguageSwitcher from './LanguageSwitcher.svelte';
  import { Activity, History, LayoutDashboard, Map, TrendingUp, TriangleAlert, Trophy } from '@lucide/svelte';
  import { currentMode, cycleTheme, loadTheme } from '$lib/theme.svelte.js';
  import { importLegacyStorage } from '$lib/legacy-storage.js';
  import { favouriteKey, listFavourites, loadFavourites, toggleFavourite } from '$lib/favourites.svelte.js';
  import { currentConnection } from '$lib/connection.svelte.js';
  import { loadCredentials } from '$lib/credentials.svelte.js';
  import { loadViewSearches, viewSearch } from '$lib/view-memory.svelte.js';
  import { locale, num } from '$lib/format.js';
  import { convertUnit, unitLabel, unitPreferences } from '$lib/units.svelte.js';
  import app from '$lib/i18n/app-i18n.generated.js';
  import { ui } from '$lib/i18n/ui.js';

  let {
    language,
    tabs = [],
    subtabs = [],
    active = 'observation',
    station = null,
    alternates = [],
    measuredAt = '',
    // Zona horaria de la estación: la hora de la medida es la suya.
    timeZone = '',
    timestamp = '',
    live = false,
    disconnectHref = '',
    onDisconnect = null,
    children
  } = $props();

  /**
   * El idioma, al alcance de cualquier componente de dentro.
   *
   * Se pasa como función para que siga siendo el vigente si cambia sin
   * recargar. Lo usan piezas hondas —el marco de las gráficas— a las que
   * llevarlo por props obligaría a atravesar media docena de componentes que
   * no tienen nada que ver con el idioma.
   */
  setContext('mlx-language', () => language);

  /**
   * Cuánto hace de la última medida.
   *
   * La página se renderiza en el servidor, así que una antigüedad calculada
   * allí nace vieja: se recalcula en el navegador y se refresca cada minuto.
   * Antes la cinta decía «En directo» junto a una hora, y ninguna de las dos
   * cosas contaba lo que importa —si el dato es de hace cinco minutos o de
   * hace tres horas—.
   */
  let ageSeconds = $state(null);

  $effect(() => {
    const stamp = Date.parse(timestamp);
    if (!Number.isFinite(stamp)) {
      ageSeconds = null;
      return;
    }

    // El primer minuto se cuenta segundo a segundo: con una estación propia,
    // que publica cada diez o quince segundos, es la única forma de ver que el
    // dato acaba de llegar. Pasado el minuto ya no aporta nada —lo que se lee
    // son minutos— y basta con mirar cada quince segundos, así el minuto
    // cambia como mucho quince segundos tarde.
    //
    // El ritmo se reajusta al vuelo, y nunca leyendo `ageSeconds`: leer aquí
    // dentro el estado que este mismo efecto escribe lo dejaría girando sin
    // fin. De ahí que la cuenta viaje en una variable local.
    let timer;
    let interval = 0;

    const tick = () => {
      const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
      ageSeconds = seconds;

      const wanted = seconds < 60 ? 1000 : 15000;
      if (wanted !== interval) {
        interval = wanted;
        clearInterval(timer);
        timer = setInterval(tick, interval);
      }
    };

    tick();
    return () => clearInterval(timer);
  });

  /**
   * Huso de la estación, y solo cuando no es el del visitante.
   *
   * La hora de la medida es la local de la estación: «14:00» significa media
   * tarde allí, que es lo que da sentido a los 33 °C. Traducirla a la hora de
   * quien mira la vaciaría de significado. Pero sin etiqueta, quien se conecta
   * a Nueva Zelanda desde aquí no sabe de quién es ese «14:00», así que se
   * marca el huso; y solo entonces, para no añadir ruido a la inmensa mayoría
   * de conexiones, que son a estaciones del propio país.
   */
  // El tema lo aplica el script del `<head>`; aquí se lee para saber qué
  // icono toca y, en automático, para seguir al sistema mientras la página
  // está abierta. `loadTheme` devuelve su propia limpieza.
  onMount(loadTheme);

  // Los filtros con los que se dejó cada pestaña. Se leen al montar —en el
  // servidor no hay sesión— y se pegan al enlace de la barra, para que
  // volver al mapa devuelva el mapa que se estaba mirando.
  onMount(loadViewSearches);

  // Favoritos, credenciales y autoconexión de la interfaz anterior. Corre
  // una vez por navegador y solo rellena lo que esté vacío; después se
  // aparta. Recarga la página si trajo algo, para que la barra los vea.
  onMount(async () => {
    const traido = await importLegacyStorage(language);
    if (traido?.favoritos || traido?.credenciales?.length) {
      loadFavourites();
      loadCredentials();
    }
  });

  /**
   * Guardar en favoritos la estación conectada, desde la propia cinta.
   *
   * La opción ya estaba en el menú de la estrella de la barra, pero había que
   * saber que existía. La identidad es la de la conexión —slug o ruta—, que
   * cada página fija al abrir la estación, y la lista es estado compartido:
   * guardar aquí enciende también la estrella de la barra.
   */
  const connection = $derived(disconnectHref ? currentConnection() : null);
  const connectionKey = $derived(favouriteKey(connection || {}));
  const savedConnection = $derived(
    Boolean(connectionKey) && listFavourites().some((item) => favouriteKey(item) === connectionKey)
  );

  function toggleConnectionFavourite() {
    if (!connection) return;
    toggleFavourite({
      slug: connection.slug || '',
      path: connection.path || '',
      name: connection.name || station?.name || '',
      provider: connection.provider || ''
    });
  }

  /** Altitud de la cinta, en metros o pies según las unidades elegidas. */
  const altitudeText = $derived(
    Number.isFinite(station?.elevation)
      ? `${num(convertUnit(station.elevation, 'altitude', unitPreferences), { language, decimals: 0 })} ${unitLabel('altitude', unitPreferences)}`
      : ''
  );

  const navTabs = $derived(
    tabs.map((tab) => {
      const search = tab.href.includes('?') ? '' : viewSearch(tab.id);
      return search ? { ...tab, href: `${tab.href}${search}` } : tab;
    })
  );

  /** Qué hará el botón al pulsarlo, que es lo que debe anunciar. */
  const themeLabel = $derived(
    ui(language, currentMode() === 'auto' ? 'theme_light' : currentMode() === 'light' ? 'theme_dark' : 'theme_auto')
  );

  let zoneLabel = $state('');

  /** Minutos que ese huso lleva sobre UTC en ese instante. */
  function offsetMinutes(zone, when) {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone: zone, hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      })
        .formatToParts(when)
        .map((part) => [part.type, part.value])
    );
    const asUtc = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour) % 24, Number(parts.minute), Number(parts.second)
    );
    return Math.round((asUtc - when.getTime()) / 60000);
  }

  $effect(() => {
    const stamp = Date.parse(timestamp);
    if (!timeZone || !Number.isFinite(stamp)) {
      zoneLabel = '';
      return;
    }
    const when = new Date(stamp);
    try {
      const here = Intl.DateTimeFormat().resolvedOptions().timeZone;
      // Se comparan desplazamientos, no nombres: Madrid y París son husos
      // distintos con la misma hora, y ahí la etiqueta sobra.
      if (here && offsetMinutes(here, when) === offsetMinutes(timeZone, when)) {
        zoneLabel = '';
        return;
      }
      zoneLabel =
        new Intl.DateTimeFormat(locale(language), { timeZone, timeZoneName: 'short' })
          .formatToParts(when)
          .find((part) => part.type === 'timeZoneName')?.value || '';
    } catch {
      zoneLabel = '';
    }
  });

  const ageLabel = $derived.by(() => {
    if (ageSeconds === null) return '';
    if (ageSeconds < 60) return ui(language, 'ago_seconds').replace('{n}', String(ageSeconds));
    const minutes = Math.floor(ageSeconds / 60);
    if (minutes < 60) return ui(language, 'ago_minutes').replace('{n}', String(minutes));
    return ui(language, 'ago_hours').replace('{n}', String(Math.floor(minutes / 60)));
  });

  // Pasada una hora sin dato nuevo, «Actualizado» en verde sería mentira.
  const fresh = $derived(ageSeconds === null || ageSeconds < 3600);
  const primaryActive = $derived(
    active === 'trends' || active === 'historical' ? 'observation' : active
  );
  const navIcons = { Activity, History, LayoutDashboard, Map, TrendingUp, TriangleAlert, Trophy };
</script>

<div class="shell">
  <NavigationProgress />

  <header class="topnav">
    <a class="brand" href="/">
      <!-- El logotipo de MeteoLabX, el mismo del icono de la aplicación.
           Se sirve a 72 px —el triple del tamaño en pantalla— para que se vea
           nítido en pantallas de retina sin arrastrar los 41 KB del icono
           original. -->
      <img class="mark" src="/brand-mark.png" alt="" width="26" height="26" decoding="async" />
      <span class="brand-txt">
        <strong>MeteoLabX</strong>
        <!-- La versión, al lado del nombre: sin abrir Novedades se sabe qué
             está sirviendo el navegador, que con despliegues seguidos es la
             primera pregunta. -->
        <span class="brand-version">{app.app_version}</span>
      </span>
    </a>

    <nav class="tabs" aria-label="Secciones">
      {#each navTabs as tab (tab.id)}
        {#if tab.disabled}
          <span class="tab off" aria-disabled="true"><span>{tab.label}</span></span>
        {:else}
          <a
            href={tab.href}
            class="tab"
            class:active={tab.id === primaryActive}
            aria-current={tab.id === primaryActive ? 'page' : undefined}
            data-sveltekit-reload={tab.external ? '' : undefined}
          >
            {#if tab.symbol}
              <span class="nav-symbol" aria-hidden="true">{tab.symbol}</span>
            {:else if tab.icon && navIcons[tab.icon]}
              {@const NavIcon = navIcons[tab.icon]}
              <NavIcon size={15} strokeWidth={1.9} aria-hidden="true" />
            {/if}
            <span>{tab.label}</span>
          </a>
        {/if}
      {/each}
    </nav>

    <div class="right">
      <ConnectMyStation {language} />
      <StationMenu {language} />
      <LanguageSwitcher {alternates} current={language} />
      <UnitPreferences {language} />
      <button
        class="theme"
        type="button"
        onclick={cycleTheme}
        title={themeLabel}
        aria-label={themeLabel}
      >
        {#if currentMode() === 'auto'}
          <!-- mitad sol, mitad luna: manda el dispositivo -->
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8.2" />
            <path d="M12 3.8a8.2 8.2 0 0 0 0 16.4Z" fill="currentColor" stroke="none" />
          </svg>
        {:else if currentMode() === 'light'}
          <!-- sol -->
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="4.4" />
            <g stroke-linecap="round">
              <path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6" />
            </g>
          </svg>
        {:else}
          <!-- luna -->
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2Z" />
          </svg>
        {/if}
      </button>
    </div>
  </header>

  {#if station}
    <div class="stripe">
      <Isobars lines={7} />
      <div class="stripe-inner">
        <div class="s-left">
          <span class="s-prov">{station.provider}</span>
          <div class="s-title">
            <h1>{station.name}</h1>
            {#if connectionKey}
              <button
                type="button"
                class="favourite"
                class:saved={savedConnection}
                aria-pressed={savedConnection}
                aria-label={ui(language, savedConnection ? 'remove_favourite' : 'save_favourite')}
                title={ui(language, savedConnection ? 'remove_favourite' : 'save_favourite')}
                onclick={toggleConnectionFavourite}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8Z" />
                </svg>
              </button>
            {/if}
          </div>
          {#if station.place}<span class="s-place">{station.place}</span>{/if}
        </div>
        <div class="s-facts">
          <span><small>ID</small>{station.id}</span>
          {#if altitudeText}<span><small>ALT</small>{altitudeText}</span>{/if}
          <span><small>LAT</small>{station.lat}</span>
          <span><small>LON</small>{station.lon}</span>
        </div>
        <div class="s-live">
          {#if live}
            <span class="live" class:stale={!fresh}>
              <i></i>{fresh ? ui(language, 'updated') : ui(language, 'stale_data')}
            </span>
          {/if}
          {#if measuredAt}
            <span class="s-time">
              <time datetime={timestamp}>
                {ui(language, 'last_reading').replace('{time}', measuredAt)}{zoneLabel ? ` ${zoneLabel}` : ''}
              </time>
              {#if ageLabel}<span class="age">· {ageLabel}</span>{/if}
            </span>
          {/if}
          {#if disconnectHref}
            <a class="disconnect" href={disconnectHref} data-sveltekit-noscroll onclick={onDisconnect}>
              {ui(language, 'disconnect')}
            </a>
          {/if}
        </div>
      </div>
    </div>
  {/if}

  {#if subtabs.length}
    <div class="subnav-band">
      <nav class="subtabs" aria-label={ui(language, 'tab_observation')}>
        {#each subtabs as tab (tab.id)}
          <a
            href={tab.href}
            class="subtab"
            class:active={tab.id === active}
            aria-current={tab.id === active ? 'page' : undefined}
          >
            {#if tab.icon && navIcons[tab.icon]}
              {@const SubIcon = navIcons[tab.icon]}
              <SubIcon size={15} strokeWidth={1.9} aria-hidden="true" />
            {/if}
            <span>{tab.label}</span>
          </a>
        {/each}
      </nav>
    </div>
  {/if}

  <main class="wrap">
    {@render children()}
  </main>
</div>

<style>
  .shell {
    min-height: 100vh;
    background:
      radial-gradient(circle at 82% -8%, var(--bg-grad-1), transparent 42%),
      radial-gradient(circle at 4% 108%, var(--bg-grad-2), transparent 46%),
      var(--bg);
  }

  .topnav {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 22px;
    padding: 11px clamp(14px, 3vw, 30px);
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border);
  }
  .brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
  .mark { width: 26px; height: 26px; flex: none; display: block; border-radius: 7px; }
  .brand-txt { display: inline-flex; align-items: baseline; gap: 6px; }
  .brand-txt strong { font-size: 0.98rem; font-weight: 750; letter-spacing: -0.01em; }
  .brand-version { font-size: 0.64rem; font-weight: 700; color: var(--muted); letter-spacing: 0.02em; }

  .tabs { display: flex; gap: 3px; margin-left: 6px; overflow-x: auto; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .tabs :global(.tab) {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 7px 13px; border-radius: 9px;
    font-size: 0.82rem; font-weight: 600; color: var(--muted);
    text-decoration: none; white-space: nowrap;
    transition: background 0.16s, color 0.16s;
  }
  .tabs a.tab:hover { color: var(--ink-2); background: var(--card); }
  .tabs a.tab.active { color: var(--ink); background: var(--card); }
  .tabs .off { color: var(--muted-2); cursor: default; }
  .nav-symbol {
    width: 15px; height: 15px;
    display: inline-grid; place-items: center;
    font: 700 1.08rem/1 Georgia, 'Times New Roman', serif;
  }

  .disconnect {
    padding: 6px 12px; border-radius: 8px;
    border: 1px solid var(--border-2); background: var(--card);
    color: var(--ink-2); font-size: 0.74rem; font-weight: 650;
    text-decoration: none; white-space: nowrap;
  }
  .disconnect:hover { color: var(--ink); border-color: var(--accent); }
  /* Solo la estrella, junto al nombre: un botón con texto en la línea de
     estado la hacía saltar de fila y engordaba la cinta. */
  .s-title { display: flex; align-items: center; gap: 8px; }
  .favourite {
    display: grid; place-items: center; flex: none;
    width: 34px; height: 34px; padding: 0;
    border: 0; border-radius: 50%; background: none; color: var(--muted-2);
  }
  .favourite:hover { color: var(--accent); background: var(--card); }
  .favourite svg {
    width: 22px; height: 22px;
    fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linejoin: round;
  }
  .favourite.saved { color: var(--accent); }
  .favourite.saved svg { fill: currentColor; }
  .right { margin-left: auto; display: flex; align-items: center; gap: 12px; }

  /* Mismas medidas que el selector de unidades, que es su vecino: 30 px al
     lado de 36 se leía como un botón a medio hacer. */
  .theme {
    display: grid; place-items: center;
    width: 36px; height: 36px; flex: none;
    border: 1px solid var(--border); border-radius: 9px;
    background: var(--panel); color: var(--ink-2);
  }
  .theme:hover { color: var(--ink); border-color: var(--border-2); }
  .theme svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; }

  .stripe { position: relative; overflow: hidden; border-bottom: 1px solid var(--border); background: var(--panel-2); }
  .stripe-inner {
    position: relative;
    display: flex; flex-wrap: wrap; align-items: flex-end; gap: 22px;
    width: min(1240px, calc(100% - 40px)); margin: auto;
    padding: 26px 0 22px;
  }
  .s-left { display: flex; flex-direction: column; gap: 5px; }
  .s-prov { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; color: var(--accent); }
  .s-left h1 { font-size: clamp(1.5rem, 3.6vw, 2.3rem); font-weight: 750; letter-spacing: -0.03em; line-height: 1.08; }
  .s-place { font-size: 0.84rem; color: var(--muted); }

  .s-facts { display: flex; gap: 22px; padding-bottom: 4px; }
  .s-facts span { display: flex; flex-direction: column; gap: 2px; font-size: 0.86rem; font-weight: 640; font-variant-numeric: tabular-nums; }
  .s-facts small { font-size: 0.6rem; font-weight: 700; letter-spacing: 0.08em; color: var(--muted-2); }

  .s-live { margin-left: auto; display: flex; align-items: center; gap: 12px; padding-bottom: 4px; }
  .live { display: inline-flex; align-items: center; gap: 6px; font-size: 0.72rem; font-weight: 650; color: #43c98a; }
  .live i { width: 7px; height: 7px; border-radius: 50%; background: #43c98a; box-shadow: 0 0 0 3px rgba(67, 201, 138, 0.18); }
  .s-time { display: inline-flex; gap: 5px; font-size: 0.76rem; color: var(--muted); font-variant-numeric: tabular-nums; }
  .age { color: var(--muted-2); }
  .live.stale { color: #e0a63f; }
  .live.stale i { background: #e0a63f; box-shadow: 0 0 0 3px rgba(224, 166, 63, 0.18); }

  .subnav-band { border-bottom: 1px solid var(--border); background: var(--panel-2); }
  .subtabs {
    width: min(1240px, calc(100% - 40px)); margin: auto; padding: 8px 0;
    display: flex; align-items: center; gap: 5px; overflow-x: auto; scrollbar-width: none;
  }
  .subtabs::-webkit-scrollbar { display: none; }
  .subtab {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 7px 12px; border-radius: 8px;
    color: var(--muted); font-size: 0.78rem; font-weight: 650;
    text-decoration: none; white-space: nowrap;
    transition: color 0.16s, background 0.16s;
  }
  .subtab:hover { color: var(--ink-2); background: var(--card); }
  .subtab.active { color: var(--ink); background: var(--card); }

  .wrap { width: min(1240px, calc(100% - 40px)); margin: auto; padding: 26px 0 40px; }

  @media (max-width: 760px) {
    /* Margen lateral de 12 px, el mismo que ya tiene la barra superior: con
       20 px por lado un móvil de 390 perdía un diez por ciento del ancho. Las
       tres bandas cambian a la vez para que los bordes sigan alineados. */
    .stripe-inner,
    .subtabs,
    .wrap { width: calc(100% - 24px); }

    /* Los cuatro datos de la estación y la línea de estado se reparten el
       ancho de la cinta en vez de apelotonarse a la izquierda: ahí no compiten
       con nada, y así se leen sin acercar la vista. */
    .s-facts { width: 100%; justify-content: space-between; gap: 10px; }
    .s-live { width: 100%; margin-left: 0; justify-content: space-between; }
    .disconnect { margin-left: auto; }

    /* En una pantalla estrecha la barra no cabe en una sola fila: las
       pestañas quedaban encogidas hasta desaparecer —tienen scroll propio,
       así que el navegador las reducía a cero— y no había forma de llegar al
       mapa ni al ranking. Se bajan a una segunda fila, a lo ancho. */
    .topnav { flex-wrap: wrap; gap: 10px; padding: 9px 12px 0; }
    /* El nombre deja sitio: el logotipo ya identifica la aplicación, y el
       hueco se aprovecha mejor con el rótulo del botón de conectar. */
    .brand { display: none; }
    .brand-txt { display: none; }
    /* Sin el logo, todos los controles vuelven a caber y permanecen visibles
       en la primera fila del móvil. */
    .right { width: 100%; margin-left: 0; justify-content: space-between; gap: 6px; }
    .tabs {
      order: 3; width: 100%;
      margin: 0; padding-bottom: 7px;
    }
  }

</style>
