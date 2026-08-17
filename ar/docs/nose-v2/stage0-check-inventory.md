# Stage 0 — the legitimate-rewrite inventory of `ar/tests/pipeline-check.js`

2026-08-16. Mandated by `spec.md` Stage 0 and by G12 ("the stage-2 list of
latch-coupled checks is enumerated during Stage 0, not mid-stage") and G7
("Enumerate which existing checks legitimately move"). Line numbers refer to
`ar/tests/pipeline-check.js` as of Stage 0 (4106 lines, sha256 prefix
`7838f9295a906bad`; the `ar/` tree is not yet under git, so the hash is the
pin). Stage 0 does not touch the file, so the numbers hold until the first
stage that does.

How to read the disposition column:

- **REWRITE (stage N)** — the assertion encodes semantics that stage N is
  chartered to replace; the check is expected to change, and the change must
  cite this inventory and the diagnosis finding it retires.
- **RETIME / RE-DERIVE (stage N)** — the property asserted survives, but the
  numbers, counts or tolerances are derived from a mechanism stage N replaces,
  so they must be re-measured rather than trusted.
- **SURVIVES** — the assertion is an invariant (or measurement-level) and any
  stage that breaks it has a bug, not a rewrite. Listed because its *setup*
  touches the machinery in question, which is exactly how a rewrite quietly
  invalidates a green check.

Checks prefixed `<sample>:` run once per sample face (face-a, face-b — two
instances each); `<model>:` runs once per catalogue model.

## Category 1 — measuring latch / estimateYaw gating / binary anchor payload

Owner: **Stage 2** (continuous pose-trust: w_pose from true euler, weighted
median window, always-carried eyeLineY/bridgeUp/ears, latch → readout alias
G12, identity gating). One item leaks to Stage 3 where noted.

| line | check | what it asserts | disposition |
| --- | --- | --- | --- |
| 2676 | `<sample>: measurement pauses while the head is pitched` | 10 frames at 30° pitch collect **zero** anchor samples — the binary pitch gate refuses outright | **REWRITE (2)**. Under C4 there is no binary refusal; the honest successor asserts w_pose ≈ 0 at 30° pitch (near-zero *weight*, not zero samples). Retires diagnosis empirics scan-cause 3. |
| 2721 | `<sample>: the first frame is already the fit` | frame one places within 0.2 mm of frame sixty | **SURVIVES — invariant.** The frame-one pin runs at every stage (spec: three mechanisms individually preserve it). Listed because its 60-frame run flows through the latch path (measuring = 1 throughout on these frontal samples). |
| 2727 | `<sample>: the fit converges and then holds still` | placement bit-still (1e-9) from frame 15 to 60 — the commitFit deadband holds after the median converges | **RE-DERIVE (2)**. The weighted median window must keep the hold; if G13's measured micro-resettling (Stage 5 scheduler) admits sub-deadband deltas, the 1e-9 becomes a sub-deadband bound. Do not loosen past FIT_DEADBAND. |
| 2761 | `<sample>: the placement follows the landmarks, not the rigid pose` | a 2% uniform landmark shift moves the placement > 1.5 mm **in one frame** (bridge live-raw; setup relies on estimateYaw being invariant to uniform shifts so the gate stays open) | **SURVIVES (2), RETIME (3)**. Stage 2 keeps the bridge live. Stage 3's One Euro pin (C3+G11) may spread a single-frame step over 2–3 frames — the check should then assert the filter *opens* (innovation-rate activity) and converges within a few frames, not bit-instant response. |
| 2768 | `<sample>: the proportions ignore landmarks that move` | templeWidth and scale bit-unchanged under the same shift — deadband + gating | **SURVIVES (2)**, comment churn only: the shift-invariance the setup exploits moves from estimateYaw to the pose matrix (also untouched by a landmark shift). |
| 2209 | `<sample>: measured bridge differs from the average face` | `anchors.measured === true` and the bridge moved off canonical | **SURVIVES (2)** — G12 keeps `measured`/latch visible as a derived readout; the payload flag must not vanish. |
| 2215 | `<sample>: measured anchors stay anatomically plausible` | payload shape sanity (templeWidth, eyeLineY vs bridge.y) | **SURVIVES (2)** — the always-carried change must not shrink the payload field set. |
| 2467 | `<sample>: ear rest points are measured from this face` | measureAnchors' *observed* ears rise with lifted ear-top landmarks | **SURVIVES (2)** as written (drives measureAnchors directly), but the product behaviour it underwrites — live ear heights while measuring — is exactly what "always-carried ears" removes. Stage 2 must add the successor assertion (carried ears still *converge* to this face's ears through the window). |
| 3951 | `measurement noise is not mistaken for a different person` | isDifferentFace tolerance floor (2% wobble passes) | **SURVIVES (2)**. Identity gating is Stage 2 scope: the predicate keeps its numbers; what may move is *when* it is consulted (today: only on measuring frames — under continuous weighting it must still fire). |
| 3956 | `a different face is caught by shape or by absolute size` | 20% narrower or 20% smaller is a stranger | **SURVIVES (2)** — same note as 3951. |
| 3962 | `a face with no iris reading is never called a stranger` | metricScale null is missing evidence | **SURVIVES (2)** — same note. |
| 3991 | `a glitching tenth of the scan cannot move the fit` | medianAnchors robustness over an equal-weight 33-sample set; ear x re-derived from the estimate's width | **RE-DERIVE (2)**. The weighted median window changes the call semantics; the equal-weight case must reproduce today's numbers exactly (that is the A/B that proves the weighting is a generalisation, not a change). |

