# Lens response conformance

This experiment tests whether one specified effective lens material survives compilation, transport and rendering. It does **not** test reconstruction from product photographs. The isolated experiment described here is now supplemented by [actual production-renderer conformance](LENS_RUNTIME.md).

## Implemented paths

- `reconstruction/lens_appearance.py` defines the versioned, scene-linear reference response. Optical-density gradients belong to lens-local surface coordinates. Reflection, transmission and absorption are separate quantities.
- `reconstruction/blender_lens_material.py` compiles that description to real Blender shader nodes: dynamic UV/angle math and additive transparent/glossy closures. The lens shader uses no precomputed image or CPU-colored emission lookup.
- `reconstruction/lens_asset.py` writes GLB test surfaces containing the complete descriptor, semantic lens role, local coordinates and hashes. `LENSES_lens_appearance` is an unregistered project extension. Generic viewers receive an explicitly approximate fallback; loading it does not establish appearance fidelity.
- `ar/src/eyewear/lens-appearance.ts` validates the same descriptor, reads the extension through Three.js, provides a CPU evaluator and supplies GLSL/uniforms. Pure mirrors remain semantically lenses even when transmission is zero.
- `ar/qa/lens-appearance.html` loads the actual GLBs and exercises that GLSL on the GPU. This is an isolated consumer in the AR codebase, **not** `TryOnRenderer` or the live face-shadow path.

The ten cases are clear, neutral tint, saturated tint, vertical gradient, multistop gradient, strong neutral mirror, strong colored mirror, total mirror, angular coating and mirrored gradient. All have zero roughness for this experiment. No product IDs or photo-specific correction constants are involved.

## Measurements and falsification

The shared reference grid has nine lens-local heights and seven incidence angles through 85 degrees. Six uniform lighting/background combinations include isolated transmission and isolated reflection. Both terms are measured independently so an incorrect tint cannot compensate for weakened reflection.

The native probe performs 1,470 Cycles renders: ten cases, 49 interior height/angle pairs and three lighting/background combinations. It compares scene-linear EXR measurements against the reference samples. Endpoints are covered by CPU/GPU tests; native samples use interior heights to avoid rendering the test plane's physical boundary. Native measurements average a small central patch, so there is a small integration difference from the exact center reference.

The browser probe checks:

- Descriptor and GLB byte integrity, exact fixture coverage and all 63 samples per material.
- Python versus TypeScript values, GPU reflection/transmission/absorption, and composed radiance.
- Exported surface coordinates, normals, dimensions and transforms against an independently coded analytic test surface. Comparing two consumers of the same broken attributes would be insufficient.
- The curved exported surface under three yaw/roll poses, with independently traced pixel locations and incidence angles. Missing rendered coverage is an error.
- Five negative controls: erased exported normals, reversed exported UVs, reversed vertical gradient, reversed mirrored gradient and reflection reduced to 30%.

Measured on this workstation, 2026-09-21: maximum native absolute RGB error **0.00022378**, maximum GPU coefficient error **0.000000228**, and maximum curved-surface RGB error **0.0000522**. All five negative controls were detected. Numeric tolerances were fixed before these runs, not adjusted to obtain a pass. Reports record fixture/source hashes. These are controlled numerical errors in linear light, not perceptual reconstruction quality percentages.

## Reproduce

From `automation/`, with its Python dependencies installed:

```powershell
python -m reconstruction.lens_asset --output data/lens-conformance
python -m unittest discover -s tests -q
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --background --factory-startup --disable-autoexec --python scripts/blender_lens_probe.py -- --cases data/lens-conformance/cases.json --output data/lens-conformance/blender
```

From `ar/`, with its existing Node dependencies and Playwright Chromium installed:

```powershell
npm run check
npm test
node qa/lens-conformance.mjs --fixtures=../automation/data/lens-conformance
```

The browser runner creates and closes its own local test server and headless browser. It neither opens a camera nor publishes the site. Native reports, EXRs and fixtures are ignored under `automation/data/lens-conformance/`; browser reports and the labeled contact sheet are ignored under `ar/qa/output/lens-conformance/`.

## What remains before this solves product lens appearance

1. Infer material, surface coordinates, normals and lighting from real photographs, preserving uncertainty when those quantities cannot be distinguished. A known descriptor render test cannot establish this ability.
2. Extend the implemented `TryOnRenderer` visible/shadow adapters beyond the authored front-sheet profile. Actual runtime environment/background sampling and face-transmission coefficients now have controlled GPU evidence; generic product assets do not yet carry validated optical coordinates and descriptors. These source changes have not been published.
3. Validate supported geometry beyond the controlled curved-sheet/layer/occlusion fixtures now exercised in the actual renderer. The isolated proof here uses an effective single surface; runtime depth ordering does not add ray displacement, multiple internal reflections, closed-volume transport or front/back coating asymmetry.
4. Measure color under the actual camera/video and display transforms. The current probe deliberately uses scene-linear measurements and a known sRGB display encoding.
5. Evaluate held-out real products and repeatability. Ten synthetic optical families establish contract coverage, not successful automation across ten glasses designs.
