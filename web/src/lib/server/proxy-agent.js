import { Agent as HttpAgent } from 'node:http';
import { Agent as HttpsAgent } from 'node:https';

/** Pool de conexiones de la API; separado del WebSocket de Streamlit. */
export function createApiAgent(origin) {
  const Agent = new URL(origin).protocol === 'https:' ? HttpsAgent : HttpAgent;
  return new Agent({ keepAlive: true, maxSockets: 64, maxFreeSockets: 16, scheduling: 'lifo' });
}
