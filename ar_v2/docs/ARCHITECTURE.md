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
  it as depth. Measured here: fitting the average head swings the bridge's depth
  error by **5.1 mm** between frontal and turned. Fitting the scanned model
  swings it by **0.37 mm**.
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

Four seconds of guided capture, one joint bundle adjustment.

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

**Two model layers, deliberately.** A 20-mode anthropometric basis for the head,
and a **free-form per-vertex normal displacement** for the nose. A low-rank basis
— anthropometric or PCA — is structurally bad at noses: they are small,
high-curvature, and carry the population's most between-group variation, so the
leading components smooth them toward the mean. Swapping one average nose for a
slightly better average nose does not fix v1's problem. The field is what
actually measures this person: measured, it cuts nose surface error from
0.99 mm to 0.35 mm.

### 2. Tracking — `src/track/`

Six numbers against known geometry. `tracker.ts` is ~290 lines against v1's
2,550, and the difference is entirely things that no longer need to exist: no
shape estimation, no seat search, no identity question, no trust ramp, no gate,
no per-frame placement.

There is deliberately **nothing in the tracker that mentions yaw.** The reported
symptom was a consequence of solving shape and pose together; with shape frozen,
PnP holds 0.42° of median rotation error at frontal and 0.93° at 60° — measured
across the population and the camera ladder. Adding a yaw term would be treating
a symptom that no longer exists.

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
  88°.

Because it is solved once and cached, **nothing in the per-frame path can make
the frame walk up and down the nose, shimmer, or behave differently at 40° of
yaw. There is no per-frame placement left to be wrong.**

### 4. Verification — `src/testkit/`, `tests/`

Three reports and 64 tests, all headless, all against a synthetic population with
known ground truth.

The population exists because v1's real finding was not that its constants were
wrong — it was that six in seven were **one person's number**, so nothing could
distinguish "this works" from "this works on Shay". Every accuracy claim here is
a distribution across 17 subjects and 3 camera geometries, reported as
median/p90/worst.

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

`render/` and `app/` own the browser. `render/convert.ts` is the **only** place
computer-vision convention (+Y down, +Z forward) becomes GL convention (+Y up,
−Z forward), and that rule is why nothing in this tree has a lone minus sign on a
Z that needs a paragraph of comment to justify.

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

## Measured results

See `README.md` for the current tables. The three headlines:

| | v1-equivalent | v2 |
| --- | --- | --- |
| Depth swing frontal → turned | 5.11 mm | **0.37 mm** |
| Pad depth error (landmark-hung vs contact-solved) | 1.47 mm | **0.46 mm** |
| Nose surface error | not measurable | **~0.8 mm** median |
