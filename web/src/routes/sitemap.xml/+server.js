import { sitemapIndex } from '$lib/server/sitemap.js';

const XML = { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' };

export async function GET({ fetch }) {
  return new Response(await sitemapIndex(fetch), { headers: XML });
}