Harness infrastructure, category 1: `anchorsForShape` (line 139) fabricates
the whole anchor payload `{measured, bridge, bridgeUp, eyeLineY, …, ears}` for
every synthetic block — a Stage 2 payload change updates it in lockstep or
every occluder check silently tests a stale shape.

## Category 2 — seat report shape / single-push semantics

Owner: **Stage 5** (physics-first B.2–B.8 inside stability-first plumbing;
G1–G6, G13, G17).

| line | check | what it asserts | disposition |
| --- | --- | --- | --- |
| 520 | `<model>: the back of the bridge is sampled across its width` | noseContacts span both sides of the centreline, ≥6 samples, bounded spread | **SURVIVES (5), extended** — the L/C/R contact split consumes this same set; add per-side minimum counts when B.3 lands. |
| 412 | `the face surface reproduces the mesh, with no gaps in it` | depthAt reproduces vertices, holes 0, NaN off the patch | **SURVIVES** — spec keep-list; Stage 5's widthAt/normalAt harness queries are built beside it, on the same field. |
| 420 | `the nose falls away from its ridge, which is why one contact point is not enough` | ≥4 mm fall-off 7 mm off centreline | **SURVIVES** — becomes the motivating measurement for the wedge law; the resting-height law check (widthAt) lands next to it. |
| 2293 | `<sample>: the frame rests on the nose rather than inside it` | **the single-push identity**: push = worst interference − PAD_SINK (±0.005 cm), touched ≥ 6, clamped false, re-measured interference = PAD_SINK ± 0.02; reads restedAt in the detail | **REWRITE (5)**. solveRestConfiguration + softmax sideInterference + PAD_SINK′ dissolve the max-based identity. Successor: the five seat measurables (two-sided bearing, standoff law, height equilibrium). Retires seat-cause 1/2 of the seat diagnosis. |
| 2307 | `<sample>: seating the frame changes its standoff and nothing else` | seat moves z only; x/y and pupilHeightInLens bit-unchanged | **REWRITE (5) — by design.** Height becomes an *output* of the seat (descent along bridgeUp); pupilHeightInLens is demoted from constraint to verdict (G17). The clearest chartered rewrite in the suite. |
| 2372 | `<sample>: no part of the frame ends up inside the face` | whole-model non-penetration (0 < deepest < 1 mm) AND deepest agrees with the solve's own worst interference (±0.3 mm) | **SPLIT (5)**: non-penetration conjunct SURVIVES (G2 raw guard exists to keep it, and it bounds the guard); the agreement conjunct encodes worst-point (argmax) semantics and is re-derived against the softmax bearing. |
| 2395 | `<sample>: the standoff slider adds to the seat instead of fighting it` | offsetZ composes after the seat exactly (1e-9) | **SURVIVES (5)** — keep-list ordering; re-prove the identity across the REST_TAU/REST_DEADBAND easing channel (same eased state both runs, or the 1e-9 is luck). |
| 3205 | `a broader nose stands the frame further off the face` | monotone nose-width → standoff via the z-push, >0.5 mm across ±15% | **REWRITE (5)** into the resting-height law: wider nose → rests *higher and further out*; the standoff direction survives, the mechanism and magnitudes do not. `widthAt` (harness-only) is the successor's query. |
| 1501 | `no part of the seated frame ends up inside the head that is drawn` | seated DEFAULT_MODEL vs the *drawn* (relieved) surface: swallowed 0, clearance > 0.5 mm | **SURVIVES — invariant** (single-surface); mechanism under it changes, assertion does not. |
| 1903 | `the hard depth test and the soft fade meet without a step` | OCCLUDER_RELIEF = PAD_SINK + feather + margin | **SURVIVES (5)** — G6 changes PAD_SINK′ *inside the softmax kernel only*; the relief derivation keeps raw PAD_SINK. G6's canonical-face standoff-delta recalibration check (≤0.3 mm) lands beside this one. |

