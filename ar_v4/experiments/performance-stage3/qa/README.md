# Separate Test 3 QA

The speed-testing promotion intentionally changes Stage 2 selector, label and
test files covered by the older 175-file preservation manifest. Historical
matched commands below require the original source snapshot as well as private
inputs; their old guard must continue to reject the changed source boundary.
For future candidates, establish a new explicit Test 2 base manifest without
rewriting the old receipts. Fresh-checkout `test:speed` and promoted browser
tests do not depend on that historical manifest.

This directory contains QA only. Previous pipelines, recordings and evidence are
read without rewriting them. New receipts go to unique ignored `output/` paths.
Only one heavy browser job may run at a time, coordinated with physical-camera
and production-browser testing.

## Preservation boundary

[The initial independent manifest](output/preservation-before-2026-09-09T13-03-49.571Z.json)
verified the previous **137-file** preservation set against its existing hashes,
then added **38 Stage2 source, test, documentation and configuration files**:
**175 unique preserved files**. Build outputs, logs, test results and QA output
are excluded. It was created before new QA adaptation. This hash boundary is not
a backup; the original files and private recordings remain in place.

```powershell
node experiments/performance-stage3/qa/preservation.mjs --verify
```

Matched and sustained runners verify all 175 files before and after their runs.
The independent PNG audit verifies the same manifest again.

## Matched rendering

`matched.mjs` compares actual Test2 and Test3 `LiveHairRenderer` implementations
with each other and the accepted frozen archive. Receipt keys `current` and
`candidate` mean **Test2** and **Test3** here. The full set remains 32 generated
and 24 recorded comparisons: both glasses, both hair models, down/up/both yaw,
optical/front/nose checks, full RGBA and geometry equality. Masks and detections
are replayed exactly; inference is not rerun or substituted.

```powershell
node experiments/performance-stage3/qa/matched.mjs --preflight
node experiments/performance-stage3/qa/matched.mjs --phase=generated32 --source=03-up-blonde-waves
node experiments/performance-stage3/qa/matched.mjs --phase=generated32 --source=01-steep-down-straight
node experiments/performance-stage3/qa/matched.mjs
python experiments/performance-stage3/qa/audit-pngs.py <new-report.json>
```

The server defaults to `http://127.0.0.1:8076`. Default `--warmup=0 --samples=0`
means one presentation per pair with renderer sessions reused per glasses/phase,
not a newly created renderer for every pair. A selected source is explicitly a
partial four-pair smoke. Recorded `original-55` is first after renderer creation
for both glasses; return-to-first-pair controls exercise reuse after other poses.
Generated cases require actual Intel D3D11. Recorded cases preserve the historical
SwiftShader/default-canvas decode and provide correctness evidence only.

The frozen geometry identifies **44 exact-zero rear-drop cases** (28 generated,
16 recorded) that must exercise the GPU path, and **12 nonzero cases** (4 generated,
8 recorded) that must explicitly use the reviewed CPU fallback. The first command
above selects four upward GPU-eligible cases; steep-down selects four nonzero
fallback cases. Both groups still require exact full-frame pixels and geometry.
Fallback parity does not count as evidence of GPU execution.

Controls include hair-off, invalid mask fallback, no-face/reacquisition, private
prepare, overlapping prepare, double finish, abort during a pending pair,
pre-aborted creation, and accepted-first `present(mask)` followed by hair toggle.
Source, detection and mask ownership remain exact. Four native PNGs per case
support independent rereading of full pixels, geometry and protections. GPU
context-loss/readback-failure cases depend on the finalized runtime and are
tracked separately rather than implied by these controls.

Runtime hashes include preserved dependencies, Test2, new GPU/shader source,
and the actual loaded Stage3 replay modules. The separately developed Stage3 live
UI is excluded from renderer-only hashes. Private inputs are routed only through
the disposable local browser context. Local-network permission and inert QA-only
HMR hooks prevent unrelated UI edits from restarting replay.

## Sustained synthetic camera

The inherited sustained runner exercises the real application scheduler and
workers through its bounded numeric sample ring. Defaults compare **Test2/Test3**
for both glasses and both hair models: eight segments, at least five seconds of
warmup and thirty measured seconds each. All four pipeline IDs are available by
explicit selection. Orders rotate across glasses/hair combinations.

```powershell
node experiments/performance-stage3/qa/sustained.mjs --preflight
node experiments/performance-stage3/qa/sustained.mjs --base=http://127.0.0.1:8078
node experiments/performance-stage3/qa/sustained.mjs --base=http://127.0.0.1:8078 --pipelines=current,test,test2,test3
```

The isolated synthetic camera uses the preserved generated portrait at 960 × 640
with a changing corner marker and requested 30 Hz timer. Reports retain actual
publication/video/timer cadence, frame-age and interval median/p95, stalls,
tracking/hair coverage, raw per-frame stage metrics, delegates, GPU identity and
resource release. The exact informational XNNPACK initialization message remains
recorded at its original console level and is classified separately; all other
errors fail. Ports 8077/8078 automatically freeze every compiled Stage3 build
file, verify served HTML/JavaScript/CSS entry bytes against that manifest before
opening the browser, and reverify the build afterward. Other production servers
can supply `--build-dir=experiments/performance-stage3/dist` explicitly.

