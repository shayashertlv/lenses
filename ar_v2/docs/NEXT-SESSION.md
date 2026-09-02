# Continue the ar_v2 work

*Paste this whole file as your opening prompt, or just say: "read
`ar_v2/docs/NEXT-SESSION.md` and continue."*

---

## 0. Who you are and what this is

You are picking up `C:\Users\Shay\PycharmProjects\lenses\ar_v2` on branch
**`ar-v2-primary`**. It is a TypeScript eyewear try-on: scan the face once, track
against the scan, seat the frame by contact physics. The owner is Shay. He wants
a real pair of glasses on a real face and is not interested in process detail —
**lead with what he can see, not with what you verified.**

Start here:

```bash
cd C:\Users\Shay\PycharmProjects\lenses\ar_v2
node scripts/fetch-vendor.mjs      # only if vendor/ is absent
npm test                            # expect all green and FOUR green gates
```

Read, in this order: `docs/HANDOFF.md` (the ten-stage migration and its traps),
`docs/SCALE.md` (the scale investigation — do **not** re-derive it, and note the
banner at the top saying which two of its own claims did not survive), then
`docs/ARCHITECTURE.md` and `docs/CONSTANTS.md`.

## 1. House doctrine — match it or the review is worse than useless

- **"A check that cannot fail is a bug."** Before letting a new gate go green,
  sabotage what it guards, watch it go red, restore. Show the red/green table.
  Sabotage in **both** directions where the gate could be too eager: the reports
  gate has an R2 case proving a comment edit does *not* fire it.
- **Adopt on ≥4 of 5 independent seeds**, median-of-seeds with the per-seed
  spread. A single draw is a coin flip. **The tree has now reversed a verdict
  FIVE times by forgetting it, and two of those were `docs/SCALE.md` reversing
  its own claims from the session that wrote them.** Before quoting any number
  from a doc, check whether it was pooled or median-of-seeds.
- **Results that went against the change stay in the headline**, never a footnote.
- Every exported constant needs a row in `docs/CONSTANTS.md`
  (`measured`/`derived`/`stated`/`published`/`physics`/`assumed`).
  `check-constants.mjs` fails otherwise — **but it cannot see an orphaned ROW, or
  an orphaned EXEMPTION.** Sweep both by hand when you delete a constant.
- **The isolation boundary is mechanised.** `core/ enroll/ track/ fit/ detect/
  testkit/` must load in Node with no browser; `check-isolation.mjs` actually
  `import()`s every built module. `render/` and `app/` own the browser.
  `render/ → fit/` is the legal direction.
- Comments carry the reasoning and the measurement, not a restatement of the
  code. Long docstrings are the style — but they must be **true**. Six separate
  places have now been found asserting the opposite of the code beside them.

## 2. What landed in the last session (4 commits)

| commit | what |
| --- | --- |
| `2328f47` | the scale-honesty cluster — a second ruler, and a caveat sized to what scale actually moves |
| `85b4a9a` | ranking next to the frame on the face, where the scale cancels |
| `3100165` | `docs/SCALE.md` correcting two of its own claims |
| `bc28773` | stage 9 — `scripts/check-reports.mjs`, and the two stale reports it caught |

**Three results went against the plan, and they are the most useful things in
the session:**

1. **`ScaleEstimate.sigma` is NOT under-reported.** `docs/NEXT-SESSION.md` A3
   and `SCALE.md` §2 said the iris "prints 4.72% while its p90 implies 5.72% and
   its median 7.68%". That pair was pooled over 150 rows; per seed the two routes
   imply 4.02/8.58/5.28/8.41/5.28 and 7.73/4.30/5.11/5.32/5.88 — each figure
   reproduces on 2 of 5 seeds and **the sign of the gap between them flips
   across seeds**. Median-of-seeds they agree at 5.28% and 5.32%. It is also
   scored on temple width (where the pipeline residual is 2.94% against 0.89% on
   the eye span) and 20% of its rows are the two hard-coded named extremes. Three
   measurements in the same original session said the sigma was well calibrated
   and none of them reached the doc.
