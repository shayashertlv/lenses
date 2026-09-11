# Efficiency research after G Combined — September 10, 2026

G's strongest next efficiency target is the remaining serialized GPU-to-CPU
work, especially the nonzero rear-temple branch. Result transport, buffer
allocation and early prefetch work are other credible targets. Their value
needs a G-specific critical-path trace with actual hair edits and motion before
implementing or selecting a candidate. No new optimization or benchmark was run
during this research.

The reviewed checkout is `b9142b2a3b957445f378d8012eea7e27ca68fd0b` on
`codex/ar-v4-nose-occlusion`; HEAD matches the local origin tracking reference.
Default scripts use `vite.combined.config.ts` and select `combined` in
`experiments/speed-lab/profiles.ts`. Unrelated source/model/testing-ground changes
and untracked experiments were present. This work adds research documentation
only. Test 2 (`8baa16c`), accepted long hair and perfect temples stay available.
`SPEED_PLAN.md` is the earlier Test 2 proposal: G already implements its main
source-reuse, fresh scheduling, overlap, prewarm and async beauty-read ideas.

## What G actually does

| Phase | Work and ownership | Remaining serialization |
| --- | --- | --- |
| Startup | Open camera; import renderer; create Test 2 then G; each creates baseline and branch native renderers plus continuity resources; start face/hair initialization. | Both comparison renderers are created before processing starts. Hair initialization overlaps face initialization, but starts after renderer creation. |
| Capture | Inside the current video callback, draw one image to an opaque sRGB canvas at the existing maximum 1280-pixel edge, read full RGBA, verify current video identity/metadata. | Main-thread drawing and `getImageData`; new canvas and RGBA allocation. Only a captured, unprocessed pending image can be replaced. |
| Inference | Hash full source; snapshot full-size hair bitmap; draw the same image to the existing maximum 640-pixel face input and snapshot its bitmap. Face and hair have separate local workers. | One face request and one hair request per worker. Hair admission waits for source hash and the preceding hair result's client validation/hash. Face timestamps remain ordered. |
| Native preparation | Apply unchanged face surface, bridge/nose pose and visibility; render beauty. Eligible G input supplies the exact clean camera RGBA directly. | Native GL calls submit on the main thread. Source reuse removes the clean GPU draw/read; it does not remove initial capture readback or beauty rendering. |
| Download and temples | Queue beauty into a PBO, fence/flush, yield while polling, retrieve and flip rows. Then prewarm/process the independent rear-temple branch. | Nonzero branch submission starts after beauty retrieval; its full readback remains synchronous. PBO retrieval and row flip still do CPU work. |
| Finish and publish | Same-pair hair wait of up to the retained 8 ms timer after preparation; protected temple/hair composition, continuity, final nose/front/outside-arm checks; `putImageData`. | Composition, independent checks and canvas submission are synchronous. A late/missing mask falls back; its worker cost can continue after publication. |

Source map: `live-main.ts:511` and `comparison-renderer.ts:30` for startup;
`live-pump.ts:56–110,143–157` for capture/inference;
`frame-pump.ts:101–125,206–235` for scheduling;
`native/renderer.ts:360,502` and `native/pbo-readback.ts:60–109` for native work;
`temples/renderer.ts:222–239,300–345` and `renderer.ts:323–395` for finishing.
Paths without a prefix here are under `experiments/speed-lab/`.

G starts image N+1's inference preparation when N enters rendering preparation.
The synchronous prefetch prefix (source-copy/hash invocation, face-input drawing)
runs before N's renderer preparation. The face worker request itself follows
bitmap creation. Once N+1 inference starts, its second slot is locked; newer
offers skip capture. Rendering/publication stay serial and there are at most two
owned image packets. There is no third-frame backlog.

This is real potential CPU/worker overlap. Each MediaPipe inference call is still
synchronous inside its own worker, as documented for
[face detection](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/web_js)
and [segmentation](https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter/web_js).
Separate workers/GL contexts do not establish simultaneous execution on the same
GPU. A trace must distinguish submission, device work, readback, callback delivery
and main-thread resumption. Adding an async wrapper to composition cannot move
its loops off the main thread.

## Existing measurements and their limits

The strongest accepted G evidence remains the owner's qualitative smoothness
feedback, 56 matched generated/recorded image comparisons and clean promotion
checks (172 unit checks, 21 browser cases). The matched renderer harness reuses
frozen detections/masks; it does not run fresh face/hair inference or establish
tracking quality during motion.

A saved clean-promotion browser control contains useful G timing observations:

