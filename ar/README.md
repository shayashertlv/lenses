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
Opening the page does not start the camera. Choose glasses and a hair model, then open the camera.
Look forward for a few seconds to let the bounded head-width calibration settle before turning.
The normal page runs the approved fixed-shape pipeline; no preview URL or comparison mode is required.

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
4. **Temple visibility**: project the fixed arm geometry, update each hair endpoint once per frame, and
   combine head depth, observed cheek depth and the current hair mask. Audit renders reuse that same state.
5. **GPU composition**: render directly to the visible canvas, with stencil protection for the optical and
   nasal regions. Hair blending stays inside the editable arm corridors. No rendered frame is read back
   during live use; only an explicit audit reads the render.
6. **Profiling**: collect numeric timings and frame age. Images, detections, masks and hashes are excluded
   from timing reports.

On laptops every frame waits for its own hair mask. Phones and tablets normally segment every second frame
and reuse the latest sufficiently recent mask, transformed with head movement. Reuse supplies no new
permission to reveal a hidden temple. A stalled or unavailable mask does not release a held endpoint.

## Fixed temples and occlusion

The shipped models use a fixed 18 mm outward splay. A static inward return begins at model-local z = -75 mm
and buries the terminal band inside the canonical head volume. Shape does not bend with
head angle or camera distance, and the system does not try to fit the tips around an ear.

A closed posterior head shell joins the canonical face perimeter to the head proxy. Canonical lateral depth,
attached to the glasses pose, prevents asymmetric landmark shape changes from cutting one arm earlier
while nodding. The central face and nose retain observed shape. A bounded bilateral head-width calibration
adjusts the anterior occluder without moving the glasses or the terminal volume.

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
A small two-pixel tent filter softens the rendered hair edge without changing category evidence.

The model dimensions and learned face depth are rendering estimates, not anatomical measurements.
Regression tests protect these rules; live appearance still depends on tracking and segmentation quality.

## Audit and diagnostics

**Hold & audit** keeps one frame, renders it with/without hair and without eyewear, and checks that hair edits
preserve the optical region, nose, background and pixels outside the editable corridors. It records endpoint,
fixed-return and head-fit metadata, plus lossless images and difference maps. The CPU binary-mask baseline
is a diagnostic reference; it does not reproduce GPU edge feathering. Reused masks skip that reference
comparison because it requires a frame's own mask. Audit images stay local unless the user downloads them.

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
| `model`, `name`, `clip`, `width`, `sha256` | Validated external-model handover through `src/eyewear/external.ts` |

`node qa/measure.mjs --sessions=1 --warm=10 --measure=30` runs a synthetic-camera measurement using the checked-in
fixture. It verifies lifecycle and rendering under controlled input; it does not measure real wearer motion,
phone performance or sustained thermal behavior. `qa/pipeline-smoke.mjs` verifies the normal page on desktop/mobile
layouts, both shipped models, restart behavior and audit protections without opening a real camera.

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
experiments are not part of the production pipeline.
