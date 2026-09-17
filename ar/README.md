# Lenses AR

The eyewear try-on pipeline, extracted on September 15, 2026 as the main pipeline from the previous AR generation
(ar_v4, since archived outside this repository). It is the guarded GPU compose measured there as mode X: the same
capture, face landmarker, hair segmenter and frame scheduling as that generation's accepted G Combined pipeline, with
the hair occlusion, the optical/nasal protection and the temple continuity cut done inside the eyewear shaders instead
of a CPU compose after a readback. No pixel leaves the GPU on a live frame. Everything runs in the browser; nothing is
uploaded.

Owner-measured in that generation's lab under a 29.6 fps camera (phone flashlight, Chrome 152, 2026-09-15): G 15.79 fps
/ age p95 141 ms; this pipeline (X) 22.67 fps / 102 ms. Reduced capture (`?capture=960`, mode Y) reached 28.73 fps at
the camera's cap; the capture edge stays a product decision and defaults to 1280 here.

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

Diagnostics, opt-in with `?diag=1`: while a session runs, the page posts its step log and, every 10 s, the last 10 s
of stage medians (numbers only; never an image, detection, mask or hash) to `POST /ar/diagnostic`; the last 150
reports are readable at `GET /ar/diagnostics.json`, so a stall or a rate on a device can be read without the device.
The page footer says so while it is on. This is how the phone defaults below were measured. Each 10 s window posts two
events: `live` (stage medians) and `hair` (every completed hair job's inference and round trip, median / p95; what was
in flight when a frame's draw started; how far each face request was posted from the draw starts around it, and the
face inference time of requests with a draw starting within 10 ms after the post against the rest, a split fixed
before the request runs; the "Hair masks" line). Each report carries the options the page received (`options`) and the ones it ignored
(`ignoredOptions`); `pipeline` counts captures replaced before inference. The server keeps reports in memory only:
every deploy empties `/ar/diagnostics.json`, so save it before pushing.

The settings line starts with "IGNORED, not a known option: …" when the address holds a key the page does not read
(`ADDRESS_OPTIONS` in `src/config.ts`), so a misspelled lever such as `?hairframe=2` cannot silently run the default.

## Phones (iPhone 17 Pro, Safari, 720x1280)

Phones and tablets now default to the canvas capture (the laptop takes the frame as a VideoFrame; both were measured on
each device on 2026-09-16, `?source=`), the CPU face landmarker first, hair on every second frame with reused masks
(2026-09-17, below) and a 640 px copy for the hair segmenter; Apple phones and tablets run the hair segmenter on the
CPU.

**2026-09-15** (GPU face landmarker first, hair on every frame). Three things were measured on the device through the
diagnostics: a frame must wait for its own hair mask when hair runs on every frame (at an 8 ms cap not one mask in two
minutes was drawn while every frame still paid for the worker; the laptop's fast capture later showed the same failure
at one frame in four), the hair segmenter sees a 640 px copy (mask readback 20-25 ms → 8-10 ms), and on Apple phones it
runs on the CPU (no readback at all; the GPU, left to the face landmarker, throttles less). The 640 px copy became the
rule on every device, waiting for the mask the rule wherever hair runs on every frame (the laptop default), and the CPU
hair delegate the Apple default. Result: 29 fps for the first 40 s, then 21-24 fps; the phone throttles after 20-40 s of
load whichever processor carries the hair, so the sustained rate is a thermal budget. Hair off runs at the camera's
30 fps. The 256 px hair model changed nothing. Face on the CPU did not help in that test; on 2026-09-17, with hair on
every frame, the CPU face landmarker read 26.3-27.7 fps at 45-96 s against 21.1-24.9 on the GPU, rests not matched (see
below). The audit on the phone passes all four protection checks; the GPU output differs from the CPU reference by 22
pixels along a hair edge at the cut boundary (max delta 42), where the laptop shows zero; a precision difference of the
Apple GPU at the z ramp, inside the editable region. `?capture=960` or `640` would cut render work further at the cost
of a softer mirror; untested, a visual decision.

