# v1 → v2 parity ledger

**Stage 10's fifth precondition: "the parity ledger is closed, each row naming a
test or a report line that exists."**

## Verdict: NOT CLOSED. Do not delete `ar/`.

Built 2026-08-26, the first time this ledger has existed as a document rather
than as a line in a checklist. 26 v1 modules, 16,407 lines, each read and mapped
to what supersedes it in v2. Then every row that claimed a citation was handed
to an independent reader whose instructions were to reject it.

    rows                                        26
    verdict `superseded`                        17
    verdict `open`   (v2 does not do it)         6
    verdict `dropped` (deliberate, reasoned)     3

    rows claiming a verified citation           14
    citations that exist verbatim               14   <- all of them
    citations that survive an adversarial read   0   <- none of them

**Every citation in this ledger is real and every one of them is too narrow.**
That is the finding. Not one row can currently name a test or report line that
demonstrates v2 doing what the v1 module it replaces does. The precondition was
written to catch exactly this, and it caught it on the first honest attempt.

The failure mode is uniform and worth naming, because it will recur: a v2 test
whose *name* reads like the capability, whose *body* asserts a much narrower
thing. `holds the frame on the face through a full turn` asserts >90% of frames
tracked and median rotation error under 1.5° on one subject at one camera — it
places no eyewear at all, and `solveSeat` is never called. `renders in sRGB with
tone mapping, which a PBR asset needs` asserts two of `scene.js`'s nine
capabilities. `every catalogue entry either derives or refuses with a reason`
asserts that 1 of 10 derives — true, and nothing to do with loading, scaling or
material correction.

**None of this says v2 is worse than v1.** It says the test suite was written to
pin v2's own new physics — the bundle, the contact solve, the scan protocol —
and was never written to demonstrate that the things v1 already did still
happen. Those are different jobs and only one of them has been done.

---

## The ledger

| v1 module | lines | verdict | superseded by | citation | covers? |
| --- | --- | --- | --- | --- | --- |
| `frame.js` | 2550 | superseded | `track/tracker.ts:track` + `fit/contact.ts:solveSeat` | "holds the frame on the face through a full turn" | **NO** |
| `smoothing.js` | 1037 | superseded | `track/smoothing.ts:PoseSmoother` | "the OneEuro arithmetic is unchanged: golden values" | **NO** |
| `tracker.js` | 394 | superseded | `detect/mediapipe.ts:createMediaPipeDetector` | — | **none exists** |
| `tracker.worker.js` | 127 | **open** | — | — | — |
| `stab.js` | 179 | **open** | — | — | — |
| `settle.js` | 373 | dropped | — | — | — |
| `occluder.js` | 1733 | superseded | `core/head.ts:buildHeadWithEars` + `render/scene.ts:setOccluder` | "a temple X-rays through a headless occluder at yaw, and not through a head" | **NO** |
| `occlusion-mask.js` | 500 | **open** | — | — | — |
| `subdivide.js` | 287 | **open** | — | — | — |
| `scene.js` | 403 | superseded | `render/scene.ts:createScene` | "renders in sRGB with tone mapping, which a PBR asset needs" | **NO** |
| `lighting.js` | 134 | **open** | — | — | — |
| `fit.js` | 1299 | superseded | `fit/contact.ts:solveSeat` | "navigator sits on the nose across the population, and converges every time" | **NO** |
| `seat-equilibrium.js` | 650 | superseded | `fit/contact.ts:solveSeat` | "slides further down the wedge as the pads get wider — monotonically" | **NO** |
| `nose.js` | 551 | superseded | `core/meshdist.ts:buildMeshDistance` | "and in the CONTACT regime, at the pose solveSeat actually returns" | **NO** |
| `temples.js` | 714 | **open** | *(occlusion half only)* | "a temple X-rays through a headless occluder…" | **NO** |
| `layout.js` | 36 | dropped | — | — | — |
| `models.js` | 995 | superseded | `fit/catalogue.ts` + `fit/frame-from-mesh.ts` + `render/frame-mesh.ts` | "every catalogue entry either derives or refuses with a reason — none throws" | **NO** |
| `anchors.js` | 873 | superseded | `enroll/bundle.ts:solveBundle` | "recovers a nose it has never seen, to about a millimetre" | **NO** |
| `person.js` | 590 | superseded | `enroll/bundle.ts:solveField` + `enroll/enroll.ts:perVertexUncertainty` | "a head that never moved has no parallax, however low the camera sits" | **NO** |
| `head.js` | 396 | superseded | `core/head.ts:buildHeadWithEars` | "a temple X-rays through a headless occluder…" | **NO** |
| `canonical-face.js` | 186 | superseded | `core/mesh.ts:parseFaceObj` | "parses to 468 vertices in millimetres" | **NO** |
| `main.js` | 1568 | superseded | `app/main.ts:startLoop` | "does not fall back to a timer when the tab is merely hidden" | **NO** |
| `ui.js` | 146 | superseded | `app/ui.ts:createUI` | — | **none exists** |
| `sources.js` | 307 | superseded | `app/sources.ts:createCameraSource` | — | **none exists** |
| `agreement.js` | 322 | dropped | — | — | — |
| `pick-face.js` | 57 | superseded | `detect/pick-face.ts:pickFace` | — | **none exists** |

