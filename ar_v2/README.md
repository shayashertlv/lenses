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

All figures are medians across a synthetic population (10–17 subjects, drawn to
stay inside the published human range) × 3 camera geometries (eye-level,
laptop-lid, phone-in-lap), with ground truth known by construction. Regenerate
with `npm run report:enroll`, `report:seat`, `report:track`.

### The forward push

How much the frame's depth error **changes** between frontal and turned — which
is what a wearer actually perceives, since a constant offset is invisible.

| | depth error at 0° | when turned | **swing** |
| --- | --- | --- | --- |
| v1-equivalent (fit the average head) | 5.28 mm | 0.27 mm | **5.01 mm** |
| v2 (fit the scanned model) | 0.14 mm | 0.39 mm | **0.26 mm** |

**A 19× reduction in the artefact that was reported.**

Rotation error, model-known, is **0.25–0.44° flat from 0° to 90° of yaw**. The
same solve against the average head runs 2.3–4.2° and puts the bridge 17–30 mm
from where it belongs.

There is deliberately **no yaw handling anywhere in the tracker.** The symptom
was a consequence of solving shape and pose together; with shape frozen it has
no mechanism.

### The nose

Surface error against ground truth, after rigid alignment. Two rows, because the
gap between them is the single most useful thing in this table:

| | nose region | pad strip | absolute scale |
| --- | --- | --- | --- |
| as shipped (pooled iris constant) | 1.60 mm | 1.15 mm | 2.86% |
| **with this wearer's true iris** | **1.12 mm** | **0.81 mm** | **0.81%** |

With the detector-bias floor also removed (Q2), the nose reaches **0.35 mm**
against **0.99 mm** for the shape basis alone.

**A result that did not go my way.** In the shipped configuration — pooled iris
constant *and* an uncalibrated detector — turning the free-form nose field
**off** scores slightly *better* (1.37 mm against 1.60 mm). The field faithfully
reproduces the biased surface the detector reports, adding structured error,
while the basis smooths it away. The field's real benefit (2.8× on the nose) only
appears once the bias is removed. So the field is right and the calibration is
missing, and the ordering of the work is: **calibrate the detector (Q2), then the
field pays.**

And where the frame comes to rest — the thing the complaint was about:

| | pad depth error |
| --- | --- |
| hung off the bridge landmark (**v1's answer**) | 1.47 mm |
| solved against the *template* nose | 0.61 mm |
| **solved against this wearer's nose** | **0.46 mm** |

The wedge relationship v1 derived analytically and could not test is now a
measured sweep: **0.74 mm of descent per mm of pad separation**, monotone across
the population.

### A bug inherited from v1, quantified

`IRIS_DIAMETER_CM = 1.17` — 11.7 mm — is a **white-adult mean**. Published
horizontal-visible-iris-diameter group means run ≈11.10 mm (Japanese),
11.26 mm (Chinese), 11.75 mm (white adults). On a wearer whose iris is genuinely
11.10 mm that ruler reads every length **5.4% long**, and the error is
*systematic*, so no amount of averaging removes it.

Measured end to end:

| | absolute scale error | nose surface error |
| --- | --- | --- |
| pooled iris constant | 2.86% | 1.60 mm |
| this wearer's true iris | **0.81%** | **1.12 mm** |

v1's prose calls iris variation "±0.5 mm of noise". A large part of it is bias,
correlated with ancestry, in the one quantity the app claims real units for. v2
reports the uncertainty alongside every millimetre, refuses to print a
lens-ordering PD from an iris-only scale, and has a card protocol slot ready
(`docs/OPEN-QUESTIONS.md` Q3).

### Two things that turned out not to earn their place

Reported because a build that only reports its wins is not a measurement.

- **The pose filter is off.** Every One Euro tuning tested — including v1's own —
  is worse than no filter, on lag *and* on jitter, monotonically. Once pose comes
  from six parameters against known geometry with 300+ correspondences, the
  estimate is cleaner than the filter's time constant. v1 needed it badly; v2's
  estimator made it redundant. (Synthetic result — Q7.)
- **The lean beat is load-bearing, the profile beat is worth ~20%.** Without
  leaning in and out, focal length is unobservable and PD error goes from 4.6 mm
  to **8.4 mm**. The profile hold is worth about a fifth of the protrusion error
  — real, but less than I expected before measuring.

- **PD is not yet good enough to order lenses with.** Median error is **4.6 mm**
  against the incumbent's published ±1 mm for 7 in 10. The system already refuses
  to present an iris-derived PD for ordering, and the fix is the card protocol
  (Q3) — but the number is here rather than buried.

---

## Running it

```bash
npm install
npm run build
python serve.py
```

Then open <http://127.0.0.1:8020/>.

With no camera it falls back to a sample still and says so. `vendor/` and
`assets/` are served from `../ar/` during the migration — they are ~100 MB and
byte-identical, and two copies of a template mesh is two things that can drift.

### The checks

```bash
npm test
```

Runs the isolation boundary, the constants ledger, and 49 tests — every analytic
jacobian against central differences, the enrollment against ground truth, the
seat against its controls.

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
    lm        Levenberg-Marquardt, Nielsen damping
    mesh      the 468-vertex template, regions, measurements
    meshdist  signed distance + closest point, for contact
    raster    a tiny depth-buffer rasteriser (visibility, silhouette, occlusion)
    shape/    20-mode anthropometric basis + free-form nose displacement field
    facemodel THE boundary type: what a scan produces
  enroll/     the four-second scan and its bundle adjustment
  track/      PnP against the scanned model; One Euro (off)
  fit/        contact-physics seat, frame assets, verdicts and optician advice
  detect/     landmark adapter + per-landmark uncertainty
  render/     three.js; convert.ts is the ONLY CV↔GL conversion in the tree
  app/        the browser shell: frame lock, camera, UI
  testkit/    synthetic population, ground-truth metrics, the three reports
```

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
7. **One Euro's four lessons**, kept written down even though the filter is now
   off.

---

## What this build cannot tell you

Ten open questions, in [`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md). The
honest headline is the same one v1 carried:

> **Nothing here has ever seen a real camera.** There is no webcam on the machine
> this was written on. The camera path is exercised as far as `getUserMedia` and
> no further.

The synthetic capture model was wrong twice during this build, both times in ways
that changed an engineering decision — occluded landmarks are *biased*, not
merely noisy; real heads wander smoothly rather than teleporting. Both were
caught by suspicion rather than by the harness. There will be others.

The four that need you: a real camera session (Q8), a detector-bias calibration
against scanned faces (Q2), caliper measurements of the eleven real frame assets
(Q10), and a licence click for FLAME 2023 (Q9).

---

## Privacy

Your scan is solved on your device and stored in your browser only. Nothing about
your face is uploaded. "Delete my measurements" deletes it.
[`docs/PRIVACY.md`](docs/PRIVACY.md) — including a real problem in v1's tree that
this design avoids by construction.
