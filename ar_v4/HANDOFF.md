# Current G readback diagnostic preview - September 14, 2026

The owner authorized creating and pushing the readback diagnostic after the last
stability ZIP was confirmed as PC evidence. Work remains in the isolated deployed
checkout below. The mobile default is `?study=readback-diagnostic`; earlier
preview links remain available.

Choose glasses, hair model and power, then Start G readback comparison. Default:
unchanged G and instrumented G, 180 seconds each in fresh documents, about seven
minutes total, one multipart `ar-g-readback-` ZIP. Reverse order and individual
options are available. Earlier stability results keep their separate database.
Follow front/nose, down/up and both yaw cues; repeat both glasses × both hair
models. This observes existing readback steps, adds no optimization and preserves
accepted G. New physical PC/iPhone results and visual acceptance remain pending.

Package fingerprint: `74555e8b1415ee1909a371b379e9775520da6f73cf3d545c9c4d38d1cb9b1ea3`.

The newest docs/REVIEWS.md entry records validation/deployment; detailed timing
scope is in `experiments/efficiency-lab/g-readback-diagnostic/TIMINGS.md`.
The PC findings below describe the preceding deployed build.

# PC G stability findings - September 14, 2026

The 05:34 ZIP is a user-confirmed Windows/Chrome/Intel Arc PC test, not iPhone.
All eight parts and 540 seconds of measurement pass raw/ownership audits on
current deployed build `e753f7b40a19`. Average completed AR/s: continuous 13.97,
restarted 14.58, fresh-page 13.86. Continuous falls 15.57 to 11.37; fresh-page
falls 15.17 to 11.67 despite reloads. Matching masks cover every tracked update.
Later worker rebuilds coincide with recovery but add about two seconds between
publication anchors, so this does not establish a responsive FPS improvement.
Preparation, native readback and face/hair retrieval grow while final composition
stays near constant. Next proposed experiment is a separate G diagnostic splitting
existing PBO extraction/wait phases; no runtime or deployment change was made for
this review. See newest docs/REVIEWS.md for exact results and private audit paths.
The existing preview below remains deployed, and G remains accepted.

# G stability preview - September 14, 2026

The owner requested the next test preview after the four phone experiments did
not establish an FPS winner and G itself declined over time. The mobile default
is now `?study=g-stability`. It measures only unchanged G: 180 uninterrupted
seconds, six 30-second windows with runtime rebuilds, and six 30-second windows
in fresh documents. Each condition starts in a fresh page; the full suite has
eight measured documents and takes about 11 minutes with video off. Existing
all-FPS and G/V links remain available. No candidate is promoted.

Completed raw parts and the next handoff commit atomically to local IndexedDB
before reload. The test locks workload/build/resolution and uses explicit
session/document ownership. Stop and unexpected refresh retain partial evidence;
a second tab cannot claim an active suite. Save one multipart ZIP after the run.
Page clocks remain separate, and continuous 30-second bins are export-only.
Compare completed AR, camera delivery, age, stalls and matching masks together;
reversed condition order helps assess drift but does not prove a thermal cause.

Package fingerprint:
`e753f7b40a198e66d5c8927b0d1fa655ec2b8e202c2851fb40a9d4c984b03ac8`.
Validation/deployment status is recorded in the newest docs/REVIEWS.md entry.
Continue only in the isolated deployed checkout specified below. Original
uncommitted work, private recordings and recovery archives remain preserved.

# Latest camera-copy phone result - September 13, 2026

The 16:19:34.843 ZIP completes the previously missing camera-copy comparison
on corrected release `3de1525f7c6d`. All 910 measured candidate frames use
upright canvas fallback: the phone supplies landscape VideoFrame dimensions
and no orientation metadata. Tracking is 100% on those frames, but the ZIP
contains no video for independent visual confirmation. Native copying was
never exercised. Balanced completed AR/s is 15.167 for fallback versus G16.467;
matching masks are 68.90% versus 55.03%. G first/final still falls 18.133 to
14.800 AR/s. No speed winner or candidate promotion is established.
See the newest docs/REVIEWS.md entry and private reproducible audit under
`.recovery/phone-fps-2026-09-13-161934/`. No runtime or deployment changes were
made for this review; continue in the isolated deployed checkout below.