2. **The catalogue ranking's scale sensitivity is the SEAT, not the width
   target.** Dropping the width measure entirely from the parametric catalogue
   changes the top-ranked-frame count *cell for cell* — 16/10/7/17 either way —
   because all five `TEST_FRAMES` default to `frontWidthMm` 138 and the width
   verdict is byte-identical across them. Ranking against a reference fixes the
   width channel exactly (0/60 at every factor) and halves the whole ranking at
   ±2.5%, and **cannot** meet the ±1% gate, because what is left is a contact
   equilibrium landing somewhere else on a resized wedge.
3. **`enroll.txt` was not stale.** It reproduces every accuracy digit at its
   declared seed. `seat.txt` and `track.txt` were, badly.

## 3. The work queue, in priority order

### A. The seat's own scale sensitivity — now the largest fixable defect

This is where B's residual went. The ranking still changes its top pick on
16.7% of faces at ±1% of scale and 25–33% at ±2.5%, and every bit of that is the
seat. The mechanism is named but not measured: **a few percent of face/frame
pairs JUMP between catching the sidewall and sliding**, and for those the
movement is several times the median (`docs/SCALE.md` §2 counts 7/250 jumps at
1%, 28/250 at 2.5%).

What to measure first: which pairs jump, what distinguishes them, and whether
the jump is physical (a real bistability of the contact solve) or numerical (the
solver settling into a different basin). If it is numerical it is fixable and
the ranking gets materially more stable for free.

The probe from last session is a good starting shape —
`scratchpad/rank-scale.mjs` in the session's temp dir, or rebuild it: 5 seeds ×
12 subjects × 15 frames, ground-truth geometry with the factor imposed, reading
`assessFit` measures directly.

*Gate:* whatever the fix, re-run the top-ranked-frame measurement. Absolute
ranking currently sits at median-of-seeds 16.7% (±1%) and 41.7%/50.0% (±2.5%);
against a reference, 16.7% and 25.0%/25.0%.

### B. The tracker's smoothing lag at yaw — measured, unexplained, and in a report

`reports/track.txt` now records it. The solver is unchanged (every
`v2-no-smoothing` number reproduces to a hundredth of a degree); the filter is
2–3× worse in rotation through the middle of the yaw sweep than the pre-port
baseline, while losing no frames at all and cutting jitter by a third.

`PRIOR_MISS_EMA_RATE`'s ledger row already records the motion prior costing
7.1× at 1 Hz ±10° and 18.9× at 1.5 Hz ±8°, and this protocol is a deliberately
fast sweep. **Whether that is the same mechanism here is unmeasured.** It is the
first thing to look at if Shay says the frame lags a turn — and the
2026-08-23 note about the locked-latch default feeling "stuck/choppy" may be the
same thing seen from the wearer's side.

### C. ~~Stage 10 — retire v1~~ — DONE 2026-08-26. There is one pipeline now.

v1 is deleted. Four of the five preconditions were met; the fifth — the parity
ledger — was **overridden by the owner, not passed**, and `docs/PARITY.md` says
so at the top along with the six capabilities v2 still does not have. Do not
read the deletion as evidence that v2's coverage is complete; it is not, and the
ledger is the record of exactly how.

`docs/NOSE-V2-SPEC.md` was moved out of v1 before the deletion. Everything else
is on `origin/ar-v1` and `origin/ar-tryon`.

### C2. ~~The two correctness holes the ledger left open~~ — both CLOSED 2026-08-26

`src/track/identity.ts` (11 tests) and five `setOccluder` tests. `docs/PARITY.md`
carries what each does and how it was measured.

**What that work left behind, in priority order:**

1. **`detect/uncertainty.ts` still has no calibration check, and the identity
   watch's remaining hole is the shape of the one it would fill.**

   The drift guard landed (`IDENTITY_SIGMA_DRIFT_MAX`) and it took the
   mid-session-drift arms from **36/36 false convictions to 0/36** with honest
   and constant-offset arms untouched. What it cannot do is tell a detector that
   has GENUINELY become noisier from one that is merely lying about it — it only
   sees that the claimed sigma moved. So when a drift and a change of wearer
   arrive in the same frames, it recalibrates onto the stranger and detection in
   those arms falls from 93% to **0-5%**.

   That hole needed no drift until 2026-09-02: the bar is derived from session
   halves and was asked of ONE frame, and an ordinary same-person session
   crosses it in 8 of 8 synthetic captures. The retirement now waits for
   `IDENTITY_STRIKES` consecutive qualifying frames on the RISE, a fall still
   retiring at once (0 of 8 false retirements, 8 of 8 swaps convicted, every
   drift arm unchanged). **What is still open is the RELEARN window**: a
   sustained transient of twelve qualifying frames or more relearns the
   reference on its own inflated sigma and then retires a second time when it
   ends — measured, 2 retirements and 32 non-judging frames, saturating there.
   Under five frames now costs nothing at all.

   A real calibration check would close that: compare the claimed sigma against
   the empirical frame-to-frame scatter the estimator already computes
   (`UncertaintyState.disagreement`, an EMA of the unexplained motion after the
   median rigid translation is removed). If the two disagree, the estimator is
   miscalibrated and the app knows it independently of identity.
   `TrackerState.vfEma` is the other half of the instrument.
