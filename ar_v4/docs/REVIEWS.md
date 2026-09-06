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

## Nose occlusion — limited local correction accepted

The owner's matched-probe feedback identified different useful contour shifts
in frames 66 and 24, with a mixed response in 62. At fixed glasses placement,
sampled cut-through rays hit farther cheek geometry or no face. The leading
problem there is missing projected nasal coverage; uniform depth, seating and
15 px contour changes do not generalize. Upper contour and physical seating
remain uncertain. Full earlier investigations and owner ratings are private in
`.recovery/nose-2026-09-06/`, `nose-upper-2026-09-06/` and
`nose-consistency-2026-09-06/`. Their rejected prototypes are not active imports.

The local correction in `src/render/nasal-boundary.ts` is accepted as a small
visual improvement, with the limitations below. Detailed design, frozen source,
matched renders and independent reviews are in
`.recovery/nose-local-boundary-2026-09-06/`. The verified original 41-file baseline
archive remains available; its recording and active assets were not overwritten.

- The method finds depth-visible nasal tangent edges and seeks nearby original-RGB
  color transitions connected to a background region. It allows smooth color
  variation within each side and rejects dark/isolated/unsupported evidence.
  The whole-face outline only locates a background seed; it is not a nose target.
  Eligibility stops at the canonical nose root after development review caught
  a false association with the far eyelid boundary.
- Supported rows fit sparse surface X changes. Y/Z, raw detection, glasses pose,
  scale, depth and lighting remain unchanged. Bounds cover original visible
  incident contour edges, including edges outside the nasal eligibility region.
  The actual Float32 result is revalidated; unfavorable geometry falls back to
  the observed surface. Emergent contour accuracy is still assessed visually.
- Refinement is synchronous on the exact presentation image with no retained
  temporal state. Capture stores the resulting surface. Replay restores that
  paired surface rather than recalculating from JPEG-altered RGB. Camera/worker
  cancellation and session ownership remain unchanged. Lenses retain normal
  face depth testing; no fade, dilation, depth bias or frame/yaw lookup is added.

The candidate was frozen before viewing evaluation RGB frames 8, 33, 43, 82 and
93. These were held out from RGB/candidate tuning; previous geometry audits had
read their detections. Independent matched review finds narrow far-rim gains on
visible nasal skin in both directions: frame 43 at (835,409) and frame 93 at
(412,390)/(406,403). Much of the upper cut-through remains. Frontal 8 is pixel
identical. Frame 82 restores seven edge pixels over background. Frame 33's mostly
central-band change and some central changes in 43/93 remain uncertain; local-X
role bands are not anatomical bridge labels. No clear near-rim or background
over-hiding regression was observed in these five evaluation views.

Development 66 improves partially; 24 remains pixel-identical and 62 changes a
small lower strip. These do not reproduce every owner-preferred probe result.
The production renderer matches all eight frozen development/evaluation renders
byte-for-byte, including replay of the paired surface. Original detections and
eyewear matrices are preserved. No physical fit or anatomical accuracy is claimed.

Sparse temporal checks on 32/33/34 and 92/93/94 show no obvious new cutoff jump
or material regression. They are about 300 ms apart with changing pose, so this
is not proof of live-video stability. Across 72 repeated calls, output is
deterministic; warm total cost including full image readback is 6.125 ms median
and 10.81 ms p95 on this machine in headless Chromium, excluding tracking/render.

The full integrated `CI=1 npm test` passed: strict TypeScript/build, all 15 unit
checks and all five browser flows (54.0 seconds for the browser suite). The checks
include exact replay-surface retention without RGB readback, and simulated-camera
flows with actual local MediaPipe: tracking, capture/replay/export, cancellation, failure,
stop and restart. No physical wearer camera was tested. Final dependency/diff
and recording-hash receipts are private alongside the matched comparisons.
No dependency, parent application, deployment setting, commit or push changed.
The original recording SHA-256 remains
`8a16382c541093562eb1fc817f3d77889ae46c8450deacc3b792740fffe46e9c`.

The owner confirmed visible improvement in the matched comparison on September 6.
This is approval of the observed partial gain, not live-motion validation. A
verified working-file checkpoint is under the private experiment directory's
`owner-accepted-checkpoint/`. No runtime behavior changed for this acceptance
checkpoint.

### Fresh capture review — September 6, 16:04 UTC

The owner's new export is preserved byte-for-byte under
`.recovery/nose-fresh-2026-09-06-160512/`, SHA-256
`6001ead5e37bf6791b6a1d97ca7f642b75b0b944d7d588cc5225140e0a07ae2f`.
All 72 valid saved poses/surfaces match the original detection and existing camera
conventions. The other 19 frames contain empty detections and no stale surface;
tracking loss is distinct from nasal cutoff. Asset hashes match. The camera's
30 fps header is not a measured rendering rate; capture sampling is about 300 ms.

