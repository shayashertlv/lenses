# Absolute scale — what it costs, what can supply it, and what cannot

Written 2026-08-25 after the owner rejected the ID-1 card outright: *"I don't
like the card method, I'd like the algorithm to not rely on it at all."*

Everything below was **measured this session** across ≥5 independent campaign
seeds. The raw probe output is in the session's workflow journals; the numbers
here are the ones that survived an adversarial second pass.

**Revised later the same day, and two of this document's own claims did not
survive re-checking.** §2's third defect ("every rung under-reports its own
sigma") and §5's attribution of the ranking's instability to
`FRAME_TO_FACE_WIDTH` are both wrong, and both were wrong for the same reason:
a number computed **pooled** rather than median-of-seeds, on a population whose
last two members are hard-coded and identical in every seed. The corrections are
in place below, marked and dated. The rule that catches this is the one at the
top of `docs/NEXT-SESSION.md`, and this document quotes it while breaking it
twice — *"a single draw is a coin flip; this tree has reversed a verdict three
times by forgetting it"*. Four times now.

---

## 0. The card is already gone, and three documents did not say so

`src/enroll/card.ts` left the working tree on 2026-08-25, at `f9c9093`. It was
never a tracked file, so no commit holds it — that is where the tree stopped
carrying it, not a commit you can recover it from. The shipping ladder is
**pd → iris → assumed**. Nothing in the running path has ever asked a wearer
for a card.

`docs/OPEN-QUESTIONS.md` recorded the deletion. `docs/ARCHITECTURE.md`,
`README.md` and `src/enroll/scale.ts`'s own header did not — they still describe
the subsystem in the present tense ("Its machinery now exists"), with its
measured results, sitting at the top of the ladder. That is what framed the
prop-free path as permanently second-best, and it is why a session read those
docs and told the owner to hold a card.

Residue cleaned with this document: the dead `disagreementPct` (returned `null`
on every path, read by nothing), the `ID1_CARD` entry in `check-constants.mjs`'s
exemption list for a constant that no longer exists, and the stale
`dist/src/enroll/card.js` — `dist/` is gitignored and never cleaned, so deleted
modules linger there.

---

## 1. The iris cannot be fixed by vision work

255 enrolments — 5 seeds × 3 camera geometries × 17 subjects — each run twice,
once shipping and once with the subject's own true iris diameter supplied. The
scale error factors exactly:

    (1 + E_total) = (11.70 / D_true) × (D_true / D_reproj) × ((Z_t/f_t) / (Z_s/f_s))
                     ─── (a) population ───   ─ (b) measurement ─   ─── (c) solver ───

Verified multiplicative to **3.11e-15** across all 255 runs.

| term | share of log-variance | median-of-seeds |
| --- | --- | --- |
| (a) population HVID assumption | **95.7%** (per-seed 81.2–98.0) | — |
| (b) measurement | 1.74% | 0.27% |
| (c) solver | 1.92% | 0.37% |

**Perfect vision buys 0.11 to 1.47 percentage points** out of a 10.3–14.5% worst
case. Median moves 3.05% → 2.99%. All five seeds agree, so the ≥4-of-5 bar is
cleared with room.

Two corrections to the tree's own prose:

- The shipping worst case is **14.5%, not the 10%** in `scale.ts`'s header. Ten
  percent was one draw; over five seeds the worst cell runs 10.28–14.50%.
- The estimator is **biased +2.59% signed** — 67% of runs read the wearer LARGE
  — because the assumed 11.70 mm sits 2.17% above the generated population mean
  of 11.452 mm. `11.70` is not a neutral constant; it encodes a population
  assumption, and which way it errs depends on who the customers are.

**Even with the wearer's group mean known** — the best any ethically permissible
deployment can do, via `POPULATION_HVID` — (a) still carries **90.9%**: a 3.5%
one-sigma against a 0.78% vision residual.

### Two incidental findings worth keeping

**The Z/f cancellation.** The bundle's solved depth is out by sd 8.38% and its
solved focal by sd 8.84%, **correlated −0.9992**. The iris ruler consumes only
the ratio Z/f, so the solver term survives at sd 0.56%. If the two were
independent, (c) would be 12.18% and would dominate everything. This is the
constructive twin of "knowing focal length does not help": *not* knowing it does
not hurt either, because the ruler only ever asks an angular question.

