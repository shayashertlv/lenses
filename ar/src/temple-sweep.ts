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
 *  ROUND FOUR (2026-09-19), ids U..Z. Earlier rounds are retired; what they settled is not offered again:
 *  - Bends below 12 mm, bands wider than 0.6-2.6 cm AS A SETTING, pivot offsets, inward bends and the angle rule were
 *    all ruled out by round one. Round two picked the bend: **18 mm**, at the 0.3-1.2 cm band, and that pair ships.
 *  - Round three answered the question that mattered most, with three entries:
 *      * the hair cut as shipped read "great";
 *      * REMOVING the cut was WORSE — so the cut earns its keep and must be stabilised, not deleted;
 *      * and with NO HAIR OVER THE ARMS AT ALL the ends still popped.
 *    That last one is decisive: the popping is not the hair mask and not the cut. Whatever moves the end of the arm
 *    is still moving it when hair plays no part.
 *
 *  So round four is a diagnostic. It strips one decision at a time until the popping stops, and it re-opens the one
 *  axis nobody has separated: the relief band's ONSET and its GRADIENT. Every band offered so far moved both ends
 *  together, which confounds them. The band turns a depth into a coverage, so its gradient is a gain on depth noise:
 *  the shipped 0.3-1.2 cm band is 9 mm wide, which is 2.2x steeper than the 0.6-2.6 cm band it replaced, so a couple
 *  of millimetres of jitter in the head's own reconstructed depth swings the end of the arm through a large part of
 *  its coverage range. A band that starts just as early but finishes far later keeps what the wearer liked (the arm
 *  tucks away promptly) while cutting that gain several-fold — and no round has tried one.
 *
 *  The W group is the backstop ladder. W3 opens the band so wide that nothing is taken from the arm at all and turns
 *  hair off with it: if the ends still pop THERE, nothing that decides visibility is responsible, and the cause is in
 *  the geometry or the anti-aliasing of a thin arm rather than in any occlusion rule.
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

/** The bend every judged entry sits at: the wearer's own pick, now the shipped default. Round four is not asking
 *  about the bend at all — it is asking what makes the END of the arm pop. */
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
  // U: the band's GRADIENT, with its onset pinned where the wearer liked it. Same moment the arm starts being given
  // up, progressively gentler slope — which is progressively less gain on the head depth's own jitter.
  ...build('U', 'How steeply an arm is given up, onset fixed', [1.2, 2, 3, 4.5, 6]
    .map(dropCm => ({band: [0.3, dropCm] as const, label: `band 0.3–${dropCm} cm${dropCm === 1.2 ? ' (as shipped)' : ''}`}))),

  // V: the ONSET, with the gradient pinned gentle. Says whether "tucks away early" can be kept once the slope is not
  // doing the damage.
  // Written out rather than computed, so the labels and the addresses carry round decimals.
  ...build('V', 'How soon an arm starts being given up, gradient fixed',
    ([[0, 2.7], [0.3, 3], [0.6, 3.3], [1.2, 3.9]] as const)
      .map(pair => ({band: pair, label: `band ${pair[0]}–${pair[1]} cm`}))),

  // W: strip one decision at a time. W3 is the backstop — nothing removes any part of the arm.
  ...build('W', 'What is left when each decision is removed', [
    {label: 'everything as shipped — the baseline to pop against'},
    {hair: false, label: 'no hair over the arms — only the depth rule decides'},
    {hair: false, band: [6, 12] as const, label: 'nothing removes the arm at all — no hair, band wide open'},
    {mode: 'angles' as const, label: 'the former angle rule instead of the per-pixel depth rule'},
  ]),

  // Z: two configurations earlier rounds called "not good", and the one that shipped before the bend was chosen.
  // Judge the bad ones first and last: a session that rates them well is a session whose ratings are drifting.
  ...build('Z', 'Controls and the previous default', [
    {bendMm: 0, band: BAND.standard, label: 'no bend at all (round one: "not good")'},
    {bendMm: 14, band: BAND.loose, label: `bend 14 mm · band ${BAND.loose[0]}–${BAND.loose[1]} cm (round one: "not good")`},
    {bendMm: 14, band: BAND.standard, label: `bend 14 mm · band ${BAND.standard[0]}–${BAND.standard[1]} cm — what shipped before 18 mm was chosen`},
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
