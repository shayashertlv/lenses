# Lens preview renderer

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
