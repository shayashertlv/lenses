/** What the capture needs to know about the input file before the browser loads it (pure): whether the GLB container
 *  is readable at all, and which external resources it references (served next to the model so arview resolves them
 *  against the model URL, WP1_DESIGN.md §1). Acceptance is NOT decided here: arview's load and spec.py decide it. */

export interface GlbInput {
  /** The JSON chunk, or null when the container could not be read (arview will refuse it; the report says why). */
  readonly json: Record<string, unknown> | null;
  readonly containerError: string | null;
  /** Relative URIs of buffers and images (data: URIs excluded), in file order, unique. */
  readonly externalUris: readonly string[];
  readonly extensionsUsed: readonly string[];
  readonly extensionsRequired: readonly string[];
}

const GLB_MAGIC = 0x46546c67, JSON_CHUNK = 0x4e4f534a;

export function readGlbInput(bytes: Uint8Array): GlbInput {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const fail = (message: string): GlbInput => ({json: null, containerError: message, externalUris: [], extensionsUsed: [], extensionsRequired: []});
  if (bytes.byteLength < 20) return fail(`${bytes.byteLength} bytes is too short for a GLB header`);
  if (view.getUint32(0, true) !== GLB_MAGIC) return fail('not a GLB (the magic is not "glTF")');
  if (view.getUint32(4, true) !== 2) return fail(`GLB version ${view.getUint32(4, true)}, not 2`);
  const declared = view.getUint32(8, true);
  if (declared !== bytes.byteLength) return fail(`the header declares ${declared} bytes, the file has ${bytes.byteLength}`);
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== JSON_CHUNK || 20 + jsonLength > bytes.byteLength) return fail('the first chunk is not a complete JSON chunk');
  let json: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)));
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return fail('the JSON chunk is not an object');
    json = parsed as Record<string, unknown>;
  } catch (error) {return fail(`the JSON chunk does not parse (${error instanceof Error ? error.message : String(error)})`);}
  const uris: string[] = [];
  for (const key of ['buffers', 'images'] as const) {
    const list = json[key];
    if (!Array.isArray(list)) continue;
    for (const entry of list) {
      const uri = (entry as {uri?: unknown} | null)?.uri;
      if (typeof uri === 'string' && uri && !uri.startsWith('data:') && !uris.includes(uri)) uris.push(uri);
    }
  }
  const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  return {json, containerError: null, externalUris: uris, extensionsUsed: strings(json.extensionsUsed), extensionsRequired: strings(json.extensionsRequired)};
}
