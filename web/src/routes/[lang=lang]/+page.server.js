/**
 * La portada en un idioma concreto: `/es`, `/ca`, `/en`…
 *
 * Es la misma página que la raíz; lo único que cambia es que aquí el idioma
 * viene en la ruta en vez de negociarse con el navegador. Así la elección de
 * idioma se guarda sola —la URL es el marcador— y cada versión tiene su
 * `canonical` y su `hreflang`.
 */
export { load } from '../+page.server.js';