# Upright camera-copy correction - September 13, 2026

The owner reports sideways camera/no glasses in the tests that did not work.
Unknown VideoFrame orientation must not be treated as zero: the camera-copy
candidate now falls back synchronously to G's upright camera snapshot before any
await when metadata is missing or transformed. Exports retain actual fallback
and dimensions/orientation; the UI explains it. Native copying is attempted only
for verified untransformed frames. G and V implementations remain unchanged.

The three new successful phone ZIPs are CPU face, render worker and compositor
reuse. None establishes an FPS win; first/final G declines by 15-23% despite fresh
runtime teardown. All use Amber/hair-only; only CPU contains video. The user did
not identify the failed option in a trace, so the capture bug is consistent with
the symptom rather than proof that V failed. See the newest docs/REVIEWS.md entry
for exact rates, matching-mask coverage, private audit paths and retest controls.
Current corrected package fingerprint:
`3de1525f7c6df92755cdc49d93f33324a13d9ee0a49f1d1d3a14b1e158b42f03`.

Continue only in the isolated deployed checkout below; original dirty files,
recordings, recovery archives, all 239 pinned G files and parent deployment
remain preserved. Physical iPhone orientation recovery and sustained FPS recovery
still require new phone evidence; no candidate is promoted.

# Fresh runtime FPS preview - September 13, 2026

Current change addresses the owner's report that switched tests run slower
than freshly opened tests. The default FPS study now fully retires and rebuilds
processing workers, renderer contexts and caches at every manual/automatic
switch, including repeated options. Camera and recording stay open. Setup has
its own 90-second budget, followed by the existing five-second/three-mask warmup
and full 30-second measurement. All six options remain in one preview/ZIP.
Runtime generation and isolation are exported. The live counter resets at every
pump boundary; the real page-refresh control preserves the selected workload.
Explicit `&switch=shared` retains the prior resource-sharing diagnostic only.
See the newest REVIEWS.md entry for evidence and validation limits.
Current package fingerprint:
`47ba023a9cc05a27a835031599ab6fca9d1765f7e5b7db7155c98c2d968f37f1`.

Continue in the isolated deployed checkout named below. G's pinned dependencies,
the original dirty checkout, recordings, recovery archives and parent deployment
remain untouched. This is a test-isolation correction, not a candidate promotion
or a measured iPhone speedup.

# FPS review candidates — September 13, 2026

Entry-point correction: the normal Railway `/ar_testing/` link and query-free
mobile page now open all six FPS options. The previous package was deployed,
but the AR client still mapped the Python landing redirect to the older G/V
study. Only AR entry mapping/navigation changed; Python routes remain unchanged.
Every preview now links to **All FPS experiments** and the preserved G/V study.
Entry-corrected package fingerprint:
`11991d87efe8c485a431ee32980d42a4b2cdd6934d5d7af39407acfd4452eca1`.

The owner requested a ready-to-test, pushed preview based on the attached FPS
review. New entry: `/ar_testing/experiments/efficiency-lab/live.html?study=fps-review`.
The owner's follow-up requests all experiments in one preview. This entry now
selects G first and exposes all six options in one live session. Its automatic
run tests G, CPU, worker, camera copy, compositor and V, then reverses that order:
12 unchanged warmup/measurement windows, about seven minutes, one local ZIP.
Explicit `&candidate=...` links retain the shorter G / option / option / G runs.
Options: forced CPU face, OffscreenCanvas render worker, exact VideoFrame camera
copy with explicit fallback, and compositor output reuse/restricted detailed work.
V remains a reference experiment. G remains accepted and U remains rejected for
promotion. Older masks, lower resolution and altered geometry are not introduced.

Continue only in
`C:/Users/Shay/PycharmProjects/lenses/ar_v4/.recovery/mobile-railway-2026-09-11/checkout/ar_v4`.
The original checkout and private recordings/recovery archives remain unchanged.
See [assessment and test protocol](docs/FPS_REVIEW_2026-09-13.md),
[mobile guide](experiments/efficiency-lab/MOBILE.md) and current REVIEWS.md findings
for mechanisms, measured versus hypothetical claims and validation limits.

