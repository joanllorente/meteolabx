<script>
  /**
   * El pie de la aplicación, con el mismo contenido que el de Streamlit:
   * versión, «Novedades» y «Privacidad».
   *
   * Los textos no están escritos aquí: los exporta
   * `scripts/export_app_i18n.py` desde `locales/*.json`, que es de donde los
   * lee la app actual. Así los dos frontends no acaban contando historias
   * distintas sobre qué datos se guardan.
   *
   * El contenido sigue saliendo en el HTML del servidor, pero se presenta en
   * diálogos modales para no alargar ni desplazar la página.
   */
  import { onMount } from 'svelte';
  import app from '$lib/i18n/app-i18n.generated.js';

  let { language = 'es' } = $props();

  const texts = $derived(app.footer[language] || app.footer.es);
  const version = $derived(
    (texts.version || 'Versión {version}').replace('{version}', app.app_version)
  );

  /**
   * Las versiones publicadas, con la actual delante.
   *
   * La lista sale de los textos generados —`app.releases`—, no de una copia
   * escrita aquí: con la serie 1 retirada, esa copia seguía ofreciendo cuatro
   * pestañas cuyos textos ya no existían, y 1.4.0 se abría vacía.
   */
  const releaseEntry = (key) => ({
    number: `${key[0]}.${key[1]}.${key[2]}`,
    improvements: texts[`release_${key}_improvements`] || [],
    fixes: texts[`release_${key}_fixes`] || []
  });

  const releaseGroups = $derived(
    (app.releases || [])
      .map((key) => ({ id: key, label: `${key[0]}.${key[1]}.${key[2]}`, entries: [releaseEntry(key)] }))
      .filter((group) => group.entries.some((entry) => entry.improvements.length || entry.fixes.length))
  );

  let selectedRelease = $state('');
  const activeRelease = $derived(
    releaseGroups.find((group) => group.id === selectedRelease) || releaseGroups[0]
  );
  const activeId = $derived(activeRelease?.id || '');

  const PRIVACY_EMAIL = 'meteolabx@gmail.com';

  /**
   * El correo se escribe al montar, no en el servidor.
   *
   * Cloudflare tiene activada la ofuscación de direcciones: cuando encuentra
   * un `mailto:` en el HTML lo sustituye por un enlace a `/cdn-cgi/l/…` y un
   * `<span class="__cf_email__">`, y además cuela un `<script>` dentro del
   * contenedor. El marcado que llega al navegador deja entonces de coincidir
   * con el que Svelte espera al hidratar, y Svelte responde tirando el render
   * del servidor y remontando la aplicacion entera en el cliente. Eso repinta
   * la página de golpe —la caja de conexión se veía un instante sin estilos— y
   * deja al mapa construido sobre un contenedor ya descartado, con
   * «Cargando el mapa…» para siempre.
   *
   * Sin dirección en el HTML del servidor no hay nada que reescribir. El
   * diálogo de privacidad solo se abre con `showModal()`, así que quien no
   * tenga JavaScript no lo ve de todos modos.
   */
  let mounted = $state(false);
  onMount(() => {
    mounted = true;
  });

  const privacySections = $derived(
    [
      { title: texts.privacy_cookies_title, items: texts.privacy_cookies },
      { title: texts.privacy_browser_title, items: texts.privacy_browser },
      { title: texts.privacy_log_title, intro: texts.privacy_log_intro, items: texts.privacy_log_items, notes: texts.privacy_log_notes },
      { title: texts.privacy_purpose_title, intro: texts.privacy_purpose_intro, items: texts.privacy_purpose_items, notes: texts.privacy_purpose_note },
      { title: texts.privacy_infra_title, items: texts.privacy_infra },
      { title: texts.privacy_retention_title, items: texts.privacy_retention },
      // La frase de contacto termina en «puedes escribir a»: el correo lo pone
      // la aplicación, igual que en la actual, para que no viva repetido en
      // los seis idiomas.
      { title: texts.privacy_contact_title, items: texts.privacy_contact, email: PRIVACY_EMAIL }
    ].filter((section) => section.title)
  );

  const asList = (value) => (Array.isArray(value) ? value : value ? [value] : []);

  const SOURCES =
    'WU · WeatherLink · Windy PWS · AEMET · Meteocat · Euskalmet · Frost · ' +
    'Meteo-France · MeteoGalicia · NWS · POEM · Met Office · MeteoHub Italia · ' +
    'IPMA · GeoSphere · SMHI · ECCC · IEM';

  let newsDialog;
  let privacyDialog;

  function openModal(dialog) {
    if (!dialog || dialog.open) return;
    document.documentElement.classList.add('footer-modal-open');
    dialog.showModal();
  }

  function closeModal(dialog) {
    dialog?.close();
  }

  function modalClosed() {
    document.documentElement.classList.remove('footer-modal-open');
  }

  function closeFromBackdrop(event) {
    if (event.target === event.currentTarget) event.currentTarget.close();
  }
