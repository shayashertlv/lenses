/** Part-id decoding and the junction image metrics (pure; port_review/arview.json analysis.capture.junction_crop_method).
 *
 *  A part-id render (src/render/override.ts) draws each eyewear mesh in one flat colour (every lens mesh the reserved
 *  magenta) on black. Antialiasing mixes colours at edges, so a pixel decodes to the nearest palette colour (or the
 *  black background) only within that colour's OWN tolerance, and is 'mixed' otherwise. Each colour's tolerance stays
 *  below half the distance to its nearest other colour, so two parts can never be confused, and one close pair shrinks
 *  only the tolerance of those two parts, never everyone's; colours too close to separate are a warning naming them.
 *
 *  Junction columns: inside a side's crop, for each column of the projected lens-top span, the topmost lens pixel is
 *  found and the decode walks up at most the projected 3 mm. The first label that is neither lens nor mixed decides the
 *  column (a decal riding on the lens counts as the lens: `onLens`): a frame part = contact, the background = gap (its length in pixels up to the next frame pixel, or the whole
 *  walk), nothing but lens = lens_only, no lens pixel in the column = no_lens. Per-part pixel counts in that band
 *  generalise the prototype's name-based eyewire count (wire_metric.py) to any part id. The band metrics of a beauty
 *  crop (white %, median vertical edge count) port band_metrics.py so the numbers stay comparable with the WP4 sheets. */

export const BACKGROUND_LABEL = -1;
export const MIXED_LABEL = -2;
export const MAX_DECODE_TOLERANCE = 24;

export interface PaletteColour {
  readonly rgb: readonly [number, number, number];
  readonly name: string;
  readonly class: 'lens' | 'frame' | 'mixed';
}
export interface PaletteDecoder {
  /** The smallest of the per-colour tolerances (background included): a summary, not what decoding uses. */
  readonly tolerance: number;
  /** Each palette entry's own tolerance (index-aligned with the palette). */
  readonly tolerances: readonly number[];
  /** The black background's own tolerance. */
  readonly backgroundTolerance: number;
  /** The closest pair of colours (background black included), Chebyshev distance. */
  readonly minimumSeparation: number;
  readonly warnings: readonly string[];
}

const chebyshev = (a: readonly number[], b: readonly number[]): number => Math.max(Math.abs(a[0]! - b[0]!), Math.abs(a[1]! - b[1]!), Math.abs(a[2]! - b[2]!));
/** Close pairs below this many levels are named in a warning (their edge pixels mostly decode as mixed). */
export const CLOSE_PAIR_LEVELS = 8;
const toleranceFor = (nearest: number): number => Math.max(1, Math.min(MAX_DECODE_TOLERANCE, Math.floor((nearest - 1) / 2)));

/** Per-colour decode tolerances for a palette: each colour's below half the distance to its nearest colour of another
 *  key (black included), at most MAX_DECODE_TOLERANCE; a pixel within a colour's tolerance is then strictly nearer to it
 *  than to any other key's colour. Identical colours on entries that must stay apart cannot be told apart and are a
 *  warning: every frame (or mixed) part is its own entry, keyed by its palette index, so two frame parts with one colour
 *  warn just as a frame part with the lens magenta does. Only the lens meshes share a key: they share magenta by design
 *  and decode as one lens label. */
