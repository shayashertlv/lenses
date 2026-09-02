# v1 → v2 parity ledger

## Verdict: v1 is deleted. Closed 2026-08-26 by the owner's decision, not by the ledger's own bar.

**Say that plainly, because the distinction is the whole value of this file.**
The precondition this document was written to enforce — *"the parity ledger is
closed, each row naming a test or a report line that exists"* — was **never
met**. It was overridden, deliberately and by the person who owns the product,
with the instruction "no need for v1 no more". That is a legitimate way for a
precondition to end. It is not the same as passing it, and a later reader
deciding how much to trust v2's coverage needs to know which of the two
happened here.

What the ledger found, on 2026-08-26, before the deletion:

    rows                                        26
    verdict `superseded`                        17
    verdict `open`   (v2 does not do it)         6
    verdict `dropped` (deliberate, reasoned)     3

    rows claiming a verified citation           14
    citations that exist verbatim               14   <- all of them
    citations that survive an adversarial read   0   <- none of them

**Every citation was real and every one was too narrow.** The failure mode was
uniform: a v2 test whose *name* read like the capability, whose *body* asserted
a much smaller thing. `holds the frame on the face through a full turn` asserts
>90% of frames tracked and median rotation error under 1.5° on one subject at
one camera — it places no eyewear and never calls `solveSeat`. That finding
stands, and deleting v1 does not answer it.

None of this said v2 was worse than v1. It said the suite was written to pin
v2's own new physics — the bundle, the contact solve, the scan protocol — and
was never written to demonstrate that the things v1 already did still happen.

---

## What was actually lost, and where it went

`ar/` was 46 files and 36.0 MiB. It is recoverable in full from
`origin/ar-v1` and `origin/ar-tryon`, and from this branch's own history.

| what | disposition |
| --- | --- |
| `src/*.js`, 26 files, 16,407 lines | deleted; on `origin/ar-v1` |
| `docs/nose-v2/spec.md`, 3,948 lines | **kept** — moved to `docs/NOSE-V2-SPEC.md` |
| `tests/pipeline-check.js`, 396 checks | deleted; the only record of that suite |
| `tests/fixtures/*.ndjson.gz`, 35.7 MB | deleted — see the privacy note below |
| `index.html`, `serve.py`, `styles.css` | deleted; v2 has its own |

**The fixtures were 95% of the tree and they were biometric data.** Three
`.ndjson.gz` files, ~3,000 frames each, per-frame 478-point facial landmarks and
head-pose matrices for a named subject over a scripted 90-second protocol.
`docs/PRIVACY.md` already recorded that v1's README claimed they were
*"deliberately not committed"* and that they were, in fact, committed. Deleting
them from the working tree is an improvement and not a loss — v2 cannot read
that format, and `enroll/telemetry.ts` records captures in `BundleFrame` shape
instead.

**They remain in git history.** If they are to be gone in the sense that word
usually means, that is a history rewrite and a force-push, and it is a separate
decision with separate consequences for anyone who has cloned this repo.

---

## The six capabilities v2 still does not have

These were `open` before the deletion and they are open now. What has changed is
that the v1 source is no longer sitting beside them as a reference — it is one
`git show` away instead.

**1. Soft, dithered occlusion** (`occlusion-mask.js`, 500 lines). v1 drew the
occluder a second time into a depth texture and injected GLSL into every frame
material so each fragment faded over a 1.2 mm feather band, resolved with an
8-level ordered Bayer dither — ordered rather than hashed so a still head does
not get television static, dithered rather than alpha-blended so transmissive
lenses stay in the opaque pass and keep their refraction. **v2 has a hard binary
depth test** (`render/scene.ts`, `MeshBasicMaterial({ colorWrite: false })` at
`renderOrder -1`).

**2. Loop subdivision of the occluder** (`subdivide.js`, 287 lines), precomputed
into a reusable CSR sparse matrix so a rebuild was a weighted sum. v2's occluder
is drawn at MediaPipe's raw 468-vertex topology, and the artefact v1 measured —
7.3 mm triangles over the nose, up to 16.5 mm — is unmitigated.

**3. The light probe** (`lighting.js`, 134 lines), which downsampled the video,
read the face bounding box, and drove key/ambient/environment intensities from
the light the camera actually saw. v2 ported the lights and not the probe, and
says so in its own source (`render/scene.ts`).

**4. The detection worker** (`tracker.worker.js`, 127 lines). v2 calls
`detector.detect(...)` synchronously inside the frame-lock tick and concedes it
in a comment. Lost with it: v1's warm-up guard, never deciding while the tab is
hidden, and comparing the *median* of recent inferences against the camera
interval rather than a lifetime mean.

**5. The live stillness meter** (`stab.js`, 179 lines) — screen-space RMS of the
placed frame's origin over a 5 s ring gated on pose stillness, *the number every
gate in v1's live protocol was read against*. `reports/track.txt` carries an
offline analogue; there is no live instrument.