The preceding all-options package fingerprint is
`c235caea31a9582cc32e97e200365abade322146ad908f05b3682f6b1c388fe1`.
The preceding focused FPS-review package fingerprint is
`71666ebb3f29f36e1031b8a0ee871b374738e0cddfe5492c68e9c05f89cd6f0b`.
Older fingerprints and phone observations below identify preceding packages.

## Latest phone evidence - September 13, 2026

Two video-on, hair-only ZIPs match the current G/V preview. Amber completes:
G 13.567 versus V 14.100 AR updates/s; matching masks on 64.61% versus 100%
of tracked frames. Tom Ford is partial: final G warmup publishes 207 tracked
frames but gets only two matching masks in time, so it cannot provide a balanced
G/V throughput comparison. All hair requests complete and drain. Both videos
were sampled; unequal poses and missing selfie-multiclass evidence prevent a
general quality or tracking conclusion. G remains accepted, V a candidate.
See the latest docs/REVIEWS.md and original-checkout private evidence under
`.recovery/mobile-comparison-2026-09-13/`. Reviewing those ZIPs did not change the runtime.

## Preserved G / V preview - September 12, 2026

The owner authorized the focused preview and push after the phone comparison.
Current entry: `/ar_testing/experiments/efficiency-lab/live.html?study=mask-preview`.
This explicit G/V entry keeps G selected. Its G/V/V/G run takes
about 2.5 minutes with unchanged warmup and measurement windows, measurements
on and optional video off. Both glasses/hair choices and movement cues remain.
Old experiment navigation is removed; explicit historical URLs and their source,
automated checks, recordings, recovery archives and accepted entry points remain.
Focused Hold exports only G/V on the same SDK diagnostic mask; live V extraction
still needs the separate path/byte checks and visual review. See MOBILE.md.
The focused runtime fingerprint is
`8acef53052f9a2d9ed3869f5a7a133dfaf7e71fa665754902e33577c996d16f8`.

## Preceding testing ground: G / V / W / X

Latest owner phone upload is measurement-only, Amber/hair-only: V delivered
16.17 AR updates/s with 100% matching masks versus G 15.58/s with 70.59%; W/X
each delivered 14.65/s. V halved category extraction cost but has an initial
activation gap and no new visual acceptance. G remains accepted/default.
See [REVIEWS.md](docs/REVIEWS.md) for time drift, startup, other-model/video
limitations and the byte-verified private archive location.

The owner authorized creating and pushing the three per-image cost experiments
for iPhone testing. Open
`https://web-production-ef3ca.up.railway.app/ar_testing/experiments/efficiency-lab/live.html?study=per-image`.
G remains selected initially and accepted. V changes callback-local hair-mask
retrieval to full-size RGBA8 with exact SDK byte conversion and explicit fallback;
W reuses four queried PACK values only within one exclusively owned native
image; X uses exact word equality in the initial full-image composition scan.
Scheduling, image/detection/pose/mask pairing, model/render resolution, geometry
and final nose/front guards remain. These are independent experiments, not a
combined candidate or a speed/quality promotion. U remains rejected as worse than G.

Manual switching and a G/V/W/X/X/W/V/G measurement-only run are available.
Every window retains the existing five-second/three-masked-frame warmup and
30-second measurement. Optional video is separate. Repeat both glasses and
both hair models, with front/nose, down/up/both yaw and hair-continuity review.
The all-request scalar hair ledger includes late costs and actual fallback,
alongside completed updates, camera delivery, mask/tracking coverage and age/stalls.
See [MOBILE.md](experiments/efficiency-lab/MOBILE.md) and [REVIEWS.md](docs/REVIEWS.md).

Continue from the isolated deployed source at
`C:/Users/Shay/PycharmProjects/lenses/ar_v4/.recovery/mobile-railway-2026-09-11/checkout/ar_v4`
on `codex/ar-mobile-testing`. Build and commit the explicit `mobile-site/` package
there. The original checkout has unrelated uncommitted work: do not replace or
clean it, its recordings, recovery archives or linked worktrees. The existing
Python route serves the package; no parent application changes are needed.
The preceding G/V/W/X runtime fingerprint is
`69d218964435964b7cef4a44f622bca0f86ab0ea2b42953c0b284c191cdcdcc5`.
Current validation and its physical-phone limits are recorded in the review.

## Current owner decision: retain G - September 12, 2026

