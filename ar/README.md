# Lenses AR

The eyewear try-on pipeline, extracted from `ar_v4` on September 15, 2026 as the main pipeline. It is the guarded GPU
compose that `ar_v4/experiments/structure-lab` measured as mode X: the same capture, face landmarker, hair segmenter and
frame scheduling as the accepted G Combined pipeline, with the hair occlusion, the optical/nasal protection and the
temple continuity cut done inside the eyewear shaders instead of a CPU compose after a readback. No pixel leaves the GPU
on a live frame. Everything runs in the browser; nothing is uploaded.

Owner-measured in the lab under a 29.6 fps camera (phone flashlight, Chrome 152, 2026-09-15): G 15.79 fps / age p95
141 ms; this pipeline (X) 22.67 fps / 102 ms. Reduced capture (`?capture=960`, mode Y) reached 28.73 fps at the camera's
cap; the capture edge stays a product decision and defaults to 1280 here.

## Run

```bash
npm install
npm run build      # verifies the pinned assets, type-checks, bundles into dist/
npm run preview    # frozen build on http://127.0.0.1:8241/
npm run dev        # dev server on http://127.0.0.1:8240/ (hot reload; never for a timed session)
npm test           # unit tests (node --test)
npm run measure -- --base=http://127.0.0.1:8241 --sessions=1 --warm=10 --measure=30   # synthetic-camera harness
```

Opening the page starts nothing. Choose the glasses and the hair model, then Open camera.

## Published site

Startup diagnostics (temporary): while a session runs, the page posts its step log and, every 10 s, the last 10 s
of stage medians (numbers only; never an image, detection, mask or hash) to `POST /ar/diagnostic`; the last forty
reports are readable at `GET /ar/diagnostics.json`, so a stall or a rate on a device can be read without the device.
The page footer says so.

The Lenses web app (`UI/`, the Python server on Railway) lists **AR** first on its landing page and serves this
pipeline at `/ar/` from `ar/site/`, the committed output of:

```bash
npm run publish    # assets + check + vite build --base=/ar/ into site/, then site/public-manifest.json
```

`UI/ar_site.py` serves only the manifest's files, verifies each file's SHA-256 before serving it, streams models in
bounded chunks and sets the isolation and Content Security Policy headers (`connect-src 'self' blob:`: nothing leaves
the origin, MediaPipe's telemetry included). Every asset address in the bundle carries Vite's base (`src/assets.ts`), so
the same source serves at the root locally and under `/ar/` on the site. After Railway publishes a commit:

```bash
python qa/verify-published.py --url https://web-production-ef3ca.up.railway.app --output qa/output/published-receipt.json
```

## The pipeline, per frame

1. **Capture** (`src/pipeline/pipeline.ts`): on `requestVideoFrameCallback` the camera frame is drawn into a canvas of at
   most 1280 px and its pixels are read once for the SHA-256 that ties the hair mask to its own image. Every row carries
   the camera's presented-frame counter, so the camera's delivered rate is always known. That counter is also what
   identifies a frame (`frame-identity.ts`); `currentTime` is never compared across a draw, because WebKit reports a
   running clock for a camera stream and such a comparison discarded every frame on iPhone.
2. **Inference**: the face landmarker (`src/face/`, MediaPipe FaceLandmarker in a worker, CPU delegate by default) sees a
   640 px copy; the hair segmenter (`src/hair/`, MediaPipe ImageSegmenter in a worker, category mask only) sees the full
   frame on the laptop and a 640 px copy on phones (`?hairinput=`), where the mask readback was a quarter of the hair
   worker's time; a mask smaller than the frame is read by nearest lookup in the shader, the cut and the CPU reference. The frame pump (`src/pipeline/frame-pump.ts`) overlaps the next frame's inference with the current frame's
   preparation, with at most two owned frames and one serial hair worker.
3. **Pose** (`src/render/renderer.ts`, `pose`): waits for the previous frame's GPU fence (at most 1 s; three unanswered
   fences in a row switch the gate off for the session and the live panel says so), then bridge pose
   (`bridge-pose.ts`), observed face surface (`face-surface.ts`) shaped by the nasal shape (`nasal-shape.ts`), the
   pose-driven rear drop (`rear-drop.ts`), the temple clip/blend and side-depth visibility configurations
   (`temple-clip.ts`, `temple-visibility.ts`), the protection geometry (`protection.ts`: optical and nasal rectangles,
   arm corridors) and the projected arm centrelines (`continuity.ts`).
4. **Render** (`render`): the hair mask goes up as a 0/255 red texture; the continuity cut walks each arm's centreline over
   the mask and cuts the arm from its first hair run of at least 10 px to the tip; the stencil marks the editable region
   (arm corridors minus protected rectangles) and the frame is drawn in two passes: everything else unblended, the
   editable region with the hair blend (`hair-occlusion.ts`). A fence is queued; the frame age ends here.
5. **Telemetry** (`src/pipeline/profiler.ts`): one numeric row per published frame; no images, detections, masks or
   hashes ever enter it, so "Download timings" can leave the page.

### Guarantees kept from G

