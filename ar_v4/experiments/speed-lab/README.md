# Speed experiment comparison

G Combined is the owner-selected current base. The eight-way comparison keeps
committed Test 2 (`8baa16c`, **speed testing**) as the previous reference.
`npm run dev` now opens this page with G selected. The earlier accepted renderers
and Test 2 launch configuration remain available through their own commands.

```powershell
npm run dev:speed-lab
```

Open [the development preview](http://127.0.0.1:8082/experiments/speed-lab/live.html).
For a production preview, run `npm run build:speed-lab`, then
`npm run preview:speed-lab`; open
[the comparison](http://127.0.0.1:8083/experiments/speed-lab/live.html).
Camera access starts only after Open camera. Frames remain in this browser.

## Compare

Choose glasses and a hair model, open the camera, and switch Algorithm live.
Hold frame stops the camera and workers, completes the full-mask diagnostic,
and compares all eight on exactly the same source, pose and mask. Held switches
show cached images immediately. Resume starts a fresh session. Both glasses,
both hair models, down/up, left/right yaw and the nose/front need visual review.

| Mode | Experiment |
| --- | --- |
| Previous base · Test 2 | Unchanged committed renderer and serial frame scheduling. |
| A · Reuse camera pixels | Reuse the exact owned opaque sRGB source instead of rendering and downloading the clean background again. Unsupported input explicitly falls back. |
| B · Fresh-frame scheduling | Keep a replaceable latest captured image ready instead of always waiting for another future camera callback. Same Test 2 renderer. |
| C · Prewarm temples | Exercise and complete the nonzero rear-temple path once, before its first required use, then discard temporary ownership. |
| D · Fewer image copies | Borrow the owned live source while rendering; retain independent bytes for Hold/export after that live canvas is released. |
| E · Async GPU downloads | Queue real WebGL2 pixel-pack-buffer reads and poll fences without busy waiting. Unsupported or failed reads use a fresh synchronous rendering of the same pair. |
| F · Overlap inference | Start the next owned image's face/hair work while finishing the current image, with at most two owned images. Same Test 2 renderer. |
| Current · G Combined | A, C, D and E together with F's bounded fresh-image scheduling and inference overlap. |

Only two renderer instances are created: the base and one configurable candidate.
Only the selected renderer processes each live image. Source, pose and mask
always travel together; late masks never attach to a newer image. Resolution,
geometry, composition, continuity and final nose/front checks stay fixed.

## Measure

Select an experiment, then choose **Measure Test 2 vs selected**. Each mode gets
five seconds of warmup and thirty seconds of measurement, beginning on completed
frames with valid tracking/masks. Repeat the same movements. Reverse the order
on a second run. The table and timing download include missing-tracking/mask
frames and disclose their coverage, so skipped work cannot silently count as
a successful speed improvement.

Compare update rate **and** frame age/interval p95. Prefetch can increase FPS
while displaying older captures; these are different outcomes. Captured frame
age includes pending-image wait and ends at canvas submission. Camera buffering
and display scanout are unmeasured. Scheduling-stage medians have different
meanings in serial and pumped modes; the report states those meanings explicitly.
Overlapping stage times must not be added together.

Actual-path counters disclose source reuse, borrowing, PBO retrieval and fallback.
Readback totals include retrieved PBO bytes and any hidden prewarm read. Timing
downloads contain no images, landmarks, masks or image identity hashes. The
separate explicit held-comparison download includes the held image.

C targets first-use stalls rather than steady FPS. Start a fresh session with C
selected to inspect its startup cost and the first down/up/yaw transition.
Switching after another candidate has warmed the renderer is not a cold-start
comparison. A warmed benchmark cannot establish that the first-use stall was
removed. E/F may contend with inference on the same GPU and can be slower.

## Checks and evidence

```powershell
npm run test:speed-lab
node --test experiments/speed-lab/qa/protocol.test.mjs
npm run test:speed-lab:browser
npm test
```

The production browser suite covers all eight live modes, both glasses/hair
models, exact held output, cancellation, failed full-mask upgrade, restart and
the timed two-mode comparison. It uses a synthetic camera and real workers.
Optional [matched QA](qa/README.md) uses preserved private inputs without
changing them; it is not required to run this preview. Current outcomes and
limitations are recorded in [REVIEWS.md](../../docs/REVIEWS.md).

The owner reported that F and G felt far superior and explicitly selected G.
That qualitative feedback and the matched checks support this promotion;
sustained physical-camera throughput, motion-to-photon delay, mobile smoothness
and thermal behavior remain unmeasured. Held equality and synthetic-camera tests
are bounded checks, not proof of universal visual equivalence.
