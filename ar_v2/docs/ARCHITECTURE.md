# Architecture

## The one sentence

**Solve the wearer once; track against that solution; seat the frame by contact
physics, once.** Everything else follows.

## The diagnosis this shape comes from

v1 works, has 396 checks and 14,000 lines of careful prose, and has two problems
its author reported by name:

1. Past 35–40° of yaw the frame is "pushed forward for no reason".
2. "The interaction of the glasses with the nose is not good at all."

They are the same problem. v1 estimates **shape and pose simultaneously, per
frame, from a per-frame landmark set, against an average head it never
replaces.** Three consequences:

- **Pose must absorb shape error.** At high yaw the landmarks are worse *and*
  the rigid similarity fit has more shape error to absorb, so the pose swallows
  it as depth. Measured here (`report:track`, median of 5 seeds, re-measured
  2026-08-31): fitting the average head swings the bridge's depth error by
  **6.87 mm** between frontal and turned. Fitting the wearer's own model — ground
  truth at `report:track`'s default — swings it by **2.83 mm**, a 2.4x
  improvement. **Both figures are re-derived on the configuration the app
  actually boots** (2026-09-04): One Euro smoothing, the constant-velocity
  motion prior, the rigidity map and the sigma and visibility the app estimates
  per frame. Every earlier number in this bullet came from a harness that ran
  none of the last three, so the comparison it drew was between two systems
  nobody ships.

  The ablation arms say where the remaining swing comes from, and it is not the
  filter: `v2-no-prior` swings **1.79 mm** and `v2-no-smoothing` **3.70 mm**, so
  the motion prior costs a millimetre of swing on this protocol and turning the
  filter off makes it worse rather than better. Retired, in order: 5.1 → 0.37 mm
  (collapsed-noise harness), 4.52 → 0.53 (one seeded draw), and 6.61 → 3.97 /
  0.50 (five seeds, but on the pre-2026-09-04 harness configuration).
- **Depth stays borrowed forever.** The nose — the only surface that carries the
  glasses — is the average nose.
- **The escape route is closed by construction.** v1's own audit found why:
  *parallax and pose trust are the same angle with opposite signs.* Turning the
  head buys `sin²θ` of triangulation and costs `wPose²` of trust, and trust won.
  Over 15 synthetic subjects at 3 camera geometries, **0 of 45** reached the
  parallax its estimator needed.

You cannot converge depth from motion you refuse to trust. That is not a tuning
miss; it is a contradiction, and it is why v2 has a different shape rather than
better constants.

## The shape

| | v1 | v2 |
| --- | --- | --- |
| Shape | estimated live, never converges | solved **once**, in a dedicated scan |
| Pose | fused with shape, per frame | solved against a **known** rigid model |
| Placement | re-solved every frame | solved **once** per (face, frame) pair |
| High yaw | catastrophic | **useful** — it is triangulation baseline |
| Uncertainty | one constant reaching the renderer | per-vertex covariance from the scan |

The organising move: **turn head rotation from the enemy into the instrument.**

## The four programs

### 1. Enrollment — `src/enroll/`

A few seconds of guided capture, one joint bundle adjustment.

**The turn beats do not name an angle.** They ask the wearer to go as far as is
comfortable and detect when they have stopped. That is not a UX nicety: the
angle this pipeline can *measure* is compressed against the angle a person
*performs*, by an uncalibrated factor — a wearer reported the scan's "30 degrees"
arriving only after roughly 70 degrees of real turn, which made the follow-up
beat's demand for 60 more anatomically impossible. Asking for something the
wearer controls (their own comfortable limit) instead of something the system
cannot calibrate is the only version of this that works on every neck and every
camera. What the scan achieved is then reported rather than required. Q13.

```
frames ──► PnP init (template) ──► keyframe selection ──► coverage check
                                                                │
       ┌────────────────────────────────────────────────────────┘
       ▼
  A. globals: poses + shape + focal length      ◄──┐
     (Schur-eliminate the poses; ~20×20 reduced)   │  ×3 rounds
  B. field: per-vertex nose displacement        ───┘
       │
       ▼
  scale ladder (pd > iris > assumed) ──► per-vertex uncertainty ──► FaceModel
```

