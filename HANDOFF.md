# Current mirror / Amber Horizon handoff — 2026-09-06

The cleaned v4 application has been promoted to the repository root as the sole
application on `main`. Run commands here. Older Python/v2/v3 code, catalogs and
launch configuration were archived and removed. Package name is `lenses`; capture
schema/download identifiers stay `ar_v4` / `ar-v4-*` for compatibility.

Current mirror now uses the owner's Amber Horizon Blender model. The source file
is unchanged; `public/models/amber-horizon.glb` is a self-contained 3.7 MB export
with 99,521 triangles, a 2K frame texture and baked gradient lenses. README records
the fixed model scale/attachment. Its front plane retains the prior mirror depth.
Imported texture bitmaps now close on disposal. No tracker, face-surface,
occlusion, reconstruction or wearer-specific fitting algorithm changed.

The owner preferred Current mirror over experimental shared-face fitters. Those
fitters, comparison UI/runners, outputs and historical documents remain archived
and absent from the active project.

The surviving application is `src/main.ts` → `runtime/`, `render/`, `capture/`.
Retain exact image/detection/pose ownership; invalidate sessions before disposal.
Replay retains its renderer but closes the camera/worker. Discard closes replay;
the next camera session gets a new canvas, worker and abort owner.

Use `npm ci`, `npm run assets`, `npm run dev` (localhost:8040). Run `npm test` for
Node checks + strict production build + actual local MediaPipe browser checks.
Only the existing Node test runner and Playwright are used. See README for usage,
geometry conventions and limits; docs/REVIEWS.md holds the final cleanup review.
The preceding cleanup passed 15 units + five browser flows and verified identical
baseline renders. Model-swap verification is recorded in docs/REVIEWS.md and
`.recovery/model-swap/`. Physical camera start/stop/restart passed during cleanup
with no face detected; actual wearer tracking remains an empirical check.

The original cleanup archive is under `.recovery/`; an additional verified
pre-promotion working-file archive and complete Git bundle are in
`.recovery/promotion-2026-09-06/`. See their READMEs and receipts before restoration.
All are local and ignored by Git. The original 96-frame wearer export is preserved
byte-for-byte in `recordings/`, including two missing-face detections. Do not
replace original detections by rerunning a tracker or merge unrelated poses.
The archive also retains the removed experiments; they are historical evidence,
not active implementation or instructions to resume them.

Next work requires research first: far lens/rim visibility through the nose at
larger yaw, potentially using independently observed image boundaries and color
contrast during a scan. Exact failure pixels, useful evidence and a general
method remain unknown. No implementation or wearer-specific tuning is authorized
by this cleanup. Preserve this baseline for future matched wearer comparisons.
