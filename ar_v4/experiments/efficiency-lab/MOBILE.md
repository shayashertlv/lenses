# Mobile AR testing

The Lenses landing page links to `/ar_testing/`, a separate static AR preview
served by the existing Python application. The Railway start command remains
`python -m UI.app`. The page begins with the accepted G renderer and offers the
independent experiments. No candidate is promoted by this page.

The current **G / V / W / X** testing ground is:
`/ar_testing/experiments/efficiency-lab/live.html?study=per-image`.
V tests full-resolution RGBA8 hair-mask retrieval, W avoids four repeated PACK
state queries within one owned image, and X compares four-channel pixels as
32-bit words during the first composition scan. All retain G's admission,
resolution, geometry and final guards. Each changes one mechanism independently.
G is selected initially. The owner rejected U as worse than G; U remains a
separate historical experiment and is not included in this new comparison.

For an immediate visual comparison, open the camera and switch G/V/W/X while
moving. For a measured run, choose **Measure only · G / V / W / X · ~5 min**.
The order is G/V/W/X, then X/W/V/G, with the same five-second/three-masked-frame
warmup and 30-second measurement in each window. Follow front/nose, down, up,
left and right cues. Repeat with Amber Horizon and Tom Ford Clear, each using
hair-only and selfie-multiclass. Save each ZIP before starting another run.
Enable video for separate visual runs; optional recording has the same behavior
and local-only export described below. Keep workload, lighting and movements
similar across options and allow the phone to cool between repeated runs.

This study also records the all-request `hairDelivery` ledger and drains late
results before export. Actual extraction paths distinguish `rgba8-readback`
from an explicit safe SDK fallback, including its reason and readback bytes.
W reports actual query savings and X reports actual word comparisons. A requested
option or successful fallback alone is not evidence that its optimization ran.
Compare completed AR updates, frame age, stalls, tracking and matching-mask
availability together; camera FPS alone cannot select a winner.

The **G vs U · hair processing** link opens
`/ar_testing/experiments/efficiency-lab/live.html?study=hair-delivery`.
This focused preview compares G with earlier hair-worker reuse, using G/U/U/G
and the same five-second/three-masked-frame warmup plus 30-second measurement
windows. Four windows take about 2.5 minutes. Start with measurements only
(the focused default), then enable video for a separate visual run. The earlier
five-option preview retains its video-on default. Every run keeps glasses, hair
model and resolution fixed and repeats front/nose, down, up, left and right cues.

Focused exports additionally contain `hairDelivery`: up to 10,000 scalar request
records, including results that missed publication. Each has its session,
generation, sequence, pipeline, capture/publication/disposal times and validated
request timing. `usedAtPublication` distinguishes a completed but unused mask
from one actually attached to its own displayed image. No image/mask hashes,
pixels or detections enter this log. Overflow/rejected observations are explicit;
existing entries may still finish after the retention limit is reached.

At focused completion, optional video stops immediately. The pump then drains
all pending results before the archive is made; these late observations do not
extend measured windows. Successful export resumes the manual mirror. A separate
20-second finalization watchdog closes a stranded session and exports an explicit
`hairDeliveryDrain.incomplete` diagnostic with any unresolved requests. Existing
15-second hair deadlines are unchanged. Stop/cancel cannot restart a closed camera.

## Record a comparison

Open the HTTPS site on the phone, select glasses and a hair model, open the camera
and wait for tracking and hair. Choose **Video + measurements · all five options**. Video is on
by default and records the displayed AR canvas without audio. Every window asks
for the same front/nose, down, up, left and right movements.

The order is G/Q/R/S/T, then T/S/R/Q/G. Each window has at least five seconds of
warmup and three tracked frames with matching hair masks, followed by a fixed
30-second measurement window. Ten windows take approximately six minutes. The
glasses, hair setting and source dimensions remain fixed for that run. Repeat
for the other glasses/hair combinations before making a general quality claim.

At completion the page attempts one download of a ZIP containing `telemetry.json`
and `ar-mirror.webm` or `ar-mirror.mp4`. **Save comparison file** and supported
mobile **Share** remain available if automatic download is blocked. Starting a
new test replaces the result retained by the page; save it first. Files stay in
the browser until the user saves or shares them. Nothing is uploaded to Railway.

Stop, camera failure, changing workload, backgrounding and recording/retention
limits preserve a marked partial result. The page requests a screen wake lock
when supported. Keep it visible; a background interval is not a valid benchmark.

## Read the result