The staging is not cosmetic. A monolithic solve over poses, shape, intrinsics
*and* ~150 free-form displacements has a dense, badly-scaled Hessian, and LM
spends its damping deciding whether a millimetre of bridge belongs to
`noseBridgeDepth` or to the field beneath it. Alternating removes the coupling
from the linear algebra without removing it from the answer.

The keyframe budget is measured, not guessed: `KEYFRAME_DEFAULTS.count`
dropped 48 → 24 in the 2026-08-22 seeded replication — the documented
"24 costs 0.15 mm" knee does not replicate, and 24 is paired-equal-or-better
on every across-seed median mm metric — and halving the keyframes roughly
halves bundle time. On the campaign machine the enrollment solve went
807 → 491 ms median, a **1.64× speedup**; quote the ratio rather than the
milliseconds, because the same code spans 468–872 ms across machines and load
in the campaign's own records.

**Two model layers, deliberately.** A 20-mode anthropometric basis for the head,
and a **free-form per-vertex normal displacement** for the nose. A low-rank basis
— anthropometric or PCA — is structurally bad at noses: they are small,
high-curvature, and carry the population's most between-group variation, so the
leading components smooth them toward the mean. Swapping one average nose for a
slightly better average nose does not fix v1's problem.

**That is the argument, and as of 2026-08-22 the measurement supports it again
— at eight times the prior it shipped with.** This paragraph has read three
ways in as many days, and the history is the load-bearing part: "it cuts nose
error 0.99 → 0.35 mm" (collapsed-RNG harness, dead); then "it loses in every
configuration tested" (one seeded re-run — which turned out to be a draw from
the seed-41 family, the one losing realisation in five); and now the
**replicated verdict**: across 5 independent seeds the field wins on median
nose RMS in ≥4/5 seeds in *both* the shipped and the bias-free configs at
every prior scale swept, and `BUNDLE_DEFAULTS.fieldPriorScale` 8 is adopted.
Median-of-seeds nose RMS: shipped, field off 1.439 → on 1.269 mm; clean 0.884
→ 0.668; pad strip 1.353 → 1.030 shipped, 0.762 → 0.471 clean.

Q21's separation settled *why* the weak-prior field lost: its deficit was
**noise-chasing, and prior-curable**. Scale the injected noise toward zero and
the shipped-config deficit collapses (+0.066 mm at 0.7 px → +0.016 at 0) while
the clean config's win grows (−0.097 → −0.125) — no residual deficit survives
at zero noise, so there is no evidence the field mis-models the surface; it
was faithfully reproducing detector noise and bias, which the stronger prior
suppresses without freezing the field (solved-field RMS 0.799 mm median at ×8
against 1.048 at ×1 — it has not been smoothed into the average nose). The
regression bar in `tests/pipeline.test.ts` is **green on the adopted
configuration** — moved by measurement, not relaxed. The digits above are the
settlement campaign's record on its frozen tree state; the checked-in reports
under `reports/` carry the merged tree's current figures.

### 2. Tracking — `src/track/`

Six numbers against known geometry. What v1's `frame.js` spent 2,550 lines on,
this does not do: no shape estimation, no seat search, no per-frame placement.
The wearer is a `FaceModel` solved once and the seat is a transform solved once.
The tracker never sees the seat: the per-frame path solves pose against the
model, smooths it, says how much to trust the answer, and — on by default in the
app — fuses a constant-velocity prediction of this frame's pose into the solve's
own normal equations, with a per-channel gate that stands the prediction aside
when it is contradicted. Placement is the scene graph carrying that cached seat
under the head pose — `applySeat` runs when a frame is chosen, `setHeadPose`
runs per video frame, and neither re-derives where the glasses sit.

**The sentence that used to sit here was wrong in half of what it claimed, and
how it went wrong is the more useful record.** It read: *"`tracker.ts` is ~290
lines against v1's 2,550, and the difference is entirely things that no longer
need to exist: no shape estimation, no seat search, no identity question, no
trust ramp, no gate, no per-frame placement."* The first, second and last are
still true. The other three are not. There is a trust ramp, and there are
several: a variance-factor EMA and a visibility fade that takes a landmark's
weight down rather than dropping it, on every frame; two prior-miss EMAs
whenever the motion prior is on, which is the app's default; and a learned latch
floor under `smooth: 'locked'`. There is a gate, and there are four hard ones —
no face, too few correspondences, reprojection or depth out of range, too much
gross error — each ending the frame in `miss()`, on top of the latch's enter
thresholds, its drift guard, and a hold-then-reset path. And `src/track/` does
ask the identity question: `identity.ts` is called on every non-degraded wear
frame and answers on the frames that qualify, off the raw per-frame variance
factor `tracker.ts` computes. That it is not asked *inside* `tracker.ts` is all
the sentence could have claimed under a heading that names the directory.

