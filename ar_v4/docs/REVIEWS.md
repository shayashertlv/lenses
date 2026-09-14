## perfecto_17fps: CPU face landmarker becomes the G default — September 14, 2026

After the verification below, the owner approved implementing the CPU face delegate in the
main pipeline and committing it as **perfecto_17fps**. Change: `experiments/speed-lab/experiments.ts`
now defaults `faceDelegate` to `'CPU'` (`DEFAULT_FACE_DELEGATE`); the G page constructs its
`DetectorClient` with that forced delegate, so the landmarker no longer competes with hair,
beauty and readbacks on Chrome's single GPU-process thread. `?face=gpu` restores the previous
GPU-then-CPU order for comparison; the on-page "speed experiment" note now describes only
deviations from the accepted defaults. Hair inference, the renderer, image/pose/mask
ownership, resolution and every nose/front, protected, outside-editable and background
safeguard are unchanged. The previously opt-in files from the September 13 review
(`experiments.ts`, live-main/live-pump, native and temple renderers, the detector's `delegate`
option, the branch renderer's transmission-scale option, the measurement harness and
`docs/G_FPS_REVIEW_2026-09-13.md`) are committed with it.

Evidence: the owner's four mains sessions (GPU 15.49 / 16.41 fps, CPU 17.94 / 17.86 fps; age p95
128–133 vs 116–117 ms; 100 % tracked and masked) and the owner's live approval. This is a
pixel-changing runtime option: landmarks differ from the GPU delegate at float-noise level
(0.005 px maximum on the fixture in the server-AR parity harness), so glasses placement may
shift sub-pixel. The 56-case matched frozen-input comparison was not rerun for this delegate;
the owner accepted on live use. Phone behaviour with the CPU delegate is unmeasured (the
served WASM has SIMD but no threads, so the delegate is single-threaded there too).

Verification: `npm run check` passes; 65 speed-lab unit tests including three new
`experiments.test.ts` cases pass; the full `npm test` run with the new default passed strict
types, all unit checks, all 11 G production browser cases and 8/9 reference cases, with the
already documented `test_` trial-model selector expectation at `tests/browser/mirror.spec.ts:648`
still failing (unrelated, exit 1 as in the previous entries); the long-hair stage passed 2/2
separately earlier today on the same tree. The commit contains only the perfecto_17fps files:
concurrent local Modeling Auto edits (`external-eyewear.ts`, its hooks in `live-main.ts`, a
README paragraph, the reference snapshot and test config edits) and the local `test_` model work
were deliberately left uncommitted. The published mobile site and the frozen efficiency/fps
release copies are unaffected until they are rebuilt from this tree.

## Owner verification of `?face=cpu` on mains with the real camera — September 14, 2026

The Sep 13 review's first recommendation (face landmarker on the CPU delegate, opt-in
`?face=cpu` on the G page) was verified by the owner today: four fresh 75-second sessions on
the local dev page (`experiments/speed-lab/live.html`, Chrome, Arc 140T laptop on mains,
real 1280x720 camera, mixed front/nose, down/up, yaw and hair motion, Amber Horizon /
hair-only, hair on), order GPU / CPU / GPU / CPU, "Download timings only" exports with the
first 15 s dropped (`.recovery/server-ar-2026-09-14/output/face-cpu-owner-2026-09-14/`,
stats script `qa/face-cpu-stats.mjs` there):

| Session | Face delegate | fps | Age median / p95 ms | Gaps > 100 ms | Face inference median | Prepare median | Tracked / masked |
|---|---|---|---|---|---|---|---|
| 1 | GPU | 15.49 | 116 / 133 | 21 | 26.4 ms | 50.0 ms | 100 % / 100 % |
| 2 | CPU | 17.94 | 99 / 116 | 0 | 13.1 ms | 40.4 ms | 100 % / 100 % |
| 3 | GPU | 16.41 | 108 / 128 | 12 | 24.0 ms | 45.7 ms | 100 % / 100 % |
| 4 | CPU | 17.86 | 99 / 117 | 0 | 13.7 ms | 40.5 ms | 100 % / 100 % |

CPU delegate: **+12 % median fps (17.90 vs 15.95), age p95 −11 %, zero >100 ms gaps** in both
sessions, with the render prepare stage also shortening by ~8 ms because the landmarker no
longer competes for the single GPU-process thread. GPU-delegate drift over the run (+6 %) is
smaller than the effect. This is a pixel-changing runtime option (landmarks differ at float
noise; the server-AR parity harness measured a 0.005 px maximum landmark delta on the face-a
fixture), so promotion still needs the matched frozen-input comparison and the owner's
held-frame visual acceptance; no default was changed. Phone behaviour is unmeasured.

## Server AR vs local G: local G retained — September 14, 2026

The owner asked for a measured comparison between the browser pipeline and a pipeline whose
AR runs entirely on a server. The isolated experiment lives in `experiments/server-ar/`
(README, DEPLOY, DECISION, RESULTS there). Mode A is the published G control (FPS build
`bca8a88b…` source list copied verbatim into `g-control/`, 208/208 files hash-verified,
including the iPhone capture correction). Mode B uploads camera video over WebRTC to a
Node supervisor that runs the same frozen G pipeline in an isolated headless Chromium page
per session and returns the composited frame as video; mode C returns the video without AR.
Frame identity is a 500x10 px marker strip carried in the pixels (client and server sequence,
CRC-8) and decoded from the displayed video in rVFC; age is client-clock only; the server
hashes the exact decoded RGBA its pump owned. Railway has no GPU and no CLI/token exists here;
the served build was verified byte-exact against origin/main and the release worktree.

Decision evidence is the owner's real-camera desktop run on mains (Chrome, Arc 140T, mixed
front/nose, down/up, yaw and hair motion, Amber/hair-only, A/B/B/A/B/A/A/B, 15 s + 60 s):
local G **15.99/14.94/14.94/15.47 fps** with age p95 **141–157 ms**; server AR on a
GPU-configured server over loopback (zero network distance, optimistic bound)
**9.94/9.84/10.63/11.57 fps** with age p95 **416–482 ms**; coverage 100 % masked in both,
0 marker/binding failures, 6 dropped server frames. Server stage total 154–187 ms per frame is
the limiting stage; transport-only returns ~22–25 fps at ~100–130 ms. The CPU/SwiftShader
configuration a Railway service would provide gave 0.3–0.6 fps with 3–10 s age. Four
concurrent GPU sessions degrade to ~1 fps each; admission at the budget is enforced.
Renderer parity on identical pixels keeps every safeguard at zero changed pixels on both
sides while SwiftShader changes 5.5 % of glasses-region pixels. Lifecycle: 11/11 executed
cases pass (permission denial/delay, cancellation, stop/restart, mode change, background,
server close, worker crash, isolation, overload); packet-loss cases not run by decision.

Recommendation: **local G**. Owner decisions recorded: no Railway deployment, no GPU host,
no impairment tool; remaining synthetic reruns stopped after the real-camera result; the
experiment was then **retired by the owner** and moved intact to the ignored
`.recovery/server-ar-2026-09-14/` (source, harnesses, reports, evidence; the temporary
`*:server-ar` npm scripts were removed again, so `package.json` is unchanged). Not measured:
physical iPhone Safari (the owner reports G averaging about 21 fps on the phone; owner
statement, no report file), internet distance, optical movement-to-display latency,
real-camera Tom Ford/multiclass/hair-off (synthetic versions in the archived RESULTS.md agree
in direction). G, accepted checkpoints, the parent application and deployment are unchanged.

## Test 4 follow-up: cheaper composition, flat FPS — September 14, 2026

Report `01154298…` is G / Test 4 / Test 4 / G, build `bca8a88bea5e`,
Amber / hair-only, 1280×720. It follows the desktop tests; this new report has
no device field, so desktop continuity is assumed rather than independently
verified. Pooled G/Test 4 FPS is 12.2246/12.1994 (−0.21%). Frame-age p95 is
178.58/174.105 ms; >100 ms intervals are 32/734 versus 16/733. All 1,471 rows
are tracked, masked and completed through asynchronous PBO readback, without
reported fallback. Stored summaries/session/serial/timestamp checks pass.

Composition median falls 6.735→4.195 ms (−37.7%, about 2.54 ms) with full
921,600-pixel reference/final audit visits on all 735 Test 4 frames. Its RGB
write counter is positive on 161 frames (max 437 pixels), with no comparable
G count or visual output. Statistics optimization is off. The per-segment
FPS sequence is 11.43→11.92→12.48→13.02; G itself improves 13.9% over the run.
The observed CPU saving does not establish a final FPS gain, and comparison
with a separate earlier Test 6 run cannot isolate the statistics contribution.

