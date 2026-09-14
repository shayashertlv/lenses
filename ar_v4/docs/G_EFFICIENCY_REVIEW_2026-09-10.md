# G Combined efficiency review — September 10, 2026

Question asked: can G run at a higher frame rate without changing what the user
sees. "Without changing" is taken in the project's own sense: byte-identical
published pixels for the same source, detection and mask, with every safeguard,
pairing and ownership rule intact. Resolution, MSAA, Three's transmission pass,
the 15 mm fade, models and tracking parameters are treated as fixed quality
settings.

Method: a read-only code review of the G hot path (speed-lab pump, scheduler,
renderer, native GL, temples branch, PBO readback, hair and face workers, finish
stage, startup), nine independent finder passes, deduplication to 30 candidates,
three adversarial verifiers per candidate (impact, exactness, project rules), and
recomputation of every headline number from the raw receipts. No runtime file was
changed. The concurrently edited `experiments/efficiency-lab/` was read only.

## Verdict

1. **On the owner's laptop with a real 1280x720 camera, G runs at 15 fps and the
   published interval is the sum of two things: waiting for the beauty render to
   complete on a GPU that is also running the face and hair inference kernels
   (about 41 ms), plus a serial main-thread tail after that fence (about 24 ms).**
   Source: the owner's `ar-efficiency-comparison-2026-09-10T07-35-19.836Z.json`
   (847 G rows, Amber, hair-only, Intel Arc 140T, ANGLE D3D11), warm subset of 695
   rows: interval median 66.4 ms, p95 95.8 ms, 17 gaps over 100 ms.
2. **Byte-exact changes can shorten the serial tail; they cannot lower the GPU
   floor.** Face (23.3 ms) and hair (25.1 ms) worker time, most of it GPU work on
   the same device, plus the beauty render itself already account for roughly
   55-60 ms of GPU-process work per frame. The efficiency-lab's own measurement of
   its L bundle (H+I+J+K) against G at 640x427 shows what that means: frame age
   fell 7.5 ms, published interval rose 2.2 ms, p95 rose 15 ms, 31 gaps over 100 ms
   appeared. Realistic byte-exact headroom on this machine is 15 to about 17 fps.
3. **The only levers that reach 25-30 fps change pixels and need the owner's
   visual acceptance:** a 640-max-edge hair input (hair 25 to about 20 ms and
   3.4x smaller mask handling), the face landmarker on the CPU delegate (removes
   about 23 ms of GPU work per frame; worker time unmeasured), FrontSide lenses.
4. **Correction of an interim statement:** the main thread is not "90% busy". At
   720p it computes about 34 ms per frame; the rest of the interval it is blocked
   in synchronous GL calls or waiting for the GPU and the face reply.

## Where a 720p frame goes (owner receipt, warm rows, medians)

| Stage | All rows | Branch rows (24.5%) | No-branch rows |
| --- | ---: | ---: | ---: |
| Published interval | 66.4 ms | 80.2 ms | 62.8 ms |
| Capture-to-publish age | 116.9 | 148.9 | 111.6 |
| prepareMs | 48.9 | 66.0 | 46.8 |
| beauty submit | 3.0 | | |
| PBO fence wait | 20.0 | | |
| PBO extract (getBufferSubData + copy + flip) | 21.4 | | |
| branch submit + synchronous read | 0 | 6.5 + 10.4 | 0 |
| finishMs (compose 5.5, checks 2.7, continuity 0.8, publish 0.8) | 10.4 | | |
| camera drawImage into the capture canvas | 7.4 | | |
| face request wall (inference 23.3, queued reply 14.5) | 39.1 | 55.7 | 37.2 |
| hair extraction (worker, GPU readback + conversion) | 25.1 | | |

