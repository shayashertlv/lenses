# Isolated face and capture review options

`ForcedDelegateDetectorClient` reuses the unchanged stage-2 worker and pinned
model. It requests exactly the chosen delegate, exposes validated actual timing,
and fails initialization if that delegate cannot start. Its structural
`FaceDetector` contract also accepts the unchanged G client. CPU landmarks can
differ from GPU landmarks; a successful worker test is not visual acceptance.

`ExactVideoFrameCapture` freezes a `VideoFrame` synchronously inside the admitted
camera callback. Its one pending copy writes RGBA into the owned image buffer;
after completion, `putImageData` supplies those same opaque sRGB bytes to the
owned canvas. Hashing, face/hair bitmaps and rendering must await `ready` and use
that pair. The canvas lease must outlive capture and its downstream readers.
Callers must count deferred leases toward the existing two-image bound.

The helper preserves the caller's dimensions, including 720×1280. `copyTo` does
not resize or apply display transforms: incompatible dimensions/rotation use
the retained frozen frame through the canvas path. Missing construction support
uses a synchronous video snapshot in the original callback; rejected conversion
uses the retained frozen frame, never a later live video image. Actual path,
fallback reason and stage timings are explicit. The accepted G capture is
unchanged. See the [WebCodecs specification](https://www.w3.org/TR/webcodecs/)
for RGBA layout, target color space, visible rectangle and display transforms.

Run from `ar_v4/`:

```powershell
npx tsc --noEmit -p experiments/efficiency-lab/review-options/tsconfig.json
node --test experiments/efficiency-lab/review-options/*.test.ts
node --test experiments/efficiency-lab/review-options/capture.browser.mjs
node --test experiments/efficiency-lab/review-options/face.browser.mjs
```

The 11 unit tests cover delegate failure/ownership and capture exact pixels,
missing features, frozen fallback, revocation, wrong layouts/transforms and
recovery. The real live-pump regression exercises delayed copies, replacement
before inference, late hair, four published images across stop/restart, exact
source/hash/detection/mask pairing and the two-canvas bound through deferred
leases. Removing the synchronous replacement release makes it fail. Three real
Chromium capture checks cover native portrait RGBA copy,
canvas equality, camera advancement, fallback and cancellation. The real CPU
worker check uses the checked-in synthetic fixture, the installed SDK and a
temporary localhost port to verify CPU detections, source preservation, bitmap
closure, cancellation and restart. These are synthetic Chromium checks; no
physical iPhone support, wearer motion quality or speed improvement is claimed.
