import { staticSitemap } from '$lib/server/sitemap.js';

const XML = { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' };

export function GET() {
  return new Response(staticSitemap(), { headers: XML });
}
