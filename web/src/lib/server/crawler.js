/**
 * Identifica clientes que se declaran rastreadores en el User-Agent.
 *
 * No pretende demostrar que el cliente sea humano: solo evita que Google y
 * otros crawlers conviertan cada URL indexable en una consulta de pago o con
 * cuota al proveedor meteorológico. Mantiene el mismo criterio que el backend
 * usa para excluir rastreadores de las estadísticas de uso.
 */
export function isCrawlerRequest(request) {
  const agent = String(request?.headers?.get('user-agent') || '').toLowerCase();
  return (
    agent.includes('googlebot') ||
    agent.includes('google-inspectiontool') ||
    agent.includes('bingbot') ||
    agent.includes('bingpreview') ||
    /bot\b|crawler|spider|headlesschrome/.test(agent)
  );
}
