/**
 * Refresco de la observación sin recargar la página.
 *
 * Las tarjetas viven de un único dato —la última observación—, así que basta
 * con volver a pedirla y sustituirla: el resto de la página no se toca. Antes
 * no se refrescaba nada, y en Weather Underground, que publica cada diez o
 * quince segundos, la ficha envejecía a la vista sin traer nada nuevo.
 *
 * No se pregunta con la pestaña en segundo plano: gastar la cuota de nadie
 * mirando una página que no se está mirando no tiene sentido.
 */
import { fetchPersonalObservation, refreshSecondsFor } from '$lib/personal.js';

/**
 * Arranca el ciclo de refresco. Devuelve la función para pararlo, tal como
 * espera `$effect`.
 *
 * `request` describe qué pedir; `onData` recibe cada respuesta buena.
 */
export function startLiveObservation(request, onData) {
  if (!request?.provider || !request?.stationId) return () => {};

  const period = refreshSecondsFor(request.provider) * 1000;
  let stopped = false;
  let timer = null;
  let active = null;

  const tick = async () => {
    if (stopped || document.hidden || active) return;
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const payload = await fetchPersonalObservation(request, { signal: controller.signal });
      if (!stopped && !controller.signal.aborted && payload) onData(payload);
    } catch {
      // Un fallo puntual no rompe nada: se conserva lo último bueno y se
      // vuelve a intentar en el siguiente ciclo.
    } finally {
      clearTimeout(timeout);
      active = null;
    }
  };

  // Al volver a la pestaña se pide enseguida: lo que hay en pantalla puede
  // llevar horas ahí.
  const onVisible = () => {
    if (!document.hidden) tick();
  };

  timer = setInterval(tick, period);
  document.addEventListener('visibilitychange', onVisible);

  return () => {
    stopped = true;
    clearInterval(timer);
    active?.abort();
    document.removeEventListener('visibilitychange', onVisible);
  };
}