**6. Temple articulation** (four of `temples.js`'s five jobs). v2 hides a temple
behind the skull loft, which is genuinely superseded and tested. It does not
split an asset's arms into separately hinged nodes, aim each at that side's ear,
splay it until it clears the head's half-width, dissolve it by depth behind the
hinge, or fade the far arm as the head turns. The frame is transformed rigidly.

**Four of these six are visual quality, not correctness**, and the owner has
said v2 looks better without them. That is a product decision and it belongs
here as one rather than as a silent omission.

---

## The two holes that were correctness — both CLOSED 2026-08-26

**~~No identity-change detection.~~ CLOSED.** `src/track/identity.ts`, 11 tests
in `tests/identity.test.ts`.

Not a port. v1's predicate was a temple-width ratio against a canonical head,
which is the right answer for a tree with no scanned model and the wrong one
here. The v2 signal is `varianceFactor` — the whitened chi-squared per degree of
freedom that `track/pnp.ts` has computed on every frame since it was written and
that nothing read. Measured over 10 subjects x 3 geometries x 5 seeds, matched
against impostor: `varianceFactor` separates at AUC 0.936 where the obvious
candidate, reprojection RMS, manages 0.773 and is destroyed outright by 15%
occlusion (a wearer scratching their chin reads as a stranger under any pixel
threshold that catches an impostor).

Three things in it are worth knowing before touching it:

- **The bar is a ratio to the wearer's own reference, never a constant** — and
  the first version of this paragraph had the risk backwards. A CONSTANT
  miscalibration is harmless: measured, a permanently 4x-overconfident detector
  produces 0 of 36 false convictions, because it inflates the reference and the
  reading together. A detector that drifts MID-SESSION produced **36 of 36**.
  `IDENTITY_SIGMA_DRIFT_MAX` is the guard, and it works because the denominator
  is observable — a change of wearer moves the mean claimed sigma by at most
  1.35x, a harmful drift by 2x or more. Its cost is that a drift and a swap
  arriving together make the watch recalibrate onto the stranger — and that cost
  needed no drift at all until 2026-09-02, because the bar is derived from
  session halves and was being asked of ONE frame. Measured through the real
  `estimateSigma`, an ordinary same-person session crosses it in 8 of 8 captures
  and the swap after it was convicted 0 of 8. The retirement now waits for
  `IDENTITY_STRIKES` consecutive qualifying frames: 0 of 8 false retirements,
  8 of 8 swaps caught, four frames later on a real drift.
- **It abstains rather than guessing.** The watch is armed in exactly one place:
  immediately after a scan taken from a camera in this session. A model restored
  from storage was measured elsewhere, possibly on another device, so there is
  nobody in the room it can be sure of — and learning a reference from whoever
  sat down would reference the stranger. The gap this closes is the one named
  above; a shared device at cold boot is a different problem.
- **Measured end to end: 0 of 80 false convictions, 214 of 240 caught, median 7
  qualifying frames to convict.** The 11% that get through are the deliberate
  side of the asymmetry.

**And it turned up that `rescan` was already wrong.** The reset was written by
hand and cleared 11 of ~18 person-derived fields. The seven it missed included
`lastCapture` — the previous wearer's raw landmarks, which **Save this scan**
then wrote to disk under the *new* wearer's PD — and `knownPdMm`, person A's
typed PD becoming the absolute ruler person B's whole face was scaled by. Both
paths now share one manifest-driven `resetPerson`, and `PERSON_STATE` is a
`Record<keyof App, ...>` so TypeScript refuses to compile a field nobody
classified. It caught the identity watch itself on the day it was added.

**~~`render/scene.ts:setOccluder` has zero coverage repo-wide.~~ CLOSED.**
5 tests in `tests/scene.test.ts`, five sabotages, every one red.

The stub harness was seven identifiers short of being able to call any handle
method at all — which is why the four tests that existed were all about
`createScene`'s straight-line body. Six of the seven are real headless imports,
so the tests run the actual `buildHeadWithEars` and the actual convention
conversion against a stub three.js. What they pin: the occluder and the shadow
catcher share ONE geometry *instance* (a clone is value-identical until the head
takes a measured shape, which is exactly when v1's shadow vanished); the face
vertices stay first and `array[i] === Math.fround(positions[i])` exactly, so no
flip, scale or offset enters between the seat's surface and the GPU; three
consecutive calls dispose each geometry and material exactly once and leave two
children; the depth-only configuration; and the camera-axis bias.

One correction to what this document used to imply: "bit-identical to
`model.positions`" is true of `head.positions` and **false** of the buffer that
reaches the GPU — after the Float32 narrowing, 1376 of 1404 face components
differ. `Math.fround` is the exact statement.

**The detector is untested end to end.** No test imports `detect/mediapipe`, and
all four reports are synthetic — they feed landmarks from their own noise model.
v2 also sets `outputFacialTransformationMatrixes` false: the 4×4 that v1's own
header called *"the whole ballgame"* is not requested. That is a deliberate and
well-argued choice (v2 solves pose against the wearer's own geometry), but
nothing exercises the real detector path.

---

## Still untested in v2, all of it browser-side

The isolation boundary covers `core/ enroll/ track/ fit/ detect/ testkit/` and
leaves `render/` and `app/` to the browser. Nothing was ever asked to stand
behind the other side of it.

1. ~~`app/framelock.ts`~~ — **CLOSED 2026-08-26**, `tests/framelock.test.ts`,
   7 tests, nine sabotages. Drop-whole-when-busy lives in `main.ts`'s loop
   rather than in the lock, so it remains uncited.
2. ~~`render/scene.ts:setOccluder`~~ — **CLOSED 2026-08-26**, 5 tests.
3. `detect/mediapipe.ts` — no test, no report, no fixture. **Still open, and now
   the largest of these**: the identity watch depends on `varianceFactor`, which
   depends on the sigma `detect/uncertainty.ts` estimates, and nothing exercises
   the real detector end to end. See the calibration note in `identity.ts`.
4. `app/sources.ts` and `app/ui.ts` — no test.
5. ~~Identity change — no mechanism~~ — **CLOSED 2026-08-26**,
   `src/track/identity.ts`, 11 tests.

**Recommended order, cheapest first, and none of it needs the owner:** a
`setOccluder` test; then a detector smoke test against a recorded capture. That
second one needs a real capture, and the one this tree cites —
`capture-2026-08-26.ndjson`, quoted in `docs/HANDOFF.md` with its own numbers —
**is in no commit and on no disk here.** Recover it before writing the test, or
record a new one with **Save this scan**.