- The optical and nasal rectangles are never written by a hair-blended or dropped fragment (stencil, protection wins).
- The rear drop stays as posed in both passes because the editable rectangles derive from the dropped arm bounds.
- When the protection cannot be established, the frame is drawn without drop or hair.
- The continuity cut uses the same hash-pinned original arm geometry as G's continuity pass.
- **Hold & audit** (`src/audit/`): the next frame is also drawn without hair, without eyewear and undropped, each read
  back once; G's CPU compose and continuity pass run on the same inputs and G's four protection checks are applied to
  the GPU output. Lossless images and difference maps are included; "Download audit" saves it.

What is not byte-exact G: the hair edge is the mask's bilinear feather rather than the two-pixel CPU feather; the
continuity cut replaces G's after-the-fact removal of detached remnants; lens transmission sees the dropped temples.

## Page options (URL)

| Parameter | Default | Meaning |
|---|---|---|
| `?capture=` | 1280 | capture max edge in px (320..1280); everything downstream scales with it |
| `?exposure=` | auto | lock the camera exposure at N × 100 µs (`312` = 1/32 s restores 30 fps in dim light, one stop darker) |
| `?face=gpu` | cpu | try the GPU landmarker first |
| `?hairrun=` | 10 | minimum hair run along the arm, in source px, that counts as a patch for the cut |
| `?continuity=0` | on | disable the cut |
| `?guard=0` | on | unguarded single pass (measurement only) |
| `?sync=0` | on | do not gate frames on the previous frame's GPU completion (measurement only) |
| `?hairz=` | −0.02 | mesh-local metres behind which temple fragments may blend under hair |
| `?eyewear=`, `?hairModel=`, `?hair=0` | | initial control values |
| `?hairinput=` | frame, phones 640 | px max edge of the copy the hair segmenter sees; the mask is that size and is read by nearest lookup everywhere (256..1280) |
| `?hairdelegate=` | probe | `cpu` or `gpu` forces the hair segmenter's delegate |
| `?hairwait=` | 8, phones 60 | ms a frame waits for its own hair mask before it is drawn without it (0..200); on phones the hair worker needs 43-65 ms and at 8 ms no mask was ever drawn |
| `?diag=0` | on | send no startup or live diagnostics to the site (A/B lever while the beacon exists) |
| `?model=&name=&clip=&width=&sha256=` | | a Modeling Auto handover (`src/eyewear/external.ts`) |

## Read the camera before judging any fps figure

The live panel prints the camera's delivered rate next to the pipeline's. The reference laptop's webcam auto-exposes at
1/16 s in dim light and then delivers 15 fps whatever the page does, which caps every pipeline at 15. `qa/camera-rate.mjs
<export.json>` prints the delivered rate of any timing export; `qa/camera-probe.mjs` measures the real camera under
several constraint sets (`--sweep` for manual exposures).

## Harness

`qa/measure.mjs` drives the page's own measurement with a synthetic 30 fps camera (the checked-in `qa/fixtures/face-a.jpg`
with slow drift), saves a screenshot of the stage and audits one frame, writing everything under `qa/output/`.
Controlled-input evidence only: not a real camera, wearer motion, phone or thermal evidence. `qa/gl-probe.mjs` prints the
unfiltered console.

First run on the frozen build (this laptop, 8 s warmup + 20 s, `qa/output/smoke-first`): 22.2 fps against a synthetic
camera that delivered 27.3 fps in that run, age median/p95 71/99 ms, 446/446 tracked, 443 masked; prepare 10 ms (GPU wait
1.1, pose 5.4), finish 7 ms (submit 5.8); audit: guard on (2 protected / 2 editable rectangles), all four checks pass,
GPU output identical to the CPU reference compose plus continuity, drop intrusion 0 px over Δ8 (max 4), cut −26 mm on
both arms. The fixture's hair never covers the arms, so the blend itself is only validated on a real face.

## Layout

```
index.html, src/main.ts, src/config.ts, src/style.css   the page, its URL options and its style
src/camera/      openCamera, the exposure lock
src/face/        landmarker protocol, timing sidechannel, client, worker
src/hair/        pinned models, protocol (category-only), client, worker, backend probe
src/eyewear/     the frame catalog with the Modeling Auto slot, the URL handover
src/pipeline/    frame pump, the pipeline, the profiler
src/render/      the renderer and its geometry modules (pinned from the accepted G tree)
src/audit/       the CPU reference compose, checks and continuity pass; the Hold audit
public/          pinned assets (GLBs, face landmarker task, hair weights, MediaPipe runtime, canonical face)
scripts/         prepare-assets.mjs (SHA-256 verification, manifest, WASM copy)
qa/              measure.mjs, gl-probe.mjs, camera-rate.mjs, camera-probe.mjs, fixtures/
tests/           node --test unit tests
```

Pitfalls recorded on the way: `WebGLRenderer({stencil: true})` also gives Three's lens-transmission render target a
stencil buffer (cleared to 0), so the lenses render wrongly under the stencil test; the renderer is created with
`stencil: false` on a stencil-bearing context. Three applies `setScissor` only at render time, so the stencil is marked
with raw GL scissor clears. A second `render()` with `autoClear = false` redraws `scene.background`; pass B sets it to
null.