| Measure | G first | Test 2 second |
| --- | ---: | ---: |
| Completed frames / measured seconds | 587 / 30.041 | 469 / 30.033 |
| Completed AR updates/s | 19.507 | 15.583 |
| Capture-to-submission age p95 | 107.620 ms | 62.600 ms |
| Publication interval p95 | 82.945 ms | 76.285 ms |
| Reported video delivery/s | 23.43 | 29.17 |
| Tracking / requested mask coverage | 100% / 100% | 100% / 100% |
| Frames with visible hair edits | 0 | 0 |

This used a static 640×427 `face-a.jpg` synthetic camera, Intel Arc 140T/ANGLE
D3D11, GPU face/hair delegates, and a nonzero rear-temple branch on every measured
frame. G completed PBO retrieval on all 587 frames with no async fallback.
It validates benchmark transitions, coverage and exports; it is **not a fair
wearer performance conclusion**. Its source timer shares the application thread,
its images contain no hair edits, and it is one ordered control window per mode.
The numeric export has no build hash. Archived staged sources and the production
test log associate it with promotion; it is not a self-contained hashed speed
study. Higher observed throughput accompanied worse age and interval tails.
Independent recomputation from the raw rows reproduces both counts, durations,
update rates and p95 values exactly. Nine relevant archived runtime/test/config
Git blobs match this HEAD. The timing receipt SHA-256 is
`b7f5ed4622fcf8b5ec46ed83637d1a0f3a2e84b1409e847772c743cb8423a38e`.

The receipt is
`.recovery/g-combined-promotion-20260909-193002/staged/ar_v4/experiments/speed-lab/test-results/current-g/controls-reversed-combined-e4c03-aries-with-factual-coverage/reversed-benchmark-timings.json`.
Its sibling `reversed-benchmark-controls-receipt.json` explicitly states the
control-only scope. These existing files remain unchanged.

Selected G durations in that same control:

| Reported span | Median | p95 | Interpretation |
| --- | ---: | ---: | --- |
| Face inference call | 23.855 ms | 29.435 ms | Worker call, including implementation synchronization; not isolated GPU time. |
| Hair extraction | 19.560 ms | 24.450 ms | `getAsUint8Array()` plus callback-lifetime copy; transfer/client validation/hash are outside this metric. |
| PBO wait | 9.555 ms | 26.090 ms | Fence progress plus polling/event-loop delay. |
| PBO CPU extraction | 1.845 ms | 21.780 ms | Retrieval, allocation, row conversion and GL checks; long tail needs attribution. |
| Nonzero branch readback | 6.215 ms | 8.590 ms | Remaining synchronous full-image reader. |
| Renderer prepare / finish | 35.745 / 2.885 ms | 48.085 / 3.395 ms | Finish lacks visible hair-edit workload here. |
| Source read / source hash | 0.300 / 1.660 ms | 0.395 / 6.140 ms | Hash includes explicit input copy and completion scheduling. |

These nested/parallel durations must not be added. Hair admission wait was
approximately zero in this control; an admission bottleneck under other workloads
is a hypothesis. The source-read median is small here, so eliminating copies
cannot be advertised as recovering the full face/hair/render time.

Historical evidence remains informative but belongs to earlier pipelines:
the supplied Test 2 run measured 10.16 AR updates/s at approximately 29.6 video
frames/s, 69.19 ms median processing and 24.74 ms future-callback wait. G already
addresses that scheduling wait. Test 3 reduced explicit readback bytes by 87.5%
yet essentially tied cadence and worsened p95 age in every combination. This
supports investigating synchronization and critical paths before another broad
GPU compositor rewrite. See [current evidence record](REVIEWS.md).

The 56-case G study includes five software fence fallbacks around 500.3–510 ms
among 24 recorded software cases; all 32 hardware cases used PBO. These are
mechanism/correctness observations, not live fallback frequency. Preserve the
bounded same-pair fallback and its accounting. A fallback never counts as an
async speed gain. The historical 186-file manifest pins local CRLF bytes for six
text files; retain it unchanged and use a new Git-blob plus local-byte manifest
for any new study.

## Measurements needed before choosing a candidate

Camera settings, browser delivery, completed updates and frame freshness are
separate quantities:

- Track `frameRate` is a setting. Current video-delivery FPS uses matched
  `presentedFrames` endpoint deltas divided by capture-clock elapsed time; absent
  matched callback metadata means unavailable. It is not measured sensor FPS.
  [Video callback metadata](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)
  concerns video presentation; it does not certify the AR canvas reached a display.
- AR FPS is `(completed frames - 1) / publication elapsed time`. Capture age ends
  at canvas submission. Pending work is included; sensor buffering and physical
  display scanout are not. Full-pair publication must remain intact.
