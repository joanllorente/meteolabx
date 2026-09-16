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

/**
 * Rastreadores que pueden pasar, pero sin prisa.
 *
 * El 16/09/2026 los de Anthropic recorrieron las fichas a más de cien por
 * minuto, y el backend acabó sin poder consultar a ningún proveedor. Un grupo
 * propio sustituye al de `*` para ese agente, así que repite el `Allow`.
 */
const RASTREADORES_PAUSADOS = ['ClaudeBot', 'Claude-SearchBot', 'Claude-User'];
const CRAWL_DELAY_S = 10;

export function GET() {
  const bloqueos = RASTREADORES_BLOQUEADOS.map(
    (agente) => `User-agent: ${agente}\nDisallow: /\n`
  ).join('\n');
  const pausas = RASTREADORES_PAUSADOS.map(
    (agente) => `User-agent: ${agente}\nCrawl-delay: ${CRAWL_DELAY_S}\nAllow: /\n`
  ).join('\n');
  return new Response(
    `${bloqueos}\n${pausas}\nUser-agent: *\nAllow: /\n\nSitemap: ${SITE_URL}/sitemap.xml\n`,
    {
      headers: {
        'content-type': 'text/plain; charset=utf-8',
        'cache-control': 'public, max-age=86400'
      }
    }
  );
}