Six diagnosis frames were selected from time/pose metadata before viewing RGB.
Six evaluation frames were reserved; the accepted source stayed frozen throughout.
Matched renders restore the saved surface, use original detections and compare
against unrefined reconstruction with identical glasses, lighting and projection.
Held-out 38 and 72 show small far-side gains; 45, 49, 62 and 85 are pixel-identical.
No newly hidden near-rim/lens role was observed in these samples. Stress frame 42
also exposes four central-band pixels whose correct visibility is uncertain;
stress 64 is unchanged. These role bands are not semantic bridge annotations.

Four independently selected far-rim crossings on visible nasal skin in 12/54
intersect far-eye/cheek triangles about 2.8–3.7 assumed centimeters behind the
glasses, with no rear-head hit. Six GPU/ray checks agree within 0.0022 cm. This
locates missing nasal coverage under the fixed preview placement; it does not
measure correct anatomical depth or physical seating. Upper internal nose/eye
boundaries and central bridge visibility remain uncertain. Wider background-
driven correction is not supported by this evidence.

Sparse sequences 11/12/13 and 53/54/55, plus the largest saved X corrections,
were reviewed separately. Changing pitch/roll and sparse sampling cannot establish
live cutoff stability. JPEG-recomputed refinement differs from the saved surface
in several frames and is diagnostic only; replay correctly preserves the live
surface. Runtime, tests, assets and dependency graph are unchanged from the
passing 15-unit/five-browser-flow checkpoint, so npm test was not repeated for
this read-only investigation and documentation. The accepted correction remains
active; no new geometry change earned acceptance. Private receipts record the
diff, both recording hashes, matched outputs and independent review.

### Internal-boundary prototype — rejected

The authorized isolated prototype tested local chromatic continuity and one-sided
eye texture without an external-background connection. Its initial small change
relied largely on lower shading/background variation. A synthetic counterexample
also showed that luminance variance alone was not textured evidence. Restricting
the region above canonical landmark 195, checking the selected far-side prior,
and removing linear illumination variation resolved those specific defects.

The resulting prototype changed no geometry or pixels in four development views
or seven previously unviewed reserved views (fresh 0/7/21/36/51/60/74). Source was
frozen before reserved evaluation and not tuned afterward. It therefore earns no
acceptance; the owner-approved Current mirror correction remains active. Private
code, matched renders, synthetic checks and independent reviews are in
`.recovery/nose-internal-2026-09-06/`. The missing evidence is a reliably located
internal nasal contour, not a stronger threshold or a demonstrated depth-test bug.
Active runtime, assets, dependencies and recording bytes remain unchanged.
Final verification passes: standalone strict TypeScript and the private synthetic
checks, plus `CI=1 npm test` with all 15 unit checks, strict production build and
five browser lifecycle flows (53.5 seconds). Independent review verifies all 11
final before/after PNG pairs and surfaces are identical to their accepted bases.
There is no added application runtime cost; live wearer stability remains unverified.

### Nose research scout - September 6

Primary papers, author releases and platform documentation support a bounded
SegFace MobileNet512 boundary-only offline experiment as the next useful test.
Manual semantic nose labels are independent of the current MediaPipe surface,
but do not establish self-occluding geometry. Its reported FPS is not verified
browser timing. Depth Anything V2 Small offers a relative-depth comparator;
3DDFA_V3 has relevant regional scan results but separate model/asset terms remain
unresolved. Neither regional 3D scores nor teacher-depth agreement proves the
upper nasal cutoff. The scout corrects unsupported claims in the earlier memo
about mandatory fading, outer-only contour methods and blanket browser limits.

The private six-page cited report is under `.recovery/nose-research-2026-09-06/`.
Two source reviews and all-page PDF inspection passed. The proposed test seals
reserved candidate outputs until conversion is frozen and scores newly hidden
near rim/bridge alongside far-side gains. No model, SDK, dependency, runtime or
test code changed; both recording hashes match. npm test was not repeated for
this research/documentation-only update; the prior 15-unit/five-browser-flow
verification remains the latest behavior check. No correction was promoted.

The owner emphasized mobile and desktop ecommerce suitability after the scout.
Added that product constraint to HANDOFF: commercial provenance and a small real
device browser-performance check must precede geometry integration; an offline
quality result alone is insufficient. SegFace's paper does not establish browser
FPS. Safari 26 ships WebGPU according to WebKit, despite the older-looking ONNX
Runtime support table, so validate the pinned runtime/model on real devices rather
than inferring support or speed from either API availability or the table alone.
Sources: https://arxiv.org/html/2412.08647v1 and
https://webkit.org/blog/17333/webkit-features-in-safari-26-0/ .
No code, dependencies or recordings changed for this clarification.

### Publication checkpoint - September 6

The owner authorized committing and pushing the accepted work. The checkpoint is
on `codex/ar-v4-nose-occlusion`, leaving the live application's `main` branch and
deployment unchanged. Only AR source, its focused replay assertions and current
documentation are included; private evidence stays ignored under `.recovery/`.

A fresh `CI=1 npm test` passed all 15 units, strict TypeScript, asset verification,
production build and five browser lifecycle flows (54.3 seconds). Independent
geometry and lifecycle reviews found no blockers. The dependency graph and both
recording hashes remain unchanged. This is acceptance of the limited observed
improvement, not a claim of complete cutoff, physical fit or mobile performance.