**2026-09-17, the phone and tablet defaults changed** (iPhone, Safari, `?diag=1` runs, owner decision): hair on every
second frame with reused masks moved by the head (`src/hair/mask-reuse.ts`) and the CPU face landmarker first.
With the GPU landmarker, once the pipeline fell behind the camera the next frame's face request was posted about 1 ms
before the current frame's draw and took about twice as long (30-35 ms against 14-15 ms), which held hair on every
second frame at 24-28 fps; on the CPU that penalty was gone (3-16 %). Measured after 45 s: every second frame with the
CPU face landmarker held 29.9-30.2 fps to 168 s and 28.5 at 199 s (frame age 30-66 ms); every frame with the CPU face
landmarker 26.3-27.7 fps at 45-96 s and 23.0-24.4 at 107-168 s; every second frame with the GPU face landmarker
24.3-28.2; every frame with the GPU face landmarker 21.1-24.9. Late in a three-minute run the hair jobs slow
(round trip 32 -> 78 ms) and the drawn masks age to about 100 ms (p95 143 ms); the owner judged the hair edge on the
phone perfect. Runs: 2 + 1 + 3 + 6, rests not matched; Android and Android tablets take the same defaults unmeasured
(their hair delegate stays the probe; a large Android tablet asking for the desktop site is recognised by touch
without a fine pointer). `?hairframes=1` restores the earlier behaviour on every phone and tablet; on iPhone and iPad
also add `?face=gpu` (Android already ran the CPU face landmarker first). Hold & audit on a phone or tablet holds a frame
that waited for its own mask (at most `?hairwait=` ms), since the CPU reference composes only a frame's own mask.

The laptop (Intel Arc 140T, real webcam, 1280x720, canvas capture, 2026-09-15) showed the same shape: 28-29 fps for
40 s, then 22, with the camera steady at 29.6 fps. What grew there was the video-to-canvas draw (10 → 21 ms) and the other
CPU-bound stages, while GPU stages stayed flat and the GPU wait dropped to zero: the CPU clocks down after its turbo
window. The draw was the largest main-thread item per frame; the capture without the 2D canvas (VideoFrame) was measured
next and has been the laptop default since 2026-09-16: 29.4 fps flat over 80 s (`?source=`, below).

## The pipeline, per frame

1. **Capture** (`src/pipeline/pipeline.ts`): on `requestVideoFrameCallback` the camera frame is drawn into a canvas of
   at most 1280 px. On laptops the frame is taken as a WebCodecs VideoFrame whose own bytes are hashed for the SHA-256
   that ties the hair mask to its image, and the canvas is never read back (`capture.ts`); `?source=canvas` is the
   former capture, the default on phones and tablets, where it measured faster (2026-09-16). A VideoFrame can hold the
   camera sensor's own pixels while the video element shows them turned upright, so the first frames of a session
   measure the turn against the browser's displayed image and the capture undoes it; a picture too uniform to measure
   falls back to the canvas. Every row carries the camera's presented-frame counter, so the camera's delivered rate is
   always known. That counter is also what identifies a frame (`frame-identity.ts`); `currentTime` is never compared
   across a draw, because WebKit reports a running clock for a camera stream and such a comparison discarded every frame
   on iPhone.
