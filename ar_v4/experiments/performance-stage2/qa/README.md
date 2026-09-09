# Separate Test 2 QA

This directory owns only QA code and new, ignored `output/` receipts. It reads the
preserved accepted archives and original recordings without rewriting them. Run
one heavy browser job at a time, coordinated with any physical-camera session.

## Exact paired rendering

`matched.mjs` compares actual **Test 1** and **Test 2** renderers with each other
and the accepted frozen archive. JSON keys `current` and `candidate` mean Test 1
and Test 2 here; `implementationLabels` records that mapping. The defaults are
zero warmup and zero timing samples: one presentation per pair, with renderer
sessions reused within each eyewear model and phase. This does not mean every
pair uses a fresh renderer. No face or hair inference is rerun.

```powershell
node experiments/performance-stage2/qa/matched.mjs --preflight
node experiments/performance-stage2/qa/matched.mjs --phase=generated32 --source=01-steep-down-straight
node experiments/performance-stage2/qa/matched.mjs
python experiments/performance-stage2/qa/audit-pngs.py <new-report.json>
```

The default server is `http://127.0.0.1:8072`. A complete run uses 32 generated
and 24 recorded comparisons: both glasses, both hair models, downward/upward and
both yaw controls, optical/front/nose checks, exact full-image RGBA and geometry.
The optional source selector explicitly marks a partial four-pair smoke; it does
not establish full archive coverage. Generated inputs use verified hardware
Intel D3D11. Recorded inputs retain the historical SwiftShader/default-canvas
decode and contribute no hardware performance timing. The recorded zero-weight,
zero-drop `original-55` pair is first after renderer creation for each glasses
model. The harness returns to the first pair after other poses.

The browser helper also checks invalid-pair fallback, no-face/reacquisition,
private preparation, overlapping prepare, double finish, abort while a pair is
pending, and pre-aborted construction. Exact source/detection/mask ownership is
checked. Test 2 must use its shared native camera with two direct readbacks for
paired hair frames; hair-off and no-face controls must avoid that auxiliary work.
Direct `present(mask)` with accepted selected must retain exact beauty and prepare
the paired hair result for a later toggle, without another presentation.
Four output PNGs are saved per pair. The independent Python audit
rereads archived and newly saved PNGs, verifies exact RGBA, full geometry,
protected/nose/outside/background/alpha invariants and input/runtime hashes.
It refuses to overwrite an existing audit. Control PNGs are not saved.
This renderer-only suite does not inject context loss or GPU initialization
failure; live scheduling, worker failure and three-way switching are evaluated
separately.

Private inputs are routed only inside the disposable local QA context. Scoped
local-network permission and an inert `/@vite/client` prevent development HMR
from interrupting QA; normal application pages are unaffected. Source hashes
cover actual renderer dependencies, including Test 1 and new Test 2 native code,
while excluding the independently developed live UI from renderer-only QA.

## Sustained synthetic camera

`sustained.mjs` exercises the actual three-way live application, face/hair workers
and scheduler. It substitutes a canvas camera only in its isolated page: a
preserved generated long-haired portrait at 960 × 640, with a changing corner
marker and a requested 30 Hz timer. Actual source draw and video delivery cadence
are recorded; the requested rate is not assumed to have been achieved.

```powershell
node experiments/performance-stage2/qa/sustained.mjs --preflight
node experiments/performance-stage2/qa/sustained.mjs --eyewear=amber-horizon --hair=hair-only
node experiments/performance-stage2/qa/sustained.mjs
```

Each selected pipeline receives at least five seconds of warmup after its first
publication and at least three paired hair frames, followed by at least 30 seconds
of measured publication. Defaults cover Current, Test 1 and Test 2 for both glasses
and both hair models: twelve segments, at least six measured minutes plus startup
and warmup. Pipeline order rotates across combinations. Production preview can
be targeted with `--base=http://127.0.0.1:8073` and the same page path.

The harness drains every completed sample from `arPerformanceProfiler.samplesAfter`
once a second and detects missed ring entries, unexpected pipelines, session
changes and worker errors. Reports retain raw numeric samples, actual window FPS,
separate interval cadence, frame-age and interval median/p95, stalls, hair-mask
coverage, changed-hair frames, fallback reasons, long tasks, detailed stage
timings, face/hair delegates and actual Intel D3D11 identity. Stop must release
all observed tracks and workers. All relevant live UI and runtime files are
hash-frozen throughout this measurement.

