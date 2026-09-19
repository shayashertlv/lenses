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
 *  ROUND TWO (2026-09-19). The first sweep, ids A..I, is retired; what it settled is not offered again:
 *  - Bends below 12 mm are out. Nothing under 12 scored better than "pretty good", and the measurement says why: at
 *    bend 10 thirty-one of the arm's sixty-three stations are still INSIDE the head occluder, at 12 mm nine are, and
 *    at 16 mm none are. "The temples enter the face" is the arm being drawn where it is inside the head.
 *  - Wide relief bands are out. 1.5–6 cm and 3–9 cm read "not good" at every bend tried.
 *  - Moving the bend's pivot back from the hinge is out. Four groups agreed: it is worse, at every bend and band.
 *  - Pulling the arms inward is out, and so is the former angle rule.
 *  - The band and the bend are NOT independent: a narrow band deletes whatever is behind the head surface, so while
 *    part of the arm is still buried (bend <= 10) tightening the band destroys it, and once nothing is buried
 *    (bend >= 12) tightening it only removes the arm where it genuinely is behind the head. Round two therefore walks
 *    the band only at bends that clear the head.
 *
 *  What round two adds is the hair cut, because that is where the remaining defect lives. Every configuration the
 *  wearer called near perfect carried the same complaint — the ends of the arms appear and disappear. The cut fires
 *  when it finds a run of hair at least `runPx` pixels long along the arm's centreline, and on the checked-in fixture
 *  that decision is a cliff: at 10 px it removes the last 6.5 cm of arm, at 14 px it removes nothing, with no
 *  hysteresis and no temporal filter. A mask boundary that moves four pixels flips the whole end of the temple. The
 *  cut group asks the wearer to confirm that on a real head; N1 (no cut at all) is the decisive one.
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
  /** Millimetres the bend's pivot is moved back from the frame's own hinge. Round two leaves this at 0 throughout:
   *  round one settled it, in four separate groups. */
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

/** The bands, narrow to wide. Round one ruled out everything wider than `standard`. */
const BAND = Object.freeze({
  sealed: Object.freeze([0, 0.6] as const),
  narrow: Object.freeze([0.15, 0.8] as const),
  tight: Object.freeze([0.3, 1.2] as const),
  middling: Object.freeze([0.45, 1.8] as const),
  standard: Object.freeze([0.6, 2.6] as const),
  loose: Object.freeze([1.5, 6] as const),
});
const band = (pair: readonly [number, number]): string => `band ${pair[0]}–${pair[1]} cm`;

interface Entry {
  bendMm?: number; band?: readonly [number, number]; mode?: TempleVisibilityMode;
  cut?: boolean; runPx?: number; hair?: boolean; label?: string;
}
const build = (letter: string, group: string, entries: readonly Entry[]): TempleTest[] => entries.map((entry, index) => ({
  id: `${letter}${index + 1}`, group,
  label: entry.label ?? `bend ${entry.bendMm ?? 16} mm`,
  bendMm: entry.bendMm ?? 16, pivotMm: 0,
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
  // J: the main axis, over the range that clears the head, at the band that worked best there.
  ...build('J', 'How far the arms bend out', [12, 14, 16, 18, 20, 22, 24]
    .map(bendMm => ({bendMm, band: BAND.tight, label: `bend ${bendMm} mm · ${band(BAND.tight)}`}))),

  // K, L, M: how soon the head takes the arm, at three bends. Narrow bands only — the wide ones are settled.
  ...build('K', 'How soon an arm is given up, at 14 mm', [BAND.sealed, BAND.tight, BAND.standard]
    .map(pair => ({bendMm: 14, band: pair, label: `bend 14 mm · ${band(pair)}`}))),
  ...build('L', 'How soon an arm is given up, at 16 mm', [BAND.sealed, BAND.narrow, BAND.tight, BAND.middling, BAND.standard]
    .map(pair => ({bendMm: 16, band: pair, label: `bend 16 mm · ${band(pair)}`}))),
  ...build('M', 'How soon an arm is given up, at 20 mm', [BAND.sealed, BAND.tight, BAND.standard]
    .map(pair => ({bendMm: 20, band: pair, label: `bend 20 mm · ${band(pair)}`}))),

  // N: the hair cut, which is what decides the END of the arm — the one complaint every good configuration carried.
  // N1 is the decisive one: no cut at all. If the ends stop misbehaving there, the cut is the cause.
  ...build('N', 'What the hair cut does to the ends, at 16 mm', [
    {cut: false, label: 'no hair cut at all — the ends cannot flicker'},
    {runPx: 4, label: 'hair cut, cuts eagerly (4 px of hair)'},
    {runPx: DEFAULT_CONTINUITY_RUN_PX, label: `hair cut as shipped (${DEFAULT_CONTINUITY_RUN_PX} px of hair)`},
    {runPx: 16, label: 'hair cut, cuts reluctantly (16 px of hair)'},
    {runPx: 30, label: 'hair cut, almost never cuts (30 px of hair)'},
    {hair: false, label: 'no hair over the arms at all — the control'},
  ].map(entry => ({...entry, bendMm: 16, band: BAND.tight}))),

  // P: the same question at the bend where the ends misbehaved most. A bigger bend swings the arm into the hair
  // sooner, so the cut takes more of it: 6.5 cm of arm at 16 mm, 5.1 cm at 24 mm on the fixture.
  ...build('P', 'What the hair cut does at 22 mm', [
    {cut: false, label: 'bend 22 mm · no hair cut at all'},
    {runPx: DEFAULT_CONTINUITY_RUN_PX, label: 'bend 22 mm · hair cut as shipped'},
    {runPx: 30, label: 'bend 22 mm · hair cut, almost never cuts'},
  ].map(entry => ({...entry, bendMm: 22, band: BAND.tight}))),

  // Z: two configurations round one called "not good". Judge them first and last: if they do not read as bad in this
  // session, the session's own ratings are drifting and the rest of it cannot be ranked finely.
  ...build('Z', 'Controls — round one called these bad', [
    {bendMm: 0, band: BAND.standard, label: 'no bend at all (round one: "not good")'},
    {bendMm: 14, band: BAND.loose, label: 'bend 14 mm · band 1.5–6 cm (round one: "not good")'},
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
export const SHIPPED_TEMPLE_TEST = Object.freeze({bendMm: 14, pivotMm: 0,
  keepCm: BAND.standard[0], dropCm: BAND.standard[1], mode: DEFAULT_TEMPLE_VISIBILITY_MODE,
  cut: true, runPx: DEFAULT_CONTINUITY_RUN_PX, hair: true});