**`model.intrinsics.f` is not a physical focal length.** It is out by a median
5.45% and a worst **43.72%**, even with the lean beat present and
`coverage.canSolveIntrinsics` true. It is accurate only in combination with the
solved depth. Anything reading it as a lens property is wrong.

---

## 2. What a scale error actually costs, per claim

5 seeds × 10 subjects × 5 frames, ground-truth geometry with the factor imposed,
so this is the gauge alone. Camera f = 1024 px, 1024×768, face at 500 mm.

| claim | ±1% | ±2.5% | ±5% | ±10% |
| --- | --- | --- | --- | --- |
| Face projection, tracking, PnP, ratios, angles | **0** | **0** | **0** | **0** (exact) |
| Every confidence in the tree | 0 | 0 | 0 | 0 *(cannot notice — see below)* |
| PICTURE: frame front width | 2.5 px | 6.2 px | 12.5 px | 25 px |
| PICTURE: whole outline displacement | 1.8 px | 4.6 px | 9.4 px | 19 px |
| WIDTH: `widthDelta` | ±1.37 mm | ±3.44 mm | ±6.87 mm | ±13.74 mm |
| WIDTH: faces regraded /50 | 6 (12%) | 25 (50%) | 39 (78%) | 36 (72%) |
| PD readout | ±0.63 mm | ±1.58 mm | ±3.15 mm | ±6.31 mm |
| CORNEAL VERTEX | ±0.035 mm | ±0.10 mm | ±0.23 mm | ±0.50 mm |
| PUPIL HEIGHT (optical centre) — *nothing computes this; see below* | ±0.18 mm | ±0.5 mm | ±1.2 mm | ±2.8 mm |
| SEAT descent | ±0.19 mm | ±0.60 mm | ±1.26 mm | ±2.88 mm |
| SEAT pantoscopic | ±0.16° | ±0.47° | ±1.02° | ±2.09° |
| SEAT jumps (>2 mm descent) /250 | 7 | 28 | 77 | 160 |
| SCORE (median / p90) | 0 / 9 | 0 / 9 | 0 / 10 | ±3 / 13–15 |
| **TOP-RANKED FRAME changes /50** | **6 (12%)** | 16 (32%) | 25 (50%) | 36 (72%) |

### What each claim actually requires

- **Tracking, occlusion, frame-lock, face-only ratios and angles: nothing.**
  Exactly invariant, to machine precision. These are free and the product should
  know it.
- **The picture: one-for-one, and cheaper than it sounds.** The face's own
  projection is invariant, but the frame is a fixed metric object seated on a
  rescaled face, so the frame/face pixel ratio moves by exactly `1/(1+e) − 1`.
  At the iris's ~3% that is ~7 px on a 1024-wide render — visible in a
  side-by-side A/B, almost certainly not in a single picture of yourself.
  **The iris is already good enough for the try-on image**, and nothing in the
  tree currently says so.
- **Pupil height: nothing computes it.** The row above is a sensitivity for a
  verdict that does not exist — `score.ts` has no pupil-height measure and never
  has, though `frame-asset.ts` described `lensCentres` as being "for the
  pupil-height verdict" until 2026-08-31. The figures are what it WOULD cost if
  the verdict were built; nothing in the tree spends them today.
- **The width verdict: ~1%.** 1.37 mm per 1% against a 4 mm boundary.
- **The catalogue ranking: better than 1%**, which nothing prop-free delivers.
  See §5 — this is the one worth fixing without a better ruler.
- **The PD readout: ~1.5%** to stay inside the ~1 mm the trade tolerates. The
  iris at 4.7% is three times too coarse, which the UI already admits.
- **The corneal vertex: almost nothing** — 10% of scale costs 0.5 mm against a
  4 mm-wide band. What it needs is a measured `templeReachMm` (Q16), not a ruler.
- **The seat: 2–3%** for the medians, because the frame is metric and does not
  scale with the face.

**So the target is 1.5%, not 4.7% and not 0.1%.** That is the number any future
ruler work has to beat.

### Three defects this exposed — two real, one not *(resolved 2026-08-25)*

