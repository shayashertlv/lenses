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
