# Test 2 and live performance profiling

Test 2 is now the owner-selected current base (September 9, 2026). This page
starts with **Current · Test 2** selected; **Long-hair checkpoint** preserves
the original `current` report ID. Test 3 remains experimental. Older evidence
below uses the original labels. No renderer was changed during this selection.

The selected development base alongside the preserved long-hair pipeline and
Test 1. Neither earlier implementation is replaced. Run from `ar_v4/`:

```powershell
npm run dev:stage2
```

Open http://127.0.0.1:8072/experiments/performance-stage2/live.html. The isolated
production equivalent is `npm run build:stage2` then `npm run preview:stage2`.
Use `-- --port 8074` with the preview command when the dev server also runs.

Select glasses and a hair model, open the camera, and switch between Current,
Test 1 and Test 2. Only the selected pipeline processes live frames. All three
renderers are initialized, so resident memory is higher than a standalone app.
Hold finishes the current owned image, stops camera/workers and obtains its full
mask. All three held outputs use that same image/detection/mask. Hair on/off and
local diagnostic download remain available. Resume starts a new owned session.

## Measure the actual camera

**Measure all three** warms each pipeline for at least five seconds and three
tracked hair frames, then collects at least 30 seconds. Keep your face visible
and repeat the same slow down/up/left/right movements. It uses Current, Test 1,
then Test 2; this is a convenience comparison with a fixed order, not a
counterbalanced performance study. A missing valid face/mask during warmup,
manual algorithm/hair change, Hold, stop or session failure leaves an explicitly
incomplete report. No benchmark automatically starts a camera.

Completed segments appear in a table with FPS, frame-interval p95, tracking and
mask coverage. Full coverage means tracking on every measured frame and, when
hair is enabled, a valid mask on every tracked frame. Missing work remains in
the timing results and is explicitly labeled; completion alone is not equivalent
workload coverage or visual acceptance.

**Download timings only** saves numeric timings, counters, dimensions and backend
labels locally. It contains no camera images, detections, masks or source hashes.
The same JSON appears in a readonly field for copying when an embedded browser
does not support the file download. Exporting is explicit, outside the normal
per-frame profiling work.
The separate held comparison download does contain the explicitly held image.

The live panel uses the latest ten seconds of the current contiguous algorithm
and hair setting. Completed AR FPS is measured independently of video delivery.
Video delivery counts browser-presented video frames, not a calibrated sensor
frame rate. Publication means completed canvas submission; sensor buffering and
physical display scanout are not measured. p95 describes the slower tail of
processing or frame intervals, not average FPS. Parallel stage durations overlap
and must not be added. Face inference is separated from worker extraction,
validation and request wall time; transport/scheduling is an elapsed residual,
not a measurement of physical transfer alone. GPU identity/delegate does not
establish where every internal model operation runs.

Test 2 retains the legacy `cleanCameraMs` field for compatibility, but it measures
only validation of already owned clean-camera pixels. Current and Test 1 include
the independent clean pass in that field, so those values cannot be compared
directly. Test 2 performs that work during preparation: see
`candidatePerformance.nativePipeline.native.cleanSubmitMs` and
`candidatePerformance.nativePipeline.native.sharedReadback.cameraReadbackMs`.
Their sum covers the clean render call and native readback; row extraction is
reported separately in `sharedReadback.extractionMs`, combined for beauty and
camera. Submission timings are elapsed CPU call durations, which can include
driver waits and uploads; they are not pure GPU execution times. Readback timings
also include synchronization and error checks. These nested stages are already
included in preparation and total render time, so do not add them again.

## Candidate scope

Test 2 retains the original native beauty rendering, physical materials,
multisampling, dimensions, exact frame/pose/mask ownership, temple geometry,
hair composition, continuity and final optical/nasal/background safeguards.
It shares the native renderer's camera texture/context for the clean camera
pass. The original beauty bytes are owned before the clean pass changes the
framebuffer. Hair off and no-face frames skip optional clean work; a failed
optional clean pass retains the protected beauty and rejects that hair result.

The initial combined-transfer atlas experiments were rejected by this GPU.
Their errors and receipts remain in [QA evidence](qa/README.md); they are not
presented as successful optimizations. The tested smaller candidate uses two
direct readbacks. It targets a duplicate context and texture upload, not fewer
transferred image bytes. The 30 FPS figure is a target, not a promised result.

## Validation

```powershell
npm run test:stage2
$env:PERFORMANCE_QA_D3D11='1'
npm run test:stage2:browser
npm test
```

The three-way production tests cover both glasses and hair models, exact held
comparisons, live valid-mask availability, pending-frame Hold/switch ordering,
full-mask failure, cancellation, stop and restart. The optional matched and
sustained synthetic harnesses are described in [qa/README.md](qa/README.md).
Run heavy browser jobs serially, including physical-camera measurements.
Private recording files and accepted/Test 1 sources remain hash-verified and
unchanged. Current results and limitations belong in
[the review notes](../../docs/REVIEWS.md). Generated stills, synthetic-camera
tests and a single desktop session do not establish sustained phone smoothness
or owner visual acceptance.
