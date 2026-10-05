# WP1 design: arview decisions (binding for every WP1 stage)

Written 2026-10-05 from the verified WP1 analysis (`port_review/arview.json`, PORT_REVIEW §7). Line-level evidence for
every point is in that JSON (`analysis.throws`, `analysis.throwCheck`, `analysis.lensid`, `analysis.capture`).
arview lives in `glasses3d/arview/`, never touches `ar/` (it reads `../../ar/public` and
`../../ar/qa/fixtures/face-a.jpg` read-only), and serves on 8250/8251.

## 1. Acceptance policy

arview shows Astra and the owner what a model looks like in AR, and says plainly what violates the owner's spec
(PLAN §2). Nothing outside §2 ever blocks a render.

| §2 row | arview behaviour |
|---|---|
| Format | **Refuse** an unreadable GLB, a missing external resource, or a file with no scene. A not-self-contained file loads (relative URIs resolve against the model URL) with a warning. Draco, meshopt and KTX2 are decoded. |
| Units | Detect from the X span after transforms: 60-250 → millimetres (×0.001), 6-25 → centimetres (×0.01). **Rescale and load with a loud warning**, and mark the units row failed. The rescale also scales KHR_materials_volume `thicknessFactor` and `attenuationDistance`, and runs before classification, rear drop and continuity. |
| Origin | Measured: slice the triangles (frame AND lens: a shield's nose notch is lens geometry) with the planes x ∈ {−1, −0.5, 0, 0.5, 1} mm; lowest point in the front 25 mm = bridge underside, as `g3d_host/spec.py` does. Its x is judged from the lens-class bounding box (whole lenses do not depend on tessellation; temples, splay and one-sided ornaments cannot move it); only when no lens geometry spans x = 0, from the front region with its surface clipped at z = front − 25 mm, with a warning. Off-centre whole-model or front-region boxes are warnings (validate_spec `_origin`, measurement `origin_x_judged_on`). Off by more than 1 mm, or no bridge found at all → **render anyway**, loud warning, origin row failed ("origin unverifiable" when nothing crosses x = 0). (The ar_v4 crystal r9 sits 10.23 mm high and must still load: WP1 proof.) |
| Orientation | Hard checks, calibrated on 8 real models (2026-10-05): Y is the thinnest axis; per side, non-lens geometry at \|x\| > 45 mm reaches z ≤ −60 mm (else "temple <side> missing"; hinge hardware alone never counts); **+Y up**: the temple roots (non-lens, \|x\| > 45 mm, −40 < z < −15 mm) sit above the lens bbox centre (real models: +4.0 to +18.0 mm; an upside-down or Y-mirrored model is negative); **roll**: left and right lens halves (split at x = 0) have bbox-centre heights within 3 mm (real: ≤ 0.16 mm); **pitch**: the temples' straight segment (−90 < z < −30 mm) slopes less than 15° (real: −9.4° to +4.7°); lenses sit at the front. Failures → render the rest (a missing temple gets a fixed cap), orientation row failed with the reason. Never hidden by a fallback. |
| Transforms | **Refuse** a mesh whose world transform differs from identity by more than 1e-6 absolute (one check at load; remove the 1e-12/1e-10 duplicates in rear-drop, continuity, part-visibility), and any node with EXT_mesh_gpu_instancing. |
| Geometry | **Refuse** non-finite positions, out-of-range indices, skinned/morph/instanced meshes, or a file with no triangles. TRIANGLE_STRIP/FAN are triangulated geometry (accepted). LINES/POINTS primitives are not rendered and are reported as a §2 geometry failure (never silently dropped). |
| Components | Lens identification per §2 below. **Refuse** when no lens is identified (the lens-dependent pipeline cannot run). A material used by both lens and frame primitives is cloned per class so it renders, and the components row is failed. |

Everything else that used to throw on asset content becomes a warning plus a fallback, each in its own try block,
recorded in the report (never silent): shadows off, hair occlusion off, continuity endpoint off for a side, retired
`LENSES_lens_appearance` descriptor stripped, malformed `LENSES_part_visibility` ignored asset-wide, out-of-range
handover parameters ignored. Internal invariants (pose math, disposed objects, WebGL2 missing) keep their throws.

Limits: none. Warnings above 300k triangles or 4096 px textures.

The capture CLI's report also embeds the Python `validate_spec` result (`glasses3d/.venv`, module
`glasses3d.g3d_host.spec`), the single source of truth for the §2 rows; arview's own runtime checks must agree with it
on the shared fixtures. If Python is unavailable the report says so loudly.

