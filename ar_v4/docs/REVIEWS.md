# Separate FPS candidates for Railway — September 14, 2026

The owner requested publishing the completed local FPS tests for iPhone and
desktop review. The additional entry is
`/ar_testing/fps/experiments/fps-candidate/live.html?fps=next-combined`.
Test 4 reduces CPU composition work; Test 5 reduces repeated statistics work;
Test 6 combines them. G remains accepted and the existing readback-diagnostic
entry is unchanged. Source and package instructions are in
`experiments/fps-public/README.md`.

The isolated source snapshot preserves 248 of the 249 recorded G dependencies
exactly. Its only baseline change is the same proven iPhone capture correction
also applied to the candidate: admit by rVFC frame counter, retain zero/nullable
timestamps, sample RAF playback time once, and keep synchronous owned pixels.
The original checkout's 249 dependencies are unchanged. The pinned 45-file
perfect-temples reference still verifies. No render/pose tuning was added for
publication; full frame audits, final nose/front/background safeguards, exact
image/detection/pose/mask pairing and the two-image bound remain active.

Full ABBA JSON has a persistent save link and selectable text, build identity,
partial results and rejection of mixed-build resumes. Fresh-page camera setup
can be resumed with a visible button. Waiting for permission does not count as
warmup or measurement. Session storage retains this comparison in its tab;
it is not a backup for reports after the tab is closed.

Build `bca8a88bea5e0cb0a8279a647918748f3b4896b872cc064e0c91e4fa148f2978`
adds 15 generated public files and preserves all 22 existing public files and
manifest entries exactly. Both main and worker SDK bundles retain same-origin
fetch protection. No root Python, deployment configuration or shared model
assets changed; no private recordings, QA files or source maps are published.

Validation: strict public/candidate TypeScript and 92 focused checks pass.
Required `npm test` passes 172 unit and 21 browser checks. Python route tests
pass 15/15 on a second invocation; the first invocation had one transient
existing manifest-mtime test failure on Windows, with no Python edits.
Production checks pass both 390px/1440px entries and all ten portrait lifecycle
configurations (G and Test 6 both glasses/hair models, Test 4/5 singles). Full
ABBA exposed and corrected a navigation visibility event cancelling its own
handoff; genuine backgrounding still stops and preserves partial data. Final
full-duration ABBA, both entry sizes and a final Test 6 lifecycle smoke pass on
`bca8a88bea5e`. ABBA retained four fresh documents/sessions, recovered a denied
automatic camera start, completed the real 5/30-second windows and saved/copied
the identical complete JSON. The persistent save link stayed usable after
download was prevented. No browser errors or unscoped requests occurred.
Receipts: `mobile-2026-09-14T09-35-35.950Z`,
`mobile-2026-09-14T09-35-43.989Z` and `mobile-2026-09-14T09-38-43.537Z` under
the ignored output directory. The first ten-case receipt retains the separately
fixed navigation failure; it is not described as a whole-run pass.

Prior balanced desktop still inference observed Test 6 at 10.20 versus G at
9.28 completed AR images/s, with full tracking/mask coverage. That preliminary
run used a different fingerprint and is not physical-phone or wearer-motion
evidence. Existing exact matched checks covered both glasses/hair models,
down/up, both yaw directions and nose/front protections. The new public build
still requires physical iPhone/desktop motion testing and owner visual review;
no candidate is promoted. Private verification receipts remain in ignored
`experiments/fps-public/qa/output/`.

# G readback diagnostic preview - September 14, 2026

The owner requested this preview after confirming the G stability ZIP was a PC
test. It is a separate observer comparison, not a speed optimization or a
replacement for accepted G. The mobile entry is `?study=readback-diagnostic`;
explicit G stability, all-FPS, G/V and legacy links remain accessible.

Unchanged G and instrumented G each run for one uninterrupted 180-second
measurement in a fresh document. Six 30-second bins are computed only at export.
Both use the same five-second / three-matching-mask warmup, video off, fixed
camera resolution/glasses/hair/power, and two renderer instances. Forward/reverse
order and individual options are available. A separate database, page lock and
handoff preserve earlier stability results. Each raw part and its next ownership
token commit atomically before real reload. One `ar-g-readback-` ZIP retains the
suite and independent document reports; page clocks are never concatenated.

The isolated `g-readback-diagnostic/` copies G's three-renderer import chain plus
PBO helper. Renderer copies match after import rebasing. The observer adds scalar
timers to existing state queries/setup/restoration, read submission, fence/poll
calls and zero-timeout waits, CPU retrieval, allocation, row flip and ImageData.
No added GL calls, readbacks, workers, yields, pooling or admission changes.
Exact image/detection/pose/mask ownership, geometry, resolution and final
nose/front safeguards stay fixed; normal G still imports accepted sources.
See [TIMINGS.md](../experiments/efficiency-lab/g-readback-diagnostic/TIMINGS.md)
for fields, partial results and overlapping timing scopes.

Durations are caller-visible elapsed time, not isolated GPU execution. Compare
control/observer throughput to assess observer overhead and within-run trends to
localize drift. Keep camera delivery, completed AR, frame age, internal/endpoint
stalls, matching-mask availability and startup separate. Do not add medians,
subtract synthetic timer cost, infer thermal cause or call this a speedup.
Repeat reversed order after cooling, both glasses × both hair models and
front/nose, down/up and both yaw cues. New physical PC/iPhone wearer motion and
mobile performance remain unmeasured until owner testing; fixture parity cannot
establish visual acceptance. G remains accepted.

Validation: strict efficiency TypeScript and 349 CPU tests passed, including
19 new copy-parity / differential GL / cancellation / timer tests. Four real
IndexedDB tests passed. Final release
`74555e8b1415ee1909a371b379e9775520da6f73cf3d545c9c4d38d1cb9b1ea3`
contains 251 fingerprinted sources and the 22-file public package. Three final
production-browser model/stop/refresh checks passed in 1.4 minutes.

The full production-clock run completed both 180-second parts, automatic reload,
12 analysis bins and durable ZIP export on the same renderer before a label-only
encoding correction (earlier fingerprint `8c0e107a9bbe`). Raw and saved-resource
audits passed: 959 G / 1492 diagnostic measured frames, no invalid/rejected or
truncated frame rows, every measured tracked frame masked, separate owned pages,
constant history length, and both camera/worker/GL lifecycles fully closed.
The loaded synthetic desktop camera is not an FPS comparison. Four G and one
diagnostic fence fallbacks across admitted rows remain in the export. The original
Playwright job was stopped during excessive per-row assertion tracing after the
measurement succeeded; it is NOT recorded as a whole-suite pass. That QA overhead
was removed with synchronous assertions, and the independent ZIP/resource audits
plus final-build lifecycle runs provide the retained evidence. Private receipts:
`.recovery/readback-audit-first.json`, `.recovery/readback-resources-first.json`,
and `test-results/mobile-2026-09-14T05-59-48.091Z` beneath this experiment.

Matched replay passed all 56 cases, all 16 control/lifecycle groups, and exact
pixels, geometry, pairing, nose/front/background protections. Generated native
GPU: 32/32 async on each implementation with no fallback. Recorded SwiftShader:
20 async and four explicitly bounded fence fallbacks on each implementation.
All 239 accepted G files and 302 frozen private files remain byte-exact. Report:
`experiments/efficiency-lab/qa/output/readback-matched-2026-09-14T06-14-22.855Z/report.json`.
The original checkout still matches 460/461 pre-existing snapshot files; its sole
previously documented docs/REVIEWS.md difference is preserved unchanged.

Required `npm test` passed: 172 unit tests and all 21 production/reference browser
checks. Four final mobile entry/refresh/runtime regressions passed on release
`74555e8b1415`, including the default diagnostic entry and retained older links.
Deployment target is the existing Railway `/ar_testing/` static package; both
`main` and `codex/ar-mobile-testing` receive this isolated AR commit. The handoff
requires matching the served fingerprint and all public file hashes, plus a real
browser visit through the landing link and every retained study. Local ignored
deployment receipts are `qa/output/readback-published.json` and
`qa/output/readback-entry-published.json` beneath the efficiency lab.

## PC G stability result - September 14, 2026, 05:34 export

The owner confirmed this was tested on the PC. All eight parts identify Windows
Chrome 152, Intel Arc 140T/D3D11, Amber Horizon, hair-only, hair on, 1280x720,
video off and unrecorded power state. This is separate from the four earlier
720x1280 iPhone/Safari exports. It uses deployed build `e753f7b40a19` and completes
all 13 real windows: 540,000 measured milliseconds, eight unique documents,
sessions and clock origins, one continuous generation, six restart generations
and one generation per fresh page. Suite wall time is 636.049 seconds.

Independent raw audits reproduce all counts, camera rates, age/gap quantiles and
coverage. Rates below use raw eligible counts divided by each full 180 seconds;
ages pool eligible frames and camera rates pool only within-window observations.

| Condition | Completed AR/s | Observed camera FPS | Age p95 | Maximum measured gap |
| --- | ---: | ---: | ---: | ---: |
| Continuous G | 13.9667 | 20.2836 | 189.94 ms | 147.38 ms |
| Restarted G | 14.5778 | 19.9368 | 163.86 ms | 157.03 ms |
| Fresh-page G | 13.8556 | 20.0576 | 182.87 ms | 189.60 ms |

Successive 30-second rates are continuous 15.567 / 15.633 / 15.667 / 14.267 /
11.000 / 11.367; restarted 11.733 / 15.067 / 14.900 / 15.433 / 15.167 / 15.167;
fresh pages 15.167 / 15.600 / 15.067 / 14.067 / 11.567 / 11.667. Continuous drops
26.98% first-to-last; fresh pages drop 23.08% despite reloads. The first restarted
window also remains slow after its initial reload. Later worker rebuilds coincide
with recovery to about 15.15 AR/s, but one fixed forward order cannot establish
causation or a reliable remedy. Its aggregate measured rate is only 4.38% above
continuous G, before considering reset interruptions. No winner is promoted.

Runtime rebuild publication anchors are 1.850-2.073 seconds apart; internal
page transitions are 2.527-2.990 seconds apart. These are not display-scanout or
video-verified blank durations. The larger excluded measurement gaps include
five seconds of warmup with live output and must not be called freezes. Every
real window earns exactly 5,000 ms warmup and 65-81 tracked matching-mask frames.
Each document has one camera attempt; camera request to first publication/mask
is 2.241-3.551 seconds. The initial 29-56 ms switch acknowledgements omit that
startup; five actual rebuilds take 1.324-1.469 seconds before warmup.

Matching masks are present on every tracked measured update: 2514/2514 in
continuous, 2624/2624 restarted and 2493/2493 fresh-page. Fresh-page has one
additional untracked update. No actual measurement-window gap exceeds 200 ms.
Nine continuous-bin boundary-crossing frames remain in the 180-second summary
but are excluded from the six bins; bin endpoint gaps can therefore exceed
actual full-window gaps. Nine nonmonotonic camera observations are explicitly
rejected, with no frame rejects/truncation. Camera settings request 30 FPS;
observed video delivery is about 20 FPS and is distinct from completed AR/s.

All 8,665 hair requests complete, all eight drains finish, and all measured
publications join the matching generation/sequence ledger with exact capture and
publication times. Six shutdown/rebuild-boundary completions have no publication
and are never reused. Maximum owned images remains two, in-flight inference one;
native asynchronous readback has no fallback. These scalar checks do not prove
physical resource reclamation or GPU parallel execution.

The strongest timing change is in preparation/readback and face/hair retrieval.
Continuous bin 3 to bin 5 median milliseconds: preparation 46.66 -> 69.66;
PBO wait 18.92 -> 33.85; PBO extraction 21.35 -> 33.11; face inference call
22.76 -> 36.25; hair category retrieval 23.95 -> 36.62; source draw 7.05 -> 14.98.
Composition only changes 5.46 -> 5.69 and final checks 2.62 -> 2.76. Fresh-page
windows 3 to 5 show a similar pattern. Stage medians overlap and cannot be added
or treated as isolated causes. The trace does not identify heat, power policy,
other GPU work or a driver/browser condition as the cause.