Harness infrastructure, category 2: the `interferenceOf` helper (line 2249)
consumes the full `seat()` report `{push, touched, interference, restedAt,
clamped}` with `limit: 100` as a pure measurement — Stage 5 rewrites it to the
sideInterference / solveRestConfiguration API, keeping the
"measurement-not-correction" unbounded-limit discipline.

## Category 3 — fitLandmarkDepth r2/weight semantics

Owner: **Stage 3** (C7 EMA'd depthFit + nose-window residual gate; G7
visibility-weighted fit input with the measureVisibility reorder).

| line | check | what it asserts | disposition |
| --- | --- | --- | --- |
| 1530 | `nose protrusion is recovered from the landmark depths, not borrowed` | fit-on beats fit-off by 2× RMS and lands < 2 mm | **RE-DERIVE (3)** — G7 excludes behind-cover vertices from the fit's sums, which moves the offset and hence both RMS numbers; the 2× property must survive, the exact figures will not. |
| 1576 | `the depth fit takes its scale from the camera, not from the average face` | fromCamera true, slope = geometric ± 2%, ≥75% of a deep nose kept after 30 frames, depthClamped 0 | **RETIME (3)** — slope-from-camera is keep-list and permanent; the 30-frame convergence is retimed by C7's EMA'd weight (warm-up), so the frame count/threshold is re-measured. |
| 1586 | `landmark depths that do not describe a head are refused, not believed` | the DEPTH_FIT_MIN_R2 global-r2 gate refuses scrambled z (`used === false`) | **REWRITE (3)**. The global r2 gate is measured inert on the user's captures (weight 1.0, sd 0, on all 12 stills) and inflates with pose severity; C7 replaces it with a nose-window residual + EMA'd weight. The *refusal property* is permanent — scrambled z must still be refused — but the gate symbol and threshold die. Retires empirics scan-cause 1. |
| 1620 | `the depth fit is solved against camera depth, not face-space z` | r2(camera) > 0.99 and beats r2(face-space) by 0.05; zero landmarks railed | **SURVIVES (3)** — r2 lives on as a reported statistic; the camera-axis question is orthogonal to the gate. Symbol churn only if the report shape renames. |

Known gap (not a check to rewrite but one to add): the bridge anchor's depth
blend by `depthFit.weight` (anchors.js, one-frame-stale fit) has **no direct
assertion anywhere in the suite** — the empirics show it is the whole-assembly
mover (jitter-cause 1 of the capture diagnosis). Stage 3 must add its check
when the weight becomes EMA'd, and Stage 0's diag-replay pins its aggregate
behaviour (bridge wander span) in the meantime.

