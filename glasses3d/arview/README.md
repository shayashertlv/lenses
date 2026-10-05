# Lenses AR

The browser-based eyewear try-on pipeline served at `/ar/` by the Lenses Python application.
The root application stays on Railway, started by `Procfile` with `python -m UI.app`.
Camera images, face tracking, hair segmentation and rendering stay in the browser.

## Run and verify

Run these commands inside `ar/` with Node.js 22.18 or later:

```bash
npm install
npm test
npm run build
npm run preview
node qa/pipeline-smoke.mjs --base=http://127.0.0.1:8241
```

`npm run preview` serves the frozen build at `http://127.0.0.1:8241/`.
`npm run dev` serves the development build with hot reload at port 8240; use a frozen build for measurements.
Opening the page does not start the camera. arview has no frame catalog and no Studio bridge (WP1 stage B): the page
renders the one model handed over as `?model=<GLB address on this computer>&name=&clip=&width=&sha256=&lensenv=`
(`src/eyewear/external.ts`, a `ModelDefinition` in `src/eyewear/catalog.ts`). Without a model the camera stays closed
and the page says how to hand one over. Every fallback the load takes (an unusable handover value, a missing temple,
a stripped retired descriptor, an arm without temple continuity, ...) is a warning in the renderer's model report
(`TryOnRenderer.report()`, `window.__ar.modelReport()`). The page lists, under the model name, the refusal reasons,
then every failed §2 row (`report().errors`, worded as spec.py's `errors`; most row failures write no warning of their
own), then the warnings (`reportLines` in `src/model/report.ts`).

Loading (WP1_DESIGN.md §1, §5): the WebGLRenderer exists before the GLB is parsed; `src/model/load.ts` reads the
container and JSON, resolves external resources against the model URL's directory (fetched once, parsed from those
bytes), and parses with Draco, meshopt and KTX2 (decoders from three's `examples/jsm/libs`, served at `/decoders/` by
`vite.config.ts`). `src/model/normalize.ts` is the one normalisation pass before any subsystem: transforms (one 1e-6
check; noise within it is baked), geometry, units (60-250 units = mm, 6-25 = cm: rescaled with a loud warning),
lens identification and the measured origin/orientation rows, ported from `glasses3d/g3d_host/spec.py` and tested to
agree with it (origin x is judged on the lenses' bounding box, the front region only when no lens spans x = 0; an
origin off by more than 1 mm is a loud `ORIGIN:` warning as well as a failed row). three is handed the default scene
alone (`defaultSceneDocument` in `src/model/glb.ts`): other scenes, nodes outside it, animations, cameras, lights and
skins are never parsed, so a broken reference there refuses nothing (each omission is a warning, the lights a
fallback; animations and declared skins fail the Geometry row from the original JSON, as spec.py). Only references on
that path are refused when broken; a missing external resource is refused (used or not, WP1_DESIGN.md §1); a primitive
without POSITION fails the Format row and is not drawn. A §2 violation of the §1 table is refused (`ModelRefusal`, carrying the whole report); everything
else renders, each subsystem in its own try block, every fallback in `report().fallbacks` and `report().warnings`. The
report also has the seven §2 rows, measurements, `rendered_sha256` (the exact bytes parsed; a `&sha256=` handover is
only compared with it) and `live_site_would_fail`. Capture accessors: `cameraState()`, `assetMatrixWorld()`,
`parts()`, `projectAssetPoints()`, `setLighting()`, and `render(mask, {override})` with `part-id`, `lens-mask`,
`lens-hidden`, `lens-only` or `normals`. `src/model/check.ts` runs the same load and pass without drawing (without a
renderer, KTX2 textures are a reported `textures` fallback); the QA temple-cutoff loader uses it.

Temples, clip and fit scale (WP1_DESIGN.md §3, §4; `src/render/temple-geometry.ts`): temples are found per side by
position, whatever their opacity (crystal and alpha-blended arms are temples like opaque ones; lens-class geometry never
is): the non-lens geometry at |x| > 45 mm behind the lens rear, or the `temple_left`/`temple_right` region of a valid
`LENSES_part_visibility` profile. A side has a temple only when it reaches z <= -60 mm (the §2 Orientation rule), so
hinge hardware alone never counts. A side without one keeps a fixed cap (no arm shape, continuity, terminal return or
hair endpoint), is a `temple <side>` fallback, and the Orientation row fails with "temple <side> missing"; a model with
no temple at all still renders. Lens geometry behind -60 mm is a warning. The temple clip is derived from the arms'
tips, each side measured on its own: the one shared plane lies just inside the shorter arm, never behind it, and never
further back than -0.14 m, clamped to [-0.2, -0.045] m; a handed-over `&clip=` inside that range overrides it (and is
cross-checked against the tips), one outside it is ignored with a warning. The report records `temples`, `temple_clip`
and `fit_scale` measurements. Continuity is built from the normalised scene the renderer draws (no pin, no second
parse), per side: an arm with no cross-section somewhere before the clip loses only its own path, named with its z.
The live auto-fit's applied scale is reported, and a scale more than 3 % from 1 is a warning (with the 1.08 fit
preference that is the usual case); `TryOnRenderer.create(..., {fixedScale: true})` renders at the authored size.

