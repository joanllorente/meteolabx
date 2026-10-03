/**
 * Avisos del backend que la ficha enseña.
 *
 * El backend distingue cada avería —termómetro congelado, frío imposible para
 * la latitud y el mes, pluviómetro que acumula lluvia que ningún parte
 * confirma— porque necesita saber cuál es para decidir qué excluye del
 * ranking. Al visitante eso no le sirve de nada: le basta con saber que no se
 * fíe. Por eso todos se resumen en un único mensaje.
 *
 * La lista es explícita, no un «todo lo que llegue»: `data_age` también es un
 * warning y no debe salir aquí, porque la cabecera ya dice «sin datos
 * recientes · hace 5 h» y repetirlo solo añade ruido.
 */
const CODIGOS_DATOS_DUDOSOS = new Set([
  'suspect_temperature',
  'suspect_precipitation',
  'unreported_precipitation',
  'suspect_wind',
  'flatlined_series'
]);

/** ¿Alguno de los avisos recibidos pone en duda los datos de la estación? */
export function hasUnreliableData(warnings) {
  if (!Array.isArray(warnings)) return false;
  return warnings.some((code) => CODIGOS_DATOS_DUDOSOS.has(String(code)));
}

/**
 * Redes de particulares: Netatmo y Windy PWS, las mismas que el filtro
 * «ocultar particulares» (`AMATEUR_PROVIDERS` en el backend). Su ficha avisa
 * de que el sensor puede no cumplir los estándares de medición.
 */
const REDES_PARTICULARES = new Set(['NETATMO', 'WINDY']);

export function isPersonalWeatherStation(provider) {
  return REDES_PARTICULARES.has(String(provider || '').toUpperCase());
}
