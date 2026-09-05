/**
 * Prefijo de la app antigua.
 *
 * Streamlit ya no vive en la raíz: `scripts/start_web.sh` lo arranca con
 * `--server.baseUrlPath=app`, así que todo lo suyo —la aplicación, sus
 * estáticos y las páginas SEO que todavía genera— cuelga de `/app`. Lo que
 * llega a este servidor pidiendo `/es/estaciones.html` hay que reescribirlo
 * antes de reenviarlo, o Streamlit responde 404 a URLs que están indexadas.
 */

export function normalizeBasePath(value) {
  const clean = String(value ?? '').replace(/^\/+|\/+$/g, '');
  return clean ? `/${clean}` : '';
}

/** Antepone el prefijo salvo que la petición ya venga con él. */
export function withLegacyBase(url, basePath) {
  const base = normalizeBasePath(basePath);
  if (!base) return url;
  const path = String(url || '/');
  if (path === base || path.startsWith(`${base}/`) || path.startsWith(`${base}?`)) return path;
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}
