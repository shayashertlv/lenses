# Isolated FPS comparison

This experiment implements separate candidates from the September 14 FPS
review. G remains the accepted application and the normal launch is unchanged.
The G option imports the original speed-lab entry. Only the candidate modes
load the separately copied runtime under this directory. All work stays local.

## Next tests

The final nine-session run shows preliminary promise for Test 6:

| Mode | Pooled FPS | Capture-to-publication age p95 |
| --- | ---: | ---: |
| Original G | 9.28 | 236.2 ms |
| Test 4: CPU pixel processing | 9.72 | 223.7 ms |
| Test 5: lighter statistics | 9.05 | 231.7 ms |
| Test 6: both CPU changes | 10.20 | 216.9 ms |

Test 6 was about 9.9% above pooled G in this run. Its two sessions measured
9.94/10.45 FPS; G measured 9.77/8.72/9.35. The baseline variation remains material,
so this is a candidate for visual/live testing, not an established universal gain.
Test 5 alone did not improve FPS; the combined result does not prove its separate
contribution. G remains the accepted version.

All 1,712 measured frames had tracking, matching masks, nonzero hair edits and
actual PBO readback, with no fallback. This run used one preserved generated
portrait at natural 1280x853, Amber Horizon/hair-only and fresh GPU inference,
five-second warmup and twenty-second windows in
G / 4 / 5 / 6 / G / 6 / 5 / 4 / G order. Windows were pooled only within sessions.
The host reported Balanced power and AC; temperature and unrelated application
load were not controlled. This differs from the first round's 1280x720 zero-edit
fixture; compare candidates within each table. No moving-camera or mobile FPS
measurement and no owner visual acceptance is supplied.

