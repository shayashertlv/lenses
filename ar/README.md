# AR try-on

A working end-to-end pipeline for wearing a 3D pair of glasses on a live camera
feed. Prototype: the goal was a correct, legible pipeline that can be made good,
not a good pipeline.

Runs entirely in the browser, entirely on-device. No frame leaves the machine, and
nothing here talks to the Lenses backend.

## Running it

```bash
python ar/serve.py
```

Then open <http://127.0.0.1:8765/>. It starts on a sample face; switch **Source** to
*Camera (live)* to use a webcam.

Bind to localhost, not a LAN address — browsers only grant camera access in a
secure context, and `localhost` counts as one while `192.168.x.x` does not.

There is no build step and no `npm install`. Dependencies are vendored under
`vendor/` and resolved by an import map, matching the no-bundler constraint the
rest of Lenses works under. It runs offline.

Two startup notes, both of which used to make the app *feel* slow before a face ever
appeared. The server sends `Cache-Control: no-cache` — revalidate, don't re-download
— so after the first visit a reload costs a handful of 304s instead of ~40MB of wasm,
model and glasses. And the glasses load in **parallel** with the camera and tracker:
tracking starts the moment there is a face, and the frames appear when they arrive,
instead of a 24MB download standing between the user and their own reflection.

## How it works

```
camera frame ─► snapshot ─► face landmarker ─► pose ─► head node ─┐
   (30 fps)        │           (in a worker)                      ▼
                   └───────────── shown together ────────► composite ─► screen
                                 (frame-locked)
```

**The rule everything hangs off: the face and the glasses share one clock.** This is
*video* AR — the user is looking at a video of their face, not at their face — so
the only alignment that matters is between the glasses and *that video*. Each camera
frame is snapshotted at submission, detected on, and shown **only together with its
own pose**. The composite is exact at every velocity by construction, and the whole
pipeline latency collapses into the mirror running ~50ms behind the room, which is
invisible. (An earlier architecture showed the newest camera frame at 60fps while
posing the glasses from an older, detected one *predicted forward to display time* —
correct for see-through AR, where the world has zero latency, and exactly wrong
here: two clocks, and every head movement sheared the glasses across the face. The
`mirror delay` readout shows the price actually paid instead: capture-to-composite,
measured, ~35–65ms.)

**The idea that makes it cheap.** MediaPipe returns a 4x4 matrix that maps its
*canonical face model* — an average human head, 468 vertices, in centimetres — onto
the face in frame. That mesh is the bridge between the model's world and the real
one. Solve the placement once, in the canonical head's own coordinates, and the
matrix carries it onto the real face at every pose, distance and angle for free.

So fitting is not a per-frame search. It happens once, at load, in `fit.js`. The
per-frame cost is a matrix multiply.

**Where the frame sits on the model** is derived from the model's own geometry, not
hardcoded per asset. Real glasses are carried by the nose, so the solver looks for
the pad contact point: take a narrow vertical column through the centre of the
frame — which catches the bridge and pads while the lenses and temples fall
outside it — and average the rearmost geometry in it.

**Where it sits on the face** is measured from that face, not assumed. This matters
because the head node poses the *canonical* head — an average of everybody — so
anchoring to it puts the glasses where an average person's features are. `anchors.js`
recovers the individual: for landmark *i*, take canonical vertex *i*'s depth, cast a
ray through the **observed** landmark, walk it to that depth, and map it back into
face space. The result lands on this person's actual feature.

Three measurements come out of that, and each drives something:

| Measured | Drives |
|---|---|
| Nose bridge (x, y) | where the pads rest — and horizontal centring, so an off-centre nose still gets a centred frame |
| Eye line (y) | the frame's height, so the pupils sit ~45% down the lens |
| Temple width | frame size in *Fit to face*, and the width verdict in *True size* |
| Nose width (x) | how far off the face the frame comes to rest — a broader nose is a wider wedge, so the same pads meet it sooner |
| Ear tops + cheeks (y, z, per side) | where each temple arm comes to rest, and where the ear occluders sit — ears are measured per side because real ears are asymmetric |

**Height is set by the eyes, bounded by the nose.** The nose bridge alone is not
enough, for two reasons. Its landmark carries a fixed offset against the canonical
mesh — both sample faces measure ~1.5mm low *by the same amount*, which is the
detector disagreeing with the average head, not two people who share a nose. And
the *model* side of the anchor is a guess about the asset: a frame with pads
modelled separately lands within a couple of millimetres, while one whose bridge is
a single bar can be 15mm out.

The eye corners have neither problem, and pupil height is what an optician actually
fits to, so the eye line sets the height and the nose bridge only bounds it. The
bound is deliberately generous (15mm) because real nose pads are adjustable — that
slack is a physical fact about glasses, not a fudge.

**The frame leans forward, and the sign is not obvious.** Real frames sit at a
pantoscopic tilt of roughly 8°: the lens plane leans so its normal points slightly
*down*, meeting the wearer's downgaze, which puts the bottom rim nearer the cheeks
than the top. Face space is +Y up and +Z out of the face, so that is a *positive*
rotation about X. Negating it — which is the natural thing to write, since the frame
is "tilting forward" — gives the retroscopic opposite, bottom rims flaring off the
cheeks and top rims into the brow. Nothing else in the pipeline notices, which is why
there is now a check on it.

**The frame rests on the nose, rather than hanging off a landmark.** Height, width
and centring all say where the frame sits *on* the face. None of them says how far
*off* it — and that axis was simply being guessed: put the model's pad contact point
on landmark 6 and let the rest follow.

A landmark is a point and a nose is a wedge, and the wedge is steep. On the canonical
head, at bridge height, the skin 7mm off the centreline sits **5.5mm further back**
than the skin on the centreline. So a frame with pads 14mm apart, hung by a centreline
point, is 5.5mm wrong at both of them — and which way depends entirely on how the
asset happens to be modelled. Measured over every vertex of all four frames:

| frame | hung from the landmark | seated on the surface |
|---|---|---|
| Acetate (AI scan) | 4.2mm clear of the face | 0.5mm of pad sink |
| Crystal acetate | 3.9mm clear | 0.5mm |
| base.obj | 1.5mm **inside** the face | 0.5mm |
| Khronos sunglasses | 1.7mm **inside** | 0.8mm |

Two of them float in front of the nose, two are buried in it, and nothing in the
pipeline noticed: the pose is right, the height is right, the width is right.

[nose.js](src/nose.js) closes it. The canonical mesh is rasterised once into a front
depth field — a height map of the face at 1mm cells — and the frame's own back-of-
bridge geometry is sampled into a couple of hundred points. The solve is then one
line: push the frame out along z until nothing is inside the face. Where it comes to
rest is whatever reaches the skin first, with no assumption about which part of the
model is a pad; on all four frames here that turns out to be the nasal sidewall,
8–9mm off the centreline, which is exactly where a pad belongs.

Half a millimetre of interference is deliberate. A pad is a plate on soft tissue, and
at exact tangency the frame reads as hovering — no two surfaces ever quite meet on
screen.

Three properties make it safe to compose with everything else:

- **It only moves z.** Vertex distance is the one placement variable that changes
  nothing about where the pupils fall in the lens, so the seat cannot undo the height
  solve that ran before it. Asserted: seating moves the frame 0.0mm in x and y and
  leaves pupil height unchanged to 1e-9.
- **The standoff slider adds to it.** Applied *before* the seat — the obvious place,
  next to the other two offsets — it would be solved straight back out again and the
  control would do nothing. After it, zero means "resting on the nose" and the number
  is vertex distance, which is what an optician measures.
- **It reads the whole bridge column, not just the pads.** On these four frames the
  pads always win, but that is a fact about these frames. Bound the samples to the pad
  line and a frame with a deeper brow bar would rest on the pads while its top sank
  into the glabella — with the solve reporting a perfect half-millimetre of contact.
  The harness checks every vertex of the model, not the samples the solve looked at.

The nose it is seated against is *this* nose, sideways. Nose width is measured from
two sidewall landmark pairs and the depth field is stretched to match, which matters
more than it sounds: the two sample faces are 7% and 10% **wider** than average and
their noses are 11% **narrower**, so a nose scaled off face width would have been out
by 20% on both. Across the plausible range it is worth 5.6mm of standoff.

Depth is still the canonical head's — a single camera cannot recover it — so this
places the frame against an average nose *profile* scaled to a measured nose *width*.
That is a real limitation and it is the honest ceiling for one camera.

**The frame slides along the nose, not up it.** The bridge slopes back towards the
brow — 0.82 up per 0.57 back on the average face. Lifting the frame vertically for
pupil height therefore walks it off the front of the nose: a 1cm lift leaves the
pads 7mm clear of the surface, and the frame visibly floats. The correction runs
along the measured bridge direction instead, so the pads stay in contact. Measured
after the fix: **0.0mm off the bridge line**, having slid 13–14mm along it.

**The temple arms get their own hinges.** Rigidly transforming the whole model
leaves the arms wherever the modeller put them, and on `base.obj` that was **1.0–1.6
cm inside the skull**, passing 3cm below the ear. The occluder then correctly hid
them and all that survived was a stub by the temple. Worse, tilting the front for
pantoscopic angle swung the arms through the same angle — backwards, since on real
glasses the arms stay level and run to the ears while the *front* tilts relative to
them.

So [temples.js](src/temples.js) splits each arm into its own node pivoted where it
meets the front, and aims it at that face's ear — the same adjustment an optician
makes by hand. Aim by the arm's straight run, not its tip: most arms hook downwards
at the end, so pointing the tip at the ear levers the whole arm about a centimetre
over the top of it.