1. ~~**Every confidence in the tree is blind to scale error by construction.**~~
   **Real, and fixed in `2328f47`.** `scaleTrust` read `model.scale.sigma` and
   never the factor, so a wearer whose true HVID is 11.10 mm carried a 5.4%
   error at exactly the confidence of one the 11.70 mm ruler fits.
   `ScaleEstimate.disagreementPct` now carries the signed gap between the
   wearer's PD and the ruler it displaced, and `scaleSigma` in `fit/score.ts`
   widens the effective sigma by whatever exceeds the 4.8% two behaving rulers
   explain between them. It has no reach on the shipping single-ruler path, and
   that is the honest state rather than a gap: nothing else can see it.
2. ~~**The scale caveat is attached to the wrong verdicts.**~~ **Real, and fixed
   in `2328f47`.** The caveat is now proportional to a measured sensitivity —
   5 seeds x 12 subjects x 15 frames, per 1% of scale as a fraction of each
   verdict's own good band: width 34.1%, height 8.3%, depth 5.6%, panto 4.6%,
   pads 2.2%, load 2.1%, vertex 0.8%, level 0.3%. Width and vertex differ by a
   factor of forty and had carried the same flat multiply.
3. **NOT REAL. "Every rung under-reports its own sigma" does not survive its own
   evidence.** The 4.72 / 5.72 / 7.68 triple was computed **pooled over 150
   rows**, against this document's own >=4-of-5 rule. Per seed the median route
   implies 4.02 / 8.58 / 5.28 / 8.41 / 5.28 and the p90 route 7.73 / 4.30 /
   5.11 / 5.32 / 5.88 — **7.68% reproduces on 2 of 5 seeds, 5.72% on 2 of 5, and
   the SIGN of the gap between the two routes flips across seeds.** Median of
   seeds they agree, at 5.28% and 5.32%. Three further problems with it:
   **20% of the rows are not draws** (`generatePopulation(count: N)` appends two
   named extremes with irises hard-coded at 11.10 and 11.90 mm, identical in
   every seed, so 15 rows sit at exactly +5.41% — on top of the measured median,
   contributing nothing to the tail); it is scored on **temple width**, where
   the pipeline residual is sd 2.94% against 0.89% on the eye span, so raising
   the constant would bake the bundle's shape recovery into a ruler; and on the
   **whole-mesh gauge over 255 runs the same printed 4.72% has |error|/sigma
   median 0.65 and p90 1.72**, bracketing a well-calibrated one-sigma from both
   sides. A separate adversarial pass in the same session measured the fraction
   of runs exceeding the claimed one-sigma at 0.300 against a Gaussian's 0.317
   and said so; it did not reach this document.

   The sigma is therefore left alone. What it genuinely cannot express is not
   size but SHAPE — the error is one-sided (+2.59% signed, 67% of wearers read
   large) and a symmetric sigma has no way to say so. That is what
   `disagreementPct` is for.

