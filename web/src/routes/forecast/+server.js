/**
 * Predicción: el visor que ya existe, servido tal cual.
 *
 * Es un SPA propio —construido aparte, con su `<base href="/forecast/">` y sus
 * metadatos— que vive en `static/forecast/`. Aquí no se toca ni una etiqueta:
 * `/forecast` es la URL que ya está indexada y la que dice su canonical, y sin
 * esta ruta el servidor de estáticos no resuelve el directorio a su index y
 * la pestaña acaba en un 404. Sus `assets` se sirven solos, como estáticos.
 */
import html from '../../../static/forecast/index.html?raw';

export function GET() {
  return new Response(html, {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      // La misma caché corta que el resto de páginas: el visor se reinstala
      // con cada build y el HTML apunta a bundles con hash.
      'cache-control': 'public, max-age=0, must-revalidate'
    }
  });
}
