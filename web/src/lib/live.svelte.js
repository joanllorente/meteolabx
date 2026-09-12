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
  let awake = !document.hidden;
  let timer = null;
  let active = null;

  const cancelTimer = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const tick = async () => {
    if (stopped || !awake || document.hidden || active) return;
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

  const schedule = () => {
    if (stopped || !awake || timer !== null) return;
    timer = setTimeout(async () => {
      timer = null;
      await tick();
      schedule();
    }, period);
  };

  const pause = () => {
    awake = false;
    cancelTimer();
    active?.abort();
  };

  // Al volver a la pestaña se pide enseguida: lo que hay en pantalla puede
  // llevar horas ahí. `pagehide` y `freeze` cubren además la suspensión móvil
  // y la caché de navegación, donde visibilitychange no siempre basta.
  const resume = () => {
    if (stopped || document.hidden || awake) return;
    awake = true;
    void tick().finally(schedule);
  };
  const onVisibilityChange = () => document.hidden ? pause() : resume();

  schedule();
  document.addEventListener('visibilitychange', onVisibilityChange);
  document.addEventListener('pagehide', pause);
  document.addEventListener('pageshow', resume);
  document.addEventListener('freeze', pause);
  document.addEventListener('resume', resume);

  return () => {
    stopped = true;
    cancelTimer();
    active?.abort();
    document.removeEventListener('visibilitychange', onVisibilityChange);
    document.removeEventListener('pagehide', pause);
    document.removeEventListener('pageshow', resume);
    document.removeEventListener('freeze', pause);
    document.removeEventListener('resume', resume);
  };
}