</script>

<footer class="foot">
  <div class="head">
    <span class="version">MeteoLabX · {version}</span>

    <button type="button" class="action" onclick={() => openModal(newsDialog)}>{texts.whats_new}</button>
    <button type="button" class="action" onclick={() => openModal(privacyDialog)}>{texts.privacy}</button>

    <a
      class="x-link"
      href="https://x.com/meteolabx"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="MeteoLabX en X"
      title="@meteolabx"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231Zm-1.161 17.52h1.833L7.084 4.126H5.117Z" />
      </svg>
    </a>
  </div>

  <p class="bottom">{texts.sources}: {SOURCES} · {texts.unaffiliated} · © 2026</p>
</footer>

<dialog
  class="modal"
  bind:this={newsDialog}
  aria-labelledby="footer-news-title"
  onclose={modalClosed}
  onclick={closeFromBackdrop}
>
  <div class="dialog-shell">
    <header class="dialog-head">
      <h2 id="footer-news-title">{texts.whats_new}</h2>
      <button type="button" class="close" onclick={() => closeModal(newsDialog)} aria-label={texts.close} title={texts.close}>×</button>
    </header>
    <div class="body">
      <!-- Con una sola versión publicada, una fila de una pestaña sobra. -->
      {#if releaseGroups.length > 1}
      <div class="release-tabs" role="tablist" aria-label={texts.whats_new}>
        {#each releaseGroups as group (group.id)}
          <button
            type="button"
            role="tab"
            aria-selected={activeId === group.id}
            class:active={activeId === group.id}
            onclick={() => (selectedRelease = group.id)}
          >{group.label}</button>
        {/each}
      </div>
      {/if}
      {#each activeRelease?.entries || [] as release (release.number)}
        <section>
          <h3 class="rel">{release.number}</h3>
          {#if release.improvements.length}
            <h4>{texts.improvements_title}</h4>
            <ul>{#each release.improvements as item (item)}<li>{item}</li>{/each}</ul>
          {/if}
          {#if release.fixes.length}
            <h4>{texts.fixes_title}</h4>
            <ul>{#each release.fixes as item (item)}<li>{item}</li>{/each}</ul>
          {/if}
        </section>
      {/each}
    </div>
  </div>
</dialog>

<dialog
  class="modal"
  bind:this={privacyDialog}
  aria-labelledby="footer-privacy-title"
  onclose={modalClosed}
  onclick={closeFromBackdrop}
>
  <div class="dialog-shell">
    <header class="dialog-head">
      <h2 id="footer-privacy-title">{texts.privacy}</h2>
      <button type="button" class="close" onclick={() => closeModal(privacyDialog)} aria-label={texts.close} title={texts.close}>×</button>
    </header>
    <div class="body">
      <h3 class="rel">{texts.privacy_title}</h3>
      <p>{texts.privacy_intro}</p>
      {#each privacySections as section (section.title)}
        <h4>{section.title}</h4>
        {#if section.intro}<p>{section.intro}</p>{/if}
        {#each asList(section.items) as item (item)}
          <p>
            <!-- El espacio va explícito: entre la llave y el bloque `#if` no
                 sobrevive ninguno, y quedaba «escribir ameteolabx@gmail.com». -->
            {item}{#if section.email && mounted}{' '}<a href={`mailto:${section.email}`}>{section.email}</a>.{/if}
          </p>
        {/each}
        {#each asList(section.notes) as note (note)}<p class="note">{note}</p>{/each}
      {/each}
    </div>
  </div>
</dialog>

<style>
  .foot { margin-top: 34px; padding-top: 16px; border-top: 1px solid var(--border); }
  .head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  .version { color: var(--muted); font-size: 0.86rem; font-weight: 700; white-space: nowrap; }

  .action {
    appearance: none; padding: 0; border: 0; background: transparent; cursor: pointer;
    color: var(--accent); font-size: 0.86rem; font-weight: 700;
    text-decoration: underline; text-decoration-thickness: 1.5px; text-underline-offset: 2px;
  }
  .action:hover, .action:focus-visible { color: var(--ink); }

  .x-link {
    display: inline-flex; align-items: center; align-self: center; margin-left: auto;
    color: var(--muted); opacity: 0.86;
  }
  .x-link:hover, .x-link:focus-visible { color: var(--accent); opacity: 1; }
  .x-link svg { display: block; width: 1rem; height: 1rem; fill: currentColor; }

  :global(html.footer-modal-open), :global(html.footer-modal-open body) { overflow: hidden; }
  .modal {
    width: min(860px, calc(100vw - 2rem)); max-width: none; max-height: min(82vh, 760px);
    padding: 0; border: 0; overflow: visible; color: var(--ink); background: transparent;
  }
  .modal::backdrop { background: rgb(4 7 12 / 0.68); backdrop-filter: blur(5px); }
  .dialog-shell {
    max-height: min(82vh, 760px); overflow-y: auto; overscroll-behavior: contain;
    border: 1px solid var(--border); border-radius: 18px;
    background: var(--panel); box-shadow: 0 26px 80px rgb(0 0 0 / 0.45);
  }
  .dialog-head {
    position: sticky; top: 0; z-index: 2; display: flex; align-items: center;
    justify-content: space-between; gap: 1rem; padding: 16px 18px 12px;
    border-bottom: 1px solid var(--border); background: var(--panel);
  }
  .dialog-head h2 { margin: 0; color: var(--ink); font-size: 1.08rem; font-weight: 850; }
  .close {
    display: grid; place-items: center; width: 2rem; height: 2rem; flex: 0 0 2rem;
    padding: 0; border: 1px solid var(--border); border-radius: 50%; cursor: pointer;
    background: var(--panel-2); color: var(--ink); font-size: 1.45rem; line-height: 1;
  }
  .close:hover, .close:focus-visible { border-color: var(--accent); background: var(--accent); color: #fff; }

  .body {
    padding: 16px 18px 20px;
  }
  .release-tabs { display: flex; gap: 8px; margin: 0 0 18px; overflow-x: auto; scrollbar-width: none; }
  .release-tabs::-webkit-scrollbar { display: none; }
  .release-tabs button {
    flex: 0 0 auto; padding: 5px 14px; border: 1px solid var(--border);
    border-radius: 999px; cursor: pointer; background: var(--panel-2);
    color: var(--accent); font: inherit; font-size: 0.82rem; font-weight: 750;
  }
  .release-tabs button:hover { border-color: var(--accent); }
  .release-tabs button.active { border-color: var(--accent); background: var(--accent); color: #fff; }
  .rel { font-size: 1rem; font-weight: 800; margin: 4px 0 8px; }
  .body section + section { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
  h4 { margin: 12px 0 5px; font-size: 0.8rem; font-weight: 700; color: var(--ink-2); }
  .body p { margin: 0 0 8px; color: var(--ink-2); font-size: 0.86rem; line-height: 1.58; }
  .body p.note { color: var(--muted); font-size: 0.8rem; }
  .body ul { margin: 0; padding-left: 1.15rem; color: var(--ink-2); font-size: 0.86rem; line-height: 1.55; }
  .body li { margin: 0 0 5px; }

  .bottom { margin-top: 16px; color: var(--muted-2); font-size: 0.74rem; line-height: 1.5; }

  @media (max-width: 600px) {
    .modal { width: calc(100vw - 1.3rem); max-height: calc(100dvh - 1.3rem); }
    .dialog-shell { max-height: calc(100dvh - 1.3rem); border-radius: 15px; }
    .dialog-head { padding: 14px 15px 11px; }
    .body { padding: 14px 15px 17px; }
  }
</style>
