# Appearance preview renderer

`viewer.js` imports the existing `arview` `TryOnRenderer`, optical-material helper,
and explicit-camera conversion. `build.mjs` uses the installed `arview` Vite and
Three packages and writes a local bundle to the ignored `dev_ui/.lens-renderer/`.
The server calls `renderer_asset(name)` in a worker thread; its source digest
invalidates the bundle when the renderer, pinned dependencies, or bridge changes.
Only its explicit asset allowlist can be served.

The browser API is:

```js
const preview = await createLensColourViewer(host, {modelUrl, materials});
preview.applyMaterials(materials);
preview.setView('angled', 'left');
preview.dispose();
```

Each material row contains its original glTF material index and complete values
from `read_lens_materials`. RGB is linear. Quick views and OrbitControls move a
metre-space camera; the actual renderer receives the same centimetre conversion
as its capture path. Material drags update uniforms, recompiling only when a
physical feature crosses zero. `capture()` and `setExplicitCamera(camera)` expose
the deterministic camera path for proof.

The general Edit page passes all separately owned materials from
`read_look_materials`. Each edit requests the exact patched GLB from the local
`appearance-model` endpoint and calls `load(url, materials, {preserveCamera:true})`.
This uses the same generated texture, projected coordinates and material values
as Save; no browser gradient approximation is involved. Reloads prepare a fresh
canvas before replacing the displayed frame because disposing the production
renderer deliberately loses its canvas's WebGL context. Orbit, pan, zoom and an
explicit fitted photo camera survive the replacement.

`pick([x,y])` takes normalized canvas coordinates and raycasts the currently
rendered geometry. It returns the material index and both rendered and authored
positions. Frozen photo temple openings rotate private geometry in the production
renderer; the original triangle vertices plus hit barycentrics recover the same
point on the authored native mesh. The existing M12 capture viewer reprojects that
point at its capture resolution. If the authored pose hides it, the capture selects
a visible view of that surface or gives a plain refusal. It never substitutes an
anchor on a different surface. Captures retain M12's centred-camera contract.

For E4 parity, prepare immutable solid/gradient GLB copies and their complete
appearance DTOs through the backend patcher, then run this once per case in the
serial browser/native slot:

```powershell
node glasses3d/dev_ui/lens_renderer/general-parity.mjs --out glasses3d/data/m12/codex/FRESH_PARITY --model COPY.glb --materials MATERIALS.json
```

The unchanged production AR capture renders the identical GLB/camera/background;
`parity.json` requires zero differing RGBA channels and unchanged source bytes.

`coatingSwatchRGB` translates the pinned Three thin-film equations and returns an
sRGB reflection swatch at a given angle. Full transmission and room lighting are
shown in the live preview. The shader comparison in `parity.mjs` checks the CPU
translation against the original Three GLSL.

The proof input preparation verifies the immutable delivery and copies its model
and exact material DTO into a fresh directory. It renders nothing:

```powershell
.\glasses3d\.venv\Scripts\python.exe -m glasses3d.dev_ui.lens_renderer.proof prepare --job glasses3d/data/jobs/JOB --out glasses3d/data/m12/codex/e2-parity-inputs
```

After other native checks have exited, reserve the native/browser slot and start
the serial proof with a fresh output directory:

```powershell
.\glasses3d\.venv\Scripts\python.exe -m glasses3d.dev_ui.lens_renderer.proof start --inputs glasses3d/data/m12/codex/e2-parity-inputs --out glasses3d/data/m12/codex/e2-parity-attempt1
```

The detached process records its PID, timestamps and exit in `status.json`, and
output in `proof.log`. It checks synthetic transmitting lenses, initially opaque
lenses, and the real copied model. Each case compares the preview's before/after
pixels against the unchanged `arview` capture command at the identical camera,
requiring zero differing RGBA channels. It also checks the ordinary initial orbit
framing, restoring original values, and transmission/coating feature boundaries.
Failed attempts stay in place; reruns use a new output directory.
