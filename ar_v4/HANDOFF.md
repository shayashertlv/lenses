# Current AR handoff — speed testing

On September 9, 2026 the owner explicitly selected **Test 2 as the current base**
and authorized commit/push as **speed testing**. Default dev/build/preview now
use `vite.speed.config.ts`; the root route opens the Test 2 comparison with
**Current · Test 2** selected. The four-way Test 3 comparison remains available.
This changes the launch choice, not Test 2's rendering implementation.

The `current` report ID still denotes the original long-hair checkpoint;
`test2` denotes the new base. IDs and legacy `candidateAccepted` diagnostics
are preserved for historical comparability; they do not describe the new default.
User-visible labels distinguish the two. Future experiments must preserve this
base and earn matched evidence and owner visual acceptance.

The original long-hair checkpoint is `9997050`; its page, renderer and
`vite.hair.config.ts` remain unchanged. Use `dev:long-hair`, `build:long-hair`
and `preview:long-hair` for that launch choice. All default builds retain its
page, perfect temples and original index. Parent Python/Railway deployment,
private recordings, linked worktrees and unrelated trial-model edits are separate.

The uploaded four-way report contains 1,139 measured frames across four complete
30-second windows with full face/mask coverage. Test 2 led at 10.16 fps versus
7.71 for the original checkpoint and 9.96 for Test 3. Reported video delivery was
29.6 fps. First nonzero rear-drop activations caused large stalls in Tests 2/3;
Test 2 also spent a median 24.74 ms waiting for its next callback. Different live
poses and absent source images prevent exact same-image visual conclusions.
Mobile smoothness, thermal behavior and motion-to-photon latency remain unknown.

Commit scope includes the three implemented performance comparisons and their
reproducible runtime/tests, plus launch/docs changes. Optional evidence tools
still need ignored local archives; no recording or generated wearer image is
published. Current verification and next research directions are in
[docs/REVIEWS.md](docs/REVIEWS.md).

Current clean-export verification: strict types/build, 90 focused speed checks
and all seven promoted browser cases passed. Full npm test passed 110 unit
tests and all eight baseline browser cases, then failed the preserved long-hair
multiclass restart while waiting for a valid category mask; hair-only passed.
No failed test was retried or given a longer deadline.

## Preserved long-hair checkpoint handoff

The owner approved integration and commit/push with the exact message
**perfecto long hair but slow** on September 9, 2026, after positive live/still
feedback and an explicit discussion of the remaining speed limitations.
This authorization promotes the reviewed hair behavior; it is not evidence of
smooth mobile performance or a request to optimize or reconstruct anything new.

## Entry points and reproducibility

`npm run dev`, `npm run build` and `npm run preview` use
`vite.hair.config.ts`. The root route redirects to
`experiments/hair-live-preview/live.html`. The older perfect-temples page and
`/index.html` remain available. `dev:reference` / `preview:reference` keep the
original landing route. The parent Python app and Railway deployment are unchanged.

The accepted source under `references/perfect-temples/` contains 45 byte-exact
files from `b26b5584c0dccbc2b30e4f12cdd432f10df577ea`, with a verified manifest.
The resolver pins all reference imports to those files. No old Git objects or
private .recovery files are required to start or build this checkpoint. Both
reviewed hair weights are tracked with hashes and attribution; the worker URLs
and model contents remain the same as the reviewed candidate.

Shared trial-model / model_studio / performance-review changes belong to other
work and must stay outside this checkpoint. Do not restore or stage them.

## Accepted behavior

The current app runs the reviewed local face/pose pipeline, rear-temple curve,
original eyewear assets and 15 mm fade. Same-source hair processing overlaps
private reference rendering. Category-only live masks avoid unused confidence
readback. GPU initialization can fall back to a fresh CPU worker. Only a mask
belonging to the exact current image and detection may be applied; late or
invalid results fall back to the accepted reference. Hair off skips new hair work.

The category compositor and projected-arm continuity check are unchanged.
All original final nose/front, outside-arm, background and alpha protections
remain. Geometry, projection, visibility and optical materials were not tuned
during promotion. Existing `acceptedCommit` diagnostic metadata retains its
b26 reference meaning; additive `acceptedRevisionLabel` and `hairAccepted`
identify this authorized checkpoint.

Hold finishes the owned pair, stops camera/workers, and requests full diagnostic
masks on that exact image using a temporary worker. Resume starts a fresh session.
Downloads remain explicit and local. The older full recording/replay workflow
stays on the reference entry point.

## Evidence and uncertainty

The owner's visual approval is the acceptance basis. Prior matched evidence
includes both frames and both hair models, eight new generated portraits
(32 cases), six exact original wearer pairs (24 cases), down/up/both yaw and
nose/front checks. Generated estimates reach roughly −31° elevation to +22°,
yaw −42° to +30°; selected short-haired wearer recordings reach about ±69° yaw.
The generated requested 60° profiles were not achieved. No physical long-hair
motion, true hair depth or personal fit ground truth was established.

The short hardware desktop simulation measured about 5.35–5.48 updates/second
with hair versus 8.18–8.57 without. Mobile smoothness, heat, battery usage and
sustained camera performance are unmeasured. Keep these limitations explicit.
Do not weaken nose/front protection, pairing or reference preservation to make
a future optimization appear faster.

Promotion was checked in an exact staged export with no app Git directory or
private recovery data. Fresh npm ci, strict types, asset verification, build
and all 110 unit tests passed. Seven of eight baseline browser tests passed.
The unchanged 60-second recorded-replay test timed out at final camera
restart after its exact native replay image comparison had passed; the full
`npm test` therefore remains failed. Both production hair lifecycle tests
passed in a separate invocation, using real local workers and checked-in
synthetic input. No deadline was raised and no failed test was retried.

All 32 generated and 24 exact recorded comparisons match the reviewed
accepted/hair output pixels and geometry, including unchanged nose/front
and outside-arm checks. Independent audits verified 226 PNG files. These
results support preservation within the tested inputs, not a new speed or
physical long-hair motion claim. Details and retained failures are recorded
in [docs/REVIEWS.md](docs/REVIEWS.md).

## Recovery

Private preservation manifests, pre-promotion source/docs and verification
receipts are under `.recovery/perfecto-long-hair-acceptance-2026-09-09/`.
Earlier source/evidence remain under the existing hair-live-performance,
hair-angle-review, hair-arm-preview, hair-mask-comparison and hair-research
archives. Nothing in those recordings or prior archives should be deleted or
rewritten. Git is not their backup. Previous full docs were archived before
the current handoff was written.
