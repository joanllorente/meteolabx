/**
 * Huso de quien mira.
 *
 * Las estaciones propias no están en ningún catálogo, así que el backend no
 * sabe en qué huso están. La zona de este navegador es la mejor apuesta para
 * la estación que alguien tiene en su casa: sin ella, las lecturas del día se
 * colocaban en hora UTC —dos horas antes de la real en verano—.
 */
export function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