Parallel stage durations must not be added. Frame age ends at completed canvas
submission and starts at application capture; it excludes sensor buffering and
screen scanout. The synthetic timer, browser and fixture affect cadence. Hardware
identity and selected delegates do not prove every inference operation ran on
the GPU. These runs do not establish physical-camera motion, phone smoothness,
anatomical fit, battery/thermal behavior or owner visual acceptance.

## Retained evidence and current status

The [final post-fix archive replay](output/matched-2026-09-09T11-48-52.691Z/report.json)
passes **all 56 comparisons and controls**, including accepted-first direct
presentation and the new native read-error safeguard's normal path. The
[independent PNG audit](output/matched-2026-09-09T11-48-52.691Z/independent-png-audit.json)
passes 56 cases, rereads 426 image/geometry files, and revalidates 145 runtime and
302 frozen-input hashes. Native Tom Ford/down and Amber/up PNGs were visually
inspected. Source/runtime behavior stayed frozen through subsequent profiling;
the sole later QA-script change classifies one exact initialization notice, as
described below. Original recordings and accepted checkpoints were untouched.

The [combined sustained evidence](output/sustained-combined-2026-09-09T12-05-13.525Z.json)
contains **4,382 completed frames over 360.08 measured seconds**: all twelve
planned segments, at least thirty measured seconds apiece after warmup. Tracking,
paired-hair coverage and actual hair-pixel changes were 100% in every measured
segment. Actual renderer was Intel Arc 140T D3D11; source was the generated static
portrait at 960 × 640. The table uses Current / Test 1 / Test 2 order for the
three-value timing cells.

| Glasses / hair model | Current FPS | Test 1 FPS | Test 2 FPS | p95 interval ms, C / T1 / T2 | p95 frame age ms, C / T1 / T2 |
| --- | ---: | ---: | ---: | --- | --- |
| Amber / hair-only | 10.73 | 13.30 | 14.43 | 116.0 / 90.2 / 80.7 | 99.6 / 77.1 / 65.9 |
| Amber / multiclass | 7.20 | 12.93 | 14.12 | 237.8 / 93.6 / 85.1 | 227.0 / 76.4 / 67.8 |
| Tom Ford / hair-only | 9.66 | 14.59 | 14.67 | 128.6 / 79.7 / 79.5 | 111.4 / 64.4 / 64.0 |
| Tom Ford / multiclass | 8.50 | 12.37 | 13.53 | 144.1 / 98.9 / 87.4 | 130.6 / 83.0 / 70.0 |

Test 2's extra cadence gain over Test 1 ranged from about **0.5% to 9.4%**;
Tom Ford/hair-only was nearly tied, though median frame age improved 58.15 to
54.63 ms. The slower Amber/Current/multiclass segment includes a 750.39 ms maximum
frame age and a 785.48 ms maximum publication gap. That observation is retained;
its cause and reproducibility are unknown. Current had 13/51/30/33 observed long
tasks in the four combinations; both candidates had zero in these measured
windows. No completed measurement was rerun to improve its result.

Representative median stage times below are from Amber/hair-only. They are wall
times; asynchronous overlaps mean they must not be added indiscriminately.

| Stage, ms | Current | Test 1 | Test 2 |
| --- | ---: | ---: | ---: |
| Source draw | 1.99 | 2.12 | 2.05 |
| Detector draw | 0.03 | 0.03 | 0.02 |
| Source pixel read | 0.71 | 0.83 | 0.75 |
| Source hash | 3.32 | 3.78 | 3.80 |
| Face bitmap | 0.69 | 0.80 | 0.82 |
| Face request wall | 21.05 | 24.21 | 23.89 |
| Worker detectForVideo wall | 20.53 | 23.59 | 23.30 |
| Prepare | 35.14 | 12.45 | 16.56 |
| Finish | 14.83 | 16.48 | 8.68 |
| Combined render work | 49.53 | 29.13 | 25.42 |
| Scheduler wait | 11.62 | 11.32 | 9.44 |

In that Test 2 segment, the two native readbacks used 4,915,200 bytes per frame:
combined readback median 10.48 ms, beauty 7.20 ms, clean camera 3.19 ms, and
combined row extraction 1.51 ms. Clean submission itself was 0.075 ms. The legacy
`cleanCameraMs` field was only 0.005 ms because it now measures validation of the
already captured clean pixels; it is not the camera-capture cost. These are CPU,
driver and synchronization wall times, not isolated GPU kernel timings.

