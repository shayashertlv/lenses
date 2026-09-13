# Offscreen render worker experiment

This separate candidate executes G's beauty rendering, rear-temple rendering,
PBO/fence download and complete CPU hair composition in one real module worker.
Capture, face/hair inference orchestration and visible publication remain on the
main thread. The source and native viewport sizes, four G options, geometry,
materials, masks and every final nose/front/outside/background guard stay fixed.
No candidate has been accepted or promoted.

`RenderWorkerRenderer` prepares one owned image, then completes it with the exact
mask selected by the existing live wait. `complete()` is asynchronous and still
private; `finish()` publishes synchronously under the existing FramePump owner.
Session, increasing source generation, source/detection hashes and RPC IDs are
checked. One RPC and one prepared image are allowed; there is no message queue.
Source and mask transfer buffers are independent copies. Both complete variants
return independent RGBA buffers so toggling never renders an unmatched image.
Hold reconstructs the exact retained input; explicit export uses the worker's
native asynchronous PNG encoder. Abort, failure and disposal terminate the
worker and reject its pending operation. Unsupported worker/WebGL initialization
fails visibly and cannot be counted as an optimized G fallback.

The worker port is reproduced by `node experiments/efficiency-lab/render-worker/port-renderer.mjs`.
Its nine copies change only OffscreenCanvas factories/types, real Three.js
CanvasTexture generic arguments, asynchronous PNG encoding and one private
completed-output copying method. All other imports resolve to the accepted
dependencies. The reproducibility test rejects any hand-edited algorithm change.
The accepted source files are never written by the port script.

Transfer costs are intentionally visible in `candidatePerformance.renderWorker`:
source and mask copy time/bytes, prepare and completion round trips, worker
prepare/compose/output-copy time, output bytes and main publication time. Moving
CPU work off the main thread does not establish a throughput win; two complete
variant copies and message scheduling can offset any benefit. Physical iPhone
throughput, thermal behavior and wearer motion still need measurement/review.

Checks:

```
node --test experiments/efficiency-lab/render-worker/*.test.ts experiments/efficiency-lab/render-worker/*.test.mjs
npx playwright test --config experiments/efficiency-lab/render-worker/playwright.config.ts
```

The native test runs both glasses and both actual hair models, compares raw
accepted/hair pixels, geometry and final guards over five synthetic pose
controls (front, down/up and both yaw), and checks held export. It also exercises
real worker cancellation, protocol failure, stop/restart, source alpha/resize
and native width-cap behavior. Synthetic transformed poses are implementation
controls, not measured anatomy or evidence of real wearer motion.

Preparation queues variant changes without republishing the worker's preceding
frame when returning from G. The latest chosen variant is applied at the owned
synchronous finish boundary; explicit toggles after finish remain immediate.
The standard `publishMs` stage measures visible main-canvas publication;
`renderWorker.workerPublishMs` separately records the private worker upload.