The line count went the same way. `tracker.ts` was 294 lines the day that
sentence was written and 1,856 at `df63327`, the commit this retraction was
measured against — 685 of those code, against `frame.js`'s 839 of 2,550. Both
files run about sixty per cent comment, so code is the fair column and the
reduction there is 1.2×, not the 8.8× the old sentence implied. The stillness
latch, the basin audit, the motion prior and the variance-factor calibration all
arrived afterwards, under a sentence nobody edited; writing this retraction
pushed the total higher again. Nothing in this tree gates a line count, so take
the current one from `wc -l` rather than from here.

There is deliberately **nothing in the tracker that mentions yaw.** The reported
symptom was a consequence of solving shape and pose together; with shape frozen,
PnP holds **0.42°** of median rotation error at frontal (per-seed 0.32–0.59),
**1.25°** at 60° (0.50–1.56) and **1.14°** at full profile (0.47–1.77) —
median of five seeds across the population and the camera ladder. Adding a yaw
term would be treating a symptom that no longer exists.

Those are the **raw solve**, which is what `report:track`'s `v2-no-smoothing`
arm isolates. Running the One Euro on top of it — which is the smoothing the app
ships — pays for it in angle: 0.84° frontal, 5.51° at 60°, 1.58° at full
profile on the same seeds, measured on `report:track`'s `v2` arm, which matches
the app's smoothing and not the rest of its configuration. See README's
forward-push section.

One published figure about this module was wrong in the other direction and is
corrected here: the module header used to claim steady-state refinement
"converges in two iterations". It is **7 median, 8.1 mean** accepted steps.

### 3. Fit — `src/fit/`

A rigid-body contact solve, once per (face, frame) pair, cached.

```
E = contact + gravity + ear support + hook + clearance + weak prior
```

- **contact** — one-sided springs at pad samples against the skin. Samples, not
  one point per side: whether a pad beds flush or digs an edge in is decided by
  the angle between it and the sidewall, which one point cannot express.
- **gravity** — a linear potential in the centre-of-mass height. This is what
  makes the frame *slide down the wedge* rather than being placed at a height.
- **ear** — the temples rest on the ears; one-sided, support from below.
- **hook** — the temple cannot travel forward past the ear. Leaving this out was
  the largest modelling error in the file: the sidewall normal has a 0.60
  forward component, so the nose pushes the frame *off the face* as hard as it
  pushes it up, and with nothing opposing that the frame slid 60 mm and rotated
  88°. It presses the frame *back*, not *down*: its Gauss-Newton row is pure +z
  and contributes nothing to the vertical balance. Anything attributing the pads'
  vertical overload to the hook is wrong — see Q15 and the `padOverClosure`
  comment in `fit/contact.ts`. It is deliberately a stiff wall, not a compliant
  arm: the physically-derived cantilever alternative (k = 3EI/L³ ≈ 0.11 N/mm,
  `SKIN.hookCantileverNPerMm`) was measured 2026-08-22 across five seeds and
  **refused** — it worsens median pad depth error in 4/5 seeds, and the "the
  wall applies 2–7× the frame's weight" premise turned out to be the tail
  (median 1.01×) — Q15 is settled on that record.

`fit/bearing.ts` — the constructed seat — was measured inferior to the contact
seat (Q18) and never ran in the app. It left the working tree on 2026-08-25,
along with the card ruler and the scan-comparison tool (`core/compare.ts`), as
part of reducing this tree to what a production try-on needs. None of the three
was ever a tracked file, so no commit holds any of them: `f9c9093` is where the
tree stopped carrying them, not a commit you can recover them from. The one
tracked file that commit deleted was `core/lm.ts`.

Because it is solved once and cached, **nothing in the per-frame path can make
the frame walk up and down the nose, shimmer, or behave differently at 40° of
yaw. There is no per-frame placement left to be wrong.**

### 4. Verification — `src/testkit/`, `tests/`

