# Lenses AR v4 — Perfecto long hair but slow

The owner accepted the reviewed long-hair version and authorized its integration,
commit and push as **perfecto long hair but slow** on September 9, 2026.
This is the current AR entry point. Performance work remains: mobile smoothness,
sustained frame rate and thermal behavior have not been established.

This development app stays inside `ar_v4/`. The parent Lenses Python application,
its Railway deployment and unrelated model-studio work are separate.

## Run

Requires Node.js 22.18+ and a camera-capable browser on localhost or HTTPS.
Run from `ar_v4/`:

```powershell
npm ci
npm run assets
npm run dev
```

Open [the AR mirror](http://127.0.0.1:8040/). The default route opens
`experiments/hair-live-preview/live.html`. Select Amber Horizon or Tom Ford and
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
- `npm run dev:reference` starts the original development configuration. Default
  hair routing belongs only to `vite.hair.config.ts`; `index.html`, the original
  Vite configuration and shared rendering sources are not replaced.

The hair server resolves reference imports to the hash-verified
`references/perfect-temples/` snapshot. Those 45 files are exact copies from
`b26b558`, so unrelated local model changes cannot alter the reviewed renderer.
A fresh or shallow checkout needs no old Git objects or private recovery files.
Both pinned hair weights are included under `public/models/hair/` and verified
during preparation and startup. See their attribution and manifest.

## Behavior and limits

The reviewed face detector, image/pose pairing, optical front, Raw + Option 17
nose configuration, rear-temple curve and 15 mm fade are unchanged. A same-frame
hair mask can replace eligible visible arm pixels with their original camera
pixels. The reviewed continuity rule removes rear fragments that hair newly
detaches from a connected arm. Every existing nose/front/outside-arm protection
remains active. Hair over the protected optical front is deliberately limited;
the masks provide neither measured hair depth nor anatomical fit.

The implementation uses local workers, hardware GPU where available with fresh
CPU initialization fallback, category-only live masks, overlapping work and no
new hair inference when hair is off. It remains slow. Short desktop simulated
camera measurements reached about 5.35–5.48 updates/second with hair and
8.18–8.57 without; these are not phone or sustained physical-camera results.
Generated portraits and exact recorded pairs support bounded visual checks,
not proof of realistic motion or universal non-regression.

## Build and check

```powershell
npm test
npm run build
npm run preview
```

The build includes long hair, perfect temples and original perfecto pages, the
verified reference renderer and both local hair weights. The default preview
route opens long hair. `npm run preview:reference` retains the original landing
route for the baseline recording/lifecycle tests. The standard tests include
strict types, reference and hair unit tests, baseline browser flows and the
long-hair production lifecycle using a checked-in synthetic fixture.

Checkpoint verification passed strict types, build, 110 unit tests and both
new production hair lifecycle tests. All 56 matched generated/recorded
comparisons preserve the reviewed pixels and geometry. The full `npm test`
still exits nonzero: seven baseline browser tests pass, while the existing
60-second recorded-replay test times out during final camera restart after
its exact replay image check passes. Its deadline was not changed.

The earlier private generated galleries, masks and wearer recordings remain
Git-ignored and are not bundled. Optional original studies require those files.
See [HANDOFF.md](HANDOFF.md), [current review](docs/REVIEWS.md),
[hair implementation](experiments/hair-live-preview/README.md) and
[asset attribution](ATTRIBUTION.md).
