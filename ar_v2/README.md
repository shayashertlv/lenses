# Lenses AR — v2

A browser eyewear try-on that **scans your face once, then tracks against the
scan**.

Everything runs on the device. Nothing about your face is uploaded.

---

## Why there is a v2

`ar/` works. It has 396 checks, 16,000 lines of source and 8,500 lines of
carefully-argued comments, and it has two problems its author reported by name:

> "the glasses are being pushed forward for no reason" — at more than 35–40° of
> head turn
>
> "the interaction of the glasses with the nose is not good at all"

**They are the same problem.** v1 estimates shape and pose *simultaneously, per
frame, from a single view, against an average head it never replaces*. So pose
has to absorb shape error — which is the forward push — and depth stays borrowed
from the average head forever — which is the nose.

v1's own audit found why it could never escape:

> parallax and pose trust are the SAME ANGLE with opposite signs

Turning your head buys triangulation and costs trust, and trust won: over 15
synthetic subjects at 3 camera geometries, **0 of 45** ever accumulated the
parallax its estimator needed. You cannot converge depth from motion you refuse
to trust. That is a contradiction, not a tuning miss.

So v2 has a different shape rather than better constants:

**Turn head rotation from the enemy into the instrument.** Spend four seconds
asking the wearer to turn their head — the exact motion v1 treats as a failure —
solve one bundle adjustment, and freeze the answer.

---

## What that buys, measured

All figures are medians across a synthetic population (8–17 subjects, drawn to
stay inside the published human range) × 3 camera geometries (eye-level,
laptop-lid, phone-in-lap), with ground truth known by construction — and, as of
this pass, medians **across five independent noise realisations** (seeds 11,
23, 37, 41, 53), quoted with the per-seed spread where a single number would
hide it. Regenerate one realisation with `npm run report:enroll`,
`report:seat`, `report:track`; the checked-in `reports/` are the seed-11 run
and say so in their headers.

**Every number below was re-measured on 2026-08-22 and many of them moved.** The
synthetic harness had been salting its capture RNG by `subject.id.length`, and
every generated id is three characters long, so all fifteen index-generated
subjects shared **one** noise realisation and the 17×3 population-by-camera grid
contained six distinct streams rather than fifty-one. The spread these tables
print was therefore almost entirely *geometry* variation with the measurement
noise held frozen — a population of faces measured through a single draw of the
same camera. That is fixed — and then, because a fix measured once is a claim
rather than a result, **replicated**: every contested figure was re-run at five
independent seeds the same day. Several numbers got worse and stayed worse. Two
claims this document used to make inverted outright, and replication settled
both rather than deleting them: the nose field's loss **un-inverted** (the
disproof was the one losing realisation in five — it wins, and was adopted at
a stronger prior), and the constructed seat's loss **confirmed** (its
inferiority is the recorded claim — recorded in prose, and guarded by no
test). Both stories are below.

Re-measured once more on 2026-08-23, at the same five seeds, after the
keyframe-selector repair (`selectKeyframes` now guarantees the six per-axis
extreme frames, which changes which keyframes every enrollment solves on):
every primary enrollment-derived median below moved by at most 0.07 in its
own units (mm, pp), the fragile difference-of-medians statistics in the beat
ablations moved by up to 0.14 mm, no claim changed direction, and the seat
and track tables did not move at all — at their defaults those reports run
against ground-truth geometry and never call the selector.

**The track figures were then re-measured on 2026-08-31 and they did move**, for
an unrelated reason: `f9c9093` rewrote `src/track/` and changed the filter, so
every SMOOTHED figure taken before it — the v1-equivalent arm as much as the
filtered one — was stale. The forward-push, rotation and jitter numbers below
are that re-measurement.

### The forward push

How much the frame's depth error **changes** between frontal and turned — which
is what a wearer actually perceives, since a constant offset is invisible.

Re-measured 2026-08-31: `runTrackReport` at the campaign seeds
{11, 23, 37, 41, 53}, 8 subjects × 3 camera geometries. Seed 11 reproduces the
checked-in `reports/track.txt` cell for cell.

| arm | swing, median of 5 seeds | per-seed swings |
| --- | --- | --- |
| v1-equivalent (fit the average head) | **6.61 mm** | 3.48 / 4.01 / 6.61 / 6.73 / 10.82 |
| v2 with the One Euro on — the smoothing that ships | **3.97 mm** | 1.13 / 1.94 / 3.97 / 4.12 / 4.56 |
| v2 unfiltered (`v2-no-smoothing`) | 0.50 mm | 0.41 / 0.41 / 0.50 / 0.64 / 0.95 |

**A median 1.7× reduction in the artefact that was reported**, on the arm whose
smoothing ships — and on 2 of the 5 seeds that arm is *worse* than fitting the
average head (per-seed ratios 0.76 / 0.97 / 1.70 / 5.58 / 5.85). The unfiltered
arm is **10.5×** (8.5 to 13.2), though that pair is not a clean filter ablation:
the average-head arm is smoothed too. Both figures are medians of per-seed
ratios; the ratios of the medians are 1.7× and 13.2×.

**Read every filtered figure here as *shipped-in-smoothing* and nothing more.**
`report:track`'s `v2` arm matches the app on the One Euro and differs from it in
four other ways: the app passes `rigidity`, turns the motion prior on, and
passes `visibility` — the far-half-face cull `tracker.ts` calls load-bearing at
yaw — and it tracks against an enrolled scan where the harness uses ground
truth. That gap is not obviously small on a sweep this fast:
`PRIOR_MISS_EMA_RATE`'s ledger row records the motion prior costing 7.1× at
1 Hz ±10°. Nobody has measured the configuration a wearer actually runs.