**Where the hinge goes matters as much as the angle.** On a welded single-mesh
asset the split is a guess — a depth cut through geometry that has no seam — and a
guess lands the pivot part-way along the arm, so the arm rotates about its own
middle and *reads as bent*. An asset modelled in parts already knows the answer:
the boundary between the temple part and the frame part is the hinge, the same
place a real pair bends. So parts are used when they exist. Telling an arm from a
lens rim is not about which is further out — both reach the full width of the
frame — but about how far *inboard* each reaches: the rim carries on to the bridge,
the arm never leaves the side of the head. That test also catches the pieces a
centre-of-mass test misses, like a gold hinge segment sitting forward alongside the
rim. A model with no usable parts falls back to the depth cut.

Having found the joint, the pivot has to sit **on** it. Averaging the frontmost
tenth of the arm to locate the hinge sounds harmless and is not: on a 130mm temple
it puts the pivot 5mm behind the joint, and every millimetre behind the joint is a
millimetre the joint itself travels when the arm swings. The frame does not follow
it, so a step opens where the arm leaves the frame and the bend appears *there*
rather than at the hinge. A tight band — a couple of millimetres of contact face —
cuts that travel from 1.4mm to 0.49mm at a 15° swing, and it sharpened the aim on
every other frame too: the arms now cross the ear rest point within 1mm instead of
3–4mm.

**The aim takes the ear's width exactly, but its height only within ~8° of
level.** Aimed dead at the measured rest point, the whole arm pitches to chase it —
and the ear-top landmarks sit at the hairline, routinely under hair, so the
measured height can land centimetres off. On a live face that beeline read as a
frame bent at the hinges, arms diving off the front, while the model itself was
perfectly straight. Real arms do not do this: they run essentially level and the
*tail* is bent to meet the ear, so the clamp keeps the run level and leaves the
height error to the hook, which is where an optician puts it.

MediaPipe's mesh has no ear *geometry* — its rearmost point is only 2.4cm behind
the cheek — but it does track ear-top and cheek **landmarks**, so the rest point's
height and depth are measured from this face, per side, and only the outward reach
is extrapolated from the measured width. Ears are among the most variable features
on a face; aimed at the *average* ear, the arm visibly crossed a real one
mid-pinna.

**The ear hides the arm, not the other way round.** Real glasses tuck behind the
ear, and this is where the illusion most visibly broke: the face-mesh occluder
stops at the silhouette, so the arm — correctly placed behind the ear in 3D — was
painted straight across it. Each ear now gets an invisible depth-writing pinna
(`head.js`), seated on the skull at the measured rest point, so the arm slides
behind it exactly as it slides behind a real ear.

**The arms press into the temple and clear at the ear, which is what a narrow frame
does.** A 140mm front on a 165mm head puts each hinge **12mm inboard of the temple**,
so the straight run from hinge to ear passes *through* the flesh there — and being
hidden for that stretch is correct, because on a real head it is behind the head's
own outline. What must never happen is the arm being inside the head at the *ear* end
too, because then it is swallowed for its whole length and the frame arrives with no
temples at all. So the run is sampled against the head's own half-width over its last
quarter and the temple opened at the hinge by the smallest angle that clears it — the
second adjustment an optician makes by hand.

Asking for that clearance along the *whole* run is the obvious reading and it was the
wrong one, for a reason that is pure geometry: the angle a clearance costs is the
shortfall divided by how far along the arm it occurs, so an obstacle 6cm out demands
half again the angle of the same obstacle at 9cm — and that angle carries on to the
tip. Requiring clearance at the temple bone stood the tip **12mm** proud of the head,
and a temple standing off the head is exactly what sweeps across the cheek at selfie
range. Requiring it only from the ear back brings it to **3mm**.

**There is no scan phase.** There was, and deleting it was the single largest
improvement in this app's history — so it is worth recording what it was and why it
was wrong. The fit used to require 45 head-on samples before it was declared final,
with *"face the camera and hold still a moment — measuring your fit… 34%"* on the
status bar throughout, and a **full restart** after every half second the face went
missing. That is 1.5–3 s in the best case and unbounded in the ordinary one, repeated
several times a minute, and it was what "the glasses take forever to get into
position" actually meant.

Nothing required it. MediaPipe's transform is a *stateless* closed-form weighted
Procrustes fit of the canonical head onto 34 landmarks — every quantity the scan
collected is available, at full quality, on the very first detection. What 45 samples
bought over 15 was a standard error of 0.187σ against 0.324σ: three times the wait for
1.7× the precision, on a measurement whose residual is dominated by bias that
averaging cannot touch. Nobody who ships this ships the gate either — Fittingbox's
start gate is ten stabilisation frames, Ditto's patent puts the sufficient sample at
"on the order of 10 frames", and WebAR.rocks carries no per-user state at all.

So the division of labour is unchanged and the timing is gone:

> **Proportions from the estimate. Position from the landmarks. Orientation and scale
> from the pose. Standoff from the nose's surface.**

The *proportions* — this face's widths, its ear geometry, how its eye line sits
against its bridge — come from a **bounded running window** of the last 31 head-on
samples, which starts at the first frame and never stops. *Head-on* is still enforced
in both axes, and those gates are the honest ones: a turned head foreshortens the
temple span (yaw gate), a pitched one puts every borrowed canonical depth at the wrong
height (pitch gate). They gate the *measurement*, not the user. **Re-measure face**
starts over.

What the lock really provided — that the frame stops breathing on the face — is now a
**deadband**: half a millimetre on a 155 mm temple span, two tenths on a 23 mm nose,
one percent of head size. Early samples move the estimate freely because the
differences are large; within about a second the median has converged inside the band
and stops moving it at all. Asymptotically a lock, with no cliff, no gate, and nothing
that can restart.

The *position* — where the bridge and eyes actually are, this frame — is
re-measured from the observed landmarks on **every** frame, and this line was drawn
in blood. The obvious economy is to lock the position too and let the rigid pose
matrix carry it, and that is exactly what shipped first: on a real face at a mere
±10° of yaw, the rigid canonical fit placed the eye region **12–15 mm sideways** of
the observed eyes (measured on real session frames — mean mesh reprojection was a
healthy 1.2 % while the eye-region residual was 3× that, because a similarity fit of
an average head to all 468 points compromises exactly where individual geometry
concentrates). Worn rigidly, that error made the glasses slide across the face with
every turn — while the landmark-measured anchor stayed within 2 mm of truth at the
same poses. So the anchor point tracks the landmarks, the estimated proportions ride
it, and the harness pins both halves: position follows a landmark shift, size does
not breathe with it.

**The ears are the exception, and gating them was a bug fix.** Ear height and depth
were live like the bridge, and ungated — while the sample window that feeds the fit
has always refused to measure past ~15° of pitch, for a stated reason that applies
just as hard to the live path: the anchor recovery borrows each landmark's
*canonical* depth, so a pitched head takes every borrowed depth at the wrong height,
and worst of all at the ear tops, which sit furthest from the face's centre of
rotation and under hair besides. The fit therefore held still while the arms chased
an ear that slid up the head as the wearer looked up. Outside the gate the carried
estimate is used — what the window measured while the pose could still be trusted.
The bridge stays live at every pose and must: it is a front-of-face landmark the
same recovery handles well, and it is what pins the frame to this frame's features.

**The estimate stands on its answer instead of easing toward it.** It is the per-field
**median** of the window, not a low-pass over it — and the difference is what used to
make the app *feel* slow even before the gate was removed. The 0.3–0.4 Hz filters
before it took seconds to converge and were visibly on their way there the whole time:
the glasses slid slowly into place on a new face. A median has no time constant — ten
samples in it already sits at the middle of what was measured, it does not drift while
collecting, and one wild frame (a blink, a hand, a detector glitch) cannot move it,
which is the property the filters were really there for.

The samples are taken against the *raw* pose rather than the smoothed one. The
measurement works by inverting the head pose to carry an observed landmark into face
space, so it is only self-consistent if it inverts the pose those landmarks were
actually solved against; inverting the smoothed pose instead folds the filter's lag
into every sample taken while the head is moving, and averaging does not remove a
bias that is consistent.

**Losing the face does not cost the fit.** It used to, after half a second, and that
timer was the reason the app felt like it was *perpetually* re-stationing itself:
every turn past the yaw gate, every hand across the face, every dropout the detector
happened to have, started another scan. Fittingbox's patented recovery state does the
opposite and is obviously right — on losing tracking they re-localise the *pose*
against a keyframe bank and never re-measure the wearer.

The thing the timer was guarding against is someone else sitting down in the chair,
which is a question about the face, so it is now asked of the face. A returning head
whose proportions land more than 12% from the estimate gets a fresh window; one that
matches keeps its fit and resumes instantly. Two independent signals feed it: shape
(`widthRatio` against the canonical head) and *absolute size* from the iris, which
catches the case shape alone cannot — an adult and a child proportioned alike.

What a long absence still discards is the pose filter's **velocity**, and only that. A
head speed carried across half a second describes a movement that finished long ago,
and on re-acquisition it throws the frame off the face until it decays. A one-frame
dropout is the opposite case — the commonest is a motion-blurred frame mid-turn, where
the accumulated velocity is worth the most — so short gaps hold it. Shorter still, up
to two consecutive faceless results are ridden out by simply *not advancing anything*:
the previous frame and its own pose stay on screen. That costs ~60 ms of a frozen
mirror and does not break the frame lock, because the pair on screen is still a frame
shown with the pose solved from that same frame. It is the only option that stays
inside the invariant — advancing the video while holding the old pose is the two-clock
mistake again, and popping the frame off the face is a flicker on every hard turn.

