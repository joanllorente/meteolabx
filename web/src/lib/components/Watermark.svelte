<script>
  /**
   * Firma dentro del propio SVG.
   *
   * Va en el dibujo, no en la tarjeta: lo que se descarga y lo que se
   * comparte es el SVG, y una marca puesta en el HTML de alrededor no viaja
   * con la imagen. Discreta, en una esquina donde no hay serie que tapar.
   */
  let { x, y, anchor = 'end' } = $props();
</script>

<text {x} {y} text-anchor={anchor} class="watermark">meteolabx.com</text>

<style>
  .watermark {
    fill: var(--muted-2);
    font-size: 9px;
    font-weight: 650;
    letter-spacing: 0.04em;
    opacity: 0.62;
    user-select: none;
  }
</style>