- G's `schedulerWaitMs` is capture-start to inference-start, including capture
  draw/read; Test 2's is finish-to-next-callback wait. Compare total age and
  publication intervals across modes, not these unlike stage medians.
- The current `stall-latency` UI is p95 frame age. Record publication gap p50/p95/
  p99/max and counts over fixed 100/200/500 ms thresholds, main-thread long tasks,
  input-event delay, failed/cancelled work and no-publication intervals separately.
  A completed-frame-only summary can miss a terminal stall with no later frame.
- Add session startup milestones: camera request/ready, renderer ready, each
  worker ready, first publication, first valid mask, prewarm, first actual nonzero
  branch. Warmed frame windows exclude startup. Prewarm currently forces a
  discarded synchronous read on the first tracked frame; it relocates first-use
  work and must be charged there.

For a separate profiling harness, record scalar phase timestamps using a shared
time basis (`performance.timeOrigin + performance.now()` across workers), request
and session IDs, worker send/receive/complete, mask extraction/validation/hash,
GPU submit/fence-poll/retrieval, CPU finish, post-publication UI work and pump drops.
Retain costs of unpublished/late masks and reasons for missing coverage. Use a
bounded event buffer or chunked local numeric receipts for long runs; the current
4096-row ring is insufficient as a complete sustained event history.