Turn **Adapt to face** off to fall back to the average head and see the difference.

**How big it is** has two modes, and the difference matters commercially:

- **True size** renders the frame at its manufactured width. A 150mm frame stays
  150mm, so it looks too wide on a narrow face — which is the honest answer, and
  the only one that makes a size recommendation mean anything.
- **Fit to face** rescales the frame to span whatever face is in front of it.
  Always flattering, always a lie about fit. Useful for models authored at
  arbitrary scale, which many are.

**True size is less true than its name, and now we can say by how much.** Face space
is measured in the *canonical* head's centimetres — the head node poses a 15.49cm-wide
average skull onto whatever face is in frame — so a 140mm frame drawn at 14.0
face-space units spans 140/154.9 of the head *whoever is wearing it*. Every face gets
the same verdict from the one mode whose purpose is to give different faces different
ones.

That is a real bias and it has an available fix. The iris is the one absolute ruler on
a face: 11.7mm across on essentially every adult, so a span measured against it *in
the same image* comes out in real centimetres, with no camera model, no distance and
no field of view involved. `measureMetricScale` does exactly that at the iris plane —
where both pupils sit in the same plane as the ruler measuring them, so there is no
perspective term to get wrong — and the app reports the result as `pupils` (59mm on
both sample faces) and uses it to tell one wearer from another.

It is deliberately **not** wired into the sizing yet, and the reason is a measurement
rather than caution. `templeWidth`, the span the verdict is calibrated against, is the
distance between two silhouette landmarks rather than anatomical head breadth:
converted into real units it puts both sample faces at 171–175mm across, where a human
head is 145–155. The scale factor is sound — the pupil distances behind it are
plausible and the two faces differ the way two faces should — but the *thresholds* it
would feed were tuned in the old units, and re-basing them needs real frames on real
faces rather than two sample photographs. Shipping half of it would swap a known,
uniform bias for an unknown, uneven one.

**An asset that coloured its own reflection keeps it.** The anti-reflective coating in
`matchTransmissiveRender` is filled in only where glTF's default says *nothing was
stated* — a white specular colour. A mirrored sunglass states gold, and an AR coat is
the precise opposite of what it is asking for: AR layers exist to **suppress**
reflection, a mirror coat to maximise it. Fitting one over the other turned a gold
mirror lens green, over a material that had said plainly what it wanted.

**A size limit nobody wrote down, expressed as a crash.** `findHinge` and its two
neighbours took the arm's z extent with `Math.max(...tris.flatMap(...))` — three
arguments spread per triangle, into a call that has an argument limit. Every frame in
the catalogue stayed under it until a 196k-triangle shield arrived with 90k of that in
its temples, and the app died on `Maximum call stack size exceeded` at the moment the
arms were split. It is now a loop. Worth remembering that a spread over per-vertex data
is a latent ceiling on asset complexity, not a style choice.

**A translucent frame still has to cast a shadow, and nearly did not.** Only genuinely
*clear glass* is exempt from casting the contact shadow, because three's shadow pass
has no notion of transmission — a 92%-transmissive rim was laying down the same shadow
a block of wood would. Testing `transmission > 0` for that was too blunt on two counts,
both caught by adding sunglasses to the catalogue. A **tinted** lens passes light and
still casts a strong shadow — that is what makes sunglasses sunglasses — so colour has
to come into it. And a frame whose transmission is *modulated by a texture* declares a
factor of 1 while actually passing about half, so the factor alone lies about it. The
sage acetate is a single translucent material covering the whole frame, and under the
old rule it went from a 1.4% contact shadow to 0.09%: pasted on.

There was a second, subtler half to the same bug. `matchTransmissiveRender` forces
`side = FrontSide` so a transmissive mesh does not refract its own back faces — and
three *derives* `shadowSide` from `side` when it is left null, with the mapping
inverted, so a FrontSide material casts from its **back** faces. On a scanned shell
those are the inside, with whatever winding the scanner produced. Measured on two
frames of the same family and size, one opaque and one translucent acetate: **0.61% of
the portrait carried a contact shadow against 0.09%**. Naming `shadowSide` explicitly
took the translucent one to 0.27% — lighter than the opaque frame, which is correct,
and no longer absent.

**Occlusion is a head, not a set of proxies for one.** This is the part that had to
be rebuilt, so it is worth being exact about what was wrong. MediaPipe gives a
*face*: 468 vertices ending at the silhouette, rearmost point 2.4cm behind the
cheek, **no ears**. A temple arm runs 12cm back and spends ten of them past the last
vertex that mesh owns. The occluder used to cover that gap with an ear ellipsoid per
side and a braincase ellipsoid held deliberately *inside* the arms so it would not
eat them — and traced through face space, the union of all three covered **2.8cm of
a 12cm arm**, in a band sitting 2cm in *front* of the ear, over the cheekbone. So
the arm was cut in the middle of the cheek and what survived read as a temple driven
into the side of the face. Both reported artefacts — "the temples go into his face",
"the temple disappears mid-way" — are that one fact seen from two angles.

The fix is to stop approximating and build a head, and the mesh hands over the means
to do it: **its boundary is a single closed 36-vertex loop around the face oval, and
lofting that loop back to an occipital pole closes the skull.** The loft shares the
rim's own vertices, so the result is watertight with the face by construction —
there is no seam for an arm to appear through, and no clearance constant deciding
which artefact to keep. From the rim forwards the head *is* MediaPipe's face,
tracked exactly as before; behind it, a skull that keeps the face's width to the ear
(a superelliptical sweep, not a circular one — a circle is already 7% narrow by the
depth of the ear, and 7% is the whole clearance an arm has) and closes 21cm from the
nose tip. 865 vertices, 1726 triangles, built once.

Ears are the one thing the loft cannot supply, and they cannot be blobs. A pinna is
a flap standing off the skull and **the crevice behind it is where the temple runs**;
model it solid and the crevice fills in, so the arm is inside the ear and vanishes in
front of it. Each ear is therefore an open dish, rim seated on the skull, apex 1.9cm
proud — it hides the arm from the side the way a real pinna does and leaves the gap
for it to run through. Drawn double-sided, all of it: a shell has a far side, and on
a hard turn the ray that must be stopped enters through it.

**The head takes the measured width, and leaving it out was a bug rather than a
simplification.** MediaPipe's transform carries no scale, so on a face 7% broader than
average the occluder's silhouette falls ~6mm inside the real one at each temple —
while the *ear rest point the arms aim at* is that same canonical half-width times the
measured ratio. The two then disagree by exactly the width ratio: the arm is routed
7mm outside the head it is meant to lie against, and at selfie range that standoff
projects into a centimetres-long streak of temple drawn across the cheek. Measured on
a real capture: head at the ear 7.61cm, ear anchor 8.31cm.

Widening the whole head is what was tried first and it does not work. The face's
surface recedes at ~2.3cm of depth per centimetre of width, so a uniform stretch walks
the occluder *forwards* as well as outwards — and near the nose, where the frame's own
pads sit half a millimetre off the skin, it drove the occluder straight through them.
It ate **4% of the rendered frame and killed the contact shadow outright**. So the
stretch is held off the middle of the face entirely and ramps in between 3cm and 5.5cm
from the centreline: outboard of the pads, the bridge column and the inner rims, and
fully applied by the cheekbone. The shadow catcher shares the occluder's geometry
*instance* rather than a copy, because the two must be the same surface to within
nothing at all — an occluder a tenth of a millimetre in front of the catcher culls the
shadow entirely, which is how the shadow disappeared the first time.