**Which arm the claim quotes is load-bearing, and this section quoted the wrong
one until 2026-08-31.** It read 4.92 / 0.47 / 1.44 mm and headlined "a median
8.0× reduction … on the arm that ships". Two things were wrong with that. The
arm was the unfiltered one — `TRACKER_DEFAULTS.smooth` is `false`, but that is
the library default, what a caller gets who asks for nothing, and neither the
app nor `report:track` is such a caller: the app has run the One Euro since
2026-08-23 (under the stillness latch first, then as the plain `true` default
once that latch was rejected as "stuck and choppy"), and `report:track` builds
each arm with `smooth: arm !== 'v2-no-smoothing'`, so two of its three arms are
filtered. And both smoothed figures predated the filter change carried by
`f9c9093` (2026-08-25, which also carries `derivativeCutoffHz` 1 → 5 and
`ROTATION_DAMPING`) — in `reports/track.txt`'s own words, "it is the FILTER that
moved" — so the v1-equivalent baseline was stale as well, because it is
smoothed too. The unfiltered arm barely shifted (0.47 → 0.50) and its published
per-seed rotation brackets still reproduce to the hundredth, which is how the
two are told apart.

The per-seed column is why this table stopped printing one number per cell:
earlier single-draw versions read 14× (collapsed noise stream), then 8.5× (one
post-fix draw), and the honest statement is that the v1-equivalent swing alone
spans 3.5–10.8 mm across five noise realisations of the same population.

Rotation error of the **unfiltered** solve — the estimator before any smoothing,
which is what `v2-no-smoothing` isolates — median of seeds across the population
and the whole camera ladder: **0.42° at frontal** (per-seed 0.32–0.59),
**1.25° at 60°** (0.50–1.56), **1.14° at 90°** (0.47–1.77). Total placement
error of the bridge, same arm, runs **0.76–0.95 mm** at the median out to 60°,
1.79 mm at 75° and **2.26 mm** at full profile (per-seed 1.97–3.47). The old
text's "falls back to 0.52° at full profile" was one draw's shape: across seeds
the profile figure eases below the 60° peak in 4 of 5, but never back near the
frontal number.

**Those are the figures this section used to print under the words "shipped
arm".** With the One Euro on, median of the same five seeds: rotation
**0.84° frontal** (0.73–0.95), **4.20° at 30°** (3.98–4.63), **5.51° at 60°**
(4.98–5.81), **1.58° at 90°** (1.27–2.23); bridge placement **3.53 mm
frontal**, 5.03 at 30°, **6.12 at 60°** (5.85–6.68), 3.17 at full profile —
4 to 7× the unfiltered arm through the middle of the sweep.

At mid-yaw the filter very nearly erases the scan's advantage in angle:
**5.51° against the average head's 5.80°** at 60°. What the scan still buys
there is placement — 6.12 mm against **16.61 mm** (the average head runs
10.9–16.6 mm depending on yaw bucket, p90s far worse). So the scan carries the
frame's *position* and the filter spends most of what it carries in *angle*.
This is a deliberately fast sweep — 35° in a third of a second — so ordinary
browsing is slower and the lag correspondingly smaller. `docs/NEXT-SESSION.md`
§B holds the open question, and it is a question about the shipping tracker
rather than about an ablation arm.

There is deliberately **no yaw handling anywhere in the tracker.** The symptom
was a consequence of solving shape and pose together; with shape frozen it has
no mechanism.

**The 20–47 mm worst-jitter outlier class is gone, and this paragraph used to
say it was here.** It reported that class at 4 of the 5 seeds, naming the arms
it landed on, and called it real, recurring and unexplained. Re-measured
2026-08-31 at the same five seeds, no such outlier exists: worst jitter runs
**3.06 to 4.35 mm** across all three arms and all five seeds, with nothing above
it. Its "tight (1.4–1.7 / 2.4–2.9 mm in every arm at every seed)" bands held on
the tree they were measured on — the pre-port filtered arm sat at 1.53 / 2.77,
inside them — and they no longer describe this one: the medians now span
0.89–1.58 and the p90s 1.89–2.76, because the two filtered arms dropped well
below the unfiltered one (see the ablation below).

Whether the outlier class was fixed or was an artefact of the pre-`f9c9093`
filter is unmeasured; it is the same port that moved every other smoothed
number on this page. What survives unchanged is the instruction: do not quote a
worst-jitter figure as though it separated the arms.

### The nose

Surface error against ground truth, after rigid alignment. Two rows, because the
gap between them is the single most useful thing in this table. Median of the
five seeds, per-seed range in brackets:

| | nose region | pad strip | absolute scale |
| --- | --- | --- | --- |
| as shipped (pooled iris constant) | 1.54 mm [0.99–1.65] | 1.19 mm [0.74–1.39] | 3.19% [1.79–4.69] |
| **with this wearer's true iris** | **0.83 mm** [0.74–0.98] | **0.57 mm** [0.52–0.78] | **2.01%** [0.92–2.91] |

