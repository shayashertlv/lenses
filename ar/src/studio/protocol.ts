/** Local studio messages carry appearance values only, never URLs, code or geometry. */
export const PROPERTY_BOUNDS = Object.freeze({
  metallic: [0, 1], roughness: [0, 1], transmission: [.001, 1], ior: [1, 2.5], clearcoat: [0, 1],
  clearcoat_roughness: [0, 1], iridescence: [0, 1], iridescence_ior: [1, 3],
  iridescence_thickness: [0, 1500], attenuation_distance: [0, 10],
} satisfies Record<string, readonly [number, number]>);
export type NumericProperty = keyof typeof PROPERTY_BOUNDS;
export type MaterialProperty = NumericProperty | 'base_color' | 'attenuation_color';
export type MaterialEdit = Partial<Record<MaterialProperty, number | string>>;
export interface StudioPreview {edits: Record<string, MaterialEdit>; viewer: {lens_reflection?: number}}
export interface StudioConnection {origin: string; channel: string; modelSha256: string}
const loopback = new Set(['localhost', '127.0.0.1', '[::1]']);
const plain = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object'
  && !Array.isArray(v) && [Object.prototype, null].includes(Object.getPrototypeOf(v));
function requireValue(test: unknown, message: string): asserts test {if (!test) throw new Error(message);}

export function studioConnection(search: string, pageOrigin: string): StudioConnection | null {
  const params = new URLSearchParams(search), origin = params.get('studioOrigin'), channel = params.get('studioChannel');
  if (!origin || !channel) return null;
  try {
    const page = new URL(pageOrigin), parent = new URL(origin), sha = params.get('sha256') ?? '';
    if (![page, parent].every(u => loopback.has(u.hostname) && ['http:', 'https:'].includes(u.protocol))) return null;
    if (parent.origin !== origin || parent.username || parent.password || !/^[\w-]{16,128}$/.test(channel) || !/^[a-f\d]{64}$/i.test(sha)) return null;
    return {origin, channel, modelSha256: sha.toLowerCase()};
  } catch {return null;}
}

/** Ignore unauthenticated messages without answering their sender. Validation below handles authenticated payloads. */
export function isStudioSender(event: Pick<MessageEvent, 'origin' | 'source' | 'data'>, parent: unknown, connection: StudioConnection): boolean {
  return event.source === parent && event.origin === connection.origin && plain(event.data)
    && event.data.type === 'lenses-studio:preview' && event.data.channel === connection.channel;
}

export function parseStudioPreview(data: unknown, modelSha256: string): StudioPreview {
  requireValue(plain(data), 'Invalid studio message.');
  requireValue(Object.keys(data).every(k => ['type', 'channel', 'model_sha256', 'edits', 'viewer'].includes(k)), 'Unknown studio message field.');
  requireValue(data.model_sha256 === modelSha256, 'The material edit belongs to a different model revision.');
  requireValue(plain(data.edits) && Object.keys(data.edits).length <= 128, 'Expected at most 128 material edits.');
  const edits: StudioPreview['edits'] = {};
  for (const [id, value] of Object.entries(data.edits)) {
    requireValue(/^(0|[1-9]\d{0,5})$/.test(id) && plain(value), 'Invalid material index or edit.');
    const edit: MaterialEdit = {};
    for (const [property, v] of Object.entries(value)) {
      if (property === 'base_color' || property === 'attenuation_color') {
        requireValue(typeof v === 'string' && /^#[a-f\d]{6}$/i.test(v), `${property} must be an sRGB hex color.`);
        edit[property] = v.toLowerCase();
      } else {
        requireValue(Object.hasOwn(PROPERTY_BOUNDS, property), 'Unsupported material property.');
        const [min, max] = PROPERTY_BOUNDS[property as NumericProperty];
        requireValue(typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max, `${property} must be between ${min} and ${max}.`);
        edit[property as NumericProperty] = v;
      }
    }
    edits[id] = edit;
  }
  requireValue(data.viewer === undefined || plain(data.viewer), 'Invalid viewer settings.');
  const viewer = data.viewer ?? {};
  requireValue(Object.keys(viewer).every(k => k === 'lens_reflection'), 'Unsupported viewer setting.');
  const reflection = viewer.lens_reflection;
  requireValue(reflection === undefined || typeof reflection === 'number' && Number.isFinite(reflection) && reflection >= .3 && reflection <= 4,
    'Lens reflection must be between 0.3 and 4.');
  return {edits, viewer: reflection === undefined ? {} : {lens_reflection: reflection}};
}

export function mergeStudioPreview(previous: StudioPreview, next: StudioPreview): StudioPreview {
  const edits = {...previous.edits};
  for (const [id, edit] of Object.entries(next.edits)) edits[id] = {...edits[id], ...edit};
  return {edits, viewer: {...previous.viewer, ...next.viewer}};
}