Retain G. Prioritize the existing G readback diagnostic and hair extraction /
GPU-to-CPU retrieval timing before further changes. Existing wall times include
scheduling and may overlap; no GPU-time or thermal-cause inference. Visual,
both-model/matched-angle and physical-iPhone acceptance remain pending.
Exact input and calculations: `.recovery/fps-desktop-reports-2026-09-14/`
`test4-analysis.md` and `test4-analysis.json`. No code/deployment change.

## Owner FPS reports: both desktop — September 14, 2026

The owner explicitly corrected the device classification: both uploaded runs
were on desktop. No iPhone inference should be made from these files; neither
JSON contains a device-type field. Both use public build `bca8a88bea5e`, Amber
Horizon / hair-only, 1280×720, with four fresh 30-second measured segments.

`452353ea…` compares G / Test 6 / Test 6 / G: pooled within-session FPS is
12.6607 / 13.1320 (+3.72%); pooled frame-age p95 is 173.535 / 163.885 ms.
`6de596f7…` compares G / Test 3 / Test 3 / G: 13.6047 / 13.1124 FPS (-3.62%);
frame-age p95 is 170.150 / 166.785 ms. All 3,162 rows are tracked and masked,
with GPU face/hair delegates and completed asynchronous readback. Stored
summaries recompute exactly; serials are contiguous, sessions/documents unique
and within-session capture/publication/video counters monotonic. No fallback.

G itself changed +13.5% between its first/last windows in the Test 6 run and
-19.0% in the Test 3 run. Those changes exceed the candidate differences; no
reliable causal win/loss or promotion is established. Test 6 remains promising:
composition median 6.31→3.87 ms, both changes used on all 790 candidate frames,
full 921,600-pixel composition audits retained. Its intermediate RGB-write
counter is positive on only 229/790 frames (max 260 pixels); G lacks comparable
pixel counts, and no images/pose/guard results are in these timing exports.
Do not interpret complete mask availability as matched visual quality.

Retain G. Next priority is isolating Test 4's CPU composition change and repeating
Test 6 under comparable desktop conditions with both glasses/hair models and
matched front/nose, down/up and both-yaw motion. Physical-iPhone evidence remains
pending. Test 3 removed its intended query/hash work but did not show a net FPS
gain here. Existing extraction/readback wall times merit separate diagnosis;
they include scheduling and cannot establish GPU execution cost by themselves.

Byte-exact originals, hashes, all per-segment/group calculations and limitations
are stored locally in `.recovery/fps-desktop-reports-2026-09-14/`. No runtime or
public deployment changed.

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

## Imported preview live model size slider - September 14, 2026

The requested size control is available in `experiments/zuri-preview/` at
`http://127.0.0.1:8095/`. Model size ranges from 100% to 150% in 1% steps;
Reset restores the supplied/original width. Changes apply during live preview
without a camera restart. The percentage carries across model changes and fresh
camera sessions; page reload defaults to 100%. Desktop keeps the control in the
glasses card. At 720px and below the same control moves directly below the mirror.
Hold freezes the last published size, disables adjustment, and retains that size
through the full-mask upgrade and all eight cached comparisons.

Separate Vite adapters scale only the eyewear matrix around the existing
`[0, 3.271027, 6.691763]` cm bridge/front anchor. Native beauty, temple branches,
optical protection and continuity use the same scaled pose. Face/head and nasal
geometry keep their original transforms. Immutable size/revision metadata follows
owned detection clones through a WeakMap without changing detection JSON or its
hash. Each comparison renderer owns its requested, pending and published size;
input during an asynchronous preparation affects the next pair. The 100% path
returns the original matrix without arithmetic. Original model bytes, widths,
accepted source files, normal launches and deployment configuration are preserved.

Validation: isolated strict TypeScript, the final production build, and 11 focused
geometry/ownership tests pass. The 53 analytical cases plus five reset cases cover
all five models at 100% and 150% in front, down/up and both yaw poses, plus 125%
front checks for both originals and Oakley. All 25 original-size G, hair and native
images exactly match the frozen pre-slider receipt; all five asset hashes match.
Enlarged G/Test 2 outputs match, the bridge pivot error is at most 3.56e-15 cm,
face/nose geometry remains exact, and every existing protection check passes.
Sixteen front/yaw images were visually inspected without an unexpected scaling
defect. Receipts: `experiments/zuri-preview/output/size-slider/`:
`qa-size-poses-2026-09-14T08-58-36-107Z/report.json` and
`qa-size-live-2026-09-14T09-01-00-426Z/report.json`.

Production live tests pass with both actual local hair workers. A request inside
a real pending GPU fence publishes the already-owned 125% frame before a 150%
frame; a later request before Hold cannot change the held 150% outputs. All eight
held image/detection/mask/matrix pairs agree, including protection and continuity.
Reset, disabled held input, cancellation, asset failure/recovery, camera restart
and size persistence pass. The same control preserves state when moved between
desktop and 390x844 mobile layouts. Scrolling the mirror into view shows both it
and the slider without horizontal overflow. An initial QA-only assumption that
the control must fit in the page's initial fold was corrected to this visibility
contract; rendering checks were unchanged.

Literal `npm test`: 186 unit tests, all 11 G browser tests and 8 reference browser
tests pass. The known unrelated `test_` selector expectation at
`tests/browser/mirror.spec.ts:660` still fails. The separately run long-hair tests
passed hair-only; multiclass initially timed out waiting for a live mask while
showing fallback. Its unchanged isolated retry passed in 23.7 seconds. Logs and
the initial failure trace are retained under `output/size-slider/`.

Evidence uses canonical poses and a repeated generic camera fixture. Matched
wearer motion, personal fit, owner visual acceptance of enlarged frames, and
sustained mobile smoothness remain unmeasured.

## Next CPU FPS tests: local preview candidate - September 14, 2026

The owner's next tests are implemented separately in `experiments/fps-candidate/`:
Test 4 reduces CPU pixel-composition work, Test 5 reuses per-publication statistics
and caches human percentile/stage readouts for 500 ms, and Test 6 combines them.
FPS/coverage counts still update on every completed frame; every frame is sampled,
with the existing 4,096-row limit and complete measured comparison segments.
The new modes keep original frame hashing and GL query behavior. All mask checks,
full-frame background/final audits, final nose/front copies and continuity remain.
The 63 previous candidate source files are preserved in the ignored round1-final
archive, and Tests 1–3 remain selectable with the new CPU paths disabled.

The final G/4/5/6/G/6/5/4/G hardware production run gave pooled completed AR FPS
**9.28 / 9.72 / 9.05 / 10.20**, and capture-to-publication age p95
**236.2 / 223.7 / 231.7 / 216.9 ms**. Test 6 is a promising local-preview candidate
at roughly **9.9%** above pooled G here, not a confirmed general improvement:
its two runs were 9.94/10.45 FPS, versus G 9.77/8.72/9.35. Test 5 alone did not
improve FPS, so the extra combined gain cannot be attributed to it independently.
The actual median composition stage fell from G 14.00 ms to Test 4 6.63 ms and
Test 6 6.34 ms; the independent final audit still visited every output pixel.

Timing used the preserved generated auburn portrait at natural 1280x853,
Amber Horizon/hair-only, fresh face/hair GPU inference, 5 s warmup/20 s measurement
per session, hardware Chromium/ANGLE D3D11 on Intel Arc 140T. All 1,712 rows had
tracking, matching masks, visible hair edits and actual PBO use; no rows were
filtered for missing work and no readback fallback occurred. Balanced power and
AC were reported before timing; temperature and other application load were not
controlled. This differs from the first round's zero-edit 1280x720 fixture.
No moving-camera, mobile/thermal FPS or new owner visual acceptance is supplied.

Validation: 72 focused tests, strict TypeScript and isolated build passed;
56 frozen generated/recorded cases matched exact pixels/geometry with both glasses,
both hair models, down/up/both yaw and all safeguards. Sixteen renderer control
groups and six production lifecycle configurations passed. Hardware matched cases
had no fallback; recorded SwiftShader cases used the unchanged bounded timeout
fallback in G 21/24 and candidate 23/24, supplying correctness evidence only.
A QA-only status-read race was fixed by capturing ready diagnostics atomically;
its failed receipt remains. A rejected unaligned byte-path telemetry edge was also
corrected before the final build; optimized-work counters stay zero when unused.

Required `npm test` again passed 186 unit tests and 11 G browser cases, retaining
the existing preserved `test_` selector failure (8/9, overall exit 1). The separately
run preserved hair suite passed 2/2. No checks/deadlines were relaxed. All 249 G
files and 302 frozen inputs remain unchanged. Final production fingerprint:
`24ef9fec6e664a84096027b309807652001ba94bb357cec08dc9f22a1d521216`.
Receipts and run commands are linked from the experiment README. The local
comparison is available with Test 6 selected; G, prior accepted references and
deployment are unchanged. No promotion occurred.

## Isolated FPS candidate implementation and result - September 14, 2026

