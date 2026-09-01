# Fix specifications — the review's remaining findings, measured

**2026-08-26.** The full-tree review confirmed 47 findings after adversarial
verification. Roughly a third were fixed the same day; three were in `ar/` and
went with it. The rest were then handed to a second, independent pass whose
instructions were to **specify rather than patch**: verify the finding, produce
an exact change, measure what it moves, name what goes red, and say how to make
the fix falsifiable.

**That pass corrected the review's own wording on nine of fourteen items and
found two of its proposed fixes to be wrong.** Read the verdict line before the
finding. This document is the reason a later session does not have to re-derive
any of it.

## How to use this

Each entry is self-contained: verdict, patch, measurement, blast radius,
falsifiable test. The measurements were taken by copying `dist/` into a scratch
directory, patching the copy, and running it — so the "after" numbers are real,
not predicted, and a fix that reproduces them is a fix that landed correctly.

Where an entry says a report moves, regenerate it with
`npm run report:<name>` and expect `check-reports.mjs` to go red on the body
hash until you do. That is the gate working.

**Doctrine that applies to every one of these**, from `docs/NEXT-SESSION.md` §1:
before letting a new gate go green, sabotage what it guards, watch it go red,
restore, and show the red/green table. A check that cannot fail is a bug.

## Priority

`docs/NEXT-SESSION.md` §3b carries the ranked queue and the one-line verdicts.
The short version:

  P1   A2   stored intrinsics planted on a camera of a different size — LIVE,
            reproducible today, and no residual can see it
  P2   C3   the silhouette term is dead in production and is worth 0.29 mm
       C4a  a PD the app accepts as its ruler is then withheld from the readout
       D1   the wearer-facing pad load describes different physics from the solve
       B1   the snap ridge gate is skipped at band ends
       D4   `padAngleRad` is two different angles under one name
  P3   D2, C1, C2, B3, C4b, C4c, D3, A3 — smaller, or different from the review

Two already landed and are recorded here for their measurements only: **A1**
(the principal-point shear, commit `947edf7`) and **B2** (the smoother's dropout
gap, same commit).

---

## Cluster A — the renderer and the camera

All three findings are **real**. Two of the three cited line numbers are wrong, one substantive claim inside A2 ("`scaleIntrinsics` is the exact rescale") is **wrong and I can show why**, and A2 has a **second site the review missed**. Everything below was measured against `dist/` — no repo file was touched; scratch scripts are in `C:\Users\Shay\AppData\Local\Temp\claude\C--Users-Shay-PycharmProjects-lenses-ar-v2\514715fb-99c5-4c9d-af84-119cfa6043ff\scratchpad\` (`a1.mjs`, `a1b.mjs`, `a1red.mjs`, `a2.mjs`, `a2b.mjs`, `a2c.mjs`, `a2d.mjs`, `a2red.mjs`, `a3.mjs`).

---

### A1 — principal-point shear, both signs inverted. CONFIRMED, and it is worse than doing nothing.

#### 1. Real, restated

`C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\render\scene.ts:365-366` — line number as cited, correct.

The three.js convention, from the vendored source rather than from memory: `vendor/three/three.core.js:11029-11032` sets `te[8] = a = (right+left)/(right-left)` and `te[11] = -1`. A camera-space point `(0,0,-d)` therefore has `clip.x = -a·d`, `clip.w = d`, so **the optical axis lands at NDC `x = -te[8]`** (and `y = -te[9]`). Verified numerically against the real `THREE.PerspectiveCamera` in `a1.mjs`.

`principalPointOffset` (`src/render/convert.ts:154-159`) returns the **desired NDC position of the optical axis**: `x = 2dx/W`, `y = -2dy/H`, which is exactly `pixelToNDC(cx, cy)`. **That function is correct.** The bug is entirely in the application: writing `te[8] += offset.x` puts the axis at `-offset.x`, i.e. the negation of the target.

So: **rendered NDC = correct NDC − 2·offset**. In pixels, the drawn object is displaced by `(−2·dx, −2·dy)` — twice the principal-point offset, in the opposite direction. **The `if (offset)` branch is strictly worse than the `else` branch**: not applying the shear at all leaves an error of `1·d` px; applying it as written leaves `2·d` px, in the same direction.

Measured (`a1.mjs`, real three.js, 1280×720, 63° vfov, subject at 450 mm — 1 px = 0.766 mm):

| principal point off centre | shipped (`+=`) | `else` branch (no shear) | fixed (`-=`) |
|---|---|---|---|
| 12.8, 7.2 px (1% of W/H) | −25.6, −14.4 px = **−19.6, −11.0 mm** | −12.8, −7.2 px = −9.8, −5.5 mm | 0.00, 0.00 px |
| 25.6, 14.4 px (2%) | −51.2, −28.8 px = **−39.2, −22.1 mm** | −25.6, −14.4 px | 0.00 |
| 64, 36 px (5%) | −128, −72 px = **−98.0, −55.1 mm** | −64, −36 px | 0.00 |

Direction: for a principal point **right of and below** image centre, the frame is drawn **left and up** by twice the offset. At 500 mm a 2 %-of-width offset is ≈ 44 mm — a third of a frame front, on a 140 mm frame.

#### 2. Exact change

**Minimal (2 characters), `src/render/scene.ts:365-366`:**

```
        camera.updateProjectionMatrix();
-       camera.projectionMatrix.elements[8] += offset.x;
-       camera.projectionMatrix.elements[9] += offset.y;
+       // three.js puts the optical axis at NDC (-te[8], -te[9]) — see
+       // Matrix4.makePerspective, te[8] = (r+l)/(r-l), te[11] = -1. So the
+       // target NDC principalPointOffset returns is SUBTRACTED, not added.
+       camera.projectionMatrix.elements[8] -= offset.x;
+       camera.projectionMatrix.elements[9] -= offset.y;
        camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
```

**Better (recommended), same site** — three.js has a first-class off-centre frustum, and the hand-patched elements are silently wiped by any later `camera.updateProjectionMatrix()` (measured: `a1b.mjs`, `te[8]` goes `−0.04 → 0.000000`; `setViewOffset` survives at `−0.040000`). Nothing in the current render path recomputes it — `WebGLRenderer.render` does not call it, only `setFocalLength` / `setViewOffset` / `clearViewOffset` / the XR + shadow paths do — but the patched-elements form is a trap for the next person:

```
      const offset = principalPointOffset(intrinsics);
      if (offset) {
-       camera.updateProjectionMatrix();
-       camera.projectionMatrix.elements[8] += offset.x;
-       camera.projectionMatrix.elements[9] += offset.y;
-       camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
+       // An off-centre frustum, three.js's own way: setViewOffset shifts
+       // `left` by offsetX·width/fullWidth, which lands the optical axis at
+       // NDC -2·offsetX/W. The negative arguments are what makes that equal
+       // the principal point. Unlike patching projectionMatrix.elements, this
+       // survives a later updateProjectionMatrix().
+       const dx = intrinsics.cx - intrinsics.width / 2;
+       const dy = intrinsics.cy - intrinsics.height / 2;
+       camera.setViewOffset(intrinsics.width, intrinsics.height,
+         -dx, -dy, intrinsics.width, intrinsics.height);
      } else {
+       camera.clearViewOffset();   // a previous non-central solve must not stick
        camera.updateProjectionMatrix();
      }
