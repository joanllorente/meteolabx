/** Caché acotada solo para metadatos públicos, nunca observaciones o credenciales. */
export function createMetadataCache({
  ttlMs = 300000,
  maxEntries = 512,
  now = Date.now,
  staleIfError = false
} = {}) {
  const entries = new Map();
  return async function cached(key, load) {
    let entry = entries.get(key);
    if (!entry || (entry.expires <= now() && !entry.promise)) {
      const stale = entry?.value;
      const hasStale = Boolean(entry?.hasValue);
      entry = { expires: Infinity, promise: null, value: stale, hasValue: hasStale };
      entry.promise = Promise.resolve().then(load).then((value) => {
        entry.value = value;
        entry.hasValue = true;
        entry.expires = now() + ttlMs;
        entry.promise = null;
        return value;
      }).catch((error) => {
        entry.promise = null;
        const mayUseStale = typeof staleIfError === 'function'
          ? staleIfError(error)
          : staleIfError;
        if (mayUseStale && entry.hasValue) {
          // No martillear una API caída en cada visita; se vuelve a intentar
          // tras un intervalo corto mientras se sirve la última ficha válida.
          entry.expires = now() + Math.min(ttlMs, 30000);
          return entry.value;
        }
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
    return structuredClone(entry.promise ? await entry.promise : entry.value);
  };
}