---

## The six `open` rows — capabilities v2 does not have

Each was grepped for across `src/`, `tests/`, `reports/` and `docs/` before
being called open.

**1. `occlusion-mask.js` (500 lines) — soft, dithered occlusion.** v1 draws the
occluder a second time into a depth texture and injects GLSL into every frame
material so each fragment fades over a 1.2 mm feather band, resolved with an
8-level ordered Bayer dither. Ordered rather than hashed so a still head does
not get television static; dithered rather than alpha-blended so transmissive
lenses stay in the opaque pass and keep their refraction. **v2 has a hard binary
depth test** — `render/scene.ts:440`, `MeshBasicMaterial({ colorWrite: false })`
at `renderOrder -1`. No second pass, no feather, no dither. Zero hits for
`feather|dither|onBeforeCompile`.

**2. `subdivide.js` (287 lines) — Loop subdivision of the occluder**, precomputed
into a reusable CSR sparse matrix so a rebuild is a weighted sum. Zero hits for
`subdivi` anywhere in v2. So v2's occluder is drawn at MediaPipe's raw
468-vertex topology, and the artefact v1 measured — **7.3 mm triangles over the
nose, up to 16.5 mm** — is unmitigated.

**3. `lighting.js` (134 lines) — the light probe.** Downsamples the video, reads
the face bounding box, and drives key/ambient/environment/screen intensities
from the light the camera actually sees. **v2 ported the lights and not the
probe, and says so in its own source**: `render/scene.ts:243` — *"Estimating the
room's real light from the video is still the intended upgrade and still
unimplemented."*

**4. `tracker.worker.js` (127 lines) — the detection worker.** v2 calls
`app.detector.detect(...)` **synchronously inside the frame-lock tick**
(`app/main.ts:656`), and its own comment concedes it: *"The detector is
synchronous here, and that is the whole picture's latency, not just the pose's…
A worker is the right home, and the lock is already shaped for one since it
drops rather than queues."* `app.busy` is set and cleared around that call and
is documented as a no-op kept for a worker that was never written. Lost with it:
v1's warm-up guard (`WARMUP_RESULTS`/`DECIDE_AFTER`), never deciding while the
tab is hidden, and comparing the *median* of recent inferences against the
camera interval rather than a lifetime mean.

**5. `stab.js` (179 lines) — the live stillness meter.** Screen-space RMS of the
placed frame's origin over a 5 s ring gated on pose stillness — *the number every
gate in v1's live protocol was read against*. `reports/track.txt` carries a
same-class offline figure ("jitter while holding a pose: 1.052 mm median") but
that is millimetres of bridge movement in a synthetic beat, not pixels of the
drawn frame on a real session. There is no live instrument.

