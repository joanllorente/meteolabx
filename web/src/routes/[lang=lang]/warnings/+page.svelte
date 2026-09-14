<script>
  import { onMount } from 'svelte';
  import AppShell from '$lib/components/AppShell.svelte';
  import SiteFooter from '$lib/components/SiteFooter.svelte';
  import WarningsPanel from '$lib/components/WarningsPanel.svelte';
  import { currentConnection, loadConnection } from '$lib/connection.svelte.js';
  import { ui } from '$lib/i18n/ui.js';
  import { appTabs } from '$lib/tabs.js';

  let { data } = $props();
  const lang = $derived(data.lang);

  onMount(loadConnection);
</script>

<svelte:head>
  <title>{ui(lang, 'warnings_title')} | MeteoLabX</title>
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<AppShell language={lang} tabs={appTabs({
    language: lang,
    slug: currentConnection()?.slug || '',
    observationPath: currentConnection()?.path || ''
  })} active="warnings" alternates={[]}>
  <WarningsPanel {data} language={lang} />
  <SiteFooter language={lang} />
</AppShell>
