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
  it as depth. Measured here (`report:track`, median of 5 seeds, 2026-08-22/23):
  fitting the average head swings the bridge's depth error by **4.92 mm**
  between frontal and turned (per-seed 2.77–7.27). Fitting the scanned model
  swings it by **0.47 mm** on the shipped, unfiltered arm (0.37–0.91) —
  1.44 mm if the One Euro filter is switched on. Earlier revisions of this
  document said 5.1 → 0.37 mm (collapsed-noise harness) and then 4.52 →
  0.53 mm (one seeded draw); the per-seed spread is why both single-number
  versions are retired.
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
  scale ladder (card > iris > assumed) ──► per-vertex uncertainty ──► FaceModel
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

Six numbers against known geometry. `tracker.ts` is ~290 lines against v1's
2,550, and the difference is entirely things that no longer need to exist: no
shape estimation, no seat search, no identity question, no trust ramp, no gate,
no per-frame placement.

There is deliberately **nothing in the tracker that mentions yaw.** The reported
symptom was a consequence of solving shape and pose together; with shape frozen,
PnP holds **0.42°** of median rotation error at frontal (per-seed 0.32–0.59),
**1.30°** at 60° (0.50–1.56) and **1.14°** at full profile (0.47–1.77) —
median of five seeds across the population and the camera ladder. Adding a yaw
term would be treating a symptom that no longer exists.

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
seat (Q18) and never ran in the app. It was deleted on 2026-08-25 along with the
card ruler and the scan-comparison tool, as part of reducing this tree to what a
production try-on needs.

Because it is solved once and cached, **nothing in the per-frame path can make
the frame walk up and down the nose, shimmer, or behave differently at 40° of
yaw. There is no per-frame placement left to be wrong.**

### 4. Verification — `src/testkit/`, `tests/`

Three reports and **171 tests**, all headless, all against a synthetic
population with known ground truth. Two of them spent a day deliberately red —
regression bars asserting claims the harness fix had disproved — and both are
green again **by measurement, not by relaxation**: a 5-seed settlement
campaign replicated each contested claim, the field bar now asserts the
adopted `fieldPriorScale` 8 configuration (which wins, Q21), and the seat bar
now asserts the constructed seat's *recorded inferiority* and guards that
record in both directions (Q18). A bar moved to accommodate a regression is
not a bar; a bar moved to match a replicated measurement is the bar doing its
job.

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
8. **One Euro's four hard-won lessons**, even though the filter itself is now off
   by default — because a cleaner estimator made it stop earning its place.

## What is not built

The fit pipeline is complete and reported numerically. **The frame is not drawn.**
There is no glTF loader in this tree, `frameNode` is childless, and the seat is
solved and applied to an empty node. `render/` exists, the camera is solved, the
pose is composited — and nothing eyewear-shaped is ever added to the scene.

This is worth stating in the architecture document and not only in the README,
because the four-program diagram above reads as a finished pipeline and, as a
*measurement* instrument, it is one. As a try-on, the last stage is missing.

One more thing built but deliberately not wired: the **card ruler**.
`enroll/card.ts` holds a detector (gradient edges, quad fit, sub-pixel line
refinement) and a factor solver with a propagated-and-measured sigma model;
`enroll/protocol.ts` has an opt-in card beat, default off; the scale ladder's
card branch consumes the readings end to end. Measured on the synthetic
harness it reads **0.80% median scale error at 1 px of edge noise against the
pooled iris's 5.14% on the same runs** — and it has never seen a real frame,
card, or hand, which is why the app wires none of it up (Q3, Q8).

## Measured results

See `README.md` for the current tables — all re-derived across five
independent seeds (11, 23, 37, 41, 53), quoted as median-of-seeds with the
per-seed spread. The three headlines:

| | v1-equivalent | v2 |
| --- | --- | --- |
| Depth swing frontal → turned | 4.92 mm | **0.47 mm** |
| Pad depth error (landmark-hung vs contact-solved) | 4.79 mm | **1.06 mm** |
| Nose surface error | not measurable | **1.54 mm** median (0.83 with a true iris) |

And the one that went against the design, kept in the headline position because
hiding it in a footnote is how a build starts reporting only its wins. The
constructed seat (`fit/bearing.ts`) was measured across 5 independent seeds at
the shipped [2, 36] band and **recorded INFERIOR**: it wins the per-seed median
in 2/5 seeds against a 4/5 adoption rule — the same 2/5 at every wide band —
pooled 1.03 / 5.22 med/p90 mm of passed-through scan error against the contact
seat's 1.24 / 3.62. Bulk slightly better, tail 1.4× worse, and the tail is what
drags the seed medians over. Q18 is settled on that record; `bearing.ts` stays
a testkit instrument and the running app seats with `contact.ts`. (Those are
the settlement campaign's digits on its frozen tree state; the merged tree
measures differently — `reports/seat.txt` is the current tree.)