The owner tried U and reports it is worse than G. U (`hair-release`) is rejected
for promotion; G remains the accepted baseline. Record this as qualitative
feedback, with no new G/U telemetry archive or matched motion evidence provided.
The isolated U implementation remains available for diagnosis. This decision is
preserved here; cf1d96d was the preceding published U preview. See docs/REVIEWS.md.

# Latest efficiency preview — September 12, 2026

The completed iPhone recording confirms the startup fix and finds no Q–T winner.
G remains accepted. The owner authorized U (`hair-release`), a separate earlier
hair-worker admission experiment with the original G renderer, exact pairing and
a strict two-image lifetime bound including late results. Its focused preview is
`/ar_testing/experiments/efficiency-lab/live.html?study=hair-delivery`.

Four G/U/U/G windows take about 2.5 minutes. Measurements are on and video off by
default; the owner can enable video for a separate visual check. The export now
includes all hair-request timing and publication disposition, including late
results. See [MOBILE.md](experiments/efficiency-lab/MOBILE.md) and the latest
[review](docs/REVIEWS.md) for protocol, evidence and missing physical-U validation.
G, Q–T, prior checkpoints and the parent application/deployment remain intact.

# Current AR handoff — G Combined

## Authorized mobile comparison — September 11, 2026

Latest phone follow-up: the owner's iPhone 17 Pro startup report
`ar-startup-2026-09-11T14-11-15.304Z.json` locates the failure at first AR publication.
Camera settings are 720×1280 at 30 fps; both renderers and GPU face/hair workers
finish setup within about 1.6 seconds after camera readiness. No AR image appears
before the existing 30-second deadline. The setting is not measured camera FPS.

The lab capture guard incorrectly required identical playback-clock readings
before and after a synchronous snapshot. Current upstream WebKit's live-stream
clock advances on each read, explaining how valid images can all be discarded
while camera callbacks keep their watchdog alive. The lab correction uses rVFC
`presentedFrames` for duplicate suppression and its `mediaTime` for frame-timestamp
telemetry; all processing still uses one owned pixel snapshot. The rAF fallback
uses one playback-clock sample with explicitly limited frame-delivery evidence.
New allowlisted startup counters freeze before timeout cleanup to distinguish
capture rejection, bitmap/inference waits and rendering if failure persists.

The accepted G dependencies, rendering resolution and pairing/safeguards remain.
The upstream source is not a revision identification of the owner's Safari build;
the cause still needs confirmation by a physical-phone retry. The reproduced
failure now passes all four portrait glasses/hair cases with real workers and
exact held comparisons. Required npm test passes 172 unit/21 browser checks;
efficiency checks pass 189 and the mobile regression passes eight browser cases.
The public runtime fingerprint starts `9df9d0b9c9bf`. See the latest
[review entry](docs/REVIEWS.md) for evidence and source links.

The owner explicitly requested an **ar_testing** landing-page option in the live
Lenses application and a continuous mobile comparison with video + measurements
in one file. `/ar_testing/` serves only the generated `mobile-site/` allowlist
through a small Python route; `Procfile` and `UI/app.py` retain their previous
contents. This is the authorized exception to the earlier no-public-AR scope.
All implementation, package assets and AR checks stay under `ar_v4/`, with only
the route, landing link and Python route tests in `UI/`.

The page starts G, with the independent Q/R/S/T experiments from
`experiments/efficiency-lab/`. The run uses the current five options in forward
and reverse order, equal warmup/measurement windows and fixed glasses/hair/source
dimensions. It saves one local ZIP with displayed-mirror video and scalar
measurements; manual Save/Share remain available. See
[MOBILE.md](experiments/efficiency-lab/MOBILE.md) for protocol, build and limits.

The source is built from an isolated main-based checkout with committed G plus
the reviewed efficiency sources. The original dirty G checkout, unrelated
model-studio work, private recordings and recovery material remain separate.
G renderer/reference bytes are unchanged. MediaPipe network guard and enforcing
CSP apply only to this public package. No candidate is selected by synthetic
tests; physical-phone camera, motion, codec, memory and thermal results are still
needed. Current verification and the earlier R/S matched evidence are summarized
in [docs/REVIEWS.md](docs/REVIEWS.md).

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
