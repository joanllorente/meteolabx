<script>
  /**
   * Tarjeta para instalar MeteoLabX como aplicación.
   *
   * Se pinta solo en el navegador: en el servidor no se sabe qué sistema ni
   * qué navegador hay al otro lado, y adivinarlo daría una tarjeta distinta
   * al hidratar. Desaparece dentro de la app instalada y durante un tiempo si
   * la persona dice «Ahora no».
   */
  import { onMount } from 'svelte';

  import { currentMethod, promptInstall, pwa, pwaContext, recordOnce } from '$lib/pwa/install.svelte.js';
  import { installText } from '$lib/pwa/install-i18n.js';
  import { recordPwaEvent } from '$lib/stats.js';

  let { language = 'es' } = $props();

  const DISMISSED = 'mlx-install-dismissed';
  const OFFERED = 'mlx-install-offered';
  // Quien dice «ahora no» no quiere verla en cada ficha que abra, pero
  // tampoco para siempre.
  const DISMISS_DAYS = 45;

  let mounted = $state(false);
  let dismissed = $state(false);
  let open = $state(false);
  let instructionsCounted = false;

  const text = $derived(installText(language));
  const method = $derived(mounted && pwa.ready ? currentMethod() : null);
  const steps = $derived(method ? text.steps[method] || [] : []);
  const visible = $derived(
    Boolean(method) && !dismissed && (method !== 'installed' || pwa.installedNow)
  );

  onMount(() => {
    try {
      const since = Number(localStorage.getItem(DISMISSED) || 0);
      dismissed = since > 0 && Date.now() / 1000 - since < DISMISS_DAYS * 86400;
    } catch {
      dismissed = false;
    }
    mounted = true;
  });

  // Se cuenta una vez por navegador: contarlo en cada ficha inflaría la cifra
  // con quien simplemente navega.
  //
  // Y no en cuanto aparece. Chrome y Edge avisan de que se puede instalar un
  // momento después de cargar: contada al instante, la tarjeta quedaba
  // apuntada como «instrucciones» aunque un segundo después fuera el botón
  // con diálogo, y esa fila del panel salía siempre a cero. Se espera un poco,
  // o se cuenta antes si la persona pulsa algo.
  const OFFER_DELAY_MS = 3000;
  const markOffered = () => {
    if (method && method !== 'installed') recordOnce(OFFERED, 'offered', pwaContext());
  };
  $effect(() => {
    if (!visible || method === 'installed') return;
    const timer = setTimeout(markOffered, OFFER_DELAY_MS);
    return () => clearTimeout(timer);
  });

  function install() {
    markOffered();
    promptInstall();
  }

  function toggleSteps() {
    markOffered();
    open = !open;
    if (open && !instructionsCounted) {
      instructionsCounted = true;
      recordPwaEvent('instructions', pwaContext());
    }
  }

  function dismiss() {
    markOffered();
    recordPwaEvent('dismissed', pwaContext());
    dismissed = true;
    try {
      localStorage.setItem(DISMISSED, String(Math.floor(Date.now() / 1000)));
    } catch {
      /* se oculta solo en esta visita */
    }
  }
</script>

