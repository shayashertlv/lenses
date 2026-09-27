# Canonical lenses in the production AR renderer

2026-09-21. Implemented and tested locally; not published. This milestone verifies rendering of a specified material. It does not infer that material from product photos or establish complete reconstruction fidelity.

## Implemented behavior

`ar/src/render/lens-material.ts` installs a fresh physical material for validated canonical optics. `lens-layers.ts` owns the opaque input and far-to-near depth peeling; it disables Three's hidden transmission prepass. Each effective sheet applies **`T * behind + R * environment`** in linear RGB, stopping at the nearest opaque surface. Legacy tint, alpha, absorption and Fresnel are not applied again. Intrinsic UV height controls the gradient, transformed normals control angle, and descriptor roughness controls PMREM reflection sampling. Canonical materials receive an environment without the legacy `sigma=0.04` preblur; environment intensity remains illumination, independent of coating strength.

The opaque capture preserves the native ACES/exposure response for ordinary frame materials while leaving camera/background colors unchanged. `opaque-display.ts` scopes this transform to the capture, preserves existing shader hooks, and avoids double application in the normal screen draw. Canonical optical composition uses that display-linear input and receives one final sRGB encoding. This is an effective AR appearance model, not a global scene-radiance pipeline with tone mapping after all light transport.

The production shadow caster evaluates the same transmission at the orthographic light's incidence angle. Front-to-back light-space peels retain transmission and exact fragment depth, stop at opaque geometry, and apply only layers before the receiving surface. It retains lens identity when transmission is zero. Receiver/contact falloff, filtering, hair protection and user-adjustable tint strength remain appearance approximations, not a physically complete simulation of face illumination.

The shared `isOpticalMaterial` helper supplies semantic identity to renderer, shadow, continuity, rear-drop, terminal fit, temple clipping/visibility and hair consumers. Untagged physical materials retain their legacy transmission-based classification. Malformed canonical descriptors fail validation instead of silently selecting the fallback.

The renderer hashes fetched GLB bytes before parsing, verifies registered pins, and passes the same bytes into continuity analysis. `renderedAssetIntegrity` exposes the hash and whether it was pinned. Unpinned legacy assets can still load, with that distinction explicit. Installation validates every canonical mesh before replacing any material. Disposal owns replacement materials, retained fallback materials/textures and the separate environment target.

## Explicit supported profile

`front_sheet_v1` requires separate static, single-material meshes with node `partRole: lens`, a declared surface profile, baked transforms, finite positions/normals/UVs, +Z-facing triangle winding and normals, and per-lens bottom-to-top `TEXCOORD_0` height spanning zero to one. Skinning, instancing, batching and morph targets are rejected. UV endpoints are validated; the shader clamps only interpolation roundoff. Positive normal magnitudes are normalized before interpolation consistently with Three's visible material.

`optical-topology.ts` additionally checks triangle intersections with a spatial hierarchy and local numerical tolerances. Every mesh must be a single-valued authored `z(x,y)` sheet. Shared edges, holes and concave regions are permitted; overlapping triangles within one mesh and coincident patches across meshes are rejected, including different tessellations. Separate sheets may overlap or cross, and per-pixel ordering handles pose-dependent changes. These checks do not establish manifoldness, semantic correctness or arbitrary global topology.

Both camera and light support **four optical interfaces per ray**. An additional peel and reduction to one generated flag detect overflow before using a truncated composition. No camera image pixels are read during live use. Nested optical meshes and opaque children are supported; temporarily hiding optical materials preserves child traversal. Float color targets are required. These extra buffers, passes and synchronous checks need mobile memory/performance validation; this is not yet a broad deployment claim.

Closed volumes, back coatings, refraction displacement, internal multiple reflections and general wraparound geometry outside the authored graph profile remain unsupported. Mixed legacy/canonical optical interfaces and translucent frame materials are rejected because they require additional ordered transport. Near-coincident geometry remains limited by raster/depth precision. No fixture or surface check constitutes product reconstruction acceptance.

Layer targets are single-sample and use nearest sampling. The numeric probes deliberately measure supported interior pixels; interior overlap-edge antialiasing and temporal stability still need dedicated measurements. The normal canvas antialiasing does not by itself establish those properties.

## Actual renderer evidence

The fixture generator keeps the shipped Amber frame, temples and materials, replaces its optics with two independently specified planar sheets, and pins source, generator, descriptor and output hashes. These intentionally rectangular optical fixtures are measurement instruments, not improved models of that product. The browser harness uses the real `TryOnRenderer`, synthetic face landmarks and production loading/fitting/optical-protection/shadow paths. It does not request a real camera.

The original thirteen cases cover the ten shared optical families, rough mirrors at 0.25 and 0.75, and unequal-magnitude authored +Z normals. Thirteen additional layer fixtures cover two/three/four interfaces, reversed order, crossing sheets whose order changes across pixels and poses, opaque content before/between/behind sheets, front/rear total mirrors, independent left/right descriptors, curved normals and nested lens/opaque children. A separate clear identity fixture compares three ordinary opaque colors with and without a fully transmitting lens under native ACES. Disabling tone mapping is a positive control, so identical clear-lens pixels cannot pass merely because tone mapping was never active.

Independent recipe geometry supplies positions, normals, UVs and analytic ray intersections; exported float32 attributes are checked against it. Original frame geometry and actual face/head depth-only materials are hidden in advance for isolated layer measurements; authored opaque controls remain. A retained diagnostic separately proves real head-depth stopping: the synthesized nose lay between two wide test sheets and correctly removed the rear one. Numeric coverage margins are geometric and fixed before comparing colors.

