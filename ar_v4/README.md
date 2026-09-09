# Lenses AR v4 — G Combined

G Combined is the owner-selected current base as of September 9, 2026.
The default launch opens the comparison with **Current · G Combined** selected.
It combines bounded overlap between successive images, source-pixel reuse,
fewer source copies, asynchronous GPU downloads and temple prewarming.
Models, resolution, image/pose/mask pairing and final nose/front safeguards remain.
Test 2 (**speed testing**, `8baa16c`) remains an independently launchable reference.

The visually accepted **perfecto long hair but slow** checkpoint (`9997050`)
and **perfect_temples** (`b26b558`) remain available. This default selection is
explicitly authorized; it does not establish universal visual equivalence or
mobile smoothness. Future optimizations remain separate experiments.

This development app stays inside `ar_v4/`. The parent Lenses Python application,
its Railway deployment and unrelated model-studio work are separate.

A separate [performance comparison candidate](experiments/performance-candidate/README.md)
implements the first three review recommendations. Run `npm run dev:performance`
and open [Current / Test comparison](http://127.0.0.1:8066/experiments/performance-candidate/live.html).
It supports live switching and an exact held-image comparison. Test 1 remains available for comparison.

A separate [Test 2 and live profiler](experiments/performance-stage2/README.md)
adds Current / Test 1 / Test 2 switching, measured AR and camera delivery rates,
and a timed three-way comparison. Run `npm run dev:stage2` and open
[the profiling preview](http://127.0.0.1:8072/experiments/performance-stage2/live.html).
Test 2 shares the native camera rendering context while preserving the existing
image/pose/mask pairing and final safeguards. It is the previous development base;
30 fps is a target, not an established result.

A separate [Test 3 GPU hair experiment](experiments/performance-stage3/README.md)
adds a fourth switchable pipeline and reports its actual GPU hair usage. Run
`npm run dev:stage3` for port 8076, or build and preview on port 8078 with
`npm run build:stage3` then `npm run preview:stage3 -- --port 8078`.
[Open the four-way comparison](http://127.0.0.1:8078/experiments/performance-stage3/live.html).
Hold compares one exact image across all four. The new compositor keeps beauty
and camera images on the GPU and retains CPU continuity and separate rear-temple
fallback where required. This remains a separate experiment awaiting visual review.

## Run

The [speed comparison](experiments/speed-lab/README.md) retains Test 2, six
individual speed ideas and the selected G Combined mode. Run
`npm run dev:speed-lab`, or build and preview with
`npm run build:speed-lab` and `npm run preview:speed-lab`.
[Open the experiment comparison](http://127.0.0.1:8083/experiments/speed-lab/live.html).
It includes live switching, a base-versus-selected timed run, and an exact held
comparison across all eight. G is selected initially in both launch modes.

Requires Node.js 22.18+ and a camera-capable browser on localhost or HTTPS.
Run from `ar_v4/`:

```powershell
npm ci
npm run assets
npm run dev
```

Open [the AR mirror](http://127.0.0.1:8040/). The default route opens
`experiments/speed-lab/live.html`, with G Combined selected. Select Amber Horizon or Tom Ford and
one hair model, then Open camera. Hair-only is selected initially. Only the
selected model runs. Compare hair on/off, or Hold frame to stop camera/workers
and compare one exact image. Download held comparison is an explicit local action;
Resume starts a fresh session. Camera frames are processed locally.

## Preserved references

- [Perfect temples without hair](http://127.0.0.1:8040/experiments/temple-sagittal/live.html)
  preserves `perfect_temples` (`b26b5584c0dccbc2b30e4f12cdd432f10df577ea`).
- [Original perfecto](http://127.0.0.1:8040/index.html) preserves the original
  renderer from `31df28eb8ca0c698467fd9bfb16f737f9a74e915`. Its recording/replay
  workflow remains available.
- `npm run dev:long-hair` / `build:long-hair` / `preview:long-hair` retain the
  accepted long-hair launch configuration (`vite.hair.config.ts`).
- `npm run dev:test2` / `build:test2` / `preview:test2` retain Test 2's launch
  configuration (`vite.speed.config.ts`) and its old comparison entry.
- `npm run dev:reference` / `preview:reference` retain the original landing route.
  The new G default belongs only to `vite.combined.config.ts`.

The speed and hair servers resolve reference imports to the hash-verified
`references/perfect-temples/` snapshot. Those 45 files are exact copies from
`b26b558`, so unrelated local model changes cannot alter the reviewed renderer.
A fresh or shallow checkout needs no old Git objects or private recovery files.
Both pinned hair weights are included under `public/models/hair/` and verified
during preparation and startup. See their attribution and manifest.

## Behavior and limits

The owner-supplied four-way timing report measured 7.71 fps for the long-hair
checkpoint, 9.94 for Test 1, **10.16 for Test 2**, and 9.96 for Test 3, with
reported video delivery near 29.6 fps. All four 30-second windows had full
face/mask coverage. This fixed-order desktop run used different live images;
Test 3 encountered more expensive rear-drop poses. It does not measure
motion-to-photon delay or prove visual equivalence.

The reviewed face detector, image/pose pairing, optical front, Raw + Option 17
nose configuration, rear-temple curve and 15 mm fade are unchanged. A same-frame
hair mask can replace eligible visible arm pixels with their original camera
pixels. The reviewed continuity rule removes rear fragments that hair newly
detaches from a connected arm. Every existing nose/front/outside-arm protection
remains active. Hair over the protected optical front is deliberately limited;
the masks provide neither measured hair depth nor anatomical fit.

The implementation uses local workers, hardware GPU where available with fresh
CPU initialization fallback, category-only live masks, overlapping work and no
new hair inference when hair is off. Historical long-hair desktop simulated
camera measurements reached about 5.35–5.48 updates/second with hair and
8.18–8.57 without; these are not phone or sustained physical-camera results.
Generated portraits and exact recorded pairs support bounded visual checks,
not proof of realistic motion or universal non-regression.

## Build and check

`npm test` now includes G's strict types, unit checks and production default-entry
tests before the preserved reference and long-hair browser suites.
`npm run test:speed-lab` includes its renderer, scheduler, temple and QA protocol
checks. `npm run test:speed:browser` tests the promoted G production entry with
both frame and hair models, held equality, cancellation and restart.
`npm run test:test2:browser` retains the earlier Test 2 default-entry suite.
Optional recorded evidence harnesses require the preserved private inputs and are not part of a
fresh checkout. See the latest validation in [docs/REVIEWS.md](docs/REVIEWS.md).

```powershell
npm test
npm run build
npm run preview
```

The build includes long hair, perfect temples and original perfecto pages, the
verified reference renderer and both local hair weights. The default preview
route opens G Combined; comparison and prior reference pages are included. `npm run preview:reference` retains the original landing
route for the baseline recording/lifecycle tests. The standard tests include
strict types, reference and hair unit tests, baseline browser flows and the
long-hair production lifecycle using a checked-in synthetic fixture.

G's matched evidence includes all 56 generated/recorded pairs with exact pixels,
geometry and safeguards, followed by an independent PNG audit. Hardware used
asynchronous downloads in all 32 cases; five of 24 software cases used the
explicit bounded-time synchronous fallback. The owner reported F and G felt
far superior and explicitly selected G. This is qualitative owner feedback,
not a quantified new camera benchmark or mobile/thermal measurement.
Clean promotion verification passes strict types/build, all 172 unit checks and
all 21 browser cases. Details are recorded in [docs/REVIEWS.md](docs/REVIEWS.md);
preserved test deadlines remain unchanged.

The earlier private generated galleries, masks and wearer recordings remain
Git-ignored and are not bundled. Optional original studies require those files.
See [HANDOFF.md](HANDOFF.md), [current review](docs/REVIEWS.md),
[hair implementation](experiments/hair-live-preview/README.md) and
[asset attribution](ATTRIBUTION.md).