2. **The identity margin thins with population size.** Impostor-min falls 30%
   between 5 and 30 subjects while matched-worst rises 2%; linear extrapolation
   closes the gap at 35-45. Every number is synthetic, from a population drawn
   from the same basis the estimator fits, with no expression change, no ageing,
   no glasses already on the face and no relatives. Re-measure before trusting
   the bar on real faces.
3. **A shared device at cold boot is still unprotected.** The watch arms only
   after a scan taken in this session, so a stored model loaded by somebody else
   is never questioned. That is deliberate — see `identity.ts`, "What it refuses
   to answer" — but it is a real second case, not a solved one.

## 3b. ~~The open review queue~~ — ALL APPLIED 2026-08-26

**Every item below landed, plus the eleven prose corrections.** Thirteen
commits, `8c77816`..`dfee12b`, one fix per commit with its own sabotage table.
`npm test` went 304 -> 326 and the four gates are green. Read the commit
messages for the measurements; `docs/FIX-SPECS.md` is left as the brief it was.

**Six things went against the specification, and they are the useful part:**

1. **The reports gate was a coin flip** (not in the queue at all; found while
   verifying A2). `check-reports.mjs` blanked each report's `ms` column
   digit-for-digit, so the column's WIDTH survived into the canary hash. The
   seat canary took four distinct values in ten runs of one unchanged build,
   one of which was the committed stamp — so `npm test` failed the reports gate
   at random on an unchanged tree and would have passed it at random on a
   changed one. Fixed in `38b81ab`. The existing test for `stripTimings` used
   135 and 106, both three digits, so it was structurally blind to it.
2. **C3's 0.29 mm was measured at a density production does not produce.** The
   harness supplies ~500 noise-free contour points; the browser scan produces
   85–103 snapped ones, and the residual is point-to-NEAREST-POINT, which pulls
   tangentially when sparse. Measured the density ladder before trusting the
   wiring: every density down to 12 points still beats supplying nothing on 5
   seeds of 5, costing ~0.08 mm of a 0.36 mm gain.
3. **C4a had a second defect underneath it.** With one range the ENDPOINTS
   still failed, per FACE rather than per number: the PD correction's round trip
   lands the recomputed span up to 1.4e-14 mm from the typed figure, and 45 and
   85 are exactly the two numbers the app's own message invites.
4. **C1's reproducer does not reach the guard.** Driving `enroll` to 28 px of
   landmark noise returns `rounds: 0` — the pose-initialisation gate rejects the
   scan and the degraded stub supplies `converged: false` by construction.
   `runBundle` is never called. The test drives the guard directly instead.