The raw shadow oracle requires an identified ANGLE Direct3D 11 backend. Its CPU raster reference uses float32 asset/uniform data and eight-bit nearest-even vertex XY snapping before interpolation, following the [Direct3D rasterization specification](https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#CoordinateSnapping). The API's reported subpixel value is retained separately. Continuous-ray residuals and the unsuccessful four-bit assumption remain diagnostics; they are not silently erased. This establishes local backend conformance, not identical floating-point results on all drivers. The spec permits conversion tolerance, and other backends require their own validated reference.

Receiver comparisons independently trace twenty shadow-map texel rays for the five bilinear comparisons, keeping the receiving pixel's depth fixed. They cover before/between/behind optical layers and opaque stops. The earlier center-ray approximation did not represent that filter and is retained in the unsuccessful report. No measured GPU color/depth map supplies expected receiver pixels, and the RGB tolerance remains 0.008.

Final run: `ar/qa/output/canonical-lens-runtime-layered-final/report.json`, status **passed**, all source hashes stable. Report SHA-256: `4f87a485292827746740a002bd56e18ea6c515d537aed060c82c1120d3f03245`.

| Check | Measured result |
| --- | --- |
| Original visible composition | 13 cases, 143 probes, 4,290 samples; maximum absolute linear RGB error **0.00438853** against fixed **0.008** tolerance |
| Layered visible composition | 13 cases, 130 probes, 12,000 samples; maximum error **0.00424105** against the same **0.008** tolerance |
| Known opaque-background transport | 13 probes, 390 samples; maximum error **0.00195822** |
| Original raw shadow-caster transmission | 234 samples; maximum continuous-ray error **0.00005451** |
| All four raw shadow peels | 3,719 rays × 4 layers; maximum raster-reference transmission error **0.00003406** and normalized depth error **0.00000003794**, below unchanged **0.00005 / 0.000002** thresholds |
| Filtered face-shadow receiver | 108 samples before/between/behind sheets and opaque stops; maximum linear RGB error **0.00343022** |
| Ordinary frame color through a clear identity lens | 90 pixels across three colors: **zero** difference from native ACES baseline; disabling tone mapping changes the same colors by up to 0.07148 / 0.13481 / 0.13728 |
| Observer incidence coverage | About **3.798°–79.919°**, with yaw and roll poses |
| Structured environment | Direction changes with yaw; higher roughness reduces directional contrast and broadens the highlight; all four relative gates passed |
| Invalid actual GLBs | Missing UVs, undeclared profile, flipped normals, GPU instancing, coincident sheets and retessellated same-mesh duplicates rejected; fifth-layer overflow rejected independently by camera and shadow passes |
| Integrity | All asset pins match the rendered bytes; exact fixture counts and before/after source-hash stability passed |

Readback uses an 8-bit sRGB canvas, float RGBA canonical caster maps and half-float environment storage; these errors are numerical conformance measurements, not product similarity percentages. Continuous-ray layered shadow residuals remain **0.00006159** transmission / **0.00000281705** normalized depth in the report. The discrete raster reference explains that difference without changing the thresholds. Directional roughness checks are relative properties, not a reference microfacet integration. The run reported no browser/shader errors or failed HTTP responses. Two saved Direct3D PMREM precision warnings concern tiny constant additions; the report retains them.

On the identified Intel Arc 140T / ANGLE D3D11 workstation, 130 layered renders at **480 × 320** took median **6.7 ms**, p95 **87.3 ms**, maximum **224.5 ms** for CPU submission plus synchronous overflow checks. These include possible shader warmup, exclude diagnostic image readback/oracle work, and are not a sustained frame-rate or mobile benchmark. They expose remaining startup/performance work rather than establish a deployment performance target.

The current AR suite passes **377 tests**, and `npm run build` passes TypeScript and asset integrity checks. The build retains Vite's bundle-size warning. The production-page smoke passed both shipped models, desktop/mobile layout, close/restart and all four held-frame protection checks, with no uncaught browser or shader errors. Its retained console contains MediaPipe warnings and CPU delegate information. Local GPU and generated fixture evidence live under ignored `ar/qa/output/`; the production-page report is under `canonical-layer-legacy-smoke`. Owned preview and harness servers were closed after validation.

## Reproduce

From `automation/`, after generating the shared optical cases if needed:

```powershell
python -m reconstruction.lens_asset --output data/lens-conformance
python scripts/runtime_lens_fixtures.py
```

From `ar/`:

```powershell
npm test
npm run build
node qa/canonical-lens-runtime.mjs --output=qa/output/canonical-lens-runtime-layered-final
```

The runtime harness starts and closes its own ephemeral Vite server and headless Chromium with the D3D11 backend. Its fixed expected case/probe counts prevent a reduced run from appearing complete. Artifacts in the chosen output directory are `report.json` and `contact-sheet.png`; choose a new directory to preserve earlier evidence. Regenerate fixtures after changing their generator or descriptor cases.

For the ordinary production-page regression, serve `dist/` with `npm run preview` and use its URL with `node qa/pipeline-smoke.mjs --base=http://127.0.0.1:8241 --out=qa/output/canonical-layer-legacy-smoke`. This uses a checked-in synthetic camera image and tests the two shipped models, desktop/mobile layout, restart and held-frame protections. It does not evaluate real wearer motion or final optical realism.

## Remaining reconstruction requirements

Infer components, optical coordinates, normals, illumination and appearance hypotheses from ordinary product photos; carry uncertainty into AR predictions; prepare generic assets for the supported renderer; measure edge/device behavior and unsupported geometry; and run the frozen complete pipeline on distinct unseen designs. Five archived products and 27 controlled renderer fixtures do not establish that generalization. See [integration](INTEGRATION.md), [photo observations](SEMANTIC_OBSERVATIONS.md), [inverse fitting](LENS_INFERENCE.md) and [current evidence](PROGRESS.md).