**6. `temples.js` (714 lines) — four of its five jobs.** v2 hides a temple
behind the skull loft, which is genuinely superseded and tested. It does **not**
split an asset's arms into separately hinged nodes, aim each at that side's ear
within a pitch limit, splay it until it clears the head's half-width, dissolve
it by depth behind the hinge, or fade the far arm as the head turns. The frame
is transformed rigidly.

## Two `superseded` rows with holes big enough to name here

**`tracker.js` — nothing in the test suite touches the detector at all.** The v2
code exists and carries v1's constants across verbatim, with the reasoning
quoted (`DEFAULT_NUM_FACES` 2 to defeat MediaPipe's untunable internal
smoothing, `DETECT_LONG_SIDE` 640, GPU→CPU fallback, `pickFace` by bbox area).
But **no test imports `detect/mediapipe`, and all four reports are synthetic** —
they feed landmarks from their own noise model, so the real detector path is
unexercised end to end. v2 also sets `outputFacialTransformationMatrixes` false:
the 4×4 that v1's own header calls *"the whole ballgame"* is not even requested.

**`frame.js` — v2 has no identity-change detection.** v1 asked every frame
whether the wearer had changed (`isDifferentFace`/`IDENTITY_STRIKES`) and reset
everything person-derived if so, against a machine-readable manifest of session
state with five named reset classes. v2's only reset path is a manual *rescan*
button. **A second person sitting down in front of a warm session silently
inherits the first one's `FaceModel`, cached seat and calibration field.** That
is legitimate-by-architecture for the per-frame estimator state v2 no longer
keeps; it is not legitimate for the state it does.

## The three `dropped` rows are sound

`settle.js` measured convergence drift of per-frame estimators v2 does not have.
`agreement.js` existed because v1 re-solved the seat every frame and had to ramp
it in; v2 solves once per (face, frame) and caches. `layout.js`'s failure mode
cannot arise in v2's DOM. All three are dropped by construction, with the
construction documented.

---

## What closing this requires

Not "write 26 tests". The honest reading of the adversarial pass is that **five
things are untested in v2 and all five are in the half of the system the
synthetic harness cannot reach**:

1. `app/framelock.ts` — imported by **no test in the suite**, and
   `ARCHITECTURE.md` calls the frame lock *"the best idea in that tree"*.
   Nothing asserts that a busy frame is dropped whole, or that the pixels shown
   are the pixels the pose was solved from.
2. `render/scene.ts:setOccluder` — **zero coverage repo-wide**. `scene.test.ts`'s
   four tests are sRGB, environment map, shadow frustum units, and screen light.
3. `detect/mediapipe.ts` — no test, no report, no fixture.
4. `app/sources.ts` and `app/ui.ts` — no test.
5. Identity change — no mechanism, so nothing to test yet.

Every one of those is browser-side, which is why the gates never caught it:
`check-isolation.mjs` deliberately covers `core/ enroll/ track/ fit/ detect/
testkit/` and leaves `render/` and `app/` to the browser. The isolation boundary
is doing its job; nothing was ever asked to stand behind the other side of it.

**Recommended order**, cheapest first, and none of it needs the owner:

- a `setOccluder` test (the occluder is the whole illusion, and it has nothing)
- a `framelock` test (pure logic, no browser needed — it is a scheduler)
- a detector smoke test against a recorded capture rather than synthetic frames
  — `capture-2026-08-26.ndjson` is the first real one and exists for this
- then decide whether the six `open` rows are wanted at all. Four of them
  (`occlusion-mask`, `subdivide`, `lighting`, `temples`' articulation) are
  **visual quality**, not correctness, and the owner has already said v2 looks
  better without them. That is a product call, and it belongs in this ledger as
  a decision rather than as a silent omission.

Until then `ar/` stays. It is the only working reference for what the answer is
supposed to look like, and this document is the evidence that we cannot yet
prove we have replaced it.
