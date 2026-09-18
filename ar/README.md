# Lenses AR

The eyewear try-on pipeline, extracted on September 15, 2026 as the main pipeline from the previous AR generation
(ar_v4, since archived outside this repository). It is the guarded GPU compose measured there as mode X: the same
capture, face landmarker, hair segmenter and frame scheduling as that generation's accepted G Combined pipeline, with
the hair occlusion, the optical/nasal protection and the temple continuity cut done inside the eyewear shaders instead
of a CPU compose after a readback. No rendered pixel is read back on a live frame (only Hold & audit reads the canvas);
the capture still copies each frame's bytes out once for its SHA-256. Everything runs in the browser; nothing is
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
(`ignoredOptions`); `pipeline` is the pump's own line: the stage the active frame is in and how long it has been there,
then offered / captured / published / dropped (replaced after capture, locked, misses) and the inference count. A report
carries at most the last 40 events (`src/main.ts`), so a session longer than about 150 s must be reassembled by unioning
successive reports; the newest report alone drops the earliest windows and the startup log. The server keeps reports in
memory only: every deploy empties `/ar/diagnostics.json`, so save it before pushing (the saved runs behind the figures
below are not in this repository (`qa/output/` is ignored); the 2026-09-17 phone snapshots are kept with the archive outside it.

The settings line under the mirror starts with "IGNORED, not a known option: …" when the address holds a key the page
does not read (`ADDRESS_OPTIONS` in `src/config.ts`), so a misspelled lever such as `?hairframe=2` cannot silently run
the default, and ends with "Address options: …" (the raw query, truncated at 240 characters) and the build stamp, so a
screenshot shows exactly what the device was asked to run. Only unknown keys are named: a known key whose value is out
of range falls back to its default silently, so read the settings line for the value that actually ran.

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
second frame at 24-28 fps; on the CPU that penalty was gone (3-16 %). Every 10 s window from 45 s on, across every run
of each configuration: every second frame with the CPU face landmarker 28.5-30.1 fps (2 runs, 199 s and 97 s; the longer
one held 29.9-30.2 to 169 s, the shorter read 29.5 at 89 s and 28.7 at 99 s; frame age median/p95 30/43 ms rising to
66/75 ms); every frame with the CPU face landmarker 23.0-27.7 (1 run, 168 s); every second frame with the GPU face
landmarker 21.8-28.2 (4 runs); every frame with the GPU face landmarker 21.1-26.7 (7 runs). Late in the 199 s run the
hair jobs slow (round trip 32 -> 78 ms) and the drawn masks age from 61 to 102 ms (p95 143 ms); the owner judged the
hair edge on the phone perfect. 14 runs that morning, 21 minutes of camera time, across three builds: rests not matched,
and the GPU-face runs span all three builds while the CPU-face runs are on the last one. Every run was in light that held
the camera at 29-30 fps, so the dim-light regime below, where the camera itself drops, is unmeasured on the phone.
Android and Android tablets take the same defaults unmeasured
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
   falls back to the canvas. Where the browser provides `requestVideoFrameCallback`, every row carries the camera's
   presented-frame counter, so the delivered rate is known; without it the pipeline falls back to
   `requestAnimationFrame` and a changed `currentTime`, and no camera rate is available (that fallback is unmeasured). That counter is also what identifies a frame (`frame-identity.ts`); `currentTime` is never compared
   across a draw, because WebKit reports a running clock for a camera stream and such a comparison discarded every frame
   on iPhone.
2. **Inference**: the face landmarker (`src/face/`, MediaPipe FaceLandmarker in a worker, CPU delegate by default) sees a
   640 px copy; the hair segmenter (`src/hair/`, MediaPipe ImageSegmenter in a worker, category mask only) sees a 640 px
   copy too (`?hairinput=`; the mask readback was a quarter of the hair worker's time at frame size); the mask is read by
   nearest lookup in the shader, the cut and the CPU reference. On laptops (and with `?hairframes=1`) every frame waits for
   its own mask (`HAIR_WAIT_MS` is only a guard against a stalled worker): a frame is never drawn without hair while its
   mask is on its way. On phones and tablets (and with `?hairframes=2`) the hair segmenter runs on every second frame and
   no frame waits: a frame without its own mask draws the newest mask moved with the head, if it is at most
   `?hairmaxage=` ms old, and is otherwise drawn without hair. The SHA-256 ties a mask to the image it was computed
   from, so a reused mask carries that earlier frame's hash: the pairing identifies the mask's own image, not the drawn
   one, which is why the audit's CPU reference composes only a frame's own mask. The frame pump (`src/pipeline/frame-pump.ts`) overlaps the next frame's inference with the current frame's
   preparation, with at most two owned frames and one serial hair worker.
3. **Pose** (`src/render/renderer.ts`, `pose`): waits for the previous frame's GPU fence (at most 1 s; three unanswered
   fences in a row switch the gate off for the session and the live panel says so), then the observed face surface
   (`face-surface.ts`) shaped by the nasal shape (`nasal-shape.ts`), both on this frame's raw detector pose; then pose
   steadiness (`pose-stabilizer.ts`: One Euro smoothing of the detector's orientation and depth), whose steadied pose
   drives the bridge pose (`bridge-pose.ts`), the
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
  the GPU output. Lossless images and difference maps are included; "Download audit" saves it. With hair on every second
  frame the audit starts a hair job on the first frame that finds the worker idle and that frame waits for its own mask
  (at most `?hairwait=` ms), passing over up to 90 frames that draw a reused mask; after that it audits a reused-mask
  frame, where the four protection checks still run but the CPU reference compose and the comparison against it are
  skipped, and the audit line says so.

What is not byte-exact G: the hair edge is a hard step at the mask's nearest texel rather than the two-pixel CPU
feather; the continuity cut replaces G's after-the-fact removal of detached remnants; lens transmission sees the dropped
temples.

## How much of a temple arm is given up to the head (`?temples=`)

An arm of the shipped frames runs 6 to 12 mm *inside* the canonical head's own silhouette — the assets are narrower
than the head they are worn on — so a plain depth test buries an arm that a wearer expects to see. Something has to
decide how much of each arm to give back. Until 2026-09-18 that decision was made from the head's angles, per side,
for the whole arm at once; it is now made per pixel from the head's own depth. `?temples=angles` restores the former
rule and the page's "04 / TEMPLE OCCLUSION" selector switches between them inside a live camera session.

**v4, `depth`, the default.** The head-depth pass the module already renders is read twice per arm fragment: the head's
depth under that pixel and the fragment's own. Their difference is how far behind the head surface that piece of arm
sits. A fragment less than 0.6 cm behind is drawn in full; one more than 2.6 cm behind is given up; between them it
fades. Nothing about the head's yaw, pitch, roll or the camera's bearing enters the decision, so it follows head
movement continuously, by construction. The pitch-driven dissolve of v3 is switched off in this mode: a fragment that
is genuinely behind the head is already culled by the head's own depth, and the overlay is what puts back the part that
is only just behind it. The 0.6 and 2.6 cm are visual choices, not measured anatomy.

**v3, `angles`, what it replaced.** Two per-side percentages, `1 - smoothstep(viewX, 0, 0.35)`, multiplied by a
confidence `smoothstep(min(camera bearing, head heading), 0.15, 0.35)`, plus a separate dissolve
`(1 - confidence) x smoothstep(pitch, sin 8°, sin 18°)` that mixes the arm into the camera image. Computed from the
shipped module over a head at 45 cm, the near arm's share of relief is **exactly 0 below 8.6° of yaw**, ramps to 1 over
the next 12° (0.1 of the arm per degree), and is then flat. The two mechanisms are not independent: `wrapFrontal` is
applied to the original materials *before* the overlay clones are made, so the dissolve erases the overlay's own pixels
too. Their product — what a wearer actually sees over the side of the head — reads:

| | yaw 0° | 10° | 15° | 20° | 25° |
|---|---|---|---|---|---|
| pitch 0°, upright | 0.00 | 0.04 | 0.57 | 1.00 | 1.00 |
| pitch 20°, chin up | 0.00 | 0.00 | 0.20 | 0.89 | 1.00 |
| roll 45°, lying down | 0.00 | 0.00 | 0.07 | 0.44 | 0.84 |
| roll 45° and pitch 15° | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| roll 70° | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Roll is the sharpest of these and it is not an edge case: the camera's bearing is taken in head coordinates, where it
scales with cos(roll), so lying down with a phone overhead takes away relief that an upright head at the same turn
gets in full. On a real camera at 50 cm, yaw −11°, pitch +13° — an ordinary selfie pose — the rule keeps 2–4 % of each
arm and dissolves 46 % of what is left. The geometry errors above are independent of the rule and applied to both.

**Two geometry errors found underneath it (2026-09-18, second pass).** Once v4 stopped erasing the arms, what was left
was an arm ending in mid-air short of the ear, on both sides, at a place that moved with the pose:

- **The arm was cut where its ear hook begins.** Both shipped assets carry the hook: on Amber Horizon the stem runs to
  local z −0.1464 and from −0.105 back it curves down to y 0.4 cm and inward to |x| 4.9 cm. The clip endpoint was
  −0.105, so what was drawn was the straight shaft alone, ending about 4 cm short of the ear and 2.5 cm above it, with
  the terminal 1.5 cm dissolved into the camera. The endpoints are now −0.140 and −0.148, just inside each asset's own
  arm end, so the hook is drawn and the head is what takes it away.
- **The head occluder was smaller than the head.** It was an ellipsoid of half (6.3, 8, 5) cm at z −2.5 — 1.4 cm
  *narrower than the canonical face mesh's own silhouette* (|x| 7.74 cm at the temple) and ending 5 cm short of a
  skull. The face mesh itself stops at z −2.44, so behind that the head's flanks and the whole ear had no occluder at
  all: nothing could hide an arm there, and its ending could only come from a cut in mesh space, which lands at a
  different screen place on each side as the head moves. It is now half (7.4, 9.5, 7.5) at z −3.5, fitted to the
  canonical mesh: it holds that mesh's own temple (f 1.22) and tragus (f 1.20) outside itself so it never reaches past
  the real silhouette, keeps the straight shaft outside it as far back as the ear, contains the ear hook (f 0.69–0.81),
  and its front face at z +3.99 stays well behind the frame at +6.53. A visual choice fitted to the canonical mesh,
  not a measured skull.

**The band is a lever, not a constant.** How much arm survives is decided by one pair of numbers and nothing else:
`?templekeep=` (0.6 cm, drawn whole up to here) and `?templedrop=` (2.6 cm, gone beyond here). Raise them to see more
arm alongside the head, lower them to tuck it away sooner; a pair that is not a band falls back to both defaults. Both
ends are in every timing row and every audit, so a judgement can be tied to the numbers that produced it.

**Bending the arms outward at the hinge (`?templebend=`, mm; on by default).** A temple that sits too close to the head
is given up by any occlusion rule, because it really is behind the head. The lever that changes that is the frame, not
the rule: `?templebend=14` — the default — splays each arm outward by 14 mm **at its tip**. The bridge, rims, lenses and
endpieces do not move at all; the arm pivots at the hinge and runs **straight** back from there, at a constant angle,
exactly as an optician's temple adjustment does. Negative pulls the arms in, and `?templebend=0` is the frame as
authored. It is written by the same single pass over the cloned arm buffers as the rear drop and the width fit, so all
three compose; the projected arm centrelines and the stencil's editable corridor follow it, so the hair cut still walks
the bent arm. Range ±24 mm, which is the arms' own inward curl — at the cap the bend has straightened the arm and it
runs back parallel to the frame front. The automatic width fit adds to the bend and the pair is capped there.

**Where the bend pivots (`?templepivot=`, mm).** The pivot is not a constant: it is read out of each asset's own
cross-section, by walking back from the front and taking the first millimetre of the lateral band the bend may move
(|x| > 4.5 cm) whose vertical extent has collapsed to a bar and stays collapsed for 20 mm — rims and endpieces are tall
there, a shaft is not. That lands at z −0.015 on Amber Horizon (4.3 mm behind its lens rear) and −0.014 on Tom Ford
(0.4 mm *in front of* its lens rear, which is why the plane is measured rather than offset from the lens). Until
2026-09-18 the bend pivoted at the rear drop's own start plane, a centimetre further back, along a smoothstep spread
over the whole arm — which put the arm's widest bulge about 5 cm behind the lens, nowhere near a hinge, and read as a
warped frame rather than an adjusted one. `?templepivot=` moves the pivot back along the shaft (0–30 mm); it cannot move
forward, because in front of the hinge the same lateral band is the rim. The first 6 mm of the ramp is rounded, so the
pivot is a hinge radius in the mesh and not a crease, and the rear drop keeps its own start plane, because it shears the
whole arm rather than bending it.

**Why 14 mm.** Measured against the occluder this page draws — the canonical face mesh in front, the ellipsoid head
proxy behind, whichever is wider at that depth — the authored arm runs *inside* the head over its whole rear half, and
the deepest point is not the tip. It is 8.6 cm behind the frame front, where the face mesh reaches its own widest
(7.74 cm half-width) and the arm has already tapered to 6.9 cm:

| arm station (local z, m) | −0.046 | −0.056 | −0.066 | −0.076 | **−0.086** | −0.106 | −0.126 | −0.136 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| arm's outer surface (cm) | 7.15 | 7.12 | 7.07 | 6.99 | 6.89 | 6.60 | 5.92 | 5.49 |
| occluder there (cm) | 6.41 | 6.85 | 7.21 | 7.56 | **7.74** | 7.38 | 6.96 | 6.52 |
| arm is inside the head by (mm) | — | — | 1.5 | 5.6 | **8.6** | 7.8 | 10.4 | 10.3 |

A straight shaft from the hinge has delivered 56 % of the tip's offset by that station, so 14 mm leaves it 0.8 mm
inside the head and puts the tip 3.7 mm proud of the proxy — which has no ear on it, and the tip is the ear hook. 10 mm
leaves the tip flush and that station 3.0 mm buried; 16 mm clears the whole arm. The bend, the pivot option and the
plane the bend actually pivots about are in every timing row (`render.templeBendM`, `render.templePivotM`,
`render.armSpreadStartZM`, `render.armSpreadTotalM`) and every audit.

Those depths are measured against a proxy head, not against a wearer. What they explain is why the millimetres were a
knife edge on a live session while the ramp was a smoothstep over the whole arm: 8 mm read as too little, 12 mm as
slightly worse than 10 (it puts the tip 2.1 mm proud of the head), and 10 mm left the one station that decides still
3.3 mm buried. The shape was the variable, not the millimetres.

**What is not established.** That v4 looks better. The checked-in fixture's head is never turned far enough for an arm
to be in front of the head — at every pose that can be synthesised from it the arms are hidden by the frame's own rims
and lenses, and the two rules render within 84 pixels of each other. The case the wearer reported (an arm ending in
mid-air at a near profile, lying down) cannot be reproduced from a still photograph, because rolling the drawn image
changes roll but not yaw. v4 is argued from the numbers above and from the geometry; it is the wearer's A/B that
decides it. Under the synthetic harness the guarded render, the four protection checks, the GPU-against-CPU-reference
comparison and the continuity cut are unchanged in either mode.

## Temple fit: an experiment beside the pipeline (`?fit=width`)

Off by default. The pipeline above is `?fit=original` and is unchanged; the selector "04 / TEMPLE FIT" on the page
switches between the two inside a live camera session, and only one of them runs at a time. The question it asks is
visual: do the arms sit better alongside a face, and disappear behind it better, when the head occluder and the
posterior arm spread follow how wide this wearer's face is?

**The estimate** (`src/render/face-width.ts`). Per tracked frame, the reconstructed camera-space surface
(`face-surface.ts`, before the nasal shape) is carried back through the frame's raw detector pose. That pose is a
similarity transform whose rotation, translation and fitted scale are exactly the head rotation and the camera
distance, so what is left is the face in the canonical frame, where a lateral span can be compared with the canonical
face's own span. It is not a screen-space width and not a division by cos(yaw). Five left/right region pairs are read,
each the average of two or three neighbouring landmarks, so no single "temple" landmark decides anything:
temple-upper (21,162), temple-side (127,234), temple-front (227,137), eye-outer (33,130,226) and eye-lateral (143,156),
with their mirrors. Each pair gives its own ratio and the median of the five is the observation.

An observation is refused, not repaired, when the head is turned more than 12°, pitched more than 15° or rolled more
than 12°; when a landmark it needs is missing or non-finite; when a pair's centre sits more than 12 % of its own span
off the head's midline (a turned or partly occluded face rather than a wider one); when the five regions spread more
than 0.20 apart; or when the median is outside 0.80–1.20. MediaPipe publishes no per-landmark confidence and none is
invented here.

The five regions do not agree, and that is the face rather than the frame: on the checked-in fixture face, read
through the shipped modules undistorted, they are 0.976 / 1.060 / 1.047 / 0.945 / 0.965 — a spread of 0.115. The same
face stretched 1.78x (see Harness) spreads 0.296, which no similarity pose can absorb, and is refused. That is where
the 0.20 bound comes from: two observations, and it is a sanity bound, not a quality test. The single ratio is only a
summary of that profile; the profile itself is recorded per audit (`widthFit.regionRatios`).

**Holding it steady.** Accepted observations go into a 120-sample window; nothing is applied until 30 of them exist, so
a session starts on the original geometry. The applied ratio then leaves 1 slowly (5 % of the distance to the window's
median per accepted observation) and is clamped to 0.92–1.08. Because only near-frontal observations are accepted, a
turn holds the estimate rather than moving it, as does a brief tracking failure; three continuous seconds without a
tracked face drop it back to the original geometry, and it collects again (the state then reads `fallback`). A new
camera session starts from nothing. There is no scan and no user step.

**What it changes.** Two things, both bounded and reversible. The invisible rear head occluder's half-width is scaled
by the ratio (6.3 cm at ratio 1; its height, depth and placement are untouched). And the posterior opaque arm shafts
are spread laterally by `(ratio − 1) × 0.07 m` per arm, capped at ±6 mm, which the ratio bound holds to ±5.6 mm, along
the same smoothstep ramp the rear drop uses: zero at the arm's start plane, so the bridge, rims, lenses and the hinge
attachment do not move, and full at the clip cap. Nothing else about the model is scaled. **0.07 m is half the
canonical face width and the ramp is the drop's: both are visual choices, not measured anatomy.**

The spread is written by the same pass that writes the rear drop (`rear-drop.ts` owns the cloned buffers and restores
the original storage before every change), so the two compose instead of overwriting each other, and spread 0 restores
the shipped geometry exactly. The same lateral function moves the projected arm centrelines, so the continuity cut
still walks along the arm and the hair cut follows the fitted arms (`continuity.ts`); the protection corridor is built
from the fitted arm bounds, so the stencil's editable region grows with them while the optical rectangle does not move.
A fitted point never crosses the 0.045 m lateral plane that the fixed temple rules test, so temple visibility,
the continuity stations and the rear drop's own eligibility classify it exactly as before.

Every timing row carries `render.widthFit`, `render.widthFitState`, `render.widthRatio` and `render.armSpreadM`, and
every audit carries the same under `widthFit`, so two comparisons stay interpretable. The page's own line reads
"Temple fit: … · ratio … · collecting | stable | fallback".

**What this is not.** No wearer has judged it yet. There is no evidence that it looks better than `?fit=original` on
any face; the synthetic checks below are regression evidence only.

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
| `?hairdelegate=` | probe; Apple phones and tablets `cpu` (an iPad asking for the desktop site is still recognised) | `cpu` or `gpu` forces the hair segmenter's delegate; on the iPhone the CPU has no mask readback (2026-09-15, face landmarker then on the GPU: 29 fps for 40 s, 2-3 fps ahead at 30-60 s; kept with the CPU face landmarker of 2026-09-17, GPU hair with it unmeasured) |
| `?hairwait=` | 120 | guard in ms after which a frame is drawn without its hair mask; read where frames wait for their own mask (hair on every frame, and the audited frame with hair on every second frame; measurement lever) |
| `?diag=1` | off | send the startup step log and live stage medians (numbers only) to this site, readable at `/ar/diagnostics.json` |
| `?model=&name=&clip=&width=&sha256=` | | a Modeling Auto handover (`src/eyewear/external.ts`); `clip` is the temple clip depth in the asset's local metres, accepted from -0.2 to -0.03 m and then drawn no nearer than -0.045 m (`TEMPLE_CLIP_NEAREST_LOCAL_Z_M` in `src/eyewear/catalog.ts`), the length the v3 end blend itself needs |
| `?steady=0` | on | turns off pose steadiness: the glasses' orientation and depth are smoothed over time before the bridge pin (`src/render/pose-stabilizer.ts`), so the image-plane position still follows each frame's nose landmarks. The live panel's "Pose shake" line reads raw vs steadied shake and the trailing angle from the timing rows. Synthetic: shake to 0.15× at rest, 2.3° trailing on a ±20° 0.5 Hz turn. Accepted by the owner live on the laptop, 2026-09-17; phones not yet judged |
| `?hairframes=` | laptops `1`; phones and tablets `2` | `1`: every frame waits for its own hair mask. `2`: runs the hair segmenter on every second frame (`src/hair/mask-reuse.ts`); no frame waits for hair, and a frame whose own mask is not ready draws the newest mask of another frame moved by the head's motion (a 2D shift/turn/scale fitted to 24 skull landmarks, applied as one 3×3 matrix on the mask lookup and to the continuity cut). The live panel's "Hair masks" line (also in the `?diag=1` hair reports) counts own / reused / missing masks, reuse age and head motion. Owner's laptop look 2026-09-17: acceptable. Other schedules looked at the same day (a held unmoved mask, hair whenever the worker is free, every 3rd/4th frame) were not, and are not in the code: `1` and `2` are the only values. Phone default since 2026-09-17 (see Phones) |
| `?hairmove=` | 8 | with hair on every second frame (phone and tablet default, `?hairframes=2`): start a mask early when the head moved more than this many px since the newest mask's frame; `0` never early |
| `?hairmaxage=` | 200 | with hair on every second frame (phone and tablet default, `?hairframes=2`): never draw a mask whose frame was captured more than this many ms from the drawn frame (30..1000) |
| `?steadyhz=`, `?steadybeta=` | 1, 0.1 | rotation cutoff at rest (Hz, 0.05..20) and added Hz per °/s of head rotation (0..5); lower `steadyhz` is steadier, higher `steadybeta` follows turns more closely |
| `?steadydepthhz=`, `?steadydepthbeta=` | 1, 0.2 | the same for depth (Hz, and Hz per cm/s) |
| `?templebend=` | 14 | millimetres to splay each temple arm outward at its tip, pivoting at the hinge and straight from there (above). ±24 mm, the arms' own inward curl; negative pulls them in, `0` is the frame as authored. Adds to the width fit's own spread, and the pair is capped at 24 mm |
| `?templepivot=` | 0 | millimetres to move the bend's pivot back from the asset's own hinge (above). 0..30; it cannot move forward, because in front of the hinge the same lateral band is the rim |
| `?templekeep=`, `?templedrop=` | 0.6, 2.6 | with `?temples=depth`, the band in centimetres behind the head surface: an arm fragment up to `templekeep` behind it is drawn whole, one beyond `templedrop` is given up, fading between. The only numbers that decide how much of an arm survives. A pair that is not a band (or either end out of range) falls back to both defaults |
| `?temples=` | `depth` | which rule gives up part of a temple arm to the head (above). `depth` decides per pixel from the head's own depth; `angles` restores the per-side percentages computed from the head's yaw, pitch and the camera bearing. Any other value is `depth`. The page's selector switches modes inside a live session |
| `?fit=` | `original` | `width` runs the experimental face-width fit (above) instead: the head occluder's width and the posterior arm spread follow a stable width ratio. Any other value is `original`. The page's selector switches modes inside a live session, so this only chooses the mode a session starts in |

## Read the camera before judging any fps figure

The live panel prints the camera's delivered rate next to the pipeline's. The reference laptop's webcam auto-exposes at
1/16 s in dim light and then delivers 15 fps whatever the page does, which caps every pipeline at 15. `qa/camera-rate.mjs
<export.json>` prints the delivered rate of any timing export; `qa/camera-probe.mjs` measures the real camera under
several constraint sets (`--sweep` for manual exposures).

## Harness

`qa/measure.mjs` drives the page's own measurement with a synthetic 30 fps camera (the checked-in `qa/fixtures/face-a.jpg`
with slow drift), saves a screenshot of the stage and audits one frame, writing everything under `qa/output/`.
Controlled-input evidence only: not a real camera, wearer motion, phone or thermal evidence.

Historic baseline, superseded by later defaults; no current synthetic baseline is recorded. First run on the frozen
build, 2026-09-15 (this laptop, 8 s warmup + 20 s; before the VideoFrame capture, pose steadiness and the phone schedule
became defaults, so rerun `npm run measure` for current figures): 22.2 fps against a synthetic camera that delivered
27.3 fps in that run, age median/p95 71/99 ms, 446/446 tracked, 443 masked; prepare 10 ms (GPU wait 1.1, pose 5.4),
finish 7 ms (submit 5.8); audit: guard on (2 protected / 2 editable rectangles), all four checks pass, GPU output
identical to the CPU reference compose plus continuity, drop intrusion 0 px over Δ8 (max 4), cut −26 mm on both arms.

`npm run measure -- --fit=width` runs the width-fit experiment. The estimate does not engage under this harness: the
fixture is a 1024x1024 image drawn into a 1280x720 camera, which stretches the face 1.78x, and the width fit refuses
every such observation as inconsistent (0.296 region spread, against 0.20). What the harness does cover in that mode is
the rest of the path — the audit's four protection checks, the GPU output against the CPU reference and the continuity
cut all run with the fit selected. Two sessions each on this laptop, 2026-09-18, 8 s warmup + 20 s: `original` 24.58 and
24.70 fps, `width` 25.56 and 27.02 fps (an earlier pair read 27.28 and 25.34 the other way round), so at 1280x720 the
observation costs nothing this harness can resolve — and it measures the observation only, never the applied geometry.
Driving the estimate needs an undistorted camera; that was checked once outside the repository with a square 720x720
draw of the same fixture (state `stable`, ratio 0.974, arm spread −1.8 mm, 357 accepted observations, all four checks
passing in both modes, and the live switch back to Original restoring ratio 1 and spread 0 exactly).

There is one fixture (`qa/fixtures/face-a.jpg`), and its hair never covers the arms: the audit runs with hair applied and
still reports zero differing pixels before against after the blend, so the harness exercises the blend without testing
it; only a real face does that. `npm run measure -- --hairframes=2` drives the phone and tablet default, but no such run
has been recorded: the reuse-and-warp path is covered by unit tests and by the owner's phone sessions only.

## What is measured, and what is not

Measured on a real camera: one iPhone 17 Pro (iOS 18.7, Safari 26.6.1), 14 runs on 2026-09-17, 21 minutes of camera
time, longest run 199 s, camera 29-30 fps throughout, 720x1280, one wearer, one room; and this laptop (Intel Arc 140T,
Chrome, real webcam) on 2026-09-15/16, whose runs are not in this repository. Judged by eye by the owner: the hair edge
and the pose steadiness on both devices.

Not measured at all: whether the per-pixel temple occlusion (`?temples=depth`, the default since 2026-09-18) looks
better than the angle rule it replaced on any wearer — see that section; the width-fit experiment on any wearer
(`?fit=width`, 2026-09-18) — it has unit tests, one
end-to-end browser check on a synthetic square fixture, and no visual judgement by anyone;
Android phones and tablets (they take the phone defaults untested), any browser other than Safari
on the phone and Chrome on the laptop, a second iPhone, a second wearer, a second lighting condition (including the
dim-light regime where the camera itself drops), any session beyond 199 s, device temperature or battery state
(throttling is inferred from the fps and age curves alone), the `?hairframes=2` path under the synthetic harness, and
any automated visual regression. The synthetic harness is controlled input only: one fixture, no wearer motion, no
thermal behaviour.

## Layout

```
index.html, src/main.ts, src/config.ts, src/style.css   the page, its URL options and its style
src/assets.ts    every asset address under Vite's base (src/env.d.ts declares the build time the page shows)
src/camera/      openCamera, the exposure lock
src/face/        landmarker protocol, timing sidechannel, client, worker
src/hair/        pinned models, protocol (category-only), client, worker, backend probe, mask reuse (every 2nd frame)
src/eyewear/     the frame catalog with the Modeling Auto slot, the URL handover
src/pipeline/    capture (VideoFrame or canvas), frame identity, frame pump, the pipeline, the profiler, and the pose-shake
                 (steadiness.ts) and hair-schedule / face-draw-overlap (hair-report.ts) readings of the timing rows
src/render/      the renderer and its geometry and shader modules, pose steadiness (pose-stabilizer.ts), the
                 experimental face-width fit (face-width.ts, `?fit=width`)
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
