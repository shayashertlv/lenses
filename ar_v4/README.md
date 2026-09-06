# Lenses AR v4 — Current mirror

Local visual eyewear try-on for Windows Chrome/Edge. Current mirror is the
wearer's preferred baseline; the experimental shared-face fitters were removed.
This is an approximate preview, not a personal scan or a physical fit measurement.
The active frame is **Amber Horizon**, exported from the supplied Blender model.
This development app lives under `ar_v4/` on `main`. The parent directory contains
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

Open http://127.0.0.1:8040 and choose **Open camera**.

```sh
npx playwright install chromium
npm test
npm run build
npm run preview
```

`npm test` runs 15 focused Node tests, strict TypeScript, a production build and
five browser lifecycle flows using real local MediaPipe with a simulated camera.
`npm run build` verifies the prepared model/mesh/fixture hashes and copies the
pinned runtime from node_modules. No assets are read from sibling projects.

The local Vite server provides the camera and worker isolation headers. Keep this
app's `package.json`, build output and assets here. The parent's Python `Procfile`
belongs to the live demo; no AR deployment or live route is configured.

## Use

**Open camera → Record head turn → Finish recording → Previous/Next → Download
capture**. Replay closes the camera and worker and shows the saved image with its
original detection. Downloads contain JPEGs, timestamps, detections, raw/corrected
poses and estimated surfaces. Recording is bounded to 30 seconds, 96 frames or
24 MiB of JPEG data. Notes are optional. **Discard capture** releases replay and
allows a fresh camera session. There is no saved-file import UI.
The `ar_v4` capture schema identifier and download names remain compatible with
existing recordings.

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
  projection, tracking and occlusion retain the baseline values. The current
  model's fixed attachment is described below.
- `src/capture/`: bounded immutable image/result storage, replay and explicit JSON
  export. `public/` contains only local runtime assets, provenance and licenses.

The assumed vertical FOV is 63°, with camera aspect from each frame. Amber's
attachment is `100 * glb_position + (0, 3.271027, 6.531958919387042)` centimeters.
Its normalized source is given an assumed 145 mm width; bridge height uses
canonical landmark 168 and the frame front retains Current mirror's original
6.691763 cm depth. These are fixed preview conventions, not wearer measurements.
The 3.7 MB GLB embeds its 2K tortoiseshell texture and baked brown lens gradient;
the roughly 100,000-triangle mesh preserves the supplied shape at lower detail.
The observed face is already in camera space and must not receive that pose again.

## Limits and next work

The far lens/rim can show through the side of the nose at larger yaw. Camera
intrinsics, learned face depth, rear-head geometry and lighting are approximate;
ears/hair and true skin contact are not reconstructed. Synthetic browser checks
do not establish real-camera motion, phone performance or anatomical accuracy.

Research is pending on actual image boundaries and color contrast during a scan.
No such algorithm, reconstruction experiment or wearer-specific tuning is included.
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