4. **A fourth defect, found while fixing the first three and in neither
   document: the PD rung's confidence moves the WRONG WAY.** `sigma =
   opticianSigmaMm / knownPdMm`, and the wearer TYPES that number, so a larger
   mistyped PD prints a smaller sigma. Measured over 10 (seed, subject) pairs:
   a PD typed 10% high gives a **10.00% scale error at sigma 0.714%** against
   0% error at 0.786% when it is right — a wrong scale carried at *higher*
   confidence than a correct one, and 13.5x the iris rung's. Not patched with an
   invented recall term, because a mistyped ruler is a blunder and not a
   Gaussian; the disagreement is the defence, and at a 10% mistype it takes the
   width confidence to zero.

Also: **`worstClearanceMm` is identically 0.000 across all 2250 rows**, both
signs, every error level. By this tree's own rule that is a check that cannot
fail, and no conclusion about it should be drawn from this population.

---

## 3. Fusion is void, and the reason is about the harness

The plan was to fuse the iris rung (4.7%) with the anthropometric prior (5%) for
~3.4%. It does not survive contact:

**The NULL estimator — "assume the wearer is template-sized", no ruler and no
image evidence about size at all — beats the shipping iris rung on 5 of 5
seeds.** Median |scale error| 2.37% against 3.92%.

Because `generatePopulation` draws `coeffs[k] = truncatedNormal()` from N(0,1)
and `bundle.ts` charges the shape prior against *the same N(0,1)*
(`basis.sigma` is filled with 1), with the template sitting **0.27% from the
population mean size**. The prior is an oracle for the population it is grading.

> **The synthetic harness cannot adjudicate any scale estimator that leans on
> the shape prior.** By this tree's own rule that is a check that cannot fail —
> and here it is the harness, not the code. Any future scale work needs a
> population *not* drawn from `basis.sigma`: real scans, or a generator that
> samples sizes from published anthropometry independently.

Three further reasons not to revisit fusion as designed:

- **The independence premise is false.** corr(iris error, anthro error) = 0.534.
  It enters through the pipeline (corr(pipeline residual, true width) = −0.892),
  not through the HVID constant (corr = −0.063). The 3.43% the formula prints is
  a false precision claim; its p90 implies 5.26%.
- **The iris rung already carries ~40% of the prior.** Solved
  eyeSpan/templeWidth regresses on truth with slope 0.798, and iris-scaled width
  tracks true width with log-log slope 0.575. Fusing adds a second dose of the
  same evidence.
- **It reproduces the equity failure from the other side.** Fusion helps wearers
  within 6% of the template (4.72% → 2.69%) and **hurts** those further away
  (6.33% → 7.44%). Shrinking toward an unexamined template is the HVID bias with
  a different label on it.

Side finding worth its own ticket: `compareToTruth` defines `scaleErrorPct` on
**temple width** (`testkit/metrics.ts:89`), which is the span furthest from where
the iris is read. The iris pipeline residual is sd 2.94% scored on temple width
but **0.89% scored on the eye span**. A third of what `report:enroll` calls scale
error is temple-region shape recovery, not ruler error.

---

## 4. The physically admissible list, and why it is empty

Scale must be injected from outside the projection. The complete list is a known
**length**, a known **distance**, or a known **camera motion**. Focal length does
not help — it is angular. With props rejected:

| candidate | verdict |
| --- | --- |
| **Wearer's PD** from a prescription | **0.79%. Clears the 1.5% target. Already built** (`enroll.ts:185`), applied against the reconstructed 3-D surface. Not a prop — a number they already own. |
| Iris (HVID) | 3.05% median, 14.5% worst, 95.7% irreducible. Shipping default. |
| Autofocus distance | **Dead on physics.** DOF for a selfie camera (f = 2.7 mm, f/2.2) at 400 mm is 286–665 mm — **±47% of Z**, which *is* the scale sigma one-for-one. Worse than the iris on every geometry, including a laptop webcam (±29%). Before availability even matters: Chrome-only, and front cameras are predominantly fixed-focus. |
| WebXR depth | **Dead on reach.** `immersive-ar` is world-facing — rear camera, takes the display. There is no front-camera depth session, and Safari on iOS exposes no WebXR at all. |
| Screen-as-ruler / first-Purkinje glint | **Dead.** The browser exposes no physical screen dimension (CSS defines 1in = 96px by fiat), and the glint's size is set by corneal radius — another ancestry-correlated population constant, the exact thing this tree refuses for HVID. |
| Rolling shutter | **Dead by physics.** Gives angular rate; angular quantities carry no length. |
| Device motion / VIO | **2.65% median, 3.81% worst-of-5 — and a structural killer.** See below. |
| Acoustic ranging | Unmeasured. Physically admissible; the unknown speaker-to-mic latency cancels exactly if you use the *change* in echo delay across the existing lean-in/lean-back beat. Same head-vs-device ambiguity as VIO. One afternoon of a real chirp probe would settle it. |

### Why VIO does not work here, structurally

The IMU measures the camera's displacement in the **inertial** frame. The bundle
measures the camera's pose **relative to the face**. Those are the same quantity
only if the head is inertially stationary — and in a handheld selfie the arm
doing the thrusting is bolted to the same skeleton as the head. The scale error
is exactly `−d_head / d_phone`. **A 10 mm braced head recoil against a 200 mm arm
thrust is 5.26%**, worse than the iris it would replace, and because the recoil
is correlated with the thrust it is a *bias* that averaging does not touch.

Two counter-intuitive sub-findings, kept because they invert the obvious guesses:

- **Clock offset is nearly free; jitter is not.** A ZUPT-bracketed beat is
  first-order immune to a constant offset — 10 ms costs 0.09%, 100 ms costs
  0.74%. What hurts is 10 ms rms of timestamp *jitter*: 1.76% median even after
  16 beats. `framelock.ts` timestamps the pixels; the jitter is on the other
  stream.
- **The right beat is SHORT and BIG.** Gravity leak from attitude drift
  integrates as `g·dθ·T³/12`. A 0.35 s / 200 mm beat is 0.31%; 1.0 s / 100 mm is
  7.08%; 2 s is 13.45%; 4 s is 61%. **Coaching a wearer to "move slowly and
  smoothly" destroys the estimator.**

**Every VIO number above is simulated.** Nobody held a phone; no
`DeviceMotionEvent` was received. The head-recoil figure is the single most
consequential guess — at 2 mm the structural objection largely evaporates, at
20 mm VIO is unusable. It is measurable in an afternoon with this tree's own
bundle.

---

## 5. The reframe: comparisons are exact, absolutes are not

Scale is a **common factor**. It cancels out of differences and survives only in
absolutes:

    widthDelta(A) − widthDelta(B) = W_A − W_B          exact, no scale dependence
    widthDelta(A)                 = W_A − 0.90 × F     carries the whole error

Both frames' true widths are known to the millimetre, so **"this pair is 4 mm
wider than that one on you" is exact**, while "this pair is 4 mm too wide for
you" carries ~7 mm at 5%.

### The attribution in this section was wrong *(measured 2026-08-25)*

This section said `rankCatalogue`'s fixed metric target (`FRAME_TO_FACE_WIDTH` =
0.90) is "precisely why 12% of faces get a different top recommendation at 1%
scale error". **It is not, and the measurement is not close.** 5 seeds x 12
subjects, ground-truth geometry with the factor imposed, on the same five
parametric frames the 12% was taken on:

    parametric catalogue, top-ranked frame changes /60
                                      x0.975  x0.99  x1.01  x1.025
    shipping weights                     16     10      7      17
    the width measure dropped entirely   16     10      7      17   <- identical
    the width measure alone               0      0      0       0

Every one of those five frames defaults to `frontWidthMm` 138, so the width
verdict is byte-identical across the catalogue and orders **nothing**. What
moves the ranking is the **seat**: it is a contact equilibrium, a fixed-size
frame lands somewhere else on a wedge 1% bigger, and two frames land at two
different somewhere-elses. That difference does not cancel out of a comparison,
so no reference frame can remove it.

**Ranking against a reference is still worth having, and it shipped in
`85b4a9a`** — but for what it actually does. On a catalogue where widths differ
(5 pad geometries x 132/140/148 mm), median-of-seeds:

    scale error                +-1%    +-2.5%
    absolute                  16.7%    41.7% / 50.0%
    against a reference       16.7%    25.0% / 25.0%
    width alone, absolute      8.3%    25.0%
    width alone, reference     0.0%     0.0%     <- 0/60 cells, every factor

The width channel is fixed **completely and exactly**; the whole ranking is
roughly halved at +-2.5% and unmoved at +-1%. `docs/NEXT-SESSION.md`'s gate for
the change — "materially fewer than 6/50 and 16/50" — is met at +-2.5% and
**not met at +-1%, and cannot be** by this mechanism.

Two consequences worth carrying forward:

- **The remaining scale sensitivity of the ranking is a SEAT problem, not a
  ruler problem**, and its tail is the few percent of face/frame pairs that jump
  between catching the sidewall and sliding. That is the next thing to measure.
- **The reference form is worth 0.09 confidence, not 1.0, until the assets are
  measured.** The scale caveat comes off but the asset caveat does not, and it
  now applies to both frames — a difference between two widths is worth what the
  worse-known of the two is worth. Nine of ten catalogue assets and all five
  parametric frames declare `dimensionSource: 'assumed'`. One number per asset
  turns this verdict from 0.09 into 1.0, which is the strongest argument yet for
  the stage-8 measurement day.

### What this does NOT license

Resizing each frame to fit the scanned silhouette. If every frame is scaled to
sit correctly on the face, **every frame fits** — a 145 mm and a 130 mm frame
render identically, the one question a customer is buying an answer to becomes
unanswerable, and the picture stops showing the product that would arrive.
The size mismatch is not noise in the render; it is the signal.

There is also a physical wrinkle: the seat is a contact solve, not a rendering
scale. Shrink the face 5% and the pads catch higher on the sidewall — descent
1.26 mm, pantoscopic 1.02°, and for a few percent of face/frame pairs the frame
*jumps* between catching the sidewall and sliding. Resizing the frame hides that
rather than fixing it.
