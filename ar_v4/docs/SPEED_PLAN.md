# Speed work after Test 2

Test 2 is the owner-selected base. These are proposed experiments, not changes
to its renderer. Target both completed paired-frame throughput and uneven frame
intervals. UI responsiveness alone is not a higher AR frame rate.

## What the current measurements imply

The uploaded desktop run delivered camera frames near 29.6 fps but Test 2
completed 10.16 AR frames/s. Median processing was 69.19 ms, with a separate
24.74 ms next-callback wait. Face inference was about 23.47 ms, render work
31.99 ms, and native beauty/clean downloads together 15.12 ms. Hair extraction
was 25.75 ms but overlaps other work; these numbers must not simply be added.
Canvas publication does not measure sensor buffering or physical display delay.

Face and hair inference already run in separate workers. Hashing, hair inference
and reference preparation overlap. Merely declaring more functions `async`
does not reduce synchronous inference or GPU synchronization. The useful changes
are to remove redundant work and overlap genuinely independent operations.

## Ranked experiments

1. **Reuse the already owned camera pixels for the clean background.** The
   scheduler already reads exact source RGBA for hashing. Test 2 subsequently
   renders that camera image into a clean GPU framebuffer and downloads it.
   A read-only audit found byte-for-byte clean/source equality in all 56 archived
   generated/recorded cases. This supports testing removal of the clean draw,
   one full readback, row flip and allocation for eligible frames. Preserve the
   native glasses beauty render, CPU compositor, continuity and every final
   guard. Require exact dimensions, opaque sRGB source, unchanged sampling and
   explicit ownership; retain native clean capture for unsupported conditions.
   Existing still evidence does not prove equivalence on all browsers/cameras.
   Main sites: `performance-stage2/live-main.ts` source read;
   `native/renderer.ts` beauty/clean capture; `native/shared-readback.ts`.

2. **Consume a fresh available frame without an extra future-callback wait.**
   Maintain one owned processing pair and a latest-frame handoff with no backlog.
   Discard superseded unprocessed frames. Preserve image, detection, hair mask
   and presentation identity together; never put old pose/mask onto new video.
   Callback metadata may lag the video element, so do not use a stale callback
   identity to label a later capture. Measure actual freshness and intervals.
   Stop, Hold, switch, failure and restart must revoke pending work correctly.
   The measured 24.74 ms is a target to investigate, not a guaranteed saving.

3. **Prewarm the nonzero temple branch.** First activation cost roughly 100 ms
   in each optimized pipeline, subsequent calls roughly 6 ms. Initialize the
   required material/render path before interaction and clear all temporary
   frame ownership afterward. Measure startup cost and first down/up/yaw motion
   separately from steady FPS. The telemetry does not prove which initialization
   operation caused the stall.

4. **Make remaining GPU readbacks genuinely asynchronous.** Test 2 downloads
   beauty before submitting clean, blocking useful GPU/CPU overlap. A bounded
   WebGL2 pixel-pack-buffer/fence path could enqueue exact native bytes and let
   other independent work proceed, retaining the CPU compositor. Reuse storage;
   never busy-wait or queue an unbounded series of old frames. Default-framebuffer
   MSAA, read/draw bindings, context loss and abort ownership need explicit tests.
   This differs from Test 3: fewer bytes there introduced multiple new shader
   passes, downloads and state queries and did not improve observed throughput.
   Async improves scheduling only where there is independent work to overlap.

5. **Remove duplicate copies and prepare a lean runtime.** The outer hair
   renderer and temple renderer both copy the source; mask validation copies an
   already transferred owned mask; hashing creates additional input copies.
   Replace these only with a tested immutable ownership contract, retaining
   callback-lifetime copies where required. Pool buffers only after all leases
   expire, including Hold/export. A standalone base can lazily initialize the
   comparison renderers and throttle telemetry UI. This primarily targets memory,
   startup and UI pressure; only one comparison renderer currently processes
   each live frame, so removing inactive renderers is not a threefold FPS gain.

6. **Pipeline inference for the next owned image with current-frame finishing.**
   After the simpler savings are measured, a bounded two-slot pipeline could
   start face/hair work for image B while rendering/composing image A. Every
   image must retain its own immutable pose, mask and buffers; face timestamps
   remain ordered, and only complete pairs publish. GPU contention may erase
   the throughput gain, and prefetch may increase input age despite higher FPS.
   Compare both latency and throughput; do not turn this into a backlog or mix
   results between images. This is real task overlap, rather than an `async`
   wrapper around the same blocking sequence.

Each experiment should be independently switchable against unchanged Test 2.
Measure medians, p95 intervals, first-use stalls, tracking/mask coverage and
eligible/fallback frame counts on matched workloads. Re-run exact held pixels,
geometry and nose/front/outside-arm safeguards for both glasses and both hair
models, down/up/both yaw, followed by owner visual review and live motion.
Use equal workloads and counterbalanced runs. Phone thermal behavior remains
unmeasured. Lower resolution, skipped inference and pose prediction deliberately
change the quality/latency tradeoff and are not the first preservation strategy.

## Contrast, exposure, warmth and depth

The face task estimates landmarks and face pose; the hair task assigns category
membership. Hair confidence is not distance or front/back ordering. Photometric
adjustments can make existing cues easier or harder to recognize but add no
measured depth. Warmth changes color cues; strong contrast can clip shadows or
highlights and remove useful boundaries. Ordinary contrast/warmth sliders have
not been shown to help these pinned models.

If poor-light robustness becomes a separate experiment, compare original input
with mild luminance/exposure correction on an inference-only copy. Preserve the
displayed original, source/processed identities and exact transform parameters.
Start with stable per-clip settings to avoid brightness-induced jitter. Evaluate
normal light, dim light and backlight for tracking loss, stationary pose jitter,
hair boundary errors and total latency. This can change inference outputs, so
it cannot be promised to preserve visuals. It is not a depth reconstruction plan.

Sources: [MediaPipe face task and synchronous calls](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/web_js),
[segmentation outputs and preprocessing](https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter/web_js),
[mask ownership/conversion costs](https://developers.google.com/edge/api/mediapipe/js/tasks-vision.mpmask),
[WebGL blocking calls and asynchronous downloads](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices),
[Web Crypto digest input copying](https://www.w3.org/TR/webcrypto/#SubtleCrypto-method-digest).