Four reports and the whole suite (the count is deliberately not written here —
it was 300 when that was tried, and wrong five days later; take it from the run),
all headless, all against a synthetic population with known
ground truth. Two of them spent a day deliberately red — regression bars
asserting claims the harness fix had disproved — and one of them is green again
**by measurement, not by relaxation**: a 5-seed settlement campaign replicated
the contested claim, and the field bar now asserts the adopted
`fieldPriorScale` 8 configuration (which wins, Q21). A bar moved to
accommodate a regression is not a bar; a bar moved to match a replicated
measurement is the bar doing its job.

The other bar is gone, and this paragraph used to say it had been rewritten. It
existed and asserted the constructed seat's *superiority*; the rewrite that
would have asserted the recorded inferiority and guarded it in both directions
was never written. Nothing in `tests/` mentions the constructed seat, and no
commit on any ref holds such a test — the superiority version survives only in a
dropped stash (`1ce584f`, unreachable from every ref and deletable by the next
`git gc`, so evidence rather than provenance). Q18's verdict is carried by prose
alone — see the seat paragraph below.

The population exists because v1's real finding was not that its constants were
wrong — it was that six in seven were **one person's number**, so nothing could
distinguish "this works" from "this works on Shay". Every accuracy claim here is
a distribution across up to 17 subjects and 3 camera geometries, reported as
median/p90/worst — and, since 2026-08-22, across **five independent noise
realisations** (seeds 11, 23, 37, 41, 53), because a population measured
through one noise draw is a sample wearing a population's clothes.

**And for most of this build that distribution was half a lie.** The capture RNG
was salted by `subject.id.length`, every id is three characters, so the whole
population shared one noise realisation and the subject×camera grid held six
distinct streams rather than fifty-one. What the tables spread over was geometry,
with the measurement noise frozen — a synthetic population that varied the face
and held the camera's mistakes constant. It is fixed, every published figure was
re-derived on 2026-08-22 — first once, then replicated at five independent
seeds by the settlement campaign — and several got worse and stayed worse. The
lesson generalises past this bug: **a harness that shares a random stream
reports a population and measures a sample**, and nothing in a green test run
distinguishes the two.

**The rule that makes the tests able to fail:** every synthetic nose carries
detail the shape basis provably cannot represent, and a test asserts that. If a
future basis change made that residual zero, every enrollment threshold would be
measuring the basis rather than the reconstruction — which is exactly v1's *"the
pads stay on the nose"* check that measured a residual that was zero by
construction.

## The layer boundary, enforced

`core/ enroll/ track/ fit/ detect/ testkit/` must run in Node with **no browser
at all**. `scripts/check-isolation.mjs` fails the build otherwise. v1 maintained
the same split by discipline; discipline is what fails at 2 a.m.

The check does two things, not one: a source-text blacklist, and an actual
`import()` of every built headless module from `dist/` — 35 today. The second
pass exists because the first cannot see a module that *parses* clean and *loads*
dirty. It needs a build, and `npm run check:isolation` does not build first, so
running the script directly on a clean tree prints a loud SKIP. That is expected,
not a failure.

The enrollment worker lives in `app/`, not `enroll/`, for exactly this reason: it
sets `self.onmessage` at module scope, so it can never be imported in Node. While
it sat in `enroll/` the boundary check reported the directory headless and was
wrong.

`render/` and `app/` own the browser. `render/convert.ts` is the **only** place
computer-vision convention (+Y down, +Z forward) becomes GL convention (+Y up,
−Z forward), and that rule is why nothing in this tree has a lone minus sign on a
Z that needs a paragraph of comment to justify. **That statement is about camera
space only.** Face space is +Y up, +Z out of the face, which already agrees with
GL and needs no conversion at all. The distinction is worth a sentence because
conflating the two cost 127 mm on a live path in this file's own history.

There is no `core/lm.ts`; it is deleted. There has never been an `enroll/schur.ts`
— the Schur complement is inline in `enroll/bundle.ts`, in `solveGlobal` — and no
live solver in this tree uses Nielsen damping. `track/pnp.ts`, `fit/contact.ts`
and `enroll/bundle.ts` each multiply lambda by a constant on accept and reject.

## What was kept from v1, unchanged

1. **The frame lock** — one clock, drop-whole, capture/display canvas split. The
   best idea in that tree.
2. **`numFaces: 2`** — MediaPipe applies untunable internal smoothing when and
   only when it is 1.
