export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...options, headers: options?.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options?.headers } });
  const text = await response.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const error = data && typeof data === 'object' ? (data as Record<string, unknown>)['detail'] ?? (data as Record<string, unknown>)['error'] : data;
    throw new ApiError(typeof error === 'string' ? error : `Request failed (${response.status}).`, response.status);
  }
  return data as T;
}
export function safeArtifactUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, location.origin);
    return url.origin === location.origin && /^\/api\/jobs\/[^/]+\/files\/[^/]+$/.test(url.pathname) && !url.search && !url.hash ? url.href : null;
  } catch { return null; }
}