If the mirror remains on **Opening**, the startup panel identifies the current
step and elapsed time. **Save startup report** works before the first image,
including after a failure or cancellation; a text-copy fallback is also provided.
The small JSON contains loading milestones, browser capabilities, the settings
requested when the camera opened, bounded same-origin resource timings, and
scalar capture/inference/preparation counters. Those counters and video state
freeze at the first publication or before failure/cancellation cleanup; later
experiment switches cannot relabel startup. It contains no camera images,
landmarks, masks, camera device IDs or request/response contents.

After the camera opens, module loading and each renderer setup have a 60-second
deadline, face startup has an outer 100-second deadline, and the first AR image
has 30 seconds. Setup as a whole is capped at 240 seconds. The face worker's
existing 45-second attempts and camera permission/playback deadlines remain in
force. A timeout closes the owned session and permits a fresh attempt; late
results cannot restart it. These timers can act only when the browser's event
loop runs; they cannot forcibly interrupt a synchronous GPU/driver stall.

The report retains each completed AR frame's scalar timing and actual experiment
counters, independent camera-delivery observations, exact window/switch boundaries,
startup timings, tracking/mask coverage, and browser/camera/build metadata.
Completed-update FPS uses the entire measurement duration, including zero-output
periods. Gap distributions include the start and end of each window. Camera
delivery FPS reports its observed span separately. Frame age ends at canvas
submission; it cannot establish sensor buffering or physical display latency.

There is no 4,096-frame ring-buffer truncation: independent run retention holds
up to 30,000 observations of each kind and stops explicitly at its limit. Pixels,
landmarks, masks and image identity hashes are excluded from timing rows. The
optional video contains the visible camera image; its page-clock start/end and
encoding settings are included. Encoder timing is approximate and video FPS is
not completed AR update FPS. Video adds CPU/GPU/encoding/memory load across all
windows. Repeat with video off to assess recording overhead. The video limit is
128 MiB; reaching it preserves a partial run rather than growing indefinitely.

Compare both rounds' update rate, frame-age tail, stalls and workload coverage,
then review the video for visual quality. A fast mode with missing tracking or
masks is not an equivalent workload. Reversed order helps expose time drift but
does not eliminate differences in movement, temperature or device conditions.
No automatic winner, mobile speedup or thermal measurement is claimed.

## Build and publish

From `ar_v4/`:

```powershell
npm ci
npm run test:efficiency
npm run build:mobile
npx playwright test --config experiments/efficiency-lab/playwright.mobile.config.ts
```

After Railway publishes that commit, verify every served file against the local
manifest and save a receipt in the ignored QA directory:

```powershell
python experiments/efficiency-lab/qa/verify-published.py --url https://web-production-ef3ca.up.railway.app --output experiments/efficiency-lab/qa/output/published-receipt.json
```

The mobile build retains the accepted source resolver and prefixes asset addresses
in its compiled main/worker modules with `/ar_testing/`. It also installs a
same-origin fetch guard before the SDK initializes in each worker. Original G
source files are not rewritten. Only this page, its required JS/CSS, verified models,
MediaPipe runtime and licenses enter `ar_v4/mobile-site/`. Public model originals
and private research directories are never copied wholesale. A generated
`public-manifest.json` records every served file's size/SHA-256; `release.json`
exposes a source fingerprint and build time also embedded in measurement exports.

Commit the explicit generated `mobile-site` package alongside its reviewed source
and the Python route. Railway's existing Python deployment serves these files
without adding a Node build or changing the parent app's start command. Deploy
only from a clean reviewed checkout; never upload the development workspace,
recordings, recovery archives or unrelated model-studio work.

The Python handler serves only the manifest's public files, verifies their hashes,
streams bounded reads, implements GET/HEAD, and scopes cross-origin isolation
and enforcing Content Security Policy headers to AR documents and worker scripts.
Private names, traversal, symlinks and unlisted files are rejected.
The manifest itself is not served. Model identities are still checked by the AR
runtime. Existing Lenses routes keep their previous headers and behavior.

The pinned MediaPipe SDK initializes background telemetry without a supported
JavaScript opt-out. The mobile guard rejects external SDK fetches before the
native network call; transport failures follow the SDK's existing error path.
The independent CSP allows only same-origin connections and local blob textures,
including in workers. WASM execution is permitted without JavaScript eval.
No camera frames, recordings or telemetry are sent to external hosts. The
continuous browser check retains a zero-external-request assertion; a separate
short check verifies both SDK guards and CSP against a harmless local sentinel.

Runtime feature detection chooses supported recording containers. See
[MediaRecorder capability checks](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static)
and [WebKit's recording API](https://webkit.org/blog/11353/mediarecorder-api/).
Desktop mobile-viewport automation does not establish physical iPhone/Android
camera, smoothness or thermal performance; those require the owner's runs.