The owner requested a test and a preview if promising. The local experiment in
`experiments/fps-candidate/` provides original G, fewer GL queries, explicit live
frame IDs, and both changes together. The normal launch and all 249 original
source/assets/config files match this task's starting bytes. Accepted entry
points, private recordings, earlier recovery material and deployment remain.

The copied PBO implementation validates its private state once with a conservative
fallback, and keeps allocation/read failures, context loss, generation checks,
cancellation and bounded fence waits. Revision 2 checks the first read before any
intervening clean-camera draw in a two-read frame; only the final submission/fence
checks are coalesced into checked CPU retrieval. This avoids relying on GL errors
surviving Three.js shader initialization. Warm one-read G frames use zero state
queries and one error query, with failures rejected before publication. Explicit
session/sequence source and detection IDs replace live content hashes only in
identity modes. Hold computes genuine hashes from retained image/detection bytes.
Exact source/pose/mask ownership and all final pixel safeguards remain.

The production comparison page runs G/test/test/G across fresh sessions, verifies
settings/session stability, retains all measured rows and exports compact local
timing reports. Production builds include a source fingerprint. Final measured
build: `b8f7be463909ec2d9b3529e7cfb7684aa9cac7ff96fefd8403a1afd90a73ce88`.

No reliable FPS win was established. The final eight-session G/Q/G/C/C/G/Q/G run
gave pooled unique-publication FPS G **10.78**, queries **10.36**, combined **10.90**;
capture-to-publication age p95 was **216.8 / 225.5 / 208.1 ms** respectively.
Combined's roughly 1.1% pooled gain is smaller than the baseline drift
(G sessions 12.20, 10.52, 10.36, 10.05 FPS). The earlier balanced revision also
failed to establish a gain; identity alone was 10.17 versus G 10.25 FPS. Results
do not support the attached review's suggested large FPS increase from these
changes. See the experiment README and ignored `qa/output/timing-summary.json`.

Timing conditions: hardware Chromium/ANGLE D3D11, Intel Arc 140T, Balanced power
plan and AC reported at start; temperature unmeasured. A static canvas camera
shares the application thread, 1280x720, Amber Horizon/hair-only, GPU delegates,
five-second warmup and twenty-second measurement per session. All 1,712 final
measured frames tracked, carried masks and used PBOs; all 850 query/combined
frames used the revised query path and deferred check. The fixture produced zero
visible hair replacement pixels. These measurements establish neither moving
wearer/mobile performance nor physical display scanout. The cause of the observed
run-order drift is unknown; do not attribute it to thermals without measurement.

Validation: 47 focused identity/PBO/study tests, strict TypeScript and isolated
production build passed. All 56 frozen matched cases passed exact pixels, geometry,
nose/front/outside-editable checks, both glasses/hair models and down/up/both yaw.
Hardware exercised PBO in 32/32; recorded software cases used PBO in 17/24 with
seven explicitly checked bounded fallbacks. Sixteen renderer lifecycle/control
groups and all four production glasses/hair combinations passed failure cleanup,
startup cancellation, live pairing, true held hashes, eight held profiles, resume,
no-face clearing and shutdown. All 302 frozen inputs remain byte-exact.

Required `npm test` passed 186 unit tests and all 11 G browser cases, but its
preserved-reference suite remains 8/9 because of the already documented `test_`
model-selector expectation at `tests/browser/mirror.spec.ts:648`; overall command
exit was 1. The otherwise skipped preserved hair suite passed separately (2/2).
No checks or timeouts were relaxed. This candidate remains local and unpromoted;
no owner-camera session or new visual acceptance occurred. Its timing evidence
does not yet justify presenting it as an FPS upgrade.

## Assessment of the attached displayed-FPS review - September 14, 2026

Scope: attached review, current source and existing experiment receipts/docs;
recommendations only. No new profiling, performance benchmark or visual test was
run, and no application behavior, accepted default or deployment was changed.

The main-thread bottleneck is plausible for the reported synthetic laptop run,
but does not exclude GPU/model contention: blocking graphics calls and worker
reply delays contribute to the observed occupancy. Source hashing uses an async
digest; its elapsed timer includes scheduling, while its input copy is synchronous.
Stage medians overlap and cannot be added into a device-time budget. The attached
query-removal plus identity patch combines two changes and its clear reported win
is hair-off; it does not establish a hair-on gain or a 25-30 FPS outcome. Earlier
claims that byte-exact optimization is exhausted are also not established.

Recommended implementation order, as separate candidates against G:

