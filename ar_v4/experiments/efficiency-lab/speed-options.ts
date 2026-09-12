export interface NativeSpeedOptions {
  readonly reuseSourcePixels: boolean;
  readonly fewerCopies: boolean;
  readonly asyncReadback: boolean;
  readonly prewarmTemples: boolean;
  readonly poolReadbackScratch: boolean;
  readonly asyncTemples: boolean;
  readonly cropBranchReadback: boolean;
  readonly omitBranchLenses: boolean;
  readonly ownedPackState: boolean;
  readonly wordCompose: boolean;
}

export const DEFAULT_SPEED_OPTIONS: Readonly<NativeSpeedOptions> = Object.freeze({
  reuseSourcePixels: false, fewerCopies: false, asyncReadback: false, prewarmTemples: false,
  poolReadbackScratch: false, asyncTemples: false,
  cropBranchReadback: false, omitBranchLenses: false,
  ownedPackState: false, wordCompose: false,
});

export function normalizeSpeedOptions(value: Partial<NativeSpeedOptions> = {}): Readonly<NativeSpeedOptions> {
  const options = {...DEFAULT_SPEED_OPTIONS, ...value};
  for (const name of Object.keys(DEFAULT_SPEED_OPTIONS) as (keyof NativeSpeedOptions)[])
    if (typeof options[name] !== 'boolean') throw new Error(`Invalid speed option: ${name}.`);
  if ((options.cropBranchReadback || options.omitBranchLenses) && options.asyncTemples
    || options.cropBranchReadback && options.omitBranchLenses)
    throw new Error('Branch region and lens experiments must run independently without async temples.');
  return Object.freeze(options);
}

/** The canvas is borrowed only while isCurrent() is true. RGBA is independently
 * owned, immutable by contract, and may outlive the live canvas for Hold/export. */
export interface OwnedSourceFrame {
  readonly canvas: HTMLCanvasElement;
  readonly rgba: ImageData;
  readonly sourceSHA256: string;
  readonly generation: number;
  readonly sessionId: number | string;
  readonly colorSpace: 'srgb';
  readonly isCurrent: () => boolean;
}

export interface SourceFrameIdentity {
  readonly sourceSHA256: string;
  readonly generation: number;
  readonly sessionId: number | string;
  readonly isCurrent: () => boolean;
}

const opaqueSourcePixels = new WeakMap<ImageData, boolean>();
/** Source snapshots are immutable after ownership transfer. Share the eligibility
 * scan across the outer, temple and native layers without rescanning each pixel. */
export function sourcePixelsOpaque(rgba: ImageData): boolean {
  const previous = opaqueSourcePixels.get(rgba); if (previous !== undefined) return previous;
  let opaque = true;
  for (let offset = 3; offset < rgba.data.length; offset += 4) if (rgba.data[offset] !== 255) {opaque = false; break;}
  opaqueSourcePixels.set(rgba, opaque); return opaque;
}

function assertIdentity(value: SourceFrameIdentity): void {
  if (!/^[a-f0-9]{64}$/.test(value.sourceSHA256) || !Number.isSafeInteger(value.generation) || value.generation < 0
    || !((typeof value.sessionId === 'string' && value.sessionId.length > 0)
      || (typeof value.sessionId === 'number' && Number.isSafeInteger(value.sessionId) && value.sessionId >= 0))
    || typeof value.isCurrent !== 'function') throw new Error('The source frame identity is invalid.');
}

function assertPixels(rgba: ImageData): void {
  if (!Number.isSafeInteger(rgba.width) || !Number.isSafeInteger(rgba.height) || rgba.width <= 0 || rgba.height <= 0
    || rgba.width > 8192 || rgba.height > 8192 || rgba.width * rgba.height > 16_777_216
    || !(rgba.data instanceof Uint8ClampedArray) || !(rgba.data.buffer instanceof ArrayBuffer)
    || rgba.data.byteOffset !== 0 || rgba.data.byteLength !== rgba.width * rgba.height * 4
    || rgba.data.buffer.byteLength !== rgba.data.byteLength)
    throw new Error('The source frame requires exact independently owned RGBA storage.');
}

/** Claim the getImageData snapshot after its hash has completed. Transferring the
 * ArrayBuffer detaches the caller's old view instead of copying every pixel. */
export function createOwnedSourceFrame(canvas: HTMLCanvasElement, rgba: ImageData,
  identity: SourceFrameIdentity): OwnedSourceFrame {
  assertIdentity(identity); assertPixels(rgba);
  if (rgba.colorSpace !== 'srgb') throw new Error('The source frame must use explicit sRGB pixels.');
  if (canvas.width !== rgba.width || canvas.height !== rgba.height) throw new Error('The source frame canvas and RGBA dimensions differ.');
  if (!identity.isCurrent()) throw new DOMException('The source frame is no longer current.', 'AbortError');
  const pixels = structuredClone(rgba.data, {transfer: [rgba.data.buffer]});
  const owned = new ImageData(pixels, rgba.width, rgba.height, {colorSpace: 'srgb'});
  return Object.freeze({canvas, rgba: owned, sourceSHA256: identity.sourceSHA256,
    generation: identity.generation, sessionId: identity.sessionId, colorSpace: 'srgb', isCurrent: identity.isCurrent});
}

/** Validate a live borrow before and after any asynchronous native operation. */
export function assertSourceFrameCurrent(source: OwnedSourceFrame, frame?: HTMLCanvasElement, sourceSHA256?: string): void {
  assertIdentity(source); assertPixels(source.rgba);
  if (!source.isCurrent()) throw new DOMException('The source frame is no longer current.', 'AbortError');
  if (source.colorSpace !== 'srgb' || source.rgba.colorSpace !== 'srgb')
    throw new Error('The source frame must use explicit sRGB pixels.');
  if ((frame !== undefined && source.canvas !== frame) || source.canvas.width !== source.rgba.width
    || source.canvas.height !== source.rgba.height) throw new Error('The source frame does not own this canvas and dimensions.');
  if (sourceSHA256 !== undefined && source.sourceSHA256 !== sourceSHA256)
    throw new Error('The source pixels belong to another paired image.');
}

export interface SpeedFrameInput {
  readonly options?: Partial<NativeSpeedOptions>;
  readonly source?: OwnedSourceFrame;
  /** A one-pair scheduler signal; excluded from retained/exported metadata. */
  readonly onNativeSubmitted?: () => void;
}
