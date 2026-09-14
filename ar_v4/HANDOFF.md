## perfecto_17fps: CPU face landmarker is the G default — September 14, 2026

Owner-approved after the verification below: `experiments/speed-lab/experiments.ts` defaults
the face landmarker to the CPU delegate; `?face=gpu` restores the previous order. Nothing
else in the pipeline changed. Measured on the owner's laptop on mains with the real camera:
17.9 fps versus 15.5–16.4 fps, p95 age 116–117 versus 128–133 ms, no >100 ms gaps, full
tracking and masks. Landmarks move at float-noise level; accepted on live use, matched
frozen-input comparison not rerun, phone unmeasured. Committed and pushed on
`codex/ar-v4-nose-occlusion` as **perfecto_17fps**; the published Railway site is unaffected
until rebuilt. See docs/REVIEWS.md.

## `?face=cpu` verified on mains with the real camera — September 14, 2026

Owner-run GPU/CPU/GPU/CPU sessions on the local G page (mains, real 1280x720 camera,
mixed motion): CPU face delegate 17.94 / 17.86 fps versus GPU 15.49 / 16.41 fps (+12 %
median), age p95 116–117 versus 128–133 ms, zero >100 ms gaps, 100 % tracked and masked.
It changes landmarks at float-noise level, so it stays opt-in until the matched
frozen-input comparison and the owner's held-frame acceptance; see docs/REVIEWS.md.

## Server AR comparison: local G retained — September 14, 2026

The owner asked whether running the whole AR pipeline on a server beats local G. The isolated
experiment, now retired by the owner and archived intact in the ignored
`.recovery/server-ar-2026-09-14/` (see its README, DEPLOY, DECISION and RESULTS), runs the
exact published G control as mode A, the same frozen pipeline inside a headless Chromium worker
on the server as mode B, and a transport-only control as mode C, with pixel-carried frame
identity and client-clock frame age. The owner's real-camera run on mains (Arc 140T, mixed
motion, 4 sessions per mode) measured local G at 15.2 fps median with 143 ms p95 age versus
server AR at 10.3 fps with 453 ms p95 age even on a GPU server with zero network distance; the
CPU-only configuration Railway offers gave 0.3–0.6 fps with 3–10 s age. Decision: **local G**;
no Railway deployment, no GPU host, no further server tests, experiment retired (owner). G,
accepted checkpoints, parent application, deployment and `package.json` are unchanged.
Unmeasured: physical iPhone Safari (owner reports about 21 fps average for G on the phone,
no report file), internet distance, optical latency. Reports in
`.recovery/server-ar-2026-09-14/output/reports/`. See docs/REVIEWS.md.

## Latest FPS follow-up: Test 4 is flat — September 14, 2026

The new G/Test 4 report observes 12.22/12.20 FPS (−0.21%) despite composition
median improving 6.735→4.195 ms. G itself speeds up 13.9% during the run.
G remains accepted; next diagnostic priority is the existing G readback study
and hair extraction/retrieval timing, not promotion of these CPU changes.
This follows the desktop context; the new JSON has no device-type field.
See docs/REVIEWS.md and ignored
`.recovery/fps-desktop-reports-2026-09-14/test4-analysis.md`. No deployment change.

## Latest FPS evidence is desktop — September 14, 2026

The owner confirmed both submitted FPS reports are desktop, correcting an
accidental iPhone selection. Test 6 observed +3.72% pooled FPS; Test 3 observed
-3.62%, with full tracking/masks. G's own 13.5–19.0% between-window changes
prevent a firm winner. G remains accepted; physical-iPhone evidence is still
pending. See the latest docs/REVIEWS.md entry and ignored
`.recovery/fps-desktop-reports-2026-09-14/analysis.md`. No runtime/deployment change.

## Published separate FPS tests — September 14, 2026

The owner-requested iPhone/desktop FPS preview is live:
https://web-production-ef3ca.up.railway.app/ar_testing/fps/experiments/fps-candidate/live.html?fps=next-combined

Commit `28599efc23b3b81bcac971391168261ea90205a0` is pushed to `main` and
`codex/fps-railway-tests`; Railway reports success. All 37 served public files
match their manifest, including the 15 new FPS files; all 22 earlier public
files/default entry remain unchanged. Public FPS build:
`bca8a88bea5e0cb0a8279a647918748f3b4896b872cc064e0c91e4fa148f2978`.

