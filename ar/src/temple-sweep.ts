/** The temple test sweep: every temple configuration worth judging by eye, in one list.
 *
 *  Why it exists. The temple arms have four levers and they interact: how far the arm is bent outward at its tip
 *  (`?templebend=`), where along the arm that bend pivots (`?templepivot=`), how much of an arm is given up to the head
 *  (`?templekeep=` / `?templedrop=`) and which rule decides that (`?temples=`). Judging them one address at a time
 *  means a new camera session per value, which is the one thing a visual comparison cannot survive: the light, the
 *  pose and the distance all move between sessions. So the list lives here, the page's sweep selector steps through it
 *  inside ONE session, and a verdict can name an id rather than four numbers.
 *
 *  Nothing here touches anything but the temple arms. The bridge, rims, lenses and endpieces are identical in every
 *  entry, and so is every other part of the pipeline.
 *
 *  The groups are the axes, meant to be walked one at a time. The values are visual choices, not measurements. */
import {DEFAULT_TEMPLE_VISIBILITY_MODE} from './render/temple-visibility.ts';
import type {TempleVisibilityMode} from './render/temple-visibility.ts';

export interface TempleTest {
  /** Short id to report a verdict by: 'A4', 'C3'. Group letter, then the position within the group. */
  readonly id: string;
  readonly group: string;
  /** What this entry changes, in words, relative to the shipped configuration. */
  readonly label: string;
  /** Millimetres of outward splay at the arm's tip. */
  readonly bendMm: number;
  /** Millimetres the bend's pivot is moved back from the frame's own hinge. */
  readonly pivotMm: number;
  /** The relief band in centimetres behind the head surface: drawn whole to `keepCm`, gone past `dropCm`. */
  readonly keepCm: number;
  readonly dropCm: number;
  readonly mode: TempleVisibilityMode;
  /** The id of the earlier entry this one repeats exactly, or null. Each ladder carries the reference rung so a
   *  comparison has something to sit next to, which means a few configurations appear twice; this names the repeat so
   *  none of them is judged twice by accident. */
  readonly sameAs: string | null;
}

/** The bands, named. `sealed` gives the arm up almost the moment it is behind the head; `open` almost never does. */
const BAND = Object.freeze({
  sealed: Object.freeze([0, 0.6] as const),
  tight: Object.freeze([0.3, 1.2] as const),
  standard: Object.freeze([0.6, 2.6] as const),
  loose: Object.freeze([1.5, 6] as const),
  open: Object.freeze([3, 9] as const),
});
const BAND_NAME = new Map(Object.entries(BAND).map(([name, pair]) => [`${pair[0]}/${pair[1]}`, name]));
const describeBand = (keepCm: number, dropCm: number): string =>
  `band ${keepCm}–${dropCm} cm${BAND_NAME.get(`${keepCm}/${dropCm}`) === 'standard' ? '' : ` (${BAND_NAME.get(`${keepCm}/${dropCm}`) ?? 'custom'})`}`;

const SHIPPED_BEND_MM = 14, SHIPPED_PIVOT_MM = 0;

interface Entry {bendMm?: number; pivotMm?: number; band?: readonly [number, number]; mode?: TempleVisibilityMode; label?: string}
const build = (letter: string, group: string, entries: readonly Entry[]): TempleTest[] => entries.map((entry, index) => {
  const bendMm = entry.bendMm ?? SHIPPED_BEND_MM, pivotMm = entry.pivotMm ?? SHIPPED_PIVOT_MM;
  const band = entry.band ?? BAND.standard, mode = entry.mode ?? DEFAULT_TEMPLE_VISIBILITY_MODE;
  return {
    id: `${letter}${index + 1}`, group,
    label: entry.label ?? `bend ${bendMm} mm`,
    bendMm, pivotMm, keepCm: band[0], dropCm: band[1], mode, sameAs: null as string | null,
  };
});

/** Name each exact repeat after the first entry that ran it, so nothing is judged twice by accident. */
const markRepeats = (tests: TempleTest[]): readonly TempleTest[] => {
  const seen = new Map<string, string>();
  return Object.freeze(tests.map(test => {
    const signature = `${test.bendMm}|${test.pivotMm}|${test.keepCm}|${test.dropCm}|${test.mode}`;
    const first = seen.get(signature) ?? null;
    if (first === null) seen.set(signature, test.id);
    return Object.freeze({...test, sameAs: first});
  }));
};