export function paletteDecoder(palette: readonly PaletteColour[]): PaletteDecoder {
  const colours: {rgb: readonly number[]; key: string; label: string}[] = [{rgb: [0, 0, 0], key: 'background', label: 'the background'}];
  palette.forEach((entry, index) => colours.push({rgb: entry.rgb, key: entry.class === 'lens' ? 'lens' : `part#${index}`,
    label: entry.class === 'lens' ? 'the lens' : `${entry.class} part "${entry.name}" (#${index})`}));
  const nearest = colours.map(() => Infinity);
  let separation = Infinity;
  const warnings: string[] = [], close: string[] = [];
  for (let i = 0; i < colours.length; i++) for (let j = i + 1; j < colours.length; j++) {
    if (colours[i]!.key === colours[j]!.key) continue;
    const distance = chebyshev(colours[i]!.rgb, colours[j]!.rgb);
    if (distance === 0) {warnings.push(`part-id colour ${colours[i]!.rgb.join(',')} is shared by ${colours[i]!.label} and ${colours[j]!.label}; they decode as one`); continue;}
    separation = Math.min(separation, distance);
    nearest[i] = Math.min(nearest[i]!, distance); nearest[j] = Math.min(nearest[j]!, distance);
    if (distance < CLOSE_PAIR_LEVELS) close.push(`${colours[i]!.label} and ${colours[j]!.label} (${distance} levels, tolerance ${toleranceFor(distance)})`);
  }
  if (!Number.isFinite(separation)) separation = 255;
  const all = nearest.map(distance => toleranceFor(Number.isFinite(distance) ? distance : 255));
  if (close.length) {
    warnings.push(`part-id colours too close to separate cleanly: ${close.join('; ')}; edge pixels of THESE parts decode as mixed, every other part keeps its own tolerance`);
  }
  return {tolerance: Math.min(...all), tolerances: all.slice(1), backgroundTolerance: all[0]!, minimumSeparation: separation, warnings: [...new Set(warnings)]};
}

/** Decode RGBA pixels to labels: an index into `palette`, BACKGROUND_LABEL (black) or MIXED_LABEL. A pixel takes its
 *  nearest colour's label only within THAT colour's tolerance (`decoder`, default paletteDecoder(palette)). */
export function decodeLabels(rgba: ArrayLike<number>, palette: readonly PaletteColour[], decoder: Pick<PaletteDecoder, 'tolerances' | 'backgroundTolerance'> = paletteDecoder(palette)): Int16Array {
  const count = Math.floor(rgba.length / 4), labels = new Int16Array(count);
  if (decoder.tolerances.length !== palette.length) throw new Error(`decodeLabels: ${decoder.tolerances.length} tolerances for ${palette.length} palette colours`);
  const cache = new Map<number, number>();
  for (let p = 0; p < count; p++) {
    const r = rgba[p * 4]!, g = rgba[p * 4 + 1]!, b = rgba[p * 4 + 2]!, key = (r << 16) | (g << 8) | b;
    let label = cache.get(key);
    if (label === undefined) {
      let best = Math.max(r, g, b), bestLabel = BACKGROUND_LABEL;
      for (let k = 0; k < palette.length; k++) {
        const c = palette[k]!.rgb, d = Math.max(Math.abs(r - c[0]), Math.abs(g - c[1]), Math.abs(b - c[2]));
        if (d < best) {best = d; bestLabel = k;}
      }
      const tolerance = bestLabel === BACKGROUND_LABEL ? decoder.backgroundTolerance : decoder.tolerances[bestLabel]!;
      label = best <= tolerance ? bestLabel : MIXED_LABEL;
      cache.set(key, label);
    }
    labels[p] = label;
  }
  return labels;
}

export interface ColumnSpan {readonly x0: number; readonly x1: number;}
export interface JunctionColumns {
  readonly columns: number;
  readonly contact_cols_pct: number;
  readonly gap_cols_pct: number;
  readonly lens_only_cols_pct: number;
  readonly no_lens_cols_pct: number;
  readonly median_gap_px: number | null;
  readonly walk_px: number;
  /** Pixels per label name in the band above each column's lens top (background and mixed included). */
  readonly band_pixels: Readonly<Record<string, number>>;
}

/** The junction columns of one crop. `labels` is the crop's decode (row-major `width` x `height`), `span` the columns
 *  (crop coordinates, inclusive) of the projected lens top, `walkPx` the projected 3 mm. */