The source requested 30 FPS, but observed video delivery was about 19.9–20.9 FPS
for Current and 29.2–29.9 FPS for the candidates. The canvas source's own timer
shares the application thread, so its delivery changes with application load.
This supports synthetic responsiveness comparisons and is not a substitute for
physical-camera or moving-wearer measurements.

The [first profiling receipt](output/sustained-2026-09-09T11-53-11.640Z/report.json)
remains **failed as a complete harness run** after three valid measured segments:
Emscripten emitted `INFO: Created TensorFlow Lite XNNPACK delegate for CPU.` via
`console.error`. A [partial-segment audit](output/sustained-2026-09-09T11-53-11.640Z/partial-segments-audit.json)
reverified all 206 source/model files and 40 compiled files before the QA-only
classification fix. Its exact original runner bytes are retained with their
matching hash. The amended harness retains this exact notice and original level,
and still fails every other error. The notice is evidence of CPU delegate
initialization; reported GPU delegates do not imply GPU-only inference.

The remaining [Amber multiclass trio](output/sustained-2026-09-09T11-57-03.507Z/report.json)
and [Tom Ford six segments](output/sustained-2026-09-09T11-59-32.340Z/report.json)
pass as complete selected runs. The original rotating orders were preserved.
The combined receipt explicitly includes the first tooling interruption and
reverifies its original QA script through the preserved exact-byte copy.
[Compiled build verification](output/production-build-2026-09-09T11-52-59.357Z-verified.json)
confirms all 40 production files stayed unchanged; the initial manifest also
verified the served HTML and six referenced JavaScript/CSS entries byte-for-byte.

The [preliminary full archive replay](output/matched-2026-09-09T11-43-29.876Z/report.json)
passes all 32 generated and 24 recorded pairs and controls with exact pixels,
geometry and source ownership. It precedes the subsequent fallback-read safety
and direct accepted-first presentation fixes. Final post-fix verification is
tracked separately; this preliminary result does not replace it.

The [bounded shared-context smoke](output/matched-2026-09-09T11-34-18.477Z/report.json)
passes all four selected steep-down comparisons: both glasses and both hair
models. Test 1, Test 2 and accepted archived native/hair/background bytes and full
geometry match exactly. Shared-camera/two-readback metrics, hair-off/no-face
auxiliary-work skips, ownership, return-to-first-pair and lifecycle controls pass.
This partial smoke does not cover the full 56-case archive or establish speed.
An earlier [four-exact-pair tooling failure](output/matched-2026-09-09T11-33-26.358Z/report.json)
passed the same pixels and controls, then incorrectly required the old Current
renderer module. The corrected provenance assertion requires actual Test 1 and
Test 2 native modules, shared-readback helper and pinned geometry dependencies.

The first atlas Test 2 smoke stopped before completing any pair because paired clean
diagnostics were absent. Its [initial receipt](output/matched-2026-09-09T11-24-46.310Z/report.json)
is retained. A [single diagnostic retry](output/matched-2026-09-09T11-25-09.317Z/report.json)
identified `The native paired copy/readback failed.` at the first steep-down
Tom Ford/hair-only frame on Intel Arc 140T D3D11: `sharedCameraReady=false`, two
copy calls, one readback, zero extraction time. Test 2 safely rejected the mask
and retained beauty; no full paired or performance pass is claimed from these
failed smoke receipts. No runtime fix or tolerance change was made by QA.

The first atlas fix also [failed before completing a pair](output/matched-2026-09-09T11-29-26.712Z/report.json),
reporting `GL 0x502`. A [bounded diagnostic](output/matched-2026-09-09T11-30-25.311Z/report.json)
attributed fresh `INVALID_OPERATION` to both `blitFramebuffer` copies; the read
itself emitted no error. The QA-only `--gl-errors` flag wraps selected calls and
replays observed errors to the original caller. It is disabled in normal QA and
is not performance evidence. Atlas attempts stopped after this diagnosis. Test 2
uses two direct native readbacks in the shared context; this
retains the separate-context/source-upload saving without claiming one readback.

Earlier Test 1 evidence is preserved in
[its QA record](../../performance-candidate/qa/README.md). Its final 56-pair
single-presentation comparison passed, but an earlier warmed multi-pose run
differed at one protected optical/nose pixel `(452,262)`, maximum channel delta
28, in Current's native output. Test 1 and the archive agreed. A fixed 12-repeat
probe did not reproduce that variance, so its cause remains unresolved. Each
pipeline preserving its own native pixel is not cross-pipeline protected-pixel
equality. Later passes do not erase that failed evidence.
