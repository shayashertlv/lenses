# Current mirror / Amber Horizon handoff — 2026-09-06

The cleaned v4 application lives in `ar_v4/` and remains in development.
Continue the nasal-occlusion work on `codex/ar-v4-nose-occlusion`; keep `main`
unchanged because it is the live Python application's deployment branch.
The parent directory is the live Lenses Python application connected to Railway;
its original UI, catalog, dependencies and `Procfile` are restored from `cb564a8`.
Run AR commands here and keep all AR manifests/assets/tests inside this directory.
Package name is `ar-v4`; capture identifiers remain `ar_v4` / `ar-v4-*`.

Current mirror now uses the owner's Amber Horizon Blender model. The source file
is unchanged; `public/models/amber-horizon.glb` is a self-contained 3.7 MB export
with 99,521 triangles, a 2K frame texture and baked gradient lenses. README records
the fixed model scale/attachment. Its front plane retains the prior mirror depth.
Imported texture bitmaps now close on disposal. That model swap changed no
tracker, face-surface, occlusion, reconstruction or wearer-specific fitting algorithm.

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
`../.recovery/model-swap/`. Physical camera start/stop/restart passed during cleanup
with no face detected; actual wearer tracking remains an empirical check.

The original cleanup archive is under `../.recovery/`; an additional verified
pre-promotion working-file archive and complete Git bundle are in
`../.recovery/promotion-2026-09-06/`. See their READMEs and receipts before restoration.
All are local and ignored by Git. The original 96-frame wearer export is preserved
byte-for-byte in `recordings/`, including two missing-face detections. Do not
replace original detections by rerunning a tracker or merge unrelated poses.
The archive also retains the removed experiments; they are historical evidence,
not active implementation or instructions to resume them.

Current mirror now includes a limited local RGB-supported nasal contour correction.
It changes selected camera-space X coordinates only, preserving observed depth,
glasses placement and ordinary depth testing for transmissive lenses. It uses
depth-visible nasal edges and coherent image evidence, with geometry guards and
fallback to the observed surface; no frame/yaw lookup or personal fitter is used.
Capture retains the resulting surface, and replay restores that exact paired
surface instead of deriving a different refinement from decoded JPEG pixels.

The owner confirmed visible improvement on September 6 after viewing the matched
comparison. Acceptance is limited: independent held-out RGB review finds small far-rim gains
in frames 43 and 93, with no clear material near-rim/background regression in the
five evaluation views. Upper crossings and physical seating remain unresolved;
some central-band changes are visually uncertain. Development 66 improves only
partially, 24 is unchanged, and this does not reproduce every preferred probe.
Sparse temporal checks are not live-video validation. Local warm processing cost
is about 6 ms median / 11 ms p95 including readback, excluding tracking/render.

The fresh September 6 capture (16:04 UTC, downloaded at 16:05) is preserved in
`.recovery/nose-fresh-2026-09-06-160512/`. Its 91 frames include 72 valid surfaces
and 19 empty detections, with no stale pose/surface pairing. Held-out frames 38
and 72 retain small far-side gains; four evaluation views are pixel-identical.
Stress frame 42 has a tiny ambiguous central-frame exposure change. The supplied
depth is consistent with rendering: clear remaining nasal-skin crossings hit
far-eye/cheek geometry behind the glasses. The upper internal nose/eye boundary
remains insufficiently supported by the current background-connected RGB cue.
The subsequent private internal-boundary prototype was implemented and rejected:
its initial texture cue admitted shading; after fixing that defect and restricting
the region, all four development and seven reserved views stayed pixel-identical.
No additional correction is enabled. Preserve this accepted version; 300 ms capture
sampling and changing pitch/roll leave live flicker unverified. The bounded attempt,
frozen code and receipts are under `.recovery/nose-internal-2026-09-06/`.

See docs/REVIEWS.md and private `.recovery/nose-local-boundary-2026-09-06/` for the
frozen method, matched before/after images, integrated parity/lifecycle checks,
independent reviews and remaining uncertainty. Earlier rejected experiments and
owner ratings remain in the other private `nose-*` recovery directories. The
verified original baseline archive in `nose-2026-09-06/` remains recoverable.
Keep the original recording byte-identical; no tracking was rerun. No physical
fit, anatomical accuracy or broad reconstruction result is established.

The September 6 primary-source research scout recommends one frozen SegFace
MobileNet512 boundary-only offline test next, before any further geometry change.
Its semantic nose mask is a candidate cue, not independent depth truth; exact
preprocessing, held-out cutoff gains, false hiding and browser cost remain untested.
Depth Anything V2 Small is an optional relative-depth comparator; 3DDFA_V3 is a
conditional geometry reference with unresolved asset terms. No model was installed
or accepted. The cited report and verification are private under
`.recovery/nose-research-2026-09-06/`; preserve the current accepted correction.

Product constraint: this is intended for ecommerce on mobile and desktop browsers.
Windows-only research feasibility is insufficient for choosing a production model.
Before geometry integration, check commercial code/weight/asset provenance and the
exact browser inference path on representative iPhone Safari, midrange Android
Chrome and ordinary laptops, including integrated GPUs. Use sustained 30 fps as a
provisional whole-app target, measuring latency, loading, memory and thermal
slowdown separately. Any optional refinement must retain exact image/pose/session
ownership and leave the accepted mirror available when unsupported or too slow.
No smooth cross-device performance has been established for SegFace or the current
complete app. The offline cue test remains exploratory, not a deployment decision.

The publication checkpoint passed a fresh `CI=1 npm test`: 15 unit tests, strict
TypeScript, asset verification, production build and all five browser lifecycle
flows. Independent geometry and lifecycle reviews found no blockers. The source
and dependency graph match the accepted correction; publication adds no model.