{#if visible}
  <section class="install" class:open class:done={method === 'installed'} aria-label={text.title}>
    <img src="/icons/icon-192.png" alt="" width="40" height="40" />
    <div class="copy">
      <strong>{text.title}</strong>
      <p>{method === 'installed' ? text.installed : text.subtitle}</p>
    </div>
    {#if method !== 'installed'}
      <div class="actions">
        {#if method === 'prompt'}
          <button type="button" class="primary" onclick={install}>{text.install}</button>
        {:else}
          <!-- La etiqueta no cambia al abrir: «Ocultar instrucciones» no cabe
               en un móvil. Lo dice la flecha, y `aria-expanded` al lector. -->
          <button
            type="button"
            class="primary"
            aria-expanded={open}
            aria-label={open ? text.hide : text.how}
            onclick={toggleSteps}
          >
            {text.how}
            <svg class="chevron" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5l3-3" /></svg>
          </button>
        {/if}
        <button type="button" class="quiet" aria-label={text.dismiss} title={text.dismiss} onclick={dismiss}>
          <span class="label">{text.dismiss}</span>
          <svg class="close" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" /></svg>
        </button>
      </div>
    {/if}
    {#if open && steps.length && method !== 'installed'}
      <ol>
        {#each steps as step}
          <li>
            {#each step.split(/(\{share\}|\{add\})/) as part}
              {#if part === '{share}'}
                <!-- El botón Compartir de iOS: cuadrado abierto y flecha arriba. -->
                <svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 3v12M7.5 7.5 12 3l4.5 4.5" />
                  <path d="M8 10H6.5A1.5 1.5 0 0 0 5 11.5v8A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-8a1.5 1.5 0 0 0-1.5-1.5H16" />
                </svg>
              {:else if part === '{add}'}
                <!-- «Añadir a pantalla de inicio»: cuadrado redondeado con un más. -->
                <svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="4" y="4" width="16" height="16" rx="4" />
                  <path d="M12 8.5v7M8.5 12h7" />
                </svg>
              {:else}
                {part}
              {/if}
            {/each}
          </li>
        {/each}
      </ol>
    {/if}
  </section>
{/if}

<style>
  /* Rejilla de una fila —icono, texto, botones— y los pasos debajo, a todo
     el ancho del texto. Con flex, en un móvil el texto no cabía junto al icono
     y la tarjeta se partía en tres pisos. */
  .install {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center; column-gap: 14px;
    margin: 0 0 18px; padding: 12px 14px;
    border: 1px solid var(--border); border-radius: var(--r-md, 14px);
    background: var(--panel);
  }
  img { border-radius: 10px; }
  strong { display: block; font-size: 0.86rem; font-weight: 680; line-height: 1.25; }
  p { margin: 2px 0 0; font-size: 0.76rem; color: var(--muted); line-height: 1.35; }
  .actions { display: flex; align-items: center; gap: 6px; }
  button {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 7px 14px; border-radius: 9px; cursor: pointer;
    font: inherit; font-size: 0.76rem; font-weight: 650; white-space: nowrap;
  }
  .primary { border: 0; background: var(--accent); color: #fff; }
  .primary:hover { filter: brightness(1.08); }
  .quiet { border: 1px solid transparent; background: transparent; color: var(--muted); }
  .quiet:hover { color: var(--ink-2); border-color: var(--border); }
  .chevron, .close { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .chevron { transition: transform 0.15s ease; }
  .open .chevron { transform: rotate(180deg); }
  .close { display: none; }

  ol {
    grid-column: 2 / -1; margin: 10px 0 2px; padding-left: 1.2rem;
    font-size: 0.76rem; color: var(--ink-2); line-height: 1.5;
  }
  li + li { margin-top: 3px; }
  .glyph {
    display: inline-block; width: 1.35em; height: 1.35em; margin: 0 1px -0.3em;
    padding: 1px; border-radius: 5px; background: var(--panel-2, rgba(127, 127, 127, 0.12));
    fill: none; stroke: var(--accent); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round;
  }

  /* Móvil: una fila fina. Fuera la entradilla, «Ahora no» pasa a una ×, y los
     pasos ocupan todo el ancho al abrirse. */
  @media (max-width: 640px) {
    .install { column-gap: 10px; margin-bottom: 14px; padding: 8px 8px 8px 10px; border-radius: 12px; }
    img { width: 32px; height: 32px; border-radius: 8px; }
    strong { font-size: 0.8rem; }
    p { display: none; }
    /* La confirmación de «instalada» sí se lee: es todo lo que dice la tarjeta. */
    .done p { display: block; font-size: 0.72rem; }
    .actions { gap: 2px; }
    button { padding: 6px 10px; font-size: 0.72rem; }
    .quiet { padding: 6px 7px; }
    .quiet .label { display: none; }
    .close { display: block; }
    ol { grid-column: 1 / -1; margin-top: 8px; padding: 0 4px 2px 1.3rem; font-size: 0.74rem; }
  }
</style>