This source shares the application thread, so its actual delivery depends on
processing load. It is not physical-camera or moving-wearer evidence. Frame age
ends at completed canvas submission, excluding sensor buffering and screen
scanout. Stage wall times can overlap and must not be blindly added. Reported GPU
delegates do not establish that every inference operation runs on the GPU.

## Current evidence

- **Final ordinary run:** `matched-2026-09-09T13-45-15.315Z` passed all56 pairs
  (32 generated/24 recorded), all16 control groups, and the independent426-file
  audit. It used one presentation per pair with reused sessions, without the
  diagnostic capture probe. The audit verified44 GPU cases and12 explicit CPU
  fallback cases, rehashed183 runtime files/302 frozen inputs, and reverified all
  175 preserved previous files. Representative Amber/up GPU and TomFord/down CPU
  PNGs were inspected. Exact whole-frame equality includes optical/front/nose
  pixels; each implementation's own guard checks alone are not that claim.
- `matched-2026-09-09T13-44-08.865Z`: after the isolated RGB8 copyTexSubImage2D
  capture replacement, all four formerly failing original-55 combinations and
  controls passed uninstrumented, with a29-file independent audit. The final full
  run above subsequently checked this fix across both backends and all56 pairs.
- `matched-2026-09-09T13-30-26.819Z`: all four up-blonde GPU pairs and lifecycle
  controls passed. Independent audit verified 33 image/geometry files, exact
  GPU counters before export and three explicit diagnostic full-color reads.
- `matched-2026-09-09T13-30-54.595Z`: all four steep-down CPU fallback pairs and
  controls passed. Independent audit verified 33 image/geometry files and
  explicit nonzero CPU execution. This does not count as GPU coverage.
- `matched-2026-09-09T13-31-39.037Z`: full run intentionally interrupted after
  17 exact generated pairs when review identified a stale external native lease
  on a failed wrapper presentation before native rendering. The owned browser
  was closed to permit the fix. Its browser-closed failure and partial receipt
  remain unchanged; it is not a completed full-set pass.
- `matched-2026-09-09T13-33-54.348Z`: after lease invalidation fix, all 32 generated
  cases and the first recorded case were exact. The second recorded
  `original-55/tom-ford-clear/selfie-multiclass` rejected its mask; the initial
  harness dereferenced that missing diagnostic mask before retaining the cause.
- `matched-2026-09-09T13-36-39.272Z`: bounded uninstrumented original-55 replay
  retained the failure. Test2 remained exact; Test3's second native beauty was
  entirely black while its clean camera stayed exact. The background reference
  check rejected it (854,269 changed pixels, max delta255), so no hair was applied.
  All921,600 displayed pixels differed from the archive. PNGs and both stats
  snapshots are retained. QA was made null-mask-safe without relaxing any checks.
- `matched-2026-09-09T13-40-46.431Z`: the same failure repeated while an initial
  diagnostic prototype hook attached to a bare Vite module URL instead of the
  actual timestamped dependency. Its zero captured probe rows make it a tooling
  failure, not a useful capture split.
- `matched-2026-09-09T13-41-55.313Z`: exact-module probe produced four exact pairs
  and controls only after adding raw framebuffer pixel reads before/after capture.
  The extra reads masked the uninstrumented failure. Actual READ/DRAW bindings
  were null before/after the first two beauty captures; sampled default/captured
  pixels matched and no GL errors appeared. This receipt is diagnostic only:
  its extra reads are outside normal counters and can change synchronization.
  The independent ordinary-pass auditor explicitly rejects such probe receipts.

The public four-factory wrapper ownership tests pass eight cases, including
pending/held display swapping, inactive export failure recovery, cancellation,
GPU context-loss forwarding and restoration of the original canvas on disposal.
Three QA mechanism tests additionally reject disguised CPU/readback substitutes.

## GPU mechanism checks and retained limitations

`gpu-contract.mjs` checks stats captured immediately after presentation, before explicit exports:
GPU cases require two native captures, three compact flag/guard reads and zero
full-color or branch readbacks. Explicit diagnostic exports subsequently read
three full-color images, and both pre-export and post-export counters are retained.
Nonzero geometry requires the declared CPU path with an actual branch readback.
The independent Python audit recomputes eligibility from the frozen geometry and
checks exact compact/full readback byte counts. Sustained telemetry checks every
published frame and separately reports GPU and nonzero CPU coverage; a zero-drop
CPU fallback cannot silently count as GPU success.

## Sustained result: transfer savings did not establish smoother output

`sustained-2026-09-09T13-55-56.542Z/report.json` and its `summary.json` retain
eight completed segments: **2,555 publications over 240.121 measured seconds**,
plus at least five seconds of warmup per segment. Both glasses and both hair
models were measured once per pipeline, with order alternating Test2/Test3 and
Test3/Test2 between combinations. No measurements were repeated to chase a win.
The production run verified **206 source/input files, 46 compiled files, eight
served HTML/JS/CSS entries and all 175 preserved previous files**.