Look forward for a few seconds while automatic sizing collects consistent upper-face observations. The Fit panel
shows progress; glasses and their shadows appear only after calibration and size settling finish. Refit hides
them again until the new fit is ready. Later slider adjustments remain visible. The default includes an 8% wider visual fit; the size slider's zero
uses that fit. Size adjustment provides a small personal correction within the frame's supported size limits;
Refit starts a new calibration. Switching glasses while the camera is open reuses the fit; closing and reopening starts fresh.
The normal page includes fitting, shadows and occlusion; no preview URL or comparison mode is required.

## Frame pipeline

1. **Capture** (`src/pipeline/`): acquire a frame of at most 1280 pixels on its longest edge. Laptops default
   to VideoFrame capture; phones and tablets use canvas capture. Frame identity and a SHA-256 bind inference
   results to the captured pixels. The frame pump bounds ownership and overlaps inference with preparation.
2. **Inference** (`src/face/`, `src/hair/`): workers run the pinned MediaPipe face landmarker and hair segmenter
   on reduced inputs. The face detector tries CPU first. Hair uses a 640-pixel input; Apple phones and tablets
   default to the CPU hair delegate, while other devices use the capability probe.
3. **Attachment** (`src/render/`): reconstruct the observed face, retain a separate cheek-depth input, apply
   nasal shaping, smooth orientation/depth, and pin the bridge to the current nose landmarks. The default
   movement gains are 0.3 for rotation and 0.8 for depth, with a 3 Hz velocity filter and 1 Hz resting cutoffs.
   Image-plane attachment is not given another smoothing delay.
   **Sizing** (`src/render/eyewear-fit.ts`) compares upper-face proportions with the frame front in a shared virtual
   reference view, removing initial camera distance and head rotation from the observations. Each model has a
   preferred front-to-face proportion. A robust estimate requires both
   sufficient samples and stable observation time, eases into a bounded uniform scale around the bridge, then
   stays locked through head movement and tracking loss until Refit or a new camera session. Calibration stores
   only rendering proportions in memory; it does not measure physical face dimensions.
4. **Temple visibility**: project the fixed arm geometry, update each hair endpoint once per frame, and
   combine head depth, observed cheek depth and the current hair mask. Audit renders reuse that same state.
5. **Face shadows** (`src/render/eyewear-shadow.ts`): project soft frame shadows and colored lens transmission
   onto the observed face. Lens tint and gradients come from the loaded material, including its texture UV set.
   A camera-relative upper light approximates soft studio lighting; it does not estimate the room's lighting.
   The shadowed camera underlay stays on the GPU and is shared by the lenses, temple fades and hair composition.
   The Shadows controls change frame strength, lens tint and softness without restarting the camera.
   Defaults are 16% frame strength, 18% lens tint and 1.9× softness.
6. **GPU composition**: render directly to the visible canvas, with stencil protection for the optical and
   nasal regions. Hair blending stays inside the editable arm corridors. Explicitly authored front parts
   receive no hair-mask shader, while actual face depth remains active. Untagged models retain the legacy behavior.
   Camera image pixels stay on the GPU
   during live use; an explicit audit can separately read the rendered image.
7. **Profiling**: collect numeric timings and frame age. Images, detections, masks and hashes are excluded
   from timing reports.

On laptops every frame waits for its own hair mask. Phones and tablets normally segment every second frame
and reuse the latest sufficiently recent mask, transformed with head movement. Reuse supplies no new
permission to reveal a hidden temple. A stalled or unavailable mask does not release a held endpoint.

## Fixed temples and occlusion

The shipped models use an authored 18 mm outward splay. An inward return begins at model-local z = -75 mm
and buries the terminal band inside the canonical head volume. Its containment is recalculated when the uniform
eyewear size changes; the front and lens proportions are preserved. After fitting, shape does not bend with
head angle or camera distance, and the system does not try to fit the tips around an ear. Drawn geometry,
optical protection, hair paths and shadow coverage all use the same fitted asset scale.

