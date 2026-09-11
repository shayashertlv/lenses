/** The public AR build is entirely local after its own assets are downloaded.
 * This guard runs in each SDK execution context, including face/hair workers.
 * Server CSP independently enforces the same boundary, including redirects.
 */
export function createSameOriginFetch(nativeFetch: typeof fetch, locationHref: string): typeof fetch {
  const page = new URL(locationHref);
  if (page.origin === 'null') throw new TypeError('AR testing requires an origin for its network boundary.');
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    let target: URL;
    try {
      const address = typeof Request !== 'undefined' && input instanceof Request ? input.url : String(input);
      target = new URL(address, page);
    } catch {throw new TypeError('AR testing could not validate the network request.');}
    if (target.origin !== page.origin || !['http:', 'https:', 'blob:'].includes(target.protocol)) {
      // Do not include URLs, headers or request bodies in errors or logging.
      // Rejection reaches the SDK's ordinary transport-error path, never a fake success.
      throw new TypeError('AR testing blocks off-origin network requests.');
    }
    return nativeFetch(input, init);
  };
}

/** Imported only by the mobile SDK bundle; ordinary G builds do not include it. */
if (typeof globalThis.location !== 'undefined' && typeof globalThis.fetch === 'function') {
  const originalFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = createSameOriginFetch(originalFetch, globalThis.location.href);
}