1. Reduce PBO state queries with explicitly owned/restored GL state, and remove
   redundant error queries from fence polling. Preserve failed-read rejection,
   allocation/error handling, context loss, bounded waits and cancellation. G's
   normal beauty path has twelve state queries plus a variable number of error
   queries, not a universal eighteen GPU stalls. Blind deletion can admit stale
   buffer bytes after failed reads. See speed-lab/native/pbo-readback.ts:44-126
   and the [WebGL guidance](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices#avoid_blocking_api_calls_in_production).
2. Introduce an explicit owned live-frame ID across source, detection, hair and
   publication, retaining session/generation/sequence validation. Compute genuine
   content hashes for Hold/export from retained bytes; do not put a fabricated ID
   in SHA256 fields. This requires protocol and Hold changes. Current CPU compose
   still needs source RGBA every frame, so Hold-only readback is not yet possible.
3. Reduce equivalent CPU finish/bookkeeping work while retaining every final
   safeguard: safe buffer ownership, equivalent word comparisons, bounded edit
   scans, cached/throttled human summaries. Keep all per-frame telemetry. Existing
   Q-T experiments already cover redundant publication, branch row reads, branch
   lens omission and summary throttling; their phone run did not establish a win.
4. For a larger improvement, investigate GPU display/composition including the
   nonzero temple path and synchronization costs. Existing Test 3 reduced renderer
   download bytes by 87.5% but reported 10.63/10.65 Test 2/Test 3 FPS and worse p95
   age. Repeating that implementation is insufficient. Keep live nose/front and
   outside-editable protection, including continuity. A branch vertex shader can
   remove deformation uploads, but must reproduce normals/tangents and CPU bounds;
   fixed front vertices alone do not preserve final front pixels. Do not replace
   final safeguards with sampled/Hold-only audits.

Capture modernization must snapshot one immutable VideoFrame/ImageBitmap and
derive every consumer from it. A live video texture sampled after inference can
show a newer camera image than its pose. Color conversion, resize and lifetime
need matched verification. Reassess the existing CPU-face option after reducing
main-thread contention; neither delegate is a proven universal winner. Lazy Test 2
creation primarily improves startup. A delayed presentation queue adds latency
without producing more distinct AR frames; resolution/model changes remain
separate quality tradeoffs.

Judge candidates by completed unique AR publications, mask/tracking coverage,
interval and capture-age p95, stalls and fallbacks in repeated interleaved runs
with power/load recorded. Canvas submission is not physical scanout. Require both
accepted glasses, both hair models, down/up/both yaw and nose/front checks, followed
by moving-wearer acceptance; no new physical camera/mobile evidence is supplied by
this assessment. Preserve G and the previously accepted references throughout.

## Local Oakley OO9208 38 920844 addition - September 14, 2026

The owner requested adding `Oakley_OO9208_38_920844.blend` with a 138 mm full
front width to the existing separate preview in `experiments/zuri-preview/`.
Oakley becomes the selected model with G Combined; Miu Miu, Zuri, Amber Horizon
and Tom Ford remain available with their existing profiles and assets. Accepted
renderers/references and default launches, the parent application/deployment,
recordings and recovery archives remain outside this import.

Source SHA-256 is `9c221465b726c221357f748378682661f06daa6ffe3a50a05a8ecafc1a0d2eef`.
Blender opened it with embedded execution disabled; the source is unchanged.
Its 3,127,281 triangles are separated into nylon, rubber, emblems and one optical
shield before independent reduction. Per-material 2048px bakes retain the black
finishes, silver emblems and green/violet shield. The shield samples the source's
frontal incoming direction and uses one physical browser material. Matched
Blender renders retain frontal appearance, but yaw color changes and the mixed
mirror/transmission response are approximate. The prominent dark U-shaped bands
within the shield and bright upper-edge highlights are also visible in the
supplied Blender source renders and remain visible in the AR fixture. No owner
visual acceptance is inferred from preservation. No G shader was retuned.

This curved front cannot use the previous 20 mm width band: at provisional
145 mm overall scale that band contains only 101.87 mm of the front. The reviewed
45 mm band includes the shield's 37.1 mm rear extent plus adjacent outer housings,
while excluding the widest posterior rubber at about 65 mm. Uniform scaling
then verifies 137.999997 mm across the selected front. The housing allowance is
a stated preview convention, not a measured hinge boundary. The central bridge
minimum belongs to nylon, with optical/rubber vertices above it. Its attachment
is `[0, 3.124180165895492, 6.691763]` cm with the existing -110 mm rear cutoff.
This establishes model scale and preview placement, not wearer fit.

An index-only correction removes 6,505 opaque triangles wholly behind -120 mm,
retaining a 10 mm hidden band. Invisible inward hook vertices had extended the
protected optical bounds over the arms. All original binary attributes/images,
material definitions and the optical primitive remain intact. The final GLB has
243,673 referenced triangles, occupies 19,244,424 bytes and has SHA-256
`6580b7f77ec18315173098ae7313651c4a5c66fe421530eaae8ceac4536853be`.
The generic provenance finalizer verifies the source, unscaled and untrimmed
copies, final asset, receipt chain and triangle counts.

Strict types, the isolated build and all five production asset hash checks pass.
All 25 analytical cases pass at front, down 40 degrees, up 30 degrees and both
40-degree yaws, retaining exact G/Test 2 pixels and nose/front/outside-arm guards.
The four earlier frames' 20 cases preserve their native/G/hair pixels and exact
source/detection pairs against the final Miu receipt. Oakley native renders
match its untrimmed derivative in all five poses. An independent saved-PNG audit
also finds final G pixels equal to the native reference; this is not an old-G
comparison. Controlled all-hair masks change 915/913 Oakley arm pixels at yaw;
front/down/up remain protected. These are synthetic coordinates and masks.

Both production Oakley hair-model cases pass real local inference, exact image,
detection and mask ownership, all eight held outputs, continuity, final
nasal/front/outside-arm/background checks, cancellation, missing-asset recovery,
hair toggles, hold/resume and stop/restart with resource cleanup. The saved AR
still uses the checked-in frontal face fixture, not an owner recording.

Literal `npm test` passed strict/build, 186 unit checks, all 11 G browser cases
and 8/9 reference cases. The existing `test_` selector mismatch at
`tests/browser/mirror.spec.ts:660` still fails on the pinned two-frame reference
page; its failure screenshot/trace are retained without a workaround or retry.
The skipped baseline long-hair stage was run separately and passed 2/2. This is
not a full-suite pass.

Conversion evidence is in ignored `experiments/zuri-preview/output/oakley/`:
`provenance.json`, `source-coordinate-audit.json`, `source-material-parameters.json`,
the separate profile/trim receipts and matched `source-*.png`/`derivative-*.png`,
`qa-poses-2026-09-14T08-28-29-516Z/report.json` with its independent pixel audit,
`qa-smoke-2026-09-14T08-28-57-520Z/receipt.json`, `npm-test.log` and
`reference-known-failure/` and `long-hair-browser.log`. The integration preservation folder records all four
prior complete registry definitions and asset hashes before and after the import.
No new wearer camera/motion, measured fit, physical phone performance or owner
visual acceptance is established by this import.

## Local Miu Miu 0MU 53WV addition - September 14, 2026

The owner requested adding `Miu_Miu_0MU_53WV.blend` to the existing local preview
and supplied a separate 127 mm full front width. `experiments/zuri-preview/`
now opens Miu Miu with G Combined at http://127.0.0.1:8095/ and retains Zuri,
Amber Horizon, Tom Ford and both hair models. The two imported assets have
independent checksum pins, verified before build and before each asset response.
The Zuri finalizer now scopes its registry edit to its named entry. Accepted
renderer/reference sources, default launches, the parent application/deployment,
recordings and recovery archives remain outside this addition.

Miu source SHA-256 is `9b3364ba539d44a910c353ea1ff4cca3facae8bfd5e665b948ddabb0c48f755b`;
Blender opened it with embedded execution disabled and the file is unchanged.
The source has 3,109,097 triangles. The browser derivative preserves four material
meshes, independently reduces the large meshes, and retains the small silicone
pad without reduction. Per-material 2048px base/roughness bakes and resized
authored normals retain the pale gold hardware and tortoiseshell finish. Clear
optics use physical transmission; the source's view-dependent transparency blend
is approximated, with visibly different refraction in matched Blender renders.
The acetate's small transmission weight is approximated as opaque because G
classifies transmitting materials as protected optics. This changes only the
imported material; the renderer's classification and protection stay unchanged.

Uniform scaling verifies a 127.000004 mm front span. Bridge/front attachment is
`[0, 2.64285872442019, 6.691763]` cm with the existing -110 mm rear cutoff. These
are preview conventions, not measured wearer fit. Hidden inward hooks initially
extended the protected optical bounds over the arms. An index-only trim removes
15,763 opaque triangles wholly behind -120 mm, retaining a 10 mm hidden band.
All original binary attributes/images, material definitions and optical
primitives are preserved. The final 232,987-triangle GLB occupies 17,655,828 bytes;
SHA-256 is `a51056d09d89eca8ca8a678f2d006cc6efa1be5c049777537fc32afdb58a0c15`.
Zuri remains exactly `8241fe47cee2777a31c42c04b039ee06fb2e6f37be33f5f13c9412431389c48b`
with its existing 127 mm profile.

Strict types and the isolated production build pass; both production GLBs match
their complete hashes. Twenty analytical cases cover all four frames at front,
down 40 degrees, up 30 degrees and both 40-degree yaws. G and Test 2 pixels match,
geometry is finite, and final nose/front/outside-arm checks pass throughout.
Miu native renders match the untrimmed model at all five poses; an independent
saved-PNG audit also finds final G pixels equal to those native references.
This is not a claim about old-G/new-G equivalence. All 15 existing-frame cases exactly match the prior final Zuri receipt for
native, G and hair-stress pixels and source/detection pairing. Miu's controlled
all-hair stress changes 500/496 arm pixels at the yaws; front/up/down remain
protected. These generated coordinates and masks are not wearer recordings,
measured fit, real motion evidence or physical phone validation. No owner visual
acceptance or baseline promotion is inferred.

Both production Miu hair-model cases pass real local inference, exact held
image/detection/mask ownership and pixel equality across all eight algorithms,
continuity, nasal/front/outside-arm/background guards, hair toggles,
hold/resume/stop/restart and resource cleanup. Explicit startup cancellation and
an injected missing-model response both recover cleanly. The smoke fixture uses
a checked-in frontal face image, not the owner's camera or personal scan.

Required `npm test` passed strict/build and 186 unit checks, then 10/11 G browser
cases. Its overlap lifecycle case timed out waiting for a test-injected prefetch
observation before the stop step; the unchanged case passed an isolated retry
after Blender conversion ended. The original failure/trace is retained and its
cause is not established. The skipped reference stage was run separately: 8/9
passed, with the existing `test_` selector mismatch at
`tests/browser/mirror.spec.ts:660` still failing on the pinned two-frame page.
The separate long-hair stage passed 2/2. This is not a full-suite pass and the
unrelated reference mismatch was not changed for this import.

Current evidence lives in ignored `experiments/zuri-preview/output/miu-miu/`:
`provenance.json`, the separate export/profile/trim receipts, matched
`source-*.png`/`derivative-*.png`, and
`qa-poses-2026-09-14T06-08-22-572Z/report.json` with its independent pixel audit,
and `qa-smoke-2026-09-14T06-09-35-232Z/receipt.json`. Original asset hashes are
recorded in `original-assets-preserved.json`; baseline execution and failure
evidence are retained in `npm-test.log`, `initial-overlap-failure/`,
`overlap-retry/`, `reference-browser.log` and `long-hair-browser.log`.

## Local Ray-Ban RB4455 Zuri preview - September 13, 2026

The owner requested a preview of `Ray-Ban_RB4455_Zuri.blend` and supplied a
127 mm full front width. The separate preview is at http://127.0.0.1:8095/;
its source/configuration are in `experiments/zuri-preview/`. G Combined and
Zuri are selected initially; both original frames and both hair models remain
available. No accepted renderer/reference files, default entry points, parent
application, deployment, recordings or recovery archives were changed.

The source SHA-256 remains `492a2eb8384069415aaf4d912bb373507d0c2403b7cf67c3587e301286ca8dcb`.
It was read with Blender auto-execution disabled. Separate per-material 2048px
base/surface bakes and independent frame/lens reduction correct texture padding
contamination seen in the first import. Matched Blender source/derivative
lighting checks retain the dark lens edges, tint, logo and tortoiseshell finish.
The browser materials approximate mixed Blender shaders; this is not exact shader
parity. Source geometry starts at 3,131,854 triangles; the final referenced model
has 243,309 triangles and occupies 21,124,024 bytes. Its SHA-256 is
`8241fe47cee2777a31c42c04b039ee06fb2e6f37be33f5f13c9412431389c48b`.

The model is uniformly scaled to the supplied front width and aligned using
the existing bridge/front anchors. The scoped Vite adapter adds only this model
to the selector, explicit image/geometry identity lists and pinned continuity
asset registry. All hash/pairing, cross-section, nasal/front, background and
outside-arm checks remain active. Inward rear hooks initially broadened optical
protection over the whole visible arm. The derivative removes only 7,236 opaque
triangles wholly behind -120 mm, retaining a 10 mm hidden band behind the existing
-110 mm shader cutoff. Original binary attributes/images, materials and optical
primitives remain exact through this index-only trim.

Final analytical checks cover all three frames at front, down 40 degrees, up
30 degrees and both 40-degree yaws. All 15 native renders exactly match their
untrimmed counterparts; 14/15 G renders also match. Zuri down changes 39 pixels
because the existing rear curve can now affect its permitted arm region.
This change is explicit, not a universal equivalence claim. Final nasal/front
and outside-arm checks remain exact. A controlled all-hair mask removes 976/966
Zuri pixels at the two yaws and 12 when down. These generated coordinates and
stress masks are not wearer recordings or inferred hair depth. The complete
receipt combines 13 cases and two resumed cases after a documented development
page reload; earlier failed attempts are retained in ignored local output.

Strict types and the isolated production build pass. Required `npm test` passed
186 unit checks and 11 G browser cases, then 8/9 reference browser cases; the
existing unrelated `test_` dropdown test at `tests/browser/mirror.spec.ts:660`
fails because the pinned reference page offers only its two accepted frames.
The skipped long-hair browser stage was run separately and passed 2/2. This
failure was not fixed or relabelled as a full-suite pass. Both final production
Zuri hair-model cases pass real local inference, exact held image/pose/mask and
pixel equality across all eight algorithms, continuity, all four safeguards,
hair toggles, hold/resume, stop/restart and resource cleanup, with no page errors
or missing requests. The frontal fixture has no eligible changed hair pixels;
the separate yaw stress controls establish visible arm masking.

Evidence: `experiments/zuri-preview/output/provenance.json`,
`hidden-hook-trim.json`, matched `source-*.png`/`material-candidate/candidate-*.png`,
`qa-poses-2026-09-13T16-34-09-470Z/report.json` and its independent pixel audit,
`qa-smoke-2026-09-13T16-33-47-630Z/receipt.json`, `npm-test.log`, and
`long-hair-browser.log`. No new physical wearer/motion/mobile
evidence or owner visual acceptance has been established. This import remains
a separate preview; G and the accepted references are not promoted or retuned.

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

## Supplied test_ model in the original testing ground

The owner identified the testing ground through the earlier “Continue nose
occlusion work” conversation: `http://127.0.0.1:8040/`. Added `test_` there as an
optional trial model. The accepted `EYEWEAR` registry still contains only Amber
and Tom Ford; their definitions/defaults and the accepted temple-sagittal picker
are unchanged. The original page uses a separate combined testing registry.

The local GLB retains 38 meshes, 108,680 triangles, physical crystal/lens volume
transmission, IOR, tint and gold hardware. Its bytes match the staged export;
the supplied Blender file remains byte-for-byte unchanged. The candidate's
central bridge underside and foremost Z determine its individual attachment.
Its width and attachment are visual preview conventions, not measured fit.
Provenance is in `public/models/test_.provenance.json`; detailed inspection and
compatibility evidence remain in `model_studio/data/imports/test_/`.

A rendered test exposed a unit mismatch: AR scene geometry is in centimeters,
while source attenuation distances are meters. An explicit trial-only conversion
now multiplies each shared physical material's finite attenuation distance once
by 100. Thickness is already scaled by Three's model matrix and is unchanged.
Tint, IOR and transmission remain unchanged. Existing assets default to scale 1,
which returns before any traversal/material change. This restores the source
optical path ratio instead of the initial near-black lens rendering.

The owner reported that temple limits did not affect the crystal arms. An
explicit trial-only material policy now uses authored optical/frame roles: the
two lenses remain protected and the crystal arms receive endpoint clipping and
fade. Shared or ambiguous optical materials keep conservative lens protection.
A separate trial visibility controller keeps physical overlay materials, uses
the same pose controls, and distinguishes the actual display target from Three's
internal transmission passes without changing tone mapping. Its front guard
includes all authored front/frame/rim/bridge/pad geometry, beyond the thinner
optical surfaces. Missing optical geometry fails before hooks are installed.

The accepted visibility helper and Amber/Tom definitions remain unchanged. The
shared clip helper's optional classifier retains its exact legacy default.
The accepted rear-drop curvature is still separate and is not promoted to this
trial model. No nose geometry, tracking/pose pairing, original model file or
recorded input is changed. New-model live downward/upward/both-yaw and nose/front
acceptance is still unmeasured.

An isolated rendered comparison covers front, downward/upward pitch and both yaw
controls. The initial broad front mask included proximal temples: its 10/18 yaw
pixel differences were independently traced to temple/hardware surfaces behind
the authored frame, with no front or optical intersection. The original failed
report and broad-region metric are retained. A separate authored-front guard
checks the actual frame/rim/bridge/pads; lens and nose guards are unchanged.
The controller was not altered to freeze an editable arm region. Reproduction
and evidence scope are in `experiments/test-model-temples/README.md`.

The final 15-case comparison passes all 90 checks with no browser/shader errors
(`experiments/test-model-temples/output/2026-09-08T18-23-54.791Z/report.json`).
Both test_ rear endpoints visibly respond; pose visibility acts in down/up and
both yaw controls. Lens, authored-front and nose pixels remain exact across
all five poses. All ten Amber/Tom native before/current comparisons are byte
exact. Geometry, material values, source pairing and input hashes remain
unchanged. Native model, downward and yaw images were visually inspected.

Strict TypeScript, 66 unit tests and the production build pass. The accepted
temple branch's 14 unit tests and strict types also pass, including down/up/both-yaw
controls and unchanged optical geometry for both original models. The full
`npm test` run passed 8/9 browser scenarios; the longer capture/replay case reached
its 60-second total limit during the final camera restart. That one scenario
passed on a targeted rerun in 57.3 seconds with a 90-second total allowance and
unchanged 20-second assertions. All nine scenarios have therefore passed, but
the original full invocation is retained as a timeout failure. The test_ scenario
checks its real asset load, paired capture identity and session cleanup.
Browser and rendered evidence uses synthetic inputs, not a wearer fit or
acceptance claim.

## Original testing-ground performance review

The owner clarified that the uneven page is the original `8040/` testing ground
with test_, not the accepted two-renderer page or separate hair preview. No AR
implementation was changed during this review. The isolated hardware experiment
and live instrumentation are in `experiments/performance-review/README.md`.

On Intel Arc 140T/ANGLE D3D11, a synthetic camera configured for 1280 x 720 at
30 Hz produced 16.2-19.2 submitted app frames/second across three short sessions.
Each model has 60 live and 60 recording measurements after warmup, with real
local face inference and successful stream closure. Median inference was
15.9-17.9 ms; median CPU render submission was 1.57-2.25 ms. Submission does not
measure GPU/display completion. Camera-source timers, the compositor and the
headless browser affect cadence; these results are not physical-camera FPS.
The 32-42 ms median gap between submission and next capture motivates scheduler
profiling, but is not proven entirely removable. Recording observation waits
reached 37-41 ms at p95 while JPEG completion remained on the next-frame path.

A QA-only inactive-visibility candidate passes 120 whole-native-image and
captured-geometry comparisons across all three models and front/down/up/both-yaw.
It hides overlays at exactly zero lateral weights and skips the head mask only
when all three weights are zero. Test_ draw submissions fall from 148 to 77
frontally and 79 on up/down; yaw work remains unchanged. Geometry/material and
source/asset receipts stay exact. Timed samples are noisy, so this is proof of
eliminated invisible work, not a measured total speedup or owner acceptance.

Recommended work is a separate inactive-work candidate, independent recording
completion with synchronous snapshot ownership, and a bounded scheduler trial
that preserves each image/detection pair and cancellation rules. Retain current
resolution, physical materials, antialiasing, pose, temple limits and nasal/front
protections; actual wearer-motion and sustained cadence checks remain required.

The existing dev server also had a stale optimized tracker dependency (HTTP 504)
from an earlier shared-cache diagnostic run. The server was refreshed with the
same routes/source and a private generated cache using
`experiments/performance-review/live-server.mjs`. All three real-worker synthetic
camera sessions then completed. That cache repair is separate from AR performance.

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

## G efficiency research resumed — September 10

Research reviewed HEAD b9142b2 on codex/ar-v4-nose-occlusion and traced G's
scheduling, workers, native rendering, readbacks, composition and startup.
The ranked proposals and comparison protocol are in
[G_EFFICIENCY_RESEARCH.md](G_EFFICIENCY_RESEARCH.md). No runtime, benchmark,
camera session or rendering change was made; existing unrelated work is preserved.

A saved clean-promotion browser control contains 587 G and 469 Test 2 frames
in separate warmed 30-second windows. Raw rows reproduce 19.507/15.583 updates/s,
but G/Test 2 frame-age p95 is 107.620/62.600 ms and publication-interval p95 is
82.945/76.285 ms. This is a static 640x427 synthetic fixture with full masks,
nonzero temples on every frame and no visible hair edits. Its own receipt limits
it to benchmark UI/transition/coverage/export verification. It is a diagnostic
signal, not a physical-camera speed conclusion or contradiction of owner feedback.

G really overlaps workers and uses PBO/fence beauty reads. Nonzero temple work
still starts after beauty retrieval and uses a synchronous read (6.215 ms median
in that control). Hair extraction measured 19.560 ms median but excludes client
validation/hash; its download versus copy cost is unresolved. PBO extraction
also has a long tail. Workers sharing a GPU do not prove simultaneous GPU work.

Next proposals are an isolated asynchronous temple join, redundant-copy removal
under explicit ownership, a bounded prefetch timing trial for freshness, and
selected-renderer-first startup. A new scalar G trace with real hair edits comes
first. Compare G against each candidate using exact paired rendering, real-worker
motion and counterbalanced runs; preserve both glasses/hair models, down/up/both
yaw, nose/front safeguards, resolution and lifecycle protections. Startup,
completed updates, video delivery, age, gaps and UI delay need separate metrics.
Historical CRLF manifests and failed receipts remain unchanged. Sustained physical
camera, mobile/thermal behavior and new long-hair motion acceptance remain missing.


## Separate G / H–L efficiency preview — September 10

The owner requested more comparison options. The isolated
[efficiency lab](../experiments/efficiency-lab/README.md) runs on development
8094 / production preview 8096. G is initially selected and imports its unchanged
renderer/options. Normal dev/build/preview still use vite.combined.config.ts;
HEAD remains b9142b2. H reuses private download scratch; I reduces camera-input
allocations/copies; J delays next-image inference until main graphics submission;
K submits the paired temple branch before awaiting beauty retrieval; L combines
them. All are unaccepted candidates. No simultaneous GPU execution or speed gain
is inferred from earlier submission or async syntax.

All three matched renderer studies (H, K, L) pass 56 exact generated/recorded
comparisons and 16 lifecycle groups each. Both glasses/hair models, down/up/both
yaw, geometry and nose/front/outside-arm safeguards are covered. Each matrix has
36 actual hair edits and 12 nonzero drops. Independent PNG audits pass. Hardware
beauty PBO completes in all 32 cases per side; recorded software still exercises
explicit bounded fallbacks. Initial nonzero prewarming remains synchronous.
[QA results](../experiments/efficiency-lab/qa/README.md) retain the actual counters,
239-file G Git/local boundary and 302 frozen-input checks. Recordings, historical
CRLF manifests, old reports and recovery material remain unchanged.

Final strict types, production build and 61 unit checks pass. All 12 new production
browser scenarios are covered by the initial 11 passes plus the corrected held-mask
case; the affected case and expanded entry check pass together. The original
failure compared displayed frame 76 with correctly drained/held frame 77. The
corrected observer independently retains the final pair's real face/category
worker outputs before injecting mask-upgrade failure; exact detection, mask and
pixel assertions remain. The first unit synchronization failure and clipped
phone-label receipts also remain; the unit waits for actual completion and long
labels now wrap. Camera-off layout passes all six modes at 1440/390/360 with no
page errors or camera requests. No test deadline was enlarged or assertion
weakened. Required npm test passed 186 units, G's 11 browser cases and eight
reference cases, then hit the unrelated existing test_ picker mismatch in the
modified reference test. The two long-hair integration cases pass separately.

Final corrections add a pump-ownership guard for Hold-then-switch/no-face recovery,
explicit candidate selection for measurement, fixed model IDs in timing rows,
observation timestamps and visible stale/held state. Completed cadence, video
delivery, frame age, long gaps, coverage and startup events remain distinct.
The final nine-file UI/telemetry/orchestration/test delta is recorded separately
from the earlier matched hashes; renderer, pump, worker, input and option files
remain byte-identical to those studies. Prior receipts are never rewritten.

No candidate is promoted. Sustained physical-camera performance, moving-wearer
acceptance, mobile smoothness and thermal behavior remain unmeasured. Existing
unrelated work and the parent Python/Railway application are preserved.


The owner subsequently reported no noticeable difference in the G/H–L preview
and requested measurement. No new candidate was selected. The existing G-only
camera session already records scalar timings; the measurement handoff uses a
fresh page, approximately 60 seconds of movement, then Close camera and Download
timings only. No recorder/runtime change was needed. Physical-camera evidence is
still pending receipt of the owner's JSON. Check retained-row/event boundaries
when analyzing; completed-row cadence excludes leading/trailing idle time.


## First owner G camera measurement — September 10

[Analysis](G_CAMERA_MEASUREMENT_2026-09-10.md) of the owner's 07:35:19 timing JSON
finds 847 contiguous G rows in 59.798 s, Amber/hair-only at 1280x720, Intel Arc 140T.
Completed cadence is 14.640/s versus sampled video delivery of 19.817/s (track setting 30);
age median/p95 is 115.670/165.335 ms. First masked image arrives 2.010 s after Open;
20 completed intervals exceed 100 ms, none 200 ms. The export's 7.67 s last-update
age follows Close, which occurred 2.915 ms after the last publication. 843/847 rows
track, all 843 have masks; 441 have actual hair edits and 170 have real temple downloads. All 847 beauty
PBO reads complete. This is one model combination and G only, without pose or
visual evidence; it does not establish H-L gains, thermal behavior or mobile speed.

After first-publication +10 s, median hair extraction of 25.110 ms, native PBO extraction
of 21.380 ms, fence wait of 19.975 ms and face request of 39.085 ms identify the next investigation.
These are overlapping wall intervals; extraction includes conversion/copies and
possibly deferred GPU work. Actual next-image overlap correlates with slower PBO
retrieval even among tracked zero-branch frames; contention remains a hypothesis.
Next: split retrieval/conversion timers, counterbalance G/J scheduling, and confirm
the SDK mask representation before a separately tested exact conversion candidate.
G/runtime/dependencies/parent deployment remain unchanged. The original 6,838,909
bytes remain intact (SHA256: 33e4444ec243c78e665687746bf6334e4a30c03512e9613765487897d5ea839e).


## Fewer processed images preview — September 10

The owner requested a separate live experiment that processes fewer images.
Efficiency-lab M/N/O cap new captures at 12/10/8 per second; G stays the ordinary
entry and the main default. An explicit ?pipeline=rate12 link selects M.
Admission uses monotonic start-to-start spacing before allocation, drawing,
readback, hashing or inference. First capture is immediate; no timer retains old
images or accrues catch-up credit. Early rejection preserves the existing pending
pair; successful snapshots alone consume admission. Original G renderers/options,
workers, resolution, geometry and nose/front safeguards are unchanged. Any applied
mask belongs to its exact source/detection/pose; existing missing/late-mask behavior
remains. The last completed AR image stays visible between updates.

Timing JSON separates observed camera callbacks, cap skips, backpressure, captures
and completed publications. Counters reset per pump; observed callbacks are not
sensor frames. Caps are maximum rates, not guaranteed cadence. Compare G and each
rate with reversed measurement order, fixed workload and repeatable movements;
consider both image age and longer intervals. Lower work does not prove lower
energy use, better responsiveness or preserved temporal tracking.

Verification: strict efficiency TypeScript, production build and all 74 efficiency
unit checks pass. All 12 focused production browser cases pass (five new rate
cases, six existing lifecycle/measurement cases, one entry/layout case). Real
local workers cover both glasses and both hair models, all three admission caps,
worker requests, exact held cache mapping, nose/front/outside-arm checks, pending
Stop, startup cancellation, switching, Hold and restart. Layout covers 1440/390/360.
Rate modes deliberately share G held pixels: this does not independently prove
visual quality under changed temporal sampling. The new camera fixture is static
640x427; no new down/up/both-yaw moving-wearer evidence is claimed. The unchanged
G renderer retains its earlier 56-pair angle evidence; it was not rerun or
presented as a new motion test. Owner review must include both glasses/hair
models, down/up/both yaw and nose/front/hair transitions.

Required npm test passes 186 units, all 11 G browser cases and eight reference
cases, then fails at the pre-existing unrelated test_ model-picker expectation
in tests/browser/mirror.spec.ts:660. The downstream long-hair phase is not reached.
No unrelated test was fixed, deadline enlarged or failed browser case retried.
A 6,345-file preservation snapshot found no changes to G/reference/worker/model,
parent entry or unrelated source/model-studio files. Receipts are under the ignored
efficiency-lab/logs/rate-* and test-results/production-* paths. Private recordings
and recovery material are untouched. No candidate is promoted; physical-camera
quality, sustained performance, mobile and thermal results remain unmeasured.


### Owner review: retain G — September 10

After reviewing the live comparison with reduced-image-rate options, the owner
reported: "G is by far superior to the others." G remains the selected main
baseline; M/N/O are not accepted for promotion and remain separate experiments.
This is qualitative live preference, without a new timing export or a stated
breakdown by model, pose or specific quality defect. It does not establish a
measured thermal, power or tracking-accuracy difference. Further efficiency work
should prioritize reducing the cost of each processed image while retaining G
cadence and exact pairing. No new runtime experiment is authorized by this
feedback alone; runtime, existing evidence and unrelated work remain unchanged.


## P: cheaper hair-mask extraction preview — September 10

The owner authorized a new preview after preferring G over reduced image rates.
The focused ?pipeline=mask&study=mask entry offers G/P only; ordinary lab/main
entry remains G. P keeps G uncapped scheduling, source resolution, face pipeline,
renderer/options, exact image/detection/pose/mask pairing and final safeguards.
It changes category-mask materialization: byte-backed masks retain an owned copy;
float/GPU masks use the public float getter and a single owned byte conversion
with Math.fround(Math.round(255*v)), preserving the installed SDK 1.0.1 map-store
semantics. No weights, class thresholds, geometry or occlusion rules change.

Only the lab uses the new isolated instrumented hair client/worker. Its G control
retains SDK getAsUint8Array plus owned slice; both modes collect matching scalar
telemetry and preserve original validation/lifetimes. Extraction mode is frozen
per request and validated on return. Actual representation, retrieval, conversion,
copy and explicit array-byte counters are exported under native.hairCategory.*.
These wall intervals can include deferred GPU work; array counters are not
measured bandwidth, power or GPU execution. Main G and its original workers stay
byte-identical. The preview does not deliberately skip more camera images.

Strict efficiency types, production build and 109 unit checks pass. All 13 focused
browser scenarios are covered by 12 initial passes plus the corrected frozen
case. Four live combinations (both glasses x both hair models) exercise actual
direct float conversion and SDK control, uncapped admission, exact ownership,
Hold/switch/stop/restart and nose/front/outside-arm checks. The 101 sampled P rows
across their initial and return windows all report direct-float-conversion and
no rate cap. These synthetic controls are not a speed comparison.

The frozen study passes 32 actual same-MPMask byte comparisons: eight preserved
generated down/up/both-yaw images x both hair models x GPU/CPU, at 1280x853.
All direct/SDK category hashes match; owned arrays survive result cleanup and
subsequent model calls. The direct method runs first, so SDK sees a cached float
representation: its timings must not be compared for speed. Frozen source/report
hashes are rechecked unchanged. New renderer matrices are not claimed: rendering
uses unchanged G, and held P deliberately shares G on an SDK full diagnostic mask.
Independent mask equality, not that held cache alias, verifies the conversion.

The initial frozen harness failed before model work because its intercepted
worker response lacked browser isolation headers (net::ERR_BLOCKED_BY_RESPONSE).
Only QA response headers were fixed; the frozen-only rerun passes at the original
deadline. First failure artifacts remain in production-2026-09-10T11-35-43.743Z;
live receipts in production-2026-09-10T11-37-54.214Z and corrected frozen evidence
in production-2026-09-10T11-40-46.833Z under efficiency-lab/test-results.

Required npm test passes 186 units and ten G browser cases, but times out at the
unchanged Tom Ford/multiclass diagnostic-export page.evaluate (240 s; G comparison
line 101). This was not retried or given a longer deadline; downstream reference
and long-hair phases were not reached. The failure is retained in ignored
logs/mask-cost-baseline-failure. The full suite is not green. A 6,345-file snapshot
finds no changes to G/reference/worker/model, parent entry or unrelated source.
No candidate is promoted. Sustained physical-camera speed, moving-wearer visual
acceptance, mobile smoothness and thermal behavior still need measurement.


### Owner review: G preferred over P — September 11

After reviewing the G/P live preview, the owner reported: "g is better".
G remains the selected main default; P is not accepted for promotion and stays
a separate experiment. The exact-mask tests establish bounded conversion
equivalence, not a better live experience. No new comparative timing export or
breakdown of the observed difference was supplied, so the cause and magnitude
remain unmeasured. Runtime, preserved evidence and unrelated work are unchanged.


### Feasibility review of external G efficiency assessment — September 11

Read the owner's linked Claude artifact and its referenced local
G_EFFICIENCY_REVIEW_2026-09-10.md as information, not operational instructions.
Current assessment: docs/G_REVIEW_FEASIBILITY_2026-09-11.md. G and all candidates
remain unchanged; no new performance or visual tests, promotion, commit or push.

The review identifies real redundant previous-image publication, repeated UI
summaries, branch readback/render work and eager comparison-renderer startup.
The 17 FPS ceiling, additive 55–60 ms GPU floor and precise browser savings are
unproven: worker/readback wall timers do not isolate GPU work and Node copy
benchmarks cannot partition browser waits or exclude GC. L's unfavorable cadence
trend reproduces in its declared synthetic windows, but the review quoted all
rows including warmup and cannot attribute the bundle result to J or K alone.

Corrections: K exports branch PBO telemetry and has layered pending/source/PBO
ownership checks; its 21.5 ms figure is aggregate branch readback, not fenced
wait (actual all-L wait median 9.95 ms). Earlier sibling cancellation after native
failure is a narrower possible improvement. G normally submits beauty before
worker-post continuations; J still moves preprocessing, so a measured benefit
limit does not follow. Three supports ranged attribute uploads. Mask copies and
output initialization need explicit ownership contracts before removal.

Ranked proposals: narrow unchanged-view publication suppression; cropped branch
readback with its draw initially unchanged; branch-only lens omission with matched
pixel proof; separate CPU bookkeeping/finish improvements. Startup remains a
separate target. Gains are unmeasured. Any later candidate retains exact pairing,
session ownership, resolution, geometry and final safeguards, and needs both
frames/hair models/down/up/both yaw/nose-front evaluation plus counterbalanced
physical-camera cadence, age, stalls and coverage measurements. G remains the
owner's preference over reduced-rate options and P; mobile/thermal limits remain
unknown. Existing review text, private evidence, unrelated work and parent app
are preserved.

### Switchable Q-T live preview from the feasibility review - September 11

Implemented only in experiments/efficiency-lab. The focused production link is
http://127.0.0.1:8096/experiments/efficiency-lab/live.html?study=review and starts G.
Q suppresses only unchanged pre-prepare publication after the pump has published;
R downloads the consumed temple row band after the unchanged full branch draw;
S hides branch lens materials only around the final draw; T refreshes repeated
human summaries every 500 ms while retaining every profiling sample and immediate
state/control transitions. Q/T use the imported original G renderer. R/S retain
resolution, geometry, exact source/detection/pose/mask and final safeguards. All
four are independent and uncapped; earlier choices remain. No promotion or push.

Strict types, production build and all 128 efficiency unit checks pass. Production
compatibility: 22 passes and one optional frozen P study skipped; focused Q-T:
all five pass, including both glasses x both hair models, real workers, exact
held output, live switching, stop/restart and cancellation. Their static camera
exports contain 594 contiguous samples: Q skips 64/68 repeat publications and T
skips 74/85 human-summary refreshes. Static R rows exercise valid empty regions;
the frozen matrices independently require positive cropped reads in every
model/hair/phase group. An initial test incorrectly required one read for a null
region; only QA was corrected, with the original failed log and trace retained.

R and S each pass all 56 generated/recorded image comparisons against G and the
accepted archive: both glasses/hair models, down/up/both yaw, exact pixels,
geometry, masks and nose/front/outside-arm checks. Independent PNG audits pass
56 each (R 666 artifact/base files; S 690), including 243 runtime hashes, 302
frozen inputs and the 239-file G Git/local-byte boundary. Reports remain in
qa/output/matched-2026-09-11T12-15-05.026Z (R) and
qa/output/matched-2026-09-11T12-18-54.945Z (S). R has 20 lifecycle control groups,
including four full raw-diagnostic rerenders after borrowed-source revocation;
S has 16. Full raw diagnostics retain their own source/pose and do not assume a
previous default framebuffer survived. Visible pixels and live counters remain
unchanged by diagnostic export.

R has 12 positive cropped reads and 44 zero-drop cases: branch bytes decline
46,960,640 to 10,024,960 (78.65%), total counted CPU downloads 291,297,280 to
254,361,600 (12.68%), with calls unchanged. S actually omits one lens material
on each of 12 nonzero branches; raw differences are confined to protected pixels
and final images remain exact. Three excludes hidden lens materials from its
render list and skips that branch transmission pass; beauty, prewarm, head-depth
preparation and readback remain. These are reduced-work receipts, not measured
GPU time, FPS, responsiveness or thermal gains. All generated hardware cases use
actual beauty PBO completion. Recorded SwiftShader bounded 500 ms same-pair beauty
fallbacks are G/R 2/2 and G/S 4/10; these are correctness fallbacks, not fast paths.

Required npm test passes 186 units and all 11 G browser cases, then the original
browser suite has eight passes and one failure: missing test_ selection option
in tests/browser/mirror.spec.ts:648. This separate trial-model work was left
untouched; downstream long-hair tests were not reached. The full suite is not
green. Logs/failure copies are retained in the ignored efficiency-lab/logs/
review-preview-2026-09-11 directory. G, parent app, original recordings, recovery
inputs and the owner's Downloads JSON are unchanged. Concurrent model_studio
changes observed during this work were left alone. Moving-wearer visual acceptance,
sustained physical-camera performance, mobile smoothness and heat remain unmeasured.


### Public mobile comparison delivered — September 11, 2026

The owner explicitly authorized a live Lenses landing-page option `ar_testing`
and selected video + measurements in one file. Commit
`0815de761dbab3c4cc8557bbb29964fe94a8af67` is pushed to main and
codex/ar-mobile-testing; Railway reports successful deployment. Live page:
https://web-production-ef3ca.up.railway.app/ar_testing/
All 21 public files were fetched from Railway and matched the tested size/SHA
manifest; release fingerprint starts 894b05be66c9. The landing link, isolation/CSP
headers and private-path rejection are verified. G remains selected; no Q–T
candidate is promoted.

Implementation is preserved in the isolated main-based worktree
`.recovery/mobile-railway-2026-09-11/checkout/`, branch codex/ar-mobile-testing.
It includes the narrow Python route and landing link plus the AR-only public
package, controller and recorder. Procfile/UI.app startup and unrelated parent
features are unchanged. The original branch stays at b9142b2; all 66 original
lab source files still match their pre-task hashes. Private recordings, archives
and concurrent unrelated work remain untouched. Resume mobile implementation
from that isolated worktree, not by replacing this dirty checkout.

Continuous runs switch G/Q/R/S/T then reverse, with equal 5 s warmup and 30 s
measurement windows, fixed workload, independent camera observations, complete
AR timing rows and explicit partial/stall/coverage/startup data. One local ZIP
contains mirror video without audio and scalar telemetry, with Save/Share fallback.
A mobile-only SDK fetch guard and enforcing document/worker CSP prevent external
telemetry. Earlier SDK-path and SDK-telemetry test failures were fixed, retained,
and documented in the worktree review; the zero-external-request check stays strict.

Isolated verification passes required npm test (172 units, 21 browser cases),
167 efficiency checks, 47 Python checks, the separate network boundary browser
check and all four production mobile-viewport scenarios. The real six-minute
recording has all ten windows, valid ZIP CRCs and decoded video near its end;
all glasses/hair combinations retain exact held output and cancellation/restart
cleanup. Public receipt: checkout/ar_v4/experiments/efficiency-lab/qa/output/
published-0815de7.json within the task archive. This is desktop synthetic-camera
functional evidence. Physical phone camera, wearer motion, codecs/downloads,
peak memory, sustained smoothness and thermal effects still require owner runs.


### iPhone startup follow-up — September 11, 2026

Owner reports iPhone 17 Pro stuck Opening for >1 minute. The actual phone stage
is not yet identified. Main/mobile worktree commit
f7fb1300f97fb88636aa68a635198b5dcbb14dd9 adds precise startup progress, a local
Save startup report button available before the first frame, and bounded
cancel/retry handling. Railway succeeded; all 21 public files match the tested
manifest, release 274f30e02554. G's 239 accepted dependencies remain exact.

Required npm test passes 172 units/21 browser cases; 183 efficiency checks pass. Two
new production startup tests pass (blocked module cancellation/late-result/retry,
real 60 s blocked GLB timeout/cleanup/retry). Network boundary and Tom Ford/multiclass
G-to-Q measurement/partial save/held/background cleanup regressions pass. Desktop
WebKit renderer creation succeeds but that Windows port cannot exercise the phone
camera/worker pipeline. This is diagnosis and bounded recovery, not proof the
underlying phone hang is fixed. Collect the phone's startup JSON before changing GPU,
inference or rendering. New mobile implementation remains in the clean worktree
.recovery/mobile-railway-2026-09-11/checkout/; original dirty AR sources untouched.

## Published iPhone capture correction - 08191d3

The real iPhone startup report reached GPU-ready but timed out before first AR publication. The efficiency-lab capture guard incorrectly rejected a snapshot when WebKit live-camera currentTime advanced. The isolated mobile checkout now uses rVFC frame counters and the same owned pixels throughout detection, hair and rendering. Accepted G rendering dependencies remain exact.

Commit 08191d394a764ecb77ff7a9e060f2514c33613f5 is pushed to main and codex/ar-mobile-testing. Railway reports success, and all 21 served files match runtime release 9df9d0b9c9bf. Required npm test passes 172 unit/21 browser checks, efficiency passes 189 checks, and all eight mobile browser regressions pass, including four 720x1280 portrait glasses/hair combinations and the reproduced advancing-clock failure. Physical-phone startup and visual behavior still need the owner retry.

Implementation and full reviews: `.recovery/mobile-railway-2026-09-11/checkout/ar_v4/`. Published receipt: `experiments/efficiency-lab/qa/output/published-08191d3.json` inside that checkout. This original dirty checkout and its efficiency sources remain separate; do not copy them over the deployed correction. Private recordings and recovery material remain excluded.


### First physical-phone comparison - September 12, 2026

The owner supplied a complete six-minute video/telemetry ZIP from the iPhone on
capture-fix runtime 9df9d0b9c9bf (08191d3). Startup succeeds: first AR publication
is 4.129 s after camera-open request, first mask 4.627 s. Amber Horizon/hair-only
uses 720x1280; both inference delegates report GPU. All ten 30 s measurement
windows complete. Independent raw recomputation matches every exported summary;
4,065 measured rows, 856 warmup rows and 26 justified boundary/switch exclusions
are retained without truncation. Eight nonmonotonic camera observations were
rejected explicitly. The original ZIP and extracted entries retain exact hashes.

Pooled completed AR updates/s for G/Q/R/S/T are 13.87/13.88/13.17/13.32/13.52.
Q exceeds G by one update over 60 measured seconds. Corresponding matching-mask
coverage among tracked publications is 41.95/28.40/74.94/63.21/52.98 percent.
Overall, 1,939 of 4,027 tracked updates use the accepted no-mask fallback.
These workloads are unequal; no candidate earns promotion. Missing masks need
not produce visible errors on every image. Camera delivery stays 27.97-29.29 fps;
no measured AR completion gap exceeds 200 ms. Pooled age p95 is about 187-194 ms
across modes, ending at canvas submission rather than physical display.

All second passes slow down (G 15.07 to 12.67 updates/s). Palindromic ordering
balances mean test time, but movement, mask coverage and nonlinear drift vary.
Temperature was not measured and video encoding adds load. The whole VP8 video
decodes through 359.67 s; 50 frames sampled across all motion cues confirm
different pose depths/angles. Brief glasses loss during deep down/yaw agrees
with reported no-face episodes; it is not causal evidence against a candidate.
Tom Ford, multiclass hair, no-recording performance and matched wearer pixels/
geometry/nose-front equivalence are not covered by this run.

Actual PBO completion occurs on every measured frame. Hair SDK retrieval median
is 31.38 ms on results that arrive, versus 0.08 ms for its final category copy.
Late results lack these timings. The client also validates/copies and hashes
masks before settling segment(), and hairTail blocks the next request until
complete. Those client/transport costs remain unmeasured. Prioritize complete
hair-result timing, then investigate earlier worker release with separate owned
validation/publication and exact mask readback; retain G and all safeguards.
No implementation, default, parent application or deployment changed. Detailed
private evidence and proposals are in ignored
`.recovery/mobile-comparison-2026-09-12/analysis.md`; scripts and receipts are
alongside it. These are initial physical-phone measurements, not universal
mobile quality or thermal results.


### G frame-rate review — September 13, 2026

Read-through of the G hot path plus new measurements on the owner's laptop
(real Arc 140T GPU, synthetic camera, on battery). Findings: the publication
interval equals main-thread occupancy, most of it blocked in `getBufferSubData`
behind Chrome's single GPU-process thread that also runs both MediaPipe
delegates; byte-exact candidates are exhausted; the face landmarker on the CPU
delegate measured +9–13% fps at two sizes (`?face=cpu`, opt-in); hair-input
downscaling, the multiclass model swap and a 0.5x transmission target were
refuted at noise level; the laptop's battery state halved G relative to the
Sep 9 promotion receipt on the same fixture. Opt-in URL experiments were added
to the G page with unchanged defaults (six pinned G files touched; `npm run
check` and 52 unit tests pass). Full write-up, tables and the reproducible
harness: `docs/G_FPS_REVIEW_2026-09-13.md`, `experiments/speed-lab/qa/measure-experiments.mjs`,
`experiments/speed-lab/qa/receipt-stats.mjs`. No promotion; owner verification on
mains power with the real camera is the next step.
