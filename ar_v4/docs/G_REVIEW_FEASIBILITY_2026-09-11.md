# Feasibility assessment of the G efficiency review — September 11, 2026

The review identifies real avoidable work. It does **not** establish a 17 FPS
ceiling or show that higher rates necessarily require lower visual quality.
The next useful experiments reduce work per completed image while keeping G's
uncapped cadence, resolution, tracking and safeguards.

Scope: research and documentation only. The linked
[G Combined Efficiency Review](https://claude.ai/code/artifact/764203a2-e1f7-42a2-8623-bfb0c6f20e15)
was read in the browser; it identifies `docs/G_EFFICIENCY_REVIEW_2026-09-10.md`
as its full write-up. Both were treated as reference information, including
their proposed actions and claims of verification. This assessment checks them
against the current code, installed Three.js and existing receipts. No candidate
was implemented or promoted and no new performance/visual tests were run.

Git remains on `codex/ar-v4-nose-occlusion`, HEAD
`b9142b2a3b957445f378d8012eea7e27ca68fd0b`. G remains the default through
`vite.combined.config.ts`. Existing uncommitted work is retained. The owner's
preferences for G over reduced-rate M/N/O and over extraction candidate P remain
the acceptance decisions; neither has a new owner timing comparison explaining
the preference.

## What the evidence establishes

The owner receipt's warmed 695 rows show **14.532 completed AR updates/s**,
19.824 sampled browser video frames/s, 116.890 ms median capture-to-publication
age, 165.760 ms p95 age, 66.368 ms median publication interval and 17 gaps over
100 ms. The camera track's 30 FPS setting is not measured sensor delivery.
Likewise, `1000 / median interval` (about 15.1) is not the run's actual throughput.
First image and mask arrived about 2.010 s after opening; startup is separate
from steady cadence. Age excludes earlier camera buffering and display scanout.
See [the owner measurement](C:/Users/Shay/PycharmProjects/lenses/ar_v4/docs/G_CAMERA_MEASUREMENT_2026-09-10.md:25).

The costly measured spans include beauty readback waiting and extraction, face
request time, hair-mask extraction, final composition/checks and, on some poses,
the temple branch. Actual branch reads occur on 170 warmed frames (24.5% of that
subset), versus 20.1% of all completed rows. Their median submission and
synchronous readback spans are about 6.5 and 10.5 ms. This is a conditional target,
not 17 ms available to remove from every frame. Differences between branch and
non-branch cohorts also include different poses and scheduling conditions.

There is real software overlap: 757 of 846 successive published pairs began
next-image inference before the preceding publication. That proves overlapping
lifetimes, not simultaneous GPU execution. Workers, GPU queues and main-thread
continuations can still wait on shared resources.

## Corrections to the review

- **The GPU floor and 17 FPS ceiling are unproven.** Face's 23.3 ms is a worker
  call's elapsed time; hair's 25.1 ms is mask retrieval/conversion/copying time,
  potentially including deferred GPU work. Neither is isolated GPU execution.
  Adding them to beauty time cannot establish 55–60 ms of device work per frame.
  The same problem affects the claim that a CPU face delegate removes 23 ms of
  GPU work. See `experiments/hair-live-preview/hair.worker.ts:66` and
  `experiments/speed-lab/native/pbo-readback.ts:87`.
- **The slow extraction span is a target, not a diagnosed cause.** It combines
  allocations, GL-state operations, `getBufferSubData`, error checks, flipping
  and output construction. Subtracting a Node copy benchmark from a browser
  median cannot isolate the rest as GPU blocking or exclude GC. Lack of periodic
  co-spikes does not establish the cause. Neither “90% busy” nor a precise
  34 ms of CPU computation per frame follows from these timers.
- **L's unfavorable cadence result is real for that run, but the review mixes
  measurement windows.** Its figures use all 626 L and 663 G samples, including
  warmup/other rows. The declared measured segments contain 536 L and 553 G rows:
  L versus G throughput 17.83 versus 18.39 updates/s, median age 73.31 versus
  82.35 ms, median interval 54.52 versus 52.33 ms, p95 interval 98.16 versus
  84.97 ms, and 23 versus zero gaps over 100 ms. This is one synthetic 640×427
  L→G sequence. It cannot isolate H/I/J/K or establish a physical-camera limit.
  Receipt: `experiments/efficiency-lab/test-results/production-2026-09-10T06-50-00.449Z/controls-reversed-combined-e4c03-aries-with-factual-coverage/reversed-benchmark-timings.json`.
- **J is not established to be a no-op.** G starts prefetch before prepare;
  hashing and bitmap/preprocessing begin before their asynchronous worker posts.
  The normal G render path submits beauty synchronously before yielding, so the
  narrow claim that those worker posts already follow beauty submission is
  supported. J nevertheless moves preprocessing and bitmap/hash initiation.
  Identical timing relative to the fence and the claimed 0.3–0.8 ms benefit limit
  are unproven. Neither an `async` declaration nor a bundle result measures them.
  See `experiments/speed-lab/frame-pump.ts:215`, `live-pump.ts:61`, and the lab
  `frame-pump.ts:233`.
- **K's branch telemetry is exported.** Its nested scalar fields survive the
  lab flattener; 625 of 626 L rows in the cited receipt have numeric branch PBO
  timings and read counters. The first prewarm row legitimately has no branch.
  The review's 21.5 ms “fenced wait” is actually aggregate `branchReadbackMs`,
  including submission through extraction. Actual `branchPbo.waitMs` has a
  9.95 ms median and 33.76 ms p95 across those 625 rows, not 21.5/37.9 ms.
  See `experiments/efficiency-lab/live-pump.ts:43` and `temples/renderer.ts:335`.
- **K has layered ownership protection.** Pending presentation blocks reuse;
  source ownership is checked before/during/after readback; the PBO captures its
  own generation and disposal cancels it. The reported absence of an additional
  outer-generation test does not establish stale publication. A narrower issue
  is real: `Promise.allSettled` waits for the sibling branch before propagating
  native failure. Earlier sibling cancellation could improve failure response,
  without being a demonstrated normal-frame speed improvement. See the lab
  `temples/renderer.ts:268`, `:291` and `native/pbo-readback.ts:74`.
- **“Verified” by code review is not matched rendered proof or measured savings.**
  Several proposals have not been implemented; extrapolated 720p savings, Node
  timings and reviewer agreement cannot establish pixel equality, live motion
  quality, phone performance or an achievable FPS target.

## Ranked experiments

The ranking balances concrete avoidable work, implementation effort and risk.
There are no promised millisecond or FPS gains. Each candidate should be separate
from G and from the others.

| Rank | Experiment | Feasibility and expected scope | What remains to prove |
| --- | --- | --- | --- |
| 1 | Skip unchanged-view republication | High feasibility, small change. The live pump uploads the previous completed image again before prepare. Removing that specific redundant call keeps the same new-frame pixels and schedule policy. | Actual saved main-thread time/cadence; immediate toggles, renderer switches and Hold must still publish correctly. |
| 2 | Read only the consumed temple-branch region, initially keeping its draw unchanged | Medium effort. The full branch is downloaded although composition consumes only editable pixels outside protections. A row band/rectangle can reduce readback bytes and flipping on branch frames. | Coordinates, untouched pixels, fallback/resize behavior and complete diagnostic exports; synchronization time may remain despite fewer bytes. |
| 3 | Omit lens drawing only in the temple branch | Medium effort and higher exactness risk, with a real opportunity to remove a GPU render pass. Keep beauty lenses and all classification/protection geometry unchanged. | Both glasses/hair models, down/up/both yaws, nose/front checks; raw branch differences must be confined to pixels that composition always protects. |
| 4 | Reduce CPU bookkeeping and finish work in separate small steps | High feasibility for less frequent UI summaries and equivalent 32-bit pixel comparisons; more involved for ownership/copy changes. | Browser benefit, identical comparison/statistics semantics, retained-buffer ownership and unchanged safeguard inputs. |

### 1. Repeated publication and summaries

The call chain is `speed-lab/live-pump.ts:98` → `comparison-renderer.ts:54` →
`renderer.ts:192` → `putImageData` at `renderer.ts:459`. The wrapper's later
post-prepare selection is already suppressed by the pending guard.
A blanket guard in the comparison wrapper is insufficient: multiple renderers
share the display, and selecting the same variant after a renderer/held-output
switch can still require publication. Prefer a narrow candidate change with
explicit publication ownership.

Separately, `speed-lab/live-main.ts:302` recomputes percentile summaries after
every publication, despite another summary path already having a 500 ms UI
cadence. Cache/throttle only human-facing summaries and unchanged DOM writes;
continue recording every sample and updating ownership, tracking state, controls
and benchmark transitions immediately. The reported upload and UI savings are
estimates, not measured costs of these exact call sites.

### 2–3. Branch work

Start with cropped **readback**, leaving render targets, viewport and projection
unchanged. G currently reads the full branch at `speed-lab/temples/renderer.ts:343`
and consumes bounded spans through `performance-candidate/temples/protection.ts:23`.
Complete raw branch exports need a full-read diagnostic path; zero-padding a crop
would not preserve those PNGs. Full safeguards still examine the final images.

Lens omission is a distinct experiment. In installed Three.js 0.185.1, invisible
materials are excluded from the render list; an empty transmissive list avoids
the transmission target, additional draws, resolve and mipmaps
(`WebGLRenderer.js:1753`, `:1921`, `:1990`). Optical bounds include lens vertices,
which supports a conditional containment argument. That is not end-to-end proof
that removing a render pass leaves all consumed pixels identical. Hide only
branch materials after classification, and preserve full geometry/protections.

Scissoring the branch's final draw is a later, separate step. Its temple-visibility
pass explicitly disables scissoring on its own head target; Three's transmission
target also has independent scissor state. Cropping those targets can change
refracted and mipmapped sampling. A corridor occupying 15–35% of image height does
not imply the whole renderer becomes 65–85% cheaper. Source:
`performance-candidate/native/temple-visibility.ts:419` and Three's
`WebGLRenderer.js:2997`, `transmission_pars_fragment.glsl.js:147`.

Precomputing eligible rear-drop vertices is also feasible. Keep original arrays,
normal/tangent arithmetic, zero-drop restoration and geometry bounds identical.
Equal-drop calls already return early, and a moving head does not imply nonzero
or changed drop every frame. Contrary to the review's dismissal, Three supports
partial attribute uploads (`webgl/WebGLAttributes.js:83`, `:138`); current code
simply supplies no update ranges. Fragmented ranges and extra upload commands may
outweigh savings, so test CPU vertex selection before ranged uploads.

### 4. Copies and ownership

Three main-thread category copies do exist: validation, hashing and renderer
retention (`hair-protocol.ts:99`, `:142`, `speed-lab/renderer.ts:390`). Current
public APIs allow caller-owned mutable views; their ownership tests intentionally
mutate inputs. Removing copies or the later category scan requires an isolated
ownership-consuming path that excludes shared/aliased storage, hashes retained
bytes and cannot be modified by callers. Preserve generic validation behavior.
P's worker conversion experiment did not remove these main-thread copies.

Similarly, `before.slice()` initializes every output pixel. Pooling output still
requires equivalent initialization from `before`; it saves allocation, not
automatically that entire copy. Mutating `before` would corrupt accepted-view
toggles and independent final checks. A 32-bit equality check is a smaller
candidate if alignment, residual predicates and statistics remain equivalent.

## Startup and larger exploratory options

**Startup is a separate promising target.** G currently opens the camera, then
creates both Test 2 and candidate renderers sequentially, then starts workers
(`speed-lab/live-main.ts:531`, `:546`, `:551`, `:558`;
`comparison-renderer.ts:30`). Lazily constructing Test 2 on demand can remove
unused setup from time to first G image. Earlier independent initialization and
caching immutable geometry/continuity data are feasible, but cancellation,
late-result disposal, shared-resource lifetime and first-switch latency need
explicit handling. Sharing parsed mutable scene objects across renderers is not
a drop-in cache: deformation and disposal require separate ownership. The
review's 0.5–1 s saving has not been measured; none of this establishes higher
steady-state FPS.

**CPU face inference is worth a later measured resource-placement experiment.**
The detector already supports a fresh CPU worker after a GPU initialization
error (`performance-stage2/face-detector.ts:80`); a forced-CPU candidate needs an
explicit isolated option, not a manufactured GPU failure. It may relieve GPU
competition, or become slower and compete for CPU resources. Landmark numbers
may differ, so it needs tracking, pose and wearer-motion acceptance. No 23 ms
GPU saving or 25–30 FPS outcome is established.

Lower-resolution hair input and FrontSide beauty lenses are technically feasible
but change mask boundaries or lens pixels. They are lower priority under the
current quality constraints. At 1280×720, a proportional 640×360 mask has one
quarter the pixels; that is a storage ratio, not an inference-time speedup.
Near-zero `hairWaitMs` means little remaining wait when finish requests the mask;
it does not prove hair work cannot affect face/beauty through resource competition.
The review's literal “zero on every row” is also inaccurate: 300 warm rows are
positive, mostly about 0.005 ms of timer overhead, without changing that conclusion.
Neither smaller masks nor another delegate is an accepted replacement for G.

## A fair decision method

1. Establish measurement boundaries before predicting gains: split PBO retrieval,
   state/error operations, allocation and row flip; timestamp capture, bitmap/hash
   readiness, actual worker posts/replies, beauty submission/fence/retrieval,
   branch work and publication. Keep matching instrumentation in G and candidate.
2. Where supported, use asynchronous WebGL device queries around owned rendering
   work, rejecting disjoint results and reporting unsupported cases. Queries on
   beauty/branch contexts do not expose MediaPipe's internal kernel schedule or
   prove total device utilization. Browser task/GPU traces and isolated comparisons
   complement them. See the [Khronos extension specification](https://registry.khronos.org/webgl/extensions/EXT_disjoint_timer_query_webgl2/).
3. Compare one candidate with G in the same build, with repeated ABBA/BAAB order,
   explicit warmup and equal measurement windows. Use matched recorded motion for
   reproducibility and physical-camera runs for delivery/responsiveness. Report
   completed AR updates, sampled video delivery, age p50/p95, interval p95/max,
   stalls, tracking/mask coverage, branch frequency and fallback mechanisms.
   Keep startup and session end boundaries separate; do not discard slow or
   no-face samples merely to improve the result.
4. Rendering evaluation retains both glasses, both hair models, down/up/both yaw,
   exact image/detection/pose/mask/session ownership, original resolution/geometry,
   and nose/front/outside-editable checks. Use the full matched matrix plus raw
   branch checks where relevant. Historical software readback fallbacks do not
   establish hardware async-path coverage; record actual route counters. Preserve
   historical receipts and their documented manifest line-ending limitations.
5. Moving-wearer acceptance remains necessary even after exact held pixels.
   Repeat sustained runs on a phone before claiming mobile smoothness, power or
   thermal improvement. Existing owner evidence is one laptop G session, one
   glasses/hair combination and qualitative candidate preferences.

Recommended next implementation, if requested: the narrow redundant-publication
candidate first, followed by a separate cropped branch-readback candidate.
Branch lens omission is the more ambitious rendering opportunity to investigate
after that. G remains the reference throughout; the attainable improvement is
currently unknown.