The nose gap between those rows is the whole argument for a ruler that knows
this wearer rather than a population mean, and it replicates cleanly (the true
iris wins the nose in 5/5 seeds). Read the scale column more carefully than the
old table allowed. The pooled-iris figure swings 1.8–4.7% by seed because a
drawn population's true irises land nearer or farther from 11.7 mm — it is the
ancestry-bias lottery, not measurement noise. And the true-iris row no longer
reads 0.66%: it is **2.01%** [0.92–2.91], because with the diameter assumption
removed what remains is the *platform* — the solved focal length and depth. This
paragraph used to put that term at ~1.5% on the card campaign's authority; the
term never needed a card, and [`docs/SCALE.md`](docs/SCALE.md) has since
decomposed it card-free over 255 enrolments at **0.37% median** (sd 0.56%),
with 95.7% of the iris path's error sitting in the population diameter
assumption — so a better ruler buys nearly all of it, not nearly none. Neither
number reconciles with the 2.01% row above; which one describes it is open.
Supplying the true iris still buys a median 2.3 points of scale
across the paired seeds (from −2.7 better to +0.7 worse — seed 37 inverts),
and the surface improvement is where the bias bites: nose 1.54 → 0.83 mm, pad
strip 1.19 → 0.57.

**A claim that inverted and then un-inverted, both times by measurement.** On
the morning of 2026-08-22 this section conceded that the free-form nose field
lost to the basis in every configuration tested — a seeded re-run had disproved
the field's published win, and the regression bar was left red rather than
relaxed. The settlement campaign then replicated the question across **5
independent seeds** and swept the field's prior, and the verdict reversed: with
`BUNDLE_DEFAULTS.fieldPriorScale` 8 (adopted), field-on beats field-off on
median nose RMS in ≥4/5 seeds in *both* the shipped and the bias-free configs,
at every prior scale swept (×1 through ×8). Median-of-seeds nose RMS: shipped
1.439 → **1.269 mm**, clean 0.884 → **0.668**; the pad strip — the surface the
glasses actually rest on — 1.353 → 1.030 shipped, 0.762 → 0.471 clean;
laptop-lid confirms 3/3 in both configs. The disproof-draw is accounted for
rather than discarded: it came from the seed-41 family, the one realisation in
five that still loses under pooled-iris + detector bias.

The diagnosis this section carried — the field faithfully reproduces whatever
the detector reports, structured error included, where the basis smooths it
away — turned out to be exactly right, and the Q21 separator measured it: hold
the claimed sigma at the shipped 0.7 px pattern and scale the *actual* noise
to zero, and the shipped-config deficit collapses (+0.066 mm → +0.016) while
the clean config's win grows (−0.097 → −0.125). No deficit survives at zero
noise, so the field was chasing landmark noise and detector bias, **not**
mis-modelling the surface — and the stronger prior is the cure, not a
concession: at ×8 the field still moves (solved-field RMS 0.799 mm median
against 1.048 at ×1), so it has not been smoothed into the average nose. The
`tests/pipeline.test.ts` bar is **green on the adopted configuration**. These
are the settlement campaign's digits on its frozen tree state; the tables
above are the merged tree, re-measured — and on the merged tree's own reports
the field wins the **paired** nose comparison in 4 of 5 seeds (0.99 vs 1.28,
1.43 vs 1.46, 1.55 vs 1.84, 1.54 vs 1.72; the loser is seed 41 again, 1.65 vs
1.18), while the *unpaired* medians-of-medians land 1.54 with the field
against 1.46 without, because the one losing seed is big enough to move the
middle of five. The campaign adopted on the paired per-seed rule, and this is
why.

And where the frame comes to rest — the thing the complaint was about. Median
of the five seeds, per-seed range in brackets:

| | pad depth error |
| --- | --- |
| seated against the *average* nose (template-nose control) | 1.44 mm [1.33–4.27] |
| seated against a *flattened* nose (flat-nose control) | 1.33 mm [0.53–1.99] |
| **seated against this wearer's scanned nose** | **1.06 mm** [0.93–1.50] |
| pads hung on a landmark, no contact solve at all (v1) | 4.79 mm [4.34–5.20] |

The v1 row is the stable one: **4.6× the baseline at the median ratio**
(per-seed 2.9–5.1×), and the template-nose control separates in 5/5 seeds
(ratios 1.25–2.94×). The wedge relationship v1 derived analytically and could
not test is a measured sweep, and the seeded replication shows how soft its
one number is. At the settlement campaign's frozen tree state the median-curve
fit `report:seat` prints read **0.64 / 0.66 / 0.81 / 0.99 / 1.04 mm of descent
per mm of pad separation across the five seeds** (median 0.81). Those are
settlement figures. Re-run at the same five seeds on the current tree, the same
fit reads **0.807 / 0.813 / 1.059 / 1.146 / 1.163** (median **1.059**), and the
one of the five that is an artefact rather than a prose number —
`reports/seat.txt`, seed 11 — reads **1.146**. Reproduce any of them with
`runSeatReport({ seed, subjects: 6 })`; `npm run report:seat` writes the seed-11
one. The estimator is soft either way: the five seeds span a factor of 1.4, and
the committed reading has moved 0.852 → 0.642 → 1.146 across three
regenerations. That move happened somewhere in `f9c9093..bc28773` and the report
was not regenerated in between, so the tree cannot say which commit did it —
though `586a2a2`, which moved the ear rest from the temple's tip to its bend,
changed exactly the load path the wedge shortfall runs through.