## Category 4 — SHAPE_TAU / SURFACE_DEADBAND rebuild cadence

Owner: **Stage 4** (person model + C6 rebuild cadence + G16 shrinkage floor).
Stage 3's wall-time assert (≤1.2× baseline, harness profile) is the successor
of the budget half of 1837.

| line | check | what it asserts | disposition |
| --- | --- | --- | --- |
| 1285 | `the occluder wears this face's nose, not the average one` | ONE `dt: 1` update adopts the observed shape whole (first-sample-adopted-whole EMA), nose within 0.12 mm | **RE-DERIVE (4)** — the view-locked deform keeps single-frame cover (invariant), but G16's shrinkage floor may stop a first sample being adopted 100%; the `deformed` helper's dt:1 settle idiom (line 1225) is re-derived, and with it every check in the occluder block that leans on it. |
| 1297 | `the shell is still watertight after the face moved under it` | boundary edges 0, surface.holes 0 after rebuild | **SURVIVES** — rebuild machinery invariant at any cadence. |
| 1784 | `the mesh still lands on the face at difficult angles` | 20 frames at dt 1/30 (SHAPE_TAU easing) land the visible nose within 7 px at 0/20/40/55° with landmark depth refused — the ungated facing×visibility ramps | **RETIME (3, 4)** — Stage 3's G7 measureVisibility reorder is upstream (expected no-op here, must be re-run, not assumed: frame-one bit-equality is asserted at the reorder); Stage 4 must keep the fast layer converging within the same 20 frames or the check honestly fails. |
| 1804 | `switching the fit off restores the average head, whole` | deform toggle resets the shape state to canonical within 0.2 mm | **SURVIVES (4), extended** — the person model's state (A/b/W, per-vertex noise EMAs) must reset with the toggle, and the check should grow that conjunct. |
| 1837 | `the deformation fits inside the tracking loop` | steady < 2.0 ms; forced rebuild (via `driftSurface = Infinity` poke) < 13.0 ms | **REWRITE (4)** — C6 changes the cadence and the drift bookkeeping the check pokes; budgets are re-derived from the Stage 0 wall-time baseline (diag-replay: updateFrame 11.4 ms aggregate mean on this machine, reported never asserted there). NOTE: this is the suite's only machine-load-sensitive check, and during Stage 0
  verification it failed **repeatedly** in the hidden (CPU-throttled) Browser
  pane: forced rebuild measured 13.93 / 13.28 / 13.87 ms over three runs
  against the 13.0 ms bound, steady always comfortably green (1.76–1.81 ms
  vs 2.0), every other check green all three runs. Stage 0 changed no
  production code, so this is the environment sitting ~3–7% over a wall-clock
  budget, not a regression — but it must be re-run in the user's real Chrome
  (per the verification memory note) before any stage treats the suite as its
  green baseline. |

## Counts

- Category 1 (latch / gating / payload): **12 checks** (+1 helper), of which
  1 REWRITE, 2 RE-DERIVE/RETIME, 9 SURVIVE-with-notes.
- Category 2 (seat report / single push): **10 checks** (+1 helper), of which
  3 REWRITE, 1 SPLIT, 6 SURVIVE (2 extended).
- Category 3 (depth-fit r2/weight): **4 checks** (+1 named gap), of which
  1 REWRITE, 2 RE-DERIVE/RETIME, 1 SURVIVES.
- Category 4 (SHAPE_TAU / deadband cadence): **5 checks**, of which 1 REWRITE,
  2 RE-DERIVE/RETIME, 2 SURVIVE (1 extended).

Every REWRITE/RE-DERIVE above must land in the same commit as the stage that
motivates it, citing the diagnosis finding it retires, with the diag-replay
A/B green against `ar/tests/diag-baseline.json`.