Use Test 6, then Compare G / test / test / G. The four fresh documents use
5-second warmup and 30-second measurement windows. If needed, tap Open camera /
continue segment after a reload. Save the complete comparison JSON or copy its
text. Repeat both glasses/hair models and front/nose, down/up and both yaw checks.
G remains accepted; new physical iPhone performance and visual acceptance are
still pending. No automatic camera access was used during public verification.

The isolated implementation is in
`.recovery/fps-railway-2026-09-14/checkout/ar_v4/experiments/fps-public/`.
Both G/test capture pumps have the same proven iPhone playback-clock correction.
Strict checks, 92 focused tests and required npm test (172 unit/21 browser) pass.
Ten portrait lifecycle cases passed, followed by final-build entries, a complete
real-duration ABBA with permission retry and saved/copied JSON, and a final Test
6 lifecycle smoke. ABBA testing found and corrected internal navigation being
mistaken for backgrounding; genuine background cancellation remains. Both
390px and 1440px live HTTPS entries pass without camera/workers or overflow.

Full reviews are in that release worktree's docs/REVIEWS.md. Private deployment
receipts: experiments/fps-public/qa/output/published-28599ef.json and
published-entry-28599ef/report.json there. This original dirty checkout, its
249 recorded G dependencies, older mobile worktree, recordings and accepted
checkpoints were preserved. Root Python/deployment files did not change.

## Current owner decision: retain G - September 12, 2026

The owner tried U and reports it is worse than G. U (`hair-release`) is rejected
for promotion; G remains the accepted baseline. Record this as qualitative
feedback, with no new G/U telemetry archive or matched motion evidence provided.
The isolated U implementation remains available for diagnosis. This decision is
recorded locally; the last published commit remains cf1d96d. See docs/REVIEWS.md.

## Published G/U mobile preview - cf1d96d, September 12, 2026

The owner-authorized focused preview is live at
https://web-production-ef3ca.up.railway.app/ar_testing/experiments/efficiency-lab/live.html?study=hair-delivery
Commit `cf1d96da9f67a1ab83f45df8e3daf821aa736bb9` is pushed to `main` and
`codex/ar-mobile-testing`. Railway succeeded and all 21 public files independently
match runtime fingerprint `0b4686e03825db646f2f688e9fd3409317c4dc93ce37359315164bab34547bf6`.

U (`hair-release`) starts a next hair request during prior asynchronous hashing,
retaining original validation and a strict two-image bound through late results.
It preserves G's replacement of unprocessed pending snapshots. G stays accepted.
The new page offers G/U/U/G, 5-second/3-masked-frame warmup and 30-second windows;
measurements-only default, optional video, one local ZIP with all request timing
and publication dispositions. Successful finalization drains late results; a
20-second watchdog retains incomplete diagnostics and allows safe camera reopen.

Verification: 215 efficiency checks, required npm test172 unit/21 browser checks,
four portrait startup cases, seven focused production browser cases, and all239
pinned G dependencies byte-exact. The synthetic desktop run observed192 actual
next submissions during prior hashing across593 U requests; it showed no speed
win (U8.30/8.50 versus G9.37/9.70 updates/s, full masks). This is not phone or motion
quality evidence. Both glasses/hair held pairs and safeguards pass; new U physical
phone and matched down/up/yaw recordings remain to be reviewed. No promotion.

Implementation, current reviews and ignored test receipts are inside
`.recovery/mobile-railway-2026-09-11/checkout/ar_v4/`; public verification receipt is
`experiments/efficiency-lab/qa/output/published-cf1d96d.json` there. Do not copy this
original dirty efficiency lab over the isolated deployed sources. This original
checkout's unrelated work, private recordings and earlier archives stay preserved;
the parent application and deployment configuration were not changed.

## First physical-phone comparison - September 12, 2026

The fixed public build now completes the owner's physical iPhone run. G/Q/R/S/T
average 13.87/13.88/13.17/13.32/13.52 AR updates/s, with unequal matching-mask
availability; no winner or promotion. First priority is measuring complete
hair-result delivery, including client validation/hash and late results. This
run covers Amber/hair-only with video encoding. See the latest review and
`.recovery/mobile-comparison-2026-09-12/analysis.md`. G and deployment unchanged.


## Published iPhone capture correction - 08191d3

