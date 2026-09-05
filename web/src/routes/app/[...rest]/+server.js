import { redirect } from '@sveltejs/kit';

import { LANGUAGES } from '$lib/seo/i18n.js';
import { negotiateLanguage } from '$lib/server/language.js';

/**
 * Cualquier otra ruta de la aplicación anterior.
 *
 * `/app/_stcore/...`, `/app/static/...` y demás interioridades de Streamlit ya
 * no existen. En vez de un 404 seco, la portada.
 */
export function GET({ request }) {
  const language = negotiateLanguage(request.headers.get('accept-language'), Object.keys(LANGUAGES));
  redirect(301, `/${language}`);
}