Do not add overlapping rows. A branch frame lengthens the interval by 17.5 ms,
which is its full serial main-thread cost (6.5 + 10.4), not only its GPU share.
That is why main-thread savings still convert to interval at 720p, down to the
GPU floor. Of the 21.4 ms "extract", about 4.4 ms is the copy and row flip
(measured in Node at 3.7 MB); the rest is getBufferSubData blocking behind
inference kernels queued after the fence. 520 of 695 rows have extract over 10 ms.
The same tail exists in the 640x427 control (171 of 826 rows) and is explained by
where the next image's face and hair requests land relative to the fence, not by
allocation or GC (no co-spikes in CPU-only stages; not periodic).

Comparison point, 640x427 synthetic control (promotion receipt, 826 G rows):
19.8 fps, interval 50.4 ms, prepare 37.9, PBO wait 10.3 (bimodal 8.7 / 22),
extract 2.0 with 21% of rows over 10 ms, branch 4.6 + 6.5 on 100% of frames, finish
2.9. The synthetic camera shares the main thread and delivered only 22 frames/s,
so its fps figures are not comparable with a hardware camera.

## A. Byte-exact changes, ranked by serial-tail time at 720p

All figures are per frame on the owner's machine unless marked. "Verified" means
three refuters could not break exactness and the size was recomputed from
receipts or a Node microbenchmark of the actual module.

1. **Finish stage (about 3 ms of 10.4).** Write the compose output into a
   renderer-owned buffer instead of `before.slice()` (verified 1.0 ms), drop the
   three redundant main-thread copies of the hair category mask and the second
   validity scan (verified 0.9-1.5 ms), compare 32-bit words in the
   composeHairArmsFast full-frame loop as checkHairProtection already does
   (verified 0.8 ms; the residual boolean is identical, statistics unchanged). Conditions: keep every safeguard scan on the
   same bytes, opt-in options so Test 2 and the frozen tests keep the copying
   semantics, categorySHA256 digested over the retained view, an ownership test
   that a held or exported image cannot change after buffer reuse. Files:
   `experiments/performance-candidate/fast-compose.ts`, `hair-protocol.ts`,
   `hair-client.ts`, `ownership.ts`, `speed-lab/renderer.ts`.