A closed posterior head shell joins the canonical face perimeter to the head proxy. Canonical lateral depth,
attached to the glasses pose, prevents asymmetric landmark shape changes from cutting one arm earlier
while nodding. The central face and nose retain observed shape. A bounded bilateral head-width calibration
adjusts the anterior occluder independently of eyewear sizing or the terminal volume.

The observed cheek has its own depth pass, independent of the stable main head surface. Posterior arm pixels
in front of that cheek remain visible; pixels behind it blend toward the paired camera image. A guarded
optical front, native fragment depth and exclusion from the physical lenses' transmission input prevent
inner temples from appearing across the eyes. The cheek transition begins 1 mm behind the surface and
widens toward two render pixels, bounded to full concealment by 6 mm. A 45 ms head-local shape filter limits
cheek depth correction to 0.75 mm and preserves the current pose and image rays.

Hair ending follows the **first resolved crossing from hinge to tip**, separately for each arm. Once that
crossing hides the shaft, every section behind it remains hidden, including gaps between later hair patches.
The fade fits inside the supported hair interval. A newly earlier crossing hides the rear immediately;
lengthening requires distinct fresh clear observations and proceeds at a bounded rate. Warped reuse can
hide more but cannot accumulate release confidence. Missing or uncertain masks hold the previous endpoint.
Brief fringe changes in a reused mask preserve recent fresh clearance evidence without extending the arm;
missing, invalid or expired evidence restarts the dwell. Light edge contacts with a clear shaft centre do not
pin an old cutoff, even when no later hair patch is detected. Centre or thick obstructions still block release.
A small two-pixel tent filter softens the rendered hair edge without changing category evidence.

Shadows use the separately observed face, before nasal/head-occluder reshaping. They disappear with tracking
loss and in the clean-camera audit view. Hair masks protect foreground hair from skin shadows when available.
Shadow lookup coordinates use a filtered local face shape attached to the same pose as the glasses, while
the receiver still rasterizes the current observed face. This stabilizes depth without delaying head motion
or filtering camera pixels. A fixed light frustum, texel-aligned sampling grid and continuous depth/edge
filtering reduce shimmer as the head turns. Tracking loss clears the local-shape history.
The light's color filtering is an appearance approximation, not a measured optical simulation or prescription
lens model. Clear lenses cast a much weaker neutral shadow than tinted lenses.

The model dimensions and learned face depth are rendering estimates, not anatomical measurements.
Regression tests protect these rules; live appearance still depends on tracking and segmentation quality.

## Lens and crystal materials

arview draws every lens through its own glTF material (native lenses). The retired canonical lens descriptor
(`LENSES_lens_appearance`, written only by the retired bsa/reconstruction routes) has no runtime here: a material that
still carries it is stripped at load, reported as a warning, and drawn as its native glTF material. Translucent (crystal)
frame materials reflect an unblurred see-through room: three's `RoomEnvironment` without its +Z panel, which sits behind
the selfie camera and clipped to a flat white rectangle. Native lenses keep the scene room (with `?lensenv`, they own that
room at 0.8 × lensenv). A total mirror remains a lens in geometry, fitting, visibility and hair consumers.

Lens identification (WP1_DESIGN.md §2, `src/eyewear/lens-identification.ts`, the same order and shared test vectors
as `glasses3d/g3d_host/lens_id.py`) decides every material's class once at load, from the glTF JSON (never three's
merged userData), per use (node x primitive):
1. extras on the material, then mesh, then node: `partRole` equal to `lens`, or legacy `part_role` starting with
   `lens`; any other explicit role (frame, temple, hardware, temple_left, ...) is not a lens;
2. a whole-word name token `lens` / `lenses` / `glass` (never `glasses`) in the material or node name, unless either
   name marks an etch, lettering, inscription or print;
3. only when nothing in the file is a lens by rules 1-2: transmission > 0 or alpha-blended alpha < 1, as a warned
   last resort.
The class is never re-derived from live transmission, so a transmissive frame or temple (crystal) stays frame. A
material used by lens and frame primitives is cloned per class (both render) and fails the §2 Components row; a
model with no identified lens is refused. Every material, its class, deciding signal and uses are in the renderer's
`lensIdentification()` block; every fallback is a model-report warning. Native lenses retain their authored geometry
and physical material.

