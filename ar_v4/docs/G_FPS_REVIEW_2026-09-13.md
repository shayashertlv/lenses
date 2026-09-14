# G Combined frame-rate review — September 13, 2026

Question asked: review ar_v4 and find ways to raise the delivered frame rate of
G Combined (the default page, `experiments/speed-lab/live.html`). Scope: the
owner's Intel Arc 140T laptop first, the iPhone second. Everything below is
either read from the project's own receipts, measured today on this laptop, or
marked as an estimate.

## Verdict

1. **G's frame interval on the laptop equals its main-thread occupancy, and the
   main thread is mostly waiting on the GPU process.** In the owner's Sep 11
   real-camera run (1280x720, 899 warm rows) the median interval is 76.8 ms;
   the main thread spends 57.3 ms in `prepare` of which only 3.4 ms is Three's
   submit, 22.9 ms is the yielded fence wait and 23.6 ms is *blocked* inside
   `getBufferSubData`; then 11.8 ms in `finish` (CPU compositing and checks) and
   about 13 ms capturing and hashing the next frame. The face reply queues
   19 ms behind that blocked main thread. The GPU process is shared by the face
   delegate, the hair delegate, the beauty render, the temple branch and the
   readbacks, and Chrome executes all of their commands on one thread.
2. **Byte-exact optimisation is exhausted.** Q/R/S/T were flat on the phone
   (13.87 vs 13.17–13.88 updates/s, Sep 12), L was flat on the laptop (Sep 10),
   and three more pixel-preserving-or-nearly candidates were refuted today with
   the real GPU: a smaller hair input, the multiclass hair model, and a
   half-resolution transmission target. None moved fps by more than run-to-run
   noise (about 5%).
3. **The cheapest real lever is the face landmarker on the CPU delegate.**
   Measured today, same fixture, same GPU, ABBA-style with a repeated baseline:
   +9% to +13% fps at both 640x427 and 1280x854, with 100% tracking and masks.
   It removes the face delegate's GPU work from the shared GPU thread; the
   single-threaded WASM SIMD inference (37 ms on battery) runs in its own worker
   and overlaps the frame. It changes landmark values slightly, so it needs the
   owner's eyes. Opt-in: append `?face=cpu` to the G page.
4. **Power state is the largest swing in delivered fps and it is not in the
   code.** This laptop was on battery (Balanced plan, 94%) during every run
   today. Plain G at the promotion fixture (640x427) ran at 9.9 fps; the Sep 9
   promotion receipt shows 19.8 fps on the same fixture and GPU. The owner's
   own Sep 11 run (9.3 fps) is 1.6x slower than the Sep 10 run (14.5 fps) with
   identical settings. Measure on mains power before believing any fps number,
   and consider surfacing a "plugged in?" hint in the page.
5. **The remaining large levers change architecture or policy, not bytes:**
   a render worker that takes the Three.js render, the PBO readback and the
   CPU compositor off the main thread (predicted: interval falls to the GPU
   floor, roughly 50–60 ms on mains, about 17–20 fps, more with item 3), and
   a hair cadence policy that runs segmentation on alternate frames (halves
   the largest GPU consumer but breaks the exact image/mask pairing rule the
   project has kept since the long-hair checkpoint; owner decision only).

## Evidence

### Owner receipts, real camera, 1280x720, G, warm rows, medians

| | Sep 10 (695 rows) | Sep 11 (899 rows) |
| --- | ---: | ---: |
| Completed updates/s | 14.53 | 9.26 |
| Publication interval med / p95 | 66.4 / 95.8 ms | 76.8 / 100.7 ms |
| Gaps over 100 ms | 17 | 51 |
| Capture-to-publish age | 116.9 ms | 140.8 ms |
| Face inference (worker) / request wall / reply queueing | 23.3 / 39.1 / 14.5 | 28.3 / 50.0 / 19.0 |
| Hair extraction (SDK mask readback, worker) | 25.1 | 30.2 |
| prepareMs | 48.9 | 57.3 |
| … beauty submit / PBO fence wait / PBO extract | 3.0 / 20.0 / 21.4 | 3.4 / 22.9 / 23.6 |
| Temple branch frames (median submit + sync read on those frames) | 24.5% (6.5 + 10.4) | 20.5% (6.8 + 8.8) |
| finishMs (compose / checks / continuity / publish) | 10.4 (5.5 / 2.7 / 0.8 / 0.8) | 11.8 (6.3 / 3.1 / 0.8 / 0.9) |
| Camera drawImage / source hash / capture-to-inference wait | 7.4 / 5.0 / 8.6 | 7.6 / 6.2 / 9.1 |
| Frames with visible hair edits | 51% | 74% |
| hairWaitMs at finish | 0 | 0 |

Files: `~/Downloads/ar-efficiency-comparison-2026-09-10T07-35-19.836Z.json` and
`…2026-09-11T12-31-09.095Z.json` (both G; the Sep 11 file also holds a Q segment
at 8.6 fps, order effect). Recompute with
`node experiments/speed-lab/qa/receipt-stats.mjs <file>`.