2. **Inference**: the face landmarker (`src/face/`, MediaPipe FaceLandmarker in a worker, CPU delegate by default) sees a
   640 px copy; the hair segmenter (`src/hair/`, MediaPipe ImageSegmenter in a worker, category mask only) sees a 640 px
   copy too (`?hairinput=`; the mask readback was a quarter of the hair worker's time at frame size); the mask is read by
   nearest lookup in the shader, the cut and the CPU reference. On laptops (and with `?hairframes=1`) every frame waits for
   its own mask (`HAIR_WAIT_MS` is only a guard against a stalled worker): a frame is never drawn without hair while its
   mask is on its way. On phones and tablets (and with `?hairframes=2`) the hair segmenter runs on every second frame and
   no frame waits: a frame without its own mask draws the newest mask moved with the head, if it is at most
   `?hairmaxage=` ms old, and is otherwise drawn without hair. The frame pump (`src/pipeline/frame-pump.ts`) overlaps the next frame's inference with the current frame's
   preparation, with at most two owned frames and one serial hair worker.
3. **Pose** (`src/render/renderer.ts`, `pose`): waits for the previous frame's GPU fence (at most 1 s; three unanswered
   fences in a row switch the gate off for the session and the live panel says so), then pose steadiness
   (`pose-stabilizer.ts`: One Euro smoothing of the detector's orientation and depth), bridge pose
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

What is not byte-exact G: the hair edge is a hard step at the mask's nearest texel rather than the two-pixel CPU
feather; the continuity cut replaces G's after-the-fact removal of detached remnants; lens transmission sees the dropped
temples.

## Page options (URL)

| Parameter | Default | Meaning |
|---|---|---|
| `?capture=` | 1280 | capture max edge in px (320..1280); everything downstream scales with it |
| `?exposure=` | auto | lock the camera exposure at N × 100 µs (`312` = 1/32 s restores 30 fps in dim light, one stop darker) |
| `?face=gpu` | cpu | try the GPU landmarker first (the iPhone default until 2026-09-17) |
| `?hairrun=` | 10 | minimum hair run along the arm, in source px, that counts as a patch for the cut |
| `?continuity=0` | on | disable the cut |
| `?guard=0` | on | unguarded single pass (measurement only) |
| `?sync=0` | on | do not gate frames on the previous frame's GPU completion (measurement only) |
| `?hairz=` | −0.02 | mesh-local metres behind which temple fragments may blend under hair |
| `?eyewear=`, `?hairModel=`, `?hair=0` | | initial control values |
| `?source=` | VideoFrame; phones canvas | `videoframe` takes the frame as a VideoFrame and hashes its own bytes, no canvas readback; `canvas` is the former capture. Laptop webcam at 30 fps: VideoFrame 29.4 fps flat over 80 s, age 41 ms, against the canvas path's 26.5 fps after the CPU clocks down at 50 s, age 52-66 ms. On the iPhone the canvas wins instead (28-29 fps where VideoFrame had fallen to 25-27; see `capture.ts`). The synthetic harness shows the reverse of the webcam (GPU-resident frames), so judge on a real camera only |
| `?hairinput=` | 640 | px max edge of the copy the hair segmenter sees; the mask is that size and is read by nearest lookup everywhere; 1280 restores the frame-size mask |
| `?hairdelegate=` | probe, Apple phones `cpu` | `cpu` or `gpu` forces the hair segmenter's delegate; on the iPhone the CPU has no mask readback (2026-09-15, face landmarker then on the GPU: 29 fps for 40 s, 2-3 fps ahead at 30-60 s; kept with the CPU face landmarker of 2026-09-17, GPU hair with it unmeasured) |
| `?hairwait=` | 120 | guard in ms after which a frame is drawn without its hair mask; read where frames wait for their own mask (hair on every frame, and the audited frame with hair on every second frame; measurement lever) |
| `?diag=1` | off | send the startup step log and live stage medians (numbers only) to this site, readable at `/ar/diagnostics.json` |
| `?model=&name=&clip=&width=&sha256=` | | a Modeling Auto handover (`src/eyewear/external.ts`) |
| `?steady=0` | on | turns off pose steadiness: the glasses' orientation and depth are smoothed over time before the bridge pin (`src/render/pose-stabilizer.ts`), so the image-plane position still follows each frame's nose landmarks. The live panel's "Pose shake" line reads raw vs steadied shake and the trailing angle from the timing rows. Synthetic: shake to 0.15× at rest, 2.3° trailing on a ±20° 0.5 Hz turn. Accepted by the owner live on the laptop, 2026-09-17; phones not yet judged |
| `?hairframes=` | laptops `1`; phones and tablets `2` | `1`: every frame waits for its own hair mask. `2`: runs the hair segmenter on every second frame (`src/hair/mask-reuse.ts`); no frame waits for hair, and a frame whose own mask is not ready draws the newest mask of another frame moved by the head's motion (a 2D shift/turn/scale fitted to 24 skull landmarks, applied as one 3×3 matrix on the mask lookup and to the continuity cut). The live panel's "Hair masks" line (also in the `?diag=1` hair reports) counts own / reused / missing masks, reuse age and head motion. Owner's laptop look 2026-09-17: acceptable. Other schedules looked at the same day (a held unmoved mask, hair whenever the worker is free, every 3rd/4th frame) were not, and are not in the code: `1` and `2` are the only values. Phone default since 2026-09-17 (see Phones) |
| `?hairmove=` | 8 | with hair on every second frame (phone and tablet default, `?hairframes=2`): start a mask early when the head moved more than this many px since the newest mask's frame; `0` never early |
| `?hairmaxage=` | 200 | with hair on every second frame (phone and tablet default, `?hairframes=2`): never draw a mask whose frame was captured more than this many ms from the drawn frame (30..1000) |
| `?steadyhz=`, `?steadybeta=` | 1, 0.1 | rotation cutoff at rest (Hz, 0.05..20) and added Hz per °/s of head rotation (0..5); lower `steadyhz` is steadier, higher `steadybeta` follows turns more closely |
| `?steadydepthhz=`, `?steadydepthbeta=` | 1, 0.2 | the same for depth (Hz, and Hz per cm/s) |

## Read the camera before judging any fps figure

The live panel prints the camera's delivered rate next to the pipeline's. The reference laptop's webcam auto-exposes at
1/16 s in dim light and then delivers 15 fps whatever the page does, which caps every pipeline at 15. `qa/camera-rate.mjs
<export.json>` prints the delivered rate of any timing export; `qa/camera-probe.mjs` measures the real camera under
several constraint sets (`--sweep` for manual exposures).

## Harness

`qa/measure.mjs` drives the page's own measurement with a synthetic 30 fps camera (the checked-in `qa/fixtures/face-a.jpg`
with slow drift), saves a screenshot of the stage and audits one frame, writing everything under `qa/output/`.
Controlled-input evidence only: not a real camera, wearer motion, phone or thermal evidence.

