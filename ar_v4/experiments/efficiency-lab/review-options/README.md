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

The helper preserves the caller's dimensions, including 720×1280. Native copying
requires explicit valid rotation and flip metadata, zero rotation, no flip and
matching visible/display dimensions. Missing metadata is not evidence of zero
rotation. Unknown orientation, known transforms or incompatible dimensions use
G's synchronous video snapshot in the original camera callback, before any
asynchronous work. The unused VideoFrame is closed immediately. This avoids
relying on `drawImage(VideoFrame)` to repair an unknown camera transform.
Missing construction support also uses that synchronous video snapshot.
Rejected asynchronous conversion can use only its verified untransformed frozen
frame, never a later live video image. Actual path, fallback reason, source/frame
dimensions, orientation metadata and stage timings are explicit. Fallback rows
are not evidence that native camera copying works or improves performance.
The accepted G capture is unchanged.

The [WebCodecs specification](https://www.w3.org/TR/webcodecs/) distinguishes
raw copying from rendered display transforms. The inspected
[WebKit interface](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/Modules/webcodecs/WebCodecsVideoFrame.idl)
does not expose rotation/flip, and its
[canvas VideoFrame path](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/html/canvas/CanvasRenderingContext2DBase.cpp)
passes no orientation to video-frame drawing. These upstream sources support
the capability guard; they do not identify the exact engine build on the phone.

Run from `ar_v4/`:

```powershell
npx tsc --noEmit -p experiments/efficiency-lab/review-options/tsconfig.json
node --test experiments/efficiency-lab/review-options/*.test.ts
node --test experiments/efficiency-lab/review-options/capture.browser.mjs
node --test experiments/efficiency-lab/review-options/face.browser.mjs
```

Unit tests cover delegate failure/ownership and capture exact pixels,
missing features, frozen fallback, revocation, wrong layouts/transforms and
recovery. Orientation cases include absent/invalid metadata with deliberately
different frozen pixels, 90/180/270-degree rotation, mirroring, swapped dimensions,
same-task video capture before advancement, cancellation and one-time closure.
The real live-pump regression exercises delayed copies, replacement
before inference, late hair, four published images across stop/restart, exact
source/hash/detection/mask pairing and the two-canvas bound through deferred
leases. Removing the synchronous replacement release makes it fail. Three real
Chromium capture checks cover native portrait RGBA copy,
canvas equality, camera advancement, fallback and cancellation. The real CPU
worker check uses the checked-in synthetic fixture, the installed SDK and a
temporary localhost port to verify CPU detections, source preservation, bitmap
closure, cancellation and restart. These are synthetic Chromium checks; no
physical iPhone support, wearer motion quality or speed improvement is claimed.
