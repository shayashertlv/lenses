# Test 3: GPU hair composition

Test 2 is now the owner-selected current base (September 9, 2026). This page
starts with **Current · Test 2** selected; **Long-hair checkpoint** preserves
the original `current` report ID. Test 3 remains experimental. Older evidence
below uses the original labels. No renderer was changed during this selection.

This is a separate, unaccepted development candidate. Current, Test 1 and Test 2
remain available and unchanged. Test 3 preserves the original native geometry,
materials, multisampling, dimensions, exact image/detection/mask pairing and final
optical, nose, outside-editable and background protections. A speed improvement
does not establish visual acceptance or promote this candidate.

**Measured outcome:** the new path reduces explicit renderer readback bytes by
87.5%, but it did not produce a consistent speed or smoothness improvement on
this computer. In the sustained synthetic comparison, aggregate Test 2/Test 3
rates were 10.63/10.65 fps; Test 3's p95 frame age was worse in all four model
combinations. Keep this as an experiment for comparison, not a replacement.

From `ar_v4/`:

```powershell
npm run dev:stage3
```

Open http://127.0.0.1:8076/experiments/performance-stage3/live.html. For the isolated
production build and a separate preview port:

```powershell
npm run build:stage3
npm run preview:stage3 -- --port 8078
```

The comparison offers Current, Test 1, Test 2 and Test 3. Only the selected
pipeline processes live frames, but all four renderers are initialized, so
resident memory exceeds a standalone implementation. Hold retains the exact pair
and full mask for comparison; it stops the live camera and workers. Resume starts
a new owned session. Timing downloads contain numeric measurements, not camera
images. The separate held diagnostic download contains the explicitly held image.

## Implemented path

The original beauty render still uses its original opaque, multisampled default
WebGL framebuffer. Test 3 uses `copyTexSubImage2D` to capture those bytes into a
same-size RGB8 texture before another pass can touch them. When hair is requested
and a face is valid, the same
native renderer and already uploaded camera texture produce a clean camera image,
captured into a second RGB8 texture. This retains the original lens transmission
and color behavior; beauty is not rerendered into a differently configured target.

