/** Caché acotada solo para metadatos públicos, nunca observaciones o credenciales. */
export function createMetadataCache({ ttlMs = 300000, maxEntries = 512, now = Date.now } = {}) {
  const entries = new Map();
  return async function cached(key, load) {
    let entry = entries.get(key);
    if (entry && entry.expires <= now()) {
      entries.delete(key);
      entry = null;
    }
    if (!entry) {
      entry = { expires: Infinity, promise: null };
      entry.promise = Promise.resolve().then(load).then((value) => {
        entry.expires = now() + ttlMs;
        return value;
      }).catch((error) => {
        if (entries.get(key) === entry) entries.delete(key);
        throw error;
      });
      entries.set(key, entry);
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value);
    } else {
      entries.delete(key);
      entries.set(key, entry);
    }
    // Cada página recibe su copia: una mutación no contamina otras visitas.
    return structuredClone(await entry.promise);
  };
}
