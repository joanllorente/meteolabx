<script>
  import {
    autoconnectPending,
    currentConnection,
    loadConnection,
    spendAutoconnect
  } from '$lib/connection.svelte.js';
  /**
   * La raíz: el panel de observación sin estación conectada.
   *
   * No es una portada ni una pantalla de bienvenida — es la misma pestaña de
   * observación, con sus tarjetas a raya y la caja de conexión arriba, igual
   * que hace la aplicación actual cuando aún no has elegido estación.
   */
  import { onMount } from 'svelte';

  import AppShell from '$lib/components/AppShell.svelte';
  import ConnectionBar from '$lib/components/ConnectionBar.svelte';
  import ObservationPanel from '$lib/components/ObservationPanel.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import { observationModel } from '$lib/observation/model.js';
  import { unitPreferences } from '$lib/units.svelte.js';
  import { LANGUAGES, SITE_URL, providerLabel } from '$lib/seo/i18n.js';
  import { page } from '$app/state';
  import { ui } from '$lib/i18n/ui.js';
  import { appTabs } from '$lib/tabs.js';
  import { autoconnectSlug, loadFavourites } from '$lib/favourites.svelte.js';

  let { data } = $props();
  const lang = $derived(data.language);

  // Modelo vacío: las tarjetas existen y enseñan «—», que es lo que hay.
  const model = $derived(observationModel(null, {}, lang, unitPreferences));


  onMount(() => {
    loadFavourites();

    // Autoconexión: si hay una estación marcada para abrirse al entrar, se
    // abre. Solo cuando nadie ha pedido otra cosa —una búsqueda en la URL
    // significa que se quiere elegir, no volver a la de siempre.
    const target = autoconnectSlug();
    if (target && !location.search && autoconnectPending()) {
      spendAutoconnect();
      // El valor guardado es un slug, `RED/identificador`, o una ruta entera
      // —las estaciones propias, que se conectan con credencial.
      location.replace(target.startsWith('/') ? target : `/${lang}/observation/${target}`);
    }
  });

  // La estación conectada vive en el navegador: se lee al hidratar y las
  // pestañas dejan de perderla al pasar por aquí.
  onMount(loadConnection);
</script>

<svelte:head>
  <title>{ui(lang, 'home_title')}</title>
  <meta name="description" content={ui(lang, 'home_description')} />
  <link rel="canonical" href={`${SITE_URL}${page.params.lang ? `/${lang}` : '/'}`} />
  <!-- La raíz negocia el idioma con el navegador: es la versión x-default,
       y cada portada con prefijo declara la suya. -->
  {#each Object.keys(LANGUAGES) as code (code)}
    <link rel="alternate" hreflang={code} href={`${SITE_URL}/${code}`} />
  {/each}
  <link rel="alternate" hreflang="x-default" href={`${SITE_URL}/`} />
  <meta name="robots" content="index, follow, max-image-preview:large" />
</svelte:head>

<AppShell language={lang} tabs={appTabs({
    language: lang,
    slug: currentConnection()?.slug || '',
    observationPath: currentConnection()?.path || ''
  })} active="observation" alternates={[]}>
  <ConnectionBar
    language={lang}
    query={data.query}
    place={data.place}
    results={data.results}
    failed={data.failed}
    searched={data.searched}
    hideAmateur={data.hideAmateur}
  />

  <ObservationPanel {model} language={lang} />

  <SiteFooter language={lang} />
</AppShell>
