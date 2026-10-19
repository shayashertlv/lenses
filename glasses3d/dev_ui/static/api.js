export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

export async function request(path, { method = 'GET', body, signal } = {}) {
  const options = { method, signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError('Could not reach the local server. Check that it is running and try again.', 'network_error', 0);
  }
  let payload;
  try { payload = await response.json(); }
  catch { throw new ApiError('The local server returned an unreadable response.', 'invalid_response', response.status); }
  if (!response.ok) {
    throw new ApiError(payload.error?.message || `The request failed (${response.status}).`, payload.error?.code || 'request_failed', response.status);
  }
  return payload;
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) => request(path, { method: 'POST', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
};

export function idempotencyKey() {
  return crypto.randomUUID();
}

export function cutoffValue(input) {
  const value = input.value;
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) || !Number.isFinite(Number(value)) || Number(value) <= 0) {
    throw new Error('Enter a positive decimal amount for the max budget.');
  }
  return value;
}
