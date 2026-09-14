# Performance comparison candidate

Separate implementation of review items 1–3: eliminate unused rendering,
redundant native image transfers, and unnecessary allocations/pixel processing.
The accepted long-hair pipeline and pinned `perfect_temples` sources remain
unchanged. This candidate needs the owner's visual acceptance before promotion.

From `ar_v4/`:

```powershell
npm run dev:performance
```

Open http://127.0.0.1:8066/experiments/performance-candidate/live.html.
Choose Amber Horizon or Tom Ford and either hair model, then Open camera.
Switch **Current / Test** while moving. Both renderers are initialized, but only
the selected pipeline processes live images. Switching commits at the next
owned frame; the camera and face/hair workers keep the same session.

**Hold frame** finishes the owned image, stops camera/workers, and obtains a
full diagnostic mask for that same image. Once ready, switch Current/Test to
compare identical source/detection/mask inputs. Download explicitly saves both
outputs locally. Resume creates a new session. Private camera images are never
uploaded. The two initialized renderers use more memory than a standalone
pipeline; this page compares execution, not standalone memory consumption.

Display rate and hair coverage reset after a pipeline or hair-on/off change.
Processing p95 uses up to the last 30 completed frames. Processing time excludes
sensor/camera buffering and final browser/display delay; it is not end-to-end
motion latency. Hair coverage matters: an apparently faster pipeline with fewer
valid same-frame masks is not an equivalent hair-quality improvement.

## Implemented changes

- Skip the independent temple branch render/readback at exact zero correction,
  retaining fresh protection bounds for the current image and pose.
- Apply consecutive temple deformations directly from original geometry without
  a redundant intermediate reset. Clear geometry/ownership on no-face/failure.
- Skip exactly inactive temple overlays and their depth pass. No approximate
  thresholds, materials, multisampling, resolution or geometry policy change.
- Read authoritative native pixels directly, with reusable bottom-up scratch.
  Omit the redundant 2D copy/read/channel comparison, explicitly identifying its
  absence in candidate diagnostics.
- Pass owned accepted pixels to hair composition instead of hidden canvas
  publication/readback. Held sources and final outputs remain independently owned.
- Compose temple edits over exact row spans; retain final optical/nasal copies.
- Omit the unused live float weight image and reuse membership/coordinate scratch
  while retaining full mask validation, continuity and final pixel guards.

Inference models, image/pose/mask pairing, hashes, scheduler, 8 ms hair completion
timer, nose configuration, optical materials, rear curve and 15 mm fade retain
the accepted rules. GPU composition, lower resolutions and new tracking/prediction
are outside this candidate.

## Validation

```powershell
npm run test:performance
npm run test:performance:browser
npm test
node experiments/performance-candidate/qa/matched.mjs --preflight
node experiments/performance-candidate/qa/matched.mjs --base=http://127.0.0.1:8066
```

`build:performance` writes its own ignored `dist` directory. The production
comparison lifecycle tests launch it on port 8067 and use the checked-in synthetic
camera fixture with real local face/hair workers. The optional matched harness
requires retained private evidence and never reinfers recorded detections/masks
or rewrites original recordings. Run heavy browser jobs serially.

On Windows, set `$env:PERFORMANCE_QA_D3D11='1'` before the browser test command
to request hardware D3D11. Check the recorded renderer and delegate; a default
headless launch may use SwiftShader. The two modes use separate result folders.
The seven browser scenarios include pending-frame Hold/switch ordering and
retaining a valid category mask when the held full-mask upgrade fails.

Current results and remaining empirical limitations are recorded in
`../../docs/REVIEWS.md`. Generated stills and synthetic camera tests cannot
establish wearer fit, physical long-hair motion or sustained mobile smoothness.