G selects the preserved speed-lab renderer through
`experiments/efficiency-lab/comparison-renderer.ts`. Its PBO implementation is
`experiments/speed-lab/native/pbo-readback.ts`: wait includes fence/flush/checks
and zero-delay timer polling; extraction includes GL state queries, buffer
retrieval, allocations, error checks, row flipping/ImageData and restoration.
Hair SDK retrieval can likewise include deferred GPU work and conversion.
Next focused experiment: a separate G diagnostic candidate timing these existing
PBO phases and poll scheduling, without extra GL queries/readbacks, changed
cadence/resolution, older masks or weaker ownership/nose/front checks. Repeat
sustained early/late intervals and reverse condition order; PC and iPhone remain
separate populations. This is a diagnostic proposal, not an implemented change.
Full visual coverage still requires both glasses and both hair models, front/nose,
down/up and both yaw directions. This ZIP has no video and only one model pair.

Original ZIP and private copy SHA-256:
`b672e235749eb41abfb9405458fa22269b40c3659124f9b609ff52c7544a52e7`.
Reproducible raw/rate audits, source manifest and stage intervals are under
`.recovery/phone-g-stability-2026-09-14-053411/` (the data is the confirmed PC run).
Only documentation and private analysis artifacts changed for this review;
runtime, deployment, original checkout, recordings and recovery archives remain
preserved. G remains accepted.

## G stability controls - September 14, 2026

The latest camera-copy phone result still falls back to G's upright canvas path,
and first/final G declined in all four recent phone comparisons. The owner asked
for the proposed stability preview. This change adds test orchestration around
accepted G, without changing G's rendering or scheduling implementation.

The default mobile study is `g-stability`; explicit FPS/GV studies remain.
Continuous G measures one uninterrupted 180-second window. Restarted G measures
six 30-second windows in one document, rebuilding workers/graphics resources
between windows while retaining the camera. Fresh-page G measures six separate
30-second documents. Each condition begins in a fresh page. All real windows
retain five seconds of warmup and three tracked images with matching masks;
startup, warmup, persistence and reloads are excluded from measured throughput.
The suite plans 540,000 measured milliseconds in eight documents, about 11 minutes
including setup. Video is off and the selected glasses/hair/power/resolution stay
fixed. Forward and reverse condition orders are available.

Continuous 30-second bins are calculated only at export; they create no pump,
worker or cache boundaries. Full-window and bin rates include their complete
clock durations and endpoint stalls. A bin requires capture and publication
within that interval; boundary-crossing frames remain in full-window raw data.
Partial bins use elapsed duration. Camera FPS, completed AR updates, tracking,
matching-mask availability and source age remain distinct. No automatic winner
or thermal diagnosis is inferred from reloads or reversed order.

