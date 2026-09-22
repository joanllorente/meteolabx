<script>
  /**
   * Lo que enseña la ficha de un pluviómetro manual en lugar de las tarjetas.
   *
   * No hay lectura actual que pintar: las tarjetas saldrían todas a raya y el
   * aviso de «no publica datos» sería falso, porque sí publica, solo que la
   * lluvia de cada día y con uno o dos días de retraso. Se dice qué estación
   * es, cuál es la última lluvia publicada y dónde está la serie.
   */
  import { ui } from '$lib/i18n/ui.js';
  import { latestDailyText } from '$lib/observation/manual-daily.js';

  let { language, daily = null, historyHref = '' } = $props();
</script>

<section class="manual">
  <p class="what">{ui(language, 'manual_daily_station')}</p>
  <p class="latest">{latestDailyText(language, daily)}</p>
  {#if historyHref}
    <a class="history" href={historyHref}>{ui(language, 'manual_daily_history')} →</a>
  {/if}
</section>

<style>
  .manual {
    margin: 0 0 20px; padding: 16px 18px;
    border: 1px solid var(--border); border-radius: var(--r-sm);
    background: var(--panel); color: var(--ink);
  }
  .what { margin: 0 0 10px; color: var(--ink-2); font-size: 0.84rem; line-height: 1.55; }
  .latest { margin: 0 0 12px; font-size: 1rem; font-weight: 700; }
  .history { font-size: 0.84rem; font-weight: 600; }
</style>