```

Both forms verified exact to 0.0000 px against `core/camera.ts:project` over three off-axis points (`a1b.mjs`). Note the `clearViewOffset()` in the `else`: today `applyIntrinsics` is called twice per boot and a second, central intrinsics after a non-central one would keep the stale shear.

#### 3. What it moves — **nothing, today**

`principalPointOffset` returns `null` on **100 % of shipped paths**, so the buggy branch is never entered. Proof: `cx`/`cy` are only ever set by `intrinsicsFromFov` (`core/camera.ts:55`, exactly `width/2`, `height/2`) and only ever moved by `applyIntrinsicsDelta` (`core/camera.ts:343`) under `mask.pp`. Every `pp` in the tree is `false` — `enroll/bundle.ts:165`, `enroll/enroll.ts:149`, `INTRINSICS_FREE_F`, `INTRINSICS_FIXED`; the only `pp: true` is `tests/core.test.ts:578`, a direct unit test of the Jacobian that never reaches the renderer. Confirmed at runtime too: `principalPointOffset` on a boot-path record returns `null` (`a2b.mjs`, first line of output).

So this is a **latent** defect that fires the day someone flips `pp: true`, which the docstring on the branch itself anticipates. No published number changes. It is still worth fixing precisely because it is dormant: the day it wakes up it will be blamed on the solver, not the renderer, and it fails in the direction that *looks* like the shear is working (the frame moves the right axis, wrong way, wrong magnitude).

#### 4. What goes red

Nothing. No test asserts on `projectionMatrix.elements`; `tests/scene.test.ts` only passes `principalPointOffset` in as a slice dependency (line 226/231) and never calls `applyIntrinsics` with a non-central record. No doc figure in `docs/CONSTANTS.md` or elsewhere mentions the principal point. That absence *is* the finding's second half.

#### 5. Falsifiable

Add to `tests/scene.test.ts` (works with the existing stub camera unchanged — its `projectionMatrix.elements` starts all-zero and `updateProjectionMatrix()` is a no-op, so the elements are exactly what `applyIntrinsics` wrote):

```ts
it('puts the optical axis at the principal point, not at its mirror image', async () => {
  // RED on the shipped file by 73.4 px. three.js places the optical axis at
  // NDC (-te[8], -te[9]) — Matrix4.makePerspective, te[8]=(r+l)/(r-l),
  // te[11]=-1 — so a `+=` draws the frame at TWICE the offset, backwards.
  const s = instantiateScene();
  const handle = await s.createScene({} as any, { preferWebGPU: false });
  const W = 1280, H = 720;
  const k = { f: H / 2 / Math.tan(63 * Math.PI / 360),
              cx: W / 2 + 32, cy: H / 2 - 18, k1: 0, width: W, height: H };
  handle.applyIntrinsics(k);
  const te = handle.camera.projectionMatrix.elements;
  const axisPx = [(1 - te[8]) * W / 2, (1 + te[9]) * H / 2];
  assert.ok(Math.hypot(axisPx[0] - k.cx, axisPx[1] - k.cy) < 0.05,
    `the optical axis is drawn at (${axisPx}) but the principal point is `
    + `(${k.cx}, ${k.cy}) — the shear's sign is inverted`);
});
```

**Demonstrated RED/GREEN** (`a1red.mjs`, which re-slices `createScene` out of the built `dist/src/render/scene.js` the same way the real harness does):
```
SHIPPED: te[8]=0.050000 te[9]=0.050000 -> axis drawn at (608.0, 378.0), pp is (672, 342) | miss 73.4 px => RED
PATCHED: te[8]=-0.050000 te[9]=-0.050000 -> axis drawn at (672.0, 342.0)                  | miss  0.0 px => GREEN
```
73.4 px = `2·hypot(32, 18)`. If you take the `setViewOffset` form, add a second assertion that `te[8]` is unchanged after a subsequent `camera.updateProjectionMatrix()` — RED on both the shipped code and the 2-character fix, GREEN only on `setViewOffset`.

---

### A2 — stale intrinsics adopted at a new camera resolution. CONFIRMED, catastrophic, and silent.

#### 1. Real, restated — with three corrections to the finding

**Line number is wrong.** The review cites `src/app/main.ts:1128`; that line is `if (live) localStorage.setItem(...)`. The actual site is **`src/app/main.ts:1401-1406`**, inside `adoptModel` (which starts at 1359):

```ts
app.intrinsics = model.intrinsicsSolved
  ? model.intrinsics
  : intrinsicsFromFov(app.source?.width ?? 1280, app.source?.height ?? 720, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
app.scene.applyIntrinsics(app.intrinsics);
```

**The review missed a second site, and it is looser than the one it found** — `src/app/main.ts:599-601`, in `startSource`:
```ts
app.intrinsics = app.model?.intrinsics
  ?? intrinsicsFromFov(app.source.width, app.source.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
```
No `intrinsicsSolved` check at all. Currently masked — boot calls `startSource` (442) then `adoptModel` (452) before `startLoop` (456), so the value is overwritten before a frame is drawn — but it is the same defect one refactor away from being live, and `startSource` reads as a source-switch entry point.

**`scaleIntrinsics` has zero callers repo-wide: CONFIRMED.** Only `src/core/camera.ts:68` and its own `dist/` build. Nothing in `src/`, `tests/`, or `docs/` calls it.

**But "`scaleIntrinsics` is the exact rescale" is WRONG.** `core/camera.ts:68-77` scales `f` and `cx` by `width / k.width` and `cy` by `height / k.height` — the mismatch between those two factors *is* the tell. It is exact only when the aspect ratio is preserved. Under an aspect change it produces a camera whose vertical field of view is not the camera's: transferring a 63° 1280×720 record to 640×480 gives a **78.50°** record. Measured cost, below.

**How the mismatch actually happens.** `getUserMedia` is asked for `{ ideal: 1280 } × { ideal: 720 }` (`src/app/sources.ts:48-49`) — *ideal*, not *exact*, so a different default device, another app holding the camera, or a driver change silently renegotiates. And there is a **deterministic reproducer that needs no hardware change at all**: scan on a camera, reload with the camera unavailable, and `startSource` falls back to `assets/samples/face-a.jpg`, which is **1024×1024**. `model.intrinsics` (1280×720) is planted on a 1024×1024 source by both sites.

**Why nothing notices.** PnP absorbs a wrong focal length into depth, so the *reprojection residual stays healthy*: measured rms 4.95–5.90 px against `SCAN_MAX_RMS_PX = 22` (`main.ts:280`), and `pose.t[2] > 50` passes on 90/90 frames. Every gate reads green while the frame is drawn a third of a screen off the face. **No residual can see this**, which is why the fix has to be a precondition check rather than a bar.

#### 2. Exact change

**(a) `src/core/camera.ts:68-77` — make the rescale physically right and refuse what it cannot know.** The correct focal scale for a webcam mode change is `max(sx, sy)`, because browsers change modes by **cropping and downscaling**, never by anamorphic squash: 16:9→4:3 crops the sides (vertical FOV survives, `sy > sx`), 4:3→16:9 crops top and bottom (horizontal FOV survives, `sx > sy`). Both verified exact against ground truth (`a2b.mjs`, `a2d.mjs`).

```
 export function scaleIntrinsics(k: Intrinsics, width: number, height: number): Intrinsics {
-  const s = width / k.width;
+  // A driver changes modes by CROPPING and downscaling, never by squashing:
+  // 16:9 -> 4:3 crops the sides and the VERTICAL fov survives (sy > sx);
+  // 4:3 -> 16:9 crops top and bottom and the HORIZONTAL fov survives (sx > sy).
+  // Either way the surviving axis is the larger ratio, so f scales by max.
+  // `width / k.width` alone is right only when the aspect is unchanged, and
+  // costs 115 mm of solved depth on a 1280x720 -> 640x480 transfer.
+  const sx = width / k.width;
+  const sy = height / k.height;
+  const s = Math.max(sx, sy);
   return {
     f: k.f * s,
-    cx: k.cx * s,
-    cy: k.cy * (height / k.height),
+    // Per-axis, which is exact for a symmetric crop of a CENTRAL principal
+    // point — the only kind this tree produces, because `pp` is never solved.
+    // A solved off-centre pp would need the crop window, which is not in the
+    // record; see `principalPointOffset`.
+    cx: k.cx * sx,
+    cy: k.cy * sy,
     k1: k.k1,   // dimensionless: `project` normalises r by z, not by f
     width,
     height,
   };
 }
```

**(b) `src/app/main.ts:1401-1406` — give it a caller, and say so when the record is transferred.**

```
 app.intrinsics = model.intrinsicsSolved
-  ? model.intrinsics
+  ? intrinsicsForSource(model.intrinsics, app.source?.width ?? 1280, app.source?.height ?? 720)
   : intrinsicsFromFov(
     app.source?.width ?? 1280, app.source?.height ?? 720, MEDIAPIPE_ASSUMED_VERTICAL_FOV,
   );
```
with a small local helper beside `adoptModel`:
```ts
/**
 * A solved camera moved onto a source of a different size.
 *
 * The scan's record is in the pixels of the mode the scan ran in, and the
 * tracker's landmarks are in the pixels of the mode running NOW — `getUserMedia`
 * is asked for 1280x720 as an *ideal*, and the still-image fallback is
 * 1024x1024. Using the record verbatim across that change costs 185 px of
 * screen misalignment and 21 degrees of pose error, with a 5 px reprojection
 * residual: no gate in this app can see it.
 */
function intrinsicsForSource(k: Intrinsics, width: number, height: number): Intrinsics {
  if (width === k.width && height === k.height) return k;
  const scaled = scaleIntrinsics(k, width, height);
  console.info(`[camera] the scan solved ${k.f.toFixed(1)} px at ${k.width}x${k.height}; `
    + `this source is ${width}x${height}, so the solve is carried over as `
    + `${scaled.f.toFixed(1)} px (${verticalFovDeg(scaled).toFixed(1)} deg vertical)`);
  return scaled;
}
```
plus `scaleIntrinsics, verticalFovDeg` added to the import at `src/app/main.ts:30`.

**(c) `src/app/main.ts:599-600` — the same call, so the two sites cannot drift:**
```
-app.intrinsics = app.model?.intrinsics
-  ?? intrinsicsFromFov(app.source.width, app.source.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
+app.intrinsics = app.model?.intrinsicsSolved
+  ? intrinsicsForSource(app.model.intrinsics, app.source.width, app.source.height)
+  : intrinsicsFromFov(app.source.width, app.source.height, MEDIAPIPE_ASSUMED_VERTICAL_FOV);
```

#### 3. What it moves — **rescale, not refusal, and here is the margin**

Method (`a2.mjs`, `a2b.mjs`, `a2c.mjs`, `a2d.mjs`): 5 synthetic subjects × 18 frames, landmarks generated by `synthesizeCapture` with the *true* intrinsics of the reload mode; `solvePnP` run with each candidate record; and a **DRAWN-vs-SEEN** column — where the renderer actually puts each landmark vertex on the real canvas versus where the detector saw it. The renderer's effective camera is `f_eff = f_rec · W_canvas / W_rec` with the principal point at the canvas centre (the record's absolute pixel units cancel because three.js stretches NDC over the viewport); that model was validated to 4 decimal places against the real `THREE.PerspectiveCamera` (`a2b.mjs`).

| scan → reload | policy | median \|dt\| | dz | rot | PnP rms | **DRAWN-vs-SEEN** |
|---|---|---|---|---|---|---|
| 1280×720 → 640×360 | **shipped** | 801.9 mm | +508.9 | 20.59° | 4.95 px | **185.0 px** of 640 |
| | scaleIntrinsics (either form) | 17.9 | −0.9 | 1.79° | 3.91 | 3.1 |
| 1280×720 → 640×480 | **shipped** | 502.4 | +255.7 | 19.65° | 5.17 | **179.8 px** |
| | scaleIntrinsics **as shipped** | 115.2 | −115.2 | 1.77° | 4.14 | 3.2 |
| | scaleIntrinsics **fixed** = truth | 17.1 | −1.0 | 1.81° | 4.14 | 3.2 |
| 1280×720 → 1024×1024 (still fallback) | **shipped** | 176.0 | −125.8 | 15.78° | 5.90 | **245.6 px** of 1024 |
| | scaleIntrinsics as shipped | 200.4 | −200.4 | 1.90° | 5.32 | 4.9 |
| | fixed = truth | 17.7 | −0.2 | 1.60° | 5.35 | 5.0 |
| 640×480 → 1280×720 | **shipped** | 315.8 | −208.0 | 26.64° | — | **662.7 px** of 1280 (52 % of the screen) |
| | fixed = truth | 17.7 | −0.6 | 1.94° | — | 4.8 |

The 17–18 mm / 1.6–2.0° floor is the control — detector noise plus template-vs-subject mismatch — and every corrected policy sits on it.

**Rescale, not refusal, and the numbers say why.** On a 55° laptop-lid camera reloading at 800×600 (`a2c.mjs`):

| policy | f | vfov of record | \|dt\| | dz |
|---|---|---|---|---|
| shipped (verbatim) | 691.6 | 55.00 | 237.9 mm | +103.5 |
| `scaleIntrinsics` as shipped | 432.2 | 69.53 | 115.4 | −115.2 |
| **refuse → assumed 63°** | 489.6 | 63.00 | 69.9 | −69.8 |
| **`scaleIntrinsics` fixed** | 576.3 | 55.00 | **13.6** | **−0.8** |
| truth (control) | 576.3 | 55.00 | 13.6 | −0.8 |

Refusing costs **70 mm of solved depth** on a camera whose FOV was honestly measured — it throws away exactly the thing `core/camera.ts`'s header argues for ("Self-consistency is not truth"). The corrected rescale is free and exact in both crop directions. **Recommend the rescale.** Keep refusal for the case the record genuinely cannot cover — a *non-central solved* principal point across an aspect change, which cannot happen until `pp: true` ships; guard it with a comment now rather than a branch.

**Published numbers: none change.** Every report and test constructs intrinsics at the resolution it synthesises at, so no figure in `docs/` is on this path.

#### 4. What goes red

Nothing, and that is the problem. `scaleIntrinsics` has zero callers, so changing its focal rule cannot break a test. `tests/app.test.ts:135` ("adopting a model clears the edge-snap field") slices `adoptModel`'s text and matches only `app.snapField = null` / `app.snapBuffer = null` — unaffected. No test calls `applyIntrinsics` with a mismatched record. No `docs/` bar covers it. **Everything in this finding is currently untested in both directions.**

#### 5. Falsifiable

**(i) A real behavioural test in `tests/core.test.ts`** — no browser, no slicing:
```ts
it('carries a solved camera across a mode change without inventing a field of view', () => {
  const hfov = (k: Intrinsics) => 2 * Math.atan(k.width / 2 / k.f) * 180 / Math.PI;
  const k = intrinsicsFromFov(1280, 720, 63);
  // same aspect: both fields of view survive
  assert.ok(Math.abs(verticalFovDeg(scaleIntrinsics(k, 640, 360)) - 63) < 1e-9);
  // 16:9 -> 4:3 is a horizontal crop: the VERTICAL fov survives, the horizontal shrinks
  const crop43 = scaleIntrinsics(k, 640, 480);
  assert.ok(Math.abs(verticalFovDeg(crop43) - 63) < 1e-9,
    `a 4:3 mode was given a ${verticalFovDeg(crop43).toFixed(2)} deg vertical fov — `
    + 'the record was stretched, not cropped');
  assert.ok(hfov(crop43) < hfov(k) - 1e-9);
  // 4:3 -> 16:9 is a vertical crop: the HORIZONTAL fov survives
  const k43 = intrinsicsFromFov(640, 480, 63);
  assert.ok(Math.abs(hfov(scaleIntrinsics(k43, 1280, 720)) - hfov(k43)) < 1e-9);
});
```
**Demonstrated RED/GREEN** (`a2red.mjs`) — the shipped `s = width / k.width` fails two of the four:
```
scaleIntrinsics AS SHIPPED   GREEN same-aspect | RED crop-to-4:3 vfov (78.502) | RED hfov shrinks | GREEN crop-to-16:9
scaleIntrinsics FIXED        GREEN | GREEN (63.000) | GREEN (78.502 < 94.901) | GREEN
```

**(ii) A textual fingerprint in `tests/app.test.ts`**, in the tree's own established idiom for `main.ts` (which cannot be imported under Node), covering **both** sites:
```ts
const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
assert.doesNotMatch(text, /app\.intrinsics = model\.intrinsicsSolved\s*\?\s*model\.intrinsics\b/,
  'adoptModel takes a stored record verbatim again — 185 px of misalignment at a '
  + 'changed camera resolution, with a 5 px reprojection residual that no gate sees');
assert.doesNotMatch(text, /app\.intrinsics = app\.model\?\.intrinsics\s*\?\?/,
  'startSource takes a stored record verbatim again');
```
RED on the current build: both regexes match `dist/src/app/main.js` today.

**(iii) A behavioural bar for the report layer**, which is the one that would have caught it originally: median DRAWN-vs-SEEN across the reload ladder must stay under 10 px. Today 185.0 / 179.8 / 245.6 / 662.7; after the fix 3.1 / 3.2 / 5.0 / 4.8. `a2b.mjs` is a ready-made runner.

---

### A3 — the tripwire cannot see the regression it names by file. CONFIRMED, and it names the file in a *string*.

#### 1. Real, restated

Line number is wrong: the review cites `src/app/main.ts:1265` (mid-docstring of `resetPerson`). `frameSanityTripwire` is at **`src/app/main.ts:1593-1632`**.

Its docstring says it is *"A console tripwire for the double-flip class of defect (`render/convert.ts`: a seat passed through the CV→GL flip lands mirrored in Y and Z, 127 mm below and behind the head)."*

The only place that defect can be introduced is `applySeat` (`src/render/scene.ts:534-539`), which writes `frameNode.matrix` from `poseToUnflippedMatrix(seat.pose)`. Swapping in `poseToGLMatrix` is the whole bug, and it happens **strictly downstream** of `seat.pose`.

The tripwire reads `app.model.positions`, `seat.pose.R`, `seat.pose.t`, and `frame.lensCentres / hinges / earRests`. Mechanised proof over the function body (`a3.mjs`):
```
tripwire body mentions "frameNode": false      "matrix": false
tripwire body mentions "poseToGLMatrix": false "poseToUnflippedMatrix": false
tripwire body mentions "applySeat": false      "scene": false
tripwire body mentions "convert": true    <-- only inside the warning STRING
```
Measured on a real `solveSeat` (subject S00, frame `standard`), right lens centre in head-node space:
```
applySeat as shipped   : (-32.22,  15.77,  58.58)
applySeat double-flipped: (-32.22, -15.77, -58.58)      displacement 121.3 mm
frameSanityTripwire, with the double flip installed:
  { cx: -0.50, lensZ: 58.57, lensY: 15.83, ok: true }   -> "[frame] sanity ok"
```
The three numbers are **bit-identical** in both cases, because they never touch the matrix.

**Be precise about what it is not.** The check is not vacuous — it *would* catch a `solveSeat` that returned a mirrored, NaN, or grossly displaced pose (mirroring `seat.pose` puts `lensZ` at −58.57 against a `hz1 − 45 = 30.77` bar, which trips). It is **mis-aimed**, not empty: it guards the seat solver and claims to guard the converter.

#### 2. Exact change

Read the matrix that is actually drawn. Same three predicates, one different source. `src/app/main.ts:1605-1612`:

```
-  const { R, t } = seat.pose;
-  const toFace = (v: ArrayLike<number>) => [
-    R[0] * v[0] + R[1] * v[1] + R[2] * v[2] + t[0],
-    R[3] * v[0] + R[4] * v[1] + R[5] * v[2] + t[1],
-    R[6] * v[0] + R[7] * v[1] + R[8] * v[2] + t[2],
-  ];
+  // **The matrix that is DRAWN, not the pose that was handed to `applySeat`.**
+  // This function used to read `seat.pose` and claim, in its own docstring, to
+  // catch the CV->GL double flip in `render/convert.ts`. It structurally could
+  // not: the flip is introduced by `applySeat` writing `frameNode.matrix`,
+  // strictly downstream of `seat.pose`, and every number below was identical
+  // with and without it — measured, "sanity ok" on a lens centre 121.3 mm out.
+  // `frameNode.matrix` is frame-local -> face space (its parent `headNode`
+  // carries the flip and face space agrees with GL), so reading it keeps this
+  // check independent of the live head pose, which is the property that made
+  // the old version worth having.
+  const m = app.scene.frameNode.matrix.elements;   // three.js column-major
+  const toFace = (v: ArrayLike<number>) => [
+    m[0] * v[0] + m[4] * v[1] + m[8]  * v[2] + m[12],
+    m[1] * v[0] + m[5] * v[1] + m[9]  * v[2] + m[13],
+    m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14],
+  ];
```
`seat` stays in the signature (the warning text is worth keeping keyed to the fit), or drop the parameter — `fitFrame` (`main.ts:1577`) calls `applySeat` before the tripwire, so the matrix is populated at call time.

**`frameNode.matrix` — not `matrixWorld`.** `matrixWorld` folds in `headNode`'s live pose and would destroy the pose-independence the docstring rightly advertises.

#### 3. Reachability and the isolation boundary — **yes, and it costs nothing**

`docs/HANDOFF.md:96` and `docs/PARITY.md:188`: the boundary is *"`core/ enroll/ track/ fit/ detect/ testkit/` must run in Node with no browser; `render/` and `app/` own the browser"*, enforced by `scripts/check-isolation.mjs`. `app/` and `render/` are on the **same** side. `frameNode` is already a documented public member of `SceneHandle` (`src/render/scene.ts:113-127`, with the convention rule spelled out on it), and `main.ts` already imports `applySeat`, `attachFrame`, `detachFrame` and `SceneHandle` from `render/scene.ts` (line 62-64). Reading `.matrix.elements` adds **no** `three` import to `main.ts` (which has none today) — it is a plain number array. **No boundary is crossed.**

#### 4. What it moves / what goes red

No measurable behaviour on a correct build: the numbers printed are the same to 0.01 mm (`58.57` vs `58.58` — the Float32 narrowing in `poseToUnflippedMatrix`'s `Float32Array` output, nothing more). Under the regression, `console.debug "[frame] sanity ok"` becomes `console.warn "[frame] SANITY TRIPWIRE"`. No test covers `frameSanityTripwire`; nothing goes red.

#### 5. Falsifiable

Two halves, and the second matters more than the first.

**(a) Behavioural**, measured (`a3.mjs`, real `solveSeat` on a generated subject):
```
PROPOSED tripwire (reads frameNode.matrix):
  applySeat correct     -> {cx:-0.50, lensZ:  58.57, lensY:  15.83, frontOk:true,  lateralOk:true, heightOk:true}
  applySeat double-flip -> {cx:-0.50, lensZ: -58.57, lensY: -15.83, frontOk:FALSE, lateralOk:true, heightOk:true}
```
The `lensesAtTheFront` predicate fires; the other two do not (a Y/Z mirror leaves X alone, and the mirrored lens Y is still inside the head's Y range) — which is fine, one is enough, but it is worth knowing that only **one** of the three predicates is load-bearing for this defect class. If you want two, tighten `lensesAtEyeHeight` from the whole head bbox `[-82.03, 77.05]` to the eye band.

**(b) Structural**, in `tests/app.test.ts`, in the same idiom as the two fingerprints already there — because the failure mode this finding *is* is a check that silently stopped reading its subject:
```ts
it('the frame sanity tripwire reads the matrix that is drawn', () => {
  // RED on the shipped file. This check named `render/convert.ts` in its
  // warning string for its whole life and never read a converter or a node
  // matrix; a double-flipped applySeat put the lens centre 121.3 mm out and
  // it printed "sanity ok".
  const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
  const at = text.indexOf('function frameSanityTripwire');
  const body = text.slice(at, text.indexOf('\nfunction ', at + 1));
  assert.match(body, /frameNode\.matrix/,
    'frameSanityTripwire reads seat.pose again — it is upstream of applySeat and '
    + 'cannot see the double flip its own message blames');
});
```
Verified RED against the current `dist/src/app/main.js`: the body contains `convert` (in the string) and none of `frameNode`, `matrix`, `applySeat`.

---

### Summary of corrections to the review itself

| | claim | verdict |
|---|---|---|
| A1 | `scene.ts:365`, both signs inverted | **correct**, line and substance; error is `2×` and opposite, i.e. *worse than the `else` branch*. The bug is in the **application**, not in `principalPointOffset`, whose signs are right. Currently **dormant** — `pp` is `false` everywhere, so `offset` is always `null`. |
| A2 | `main.ts:1128` | **line wrong** — it is `main.ts:1401-1406`, and there is a **second, less guarded site at `main.ts:599`** the review missed. |
| A2 | `scaleIntrinsics` has zero callers | **correct**. |
| A2 | `scaleIntrinsics` "is the exact rescale" | **wrong** — exact only when the aspect is preserved; `f` must scale by `max(sx, sy)`, not `width / k.width`. As written it costs 115 mm of solved depth on a 1280×720 → 640×480 transfer and hands the record a 78.5° vertical FOV for a 63° camera. |
| A2 | "measure: scan at 1280×720, reload at 640×480" | done: **502 mm** of translation error, **19.7°** of rotation, **180 px** of screen misalignment on a 640-wide canvas — at a **5.2 px** reprojection residual against a 22 px gate. Silent. |
| A3 | `main.ts:1265`, cannot see the CV→GL flip it cites | **line wrong** (it is `main.ts:1593`); **substance exactly right**, and mechanically provable — the body's only mention of `convert` is inside the warning string. It *is* a real check of the seat solver, so fix the aim, do not delete it. Reading `frameNode.matrix` from `app/` breaks **no** boundary: `app/` and `render/` are the same side of it, and `frameNode` is already public on `SceneHandle`. |

---

## Cluster B — the tracker

All measurements ran against `C:\Users\Shay\PycharmProjects\lenses\ar_v2\dist\`, unmodified. Fixes were simulated in scratch copies (`snapvars.mjs`, a `tracker.js` copy with the imports rewritten to absolute `dist/` URLs and one switchable line). Scratch lives in the session scratchpad; nothing in the repo was touched.

Two parity gates first, so the numbers below mean something:
- my instrumented `snapOffsets` is **bit-identical** to `dist/src/track/snap.js`'s on the edge / noise / flat fixtures;
- my replica of `runTrackReport({seed: 11})` reproduces `reports/track.txt` **exactly** (v2 arm `-0.25 / 3.41 / 0.86`, `-0.68 / 4.35 / 3.25`, … jitter `1.052 / 2.104 / 3.057`; average-head `7.73 / 10.91 / 2.73`, `lost 7`).

---

### B1 — `src/track/snap.ts:189` — REAL, and the recommended fix is smaller than the finding implies

#### 1. Verdict

**Real, and the mechanics are exactly as stated.** `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\track\snap.ts:189` and `:196` both guard on `bestIdx > 0 && bestIdx < steps - 1`, with no `else` on either. At `SNAP_DEFAULTS` (`{searchPx: 8, minGradient: 6, stepPx: 1}`, verified from `dist`) `steps = 17`, so the ends are 2 of 17 = **11.8%** of the band. A peak landing there skips the ridge gate and the parabola, and is emitted with `offsetPx = ±searchPx` exactly.

Two corrections to the finding's wording:

- **"at full confidence" is an overstatement, but the truth is worse.** Confidence is still computed and still has to clear `> 0.2`. Measured over 4000 randomly-oriented samples in grain ±8: band-end accepts carry **median conf 0.647, max 0.872**, against interior accepts' **median 0.588, max 0.829**. Band-end junk is admitted at *higher* confidence than the interior evidence it sits beside, because to be the band max and clear the median test a spike has to be big.
- **"clamp it" is not an available fix — the code already clamps.** `bestT = -searchPx + bestIdx*stepPx` is ±8 by construction. The only real options are *reject* or *gate one-sided*.

Exposure, measured on a real occluding contour (template mesh rasterised at 224 px through `occludingContour({jumpMm: 6, stride: 2})`, 6 yaws, 352 samples):

| grain (uniform ±A, so σ = A/√3 per px) | peak at a band end | accepted at a band end | share of all confident samples | median accepted \|offset\| |
|---|---|---|---|---|
| ±4 (σ 2.3/px — the docstring's "1–3 per px" sensor grain) | 42 / 352 (11.9%) | **0** | — | — |
| ±8 (σ 4.6/px — dim room) | 42 / 352 | **30** | 30/80 = **37.5%** | **8.00 px** |
| ±16 (σ 9.2/px — high ISO) | 42 / 352 | **42 (every one)** | 42/143 = **29.4%** | **8.00 px** |

At 450 mm with `f = 587.5`, 8 px = **6.13 mm**, and `contourPushes`' cap is 3 mm = 3.92 px — so *every* band-end acceptance is a **full-cap push in a direction noise chose**.

The exposure is genuinely confined to noisy captures: at the sensor grain the module's own `minGradient` docstring quotes, the gate holds completely (0 accepts of any kind).

#### 2. The change

`src/track/snap.ts:189-196`, before:

```ts
    if (bestIdx > 0 && bestIdx < steps - 1) {
      const flank = (responses[bestIdx - 1] + responses[bestIdx + 1]) / 2;
      if (flank < 0.45 * best) continue;
    }

    // Sub-pixel: parabola through the peak and its neighbours.
    let t = bestT;
    if (bestIdx > 0 && bestIdx < steps - 1) {
```

after:

```ts
    const interior = bestIdx > 0 && bestIdx < steps - 1;
    if (interior) {
      const flank = (responses[bestIdx - 1] + responses[bestIdx + 1]) / 2;
      if (flank < 0.45 * best) continue;
    } else {
      // A band end has one neighbour, not two, so the same ridge test runs
      // one-sided against the same 0.45. A real edge at or past the band's
      // edge arrives as a RAMP and its inner neighbour carries the ramp; a
      // one-sample spike's neighbour falls back to the band median. Without
      // this branch a band-end peak — 2 of the 17 shipped positions — skipped
      // the gate entirely and was emitted at the maximum |offset|.
      const flank = responses[bestIdx === 0 ? 1 : steps - 2];
      if (flank < 0.45 * best) continue;
    }

    // Sub-pixel: parabola through the peak and its neighbours. A band-end peak
    // has no parabola; its offset stands at the clamp, ±searchPx.
    let t = bestT;
    if (interior) {
```

No new exported constant — `0.45` is reused as a literal exactly as the interior test uses it. (A new `export const` would fail `scripts/check-constants.mjs` without a row in `docs/CONSTANTS.md`.)

**Why one-sided rather than outright reject.** Swept the true-edge offset δ across ±10 px at contrast 50 / grain ±4 on the same real contour:

| δ (px) | ≤7 | 7.5 | 8 | 8.5 | 9 | 10 |
|---|---|---|---|---|---|---|
| current, kept | 100% | 100% | 100% | 100% | 100% | 100% |
| **one-sided, kept** | **100%** | **100%** | **100%** | **100%** | 89–94% | 52% |
| reject, kept | 100% | **52%** | **3%** | **0%** | 0% | 0% |

Outright rejection throws away every genuine snap from about 5.7 mm of geometric error outward — which is the *largest* error the snap exists to correct. One-sided keeps all of it out to the band edge and degrades gracefully past it (the tanh ramp's own inner-neighbour ratio crosses 0.45 at about δ = 10). Threshold ROC, if you want to trade differently: τ = 0.65 cuts noise accepts 70% but keeps only 14% at δ = 10; τ = 0.85 keeps only 37% even at δ = 8.

#### 3. What it moves — measured

**At the snapper's own output (real):**

| grain | claimed/frame (63-sample contour) | mean \|offset\|, confident samples |
|---|---|---|
| ±8 shipped | 16.4 / 63 | 5.12 px |
| ±8 one-sided | 13.3 / 63 (**−19%**) | 4.47 px (**−13%**) |
| ±8 reject | 11.5 / 63 (−30%) | 3.92 px (−23%) |
| ±16 shipped | 27.4 / 63 | 4.98 px |
| ±16 one-sided | 22.7 / 63 (**−17%**) | 4.36 px (**−12%**) |

**At the field the renderer actually consumes — essentially nothing, and that is the finding inside the finding.** 60 frames of the app's loop (`snapOffsets → contourPushes → CalibrationField.update/advance`) at 25° yaw, flat light, truth = 0.000 mm everywhere:

| | max mm | median mm |
|---|---|---|
| grain ±8 shipped | 2.721 | 1.008 |
| grain ±8 one-sided | 2.902 | 0.608 |
| grain ±8 reject | 2.902 | **0.601** |
| grain ±16 shipped | 2.788 | 1.226 |
| grain ±16 one-sided | 2.735 | 1.017 |
| grain ±16 **reject** | 2.735 | **2.090 — worse** |

And on a real +3 px boundary error (contrast 50, grain ±4, truth 2.30 mm) all three modes land on **the same numbers to three decimals** (max 2.559, median 2.139): every sample is confident and interior, so the band-end rule never fires.

The reason the flat-light corruption survives the fix is `CalibrationField`'s agreement gate (`snap.ts:414-420`, `agreementMm: 1.5`, armed once `weight > 3`). It latches onto whatever the first four frames of noise produced and then *refuses the opposite-sign observations that would have averaged it back out*. Isolated by setting `agreementMm: 1e9`:

| grain ±8, shipped rule | agreement gate ON (shipped) | gate OFF |
|---|---|---|
| max mm | 2.721 | 1.956 |
| median mm | 1.008 | **0.517** |

The gate roughly **doubles** the flat-light corruption it was built to prevent. B1's fix is right and cheap, but if the goal is "flat light degrades to the geometric baseline", the band-end gate is the smaller half of the problem. That is a separate finding and I have not costed a fix for it.

#### 4. Bars

- `tests/snap.test.ts` — **nothing goes red.** All five snapper assertions pass under both candidate rules (verified by running the file's own fixtures): edge-at-prediction, +3 px recovery, the −2.83 px diagonal, flat-skin abstention, and the noise bar.
- The noise bar is worth staring at: `'abstains on noise without structure'` asserts `claimed <= samples.length / 4` = 6, and the shipped code claims **exactly 6 of 24** — it sits *on* the bar. One of those six is a band-end acceptance at **−8.00 px, confidence 0.67**. Both fixes take it to 5.
- **No report moves.** `snapOffsets` has exactly one production caller (`src/app/main.ts:1003`) and zero harness callers — `src/testkit/` contains no reference to `snap` at all, and `reports/occlusion.txt` has no snap row. The module's only coverage outside the browser is `tests/snap.test.ts`.

#### 5. Falsifiability

Tighten the existing bar and add one direct assertion:

```ts
// tests/snap.test.ts, in 'abstains on noise without structure'
assert.ok(claimed <= 5,
  `${claimed}/${samples.length} samples claimed edges in structureless noise`);
```

Measured: shipped **6 → FAIL**; one-sided **5 → PASS**; reject **5 → PASS**. That is the bar going RED on today's code, which is what makes it worth having.

And the mechanism directly, so a future refactor cannot re-open the hole without noticing:

```ts
it('a one-sample spike at a band end is refused like one anywhere else', () => {
  // Flat band, one spike at k = 0 (t = -searchPx). The interior form of this
  // fixture is already refused; the end form was not.
  const s = [{ x: 100, y: 100, nx: 1, ny: 0, depthMm: 450 }];
  const spikeAt = (t0: number) => (x: number) => {
    const t = x - 100;                       // along-normal, source px
    return 140 + (Math.abs(t - t0) < 0.5 ? 60 : 0);
  };
  for (const t0 of [-SNAP_DEFAULTS.searchPx, 0, SNAP_DEFAULTS.searchPx]) {
    const r = snapOffsets(s, spikeAt(t0));
    assert.equal(r.confidence[0], 0,
      `a lone spike at t=${t0} was accepted at ${r.offsetPx[0]} px`);
  }
});
```

Today the `t0 = ±searchPx` legs fail and the `t0 = 0` leg passes — which is the asymmetry in one line.

---

### B2 — `src/track/tracker.ts:1116` — REAL, with the contract inverted in the docs, and it is NOT the "stuck/choppy" mechanism

#### 1. Verdict

**Real.** Inside `track()`, five sites read `input.dt`:

| line | consumer | gap credited? |
|---|---|---|
| `tracker.ts:835 → 847-848` | motion prior (`dtSolve + gapSeconds`) | ✅ |
| `tracker.ts:1019` | stall reset (`… + gapSeconds`) | ✅ |
| `tracker.ts:1082` | velocity clock (`state.velTime += … + gapSeconds`) | ✅ |
| **`tracker.ts:1116`** | **One Euro** (`input.dt` bare) | ❌ |
| `tracker.ts:1200` | latch pursuit slew (`input.dt > 0 ? input.dt : 1/30`) | ❌ |

So **"the one clock never credited" is off by one** — the latch pursuit at `:1200` is uncredited too. I would leave that one alone: the pursuit walks an anchor toward the raw pose only while the head reads as still, and nothing observed the gap, so under-stepping there is defensible. The smoother's is not.

The decisive evidence that the credit belongs to the smoother is the app, not the doc. `src/app/framelock.ts:126` computes `captureDt` as *"Seconds since the previously **SUBMITTED** frame"*, and `src/app/main.ts:738/841` hands that same value to `track()` on missed and consumed frames alike. So on a recovery frame `input.dt` is one detector interval and the dropped time is banked in `state.lostSeconds` — exactly the model `tracker.ts:993-1000` describes in prose ("*miss() banks their dt in lostSeconds, and the frame that recovers must credit it*") and that three of the four clocks implement.

**`TrackInput.dt`'s own docstring (`tracker.ts:467-470`) states the opposite contract** — *"Seconds since the previous frame that was actually consumed. Not the camera interval"* — which no caller in the tree honours, and which, if a caller ever did honour it, would make lines 847/1019/1082 double-count. That docstring is a second, separate defect and should be fixed in the same edit.

Magnitude bound: `lostSecondsBeforeReset = 0.5 s`, and `miss()` calls `smoother.reset()` at the crossing, so the largest uncredited gap is 14 frames / 0.467 s at 30 fps. It is not "almost no time" — it is one frame's worth when up to 15 frames' worth passed.

#### 2. The change

`src/track/tracker.ts:1115-1117`, before:

```ts
  let smoothed = options.smooth
    ? state.smoother.filter(result.pose, input.dt, noiseScale)
    : poseClone(result.pose);
```

after:

```ts
  // The dropout gap belongs to the filter's clock too. `input.dt` is the
  // interval since the previous SUBMITTED frame (see `FrameLock.captureDt`);
  // the time the misses banked rides in `gapSeconds`, and the motion prior,
  // the stall reset and the velocity clock above all already credit it. Same
  // idiom as `state.velTime`, deliberately — the two clocks describe the same
  // wall time and must agree exactly.
  let smoothed = options.smooth
    ? state.smoother.filter(
      result.pose, (input.dt > 0 ? input.dt : 1 / 30) + gapSeconds, noiseScale)
    : poseClone(result.pose);
```

And, in the same edit, `tracker.ts:467-470`:

```ts
  /** Seconds since the previous frame the tracker was CALLED on — the frame
   *  lock's submit interval, not the consumed-frame interval. Time inside a
   *  dropout is banked by `miss()` in `state.lostSeconds` and credited back
   *  on the recovering frame by every clock in `track()`; a caller that
   *  pre-added the gap here would have it counted twice. */
  dt: number;
```

#### 3. What it moves — measured

**The hermetic statement first.** Two arms that describe the same wall clock: (A) N frames the tracker *watched* go dark, then a good frame at `dt = 1/30`; (B) the same gap the tracker was simply *not called* during, then the same good frame at `dt = (N+1)/30`. Noiseless landmarks, 120 mm/s slide, `smooth: true`. Every other clock in `track()` already makes these identical:

| N | shipped, \|A − B\| | credited, \|A − B\| |
|---|---|---|
| 1 | 0.7540 mm | 0 |
| 3 | 1.6099 mm | 0 |
| 5 | 2.0809 mm | 4.0e−28 mm |
| 8 | 2.4879 mm | 0 |
| 12 | 2.7867 mm | 0 |
| 14 | **2.8840 mm** | 0 |

**Recovery-frame lag against truth** (same fixture; the raw solve's lag is 0.000, so all of it is the filter):

| N | shipped | credited |
|---|---|---|
| 1 | 3.621 mm | 2.867 mm |
| 5 | 4.963 | 2.882 |
| 12 | 5.694 | 2.907 |
| 14 | **5.798** | **2.914** |

Credited, the recovery lag is **independent of dropout length** (2.87 → 2.91 mm, which is just the filter's steady-state lag at 120 mm/s). That is the property a correctly clocked filter must have.

**The aftermath, which is the part worth knowing** (N = 12, per-frame lag after recovery, mm):

```
frame:      0     1     2     3     4     5     6     7     8    …
shipped: 5.69  1.64  1.37  1.67  2.04  2.36  2.58  2.73  2.83  → 2.97
credited:2.91  2.91  2.91  2.92  2.92  2.93  2.93  2.93  2.94  → 2.96
```

The short `dt` inflates `raw = Δx/dt` by up to 15×, so after the one stalled frame the beta term blows the cutoff open and the filter runs **effectively off for about five frames**, overshooting to *less* lag than steady state. So the shipped signature is **one frame of gross lag followed by ~5 unfiltered frames** — a jerk and a wobble, once per dropout.

**On the wearer's own reported fixture** (`smooth: true`, ±30 mm 0.75 Hz wave, `sigmaPx = 0.7`, peak lag over the 15 frames after recovery, against a 3.28 mm no-dropout baseline over the same frames):

| N | shipped excess | credited excess | peak per-frame step, shipped → credited |
|---|---|---|---|
| 5 | −0.07 mm | +0.15 mm | 4.78 → 4.82 |
| 8 | +1.32 | +0.21 | 13.62 → 15.67 |
| 12 | +2.17 | +0.08 | 31.31 → 33.88 |
| 14 | **+2.40** | **+0.00** | 39.45 → 42.15 |

The trade is explicit: the fix removes up to 2.4 mm of recovery lag and adds up to ~2.7 mm to the single catch-up step. Gaps of 1–5 frames are worth −0.13 to +0.39 mm either way — nothing.

#### Does this support "stuck/choppy"? **No, and I would say so plainly.**

- **In `'locked'` at rest the fix is bit-identical.** 600-frame still session, latch engaged 97–98% of frames, with 150 dropped frames: median step 0.0000, p99 0.035, worst 0.090, median lag 0.081 — **the same four decimals in both arms**. The emitted pose while latched is `poseClone(state.latchedPose)` (`tracker.ts:1219`); the One Euro output is not emitted at all. Whatever made the locked latch feel stuck, it is not this.
- **Where the fix does bite, it trades stuck for chop, not the reverse.** Locked + slow drift + 10-frame dropouts: median lag 1.060 → 1.052 mm, worst per-frame step **1.361 → 2.296 mm**. `smooth: true` + slow drift: median lag 0.456 → 0.420, p99 step **0.785 → 1.712**.
- **A still head sees no aftermath at all** (40 noise seeds, σ 0.7 px, N = 12): total emitted travel over frames 1–8 is 0.326 mm shipped vs 0.344 mm credited. The under-smoothing burst needs real displacement through the gap to exist.

The honest case for the fix is not the wearer's complaint. It is that a filter told the wrong `dt` does not have a cutoff in Hz, and that a gap the tracker watched and a gap it slept through must produce the same output. The 2.9 mm is the size of the lie.

#### 4. Bars

- **`tests/core.test.ts:1836` "the OneEuro arithmetic is unchanged: golden values" — untouched.** It constructs `new OneEuro(settings)` and calls `filter(x, 1/30)` directly. The change is at the tracker's call site and never reaches it.
- **`tests/core.test.ts:1817` "smooth:true is exactly the default PoseSmoother over the raw solves" — untouched, and still bit-exact.** Its fixture drives 40 consecutive good frames at `dt = 1/30`; `gapSeconds` is 0 throughout, so the added term is `+0`.
- The three tests that do combine `landmarks: null` with smoothing — `'the quiet streak does not straddle a dropout'` (`:2293`), `'a gap split across misses and a slow recovery frame still resets the window'`, `'a brief dropout does not corrupt the velocity clock'` — all assert on `r.latched`, `r.velMmS` and `r.priorShare*`, every one of which is computed from `result.pose` (raw) and the raw velocity ring, never from `smoothed`. They stay green.
- **`reports/track.txt` moves in exactly two cells, and `scripts/check-reports.mjs` will go red on the body hash.** Replicated at seed 11:
  - `v2` arm: **every row identical**, and jitter identical to three decimals (1.052 / 2.104 / 3.057) — it has `lost 0` in every bucket, so there is no gap to credit.
  - `average-head` arm, yaw 0 (the only bucket with `lost 7`): depth err **7.73 → 7.80 mm**, total err **10.91 → 10.95 mm**. Rot err, all other buckets, and all jitter figures unchanged.

  That is a bar that needs **re-measuring**, not a bar doing its job — the arm that moves is v1's-situation control, and it moves by 0.7% on one cell.

#### 5. Falsifiability

The equivalence above is the assertion, and it is exact — no tolerance to argue about:

```ts
it('a dropout the tracker watched and one it slept through are the same gap', () => {
  // Every clock in track() credits state.lostSeconds back on the recovering
  // frame. If one of them does not, these two arms diverge.
  const warm = (state: TrackerState) => {
    for (let f = 0; f < 20; f++) {
      track(state, { ...frameAt(f), intrinsics: K, dt: 1 / 30 });
    }
  };
  const N = 12;
  const watched = createTracker(model, { smooth: true });
  warm(watched);
  for (let k = 0; k < N; k++) {
    track(watched, { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 });
  }
  const a = track(watched, { ...frameAt(20 + N), intrinsics: K, dt: 1 / 30 });

  const slept = createTracker(model, { smooth: true });
  warm(slept);
  const b = track(slept, { ...frameAt(20 + N), intrinsics: K, dt: (N + 1) / 30 });

  const dMm = Math.hypot(a.pose!.t[0] - b.pose!.t[0],
    a.pose!.t[1] - b.pose!.t[1], a.pose!.t[2] - b.pose!.t[2]);
  assert.ok(dMm < 1e-9,
    `the watched gap and the slept gap emit poses ${dMm.toFixed(4)} mm apart — ` +
    'a clock inside track() did not credit state.lostSeconds');
});
```

**How to make it RED:** it already is. On today's tree with a moving `frameAt` (120 mm/s is enough) it measures **2.7867 mm** at N = 12; with the fix it measures **0**. Sabotage in the other direction — reverting the single term — reproduces the 2.79. Add a `N = 1` leg too: it measures 0.7540 mm today, so the test convicts even a one-frame blink.

---

### B3 — `src/track/tracker.ts:403` — REAL as stated, but the implied fix is **backwards**

#### 1. Verdict

**Real as a factual claim, and I would not change the counter.** `state.framesTracked` is incremented once at `tracker.ts:1002` and initialised at `:447`; `grep` over the whole tree finds no other write. Neither `miss()`'s reset block (`:1585-1600`) nor `adoptAuditPose` (`:1537`) clears it. So it is session-cumulative and the comment at `:403` — *"Frames since the last full acquisition"* — is false.

**Every reader, and what its threshold means:**

| # | reader | what it means **today** (cumulative) | what the comment says it means (per-acquisition) |
|---|---|---|---|
| 1 | `tracker.ts:965` — `state.framesTracked % options.basinAuditInterval === 0`, guarded by `state.lastRaw` | *every 30th gate-passing frame of the SESSION* — a fixed amortised rate of one cold audit per 30 tracked frames | *the 30th frame after each acquisition* — the audit's phase restarts at every reacquisition |
| 2 | `src/app/diagnostics.ts:246` — `framesTracked: t?.framesTracked ?? null`, emitted next to `acquisitions` (also cumulative) | a session total; `framesTracked / acquisitions` is the **mean frames per lock**, which is a real statistic | "frames since the last lock" — a small number describing only the current spell |
| — | `src/testkit/report-occlusion.ts:648 / 798 / 1310` | **not a reader.** That is `StabilityResult.framesTracked = perFrame.length`, a same-named local field with no relation to `TrackerState` | — |

Reader 1's *option* docstring (`tracker.ts:146`, *"Every this-many tracked frames"*) already describes the cumulative behaviour correctly. It is only the state field's comment that is wrong.

#### 2. The change — fix the comment, not the counter

`src/track/tracker.ts:402-404`, before:

```ts
  lostSeconds: number;
  /** Frames since the last full acquisition. */
  framesTracked: number;
```

after:

```ts
  lostSeconds: number;
  /**
   * Frames this SESSION whose solve passed the gate. Cumulative and never
   * reset — not per-acquisition, whatever this comment used to say. Two
   * readers depend on that: the basin audit's cadence
   * (`framesTracked % basinAuditInterval`), which needs the cumulative
   * reading to hold its amortised rate, and the diagnostics paste, where
   * `framesTracked / acquisitions` is the mean frames per lock only under
   * this reading. Measured before touching it: resetting the counter at each
   * acquisition starves the audit exactly where a wrong basin is most
   * likely — 13 audits over 428 tracked frames becomes 1, on a session that
   * reacquires more often than the 30-frame period.
   */
  framesTracked: number;
```

#### 3. What it moves — measured

I built the per-acquisition variant (`if (coldAcquired) { state.acquisitions++; state.framesTracked = 0; }`) and ran both against the same sessions (`smooth: true`, 20° yaw + 20 mm lateral wander, σ 0.7 px, dropouts long enough to pass the 0.5 s reset):

| session | tracked frames | acquisitions | audits, **cumulative (shipped)** | audits, **per-acquisition (as documented)** |
|---|---|---|---|---|
| no dropouts, 600 frames | 600 | 1 | 19 (spacing 30/30) | 19 (spacing 30/30) — identical |
| 20-frame gap every 90 | 480 | 7 | 13 (2.7 / 100 frames) | 13 (2.7 / 100) — identical |
| 20-frame gap every 37 | 428 | 24 | **13 (3.0 / 100)** | **1 (0.2 / 100)** |
| 20-frame gap every 31 | 339 | 29 | **10 (2.9 / 100)** | **1 (0.3 / 100)** |

The implemented semantics holds 2.7–3.2 audits per 100 tracked frames in every regime. The documented semantics collapses to **one audit in an entire flaky session** — because a counter that restarts at every reacquisition never reaches 30 when reacquisition arrives every ~17 tracked frames. A session that keeps losing and regaining the face is precisely the one whose warm chain is most likely to be in the wrong basin, and the "documented" behaviour would switch the audit off there.

Diagnostics: no numeric change, only what the field is understood to mean. Adopting per-acquisition would also silently change the paste's `framesTracked` from a session total (e.g. 480) to a spell length (e.g. 40), and break the `framesTracked / acquisitions` reading a reviewer would naturally take.

#### 4. Bars

**Nothing goes red under the recommended comment-only change.** No test in the tree references `TrackerState.framesTracked` (grep over `tests/` returns nothing), no report reads it, and `docs/` never names it. That is the real problem: **the field's semantics is pinned by nothing at all**, which is how a comment and its code drifted apart with no gate noticing. The `report-occlusion.ts` name collision is worth knowing about — it is exactly the shape of thing a later reader "fixes" into agreement.

If instead someone changes the counter to match the comment, the visible bars stay green (nothing asserts on audit rate either), and the basin audit quietly stops running in flaky sessions. That is the worst possible outcome and it is the one a naive reading of B3 produces.

#### 5. Falsifiability

Pin the **rate**, which is the property the audit's option docstring actually claims, so neither semantics can drift silently again:

```ts
it('the basin audit keeps its amortised rate across reacquisitions', () => {
  // framesTracked is SESSION-cumulative. A per-acquisition counter would
  // never reach basinAuditInterval in a session that reacquires more often
  // than the period, and the audit would stop exactly where a wrong basin
  // is most likely. Measured on this fixture: cumulative 13 audits in 428
  // tracked frames, per-acquisition 1.
  const state = createTracker(model, { smooth: true });
  let tracked = 0;
  for (let f = 0; f < 900; f++) {
    const dark = f % 37 >= 17;            // 20 dark frames past the 0.5 s reset
    const r = track(state, dark
      ? { landmarks: null, sigmaPx: null, intrinsics: K, dt: 1 / 30 }
      : { ...frameAt(f), intrinsics: K, dt: 1 / 30 });
    if (r.tracked && r.rawPose) tracked++;
  }
  assert.ok(state.acquisitions > 10,
    `only ${state.acquisitions} acquisitions — the fixture never lost the face`);
  assert.ok(state.basinAuditsRun >= tracked / (2 * TRACKER_DEFAULTS.basinAuditInterval),
    `${state.basinAuditsRun} audits over ${tracked} tracked frames — the audit ` +
    'cadence is counting something that resets');
});
```

**How to make it RED:** the bar is `428 / 60 = 7.1`. Today's code scores **13** (pass). Insert `state.framesTracked = 0;` into the `if (coldAcquired)` block at `tracker.ts:960` and it scores **1** (fail). The precondition assertion on `acquisitions` is there so the test cannot pass by never losing the face.

---

### Files

- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\track\snap.ts` — B1 at `:189` and `:196`; the neighbouring `CalibrationField` agreement-gate finding at `:414-420`.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\track\tracker.ts` — B2 at `:1116`, its docstring twin at `:467-470`, the uncredited sibling at `:1200`; B3 at `:403`, its reader at `:965`.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\app\framelock.ts:126` — `captureDt` is the **submit** interval; this is what settles B2's contract.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\app\main.ts:738, :841, :1003` — the only production callers of `track()` and `snapOffsets`.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\tests\snap.test.ts` — the noise bar the shipped code sits exactly on.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\tests\core.test.ts:1817, :1836, :2293` — the golden/bit-identical tests B2 does not disturb.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\reports\track.txt` — the two cells B2 moves (average-head, yaw 0).

---

## Cluster C — enrolment. Specification, with measurements.

**Tree state.** I started at `e34f1e3`; the tree moved to `ad8c695` mid-session. Only `src/app/main.ts`, `ui.ts`, `enroll/protocol.ts`, `fit/*`, `render/*`, `track/identity.ts` changed. **`src/enroll/bundle.ts`, `src/enroll/enroll.ts`, `src/enroll/scale.ts` and `src/core/facemodel.ts` are untouched**, so every enrolment measurement below stands and every `enroll/` line number is current. All `main.ts` line numbers are re-verified against `ad8c695` — the review's cited `main.ts:978/979/723-734` are stale by ~8 lines.

Method: everything runs against the pre-built `dist/`; fixes were simulated in a patched copy under my scratchpad. Nothing in the repo was modified and no build was run.

---

### C1 — `BundleReport.converged` cannot go false. **REAL, but the fix the finding implies is worthless. The real defect is next door.**

#### 1. Verified, by construction and by measurement

`C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\enroll\bundle.ts:380` initialises `let converged = true`. The **only** clearing site is `:415`:

```ts
if (!Number.isFinite(stats.rms)) { converged = false; break; }
```

`reprojectionStats` (`bundle.ts:1306`) returns `rms: NaN` on exactly one branch: `if (errors.length === 0)`. Every other return path computes `Math.sqrt(sum / errors.length)` over `errors` whose members are `Math.hypot` of finite quantities (`project()` at `core/camera.ts:85` refuses `!(z > 1e-6)`, which also rejects NaN, so a diverged NaN state lands in the same empty-`errors` branch). So `converged === false` ⇔ **not one landmark in any frame projected in front of the camera**. The review's statement is exact.

`degraded` rests on it at `src/enroll/enroll.ts:382`: `degraded: !coverage.sufficient || !report.converged`.

Measured firing rates:

| population | cells | `converged===false` | λ-ceiling break | `ldlt` failure in `solveGlobal` | `fieldFailures>0` |
|---|---|---|---|---|---|
| healthy (seeds 11+23 × 10 subjects × 3 geometries) | 60 | 0 | 0 | 0 | 0 |
| stressed but solvable (noise ×2…×14, bias 3/6 mm, no-profile, no-turn, 3 frames/beat, `yawUnderRotation` 0.75, `rounds=1`, `iterationsGlobal=2`, `fieldPriorScale` 0 and 1e-9, `shapePrior` 0, all-intrinsics with and without lean) | 36 | 0 | 0 | 0 | 0 |
| total collapse (`noisePx` ≥ 20) | 8 | 8 | 0 | 0 | 0 |

**The gap this leaves is measured, and it is large.** At `noisePx: 10` (14× the shipped 0.7) the pipeline returns `converged: true`, `degraded: false`, `varianceFactor` 1.05–1.09 — and a nose that is **15.10 mm** (S00/eye-level) and **12.80 mm** (broad-low/eye-level) from truth. At `noisePx: 20` the flag finally fires, and by then the returned model is the untouched template (nose RMS identical across camera geometries: 2.860 for S00, 4.171 for broad-low, at both eye-level and phone-lap).

#### 2. What convergence should mean — and the measurement that rules out all three candidates the finding offers

I instrumented `solveGlobal`'s LM loop in a patched copy (step accepted/rejected, index of the last accepted step, λ at exit, relative cost drop of the final accepted step).

**λ ceiling:** never reached, in 96 cells, including `fieldPriorScale: 0`, `shapePrior: 0`, and `{f, pp, k1}` all unlocked on a lean-less capture. A flag that never fires.

**LM step acceptance:** on the last round, the index of the last accepted step (budget `iterationsGlobal = 12`) has median **10**, min 6, max 11 — **55 of 60 cells are still accepting steps when the budget runs out.** But raising the budget shows this test is a mirage:

| `iterationsGlobal` | still budget-bound | rel. cost drop of final accepted step | median nose RMS mm | median bundle ms |
|---|---|---|---|---|
| 12 (shipped) | 11/12 | 6.0e-6 | 1.430 | 309 |
| 20 | 11/12 | 5.0e-8 | 1.425 | 477 |
| 40 | 12/12 | 5.9e-11 | 1.425 | 963 |
| 80 | 7/12 | 2.7e-14 | 1.425 | 1442 |

LM will always find a numerically-downhill step in a smooth problem, so "still accepting" stays true essentially forever while the answer stops moving after iteration 12. **"Still accepting steps" would fire on ~92% of healthy scans and mean nothing.** (Useful side result: the shipped budget is right. The last 0.005 mm of nose accuracy costs 4.7× the wall time.)

**Residual plateau:** the relative cost drop of the *final accepted step of the last round*, over 60 healthy cells: median 2.83e-6, min 3.2e-13, **max 9.20e-4**. Round-over-round `|Δrms|/rms`: median 4.2e-3, p90 7.7e-3, worst 1.04e-2. At any tolerance above 1e-3 a plateau test reports "converged" on 60/60 healthy cells *and* on every stressed-but-solvable cell — i.e. it is exactly as informative as the flag it replaces.

**Conclusion: there is no convergence test to be had.** The bundle always converges. What goes wrong is a converged solve on bad evidence, and no property of the solver can see that.

#### 3. Recommended change

**(a) Complete the catastrophic guard, and rename what it means.** Zero measurable effect — say so out loud in the comment rather than implying a new capability.

`bundle.ts:380`, before:
```ts
  let converged = true;
```
after:
```ts
  // NOT "the solve reached a stationary point" — it never fails to. Measured
  // over 96 enrolments (60 healthy, 36 deliberately stressed) the global LM
  // never hit the lambda ceiling, `ldlt` never refused the reduced system, and
  // the field never failed to factorise; the last accepted step of the last
  // round improves the cost by 2.8e-6 relative on the median and 9.2e-4 at
  // worst. This flag is the CATASTROPHE guard: did the solve leave anything at
  // all behind. It first goes false at 20 px of landmark noise, where not one
  // landmark projects and the returned surface is the untouched template.
  let solveCollapsed = false;
```
`bundle.ts:415`, before:
```ts
    if (!Number.isFinite(stats.rms)) { converged = false; break; }
```
after:
```ts
    if (!Number.isFinite(stats.rms)) { solveCollapsed = true; break; }
```
`bundle.ts:429`, before / after:
```ts
    converged,
```
```ts
    // Every round left the field unsolved, so the "nose" is the shape basis and
    // nothing more. Counted at :430 today and read by nobody.
    converged: !solveCollapsed && fieldFactorisationFailures < opt.rounds,
```

**(b) Do NOT move `degraded` onto a convergence bar.** The three evidence bars that could carry it were each measured and each fails:

- `quality.nose.sigmaMm` tracks landmark noise beautifully (healthy 0.26–0.46 mm → 0.57–1.48 at noise ×4 → 1.81–2.79 at ×14) but `core/facemodel.ts:539-551` already records that it anti-correlates with true error across camera geometries (−0.09, n=18) — it is a conditional precision, and using it would re-introduce exactly the term that file removed.
- `varianceFactor` catches detector *bias* decisively (1.6 healthy → 12.0 at 3 mm bias → 44.6 / 235 at 6 mm) and is blind to honest noise (1.05–1.19 at noise ×4 to ×14, because the synthesizer scales the claimed sigma with the real one). It is already wired into `noseConfidence`'s `agreement` term, which is the right place.
- `noseConfidence(model).value < 0.5` fires on **every phone-lap cell** of the healthy population (0.42–0.48, driven entirely by `agreement`). Marking a third of healthy scans `degraded` would switch off the identity watch at `src/app/main.ts:906` and add the `caveat` measure at `src/fit/score.ts:323` for people whose scans are fine.

The honest specification is: `degraded` keeps its two current terms, and the thing that is missing — an accuracy estimate — is the open question `facemodel.ts:556` already names ("**This build does not have one**"). Do not manufacture one from solver internals.

**(c) One thing worth adding that does move a published number: `perRound` currently reports `costGlobal`/`costField` and nothing about how the LM exited.** Adding `stepsAccepted` and `lastAcceptedIter` per round costs nothing and makes the budget-bound result above discoverable without patching the tree.

#### 4. What goes red

Nothing. `converged` is false on exactly the same inputs; `tests/pipeline.test.ts:651` (`!degraded` on a healthy scan) and `:799` (`degraded` on a turn-less scan — carried by `coverage.sufficient`, not by this flag) both stay green. `scripts/check-reports.mjs` strips comments before hashing, so a comment-only edit does not even move `source`; the `converged` rename does move it, and the canary then reports "the numbers did not move" and passes (`check-reports.mjs:320-326`).

#### 5. Falsifiability

Add to `tests/pipeline.test.ts`:

```ts
it('the collapse guard actually fires, and only on collapse', () => {
  const subject = generatePopulation(mesh, basis, { count: 1 })[0];
  const geometry = CAMERA_LADDER[0];
  const at = (noisePx: number) => {
    const cap = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 8, noisePx });
    return enroll({ mesh, basis, frames: cap.frames.map((f) => ({
      landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
      silhouette: f.silhouette, beat: f.beat })),
      imageWidth: geometry.width, imageHeight: geometry.height });
  };
  assert.ok(at(0.7).bundle.converged, 'a healthy scan reported a collapsed solve');
  const dead = at(28);
  assert.ok(!dead.bundle.converged && dead.model.degraded,
    'twenty-eight pixels of landmark noise left not one projectable landmark, ' +
    'and the scan still reported itself converged');
});
```
**To make it RED:** revert `:415` to `converged = false` on any weaker condition, or delete the `!coverage.sufficient || !report.converged` term at `enroll.ts:382` — the second assertion fails immediately. (I ran both halves against `dist/`: `at(0.7)` gives `converged: true`, `at(28)` gives `converged: false, degraded: true`, `reprojectionRmsPx: NaN`.)

---

### C2 — `collectFrame` overwrites `visibility === null` with `fill(1)`. **REAL, but MIS-STATED: the blast radius is one frame per scan, not every frame, and the fingerprint the finding cites belongs to the bug that is already fixed.**

#### 1. Verified, restated precisely

`src/app/main.ts:1043-1045` (current HEAD; the review cites `:978`):
```ts
    visibility: visibility
      ? new Float64Array(visibility)
      : new Float64Array(app.mesh.vertexCount).fill(1),
```
and the deliberate producer at `:770-772`:
```ts
    // Before the first pose there is nothing to rasterise against, so nothing is
    // known to be hidden. `null` rather than a confident `fill(1)`.
    : { sigmaPx: acquisitionSigma(...), visibility: null };
```

**The correction.** `visibility` is null only when `app.lastPose` is null at `:761`. `app.lastPose` is set at `:808` on the first successful scan-phase PnP and cleared at `:803` (unstable fit) and `:1302` (`resetPerson`). The `:803` branch returns before `collectFrame`. So the `fill(1)` reaches **exactly one collected frame per pose acquisition** — the frame on which `acquire` flips to `scan` — plus one per subsequent instability episode. Not "every production frame". The review's cited fingerprint (`noseObservations == framesUsed`) is the signature of the *whole-stream* fill(1) that `main.ts:754-760` documents as already fixed.

Effect confinement is correct: `frame.visibility` is read in exactly one place, `src/enroll/enroll.ts:465` (`const w = frame.visibility[i]`) inside `perVertexUncertainty`, plus serialisation at `enroll/telemetry.ts:115,159`. `bundle.ts:99` says so ("used only for reporting coverage") and nothing in `bundle.ts` reads it.

#### 2. Blast radius, measured (4 subjects × 3 geometries, seed 11)

**Production-faithful (frame 0 only):** identical to true visibility in **11 of 12 cells**. The one difference is `broad-low/eye-level` — the only cell where frame 0 survived keyframe selection:

| | true visibility | frame-0 `fill(1)` | Δ |
|---|---|---|---|
| nose `observations` | 11.833 | 12.222 | +0.389 (+3.3%) |
| nose `parallaxRms` | 29.60° | 29.49° | −0.11° |
| nose `sigmaMm` | 0.4570 | 0.4519 | −0.0051 mm |
| `noseConfidence` | 1.000 | 1.000 | 0 |
| nose RMS vs truth | 1.8520 | 1.8520 | 0 |

**Whole-stream `fill(1)` (the historical bug, upper bound):** `observations` = `framesUsed` = 24.00 exactly in all 12 cells — the documented fingerprint reproduced; `parallaxRms` +9.3° to +12.8°; nose `sigmaMm` −0.039 to −0.078 mm (the nose is reported 10–25% more precisely than it is, and that number is printed to the wearer at `src/app/ui.ts:357`, "nose measured to X mm"); nose RMS vs truth **unchanged to four decimals in all 12 cells** — visibility never touches the solve.

**`noseConfidence` did not move in a single cell, at any fill fraction.** I swept the fill fraction 0 → 1/196 → 5% → 10% → 25% → 50% → 100%: the `observed` term reads **1.000 everywhere**. Healthy `observations / framesUsed` runs 0.415–0.513 against `NOSE_OBSERVED_FRACTION = 0.40`, so the term is 1.04–1.28 before its clamp. **The branch the finding says fill(1) disabled cannot fire on a healthy scan with true visibility either** — `core/facemodel.ts:497-499` says the bar was deliberately set "just under the measured minimum". The phone-lap confidences of 0.42–0.47 are `agreement` (varianceFactor 4.0–4.7 against `TYPICAL_VARIANCE_FACTOR` 1.9), not `observed`.

#### 3. The fix

Making `visibility` nullable end-to-end would touch `App.collected`, `enroll-client.ts:319`, `enroll.worker.ts:74,133`, `BundleFrame.visibility`, and `enroll.ts:465`. Not worth it. The frame that has no visibility also has a **flat `acquisitionSigma`** (`detect/uncertainty.ts:229-234`, `floorPx * 2` for every landmark) — it is the least informative frame in the scan. Drop it.

`src/app/main.ts:1040`, before:
```ts
  app.collected.push({
    landmarks: new Float64Array(landmarks),
    sigmaPx: new Float64Array(sigmaPx),
    visibility: visibility
      ? new Float64Array(visibility)
      : new Float64Array(app.mesh.vertexCount).fill(1),
    silhouette: null,
    beat,
  });
```
after:
```ts
  // `null` visibility means this frame arrived before any pose existed, so
  // nothing could be rasterised — which also means its `sigmaPx` is the flat
  // `acquisitionSigma`, not a per-landmark estimate. It is the least
  // informative frame in the scan, and `fill(1)` asserted the opposite: that
  // every landmark, including the far-side nose, was fully visible. There is
  // exactly one such frame per pose acquisition (plus one after each
  // `SCAN_MAX_RMS_PX` refusal). Dropping it costs nothing measurable; keeping
  // it and lying about it cost 0.005 mm of reported nose precision on the one
  // cell in twelve where it reached the keyframe selection.
  if (!visibility) return;
  app.collected.push({
    landmarks: new Float64Array(landmarks),
    sigmaPx: new Float64Array(sigmaPx),
    visibility: new Float64Array(visibility),
    silhouette: null,
    beat,
  });
```
The protocol is unaffected: `advanceProtocol` already ran at `:821` before `collectFrame` at `:824`.

#### 4. What it moves — measured

Simulated by dropping frame 0 from the capture (6 cells, seed 11): nose RMS changes in **1 of 6** cells, by **−0.0097 mm** (broad-low/eye-level 1.8520 → 1.8423); `observations` changes in 1 of 6 by +0.005; `noseConfidence` unchanged everywhere. **No published number moves.** `reports/enroll.txt` is generated from the harness, which never takes this path, so it is untouched.

#### 5. Tests / bars

Nothing goes red. `tests/pipeline.test.ts:662-667` (`q.observations < model.framesUsed`) is the existing fingerprint for this bug class, but it runs on harness frames which always carry true visibility, so it never saw the one-frame version and will not see the fix. `tests/app.test.ts:119-131` fingerprints the *wear*-branch `track()` call and is unaffected.

#### 6. Falsifiability

`main.ts` boots at module scope and cannot be imported under Node, so use this tree's established textual idiom (`tests/app.test.ts:119`), reading the built `dist/src/app/main.js`:

```ts
it('the scan does not invent visibility for the pre-pose frame', () => {
  const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
  const fn = text.slice(text.indexOf('function collectFrame'));
  const body = fn.slice(0, fn.indexOf('\nfunction ', 1));
  assert.match(body, /if \(!visibility\)\s*return/,
    'collectFrame no longer refuses the pre-pose frame');
  assert.doesNotMatch(body, /fill\(1\)/,
    'collectFrame is asserting full visibility for a frame nothing was rasterised against');
});
```
**To make it RED:** restore either arm of the ternary — the `doesNotMatch` fails on the `fill(1)` and the `match` fails on the missing guard.

---

### C3 — `silhouette: null` on every production frame. **REAL. Verdict: WIRE IT UP. The term is worth a replicated 0.29 mm off the standoff p90 and the benefit survives realistic edge noise.**

#### 1. Verified

Two production sites hard-code it: `src/app/main.ts:1046` and `src/app/enroll.worker.ts:134`. `useSilhouette` defaults `true` (`bundle.ts:245`), so all five silhouette paths (`bundle.ts:681`, `:1035`, `:1129`, `:1147`, `:1209`) enter and immediately `continue` on `!frame.silhouette`. `BundleReport.silhouetteResiduals` (`bundle.ts:424`) is therefore 0 on every real scan — and **it has zero consumers anywhere in `src/` or `tests/`**, so nothing notices.

`silhouette: null` with `useSilhouette: true` is behaviourally identical to `useSilhouette: false`, so the harness's `no-silhouette` variant *is* the production configuration. That makes the measurement direct.

#### 2. What the term is worth when it IS supplied — 5 seeds {11,23,37,41,53} × 10 subjects × 3 geometries = 150 paired cells

Median-of-seeds (the campaign estimator; per-seed values in brackets):

| metric | silhouette ON | OFF | Δ | seeds where ON wins |
|---|---|---|---|---|
| \|standoff\| p90 | **1.626** [1.561/1.691/1.626/1.747/1.338] | 1.913 [1.818/2.170/2.189/1.913/1.630] | **−0.287 mm (−15.0%)** | **5/5** |
| \|standoff\| worst | **1.929** | 2.341 | **−0.412 mm (−17.6%)** | **5/5** |
| \|standoff\| median | 0.712 | 0.795 | −0.083 mm | 4/5 |
| nose median | 1.541 | 1.560 | −0.019 mm | 3/5 (80/150 cells) |
| bridge median | 1.447 | 1.405 | +0.042 mm | 2/5 |
| **pad-strip median** | 1.187 | 1.131 | **+0.056 mm (worse)** | **1/5** (56/150 cells) |

Standoff is the measurement `reports/enroll.txt` describes as the one "which decides whether a frame clears the face at all", and it is the only metric where the silhouette wins in every seed on both tail statistics. The pad strip is the price and it replicates as a price.

#### 3. Would it survive a real edge detector? — measured

The harness supplies a *noise-free contour of the true geometry* (`testkit/synthetic.ts:722`, `extractSilhouette` of the truth depth buffer). A production silhouette would come from `snapOffsets` reading image luminance. So I degraded the supplied contour (2 seeds × 8 subjects × 3 geometries = 48 cells):

| silhouette supplied as | nose median | pad median | \|standoff\| p90 | \|standoff\| worst |
|---|---|---|---|---|
| clean (harness) | 1.062 | 1.019 | 1.561 | 2.027 |
| + 2 px Gaussian per sample | 1.074 | 1.039 | 1.703 | 2.014 |
| + 5 px Gaussian per sample | 1.078 | 1.018 | 1.681 | 2.222 |
| + 2 px and 50% of samples dropped | 1.043 | 1.033 | 1.610 | 1.978 |
| + 3 px constant translation | 1.035 | 1.032 | 1.701 | 2.076 |
| + 5% radial dilation (systematic bias) | 1.082 | 0.994 | 1.859 | 2.393 |
| **none (production today)** | **1.200** | 0.928 | **1.969** | **2.341** |

Every supplied variant beats "none" on the nose median (1.03–1.08 against 1.20) and on the standoff p90 (1.56–1.86 against 1.97), including a 5% systematic dilation, which is a larger bias than a competent edge detector would carry. **The term is not fragile.** The pad-strip cost is also robust — it is a real cost, not a noise artefact.

#### 4. Recommended change: wire it, in two steps

**Step 1 — stop the silence being silent.** This is the change I would land first, because it is small and it makes the gap visible in the diagnostics that already exist.

`src/enroll/enroll.ts`, after the bundle runs (~`:157`):
```ts
  if (bundleOptions.useSilhouette !== false && report.silhouetteResiduals === 0) {
    notes.push('no silhouette was supplied — the profile contour term was skipped');
  }
```
This is not cosmetic: `docs/CONSTANTS.md`'s `silhouetteWeight` row publishes `1.0` as **`measured`**, and that sweep was run entirely on a term production never executes. A note that says so keeps the constant honest until step 2 lands.

**Step 2 — supply it.** The machinery exists and is already used per-frame in the wear phase (`runEdgeSnap`, `src/app/main.ts:~1140`): `rasterize` the mesh at the solved pose → `occludingContour` → `snapOffsets` against `app.lock.display` → the matched image points are exactly the `Float64Array` of `[x, y, …]` in intrinsics pixels that `buildSilhouetteIndex` (`bundle.ts:899`) wants. During the scan the pose is solved against the template, which is good enough — the contour only has to be near enough for `nearestSilhouette`'s grid to match, and the 5%-dilation row above bounds the damage from getting it wrong. Note `extractSilhouette` (`core/raster.ts:213`) is **not** the function you want: it returns the *predicted* contour of the model, and its docstring already says it has zero callers.

**Do not remove the term.** Removing it would delete a replicated 0.29 mm tail improvement on the one metric the report says decides whether a frame clears the face, and would silently invalidate the `silhouetteWeight` sweep, three tests in `tests/pipeline.test.ts:1043-1145`, and the harness's `no-silhouette` ablation row.

#### 5. What goes red

Step 1 changes `enroll.ts` → `check-reports.mjs` `source` hash drifts → the canary runs → the enroll canary is generated from the harness, which *does* supply silhouettes, so `silhouetteResiduals !== 0` and no note is added, so **the canary matches and the gate passes with "the numbers did not move"**. Step 2 changes no library code at all (it changes `main.ts`, which no report imports).

The three tests at `tests/pipeline.test.ts:1043-1145` build their own frames with `f.silhouette` and stay green in both steps.

#### 6. Falsifiability

```ts
it('a production-shaped scan says out loud that it had no silhouette', () => {
  const subject = generatePopulation(mesh, basis, { count: 1 })[0];
  const geometry = CAMERA_LADDER[0];
  const cap = synthesizeCapture(mesh, subject, geometry, { framesPerBeat: 8 });
  // Exactly what `collectFrame` and `enroll.worker.ts` hand the bundle.
  const asProduction = enroll({ mesh, basis, frames: cap.frames.map((f) => ({
    landmarks: f.landmarks, sigmaPx: f.sigmaPx, visibility: f.visibility,
    silhouette: null, beat: f.beat })),
    imageWidth: geometry.width, imageHeight: geometry.height });
  assert.equal(asProduction.bundle.silhouetteResiduals, 0);
  assert.ok(asProduction.model.notes.some((n) => /silhouette/.test(n)),
    'the contour term was skipped on every frame and the scan did not say so');
});
```
**To make it RED:** delete the `notes.push`, or set `useSilhouette: false` in `BUNDLE_DEFAULTS` without also removing the term. Once step 2 lands, invert it — assert `silhouetteResiduals > 0` on a scan built the way `collectFrame` builds one — and it goes red the moment somebody re-hardcodes `silhouette: null`.

---

### C4a — PD accepted on [45, 85], reported through [46, 80]. **REAL, and worse than stated.**

`src/enroll/enroll.ts:187` gates the *correction*: `input.knownPdMm >= 45 && input.knownPdMm <= 85` (matching `main.ts:239` and `:1636`, the `set-pd` handler). `enroll.ts:313` gates the *readout* through `PD_PLAUSIBLE_MM = [46, 80]` (`src/enroll/scale.ts:92`). Because the correction at `:189` scales the geometry by `knownPdMm / span`, `interpupillarySpan` afterwards **equals `knownPdMm` by construction** — so the readout gate is being applied to the wearer's own typed number.

Measured (S00/eye-level, seed 11):

| `knownPdMm` | `scale.source` | `scale.factor` | `model.pdMm` | note produced |
|---|---|---|---|---|
| 44.9 | iris | 1.39565 | 61.779 | "the PD supplied (44.9 mm) was not used — outside the 45 to 85 mm human range" ✔ correct |
| **45.0** | **pd** | **1.01660** | **null** | **"pupillary distance not reported — the measured eye span is outside the human range, so something in the eye landmarks or the scale is wrong"** |
| 45.5 | pd | 1.02790 | null | same |
| 45.99 | pd | 1.03897 | null | same |
| 46.0 | pd | 1.03919 | 46.000 (±0.500) | — |
| 80.0 | pd | 1.80729 | 80.000 (±0.500) | — |
| **80.01** | **pd** | **1.80752** | **null** | same self-blaming note |
| 85.0 | pd | 1.92025 | null | same |
| 85.1 | iris | 1.39565 | 61.779 | correct refusal |

So on `[45, 46) ∪ (80, 85]` the scan **is resized by the wearer's PD** — the whole geometry, every millimetre downstream — and then refuses to print that same number back, blaming the eye landmarks. `ui.ts:349-354` simply omits the PD line, so the wearer sees the scan silently adopt their number and silently deny having it.

**Fix — one range, named once.** In `src/enroll/scale.ts:92`:
```ts
export const PD_PLAUSIBLE_MM: readonly [number, number] = [46, 80];
```
→
```ts
/**
 * The span a pupillary distance has to fall inside to be accepted as a ruler
 * or reported as a measurement — one range, because these were two.
 *
 * The acceptance gate in `enroll` was [45, 85] (matching the app's `set-pd`
 * handler) and the readout gate here was [46, 80]. Since the correction sets
 * `interpupillarySpan` EQUAL to the wearer's figure, a PD of 45.0 or 82.0 was
 * accepted as the ruler, resized the whole scan, and was then refused as a
 * readout with "something in the eye landmarks or the scale is wrong" — about
 * a number the wearer had typed. 45 to 85 is the range the app already
 * promises; the readout has no business being narrower than the ruler.
 */
export const PD_PLAUSIBLE_MM: readonly [number, number] = [45, 85];
```
and at `enroll.ts:187`, replace the literals with the constant:
```ts
    if (span > 1 && input.knownPdMm >= PD_PLAUSIBLE_MM[0] && input.knownPdMm <= PD_PLAUSIBLE_MM[1]) {
```
(and the wording at `enroll.ts:288` becomes `` `it is outside the ${PD_PLAUSIBLE_MM[0]} to ${PD_PLAUSIBLE_MM[1]} mm human range` ``). `main.ts:239` and `:1636` should import the same constant rather than repeat `45`/`85`.

**What it moves.** Nothing on the shipped population: no synthetic subject and no `reports/enroll.txt` row supplies `knownPdMm`, and the widened range only affects the two dead bands. `model.pdMm` becomes non-null for wearers on `[45,46) ∪ (80,85]`, with `pdSigmaMm = span × sigma` as for everyone else. If you would rather *narrow* the acceptance gate to [46,80] instead, that is also self-consistent — but it must move `main.ts:239`, `main.ts:1636` and `enroll.ts:288` together, and it refuses a genuinely small adult rather than reporting them.

**Bars.** None. `tests/core.test.ts:713,750` pin a serialisation round-trip at `pdMm: 61.8`, well inside both ranges.

**Falsifiability.**
```ts
it('a PD it accepted as a ruler is a PD it will report', () => {
  for (const pd of [PD_PLAUSIBLE_MM[0], 61.8, PD_PLAUSIBLE_MM[1]]) {
    const r = enroll({ /* healthy capture */, knownPdMm: pd });
    assert.equal(r.model.scale.source, 'pd', `${pd} mm was not taken as the ruler`);
    assert.ok(r.model.pdMm !== null && Math.abs(r.model.pdMm - pd) < 1e-6,
      `the scan was resized by ${pd} mm and then refused to report it`);
    assert.ok(!r.model.notes.some((n) => /eye landmarks or the scale is wrong/.test(n)));
  }
});
```
**To make it RED:** restore `[46, 80]` in `PD_PLAUSIBLE_MM` — the endpoints fail on `pdMm === null` immediately (verified against `dist/`: at 45.0 today, `scale.source === 'pd'` and `pdMm === null`).

---

### C4b — `fieldRmsMm` is in pre-scale gauge units, not mm. **REAL, and the gauge is not a constant — it varies per solve by up to 2.7×.**

`bundle.ts:431` computes `displacementStats(state.field).rmsMm` inside `runBundle`, which finishes at step 4 of `enroll`. `applyScale(..., field)` runs afterwards at `enroll.ts:175` (iris/PD ruler) and again at `:189` (wearer's PD), and `enroll.ts:363` recomputes `displacementStats(field)` for `model.displacementRmsMm`. So the two differ by exactly the scale factor.

Measured (4 subjects × 3 geometries, seed 11) — `report.fieldRmsMm × scale.factor === model.displacementRmsMm` to full precision in **all 18 cells**:

- `scale.factor`: median **1.462**, range **1.099 – 2.732**
- `fieldRmsMm` understates the millimetre figure by **9.0% – 63.4%, median 31.6%**
- It is *invariant to the ruler*: with `knownPdMm` 55 / 61.8 / 70 the reported `fieldRmsMm` stays 0.6965 while the true field goes 0.8654 / 0.9724 / 1.1015 mm.
- Worse, it is not comparable between scans of the same face: **S00 on eye-level reports 0.6965 and on phone-lap 0.4758, for fields that are really 0.9721 and 0.9994 mm.** The pre-scale gauge tracks the unsolved focal length, so the number moves with the camera, not the nose.

The docstring's "mm" is false, and the "Zero on a real face means the stage did not run" half is still true (zero is zero in any gauge).

**Fix.** The value cannot be converted inside `runBundle` — the scale is solved two steps later — so name it honestly. `bundle.ts:296-298`, before:
```ts
  /** RMS of the solved free-form field, mm. Zero on a real face means the
   *  stage did not run — it is not a plausible measurement. */
  fieldRmsMm: number;
```
after:
```ts
  /**
   * RMS of the solved free-form field, in the SOLVE'S OWN GAUGE — not mm.
   *
   * The bundle finishes before `solveScale`, so this is the field measured in
   * whatever units the reconstruction came out in, and that gauge tracks the
   * camera's unsolved focal length rather than the wearer. Measured over 18
   * enrolments the scale factor applied afterwards runs 1.099 to 2.732 (median
   * 1.462), so this number understates the millimetre figure by 9% to 63%, and
   * the SAME face on two cameras reports 0.6965 and 0.4758 for fields that are
   * really 0.9721 and 0.9994 mm.
   *
   * The millimetre figure is `FaceModel.displacementRmsMm` (`enroll.ts:363`),
   * recomputed after `applyScale`. That is the one to show a wearer and the one
   * `ui.ts` already shows. Use this only to compare two solves of the SAME
   * capture — which is exactly what `tests/pipeline.test.ts` does.
   *
   * Zero still means the stage did not run; that is gauge-independent.
   */
  fieldRmsGauge: number;
```
plus the rename at `bundle.ts:431` and at `enroll.ts:615` (the degraded stub). Every remaining reference is in `tests/pipeline.test.ts:1062,1139-1142`, which compares two solves of one capture — a legitimate use that the rename makes obvious rather than accidental. Those tests' comments ("3.3923 mm", "1.2463 at x8") should drop the "mm".

**What it moves:** nothing numeric — pure rename. `check-reports.mjs` `source` drifts; the canary matches; the gate passes. No test goes red (the tests reference the field by name and would need the same rename in the same commit).

**Falsifiability.** The assertion that catches a regression is a units check, not a value check:
```ts
it('the bundle reports the field in its own gauge and the model reports it in mm', () => {
  const r = enroll({ /* healthy capture, no knownPdMm */ });
  assert.ok(Math.abs(r.bundle.fieldRmsGauge * r.model.scale.factor
                     - r.model.displacementRmsMm) < 1e-9,
    'the two field magnitudes no longer differ by exactly the scale factor — one of them ' +
    'has changed gauge and the docstring is now lying about which');
  assert.ok(Math.abs(r.model.scale.factor - 1) > 0.05,
    'the scale factor is near 1 on this fixture, so this test could not tell the gauges apart');
});
```
**To make it RED:** the second assertion is the falsifiability guard for the first (measured `scale.factor` 1.099–2.732, so it holds with margin); the first goes red the moment anyone "helpfully" rescales one of the two.

---

### C4c — `landmarkRigidity` documented 0..1, returns up to 1.8. **REAL as a DOCUMENTATION defect. MIS-STATED as a solver defect: the over-weighting is deliberate, and measured over 72 paired cells it does nothing.**

#### 1. Verified

`bundle.ts:320` says "How much each mesh landmark is allowed to speak, **0..1**". `bundle.ts:342` is `out[i] = base * (1 + 0.8 * noseRegion.weight[i])` with `base ∈ [0.12, 1.0]`. **`bundle.ts:327-328`, twenty-two lines above the code, already says the boost is intentional**: "The nose region gets a further boost, because it is both the smallest target and the one the whole exercise is for." The two halves of the same docstring contradict each other; the "0..1" is the stale half.

Measured on the template mesh: min **0.1200**, max **1.8000**; **141 of 468 vertices (30.1%) above 1**, 121 above 1.5.

#### 2. Every consumer, and what >1 does in each

| site | expression | effect at r = 1.8 |
|---|---|---|
| `enroll.ts:106` → `track/pnp.ts:735-737` (`buildCorrespondences`) | `sigma /= Math.sqrt(r)` | claimed sigma × 0.745 → information × 1.8, in the **PnP initialisation only**. The `maxSigma = 12` cutoff is applied *before* the division (`pnp.ts:731`), so the boost cannot smuggle a rejected landmark back in. |
| `bundle.ts:634` (`accumulateGlobal`) | `w = Math.sqrt(rigidity[i]) / sigma` | information × 1.8 in stage A |
| `bundle.ts:1011` (`solveField`) | same | information × 1.8 in stage B |
| `bundle.ts:1200` (`costLandmarks`) | same | matching cost for the accept test — the two agree, which is the property `solveField`'s docstring is about |
| `bundle.ts:1289` (`reprojectionStats`) | `if (state.rigidity[i] < 0.5) continue` | a *gate*, and **the boost does not move it**: 309 vertices pass with the boost and 309 without |
| `bundle.ts:1303` (`chiSquare`) | uses the **raw** `sigma`, no rigidity | `varianceFactor` is not inflated by the boost — correct |

A second-order effect worth naming: the Huber threshold is in whitened sigmas, so a nose residual is clipped at 1/√1.8 = 0.745× the raw residual of a cheek residual. That is a real asymmetry and it is the intended one — the nose is where outliers matter most.

#### 3. What the 80% over-weighting actually does to a solve — 72 paired cells (seeds 11/23/37 × 8 subjects × 3 geometries)

| metric | with boost (med/p90/worst) | boost removed | paired median Δ | boost better |
|---|---|---|---|---|
| nose | 1.521 / 2.302 / 4.680 | 1.582 / 2.350 / 4.493 | −0.0109 | 40/72 |
| pad strip | 1.337 / 2.280 / 4.426 | 1.338 / 2.354 / 4.594 | −0.0187 | 43/72 |
| bridge | 1.490 / 2.558 / 4.171 | 1.508 / 2.603 / 4.141 | +0.0164 | 34/72 |
| \|protrusion\| | 0.727 / 1.527 / 3.266 | 0.761 / 1.626 / 2.210 | +0.0068 | 35/72 |
| \|standoff\| | 0.825 / 1.539 / 2.027 | 0.801 / 1.661 / 2.158 | +0.0131 | 34/72 |
| `varianceFactor` | 1.817 / 5.052 / 6.478 | 1.851 / 5.071 / 6.560 | −0.0258 | **67/72** |

`residualsUsed` is **identical in all 72 cells**. Every accuracy metric is a coin flip at the tenth of a millimetre. The only consistent signal is a 0.026 improvement in `varianceFactor` (67/72), which is a residual-fit statistic, not an accuracy.

**So the boost is neither the harm the finding implies nor a measurable benefit.** The defect is the docstring.

#### 4. Recommended change — comment only

`bundle.ts:320`, before:
```
 * How much each mesh landmark is allowed to speak, 0..1.
```
after:
```
 * How much each mesh landmark is allowed to speak: 0.12 at the chin, 1.0 above
 * the subnasale, and up to 1.8 on the nose.
 *
 * The range is deliberately NOT 0..1 — 141 of the template's 468 vertices come
 * out above 1 — and the reason is the nose boost two paragraphs down. It is a
 * SIGMA MULTIPLIER, not a probability: every consumer divides by `sqrt(r)`
 * (`pnp.ts:736`) or multiplies the residual by it (`bundle.ts:634, 1011,
 * 1200`), so 1.8 means "trust this landmark's own sigma 1.8x", and there is
 * nothing to normalise it against.
 *
 * What the boost is worth, measured as a paired ablation over 72 enrolments
 * (3 seeds x 8 subjects x 3 camera geometries, boost on vs clamped to 1):
 * nose RMS -0.011 mm on the paired median with 40 of 72 improving, pad strip
 * -0.019 with 43/72, bridge/protrusion/standoff all slightly the other way at
 * 34-35/72. It is a wash on accuracy. It is kept because the argument for it
 * is sound and the cost is nil, not because it buys millimetres.
 *
 * **Do not "normalise" this to 0..1 by dividing through by 1.8.** Clamping is
 * harmless (`residualsUsed` is identical in all 72 cells, because no vertex
 * crosses `reprojectionStats`' 0.5 gate either way), but SCALING is not: it
 * takes 8 vertices below 0.5, drops `residualsUsed` 7416 -> 7224, and moves
 * `varianceFactor` and every sigma derived from it.
```

**What it moves:** nothing. `check-reports.mjs` transpiles with `removeComments: true` before hashing, so this does not even drift `source`.

**Falsifiability.** The docstring cannot be tested, but the thing it warns about can:
```ts
it('rigidity is a sigma multiplier, and the nose boost does not move the stats gate', () => {
  const rig = landmarkRigidity(mesh, standardRegions(mesh).nose);
  const max = Math.max(...rig);
  assert.ok(max > 1.5 && max <= 1.8,
    `rigidity peaks at ${max.toFixed(3)} — the nose boost has been normalised away`);
  const gated = (a: Float64Array) => a.reduce((n, v) => n + (v >= 0.5 ? 1 : 0), 0);
  const clamped = Float64Array.from(rig, (v) => Math.min(v, 1));
  assert.equal(gated(rig), gated(clamped),
    'the nose boost now lifts vertices across reprojectionStats\' 0.5 gate, so ' +
    'residualsUsed and varianceFactor depend on it');
});
```
**To make it RED:** divide the array by 1.8 (both assertions fail — I measured `gated` 309 → 301 and `residualsUsed` 7416 → 7224 under exactly that change), or drop the `(1 + 0.8 * weight)` factor (the first fails).

---

### Summary of verdicts

| finding | verdict | what the fix moves |
|---|---|---|
| **C1** `converged` cannot go false | **Real, and the implied fix is worthless.** Every candidate convergence test (λ ceiling, step acceptance, residual plateau) either never fires or always fires — measured over 96 cells. The real defect is that a 15 mm nose passes as `degraded: false`, and no solver property can see it. | Nothing numeric. Completes the guard, renames what it means, and refuses to invent an accuracy estimate this build does not have. |
| **C2** `fill(1)` overwrite | **Real, mis-stated.** One frame per scan, not every frame; the cited `noseObservations == framesUsed` fingerprint belongs to the already-fixed whole-stream bug; and `noseConfidence` does not move at *any* fill fraction because both its terms saturate. | ≤ 0.01 mm on 1 cell in 6. No published number. |
| **C3** `silhouette: null` | **Real. Wire it up.** Worth −0.287 mm on the standoff p90 and −0.412 mm on the worst, replicated 5/5 seeds, and the benefit survives 5 px of edge noise, 50% sample loss, and a 5% systematic dilation. Price: +0.056 mm on the pad-strip median, 4/5 seeds. | Step 1 (a note) moves nothing. Step 2 would move `reports/enroll.txt` only if the harness changed, which it need not. `docs/CONSTANTS.md`'s `measured` label on the `silhouetteWeight` row needs the caveat that the sweep never ran in production. |
| **C4a** PD [45,85] vs [46,80] | **Real, and worse than stated.** On `[45,46) ∪ (80,85]` the scan is resized by the wearer's PD and then blames its own eye landmarks for not being able to report it. | `pdMm` becomes non-null in the two dead bands. No shipped number moves. |
| **C4b** `fieldRmsMm` gauge units | **Real, and worse than stated.** Understated by 9–63% (median 32%), and the gauge tracks the camera's unsolved focal length — the same face reports 0.6965 and 0.4758 on two cameras for 0.9721 and 0.9994 mm fields. | Nothing — pure rename. |
| **C4c** `landmarkRigidity` 0..1 vs 1.8 | **Real as a doc defect, mis-stated as a solver defect.** The boost is deliberate and documented 22 lines below the wrong sentence; measured over 72 paired cells it is a wash (nose −0.011 mm, 40/72). The 80% "over-weighting" does nothing. | Nothing. And the report includes the trap: clamping is safe, *scaling* to 0..1 moves `residualsUsed` 7416 → 7224 and `varianceFactor` with it. |

---

> Environment note: I did not modify the repo, run `npm run build`, `npm test`, or `tsc`. Every "after" number below was produced by copying `dist/` into my scratch dir, patching the copy's `.js`, and running it (`.../scratchpad/sim` = D1 patch, `.../scratchpad/sim2` = D2 patch). All "before" numbers come from the repo's own already-built `dist/`.

---

## Cluster D — the fit, and a naming collision

### D1 — `describeSeat`'s pad lift vs. the solver's contact row

### 1. Verdict: **REAL, but the finding's own remedy is a sign error.**

The disagreement is real and located exactly where the review says.

- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\fit\contact.ts:1217` — `per[side].lift += normalN * cp.ny;` where `cp.ny` is the barycentrically-interpolated **vertex** normal.
- `C:\Users\Shay\PycharmProjects\lenses\ar_v2\src\fit\contact.ts:759-763` (`addOneSided`, inside `accumulate`) — the contact row is built from `ux,uy,uz = (p - cp) * inv`, i.e. `u = (p − cp)/|p − cp|`.

**Where the review is wrong.** `u` is the gradient of the *residual* `r = sqrt(k)·|p − cp|`, not the direction of the contact force. With `E = ½k·d²` and `d = |p − cp|`, `∂d/∂p = u`, so the force the solve balances is `F = −∂E/∂p = −k·d·u`. The vertical component the report wants is therefore `+normalN · (cp.y − p[1])/|p − cp|` — **minus** `u`, not `u`.

I measured the naive reading. Substituting `u_y` directly (145 pairs, 29 faces × 5 parametric frames, seed 0):

| | median `|Δ padLoadFraction|` | grade changes |
|---|---|---|
| `+u_y` (finding as written) | **86.93 pts** | 145 / 145 |
| `−u_y` (the correct force direction) | **0.76 pts** | 12 / 145 |

`+u_y` drives `padLoadFraction` to 0.0% on every frame in the catalogue. `cp.n` and `−u` sit **9.20°** apart at the median (p90 18.05°, max 48.20° over 1,416 bearing samples) — which is the same spread the 2026-08-25 comment at `contact.ts:746` already records ("9.0 degrees apart at the median and 34.7 at the worst"). `cp.n` and `+u` sit **170.8°** apart.

### 2. The exact change

`src/fit/contact.ts:1200-1217` — replace the comment block and the one statement:

```ts
      // Only the VERTICAL component of the contact normal carries weight. …
      // SIGNED, and the `Math.max(cp.ny, 0)` that used to sit here was wrong. …
      per[side].lift += normalN * cp.ny;
```

with

```ts
      // The direction the SOLVE pushes, not the surface's own normal. `accumulate`
      // builds its contact row from u = (p - cp)/|p - cp|, the gradient of the
      // penetration depth, so the force it balances is -k*d*u. `cp.n` is a
      // barycentrically interpolated VERTEX normal (meshdist says so on the line
      // that computes it) and sits 9.2 deg from -u at the median over 1,416
      // bearing samples, 48.2 deg at the worst. Reporting a load share against a
      // direction the solve never used describes different physics from the one
      // that was solved.
      //
      // SIGNED, and the `Math.max(..., 0)` that used to sit here was wrong: some
      // contacts push the frame DOWN (121 of 1,416 bearing samples over 29 faces
      // x 5 frames), and it is the signed sum that closes the vertical balance.
      per[side].lift += normalN * (cp.y - p[1]) / Math.max(cp.magnitude, 1e-9);
```

`p`, `cp` and `cp.magnitude` are all already in scope in that loop. Nothing else changes — `describeSeat` never feeds back into the solve, so **no pose moves**.

### 3. What it moves — measured

**`reports/seat.txt` moves. One column, and only that column.** I regenerated the committed report at its declared seed (`runSeatReport({seed:11})`) from the patched scratch build and diffed against the committed body:

```
 frame          pad sep  descent mm (med/p90/worst)  |depth err| mm  pad load  panto deg  pad tilt deg
-narrow-pads         13         -0.82 / 2.78 / 5.83            0.76      0.91        0.7          20.9
-standard            17        6.94 / 13.81 / 13.96            0.79      0.70        8.1          19.6
-wide-pads           22       12.77 / 15.89 / 15.97            0.60      0.89        9.2          20.4
-heavy-acetate       19       11.82 / 15.82 / 15.91            0.67      0.73        9.7          21.0
-steep-pads          17        6.09 / 13.93 / 14.39            0.67      0.75        6.2          26.2
+narrow-pads         13         -0.82 / 2.78 / 5.83            0.76      0.92        0.7          20.9
+standard            17        6.94 / 13.81 / 13.96            0.79      0.57        8.1          19.6
+wide-pads           22       12.77 / 15.89 / 15.97            0.60      0.92        9.2          20.4
+heavy-acetate       19       11.82 / 15.82 / 15.91            0.67      0.75        9.7          21.0
+steep-pads          17        6.09 / 13.93 / 14.39            0.67      0.69        6.2          26.2
```

Descent, depth error, pantoscopic, pad tilt, unconverged: **byte-identical**. Worst move is `standard`, **0.70 → 0.57 (−13 pts)**.

**Population** (29 faces × 5 parametric frames, seed 0, 145 pairs): `|Δ padLoadFraction|` median **0.76 pts**, p90 **11.15**, max **38.87**. Signed mean −2.75 pts. 12/145 pairs change the wearer-facing `load` grade in `score.ts` (11 good→fair, 1 good→poor).

**Catalogue** (10 real derived assets × 8 faces, seed 11, 80 pairs): median **1.09 pts**, p90 **6.77**, max **23.97** (`horizon-amber`). 5/80 change grade. Per-frame medians move `navigator` 0.84→0.83, `khronos` 0.84→0.84, `horizon-amber` 0.77→0.76, `meshy` 0.74→0.77, `shield-golden` 0.06→0.06.

**Wearer-facing score** (enrolled models, 6 subjects × 5 frames, seed 11): 1 of 30 pairs changed the `load` grade, moving `score` by 2 points (`broad-low/wide-pads`, 53→55). The `load` measure carries weight 1.0 of 16.5 and a 0.8 confidence multiplier, so the ceiling is ~2.7·c points for good→fair and ~5.5·c for good→poor.

**It also closes the balance the comment claims it closes.** `totalLift / weightN` should be 1 at a converged pose:

| | median | `|err|` median | p90 | max |
|---|---|---|---|---|
| `cp.ny` (145 pairs) | 1.1092 | 0.1833 | 0.5473 | 1.3897 |
| `−u` | 1.0471 | **0.1372** | **0.3994** | **0.7974** |
| `cp.ny` (catalogue, 120 pairs) | 1.0394 | 0.2136 | 0.5669 | 4.3518 |
| `−u` | 1.0047 | **0.1323** | **0.3464** | 4.3429 |

A 25–38% reduction in the residual by the report's own stated criterion. (It is *not* small in absolute terms — 13–14% of the frame's weight remains unbalanced — for the reasons the `padOverClosure` comment already gives: the prior and the `no improving step` corner. That is a separate open item, not this one.)

Two other numbers move: `padOverClosure > 1` count 59→58 of 145 with the worst case **1.900 → 1.660**; downward-facing bearing samples 22→121 of 1,416.

### 4. What goes red

- **`npm test` → `check-reports.mjs` → `reports/seat.txt` STALE.** `contact.ts` is in `report-seat`'s import graph, so `source` drifts, the canary is computed, and the canary moves because the pad-load column moves. **This is the bar doing its job.** Fix: `npm run report:seat` and commit the new body + stamp.
- **`reports/occlusion.txt` will NOT go red.** `report-occlusion.ts:908-911` reads only `seat.pose`, which is unchanged. `check-reports` will print "source drifted, but the numbers did not — re-stamp when convenient" and pass.
- **`tests/asset.test.ts:535-543`** (`onPads == population.length` on navigator, 7 subjects, seed 7) **stays green.** Measured: 7/7 both before and after; the values move 69.3→64.0 and 85.1→78.4 on the two that move at all, both far above the 0.5 bar.
- No other test reads `padLoadFraction` or `padOverClosure`.
- **Prose to re-measure or annotate** (not build breakage): `docs/OPEN-QUESTIONS.md:712` (`padLoadFraction` median 0.865 / p90 0.970 / "exactly 1.000"), `:1198` (`padOverClosure` re-measure), `docs/CONSTANTS.md`'s `TEMPLE_LEVEL_RUN_MIN_FRACTION` row (khronos 13% / shield-golden 6%) and its `ARM_REACH_MAX_MM` row ("99% median with a 0% worst case"), `docs/HANDOFF.md:153-158`, `docs/OPEN-QUESTIONS.md:853`.
- The comment being replaced is **already stale independently**: it cites "77 of 1,316 bearing samples over 29 synthetic faces × 5 catalogue frames". I measure 22 of 1,416 at seed 0 and 51 of 1,457 unseeded. Neither reproduces, on either population.

### 5. Falsifiability — an exact identity, verified RED then GREEN

At a pose where **every** pad sample penetrates, the approach spring contributes nothing, so the reported pad lift must equal `−∂E_contact/∂t_y` exactly. Both `solveSeat` and `energyTerms` are already exported; `Terms.contact` is the pad term alone (ears are `.ear`, clearance is `.clearance`), so this needs no options gymnastics and no dependency on D3.

```ts
// Push `standard` 2.15 mm back into the nose so all 18 pad samples penetrate.
const settled = solveSeat(model, mesh, regions, frame);
const t = new Float64Array(settled.pose.t); t[2] -= 2.15;
const pose = { R: new Float64Array(settled.pose.R), t };

const rep = solveSeat(model, mesh, regions, frame, { maxIterations: 0, initialPose: pose });
const reportedLiftN = rep.padOverClosure * frame.massG * GRAVITY_N_PER_G;

const E = (dy: number) => {
  const tt = new Float64Array(pose.t); tt[1] += dy;
  return energyTerms(model, frame, { R: pose.R, t: tt }, distance, clearance,
                     { R: pose.R, t: pose.t }, SEAT_DEFAULTS).contact;
};
const h = 1e-4;
const fy = -(E(h) - E(-h)) / (2 * h);

assert.ok(Math.abs(reportedLiftN - fy) / Math.abs(fy) < 1e-6,
  `the reported pad lift (${reportedLiftN}) is not the vertical force the contact ` +
  `energy exerts (${fy}). The report is describing a direction the solve never used.`);
```

Measured, seed 11, subject S00, `standard`:

| | reported lift | `−dE/dt_y` | relative diff |
|---|---|---|---|
| shipped `cp.ny` | 4.37858284e-1 N | 1.91041989e-1 N | **1.292 (129% — RED)** |
| patched `−u` | 1.91041989e-1 N | 1.91041989e-1 N | **1.6e-11 (GREEN)** |

Add a guard in the same test asserting all 18 samples penetrate at that pose, so a future change to the frame or mesh cannot silently turn the identity into an inequality.

---

### D2 — the graded pad-curvature bar (`1.0`) vs. the solver's (`0.9`)

### 1. Verdict: **REAL, and the tree already knows.**

- `src/fit/score.ts:214` — `grade: seat.padSeatErrorArticulatedMm > 1.0 ? 'poor' : gradeBy(tilt, 10, 25)`
- `src/fit/contact.ts:1373-1377` — `if (articulation.residualMm > PAD_CURVATURE_LIMIT_MM) notes.push('… this frame does not suit this face')`
- `src/fit/contact.ts:453` — `export const PAD_CURVATURE_LIMIT_MM = 0.9;`

`PAD_CURVATURE_LIMIT_MM`'s own docstring names the defect verbatim at `contact.ts:451`: *"`advice.ts` still grades pad contact against its own bare `1.0` for the same decision. Both should point here."* **`advice.ts` no longer exists** — it was renamed to `score.ts` (a stale `dist/src/fit/advice.js` with no `src` counterpart is still sitting in `dist/`, along with `bearing.js`). So the one pointer at the known defect points at a deleted file.

### 2. The exact change

`src/fit/score.ts:30`:
```ts
-import { solveSeat, type SeatResult, TARGET_CONTACT_MM } from './contact.js';
+import { solveSeat, type SeatResult, TARGET_CONTACT_MM, PAD_CURVATURE_LIMIT_MM } from './contact.js';
```
`src/fit/score.ts:214`:
```ts
-    grade: seat.padSeatErrorArticulatedMm > 1.0
+    grade: seat.padSeatErrorArticulatedMm > PAD_CURVATURE_LIMIT_MM
```
And delete the now-satisfied sentence at `src/fit/contact.ts:451` (it also names a file that is gone).

### 3. What it moves — measured

**Band occupancy.** Residual in `(0.9, 1.0]`, 29 faces × 15 frames (5 parametric + 10 derived catalogue), 435 pairs, seed 0:

- `> 0.9` (note fires): **281**
- `> 1.0` (score already says poor): **262**
- **in the band: 19 of 435 (4.4%)**; of those, **13 change the `pads` grade fair→poor**, 6 already graded poor from tilt.

7 of the 13 are `navigator` — the one asset with author-declared pads, so this is not a synthetic-frame artefact.

At the report's own realisation (8 faces × 15 frames, seed 11, 120 pairs): 4 in the band, and the worst case is the one that matters most — `crystal-lenses/S02`, residual 0.9080, tilt 8.4°: today the seat note says *"this frame does not suit this face"* while the graded verdict says the pads are **'good'**. (The review predicted 'fair'; 'good' also occurs and is the stronger case.)

**Score impact.** On the synthetic ground-truth models the harness uses, **it moves nothing** — but not because the fix is inert. `truthModel` (in `report-seat.ts:245` and every test helper) sets `quality: {}`, so `noseConfidence` returns `{value: 0}` and *every seat-derived measure* has confidence exactly 0.0000; `scoreOf` shrinks all of them to neutral. Measured on the ground-truth path: 14 of 435 grade changes, **score delta 0.00 on every one**.

On a real enrolled model (`enroll` over a synthesized capture, seed 11, `pads` confidence 0.886–0.894), with a residual planted mid-band at 0.95 and tilt 0:

| subject | `pads` grade | score |
|---|---|---|
| S00 | good → poor | **75 → 59** |
| S01 | good → poor | **74 → 58** |
| broad-low | good → poor | **69 → 53** |

The typical fair→poor case is ~7.3 points (`0.45 × 3.0 × 0.89 / 16.5 × 100`).

`reports/seat.txt` does **not** move — `score.ts` is not in any report's import graph.

### 4. What goes red

- Nothing. No test asserts on the `pads` grade or on `assessFit`'s score, and no report imports `score.ts`. **That is the problem, not a reassurance** — a wearer-facing verdict with two contradicting bars had no bar of its own.
- **Secondary, and it bears on the value rather than the naming:** `docs/CONSTANTS.md`'s derivation table for `PAD_CURVATURE_LIMIT_MM` no longer reproduces. It claims median 0.54 / p90 0.90 / max 1.67 and a sensitivity of `0.7:44, 0.8:27, 0.9:14, 1.0:9, 1.2:3` over 145 pairs. Measured today on the same 5 frames × 29 faces:

  | seed | median | p90 | max | 0.7 / 0.8 / 0.9 / 1.0 / 1.2 |
  |---|---|---|---|---|
  | unseeded | 0.46 | 0.89 | 1.35 | 32 / 21 / 13 / 10 / 2 |
  | 11 | 0.41 | 1.02 | 1.68 | 34 / 30 / 23 / 16 / 8 |
  | 23 | 0.44 | 0.81 | 1.37 | 27 / 16 / 14 / 8 / 3 |
  | 0 | 0.41 | 0.99 | 1.51 | 31 / 24 / 19 / 14 / 3 |

  No seed reproduces the table; the 2026-08-25 contact-row fix moved every settled pose. **Take the naming fix now regardless** — it is a consistency defect independent of the value — and re-run the derivation before moving 0.9 itself.

### 5. Falsifiability

`assessFit` already accepts `cachedSeat?: SeatResult`, so this needs no solve:

```ts
it('a residual the seat refuses cannot grade better than poor', () => {
  const real = solveSeat(model, mesh, regions, TEST_FRAMES[1]);
  // Mid-band: past the bar the SEAT refuses at, under the bar the SCORE used.
  const seat = { ...real,
    padSeatErrorArticulatedMm: (PAD_CURVATURE_LIMIT_MM + 1.0) / 2,
    padTiltDeg: [0, 0] as [number, number] };
  const pads = assessFit(model, mesh, regions, TEST_FRAMES[1], seat)
    .measures.find((m) => m.id === 'pads')!;
  assert.equal(pads.grade, 'poor',
    'the seat says this frame does not suit this face and the verdict says the pads ' +
    `are '${pads.grade}'. Two bars for one decision.`);
});
```

Verified against the shipped `dist/`: residual 0.95, tilt 0 → **`grade: 'good'` — RED today.** Green after the one-line change. Because the fixture is derived from `PAD_CURVATURE_LIMIT_MM` itself, it stays honest if the constant is later re-derived.

---

### D3 — `useEars`

### 1. Verdict: **REAL, and worse than stated.**

Every reference in the whole tree (`src`, `tests`, `scripts`, `docs`, `reports`, `README.md`):

- `src/fit/contact.ts:264-265` — the docstring and the field on `SeatOptions`
- `src/fit/contact.ts:291` — `useEars: true` in `SEAT_DEFAULTS`
- `src/fit/contact.ts:670` — `if (opt.useEars)` in `energyTerms`
- `src/fit/contact.ts:822` — `if (opt.useEars)` in `accumulate`

That is all. No caller anywhere sets it (`solveSeat` has 27 call sites across `src` and `tests`; none passes it), no test names it, no doc names it. **There is no ledger row and no exemption entry to sweep** — `check-constants.mjs` exempts `SEAT_DEFAULTS` wholesale at line 55, and `docs/CONSTANTS.md` carries rows only for `SEAT_DEFAULTS.priorWeight` and `.rotationPriorWeight`. So the review's "ledger row" does not exist; that half of the finding is over-stated.

The docstring's claim — *"Off is how the harness measures what it does"* — is false. `report-seat.ts`'s three controls are `template-nose`, `nominal` (`maxIterations: 0` + `landmarkHungPose`) and `flat-nose`. None of them turns the ear term off.

**And the flag is not merely dead — it is broken.** `describeSeat` takes no `opt` and computes `earLift` and `hookForceN` unconditionally at `contact.ts:1246-1258`. So turning ears off silences them in the *energy* while leaving them in the *report*. Measured (seed 11, `standard`):

| | `padLoadFraction` | descent | `hookForceN` |
|---|---|---|---|
| S00, ears on | 65.5% | 7.55 mm | 0.6591 N |
| S00, ears off | **0.0%** | **42.81 mm** | **113.2872 N** |
| S01, ears on | 85.1% | 5.85 mm | 0.1968 N |
| S01, ears off | **0.5%** | **18.86 mm** | **19.4682 N** |

The frame falls 43 mm down the face and the report announces a 113 N hook force that no equilibrium ever exerted. Anyone who reached for this flag as an ablation would have got a number that looks like a measurement.

It also silences **both** ear terms — the vertical support *and* the hook — exactly as the review says (`contact.ts:670-683` and `822-853`).

### 2. The exact change — removal

Four edits, all in `src/fit/contact.ts`:

```ts
// 264-265 — delete
-  /** Include the ear support term. Off is how the harness measures what it does. */
-  useEars: boolean;

// 291 — delete
-  useEars: true,

// 670 — unwrap (dedent the body through line 683)
   let ear = 0;
-  if (opt.useEars) {
-    const ears = earRestPoints(model);
-    for (let s = 0; s < 2; s++) { … }
-  }
+  const ears = earRestPoints(model);
+  for (let s = 0; s < 2; s++) { … }

// 822 — unwrap (dedent the body through line 853), same shape
```

Nothing else has to go with it: no ledger row, no exemption, no test, no doc sentence.

**If you would rather wire it**, the removal list becomes an addition list, and it is longer than it looks: `describeSeat` needs the `opt` (or a `useEars` boolean) threaded through `solveSeat:1090`, both `earLift` and `hookForceN` need gating, `padLoadFraction`'s denominator has to fall back to `padLift` alone, and the flag then needs a test that exercises the off branch. I recommend removal: the tree's own rule is that a branch nothing measures is not a measurement, and this one currently produces a wrong report when taken.

**Same shape, not named by the review:** `useClearance` (`contact.ts:266`, `292`) is also never set by any caller. I flag it rather than folding it in — it gates a genuinely expensive `buildMeshDistance` over the whole face, so the owner may want it kept for that reason, but as a *behaviour* switch it is equally dead.

### 3. What it moves

**Nothing measurable.** The flag is `true` on every path; removing it deletes a branch never taken. `reports/*.txt` bodies are byte-identical.

### 4. What goes red

- `check-reports.mjs`: `contact.ts` is in `report-seat`'s and `report-occlusion`'s import graphs, so `source` drifts. The canaries do not move, so the gate prints *"source … -> …, but the numbers did not move … re-stamp when convenient"* and **passes**. Re-stamp at leisure.
- `check-constants.mjs`: unaffected (`SEAT_DEFAULTS` is exempt as a bag; no row names the field).
- No test goes red.

### 5. Falsifiability

Removal is structurally self-enforcing — the field is gone, so `useEars: false` is a type error. If you **wire** it instead, the assertion that catches the current defect is:

```ts
it('turning the ears off turns them off in the report too', () => {
  const off = solveSeat(model, mesh, regions, TEST_FRAMES[1], { useEars: false });
  assert.equal(off.padLoadFraction, 1,
    'with no ear term in the energy the pads carry everything, and the report ' +
    `says they carry ${(off.padLoadFraction * 100).toFixed(1)}%`);
  assert.equal(off.hookForceN, 0,
    `a hook that is not in the energy reported ${off.hookForceN.toFixed(1)} N`);
});
```

Verified RED today: `padLoadFraction` reads 0.000 and `hookForceN` reads 113.29 N.

---

### D4 — `padAngleRad` is two angles

### 1. Verdict: **REAL, every claim independently reproduced, and the tie-break is decisive.**

I re-measured all six of the stated numbers from `dist/` without reference to the finding's arithmetic:

| claim | measured |
|---|---|
| consumer at `frame-asset.ts:293` builds `n = (-side·cos a, 0, -sin a)`, `ny ≡ 0` | ✅ `parametricFrame({padAngleRad: 0.67})` → max `|ny|` over all pad normals = **exactly 0** |
| producers measure `atan2(hypot(ny,nz), |nx|)` | ✅ `frame-asset.ts:830-831` and `scripts/extract-pad-truth.mjs:114` |
| mean `|ny|` on the two deriving assets is 0.31–0.33 | ✅ **0.327** (navigator), **0.310** (khronos), over `derivePads`' own selected faces |
| dropping `ny` lowers the per-sample mean by 6.67° / 8.48° | ✅ **6.67°** (45.31→38.64) and **8.48°** (35.79→27.31), exactly |
| `derivePads` reads 43.24° against an authored 34.56° (+8.68) | ✅ **43.24 / 34.56 / +8.68** |
| khronos reads +11.0 against a documented +6.1 that no longer reproduces | ✅ **27.76 vs 16.77 = +10.99**; `docs/HANDOFF.md:175-176` and `docs/NEXT-SESSION.md:208` both say +6.1 |

**The tie-break, which the finding does not have.** `SKIN`'s docstring at `contact.ts:113-114` records the template's nasal sidewall normal as **`(-0.76, +0.24, +0.60)`**, and `FrameSpec.padAngleRad`'s docstring at `frame-asset.ts:172` derives the constant as **`atan(0.60 / 0.76) = 0.67 rad`**. That is `atan2(|nz|, |nx|)` — the **yaw**, with the `+0.24` vertical component explicitly present in the data and explicitly excluded from the derivation. Computed: yaw = **0.6683 rad (38.29°)**; the same normal's cone angle is **0.7047 rad (40.37°)**.

So: the constant was derived as a yaw, the consumer consumes it as a yaw, and only the two measuring sites — both added later — read it as a cone. **Yaw keeps the name.**

Nothing else reads the field. `FrameAsset.padAngleRad` is written at `frame-from-mesh.ts:1059` and interpolated into a note string at `:883`, and read by nothing in `src/`, `tests/` or `scripts/`. `tests/pipeline.test.ts:1439` types `padAngleRad` off `ground-truth.json` and then asserts only on `padSeparationMm` (`:1489-1494`). **The angle has zero test coverage anywhere.**

### 2. The exact change

**(a) `src/fit/frame-asset.ts:827-832`**
```ts
-  // How far the contact plane leans out of the sagittal plane — the quantity
-  // `padAngleRad` names, and the same one `scripts/extract-pad-truth.mjs`
-  // measures on the two assets that declare their pads.
-  const lean = (n: { nx: number; ny: number; nz: number }) =>
-    Math.atan2(Math.hypot(n.ny, n.nz), Math.abs(n.nx));
-  const angle = (lean(r) + lean(l)) / 2;
+  // `padAngleRad` is a YAW about the vertical axis — the quantity
+  // `parametricFrame` inverts at line 293 to build `n = (-side*cos a, 0, -sin a)`,
+  // and the one `SKIN`'s `atan(0.60 / 0.76) = 0.67` was derived as. It is NOT the
+  // cone angle from the x axis: this measured the cone until 2026-08-26, which on
+  // a real pad differs by 6.7 deg (navigator) and 8.5 deg (khronos) because a
+  // pad's normal leans DOWN as well as in (mean |ny| 0.33 and 0.31).
+  const yaw = (n: { nx: number; nz: number }) =>
+    Math.atan2(Math.abs(n.nz), Math.abs(n.nx));
+  // The vertical component the yaw drops, kept rather than discarded: it is a real
+  // property of the pad (an optician's frontal angle) and this derivation recovers
+  // it BETTER than it recovers the yaw — 2.1 deg out on navigator against 10.4.
+  const drop = (n: { ny: number }) => Math.asin(Math.max(-1, Math.min(1, -n.ny)));
+  const angle = (yaw(r) + yaw(l)) / 2;
+  const vertical = (drop(r) + drop(l)) / 2;
```
plus `padVerticalLeanRad: vertical` on the returned object (`:841`), on `PadDerivation` (`:465`), and on the `fail()` stub (`:852`, as `NaN`).

**(b) `scripts/extract-pad-truth.mjs:112-118`** — the identical change:
```js
-  const angleOf = (n) => Math.atan2(Math.hypot(n[1], n[2]), Math.abs(n[0]));
+  // A YAW about the vertical axis: the quantity `parametricFrame` inverts and the
+  // one `padAngleRad` names. See frame-asset.ts:293.
+  const yawOf = (n) => Math.atan2(Math.abs(n[2]), Math.abs(n[0]));
+  const dropOf = (n) => Math.asin(Math.max(-1, Math.min(1, -n[1])));
   return {
     padSeparationMm: Math.abs(left.centroid[0] - right.centroid[0]),
-    padAngleRad: (angleOf(left.normal) + angleOf(right.normal)) / 2,
+    padAngleRad: (yawOf(left.normal) + yawOf(right.normal)) / 2,
+    padVerticalLeanRad: (dropOf(left.normal) + dropOf(right.normal)) / 2,
```
and emit `padVerticalLeanRad` / `padVerticalLeanDeg` alongside the existing pair at `:129-134`.

**(c) `scripts/extract-pad-truth.mjs:139-142`, the `definition` string** — currently *"padAngleRad is the mean lean of those contact normals out of the x axis"*, which is the cone. Replace with:
> `padAngleRad` is the mean YAW of those contact normals about the vertical axis — `atan2(|nz|, |nx|)`, the angle `parametricFrame` inverts to build a pad plane and the one `SKIN`'s `atan(0.60/0.76) = 0.67 rad` was derived as. `padVerticalLeanRad` is the downward component the yaw drops, `asin(-ny)`. They were one number until 2026-08-26 and it was neither.

**(d) `scripts/extract-pad-truth.mjs:146-157`, the `corroboration` note.** The sentence *"this area-weighted normal average reads 0.603 rad on navigator where the plane fit reads 0.673 — 4 degrees apart"* **cannot be carried across the definition change**: 0.673 was produced by a plane fit under the cone definition and there is no yaw counterpart on disk. Either re-run the plane fit under the yaw definition or delete the numeric comparison and keep only the qualitative claim ("SEPARATION replicates across methods and ANGLE does not").

**(e) `docs/HANDOFF.md:175-178` and `docs/NEXT-SESSION.md:208-209`** — "+8.7° (navigator) / +6.1° (khronos)". The khronos figure does not reproduce (it is +11.0), and both are cone-definition biases. Restate as the table in §3.

**(f) `src/fit/frame-asset.ts:482` and the `PAD_INWARD_COS` row in `docs/CONSTANTS.md`** — both say *"a real pad leans 15 to 35 degrees (see `assets/glasses/ground-truth.json`)"* in justification of `PAD_INWARD_COS = 0.35`. `PAD_INWARD_COS` gates on `n·x̂`, which **is** the cone angle, so this citation must keep pointing at the cone numbers, not the new `padAngleRad`. This is exactly why `ground-truth.json` should carry all three angles rather than swapping one for another.

**(g) `src/fit/frame-asset.ts:60-62` and `:168-176`** — the two `padAngleRad` docstrings should say "yaw about the vertical axis" explicitly, and `:168-176` should note that the template's `+0.24` vertical component is deliberately not in the number.

### 3. What it moves — measured

**Nothing in any solve, any report, or any wearer-facing number.** `parametricFrame`'s output is unchanged (the field is an input there, and its round-trip through its own normals gives cone = yaw = 38.3882° identically, because `ny ≡ 0`). `FrameAsset.padAngleRad` on the mesh path is read by nothing. I verified `reports/seat.txt` is untouched.

The only numbers that move are the two measuring sites' outputs:

| | shipped (cone) | new `padAngleRad` (yaw) | new `padVerticalLeanRad` |
|---|---|---|---|
| **`ground-truth.json` navigator** | 0.6031 rad / 34.56° | **0.5375 rad / 30.80°** | 0.2882 rad / 16.51° |
| **`ground-truth.json` khronos** | 0.2927 rad / 16.77° | **0.1388 rad / 7.95°** | 0.2585 rad / 14.81° |
| **`derivePads` navigator** | 0.7546 rad / 43.24° | **0.7193 rad / 41.22°** | 0.2518 rad / 14.43° |
| **`derivePads` khronos** | 0.4845 rad / 27.76° | **0.4485 rad / 25.70°** | 0.1899 rad / 10.88° |

**Two published claims move, and both get worse — say so rather than burying it.**

- **The derivation bias grows.** navigator **+8.68° → +10.42°**; khronos **+10.99° → +17.74°**. The cone's apparently-moderate bias was partly *cancellation*: the derivation over-reads the yaw and **under**-reads the vertical lean (**−2.08°** navigator, **−3.93°** khronos), and the cone mixes the two errors with opposite signs. Splitting them shows that the one quantity this derivation recovers well — the vertical lean, to 2–4° — was hidden inside the one it recovers badly.
- **The corroboration of `0.67` weakens.** `extract-pad-truth.mjs:153-155` currently says the claim that authored pads corroborate `0.67` *"to within half a percent"* is true of one method only and *"by this one it is 3.8 degrees out"*. Under the yaw definition it is **7.59° out** on navigator (0.67 rad = 38.39° vs an authored yaw of 30.80°). **That is the honest number**: the 3.8° figure was comparing a full-lean measurement against a constant that only ever produces yaw — i.e. the flattering number was itself an artefact of the naming collision.

### 4. What goes red

- **Nothing in `npm test`.** No test asserts on `padAngleRad` — from either producer. `tests/pipeline.test.ts:1489-1494` reads `truth.measured['navigator.glb']` and asserts only `padSeparationMm < 1.0 mm` out.
- **`node scripts/extract-pad-truth.mjs --check` goes red** until `assets/glasses/ground-truth.json` is regenerated (it byte-compares). It is **not** in `npm test` — the test script runs `check-isolation`, `check-constants`, `check-selfcontained`, `check-reports`, `node --test` — so this is a manual workflow step, not CI breakage. Regenerate with `node scripts/extract-pad-truth.mjs`.
- **`check-reports.mjs`**: `frame-asset.ts` is in `report-seat`'s graph, so `source` drifts; the canary does not move; the gate prints "the numbers did not move … re-stamp when convenient" and passes.
- Adding `padVerticalLeanRad` to `PadDerivation` requires touching `frame-from-mesh.ts:870`'s consumption only if you also want it on `FrameAsset` — it is optional and I would keep it off `FrameAsset` for now, since nothing reads even the angle that is already there.

### 5. Falsifiability

The collision existed because a parametric frame makes the two definitions **identical** (`ny ≡ 0`), so every round-trip test passed. Pin the *invertibility* — the property the name actually has to carry — on a frame that has vertical lean:

```ts
it('padAngleRad is the angle parametricFrame inverts, on a real pad too', () => {
  // (a) round-trip: the definition must recover what parametricFrame built.
  for (const a of [0.20, 0.42, 0.67, 0.80]) {
    const f = parametricFrame({ id: 'rt', padSeparationMm: 17, padAngleRad: a });
    for (let i = 0; i < f.padNormals.length / 3; i++) {
      const nx = f.padNormals[i * 3], nz = f.padNormals[i * 3 + 2];
      assert.ok(Math.abs(Math.atan2(Math.abs(nz), Math.abs(nx)) - a) < 1e-12);
    }
  }

  // (b) THE DISCRIMINATOR. A parametric pad has ny == 0, so a cone angle and a
  // yaw agree on it exactly and (a) cannot tell them apart. navigator's authored
  // pads have a mean |ny| of 0.33, and there the two differ by 6.7 degrees.
  const d = derivePads(navigator.positions, navigator.indices);
  assert.ok(d.ok);
  let maxAbsNy = 0;
  for (let i = 0; i < d.padNormals.length / 3; i++)
    maxAbsNy = Math.max(maxAbsNy, Math.abs(d.padNormals[i * 3 + 1]));
  assert.ok(maxAbsNy > 0.2,
    'this asset has no vertical pad lean, so it cannot discriminate the two ' +
    'definitions — pick another asset before trusting the assertion below');

  const want = truth.measured['navigator.glb'].padAngleRad;   // 0.5375 after regen
  assert.ok(Math.abs(d.padAngleRad - want) < 0.25,            // ~14 deg: the derivation
    `derivePads reads ${d.padAngleRad.toFixed(4)} rad against the asset's own ` +
    `${want} — check it is measuring a YAW and not a cone angle from the x axis`);
});
```

Make it RED: revert (a) to `atan2(hypot(ny,nz), |nx|)` and the round-trip still passes (proving the round-trip alone is worthless), while (b) moves 0.7193 → 0.7546 against a truth of 0.5375 and blows the 0.25 rad bar. Guard (b) is what stops the next person swapping in an asset with flat pads and quietly restoring the blind spot.

---

## Summary

| | verdict | moves a published number? | test goes red? |
|---|---|---|---|
| **D1** | real, **but the stated fix is a sign error** — use `−u`, not `u` | **yes** — `reports/seat.txt` pad-load column, all 5 frames, worst `standard` 0.70→0.57 | `check-reports` STALE on `seat.txt` (bar doing its job); `asset.test.ts` onPads stays 7/7 |
| **D2** | real, and `PAD_CURVATURE_LIMIT_MM`'s own docstring already names it (pointing at a deleted `advice.ts`) | no report; up to **16 score points** on an enrolled wearer; 19/435 pairs in the band, 13 change grade | none — which is the defect |
| **D3** | real; **no ledger row or exemption exists** (that half over-stated), and the flag is *broken*, not merely dead | nothing measurable | none; `check-reports` prints "numbers did not move" and passes |
| **D4** | real, all six numbers reproduce exactly; **yaw keeps the name** (`SKIN`'s `atan(0.60/0.76)` settles it) | `ground-truth.json` angles; bias **+8.68→+10.42** / **+10.99→+17.74**; corroboration **3.8°→7.59°** | none — the angle has no coverage at all |

Two secondary observations, both in scope and both cheap to act on: three files in `dist/` have no `src` counterpart — `dist/src/core/compare.js`, `dist/src/core/lm.js` and `dist/src/enroll/enroll.worker.js` (the `advice.js` and `bearing.js` this sentence used to name are themselves gone now; `fit/contact.ts` still names the deleted `advice.ts` in two docstrings, and the `contact.ts:451` this sentence used to cite was already pointing at neither); and `docs/CONSTANTS.md`'s sensitivity table for `PAD_CURVATURE_LIMIT_MM` reproduces on no seed I tried, so re-derive before moving 0.9 itself — but take D2's naming fix now regardless, since it is a consistency defect independent of the value.