The prior RGBA8 atlas was invalid for this framebuffer. Its RGBA8 format differed
from the opaque RGB8 source, and its second slot used different resolve rectangle
coordinates. Test 3 uses separate same-size textures and identical coordinates.
See the [WebGL2 framebuffer rules](https://registry.khronos.org/webgl/specs/latest/2.0/)
and [GLES3 section 4.3.3](https://registry.khronos.org/OpenGL/specs/es/3.0/es_spec_3.0.pdf).

The initial same-size RGB8 `blitFramebuffer` implementation passed hardware
generated cases but failed on the second recorded `original-55` presentation on
SwiftShader: beauty was black, the clean camera stayed exact, and the independent
reference check rejected the pair. Adding a diagnostic read before capture made
the failure disappear, so that instrumented result could not validate live
behavior. Replacing only the capture command with `copyTexSubImage2D` passed the
uninstrumented hair-only → multiclass sequence for both eyewear models, including
zero full-color live reads and lifecycle controls. The independent 29-file PNG
audit also passed. This verifies the replacement on those cases; the underlying
browser/driver cause remains unproven. The original failure, instrumented probe
and successful replacement receipts remain available:

- [Uninstrumented blit failure](qa/output/matched-2026-09-09T13-36-39.272Z/report.json)
- [Diagnostic reads masking the failure](qa/output/matched-2026-09-09T13-41-55.313Z/report.json)
- [Uninstrumented copy replacement and audit](qa/output/matched-2026-09-09T13-44-08.865Z/report.json)

The complete 56-pair replay of the final copy implementation subsequently passed;
its final receipts are linked below.

For an exact-zero rear drop, the accepted temple result is the native beauty, so
hair composition can stay on the GPU. Integer category and region textures retain
the CPU top-down coordinates; native color textures retain framebuffer bottom-up
coordinates. Shaders use explicit integer texel coordinates and byte arithmetic.
They apply the same category boundary rule, CPU continuity result and final
authoritative protected-pixel copy, with blending, dithering and color conversion
disabled for these raw passes.

A packed flag pass supplies the existing CPU statistics and topology-only
continuity calculation without downloading full color images. A separate reference
reduction checks the clean camera outside the region union. After composition,
an independent shader compares output against beauty for all four final guard
categories; a componentwise maximum reduction must be zero in every category.
The reduction proves zero changed guard pixels on success; it does not claim to
count every changed pixel in a rejected image.

Nonzero rear-drop geometry still uses the reviewed CPU composition path and its
independent branch renderer. Its GPU beauty/clean images are read back explicitly
for that fallback. The current frozen 56-case set contains **44 GPU-eligible
exact-zero cases** (28 generated, 16 recorded) and **12 nonzero CPU cases**
(4 generated, 8 recorded). The final replay verified execution of these paths by
their transfer counters as well as exact rendered output. These counts describe
the evaluated cases, not live usage percentages. CPU fallback parity cannot stand
in for GPU-path evidence.

## Transfers, publication and ownership

A successful eligible live hair frame performs two GPU captures and three compact
GPU-to-CPU reads: `4 * ceil(width / 4) * height` flag bytes, four reference bytes,
and four final-guard bytes. At 1280 × 853 that is 1,091,848 explicit readback bytes,
compared with 8,734,720 bytes for the two full RGBA beauty/clean images alone.
Category, region and detached-pixel textures still require uploads; statistics
and continuity still require CPU work. These counters concern the renderer, not
source hashing, inference or every transfer inside the browser.

Full RGBA reads remain available for explicit held diagnostics and CPU fallback.
Eligible diagnostic export reads beauty, clean camera and final output; pre-export
and post-export metrics are retained separately. Readback counts are cumulative
for the current leased pair, including diagnostic reads. `gpuStorageBytesRequested`
describes requested texture storage, not measured driver memory allocation.

Preparation never paints the public canvas. After successful final checks, a raw
pass restores the selected beauty/output on the private native canvas; the public
GPU-preferred 2D canvas receives it through `drawImage`. The browser may perform
implicit copies or synchronization here. A context preference is not proof of a
zero-copy GPU publication path, and this cost remains in publication timings.
Correctness QA deliberately reads public pixels; sustained performance measurement
must avoid those diagnostic readbacks.

GPU frames carry a source generation and reject stale leases after the next native
presentation or disposal. Raw passes restore their touched GL state so Three's
state caches remain consistent. Capture failure invalidates the lease and renders
fresh native beauty for the checked CPU fallback. A failed reference/composition
or final guard rejects that hair result and shows the current beauty; it does not
silently claim successful GPU hair composition. Context loss requires the session
failure/restart path. Unsupported GPU behavior can therefore reduce availability
of the experimental hair result even while Current and Test 2 remain selectable.

## Timing interpretation

`gpuTransferMetrics` separates capture, upload, packed flags, reduction, composition,
publication, explicit diagnostics and GL state save/restore. Readback counters
distinguish compact reads from full-color reads. These are elapsed CPU call
durations, including driver work and synchronization, not pure GPU execution
timestamps. Shader compilation and state save/restore are also included in their
enclosing operations; nested measurements must not be added again to total time.
The legacy `cleanCameraMs` field validates already owned pixels on the CPU path;
actual native clean work is measured during preparation. Compare complete frame
time and completed publication cadence across pipelines rather than treating that
legacy field as a comparable clean-render measurement.

Completed AR FPS differs from browser video delivery. Frame age ends at canvas
submission and excludes sensor buffering and physical display scanout. The
synthetic camera shares the application thread and does not establish physical
camera or phone smoothness. Thirty FPS remains a target, not a promised result.

## Evidence and validation limits

The isolated [capture probe](qa/output/framebuffer-capture-2026-09-09T13-06-54-844Z.json)
confirmed exact RGB8 capture and GPU texel selection at 192 × 128 and 1280 × 853 on
Intel Arc 140T/D3D11 with four samples. RGBA8 and offset resolve negative controls
reproduced `GL 0x502`.

The integrated [GPU helper smoke](qa/output/gpu-frame-smoke-2026-09-09T13-27-52-163Z.json)
passed at 23 × 17 and 1280 × 853: exact native colors, exact composition/statistics
against the existing CPU compositor, all four guards, restored GL state and stale
lease rejection. Deliberate protected-output corruption was detected and blocked
from publication while untouched beauty remained available. This is mechanism
evidence with synthetic pixel fixtures, not full AR appearance or speed evidence.
Earlier interrupted probe receipts are retained beside the passing receipt.

```powershell
npm run test:stage3
$env:PERFORMANCE_QA_D3D11='1'
npm run test:stage3:browser
npm test
```

The final [matched AR replay](qa/output/matched-2026-09-09T13-45-15.315Z/report.json)
passed **56/56 pairs and all controls**: 32 generated cases on hardware D3D11 and
24 recorded cases on SwiftShader. Both eyewear models, both hair models, downward,
upward and both yaw controls retained exact accepted/hair/background pixels and
geometry against Test 2 and the archived references. The 44 GPU cases exercised
two native copies, three compact reads and zero full-color reads before export;
the 12 nonzero rear-drop cases exercised the explicit CPU fallback. Neutral
first-frame and return-after-other-poses controls, held variant switching and
failure/cancellation lifecycle checks also passed.

The independent [426-file PNG audit](qa/output/matched-2026-09-09T13-45-15.315Z/independent-png-audit.json)
passed exact byte, guard, geometry and transfer-count checks and reverified 175
previously preserved files. The focused Stage 3 suite passed **30 tests**; strict
TypeScript and the isolated production build passed. Seven production-browser
scenarios pass: exact four-way held outputs for both glasses/hair combinations,
stop/restart, startup cancellation, pending Hold/switch ordering and failed full-mask
upgrade recovery. The first run passed three controls and exposed a QA assumption
that its nonzero-drop portrait should use the GPU. After correcting that assertion
to require the actual pose's GPU or CPU path and branch-read count, all four
remaining scenarios passed against the unchanged build. Initial failure traces
remain in `test-results/integration-d3d11`; the four completed reruns are in
`test-results/integration-d3d11-reviewed`. This portrait exercises CPU fallback;
the matched 44 GPU cases and sustained GPU frames provide separate GPU evidence.

Desktop and 390/360-pixel layouts were inspected without page overflow. The
responsive table check used clearly labeled placeholder data, not timing results.
Procedures, preservation hashes, explicit GPU/fallback coverage and current
receipts are documented in [QA evidence](qa/README.md). Run heavy browser jobs
serially, including physical camera measurements. Passing synthetic/recorded checks alone does not replace
the owner's visual acceptance, prove anatomical accuracy, or establish sustained
mobile performance.

## Sustained comparison

The [final production measurement](qa/output/sustained-2026-09-09T13-55-56.542Z/summary.json)
contains 2,555 completed frames over 240.12 measured seconds: eight 30-second
segments after warmup, with alternating Test 2/Test 3 order. It uses a static
generated 960×640 synthetic camera on Intel Arc 140T/D3D11. Every measured frame
has tracking, a paired mask and hair edits; all 1,279 Test 3 frames use GPU hair
composition with zero full-color renderer readbacks before diagnostics.

| Glasses / hair | Test 2 FPS | Test 3 FPS | Test 2 age p95 | Test 3 age p95 |
| --- | ---: | ---: | ---: | ---: |
| Amber / hair-only | 11.86 | 11.66 | 93.04 ms | 104.54 ms |
| Amber / multiclass | 10.10 | 10.59 | 103.50 ms | 108.48 ms |
| Tom Ford / hair-only | 10.40 | 10.73 | 104.43 ms | 106.90 ms |
| Tom Ford / multiclass | 10.16 | 9.63 | 104.91 ms | 116.49 ms |

Age starts at application capture and ends at completed canvas submission. It
does not measure sensor buffering or display scanout. Pooled median render work
was 34.28 ms for Test 2 and 39.11 ms for Test 3. Test 3 shortened preparation
(19.63→9.00 ms) but lengthened finish (14.44→29.55 ms). These distribution medians
are not additive. Native copy, compact flags, CPU region/statistics work, uploads,
guard reductions and state management all still cost time. Deferred GPU work can
complete during later flag reads, so those durations are not transfer-only costs.

At this resolution, renderer reads fell from 4,915,200 to 614,408 bytes/frame.
Test 3 also uploads 1,843,200 bytes/frame for categories, regions and detached
pixels, and performs three compact synchronization reads versus Test 2's two full
reads. This experiment shows that moving fewer bytes alone did not make this
pipeline faster. Source hashing and face inference also varied between segments;
small FPS crossovers do not establish a causal improvement.

Synthetic video delivery was lower in Test 3 (about 23–25 versus 25–28 fps), and
reported long tasks were 39 versus 1. The source timer shares the application
thread, so that coupling limits interpretation. All 206 frozen inputs/runtime
files, 46 production files, 8 served entries and 175 preserved files were verified.
Physical-camera Test 3 motion, phone performance and visual acceptance remain
unmeasured. Full per-stage counters and timing limitations are in the QA record.

The required full `npm test` passes strict types, assets/build, 124 unit tests
and 8/9 reference-browser scenarios, including recorded replay and cancellation.
It still exits 1 because the separately modified `test_` test expects a third
picker option absent from the accepted two-model build; this stops the command
before its later hair integration suite. See `logs/npm-test-final.log`. The new
experiment's 30 focused tests and seven production scenarios are separate passes;
no accepted test, model registry or deadline was changed to hide that mismatch.
