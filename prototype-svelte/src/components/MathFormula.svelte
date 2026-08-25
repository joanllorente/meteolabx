<script>
  import katex from 'katex';
  import 'katex/dist/katex.min.css';

  let { expression, label = '' } = $props();

  const rendered = $derived(katex.renderToString(expression, {
    displayMode: true,
    throwOnError: false,
    strict: false,
    trust: false,
    output: 'htmlAndMathml'
  }));
</script>

<figure class="formula-block">
  {#if label}<figcaption>{label}</figcaption>{/if}
  <div class="formula" aria-label={label || expression}>{@html rendered}</div>
</figure>

<style>
  .formula-block{min-width:0;margin:10px 0;padding:0}
  figcaption{margin-bottom:5px;color:var(--muted);font-size:.55rem;line-height:1.35}
  .formula{max-width:100%;color:var(--ink);overflow-x:auto;overflow-y:hidden;padding:2px 0 4px}
  .formula :global(.katex-display){margin:0;text-align:left}
  .formula :global(.katex){font-size:.82rem}
  @media(max-width:760px){.formula :global(.katex){font-size:.74rem}}
</style>