**There is no ledger constant, and this paragraph has claimed one since
`f9c9093`.** It read *"The ledger constant `WEDGE_SLOPE_MM_PER_MM` stays 0.92
— the pooled face-by-separation regression over 29 faces from the 2026-08-22
single re-run"*, and every load-bearing clause of that is wrong.
`fit/advice.ts` was rewritten into `fit/score.ts` in `f9c9093`; the constant
went out with it, and its ledger row went in the same commit that wrote this
sentence. That row had held **0.74**, never 0.92. The sweep has never run on 29
faces — `report:seat` defaults to `subjects: 6`, which `generatePopulation`
turns into eight, and `npm run report:seat` does not override it (only the
freshness canary does, and it fingerprints the report rather than producing
it). "Pooled" misnames the estimator too: `report:seat` takes the population
median at each separation and fits one line through the seven medians, which is
not a pooled face-by-separation regression. And nothing in this tree emits
per-wearer slopes, so *"0.27 to 1.60"* cannot be checked here either.

Where 0.92 came from is worth recording, because it was real once. Its only
surviving derivation is a **dropped stash** — `1ce584f`, 2026-08-22, unreachable
from every ref and deletable by the next `git gc` — and that derivation refutes
the sentence it fathered: it calls 0.92 the *median-curve* fit over *eight*
faces, puts the *pooled* figures at 0.97 and 0.93, calls it "the low end of a
0.90-to-1.18 range rather than a sharp value", and gives the per-wearer range as
0.28 to 1.60, not 0.27. It was never committed to any branch. What survives of
it here is one derivation step in `fit/contact.ts`'s header, and that header now
divides by the number the instrument prints.

**Where this claim is weaker than it looks.** The *flat-nose* control — a nose
with its sidewall flare removed, which should not let a frame find a resting
height at all — is unreliable: in **2 of 5 seeds it reads BETTER than the
baseline** (0.81 vs 1.06 at seed 11, 0.53 vs 1.45 at seed 41), and when it
does separate it manages only 1.2–2.1×. The single-draw version of this
paragraph said "that control is no longer doing its job" off one such draw;
five seeds say the job is done intermittently, which for a falsifiability
control is the same verdict. Until it fails reliably, the separation between
"solved on this face" and "solved on some other face" rests on the
template-nose row.

### The scan got faster, and the quotable number is a ratio

The settlement campaign halved the keyframe budget (`KEYFRAME_DEFAULTS.count`
48 → 24) after the documented "24 costs 0.15 mm" knee failed to replicate at
five seeds — 24 is paired-equal-or-better on every across-seed median mm
metric, failing only one seed's protrusion p90 (+0.192 mm against a 0.10
allowance). Halving the keyframes roughly halves bundle time: 630 → 331 ms
(0.53×), end-to-end enrollment solve **807 → 491 ms median on the campaign
machine — a 1.64× speedup at equal-or-better accuracy**. Quote the ratio, not
the milliseconds: the campaign's own records span 468–872 ms for the same code
across machines and load, and this documentation pass's five-seed re-run
prints 392–437 ms medians in `reports/enroll.txt`'s ms column on the same
hardware (the previous pass, under overnight load, printed 1.34–1.41 s) — a
more-than-3× swing on identical code. Rounds
stayed at 3: pooled paired medians actually favour one round with the field on
(a further 1.8× was available), but the per-seed adoption rule passed only 3/5
in both field configurations, and this tree does not adopt on a pooled median
(`BUNDLE_DEFAULTS.rounds`).

### A bug inherited from v1, quantified

`IRIS_DIAMETER_CM = 1.17` — 11.7 mm — is a **white-adult mean**. Published
horizontal-visible-iris-diameter group means run ≈11.10 mm (Japanese),
11.26 mm (Chinese), 11.75 mm (white adults). On a wearer whose iris is genuinely
11.10 mm that ruler reads every length **5.4% long**, and the error is
*systematic*, so no amount of averaging removes it.

Measured end to end (median of 5 seeds, per-seed range in brackets):

| | absolute scale error | nose surface error |
| --- | --- | --- |
| pooled iris constant | 3.19% [1.79–4.69] | 1.54 mm [0.99–1.65] |
| this wearer's true iris | **2.01%** [0.92–2.91] | **0.83 mm** [0.74–0.98] |

The scale gap between the rows narrowed against earlier versions of this table
not because the bias shrank but because the *platform* term — solved focal
length and depth, put at ~1.5–2% on its own by the card campaign, which
`docs/SCALE.md` has since contradicted card-free at 0.37% (see above, and treat
the attribution as open) — was said to dominate both rows; the paired seeds
still show the true iris buying a median 2.3 points of scale. The
nose column is where the bias does its damage, and there the gap is 5/5-seed
stable.

v1's prose calls iris variation "±0.5 mm of noise". A large part of it is bias,
correlated with ancestry, in the one quantity the app claims real units for. v2
reports the uncertainty alongside every millimetre and refuses to print a
lens-ordering PD from an iris-only scale.

**That paragraph used to end "and the card protocol is no longer just a slot",
and what followed measured that card.** The card is gone; the measurement is
kept, because Q3 was closed by deletion and not by refutation:

> Measured 2026-08-22 on the synthetic harness (5 seeds × 3 geometries × 8
> subjects, 120 scans per noise level, the basic detector run for real against
> rasterised frames):
>
> | ruler | median abs scale error | p90 | worst |
> | --- | --- | --- | --- |
> | pooled iris 11.7 mm (same runs) | 5.14% | 10.28% | 16.49% |
> | **card at 1 px edge noise (synthetic, unwired)** | **0.80%** | 3.16% | 11.70% |
>
> The tail is the camera solve, not the card: edge noise from 0.5 to 2 px moves
> the median by 0.15 points, the residual correlates 0.973 with the iris path's
> error once its diameter assumption is subtracted, and the card's signed mean
> error is 0.13% — the ancestry bias is gone. It stays unwired because the
> detector has never seen a real frame (`docs/OPEN-QUESTIONS.md` Q3, Q8).

`enroll/card.ts`, its scan beat and the ladder's card rung left the working
tree on 2026-08-25, at `f9c9093`, and the owner then rejected the method
outright: *"I don't like the card method, I'd like the algorithm to not rely on
it at all."* **Do not rebuild it**, and do not go looking for it either:
`card.ts` was never a tracked file, so no commit holds it — `f9c9093` is where
the tree stopped carrying it, not a commit you can recover it from, and the row
above cannot be re-derived. The shipping ladder is `pd → iris → assumed`, and
nothing in the running path has ever asked a wearer for a card.

What replaces it is not a better ruler but a smaller requirement.
[`docs/SCALE.md`](docs/SCALE.md) measures what scale error actually costs — at
±1% the frame front width moves 2.5 px on a 1024-wide render and the top-ranked
frame changes for 6 wearers in 50 — and sets the target at **1.5%, not 0.1%**.
The wearer's own prescription PD is the rung aimed at it: a propagated **0.79%**
against the iris's propagated 4.70%, and it is already built, applied against
the reconstructed 3-D surface rather than an image-space pupil span. Read those
two as *ruler sigmas, not measured end-to-end scale errors* — `enroll/scale.ts`
is explicit that `docs/SCALE.md` quotes the 0.79% without that qualification and
that it should not be read as a measurement of the PD rung, and no seeded
end-to-end measurement of that rung exists. The iris stays the shipping default:
good enough for the try-on picture, and the reason an iris-scaled PD is printed
with `(iris — not for ordering lenses)` beside it rather than offered as a
lens-ordering measurement.

### Things that turned out not to earn their place

Reported because a build that only reports its wins is not a measurement. One
entry has since reversed — the pose filter earned its place back, twice, once in
the field and once on the harness — and it is kept here with its reversal
rather than quietly moved.

- **The pose filter did not earn its place, then a wearer put it back, then it
  started earning its place.** The original finding: every One Euro tuning
  tested — including v1's own — was worse than no filter, on lag *and* on
  jitter, because once pose comes from six parameters against known geometry
  with 300+ correspondences the estimate is cleaner than the filter's time
  constant. Measured across 5 seeds on 2026-08-23: unfiltered won jitter median
  **5/5** (1.46 vs 1.53 mm) and p90 **5/5** (2.55 vs 2.77). (Synthetic result —
  Q7.)

  Then the first real wearer reported jiggle that grows with yaw — which is the
  falsifier Q7 had written down in advance, that a real detector's noise is
  correlated across landmarks in a way this harness's per-landmark independence
  underestimates. The app has run the One Euro since 2026-08-23 — under the
  stillness latch first, and as the plain `true` default after that latch was
  rejected. The library default `TRACKER_DEFAULTS.smooth` is still `false`; both
  facts are true of different objects, and the arm every wearer has seen is the
  filtered one.

  **And the jitter verdict has since reversed, which nothing in this repo had
  noticed.** Re-measured 2026-08-31 at the same five seeds: the **filtered** arm
  wins jitter median 5/5 (median-of-seeds **0.945 mm** against the unfiltered
  1.469) and p90 5/5 (**1.944** against 2.519). The unfiltered arm barely moved
  (1.46 → 1.469) — the filter did, in `f9c9093`, which carries
  `derivativeCutoffHz` 1 → 5, `ROTATION_DAMPING` 0.25 and the rewrite together.
  So "worse on lag *and* on jitter" is now only true of lag, and lag is where it
  is expensive:
  4–7× the unfiltered placement error through mid-yaw, and a forward-push swing
  of 3.97 mm against 0.50. Two caveats stand: the *tuning sweep* behind the word
  "every" has never been re-run, and the 20–47 mm worst-jitter outlier class the
  old text discussed does not appear on this tree at all (worst jitter runs
  3.06–4.35 mm across every arm and seed).
- **The profile beat holds its value; the lean beat's PD evidence stays
  evaporated.** Dropping the profile hold costs a median **+0.14 mm** of nose
  surface error, paired per seed (0.00 to +0.45 across the five), and moves
  the median absolute protrusion error **+0.19 mm** (+0.01 to +0.54) — real,
  replicated, about what was expected. Dropping the *lean* beat used to be the
  dramatic one (PD "4.6 → 8.4 mm"). Across five seeds it moves median PD by
  **+0.28 mm** paired (−0.42 to +0.29) and absolute scale by +0.29 points
  (−0.39 to +0.92) — both inside the seed-to-seed spread of the metrics
  themselves, and the intermediate single-run claim that scale *improves*
  without the lean does not replicate either (it worsens in 3/5 seeds).
  Neither direction clears noise.

  This is a structural change, not noise. `model.pdMm` is no longer an
  image-space span read beside the iris; it is read off the reconstructed 3-D
  surface after `applyScale`. So PD error is now essentially *scale* error
  wearing different units — the 14-subject re-run's pair makes the identity
  visible: 4.23% median scale error against 2.63 mm on a ~63 mm PD is 4.2%,
  and the same identity holds on this pass's five-seed pairs — and scale comes
  from the iris ruler, not from the focal-length solve the lean beat
  conditions. The lean beat may still be load-bearing for focal length itself;
  what is no longer true is that **PD** is the evidence for it.
  `COVERAGE_THRESHOLDS.distanceSpanPct` is currently a `measured` constant
  whose measurement has evaporated.

