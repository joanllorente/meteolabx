<script>
  /** Selector global de unidades, compartido por toda MeteoLabX. */
  import { onMount } from 'svelte';
  import app from '../i18n/app-i18n.generated.js';
  import {
    chooseUnit,
    loadUnitPreferences,
    unitOptions as OPTIONS,
    unitPreferences as preferences
  } from '../units.svelte.js';

  let { language = 'es' } = $props();

  let open = $state(false);
  const texts = $derived(app.units?.[language] || app.units?.es || {});
  const fields = $derived(texts.fields || {});
  const temperatureLabel = $derived(OPTIONS.temperature[preferences.temperature] || '°C');

  // Véase ConnectMyStation: la cabecera con blur no puede ser el contenedor
  // de referencia de un diálogo que debe ocupar toda la ventana.
  function portal(node) {
    document.body.appendChild(node);
    return { destroy: () => node.remove() };
  }

  onMount(loadUnitPreferences);
</script>

<button
  class="units-trigger"
  type="button"
  title={texts.title}
  aria-label={texts.title}
  aria-expanded={open}
  onclick={() => (open = true)}
>{temperatureLabel}</button>

{#if open}
  <div
    class="units-backdrop"
    role="presentation"
    use:portal
    onclick={(event) => event.target === event.currentTarget && (open = false)}
  >
    <div class="units-modal" role="dialog" aria-modal="true" aria-labelledby="units-title">
      <header>
        <div>
          <h2 id="units-title">{texts.title}</h2>
          <p>{texts.description}</p>
        </div>
        <button class="close" type="button" aria-label={texts.close} title={texts.close} onclick={() => (open = false)}>×</button>
      </header>

      <div class="unit-list">
        {#each Object.entries(OPTIONS) as [category, options] (category)}
          <section class="unit-row">
            <h3>{fields[category]}</h3>
            <div class="choices" role="group" aria-label={fields[category]}>
              {#each Object.entries(options) as [value, label] (value)}
                <button
                  type="button"
                  class:active={preferences[category] === value}
                  aria-pressed={preferences[category] === value}
                  onclick={() => chooseUnit(category, value)}
                >{label}</button>
              {/each}
            </div>
          </section>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
  .units-trigger {
    min-width: 36px; height: 36px; padding: 0 9px;
    border: 1px solid var(--border); border-radius: 9px;
    background: var(--panel); color: var(--ink-2);
    font: inherit; font-size: 0.72rem; font-weight: 750;
  }
  .units-trigger:hover, .units-trigger[aria-expanded='true'] {
    color: var(--ink); border-color: var(--border-2); background: var(--card);
  }
  .units-backdrop {
    position: fixed; z-index: 110; inset: 0; display: grid; place-items: center;
    padding: 20px; background: rgb(4 10 20 / 0.58); backdrop-filter: blur(3px);
  }
  .units-modal {
    width: min(570px, 100%); max-height: calc(100vh - 40px); overflow-y: auto;
    padding: 20px; border: 1px solid var(--border-2); border-radius: 16px;
    background: var(--panel); color: var(--ink); box-shadow: 0 24px 80px rgb(0 0 0 / 0.3);
  }
  header {
    display: flex; justify-content: space-between; align-items: flex-start; gap: 20px;
    padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  h2 { margin: 0 0 4px; font-size: 1.15rem; }
  header p { margin: 0; color: var(--muted); font-size: 0.72rem; line-height: 1.45; }
  .close {
    flex: 0 0 auto; width: 32px; height: 32px; border: 1px solid var(--border);
    border-radius: 9px; background: var(--panel-2); color: var(--ink-2);
    font: inherit; font-size: 1.25rem; line-height: 1;
  }
  .close:hover { color: var(--ink); border-color: var(--border-2); }
  .unit-list { display: flex; flex-direction: column; padding-top: 5px; }
  .unit-row {
    display: grid; grid-template-columns: 130px 1fr; gap: 18px; align-items: center;
    padding: 13px 0; border-bottom: 1px solid var(--border);
  }
  .unit-row:last-child { border-bottom: 0; }
  h3 { margin: 0; font-size: 0.76rem; }
  .choices { display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; }
  .choices button {
    min-width: 52px; padding: 7px 10px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--panel-2); color: var(--muted);
    font: inherit; font-size: 0.7rem; font-weight: 650;
  }
  .choices button:hover { color: var(--ink); border-color: var(--border-2); }
  .choices button.active {
    color: #fff; border-color: var(--accent); background: var(--accent);
  }
  @media (max-width: 540px) {
    .units-backdrop { padding: 10px; }
    .units-modal { padding: 16px; }
    .unit-row { grid-template-columns: 1fr; gap: 8px; }
    .choices { justify-content: flex-start; }
  }
</style>