The real iPhone startup report reached GPU-ready but timed out before first AR publication. The efficiency-lab capture guard incorrectly rejected a snapshot when WebKit live-camera currentTime advanced. The isolated mobile checkout now uses rVFC frame counters and the same owned pixels throughout detection, hair and rendering. Accepted G rendering dependencies remain exact.

Commit 08191d394a764ecb77ff7a9e060f2514c33613f5 is pushed to main and codex/ar-mobile-testing. Railway reports success, and all 21 served files match runtime release 9df9d0b9c9bf. Required npm test passes 172 unit/21 browser checks, efficiency passes 189 checks, and all eight mobile browser regressions pass, including four 720x1280 portrait glasses/hair combinations and the reproduced advancing-clock failure. Physical-phone startup and visual behavior still need the owner retry.

Implementation and full reviews: `.recovery/mobile-railway-2026-09-11/checkout/ar_v4/`. Published receipt: `experiments/efficiency-lab/qa/output/published-08191d3.json` inside that checkout. This original dirty checkout and its efficiency sources remain separate; do not copy them over the deployed correction. Private recordings and recovery material remain excluded.

# Current AR handoff — G Combined

Latest mobile follow-up: main f7fb130 is verified live with startup stage/elapsed
reporting and **Save startup report**, available even before any AR frame.
The owner reports iPhone 17 Pro loading >1 minute; collect that phone report to
identify its actual blocking stage. Timeout/retry tests pass; the phone's root
cause remains unconfirmed. Continue implementation in the isolated mobile
worktree below; preserve this original dirty G checkout.


## Mobile testing delivery — September 11

The explicitly requested public preview is live at
https://web-production-ef3ca.up.railway.app/ar_testing/ — main commit 0815de7.
It starts G and offers Q–T plus a continuous video-and-measurements ZIP run.
Its clean implementation worktree is `.recovery/mobile-railway-2026-09-11/checkout/`
on codex/ar-mobile-testing. This original dirty G checkout was preserved; use
that worktree for mobile changes. Railway's 21 public assets match the tested
manifest. See the latest docs/REVIEWS.md entry and that worktree's
ar_v4/experiments/efficiency-lab/MOBILE.md for protocol and verification.
Physical mobile performance and visual acceptance remain unmeasured.


On September 9, 2026 the owner reported that F and G felt far superior to the
other speed experiments, then explicitly requested implementing G and committing
and pushing it. G Combined is the selected current base. Default dev/build/preview
use `vite.combined.config.ts`, opening `experiments/speed-lab/live.html` with
**Current · G Combined** selected before startup and on the first published image.

G uses the reviewed bounded two-image scheduler and inference overlap, source
RGBA reuse, fewer source copies, real PBO/fence downloads and nonzero-temple
prewarming. This promotion retains the previously checked renderer, geometry,
models, resolution, exact image/pose/mask ownership and final safeguards.
The same eight-way comparison remains available; its report IDs and historical
`baseCommit`/`candidateAccepted` fields stay stable. Additive `currentBase`,
`ownerSelectedG` and `previousBase` fields identify the selection explicitly.

Test 2 (`8baa16c`, **speed testing**) remains independently launchable with
`dev:test2`, `build:test2` and `preview:test2`, using unchanged `vite.speed.config.ts`.
Accepted long hair (`9997050`), perfect temples (`b26b558`) and original perfecto
remain available. The parent Python/Railway deployment, linked worktrees,
private recordings/recovery files and unrelated trial-model work are separate.

The existing full G study and independent audit preserve all pixels and geometry
on 56 matched generated/recorded pairs, including both glasses, both hair models,
down/up/both yaw and nose/front checks. Hardware used PBO in 32/32; software in 19/24
with five exact bounded-fence fallbacks. Color/size/alpha/cancellation checks also
passed. No rendering retuning occurs during promotion. The owner's new feedback
is qualitative; physical-camera FPS, motion-to-photon latency, sustained mobile
smoothness and thermal behavior remain unmeasured for G.

Default checks now include the lab's strict types, renderer/scheduler/temple/QA
unit checks and production G default/lifecycle suite. The preserved Test 2 browser
suite has its own launch alias. Clean staged-export verification passes strict
types/build, all 172 unit checks and all 21 production browser cases, including
the preserved reference and long-hair suites. See docs/REVIEWS.md for evidence
details and the optional historical manifest's line-ending boundary.

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
