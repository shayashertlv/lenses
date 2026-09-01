# Open questions

Things this build cannot answer from inside itself. Each one names what is
currently assumed, what it would take to settle it, and what it is worth.

The list is short on purpose. v1's audit found 196 constants of which only 32
rested on physics or geometry, and the reason that number got so large is that
"we should measure this someday" was never written anywhere a reviewer would see
it. Anything in this file is a number the system is currently guessing.

---

## Q-new 2026-08-25 — is 0.9 mm still the right pad-curvature limit for a REAL pad?

`PAD_CURVATURE_LIMIT_MM` is 0.9, and it was set against `parametricFrame`'s pad:
a flat 12 x 8 mm rectangle sampled 18 times. `navigator.glb`'s pads are moulded
and genuinely curved, and measured over 7 synthetic subjects it exceeds the limit
on **2 to 3 of them at every sample count** (812, 200, 64, 32, 18), where the
parametric standard exceeds on 1.

That is not a sampling artefact — it is the same at full resolution — so it is
one of two things and nothing here distinguishes them:

 - navigator really does bed down worse than a flat pad on these noses, and the
   verdict is right; or
 - 0.9 mm is a bar calibrated on a shape no real pad has, and every mesh-backed
   asset will trip it.

Deciding needs a real pad on a real nose, which is the same measurement day
stage 8 already needs. Until then the verdict fires more often on measured
frames than on parametric ones, and a reader should know that is expected.

## Q1 — What is the detector's actual landmark noise?

**Assumed:** 0.7 px at 640 px long side, on a well-lit frontal face
(`detect/uncertainty.ts`, `UNCERTAINTY_DEFAULTS.floorPx`).

**Why it matters:** it is the denominator of every residual in the bundle, so it
sets how much the solve believes the landmarks against its own priors. Too low
and the shape chases noise; too high and the priors win and everyone gets the
average face — which is v1's failure mode arriving by a different road.

**To settle it:** a still image, driven through the detector 200 times with
seeded sub-pixel jitter, reporting the per-landmark spread. About an hour of
work, and it needs a real camera or a real photograph, which is Q8.

**Worth:** moderate. The solve is not very sensitive to it within a factor of
two — but nobody has checked that claim either.

---

## Q2 — What is the detector's bias against real skin? **(needs you)**

**Assumed:** zero (`enroll/detector-bias.ts`).

**Why it matters:** this is the accuracy floor of the whole reconstruction. The
synthetic harness injects a 0.6 mm per-landmark systematic offset — modelling
the fact that a landmark convention is not a skin surface — and with it in place
the recovered nose sits at ~0.7 mm RMS instead of ~0.35 mm. **Half the current
error is this.**

It cannot be solved per wearer. From one detector's output, "the detector is
biased" and "this face is that shape" are the same observation. It is a property
of the detector, measurable once, against ground-truth scans.

v1 measured it from **two faces** and shipped the result; its own audit flags
that as `n=2`.

**To settle it:** 10–20 people, each with a real 3D face scan *and* a set of
camera frames. `measureDetectorBias()` already computes the answer from paired
(reconstruction, truth) geometries — it just needs the pairs. Public datasets
with paired scans and images (FaceScape, Stirling/ESRC, the NoW benchmark's own
subjects) would do it without recruiting anybody.

**Worth:** high. It is the single largest remaining error term.

---

## Q3 — CLOSED 2026-08-25 by removal. The card ruler is no longer in the tree.

The question was whether to ship a beat asking the wearer to hold an ID-1 card
against their brow, for an absolute ruler better than the pooled iris. It was
built (`enroll/card.ts`: a gradient-edge detector, quad fit, sub-pixel line
refinement, and a factor solver with a propagated-and-measured sigma model),
measured at **0.80% median scale error at 1 px of edge noise against the pooled
iris's 5.14% on the same runs** — and never wired to the app, because the
detector had never seen a real frame, a real card, or a real hand.

It is now deleted, along with the beat and the ladder's card rung, as part of
reducing this tree to what a production try-on needs. The scale ladder is
`pd > iris > assumed`.

**This is a deletion, not a refutation.** The measurement above still stands and
the iris remains the dominant absolute-scale error (Q8). If an absolute ruler is
wanted later, the argument for a card is unchanged and the implementation is in
`git log` — it was removed for being unvalidated and unwired, not for being
wrong.

**Correction, 2026-08-31: three of the clauses above are wrong, and they are
the ones a reader would act on.**

- *"the argument for a card is unchanged"* — it is not. The owner rejected the
  method outright: *"I don't like the card method, I'd like the algorithm to not
  rely on it at all."* (`docs/SCALE.md`). That closes the argument; it does not
  leave it open pending evidence.
- *"the implementation is in `git log`"* — it is not. `card.ts` was never a
  tracked file, so `git log --all -- '*card*'` returns nothing across the whole
  history. The 0.80% row above cannot be re-derived from this repository. It is
  a record and nothing else.
- *"it was removed for being unvalidated and unwired, not for being wrong"* —
  true of the removal on 2026-08-25 and false as a standing invitation. Read
  today it promises that a real camera session (Q8) would revive the card. It
  would not: the rejection came after the removal and is about the method, not
  its validation state.

What replaces the card is not a better ruler but a smaller requirement:
`docs/SCALE.md` sets the target at 1.5%, and the wearer's own prescription PD is
the rung aimed at it — a propagated ruler sigma of 0.79%, which
`enroll/scale.ts` is careful to say is not a measured end-to-end error of that
rung.

## Q4 — What is the compliance of nasal skin under a pad? **(needs you)**

**Assumed:** 1.0 N/mm combined across both pads (`fit/contact.ts`, `SKIN`).

**Why it matters:** it sets how deep the pads sit and therefore, weakly, the
resting height.

**Derived, not measured.** The derivation is in the file: the sidewall normal is
24% vertical, a 24 g frame weighs 0.235 N, so ~1 N of normal force is needed if
the nose carries all the weight, and pads visibly compress skin by about a
millimetre. That is one observation of one nose with arithmetic attached.

**To settle it:** a force gauge, a pad, and ten noses. Genuinely an afternoon.

**Worth:** believed low — but the harness does *not* say so, and an earlier
version of this entry claimed it did. Nothing in the tree sweeps stiffness;
`testkit/report-seat.ts` sweeps pad separation, which is a different parameter.
The expectation is still that a factor of four moves the settled height by
about half a millimetre and changes no fit verdict, because the wedge slope
dominates — but that is the argument in `fit/contact.ts`'s header, not a
measurement. Running the sweep would both settle the worth and cost about ten
minutes, which makes it the odd one out on this list: it is the least-supported
number in the tree *and* the cheapest to bound.

---

## Q5 — The template has no ears.

**Assumed:** the temple rest point is extrapolated from the outer canthus and
the cheek landmark (`fit/contact.ts`, `earRestPoints`).

**Why it matters:** the ear carries 15–25% of the frame's weight in the current
model and is what stops it sliding down the nose indefinitely. The rest point is
a guess placed by anatomy, not a measurement, and v1's version of this guess was
about 10 mm too high — enough that with any realistic stiffness the ear term was
22× the frame's weight and shoved the frame off the nose entirely.

**To settle it:** a template with ears. FLAME 2023 Open has them (CC-BY-4.0), and
swapping it in is the same job as Q6 below.

**Worth:** moderate. It caps how well pantoscopic tilt and weight distribution
can be predicted.

---

## Q6 — Temple splay is modelled as a field and used nowhere.

**Assumed:** `FrameAsset.splayStiffnessNPerMm` exists and nothing reads it. Arm
contact is excluded from the clearance test entirely, because testing a straight
hinge-to-ear line for non-penetration reported a constant 10 mm foul on every
face (the line passes through the skull).

**Why it matters:** a wide head pushes the arms outward; the arms push back, and
that force has a vertical component at the ear that carries part of the weight.
It is why a stock frame sits differently on a wide face and a narrow one even
when the nose is identical.

**To settle it:** model the arm as a curve with bending compliance and put its
contact into the energy. Not hard, but it needs an arm geometry more honest than
"a straight line from the hinge to a point behind the ear".

**Worth:** moderate, and it grows with the frame's weight.

---

## Q7 — Should the pose filter be on? **(needs a real camera)**

**Assumed:** off (`track/tracker.ts`, `TRACKER_DEFAULTS.smooth`).

**Why:** measured. Across the synthetic population and camera ladder, every One
Euro tuning from v1's own down to a very light one is worse than no filter — on
lag *and* on jitter, monotonically. Once pose comes from six parameters against
known geometry with 300+ correspondences, the estimate is cleaner than the
filter's own time constant, so the filter only adds error.

**Why it is a question anyway:** that is a synthetic result. The model here
treats landmark noise as independent per landmark. A real detector's noise is
almost certainly correlated across landmarks — whole regions drift together
under a lighting change — and correlated noise produces pose noise that
per-landmark independence underestimates. If real pose noise is materially
higher, the filter earns its place again.

**To settle it:** run `npm run report:track` against a recorded real session with
`smooth` both ways. Needs Q8.

**Correction, 2026-08-31: the assumption above is not what ships, and the
falsifier in "Why it is a question anyway" came true.** The app has run the One
Euro since 2026-08-23 — under the stillness latch first, and as the plain
`smooth: true` default after that latch was rejected — following the first real
wearer's report of jiggle that grows with yaw, the correlated-noise failure this
question wrote down in advance. `TRACKER_DEFAULTS.smooth` is still `false`, which is the library
default and not the shipped one; `src/app/main.ts`'s `App.smooth` comment
carries the full history, including the stillness latch that shipped in between
and was rejected as "stuck and choppy".