Open [Test 6](http://127.0.0.1:8100/experiments/fps-candidate/live.html?fps=next-combined),
then use Open camera or Compare G / test / test / G. Timing receipts:
[summary](qa/output/round2-summary.json), [all measured rows](qa/output/round2-balanced/report.json),
[host context](qa/output/round2-host-before-timing.json).

Test 4 reduces CPU composition work using full RGBA word comparisons, a shorter
background branch, local counters and exactly rounded RGB writes. It preserves
the complete reference scan, mask validation, continuity and the independent
final audit of every output pixel. Unaligned inputs retain the preceding byte
implementation and explicitly report that the optimized path was unused.

Test 5 reuses one statistics snapshot within the synchronous publication callback
and keeps incremental counts for the current ten-second window. FPS and coverage
remain current on every completed frame; only human percentile/stage readouts are
cached for at most 500 ms. Every frame is still sampled, with the existing
4,096-row retention limit; timed comparison segments retain all measured rows.
Final summaries and exports use the raw data rather than the UI cache.
Test 6 combines these two changes. All three retain G's original frame hashes
and graphics-query settings, independently of the first three tests.

The first round's 63 source files are preserved in the ignored
`.recovery/fps-candidate-2026-09-14/round1-final/` archive. Its three modes remain
selectable in this page with the new CPU options disabled.

Validation passed 72 focused tests, strict TypeScript, the isolated build, all
56 matched-image cases and six production lifecycle configurations. These cover
both glasses/hair models, down/up/both yaw, full pixel safeguards, genuine held
hashes, failure cleanup, stop and restart. Hardware matched cases used PBO in
32/32; the recorded software phase required the unchanged bounded fallback in
21/24 G and 23/24 candidate cases, so it supplies correctness evidence only.
The required `npm test` again passed 186 unit tests and all 11 G browser cases,
with the same existing preserved `test_` selector failure (8/9). The separately
run preserved hair suite passed 2/2. No deadline or check was relaxed.

Local receipts: [matched images](qa/output/matched-2026-09-14T08-50-35.638Z/report.json),
[Test 6 lifecycle](qa/output/round2-final-controls-1789376418163/report.json),
[Test 4/5 lifecycle](qa/output/round2-single-controls-1789376527093/report.json).
The production build stayed
`24ef9fec6e664a84096027b309807652001ba94bb357cec08dc9f22a1d521216`
through all final production checks and timing sessions.

## First-round result — September 14, 2026

Implemented and validated, but no reliable FPS improvement is established.
The final interleaved production sweep measured completed unique publications:

| Mode | Pooled FPS | Capture-to-publication age p95 |
| --- | ---: | ---: |
| Original G | 10.78 | 216.8 ms |
| Test 1: fewer graphics queries | 10.36 | 225.5 ms |
| Test 3: queries and live frame IDs | 10.90 | 208.1 ms |

The combined gain is about 1.1% across this sweep, with substantial baseline
drift (individual G sessions ranged from 10.05 to 12.20 FPS). The first revision
also failed to establish a gain; live IDs alone measured 10.17 versus G's 10.25
FPS. These are synthetic desktop measurements, not physical display scanout or
wearer/mobile evidence. The static fixture exercised full hair inference and
mask coverage but produced zero visible hair replacement pixels. Timings use
Amber Horizon and the hair-only model, 1280x720, five seconds of warmup and
20 seconds of measurement per session. Raw rows retain all samples.

Final validation passed 47 focused tests, strict TypeScript, the isolated build,
56 matched-image cases and four production lifecycle combinations across both
glasses and both hair models. All 249 original files and 302 frozen inputs remain
unchanged. The required `npm test` retains the existing preserved-reference
`test_` model-selector failure; its unit and G suites passed. The separately run
preserved hair suite passed. No candidate has been promoted or visually accepted.

Local receipts: [timing summary](qa/output/timing-summary.json),
[final timings](qa/output/revision2-balanced/report.json),
[matched images](qa/output/matched-2026-09-14T08-11-58.677Z/report.json),
[production lifecycle](qa/output/final-controls-revision2/report.json).

## Running the comparison

| Choice | Change |
| --- | --- |
| G | Original G, including original frame hashing and graphics queries. |
| Test 1 | Validate private pixel-pack state once and avoid redundant graphics queries; retain failed-read rejection, boundary error checks and cancellation. |
| Test 2 | Explicit session/frame source and detection IDs in live processing; genuine content hashes calculated from the retained image/detection on Hold. |
| Test 3 | Both changes together. |
| Test 4 | Cheaper equivalent CPU pixel processing, with every final pixel check retained. |
| Test 5 | Reuse publication statistics and incrementally maintain FPS/coverage; cache only human percentile/stage readouts for 500 ms. |
| Test 6 | Test 4 and Test 5 together; original frame hashing and graphics queries remain. |

Both accepted glasses and hair models are available. The models, source
resolution, bounded two-image scheduler, exact image/detection/mask pairing,
temple geometry and final nose/front/outside-editable protections remain.
No frame interpolation, lower-resolution mode or stale-mask reuse is introduced.
Changing the performance test starts a fresh session; it cannot change the
ownership protocol underneath an in-flight frame.

From `ar_v4`, build and run the production preview:

```powershell
npx tsc --noEmit -p experiments/fps-candidate/tsconfig.json
npx vite build --config experiments/fps-candidate/vite.config.ts
npx vite preview --config experiments/fps-candidate/vite.config.ts
```

Open [the comparison](http://127.0.0.1:8100/experiments/fps-candidate/live.html).
Select a test and Open camera. Use Compare G / test / test / G for repeated fresh
sessions, five seconds of warmup with at least three tracked/masked frames, then
30 seconds of measurement per segment. Keep the page foreground, the same power
setting and similar movements. The final report includes tracking and hair-mask
coverage, frame age and cadence, not just FPS. Completed and interrupted measured
data can be downloaded locally. No camera images or source/detection identities
are included in timing reports.

Hold retains the exact image and computes true SHA-256 image/detection hashes.
The inherited held panel compares the retained eight rendering profiles on that
image. It does not by itself compare live scheduling across G and this candidate;
the separate matched and live tests provide that evidence. Never interpret a
faster synthetic rate as wearer-motion approval or measured mobile performance.

For the development/matched-test server use:

```powershell
npx vite --config experiments/fps-candidate/vite.config.ts
```

This uses port 8101. The production preview uses port 8100.

## Validation

```powershell
node experiments/fps-candidate/qa/verify-preservation.mjs --verify
node --test experiments/fps-candidate/qa/identity.test.ts experiments/fps-candidate/runtime/experiments/speed-lab/native/pbo-readback.test.ts
node --test experiments/fps-candidate/qa/study.test.mjs
node --test experiments/fps-candidate/qa/options-round2.test.ts experiments/fps-candidate/qa/compose-round2.test.ts experiments/fps-candidate/qa/bookkeeping-round2.test.ts
npm test
node experiments/fps-candidate/qa/measure.mjs --base=http://127.0.0.1:8100
node experiments/fps-candidate/qa/measure.mjs --controls --order=combined --matrix
node experiments/fps-candidate/qa/matched.mjs --base=http://127.0.0.1:8101 --variant=combined --phase=all --allow-async-fallback
```

Schedule GPU/browser runs sequentially. `measure.mjs` defaults to the balanced
G / queries / identity / combined / combined / identity / queries / G order,
at 1280x720 with a static synthetic 30 FPS canvas camera. Its camera shares the
application thread; this is not the owner's physical camera or phone. Use
`--matrix` to cover both glasses and both hair models. All measured samples,
including missing tracking/masks and fallbacks, stay in the result.

Matched tests read the preserved optional private generated/recorded inputs
without changing them. They cover 32 generated and 24 recorded pairs, including
down/up/both yaw and final guards. `--allow-async-fallback` permits only the
existing bounded software fallback; hardware cases must exercise actual PBOs.
The harness records the known older-manifest mismatch from pre-existing opt-in
edits and verifies all 249 original files against this task's starting bytes.

Ignored receipts belong in `qa/output/`. Production `fps-build.json` fingerprints
candidate source and the original-source preservation manifest. The scaffold and
page-builder scripts are one-time construction tools, not normal run commands;
they intentionally refuse to overwrite existing files.

Results and limitations are recorded in the current `docs/REVIEWS.md` entry.
No baseline promotion, public deployment or owner visual acceptance is implied.

For the next tests, use an existing hair-editing fixture rather than the original
zero-edit image. This optional command requires the preserved local archive:

```powershell
node experiments/fps-candidate/qa/measure.mjs --fixture=generated:02-down-turn-auburn --require-hair-edits --order=g,compose,bookkeeping,next-combined,g,next-combined,bookkeeping,compose,g
node experiments/fps-candidate/qa/measure.mjs --controls --order=next-combined --matrix --fixture=generated:02-down-turn-auburn --require-hair-edits
node experiments/fps-candidate/qa/matched.mjs --base=http://127.0.0.1:8101 --variant=next-combined --phase=all --allow-async-fallback
```

The source is read in place at its natural 1280x853 size and hash-verified before
and after each run. Face/hair inference runs anew; no archived detections or masks
are substituted for live work. `--require-hair-edits` fails a run containing any
measured zero-edit frame; it never filters those frames out. A repeated still
with changing live inference is not moving-wearer or phone evidence.
