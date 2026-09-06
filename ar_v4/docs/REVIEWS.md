# Current baseline review — 2026-09-06

## Live demo restored; AR kept in development

- Restored the live Lenses Python application, catalog, dependencies and Procfile
  at the repository root from prior main commit `cb564a8`. AR remains under
  `ar_v4/`, outside the live handler and Railway's root build entry point.
- Verified a 40-file AR checkpoint archive before relocation. Moved current AR
  source, assets, tests and the private recording byte-for-byte; no rendering,
  tracking or capture algorithm changed. All 75 dependency entries are unchanged.
- Recovery archives remain in the parent's `../.recovery/`. Exact restoration,
  tests and publication receipts are in `../.recovery/restore-live-2026-09-06/`.
  Linked worktrees are untouched. V2/v3 remain archived, not active applications.
- Added root/AR instructions to preserve the production boundary. AR has its own
  package manifests and synthetic fixture exception to the parent's photo ignore.
  Capture schema identifiers remain compatible; private recordings stay untracked.
- Live verification passes 67 UI and 170 root checks (237 total), without Gemini
  calls. The real Python handler returns 404 for `/ar_v4/` and `/ar_v4/index.html`.
  The test's incidental template line-ending change was restored to original bytes.
- From `ar_v4/`, `CI=1 npm test` passes strict TypeScript, the production build,
  15 unit checks and all five real local MediaPipe browser flows (50.9 seconds),
  using a simulated camera. Capture/replay/export, cancellation, fallback, failure,
  stop and restart remain covered. Current source and model bytes are unchanged.
- Final staged review retains all 230 previous-main files, 40 nested AR files and
  a root development-boundary instruction file. Only README/ignore instructions
  differ from the previous live tree. Private recordings and recovery are ignored.

## Amber Horizon model replacement

Amber Horizon replaces Navigator. The original Blender file and the 96-frame
wearer recording remain byte-identical. Earlier cleanup notes are retained in
`../.recovery/model-swap/before-files/REVIEWS.md`; this is the current checkpoint.

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

Receipts: `../.recovery/model-swap/export-report.json`, `render-review.json`,
`npm-test-final.log` and `final-review.json`. Current asset attribution and
attachment conventions are in ATTRIBUTION.md and README.md.