3. **Picking the wearer by landmark-bounding-box area**, not width.
4. **Constants provenance classes** and *"a check that cannot fail is a bug"*.
5. **The headless-arithmetic split**, now mechanised.
6. **The wedge insight** — height, standoff and roll are one coupled equilibrium
   and a frame slides down until both pads bear.
7. **"Keep previous, never assume average"** on refused measurements.
8. **One Euro's four hard-won lessons.** A cleaner estimator made the filter
   stop earning its place on the synthetic population, and `TRACKER_DEFAULTS`
   still defaults it off; the first real wearer overturned that, and the app has
   run the One Euro since 2026-08-23 — latched at first, then plain. The
   2026-08-31 claim that the synthetic verdict had reversed too is **retracted**:
   it came from a jitter column that differenced the estimate against its own
   previous frame rather than against truth, so it paid a filter for lagging
   behind the wearer's own postural wander. Corrected 2026-09-03, the synthetic
   verdict at the harness's assumed detector noise is what it always was —
   the filter costs more than it buys — and it flips only above roughly 3 px of
   landmark noise, which nobody has measured (Q1). The wearer's report is
   therefore the only evidence the shipped default has. Q7.

## What is built, and what is not

**This section asserted the opposite of the code for two rounds of work and has
now been rewritten twice.** It first said "the frame is not drawn" after
`render/frame-geometry.ts` had started drawing a parametric one, and it said
"there is no glTF loader in this tree" until 2026-08-25, when there is.

As of 2026-08-25 the try-on draws a **real, measured pair of glasses**:
`assets/glasses/navigator.glb`, 68,638 triangles, read headlessly by
`fit/mesh-io.ts` for its geometry and again by `render/frame-mesh.ts` through
three.js for its materials, placed by the contact solve and rendered with an
environment map, tone mapping and a contact shadow.

The two readers agree because **neither of them decides where the frame goes
twice**. `fit/frame-from-mesh.ts` rotates the asset into frame space, scales it
to its declared width and re-centres it on the pad-contact origin, then hands the
resulting 4×4 to the renderer as `FrameAsset.source.meshToFrame`. The renderer
applies that matrix and computes nothing. A renderer that derived its own
placement would agree until the day it did not, and the symptom would be a frame
drawn a few millimetres from where it was fitted — which looks exactly like a
tracking bug and is not one.

**All ten catalogue assets are wearable as of 2026-08-26**, and this section
said the opposite of that for one commit. What differs between them is not
whether they derive but **what each can prove about its own arms**, which
`FrameAsset.earRestSource` carries to the wearer-facing note:

| tier | assets | what it means |
| --- | --- | --- |
| `measured` | `navigator` | a part named `Temple_*`, its bend walked directly |
| `derived` | both aviators, both horizons, both crystals, `meshy` | no temple named; the arm is found by splitting the mesh and fitting its knee |
| `constructed` | the five `TEST_FRAMES` shapes | placed by the spec — `templeReachMm`'s shared 95 mm, a swept constant rather than a measurement of any real frame. They reported `derived` until 2026-09-02, which made the flag unable to answer the one question it is asked: was this reach measured off THIS frame? |
| `assumed` | `sunglasses-khronos`, `shield-golden` | a wrap or an earhook. Its arm never stops descending, so **there is no rest point in the geometry**; the wearer's own ear supplies the reach and height and only the lateral position is the asset's |

The nine refusals this table used to list were correct answers to a narrower
question: a rest point could only come from a part called `temple`, and nine
assets do not have one. `deriveArmRest` asks the geometry instead. The
discriminator between the last two tiers is the **ratio** of the arm's curl
slope to its level slope — dimensionless, so it survives a droopy scan where the
absolute-fall tolerance `findBend` uses does not — and it is bounded on both
sides by measurement: 9.4–42 across the eight real temples, 3.1–5.9 across the
two wraps.

`findBend` is still tried first and navigator still goes through it, deliberately:
the knee fit's own answer for navigator is 9 mm further back, inside the band
the seat is most sensitive to, and adopting it everywhere would silently re-tune
the one asset every seat number in this tree was measured on.

Eight of the ten still carry an estimated front width, so their **width**
comparisons remain one estimate against another. That is the stage-8 measurement
day, and it is now the only thing it is about: one number per asset, with a rule
or a supplier's spec. Data entry, not geometry.

### The parametric frame is described once