2. **Rear-drop branch (17 ms serial on 24.5% of frames, 4.3 ms average).**
   - Hide the transmission lens in the branch's own scene (`material.visible =
     false` after createTempleClip, createRearDrop and createTempleVisibility
     have classified it). This removes Three's whole transmission pass from the
     second context. Published pixels stay byte-identical because every lens
     vertex is inside the projected optical protected rect (+4 px) and the
     branch is consumed only in editable-minus-protected spans; only the
     diagnostic branch PNG changes. Verified by three refuters; must still be
     proven on the 56-pair matched harness with a raw-branch diff. About 2 ms
     at 640, about 4 ms at 720p on branch frames.
   - Scissor the branch draw to the corridor bounding box and read back only
     those rows (corridor rows are 15-35% of height on all 42 archived poses).
     Verified byte-exact; about 1 ms at 640, about 3.4 ms at 720p on branch
     frames. Requires protection computed before the draw (setDrop first).
   - Replace the frozen rear-drop rewrite with an eligible-vertex update in a
     sibling module (the frozen file stays byte-pinned); CPU only, verified
     1.5-2.5 ms on frames where dropM changes (every frame on a moving head).
     Three still re-uploads the whole attribute, so the partial-upload part of
     the idea does not hold.
   - Do NOT count on "skip the branch when the editable corridor is fully
     protected": it never fires on real poses. Editable-minus-protected area was
     0.06% to 6.1% of the frame on all 24 recorded wearer cases and all 18
     generated 1280x853 poses; it is empty only on the synthetic frontal fixture.
   - Efficiency-lab K (async branch PBO): exactness proven (56/56 matched), but
     the L run shows the fenced branch wait growing to 21.5 ms under contention
     and no interval gain at 640. Re-measure K alone on the real camera; expect
     an age gain, not an fps gain, until GPU load drops.
3. **PBO extract copy (about 1 ms):** reuse the bottom-up scratch (efficiency-lab
   H, verified). The remaining 17 ms of "extract" is GPU blocking, not copy.
4. **Capture (about 1 ms):** pooled capture and face canvases and no explicit
   hash-input copy (efficiency-lab I, verified). The 7.4 ms drawImage is the
   video-to-CPU conversion; replacing it with a GPU-side texture path is not
   provably byte-exact (YUV conversion rounding) and was not proposed.
5. **Redundant putImageData every frame (about 1 ms plus a 3.7 MB compositor
   upload):** `live-pump.ts:98` calls `selectVariant` before every prepare, which
   re-publishes the previous frame (`speed-lab/renderer.ts:192-195, 455-461`).
   Guard on variant change. Present in G and in efficiency-lab.
6. **Small items (about 1-1.5 ms combined, resolution independent):** skip the
   second detection clone on the live path, memoize the stats clone, build the
   temples diagnostics lazily, throttle the per-publish summarize (28 sorts) and
   unchanged DOM writes to the 500 ms cadence, one getError after the retrieval
   batch instead of per poll (profile first, as the research doc requires).
7. **Startup (2.0 s to first mask on the owner's run):** create the Test 2
   renderer lazily on first switch or Hold (2 contexts, 2 GLTF parses, 2 PMREMs,
   one continuity build), start renderer and worker initialisation while
   getUserMedia is pending, fetch and parse the GLB once and share the continuity
   model. Exact; 0.5-1 s on desktop, more on phones; no steady-state fps effect.

Sum of items 1-6 is roughly 10-13 ms of the 24 ms serial tail, so the interval
could move from about 66 toward about 55-58 ms only if the GPU floor allows it;
the L measurement says the floor is close. Expect about 17 fps, not more.

Refuted or downgraded and not worth pursuing: MessageChannel fence yields (no
timer clamp is visible in the poll data; mean gain 0.07 ms), merging beauty and
branch into one WebGL context (about 0.5-2 ms incremental over K, unknown
exactness, large rebuild), CPU hair delegate (measured +110 ms, 0 of 20 masks on
hardware), earlier branch submission before the J signal (net zero: the GPU is
busy either way), moving the finish passes into a worker (1-5 ms, but needs a
second rendered pair in flight and puts the final checks off the publishing
thread), arm-only overlay index buffers (0.2-0.8 ms, turned-head frames only).

Verification coverage: 30 candidates, three independent refuters each; 84 of 90
verdicts completed. The startup precompile item (M28) received no verdict and
the shared-GLB (M27) and L-bundle (M30) items each lost one vote to a session
limit; their figures above are the unverified finder estimates.

## B. Scheduling changes (exact pixels, different frames)

These keep every pixel for a given input but change which images are processed
and their age, so they need matched real-worker motion sequences, not held
frames. Efficiency-lab J (admit N+1 inference after native submission) measured
neutral to negative inside L; the verifiers found J cannot actually move the
worker requests relative to the fence (G already posts them after the beauty
submission), so its residual is 0.3-0.8 ms. A later admission, after beauty
retrieval, would let the beauty complete uncontended (about 12-15 ms instead of
41) but starts inference later; predicted interval about 62 ms at 720p, worse
age. Pulling the newest video frame at a fixed phase so N+1 inference always
overlaps N's tail (37% of control pairs did not overlap) survived verification
only at 1-3 ms, not the 6-7 ms first claimed, and costs age. Phase-shifting the
hair request out of the beauty window was refuted (the moved work lands on a
synchronous wait). All of these are bounded by the same GPU floor.

## C. Changes that alter pixels (owner acceptance required)

1. **Hair worker input at 640 max edge instead of the full 1280x720 capture.**
   MediaPipe upsamples the category mask to the input size on the GPU and reads
   back a full-size float mask; the resolution-dependent part is about 5 ms of
   the 25 ms hair worker time at 720p, plus 3.4x smaller mask copies, scans and
   hashes on the main thread (about 2 ms). The hair worker is not on the
   critical path even at 720p (hairWaitMs is 0 on every owner row), so the value
   is GPU contention relief, which no receipt quantifies. Mask boundaries
   quantise to about 2 render pixels and the feather test changes; the held path
   must use the same downscale. Replacing MediaPipe's getAsUint8Array with an
   own readPixels into reused scratch is byte-exact but worker-side only
   (0.4-1.7 ms), so it does not move the interval.
2. **Face landmarker on the CPU (XNNPACK) delegate.** Removes about 23 ms of GPU
   work per frame from the shared device; the worker already supports the CPU
   delegate as a fallback, so timing it is a small experiment. Landmark floats
   differ between delegates, so pose and pixels differ. Phones may be slower on
   CPU; desktop is unmeasured.
3. **FrontSide transmission lenses** skip Three's back-face transmission redraw
   on both contexts; lens pixels change.

Everything else that is large is a fixed quality setting: 1280 capture, MSAA,
transmission resolution, the beauty render, the models.

## D. Facts the tree should carry forward

- Efficiency-lab L vs G (`experiments/efficiency-lab/test-results/production-2026-09-10T06-50-00.449Z`,
  640x427, same rig): prepare 40.3 to 32.8 ms, age 82.2 to 74.5, face reply
  queueing 15.9 to 4.0, interval 52.3 to 54.5, p95 85 to 100, max 99 to 584 ms,
  gaps over 100 ms 0 to 31. K's fenced branch wait 21.5 ms median, 37.9 p95.
- K gaps found in code: the branch PBO isCurrent has no pair-generation check;
  a native rejection does not cancel the branch poll for up to 500 ms; two poll
  loops each pay getError per poll; branch PBO metrics are flattened to
  undefined in the timing export.
- The rear-drop branch fires on 100% of synthetic frames but 24.5% of the
  owner's real frames and 8 of 24 recorded wearer cases.
- The PBO extract tail is GPU-process contention, not GC; no setTimeout nesting
  clamp is observable; the synthetic benchmark camera is throttled by the page's
  own main thread (22 presented frames/s under G).
- Node microbenchmarks of the real modules at 1280x720: composeHairArmsFast
  9.8 ms with edits, checkHairProtection 6.8 ms, before.slice 1.1 ms, PBO
  copy+flip 2.0 ms, composeProtectedPixels 2.1 ms, hash input copy 1.2 ms.

## E. How to prove any of it

1. Compare G against exactly one candidate on the real camera, ABBA and BAAB,
   30 s windows after warmup, same movements, both frames and both hair models,
   using the existing Measure control; export the timing JSON and judge
   published interval, age p95, gaps over 100 ms and mask coverage together.
2. Prove exactness on `experiments/speed-lab/qa/matched.mjs` (or the
   efficiency-lab copy) over the 56-pair matrix; for the branch-lens change add
   a raw-branch diff showing every changed branch pixel inside protectedRects.
3. Add EXT_disjoint_timer_query attribution for beauty and branch device time
   and split the PBO extract into retrieval, copy and flip before crediting any
   GPU-side saving; CPU timers cannot isolate device time.
4. Repeat on a phone before claiming mobile smoothness; nothing here measures
   thermal behaviour.

Evidence paths: owner receipt in Downloads (SHA-256 33e4444e…), promotion
control `.recovery/g-combined-promotion-20260909-193002/…/reversed-benchmark-timings.json`,
L run above, matched studies `experiments/efficiency-lab/qa/output/matched-2026-09-10T06-*`,
archived protections `experiments/performance-candidate/qa/output/matched-2026-09-09T10-22-02.200Z/report.json`
and `.recovery/hair-live-performance-2026-09-08/recorded-runs/2026-09-08T19-30-46.916Z/report.json`.
