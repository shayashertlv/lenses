# Lenses AR v4 — Current mirror

Local visual eyewear try-on for Windows Chrome/Edge. The owner selected the
Raw + Option 17 nasal shape for the current development app. This is an
approximate preview, not a personal scan or a physical fit measurement.
Choose **Amber Horizon** (the default) or **Tom Ford · Clear lenses**, both exported
from the owner's supplied Blender models.
This development app lives under `ar_v4/`. The parent directory contains
the separate live Lenses Python demo deployed to Railway. AR is not deployed there.
The abandoned v2/v3 applications remain archived and absent from the active tree.

## Run and check

Requires Node.js 22.18+ and a camera-capable browser on localhost or HTTPS.
Run these commands from `ar_v4/`:

```sh
npm ci
npm run assets
npm run dev
```

Open http://127.0.0.1:8040, choose a frame, then choose **Open camera**.

```sh
npx playwright install chromium
npm test
npm run build
npm run preview
```

`npm test` runs focused Node tests, strict TypeScript, a production build and
browser lifecycle flows using real local MediaPipe with a simulated camera.
`npm run build` verifies the prepared model/mesh/fixture hashes and copies the
pinned runtime from node_modules. No assets are read from sibling projects.

The local Vite server provides the camera and worker isolation headers. Keep this
app's `package.json`, build output and assets here. The parent's Python `Procfile`
belongs to the live demo; no AR deployment or live route is configured.

## Use

**Choose a frame → Open camera → Record head turn → Finish recording → Previous/Next → Download
capture**. Replay closes the camera and worker and shows the saved image with its
original detection. Downloads contain JPEGs, timestamps, detections, raw/corrected
poses and estimated surfaces. Replay restores the captured surface directly,
without applying the nasal shape again. Recording is bounded to 30 seconds, 96 frames or
24 MiB of JPEG data. Notes are optional. **Discard capture** releases replay and
allows a fresh camera session. There is no saved-file import UI.
The frame selector stays fixed during startup, live camera and replay. Close the
camera or discard the capture before changing pairs. Each session loads only its
selected GLB. New downloads identify that model in the header and frame metadata;
original recordings are unchanged. First use starts live tracking after camera
permission; no personal scan or calibration step is implemented.
The `ar_v4` capture schema identifier and download names remain compatible with
existing recordings. New metadata identifies the occlusion method, selected and
applied shape, and any geometry fallback. Older saved surfaces retain their
original meaning; replay does not relabel them as Raw + Option 17.

Frames stay in memory until an explicit download; nothing is uploaded or written
to browser storage. Close camera, a hidden live page, navigation or an error ends
the session. Stop/discard removes the in-memory recording.

## Architecture

- `src/main.ts`: one session owns the camera, worker, renderer and callbacks.
  One frozen image is downsampled for detection, then presented with that result;
  no inference backlog or independent video overlay. CSS mirrors the whole canvas.
- `src/runtime/`: camera acquisition, validated detector messages, local MediaPipe
  worker; GPU startup falls back to a fresh CPU worker. Old sessions cannot publish.
- `src/render/`: Three.js renderer, fixed virtual projection, original bridge
  correction and observed face depth surface. The glasses stay rigid. Raw pose
  reconstructs the face; corrected X/Y translation places the glasses and rear
  head proxy. GLB meters convert once to canonical centimeters. Lighting,
  projection and tracking retain the baseline values. `nasal-shape.ts` applies the
  frozen Option 17 shape to the original reconstructed surface for both models,
  without RGB boundary repair. Its original geometry guards fall back to the exact
  raw surface when rejected. Glasses and transmissive lenses retain ordinary face
  depth testing. The fixed attachment is described below.
- `src/capture/`: bounded immutable image/result storage, replay and explicit JSON
  export. `public/` contains only local runtime assets, provenance and licenses.

The fixed shape is `central-wp020-dp015`, with central width parameter 0.20 and
forward depth parameter 0.15 in the existing preview conventions. It adds no
lighting classifier, yaw switch, smoothing or personalized fit. The old
`src/render/nasal-boundary.ts` stays byte-identical solely because historical
private recovery harnesses import and hash it; the active renderer does not use it.

The assumed vertical FOV is 63°, with camera aspect from each frame. Both models'
attachment is `100 * glb_position + (0, 3.271027, 6.531958919387042)` centimeters.
Their normalized sources are given an assumed 145 mm width; bridge height uses
canonical landmark 168 and the frame front retains Current mirror's original
6.691763 cm depth. These are fixed preview conventions, not wearer measurements.
The 3.7 MB GLB embeds its 2K tortoiseshell texture and baked brown lens gradient;
the roughly 100,000-triangle mesh preserves the supplied shape at lower detail.
The observed face is already in camera space and must not receive that pose again.

Tom Ford's self-contained 2.9 MB GLB has 86,831 triangles, a 2K frame color atlas
and 1K normal/metallic-roughness maps. The supplied mesh includes lens surfaces
but only an opaque material. An authored neutral transmissive material replaces
the lens paint for this testing option; the frame texture and shape are retained.
It is an optical approximation, not measured prescription or coating behavior.
Transmission still softens fine image detail and adds edge highlights; use the
original paired RGB when judging very subtle eye/temple boundaries.
Asset preparation and proof views stay private in
`.recovery/clear-lens-option-2026-09-06/`. See ATTRIBUTION for source identity.
The smaller file/mesh does not establish lower rendering cost; the extra frame
maps and physical transmission still consume GPU resources.

## Limits and next work

The far lens/rim can show through the side of the nose at larger yaw. Camera
intrinsics, learned face depth, rear-head geometry and lighting are approximate;
ears/hair and true skin contact are not reconstructed. Synthetic browser checks
do not establish real-camera motion, phone performance or anatomical accuracy.
The owner preferred Raw + Option 17 on the marked right-turn overcut image and
later explicitly chose its integration. Earlier feedback slightly favored RGB
repair at one opposite-turn angle; removing that stage also removes its small
local contribution. This is a chosen appearance tradeoff, not proof of correct
occlusion at all angles. The integrated path still needs physical-camera review
for far cut-through, near rim/bridge/pad hiding and motion across both turns,
including the previously resolved rapid cutoff flicker.

Temples/ear contact and pose jitter remain deferred. No personal scan, ear
occluder, multiview reconstruction or smoothing change is included. Sustained
whole-app 30 FPS remains a target; physical mobile, thermal, display-FPS and
end-to-end latency measurements are unavailable. Old RGB-stage timings do not
describe the current path.
See [HANDOFF.md](HANDOFF.md) for the checkpoint and [docs/REVIEWS.md](docs/REVIEWS.md)
for cleanup verification. Asset attribution is in [ATTRIBUTION.md](ATTRIBUTION.md).

The untouched wearer export is in [recordings/](recordings/README.md). The full
pre-cleanup project and original export are recoverable from
`../.recovery/ar_v4-before-cleanup-2026-09-06.tar.gz`; instructions and SHA-256 receipts
are in `../.recovery/`. These local files are ignored by Git and excluded from the
production build.
The older applications' final working files, including uncommitted changes, are
also saved in `../.recovery/promotion-2026-09-06/`, with a verified archive, Git bundle
and restoration instructions. Recovery files and private recordings stay local.
