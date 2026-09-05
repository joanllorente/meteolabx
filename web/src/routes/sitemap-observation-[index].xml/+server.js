import { error } from '@sveltejs/kit';

import { sitemapChunks } from '$lib/server/sitemap.js';

const XML = { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' };

export async function GET({ params, fetch }) {
  const index = Number(params.index);
  if (!Number.isInteger(index) || index < 1) error(404, 'not_found');
  const chunks = await sitemapChunks(fetch);
  const chunk = chunks[index - 1];
  if (!chunk) error(404, 'not_found');
  return new Response(chunk, { headers: XML });
}