5. **D4's falsifiable test cannot discriminate.** `|derivePads.padAngleRad -
   authored| < 0.25 rad` passes under BOTH definitions, because the derivation
   over-reads by 10.4 deg and its cone reading is only 12.4 deg out. What does
   discriminate: a yaw is invariant to a vertical stretch of the frame and a
   cone angle is not (1.40 deg against 4.90 over a 4x stretch).
6. **`frame-from-mesh.ts`'s stale table is structurally wrong, not numerically.**
   `TEMPLE_LEVEL_RUN_MIN_FRACTION`'s two "degenerate neighbours" no longer reach
   it at all; they are refused by `ARM_KNEE_RATIO_MIN`.

**Two tests passed under sabotage on their first draft and had to be rewritten**
— which is the doctrine earning its keep, not an aside. `snappedContourPoints`'
guard was asserted by output LENGTH, which the counting pass decides, so
deleting the guard from the WRITING pass changed nothing; and C4a's range was
swept using `PD_PLAUSIBLE_MM` itself as the fixture, so narrowing the constant
moved the fixture with it and three of four sabotages passed.

**Trap 5 bites in both directions.** `tsc` does not strip comments, so a
`doesNotMatch` fingerprint fails on a CORRECT build when the comment explaining
why the code is gone contains the string being refused. `tests/app.test.ts` now
reads comment-stripped bodies through one helper.

### What was left open — ALL SIX CLOSED 2026-08-27

Six commits, `5aa08e6`..`4bfa90e` plus this one. `npm test` 326 -> 328.

1. **`CalibrationField`'s agreement gate protected whatever it saw first.** A
   hard refusal makes the acceptance region a window centred on the current
   estimate, so once the field latched onto anything, the observations that
   would pull it back out were exactly the ones it refused. Replaced with
   `huber(1)`'s own weight — `core/robust.ts`'s header is the argument, in the
   tree's own words. Flat light 0.316 -> 0.214 mm; **an outlier arriving before
   the estimate settles goes from p90 1.93 mm to 0.38**. Costs 0.02-0.05 mm when
   the outlier arrives late, which is in the commit's headline.
2. **The pad verdict was describing the ASSET, not the wearer.** `PAD_INWARD_COS`
   was doing two jobs: FINDING the pads (needs 70 degrees) and SELECTING the
   contact samples (far too loose at 70). The samples spanned the pad's whole
   inward hemisphere — 19-29 degrees of normal spread, 3.2-13.5 mm deep — and
   `padSeatErrorArticulatedMm` reported that wrap as the wearer's own curvature,
   firing on 85-100% of faces for all ten derived assets. New
   `PAD_CONTACT_CONE_COS = 0.955`, graded against the two authored pads:
   khronos precision **48.2% -> 79.0%**, the bar fires on **29.9% of pairs
   against 65.1%**, parametric frames byte-identical.
3. **`derivePads`' angles came off the hemisphere while its samples came off the
   contact face.** Moved onto the contact faces: khronos separation error
   **+2.24 -> +0.31 mm**, navigator **+0.42 -> -0.03**, khronos yaw bias
   **+17.75 -> +7.00 deg**. navigator's yaw went 0.6 of a degree the wrong way.
4. **`PAD_CURVATURE_LIMIT_MM` re-derived, and reclassified `measured` ->
   `stated`.** Three legs: the physics (its 1.56x worst/RMS ratio is really
   **2.21x**, implying 0.63-0.68), separating the catalogue's deliberate tilt
   defect from its deliberate shape defect (peaks at 0.60 and is **weak at every
   threshold** — best 13 of 29), and the floor under it (a face's best frame
   reads 0.237 mm, so the bar must clear it). **Held at 0.9 by decision**:
   moving to 0.65 would take a wearer-facing refusal from 29.9% to ~48% on a
   discriminator just measured as barely discriminating. The value now carries
   its own argument instead of a derivation that had stopped supporting it.
5. **`willReadFrequently` measured on both sides — there is no trade.** The flag
   never took effect (`framelock.ts` creates the context first). Moving it to
   the creation site is not worth making: in Chrome at 1280x720, five
   repetitions, medians — readback 0.389 -> 0.361 ms, upload 1.854 -> 1.822,
   full draw+read+upload cycle 2.226 -> 2.200, every spread overlapping. One
   machine, one browser.
6. **The two checks that could not fire, characterised rather than left.**
   `runBundle`'s `fieldFactorisationFailures` term cannot be reached by any
   INPUT — tried `fieldPriorScale` 8/1/0/-1/-100 across 99/3/1/0 frames — and
   that is the right shape: it guards a CODE defect in the priors, one that has
   actually happened. `acrossSeeds` has no callers because its SIGNATURE is
   wrong for the campaign: it takes `figure(seed) => number` and so re-runs a
   74-second realisation per column, where every campaign here runs once per
   seed and reads a dozen figures out. The signature that would fit is written
   on it.

**The one thing this opened that is bigger than what it closed:**
`padSeatErrorArticulatedMm` separates "an optician can bend these pads" from
"this frame is the wrong shape for you" by 13 of 29 at its best threshold, with
the two distributions overlapping heavily. **The thing to build is a statistic
that separates tilt from shape properly**, not a better threshold on this one.

Two things tried and REMOVED, because they went against me:

- **Quadric smoothing of the pad samples.** After the contact cone it removes
  0.02 mm on navigator and 0.39 on khronos, and it destroys the only independent
  grading this derivation has — `tests/asset.test.ts` matches derived samples
  against authored face centroids EXACTLY, which is crisp only while the samples
  are verbatim. Smoothed, that score becomes a function of the match tolerance.
- **An all-or-nothing `PAD_MIN_FACES` fallback**, which put a cliff in the
  middle of the cone parameter and made the sweep non-monotone.

And one correction: `bc2c14a` published population figures measured on a build
that included the smoothing I then removed. Corrected in `28b86b1` — 65.1% ->
29.9%, not 63.4% -> 22.8%.

---

## 3b-original. The queue as it was specified

The 2026-08-26 full-tree review confirmed 47 findings. Roughly a third are fixed
(see the commits between `c85159d` and `947edf7`); three were in `ar/` and went
with it. **The rest are below, each with its verdict independently re-measured
before anyone touched code** — which was worth doing, because the investigation
corrected the review's own wording on nine of fourteen items and found two of its
proposed fixes to be wrong.

Read the verdict line before the finding. Several are **not** what the review
said they were.

### P1 — live, reproducible today, and no residual can see it

**A2. A stored model's intrinsics are planted on a camera of a different size.**
`src/app/main.ts:1401-1406` in `adoptModel`, and a looser second site the review
missed at `:599-601` in `startSource` (no `intrinsicsSolved` check at all,
currently masked only by boot ordering).

*Deterministic reproducer, no hardware change needed:* scan on a camera, reload
with the camera unavailable. `startSource` falls back to
`assets/samples/face-a.jpg`, which is **1024x1024**, and the 1280x720 record is
planted on it. `getUserMedia` is also asked for `{ideal: 1280} x {ideal: 720}`,
so another app holding the camera renegotiates silently.

*Why nothing notices:* PnP absorbs a wrong focal length into depth, so the
reprojection residual stays healthy — measured 4.95-5.90 px against
`SCAN_MAX_RMS_PX = 22`, and `pose.t[2] > 50` passes on 90 of 90 frames. **Every
gate reads green while the frame is drawn a third of a screen off the face.** The
fix has to be a precondition check, not a bar.

*And the review was wrong about the remedy:* `scaleIntrinsics` (`core/camera.ts:68`,
zero callers repo-wide — confirmed) is **not** the exact rescale. It scales `f`
and `cx` by `width/k.width` and `cy` by `height/k.height`, which is exact only
when the aspect ratio survives. Transferring a 63-degree 1280x720 record to
640x480 gives a **78.50-degree** camera. The right focal scale for a webcam mode
change is `max(sx, sy)`, because browsers crop-and-downscale rather than squash:
16:9 to 4:3 crops the sides, 4:3 to 16:9 crops top and bottom. Both verified
exact against ground truth.

### P2 — real accuracy or a wearer-facing number

**C3. The silhouette term is dead in production, and it is worth having.**
`main.ts:1046` and `enroll.worker.ts:134` hard-code `silhouette: null` while
`useSilhouette` defaults true, so all five paths in `bundle.ts` enter and
immediately `continue`. `BundleReport.silhouetteResiduals` is 0 on every real
scan and has **zero consumers**, so nothing notices. **Verdict: wire it up** —
measured at a replicated **0.29 mm off the standoff p90**, and the benefit
survives realistic edge noise. Note the consequence for the harness: the
`no-silhouette` variant *is* the production configuration today, so every
published enrolment figure was measured on the wrong arm.

**C4a. A PD the app accepts is then withheld — "worse than stated".** The
correction gates on [45, 85] (`enroll.ts:187`, matching the UI) and the readout
on `PD_PLAUSIBLE_MM` [46, 80] (`enroll.ts:313`). Since the correction scales the
geometry so `interpupillarySpan` equals `knownPdMm` **by construction**, the
readout gate is being applied to the wearer's own typed number. A wearer who
types 45 has it used as the absolute ruler and then not shown.

**D1. The wearer-facing "% on the nose" describes different physics from the
solve — and the review's remedy is a sign error.** `describeSeat`
(`contact.ts:1217`) projects onto `cp.ny`, the interpolated vertex normal; the
contact row (`contact.ts:759-763`) is built from `u = (p - cp)/|p - cp|`. But
`u` is the gradient of the *residual*, not the direction of the force: with
`E = k*d^2/2`, `F = -k*d*u`, so the vertical component wanted is
**`+normalN * (cp.y - p[1])/|p - cp|` — minus `u`, not `u`.** Substituting `u_y`
naively was measured and is wrong.

**B1. `snapOffsets` skips its ridge gate whenever the peak lands at a band end.**
`snap.ts:189` and `:196` both guard `bestIdx > 0 && bestIdx < steps - 1` with no
`else`, so 2 of 17 positions skip both the flank check and the sub-pixel
parabola and emit `offsetPx = +/-searchPx` exactly. Two corrections: "at full
confidence" understates it — band-end accepts carry **higher** confidence than
interior ones (median 0.647 against 0.588), because to be the band max and clear
the median test a spike has to be big; and "clamp it" is not available, the code
already clamps. Reject, or gate one-sided.

*Exposure is genuinely confined to noisy captures:*

    grain            peak at band end   accepted   share of confident samples
    +/-4  (spec)       42/352            0          --
    +/-8  (dim)        42/352           30          37.5%
    +/-16 (high ISO)   42/352           42          29.4%

At 450 mm, 8 px is 6.13 mm and `contourPushes` caps at 3 mm — so every band-end
acceptance is a **full-cap push in a direction noise chose**.

**D4. `padAngleRad` is two different angles under one name.** Every claim
independently reproduced: the parametric path consumes it as a yaw about the
vertical (`ny` exactly 0), both producers measure a cone angle from the sagittal
normal, mean `|ny|` is 0.327 / 0.310 on the deriving assets, and dropping `ny`
moves the mean 6.67 / 8.48 degrees. Decide which definition keeps the name, and
change `assets/glasses/ground-truth.json`'s stated definition with it.

### P3 — real, and smaller or different than the review said

**D2. Two bars for one decision, and the tree already knows.** `score.ts:214`
grades on a bare `1.0` while `solveSeat` fires at `PAD_CURVATURE_LIMIT_MM = 0.9`.
That constant's own docstring names the defect verbatim — and points at
`advice.ts`, which no longer exists (renamed to `score.ts`). *Related: this
line used to name stale `dist/src/fit/advice.js` and `bearing.js` as surviving
with no `src` counterpart. Both are gone from `dist/` now; the three that do
survive are listed in `docs/FIX-SPECS.md`.*

**C1. `converged` cannot go false — but the implied fix is worthless and the
real defect is next door.** `converged === false` iff not one landmark in any
frame survived. Read the spec before changing it.

**C2. The `visibility` overwrite is one frame per scan, not every frame.** The
review's blast-radius fingerprint belongs to a bug already fixed. Still worth
correcting; not worth the priority the review gave it.

**B3. `framesTracked` is session-cumulative against a docstring saying
per-acquisition — and the implied fix is backwards.** Do not change the counter;
fix the comment and check each of its three readers.

**C4b. `fieldRmsMm` is in pre-scale gauge units, and the gauge is not constant** —
it varies per solve by 1.099x to 2.732x, so the field understates the millimetre
figure by 9.0% to 63.4%, median 31.6%.

**C4c. `landmarkRigidity` is a DOCUMENTATION defect only.** The 0..1 claim is the
stale half of a docstring whose other half, twenty-two lines above the code,
already says the nose boost is deliberate. Measured over 72 paired cells, the
over-weighting does nothing.

**D3. `useEars` is dead, and there is nothing to sweep.** Four references, all in
`contact.ts`, no caller, no test, no doc. The review's "ledger row" does not
exist — `SEAT_DEFAULTS` is exempted wholesale.

**A3. The frame-sanity tripwire names its regression in a string** and cannot see
it: it computes from `seat.pose` and frame-local marks, never from
`frameNode.matrix`.

### Prose that asserts the opposite of the code

`raster.ts:34` (denies `render/` has an occlusion pass — it does),
`report-occlusion.ts:1324` (claims a loss rate its own table prints as 0),
`telemetry.ts:74` (cites a `scale.ts` card branch that never existed there),
`mesh.ts:307` (promises a cross-check against `LM.EYE_*` that no code performs),
`scale.ts:124` (documents an iris yaw gate that does not exist),
`metrics.ts:204` (`acrossSeeds` has no callers), `report-enroll.ts:29`
(`useTrueIris` docstring inverted), `report-seat.ts:144` (legend defines
`|depth err|` as the flushness number), `extract-pad-truth.mjs:145` (a +/-1.4 mm
tolerance built on a number its own data contradicts), `bundle.ts:884` (claims a
harness pins the silhouette grid; none does),
`frame-from-mesh.ts:218` (`TEMPLE_LEVEL_RUN_MIN_FRACTION`'s derivation table is
stale in all three rows).

### Still untested, browser-side

`detect/mediapipe.ts` — no test, no report, no fixture, and now the largest of
these because the identity watch depends on the sigma it estimates.
`app/sources.ts` and `app/ui.ts` — no test at all.

## 4. Blocked on Shay — tell him, don't wait silently

- **One physical day for stage 8: ten weighings and calipers.** This got more
  valuable last session, not less. The comparative width verdict is worth **0.09
  confidence instead of 1.0** purely because eight of ten catalogue assets and all
  five parametric frames declare `dimensionSource: 'assumed'` — the caveat now
  applies to *both* frames in a comparison. One measured number per asset turns
  the tree's only scale-free width claim from nearly worthless into exact.
  **The refusal premise is gone and the width one is not.** All ten assets now
  wear — `frameFromMesh` finds each arm from geometry rather than needing a part
  called `temple` — so this is no longer about whether a frame can be tried on.
  It is only about WIDTH, which is the one quantity no geometry recovers.
- **One scan session.** Set PD → scan → **Save this scan**. Still the only route
  by which a real face reaches an otherwise entirely synthetic harness. It is now
  also the only way to see `ScaleEstimate.disagreementPct` do its job on a real
  wearer, since it needs two rulers and a real PD.
- **A written side-by-side verdict + screenshots per asset**, for stage 10.
- Answered last session, recorded here so nobody re-asks: the ranking reference
  is **the frame currently on screen**, and the PD ask **stays in the Instruments
  drawer** for now.

## 5. Open, measured, unfixed

- **The PD rung's confidence moves the WRONG WAY, and only a second ruler
  catches it.** `sigma = opticianSigmaMm / knownPdMm`, and the wearer TYPES that
  number, so a PD typed 10% high gives a 10.00% scale error at sigma 0.714%
  against 0% error at 0.786% when it is right. Deliberately not patched with an
  invented recall term — `disagreementPct` is the defence and it works — but on
  a scan where no iris resolved, a mistyped PD has nothing checking it at all.
- **The synthetic harness cannot grade a scale estimator.** The null — "assume
  the wearer is template-sized" — beats the shipping iris rung on 5/5 seeds,
  because `generatePopulation` draws from the same N(0,1) the shape prior charges
  against. Any future scale work needs a population **not** drawn from
  `basis.sigma`. `docs/SCALE.md` §3.
- **`model.intrinsics.f` is not a physical focal length** — median 5.45% out,
  worst 43.72%. Accurate only in combination with the solved depth (correlated
  −0.9992). Anything reading it as a lens property is wrong.
- **`derivePads`' `padAngleRad` bias is +11.1°** (navigator) / **+7.0°**
  (khronos) as of 2026-08-27, down from +10.4 / +17.7. `PAD_CONTACT_CONE_COS`
  cut it by computing the angles off the pad's contact face rather than its
  whole inward hemisphere; the vertical lean came down with it (−0.71 / +2.50
  against −2.08 / −3.93) and `padSeparationMm` improved most of all, +0.42 →
  −0.03 mm on navigator and +2.24 → +0.31 on khronos. navigator's yaw is the
  one figure that went the wrong way, by 0.6 of a degree. Still not zero, and
  still harmless today: nothing in `src/` reads the angle.
- **navigator exceeds `PAD_CURVATURE_LIMIT_MM` on 2–3 of 7 subjects** at every
  sample count, against the parametric standard's 1.
- **`worstClearanceMm` is identically 0.000 across 2250 rows** — a check that
  cannot fail. The synthetic population cannot exercise it.
- **The frame is described three times, not two.** `contact.ts`'s
  `clearanceSamples` stays separate deliberately. **The fix is to give the rim a
  dish, not to merge.**
- **`compareToTruth`'s `scaleErrorPct` is on temple width** (`metrics.ts:89`),
  the span furthest from where the iris is read — sd 2.94% there against 0.89%
  on the eye span. A third of what `report:enroll` calls scale error is
  temple-region shape recovery. This is now known to have corrupted a published
  conclusion (§2.1 above); it is worth fixing or renaming.
- **The 6.7 mm PD disagreement has no underlying measurement.** `HANDOFF.md:307`
  asserts it, `telemetry.ts:65` and `main.ts:1465` both cite HANDOFF.md back, and
  `NEXT-SESSION.md` repeated it. Every hit in both workflow journals is an agent
  reading the doc. Do not cite it as evidence.

## 6. Traps — each cost real time

1. **`fixtures.ts`'s two `TEMPLATE_PATHS` are both load-bearing.** `src/testkit/`
   and `dist/src/testkit/` are different depths. Cutting one silently dropped the
   suite from 216 tests to 70. Same pattern in `tests/asset.test.ts`.
2. **Never test Z against an asset's depth midpoint.** Temples run ~140 mm back.
3. **Mirroring a mesh reverses winding**, which inverts every normal.
4. **v1 is in CENTIMETRES, this tree is in millimetres.** Every ported constant
   is ×10.
5. **Comments survive into `dist/`**, so a textual gate on an English word is a
   check that cannot fail. Instantiate the compiled function instead.
6. **`dist/` is never cleaned.** A deleted module's artefact lingers.
7. **The Bash tool chokes on heredocs containing apostrophes or backticks** —
   and it will fail with `unexpected EOF` rather than anything informative. Write
   the Python patch script to a file with `Write`, then run it. This cost time
   again last session.
8. **Do not run `npm run build` from two places at once** — `dist/` is shared.
9. **`ar_v2/serve.py` honours `PORT`**; `.claude/launch.json` has `autoPort`.
   Port 8020 usually has one of Shay's own servers on it — do not evict it.
10. **`generatePopulation(mesh, basis, { count: N })` returns N+2 subjects**, and
    the last two are named extremes with irises hard-coded at **11.10 and
    11.90 mm, identical in every seed**. At small `count` they are 20% of the
    sample and they produce the same two scale errors (+5.41%, −1.68%) in every
    draw. **Any population statistic at small N is contaminated by them** — this
    is what put a 2-of-5-seed number into two documents as fact.
11. **The committed reports are CRLF and a regenerated body is LF**, so a naive
    `diff` shows every line changed and tells you nothing. `tr -d '\r'` both
    sides, and find the body's start by its title line, not by a fixed offset.
12. **`npm run report:<name>` now regenerates AND stamps** through
    `scripts/check-reports.mjs --write`. Do not hand-edit a `[provenance]` line;
    the canary is what makes it mean anything. The one exception on record is
    2026-09-01, when the canary's own CONFIGURATION changed — occlusion's canary
    went from a forced seed to the campaign shape its committed body was
    generated at — so `canary=` moved while the body stayed byte-for-byte what
    the generator wrote. That field was typed, because `--write` has no
    stamp-only path and regenerating a body to land a gate fix would refresh a
    measurement. It is safe only because occlusion's `source` is drifted: the
    gate re-derives the canary on the very next run and goes red if the typed
    value was wrong.

## 7. Where the evidence lives

Three investigation workflows persist on disk with every probe's full return
value — read these before re-deriving anything:

```
C:\Users\Shay\.claude\projects\C--Users-Shay-PycharmProjects-lenses-ar-v2\
  59c106cc-a485-4a94-a9f8-5cab29c0f667\subagents\workflows\
    wf_f8992775-8b9\journal.jsonl   asset pipeline (stages 4/5/7)
    wf_a37e9568-955\journal.jsonl   absolute scale (docs/SCALE.md)
  a47a49b8-a8c8-421e-984b-244928c95f51\subagents\workflows\
    wf_5998756d-034\journal.jsonl   the scale-honesty recon, including the
                                    re-check that overturned A3
```

Each line is one agent's result. **The third one is the important one**: it
contains the per-seed breakdown that nobody in the original session computed,
and the three independent measurements saying the sigma was fine.