- **PD is not yet good enough to order lenses with.** Median error is
  **1.91 mm** across the five seeds (per-seed 1.13–2.39) against the
  incumbent's published ±1 mm for 7 in 10 — better than the 2.63 mm of the
  last pass and the 4.6 mm before that, and still the same verdict: even the
  best seed's median misses the bar. The system already refuses to present an
  iris-derived PD for ordering. This bullet used to name the card protocol as
  the fix; the card was deleted and the method rejected (Q3,
  [`docs/SCALE.md`](docs/SCALE.md)), so what is left is the wearer's own
  prescription PD, below. Either way the number is here rather than buried.

  Note what the wearer's-own-PD path does and does not fix. Supplying a
  prescription PD drives the reported PD error to **identically zero**, because
  the correction sets the solved span equal to the number you typed in — that
  column stops being a measurement. The scale error it buys is real but far
  smaller than once claimed: **1.22% median** against the pooled iris's 4.23%
  on the same run (the 14-subject single re-run of 2026-08-22 — this pair has
  not been re-swept seeded), where this repo used to publish 0.44% against
  4.39% and call it tenfold. It is about 3.5×, and at the worst case (9.4%
  against 12.3%) it is barely a difference at all.

---

## Running it

```bash
npm install
npm run build
python serve.py
```

Then open <http://127.0.0.1:8020/>.

With no camera it falls back to a sample still and says so, and the
**Use an average face** control puts glasses on that still without a scan — the
fastest way to look at every frame in the catalogue.

`assets/` is tracked here and `vendor/` is fetched here by
`scripts/fetch-vendor.mjs` and SHA-256 verified. Nothing is served from outside
this directory; `scripts/check-selfcontained.mjs` fails the build if that comes
back. This paragraph used to say the opposite — that both were served from the
sibling v1 checkout during the migration — which had been false since stage 1,
and pointed every new reader at a tree they did not need. v1 is now deleted, so
the claim is not merely stale but unfollowable.

### The checks

```bash
npm test
```

Runs four gates — the isolation boundary, the constants ledger, self-containment
and the report stamps — and the whole suite: every analytic jacobian in the tree
against central differences, the enrollment against ground truth, the seat
against its controls.

The count that used to sit here, **300 tests**, was exact through `ad8c695` and
has been wrong since `947edf7` — both on 2026-08-26, five days before anyone
noticed. It read 338 on the morning of 2026-09-01 and more by that afternoon,
which is the argument. This paragraph does not carry the current one on purpose:
take it from the `pass` line `node --test` prints at the end of the run.

Two qualifications:

- **The retraction outlived the defect — and was never true.** This bullet used
  to read: *"Only the four camera jacobians (`dProjDPoint`, `dProjDPose`,
  `dProjDModelPoint`, `dProjDIntrinsics`) are difference-tested.
  `shape/basis.ts`'s `basisJacobian`, `shape/displacement.ts`'s
  `displacementJacobian` and `fit/contact.ts`'s `pointJacobian` have no
  central-difference test anywhere in the tree."* All three have one, each in
  its own case under `tests/core.test.ts`'s `analytic jacobians match central
  differences` — `d(vertex) / d(shape coefficient)`, `d(vertex) / d(free-form
  field value)`, and `d(seated point) / d(pose increment), against the increment
  poseOplus applies`. They always did: f9c9093 added the three tests, the
  corrected header in `src/core/camera.ts`, and this bullet denying both, in one
  commit. The window in which the retraction was true is
  empty; it did not drift, it was born wrong, and it then sat here for a week
  contradicting the tests in the file it was describing.

  What is true now: `src/` holds exactly seven analytic jacobians — `grep -rE
  'function [a-zA-Z]*[Jj]acobian' src/` finds `basisJacobian`,
  `displacementJacobian` and the module-private `pointJacobian`, and the other
  four are the camera ones above — and all seven are differenced, as are
  `core/robust.ts`'s `barronDrho`, the seat gradient in both its regimes, and the
  pad lift `describeSeat` reports.

  **What is still not.** *Every analytic jacobian* is true; *every analytic
  derivative* is not, and the biggest one missing is the derivative the tree
  actually runs: `huber`'s rho' has no difference test anywhere, and `huber` is
  the shipped loss in all three solvers (`track/pnp.ts`, `enroll/bundle.ts`,
  `track/snap.ts`) — the Barron family beside it is `redescending: false` and
  unwired. Nor does anything else on this list: `cauchy`'s rho',
  `enroll/bundle.ts`'s `accumulateGlobal`, `accumulateSilhouette` and
  `solveField`, the exported `accumulateDisplacementPriors`, and `refinePnP`'s
  inline gradient and Hessian. And one blind spot is permanent even where the
  coverage is complete: `duyn` and `dvxn` in `src/core/camera.ts` are assigned
  the identical expression, so a transpose of the 2x2 distortion block is a
  no-op that no difference test at any `k1` can see. Sweeping `k1` buys the
  asymmetric corruptions and nothing more.
- **The isolation boundary now does two things**: a source-text blacklist, and an
  actual `import()` of every built headless module from `dist/` — 41 of them on
  2026-09-01, and the gate prints its own count (`N module(s) imported cleanly in
  Node.`), which is the figure that stays current. The import pass needs a build,
  and `npm run check:isolation` does not build first —
  running it directly on a clean tree prints a loud SKIP, which is expected
  rather than a failure.

**Two of those tests spent 2026-08-22 deliberately red**, and both ended the
day green **by measurement rather than relaxation**. They were regression bars
asserting published claims a seeded re-run had disproved, and the settlement
campaign replicated both claims across 5 independent seeds. `the field earns
its place` is no longer a disproved claim: the disproof was a single unseeded
draw from the seed-41 family — the one losing realisation in five — and
replicated, the field wins in both configs and was adopted at prior scale ×8
(nose median-of-seeds shipped 1.439 → 1.269 mm, clean 0.884 → 0.668); the test
asserts the adopted configuration. The seat settled the other way: the
constructed seat won 2/5 seeds against a 4/5 adoption rule, and its
**inferiority is the recorded claim**. **There is no seat bar.** This sentence
used to promise one that guarded the record in both directions; no such test
exists in `tests/`, and none exists in any commit on any ref. The record is
unguarded in the plain sense — if the constructed seat became better than the
contact seat today, nothing would fail — and unguardable in a stricter one,
because `bearing.ts` left the working tree at `f9c9093` and nothing in this
tree computes a constructed seat at all. See "What this build cannot tell
you".

```bash
npm run report:enroll    # reconstruction accuracy + ablations
npm run report:seat      # where each frame rests + falsifiability controls
npm run report:track     # pose error vs yaw, the forward-push table
```

---

## How it is put together

```
src/
  core/       pure maths — no browser, no three.js, millimetres throughout
    linalg    Float64, manifold rotations, dense solves
    camera    pinhole + analytic jacobians
    mesh      the 468-vertex template, regions, measurements
    meshdist  signed distance + closest point, for contact
    raster    a tiny depth-buffer rasteriser (visibility, silhouette, occlusion)
    shape/    20-mode anthropometric basis + free-form nose displacement field
    facemodel THE boundary type: what a scan produces
  enroll/     the four-second scan and its bundle adjustment;
              the scale ladder is pd -> iris -> assumed (docs/SCALE.md)
  track/      PnP against the scanned model; One Euro (library off, app on)
  fit/        contact-physics seat, frame assets, numeric fit scoring
  detect/     landmark adapter + per-landmark uncertainty
  render/     three.js; convert.ts is the ONLY CV↔GL conversion in the tree
  app/        the browser shell: frame lock, camera, UI, the enrollment worker
  testkit/    synthetic population, ground-truth metrics, the three reports
```

There is no `core/lm.ts`. A shared Levenberg-Marquardt module existed, was
documented here, and was unreachable — the three live solvers (`track/pnp.ts`,
`fit/contact.ts`, `enroll/bundle.ts`) each carry their own textbook
multiply-by-a-constant damping inline. Any prose claiming **Nielsen damping** as a
property of this tree is false: no live solver uses Nielsen's gain-ratio rule.
The deleted module also attributed the Schur elimination to an `enroll/schur.ts`
that has never existed; it is inline in `bundle.ts`, in `solveGlobal`.

`enroll.worker.ts` lives in `app/`, not `enroll/`, and the reason is the boundary
below: it sets `self.onmessage` at module scope, so it could never be imported in
Node while the isolation check claimed `enroll/` was headless.

`core/ enroll/ track/ fit/ detect/ testkit/` are **enforced headless** —
`scripts/check-isolation.mjs` fails the build if any of them touches a browser.
v1 kept the same split by discipline; discipline is what fails at 2 a.m.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full picture.

---

## What v2 keeps from v1, unchanged

Not everything needed rewriting. Carried over verbatim, with the reasoning
intact:

1. **The frame lock** — one clock for the pixels and the pose, drop frames whole
   rather than queueing them. The best idea in that tree, and the reason the
   composite is exact at every velocity.
2. **`numFaces: 2`** — MediaPipe applies untunable internal smoothing when and
   only when it is 1.
3. **Picking the wearer by landmark-bounding-box *area***, not width — a face
   turned to profile loses width without getting further away.
4. **Constants provenance classes**, and *"a check that cannot fail is a bug"*.
5. **The wedge insight** — height, standoff and roll are one coupled equilibrium.
6. **"Keep previous, never assume average"** on refused measurements.
7. **One Euro's four lessons** — load-bearing again, not just on paper: the
   library default is off and the app ships the filter on.

---

## What this build cannot tell you

Ten open questions, in [`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md). The
honest headline is the same one v1 carried:

> **Nothing here has ever seen a real camera.** There is no webcam on the machine
> this was written on. The camera path is exercised as far as `getUserMedia` and
> no further.

That list used to include the card detector (Q3), "built and measured, but only
against images this tree's own rasteriser produced — no real card, hand, glare
or motion blur — and wired to nothing". The detector left the tree on
2026-08-25 and the method was rejected, so the entry is a record rather than a
gap waiting to be filled: Q3 closed by removal, not by refutation, and its
measurement is preserved in the iris-bias section above. The live answer to
absolute scale is the 1.5% target in [`docs/SCALE.md`](docs/SCALE.md) and the
prescription-PD rung aimed at it.