Interpretation of the Sep 11 row: 3.4 + 23.6 + 11.8 + 13 + branch share ≈ 55 ms
of main-thread compute or blocking per frame, plus 22.9 ms yielded during the
fence wait in which the next capture, hair validation and UI work run. That sum
is the 77 ms interval. Reducing GPU work shortens the blocked part; taking the
render off the main thread removes the serialization.

### Today's measurements, this laptop, on battery

Synthetic camera (`tests/fixtures/face-a.jpg` on a 30 fps captureStream, the
promotion receipt's method), Playwright Chromium with `--enable-gpu
--use-angle=d3d11`, ANGLE D3D11 on the Arc 140T, 8 s warmup, 25 s window,
Amber Horizon, hair on, GPU delegates unless stated. The rear-temple branch
fires on 100% of these frontal frames, so absolute numbers sit below the
real-camera runs; compare rows within a table.

640x427 (the promotion fixture size):

| Configuration | fps | interval med / p95 | face inf | hair ext | PBO wait / extract | finish |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| G | 9.85 | 96.4 / 160.1 | 42.0 | 35.9 | 19.1 / 13.9 | 8.8 |
| `?face=cpu` | **10.73** | 88.2 / 144.6 | 37.0 (CPU) | 28.0 | 19.8 / 12.8 | 8.9 |
| `?hair=320` (mask 320x214) | 9.93 | 96.4 / 158.5 | 41.9 | 29.3 | 25.9 / 7.5 | 8.9 |
| `?tx=0.5` | 9.34 | 102.7 / 164.3 | 45.3 | 39.1 | 20.6 / 14.3 | 9.2 |
| G again | 9.49 | 98.3 / 163.0 | 44.6 | 37.8 | 20.3 / 8.2 | 9.0 |

1280x854 (720p-class pixel count):

| Configuration | fps | interval med / p95 | face inf | hair ext | PBO wait / extract | finish |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| G | 5.42 | 182.2 / 252.7 | 43.4 | 56.0 | 33.5 / 30.5 | 34.4 |
| `?face=cpu` | **5.92** | 166.9 / 228.4 | 37.4 (CPU) | 47.3 | 27.7 / 25.0 | 35.7 |
| multiclass hair model | 5.36 | 184.4 / 259.8 | 44.3 | 57.1 | 34.1 / 35.2 | 35.3 |
| `?face=cpu` + multiclass | 5.84 | 171.8 / 232.0 | 36.0 (CPU) | 46.5 | 31.5 / 25.5 | 34.9 |
| `?hair=640` (mask 640x427) | 5.43 | 184.8 / 253.0 | 44.6 | 37.4 | 36.6 / 31.8 | 34.2 |
| G again | 5.45 | 181.7 / 257.2 | 42.7 | 54.8 | 33.2 / 32.4 | 36.2 |

At 1280 the harness itself is main-thread bound (the synthetic camera redraws a
1280-wide image on the page thread every 33 ms; finish is 35 ms versus 12 ms in
the owner's run; the face reply queues 80 ms). That understates GPU-side
levers, which is why the 640 table is the better guide for them. The face-CPU
gain appears in both. Raw results: `experiments/speed-lab/qa/output/fps-review-2026-09-13/`.

### Phone (iPhone 17 Pro, 720x1280, owner runs)

Sep 12: G 13.87 updates/s with matching masks on only 41.9% of tracked frames;
Q/R/S/T 13.17–13.88. Sep 13 (`ar-mobile-comparison-2026-09-13T05-23-56.760Z.zip`):
G 14.19 / 13.05 (rounds 1 / 2) with masks on 67% / 59% of tracked frames;
the byte-readback candidate 14.32 / 13.98 with masks on 100% / 100% and hair
extraction 13.6 ms versus 28.9–31.7 ms. On the phone the hair readback governs
mask delivery, not fps. Hair inference itself is 26–28 ms there.

## Where a laptop frame goes, and why the earlier candidates could not help

Chrome runs every WebGL context's command buffer on one GPU-process thread. A
blocking call in any context (the hair worker's synchronous float `readPixels`
of the full-size mask, the main thread's `getBufferSubData`, the branch's
synchronous `readPixels`) stalls the others for its duration. That is why the
main thread's PBO extract is 23.6 ms for a 3.7 MB copy that takes 2 ms in Node,
why the face worker's 28 ms "inference" is several times what its 192x192
landmark model costs, and why shrinking the hair mask today only moved time from
`extract` into `wait` (640 table, row 3) without changing the interval.

Per-frame GPU-process consumers, in rough order on this laptop: hair
segmentation (25–30 ms wall in its worker, model fixed at 512x512 regardless of
input), face landmarker (15–25 ms of the 28 ms wall), beauty render with
MSAA, the transmission pass and its mipmaps (about 10 ms), the temple branch on
20–25% of real frames (a second full render plus a synchronous read), the
camera texture upload and the readbacks. The two inference delegates are the
largest and the only ones that can be moved off the GPU without changing the
rendering.

Main-thread consumers per frame: `getBufferSubData` block (24 ms), CPU compose
and checks (12 ms, 35 ms when the thread is contended), camera `drawImage` and
`getImageData` (8.6 ms), source hash copy and digest (6 ms), Three submit
(3.4 ms), branch submit and read (17 ms on 20% of frames), UI summaries (about
1–2 ms). Prefetch overlaps the next frame's inference with this work, but every
one of these items sits on the serial path to the next publication.

## Ranked ways to raise fps

| # | Change | Effect | Pixels | Evidence | Cost |
| --- | --- | --- | --- | --- | --- |
| 1 | Face landmarker on the CPU delegate (`?face=cpu`) | +9–13% fps today on battery; expected larger on mains and at 720p because the freed GPU time also shortens the PBO extract block | Landmarks differ at float noise level; pose and occlusion may shift sub-pixel | Measured, two sizes, repeated baseline | Done, opt-in |
| 2 | Render worker: Three.js beauty + branch + PBO readback + compose in a worker on OffscreenCanvas; main thread keeps capture, workers and display | Removes ~55 ms/frame of main-thread block; predicted interval → GPU floor (50–60 ms on mains ≈ 17–20 fps; lower floor with #1) | Byte-exact in principle (same GL and same compose code) | Estimate from the receipt's timeline; not built | Large refactor; Hold/export ownership must cross the worker boundary |
| 3 | Capture through `VideoFrame.copyTo` (RGBA) instead of `drawImage` + `getImageData` | About 8 ms/frame of main-thread time (10% of the interval) | Different YUV→RGB rounding; every safeguard compares render to source, so internal consistency holds | Estimate from `sourceDrawMs` 7.6 + `sourceReadbackMs` 1.0 | Small; Chrome only, needs a fallback |
| 4 | 32-bit word compares and scan restriction in `composeHairArmsFast`; reuse the output buffer | About 4 ms/frame | Byte-exact | Node microbenchmarks in the Sep 10 review | Small |
| 5 | Hair on alternate frames (mask from frame N applied to N+1 only when the pose moved less than a threshold) | Halves the largest GPU consumer; on the phone also raises mask coverage | Breaks the exact image/mask pairing rule | Not measured; policy | Owner decision |
| 6 | Mains-power hint and a measurement rule: all fps claims on AC | Explains 1.6–2x swings between days | None | Battery status observed today; receipts differ 1.6x on identical settings | Trivial |

Refuted today (no fps change beyond noise, real GPU): hair input capped at
320/640 px (cuts the worker's extraction by 7–19 ms but the interval does not
move); the 256-pixel multiclass model instead of the 512 hair-only model;
transmission target at 0.5x. Refuted earlier and not worth re-running: H/I/J/K/L,
Q/R/S/T, the CPU hair delegate (+110 ms), MessageChannel fence yields, merging
contexts, skipping the branch when the corridor is fully protected.

## What changed in the tree

Opt-in, URL-gated experiments on the G page; with no parameters every default
reproduces G exactly. `experiments/speed-lab/experiments.ts` (new) parses
`?face=cpu|gpu`, `?hair=<max edge px>`, `?tx=<0–1>`; `live-main.ts` shows an
"experiment active" line and passes the settings; `live-pump.ts` downsizes the
hair input when asked and records `experiment.*` fields in every sample;
`performance-stage2/face-detector.ts` accepts a forced delegate (no GPU→CPU
retry when forced); `speed-lab/native/renderer.ts`, `speed-lab/temples/renderer.ts`
and `performance-candidate/temples/branch-renderer.ts` accept an optional
`transmissionResolutionScale` (default 1). All six edited files are on the
efficiency-lab's pinned G manifest (`experiments/efficiency-lab/qa/g-base-manifest.json`),
so that lab's preservation check will report them as changed; the defaults are
unchanged; `tsc`, `npm run check`, the 52 speed-lab/face unit tests and the
production G browser suite (`playwright.current.config.ts`, 11 of 11, real
workers and GPU, 4.7 min) pass with the edits in place.

Scripts: `experiments/speed-lab/qa/receipt-stats.mjs` (per-stage medians from
any timing receipt) and `experiments/speed-lab/qa/measure-experiments.mjs`
(runs named configurations on the real GPU with the synthetic camera and prints
the tables above).

## How the owner should verify item 1

1. Plug the laptop in. Run `npm run dev`. Open the page, Open camera, move as
   usual for 40 s, Download timings only. Close the camera.
2. Open `http://127.0.0.1:8040/experiments/speed-lab/live.html?face=cpu`,
   repeat the same movements, Download timings only.
3. Repeat both in the reverse order.
4. `node experiments/speed-lab/qa/receipt-stats.mjs <file>` on each; compare
   fps, interval p95, gaps over 100 ms and mask coverage together, and look at
   the glasses' seating and the nose edge while turning.

If the CPU delegate wins on mains as it does here, promote it as the default
with the GPU path as the fallback (the `delegate` option already supports
that), then decide on item 2.
