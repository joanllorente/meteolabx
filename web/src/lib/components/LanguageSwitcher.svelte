<script>
  import { page } from '$app/state';
  import { LANGUAGES } from '$lib/seo/i18n.js';

  let { current } = $props();

  const codes = Object.keys(LANGUAGES);

  const options = $derived.by(() => {
    const path = page?.url?.pathname || '/';
    const [, first, ...rest] = path.split('/');
    const search = page?.url?.search || '';
    const hash = page?.url?.hash || '';
    const selection = (path, code) => {
      const target = new URL(path, page.url);
      target.searchParams.set('set_language', code);
      return target.pathname + target.search + hash;
    };
    // La raíz no lleva prefijo: cada idioma tiene su propia portada.
    if (!codes.includes(first)) {
      return codes.map((code) => ({
        code,
        href: selection(`/${code}${search}`, code),
        title: LANGUAGES[code]?.language_label || code
      }));
    }
    return codes.map((code) => ({
      code,
      href: selection(`/${[code, ...rest].join('/')}${search}`, code),
      title: LANGUAGES[code]?.language_label || code
    }));
  });
</script>

{#if options.length}
  <nav class="languages" aria-label="Idioma">
    {#each options as option (option.code)}
      <a
        href={option.href}
        data-sveltekit-reload
        data-sveltekit-preload-data="off"
        rel="nofollow"
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