The synthetic half has also moved, and it moved the other way. Re-measured
2026-08-31 with `runTrackReport` at the campaign seeds: the filtered arm now
wins jitter median 5/5 (0.945 mm against 1.469) and p90 5/5 (1.944 against
2.519). "Every tuning is worse than none, on lag *and* on jitter" is no longer
true of jitter on this tree — the filter changed (`derivativeCutoffHz` 1 → 5,
`ROTATION_DAMPING` 0.25, and the port in `f9c9093`) while the unfiltered arm did
not. What the filter costs is lag: 4–7× the unfiltered arm's placement error
through the middle of the yaw sweep, and a forward-push swing of 3.97 mm against
0.50. So Q7 is no longer "should the filter be on" — it is on — but "is this
tuning the right trade", and that still needs a real session (Q8) plus the A/B
against latch v2 the Steady button already carries.

---

## Q8 — Nothing here has ever seen a real camera. **(needs you)**

**Assumed:** that the synthetic capture model is representative.

This is v1's open item, inherited verbatim, and it is the honest headline of this
whole build: *there is no webcam on the machine this was written on.* The camera
path is exercised as far as `getUserMedia` and no further. Everything downstream
is covered by synthetic fixtures. As of 2026-08-22 the list of subsystems that
have never seen a real frame includes the card detector (Q3), which was
validated only against images this tree's own rasteriser produced. (2026-08-31:
the card detector left the tree on 2026-08-25 and is not on the list any more —
Q3.)

The synthetic model was wrong twice during this build, in ways that changed
engineering decisions:

- Occluded landmarks were modelled as *noisier*. They are actually *biased*,
  toward the detector's own average-face prior. Fixing that changed the profile
  beat from "worth nothing" to "worth 20% of the protrusion error".
- Head position was modelled as jittering 6 mm per frame. Real heads wander
  smoothly. Fixing that changed the measured cost of the pose filter by 3×.

Both were caught by suspicion, not by the harness. There will be others.

**To settle it:** run the app on a machine with a camera. `python serve.py`, open
`http://127.0.0.1:8020/`, do the scan. What would be most useful back:

1. Does the guided scan work — is it obvious what to do, does it finish?
2. What does `__ar.model.quality.nose` report after a real scan?
3. Does the frame stay on the nose past 40 degrees of yaw?
4. Is the mirror delay (shown in the readouts) noticeable?

---

## Q9 — FLAME 2023 is not installed. **(needs you — a licence click)**

**Assumed:** a hand-built 20-mode anthropometric basis
(`core/shape/anthropometric.ts`), because it can be constructed from the template
alone and therefore always runs.

FLAME 2023 Open is **CC-BY-4.0** — commercial use permitted, unlike every earlier
FLAME — with 300 shape components learned from 33,000+ scans of 3,800+ heads. It
should be strictly better as an identity basis, and it has ears (Q5).

**To settle it:** accept the licence at https://flame.is.tue.mpg.de/ and download
the 2023 model. `core/shape/basis.ts` is an interface with exactly this swap in
mind, and `basisExplains()` measures how much of a held-out face each basis can
account for — so the swap is a measurement, not a belief.

**Worth:** unknown, and that is the point of the instrument. My expectation is
that it improves the *head* materially and the *nose* barely, because the nose is
recovered by the free-form field rather than the basis — but that is a
prediction, not a result.

---

## Q11 — The enrollment worker is not yet the whole story.

**Done:** the bundle now solves in a module worker (`app/enroll.worker.ts`),
so the main thread stays responsive through the scan. Verified end to end in a
browser: `ranOn: 'worker'`, and the model round-trips through the same
`serializeFaceModel` path a returning wearer uses — so a format bug cannot hide
until somebody reloads.

It lives in `app/` and used to live in `enroll/`, and the move is itself an
open-questions-shaped fact: `enroll/` is an *enforced-headless* directory, and
this file sets `self.onmessage` at module scope, so it could never be imported in
Node — while `scripts/check-isolation.mjs` went on reporting the boundary intact.
The blacklist checked the source text and nothing tried to load it. That is why
the isolation check now also `import()`s every built headless module. Any
reference to the served URL `dist/src/enroll/enroll.worker.js` is stale; it is
`dist/src/app/enroll.worker.js`.

**Two reporting changes worth knowing before trusting an old dump.** `loop:
'timer'` and the ` · timer loop` readout suffix used to be reachable by an
ordinary tab switch of more than ~1.2 s, and are now reachable only by a
genuinely non-firing rAF in a *visible* tab (the loop also takes rAF back if it
returns). So any prior dump reporting `loop: 'timer'` may have been taken on a
falsely-latched 60 Hz timer rather than in a real timer-only environment, and its
pacing figures should be re-measured rather than trusted. Separately,
`runtime.enrollmentWorker` used to under-report inline solves, because a fallback
from a healthy worker was indistinguishable from having no worker at all;
`enrollmentSolvedOn` now separates them, so the population of dumps that *appear*
to have solved on a worker may shrink once real ones come in.

**Not done:** the *detector* still runs on the main thread, synchronously, at
~20 ms a frame. v1 put it in a worker and measured the trade carefully: a worker
gets its own GL context, which on some machines makes inference genuinely slower
(34 ms against 14 ms on the machine v1 shipped on), so it shipped a measured
one-way fallback rather than a blanket choice. That whole apparatus should be
ported — it is a solved problem, recoverable from `origin/ar-v1`'s `src/tracker.js` with its
reasoning attached.

**Worth:** moderate. Under the frame lock the display advances once per
detection, so a 20 ms main-thread inference costs UI responsiveness rather than
mirror rate — which is the cheaper of the two coins, and exactly the trade v1
documented.

---

## Q14 — Camera exposure is not controlled, only spoiled.

**Fixed the obvious half:** the app asked `getUserMedia` for 60 fps. Frame rate
and exposure are the same knob on a sensor — at 60 fps the longest possible
exposure is 16.7 ms, at 30 fps it is 33 ms — so the request threw away a full
stop of light in any dim room, for a rate the pipeline never came close to
consuming. v1 asked for the same thing and its note praised the latency saving
without mentioning the cost. Now 30.

**Still open:** nothing measures or reports whether the camera is actually
exposing well, beyond a mean-luminance readout and a "dim" hint. A wearer in a
dim room gets a dark mirror, noisier landmarks, and a worse scan, and the only
advice the app can give is "add light".

Worth investigating: `MediaStreamTrack.getCapabilities()` exposes
`exposureMode`, `exposureCompensation` and `brightness` on some platforms, and
where they exist the app could ask for a longer exposure explicitly rather than
hoping. It is device-specific and fragile, which is why it is a question rather
than a change.

**Worth:** moderate, and it compounds — Q1 (landmark noise) and Q13 (yaw
compression) both get worse in the dark, so this may be upstream of both.

---

## Q13 — Measured yaw is compressed against physical yaw, by an unknown factor. **(needs you)**

**Reported, not measured here:** a wearer found that the scan's "30 degrees of
turn" completed only after roughly **70 degrees** of real head rotation — and the
follow-up beat, which asked for 60, was then anatomically impossible.

**Why:** MediaPipe's landmarks under-rotate as half the face becomes invisible.
The network is regularised toward a frontal prior, so the mesh it reports at a
deep turn corresponds to a shallower rotation than the head actually performed.
This is a property of the detector, not of anything in this tree.