**The arms are faded by depth, not cut.** The last piece, and the one that makes the
rest robust: an arm that stops dead has to stop somewhere, and wherever that is, a
viewer reads it as the end of a solid object — on a face, as a spike driven into the
cheek. The head proxy only has to be a few millimetres narrow, or the hair a
centimetre thick, for the cut to land on skin. So each arm dissolves with its own
depth behind its hinge (a `branchFadingZ`, in WebAR.rocks' terms), which costs nothing
where the arm matters: head-on it recedes ~1cm of depth per centimetre of run and the
head is hiding it anyway; in profile it crosses the view at ~1cm of depth and never
reaches the threshold. The range is bounded from below by the 45° case, where the arm
reaches the ear at ~6.6cm of depth and has to still be there for the ear to tuck it
away.

**A lens needs something to reflect, and there was nothing there.** The frame with
modelled lenses rendered as a frame with *empty rims*, and every property of that
lens was correct while it did: transmission 1.0, ior 1.52, a `MeshPhysicalMaterial`,
`USE_ENVMAP` defined, the environment present. Measured on a real capture, scaling
its environment reflection **twentyfold changed one pixel**.

That is geometry, not a bug. A lens facing the camera reflects what is *behind the
camera*, and the scene had nothing there: `scene.environment` is a generic procedural
room with no bright feature behind the viewer, and the key sits ~30° off the view
axis — thirty times the width of a polished lens's specular lobe at roughness 0.02.
Raising roughness does not rescue it (still 16° short) and it is unavailable anyway:
on a transmissive material roughness is the mip level the refracted image is sampled
at, so it would frost the eye behind the lens.

What a real lens reflects in a webcam photo is almost always **the screen** — which
is exactly where the camera is. So there is a fourth light at the camera, aimed at the
head. It is the one light source in the room whose position this app actually knows.
It is deliberately weak: with light and view coincident the half-vector *is* the
surface normal, so a polished surface concentrates the lobe into the patch facing the
camera while a matte one barely registers it. Its floor is the highest of any light
here, because a screen is the one thing that does not go away when the room does:
somebody trying frames on in the dark is lit almost entirely by their monitor, and that
is exactly when the reflection is strongest.

**It shipped four times too bright first, and the way it was wrong is the useful
part.** A reflection strong enough to be *obvious* looked like a camera flash — a
blown-out white patch covering half the lens, hard-edged. Two things make that
inevitable rather than a matter of taste. A transmissive material has to opt out of
tone mapping to match the un-graded camera pixels behind it, so there is no filmic
roll-off left to catch a bright specular: "clearly visible" and "clipped to flat
white" are the same setting. And the target was wrong anyway — the physics is not
"glass reflects 4%". Every modern lens carries a multi-layer **anti-reflective
coating** whose entire purpose is that it does not: the layers pass nearly all the
light and leave a soft *coloured* residual — green, blue or yellow-green depending on
the stack. That tint is the most recognisable thing about a coated lens, and it is why
a white highlight reads as fake however well it is placed. Nobody's glasses reflect
white.

So the lens is modelled as coated: `specularIntensity` down to 0.4 and `specularColor`
tinted green, which are exactly the right controls because they *are* the dielectric
F0, which is what a coating modifies — and neither touches what the lens transmits. A
second, much broader `clearcoat` lobe stands in for the fact that the thing being
reflected is a screen a foot across rather than a point. It has to be clearcoat rather
than roughness: on a transmissive material roughness is the mip level the refracted
image is sampled at, so raising it would frost the eye behind the lens.

**The glass carries its own environment, and finding out why took a while.** A lens
reflecting a single light looks the same wherever the head goes, which is the
difference between glass and a decal — a real curved lens catches something different
on every part of itself and changes as it moves. The environment map is what supplies
that, and it appeared to contribute nothing: scaling the lens's `envMapIntensity`
twentyfold changed *one pixel*. The reason is in `WebGLRenderer`:

```js
if ( ( material.isMeshStandardMaterial || … ) && material.envMap === null
     && scene.environment !== null ) {
  m_uniforms.envMapIntensity.value = scene.environmentIntensity;
}
```

A material with no `envMap` of its own has its `envMapIntensity` overwritten by the
scene's, every frame. Every material here relies on `scene.environment`, so the
per-material dial was dead for all of them — and untestable in the obvious way, since
turning it does nothing and that reads as "there is nothing to reflect". Pointing
`envMap` at the same texture restores it, and the glass then reflects the room four
times harder than the level chosen to *light* things by, which is what brings the
walls and lit panels up out of the noise.

The honest ceiling is that it is reflecting a *generic* room. A real lens reflects the
one the wearer is in, and closing that means an environment map estimated from the
camera feed — exactly the problem Apple's `EnvMapNet` addresses: a narrow field-of-view
LDR camera cannot see what a forward-facing lens reflects, so the environment behind
the viewer has to be inferred rather than sampled.

**Squaring up a scan has three degrees of freedom and a bounding box pins two.** Get
the *pitch* wrong and the width, height and depth all stay plausible while the lens
plane rakes over — `glasses01-with-lenses.glb` shipped once at a **20.7°** rake, which
reads as a frame hinged at a sharp angle to itself from any view off-axis, and no
other check in the harness moved. Two plausible references for "up" both fail on that
asset: its own +Y (every part sits on y=0, as though set on the ground) gives 20.7°,
and the temples — which run level on a worn frame — hook down at the tips, so their
long axis gives 24.7°. What works is the frame front itself: the flattest thing in the
asset, whose rake is a real known property, so up is chosen to put it at the **7.2°**
measured off the sister scan of the same frame. Solving that one constraint brings the
bounding box to 140.0 x 45.9 x 141.3 mm against the sister's 140.0 x 46.9 x 141.2 and
lands the quaternion within a degree of it — neither of which was constrained, and both
of which two captures on one rig should agree on.

**Lighting follows the scene.** A frame lit by a fixed studio key glows in a dark
room and reads grey by a bright window — nothing betrays a composite faster than an
object lit differently from the scene it claims to be in. Every 15th frame the
source image is downsampled and the face region's brightness and colour cast drive
the key, the ambient **and the environment map** ([lighting.js](src/lighting.js)).
The environment is the one it would be easy to forget, and the first version did: a
metal frame at metalness 0.95 is ~95% reflections, so dimming only the lights left
it glowing studio-bright in the dark — the exact giveaway the feature exists to
kill. Changes ease in over about a second so a passing cloud does not flicker the
frame, but a *source switch* re-primes outright — easing is for a lamp turning on
in the same room, not for changing rooms. Sampling the *face* region rather than
the whole image keeps a bright window behind the wearer from reading as a bright
face; the cast is applied at half strength, because the face's own colour is mixed
into it. Everything has floors, so the frame dims with the room but never
vanishes.

**The frame casts a shadow on the face.** The contact cue: without it the glasses
float in front of the face however exact the placement. The key light tracks the
head (so the shadow frustum stays tight and the resolution constant), the frame
casts, and an invisible copy of the face — a `ShadowMaterial` receiver, transparent
everywhere the shadow is not — catches. Drawn with depth-test on and depth-write
off, it can neither paint over the frame nor occlude anything.

It has to be *soft* and *light*, and the first version was neither. A frame sits
about a centimetre off the skin, and at that distance a real shadow's edge is
already diffuse; a pin-sharp one at a third opacity does not read as a shadow, it
reads as a second darker pair of glasses drawn on the face — which is exactly how
it looked next to a frame that had failed to render at all. Blurred (a 2048 map
buys the radius) and dropped to 0.18. The remaining honest gap: three's shadow map
takes no account of transmission, so a crystal frame casts the same shadow a solid
one would, where it should cast a much fainter one.

**Keeping up with the face** is a separate problem from smoothing it, and conflating
the two is what makes AR eyewear swim. Three things were each costing time:

**A filter with no velocity state must lag anything that is moving.** A first-order
low-pass trails a moving signal by its own time constant, `1 / (2π·fc)` — 133ms at
1.2Hz, for as long as the head keeps moving. One Euro raises the cutoff with speed,
but at the betas that keep a still head still it buys back a fraction of that. So the
pose now runs on a double-exponential predictor (Holt; LaViola's DESP in the tracking
literature, where it matched a Kalman filter at a fraction of the cost). It carries a
velocity, and a signal moving at a constant rate is a fixed point of it — no
steady-state lag at all. Measured on a head crossing frame at 25cm/s: **44.2mm behind
with the velocity term off, 0.00mm with it on**; turning at 92°/s, 15° behind against
0.00°. What is left is a transient after an acceleration, which decays — a much better
failure mode than a constant offset. The cutoffs went *down* at the same time, so it
also filters a still head harder than before.

The trend gain is not an independent knob, and getting that wrong rings. The
recurrence goes complex — overshoot, then oscillation — once `gamma` exceeds
`(2 − alpha − 2√(1 − alpha)) / alpha`, which for small alpha is `alpha/4` and stays
above it everywhere. So it is expressed as a *fraction of* alpha, and anything at or
under 0.25 provably cannot ring. Tuned as a free parameter it bounces for about a
second after every stop, which reads as the glasses jiggling on the nose.

**Detection was running on the display's clock, not the camera's.** A webcam delivers
30 frames a second; a display asks for 60. Detecting once per render therefore ran the
landmarker twice on half the frames — the same pixels, inferred twice — and told the
filter the head had moved that far in half the time it really took, which halves its
velocity estimate and everything built on one. `requestVideoFrameCallback` fixes both:
one detection per delivered frame, timestamped with when the frame was *taken*.

**And inference ran inside the render loop.** Profiled end to end, the frame budget is
not where intuition puts it: rendering the whole scene — shadows, transmission,
occluders — costs 0.1–1.5ms, the placement solve 0.1ms, and the landmarker ~21ms,
*independent of input resolution* (a 320×320 frame costs the same as 1280×1280, because
the model resamples internally). `detectForVideo` is synchronous, so on every camera
frame the render that was about to present waited 21ms — a stall at the camera's rate,
which reads as stutter no matter how good the resulting pose is. So the landmarker now
runs in a **worker** ([tracker.worker.js](src/tracker.worker.js)): frames cross as
transferred `ImageBitmap`s (~0.1ms of main-thread cost), results come back as two
transferred arrays, and the estimator absorbs the asynchronous hop for free because the
latency compensation already reasons from *capture* time. This is also the shape
Google's own face-landmarker samples now ship, and tasks-vision 1.0.1 provides a wasm
build made for it (`vision_wasm_module_internal`, which a module worker can import
directly).

A worker gets its own GL context, and on some machines inference there genuinely
costs more — 34ms against 14ms on the machine this shipped on, GPU delegate both
sides. So there is a fallback to the main thread, and getting its *decision* right
took three attempts, each of which shipped wrong first and was caught live:

- **It must not average in the warm-up.** The first inferences through a fresh GPU
  context include shader compilation — hundreds of milliseconds — and folded into a
  long-lived mean they read "the worker is slow" long after it stopped being true.
  With a one-way latch, every session on the machine above silently fell back at
  startup, wore the main-thread stall the worker exists to remove, and made three
  rounds of unrelated latency fixes feel like they had "changed nothing". The
  decision now skips the first five results, uses the median of the last fifteen,
  and will not decide inside the first four seconds or while the tab is hidden.
- **It must not believe a fictional camera.** The still-sample source used to answer
  "new frame" on every render tick — a 60fps camera that does not exist — and the
  decision once fired against that 17ms interval before the real camera was ever
  selected. The sample now delivers frames at 30fps, like the thing it stands in for.
- **The bar is 2× the camera interval, asymmetric on purpose.** A worker at 1.5× the
  interval still answers ~20 times a second while the main thread renders every frame
  on time; a main-thread tracker at even *half* the interval stalls the render loop
  on every camera frame. Measured live: main-thread inference of 14ms inside a 16.7ms
  frame budget missed vsync at the camera's rate — judder no fps average shows —
  while the worker at 34ms held **360 consecutive frames without a single missed
  vsync**. Falling back trades smooth video for denser detections, so the worker has
  to be clearly drowning first.

The `tracker` readout shows where inference is running and what it costs;
`?tracker=main` or `?tracker=worker` pins it, and `window.__ar` exposes the live
state for diagnosis.

**Three readouts exist to tell "we are slow" apart from "we are being starved",**
because until they did, the two were indistinguishable from inside the app and every
argument about latency was an argument about which one it was.

- `dropped / upstream` — camera frames the browser presented that we never received,
  from rVFC's `presentedFrames`, and how much of the mirror delay happened *before any
  of our code ran*, from `captureTime`. The first matters because the perceptual
  literature is consistent that dropouts cost far more than a uniformly lower rate;
  the second because it is the part no pipeline work can recover. A dash means the
  browser would not say, which is a measurement gap and not a zero.
- `lag bound` — how hard the pose filter's divergence ceiling is having to pull.
  Anything but 0% in ordinary use means the smoothing is mistuned, not merely insured.
- `pupils` — pupil distance in real millimetres, from the iris.

The camera's *agreed* settings are logged at startup too. `frameRate: { ideal: 60 }`
is a request, and a webcam that only does 30 — or that has silently dropped to 15
because the room is dark and it is lengthening exposures — says so nowhere else. That
number is the ceiling on everything downstream.

**The landmarker is fed 640px, not 720p, and the difference is free.** A camera frame
costs three full-resolution copies on the way to a pose: the snapshot the composite
displays, the `ImageBitmap` handed to the worker, and the texture upload inside it.
Two of those buy nothing — MediaPipe's face detector runs at 192×192 and the mesh on a
256×256 crop, whatever they are given, so everything above that is resampled away
inside the model. Measured on the same face at a spread of input scales, landmark
agreement is flat from 960px down to 320px (0.27–0.42mm, which is the noise floor, not
a trend), so the detection copy is capped at 640 on its long side for 61% fewer pixels
to copy, transfer and upload every frame. The *display* canvas is untouched at the
camera's own resolution — this is the detection path only, and it is drawn from the
snapshot rather than from the live element so the frame lock is unaffected.

That measurement needed care: `detectForVideo` carries tracking state between calls —
it re-uses the previous frame's region of interest — so feeding it a different image
size on each call measures the state churn instead. The first version of this check
read 0.19mm at 960 and 1.67mm at 800, which is the signature of a confound rather than
of resolution loss. Letting each scale settle for a few frames flattens it.

**The app asks for two faces, and never uses the second one.** MediaPipe filters the
landmarks internally when, and only when, `numFaces` is 1 — *"Smoothing is only
applied when num_faces is set to 1"*, from their own web guide — so our pose filter
was sitting second in a chain of two, reading a signal whose velocity had already been
damped by a filter with no knobs, no readout, and no way to measure it from inside.
Asking for two faces is the documented way to switch it off, and on a real head it was
worth more than any of the tuning below it. `?faces=1` puts it back, which is the A/B
this was decided on.

The price is the right to index `[0]`. Nothing documents the order of the returned
faces, so a second person in the room — or a portrait on the wall — could take the
glasses for a frame and hand them back. [pick-face.js](src/pick-face.js) chooses the
wearer by the *area* their landmarks span: whoever is being fitted sits at arm's
length from their own camera and everyone else is further away. Area rather than
width, because a wearer turning to profile loses width without getting any further
off, and a width test would hand their glasses to a more front-on bystander every time
they looked sideways.

**Why the pose filter still matters under the frame lock — and why it must sample at
lead zero.** The composite needs the pose *at the displayed frame's own capture*, so
any filter lag is drawn directly as slip on that frame. Under the frame lock the
glasses cannot lag the face *across* frames — but a filtered pose lags the true pose
*within* one, and that is drawn as the frame following the face a beat late.

That is where the last of the delay was hiding, and it was a one-line default. One
Euro raises its cutoff with the speed of the signal; ours was reading that speed off
the *filter's own trend estimate*, which converges at `trendGain × alpha` — about
0.035 per frame, a time constant near a second. Every head movement is shorter than
that, so the speed signal read ~0 throughout, `beta` had nothing to multiply, and an
adaptive filter behaved as a fixed 0.9Hz pole lagging by its own 177ms. The ramp test
in the harness passed the whole time, because a ramp is exactly long enough for the
trend to converge — the one input shape that hides the bug.

Measured on a 0.5Hz look-around peaking at 25cm/s: **29.5mm of slip.** Reading the
measured rate instead, sizing `beta` against the speeds a head reaches, and making the
speed estimate itself responsive (`dCutoff` 1 → 3Hz) takes that to **2.1mm**, with
per-frame noise on a still head still attenuated to 0.6mm — under a pixel.

**Where `beta` sits is a measured point on a curve, not a preference.** Lag and
shimmer are the two ends of one knob, so the harness prints the whole frontier rather
than asserting a number:

| `beta` | lag on the sweep | shimmer at rest |
|---|---|---|
| 0.30 | 5.6mm | 0.47mm |
| 0.60 | 3.3mm | 0.52mm |
| **1.20** | **2.1mm** | **0.60mm** |
| 2.40 | 1.4mm | 0.75mm |
| 4.80 | 1.1mm | 0.90mm |

The curve is a smooth hyperbola with no cliff in it, so "the knee" has to be tested by
curvature rather than by a threshold — at the shipped point, *halving* beta costs more
lag (1.2mm) than *doubling* it saves (0.6mm). That inequality reverses below the knee,
which is how the first pass at this was caught shipping 0.5 and leaving 1.7mm of
responsiveness unclaimed for 0.08mm of shimmer.

**The velocity term is now off, which reverses an earlier decision.** Carrying one is
free timeliness on a constant-rate ramp — 0.00mm against 3.0mm — and overshoot at
every reversal, because the trend converges slower than a head turns around. A head
reverses two or three times a second, so on the same sweep the trend costs 12.0mm
against 3.8mm: three times what it saves. The capability stays on `Predicted` and the
harness measures both configurations side by side, so the trade cannot quietly invert
again. The app has no use for prediction anyway — it samples at lead zero, because
predicting past the displayed frame is precisely the two-clock mistake the frame lock
removed.

**Smoothing** turns the filter off entirely, for the raw jitter underneath.

## Verified

`python ar/serve.py` then <http://127.0.0.1:8765/tests/pipeline-check.html> — 343
checks. 342 pass anywhere; the one remaining is a wall-clock budget ("the
deformation fits inside the tracking loop", 13 ms for a full occluder rebuild)
which is environment-ruled: it measures real elapsed time, so it passes on an
unloaded machine and reads red in a throttled or backgrounded tab. It is the only
check in the suite whose result depends on the machine rather than the code, and
it is left in rather than deleted because the budget is real — it just has to be
read with that caveat. Add `?model=crystal` (or any entry's `value`) to run
the whole suite against a different frame: the checks are written against whatever
is loaded rather than against one asset, so that is how a newly added frame gets
put through all of them. It drives `updateFrame`, the same function the live
loop calls, rather than a parallel copy. It also runs to completion in a hidden
tab, which took one deliberate choice: image loading waits on `onload`, not
`decode()`, because `decode()` is allowed to defer until the image is about to be
rendered — and in a background tab that moment never arrives.

The fitting checks are written to be falsifiable, and the distinction is worth being
strict about, because a check that cannot fail reads exactly like one that keeps
passing. "Pupils land at 45%" is true by construction once the solver targets it, so
it only guards regressions. Two others were quietly in the same category and have
been given something that can actually fail:

- **the pads stay on the nose** measured a residual that is *identically* zero — the
  solver puts the pads on the target and then slides them along `bridgeUp`, so the
  perpendicular component is zero for every model and every face. The harness was
  re-deriving the solver's algebra and comparing it with itself. It still reports the
  residual, but now alongside a control that solves against a deliberately wrong
  bridge direction and requires the pads to come *off* the nose: 17.8mm and 20.5mm on
  the two faces, against 0.0mm for the real one. A number that can only be zero is not
  a measurement until you have shown it can be something else. (It is measured on the
  placement *before* the nose seat, deliberately — the bridge line is the midline
  ridge and the seat's whole job is to take the frame off it.)
- **the fit converges and then holds still** fed the same detection 60 times, so the
  placement could not have moved whether the old lock worked or not; only the
  frames-to-lock count was falsifiable. Now every landmark is shifted 2% of the frame
  once the estimate has settled and the placement has to ignore it (0.0000mm), *and*
  the same shift has to reach the placement (4.7mm) — otherwise a solver that ignored
  the face entirely would pass too. Alongside it, **the first frame is already the
  fit** asserts the property the scan gate was standing in the way of: frame one
  places the frame 0.000mm from where frame sixty does.
- **the glass checks used to run on the wrong assets.** The filter read
  `m.crystal && m.orient`, so the only lenses ever put in front of a camera belonged
  to the two crystal scans — a frame that shipped correct, self-declared lenses
  skipped every render check *because* its materials were right, which is exactly
  backwards. They now run for any asset that arrives carrying glass, with the
  crystal- and orientation-specific assertions guarded, and two consequences fell out
  of that: the lens-pixel mask had to be rebuilt (diffing transmission 0 against 0.5
  finds a *clear* lens barely at all — 92 pixels on the aviator, none on the
  navigator; forcing it opaque **and black** against the lens hidden finds ~1000 on
  both), and "you can see through the frame" had to be gated on the glass being clear,
  because a sunglass lens failing it is doing its job.
- **absolute head size is recovered from the iris** is asserted against *human*
  ranges — a 54–74mm pupil distance — rather than against anything this codebase
  computes, and that is the only reason it works. Three earlier versions of the
  measurement each produced a plausible-looking ratio (0.94×, 0.78×, 1.04×) that a
  self-referential check would have waved through; what caught them was that the same
  numbers implied a 37mm intercanthal distance, or a head 182mm across. When a
  measurement claims real units, the assertion has to come from the world.
- **a glitching tenth of the scan cannot move the fit** — three absurd frames among
  thirty leave the median exactly where the clean scan put it, where a mean would
  have moved 2.7% on width alone; and the ear targets stay derived from the
  estimate's own width rather than any single frame's. This is the property the old
  per-frame filters existed for, asserted directly against the statistic that
  replaced them.

The ones that could always fail:

- **the safety clamp is not what sets the height** — pinned at its bound, the clamp
  is deciding instead of the optics and the pupil check silently stops meaning
  anything. This caught exactly that when `base.obj` was added.
- **two faces come out measured and fitted differently** — if the measurement ever
  stopped reaching the placement, every other check would still pass.
- **the frame rests on the nose rather than inside it** — the frame's own
  back-of-bridge samples, measured against the face's depth field where the solver
  left them. Hung from the landmark they sit 6mm clear of the skin on both faces;
  seated, 0.5mm into it. And then the same question asked of **every vertex of the
  model** rather than of the samples the solve looked at — 92,000 of them per face —
  because that is the one that can fail while the first passes: a solve given
  incomplete samples seats the frame perfectly on the geometry it was shown and lets
  the rest go wherever it likes.
- **seating changes the standoff and nothing else** — 0.0mm in x and y, pupil height
  unchanged to 1e-9. If the seat ever moved the frame vertically it would be undoing
  the height solve, and the two would chase each other frame to frame.
- **a broader nose stands the frame further off the face**, and **the nose is
  measured, not scaled off the face** — the second is what makes the first mean
  something. Both sample faces are wider than average with narrower-than-average
  noses, so a nose derived from face width would have been out by 20% and 24%.
- **the arms run outside the head, at ear height** — the two numbers that were wrong.
  Traced in face space, the arm passes the cheek at x=8.10cm against a head edge of
  8.25cm, and comes within 1mm of the ear rest point.
- **the ear rest points are measured from this face** — lifting the ear-top
  landmarks in the image raises the rest points with them; if they ever fall back
  to the average head's ears, this fails.
- **the head shell closes, and closes around the face** — zero boundary edges left
  after the loft, every one of the 468 face vertices unmoved, and no lofted vertex
  in front of the face surface. A seam in an occluder is a slot the arm shows
  through; a loft that crept forward would eat the frame.
- **the head keeps its width back to the ear** — 7.55cm half-wide at the temple,
  7.60cm at the ear rest point, narrowing to nothing by 15cm behind the rim. This is
  the number the arms are routed against, so a sweep that narrowed too early would
  splay them off the head.
- **the temple arm clears the head at the ear end** — sampled every half-centimetre
  along 11.3cm of arm: 3mm of clearance over the last quarter, and most of the run
  outside the head, with the rest pressing into the temple where a narrow frame does.
  Both halves matter and they pull opposite ways; asserting only the first is what
  stood the arm off the head.
- **the ears tuck the arm behind the pinna** — geometrically (the dish is seated on
  the head and its apex is outboard of the arm, with the arm between them) and in
  pixels (with the head turned 45°, toggling just the ears removes ~0.1% of the
  frame's pixels — the strip of arm that would otherwise be painted across the ear).
- **the head hides the arm where the face mesh alone cannot** — at 88° the whole
  head is diffed against a bare copy of MediaPipe's face mesh, which is exactly what
  the occluder used to be in that region. 0.1–0.17% of pixels differ, and they are
  the whole side of the head.
- **the head never takes a bite out of the frame front** — arms hidden, occluder
  toggled, head-on: a few hundred pixels at most, and they are the pads deliberately
  sunk half a millimetre into the skin. This exists because the pixel checks are
  provably blind to a depth-only occluder until it eats something — and when the
  x-stretched version did, it ate 4% of the frame.
- **the frame genuinely dims with the room** — rendered end-to-end at the two
  lighting extremes, the frame's pixels must be brighter in the bright room. The
  pure-function checks passed while the first version failed exactly here: it
  dimmed the lights but not the environment map, and a metal frame is ~95%
  reflections.
- **an ear measured 3cm low bends the tail, not the arm** — the aimed arm must
  stay within 8° of level and still head outwards and back. This is the check for
  the live-camera bug where straight temples rendered as bent: hair over the
  ear-top landmarks skewed the measured rest height, and the beeline aim pitched
  the whole arm after it.
- **measurement pauses while the head is pitched** — ten frames at 30° pitch must
  collect zero anchor samples, the pitch-axis twin of the yaw gate that always
  existed.
- **the frame is measured across its geometry, not its rotated boxes** — the
  vertex-accurate width has to hit the declared width *and* beat the loose one.
  This is the check for the bug above, and it can only fail on a rotated asset,
  which is why it needed one to find it.
- **the frame is visible, not just transparent**, and **you can see through the
  frame, not just light through it** — the pair, and they have to be a pair. The
  first requires 40% of the frame's own silhouette to still read differently from
  what is behind it (it manages 100%); the second renders it against a backdrop
  split blue one side and red the other and requires it to carry the split (78
  levels).

  Three earlier versions of the see-through check were too weak, and the
  progression is the useful part. Against *white*, "brighter" is a lighting
  accident — an opaque frame lit by the environment is bright too. Against a
  *uniform* colour, a frosted frame that has smeared the whole scene into one
  average scores exactly as well as a clear one. And on its own, however good, it
  passes perfectly on a frame that is not being drawn at all: an absent frame
  trivially "carries the image behind it", because it *is* the image behind it.
  That is precisely what shipped, and only asking whether you can see the frame
  catches it.
- **the scan's parts are sorted into crystal, metal and horn**, and **transmission
  lands on a material that can carry it** — three physical materials transmitting,
  zero standard materials holding a `transmission` the shader will ignore.
- **the arms hinge at the frame, not mid-arm**, and **aiming an arm does not drag
  the joint off the frame** — the second is the one with teeth: swinging the arm
  15° may move its own front face no more than 1.2mm. That is the number that says
  the pivot is on the joint rather than behind it, and it is what a bend appearing
  in the wrong place actually measures.
- **the contact shadow is a shadow** — toggling the key light's shadow changes
  pixels on the face and changes them *darker*; a glow, a colour shift, or an
  unwired `castShadow` flag all fail it.
- **the lighting estimator answers for known scenes** — brightness rises
  monotonically from a dark swatch to a bright one, the mapped intensities follow
  but never reach zero, a warm scene warms the tint at reduced strength, and a
  half-black/half-white frame probed at the bright half reads bright — the
  window-behind-the-wearer case the region parameter exists for.
- **positive tilt tilts the frame the pantoscopic way** — for a long time it did the
  exact opposite, and nothing else here noticed, because nothing else here can. The
  lens still covered the eye line, the pads still sat on the nose, the arms still
  reached the ears. Only the sign of one rotation was wrong.

Three checks cover what the geometry cannot show, because a rebuilt model can be
perfectly placed and still wrong to look at: that a positive pantoscopic tilt leans
the lens normal *downwards*, that all five of the sample `.glb`'s materials survive
the temple rebuild, and that its vertex normals still lie on their surfaces
afterwards (mean 14.2°, 3% beyond 45°).

The pose estimator is exercised on synthetic sequences, which a still portrait cannot
do: a filter fed one frame only reports its own initial condition. So the harness
drives one — a 3cm step that the filter has to trail (0.82cm after one frame) and then
close on (3.58cm, exact), a zero timestep that must not become a NaN in the head
matrix, and the two properties of quaternion filtering a still portrait cannot show:
that the output stays on the unit sphere, and that the same rotation arriving as `q`
and as `-q` stays one rotation rather than averaging across the hypersphere.

Four checks are specifically about *keeping up*, and the first is the one with teeth,
because it compares against the thing it replaced rather than against a remembered
number:

- **the estimator does not trail a head that is simply moving** — the same filter with
  its velocity term switched off *is* a plain low-pass at the same cutoff, and it sits
  44.2mm behind a head crossing frame at 25cm/s, for as long as the head keeps moving.
  With the velocity term on: 0.00mm, because a constant rate is a *fixed point* of the
  recurrence rather than something it converges near. The rotational twin measures 15°
  against 0.00° at 92°/s. Both are taken over the last second of a steady move, so
  what is compared is settled behaviour and not the filter's start-up — which is the
  whole distinction, since the low-pass's error *is* its settled behaviour.
- **asked for the head 80ms from now, it answers for 80ms from now** — landing on
  where the head will actually be, 20mm further on than where it was photographed.
- **prediction is bounded** — asked for a pose five seconds ahead of a head moving at
  25cm/s, it moves 4.9cm against the 124cm the velocity it is carrying would have
  taken it. Compared against the unbounded extrapolation rather than against the
  clamped horizon, because `sample()` clamps its argument before the filters see it,
  so asking whether five seconds agrees with 200ms is asking whether a call agrees
  with itself.
- **the speed bound contains a runaway estimate without limiting a real head**, and
  its rotational twin. Both halves have to hold at once, and the pair exists because
  only one of them used to: at 25/60/100cm/s the frame reaches 22/54/90mm against the
  23/54/90mm it should — untouched — while an estimate claiming 400cm/s is held to
  300mm instead of 800mm. The displacement cap this replaced passed a check that only
  asked the second question.
- **the worker tracker sees the same face the main tracker sees** — the same frame
  through both paths, compared at the landmarks and the head translation. The worker
  crossing is new surface between the landmarker and the placement (a different wasm
  loader, a bitmap handoff, a flatten to transferred arrays and a rebuild), and any of
  it wrong means the app tracks a subtly different face than the harness verified.
  Measured: agreement to 0.000% of the image and 0.00mm of translation.
- **the frame-locked pose sits on the frame it belongs to, even mid-movement** — at
  25cm/s the pose sampled at each frame's own capture lands within 0.1mm of that
  frame's truth. Under the frame lock this residual is drawn directly as slip on
  every composite, so it is the one number that decides whether the glasses look
  welded to the face or painted near it.

**The fit survives losing the face entirely** is the inverse of what this file used to
assert, and the inversion is the point: the old check pinned the behaviour that made
the app feel slow. What remains is driven at two frame rates, because the threshold is
a duration and the point of that is that the two behave alike — it used to count
frames, and 30 lost frames is half a second on one webcam and a quarter on the next.
Beside it, **a different face is caught by shape or by absolute size** pins the
replacement: a 20% narrower face is caught by shape, a face of identical proportions
20% smaller is caught by the iris, and a 2% frame-to-frame wobble in both is not
mistaken for either.

Two results are worth repeating.

**The camera model is right.** MediaPipe solves head pose against an assumed pinhole
camera, and if our virtual camera disagrees the frame sits correctly at one distance
and slides off the face at every other. Nothing in the rendered image makes this
obvious. So the harness sweeps field of view from 40° to 100° and reprojects the
canonical mesh back onto the observed landmarks at each one:

| vertical FOV | mean reprojection error |
|---|---|
| 45° | 126 px |
| **63°** (assumed) | **21 px** |
| 67° (measured minimum) | 15 px |
| 90° | 75 px |

The error is near its floor at 63° and explodes either side of it. The assumption
holds.

**The transform is rigid — it carries no size information.** Its uniform scale is
exactly 1.0 on every face. MediaPipe does not resize the canonical head to match a
face; it keeps it at nominal size and moves it nearer or further to explain how
large the face appears. That is the monocular scale/depth ambiguity, and it means
**absolute head width in millimetres is not recoverable from a single camera.** Any
"your head is 154mm wide" read off this matrix would just be the canonical head's
own width, dressed up as a measurement.

What *is* recoverable is the residual: compare where the canonical temples reproject
against where the observed temples actually are, and the difference is a real shape
difference. So the readout is a ratio — *width vs avg*, `+6.6%` — not a millimetre
figure. It is enough to drive frame-width selection, which is a comparison anyway.

## Limitations

- **Strong head roll degrades tracking.** Lying sideways (head rolled 40°+ against a
  pillow) is outside the landmarker's comfort zone: a cold detection fails outright
  and VIDEO-mode tracking carries a degraded pose through it — measured on real
  session frames, this is where placement genuinely breaks while commercial trackers
  hold. The standard fix is roll pre-rotation: rotate the snapshot upright by the
  previous frame's roll before detection and un-rotate the resulting pose and
  landmarks. The frame-locked snapshot pipeline gives it a natural seam (a second
  rotated canvas at submit); it is the highest-value piece of work not done here.
- **The occluding head is the average one, at the measured width.** Its front is this
  face — MediaPipe's mesh, tracked — but the skull behind it is a loft of the average
  rim, the ears are analytic dishes at measured positions, and only its *width* is
  personal. Its depth, its crown and its jaw are everybody's. Hair volume, hats and
  glasses-over-hair are not modelled at all, so the boundary at the hairline is
  approximate and a temple can still show a few millimetres of itself against thick
  hair. A per-face head shape, a depth sensor or a learned matte would close it; the
  arm's depth fade is what keeps the residual from reading as an edge.
- **Lighting adaptation is intensity and tint only.** No light *direction* is
  estimated — the key stays upper-front — and the contact shadow direction is
  therefore plausible rather than matched to the room. Single-image illuminant
  estimation could drive the key's position; it is not attempted here.
- **Shadows ignore transmission.** three's shadow map records depth, not opacity,
  so the crystal frame casts the shadow a solid one would. Softened and lightened
  to keep it plausible, but a clear frame's shadow should be fainter than its
  temples', and here it is not.
- **No per-SKU fitting data.** The auto-fit gets close and the placement sliders
  close the gap, but the offsets are not persisted. Real systems store a fitting
  offset per frame; `DEFAULT_FIT` is where that would be read from.
- **The nose is seated against an average profile, scaled to a measured width.**
  Depth is not recoverable from one camera, so the surface the frame rests on is the
  canonical head's, stretched sideways to this person's nose width and shifted onto
  this person's bridge. That gets the *shape* of the wedge from an average and its
  *width* from a measurement, which is the best a single camera can do — a tall
  narrow nose and a low broad one of the same width would be seated identically.
  Pantoscopic tilt stays on the manual slider for the same reason.
- **The mirror runs behind the room by the pipeline's latency** — measured live at
  ~35–65ms (capture to composite, the `mirror delay` readout), and it steps at the
  detection rate rather than the display's. That is the price of the frame lock,
  paid deliberately: a mirror uniformly late by 50ms is imperceptible, while the
  same latency expressed as glasses sliding across a fresher face — which is what
  prediction-to-display-time produced — is the most visible artefact this app ever
  had. A tracker faster than the camera (the main-thread path here) makes the step
  rate the camera's own 30fps.
- **Inference is ~21ms per frame, and that is the ceiling a different tool would have
  to beat.** The alternatives were surveyed deliberately (August 2026) before deciding
  to keep MediaPipe, and the reasons are worth recording so the survey is not redone
  from scratch:
  - **A newer MediaPipe is not available**: the vendored runtime is byte-identical
    (sha256) to `@mediapipe/tasks-vision` **1.0.1**, the current stable release, and
    `face_landmarker.task` is the only published model — there is no "lite" variant
    to trade accuracy for speed.
  - **Jeeliz** (and its glasses-VTO widget) is genuinely fast and purpose-built, but
    it outputs a pose and expression coefficients, not a dense mesh. Everything that
    distinguishes this fit — the measured nose width, the per-side ear tops, seating
    the frame on the face's surface — consumes the 478 landmarks and the facial
    transformation matrix, and would have to be deleted, not ported.
  - **TensorFlow.js face-landmarks-detection** (including with a WebGPU backend)
    outputs landmarks but **no facial transformation matrix** — the one artefact this
    whole pipeline is built on. Replacing it means reimplementing MediaPipe's metric
    pose solve, which is a project, not a swap.
  - **Commercial SDKs** (Banuba, DeepAR, 8th Wall &c.) fail the constraints this
    prototype exists to prove: vendored, offline, nothing uploaded.

  What *was* worth taking: inference off the main thread (shipped, above). What is
  left on the table is the model's own 21ms, which bounds detection at ~45/s — more
  than any webcam delivers, so it is not currently the binding constraint.
- **The measured latency is only as good as the browser's.** `captureTime` is the
  sensor's own stamp and not every browser populates it; without it the pipeline's
  own transport and decode time are invisible and the compensation is short by
  however long those took. The readout marks that case with a `+` rather than
  inventing a figure for it.
- **Two sample faces is not a calibration set.** The detector-vs-canonical bias in
  the nose bridge landmark is real and measurable, but its size is estimated from
  n=2. A production version would measure it once across a proper dataset and
  subtract it, rather than routing around it via the eye line.
- **Lens rendering is whatever the asset says.** The sample `.glb` carries its own
  transmissive, iridescent lens materials and they are preserved through the temple
  rebuild — but nothing here *controls* them. Tinting, mirroring and transmission per
  SKU are not modelled.
- **One face, front camera, no depth sensor.** Nothing uses ARKit/ARCore depth. On a
  device that has it, depth would fix both the occluder and the size ambiguity.
- **Not tested against a live camera in this environment** — there is no webcam on
  the build machine, so the camera path is exercised only as far as `getUserMedia`.
  Everything downstream of it — including the pose estimator the still-image checks
  used to skip — is covered by the harness against still portraits and synthetic
  sequences, and the live loop has been driven end-to-end against the sample source.
  What that cannot cover is the hardware itself: the frame-callback path, the real
  `captureTime`, whether a given webcam honours the 60fps request, and a camera that
  stops mid-session — which is detected and reported rather than left claiming a
  tracked face on a frozen frame, but has only been reasoned about, not unplugged.
- Only the SIMD build of the MediaPipe wasm runtime is vendored. Every browser since
  ~2021 has it; a browser without it will fail to load rather than fall back.

## Layout

| Path | |
|---|---|
| `src/canonical-face.js` | parses the average-head mesh, names the landmarks |
| `src/tracker.js` | MediaPipe wrapper, and the client that runs it in a worker with a measured fallback |
| `src/tracker.worker.js` | the landmarker off the main thread; frames in as bitmaps, poses out as transfers |
| `src/anchors.js` | measures *this* face — bridge, eye line, widths — in face space |
| `src/nose.js` | the face as a depth field; seats the frame on it instead of on a landmark |
| `src/fit.js` | measures a model, solves placement, judges the fit |
| `src/models.js` | the frame catalogue; loads glTF and OBJ, normalises scale |
| `src/temples.js` | splits the arms onto hinges, aims them at the ears, opens them past the head |
| `src/head.js` | lofts MediaPipe's face into a closed head, with ears, for occlusion |
| `src/frame.js` | one frame: pose → smooth → place. Shared by app and tests |
| `src/scene.js` | three.js layer, camera matching, occluders, shadow catcher, debug meshes |
| `src/lighting.js` | scene brightness/tint probe that drives the lights |
| `src/smoothing.js` | the One Euro law, and the velocity-carrying pose predictor built on it |
| `src/sources.js` | camera and still-image sources |
| `src/main.js` | wiring, loop, controls |
| `tests/pipeline-check.html` | the harness |
| `serve.py` | static server with the right MIME types |

## Adding a frame

Drop a `.glb` or `.obj` in `assets/glasses/` and add an entry to `MODELS` in
[models.js](src/models.js):

```js
{ value: 'aviator', label: 'Aviator', url: '../assets/glasses/aviator.obj', realWidthMm: 142 }
```

It should follow the glTF convention — +Y up, +Z out of the face — which most
commerce assets do. The solver measures everything else: frame width, lens height,
and where the pads meet the nose.

`realWidthMm` is the frame's real total width. glTF declares metres so a
well-authored `.glb` needs no correction, but OBJ declares nothing — `base.obj`
arrives 1.85 units across — and true-size fitting is meaningless without it. It is
a number a retailer already has: it is printed on the temple arm.

**Getting a Meshy `.blend` into the catalogue**, which is the path the tortoiseshell
aviator took, and the three things that had to happen to it:

- **Budget.** It arrived as 1.95M triangles with an 8192 base-colour map — 206MB.
  Over half those triangles were the *lenses*: two smooth curved shells carrying
  910k between them, more than the entire rest of the model. Decimated to 106k with
  textures at 2048/1024/1024, it ships at **3.3MB**.
- **Do not separate by loose part.** This is the trap, and it looked like the clever
  move. `prepareTemples` prefers an asset's own parts over its geometric cut, because
  a real seam beats a guess — but Meshy welds the *front half* of each temple into the
  frame shell and leaves the back half floating, so separating loose parts hands it two
  half-arms and it hinges each temple at its own midpoint. The arms came out 55mm long
  against a real 135, never reached the cheek, and read as snapped. Separated by
  **material** only, `classifyParts` finds no left-and-right pair, returns null, and
  the per-triangle depth cut runs: 110mm arms, hinged at the frame.
- **Textures need forcing.** `img.has_data` is False for a *packed* image in a
  background Blender until something decodes it, so a resize loop guarded on it
  silently does nothing and the export lands at 33MB with its full-size maps still in.
- **Take a texture's role from its colour space, not from its wiring.** sRGB is
  something a human looks at; Non-Color is data. Deciding by what a map feeds into
  breaks the moment an asset does something clever — the sage frame drives its tint
  through a graph that splits the *base colour* map with a Separate Color node, and a
  "feeds Separate Color, therefore ORM" rule downsized the one texture carrying the
  product's whole appearance to 1024.
- **glTF cannot carry a node graph.** Meshy drives its gradient sunglass tint from a
  texture coordinate through a Map Range into a colour ramp; the exporter drops the
  lot silently and ships whatever constant sits in the socket underneath — which is
  white, so a graduated sunglass arrives as a *clear* lens with nothing in the file to
  say it was ever otherwise. Sample the ramp and write its mean back as a flat factor
  before exporting. Flat instead of graduated is a real loss and a small one: three
  multiplies transmitted light by the base colour, so the lens still darkens and warms
  what is behind it.
- **A mirrored lens is a Mix Shader, and needs collapsing by hand.** The golden shield
  is authored as 72% gold metal over 28% transmissive amber. glTF has one PBR material
  per primitive and no mix node, so the exporter cannot follow it. Collapse it onto the
  two extensions that carry its two halves: what gets *through* becomes
  `KHR_materials_transmission`, scaled by the mix and tinted by the lens; what bounces
  *off* becomes `KHR_materials_specular`'s colour, taken from the metal. Metallic stays
  0 — a metal cannot transmit, so carrying the mix's 0.72 across would cancel the
  transmission the other half of the blend exists for.

Its lenses needed no work at all, which is the point of the split between
`prepareDeclaredGlass` and the crystal rescue. Meshy authored them as
`Lens_Prescription_Glass` with a transmission of 1 and an index of refraction of
**1.586** — not a round number anybody guesses, but the refractive index of
polycarbonate, the standard high-index ophthalmic material. Both survived the export
as `KHR_materials_transmission` and `KHR_materials_ior`, so the loader finds them and
adds only what glTF cannot say: how that glass composites onto a photograph.

Scanned and AI-generated assets get one more knob: `pbr: { metalness, roughness }`
corrects the surface response without touching the textures. Scan pipelines
routinely stamp `metallicFactor: 1.0` on everything, which renders a cream acetate
frame as dark brushed metal; the default model (`meshy-glasses.glb`, an
image-to-3D scan at arbitrary scale) uses exactly this. Its texture coordinates
also forced the temple rebuild to carry UVs through — an untextured frame never
noticed they were being dropped.

`orient` squares up an asset the exporter left rotated, and it is measured rather
than eyeballed. The parts scan arrives 42.7° off axis: a ~40° yaw plus a 5.3° roll.
Its two lens parts are symmetric, so the vector between their centroids *is* the
frame's width axis, and the vector from the temples' centroid to the lenses' *is*
its forward axis; an orthonormal basis built from those two, inverted, is the
correction. The check that the axes were right is that the height difference
between the two lenses falls to zero.

Squaring a model up exposed a measurement bug that had been latent for as long as
every asset arrived axis-aligned. `Box3.setFromObject` transforms each mesh's
*bounding box* rather than its vertices unless asked for precision, and the AABB of
a rotated box is larger than the AABB of the geometry inside it. The rotated frame
measured **233mm** wide instead of 140, and was then dutifully shrunk to fit — it
would have worn at 84mm. Both `analyseModel` and the width normaliser now measure
across geometry.

**Making a scan transparent, when the file says it is not.** The crystal frame
arrives 100% solid, and the reason is worth stating precisely: its exporter listed
`KHR_materials_volume` among the extensions the file uses, then declared it on no
material at all — and never wrote a `KHR_materials_transmission` anywhere. The
*intent* to be transparent survives in the file; nothing a renderer can act on
does. So something has to supply the missing information, and the honest source is
the asset itself — the textures it did ship. Metalness comes out of the packed
metallic-roughness map and brightness out of the base colour, and those two
separate this frame's materials cleanly: gold trim at 0.89 metalness, horn temples
at 0.15 luminance, crystal at 0.7. Nothing is keyed on a part's name or index, and
a model that ships proper materials never reaches this code.

The same frame *with lenses* (`glasses01-with-lenses.glb`) shows what "proper
materials" buys and what it does not. Its two lens meshes do declare
`KHR_materials_transmission`, so the loader builds real glass from the file and the
classifier steps over them — three crystal parts, three gold, two horn, and one
material it was told about. But glTF describes what a material *is* and says nothing
about how it composites onto a photograph, and the two settings that decide that were
learned on the frame: transmissive materials belong in the **opaque** pass, where
three gives them their dedicated buffer, and they must opt out of **tone mapping**.
The camera feed is an sRGB background, which three excludes from tone mapping, so
light coming through the lens is graded once more than the skin beside it and the
difference lands exactly along the rim. A lens is a larger, flatter, more transmissive
version of the same problem sitting directly over an eye, so it gets the same two
settings — and nothing else. Its optics are the asset's business.

One setting decides whether the result reads as crystal or as frosted bathroom
glass, and it is not the transmission: it is **roughness**. Three blurs the
refracted image by sampling further down the transmission buffer's mip chain as
roughness rises, so at 0.55 the frame averages the entire scene behind it into one
flat milky tone — transmitting perfectly, and looking solid. Polished acetate
belongs near 0.1. This shipped wrong once, and the check that should have caught it
could not: it measured colour against a *uniform* backdrop, where a frame that has
smeared everything into one average scores exactly as well as one you can see
through. The backdrop is now split blue/red, and the frame has to carry the split.

The conversion itself is the part that fails silently if you get it wrong, and it
does so twice over. Transmission exists only on `MeshPhysicalMaterial`, and the
loader returns a `MeshStandardMaterial` for any asset without a physical glTF
extension — which is exactly the asset that needs it. Setting `.transmission` on
the standard material compiles, runs, and does nothing: the property is not in its
shader. The copy also has to go through `MeshStandardMaterial.prototype.copy`
rather than the physical material's own, because that one reads
`source.transmission` and `source.ior` — `undefined` on a standard material — and
would poison the fields being set.

And then that copy has a sting of its own: it assigns `defines` wholesale,
replacing the physical material's `{ STANDARD, PHYSICAL }` with just
`{ STANDARD }`. The renderer still sees `transmission > 0` and adds
`USE_TRANSMISSION` to a shader that now has no `PHYSICAL` block to define it
against — the program fails to compile and **the mesh is silently not drawn**. It
is a uniquely misleading failure: the material answers `transmission: 0.55` when
asked, the geometry submits its triangles, `renderer.info` counts them, and the
frame is simply absent from the picture. Because any transmission at all triggers
it and none is fine, it reads as "very transparent" rather than as broken — the
wearer sees their own face with a shadow and two gold studs floating on it. The
defines are restored after the copy.

**One caveat on OBJ:** with no `.mtl` alongside, there are no material groups, so
the whole frame — lenses included — renders as a single material. It reads as a
mirrored sunglass, which is fine, but clear lenses need either an `.mtl` or a `.glb`
with the lens as its own material.

Attribution for the vendored model and libraries is in [ATTRIBUTION.md](ATTRIBUTION.md).
