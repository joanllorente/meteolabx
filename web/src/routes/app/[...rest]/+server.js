import { redirect } from '@sveltejs/kit';

import { LANGUAGES } from '$lib/seo/i18n.js';
import { visitorLanguage } from '$lib/server/language.js';

/**
 * Cualquier otra ruta de la aplicación anterior.
 *
 * `/app/_stcore/...`, `/app/static/...` y demás interioridades de Streamlit ya
 * no existen. En vez de un 404 seco, la portada.
 */
export function GET({ request, cookies, setHeaders }) {
  const language = visitorLanguage({ request, cookies }, Object.keys(LANGUAGES));
  setHeaders({ 'cache-control': 'private, no-store', vary: 'Accept-Language' });
  redirect(302, `/${language}`);
}