**Why the harness could not catch it.** The synthetic generator produces
landmarks by projecting true geometry, so its measured yaw tracks the truth to
within ten percent — it slightly *over*-reads, if anything. It was structurally
incapable of representing the failure. That is now the **fourth** time the
synthetic model has been kinder than reality in a way that changed a decision
(the others: occluded landmarks are biased rather than merely noisy; heads wander
smoothly rather than teleporting; and the pose composition error that cancelled
against `headEuler`'s).

**What has been done about it:**

- The turn beats no longer name an angle. They ask the wearer to go as far as is
  comfortable and detect when they stop, so no threshold has to be calibrated and
  the scan cannot demand the impossible.
- `synthesizeCapture` gained a `yawUnderRotation` option that reproduces the
  effect (`k = 0.75` turns a true 75 degrees into a measured 21). It defaults to
  **0**, because switching it on would move every accuracy number in this
  repository onto a basis calibrated from one anecdote.
- `COVERAGE_THRESHOLDS` now says out loud that its numbers are measured degrees
  with an unknown relationship to physical ones.

**To settle it:** a session where the physical angle is known independently —
a wearer turning against a protractor or a marked floor, or two cameras at a
known angle — recording measured yaw against it. Half an hour with a real camera.
Then `yawUnderRotation` gets a real calibration, the harness stops flattering the
system, and the coverage thresholds can be re-derived in physical degrees where
the triangulation argument actually lives.

**Worth:** high, and it is cheap. It also determines whether the *bundle's* own
solved poses inherit the compression — they should partly correct, since shape is
free — which nothing currently checks.

---

## Q18 — SETTLED 2026-08-22. The constructed seat is recorded INFERIOR; `bearing.ts` is out of the tree.

**The verdict, replicated across 5 independent seeds** (17 subjects each,
eye-level, frame 'standard', the band the settlement adopted, [2, 36]; seating each subject on
its truth and on its own reconstruction and taking \|difference of mean
`vertexDistanceMm`\|, per-seed median / p90 mm):

| seed | constructed | contact | winner (median) |
| --- | --- | --- | --- |
| 11 | 1.52 / 5.55 | 1.19 / 3.37 | contact |
| 23 | 0.81 / 5.69 | 1.41 / 3.35 | constructed |
| 37 | 1.24 / 5.39 | 1.08 / 3.74 | contact |
| 41 | 1.23 / 5.31 | 1.14 / 3.94 | contact |
| 53 | 0.76 / 2.20 | 1.40 / 3.25 | constructed |

The decision rule required the constructed seat to win the per-seed median in
≥4/5 seeds; it won **2/5** — and the same 2/5 at every wide band ([4, 34],
[6, 32]; 1/5 at the old [8, 30]), so the verdict does not depend on the band.
Pooled n=85: constructed **1.03 / 5.22** med/p90 against contact **1.24 /
3.62** — the constructed bulk is slightly better, its tail 1.4× worse (1.9× at
[4, 34]), and the tail is what drags 3 of 5 seed medians over. So
`fit/bearing.ts` did not stay a testkit instrument, and has no header to tell
the truth: it left the working tree at `f9c9093` — the same commit that wrote
this sentence saying it stays — and it was never a tracked file, so no commit
holds it. The pipeline seats with `contact.ts`. These digits are the settlement
campaign's record, taken on the frozen campaign state; the merged tree
measured differently but reached the same verdict — a re-measurement on the
final tree read **2/5 again** (wins at seeds 23 and 37; pooled constructed
0.94 / 7.53 over n=32, with 8 of 40
constructions *failing* on scanned models, against the contact seat's
1.32 / 3.60 with none). That re-measurement is **not** recorded in
`tests/pipeline.test.ts`, as this paragraph claimed — nothing in `tests/`
mentions the constructed seat. It is recorded here, in prose, and no
re-derivation is possible: the constructed arm's code is in no commit. The
verdict is what was settled; the digits are its evidence, and prose is now the
only form that evidence takes.

**Where the error enters — the decomposition answer this question was waiting
for** (one door at a time, band [4, 34], pooled n=85):

- **The plane fit is innocent**: 0.07 mm isolated, 0.23 mm marginal-in-context.
- **Pass-through is RELATIVE.** `vertexDistanceMm` subtracts the eye-corner
  plane, so a seat passes through the depth error of its reference region
  *relative to the eye corners'*, not that region's absolute repeatability.
  The contact seat's cheek reference shares the global solve's common-mode
  depth error with the eye corners, so most of it cancels (median signed
  −0.51 mm); the sidewall's does not (+0.69).
- **The tail is interaction-dominated** (interaction term median 0.49 but p90
  10.70): Gauss-Newton translation on a reconstructed distance field flips
  contact basins under small joint input changes, which no isolated term
  predicts and nothing at run time flags.

On a TRUE surface the construction still did what it set out to do —
\|contactMm\| per-seed medians 0.44–0.49 against the contact seat's 0.78–1.48
of pad depth error — which is why this paragraph said it was being kept as an
instrument rather than deleted. It was not kept: the same commit that wrote
that sentence dropped `bearing.ts` from the working tree.

**The earlier history, kept short:** the single re-run of 2026-08-22 morning
had already inverted the original claim (2.80 / 7.94 pooled over the ladder
against 1.47 / 3.95), and the seeding fix was not what made it worse — it
destroyed the *evidence*, an 8-sample single-geometry sweep whose one frozen
noise draw sat inside the benign mode of a bimodal distribution. The
replication above is what turned that one draw into a verdict. This paragraph
used to close by saying the regression test that had asserted the constructed
seat's superiority now asserts the recorded inferiority instead. **The rewrite
was never written.** The superiority version did exist — it survives in dropped
stash `1ce584f`, importing `constructSeat` from `fit/bearing.js` — but it was
never committed, nothing in `tests/` mentions the constructed seat today, and
`git log --all -S"constructSeat"` is empty across every ref. The verdict above
is a record; it is not a bar.

The paragraph below is the original assessment, kept because its reasoning about
roll is undisturbed:

Two of three went the new seat's way and the third did not go quietly. Roll came
from the crest direction — the cross product of two planes each fitted to a
handful of points — and no band width rescues it: 0.71 degrees median at best,
still eightfold worse than an energy minimum that averaged over the whole contact
patch and the ear.

Roll is therefore **not reported** by the constructed seat, and the frame is
placed level. That is a capability the old seat had and this one does not.

**To settle it:** a wearer wants to know whether their own face is asymmetric
enough to make a symmetric frame look crooked. That is a property of the face,
measurable directly from the landmarks (the eye line against the nasal axis), and
it does not need a seat at all. Add it as a face measurement and the loss becomes
a relocation rather than a deletion.

**Worth:** medium. The `level` verdict is currently graded to 0.1 degrees off a
number carrying 3 degrees of error, so it is worse than useless today either way.

---

## Q19 — Nothing at run time predicts how well a scan will seat.

**Measured** across the camera ladder, correlation of the finished
lens-distance error against every candidate the pipeline already computes:

    sidewall plane residual   -0.25
    noseConfidence            -0.12
    varianceFactor            +0.07
    quality.nose.sigmaMm      -0.48   (wrong sign)

The strongest correlate points the wrong way. So `VERTEX_SEAT_SIGMA_MM` was a
population constant, and no verdict could honestly say *this* scan is better or
worse than typical. It is not in the tree at all now — it was an export of
`fit/bearing.ts` and went out of the working tree with it at `f9c9093` — so the
per-scan verdict this paragraph rules out is not merely dishonest but absent.

**The population constant was re-set deliberately on 2026-08-22** — the
decision the earlier version of this paragraph said nobody had taken. It was set
to **3.03**, the eye-level 5-seed pooled sigma_rms at the band the settlement
adopted, [2, 36] (pooled n=85: med 1.03 / p90 5.22 / worst 12.28). The 4.83 this
paragraph used to demand was the full-ladder single-draw figure at the OLD
[8, 30] band — the replicated [8, 30] sigma_rms is 4.50, consistent, and the
full-ladder figure at [2, 36] has not been measured. The constant itself went
out with `fit/bearing.ts` and is not in the tree or the ledger; what is written
above is the whole of the record. What no single sigma could carry is written
here rather than on a row: the distribution is heavy-tailed (p90/med 5.1 against
2.44 half-normal) and biased (+1.10 mm mean signed — the reconstruction reads
long).

The two-population observation still sharpens this question. The Q18
decomposition found the tail is **interaction-dominated** — Gauss-Newton
translation on a reconstructed distance field flips contact basins under small
joint input changes (interaction term median 0.49, p90 10.70) — which no
isolated input predicts and **nothing at run time flags**. So the untried
predictor this question wants is still untried; what changed is that the tail
now has a mechanism to look for rather than a mystery.

That is the third time this repository has caught a formal covariance failing to
predict the error it appears to describe — `quality.nose.sigmaMm` at 7.5x, the
plane-residual sigma at 11x, and now the whole class. Worth stating as a rule:
**in this pipeline a covariance describes the conditioning of its own fit and
never the error in its input, and must be calibrated against ground truth before
it is reported to anybody.**

**To settle it:** either find a predictor (a residual against held-out frames is
the obvious untried candidate) or keep reporting the population figure and say so.

**Worth:** high. Without it every abstention gate is population-wide, which means
a good scan was refused advice on the same terms as a bad one. (The advice layer was removed 2026-08-25; what survives is the confidence itself, which now shrinks a criterion toward neutral in `scoreOf` rather than gating a sentence.)

---

## Q20 — The RNG collapse, and what its replication has and has not repaid. **(2026-08-22; the 5-seed replication landed the same day)**

**The defect:** `testkit/synthetic.ts` salted its capture RNG by
`subject.id.length`. Every generated id is three characters. So all fifteen
index-generated subjects shared **one** noise realisation, and the 17×3
subject-by-camera grid contained six distinct streams rather than fifty-one.
Every synthetic figure this repository has ever published — every p90, every
worst case, every bracket under a `measured` constant — was computed on a
collapsed noise dimension. The spread the tables printed was geometry variation
with the measurement noise held frozen.

It is fixed, and every published number was re-derived on 2026-08-22 — first as
one re-run, then, later the same day, **as the replication this question asked
for**: a settlement campaign ran every contested sweep at five independent
seeds, with per-seed adoption rules rather than pooled hand-waving. The state
of the bullets that used to sit here:

- ~~"The re-derivation is one person's afternoon and one machine's run"~~ —
  half resolved. The contested figures are now **5 independent noise
  realisations**, not one; the per-seed spreads are published beside the
  medians, and the checked-in reports are regenerated at a named seed (11)
  with the doc tables quoting median-of-5. Still one machine.
- ~~"Two claims inverted and both regression tests are red; neither decision
  has been taken"~~ — **both decisions are taken**, by measurement, not by
  relaxation. The field: replicated, it *wins* in both configs and was adopted
  at `fieldPriorScale` 8 — the disproof was a single unseeded draw from the
  seed-41 family, the one losing realisation in five (Q21) — and `the field
  earns its place` is the green test. The constructed seat: replicated, it
  *loses* under the 4/5 rule (2/5, band-invariant), and the inferiority is the
  recorded claim (Q18). This bullet used to say "both tests are green" and that
  the seat test guards its record in both directions. There is one test, not
  two: no bar on the constructed seat exists in `tests/`, and none in any commit
  on any ref — only in dropped stash `1ce584f`, and that one asserts the
  opposite. The seat's record is carried by prose and guarded by nothing.
- ~~"Two `measured` constants are known wrong and still shipping"~~ —
  resolved, then overtaken. Both were re-derived on 2026-08-22 —
  `SIDEWALL_BAND_MM` 2–36 and `VERTEX_SEAT_SIGMA_MM` 3.03, the latter with an
  eye-level-only caveat — and neither ships now, because neither is a constant
  of this tree any more. See the retirement bullet below; there is no row to
  carry that caveat on, and there never was one.
- **Constants now resting on replicated (5-seed) measurement:**
  `KEYFRAME_DEFAULTS.count` (24), `BUNDLE_DEFAULTS.rounds` (kept 3 by rule),
  `BUNDLE_DEFAULTS.fieldPriorScale` (8), `TYPICAL_VARIANCE_FACTOR` (1.9), the
  temple-reach leverage behind `VERTEX_REACH_CONFIDENCE`, and the keep-wall
  verdict on `SKIN.hookStiffnessNPerMm`.
- **Still resting on a single re-run or an un-rerun bracket**, marked on their
  rows: `silhouetteWeight`, `shapePrior`, `PAD_CURVATURE_LIMIT_MM`,
  `TRACKER_DEFAULTS.smooth` (shipped pair only — and the jitter half of it
  reversed on 2026-08-31, see Q7 and its ledger row),
  `lensAheadOfPadsMm` (sweep predates the seeding fix entirely),
  `EYE_ROTATION_LIMIT_DEG`'s 0.77%→0.59% figure, and `CAMERA_LADDER`'s
  "0 of 600 admitted frames", which is a v1 measurement this tree cannot
  reproduce at all.
- **One name left that list on 2026-09-01, because it is not a constant.**
  `WEDGE_SLOPE_MM_PER_MM` was listed above as marked on its row. It has had no
  row and no definition since `f9c9093` rewrote `fit/advice.ts` into
  `fit/score.ts` and dropped both, so there was nothing to mark. Its last ledger
  value was 0.74; the 0.92 README quoted was never carried by any commit, report
  or row here. The only object in this repository that derives 0.92 is a dropped
  stash (`1ce584f`, 2026-08-22) — unreachable from every ref and deletable by
  the next `git gc`, so treat this pointer as evidence, not as provenance — and
  its own docstring calls 0.92 the median-curve fit over eight faces and the low
  end of a 0.90-to-1.18 range, not the pooled 29-face regression README claimed.
  The quantity itself is not unmeasured: `report:seat` prints 1.146 at seed 11
  on this tree, `tests/pipeline.test.ts` bounds it to (0.3, 2.0), and
  `fit/contact.ts`'s header now divides by the printed number. What has no home
  is the NAME, and it is not getting one back — a row the ledger's own gate
  cannot check the value of is the dead provenance this file keeps complaining
  about.
- **Two more names left that list on 2026-09-01, for the same reason.**
  `SIDEWALL_BAND_MM` (2–36) and `VERTEX_SEAT_SIGMA_MM` (3.03) were listed above
  as resting on replicated measurement, and `docs/CONSTANTS.md` called them
  "the two rows … no longer marked unsettled". Neither is a constant of this
  tree, and neither has ever had a row here. Both were exports of
  `fit/bearing.ts`, which left the working tree at `f9c9093` and was never a
  tracked file, so both went out with it. `grep -rn SIDEWALL_BAND_MM src/`
  returns nothing at all, and `VERTEX_SEAT_SIGMA_MM` survives in `src/` only
  as a past-tense line in `testkit/synthetic.ts`. The ledger section that once
  carried their cells exists only in the same dropped stash as
  `WEDGE_SLOPE_MM_PER_MM`'s derivation (`1ce584f`), where they read 8-to-30
  and 1.6 — the pre-settlement values — so no version of this ledger has ever
  held both the rows and the sentence announcing them. Retirement rather than
  restored rows, for the reason given just above. Where the quantities went:
  nowhere. The sidewall band has no successor — `contact.ts`'s `nominalPose`
  takes the two `NOSE_WALL_HIGH` landmarks directly, with no band and no
  millimetre extent — and the seat sigma has none either, since `fit/score.ts`
  grades the vertex criterion by threshold and `VERTEX_REACH_CONFIDENCE`
  rather than against a population spread. The capability the pair fed —
  telling a wearer whether *their* scan seats better or worse than typical —
  is not in this tree.
- **One caveat the campaign added rather than removed:** its digits were
  measured on a frozen tree state, and fixes to enrollment and the contact
  seat landed in the same pass, so the merged tree measures differently
  (verified). Settlement records and current-tree figures are now two
  different things, and each doc says which it is quoting.

**Why it is a question and not a chore:** the failure mode generalises. A shared
random stream makes a harness report a population and measure a sample, and
*nothing in a green test run distinguishes the two*. This tree's falsifiability
rule — every synthetic nose carries detail the basis provably cannot represent —
is a guard against a test that cannot fail. There is no equivalent guard against
a test that cannot *vary*. `assertDistinctNoiseStreams` now exists and is the
start of one.

**To settle what remains:** re-run the three reports on a second machine and
diff. The other two settle-its from the first version — decide the red tests,
set `VERTEX_SEAT_SIGMA_MM` deliberately — are done, and both have since been
overtaken: only one of the two "red tests" was ever a test, and the constant
left the tree with `fit/bearing.ts`.

**Worth:** high. It is the credibility of every other number in this repository.

---

## Q21 — SETTLED 2026-08-22. The field earns its place; the deficit was noise-chasing, and the prior cures it.

**The history, in one paragraph.** The field was retained on a collapsed-RNG
measurement (0.35 against 0.99 mm), lost that footing when the single seeded
re-run inverted it (the 14-subject true-iris row read 24% *worse*), and stood
for a day as a design decision with no result behind it. The settlement
campaign then replicated the comparison at 5 independent seeds and swept the
prior — the one sweep the earlier version of this question asked for.

**The adoption.** With `fieldPriorScale` at ×1, ×2, ×4 and ×8 the adoption
rule (field-on ≤ field-off on median nose RMS in ≥4/5 seeds in BOTH the
shipped and clean configs, pad strip better outright) passed at **every scale
swept**; ×8 is the measured-best qualifying cell and is adopted. Median-of-
seeds nose RMS: shipped off 1.439 → on 1.269 mm; clean 0.884 → 0.668; pad
strip shipped 1.353 → 1.030, clean 0.762 → 0.471. Laptop-lid at ×8: 3/3 both
configs. The disproof-draw is accounted for rather than discarded: it was from
the seed-41 family, the one realisation in five that still loses under
pooled-iris + detector bias. The `tests/pipeline.test.ts` bar is green on the
adopted configuration — moved by measurement, not relaxed.

**The two candidate explanations, separated.** They turned out to be one
mechanism seen from two sides. The noise separator — actual injected noise
scaled down with the claimed sigma held at the shipped 0.7 px pattern — gives
the paired median nose deficit: shipped **+0.066 mm at 0.7 px → +0.016 at 0**;
clean **−0.097 → −0.125** (already a growing win); ultraclean control at zero
noise −0.125. No residual deficit survives at zero noise, so there is **no
evidence of field mis-modelling** (registration or normal error). The answer
is hypothesis (a): the field chases landmark noise — plus detector bias in the
shipped config — not a mis-modelled surface, and a stronger prior
monotonically rescues it, which is why explanation (2)'s knob was the cure for
explanation (1)'s disease.

**What is still open, one flank:** the mixed "true-iris WITH detector bias"
variant was measured only on the historical no-seed draw (+0.188, the field's
worst configuration) and needs its own seeded sweep before any doc quotes
per-variant numbers for it. And ×8 is the edge of the swept range — the
optimum may lie beyond, and the two prior weights were never swept separately
(`BUNDLE_DEFAULTS.fieldPriorScale`'s row carries both caveats). The digits
above are the settlement campaign's record on its frozen tree state; the
merged tree's current field-on/field-off gap is in `reports/enroll.txt`.

**Worth, retrospectively:** the field is the reason this rewrite exists — it
is what "actually measures this person" means — and it now has a replicated
result behind it instead of a structural argument alone.

---

## Q24 — SETTLED 2026-08-23. The +2.28 mm was the solve's gauge, not the corners; nothing was repaired because nothing was confirmed.

**The reproduction failed, and the failure is the settlement.** This
question's own procedure asked for the bias to be reproduced before any
repair: 17 subjects × campaign seeds {11, 23, 37} (three of the settlement
campaign's five), eye-level, the shipped configuration — pooled iris,
framesPerBeat 12. The `VERTEX_SEAT_SIGMA_MM` row this sentence pointed at for
the rest of the protocol does not exist and never did, so the protocol is what
is written here and nothing more —
signed depth error at the four eye-corner vertices (LM 33 / 263 / 133 / 362)
after the tree's standard whole-mesh rigid alignment, the same `rigidAlign`
every committed grader uses (`compareToTruth`, the detector-bias
calibration). Pooled n=51:

    median signed    +0.003 mm     (per-seed +0.128 / +0.003 / −0.045)
    mean signed      −0.006 mm
    median |signed|  0.206 mm

Not +2.3. The signed and absolute medians differ by two orders of magnitude,
so under this alignment there is no bias *signature* at all, let alone the
recorded one.

**Where a number with the recorded signature does live.** Six operational
definitions of "corner depth error", same population, at the gaze default
and at the prescribed sweep point (median signed, mm):

| definition | gaze 1.5 | gaze 0 |
| --- | --- | --- |
| A — whole-mesh rigid align | +0.003 | +0.049 |
| B — NO registration, raw canonical frames | **−5.10** (med \|.\| 5.10) | **−4.84** (med \|.\| 4.84) |
| C — aligned on the nose region | −0.24 | −0.21 |
| D — aligned on the pad strip | −0.10 | −0.10 |
| E — corners minus pad strip, whole-aligned | −0.12 | −0.09 |
| F — corners minus cheeks, whole-aligned | +0.90 | +0.92 |

Only B — comparing the reconstruction's canonical frame against the truth's
with no registration at all — reproduces the signature Q24 was filed on
(median signed equals median absolute). And B is the bundle's **gauge**: the
whole-mesh rigid transform the solve is free to choose, uncontrolled enough
to swing between −2.5 and −8.2 mm across seeds here — and structurally
invisible to `vertexDistanceMm`, which subtracts the lens plane from the
corner plane inside ONE frame, so a common rigid error cancels exactly. The
Q18 decomposition itself named the component that cancels "the global
solve's common-mode depth error"; the +2.28 was that common mode read
through a door where it does not cancel. It was the instrument, not the
corners.

**The prescribed gaze sweep, run anyway: nothing follows gaze.** A moves
+0.003 → +0.049, B −5.10 → −4.84, F +0.90 → +0.92 — all inside their own
seed spread. The harness's `applyGaze` moves the eye ring laterally (x/y in
face space, no z), and it is now a measurement rather than an assumption
that the bundle keeps no depth bias from it. So the suspect is acquitted,
and per this question's own rule — do not invent a fix for an unconfirmed
mechanism — **no eye-ring sigma inflation was applied**. The bundle, the
tracker and the eye ring's weights are untouched.

**What is real in the neighbourhood, correctly attributed:**

- Corners relative to the pad strip — the surface the frame actually seats
  on: **−0.10 mm** median. The reference plane is clean where a seat can
  feel it.
- Corners relative to the cheeks: **+0.90 mm** median, gaze-independent.
  This is Q18's "cheek reference shares the common mode (−0.51 relative)"
  re-measured larger on the merged tree, and it is the contact seat's real
  pass-through channel — a cheek-depth question (Q15's `BEHIND_CHEEK_MM`
  chain), not an eye-corner one.
- With the ruler's scale divided out before aligning, the corners read
  +0.19 to +0.23 mm forward at both gaze settings — the honest residual, an
  order of magnitude below the number this question was filed about.

**Guarded:** `tests/pipeline.test.ts` ("the eye-corner reference plane
(Q24)") pins the settled fact on the fixture draw — measured median
−0.124 mm, worst \|.\| 0.459 over its 8 subjects — with bars at 0.75 / 1.5 mm
that a +2.3 mm corner bias fails loudly (breakage-verified by injecting one:
median bar fires at 2.144, worst bar at 2.568).

**What remains, and whose it is:** whether the REAL detector's
gaze-following puts a genuine depth bias into real corners is exactly the
ground-truth calibration Q2/Q8 already own — the synthetic harness cannot
answer it, and this settlement shows its gaze model does not manufacture
one. This settlement deferred one correction to `fit/bearing.ts`'s header,
which it said still quoted the +2.28 as a finding awaiting investigation.
There is no such header. `bearing.ts` left the working tree at `f9c9093` — the
commit this settlement was written against — and it was never a tracked file,
so no commit holds it. The deferral is discharged: there is nobody to hand it
to and nothing to edit.

---

## Q22 — Nobody has measured how far a nose-pad arm can actually be bent. **(new 2026-08-22; every premise under it retracted 2026-09-01)**

**Assumed:** nothing. Every premise this entry used to rest on is retracted
below; the title is all that survives.

**The constant it named never existed.** The entry opened *"`PAD_SEPARATION_SWEPT_MM`
= 12 to 24 mm, which is the range the wedge slope was measured over ... and the
ledger says so."* No commit in this repository has ever held a constant by that
name — it occurs exactly once across every ref, in the line it was written on, by
`f9c9093`. The range itself is real and needs no constant: `testkit/report-seat.ts`
sweeps pad separation at 12, 14 … 24 mm and names none, and
`tests/pipeline.test.ts` bounds the same span in steps of three. What the ledger
"said so" in was the `WEDGE_SLOPE_MM_PER_MM` row, which `f9c9093` deleted along
with the constant; Q20 above carries that retraction.

**Why it mattered, and why it does not today.** The bound was described as the
only limit on what `adjustmentsFor` may prescribe — *"Narrow the pads by about N
mm"*, computed as `descentMm / WEDGE_SLOPE_MM_PER_MM`. That function lived in
`fit/advice.ts`, which the same `f9c9093` rewrote into `fit/score.ts`, and the
rewrite dropped the advice layer entirely: `FitAssessment` is now `frameId`,
`seat`, graded `measures` and a `score`, with no `adjustments` field and no
sentence addressed to an optician anywhere in `src/`. The last trace was a dead
`el('adjustments')` binding in `ui.ts` for an element `index.html` never had,
removed with this entry.

**And there was never a clamp.** Read the deleted function
(`faece72:ar_v2/src/fit/advice.ts`): `narrowBy` is a bare division printed at
`toFixed(0)`, with no bound of any kind. The entry's load-bearing sentence — that
an instruction "is clamped to the interval where the number behind it was
measured" — was untrue of the code on the day it was written, and that code had
already been deleted by the commit that wrote it.

The geometric sentence fails the same way. Pads meeting at the midline at
`padWidthMm * sin(padAngleRad)` is ~5 mm for `parametricFrame`'s default 8 mm
pad — the five `TEST_FRAMES` shapes, which are stand-ins nobody can buy. All ten
catalogue assets carry `padWidthMm: null`, deliberately (*"real geometry has a
contact patch, not a rectangle"*), and `frame-from-mesh.ts` says in the same
comment that nothing in `src/` reads the field. No hard limit is computed
anywhere, for any asset a wearer can be shown.

**What survives is the title.** Nothing in `FrameAsset` records pad-arm travel and
nobody has measured it. That costs nothing while the tree prescribes nothing —
but the solver already moves pads freely in `padSeparationMm` (the wedge sweep is
exactly that), so the first verdict that turns a solved descent back into "narrow
the pads by N mm" needs this number and will otherwise reinvent the same
unbounded division.

**To settle it:** one optician, one afternoon, a handful of frames and a caliper.
Still the cheapest open question in the file.

**Worth:** low today — nothing reads it. Moderate again the moment advice returns.

---

## Q23 — On some seats the ears carry literally nothing, and no verdict says so. **(new 2026-08-22)**

**Re-measured 2026-08-26**, after `describeSeat` stopped projecting each contact
force onto the interpolated vertex normal and started projecting it onto `-u`,
the direction the solve balances. Same population — 5 catalogue frames × 29
synthetic faces, 145 pairs, ground truth geometry:

| | `padLoadFraction` median | p90 | exactly 1.000 |
| --- | --- | --- | --- |
| against `cp.n` (to 2026-08-25) | 0.865 | 0.970 | **9 of 145** — 6.2% |
| against `-u` (now) | 0.872 | **1.000** | **25 of 145** — 17.2% |

**The finding got bigger, not smaller.** Nearly three times as many pairs have
the ears carrying nothing at all, and the p90 is now 1.000 exactly — so more
than a tenth of seats put the whole frame on the nose. On the smaller 13-face
population it is 8 of 65 either way.

A `padLoadFraction` of 1.000 is not a rounding artefact and not the old clamping
bug — that was fixed, and the value is now `padLift / (padLift + earLift)`. It
means `earLift` solved to zero: **the temples are carrying none of the frame and
the entire weight is on the nose.** That is the configuration a wearer describes
as the glasses digging in, and it is the one an optician fixes by adjusting the
temples rather than the pads.

**Why it is open rather than a bug:**

- It may be physical. A frame whose temple reach is too long for the head really
  does hang off the nose, and Q16 says the temple reach is the highest-leverage
  unmeasured number in the tree. So some of these 9 may be honest.
- It may be the ear model. The template has no ears (Q5); the ear rest is
  extrapolated from `BEHIND_CHEEK_MM`. An ear contact that is placed slightly
  wrong is an ear contact that misses.
- Nothing distinguishes the two, because there is no verdict on `padLoadFraction`
  reaching 1. `fit/score.ts` grades a load *below* 0.35 — a frame perched on its
  temples — and says nothing at the other end.

**To settle it:** find out whether the 9 share a frame, a face, or a geometry.
If they share a frame it is the catalogue; if they share a face it is Q16; if
they are scattered it is the ear model.

**Worth:** moderate-to-high. The asymmetry is the tell — an unbalanced verdict
that grades one end of a range and not the other usually means the other end was
never seen, and it has now been seen 9 times.

---

## Q16 — The frame's fore-aft position is set by the wearer's CHEEK, through two constants nobody measured. **(highest-leverage number in the tree)**

**Confirmed on a real face, 2026-08-21.** A wearer's two scans half an hour apart
moved `padDepthErrorMm` by 2.68 mm and vertex distance by 5 mm — 6 mm from the
eye down to 1 mm — while every reported measurement moved about 1%. An
eight-agent investigation cleared the code (the seat solve is bit-identical
through the change, 0.000000000000000 to thirteen digits) and reconstructing the
wearer from their reported measurements accounted for only 22% of the move.

The missing input was **cheek depth**, which nothing measured and nothing
reported. The mechanism is an identity:

- `earRestPoints` puts the anatomical rest at `cheek.z − BEHIND_CHEEK_MM` (17,
  `stated`).
- Every parametric frame's temple reach is an inline `−95` (`assumed`, no field
  on `FrameSpec`).
- So the front comes to rest at **`cheek.z + 78`**, and the solve tracks that
  until the hook saturates.

Measured by shifting only the cheek vertices in z on one synthetic face:

| cheek Δz | `pose.t[2]` | `cheek.z + 78` | `padDepthErrorMm` | vertex |
| --- | --- | --- | --- | --- |
| −6 mm | 49.45 | 48.78 | −1.25 | 10.55 mm |
| 0 | 53.42 | 54.78 | +0.13 | 14.47 mm |
| +3 mm | 54.49 | 57.78 | +1.38 | 15.57 mm |

A 9 mm cheek shift reproduces the wearer's entire swing. So the seat is not
unstable — it is being driven by a dominant input that was invisible, through two
guesses, and the nose the whole pipeline exists to measure barely enters into it.

`cheekDepth` is now reported in `FaceMeasurements` so this is visible at all;
across 40 synthetic faces it runs 54.2 to 69.6 mm.

**Update 2026-08-22 — half of the settle-it is done, and the leverage is now
replicated.** The temple reach has a spec field: `FrameSpec.templeReachMm`,
default 95, guard-checked in `parametricFrame` — the inline `−95` is gone. And
the leverage is no longer a single-draw figure: on the fixed RNG (5 seeds
{11, 23, 37, 41, 53} × count-8 population × 5 frames, wall arm, cross-seed
medians), reach 90 / 95 / 100 mm puts the corneal vertex at **8.7 / 13.0 /
16.7 mm** and median descent at −0.05 / 3.84 / 9.33 mm, while hook force falls
1.79 → 1.01 → 0.72 frame-weights. Also refuted along the way: the hope that a
compliant hook (Q15) would deflate this leverage. It trims the *force* swing
(1.07 → 0.68 weight-units over the 10 mm of reach) but not the positional one
(vertex swing 8.00 → 7.26 mm, and the descent and depth-error swings **grow**).

**Still open:** measuring the reach off real assets (Q10); the mapping from
stocked overall arm length (135/140/145/150 mm) to hinge-to-bend reach, which
is unmeasured and is why the field *defaults* rather than derives; and
`BEHIND_CHEEK_MM` against a template that has ears (Q5). Until those, the
single number that decides where a wearer's lenses sit is a defaulted spec
field, one stated offset, and a landmark on the side of their head.

**Worth:** the highest on this list, and now demonstrated rather than argued.

---

## Q16b — The original sweep, kept because it was the first evidence

**This section is history, kept because it was the first evidence.** Both of
its premises are resolved: `FrameSpec.templeReachMm` exists (default 95), and
the sweep below was replicated 2026-08-22 on the fixed RNG — its vertex column
was canthal-minus-12, i.e. already corneal-equivalent, and it replicated in
direction and magnitude: 9.6 → 16.9 mm against the replicated corneal
8.7 → 16.7. The current numbers live in Q16 above.

**Assumed at the time:** every frame `parametricFrame` built had
`earRests: [..., -95], [..., -95]` — a 95 mm temple reach, hardcoded
(`fit/frame-asset.ts`), with no spec field to override it.

**Why it mattered:** measured across 14 subjects, moving it ±5 mm:

| reach | median descent | median vertex | median panto | faces under 8 mm |
| --- | --- | --- | --- | --- |
| 90 mm | −0.36 mm | 9.6 mm | 0.1° | 6 / 14 |
| 95 mm | 3.72 mm | 12.7 mm | 3.7° | 0 / 14 |
| 100 mm | 8.62 mm | 16.9 mm | 6.7° | 0 / 14 |

Vertex distance spans the **entire** good band (10–18 mm) over that range, and
pantoscopic tilt crosses its good boundary. For comparison, Q15's hook stiffness
— which this file previously called the highest-leverage unjustified number —
moves vertex distance 12.7 → 14.3 mm over a factor of **one hundred**. The
temple reach out-leverages it by roughly six times over a perturbation forty
times smaller.

Worse, ±5 mm is the *conservative* case: real temples are stocked at 135, 140,
145 and 150 mm, so the across-catalogue spread is larger than the sweep.

**What settled since:** the spec field exists, the ledger row exists (class
still `assumed`, because a default is not a measurement), and the sweep is
replicated — see Q16. What remains is Q10's caliper: measuring the reach on
the real assets, which is the only thing that can ever move the class.

---

## Q15 — SETTLED 2026-08-22. The hook stays a wall; compliance was tested and refused.

**What was assumed:** `SKIN.hookStiffnessNPerMm` = 0.8, `stated`, holding a
one-sided constraint that the temple cannot travel forward past the ear
(`fit/contact.ts`) — and this file's recommendation that it *should* be
compliant, because a temple arm is a slender beam and it bends.

**The test.** The compliant arm was derived rather than tuned: k = 3EI/L³ =
0.11 N/mm off the parametric frame's own geometry (`SKIN.hookCantileverNPerMm`
in the ledger, with the {k/2, k, 2k} bracket covering the published-E spread).
Ground truth, count-8 population × 5 catalogue frames × seeds
{11, 23, 37, 41, 53}; each cell the median over the 5 per-seed medians:

**The pad-load column below predates 2026-08-26**, when `describeSeat` stopped projecting each contact force onto the interpolated vertex normal `cp.n` and started projecting it onto `-u`, the direction the solve actually balances. The two sit 9.2 degrees apart at the median. The figures are kept as the record of what was measured then; do not compare them against a pad load taken today.

| arm | k (N/mm) | \|depth err\| mm | pad load | hook/weight | corneal vertex mm | in 12–16 band | descent mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| wall | 0.800 | **0.86** | 0.86 | 1.01 | 13.0 | 0.54 | 3.84 |
| k/2 | 0.055 | 1.10 | 0.84 | 0.86 | 14.4 | 0.64 | 6.72 |
| k | 0.110 | 0.97 | 0.84 | 0.92 | 13.7 | 0.58 | 5.28 |
| 2k | 0.220 | 0.95 | 0.85 | 0.98 | 13.4 | 0.54 | 4.69 |

**The verdict: KEEP THE WALL.** Adoption required all three conditions (pad
depth within 0.05 mm, hook force not worse, in-band fraction not worse) in
≥4/5 seeds; measured **1/5** (seed 53 alone). Pad depth is what failed:
compliance at the derived k worsens median \|pad depth error\| by
+0.175 / +0.088 / +0.332 / +0.196 / −0.016 mm across the five seeds against
the 0.05 mm allowance — 4/5 fail. The hook-force condition passed **vacuously**,
because Q15's own premise was wrong: "it applies 2 to 7 times the frame's
weight" is the TAIL, not the population. Over 250 subject-frame pairs the
wall's hook force is median **1.01×** the frame's weight, p90 2.40×, max
11.1×, 30/250 pairs above 2×. The in-band condition passed 5/5.

**What compliance does buy, recorded so the other branch stays findable:** the
force tail deflates (max 11.1× → 5.8× weight, pairs above 3× from 19 to 7)
and the in-band fraction is ≥ the wall's in 5/5 seeds. A decision rule that
weighed the force *tail* rather than the median could reach the other branch;
this one weighed pad depth, which is what a wearer feels, and pad depth said
wall. Note also what compliance does **not** buy: it trims the force swing of
the temple-reach leverage (1.07 → 0.68 weight-units over ±5 mm of reach) but
not the positional swing (vertex 8.00 → 7.26 mm; descent and depth-error
swings grow) — see Q16.

The clarifying clause from the earlier pass stands: the hook presses the frame
*back*, not *down*. Its Gauss-Newton row is pure +z, so it contributes nothing
to the vertical balance and cannot be the reason pads carry more than the
frame weighs. At 0 the frame falls off entirely (descent 57.7 mm), so the term
is load-bearing and cannot be deleted.

**Still open inside a settled verdict:** the contact point itself. The
derivation stated the temple cross-section (4.0 × 2.5 mm — the parametric
frame carries none to measure) and Q5's missing ears still decide where the
hook actually lands. A re-test belongs after Q5, not before. (The digits above
are the settlement campaign's record on its frozen tree state; the verdict is
what was settled.)

---

## SETTLED 2026-08-21 — the sigma floor was in the wrong pixels

**Every real scan trusted its landmarks four times too much.** `floorPx` = 0.7 is
calibrated at the detection resolution, 640 px on the long side. The app scales
the detector's output up to source pixels before calling `estimateSigma`, so at a
1280-wide capture the landmarks arrived twice as large as the floor describing
them. Sigma came out half what it should be, and a covariance goes as sigma
squared.

The fingerprint was a number that did not exist until this session: the
a-posteriori variance factor. A real wearer's scans measured ≈3.5 where the
synthetic harness gives 1.44–1.75 — and the harness never runs `estimateSigma`
at all, it feeds `sigmaPx` straight from its own noise model. √3.5 ≈ 1.87 ≈ 2,
which is the scale factor exactly. **Harness-kinder-than-reality instance #12**,
and the review had independently flagged the units without connecting them to it.

`estimateSigma` now takes a `pixelScale`, and the function has tests for the
first time — the review noted it had none at all.

The knock-on: the wearer's confidence was below the advice gate purely because of
this, so a scan that repeats to 0.478 mm was told *"not enough of your nose was
measured to advise on adjustments"*. `TYPICAL_VARIANCE_FACTOR` stayed 1.6 in
this fix; it was never the problem. (It was later raised to 1.9 by the
2026-08-22 five-seed replication — a population correction, not part of this
defect; see the ledger row, which also records that the honest synthetic band
now contains the ≈3.5 fingerprint this entry leaned on.)

**Also refuted along the way.** `obliquityRms` — how far off-axis the camera sat,
which is what the old `viewAngleAt` actually measured — was built as a candidate
confidence term on the theory that camera geometry predicts reconstruction
quality. It correlates **+0.08** with true nose error against the variance
factor's +0.61, and every formulation using it scored worse than the shipped one
(−0.08 and −0.11 against −0.55). It was kept as a reported diagnostic on the
grounds that it genuinely describes camera placement.

**Correction, 2026-08-22: it is deleted, not kept.** `quality.*.obliquityRms` no
longer exists on `RegionQuality` and its accumulator is gone from
`perVertexUncertainty`. It never reached a consumer, so a diagnostic nobody read
was carrying the cost of being computed per (vertex, frame) and the risk of being
mistaken for a calibrated quantity. Any dashboard column or pasted dump citing it
is **gone**, not stale. The +0.08 against +0.61 measurement is unaffected and is
now quoted at `RegionQuality.parallaxRms` in `core/facemodel.ts`.

---

## SETTLED 2026-08-21 — a ruler measured on the wearer

The card protocol is still not wired and the reason is worth stating plainly: the
scale *math* has always been there, but the file says explicitly that the
detector is not, and finding four corners in a video frame is a real subsystem
with real failure modes and no real photographs here to test it against. Building
it untested would be this project's own recurring mistake in a new place. Q3
stays open. (2026-08-22: the detector and the opt-in scan beat now exist in
`enroll/card.ts` / `protocol.ts` and the ladder consumes them, measured on the
synthetic harness — see Q3 for the table. Still not wired to the app: the
detector remains unvalidated on real frames, which is the same Q8 reason as
before.)

**Correction, 2026-08-31: Q3 does not stay open.** It closed on 2026-08-25 by
removal — `enroll/card.ts` and its scan beat are out of the tree, the ladder is
`pd > iris > assumed`, and the owner rejected the method outright. Read
`docs/SCALE.md` for what is true now. This correction reaches the card clause
above and the 2026-08-22 parenthetical, and nothing else in the section: the
rest of it — including *"Do not quote a PD error figure from this path"* and
the account of where the PD correction is applied — still describes the
shipping code and still holds.

What shipped instead is cheaper and, for a spectacle wearer, already measured:
**their own PD**, off a prescription. One text field, no computer vision.

Measured across the population:

Measured 2026-08-22 over 14 subjects × 3 camera geometries, after the RNG-seeding
fix. **The advantage is real and it is three and a half times, not ten:**

| ruler | median scale error | worst |
| --- | --- | --- |
| pooled iris (shipping) | 4.23% | 12.33% |
| wearer's PD | **1.22%** | 9.38% |

The figures this section used to carry — 0.44% median and 1.12% worst against the
iris's 4.39% and 7.34% — came from the collapsed noise stream, and the worst-case
column is where the collapse flattered it most. A scan that goes badly goes badly
whichever ruler it is holding: 9.4% against 12.3% is not a rescue. The median is
still worth having, and the card protocol (Q3) is still the better answer.
(2026-08-31: it is not. Q3 closed by removal on 2026-08-25 and the owner
rejected the method. The shipping default ruler is still the iris; the wearer's
own PD is the rung above it, entered only when they type the number in, and
`docs/SCALE.md` sets the 1.5% target any ruler has to meet.)

**Do not quote a PD error figure from this path.** With a wearer's own PD supplied
the reported `pdMm` error is identically **0.000 mm**, algebraically, because the
correction sets the solved span equal to the number that was typed in. That column
stops being a measurement the moment the field is filled.

**The first attempt made it worse, and the repo had already written down why.**
The obvious implementation sits in `solveScale` beside the iris and uses the
`pdPx` that `readIris` already returns — an image-space pupil separation, which
foreshortens with yaw. That is the exact property the iris was chosen to avoid,
and `scale.ts`'s own header says so. Measured, it gave 6.35% median error against
the iris's 4.39% while reporting a confident 0.93% sigma: wrong *and* confident.
That path no longer exists at all — `solveScale` returns no PD.

It is applied in `enroll` instead, against the reconstructed 3-D surface, where
the bundle has already divided head angle out. **Two consequences worth recording,
because they changed what other numbers mean:**

- The **PD readout** (`model.pdMm`), not merely the scale, is now taken from
  `interpupillarySpan` after `applyScale`, and `PD_PLAUSIBLE_MM` is applied there
  rather than in `scale.ts`. `model.pdSigmaMm` is `pdMm * scale.estimate.sigma` on
  every path — about 3.0 mm on the pooled iris where the old code printed 2.7,
  and `PD_RULER.opticianSigmaMm` (0.50 mm) on the wearer's-own-PD path.
- Because PD is now read off the scaled surface, **PD error is absolute scale
  error in different units**: 4.23% median scale error against 2.63 mm on a
  ~63 mm PD. That is why the lean beat's published effect on PD evaporated (see
  `docs/CONSTANTS.md`, `COVERAGE_THRESHOLDS.distanceSpanPct`) — PD stopped being
  a probe of the focal-length solve.

The remaining question is how good the eye-corner midpoint is as a stand-in for a
pupil, which is Q17.

---

## Q17 — The eye-corner midpoint is a proxy for a pupil, and its bias is unmeasured.

**Assumed:** that the midpoint of the inner and outer eye corners sits where the
pupil does, so a wearer's known PD can be compared against it
(`interpupillarySpan` in `enroll/enroll.ts`).

**Why it matters, and the blast radius grew on 2026-08-22.** It is the best ruler
in the tree — it takes population scale error from 4.23% median to 1.22% — and a
systematic bias in the proxy goes straight into every millimetre downstream,
silently, because it would be *consistent* and therefore invisible to the
repeatability harness too.

What changed is that this proxy is no longer confined to wearers who supply a PD.
`model.pdMm` is now read from `interpupillarySpan` on **every scan**, so the
eye-corner midpoint is the source of the PD number a wearer copies into `set-pd`
— and, on the iris path, of a figure they might take to an optician. An
unmeasured proxy that used to sit behind an optional text field now sits in the
default readout.

The medial canthus sits closer to the nose than the visual axis does, so this
span probably runs slightly wide of a true interpupillary distance. In the
synthetic harness the iris centres are *placed* on exactly these midpoints, so
the proxy is exact by construction and the harness cannot see the bias at all.
**Harness-kinder-than-reality by design, and knowingly this time.**

**To settle it:** one wearer with a prescription PD, one caliper measurement of
their outer-eye span, and the residual is the bias. It needs a real face, which
is Q8.

**Worth:** high, and cheap. It is the accuracy of the one ruler that works.

---

## SETTLED 2026-08-21 — the four quality defects

**`quality.sigmaMm` was never an accuracy, and rescaling it would not have made
it one.** It is the bundle's formal covariance — a conditional precision, the
spread of the solution *given* the shape basis and the solved poses — so it
cannot see the error the basis itself introduces. Measured against ground truth
across the camera ladder, its correlation with true nose error is **−0.09**: not
weak, absent, and slightly inverted. The worst geometry in the ladder (a phone in
the lap) gives the largest true error, 1.79 mm, together with the *smallest*
formal sigma, 0.096 mm, because an extreme viewing angle reads to a covariance as
an abundance of information.

The bundle now reports an a-posteriori variance factor — chi-square over
redundancy, the standard photogrammetric check of whether the detector's claimed
sigma survived contact with the residuals — and `enroll` rescales the formal
covariance by its square root. Measured: 1.44 to 1.75, so the detector is
optimistic by 20–30% and the millimetres were too tight by that much. But this
corrects a *precision*; **this build has no runtime accuracy estimator** and the
docstring now says so rather than implying otherwise.

`noseConfidence`'s third term is the variance factor instead of sigmaMm. Result:
correlation with true nose error moved from ≈0 to **−0.59**; reconstructions
worse than 1.5 mm now sit at a median confidence of 0.32 while those under 1.1 mm
sit at 0.96.

**`parallaxRms` measured the camera, not the head.** It averaged
`viewAngleAt` — the angle between each view ray and the model's own +Z axis,
which is the obliquity of *one* view rather than a relationship between several.
A motionless head in front of a camera below eye level therefore reported 15.9
degrees against a 12 degree threshold, the term was pinned at 1.0 on every laptop
and phone, and the one repair the model could offer a wearer — *"not enough head
turn during the scan"* — was unreachable. It is now the angular dispersion of the
view directions about their own mean, carried by the length of their resultant.
Measured: 28.7 / 28.6 / 28.2 degrees for the same protocol at all three camera
heights (it no longer sees the camera), 16 degrees for a wearer who barely moved,
and **zero** for a still capture at any camera pitch. `viewAngleAt` was deleted
rather than left in place, because the name was the trap.

**The shipping iris path had no test.** Every enrollment test passes
`irisMm: subject.irisDiameterMm`; `main.ts` never sets `irisMm` at all, so every
real scan uses the pooled 11.7 mm default. Measured, eye-level, n=12:

| | nose median | nose worst | scale error median | scale error worst |
| --- | --- | --- | --- | --- |
| true iris (what the tests used) | 1.04 mm | 1.52 mm | 0.39% | 1.31% |
| **shipping** | 1.47 mm | 3.38 mm | 2.71% | **10.08%** |

Both paths are now tested, with separate bars, plus an assertion that the
uncertainty the model reports actually covers the error the assumption makes —
`IRIS.sigmaMm` is honest, so it does. The real repair is the card protocol (Q3),
which as of 2026-08-22 includes the detector and the scan beat, measured
synthetically (Q3), and is still deliberately unwired (Q8). (2026-08-31: the
card left the working tree on 2026-08-25 and the method was rejected — it was
never a tracked file, so no commit holds it. The real repair is the wearer's own
prescription PD — `docs/SCALE.md`.)

**`browRidge` was an identically-zero field.** Its fade-out ramp was scaled by
the chin-to-forehead span rather than eye-to-forehead, putting the ramp's lower
bound at 88.5 mm when the highest vertex in the mesh is at 82.6 — so every vertex
clamped, the factor came out `1 − 1`, the mode was pruned, and the basis shipped
**19 modes under the name `anthropometric-20`** while the largest coefficient of
variation in the anthropometry table carried no load. The band is now scaled to
the eye-to-forehead span and peaks about 10 mm above the eye line, where a
supraorbital torus actually sits. `dim` is 20 for the first time.

The systemic half matters more than the mode: a generator that produces no
displacement anywhere now **throws** instead of falling through to a zero mode
that `pruneBasis` quietly drops. A named trait that moves nothing is a bug, not a
configuration.

---

## SETTLED 2026-08-21 — the scan, after the full-tree review

**The plateau detector was a velocity gate.** `advanceProtocol` re-latched
`state.best` to the current value on every non-improving frame, so the test was
always against *last frame*, never the value at the last reset. Turning at
anything under `epsilonDeg` per frame — 1.2 deg at 30 fps is 36 deg/s, a brisk
turn — never registered as improvement, so the beat closed on patience while the
head was still moving. Measured on the old code: a wearer turning 45 degrees had
the beat close at **16.2**, and one who reached 33 was recorded as 17.5. That is
the mechanism behind the real scan that stopped at 23.7 degrees of parallax and
reported "no profile view", and behind most of Q13's apparent yaw compression.

The reference and the reported peak are now two fields, because they are two
jobs. Written correctly the beat gives up below `epsilonDeg / patience` =
1.8 deg/s, a head that has genuinely stopped; the bug made that threshold twenty
times too fast. Three tests cover it, and all three fail on the old code —
**every other protocol test teleports** to the target angle and holds, which is
why nothing caught this.

**The enrollment worker's fallback could not run.** `postMessage` transferred the
frame buffers, detaching every `Float64Array` on the caller's side; both failure
paths — the 60 s timeout and a worker-side error — then re-solved inline from
arrays of length 0. It did not throw. `enroll` returned a degraded model of the
average face, the app attached a scan record to it and wrote it to localStorage
as the wearer's measurements. Now cloned rather than transferred: the argument
for transfer was avoiding a copy "at exactly the moment it is trying to stay
responsive", but this runs after the scan ends with the main thread idle waiting
on a one-to-three-second solve, so a 3.6 MB clone costs single-digit
milliseconds. `runInline` also refuses empty frames now, so the failure can never
be silent again.

**The harness was not testing the iris ruler, it was assuming it.**
`synthesizeIris` placed the four contour points on a camera-facing circle in
*pixel* space, so the projected iris was a perfect circle at every yaw with
visibility pinned to 1 even at 86 degrees. Its own docstring asserted the physics
it declined to implement. It now models a real disc that rotates with the head,
counter-rotating in the orbit to hold fixation up to `EYE_ROTATION_LIMIT_DEG`,
with visibility inherited from the eye corner. **Harness-kinder-than-reality
instance #10.**

With the physics modelled the effect is visible and bounded: readings are flat
within 2.5% out to 60 degrees, then −5.6% / −7.6% / −10.4% across the 60–90 band
while iris visibility falls 0.69 → 0.13. A yaw gate at 55 degrees was then
written and **measured to be worse** — mean scale error 0.68% with it against
0.47% without, because `solveScale` takes a median and discarding a third of the
sample costs more stability than the biased tail costs accuracy. The gate was
removed and the refutation is recorded in `scale.ts` at length, because the next
reviewer will notice the same missing gate and reach for the same fix.

The faithful iris is a net improvement on its own: population scale error
0.77% → **0.59%**, nose median 1.10 → 1.04 mm, standoff p90 0.92 → 0.76 mm.

---

## SETTLED 2026-08-21 — the first real-wearer dump

A real scan produced five defects that synthetic runs could not. All are fixed;
recorded here because each is a *class* of mistake that will recur.

**The app threw away visibility.** `estimateSigma` returns `{sigmaPx, visibility}`;
`app/main.ts` destructured only `sigmaPx` and handed the bundle
`fill(1)` — every landmark fully visible on every frame, including the far-side
nose at 35° of yaw. Fingerprint: `noseObservations` exactly equal to `framesUsed`
(48 and 48 in the real dump). `noseSigmaMm` read 15–19% optimistic, and
`noseConfidence`'s `observed = min(observations/25, 1)` was pinned at 1.0 for
every real wearer forever, so its failure branch could never fire. **Harness-
kinder-than-reality instance #5**: every test passed the synthesizer's true
visibility, so nothing exercised the app's path. Now asserted in
`tests/pipeline.test.ts`: nose observations must come out strictly below the
frame count.

**`padLoadFraction` had the wrong denominator.** It was
`clamp(padLift / weightN, 0, 1)` — pad-only, and clamped — so it read exactly
1.000 whether the ears carried nothing or 70%, and told a wearer their nose was
carrying everything when it was carrying three quarters. Now
`padLift / (padLift + earLift)`, with the unclamped `padOverClosure` exposed
separately. `padLift` is now **signed**. Note the comment already in `contact.ts`
recording an *earlier* version of the same bug: the fix at the time corrected the
projection and left the denominator.

**Correction, 2026-08-22 — the attribution was wrong.** This entry said a
`padOverClosure` above 1 means "something else is driving the pads in (see Q15)",
naming the temple hook. Measured, the hook cannot be it: its Gauss-Newton row is
pure +z, so it contributes nothing to the vertical equilibrium, and the clearance
term never engages at all (zero of 1,462 solves). The measured causes are the
**translational prior** pulling a frame that perched above nominal back down —
1–17% of the frame's weight on the affected pairs — and solves that stop on "no
improving step" with a vertical gradient still 0–23% of the weight. The full
explanation is written out at `padOverClosure` in `fit/contact.ts`.

Re-measured over 5 catalogue frames × 29 faces (145 pairs), 2026-08-26:
`padOverClosure` median **0.928**, p90 **1.250**, worst **1.660**, above 1 on
**58 of 145**.

**That is four times the count this paragraph used to give** (median 0.82, p90
0.96, worst 1.264, above 1 on 11 of 145), and only part of the move is the
pad-load redefinition of 2026-08-26 — which took the count from 59 to 58 and
the worst from 1.900 to 1.660, i.e. *down*. The rest of the gap was already
there: the 11-of-145 figure predates the 2026-08-25 contact-row fix, which moved
every settled pose. It is quoted here as a reminder that a re-measurement in a
document is only as current as the last time somebody ran it.

**The displacement field never scaled.** `applyScale` scaled positions and pose
translations but not `field.values`, so `displacementRmsMm` carried an `Mm`
suffix while holding pre-scale gauge units — 0.74 reported for a field that was
1.09 mm. The invariant now lives in one place: everything with length units
scales together.

**Two faults could not reach a grade.** `padDepthErrorMm` and `pantoscopicDeg`
each appeared exactly once in the former `advice.ts`, inside an adjustment string, with no
key in `WEIGHTS`. A wearer whose pads buried 1.9 mm and whose lenses sat at −1°
was told *"Rests where it should on your nose"* and scored 81. Both now have
verdicts and weights. Worth stating as a rule: **a fault a wearer can feel must
be able to move the number.**

**The scan record died before anyone could read it.** The model persists to
localStorage and restores straight into `wear`; the protocol is rebuilt empty
beside it. So a real dump reported *"In progress — 0 of 7 done"* next to a model
built from 48 frames, and the one measurement needed for Q13 was already gone.
The model now carries a `ScanRecord`.

**Also refuted, and worth not re-raising:** `scale.factor` is a gauge-fixing
constant (the bundle has an exact scale null direction) and its value carries no
information alone; `reprojectionRmsPx` cannot detect model mismatch at all,
because the six pose degrees of freedom absorb shape error — fitting the *average
template* scores marginally better.

---

## Q12 — SETTLED 2026-08-20. The seat's falsifiability control was too weak.

**The problem:** `report:seat` compares a contact-solved seat against three
controls. Two of them separate cleanly (the template nose at 1.51 mm against
1.03 mm for the real one). The third — "nominal placement", meaning pads hung on
a landmark with no solve at all — comes out at 1.21 mm, only 1.2× worse, and the
gap swings with the population (clean on 5 subjects, marginal on 8).

**Why:** `nominalPose` was corrected during development to place the pads on the
nasal sidewall rather than on the bridge apex, because starting the solve with
the pads floating 2 to 10 mm off the skin is not a plausible initialisation. But
that also made the *baseline* much better than what v1 actually ships, so the
control now understates the contact solve's contribution rather than measuring
it.

**To settle it:** two separate poses. One that genuinely reproduces v1's
placement (bridge apex, pads wherever they fall) as the control, and one that is
a sane solver initialisation. They are different jobs and should not be the same
function. Then re-derive the bar from the measurement.

**Worth:** high for the *claim*, nil for the product. The seat itself is
unaffected; what is affected is how much of the improvement this repository can
honestly attribute to it.

**Settled:** split into two functions. `landmarkHungPose` puts the frame's *pad
centroid* on the bridge landmark and solves nothing — v1's rule, reproduced —
while `nominalPose` stays the solver's initialisation. The control separates at
**5.37 mm against 1.15 mm, 4.7x**, where sharing one function had it at 1.55x.
The test bar moved from 1.8x to 3x.

So the contact solve was never worth "about fifty percent"; it is worth more than
fourfold, and the control had simply been handed the answer. Two remaining weak
controls are noted but not chased: the template-nose control separates by only
1.3x, and the *flat*-nose control comes out slightly BETTER than the baseline
(1.04 against 1.15), which it should not — a flat nose is not a good model of a
real one, and a control that passes by accident is the thing this question is
about. Both are about the controls, not the seat.

---

## Q10 — No real frame assets have measured geometry.

**Assumed:** five parametric frames generated from numbers
(`fit/frame-asset.ts`, `TEST_FRAMES`). Every one declares
`dimensionSource: 'assumed'`.

This is v1's problem restated: nine of its eleven catalogue frames declared
`widthSource: 'assumed'` because their geometry had been normalised to a 140 mm
placeholder. It is an **asset pipeline** problem, not an algorithm problem, and
no amount of solver quality survives it.

`assets/glasses/` has ten real `.glb` files (plus `base.obj`). `derivePads()` will find their
pad surfaces. What is missing is the true physical size of each — a caliper
measurement of the front width, and ideally the mass.

**Worth:** high, and it is the cheapest high-value item on this list. Eleven
frames, a caliper, twenty minutes.