First run on the frozen build, 2026-09-15 (this laptop, 8 s warmup + 20 s; before the VideoFrame capture and pose
steadiness became defaults, so rerun `npm run measure` for current figures): 22.2 fps against a synthetic camera that
delivered 27.3 fps in that run, age median/p95 71/99 ms, 446/446 tracked, 443 masked; prepare 10 ms (GPU wait 1.1, pose
5.4), finish 7 ms (submit 5.8); audit: guard on (2 protected / 2 editable rectangles), all four checks pass, GPU output
identical to the CPU reference compose plus continuity, drop intrusion 0 px over Δ8 (max 4), cut −26 mm on both arms.
The fixture's hair never covers the arms, so the blend itself is only validated on a real face.

## Layout

```
index.html, src/main.ts, src/config.ts, src/style.css   the page, its URL options and its style
src/assets.ts    every asset address under Vite's base (src/env.d.ts declares the build time the page shows)
src/camera/      openCamera, the exposure lock
src/face/        landmarker protocol, timing sidechannel, client, worker
src/hair/        pinned models, protocol (category-only), client, worker, backend probe, mask reuse (every 2nd frame)
src/eyewear/     the frame catalog with the Modeling Auto slot, the URL handover
src/pipeline/    capture (VideoFrame or canvas), frame identity, frame pump, the pipeline, the profiler, and the pose-shake
                 (steadiness.ts) and hair-schedule (hair-report.ts) readings of the timing rows
src/render/      the renderer and its geometry and shader modules, pose steadiness (pose-stabilizer.ts)
src/audit/       the CPU reference compose, checks and continuity pass; the Hold audit
public/          pinned assets (GLBs, face landmarker task, hair weights with their manifest and attribution, MediaPipe
                 runtime, canonical face, licenses)
scripts/         prepare-assets.mjs (SHA-256 verification, manifest, WASM copy); publish-site.mjs (`npm run publish`:
                 site/ and its public-manifest.json); prepare-hair-assets.mjs (the hair weights against their manifest,
                 offline; `--download` restores a missing one from its pinned Google URL)
site/            the published build the web app serves at /ar/ (committed; rebuilt only by `npm run publish`)
qa/              measure.mjs, camera-rate.mjs, camera-probe.mjs, verify-published.py (the check after a deploy), fixtures/
tests/           node --test unit tests
```

Pitfalls recorded on the way: `WebGLRenderer({stencil: true})` also gives Three's lens-transmission render target a
stencil buffer (cleared to 0), so the lenses render wrongly under the stencil test; the renderer is created with
`stencil: false` on a stencil-bearing context. Three applies `setScissor` only at render time, so the stencil is marked
with raw GL scissor clears. A second `render()` with `autoClear = false` redraws `scene.background`; pass B sets it to
null.
