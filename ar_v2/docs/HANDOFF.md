# Handoff: finish making ar_v2 the AR system and retire v1

You are picking up a migration that is **8 of 10 stages done** (2026-08-26).
Read this whole file before touching anything. It is written to save you the day
I spent finding things out, and every number in it was measured on this tree
rather than recalled.

**Status, so the rest of this file is read in the right tense:**

    1  custody                        DONE
    2  baseline the real face         DONE  -- the recorder AND a real capture
    3  the asset bridge               DONE
    4  one surface, one description   DONE  (two of three; see stage 4)
    5  navigator end to end, headless DONE
    6  the back of the head           DONE
    7  navigator rendered on a face   DONE
    8  the other nine assets          PARTLY -- five widths off the placeholder,
                                      still `assumed`, nothing weighed
    9  re-baseline the reports        DONE  (bc28773) -- and the gate caught two
                                      reports describing code that no longer existed
    10 retire v1                      NOT DONE -- 4 of 5 preconditions met; the
                                      parity ledger is the one that is not

The two things that are not done are the two that need a physical day with the
frames, and a parity ledger nobody has written. Everything else is closed.

---

## 0. The task, in the owner's words

> "implement v2 into the main pipeline instead of v1. make v2 the main system
> and implement the rest around it (the rendering, etc). then we'll have a solid
> state to work on. after implementing v2 you can get rid of v1 and outscope
> view the ar project and review it and clean it."

He has since asked, plainly, whether v2 is the engine yet. **It is not**, and he
is not interested in process detail — he wants a real pair of glasses rendering
on a real face. When you report, lead with what he can see, not with what you
verified.

---

## 1. What this repository is

`C:\Users\Shay\PycharmProjects\lenses`. Three things that do not depend on each
other as much as their names suggest:

- `UI/`, `face_analysis/`, `optimal_configuration/`, … — a Python product
  (`Procfile`: `web: python -m UI.app`). **It does not reference either AR tree.**
  No imports, no templates, nothing. Do not go looking for an integration point;
  there isn't one. "The main pipeline" means the AR project itself.
- `ar/` — **v1**. JavaScript, 16,407 lines of `src`. The AR try-on that works
  today. It has the rendering: eleven real glTF eyewear assets, materials, lens
  glass, a light probe. Its estimator is the one being replaced.
- `ar_v2/` — **v2**. TypeScript, 270 tests, four mechanised gates. A much
  better estimator (scan once, track against the scan, seat by contact physics)
  that draws only a **parametric** frame — tubes and ellipses — and until this
  week had no way to read an asset at all.

Both are standalone static apps, each with its own `serve.py`. You open one page
or the other.

---

## 2. Branch and PR state

- Working branch: **`ar-v2-primary`**, off `ar-tryon`. Working tree clean.
- Three commits on it: `da2938e` (pure rename), `ec9c315` (custody wiring),
  `bf1f84e` (mesh reader + ground truth + derivePads rewrite).
- Two stacked PRs are open and **must not be disturbed**: **#2** (`main` ←
  `ar-v1`, 51,125 lines — v1 plus the vendor-untracking) and **#3** (`ar-v1` ←
  `ar-tryon`, 31,997 lines — ar_v2). `ar-v2-primary` sits on top of #3's head.
- `gh` is **not installed**; the repo is public, so the GitHub API works with the
  token in the Windows credential manager. To use it without printing it:
  `printf "protocol=https\nhost=github.com\n\n" | git credential fill | <your script>`

---

## 3. House doctrine — match it or the review will be worse than useless

This tree has an unusually strict epistemic culture. Violating it is the main
way to do damage here.

- **"A check that cannot fail is a bug."** Before you let a new gate go green,
  demonstrate it red. Sabotage the thing it guards, watch it fail, restore.
- **Every exported constant needs a provenance row in `docs/CONSTANTS.md`**
  (`measured` / `derived` / `stated` / `published` / `physics` / `assumed`).
  `scripts/check-constants.mjs` fails the build otherwise. **Trap: it only
  catches constants with no row. An orphaned ROW with no constant passes
  silently** — so when you delete a constant, sweep the ledger by hand.
