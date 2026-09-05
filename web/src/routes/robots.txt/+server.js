import { SITE_URL } from '$lib/seo/i18n.js';

/** El mismo robots.txt que servía el generador estático. */
export function GET() {
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${SITE_URL}/sitemap.xml\n`, {
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'public, max-age=86400'
    }
  });
}