| Glasses / hair model | FPS Test2 / Test3 | p95 interval ms Test2 / Test3 | p95 frame age ms Test2 / Test3 |
| --- | ---: | ---: | ---: |
| Amber / hair-only | 11.86 / 11.66 | 108.63 / 117.18 | 93.04 / 104.54 |
| Amber / multiclass | 10.10 / 10.59 | 117.88 / 122.27 | 103.50 / 108.48 |
| TomFord / hair-only | 10.40 / 10.73 | 119.69 / 122.47 | 104.43 / 106.90 |
| TomFord / multiclass | 10.16 / 9.63 | 119.70 / 129.96 | 104.91 / 116.49 |

Pooled publication rate was **10.630 FPS Test2 versus 10.651 FPS Test3** (about
0.20% apart). Per-combination differences ranged from −5.25% to +4.87%; Test3's
p95 interval and frame age were worse in all four combinations. Pooled median
frame age was 82.49 / 79.05 ms, but p95 was 103.65 / 110.04 ms. There were
**one / 39 observed main-thread long tasks** during Test2 / Test3 windows. These
single windows do not provide statistical repeatability or a causal proof.

All 2,555 frames had a face, an exact paired mask and actual hair pixel changes.
All **1,279 Test3 frames used GPU composition**, with no CPU fallback and zero
full-color diagnostic readbacks. Each Test2 renderer frame read **4,915,200 bytes
in two calls**; each Test3 renderer frame read **614,408 bytes in three compact
calls**, an **87.5% byte reduction**. Test3 additionally uploaded 1,843,200 bytes
per frame for category/region/continuity data. These are renderer counters, not
total application transfers: source canvas readback, hashing and inference remain.

| Pooled per-frame median, ms | Test2 | Test3 |
| --- | ---: | ---: |
| Source draw / detector draw | 3.29 / 0.04 | 2.47 / 0.03 |
| Source readback / hash / face bitmap | 2.31 / 6.83 / 1.93 | 0.96 / 3.52 / 0.86 |
| Face request / native inference wall call | 34.10 / 33.26 | 29.85 / 29.07 |
| Prepare | 19.63 | 9.00 |
| Finish | 14.44 | 29.55 |
| Total render work | 34.28 | 39.11 |
| Scheduler wait | 11.87 | 11.29 |
| Compose / continuity / final checks / publish | 7.41 / 1.35 / 5.35 / 0.80 | 19.58 / 0.79 / 7.03 / 1.19 |

Test3 moved work out of preparation, but its later finishing work offset those
savings. Its pooled native capture wall time was 2.05 ms, flags readback 4.88 ms,
reference reduction 3.79 ms, GPU composition submission 1.64 ms, audit submission
0.18 ms and audit reduction 4.22 ms. GPU state save/restore accumulated 7.85 ms
per frame across operations. These clocks overlap: capture/compose/publication
include state work, and a later readback can wait for earlier queued GPU commands.
They must not be added as separate GPU execution times or transfer-only costs.

`composeMs` includes CPU region construction, GPU flag generation/readback,
reference reduction and CPU flag statistics. `finalChecksMs` includes GPU
composition and the final audit/reduction. The per-frame difference between
compose time and its three measured flag/reference substeps had a 10.81 ms
median, but that remainder also contains state/validation overhead; it is not an
isolated CPU-statistics measurement. Reduction targets requested 48,784 bytes of
new storage per measured frame even after warmup. Repeated state queries and
separate reusable reduction storage are plausible future targets, not measured
speed improvements or permission to weaken the full guards.

Unchanged source/hash/face stages also varied substantially between windows;
small FPS crossovers cannot be attributed solely to the compositor. The actual
Intel Arc 140T / D3D11 context and GPU delegates were recorded. Four exact XNNPACK
CPU initialization INFO notices were retained; GPU delegates do not imply every
inference operation executes on the GPU. Actual synthetic video delivery was
25.3–27.6 FPS for Test2 and 23.1–25.1 for Test3 against the requested30 Hz timer.
The timer shares the application thread, so this source is workload dependent.

This study supports exact still preservation and reduced renderer readback bytes.
It **does not establish a smoothness improvement over Test2**, physical-camera
latency, nonzero-drop speed, moving-wearer behavior, GPU memory use or mobile
thermal/battery performance. All tracks and workers were released; no unexpected
page, network or worker errors occurred.

The previous [Stage2 QA record](../../performance-stage2/qa/README.md) is retained:
56 exact post-fix pairs and 12 sustained segments, the earlier atlas failures,
the initial INFO-classification interruption, unequal Test2 speed gains and the
slow Current/multiclass interval. The earlier warmed optical/nose one-pixel
variance remains unresolved. New passes do not erase these observations or imply
owner visual acceptance, anatomical accuracy or measured mobile smoothness.
