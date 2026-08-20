# Open questions

Things this build cannot answer from inside itself. Each one names what is
currently assumed, what it would take to settle it, and what it is worth.

The list is short on purpose. v1's audit found 196 constants of which only 32
rested on physics or geometry, and the reason that number got so large is that
"we should measure this someday" was never written anywhere a reviewer would see
it. Anything in this file is a number the system is currently guessing.

---

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

## Q3 — Should the card protocol ship?

**Assumed:** not implemented. `enroll/scale.ts` has the interface and the
solver; there is no card *detector*.

**Why it matters:** the iris ruler is biased by ancestry (see below) and carries
±4–5% of scale uncertainty. A standard ID card is ±0.14%. Measured on the
synthetic population, using the true iris instead of the pooled constant took
scale error from 3.4% to 0.3% and nose surface error from ~1.4 mm to ~0.8 mm.

**To settle it:** implement quad detection for a card held at the brow — a small,
self-contained vision problem. Then decide whether the five seconds it costs the
wearer is worth it, which is a product question rather than an engineering one.

**Caution:** the incumbent holds five worldwide patents covering iris/frame/ear
detection and a card protocol for ocular measurement. Get a
freedom-to-operate read before shipping this commercially.

**Worth:** high for anyone ordering lenses; low for browsing.

---

## Q4 — What is the compliance of nasal skin under a pad? **(needs you)**

**Assumed:** 1.0 N/mm combined across both pads (`fit/contact.ts`, `SKIN`).

**Why it matters:** it sets how deep the pads sit and therefore, weakly, the
resting height.

**Derived, not measured.** The derivation is in the file: the sidewall normal is
24% vertical, a 24 g frame weighs 0.235 N, so ~1 N of normal force is needed if
the nose carries all the weight, and pads visibly compress skin by about a
millimetre. That is one observation of one nose with arithmetic attached.

**To settle it:** a force gauge, a pad, and ten noses. Genuinely an afternoon.

**Worth:** low, and the harness says so — sweeping it across a factor of four
moves the settled height by about half a millimetre and changes no fit verdict.
It is on this list because it is the least-supported number in the tree, not
because it is the most consequential.

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

---

## Q8 — Nothing here has ever seen a real camera. **(needs you)**

**Assumed:** that the synthetic capture model is representative.

This is v1's open item, inherited verbatim, and it is the honest headline of this
whole build: *there is no webcam on the machine this was written on.* The camera
path is exercised as far as `getUserMedia` and no further. Everything downstream
is covered by synthetic fixtures.

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

**Done:** the bundle now solves in a module worker (`enroll/enroll.worker.ts`),
so the main thread stays responsive through the scan. Verified end to end in a
browser: `ranOn: 'worker'`, and the model round-trips through the same
`serializeFaceModel` path a returning wearer uses — so a format bug cannot hide
until somebody reloads.

**Not done:** the *detector* still runs on the main thread, synchronously, at
~20 ms a frame. v1 put it in a worker and measured the trade carefully: a worker
gets its own GL context, which on some machines makes inference genuinely slower
(34 ms against 14 ms on the machine v1 shipped on), so it shipped a measured
one-way fallback rather than a blanket choice. That whole apparatus should be
ported — it is a solved problem sitting in `ar/src/tracker.js` with its
reasoning attached.

**Worth:** moderate. Under the frame lock the display advances once per
detection, so a 20 ms main-thread inference costs UI responsiveness rather than
mirror rate — which is the cheaper of the two coins, and exactly the trade v1
documented.

---

## Q12 — The seat's falsifiability control is too weak.

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

---

## Q10 — No real frame assets have measured geometry.

**Assumed:** five parametric frames generated from numbers
(`fit/frame-asset.ts`, `TEST_FRAMES`). Every one declares
`dimensionSource: 'assumed'`.

This is v1's problem restated: nine of its eleven catalogue frames declared
`widthSource: 'assumed'` because their geometry had been normalised to a 140 mm
placeholder. It is an **asset pipeline** problem, not an algorithm problem, and
no amount of solver quality survives it.

`ar/assets/glasses/` has eleven real `.glb` files. `derivePads()` will find their
pad surfaces. What is missing is the true physical size of each — a caliper
measurement of the front width, and ideally the mass.

**Worth:** high, and it is the cheapest high-value item on this list. Eleven
frames, a caliper, twenty minutes.
