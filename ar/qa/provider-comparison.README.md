# Local provider and prepared-asset comparison

This QA harness reads self-contained local GLBs. It makes no provider calls, and
does not change the input files or the production AR renderer. From `automation/`:

```powershell
python -m qa.provider_comparison --manifest data/example/manifest.json --output data/example/standard
python -m qa.provider_comparison --manifest data/example/manifest.json --output data/example/ar --ar-check
```

The standard stage uses GLTFLoader materials, declared orientation, centering and
uniform scale. The AR stage hands the exact input bytes to `TryOnRenderer` and
records compatibility or rejection. A prepared GLB must declare that provenance;
AR rendering success is neither a semantic labeling certificate nor product
appearance acceptance. The AR source is a generated canvas with a synthetic
canonical face pose, not a photographed wearer.

## Manifest

```json
{
  "input_description": "Prepared candidate; optical appearance changed, frame texture retained.",
  "cases": [{
    "id": "candidate-01", "product": "oakley", "provider": "appearance-01",
    "path": "candidate.glb", "rotation_degrees": [0, 0, 0], "width_mm": 145,
    "input_provenance": {"preparation_report": "preparation.json"}
  }],
  "products": {"oakley": {"photos": {"front": {"path": "front.jpg"}, "back": "back.jpg"}}},
  "width": 640, "height": 480,
  "views": ["front", "angled", "angled-opposite", "rolled", "back"],
  "modes": ["raw"], "backgrounds": ["light", "dark", "checker"],
  "environments": [
    {"id": "room", "preset": "room", "intensity": 0.8},
    {"id": "broad", "preset": "broad_studio", "intensity": 0.8},
    {"id": "side", "preset": "side_studio", "intensity": 0.8}
  ],
  "background_fixture": "checker", "background_audit": true, "shadows": true,
  "ar_views": [
    {"id": "front", "yaw_degrees": 0},
    {"id": "angled", "yaw_degrees": 35},
    {"id": "rolled", "roll_degrees": 25}
  ]
}
```

Paths resolve relative to the manifest. Optional `model_sha256` pins each GLB.
Input descriptions and per-case provenance are copied to the reports. Source
photos remain uncalibrated references, and missing view/photo pairs stay empty.

Standard views also include `left`, `right`, and `top`. `rolled` is a front camera
rolled 25 degrees. `angled-opposite` uses normalized direction `[-.68,.22,1]`.
Optional `vertical_span` fixes the orthographic camera span. Standard diagnostic
modes are `raw`, `normals`, `unlit`, and `primitives`; only `raw` shows the supplied
GLTFLoader materials. Backgrounds are `light` (#eeeeee), `dark` (#342d2b), and a
beige checker. These are not photographed skin backgrounds.

AR uses 720×480 pixels, independently of standard `width`/`height`. AR view IDs
are explicit; yaw is bounded to ±80°, pitch and roll to ±60°. Rotations use YXZ
order. Explicit AR views/lighting settle 30 synthetic pose steps per view. A rear
face pose is not supported. For true canonical rear optics, add
`{"id":"back","type":"asset-back"}`. This separate inspection settles a
front pose, rotates the asset 180° about its bridge, hides synthetic face
occluders, and disables shadows/guard, following `semantic-material-cards.html`.
It restores transforms/visibility afterwards and reports the intervention. It
does not simulate a wearer looking backwards; its yaw/pitch/roll must be zero.
Other AR temple/pose logic remains active, so this diagnoses rear canonical
optics rather than certifying an unmodified full-geometry turntable.
The AR `background_fixture` is `solid` or `checker`; `background_audit` adds the
source canvas, bare render, exact pixel-delta receipt and no-shadow control.
For a matched white background control, set `background_fixture: "solid"` and
`background_color: "#ffffff"`. The optional color accepts only #RRGGBB and only
for the solid fixture. The default solid color remains #cba68d.

Omit `environments` to preserve the original room default and original filenames.
Explicit configurations permit the three presets above and intensity >0 to 4.
`room` is the production RoomEnvironment. The two studio presets are deterministic
neutral analytic environments with broad, nonzero directional illumination.
They retain reflections; they do not modify roughness, coating strength or tint.
The directional light remains intensity 2 at [-10,15,20], with ACES exposure 1.
Standard materials use PMREM blur .04; canonical AR optics retain their native
zero-blur PMREM and runtime lens intensity multiplier. This distinction is
intentional and recorded. The runtime has no public lighting-injection API, so
the QA adapter follows the existing `canonical-lens-runtime.html` inspection
seam, validates it and restores all touched fields. No `src/` changes are needed.

## Receipts and layouts

`report.json` records source GLB hashes, harness/source hashes before and after,
runtime compatibility, each render hash and lighting configuration. Standard
`normalization.world_to_render` maps original scene-world points to normalized
render coordinates. Each render has the exact camera view/projection matrices;
all matrices use Three.js column-major layout. Original node transforms should
be applied once before `world_to_render`. Pixel coordinates follow NDC to image
mapping: x=(ndc.x+1)*width/2, y=(1-ndc.y)*height/2.
Actual AR renders also record `spatial.asset_to_world`, `world_to_asset`, camera
origins, and per-lens `mesh_to_world`/`world_to_mesh` with `camera_origin_in_mesh`.
The latter directly matches each lens's local POSITION/NORMAL coordinates and
can drive measured perspective-incidence calculations instead of assuming the
requested face translation is the final glasses placement. World units are AR
centimetres; canonical asset/mesh coordinates are metres.

Standard filenames are `ID__MODE__BACKGROUND__env-ENV__VIEW.png`; AR filenames
are `ID__actual-ar__env-ENV__VIEW.png`. The `__env-ENV` component is omitted when
lighting is unspecified. `contact-sheets.json` lists product comparison sheets.
`candidate-cards.json` binds anonymous review cards to model/render hashes and
records each card's environment, columns (views), and rows (backgrounds). Images
have a neutral "Candidate" title and no model, family, provider, parameter or
score labels; references should be supplied separately. The reusable Python
`candidate_cards(manifest_path, output, *, modes=("actual-ar",), views=None,
environments=None, backgrounds=None)` filters those dimensions and returns the
same receipt. Select `environment == "broad"` for a broad-light primary review
and retain other environments for stability inspection. Use the returned layout
when prompting a reviewer; do not reuse an assumed legacy layout. Separate
`labeled-candidate-cards.json` files are human-facing cards with the source photo
row and case name, and should not enter a blinded comparison.

Canonical optical descriptors use private metadata, which standard GLTFLoader
does not interpret. Such assets can contain an explicitly approximate standard
material fallback; the report records that flag and labels its render behavior.
Use `mode == "actual-ar"` cards to rank canonical material hypotheses. Standard
renders remain useful for inspecting complete geometry without face occlusion.

`environment-differences.json` measures whole-image RGB change between lighting
presets. It is a lighting-response diagnostic, not a calibrated image-error or
acceptance metric. Reports always retain `accepted: false` and unmeasured visual
quality until a separate evidence-based review establishes a narrower claim.