Compact authored front-frame parts keep their inner rim and hinge hardware in the lens input. The temple-only
visibility rules do not apply to those parts, even when the frame and arms share a glTF material. Eligibility requires
an explicit `frame` role (`partRole` or legacy `part_role`) and geometry wholly inside the protected front region
(local Z at least -30 mm), with baked transforms. Untagged parts and long frame-tagged bodies retain the geometric arm
rules. Ordinary face depth
still applies; this exemption does not draw a frame through the wearer's head.

New exports can provide explicit node extras `LENSES_part_visibility: {version: 1, region: ...}` with
`front`, `temple_left` (native negative X), `temple_right` (positive X), or `lens`. This opt-in contract must cover
every mesh primitive, agree with the identified class (region `lens` on lens parts only), and contain static
geometry with baked authored transforms. Only an immediate glTF primitive Group may supply a part's profile;
model-wide metadata does not substitute for ownership. The regions are optional metadata: malformed, partial, or
conflicting profiles are ignored for the whole asset, recorded as a `part visibility` fallback listing every problem (unbaked transforms
and non-static geometry are still refused, once, by the load-time §2 check in `src/model/normalize.ts`).
Unprofiled assets keep the legacy behavior above; old whole-body `frame` tags are never automatically upgraded.
Sides are judged by geometry: a `temple_left` region must lie at x < 0 and `temple_right` at x > 0 (every consumer maps
`temple_left` to the negative-X side), otherwise the profiles are ignored for the whole asset and position decides.

For profiled assets, front pieces and their hardware remain fixed and survive all arm-specific clipping,
deformation, lens-input filtering, and shadow trimming regardless of wrap depth. Explicit arms supply continuity
and fitting geometry independently of color or transmission. Shared materials and geometry are separated by
ownership policy without changing their PBR values or source buffers. Normal face depth still occludes the front.
A binary hair category cannot tell whether hair is in front of the frame or behind it, so it never erases explicit
front parts or their hardware. True bangs over the front would require additional depth/order evidence that this
pipeline does not have. This preserves temple hair behavior without claiming perfect front-hair ordering and
does not relax the canonical lens profile's existing alpha/material restrictions. Alpha-blended native arms
retain ordinary depth behavior; they are not converted into opaque depth-relief overlays.

Crystal frame materials with opaque alpha mode use a separate, sharp look-through capture of the camera and opaque
hardware, including the near-arm visibility overlays. Lenses and crystal sampling materials are excluded from that
capture. On the canvas, the crystal samples it; inside either the explicit canonical or Three's native lens input,
the crystal instead samples the paired background. The two sources prevent the lens arm-exclusion rule from erasing
an internal temple core, while keeping far-arm hardware out of the lenses. Hair occlusion, arm clipping, continuity,
rear drop and face depth remain active. This straight look-through approximates crystal transmission without refraction
offsets or roughness blur. The crystal twin loses the surface's reflected share and limits its reflection, clearcoat
included, with the crystal's knee (half the headroom). Alpha-blended temples are not covered by this look-through path;
they are still temples (shaped, clipped, followed by continuity, hair-occluded), but their near-arm overlay stays hidden
because a blended overlay is not built.
Volume absorption distances
(`KHR_materials_volume`, metres in the file) are converted once to the scene's centimetres (`src/render/eyewear-volume.ts`),
since Three scales the thickness through the model matrix but reads the distance in world units; the shadow keeps the
authored ratio. No shipped asset carries a volume extension.

The local harness `node qa/canonical-lens-runtime.mjs` exercises the actual renderer with controlled fixtures,
synthetic poses, gradients, mirrors, opaque content, light-angle transmission and structured environments.
See [fixture generation, commands, evidence and limitations](../automation/plan/LENS_RUNTIME.md).
This integration is source-only until an explicit publish operation.

The separate `effective_optical_group_v1_experiment` profile preserves explicitly grouped closed/multipart
geometry. A strict importer checks group bindings, shared intrinsic height, nonvanishing interpolated normals
and exact coplanar agreement. Camera and light passes first retain one nearest event per group, then compose
distinct groups. Equal descriptors never merge distinct groups. Eight groups and four interactions per ray
are the explicit bounds. This is a symmetric effective response, not volume refraction or separate back coatings.
All five saved candidate GLBs load through this local path, but its strict GPU depth qualification remains
incomplete and photographic materials remain unmeasured. See [the experimental runtime evidence and limits](../automation/plan/EFFECTIVE_GROUP_RUNTIME.md).

## Audit and diagnostics

