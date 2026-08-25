# Attribution

Everything under `vendor/` and `assets/` is third-party and vendored deliberately,
so the prototype runs offline with no install step. Licences below.

**`vendor/` is no longer in git.** It is 105,315 lines of code nobody here wrote,
and it was 61% of the pull request that introduced this tree. It is now fetched
and SHA-256 verified by `scripts/fetch-vendor.mjs` against the pinned versions
named below — run that once after cloning, or `--check` to verify a tree you
already have. Still vendored rather than an npm dependency, for the reason this
file always gave: the app is a static page with no build step and no bundler,
`three.module.js` is loaded by an import map, and the MediaPipe wasm is fetched
by path at runtime. `assets/` is unchanged and still tracked.

## 3D models

**`assets/glasses/crystal-parts.glb`**
Generated with Tripo (image-to-3D), August 2026, by the Lenses team. Eight named
parts with per-part PBR textures. Arrives 42.7° off axis and at arbitrary scale,
both corrected at load (`orient` and `realWidthMm` in `src/models.js`). Its
materials declare no transparency at all — see the README — so the crystal
treatment is applied at load rather than read from the file. Confirm Tripo's
licence terms for the generated asset before this ships anywhere public.

**`assets/glasses/glasses01-with-lenses.glb`**
Generated with Tripo (image-to-3D), August 2026, by the Lenses team — the same frame
as `crystal-parts.glb` with the lenses modelled as their own meshes. Eight named
parts with per-part PBR textures plus two lens meshes sharing a `LensGlass` material.
Arrives 39° off axis with a 7° roll, at arbitrary scale (0.99 units across), both
corrected at load (`orient` and `realWidthMm` in `src/models.js`). Unlike
`crystal-parts.glb` the *lenses* do declare `KHR_materials_transmission`, so the
loader builds real glass from the file and only the frame parts need the crystal
treatment. Confirm Tripo's licence terms for the generated asset before this ships
anywhere public.

**`assets/glasses/navigator.glb`**
Modelled by the Lenses team in Blender, August 2026, exported with Khronos glTF
Blender I/O v5.2.39. The only authored asset in the catalogue rather than a scan, and
it needs none of the corrections the scans do: it arrives axis-aligned, in metres at
life size (147.5 mm across, 140 mm temples), with named parts — `Frame_Front`,
`Temple_L/R`, `Ferrule_L/R`, `NosePad_L/R`, `Lens_L/R` — and three materials that
declare what they are (`Lens_Gradient` transmissive, `Metal_Silver` metallic,
`Acetate_Black`). Its `KHR_materials_clearcoat` carries a roughness with no
`clearcoatFactor`, which defaults to zero, so the intended lacquer on the acetate did
not survive the export; left as authored.

**`assets/glasses/meshy-glasses.glb`**
Generated with Meshy AI (image-to-3D), August 2026, by the Lenses team. Arbitrary
scale (1.88 units across), normalised to 140mm via `realWidthMm` — **set that to
the real product's width**. The exporter's metallicFactor of 1.0 is corrected at
load (`pbr` in `src/models.js`); the textures are used as authored. Confirm
Meshy's licence terms for the generated asset before this ships anywhere public.

**`assets/glasses/base.obj`**
Supplied by the Lenses team, August 2026. Blender export, no material file.
Provenance and licence not recorded here — confirm before this ships anywhere
public.

Authored at arbitrary scale (1.85 units across) and normalised to 140mm by
`realWidthMm` in `src/models.js`. **Set that to the real measured width of the
actual product** — true-size fitting is only as honest as that number.

**`assets/glasses/sunglasses-khronos.glb`**
Sunglasses, from the Khronos glTF Sample Assets repository.
© 2024 Darmstadt Graphics Group GmbH — model and textures by Eric Chadwick.
Licensed **CC BY 4.0 International**. Khronos and 3D Commerce logos appear on the
model as non-copyrightable marks.
<https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/SunglassesKhronos>

Chosen because it is modelled at real-world scale — about 150mm across, 1 unit =
1 metre — which is what lets the fitting solver reason in physical units. Assets
authored at arbitrary scale need the *Fit to face* sizing mode instead.

## Face model and tracking

**`assets/face/canonical_face_model.obj`** and **`assets/models/face_landmarker.task`**
MediaPipe, © Google. Licensed **Apache 2.0**.
<https://github.com/google-ai-edge/mediapipe>

**`vendor/mediapipe/`** — `@mediapipe/tasks-vision` 1.0.1, Apache 2.0.
Verified byte-identical to the npm release (sha256 of `vision_bundle.mjs` and the
wasm against `cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1`, August 2026).
Two builds of the SIMD wasm runtime are vendored and no others, to keep the tree
at a reasonable size: `vision_wasm_internal.*` for the main thread and
`vision_wasm_module_internal.*` for the worker — the module build registers its
`ModuleFactory` on the global itself and carries an ES export, which is what lets
a module worker load it with a plain dynamic import.

## Rendering

**`vendor/three/`** — three.js r185.1, © three.js authors. Licensed **MIT**;
full text at `vendor/three/LICENSE`. Includes `GLTFLoader`, `OBJLoader`,
`BufferGeometryUtils`, `SkeletonUtils` and `RoomEnvironment` from the addons.

## Sample faces

**`assets/samples/face-a.jpg`**, **`assets/samples/face-b.jpg`**

Synthetic faces generated by StyleGAN2 (Karras et al.) via
thispersondoesnotexist.com. **These are not photographs of real people.** They were
chosen over a real portrait on purpose: a try-on fixture ends up rendered wearing a
client's product in screenshots, and a recognisable person there raises likeness and
implied-endorsement problems that a generated face does not have.
