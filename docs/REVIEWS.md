# Current baseline review — 2026-09-06

## Root promotion

- Promoted cleaned v4 to the repository root as the sole application on `main`.
  Removed old Python/UI/catalog applications and v2/v3, obsolete launch/deployment
  files, design notes, tests and local generated material: 1,531 working files.
- Before deletion, archived and independently SHA-256-verified all 1,723 selected
  working files, including ignored private legacy assets and uncommitted work.
  A separately verified Git bundle retains all pre-promotion refs and history.
  Recovery is in `.recovery/promotion-2026-09-06/`. Existing v4 recovery and the
  original wearer recording remain byte-identical. Linked worktrees are untouched.
- Independent review confirmed source, rendering assets, scripts and tests are
  unchanged by relocation. All 75 dependency lock entries remain unchanged.
  Updated package name/Node requirement, root docs and Git exclusions; explicit
  Git attributes preserve prepared-asset bytes across platforms. Existing capture
  identifiers remain compatible. No rendering or tracking algorithm changed.
- A fresh root `npm ci` and `CI=1 npm test` pass: strict TypeScript, 15 unit checks,
  production build and all five actual local MediaPipe browser flows (1.4 minutes).
  Tests exercise tracking, exact capture/replay/export pairing, cancellation,
  failure, fallback, stop and restart with a simulated camera.
- Reviewed the exact 40-file publication tree: no private recordings, recovery,
  generated runtime, dependencies or old applications are included. A separate
  checkout of those staged files passed `npm ci` and the production build; all
  four required asset hashes match. `git diff --check` passes. The existing large
  Three.js chunk warning remains. Deployment requires static `dist/` hosting;
  the former Python entry point is obsolete. Wearer tracking remains empirical.

## Amber Horizon model replacement

Amber Horizon replaces Navigator. The original Blender file and the 96-frame
wearer recording remain byte-identical. Earlier cleanup notes are retained in
`.recovery/model-swap/before-files/REVIEWS.md`; this is the current checkpoint.

- Inspected the supplied file with embedded script execution disabled. Its single
  mesh had 1,990,429 triangles and an 8K frame atlas. Applied its existing seam
  smoothing, reduced to 99,521 triangles, resized the atlas to 2K and baked the
  object-height lens gradient into an 8 × 512 PNG. The self-contained GLB is
  3,670,784 bytes. Transmission 1, IOR 1.586 and roughness 0.05 are preserved.
- The normalized source has no verified physical size. A fixed 145 mm width is
  assumed; bridge height follows canonical point 168. Review caught frontal
  clipping when depth also followed that point. Corrected the asset attachment
  to retain Current mirror's original 6.691763 cm front plane. This is fixed
  model alignment, not wearer-specific tuning or new occlusion logic.
- Imported textures exposed a cleanup gap: Texture.dispose does not close decoded
  ImageBitmap pixels. The renderer now closes shared imported bitmaps exactly
  once. The camera canvas remains caller-owned. The existing cancellation test
  now includes texture and shared-bitmap disposal; no test framework was added.
- The first export retained original packed 8K bytes despite resized image data.
  Reloading the saved 2K image corrected that. Blender 5.2 produced the export
  and then crashed during shutdown; independent GLB parsing, embedded-image
  decoding and actual browser rendering verified the completed artifact.
- Updated model URL, hash/provenance, UI name/swatch, geometry check and docs.
  Removed the old active GLB after verifying its archived hash. No runtime
  dependency was added. Tracker, projection, bridge correction, observed surface,
  capture storage and detector worker bundle are unchanged.

Final `npm test` passes: 15 unit checks, strict TypeScript, production build and
all five browser flows (1.3 minutes). They cover actual local MediaPipe and
embedded-texture loading, exact capture/replay/export pairing, cancellation,
failure, fallback, stop and restart. Node tests mock only image decoding with
real Three textures; browser tests exercise the actual embedded image bytes.

Independent rendered review passes the synthetic front view and saved wearer
frames 0, 24 and 72: upright frame, continuous frontal bridge/rims, retained
tortoiseshell and gradient. Original detections were used without refitting;
missing-face frame 69 hides the glasses. Approximate face/head occlusion can
still hide temple segments near hair, and large-yaw nasal overlap remains an
existing limitation. No physical fit, anatomical accuracy or live wearer
acceptance is established by these checks. Research remains pending.

Receipts: `.recovery/model-swap/export-report.json`, `render-review.json`,
`npm-test-final.log` and `final-review.json`. Current asset attribution and
attachment conventions are in ATTRIBUTION.md and README.md.
