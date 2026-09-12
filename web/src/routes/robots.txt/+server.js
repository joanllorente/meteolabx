import { SITE_URL } from '$lib/seo/i18n.js';

/**
 * Rastreadores de análisis SEO que no traen visitas.
 *
 * Recorren el sitio entero para vender informes de enlaces a terceros, y aquí
 * eso se paga: la salida de datos del servicio web es lo que factura Railway,
 * y con ~35 000 fichas de estación una pasada completa son muchos megas. Los
 * buscadores que sí mandan gente —Google, Bing, DuckDuckGo— no se tocan.
 */
const RASTREADORES_BLOQUEADOS = [
  'AhrefsBot',
  'SemrushBot',
  'MJ12bot',
  'DotBot',
  'BLEXBot',
  'DataForSeoBot',
  'PetalBot',
  'SeekportBot',
  'serpstatbot'
];

export function GET() {
  const bloqueos = RASTREADORES_BLOQUEADOS.map(
    (agente) => `User-agent: ${agente}\nDisallow: /\n`
  ).join('\n');
  return new Response(`${bloqueos}\nUser-agent: *\nAllow: /\n\nSitemap: ${SITE_URL}/sitemap.xml\n`, {
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'public, max-age=86400'
    }
  });
}