- **Adopt on ≥4 of 5 independent seeds**, report median-of-seeds with the
  per-seed spread. A single synthetic draw is a coin flip; this tree has
  reversed a verdict twice by forgetting that.
- **Results that went against the change stay in the headline**, not a footnote.
  Read any recent commit message for the register.
- **The isolation boundary is mechanised.** `core/ enroll/ track/ fit/ detect/
  testkit/` must run in Node with no browser. `scripts/check-isolation.mjs`
  actually `import()`s every built module. `render/` and `app/` own the browser.
- `render/convert.ts` is the **only** place CV convention (+Y down, +Z forward)
  becomes GL. Face space and frame space are +Y up, +Z out of the face and need
  no conversion.
- Comments here carry the reasoning and the measurement, not a restatement of
  the code. Long docstrings are the style, not clutter — but they must be
  **true**, and a 2026-08-24 review found four that asserted the opposite of the
  code beside them.

Verify everything with:

```bash
cd ar_v2 && npm test
```

That runs build → isolation → constants → self-contained → 222 tests. All four
must pass. Do **not** run `npm run build` concurrently from two places; `dist/`
is shared.

---

## 4. Read these first

- `docs/ARCHITECTURE.md` — the shape of v2. **Its "what is not built" section is
  stale**; the frame IS drawn (parametrically).
- `docs/OPEN-QUESTIONS.md` — every number the system is guessing.
- `docs/CONSTANTS.md` — the provenance ledger.
- `assets/glasses/ground-truth.json` — the only independent pad measurement in
  either tree. Read its `uncertainty` and `corroboration` fields.
- Claude's memory directory for this project has files
  `ar-v2-becomes-the-system`, `ar-v2-review-2026-08-24`, `ar-v2-perfect-campaign`
  — they carry context that is not in the repo.

---

## 5. What is already done

### Stage 5 + stage 7 — a real pair of glasses, on a face *(2026-08-25)*

**navigator.glb is the engine's frame now.** It loads through `fit/mesh-io.ts`,
derives a complete measured layout, seats by contact physics, and renders as
68,638 real triangles with materials, an environment map and a contact shadow.
242 tests green, three gates green.

New: `src/fit/catalogue.ts`, `src/fit/frame-from-mesh.ts`,
`src/render/frame-mesh.ts`, `tests/asset.test.ts`, `tests/scene.test.ts`.

**The one number that decided it was not the pads.** The first end-to-end run put
the ear rest at the temple's REARMOST vertex, which is the obvious reading of
"where the arm comes to rest" and is wrong: a real temple runs level to the bend
and then curls DOWN behind the ear, so its rearmost vertex is the tip — on
navigator 30 mm too far back and 21 mm too low. The ear term in `contact.ts` is
ONE-SIDED, so a rest below the ear never engages. Measured, tip against bend,
everything else identical, over 17 synthetic subjects:

    ear rest at      descent mm            pantoscopic deg   pad load       converged
    the TIP          8.57 [-1.2, 42.4]     21.5 [10, 53]     99% [0, 100]   13/17
    the BEND         4.07 [ 0.6, 12.1]      0.3 [-2, 11]     90% [69, 100]  17/17
    parametric ref   4.09 [ 0.4, 10.7]      3.9 [ 1, 15]     93% [56, 100]  17/17