Measure native GPU durations only with supported, asynchronously retrieved
[disjoint timer queries](https://registry.khronos.org/webgl/extensions/EXT_disjoint_timer_query_webgl2/),
rejecting disjoint results; opaque MediaPipe calls still need worker/browser trace
attribution. CPU timers alone cannot isolate device execution. Profile
`getError`/state-query overhead without removing the existing error checks.
[WebGL guidance](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices)
explains that these calls and synchronous readbacks can introduce round trips,
and that PBO/fence retrieval still has a CPU stage. Trace overhead must be checked
against an otherwise identical run with detailed tracing off. Do not add extra
diagnostic pixel reads to speed measurements; earlier such reads masked a defect.

## Ranked experiments, each isolated from G

The ranking is provisional: code and the control above motivate it, but a G trace
with hair edits can change priorities. Each numbered experiment needs its own
G-versus-candidate result. Test substeps individually before combining them.

1. **Complete the asynchronous temple path.** Expose the validated baseline
   geometry after its native submission, submit the required nonzero branch
   before awaiting beauty retrieval, queue that branch's own PBO read, and join
   both exact outputs before unchanged composition/checks. The current code has
   an explicit serial barrier and a measured 6.215 ms branch read in the control.
   First test branch PBO retrieval, then earlier branch submission. Preserve
   native MSAA/color/context behavior, zero-drop skip, one pending rendered pair,
   abort ownership and same-pair fallbacks. Measure branch-frequency strata,
   CPU blocked time, both fences, bytes, age and gap tails. GPU contention can
   erase a gain; moving the barrier alone does not make the device faster.
   Capture each native framebuffer into its own PBO immediately after its render,
   before any yield or clear. Expose immutable same-pair geometry and join matching
   generations across the distinct contexts. Failed branch retrieval must rerender
   and synchronously read that same requested nonzero branch; dropping it is not
   equivalent fallback output.

2. **Reduce redundant copies under explicit leases.** Start with private PBO
   bottom-row scratch reuse; leave escaped top-down/held output independently
   owned. Resize/release scratch on viewport changes/disposal; never expose or
   transfer it to output storage. Keep GL error checks so failed retrieval cannot
   publish previous scratch bytes. At 1280×720 this avoids one 3,686,400-byte
   temporary allocation per beauty retrieval, a byte-count prediction rather
   than a speed result. Then
   test removal of the explicit source-hash copy and excess category-buffer
   copies while keeping identical hashes and all validation. WebCrypto already
   [snapshots digest input](https://www.w3.org/TR/webcrypto-2/#SubtleCrypto-method-digest).
   The hair path currently copies inside the MediaPipe callback, copies after
   client validation, copies for hashing and copies for renderer retention.
   Keep the required callback-lifetime copy; use verified transfer/retention
   ownership for any later reuse. Split the measured 19.560 ms extraction into
   conversion/readback versus copying before expecting this to solve it. If
   conversion dominates, simple host-copy removal cannot recover that duration.
   Measure allocation/GC, main-thread mask handling, worker turnaround and tails.
   Only if actual edited-hair profiles warrant it, consider exact scratch reuse
   in continuity; retain its arithmetic and every independent final check.

3. **Test later prefetch admission for freshness.** Compare unchanged G with
   starting N+1 preprocessing after N's beauty/PBO commands have been submitted.
   This targets the current synchronous prefetch prefix and possible GPU
   contention; it is a hypothesis, not a demonstrated defect. Keep two images,
   one request per inference worker, increasing face timestamps and complete
   image/detection/pose/mask publication. Do not add a queue, skip required hair
   work, predict a pose or attach results to newer video. Measure locked-slot
   drops, pending age, completed cadence, age/gap p95 and UI event delay. Require
   real inference on matched temporal sequences because changed admission can
   change a stateful face tracker's input sequence. A throughput gain with worse
   freshness or tracking is not an accepted responsiveness gain.

4. **Make optional comparison startup lazy.** Create the selected G renderer
   first and initialize Test 2 resources only when comparison needs them; evaluate
   safe overlap of independent asset/worker startup as a separate substep. There
   are two outer renderers and four native render contexts today, not eight live
   renderers. Both outer renderers repeat model/continuity preparation. Cache only
   immutable hash-verified CPU assets within clear lifetimes; keep GPU resources
   context-owned. Measure cold/warm startup, first masked publication, prewarm,
   first switch/Hold, memory, cancellation and restart. Deferred work can make
   switching slower, so include that cost. Steady FPS improvement is unproven.

Diagnostic overhead is a small control within profiling: G recomputes/sorts a
full recent summary each publication and repeats it for the 500 ms profile panel
(`live-main.ts:156,302`). Compare cached/throttled display summaries while retaining
every raw sample. This does not remove rendering or inference work. Source-opacity
caching, region/coordinate scratch reuse, zero-drop skipping and inactive
overlay/head-mask suppression are already implemented and are not new proposals.

## Fair comparison and acceptance

1. Pin unchanged G to this commit in a separate AR-only study. Save Git blob IDs,
   actual local source/compiled hashes, dependencies, browser/GPU/backend, camera
   dimensions/settings, model hashes, profile options and workload identity.
   Preserve historical receipts and unrelated work. Compare **G versus one new
   candidate**, not only Test 2 versus candidate; the current lab button uses
   Test 2. Use equal resident resources except when startup/memory is the variable.
2. Separate renderer equivalence from end-to-end performance. Replay the same
   frozen source/detection/pose/mask pairs through G and candidate at unchanged
   resolution, exact RGBA/geometry/alpha and independent final safeguards. Include
   Amber and Tom Ford × hair-only and multiclass × down/up/left yaw/right yaw,
   nose/front, zero/nonzero rear drop, visible hair edits and no-edit controls.
   Repeated warmed presentations/pose returns must supplement the original
   single-presentation matrix; retain the earlier warmed protected-pixel variance
   as unresolved unless directly explained.
3. For tracking/scheduling, feed identical ordered moving inputs and timestamps
   to real workers, in addition to frozen-output renderer tests. Separately run
   real-time paced sequences and disclose which input images each mode processes.
   Measure tracking loss/reacquisition, stationary jitter, pose changes and hair
   boundary/flicker; held equality alone cannot establish these properties.
4. Use fresh sessions for cold-start controls. For warmed tests, exercise both
   temple branches and masks first, then counterbalance G/candidate order (ABBA
   and BAAB across repeated sessions), with at least 30-second measurement windows
   and several repetitions per glasses/hair combination. Replay equal pose/branch
   distributions. Report per-run distributions and paired deltas, not a claim of
   independence for every autocorrelated video frame. Keep all failed/partial runs
   and all missing-mask frames in totals. A lower-quality workload is not a win.
5. Confirm on a physical desktop camera and target phones with actual long-hair
   motion and owner review. Use proposed 5–10 minute sustained runs after the
   short comparisons, equal power/browser/display settings and starting thermal
   conditions, tracking time-window degradation and memory. Report physical
   motion-to-photon only if measured with an appropriate external setup. Desktop
   synthetic speed is not mobile or thermal evidence. Existing short-haired
   wearer recordings and generated poses leave long-hair motion/true profile
   coverage incomplete; identify those gaps explicitly.
6. Require exact preservation checks, model/pose matrix coverage, cancellation,
   stop/restart, Hold/switch while pending, failed mask upgrades, context loss and
   async fallback accounting. Run `npm test` for a later behavior change at the
   unchanged deadlines. Predeclare the target metric and treat age/gap tails,
   tracking, mask coverage and UI response as co-requirements; accept a benefit
   only when it exceeds repeat-run variation without a reproducible regression.
   Promotion still needs the owner's visual acceptance. No promotion is proposed
   by this research.

The next concrete step is a separate scalar G profiling study using actual hair
edits and representative motion, followed by experiment 1 if its serial branch
cost remains material. Sustained physical-camera throughput, mobile smoothness,
thermal behavior, anatomical fit and large-yaw nose research remain unestablished.
No reconstruction or wearer-specific retuning is part of this plan.