export function junctionColumns(labels: Int16Array, width: number, height: number, palette: readonly PaletteColour[], span: ColumnSpan,
  walkPx: number, onLens: ReadonlySet<string> = new Set()): JunctionColumns {
  // `onLens`: frame parts riding on the lens surface (junction.ts lensDecals) read as the lens they sit on, never as brow.
  const isLens = (label: number) => label >= 0 && (palette[label]!.class === 'lens' || onLens.has(palette[label]!.name));
  const walk = Math.max(1, Math.round(walkPx));
  const x0 = Math.max(0, Math.floor(span.x0)), x1 = Math.min(width - 1, Math.ceil(span.x1));
  let contact = 0, gap = 0, lensOnly = 0, noLens = 0, columns = 0;
  const gaps: number[] = [], band: Record<string, number> = {};
  const nameOf = (label: number) => label === BACKGROUND_LABEL ? 'background' : label === MIXED_LABEL ? 'mixed' : palette[label]!.name;
  for (let x = x0; x <= x1; x++) {
    columns++;
    let top = -1;
    for (let y = 0; y < height; y++) if (isLens(labels[y * width + x]!)) {top = y; break;}
    if (top < 0) {noLens++; continue;}
    let decided: 'contact' | 'gap' | 'lens_only' = 'lens_only', gapStart = -1;
    for (let y = top - 1; y >= Math.max(0, top - walk); y--) {
      const label = labels[y * width + x]!;
      band[nameOf(label)] = (band[nameOf(label)] ?? 0) + 1;
      if (decided === 'lens_only') {
        if (isLens(label) || label === MIXED_LABEL) continue;
        if (label === BACKGROUND_LABEL) {decided = 'gap'; gapStart = y; continue;}
        decided = 'contact'; continue;
      }
      if (decided === 'gap' && gapStart >= 0 && label !== BACKGROUND_LABEL && label !== MIXED_LABEL && !isLens(label)) {
        gaps.push(gapStart - y); gapStart = -1;
      }
    }
    if (decided === 'gap' && gapStart >= 0) gaps.push(gapStart - Math.max(0, top - walk) + 1);
    if (decided === 'contact') contact++; else if (decided === 'gap') gap++; else lensOnly++;
  }
  const pct = (value: number) => columns ? Math.round(value / columns * 1000) / 10 : 0;
  const sorted = [...gaps].sort((a, b) => a - b);
  return {columns, contact_cols_pct: pct(contact), gap_cols_pct: pct(gap), lens_only_cols_pct: pct(lensOnly), no_lens_cols_pct: pct(noLens),
    median_gap_px: sorted.length ? sorted[sorted.length >> 1]! : null, walk_px: walk, band_pixels: band};
}

export interface BandMetrics {readonly white_pct: number; readonly median_edges: number; readonly rows: readonly [number, number]; readonly columns: readonly [number, number];}
/** band_metrics.py on a beauty image: rows y0-8 .. y0+20 around the lens top `y0`, the span's columns trimmed 12 % at each
 *  end; white = luminance > 215 and saturation < 40; edges = runs of vertical luminance steps > 18 per column (median). */
export function bandMetrics(rgba: ArrayLike<number>, width: number, height: number, y0: number, span: ColumnSpan): BandMetrics | null {
  const spanWidth = span.x1 - span.x0;
  const xa = Math.max(0, Math.floor(span.x0 + .12 * spanWidth)), xb = Math.min(width, Math.floor(span.x1 - .12 * spanWidth));
  const ya = Math.max(0, Math.round(y0) - 8), yb = Math.min(height, Math.round(y0) + 20);
  if (xb <= xa || yb - ya < 2) return null;
  const luminance = (x: number, y: number) => {const p = (y * width + x) * 4; return (rgba[p]! + rgba[p + 1]! + rgba[p + 2]!) / 3;};
  let white = 0, total = 0;
  const edges: number[] = [];
  for (let x = xa; x < xb; x++) {
    let runs = 0, previous = false;
    for (let y = ya; y < yb; y++) {
      const p = (y * width + x) * 4, r = rgba[p]!, g = rgba[p + 1]!, b = rgba[p + 2]!;
      const l = (r + g + b) / 3, saturation = Math.max(r, g, b) - Math.min(r, g, b);
      if (l > 215 && saturation < 40) white++;
      total++;
      if (y + 1 < yb) {
        const step = Math.abs(luminance(x, y + 1) - l) > 18;
        if (step && !previous) runs++;
        previous = step;
      }
    }
    edges.push(runs);
  }
  edges.sort((a, b) => a - b);
  const mid = edges.length >> 1;
  const medianEdges = edges.length % 2 ? edges[mid]! : (edges[mid - 1]! + edges[mid]!) / 2;
  return {white_pct: Math.round(white / total * 1000) / 10, median_edges: medianEdges, rows: [ya, yb], columns: [xa, xb]};
}
