/** Candidate-only ownership tokens. These are explicitly NOT content hashes.
 * A live token identifies one immutable captured image in one camera session. */
export interface LiveFrameIdentity {kind: 'source' | 'detection'; sessionId: string; sequence: number;}
export const isContentHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export function parseLiveIdentity(value: unknown): LiveFrameIdentity | null {
  if (typeof value !== 'string') return null;
  const match = /^live:(source|detection):([A-Za-z0-9_-]{8,128}):(0|[1-9][0-9]*)$/.exec(value);
  if (!match) return null;
  const sequence = Number(match[3]);
  return Number.isSafeInteger(sequence) ? {kind: match[1] as LiveFrameIdentity['kind'], sessionId: match[2]!, sequence} : null;
}
export function liveFrameIdentity(kind: LiveFrameIdentity['kind'], sessionId: string, sequence: number): string {
  const token = `live:${kind}:${sessionId}:${sequence}`;
  if (!parseLiveIdentity(token)) throw new Error('Invalid live image ownership identity.');
  return token;
}
export function isSourceIdentity(value: unknown, sequence?: number): value is string {
  if (isContentHash(value)) return true;
  const token = parseLiveIdentity(value);
  return token?.kind === 'source' && (sequence === undefined || token.sequence === sequence);
}
export function isPairedIdentity(source: unknown, detection: unknown): boolean {
  if (isContentHash(source) || isContentHash(detection)) return isContentHash(source) && isContentHash(detection);
  const a = parseLiveIdentity(source), b = parseLiveIdentity(detection);
  return a?.kind === 'source' && b?.kind === 'detection' && a.sessionId === b.sessionId && a.sequence === b.sequence;
}
export function identityKind(source: string): 'sha256' | 'session-frame' {
  if (isContentHash(source)) return 'sha256';
  if (isSourceIdentity(source)) return 'session-frame';
  throw new Error('Invalid source identity.');
}
export function identityMetadata(pair: {sourceIdentity: string; detectionIdentity: string}): {
  identityKind: 'sha256' | 'session-frame'; sourceSHA256?: string; detectionSHA256?: string;
} {
  if (!isPairedIdentity(pair.sourceIdentity, pair.detectionIdentity)) throw new Error('Invalid image/detection ownership pair.');
  return isContentHash(pair.sourceIdentity)
    ? {identityKind:'sha256',sourceSHA256:pair.sourceIdentity,detectionSHA256:pair.detectionIdentity}
    : {identityKind:'session-frame'};
}
export async function contentHash(bytes: Uint8Array | Uint8ClampedArray): Promise<string> {
  const copy = new Uint8Array(bytes);
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', copy.buffer)), value => value.toString(16).padStart(2, '0')).join('');
}
/** Hash both exact retained inputs before returning any replacement pair.
 * Callers check ownership again and publish pair + mask together after await. */
export async function heldContentPair<T extends {sourceIdentity: string; detectionIdentity: string}>(
  pair: T, rgba: Uint8ClampedArray, detection: unknown): Promise<T & {identityKind: 'sha256'; sourceSHA256: string; detectionSHA256: string}> {
  if (!isPairedIdentity(pair.sourceIdentity, pair.detectionIdentity)) throw new Error('Invalid held image/detection ownership pair.');
  const [sourceSHA256, detectionSHA256] = await Promise.all([contentHash(rgba), contentHash(new TextEncoder().encode(JSON.stringify(detection)))]);
  if (isContentHash(pair.sourceIdentity) && (pair.sourceIdentity !== sourceSHA256 || pair.detectionIdentity !== detectionSHA256))
    throw new Error('Held bytes do not match their saved content hashes.');
  return {...pair, sourceIdentity: sourceSHA256, detectionIdentity: detectionSHA256, identityKind: 'sha256', sourceSHA256, detectionSHA256};
}
