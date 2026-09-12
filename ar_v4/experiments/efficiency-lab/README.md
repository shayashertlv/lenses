# G and the next efficiency candidates

The owner-authorized `/ar_testing/` page adds a continuous mobile comparison
and one ZIP containing timing data and optional AR video. See the
[mobile recording and publishing guide](MOBILE.md).

This separate preview keeps the unchanged G implementation as its initial
reference and adds independently selectable efficiency and processing-rate candidates. The regular
`npm run dev` / `build` / `preview` entry remains G through
`vite.combined.config.ts`. No candidate here has been promoted or visually
accepted. The original eight-way speed lab and older checkpoints remain available.

```powershell
npm run dev:efficiency
```

Open [the development comparison](http://127.0.0.1:8094/experiments/efficiency-lab/live.html).
For the production preview:

```powershell
npm run build:efficiency
npm run preview:efficiency
```

Open [the production comparison](http://127.0.0.1:8096/experiments/efficiency-lab/live.html).
Camera access starts only after Open camera. Processing stays local.

| Choice | What changes from G |
| --- | --- |
| G · Current baseline | Original G renderer/options, bounded two-image scheduling and original early prefetch. |
| H · Reuse download memory | Reuse private bottom-up readback scratch. Displayed and held output buffers remain independently owned. |
| I · Lean camera input | Reuse released capture/face-preprocessing canvases and remove an extra source-hash input copy. Pixel dimensions, resampling, source hash and detector remain unchanged. |
| J · Later inference start | Admit next-image prefetch after the current main-image render/download submission. The same two-image limit and exact image/pose/mask pairing remain. |
| K · Overlap temple downloads | Submit the nonzero rear-temple branch using copied current geometry before awaiting native beauty retrieval; retrieve through a separate bounded PBO and join both owned results. |
| L · Combined candidate | H, I, J and K enabled together. It is a candidate for comparison, not the default. |
| M · Up to 12 images/s | Original G with a maximum of 12 new camera-image captures per second. |
| N · Up to 10 images/s | The same isolated admission limit at 10 images/s. |
| O · Up to 8 images/s | The same isolated admission limit at 8 images/s. |
| P · Lean hair-mask extraction | G scheduling and rendering, with an exact direct float-to-category conversion that avoids SDK conversion temporaries when the mask is not already bytes. No added rate cap. |
| Q · Skip repeat image upload | Skip the redundant pre-prepare publication after the same pump has already displayed an image and the view is unchanged. New images and explicit toggles still publish. |
| R · Smaller temple download | Draw the complete temple branch, then download only its consumed row band. Rendering resolution, projection and final protections remain fixed. |
| S · Lean temple render | Omit lens materials only from the additional temple-branch draw, after classification. The main beauty render, geometry and protection bounds remain unchanged. |
| T · Lighter statistics | Refresh repeated human-facing statistics every 500 ms and avoid unchanged text writes while retaining every profiling sample and immediate state/control transitions. |
| U · Earlier hair processing | Release the serial hair worker after its matched reply so the next owned image can start while the previous result finishes client validation/hashing. Publication still waits for the validated, hashed result for that exact image. Retain at most two source images, including late results. |
| V · Byte-format hair download | Convert the callback-local GPU category texture into a full-size RGBA8 target, retrieve four bytes per pixel and retain the exact SDK category conversion. Unsupported paths explicitly fall back to the SDK. |
| W · Fewer graphics queries | Reuse four PACK values between PBO submission and retrieval for the same exclusively owned native image. Query dynamic bindings and retain all error, fence and cancellation checks. |
| X · Faster pixel comparisons | Use aligned Uint32 equality for the first accepted/background RGBA residual scan; keep byte fallback, all pixel arithmetic and final guards. |

### Current G/V/W/X testing ground

Use `live.html?study=per-image`. The page starts G and offers only G/V/W/X,
with manual switching and eight equal timed windows: G/V/W/X/X/W/V/G.
Measurements are on and video off by default. Each candidate keeps G's admission
and resolution and changes only its named mechanism. The scalar all-request hair
ledger includes late extraction paths/costs; W and X report actual mechanism use.
See [MOBILE.md](MOBILE.md) for the phone protocol and export.

The owner tried U and reported it worse than G on September 12. U is rejected
for promotion and is excluded from this new study. No V/W/X speed benefit or
physical-phone visual acceptance is implied by desktop tests. Their assessment
must include both glasses, both hair models, down/up/both yaw, front/nose and
hair continuity, with completed updates and matching-mask coverage considered
together. V's held image uses the same retained SDK mask as G; its independent
callback-local GPU differential checks are separate evidence for extraction.

### Focused G/U preview

Use `live.html?study=hair-delivery` for G and U, including a four-window
G/U/U/G comparison with measurements only by default. Optional video uses the
same recorder as the earlier review. See [MOBILE.md](MOBILE.md) for the protocol.
U uses the accepted G renderer directly, with unchanged model, resolution,
source/detection/pose/mask checks and the existing eight-millisecond mask wait.
Its two-image cap includes images already published while their late hair result
is still being validated. A third image is rejected before capture or inference.

The September 12 phone recording found no Q–T winner: Q exceeded G by one
completed update over 60 measured seconds, with substantially less mask coverage.
G completed 13.87 AR updates/second while camera delivery remained about 28–29
FPS. Hair timings were previously visible only for results available at
publication. That censored sample cannot measure the complete hair-request tail.

The focused run now retains scalar timings for every submitted hair request,
including late, cancelled and failed results, alongside its eventual publication
disposition. Both G and U receive identical instrumentation. Worker-local fields
are durations; main-page timestamps identify actual submission, receipt,
validation and hash completion. A resolved admission promise is not evidence of
simultaneous GPU execution. Client validation still runs synchronously on the
main thread; the next submission can run only once that work yields to hashing.
Compare the next request's actual submission with the prior hash completion to
measure this overlap. U's benefit is unmeasured; earlier work may contend
with rendering, and stricter lifetime backpressure may reduce update throughput.
Preserve G until phone measurements and visual review justify any promotion.

### Review experiments Q–T

[Open G and the four review experiments](http://127.0.0.1:8096/experiments/efficiency-lab/live.html?study=review).
This focused link starts G and offers only G/Q/R/S/T. The ordinary lab retains
all earlier choices. Only the selected pipeline processes each live image; the
four new choices are independent, uncapped experiments. None is a promoted winner
or a promised FPS improvement.

Choose glasses and a hair model, Open camera, then switch Algorithm while moving.
Use both glasses and both hair models, down/up/both yaw, and inspect nose/front
and hair/temple transitions. Hold frame compares the exact retained image,
detection/pose and mask. Q and T share G's held result because they change live
publication or UI bookkeeping; their mechanism is verified separately in live
tests. R and S render their own held results. Resume starts a fresh session.

R retains the whole branch draw and changes readback only. Its full raw-branch
diagnostic rerenders the independently retained pair explicitly; it never assumes
the old default framebuffer still contains that image. S changes branch lens
visibility only during its final draw and restores material state afterward.
Full nose/front/outside-arm safeguards remain authoritative. First-use prewarming
and no-branch behavior are retained; branch optimizations cannot help every pose.
R/S are deliberately not combined with each other or K's asynchronous branch.

Measure G vs selected and reverse the order on a subsequent run. Download timings
only to compare completed update rate, image age, long gaps, tracking/mask coverage
and actual mechanism counters. Repeated uploads and UI summaries can be wasteful
without being the dominant bottleneck. Smaller transfers can still wait on the
GPU. Saved byte counts and asynchronous calls are not measurements of GPU time,
power or heat. Moving-wearer acceptance and physical-camera performance remain
the owner's review; mobile and thermal behavior are unmeasured.

### Cheaper work per image

[Open the focused G/P preview](http://127.0.0.1:8096/experiments/efficiency-lab/live.html?pipeline=mask&study=mask).
This link starts P and shows only G and P in Algorithm. The ordinary lab link
still starts G and retains all earlier experiments. The owner preferred G to
the reduced-rate preview, so P returns to G's uncapped admission and scheduling.

P targets category-mask extraction only. For an existing byte representation it
retains the owned byte copy. Otherwise it uses the SDK's public float getter and
converts directly into one owned byte array, preserving the intermediate float32
rounding in the installed SDK's conversion. The getter may still synchronize
with the GPU. The model, weights, source dimensions, face processing, G renderer,
image/pose/mask pairing and nose/front safeguards remain unchanged. There is no
promise of faster updates until measured, and a byte-backed mask saves no
conversion allocations through this change.

Only this lab uses the isolated instrumented hair worker/client. G in the lab
uses the original SDK category getter plus owned copy; P uses direct extraction.
Both collect the same representation and extraction timers and retain original
worker/session/pair validation. Switching freezes the extraction choice per
request without reloading a different model. Main G and its original worker are
unchanged. Scalar metrics under `native.hairCategory.*` distinguish actual byte,
float and GPU-backed routes and retrieval/conversion/copy intervals. These wall
times can include deferred GPU work and are not isolated GPU execution measures.

Hold still upgrades the exact held image through the SDK full-mask diagnostic
and shares that one source/detection/mask across renderers. P shares G's held
pixels; that UI alone does not independently verify its live extraction method.
Separate extraction QA compares direct and SDK category bytes on the same actual
MediaPipe mask before cleanup. Moving-wearer quality, sustained throughput,
mobile smoothness and thermal behavior still require physical-camera review.

Compare G/P with both glasses and both hair models, down/up/both yaw and nose,
front and hair/temple transitions. Use Measure G vs selected, repeat with reversed
order, then Download timings only. Keep camera, models, lighting and movements
consistent. Check update rate, age, long gaps and tracking/mask coverage together.

P verification uses `npm run test:efficiency` and the production browser suite.
The optional frozen angle study needs the preserved local archives:

```powershell
npm run build:efficiency
$env:AR_HAIR_EXTRACTION_FROZEN='1'
npx playwright test --config experiments/efficiency-lab/playwright.config.ts hair-cost/extraction.spec.ts
```

The same-mask study compares direct extraction before SDK extraction, so SDK
retrieval uses a cache. Its timings are not a speed comparison. September 10
results pass all 32 byte/ownership cases and all four live model combinations;
see the current review for the initial QA header failure, corrected frozen-only
run, production checks and the separate unchanged-G export timeout.

### Process fewer images

[Open the 12 images/s preview](http://127.0.0.1:8096/experiments/efficiency-lab/live.html?pipeline=rate12),
then use Algorithm to compare M, N, O and G. A normal link still starts with G.
These are caps, not promised update rates. Camera delivery and resolution are
unchanged. Only a fresh eligible image is captured: skipped images never reach
copying, hashing, face/hair inference or rendering. There is no queue of images
waiting for a rate timer and no catch-up burst after a stall. G's original
two-image ownership bound still applies. Each processed image retains its own
detection, pose and mask; the last complete AR image stays visible until the next
one completes. This may reduce load or contention but can make motion choppier.
It does not interpolate poses or apply an old mask to a newer camera image.

The original G renderer and options are imported directly for all three rate
choices. Their held comparison shares G's exact result because admission is the
only changed behavior. Held equality does not evaluate movement, tracking across
skipped images or responsiveness. For those, use both glasses and both hair
models, look down/up, turn in both yaw directions, and inspect nose/front and
hair/temple transitions. Physical-camera acceptance remains the owner's review.

Use Measure G vs selected, then repeat with the order reversed, keeping the
same glasses, hair model, lighting, camera/browser and movements. Compare actual
completed update rate, captured-image age, gaps and tracking/mask coverage;
lower work is not automatically a better experience. Use fresh sessions for
startup comparisons. Avoid concurrent camera previews during measurement.
Download timings only exports the selected rate and cumulative admission counters
under `native.admission.*`, alongside existing pump counts and scalar timings.
These counters describe observed callbacks and owned captures, not unseen sensor
images or completed updates. Switching starts a fresh pump and resets its counts.
Heat, battery use and CPU/GPU utilization require separate measurement.

Only the selected pipeline processes each live image. Two outer renderers are
resident: the imported original G and a separately configured candidate. First
tracked-frame temple prewarming still performs its existing completed synchronous
read. Zero-drop branches still skip work. Failed async reads retain the bounded,
explicitly counted same-pair synchronous fallback. Fallback is not an async gain.
Earlier GPU submission is not proof of simultaneous execution on the same GPU.

## Compare

Choose glasses and a hair model, open the camera, and change Algorithm while
moving. Use both Amber Horizon and Tom Ford, both hair-only and multiclass,
down/up and both yaw directions; inspect nose/front and hair/temple transitions.
Hold frame stops live resources and compares all choices on one exact source,
detection/pose and mask. Hair off/on also uses that owned image. Held I shares G
pixels because its change is in input handling; live input tests cover that path.
Resume starts a fresh session. Downloads remain explicit local actions.

Measure G vs selected gives each mode five seconds of warmup and thirty seconds
of measurement. Reverse the order on a second run. Compare **update rate, frame
age, interval p95, longest gap and coverage**, using the same movements. Faster
updates can show older images. Missing tracking/masks remain in the totals.
No synthetic test or raw FPS number establishes personal fit or motion quality.

Timing exports contain fixed glasses/hair model IDs, scalar data, actual branch/download/input counters and
bounded startup/session/long-task events. They exclude images, masks, landmarks
and image identity hashes. Observation timestamps retain time since the last
publication, including intentional held/closed time; that age alone is not a
stall count. Live status marks stale or held readings. The separate held export includes the stopped image.
Open-to-first-image and first-mask times include camera permission and resource
startup; warmed benchmarks exclude those costs. Capture age ends at canvas
submission and excludes camera buffering and physical display scanout.

## Verify

```powershell
npm run test:efficiency
npm run test:efficiency:browser
npm test
```

The unit and production browser suites exercise ownership, exact held output,
real local workers, switching, failed mask upgrades, cancellation and restart.
Optional [matched rendering QA](qa/README.md) reads the preserved private 56-pair
matrix without modifying it; a new G manifest records Git blobs and local byte
representations separately. New reports use unique ignored directories. Earlier
CRLF manifests, failed receipts and recordings remain unchanged.

See [current review](../../docs/REVIEWS.md) for actual verification outcomes and
[the research](../../docs/G_EFFICIENCY_RESEARCH.md) for rationale and evidence
limitations. New candidate speed, moving-wearer acceptance, sustained physical
camera performance, mobile smoothness and thermal behavior require measurement.