Only scalar reports are persisted. IndexedDB atomically saves each raw report
and the next document token before navigation. Web Locks and one-use handoffs
prevent duplicate-tab ownership; interrupted claims become explicitly partial.
Each handoff replaces the current history URL and uses `location.reload()`;
it does not accumulate standard-navigation history entries. Current
[WebKit back/forward-cache code](https://raw.githubusercontent.com/WebKit/WebKit/main/Source/WebCore/history/BackForwardCache.cpp)
excludes reloads while allowing standard navigation, so this better matches the
owner's refresh control and avoids relying on cleanup alone to retire a cached
page heap. It does not establish GPU-process memory reclamation or cooling on
the particular iPhone. Storage failures retain the unsaved report for
retry/export, and navigation failures expose continuation after a successful save. Stop during pending
creation cannot later auto-start, hidden documents cannot start camera work, and
BFCache restoration reloads the document. Completed parts stay available until
explicit deletion. A ZIP contains `telemetry.json` plus independently owned raw
part JSON files; page-relative clocks must never be concatenated. Export bundles
Blob parts without parsing all earlier raw reports during measurement.

Validation on final fingerprint `e753f7b40a19`: strict mobile build and all 323
efficiency tests pass. All 10 production browser tests pass, including one
unshortened all-condition run (540,000 measured milliseconds in eight distinct
documents/sessions/clock origins), all four glasses/hair combinations, runtime
resource teardown, six lifecycle/storage/duplicate-tab failure cases, actual
reload navigation and constant history length. The continuous report retains one
runtime generation and six export-only bins; restarted G has six measured runtime
generations. The other three model combinations use bounded live checks with
explicit partial exports. All use static synthetic portrait pixels with real
workers/GPU and production timing, not a physical iPhone or wearer motion.

The independently audited full ZIP has SHA-256
`fa6f7b58233e30ef476702fe3ac77b5abdca5cc5fa0073aca150a2f087d0850a`.
Its raw rows reproduce AR/camera rates, frame ages, endpoint gaps, tracking and
matching-mask counts. Screenshots show upright output and visible glasses for
all four combinations. Three additional real-IndexedDB checks cover atomic abort,
competing claims and reload recovery. Private receipts, ZIPs and independent
Python audits are under `.recovery/g-stability-2026-09-14/browser-validation/`.
Required `npm test` passes: 172 unit tests and 21 browser tests, including
both current G and preserved reference/hair-worker lifecycles. Four additional
mobile regressions pass for the default entry, retained FPS/GV links, selected
refresh behavior and cancelled CPU-worker setup. Public verification receipts
are written to `qa/output/g-stability-published-2026-09-14.json` and
`qa/output/g-stability-entry-published-2026-09-14.json` by the deployment checks
(the paths are relative to `experiments/efficiency-lab/` and stay out of Git).

All 239 pinned G files match exact Git blobs at
`b9142b2a3b957445f378d8012eea7e27ca68fd0b`. The historical original-source manifest
matches 460 files; its sole later difference is unrelated original Zuri review
text in `docs/REVIEWS.md`, last written September 13 and left untouched. All four
recent downloaded phone ZIPs and their private archive copies retain recorded
hashes. No root Python routes/deployment files or original checkout source were
edited. The 22-file mobile package has fingerprint
`e753f7b40a198e66d5c8927b0d1fa655ec2b8e202c2851fb40a9d4c984b03ac8`.

Physical-iPhone performance, camera permission behavior after reload, and visual
acceptance remain empirical. Evaluation must cover Amber Horizon and Tom Ford
Clear with both hair-only and selfie-multiclass, front/nose, down/up and both yaw
directions. Static synthetic browser output does not establish wearer motion or
phone speed. G remains accepted; no optimization is promoted.

## Camera-copy phone retest - September 13, 2026, 16:19 export

The owner's newly supplied `ar-mobile-comparison-2026-09-13T16-19-34.843Z.zip`
is the previously missing camera-copy test. It matches corrected release
`3de1525f7c6d`, completes G / camera copy / camera copy / G with four full
30-second windows, and uses fresh runtime generations 4/5/6/7. This is
Amber Horizon, hair-only, hair on, 720x1280, with video disabled.

All 910 measured camera-copy frames report `canvas-video-fallback` and
`orientation-metadata-unavailable`; none uses native RGBA copying. The phone
reports an upright 720x1280 video element but 1280x720 VideoFrame coded, visible
and display dimensions, with absent rotation/flip metadata. The guard therefore
actually activates on the target phone. Face tracking completes on all 910
candidate frames. These scalars demonstrate a completed tracked fallback run;
without video they do not independently establish displayed orientation,
visible glasses, dynamic visual quality, or nose/front and hair-edge acceptance.

| Measured result | G | Camera copy using fallback |
| --- | ---: | ---: |
| Completed AR updates/s, both windows | 16.467 | 15.167 |
| Tracking coverage | 99.70% (985/988) | 100% (910/910) |
| Matching masks among tracked frames | 55.03% (542/985) | 68.90% (627/910) |
| Capture-to-publication age p95 | 155.78 ms | 161.90 ms |

Candidate throughput is 7.89% lower in this run, with higher matching-mask
availability and different work at publication. G still declines from 18.133 to
14.800 AR/s (18.38%); the two candidate windows are 15.300 and 15.033. Time/order
and mask availability prevent assigning that entire difference to capture.
This is not a direct-copy optimization measurement, and there is no promotion.
Camera delivery remains 29.05-29.62 FPS. Maximum measured endpoint gap is
143.98 ms, with none above 200 ms. Frame-age p95 by window is 140.14, 158.36,
169.02 and 168.36 ms. The three untracked measured frames all occur in first G;
there is no video to explain their cause. One invalid camera observation during
the first candidate measurement is explicitly rejected; retained observations
remain monotonic, and camera delivery is separate from completed AR throughput.

Runtime setup takes 1.85-2.04 seconds, followed by the unchanged five-second
warmup; its matching-mask counts are 26/25/34/18. Initial page camera-to-first
publication is 4.42 seconds and to first matching mask 4.84 seconds, outside the
comparison windows. Scalar session/sequence/hair-publication audits pass for all
eligible rows; all 2,246 hair requests complete and drain. No rows are truncated.
The native-frame probe plus canvas fallback reports 7.94 ms median total capture
cost, but overlapping stages and different mask work forbid summing stage medians
or treating them as isolated causal costs.

The original ZIP's SHA-256 is
`2589e4f25f89bb4d707cc9f77658c3c48c0612940d482fe64f7ebd7dcee3cf07`.
Its byte-identical private copy, source manifest and reproducible independent
raw audit are under `.recovery/phone-fps-2026-09-13-161934/`. Runtime code and
deployment are unchanged by this review. The earlier three working FPS options
plus this fallback test are now reviewed; this file is not a V or all-options
run. Full phone visual coverage still requires both glasses and both hair models,
front/nose, down/up and both yaw directions. G remains accepted.

## Phone orientation failure and completed FPS tests - September 13, 2026

The owner reports that the other tests showed a camera image rotated 90 degrees
and no glasses. The three supplied ZIPs contain only successful CPU-face,
render-worker and compositor comparisons; none contains the failed state. The
camera-copy implementation had a concrete orientation defect consistent with
that symptom: absent `VideoFrame.rotation` was treated as zero, and transform
fallback drew the same raw VideoFrame. This is a code finding, not a trace proving
which phone option failed. V does not use this capture helper.

Camera copying now requires explicit valid rotation/flip metadata, zero rotation,
no flip and matching visible/display dimensions. Unknown orientation or a known
transform uses G's upright HTMLVideoElement snapshot synchronously in the
original admitted camera callback, before any await. The unused VideoFrame closes
once. An asynchronous copy failure may use only the already verified untransformed
frozen frame; it never rereads live video. Resolution, source/detection/pose/mask
ownership and downstream rendering remain unchanged. The UI identifies upright
fallback, and exports include actual capture path, reason and source/frame
orientation/dimensions. Fallback is not evidence of a native-copy speedup.
See [capture contract and primary implementation references](../experiments/efficiency-lab/review-options/README.md).

Independent CRC/raw-clock/pairing audits pass for all three 47ba023a9cc0 packages:
four complete 30-second windows each, fresh runtime generations 2/3/4/5, no row
truncation, all hair requests completed and drained. Within each ZIP, balanced
G / option / option / G aggregates are:

| Option | Recording | Completed AR/s, option / G | Change | Matching masks, option / G |
| --- | --- | ---: | ---: | ---: |
| CPU face | Actual video on | 15.817 / 16.267 | -2.77% | 75.55% / 57.17% |
| Render worker | Off | 14.700 / 15.917 | -7.64% | 100% / 64.50% |
| Compositor reuse | Off | 14.150 / 14.450 | -2.08% | 40.87% / 64.59% |

There is no measured FPS winner. Fresh runtime teardown did not remove drift:
first/final G rates are 18.367/14.167, 17.600/14.233 and 15.633/13.267 AR/s,
declines of 22.9%, 19.1% and 15.1%. Camera delivery remains about 28.48-29.99 FPS,
tracking is 100%, and no measured completion gap exceeds 200 ms. First/final G
frame-age p95 increases from 146 to 174, 156 to 169 and 167 to 193 ms. Runtime
setup is excluded and separately reported; these declines are not startup FPS.
Thermal load, browser-wide state and pose differences remain hypotheses, not
established causes. Runtime rebuilding does not establish fresh-page equivalence.

The worker backend actually ran on all 882 measured candidate frames, with 100%
matching masks but more total render cost (prepare RPC median about 44 ms,
complete RPC about 14-17 ms). Compositor reuse actually ran on its 347 masked
candidate frames; 502 other candidate frames lacked a matching mask at publication.
Its lower aggregate composition timing therefore does not represent equivalent
work. Full-resolution residual scanning, initial copying and final safeguards
remain. CPU delegate use is verified. Do not pool these absolute rates across
ZIPs: only the CPU run includes an actual 23,032,735-byte video.

All uploads are Amber Horizon, hair-only, hair on, 720x1280. Twenty CPU-video
samples show an upright view and visible glasses at front, down, up and both yaw
directions across the four windows. Motion amplitudes are unequal and samples do
not establish temporal quality. There is no Tom Ford or selfie-multiclass phone
video here, and no worker/compositor video. Higher mask coverage alone is not
visual acceptance. No candidate is promoted; G remains accepted.

Private byte-identical ZIP copies, source SHA-256 manifest, reproducible independent
audit and sampled-video review remain under
`.recovery/phone-fps-2026-09-13-new/`. Original recordings are unchanged and excluded
from Git and the mobile package.

Strict TypeScript and all 305 efficiency checks pass. Five production browser
orientation cases pass without retry on release `3de1525f7c6d`: missing rotation/
flip across both glasses and both hair models, plus explicit 90-degree rotation
and swapped storage dimensions. Each follows G / camera copy / V / G using one
camera and real GPU workers. All keep upright 720x1280 output, tracked matching
masks, exact held input/detection/pose/mask and matching geometry/output pixels,
with unchanged nose/front/background guards. The deliberately sideways copy path
is never invoked; all 234 VideoFrames, 50 workers and five camera streams close.
Amber and Tom Ford screenshots show upright faces and glasses. These static
synthetic checks do not establish phone throughput or down/up/yaw motion quality.
Receipts are under `experiments/efficiency-lab/test-results/mobile-2026-09-13T15-53-28.448Z/`.

Three real Chromium capture checks also pass: native portrait RGBA copy remains
active with exact downstream canvas bytes, asynchronous fallback retains the
frozen image after camera advancement, and cancellation closes before writing.
Logs are `.recovery/phone-fps-2026-09-13-new/orientation-browser.log`,
`native-capture.log` and `efficiency-tests.log`. Physical iPhone orientation
recovery still requires a new run; the failed state was not present in the ZIPs.

The required `npm test` completed 172 unit checks and 20 browser cases, then
failed the unchanged long-hair reference's selfie-multiclass resume check: no
matching category mask was observed during its 55-second polling deadline. The
same case passed in 53.9 seconds when rerun alone with unchanged assertions and
timeouts. No reference code was edited. The full command therefore had one failure;
the focused rerun does not erase it or establish the cause of the timeout.
The failure trace and source hashes are preserved under
`.recovery/phone-fps-2026-09-13-new/reference-integration-failure/`, with original
`npm-test.log` and separate `reference-retry.log`. The five new production
orientation cases passed initially; this separate software-rendered reference
check does not exercise the camera-copy helper. All 239 G dependencies still
match their pinned Git blobs, all 461 original-checkout files retain their hashes,
and all three phone ZIP originals remain byte-identical.

For the phone retest, refresh the corrected page and select camera copy first;
confirm an upright view and glasses. Then test V independently and the all-options
run. Keep video off for throughput and save a separate video run for visual review.
Cover Amber Horizon and Tom Ford Clear with both hair-only and selfie-multiclass,
including front/nose, down, up, both yaw directions, hair edges, tracking and
responsiveness. Compare full-window completed AR/s, age/stalls, startup and matching
masks, and use separate refreshed runs with reversed order to investigate the
remaining drift. Native-copy fallback must remain identifiable in comparisons.

## Fresh runtime switching - September 13, 2026

The owner reports lower FPS after algorithm switches, with the highest rates
seen after refreshing into a selected option. The prior harness cached both
face delegates, a render worker after first use, and the V hair-extraction GPU
cache. A fresh page did not have the same resource history. Idle retained
workers are not evidence of GPU execution or proof of the phone slowdown.
The saved synthetic desktop all-options run also varied substantially inside
measured windows: G 4.93 then 2.07 completed AR/s, with several candidates faster
on their return pass. Tracking and matching-mask coverage were 100%; this is
neither a monotonic leak diagnosis nor a valid physical-phone ranking.

The default FPS review now drains the old pump and all late hair results,
revokes its runtime, terminates face/hair/render workers, disposes renderer
contexts and caches, and creates the selected runtime afresh. This applies to
manual changes, initial G-to-G run setup, and adjacent repeated windows. Camera,
recording canvas and scalar run ownership remain continuous; sequence IDs never
reset within a session. Captured runtime identity fences asynchronous startup,
fallback, stop and restart. G and candidate rendering algorithms are unchanged.
Main-thread renderer disposal invokes the existing context-loss cleanup; no
pinned renderer or geometry implementation was edited.

Face/render runtime setup has a 90-second deadline. Once ready, the unchanged minimum
five seconds and three tracked same-image masks precede each full 30-second
measurement. Masked warmup has its separate 15-second deadline. Setup is
reported separately, not erased or folded into measured FPS. Hair initialization
still starts in parallel, as on page startup; any remaining hair startup occupies
the gated warmup, and the three-matching-mask requirement is never bypassed. Original shared
studies retain their previous timing policy. Explicit `&switch=shared` on FPS
review retains the old resource-sharing control and marks its telemetry; do
not pool it with the new default. Each frame records runtime generation and
isolation policy. The live rolling counter gets a new serial boundary for
every pump, including the same option twice, while exported history remains.

**Refresh selected test** retains algorithm, glasses, hair model, hair on/off
and power choices across a real page navigation. Tap Open camera afterward.
Save any result before refreshing. This is a direct control for browser-wide
state not reset by runtime teardown. The UI labels its rolling estimate; rank
using the ZIP's full-window completed AR/s, camera delivery, frame-age tails,
endpoint stalls, startup and tracking/matching-mask availability separately.

Strict TypeScript and 302 efficiency checks pass. Seven focused browser cases
pass: all six options then G across all four model combinations, delayed hair
readiness through Stop/restart, automatic separate setup and adjacent CPU epochs,
and hair-toggle live counter recovery. The resource matrix keeps two active
workers (three for worker rendering) and four active main WebGL contexts, with
all preceding epoch workers terminated and preceding contexts lost. One camera
stream remains throughout. Receipts are in
`test-results/mobile-2026-09-13T15-05-45.772Z/` (resource/lifecycle cases) and
`test-results/mobile-2026-09-13T15-09-35.300Z/` (automatic/counter cases).
The first automatic harness attempt sampled an eligible old manual-G row while
setup drained; the corrected check requires the new runtime epoch. The original
failure is retained. No runtime failure was established by that sampling error.

The real-clock partial run completes G and CPU for 30 seconds each and reaches
a separate second CPU runtime. CPU setup includes an injected 16-second ready
reply delay (18.238 seconds total switch wait), beyond the former 15-second
switch-inclusive limit. It retains 553 rows and 556 unique completed hair
requests, with no rejection/truncation and a drained ledger. Independent ZIP CRC,
raw boundary/denominator and measured-pair checks pass in
`.recovery/switch-isolation-2026-09-13/auto-independent-audit.json`; an independent
all-eligible-row hair audit accompanies the browser receipt. ZIP SHA-256:
`f703ed6aa2c3fbf90a24f722193341346397a87279e352e475f28666d22048cb`.
This is an intentional partial lifecycle test, not a new full twelve-window or
physical-phone throughput benchmark. Full-run order/window behavior is also
covered by protocol checks; earlier full-run evidence remains historical.

The four existing all-options live/held/export/resume cases also pass with
fresh runtimes for both glasses and both hair models; held accepted/hair pixels,
geometry and nose/front checks remain matched. Two camera-off refresh/navigation
regressions pass, including a second actual reload of an identical URL and no
automatic camera/worker startup. Log: `.recovery/switch-isolation-2026-09-13/refresh-held.log`.
The final package is `47ba023a9cc05a27a835031599ab6fca9d1765f7e5b7db7155c98c2d968f37f1`.
Its last rebuild registers the new refresh test and normalizes edited text line
endings; no runtime logic changed after the 8b89804e3df6 automatic/counter and
held integration checks. Final-package refresh tests pass again (2/2), and all
244 fingerprinted source inputs match the package. The local website landing
navigation passes. Published verification uses `qa/verify-published.py` and
`qa/verify-published-entry.mjs` against this same release.

`npm test` also passes: 172 unit checks and 21 browser tests, including
accepted-source replay, cancellation/failure recovery and both hair models.
Log: `.recovery/switch-isolation-2026-09-13/npm-test.log`.

All 239 accepted G dependencies verify as exact pinned Git blobs and the 461
original-checkout files match their prior hashes. Existing recordings and recovery
archives are unchanged. Physical iPhone recovery is not yet measured. Re-test both
Amber Horizon and Tom Ford Clear, each with hair-only and selfie-multiclass:
front/nose, down, up, left and right yaw, hair edges, tracking and responsiveness.
Use a separate video run for visual acceptance; no candidate is promoted.

## FPS review experiments - September 13, 2026

Railway entry correction: the all-options package at `84762f7` was pushed and
served correctly, but the normal `/ar_testing/` redirect was still canonicalized
by the AR client into `mask-preview`. It now resolves in the browser to
`?study=fps-review`, with all six options and G first. A visible All FPS experiments
link is available from the preserved G/V and historical studies. Python routes,
rendering, measurement protocol and private files are unchanged by this correction.
The entry-corrected package fingerprint is
`11991d87efe8c485a431ee32980d42a4b2cdd6934d5d7af39407acfd4452eca1`.
Strict TypeScript and 292 focused checks pass. Entry validation now tests actual
browser navigation from the normal landing link, alongside the existing
published-byte check; an HTTP redirect/manifest check alone did not expose the
previous client-side default mismatch.
The camera-off entry regression passes (1/1), including query-free and old
default URLs, all-six/G-first controls, explicit G/V links and historical studies.
`qa/verify-published-entry.mjs` also passes against the local production Python
routes, following the actual website landing link and returning through G/V.
Camera requests and worker construction remain zero throughout both checks.
Logs and local navigation receipt are in `.recovery/landing-entry-2026-09-13/`.

Owner follow-up: the bare `?study=fps-review` entry now contains all six choices
in one camera session: G, CPU face, render worker, VideoFrame copy, compositor
reuse and V. G remains selected first. The new `fps-all` recording protocol runs
all six forward and in reverse (12 windows, about seven minutes, one ZIP).
Explicit candidate links preserve the focused four-window protocol below.
Warmup, 30-second measured windows, pairing, algorithms, geometry and guards are
unchanged. The longer run retains at most 30,000 scalar hair requests, matching
the existing frame/video row bound; overflow is still explicit. This change
assembles existing experiments in one testing ground; it does not combine their
implementations or establish a new speed or visual result.

All-options package fingerprint:
`c235caea31a9582cc32e97e200365abade322146ad908f05b3682f6b1c388fe1`.
The package has 22 public files, checked against their sizes and SHA-256 hashes;
244 fingerprinted inputs match the build. The 239 G dependencies remain exact
pinned Git blobs and all 461 original-checkout source/doc/config hashes match.
`npm test` passes 172 unit checks and 21 browser tests; strict TypeScript and
292 efficiency checks pass. New protocol tests cover both run directions,
separate ownership for adjacent V windows, a zero-output worker window and
full 30-second denominators with pre-window captures excluded.

Six production browser checks pass, including all six live paths in one camera
session, matching held outputs/display pixels, export and restart for both
glasses and both hair models, plus preserved focused/fallback behavior.
Receipts: `test-results/mobile-2026-09-13T09-38-34.304Z/`. The full automatic run
completed all 12 real-clock windows and saved 2,420 frame rows, 2,063 measured
rows and 2,431 hair requests with a completed drain. Its ZIP SHA-256 is
`c8f1e04f93e83774e8aef02e2435b4efa896524cd507c93219d5b7c72bd85fa9`.
The initial Playwright wrapper exceeded its 630-second test budget during the
subsequent per-value matcher audit; the recording had already completed.
An independent Python audit of the retained ZIP passes CRC/byte identity, all
full-window clocks, CPU/GPU delegates, session/mask publication pairing and raw
FPS, age, stall and coverage calculations. It is retained privately at
`.recovery/all-preview-2026-09-13/independent-pipeline-audit.json`. This is
synthetic-browser evidence; the physical iPhone comparison still needs owner runs.
The browser test now shares `qa/fps-all-audit.ts`, using ordinary Node assertions
instead of thousands of traced matcher steps. The same retained report passes
that audit in 1.03 seconds, including rejection of eight deliberately corrupted
copies covering order, denominator, session, mask pairing, delegate, worker path,
age and coverage. Receipt: `.recovery/all-preview-2026-09-13/fps-all-retained-audit.json`.
Strict TypeScript passes after the harness correction. The runtime fingerprint
is unchanged; the full camera run was not repeated just for matcher overhead.

The owner requested implemented tests and a pushed iPhone preview from the attached
FPS review. The first focused study kept G selected first and compared one
option using G / option / option / G: CPU face, worker rendering, VideoFrame
capture and compositor reuse. Existing V is an optional reference. No new
candidate is accepted; U remains rejected. Older masks, altered resolution,
geometry changes and nose/front relaxation are excluded.

The attached laptop aggregate receipts support trying CPU first, but contain zero
visible hair edits. GPU queue serialization, the claimed exhaustion of exact
optimizations, the predicted worker FPS and attribution to battery power are not
proved by those receipts. Existing phone results and full-window denominators
remain as recorded below. Mechanisms, risks and fair experiments are detailed in
`docs/FPS_REVIEW_2026-09-13.md`.

All 239 G dependencies remain exact pinned Git blobs. Implementation stays in the
isolated deployed checkout; 461 source/document/config files in the original dirty
checkout were hashed and remained unchanged. The attached review was copied to
ignored `.recovery/fps-review-2026-09-13/`; recordings and prior archives were not
modified or published.

Compositor evidence: `qa/output/matched-2026-09-13T08-28-18.695Z/` contains a passing
56-case report (32 generated, 24 recorded), 16 lifecycle control groups and its
immediately completed independent PNG audit. The audit checks 666 files, all 239
G dependencies and 302 frozen input files. There are 36 visible hair-edit cases
and 12 nonzero rear-temple cases. All 56 use the candidate; 52 reuse output memory.
The detailed loop visits 4,365,600 of 57,057,280 pixels (7.65%); full residual and
final guard scans each still visit all 57,057,280 pixels. These are work counters,
not a speedup or peak-memory measurement. All 32 hardware cases use actual native
PBO downloads; the recorded software phase includes declared bounded fallbacks
(G 7, candidate 12), which do not count as fast-path evidence. Report SHA-256:
`d3019b7024fbe588f076c1fcc9f4896fef1d5dcdbe6cafd0f25e00df9a5afe3d`.
Independent audit SHA-256:
`7980c57fb353469a592f6fdf070ebe70cb9ddbb9846a433cdfb930996114758e`.

The matrix's recorded runtime boundary predates subsequent wrapper-only worker
publication/variant and continuous-switch lifecycle corrections. No compositor,
G algorithm, geometry or guard changed after that audit. Later production browser
checks validate the final integration; historical receipt hashes are not rewritten.

Integration review found and corrected: deferred VideoFrame leases exceeding the
two-image bound; selection changing during first detector startup; finalization
resuming a CPU-labelled pump before its CPU initializer completed; worker re-entry
publishing an old frame during private preparation; and worker-private publication
being mislabeled as visible main-canvas publish time. Dedicated regressions cover
these boundaries. Native capture waits are now explicit in startup diagnostics.

Physical iPhone throughput, sustained heat, camera color conversion and moving
wearer visual acceptance remain for the owner's new tests. Hold aliases for CPU,
VideoFrame and V do not independently test their input/inference changes. Both
frame models, both hair models and all five movement controls remain required.

Preceding focused package fingerprint:
`71666ebb3f29f36e1031b8a0ee871b374738e0cddfe5492c68e9c05f89cd6f0b`
(244 fingerprinted inputs, 22 public files). Recomputing the source fingerprint
and checking every packaged file's SHA-256 and size matches this build.
`npm test` passes 172 unit checks and 21 browser tests; `test:efficiency` passes
strict TypeScript and 289 focused checks. Three native camera-copy checks and
one actual CPU-worker check pass. These use synthetic Chromium camera inputs.

Final render-worker native tests pass 6/6 with 20 exact pose/model/hair pairs,
40 accepted/hair raw pixel comparisons, 20 private-display checks, actual PBO
readbacks, ownership and four alpha/resize/1280-cap controls. Receipts are under
`test-results/render-worker-native-2026-09-13T08-48-06.607Z/`. The three production
startup-switch regressions pass under `test-results/mobile-2026-09-13T08-46-11.233Z/`:
selection during initialization, stop/restart ownership, and Stop during the first
continuous CPU initialization with partial ZIP export and correct manual resume.
These retain real worker initialization and clocks. A corrected test assertion
uses completed detections instead of transient timing reset by the next request.
All paths in this section are relative to `experiments/efficiency-lab/` unless
otherwise identified; private run logs remain in `.recovery/fps-review-2026-09-13/`.

The final production mobile suite passes 11/11 in 8.8 minutes, under
`test-results/mobile-2026-09-13T08-49-38.514Z/`: all 16 candidate/glasses/hair live,
held-pixel and restart combinations; full real-clock G/CPU/CPU/G and
G/worker/worker/G runs with independent ZIP/CRC/raw-window checks; explicit
capture fallback and unsupported-worker failure; preserved G/V and historical
entry points; optional partial video; Stop during switching; late-mask drain
disposition; and local SDK/worker network boundaries. Measurements retain the
real five-second warmup and 30-second windows. No laptop synthetic rate is
presented as a physical iPhone speed result.

## Physical G/V video runs - September 13, 2026

The two new owner ZIPs match the published `8acef53052f9...` runtime. Both use
720x1280, hair-only and video recording. Original ZIPs remain byte-identical;
CRC, all exported window/stage/native numeric summaries and independent raw
window counts pass. Scalar publication/request matching passes for every
in-run row; each archive also retains one excluded pre-run publication without
a run-ledger request. This is scalar consistency, not an independent pixel/pose
pairing proof. No runtime, deployment or accepted-baseline change was made.

Amber (`05-23-56.760`) completes G/V/V/G with 60 measured seconds per profile.
G delivers 814 updates (13.567/s), V 846 (14.100/s), a 3.93% increase. G has
513 matching masks on 794 tracked frames (64.61%); V has 846/846 (100%).
Matching-mask update rates are 8.55 versus 14.10/s. Median/p95 frame ages are
129.44/193.66 ms for G and 130.67/186.28 ms for V. Camera delivery is
28.60-28.86 FPS. No measured publication or endpoint gap exceeds 200 ms.
G declines 14.133 to 13.000/s; V declines 14.267 to 13.933/s across passes.
Twenty G frames lack a face result during a very deep downward pose; a decoded
video frame at 17.559 s confirms glasses disappear then. Different live poses
prevent attributing this to V improving the unchanged face detector.

Tom Ford (`05-27-26.671`) is partial: first G 14.633/s, then V 13.800 and
13.167/s. Final G never starts measurement, so this is not a balanced winner
comparison and its missing window is not zero FPS. Its 15-second warmup still
publishes 207 tracked frames, but receives only two matching masks in time.
All 207 hair requests complete; completion is median 6.70 ms after publication.
The existing same-image wait and three-mask warmup rules explain the stop;
neither rule was relaxed. This is late mask delivery, not a frozen camera.

Every V request actually uses RGBA8 (Amber 1,006; Tom 962), with zero fallback.
All requests drain (1,992 and 1,696). Measured-request category extraction is
31.21 to 13.60 ms median in Amber, but complete request wall time changes only
69.68 to 67.64 ms as transport/scheduling residual grows. These overlapping wall
durations do not isolate GPU execution. Both run startups succeed: first AR at
3.481/3.913 s and first matching mask at 4.165/4.486 s after opening.

Forty decoded video samples cover both glasses with front, down/up and both
yaw movements, plus final Tom G warmup. They show differing poses and are not
matched image/pose/mask tests or owner visual acceptance. Neither archive uses
selfie-multiclass. Nose/front and hair-continuity non-regression still require
matched review across both hair models; no promotion follows. V remains the
strongest candidate from the completed Amber evidence; G stays accepted.
Private evidence, archive copies and visual samples are in the original checkout's
`.recovery/mobile-comparison-2026-09-13/`; see `agent-metrics/FINDINGS.md`,
`independent-audit.json` and `video-review/`. The completed video-on run must not
be pooled with the preceding measurement-only workload.

## Focused G/V preview - September 12, 2026

The owner authorized a preview and push after the phone comparison. The current
mobile entry is `?study=mask-preview`: G/V only, G selected, measurements on and
optional video off. G/V/V/G retains four independent five-second/three-matching-
mask warmups and full 30-second measurement windows. Both glasses/hair selectors,
front/nose, down/up/both-yaw cues, all-request hair timing, late-result drain,
camera delivery, completed AR updates, mask coverage, age/stalls and startup stay.
No live inference, extraction, rendering, scheduling, model, resolution or guard change
is introduced. V is still a candidate; the phone evidence below is not promotion.

Obsolete experiment navigation is removed from the preview. Historical choices
remain through explicit URLs and their automated checks are retained. The mobile
page canonicalizes the existing Python redirect to `mask-preview`; the parent
application is untouched. Query-free entry and the brand link also stay focused.
Local full-lab and accepted checkpoints, private recordings and recovery archives
remain available. Scope is the isolated deployed checkout described in HANDOFF.

Focused Hold now computes/exports only G/V, retaining the owned source/detection/
pose and SDK full diagnostic mask. It performs one G render and shares its output
with V; this is explicitly not independent evidence of V's live mask extraction.
The optional held scope is validated and copied before awaits, with G first and
the selected profile included. Cancellation, failure rollback and disposal stay.
Historical held comparisons retain their full default scope.

Strict efficiency types, the mobile production build and all 249 efficiency unit
checks pass, including bounded held scope, caller mutation, failure and disposal.
The 239 pinned G dependencies still match their exact accepted Git blobs. Required
`npm test` passes all 172 unit and 21 browser checks, including G, reference and
both long-hair lifecycle cases with unchanged deadlines. The mobile suite passes
the complete real four-window run, default/brand/legacy navigation, optional video
with 720x1280 decoding, stop-during-switch, delayed mask-hash disposition and SDK/CSP
network boundary. Independent ZIP CRC/window recomputation confirms 140.216 s,
1,339 AR rows (1,141 measured), 1,342 fully drained hair requests, full measured
tracking/mask coverage and no AR-row rejection/truncation. Seven nonmonotonic
camera callbacks are explicitly rejected; this is desktop functional evidence.
The full run used fingerprint `1b3c4ca7cdc1...`. A subsequent text-only correction
keeps the G/V Hold hint from being overwritten by obsolete rate-option guidance;
the final package fingerprint starts `8acef53052f9`.

Four new portrait checks initially failed because the QA assertion wrongly
treated export-envelope metadata as additional profiles. Only the assertion was
corrected to check the actual profile keys and declared scope while retaining
metadata, pixel/pair/mask/geometry checks, final safeguards and restart assertions.
All four affected cases pass against the final package, including actual V GPU
readback, exact held output, final nose/front/outside/background checks, cleanup
and a fresh session after restart. The nine focused mobile cases are covered by
five initial passes and four corrected passes. Original failure logs and artifacts
are retained; no runtime protection or deadline changed.
Receipts are under ignored `logs/mask-preview-2026-09-12/` and timestamped mobile
`test-results/` within the efficiency lab. The original owner ZIP is byte-exact.
Four model-studio files changed concurrently in the original checkout and were
left alone; the other 694 sampled original files remained unchanged.

The generated public package contains 21 allowlisted files. Publication receipts
are stored in the ignored QA output directory after the authorized push.
No physical phone visual acceptance for V, other glasses/hair performance or
thermal conclusions are established by this preview cleanup.

## First physical-phone V/W/X measurements - September 12, 2026

The owner supplied `ar-mobile-comparison-2026-09-12T15-57-21.924Z.zip`:
telemetry only, video disabled, Amber Horizon/hair-only at 720x1280. Correct
fingerprint `69d218964435...`, complete eight-window protocol and 240 measured
seconds. Independent recomputation matches every exported window/stage/native
numeric summary. All 4,373 hair requests completed and drained without ledger
rejection/truncation. The 4,366 retained AR rows include 3,663 measured updates,
all reporting a face. Detection availability is not tracking-quality proof.

| Profile | AR updates/s, 60 s total | Matching-mask coverage | Updates with matching mask/s | Age median / p95 ms |
| --- | ---: | ---: | ---: | ---: |
| G | 15.583 | 70.59% | 11.000 | 110.86 / 181.04 |
| V | 16.167 | 100% | 16.167 | 114.79 / 173.82 |
| W | 14.650 | 77.93% | 11.417 | 126.58 / 177.30 |
| X | 14.650 | 50.51% | 7.400 | 114.06 / 173.52 |

V delivered 35 additional updates (+3.74%) and 46.97% more updates with matching
hair masks. All 1,148 V requests used actual RGBA8 retrieval with zero fallback;
all 970 measured V publications had a matching mask. Across requests captured
in measured windows, including late outcomes, median category extraction was
G 26.90 ms versus V 13.30 ms; inference was similar (24.86 versus 24.28 ms).
Reduced worker time did not translate one-for-one into result delivery: V's
transport/dispatch residual grew. These wall times do not isolate GPU work.

First-use cost remains separate: V's first category extraction took 282.92 ms
and its first activation caused a 376.14 ms publication gap; first W caused a
483.80 ms gap. Both were before measurement. Actual warmed-window completion
gaps were at most 113.64 ms, with none over 200 ms. The eligible-frame/endpoint
metric has a different maximum, 166.68 ms. Initial opening to first AR was
4.001 s and to first matching mask 4.460 s. V's median frame age was slightly
worse than G, while its p95 was better; no blanket responsiveness claim follows.

W saved four queries on all 879 measured images (3,516 total), but measured query
wall time was median 0 / p95 0.02 ms. X's word scan ran on all 444 masked images,
over 409,190,400 pixels; its other 435 images lacked masks. Mask-conditioned
composition was lower for X (4.26 ms versus G 5.02 ms), but unequal motion/time
workloads prevent attributing this solely to word comparison. W/X each completed
5.99% fewer total updates than G; neither improved throughput in this run.

Camera delivery was 28.54-29.19 FPS, separate from AR updates. One nonmonotonic
callback was rejected in final G; its offending values are not retained.
All mediaTime values were zero, so camera rate uses presented-frame counters
and callback time. Observations cover over 99.78% of each window. G fell from
17.47 to 13.70 AR/s between passes; V from 17.80 to 14.53. Symmetric order balances
linear drift but cannot eliminate nonlinear drift/different motion or diagnose
a thermal cause. Earlier video-on results are not an equivalent workload.

V is the strongest follow-up candidate; G remains accepted/default. No video or
other glasses/hair combinations are present, so down/up/both yaw, nose/front and
hair-continuity quality cannot be verified from this ZIP. Next evidence should
focus on G/V across both glasses and hair models with matched movement cues and
separate video-on visual review. No promotion follows from this run.
The upload is unchanged; a byte-identical copy and recomputed `audit.json` are
retained under `.recovery/phone-per-image-2026-09-12/` in this deployed checkout.
ZIP SHA-256: `2ce3ccf0e4c2d31eaca3ad72091725386cb6263152657643bee9f9c3f3e6dd72`.
This records findings only; runtime and deployment are unchanged.

## Authorized per-image experiments V/W/X - September 12, 2026

The owner requested implementation and publication of the three researched
experiments for iPhone testing. G stays accepted and selected initially; U remains
rejected as worse than G. The focused `?study=per-image` page offers G/V/W/X and
eight unmodified 5-second/three-masked-frame warmups plus 30-second measurement
windows in G/V/W/X/X/W/V/G order. Measurement-only is the default; optional video
and manual switching remain. Both glasses/hair choices and all movement cues
are retained. No additional async scheduling or admission limit is introduced.

V uses the same callback-local MediaPipe texture and a cached full-size RGBA8
target. The shader reproduces the installed SDK's exact float32-to-category byte
semantics; an independent category buffer leaves the callback. It restores shared
GL state and uses explicit SDK fallback for unsupported/failed paths and full
diagnostics. W saves four repeated PACK queries only between one owned native
PBO submission/retrieval, invalidating around intervening context work and cleanup.
Dynamic bindings, fence/error checks and bounded fallback remain. X changes only
the initial RGBA residual comparison to aligned word equality, retaining byte
fallback, pixel arithmetic and every final protection check. The 239 pinned G
dependencies remain exact Git blobs; the original CRLF-manifest distinction is
preserved without normalization.

The all-request ledger retains actual extraction path, retrieval/conversion/copy,
total and attempted-V costs, bytes and fallback reason, including late masks.
W and X report actual mechanism use. A requested profile or safe fallback is not
an optimized-path success. Completed AR updates, camera delivery, frame-age tails,
stalls, startup, tracking and matching-mask coverage remain separate measures.
No phone improvement is inferred from reduced byte/query/comparison counts.

Strict efficiency types and 242 unit checks pass. Actual GPU shader comparison
passed 403,461 designed float32 inputs with zero byte differences, including
rounding boundaries, invalid values, GL/error-state preservation and owned output.
Eight actual GPU masks and eight explicit CPU SDK fallbacks match exactly across
both hair models and portrait/landscape/odd sizes. Sixteen GPU masks from the eight
preserved directional inputs also match SDK bytes exactly. V's held comparison
uses G's retained SDK mask; that shared output is not its extraction proof.

Required `npm test` passed 172 unit and 20/21 browser cases on its first run.
The unchanged long-hair multiclass restart case hit its 55-second latest-mask
assertion: a fresh matching-mask frame appeared before the separate stats poll,
followed by sustained missing masks under SwiftShader/CPU inference. The trace
does not establish the precise worker cause. An unchanged targeted recheck passed
in 43.4 seconds; do not label the initial full run green. Its original failure log
and the recheck log remain in ignored `logs/per-image-2026-09-12/`. No deadline or
baseline behavior was relaxed. GPU codec/model receipts remain in ignored
`test-results/production-2026-09-12T14-47-18.081Z/`; the initial missing-build
preview-readiness failure is retained separately.

W passes all 56 matched generated/recorded pairs and the independent PNG audit
(`qa/output/matched-2026-09-12T14-50-44.852Z/`). All accepted/hair pixels and
geometry are exact, with zero nose/front/outside/background/alpha violations.
All 32 hardware cases use W and save four queries each; all 24 SwiftShader cases
use the existing bounded fence fallback and claim no W query savings. Both
glasses/hair models, down/up/both yaw and 16 lifecycle/control checks are covered.
The audit reverified 245 runtime files and 302 frozen inputs without modifying
the original archives. The checkout can read them through explicit
`--archive-root`, while `--git-exact` enforces the accepted Git bytes rather than
silently rewriting the historical original-CRLF manifest.

X also passes all 56 pairs and its independent PNG audit
(`qa/output/matched-2026-09-12T14-59-36.442Z/`), with exact accepted/hair pixels,
geometry and final safeguards. Word comparison runs over the full image on all
56 cases; software PBO fallback is still reported separately. These matrices
exercise rendering and frozen pairing, not camera/worker scheduling or wearer
motion. Actual iPhone speed, masks under motion, thermal behavior and visual
acceptance of V/W/X remain for the owner's matched runs; no candidate is promoted.

The production Python-mounted mobile package passes seven short browser cases:
the SDK/CSP network boundary; all four portrait glasses/hair combinations using
real GPU face/hair workers and actual V/W/X mechanisms; exact held pixels,
detections/masks/geometry/guards and restart; optional-video partial export and
Stop during switching; and one deliberately delayed real mask hash with its
original late publication disposition retained through final drain. Layout fits
the 390-pixel viewport and was visually inspected. Each source is 720x1280;
static synthetic input is functional coverage, not personal wearer-motion proof.
Logs and receipts remain ignored under the lab's `logs/` and `test-results/`.

The full eight-window production run and independent ZIP/CRC/scalar audit pass
with unchanged clocks, windows and deadlines (7.7-minute browser check, including
the export audit). The measurement-only session lasted 280.822 seconds and
retained 1,829 frame rows and 1,834 hair-request rows without rejection/truncation;
all pending requests drained. All 1,562 measured updates had tracking and matching
masks. Desktop completed updates/s by round: G 5.90/5.93, V 11.63/5.93,
W 5.13/5.70, X 6.00/5.83. V's first-pass gain did not repeat; this synthetic,
traced desktop run does not select an iPhone winner. The full archive is retained
under `test-results/mobile-2026-09-12T15-12-33.939Z/`.
The public package contains 21 allowlisted files and matches source fingerprint
`69d218964435964b7cef4a44f622bca0f86ab0ea2b42953c0b284c191cdcdcc5`.

## Owner comparison: U rejected for promotion - September 12, 2026

After trying the preview, the owner reports U is worse than G. Keep G as the
accepted baseline; U (`hair-release`) is rejected for promotion. Preserve the
separate experiment and its evidence rather than inferring acceptance from
successful scheduling or image checks.

This is qualitative owner feedback. No new G/U measurement ZIP or matched
motion recording accompanied it, so the affected quality/performance dimensions
and device conditions are not quantified. The earlier synthetic desktop run
also favored G (9.37/9.70 versus U8.30/8.50 updates/s), despite demonstrated
next-request overlap during hashing. Increased GPU contention remains a possible
explanation, not a diagnosed cause. Earlier Q-T phone evidence must not be
relabelled as a U measurement. This update changes local findings only; it does
not change runtime, accepted G dependencies or Railway deployment.

# Current efficiency follow-up — September 12, 2026

The owner's completed iPhone 17 Pro recording confirms that the camera-clock fix
starts real AR: first publication 4.129 seconds and first matching mask 4.627
seconds after the camera-open request. The complete 359.687-second recording has
10 measured windows and 4,065 measured AR updates. G averaged 13.87 completed
updates/second; camera delivery was about 28–29 FPS. Q produced one additional
update over 60 measured seconds but had 28.40% matched-mask coverage versus
G's 41.95%. The later options and every second pass were slower. Unequal
movements/mask workloads and recording load prevent a winner or thermal claim.
There were no measured completion gaps above 200 ms. Age ends at publication,
not physical display. Detailed private audit/media remain in ignored recovery.

The owner requested a new live preview. U (`hair-release`) is isolated from G,
uses the accepted renderer directly, and changes only hair request admission.
A matched worker reply releases the next request while the previous validated
output awaits hashing; synchronous client validation itself cannot run in
parallel with main-thread submission. At most one worker computation and two
owned requests are admitted. U retains source-image leases through late result
settlement, including after publication, so no third source is captured. All
source/detection/pose/mask/session checks, model pixels, resolution, the existing
8 ms optional-mask wait and final nose/front safeguards remain. Earlier work may
increase GPU contention or backpressure; a speed/quality benefit is hypothetical.

The focused `?study=hair-delivery` preview compares G/U/U/G with identical
instrumentation, fixed workload, 5-second/3-masked-frame warmup and 30-second
measurement windows. It defaults to measurements only; optional video is retained.
Both glasses/hair settings and all five movement cues remain available. The ZIP
adds a bounded scalar ledger of every hair request, including late/failed/cancelled
results and whether its mask was used at publication. Actual submissions,
receipts, validation/hash completion and worker durations distinguish client
waiting from computation; admission timestamps alone do not establish GPU
parallelism. Final export drains late results without extending measured time.
A 20-second finalization watchdog explicitly reports incomplete cleanup, closes
the session and keeps unresolved records rather than hanging or restarting it.

Initial verification: 215 efficiency unit checks pass, including delayed-hash
release, G serialization, two-image lifetime bounds, original output validation,
late/cancelled ownership, frozen/private telemetry and strict run protocols.
The 239 pinned G dependencies retain their exact accepted Git blob bytes; the
historical original-CRLF manifest distinction is preserved without normalization.
Required npm test passes 172 unit and 21 browser checks. The packaged portrait
startup regression passes all four glasses/hair cases. Seven focused production
browser cases pass: full unmodified four-window measurement-only export, all
four held G/U image/detection/mask/geometry/guard comparisons and restart, plus
optional-video partial export and Stop during an asynchronous switch. A final
case also waits through the real 20-second drain watchdog, verifies the incomplete
archive and closed resources, reopens the camera and safely resolves the old bitmap.

The full desktop run retained 1,264 hair requests without rejection/truncation
and drained all submitted outcomes. Of 593 completed U requests, 192 had a next
actual submission during prior hashing. This establishes application scheduling
overlap, not concurrent GPU kernels. U completed 8.30/8.50 updates/second versus
G's 9.37/9.70, all with matched masks; synthetic camera delivery also differed.
This desktop functional run demonstrates no speed gain. The owner's physical
phone had much lower mask availability and remains the intended next comparison.

These new image checks use static portrait input and shared held G rendering.
Five movement cues are checked in the real timed protocol; new matched U motion
recordings for down/up/both yaw and physical nose/front quality are still missing.
Earlier accepted direction/geometry evidence is preserved, not relabelled as a
new scheduling-quality proof. Logs, screenshots and test archives stay ignored.
The public runtime fingerprint is
`0b4686e03825db646f2f688e9fd3409317c4dc93ce37359315164bab34547bf6`.
No candidate is promoted and no new physical-phone U result is available yet.

# Current AR review — September 9, 2026

The owner authorized implementing the reviewed long-hair version and committing
and pushing it as **perfecto long hair but slow**. This follows positive live
and generated-still feedback and disclosure that the current version is slow.
Approval is visual acceptance of this checkpoint, not a mobile performance claim.

Promotion makes the reviewed hair page the default launch and build entry while
retaining perfect_temples and original perfecto. Exact b26 reference sources and
both reviewed hair weights are packaged for a fresh checkout. The compositor,
continuity rule, input/pose ownership, geometry, nose/front guards and 15 mm fade
remain unchanged. Only entry/packaging, acceptance labels and additive diagnostic
identity change. The parent Python/Railway app and unrelated model work are excluded.

Prior independent evidence covers 32 new generated-still cases and 24 exact
short-haired wearer-recording pairs, both frames/hair models, down/up/both yaw.
All reference image/geometry and final nose/front/outside-arm checks pass within
those inputs. These tests do not establish long-hair motion or measured fit.
The generated pose estimates span approximately −31° to +22° elevation and
−42° to +30° yaw; requested 60° profiles were not achieved.

Performance remains limited. Short Intel Arc 140T/D3D11 simulated-camera samples
measured about 5.35–5.48 updates/second with hair versus 8.18–8.57 without.
Mobile/physical-camera/sustained/thermal/battery evidence is missing. Model-only
segmentation timings must not be reported as full AR frame rate.

Promotion verification used the exact staged tree
`8f1601c694f30853cf6027728521a1a9f927d570` in a fresh export without an app
Git directory, private recovery files or unrelated trial-model changes.
Fresh npm ci, strict types, pinned asset checks, production build and all
110 unit tests passed. Seven of eight baseline browser tests passed.
The existing cumulative 60-second recorded-replay test timed out during
final camera restart, after its exact native replay PNG comparison passed.
The full npm test exited 1. Both new production hair lifecycle tests passed
separately, with real local hair-only/multiclass workers, exact held pairs,
export and session cleanup/restart. No limits were raised and no retry was
used. The exported source remained byte-exact throughout these checks.
Only these documentation results were added after the tested tree.

All 32 generated and 24 original recorded promotion comparisons exactly
match the previously reviewed accepted/hair images and geometry. Independent
PNG audits verified 226 files; nose/front/outside-arm checks remain zero.
Source images, detections, poses and masks were reused with exact ownership;
there was no reinference or substitution of synthetic geometry for recorded
evidence. Ten packaging checks also verified dev/preview model delivery,
hashes, build aliases, unknown requests and rejection of tampered weights.

The fresh-run logs, replay failure trace and separate hair lifecycle receipt
are in the current acceptance archive. The prior shared-tree replay timeout
and all older study receipts are retained in their original archives.

Current acceptance artifacts:
[packaging and verification](../.recovery/perfecto-long-hair-acceptance-2026-09-09/),
[latest still review](../.recovery/hair-angle-review-2026-09-09/RESULTS.md),
[recorded/performance evidence](../.recovery/hair-live-performance-2026-09-08/RESULTS.md).
Historical experiments and previous full review text remain in their archives.

## Separate performance comparison candidate — September 9

The owner's requested review items 1–3 are implemented only in
`experiments/performance-candidate/`. `npm run dev:performance` serves its
Current/Test comparison at `8066/experiments/performance-candidate/live.html`;
`build:performance`/`preview:performance` provide an isolated production build.
The accepted long-hair entry point and pinned `perfect_temples` are unchanged.

The candidate skips the unused exact-zero rear-drop render/readback, redundant
zero geometry reset, inactive temple overlays and an unnecessary depth pass.
It reads native pixels directly, passes an independently owned native image to
hair composition, composes temple edits over exact row spans and reuses private
membership/coordinate scratch without producing the unused live weight image.
Image/pose/mask ownership, inference, resolution, scheduling, the 8 ms hair timer,
materials, geometry policy and final nose/front/background guards remain fixed.
The omitted diagnostic 2D/native comparison is explicitly marked in exports.

Only the selected pipeline processes live frames. A switch commits at the next
owned frame without restarting camera/workers; an explicit Hold completes the
current pair, stops live resources and lets both renderers use the identical
image/detection/mask. Both initialized renderers remain resident, so the comparison
page's memory use is not representative of a standalone candidate. Display rate,
hair availability and processing p95 are visible; processing is not sensor-to-screen
latency, and lower hair availability would not be an equivalent quality speedup.

Strict types, the isolated build and 27 focused tests pass. The first production
browser run passes all five live switching/held comparison/stop/restart scenarios,
covering both glasses and hair models with actual local workers. Its sampled
104/104 live frames have valid category masks, and all four held comparisons have
identical source, full mask, output pixels and geometry. That browser used
SwiftShader/CPU; the initial receipt's generic “Hardware browser” scope text was
incorrect (the recorded backend is authoritative). Its timing is not hardware
performance evidence, and subsequent receipt wording is corrected.

The separate Intel Arc 140T/D3D11 production run also passes all five scenarios,
with GPU hair inference and 104/104 valid sampled live category masks. Across the
four glasses/hair combinations, median render work is 73.7–82.2 ms for Current
and 51.0–54.9 ms for Test; median frame processing is 125.3–145.6 ms and
101.8–117.0 ms respectively. These are 13 observed warmed samples per pipeline,
Current then Test, from a 640×427 neutral synthetic camera with no edited hair
pixels. They are preliminary local observations, not a counterbalanced motion
benchmark or end-to-end latency claim. Receipts are summarized in
`experiments/performance-candidate/logs/hardware-live-summary.json`.
Two additional production controls tests pass: an algorithm switch plus Hold
during a privately prepared frame, and a failed full held-mask upgrade retaining
the valid category mask, exact per-pipeline hair toggles and working download.
Desktop and narrow-screen controls were inspected; the algorithm panel spans the
mobile sidebar. All 103 independently monitored accepted source/asset/recording
files retain their original hashes.

The [matched QA and independent audit](../experiments/performance-candidate/qa/README.md)
pass 32 generated and 24 exact recorded pairs across both glasses/hair models,
down/up/both yaw and nose/front checks. This is one presentation per pair with
sessions reused through pose transitions, including a fresh zero-visibility
recorded frame for each glasses model. All tested current/candidate/archive RGBA
and geometry match; 302 private input receipts are unchanged. The earlier warmed
run remains failed at one protected optical/nose pixel in the current native
output; candidate matched the archive. A fixed 12-render probe per pipeline was
stable but did not resolve that variance. No tolerance or safeguard was relaxed.
There is no complete warmed speed claim or owner visual acceptance.

The required `npm test` invocation passes strict types, build and 124 unit tests,
then passes 7/9 reference browser scenarios. The recorded-replay scenario exceeds
its existing 60-second total deadline; the separately modified test_ scenario
expects a third picker option absent from the accepted two-model build. This
failure stops the command before the later hair integration suite. Both failures
are retained in `experiments/performance-candidate/logs/npm-test.log`; accepted
code and test deadlines were not changed. Real camera long-hair motion, wearer
acceptance and sustained mobile smoothness remain unmeasured.

## Test 2 and live profiling — September 9

`experiments/performance-stage2/` adds a separate Current / Test 1 / Test 2
preview and scalar frame profiler. Test 2 shares the original native renderer's
camera texture/context for the clean camera pass. Beauty pixels are owned before
the clean pass touches the framebuffer; both direct readbacks remain. It removes
a duplicate context/upload, not the transferred image bytes. Resolution, model
configuration, exact image/pose/mask ownership, materials and final safeguards
remain fixed. Optional capture failure renders fresh protected beauty and rejects
hair; the Test 2 fallback reader also checks GL errors before publishing pixels.
All three renderers remain resident, but only the selected one processes live
frames. Accepted and Test 1 sources are preserved.

The profiler separates camera delivery, completed AR cadence, processing/interval
p95 and individual stages. It uses local clock durations and does not measure
sensor buffering or physical display scanout. Parallel spans overlap. Test 2's
legacy `cleanCameraMs` is validation-only; clean submission/readback now belong
to preparation and must be compared through the nested metrics or total render
work. The automatic comparison measures each pipeline for at least 30 seconds
after warmup. Cached results show tracking and mask coverage; full coverage is
explicitly 100%, not a visual acceptance decision. Missing work stays in timings.
Downloads have a readonly numeric JSON fallback for embedded browsers.

An authorized physical-camera run on this computer reached the completion UI
with Amber Horizon/hair-only at 1280×720. Rolling panels showed Current about
7–8 fps, Test 1 about 10 fps, and Test 2 about 11 fps while browser video delivery
stayed near 29.6 fps. The embedded browser did not produce an accessible Blob
download, so these are observed ranges, not saved 30-second aggregate results.
The observations also predate the final fallback-reader/API and reporting fixes;
they do not establish final-source speed. No camera images were saved by this
benchmark. The camera was closed afterward. Fixed order, uncontrolled motion,
one desktop/model combination and missing raw data limit this physical evidence.
See the ignored `qa/output/physical-camera-observations-2026-09-09.json` receipt.

The final renderer passes 56 exact Test 1/Test 2/archive comparisons: 32 generated
and 24 recorded pairs, both glasses and hair models, down/up/both yaw and final
nose/front/background checks. Accepted-first direct presentation followed by a
hair toggle is covered. These matched stills do not establish visual acceptance
or resolve the earlier Current warmed protected-pixel variance. Strict types,
the isolated production build and 33 focused tests pass. Seven production browser
scenarios pass, including exact all-three held pixels/geometry, both glasses/hair
models, pending Hold/switch ordering, full-mask failure, cancellation and restart.
Timing downloads exactly match the copyable JSON fallback and contain no images.

The final production synthetic study contains 4,382 completed frames over 360.08
measured seconds, all twelve planned 30-second segments with rotating orders.
Every measured frame had tracking, a paired hair mask and actual hair edits.

| Glasses / hair | Current FPS | Test 1 FPS | Test 2 FPS |
| --- | ---: | ---: | ---: |
| Amber / hair-only | 10.73 | 13.30 | 14.43 |
| Amber / multiclass | 7.20 | 12.93 | 14.12 |
| Tom Ford / hair-only | 9.66 | 14.59 | 14.67 |
| Tom Ford / multiclass | 8.50 | 12.37 | 13.53 |

Test 2's additional cadence gain over Test 1 was 0.5–9.4%; one combination nearly
tied. For Amber/hair-only, median render work was 49.53 / 29.13 / 25.42 ms and
p95 publication interval was 116.0 / 90.2 / 80.7 ms. Test 2 still spent 10.48 ms
in two native readbacks, 1.51 ms extracting rows and 23.30 ms in the face worker's
inference call. Reducing those costs offers more headroom than requesting a faster
camera. This is a next experiment, not a demonstrated path to 30 fps.

These are Intel Arc 140T/D3D11 results on a static generated 960×640 source.
Its main-thread timer delivered about 20 video fps for Current and 29–30 for
candidates, so the speed results include synthetic-source scheduling effects.
A 785 ms Current/Amber/multiclass publication gap remains in the results with
unknown cause. The first three measured segments survived a harness stop caused
by an informational CPU-delegate notice classified as an error; exact sources
and compiled output were reverified before that QA-only classification fix, and
no completed measurement was rerun. The [QA record](../experiments/performance-stage2/qa/README.md)
contains the individual/combined receipts, p95 values and independent PNG audit.
All 40 compiled production files were reverified. Sustained phone behavior,
moving-wearer quality and owner visual acceptance remain unestablished.

The required `npm test` passes strict types, the build, 124 unit tests and 8/9
reference browser scenarios, including the recorded replay that timed out in
the earlier run. Its remaining failure is the separately modified `test_` test
expecting an option absent from the accepted two-model build; the command stops
before its later hair integration suite. The exact log is
`experiments/performance-stage2/logs/npm-test-final.log`. No accepted code or
test deadline was changed to address that unrelated mismatch. Desktop and
390/360-pixel layouts were inspected without page overflow. All 137 independently
monitored accepted/reference/Test 1 source, asset and recording files retain their
original hashes (`logs/preservation-final.json` in this experiment). The preview
is local; the parent application and Railway deployment are untouched.

## Test 3 GPU hair comparison — September 9

`experiments/performance-stage3/` adds a fourth isolated pipeline. Its original
multisampled native beauty and clean camera images are captured into separate
same-size RGB8 textures. Integer GPU composition preserves the existing category
boundary arithmetic; compact flags feed the unchanged CPU continuity geometry
and connectivity rules. Independent GPU reductions check clean reference and all
four final protected regions before publication. Full color readbacks remain for
explicit held exports and nonzero rear-temple CPU composition. Source/pose/mask
ownership, image dimensions, models and accepted rendering definitions stay fixed.

The comparison retains all four live choices, exact held switching, numeric
timings and copyable JSON. It shows Test 3 GPU usage among tracked hair frames,
including missing masks and CPU fallback in that denominator. Four renderers are
resident; only the selected pipeline processes live frames. GPU-preferred canvas
publication still has browser-controlled copy/synchronization costs.

The hardware helper passes exact byte/statistic comparisons at 23×17 and
1280×853, including padding, restored GL state and deliberate final-guard failure.
Separate RGB8 targets correct the incompatible RGBA8/offset resolve from the
earlier atlas attempt. A failed wrapper presentation now explicitly invalidates
retained native GPU leases before source validation; diagnostic counters include
later reads. These mechanism checks alone establish neither full AR appearance
nor a speed gain. Complete rendering and sustained results follow in this section.

The initial full run found a repeatable black native beauty on the second
software-rendered recorded pair, despite an exact clean camera image and no GL
capture error. The reference guard rejected hair; that did not make the black
fallback acceptable. Additional diagnostic reads masked the defect. Replacing
only the GPU blit with same-size RGB8 `copyTexSubImage2D` passed the original four
uninstrumented recorded comparisons and lifecycle controls, with an independent
29-file audit and zero full color reads before export. The browser/driver cause
is unproven. Failed and instrumented receipts remain documented in the separate
QA record; final validation uses no diagnostic reads during live preparation.

The final uninstrumented renderer passes all 56 exact matched comparisons:
32 generated and 24 original recorded pairs, both glasses/hair models,
down/up/both yaw, exact camera/pose/mask pairing and final nose/front/background
guards. All 44 eligible pairs use GPU composition; all 12 nonzero pairs use
explicit CPU correction. Sixteen control groups pass. The independent audit
verifies 426 files, including pre-export versus diagnostic readback counts;
183 runtime, 302 frozen-input and 175 preserved-file hashes are rechecked.
See `qa/output/matched-2026-09-09T13-45-15.315Z/` in the new experiment.
Strict TypeScript, the separate production build and all 30 focused tests pass.

Seven production-browser scenarios pass across two runs: four glasses/hair
combinations verify exact all-four held source/mask/pixels/geometry, downloads,
stop and restart; three unchanged controls verify pending Hold/switch ordering,
failed full-mask upgrades and startup cancellation. The first run's four failures
were a new QA assertion incorrectly requiring GPU use for the fixture's nonzero
rear drop. Corrected QA requires GPU or CPU execution from actual geometry and
the corresponding branch-read count; the runtime and pixel expectations did not
change. Those four scenarios then pass in a separate retained output directory.
The production fixture exercises CPU fallback; the 44 matched GPU cases remain
separate GPU rendering evidence. Desktop and 390/360-pixel layouts have no page
overflow, including a clearly labeled placeholder table used only for layout QA.

The final production benchmark measures 2,555 completed frames over 240.12 seconds
in eight warmed 30-second segments, alternating Test 2/Test 3 order. All frames
have tracking, paired masks and actual hair edits; all 1,279 Test 3 frames use
GPU hair composition. On the static generated 960×640 Intel Arc 140T/D3D11 source:

| Glasses / hair | Test 2 FPS | Test 3 FPS | Test 2 age p95 | Test 3 age p95 |
| --- | ---: | ---: | ---: | ---: |
| Amber / hair-only | 11.86 | 11.66 | 93.04 ms | 104.54 ms |
| Amber / multiclass | 10.10 | 10.59 | 103.50 ms | 108.48 ms |
| Tom Ford / hair-only | 10.40 | 10.73 | 104.43 ms | 106.90 ms |
| Tom Ford / multiclass | 10.16 | 9.63 | 104.91 ms | 116.49 ms |

Aggregate cadence is effectively tied (10.630/10.651 fps), while Test 3's p95
capture-to-submission age is worse in every combination. Pooled median render
work increases from 34.28 to 39.11 ms: preparation decreases (19.63→9.00 ms),
finish increases (14.44→29.55 ms). Explicit renderer readback bytes decrease
87.5%, from 4,915,200 to 614,408 bytes/frame, but three compact synchronization
reads, 1,843,200 bytes of new uploads, CPU region/statistics work and GPU checks
remain. Later flag reads can absorb queued native GPU work; nested durations
overlap. Reducing transferred bytes alone did not improve this pipeline's feel.

Reported long tasks are 1/39 for Test 2/Test 3, and synthetic video delivery is
about 25–28/23–25 fps. Source hashing and face inference timings also vary; the
source timer shares the application thread. These are within-run observations,
not a physical-camera, mobile or causal driver claim. All 206 source/input files,
46 compiled files, 8 served entries and 175 preserved files are verified. The
complete receipt and summary are in `qa/output/sustained-2026-09-09T13-55-56.542Z/`.
Test 3 remains an experiment; these results do not justify replacing Test 2 or
promoting either candidate. New Test 3 wearer motion and visual acceptance remain
unmeasured. The comparison preview is open locally with the camera off.

The required `npm test` passes strict types, assets/build and all 124 unit tests,
then 8/9 reference browser scenarios, including replay and cancellation. Its
existing separately modified `test_` scenario still expects a third picker option
absent from the accepted two-model build; exit 1 stops the later hair integration
suite. `experiments/performance-stage3/logs/npm-test-final.log` retains the result.
No accepted test deadline or model registry was changed. The separate experiment
checks above passed; the parent Python application and Railway deployment remain
outside this change.

## Speed testing base — September 9

The owner explicitly selected Test 2 as the current base and authorized
commit/push as **speed testing**. Default dev/build/preview use the new
`vite.speed.config.ts`, opening the stage2 comparison with Test 2 selected.
Both stage2/stage3 controls label it **Current · Test 2**; original `current`
report IDs retain their historical meaning and display **Long-hair checkpoint**.
Rendering code, weights, geometry, exact-pair ownership and all final guards are
unchanged during promotion. Accepted long-hair (9997050), perfect temples
(b26b558) and original perfecto remain available. Dedicated long-hair launch
aliases preserve the accepted route and its lifecycle tests.

The owner-uploaded report ar-speed-comparison-2026-09-09T14-18-35.157Z.json
contains four complete warmed measurement windows: 1,139 frames over 120.197 s.
Recomputed summaries match all 128 audited summary fields. Original/Test1/Test2/
Test3 achieved 7.711/9.937/10.164/9.962 fps at reported video delivery 29.63–29.65
fps and 100% face/mask coverage. Test2 processing median/p95 was 69.19/86.25 ms.
Test3 used its GPU path on 261/300 frames; all 39 CPU cases were expected
nonzero-drop branches, with no GPU failure. Test2 had 21/306 nonzero-drop frames.
Different live images and branch frequency limit causal comparisons. The report
contains no source images/poses or build hash and cannot establish visual
equivalence, measured fit or end-to-end display latency. Mobile remains unmeasured.

The first nonzero rear-drop activation in each Test2/Test3 session cost 97.10/
101.98 ms in branch presentation (178.16/201.94 ms total frame time), followed
by roughly 6 ms branch presentations. Neither five-second warmup exercised this
path. The location of the first-use cost is established; shader compilation is
a hypothesis. Test2 median scheduler wait was 24.74 ms. The current scheduler
requests a future video callback only after finishing the previous frame.

Next ideas are recorded in [SPEED_PLAN.md](SPEED_PLAN.md); no new optimization
or preprocessing change is part of this default selection. Earlier 56-case
matched generated/recorded checks cover both frames, both hair models, down/up/
both yaw and nose/front protection. They remain bounded evidence, not universal
visual acceptance. Promotion verification is recorded below when complete.

Promotion verification used an exact staged export without private inputs or
unrelated worktree source/models, with fresh npm ci. All 90 focused speed
checks passed (22 candidate unit + 5 renderer lifecycle + 33 Test2 + 30 Test3),
as did strict types and the production build. Full npm test passed 110 unit
tests and all eight baseline browser tests at unchanged deadlines, including
recorded replay. Its long-hair lifecycle phase passed hair-only but failed the
multiclass restart: no category-only live mask appeared within the existing
55-second polling window after Resume. The full suite therefore remains failed;
the preserved renderer/test was not changed or retried to hide that result.
Logs, trace and screenshot are retained in
`.recovery/speed-testing-promotion-2026-09-09/`.

Promotion route checks found that the pinned resolver emits perfect-temples HTML
under its reference source path, leaving the old public URL to SPA fallback.
The new speed config emits a byte-exact compiled HTML alias at the documented
URL; original resolver/renderer files remain unchanged. Stage2 standalone builds
also include the newly linked Test3 entry. The final build passed; the compiled
temple alias was independently checked byte-for-byte. The dedicated promoted
production browser result follows.

All seven final promoted production browser cases passed on the desktop D3D11
backend (2.3 minutes): both glasses × both hair models, correct Test2 default
before camera and on first publication, exact held images/masks/geometry, real
preserved-page titles, isolation headers, startup cancellation, stop/restart,
pending-frame Hold/switch ownership and failed full-mask upgrade retention.
These use a synthetic camera fixture and real local workers, not a new physical
camera or mobile speed measurement. The standalone Stage2 build also passed
and includes its linked Test3 page. Test2 renderer bytes were not changed.

Commit scope was checked separately from unrelated source/model-studio work.
No parent application, Railway configuration, trial model, private camera image,
recording or recovery output is included. The old Stage3 QA preservation manifest
intentionally rejects the changed selector/test source boundary; historical
receipts remain unchanged and future experiments need a new explicit base manifest.

## G Combined selected as current base — September 9

The owner reported that F and G felt far superior, then explicitly requested
implementing G and committing/pushing it. G is the initial selection for default
dev/build/preview. The eight-way comparison keeps Test 2 (8baa16c) as the
previous reference, with its own launch commands and unchanged renderer.

G combines source-pixel reuse, fewer source copies, temple prewarming and real
WebGL2 PBO/fence downloads with bounded two-image scheduling and inference
overlap. Each image retains its exact source, pose and mask. Only the selected
renderer processes live frames. Hold/switch drains work; Stop revokes ownership.
Async failures use a bounded, explicitly counted same-pair synchronous fallback.
Geometry, resolution, continuity and final nose/front safeguards are unchanged.

The tested G renderer/options are unchanged by promotion. Existing matched
checks and independent PNG audit pass 56 generated/recorded pairs and 16 control
groups, both glasses/hair models, down/up/both yaw, exact pixels/geometry and
nose/front/outside-arm protections. All 56 use source reuse and borrowing;
36 have visible hair edits and 12 exercise nonzero rear drop. Hardware uses PBO
in 32/32; software in 19/24 with five declared 500.3–510 ms fallbacks. No fallback
counts as an async speed gain. The color/size study passes 36 comparisons and
eight alpha/cancellation/recovery controls, independently checking 302 PNGs.
All 186 protected base files in the original worktree still match their manifest.
Its historical CRLF hashes require that byte representation; optional QA documents
the distinction from normalized Git text. Frozen inputs and
prior receipts, including the initial strict-mechanism and browser failures,
remain unchanged in ignored archives. Optional reproduction is documented in
[Speed lab QA](../experiments/speed-lab/qa/README.md).

The lab fixes queued selection during Hold, strips image identities from timing
exports, and preserves opaque canvas history for unsupported transparent input.
The benchmark compares Test 2 and the selected mode with separate warmed windows,
reversible order, actual-path counters, tracking/mask coverage and frame age.
Timing exports contain no images, masks, landmarks or image hashes.

Clean staged-export verification passes npm ci, strict types, the production
build, all 172 unit checks (110 existing plus 62 lab) and all 21 browser cases:
11 G default/comparison/lifecycle, eight reference and two long-hair integration.
The strengthened exact checkpoint-title test also passes; camera-off layout
passes at 1440, 390 and 360 pixels without overflow or page errors. G development
and previous Test 2 preview redirects preserve their expected entry and query.
No tests were retried to obtain a pass or given longer deadlines. The export
excludes unrelated trial-model work and private artifacts. Test receipts are
retained under the ignored G promotion archive; the production preview is on
port 8090. The commit contains only AR runtime, comparison, tests and documentation.
The owner's feedback is qualitative; physical-camera throughput, motion-to-photon
latency, sustained mobile smoothness and thermal behavior remain unmeasured.

## Authorized public mobile comparison — September 11, 2026

The owner explicitly requested an `ar_testing` option on the live Lenses landing
page and a continuous run that saves **video + measurements in one file**.
Implementation uses an isolated checkout based on `main` ad822c4, carrying the
committed G source and the separate efficiency lab. The original dirty checkout,
private recordings, recovery files and unrelated model-studio work are preserved.
Parent changes are the landing link, a scoped static route, its tests and README;
`Procfile`, `UI/app.py`, Python requirements and deployment startup are unchanged.

The mobile page starts G and offers independent Q/R/S/T: suppress unchanged
pre-prepare publication; crop consumed branch readback rows; omit branch lens
materials; refresh repeated human summaries less often. None is promoted. The
prior R and S review each passed 56 matched generated/recorded cases, both glasses
and hair models, down/up/both yaw and final nose/front checks. Their exact images,
geometry and masks remain bounded evidence. Those earlier receipts remain in the
original ignored `qa/output/matched-2026-09-11T12-15-05.026Z` and
`matched-2026-09-11T12-18-54.945Z` directories. Reduced bytes or branch work is not
a mobile speed or visual-acceptance claim. All 239 accepted G dependencies match
their documented local-byte or Git-blob identity in this publication checkout.

The continuous protocol runs G/Q/R/S/T then T/S/R/Q/G, with five-second warmup,
three tracked/masked warmup frames and a full 30-second measurement window for
each mode. It fixes glasses, hair, source dimensions and session ownership,
drains switches, and retains source/publication window boundaries. Camera
delivery, completed AR throughput, age, stalls including window endpoints,
tracking/mask coverage, startup and switch costs are separate. Independent raw
retention avoids the old 4,096-row profiler limit and explicitly stops at limits.
Video records the displayed canvas with no audio; a local ZIP contains video and
scalar telemetry. Automatic download has manual Save/Share fallbacks. Partial
stop/background/failure runs remain marked, and starting a new run releases the
previous retained archive. See [MOBILE.md](../experiments/efficiency-lab/MOBILE.md).

The explicit public package contains 21 files, 40,889,291 bytes. Its private
manifest allowlists exact size/SHA-256 values; generated bytes are preserved in
Git without line-ending normalization. No private inputs or research directory
is exposed by the Python route. Release source fingerprint is
`894b05be66c9d6f53b8e5a77117cdd9d805753ec235b132952651a57d7101893`.

Two real integration failures were fixed before publication. MediaPipe appended
a slash to its runtime directory, producing a noncanonical double separator;
the mobile compile-time address mapping now supplies the canonical root. The
first complete video run then caught 13 background SDK telemetry requests. The
pinned SDK has no supported JavaScript opt-out, so the mobile build installs a
same-origin fetch guard before SDK task creation in both workers. Enforcing CSP
also applies to AR documents and worker responses, allowing local textures and
WASM without external connections or JavaScript eval. The final full-run test
retains its zero-external-request assertion; it was not relaxed. A separate
browser sentinel proves both worker guards and independent CSP enforcement.

Verification in the isolated checkout passes the required `npm test`: 172 unit
checks and all 21 baseline/G/long-hair browser cases at their existing limits.
Final strict efficiency types and all 167 efficiency checks pass. All 47 Python
UI/route/DOM/CSS checks pass, including AR headers, private paths, streaming,
integrity, GET/HEAD/304/errors and unchanged parent pages. The production mobile
boundary check passes, and all four real mobile-viewport browser scenarios pass
in nine minutes. The full ten-window recording produces a CRC-verified ZIP with
a video decoded near 348.5 seconds; all four glasses/hair combinations pass exact
held images/geometry/masks/guards, stop/restart and partial/cancel cleanup.
Backgrounding is explicitly simulated. No camera or pipeline timers are shortened.

Test artifacts, including both earlier failed attempts, remain under the ignored
`.recovery/mobile-railway-2026-09-11/` checkout; final browser receipts are in
`experiments/efficiency-lab/test-results/mobile-2026-09-11T13-16-10.296Z/` there.
Whitespace checks pass for authored source; generated vendor shaders/runtime and
preserved output line endings are retained byte-for-byte for their manifest.
Desktop Chromium/D3D11 with a synthetic camera is functional evidence only.
Actual phone camera, wearer motion, codec/download behavior, peak memory,
sustained smoothness and thermal effects remain to be measured by the owner.

### iPhone loading report and bounded recovery — September 11

The owner reported an iPhone 17 Pro remaining on Opening/Preparing Mirror for
more than one minute. The old label spans module import, G/candidate renderer
creation, face-worker startup and first publication; it does not locate the
phone's stall. Renderer import/fetch/GLB decoding and first-frame preparation
had no overall startup deadline. Face-worker attempts already have 45-second
deadlines; missing non-SIMD assets cannot explain the pinned forced-ESM loader.

The lab now reports each setup stage and elapsed time, with a small local startup
JSON available before the first image and after failure/cancellation. It includes
safe browser/capability/settings/milestone/error data and at most 120 same-origin
resource timing entries on manual export, stripping query strings and excluding
images, face data, camera device IDs and request/response contents. A copyable
text fallback accompanies the file. Worker canvas capability remains explicitly
unknown when only main-thread support is observed.

After camera readiness, module/G/candidate phases each have 60 seconds, face has
an outer 100 seconds, and first publication has 30 seconds, with a 240-second
overall setup cap. Existing camera and worker limits remain. Timeout closes the
owned session; completion/cancel clears its timers and stale results cannot
revive it or affect a retry. The comparison wrapper adds only stage notifications
and an abort check before candidate creation. All 239 accepted G dependency
identities remain exact. Renderer, geometry, models, resolution and inference
remain unchanged; no experiment is promoted.

Strict types and all 183 efficiency checks pass, including 16 new timer and
renderer-orchestration checks. Required npm test again passes 172 units and all
21 G/reference/long-hair browser cases. Both new production startup browser cases
pass: a delayed real module response exposes a pre-frame report and permits
cancel/late-result rejection/retry; a blocked real GLB response reaches the actual
60-second deadline, releases streams and permits a new masked G session. No
production timers or clocks are accelerated by these tests.

The updated public build also passes the independent network boundary check and
the real Tom Ford/multiclass G-to-Q measurement/partial-save/held-guard/background
cleanup regression. Its runtime release fingerprint starts `274f30e02554`.

Desktop WebKit 26.6 successfully imports the published renderer module and
creates/disposes its four render contexts. That Windows port lacks the camera,
worker-canvas and video-callback capabilities needed to reproduce the complete
iPhone pipeline, so it is not an iPhone startup pass. Probe scripts/receipts and
test logs are preserved in ignored QA/recovery output. The phone's actual stalled
stage and root cause remain unconfirmed until its startup report is collected.
These changes provide bounded recovery and diagnosis, not proof of a phone fix.
Timers cannot forcibly interrupt a synchronously blocked browser/GPU event loop.

### iPhone first-publication capture finding — September 11

The owner's private `ar-startup-2026-09-11T14-11-15.304Z.json`, from runtime
fingerprint `274f30e02554`, now locates the stall after successful setup. Camera
settings are 720×1280 at 30 fps; camera-ready is 2,569 ms, both renderers are ready
at 3,683 ms, face GPU at 4,079 ms and hair GPU at 4,147 ms on the page clock.
First publication times out after 30,001.5 ms, without a published AR image, hair
error or reported GPU fallback. Thus setup takes about 1.6 seconds after camera
readiness; the report does not measure delivered camera FPS or sustained AR speed.
The original private report remains outside the repository and is not modified.

The lab's capture factory reads `video.currentTime`, draws into its owned canvas,
then discards the snapshot unless a second `currentTime` read is exactly equal.
That assumption is incompatible with current upstream WebKit's MediaStream path:
[HTMLMediaElement::currentMediaTime](https://raw.githubusercontent.com/WebKit/WebKit/main/Source/WebCore/html/HTMLMediaElement.cpp#:~:text=MediaTime%20HTMLMediaElement%3A%3AcurrentMediaTime%28%29%20const)
queries the player on every read while playing, and
[MediaPlayerPrivateMediaStreamAVFObjC::currentTime](https://raw.githubusercontent.com/WebKit/WebKit/main/Source/WebCore/platform/graphics/avfoundation/objc/MediaPlayerPrivateMediaStreamAVFObjC.mm#:~:text=MediaTime%20MediaPlayerPrivateMediaStreamAVFObjC%3A%3AcurrentTime%28%29%20const)
returns the current monotonic time minus the stream start. Playback time can
therefore change without a different decoded image. Rejected snapshots never
start inference, while advancing callback observations refresh the separate
six-second camera watchdog. This explains the observed failure pattern, but
upstream source does not identify the exact WebKit revision on this phone.

The correction stays in the efficiency lab: rVFC `presentedFrames` supplies
duplicate/admission identity and `mediaTime` supplies callback frame-timestamp
telemetry, including a possible zero timestamp for live streams. No second
playback-clock read vetoes the synchronous snapshot. Source hash, face/hair
bitmaps, detections, pose, mask and rendering retain the same owned pixels and
session. The rAF fallback samples playback time once for best-effort duplicate
suppression, leaving camera-frame counters unavailable. Allowlisted scalar
startup counters are frozen before timeout cleanup so a further failure can
separate capture rejection from bitmap, inference and preparation waits without
exporting images, identities, detections or masks.

The change preserves the 239 accepted G dependencies, model/resolution policy,
geometry and final nose/front safeguards; no optimization is promoted. An
independent byte audit verifies all 239 against their exact pinned Git blobs;
231 also match the recorded original local bytes. The other eight are the
documented LF checkout representation. The historical raw-byte verifier still
rejects that representation, as expected; it and its manifest remain unchanged.
No normalization was used to accept an arbitrary source change.

Both regressions reproduce the original defect: a Node load hook runs the old
committed pump against an advancing getter and observes zero captures; the old
published bundle with real workers and a 720×1280 synthetic camera reaches the
same first-AR 30-second timeout. The latter's failure cleanup initially tried to
click a hidden Stop button; the retained report already records the target
timeout. The test now checks visibility before cleanup without changing any
application deadline. Logs, report and trace retain the failed attempt.

Required npm test passes all 172 unit checks and 21 G/reference/long-hair browser
cases. Strict efficiency types and all 189 checks pass. The pump tests check
frame-counter deduplication with zero or unavailable PTS, rAF's single clock
sample, full-resolution source/face/hair/hash/mask pairing, and revoked callbacks
or inference after restart. Scalar startup diagnostics retain their own copy and
the initial session selections, excluding arbitrary image/identity fields.

The updated production bundle passes all four 720×1280 portrait combinations,
both glasses × both hair models, under the advancing-clock simulation. Each
starts and restarts real workers, then compares exact held source/detection/mask,
rendered images and geometry across G/Q/R/S/T with zero nose/front/background/
outside-arm guard changes. These are static synthetic portrait tests. The prior
56-case down/up/both-yaw matched rendering evidence is retained; no new wearer
motion or physical-phone visual acceptance is implied. The owner must retry on
the phone to confirm its startup; mobile smoothness remains unmeasured.

All eight final production mobile browser cases pass (three minutes): the four
portrait cases above, unchanged real module/60-second GLB timeout-and-retry
checks, the local-network boundary check, and Tom Ford/multiclass G-to-Q timing,
partial video/telemetry save, exact held safeguards and background cleanup.
Receipts are in ignored `test-results/mobile-2026-09-11T14-49-11.528Z/` and the
`logs/clock-*` files. Runtime fingerprint is `9df9d0b9c9bf`; all 21 staged public
files match their exact generated size/hash. Every changed path is inside
`ar_v4`; this fix changes no parent Python application or deployment settings.