`fit/frame-layout.ts` owns where a parametric frame's rims, lens discs, bridge,
endpieces and temples are, plus the six cosmetic constants that size them. The
renderer builds three.js objects from it; the occlusion instrument samples it.
Neither computes a coordinate.

It lives in `fit/` and not in `render/` for a mechanical reason:
`check-isolation.mjs` actually `import()`s every built module under `core/
enroll/ track/ fit/ detect/ testkit/` in Node, so a testkit module importing
anything from `render/` would make Node resolve `three` — a vendored browser
file, not a dependency — and the gate would fail. `render/ -> fit/` is the legal
direction and was already exercised.

The two used to be twins kept in step by a comment in each header, and **the
bridge had drifted 4.000000 mm**: the samples sat 2.4 mm clear of a 1.6 mm tube,
measuring air, and under-reporting that part's occlusion by 9–14 points. There
was no test to catch it and there could not have been one — nothing in the suite
can import `render/`. `tests/layout.test.ts` instantiates the compiled renderer
against a stub instead.

A **third** description survives on purpose: `contact.ts`'s `clearanceSamples`.
Merging it was measured and rejected — the drawn rim is a flat ellipse with no
dish and no pantoscopic tilt, so feeding it to the clearance term reports 19–20
mm of cheek penetration on every catalogue frame. That is the renderer's shape
being wrong, not the fit, and importing it would put a false wearer-facing
verdict on every frame. See that function's header.

**The card ruler is gone**, and this section asserted otherwise for four
commits. `enroll/card.ts` left the working tree at `f9c9093` and was never a
tracked file, so no commit holds it; the scale ladder is
`pd → iris → assumed` and nothing in the running path has ever asked a wearer
for a card. The owner has since rejected the method outright.

What replaces it is not a better ruler but a smaller requirement: measured, the
target is **1.5%**, the wearer's own prescription PD already delivers 0.79%, and
the iris is good enough for the try-on picture. `docs/SCALE.md` carries the
measurements, including why the remaining physically-admissible signals are all
dead and why the synthetic harness cannot grade a scale estimator at all.

## Measured results

See `README.md` for the current tables — all re-derived across five
independent seeds (11, 23, 37, 41, 53), quoted as median-of-seeds with the
per-seed spread. The three headlines:

| | v1-equivalent | v2 |
| --- | --- | --- |
| Depth swing frontal → turned | 6.87 mm | **2.83 mm** — the shipped configuration, all of it |
| Pad depth error (landmark-hung vs contact-solved) | 4.79 mm | **1.06 mm** |
| Nose surface error | not measurable | **1.54 mm** median (0.83 with a true iris) |

**Read that last row with `as-measured` beside it.** Every figure in this table
is produced at three stimulus constants nobody had measured: the detector's
landmark noise, how fast a resting head moves, and how far the turn beats go.
All three now have a number from a real session (`AS_MEASURED`, 2026-09-04),
and the harness's turn beats reach 80 degrees where the app's own reached 43.
At the measured stimulus the nose figure degrades about 10% on the
median-of-seeds basis and nose protrusion error by 46%; at seed 11, the
committed realisation, nose RMS reads 1.48 mm against `full`'s 0.91. The
constants are NOT adopted as defaults — one capture is one person — but no
number in this table should be quoted without knowing that the row describing
the scan a wearer actually gives is the worse one.

And the one that went against the design, kept in the headline position because
hiding it in a footnote is how a build starts reporting only its wins. The
constructed seat (`fit/bearing.ts`) was measured across 5 independent seeds at
the band the settlement adopted, [2, 36], and **recorded INFERIOR**: it wins the per-seed median
in 2/5 seeds against a 4/5 adoption rule — the same 2/5 at every wide band —
pooled 1.03 / 5.22 med/p90 mm of passed-through scan error against the contact
seat's 1.24 / 3.62. Bulk slightly better, tail 1.4× worse, and the tail is what
drags the seed medians over. Q18 is settled on that record. `bearing.ts` did
not stay a testkit instrument — it left the working tree at `f9c9093`, the same
commit that wrote this sentence saying it stays — and the running app seats
with `contact.ts`. (Those are the settlement campaign's digits on its frozen
tree state, and nothing here can re-derive them: the code that produced the
constructed arm was never a tracked file and is in no commit.
`reports/seat.txt` is the current tree, but it carries the contact arm only, so
it is not a current-tree version of this comparison.)
