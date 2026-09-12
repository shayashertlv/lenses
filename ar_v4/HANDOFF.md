## Current testing ground: G / V / W / X - September 12, 2026

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
The candidate runtime fingerprint is
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
