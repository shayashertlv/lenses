/** The temple test sweep: the temple configurations worth judging, in one list, stepped inside one camera session.
 *
 *  Why it exists. The temple levers interact, and judging them one address at a time means a new camera session per
 *  value — which is the one thing a visual comparison cannot survive, because the light, the pose and the distance all
 *  move between sessions. So the list lives here, the page's sweep selector steps through it live, and a verdict can
 *  name an id rather than five numbers.
 *
 *  Nothing here touches anything but the temple arms. The bridge, rims, lenses and endpieces are identical in every
 *  entry; the hair cut and the hair occlusion below act only on the arms.
 *
 *  ROUND THREE (2026-09-19), ids Q..Z. Rounds one (A..I) and two (J..P) are retired; what they settled is not
 *  offered again:
 *  - Bends below 12 mm are out. Nothing under 12 scored better than "pretty good", and the measurement says why: at
 *    bend 10 thirty-one of the arm's sixty-three stations are still INSIDE the head occluder, at 12 mm nine are, and
 *    at 16 mm none are. "The temples enter the face" is the arm being drawn where it is inside the head.
 *  - Wide relief bands are out. 1.5–6 cm and 3–9 cm read "not good" at every bend tried.
 *  - Moving the bend's pivot back from the hinge is out. Four groups agreed: it is worse, at every bend and band.
 *  - Pulling the arms inward is out, and so is the former angle rule.
 *  - The band and the bend are NOT independent: a narrow band deletes whatever is behind the head surface, so while
 *    part of the arm is still buried (bend <= 10) tightening the band destroys it, and once nothing is buried
 *    (bend >= 12) tightening it only removes the arm where it genuinely is behind the head.
 *  - **Round two's verdict: 18 mm looks the best**, judged against 12..24 mm at the 0.3–1.2 cm band. That pair is
 *    now what ships, and round three is built around it.
 *
 *  So round three asks the three questions that are left, all of them AT 18 mm:
 *  - Is 18 a peak or a plateau? The bend ladder walks 15..21 mm in single millimetres.
 *  - Which band, now that the bend has been chosen? Round two walked the band at 14, 16 and 20 mm but never at 18.
 *  - What does the hair cut do to the ends? That is the one complaint every good configuration has carried: the ends
 *    of the arms appear and disappear. The cut fires when it finds a run of hair at least `runPx` pixels long along
 *    the arm's centreline, and on the checked-in fixture that decision is a cliff: at 10 px it removes the last
 *    6.5 cm of arm, at 14 px it removes nothing, with no hysteresis and no temporal filter, so a mask boundary that
 *    moves four pixels flips the whole end of the temple. S1 (no cut at all) is the decisive entry, and S6 (no hair
 *    over the arms at all) is the backstop: if the ends still misbehave there, it is not hair.
 *  - And whether the band and the cut interact, which decides whether they can be chosen separately.
 *
 *  The values are visual choices. The measurements behind them are against a proxy head, not a wearer's anatomy. */
import {DEFAULT_TEMPLE_VISIBILITY_MODE} from './render/temple-visibility.ts';
import type {TempleVisibilityMode} from './render/temple-visibility.ts';
import {DEFAULT_CONTINUITY_RUN_PX} from './render/continuity.ts';

export interface TempleTest {
  /** Short id to report a verdict by: 'J4', 'N1'. Group letter, then the position within the group. */
  readonly id: string;
  readonly group: string;
  /** What this entry changes, in words, relative to the others. */
  readonly label: string;
  /** Millimetres of outward splay at the arm's tip. */
  readonly bendMm: number;
  /** Millimetres the bend's pivot is moved back from the frame's own hinge. Round three leaves this at 0
   *  throughout: round one settled it, in four separate groups. */
  readonly pivotMm: number;
  /** The relief band in centimetres behind the head surface: drawn whole to `keepCm`, gone past `dropCm`. */
  readonly keepCm: number;
  readonly dropCm: number;
  readonly mode: TempleVisibilityMode;
  /** Whether the hair continuity cut runs at all, and the run of hair in pixels it needs before it cuts an arm. */
  readonly cut: boolean;
  readonly runPx: number;
  /** Whether hair hides the arms at all. Off is a control: it removes every hair term from the temples at once. */
  readonly hair: boolean;
  /** The id of the earlier entry this one repeats exactly, or null. The ladders share rungs on purpose, so a band
   *  ladder and the bend ladder meet at the same configuration; this names the repeat rather than hiding it. */
  readonly sameAs: string | null;
}

/** The bands, narrow to wide. Round one ruled out everything wider than `standard`; `tight` is what ships. */
const BAND = Object.freeze({
  sealed: Object.freeze([0, 0.6] as const),
  narrow: Object.freeze([0.15, 0.8] as const),
  tight: Object.freeze([0.3, 1.2] as const),
  middling: Object.freeze([0.45, 1.8] as const),
  standard: Object.freeze([0.6, 2.6] as const),
  loose: Object.freeze([1.5, 6] as const),
});
const band = (pair: readonly [number, number]): string => `band ${pair[0]}–${pair[1]} cm`;

/** The bend round three is built around: the wearer's own pick from round two. Every group that is not asking about
 *  the bend itself sits here, so the band and the hair cut are judged at the bend that ships. */
const AROUND_MM = 18;