**The two pad-load figures in that table predate 2026-08-26**, when `describeSeat` stopped projecting each contact force onto the interpolated vertex normal `cp.n` and started projecting it onto `-u`, the direction the solve actually balances — the two sit 9.2 degrees apart at the median. The shape of the tip-vs-bend result does not depend on it (the tip's bimodality is a descent and pantoscopic story as much as a load one), but the numbers below are from the old projection.

The tip's pad load reads 99% MEDIAN with a 0% worst case — the frame either hangs
on the nose alone or slides off, and the median hides it.

**The bend also checks the tree's highest-leverage constant for the first time.**
`templeReachMm` defaults to 95 mm, swept against synthetics, never measured on a
real frame. navigator's bend reads **96.2 mm** of reach — 1.2 mm from the swept
default. One asset, not a population; recorded as corroboration, not as a new value.

**Results that went against the plan, in the headline:**

- **Stage 5's own gate cannot be met and the pre-agreed fallback is taken.**
  ">=90% of samples on author-named pads" holds on navigator (100%) and is
  unreachable on khronos — an exhaustive 212,400-configuration search tops out at
  89.89%. khronos is now a recorded ceiling (>=45% ratchet), not a bar.
- **Only 1 of 10 assets derives.** Nine refuse, each at a named step. That is the
  honest state, and `tests/asset.test.ts` asserts the list EXACTLY so that making
  the derivation credulous turns it red.
- **`derivePads`' padAngleRad is systematically biased**, and **2026-08-26
  re-measured it under the corrected definition, where it is worse**. The field
  was two different angles under one name until then: the parametric path
  consumes it as a YAW about the vertical (`ny` identically zero) while both
  measuring sites read it as a CONE angle from the x axis. Split apart:

  | | derivation bias, cone (to 08-25) | bias, yaw (now) | bias on the vertical lean |
  | --- | --- | --- | --- |
  | navigator | +8.68 deg | **+10.42** | −2.08 |
  | khronos | +10.99 (not the +6.1 this line used to claim) | **+17.74** | −3.93 |

  The cone's apparently-moderate bias was partly CANCELLATION: the derivation
  over-reads the yaw and under-reads the vertical lean, and the cone mixes the
  two with opposite signs. Splitting them shows that the one quantity this
  derivation recovers well — the vertical lean, to 2–4 deg — was hidden inside
  the one it recovers badly. The cause is unchanged: the rearward gate discards
  the forward-leaning 41.8% of the pad by face count. Nothing in `src/` consumes
  the field — `contact.ts` deliberately uses the mean pad NORMAL instead — so
  this is still a reporting defect, not a physics one. Not fixed.
- **`serve.py` was broken by stage 1, four commits ago.** `faece72` (08-20)
  constructed the server correctly; `ec9c315` (08-25, "v2 owns the assets and the
  runtime") rewrote `main()` to delete the `SHARED_ROOTS` mapping and took the
  `ThreadingHTTPServer(...)` line with it, so every start raised `NameError:
  name 'server' is not defined`. **The commit whose whole purpose was making the
  tree servable on its own was the commit that stopped it serving**, and
  `check-selfcontained.mjs` was green throughout — it reads the source for the
  paths serve.py would map and never starts it. A path check is not a smoke test.
  (An earlier draft of this section said serve.py "had never worked". Wrong: it
  worked for five days. A server started 08-23 was still up and serving this tree
  while the file in the working copy could not start.)
- **The WebGPU branch in `scene.ts` is unreachable.** The vendored
  `three.module.js` does not export `WebGPURenderer` (verified by importing it in
  Node), so `backendName` has always been `'webgl2'` — and it goes into the
  diagnostics a wearer pastes, so every reader concluded a fallback had fired.
  Header corrected; branch kept.

**Two guards were knife edges and are now measured:**

- `PAD_UP_REFERENCE_FRACTION` (new): the up-check compared against
  `(minY+maxY)/2` of the whole mesh, which temple droop drags down.
  **aviator-amber was refusing by 0.0026 mm.** The front-slab reference gives
  every asset >=0.8 mm of margin. Swept 0.05-0.50 and **it is not monotone** —
  0.25 fails on two assets while 0.33 and 0.50 pass, so the safe band is the one
  contiguous with the failure boundary, 0.08-0.15.
- `PAD_SIDE_IMBALANCE_MAX` (new): the two authored-pad assets score EXACTLY 0.0
  face-count imbalance; `glasses01-with-lenses` scores 0.415.

**A bug the new tests caught while being written**: `findBend` measured its level
run from `topBin` backwards only. On a temple whose level section is exactly flat
every bin ties, `topBin` lands at the REAR of the tie, and a synthetic temple 70%
dead level measured 12.4 mm of level run out of 99 and was refused as an earhook.
navigator hid it because a real temple rises gently toward its hinge.

Also fixed: `check-isolation.mjs` reported `assets/glasses/navigator.glb` as
"touches navigator" — a filename read as a browser global. Dotted-global patterns
now scan with string literals blanked; bare identifiers still scan raw, because
`globalThis['localStorage']` is a real bypass. Demonstrated red on a genuine
`navigator.gpu` access.

**Still open from these two stages:** stage 4 (the frame layout is described
THREE times, not two — `contact.ts:947`'s `clearanceSamples` uses a third lens-size
convention, `frontWidthMm * 0.11`, that `rimHalfAxes` explicitly calls wrong).
Stage 6 (no head proxy, so a temple at yaw has nothing to hide behind). And the
wearer's verdict on a real face, which no synthetic can supply.


### Stage 1 — custody (`da2938e`, `ec9c315`)

`assets/` (77 MB, 15 tracked files), `scripts/fetch-vendor.mjs` and
`ATTRIBUTION.md` moved from `ar/` to `ar_v2/` as a **pure rename** (17 files,
R100, zero insertions). `vendor/` moved on disk (it is untracked; fetched and
SHA-256 verified by `node scripts/fetch-vendor.mjs`).

**The borrow was inverted, not removed.** `ar_v2/serve.py` serves only itself;
v1's server gained the mirror-image mapping and reached into `ar_v2` for
`/assets/` and `/vendor/`. **v1 had to stay bootable until the very end** — it was
the only working reference for what a rendered frame should look like, and
stage 10's precondition is a written side-by-side verdict.

New standing gate: `scripts/check-selfcontained.mjs` (text pass + physical
pass), wired into `npm test`.

### Stage 3 — the asset bridge (`bf1f84e`)

- **`src/fit/mesh-io.ts`** — a headless GLB reader. No three.js, so `fit/` stays
  on the right side of the boundary. Deliberately narrow: measured across the
  catalogue there is **no Draco, no meshopt, no quantisation, no sparse
  accessors and no external buffers**, so anything else throws rather than
  guessing.
- **`assets/glasses/ground-truth.json`** + `scripts/extract-pad-truth.mjs`
  (has a `--check` mode). navigator **13.843 mm / 0.6031 rad**, khronos
  **10.635 mm / 0.2927 rad**.
- **`derivePads` rewritten** around the criterion that defines a pad: *two
  surfaces facing each other across the midline, leaning back toward the
  wearer.* New constants, all with ledger rows: `PAD_INWARD_COS` 0.35,
  `PAD_REAR_COS` 0.04, `PAD_MIN_FACES` 20, `PAD_FRONT_FRACTION` 1/3.

**Result: navigator went from 39% of samples on the real pads and +6.1 mm of
separation error, to 100% and +0.42 mm.**

---

## 6. Traps — each of these cost real time. Do not re-discover them.

1. **`fixtures.ts` `TEMPLATE_PATHS` has two entries and both are load-bearing.**
   `src/testkit/` and `dist/src/testkit/` are **different depths** because the
   build adds a level. I cut them as a redundant "fallback ladder" and the suite
   silently dropped from 216 tests to 70.
2. **Never test Z against an asset's depth midpoint.** The temples run ~140 mm
   back, so a pair of glasses' depth midpoint lies *behind the wearer's ears*
   and every nose pad on earth is in front of it. A guard written that way
   refuses the entire catalogue. Use `PAD_FRONT_FRACTION`.
3. **Mirroring a mesh reverses triangle winding**, which inverts every computed
   normal — after which the inward-facing test cheerfully finds the *back* of
   each pad and returns a plausible number. `derivePads` now has a signed-volume
   guard (all ten assets measure positive, 5.2e3 to 4.2e7; all ten
   mirrored-without-rewinding measure negative). When you build flipped
   fixtures, **re-wind the indices** or you are testing the wrong thing.
4. **`padSeparationMm` is not a physics input.** `contact.ts` reads `padSamples`
   and `padNormals` directly. The field is consumed only by the renderer's rim
   sizing, the occlusion instrument and the seat report. It means the separation
   of the two pads' **contact-sample centroids** — whole-mesh centroids give
   18.48 mm on navigator where the contact faces give 13.84.
5. **The lens aperture wall faces the midline exactly as squarely as a pad
   does** — it is the inside of a hole. What separates them is the rearward
   lean: aperture and frame front read mean nz of **exactly 0.000**, authored
   pads read **−0.106**.
6. `check-constants.mjs` cannot see an orphaned ledger row (see §3).
7. The Bash tool on this machine chokes on large heredocs. Write Python patch
   scripts to a file and run them, or use the Write/Edit tools.

---

## 7. The remaining eight stages

Stage 2 must land before you trust any measurement taken after it. Stages 4–7
are the agent-executable spine. **Every stage must leave a working system.**

### Stage 2 — baseline the real face — **DONE (2026-08-26)**

The recorder half is built and no longer blocks anything:
`src/enroll/telemetry.ts` plus a **"Save this scan"** button. Finish a scan, press
it, and a `capture-<date>.ndjson` lands on the wearer's own disk. Nothing
uploads and no pixels are recorded — landmarks and sigmas only.

**It is not the port the handoff budgeted, and the reason matters.** v1's
recorder wrote `{ t, m:[16], l:[478x3 normalised] }` — MediaPipe's
transformation matrix and normalised 3D landmarks. **v2 consumes neither.** Its
detector returns landmarks in PIXELS, 2 per landmark, and never asks MediaPipe
for a matrix at all (`render/scene.ts`: "v2 does not consume it"). Replaying a
v1 fixture would mean converting a format v2 has no use for into one it does,
through the estimator under test.

So the recording is `BundleFrame`-shaped — what `enroll/bundle.ts` actually
takes — and it comes from `app.collected`, the array the app's own guided scan
already fills. **The recording path IS the shipping path**, so there is no
second protocol to keep in step. Seven of v1's ten imports were for a live
stillness meter its own header says recording does not depend on; none of that
is here.

Also gone with it: the "impossible after v1 dies" dependency. Nothing in the
recorder touches v1, so stage 10 no longer waits on this.

**CLOSED 2026-08-26.** The owner ran a session and produced
`capture-2026-08-26.ndjson`: 141 frames, 1280x720, focal solved, one real face.
It replays through the shipping estimator and lands cleanly — coverage
sufficient, not degraded, reprojection 5.70 px against ~4.94 on synthetics,
24 keyframes, nose sigma 0.67 mm, iris scale x2.1366 +/-4.89%, PD readout
64.6 +/- 3.2 mm.

Three things the replay taught that no synthetic could:

- **The `centre` beat recorded ONE frame** of 141 (`turn-right` 17,
  `turn-left` 32, `nod-down` 25, `nod-up` 15, `lean-in` 21, `lean-back` 29).
  Centre is the beat that establishes the neutral. It did not degrade the scan,
  but it is thin and nobody designed it that way.
- **The card instruction above is dead.** `?card=1` refers to a rung deleted in
  `f9c9093` and rejected by the owner. Do not ask for a card.
- **No PD was set**, and chasing one cost a long exchange: the owner offered
  145, then "49 29", both of which are the FRAME's markings (eye size, bridge,
  temple length) rather than a pupillary distance. The lesson for the UI is that
  "PD" is not a word a wearer reliably maps to the right number. The iris rung is
  the shipping default and it is fine for the picture.

**The capture is not committed.** It lives in the owner's `Downloads/`, so this
stage's fixture depends on a file that could be deleted. Committing it needs his
consent — it is landmarks and sigmas only, no pixels, but it is still a real
person's face geometry entering git history permanently. Ask before you do it.

**The 6.7 mm PD disagreement this stage used to cite has no underlying
measurement.** It is asserted at line ~307 of an earlier draft of this file,
`telemetry.ts` and `main.ts` both cite THIS FILE back for it, and every hit in
both workflow journals is an agent reading the doc. Do not treat it as evidence.

*Gate, unchanged and not yet met:* replay asserts within-capture PD spread <= 7%
on >= 4 of 5 seeds, and a committed cross-capture figure pinned to +-1 mm. The
replay path itself is tested (`tests/telemetry.test.ts`): a capture round-trips
to disk within 5e-4 px, keeps an absent landmark absent, refuses a truncated
file, and solves through the real `enroll` to within 0.05 mm of the live scan.


### Stage 4 — one surface, one description — **DONE (2026-08-25), two of three**

`src/fit/frame-layout.ts` now owns the arithmetic and the six cosmetic constants.
The renderer builds from it and the occlusion instrument samples from it; neither
computes a coordinate.

**The 4 mm was real and it flattered.** The instrument dropped the rims by
`LENS_DROP_MM` and forgot the bridge, so its 16 bridge samples sat 4.000000 mm
above a tube of radius 1.6 mm — **2.4 mm of clear air**, measuring geometry that
did not exist. Correcting it raises the bridge's hidden fraction 31.3% -> 40.0%
at yaw 0 and 37.5% -> 51.3% at yaw 30: the samples had been riding up the nose
dorsum into its shallowest millimetres, which is the exact defect `LENS_DROP_MM`
exists to prevent. Everything else agreed bit-for-bit.

**The handoff was wrong about the gate, and the correction matters.** There was
no twin-agreement check to make vacuous — **no test in this tree references
`frame-geometry.ts` and none can**, because it imports three.js and the suite
runs under Node where three is a vendored browser file, not a dependency. The
twin was two comments asking a reader to keep it in step. `tests/layout.test.ts`
instantiates the compiled renderer against a recording stub and asserts what the
shared buffer does NOT make free: every drawn part has samples under a name the
instrument knows, the renderer applies no offset of its own, and a mesh-backed
asset is REFUSED rather than measured. All four shown red on their own sabotage,
including reintroducing the 4 mm.

**Two parts were added to the instrument, and they did not come out the same
way** — both measured before adding:
 - **lens discs carry real signal**: a new report row at 1474 contested samples
   and 31.3% X-ray, comparable to the rim. Profile occlusion of the far lens was
   entirely unmeasured.
 - **endpieces buy nothing**: 0 contested at every yaw, and they cannot widen the
   band because their far end is the hinge the temples already reach. Added so
   the part list is exhaustive and the coverage gate has nothing to except. The
   row is an honest, probably permanent zero.

`byPart` was `[0, 0, 0]` hardcoded — a fourth part would have written
`byPart.contested[3]++` on `undefined`, yielding **NaN silently**, no type error,
no runtime error, a NaN row in the report. Any coverage gate on top of that could
not have failed. Now sized from `framePartNames.length`.

**The third description stays, deliberately.** `contact.ts`'s `clearanceSamples`
is a fourth-of-a-rim at `0.11 x frontWidth` with no drop — 6.6 mm narrower,
6.8 mm shallower and 4 mm too high. Collapsing it was measured and REJECTED:
feeding the drawn rim in makes the clearance term engage on every catalogue
frame at 19.3-20.5 mm of penetration, confined to the bottom-outer arc
(255-300 deg). That is an artefact of the renderer drawing a FLAT ellipse with
no dish and no pantoscopic tilt, not a fit result, and importing it would have
`solveSeat` telling wearers their frame fouls their face by 19.7 mm on every
frame in the catalogue. `tests/pipeline.test.ts` already fails on that change by
design. The fix is to give the drawn rim a dish, not to merge.

The clearance term was already shown able to fire — that test exists and passes.
*(The predicted ~6 tsc errors did not materialise; the collapse was clean. What
went red instead was `check-constants`, on all six newly-exported constants.)*

### Stage 5 — navigator end to end, headless. **HARD-STOPPED AT TWO WEEKS.**
Load navigator through `mesh-io`, build a real `FrameAsset`, run `solveSeat`.
**At two weeks, derivation is pre-agreed to demote from *producer* to
*checker*:** each asset declares its pad geometry in the catalogue and
`derivePads` must either agree within 1 mm or refuse the asset. The decision is
pre-agreed precisely so it is not made under sunk cost. That fallback unblocks
stages 6–10 unchanged.
*Gate:* ≥90% of returned samples land on author-named pad parts. **navigator is
at 100% today; khronos is at ~48% and +2.24 mm.** One of the two gradeable
assets clears the bar and nine of eleven cannot be graded at all.

### Stage 6 — the back of the head — **DONE (2026-08-25)**

`src/core/head.ts`, ported from v1's `head.js` with **every length x10** — v1 is
in centimetres and this tree is in millimetres, the same trap the shadow frustum
carries, where a copied constant is silently a tenth of its intended size and the
symptom is a proxy that does nothing rather than an error.

The face mesh's own boundary is a **closed 36-vertex loop**; lofting it back to
an occipital pole closes the head. Measured on this template: the loft is
**watertight — 0 boundary edges against the face mesh's 36** — it reaches z =
-141.9 where the face stopped at -24.4, and it holds **99.2% of the ring's width
at the ear**, against 93.1% for a circular sweep. The three constants
(`SKULL_DEPTH_MM` 140, `SKULL_FULLNESS` 3.5, `SKULL_RINGS` 12) were re-derived on
v2's template and reproduce v1's own readings exactly, which is the check that
the port is on the same geometry rather than a coincidence.

Ears are **open dishes, never balls**, and that is now mechanised: a ball has no
boundary and a dish does, so `tests/head.test.ts` asserts the pinna HAS boundary
edges. v1 records that its earlier 40 mm ear balls filled the crevice the temple
runs through, so the arm ended up inside the head and vanished.

**The instrument was blind to the whole problem, and that is the finding.**
`occlusionCell` rasterised BOTH arms with `mesh.indices`, so truth and occluder
were the same 468-vertex face — an arm drawn against nothing in both cancelled
exactly. The temple row read **4.57% X-ray while the entire back of the head was
absent from the model**, because there was no way to express a truth that
included one. `OcclusionArm` now carries optional `indices`.

The gate, non-vacuous by construction — same face, same frame, same pose, only
the occluding surface changes:

    yaw    occluder = face only    occluder = face + head
     0            0.0%                    0.0%
    15            0.0%                    0.0%
    30            0.0%                    0.0%
    45            8.9%                    0.0%
    60           12.5%                    0.0%

**Below 45 degrees the skull buys nothing**, which is worth knowing and is not a
disappointment: at low yaw the temple is not behind the head. Four sabotages
shown red — re-blinding the instrument, a circular sweep, capping the pinna into
a ball, and the shell editing a face vertex.

`nudgeOccluder` re-lofts after the edge snap, because the snap moves the 36 rim
vertices the skull SHARES and leaving it put tears a seam behind the ear at
exactly the millimetre scale the snap works at.

**The seat-and-occluder invariant is intact and is now asserted**: the loft keeps
the face's vertices first and at their own indices, bit-identical to
`model.positions`, so everything in front of the rim is the surface the contact
solve seated against. The addition is all behind it.


### Stage 7 — navigator on a real face, rendered *(the one he wants to see)*
The renderer for a real mesh: materials, lens glass, an environment map, one
shadow-casting key. There is currently **no shadow map, no environment, no
tone mapping** in `scene.ts`. Lens identity is in the material names on 7 of 10
assets (`Lens_Prescription_Glass`, `Lens_Gradient_Rx`, `lens_interior`, …).
*Gate:* mechanised half in `app.test.ts`'s compiled-source style — `scene.ts`
must set `outputColorSpace`, tone mapping, a PMREM environment and
`shadowMap.enabled`. The other half is a screenshot pair and his verdict.

### Stage 8 — the other nine, and an honest refusal *(PARTLY BLOCKED)*
Needs one physical day: eleven weighings and calipers where a real pair exists.
*Gate:* the run must **refuse** pads on `shield-golden` (a wrap with no distinct
pads) and on the acetate saddles. **A run where all eleven produce pads is a run
that failed.** Every asset carries either a measured or a declared geometry, and
says which.

### Stage 9 — re-baseline the reports — **DONE (`bc28773`, 2026-08-26)**
"Every checked-in report describes a configuration that no longer exists" was
two-thirds right. `enroll.txt` reproduced EXACTLY at its declared seed, every
accuracy digit; `seat.txt` and `track.txt` were badly stale.

`scripts/check-reports.mjs` is wired into `npm test`. **It does not hash the
declared inputs, and the reason is worth keeping:** every constant seat.txt's
own header names still held exactly the declared value while seat.txt was
stale, so that gate would have been green throughout — a check that cannot
fail. It hashes (a) the generator's transitive import graph with comments
stripped, cheap, every run, and (b) on drift only, a canary run of the
generator itself with the clock stripped.

`npm run report:<name>` now regenerates AND stamps through the same script.

### Stage 10 — DONE 2026-08-26. v1 is deleted; this tree is the only pipeline.
It went in one act, as planned: v1's `main.js` imported eight modules by direct
ES import, so piecemeal deletion would have made it unbootable partway.

**Four of the five preconditions were met and the fifth was overridden**, and
the distinction is recorded rather than smoothed over. Met: (1)
`check-selfcontained` had run on every commit since stage 1; (2) the recorder
runs from its new home; (3) the owner gave a blanket verdict ("v2 is for sure
better") rather than the per-asset screenshot pair that was asked for, accepted
at his discretion; (4) the pad ground truth is committed. **Not met: (5) the
parity ledger.** It was built on 2026-08-26, every citation in it was real, and
not one survived an adversarial read. The owner instructed the deletion anyway
("no need for v1 no more"), which is a legitimate way for a precondition to end
and is not the same as passing it. `docs/PARITY.md` carries the full finding,
what was lost, and the six capabilities v2 still does not have.

One file was kept: `docs/NOSE-V2-SPEC.md`, moved from v1 and the only document
in that tree with no equivalent here. Everything else is recoverable from
`origin/ar-v1` and `origin/ar-tryon`.
Then the cold review of the whole AR project.

**Honest total: about four months.** Roughly 77% of v1 (12,654 of 16,407 lines)
is superseded or dropped by construction — note that `frame.js` (2,550 lines) is
v1's *tracking* pipeline, **not** an asset loader; the loader is `models.js`
(995 lines) and it is worth reading before you write stage 7.

---

## 8. Open questions I could not settle

- ~~**`aviator-amber` refuses while `aviator-tortoiseshell` derives**~~ —
  **CLOSED 2026-08-25.** It was never a property of the asset. The up-check
  compared the pad centroid against `(minY+maxY)/2` of the whole mesh, which
  temple droop drags downward, and amber's sign change landed **0.0026 mm** from
  the 140 mm placeholder width somebody happened to declare — at 139.899 mm it
  passed. It also flipped on `columnHalfWidthMm`: both aviators derive at >=20 mm
  and the default 18 sat exactly on amber's zero crossing. The guard was reporting
  the catalogue's placeholder, not the geometry. See `PAD_UP_REFERENCE_FRACTION`.
- **khronos sits at ~48% precision.** Its frame front is sculpted rather than
  flat and carries genuinely rearward-leaning faces of its own.
- `meshy-glasses.glb` is **one fused mesh, 106k triangles, no part names, no
  extensions** — the hard case for every part-based approach.
- The catalogue is ~1.05M triangles total and 36 MB of embedded texture. Fine
  for the GPU; **anything CPU-side that walks all vertices per frame is not.**

---

## 9. How to start

```bash
cd C:\Users\Shay\PycharmProjects\lenses\ar_v2
git checkout ar-v2-primary
node scripts/fetch-vendor.mjs      # if vendor/ is absent
npm test                            # expect 300/300 and four green gates
```

Then read `src/fit/frame-asset.ts`'s `derivePads` and
`scripts/extract-pad-truth.mjs` — between them they contain most of what stage 5
needs to know.

If you can only do one thing, do **stage 5 and then stage 7**: that is the
shortest path to a real pair of glasses on a real face, which is the only thing
the owner has actually asked to see.