## 2. Lens identification (same order as `g3d_host/lens_id.py`)

Per use (node × primitive), from `gltf.parser.json` (not three's merged userData):
1. extras on the material, then mesh, then node: `partRole` equal to "lens" (trimmed, case-insensitive) or legacy
   `part_role` starting with "lens" (case-insensitive). Any other explicit role (frame, temple, hardware, temple_left, ...) means not a lens and stops rules 2-3.
   A disagreeing lower-priority role is a warning.
2. whole-word token `lens` / `lenses` / `glass` (never `glasses`; camelCase, ACRONYMCase and digits split) in the material or node
   name, unless either name has a token starting with `etch`, `letter`, `inscri` or `print`.
3. only if rules 1-2 found no lens anywhere in the file: transmission > 0, or alphaMode BLEND with alpha < 1, as a
   warned last resort.

The class of a material is decided once at load and never re-derived from live transmission. Tests read the shared
vectors `glasses3d/tests/fixtures/lens_id_cases.json`. Every consumer (renderer lens meshes, rear drop, continuity,
temple visibility, temple clip, hair occlusion, terminal fit, shadows, translucent twin/look-through) reads the
decided class. The report lists every material: class, deciding rule/signal/site, and its uses.

## 3. Temples, clip and continuity

- Temples are found by position per side, whatever their opacity: non-lens geometry at \|x\| > 45 mm behind the lens
  rear. Lens rear = min z of lens-class geometry; lens geometry behind −60 mm is a warning (lens material reaching the
  temple region).
- The temple clip is derived per side (port the stash QA rule `qa/temple-cutoff.mjs:38-44`: from the shorter arm's
  tip, never a single global minimum) and clamped to [−0.2, −0.045] m; a handover clip outside that range is ignored
  with a warning.
- Continuity: no pin registry. It uses the bytes and the normalisation the renderer already applied (no second,
  un-normalised parse). The report records the sha256 of the bytes actually rendered; the WP2 ledger binds to that,
  never to a `&sha256` handover value.
- A side with no posterior cross-section gets the fixed cap and a warning for that side only.

## 4. Fit scale

The live fit auto-scales the frame to the face (×0.85-1.15). Captures keep the live behaviour (the owner judges that
way) and report the applied scale; |scale − 1| > 3 % is a warning. `--fixed-scale` renders at authored size.

## 5. Renderer API (replaces private-field access)

`create(canvas, signal, model: ModelDefinition, options)` with `ModelDefinition = {url, displayName, templeClipZM?,
lensEnvIntensity?, offsetCm?}` (no catalog). Public read-only accessors: `report()`, `cameraState()`,
`assetMatrixWorld()`, `parts()` (mesh, class, side), `projectAssetPoints(Float32Array)`, `setLighting(config|null)`,
and `render(mask, {override?: 'part-id'|'lens-mask'|'lens-hidden'|'lens-only'|'normals'})` applied consistently to
originals, overlays and twins. Decoders are registered on every GLTFLoader instance; the WebGLRenderer exists before
parsing (KTX2 needs it).

## 6. Capture CLI and webcam page

`node qa/capture.mjs <glb> --poses default|junction|all --out <dir>` (bin `arview capture`). Default poses: front,
yaw ±30, yaw ±45, pitch ±20 on skin, beauty + part-id, plus one worn front portrait on face-a.jpg (read-only from
`../../ar/qa/fixtures`, sha256 b6491464…; no other face image ever). Junction: yaw ±30 × pitch ±20, full frames plus
crops around each lens-brow contact computed from triangle slices. One page.evaluate per step with a per-step timeout;
on a stall, kill Chromium, write a partial report and exit non-zero within about 2 minutes. PNGs stream to disk as
they are produced. Vite cacheDir stays inside arview. GPU check: refuse to report results from a software renderer.
Report: status, spec (Python), runtime (warnings, fallbacks, lens identification, temples, units, clip, fit scale,
rendered sha256), live_site_would_fail.

Webcam page: `?model=<loopback URL>` handover (as `ar/src/eyewear/external.ts`), frozen build served on loopback with
COOP/COEP; the page shows the report's errors and warnings.