interface Entry {
  bendMm?: number; band?: readonly [number, number]; mode?: TempleVisibilityMode;
  cut?: boolean; runPx?: number; hair?: boolean; label?: string;
}
const build = (letter: string, group: string, entries: readonly Entry[]): TempleTest[] => entries.map((entry, index) => ({
  id: `${letter}${index + 1}`, group,
  label: entry.label ?? `bend ${entry.bendMm ?? AROUND_MM} mm`,
  bendMm: entry.bendMm ?? AROUND_MM, pivotMm: 0,
  keepCm: (entry.band ?? BAND.tight)[0], dropCm: (entry.band ?? BAND.tight)[1],
  mode: entry.mode ?? DEFAULT_TEMPLE_VISIBILITY_MODE,
  cut: entry.cut ?? true, runPx: entry.runPx ?? DEFAULT_CONTINUITY_RUN_PX, hair: entry.hair ?? true,
  sameAs: null as string | null,
}));

/** Name each exact repeat after the first entry that ran it, so nothing is judged twice without being told. */
const markRepeats = (tests: TempleTest[]): readonly TempleTest[] => {
  const seen = new Map<string, string>();
  return Object.freeze(tests.map(test => {
    const signature = [test.bendMm, test.pivotMm, test.keepCm, test.dropCm, test.mode, test.cut, test.runPx, test.hair].join('|');
    const first = seen.get(signature) ?? null;
    if (first === null) seen.set(signature, test.id);
    return Object.freeze({...test, sameAs: first});
  }));
};

/** Every configuration, in the order the sweep steps through them. */
export const TEMPLE_SWEEP: readonly TempleTest[] = markRepeats([
  // Q: is 18 mm a peak or a plateau? Single millimetres either side of the wearer's pick, at the band it ships with.
  ...build('Q', 'How far the arms bend out, around 18 mm', [15, 16, 17, 18, 19, 20, 21]
    .map(bendMm => ({bendMm, band: BAND.tight, label: `bend ${bendMm} mm · ${band(BAND.tight)}`}))),

  // R: the band at 18 mm, which no round has walked. Narrow bands only — the wide ones are settled.
  ...build('R', 'How soon an arm is given up, at 18 mm', [BAND.sealed, BAND.narrow, BAND.tight, BAND.middling, BAND.standard]
    .map(pair => ({band: pair, label: `bend 18 mm · ${band(pair)}`}))),

  // S: the hair cut, which decides where an arm ENDS — the one complaint every good configuration has carried.
  // S1 is decisive (no cut at all); S6 is the backstop (no hair over the arms at all).
  ...build('S', 'What the hair cut does to the ends, at 18 mm', [
    {cut: false, label: 'no hair cut at all — the ends cannot flicker'},
    {runPx: 4, label: 'hair cut, cuts eagerly (4 px of hair)'},
    {runPx: DEFAULT_CONTINUITY_RUN_PX, label: `hair cut as shipped (${DEFAULT_CONTINUITY_RUN_PX} px of hair)`},
    {runPx: 16, label: 'hair cut, cuts reluctantly (16 px of hair)'},
    {runPx: 30, label: 'hair cut, almost never cuts (30 px of hair)'},
    {hair: false, label: 'no hair over the arms at all — the backstop'},
  ].map(entry => ({...entry, band: BAND.tight}))),

  // T: the band and the cut together. If the best band is the same with the cut out of the way as with it in, the two
  // can be chosen separately; if it is not, they cannot, and the cut has to be settled first.
  ...build('T', 'The band and the cut together, at 18 mm', [
    {band: BAND.sealed, cut: false, label: `${band(BAND.sealed)} · no hair cut`},
    {band: BAND.tight, cut: false, label: `${band(BAND.tight)} · no hair cut`},
    {band: BAND.standard, cut: false, label: `${band(BAND.standard)} · no hair cut`},
    {band: BAND.sealed, runPx: 30, label: `${band(BAND.sealed)} · hair cut almost never`},
    {band: BAND.standard, runPx: 30, label: `${band(BAND.standard)} · hair cut almost never`},
  ]),

  // Z: two configurations earlier rounds called "not good", and the one that shipped until today. Judge the two bad
  // ones first and last: a session that rates them well is a session whose ratings are drifting.
  ...build('Z', 'Controls and the previous default', [
    {bendMm: 0, band: BAND.standard, label: 'no bend at all (round one: "not good")'},
    {bendMm: 14, band: BAND.loose, label: 'bend 14 mm · band 1.5–6 cm (round one: "not good")'},
    {bendMm: 14, band: BAND.standard, label: 'bend 14 mm · band 0.6–2.6 cm — what shipped until today'},
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
  return `[${test.id}] bend ${test.bendMm} mm · band ${test.keepCm}–${test.dropCm} cm`
    + ` · ${!test.hair ? 'no hair on the arms' : test.cut ? `hair cut at ${test.runPx} px` : 'no hair cut'}`
    + `${test.pivotMm === 0 ? '' : ` · pivot ${test.pivotMm} mm`}`
    + `${test.mode === DEFAULT_TEMPLE_VISIBILITY_MODE ? '' : ` · ${test.mode} rule`}`;
}

/** The address that runs one entry from a cold page, for sharing a single configuration. */
export function templeTestSearch(test: TempleTest): string {
  return `?templebend=${test.bendMm}&templepivot=${test.pivotMm}&templekeep=${test.keepCm}&templedrop=${test.dropCm}`
    + `${test.mode === DEFAULT_TEMPLE_VISIBILITY_MODE ? '' : `&temples=${test.mode}`}`
    + `${test.cut ? `&hairrun=${test.runPx}` : '&continuity=0'}`
    + `${test.hair ? '' : '&hair=0'}`;
}

/** The configuration the page ships with, so the sweep always contains "the same as now". */
export const SHIPPED_TEMPLE_TEST = Object.freeze({bendMm: 18, pivotMm: 0,
  keepCm: BAND.tight[0], dropCm: BAND.tight[1], mode: DEFAULT_TEMPLE_VISIBILITY_MODE,
  cut: true, runPx: DEFAULT_CONTINUITY_RUN_PX, hair: true});