/** Every configuration, in the order the sweep steps through them. */
export const TEMPLE_SWEEP: readonly TempleTest[] = markRepeats([
  // A: the main axis. How far out the arms are bent, everything else as shipped.
  ...build('A', 'How far the arms bend out', [0, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24].map(bendMm => ({bendMm}))),

  // B and C: the same bend spent differently. Moving the pivot back keeps the tip where it is and takes the offset out
  // of the front of the shaft, so the arm hugs the temple and only opens out behind it.
  ...build('B', 'Where the bend pivots, at 10 mm',
    [0, 6, 12, 18, 24, 30].map(pivotMm => ({bendMm: 10, pivotMm, label: `bend 10 mm · pivot ${pivotMm} mm back`}))),
  ...build('C', 'Where the bend pivots, at 16 mm',
    [0, 6, 12, 18, 24, 30].map(pivotMm => ({bendMm: 16, pivotMm, label: `bend 16 mm · pivot ${pivotMm} mm back`}))),

  // D: how much of an arm the head is allowed to take, at the shipped bend.
  ...build('D', 'How soon an arm is given up', [BAND.sealed, BAND.tight, BAND.standard, BAND.loose, BAND.open]
    .map(band => ({band, label: `bend 14 mm · ${describeBand(band[0], band[1])}`}))),

  // E: the two levers crossed. A bigger bend and a wider band both keep more arm; this says which one you are seeing.
  ...build('E', 'Bend against band', [
    {bendMm: 10, band: BAND.tight}, {bendMm: 10, band: BAND.loose},
    {bendMm: 16, band: BAND.tight}, {bendMm: 16, band: BAND.loose},
  ].map(entry => ({...entry, label: `bend ${entry.bendMm} mm · ${describeBand(entry.band[0], entry.band[1])}`}))),

  // F: the pivot crossed with the band, at the bend that clears the head everywhere.
  ...build('F', 'Pivot against band, at 16 mm', [BAND.tight, BAND.standard, BAND.loose]
    .map(band => ({bendMm: 16, pivotMm: 20, band, label: `bend 16 mm · pivot 20 mm back · ${describeBand(band[0], band[1])}`}))),

  // G: the arms pulled IN rather than out, in case the shipped ones are already too wide for this face.
  ...build('G', 'Arms pulled in', [-4, -8, -12, -16].map(bendMm => ({bendMm, label: `bend ${bendMm} mm (pulled in)`}))),

  // H: the rule that shipped before 2026-09-18, which decides from the head's angles rather than per pixel. Here to
  // tell "the new rule is wrong" apart from "the geometry is wrong".
  ...build('H', 'The former angle rule', [0, 10, 14, 16]
    .map(bendMm => ({bendMm, mode: 'angles' as TempleVisibilityMode, label: `bend ${bendMm} mm · angle rule`}))),

  // I: the combinations the measurements like — each clears the head occluder everywhere, by different means.
  ...build('I', 'Measured favourites', [
    {bendMm: 12, pivotMm: 0, label: 'bend 12 mm — the least that clears the head'},
    {bendMm: 16, pivotMm: 30, label: 'bend 16 mm · pivot 30 mm back — nothing at the temple, all of it at the ear'},
    {bendMm: 18, pivotMm: 20, label: 'bend 18 mm · pivot 20 mm back'},
    {bendMm: 20, pivotMm: 30, label: 'bend 20 mm · pivot 30 mm back'},
    {bendMm: 16, pivotMm: 12, band: BAND.tight, label: 'bend 16 mm · pivot 12 mm back · band 0.3–1.2 cm'},
    {bendMm: 12, pivotMm: 0, band: BAND.tight, label: 'bend 12 mm · band 0.3–1.2 cm'},
  ]),
]);

/** The entry with this id, or null. Ids are matched without case. */
export function templeTestById(id: string | null | undefined): TempleTest | null {
  if (typeof id !== 'string') return null;
  const wanted = id.trim().toUpperCase();
  return TEMPLE_SWEEP.find(test => test.id === wanted) ?? null;
}

/** The groups in sweep order, each with its entries. */
export function templeSweepGroups(): {group: string; tests: TempleTest[]}[] {
  const groups: {group: string; tests: TempleTest[]}[] = [];
  for (const test of TEMPLE_SWEEP) {
    const last = groups.at(-1);
    if (last && last.group === test.group) last.tests.push(test);
    else groups.push({group: test.group, tests: [test]});
  }
  return groups;
}

/** One line naming a configuration, short enough to sit in the page's debug line and be read off a screenshot. */
export function describeTempleTest(test: TempleTest): string {
  return `[${test.id}] bend ${test.bendMm} mm · pivot ${test.pivotMm} mm · band ${test.keepCm}–${test.dropCm} cm`
    + `${test.mode === DEFAULT_TEMPLE_VISIBILITY_MODE ? '' : ` · ${test.mode} rule`}`;
}

/** The address that runs one entry from a cold page, for sharing a single configuration. */
export function templeTestSearch(test: TempleTest): string {
  return `?templebend=${test.bendMm}&templepivot=${test.pivotMm}&templekeep=${test.keepCm}&templedrop=${test.dropCm}`
    + `${test.mode === DEFAULT_TEMPLE_VISIBILITY_MODE ? '' : `&temples=${test.mode}`}`;
}

/** The entry that matches the shipped defaults exactly, so the sweep always contains "what ships". */
export const SHIPPED_TEMPLE_TEST = Object.freeze({bendMm: SHIPPED_BEND_MM, pivotMm: SHIPPED_PIVOT_MM,
  keepCm: BAND.standard[0], dropCm: BAND.standard[1], mode: DEFAULT_TEMPLE_VISIBILITY_MODE});