**This section carried a fourth entry that was false, and it is worth recording
what it said**, because it was the front-door document reasoning from it:

> **No eyewear geometry is rendered.** There is no glTF loader in this tree,
> `frameNode` is childless, and the seat is solved and applied to an empty node.
> The app shows the camera feed and the measurements. It does not show the
> glasses.

Every clause was wrong by 2026-08-25. `render/frame-mesh.ts` imports
`GLTFLoader` and calls `loadAsync`; `render/scene.ts` adds the loaded object
under `frameNode`; the app draws a measured pair of glasses. The paragraph
beneath it then concluded "in v2 there is nothing visible to describe" about the
owner's own reported symptoms, and closed "Disclosure was chosen over building
it" — a disclosure of something that was not true. All ten catalogue assets are
now wearable and the try-on is the first thing on the page.

The synthetic capture model was wrong twice during this build, both times in ways
that changed an engineering decision — occluded landmarks are *biased*, not
merely noisy; real heads wander smoothly rather than teleporting. Both were
caught by suspicion rather than by the harness. There will be others: the
RNG-seeding collapse described at the top of the measured section is the third,
it survived every check in the tree for the whole of the build, and it was
invisible precisely because a frozen noise draw looks exactly like a quiet one.

**What the 2026-08-22 re-derivation could not settle, the settlement campaign
then did** — by replicating every contested claim across five independent seeds
instead of trusting any one draw. The four unsettled findings, and where each
landed:

- **The field earns its place after all.** The "loses in every configuration"
  finding was a single draw from the one realisation in five that loses;
  replicated, the field wins in both configs and was adopted at prior scale ×8
  (nose median-of-seeds shipped 1.439 → 1.269 mm, clean 0.884 → 0.668). Q21
  records why the weak-prior field lost: noise-chasing, prior-curable.
- **The constructed seat's inversion replicated, and is the recorded claim.**
  At the [2, 36] band the settlement had just adopted, over 5 independent seeds
  (17 subjects each, eye-level), it won the per-seed median in 2/5 against a
  4/5 adoption rule — pooled 1.03 / 5.22 med/p90 against the contact seat's
  1.24 / 3.62, bulk slightly better, tail 1.4× worse (1.9× at [4, 34]), and
  the verdict was identical at every wide band. Q18 is settled as recorded
  inferiority. `bearing.ts` did not stay a testkit instrument: it left the
  working tree at `f9c9093`, the same commit that wrote this bullet, and it was
  never a tracked file, so no commit holds it, and none of the comparison
  above can be re-derived here. No band ships now, because nothing in this
  tree constructs a seat.
- **`VERTEX_SEAT_SIGMA_MM` was set to 3.03, and then left the tree.** It was
  an export of `fit/bearing.ts`, and both went out of the working tree at
  `f9c9093`. The constant has no definition anywhere in `src/` and no ledger
  row, and nothing replaced it — `fit/score.ts` grades the vertex criterion by
  threshold and `VERTEX_REACH_CONFIDENCE`, with no population sigma at all.
  What follows is the record of that setting, not a property of this tree.
  3.03 was the eye-level 5-seed pooled sigma_rms at the band the settlement
  adopted. sigma_med 1.53 describes the bulk; a +1.10 mm mean signed bias —
  the reconstruction reads longer — which no sigma
  carries; p90/med is 5.1 against a half-normal's 2.44, so neither digit is
  faithful alone. The "about 4.8" the old bullet demanded was the full-ladder
  single-draw figure at the OLD [8, 30] band (implied 4.83, consistent with
  the replicated [8, 30] sigma_rms of 4.50); the full-ladder figure at
  [2, 36] has not been measured.
- **`SIDEWALL_BAND_MM` was set to 2–36, and then left the tree.** Same story:
  an export of `fit/bearing.ts`, gone from the working tree at `f9c9093`, with
  no definition in `src/` and no ledger row, and no successor — `contact.ts`'s
  `nominalPose` takes the two `NOSE_WALL_HIGH` landmarks directly, with no band
  and no millimetre extent. The record of the setting, which is all this now
  is: 2–36, settled by the replicated sweep, med-of-med 1.23 — a tie with
  4–34, broken on per-seed median (5/5), pooled p90 (5.22 against 6.88) and
  zero construction failures. On the template
  every wide band selects the whole usable strip (10 pts/side, y 8.0–31.8),
  so the sweep effectively measured whole-strip against trimmed. The plane
  residual still points the wrong way, now replicated: 8–30 has the
  *tightest* truth residual (0.51 against 0.83–0.88) and the *worst* error.

One caveat those campaign digits carry, everywhere they are quoted: fixes to
enrollment and to the contact seat landed in the same pass, and the merged
tree measures differently (verified against a current-tree partial run). The
campaign digits are the settlement record for each decision; the tables above
and the checked-in `reports/` are the merged tree, re-measured at seeds
{11, 23, 37, 41, 53}.

The four that need you: a real camera session (Q8), a detector-bias calibration
against scanned faces (Q2), caliper measurements of the eleven real frame
assets (Q10), and a licence click for FLAME 2023 (Q9).

---

## Privacy

Your scan is solved on your device and stored in your browser only. Nothing about
your face is uploaded. "Delete my measurements" deletes it.
[`docs/PRIVACY.md`](docs/PRIVACY.md) — including a real problem in v1's tree that
this design avoids by construction.
