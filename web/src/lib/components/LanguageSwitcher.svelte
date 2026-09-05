<script>
  /**
   * Los mismos idiomas que declaran los `hreflang` de la página.
   *
   * Las fichas de estación traen sus alternativas ya calculadas —son las que
   * ve Google—, pero el mapa, el ranking y el histórico no tienen `hreflang`
   * y se quedaban sin selector: cambiar de pestaña era perder el idioma. Ahí
   * se derivan de la propia ruta, cambiándole el prefijo.
   */
  import { page } from '$app/state';
  import { LANGUAGES } from '$lib/seo/i18n.js';

  let { alternates = [], current } = $props();

  const codes = Object.keys(LANGUAGES);

  const options = $derived.by(() => {
    if (alternates?.length) {
      return alternates.map((entry) => ({
        code: entry.code,
        href: new URL(entry.url).pathname,
        title: LANGUAGES[entry.code]?.language_label || entry.code
      }));
    }
    const path = page?.url?.pathname || '/';
    const [, first, ...rest] = path.split('/');
    const search = page?.url?.search || '';
    // La raíz no lleva prefijo: cada idioma tiene su propia portada.
    if (!codes.includes(first)) {
      return codes.map((code) => ({
        code,
        href: `/${code}${search}`,
        title: LANGUAGES[code]?.language_label || code
      }));
    }
    return codes.map((code) => ({
      code,
      href: `/${[code, ...rest].join('/')}${search}`,
      title: LANGUAGES[code]?.language_label || code
    }));
  });
</script>

{#if options.length}
  <nav class="languages" aria-label="Idioma">
    {#each options as option (option.code)}
      <a
        href={option.href}
        hreflang={option.code}
        lang={option.code}
        title={option.title}
        aria-current={option.code === current ? 'page' : undefined}
      >{option.code.toUpperCase()}</a>
    {/each}
  </nav>
{/if}

<style>
  .languages { display: flex; gap: 2px; }
  .languages a {
    padding: 3px 6px;
    color: var(--muted);
    font-size: 0.74rem;
    font-weight: 600;
    text-decoration: none;
    border-radius: 6px;
  }
  .languages a:hover { color: var(--ink); background: var(--card); }
  .languages a[aria-current='page'] { color: var(--ink); background: var(--card); }
</style>