**Hold & audit** keeps one frame, renders it with/without hair and without eyewear, and checks that hair edits
preserve the optical region, nose, background and pixels outside the editable corridors. All four equality checks
remain strict: a geometric front/hair-mask overlap does not authorize a protected pixel change. It records endpoint,
fixed-return and head-fit metadata, plus lossless images and difference maps. The CPU binary-mask baseline
is a diagnostic reference; it does not reproduce GPU edge feathering. Reused masks skip that reference
comparison because it requires a frame's own mask. Audit images stay local unless the user downloads them.
An additional shadows-off view and difference map record the effect on that exact same held frame; hair-on
and hair-off views share the same shadow underlay, retaining all four existing protection checks.

`?diag=1` enables numeric startup and timing reports to the same site's `/ar/diagnostic` endpoint, readable at
`/ar/diagnostics.json`. The page identifies when this is enabled. Reports contain no images, face landmarks,
masks or hashes; server memory holds only a bounded recent history and clears on deployment.
Unknown address options are shown as ignored. Old temple comparison and sweep options no longer select
alternate pipelines.

Useful diagnostic options are defined in `src/config.ts`:

| Option | Default / purpose |
|---|---|
| `capture`, `source` | 1280-pixel maximum; VideoFrame on laptops, canvas on mobile |
| `face` | CPU first; `gpu` reverses the fallback order |
| `hairinput`, `hairdelegate` | 640-pixel input; auto delegate except CPU on Apple mobile |
| `hairframes`, `hairmove`, `hairmaxage` | Device-specific mask scheduling, motion-triggered refresh and reuse age |
| `hairwait` | 120 ms worker-stall limit where a frame waits for its own mask |
| `eyewear`, `hairModel`, `hair` | Initial glasses, hair model and local hair-occlusion toggle |
| `steady`, `steadyhz`, `steadybeta`, `steadydepthhz`, `steadydepthbeta` | Pose filter diagnostics |
| `guard`, `sync`, `hairz`, `exposure` | Render/camera diagnostics; normal defaults retain protection and GPU pacing |
| `diag` | Same-origin numeric telemetry, off by default |
| `model`, `name`, `clip`, `width`, `sha256`, `lensenv` | Validated external-model handover through `src/eyewear/external.ts`; `lensenv` (0.3-4) scales the lens environment reflection |

`node qa/measure.mjs --sessions=1 --warm=10 --measure=30` runs a synthetic-camera measurement using the checked-in
fixture. It verifies lifecycle and rendering under controlled input; it does not measure real wearer motion,
phone performance or sustained thermal behavior. `qa/pipeline-smoke.mjs` verifies the normal page on desktop/mobile
layouts, both shipped models, restart behavior and audit protections without opening a real camera.
For deterministic shadow/color comparisons against a development server, run
`node qa/shadow-smoke.mjs --base=http://127.0.0.1:8240`. It saves same-frame on/off images and verifies both
models, controls, tracking loss and clean-camera output using the synthetic camera fixture.
`node qa/shadow-stability.mjs --base=http://127.0.0.1:8240` measures frame-to-frame shadow variation under
repeatable pose and landmark-depth noise with fixed camera pixels. It saves images and attenuation metrics
for both models; these controlled measurements do not replace a live wearer check.
`node qa/fit-smoke.mjs --base=http://127.0.0.1:8240` checks opening calibration, locked sizing, manual adjustment,
model changes, Refit, fresh camera sessions and held-frame protection with the synthetic camera fixture.

## Publish

The live route serves the committed `site/` output, not `src/` or `dist/`:

```bash
npm run publish
```

This builds with base `/ar/` and writes `site/public-manifest.json` containing every public file's size and
SHA-256. `UI/ar_site.py` serves only manifest-listed files, checks their hashes and applies isolation and
Content Security Policy headers. Published bytes have line-ending conversion disabled by `site/.gitattributes`.
AR changes must not replace the root Python application or its deployment configuration.

After deployment, verify the served bytes and headers against the local manifest:

```bash
python qa/verify-published.py --url https://web-production-ef3ca.up.railway.app --output qa/output/published-receipt.json
```

Keep package manifests, assets and regression tests within `ar/`. The pinned models and MediaPipe runtime are
prepared by `scripts/prepare-assets.mjs`. Keep private recordings, local modeling work, `.env` files and experiment
archives outside Git. `node_modules/`, `dist/` and `qa/output/` are ignored. Temporary comparison pages and session
experiments are not part of the production pipeline. [qa/README.md](qa/README.md) indexes the QA tools: page gates,
harnesses used by automation, and optical-group evidence reproducers.
