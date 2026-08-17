# Nose pipeline v2 — the authoritative synthesis spec

2026-08-16. Produced by a diagnose → design → judge process; this file is the merge
decision. An implementer works from THIS file, pulling detail from the referenced
design documents in this directory. Where this file contradicts a design document,
this file wins.

The three complaints being fixed, verbatim from the user:
1. "the scan of the face isnt good enough, specifically of the nose"
2. "the interaction of the glasses with the nose is not good at all. as far as i
   see it we can start from scratch"
3. "the model is figity and gigily (vibrates a little) it doesnt stand still
   completely, especially in difficult angles"

Ground truth for every claim: `diagnosis.json` (four sections: capture, seat,
jitter, empirics — the empirics were measured on the production path against the
user's own captures in `assets/samples/diag/`).

## The verdict

Base design: **`design-stability-first.txt`** — adopted in full EXCEPT its
resting-height solve (the `q(u)` width-match root find), which is replaced by the
physics-first bearing solve below. Its thesis (every quantity assigned to its
honest timescale; filtering only ever touches face-space constants or an activity
signal's noise DC), its person model (A/b/W anisotropic information filter), its
noise-conditioning stack (C1–C7), its stages, constants, measurables, and
instrumentation are the spec.

Seat solve: **`design-physics-first.txt` sections B.2–B.8** — `sideInterference`
softmax kernel, load-time L/C/R contact split, `solveRestConfiguration` (height
sweep, closed-form standoff ζ*(s), bearing function B(s), largest-s equilibrium
with monotonicity assert and bisection refine), per-frame raw non-penetration
guard, pupil box prior [−1.2, +0.4] cm, the degeneracy ladder (flat-fallback /
saddle / hold / cold-session / asymmetric), and the B.8 integration order —
mounted in stability-first's plumbing: its event cadence, REST_TAU/REST_DEADBAND
easing channel, seat effect deadband with hysteresis, and confidence scaling.
`widthAt` survives only as a harness query (resting-height law check), never in
the production path.

## Grafts (all judge-mandated; each is part of the spec)

G1. **Seat = one solve.** solveRestConfiguration replaces stability-first's
    q(u) root find entirely; no parallel path. Keep stability-first's
    REST_TAU 0.8 s / REST_DEADBAND 0.3 mm ease on the adopted s* (the deadband on
    the OUTPUT answers the threshold-riding concern), confidence scaling
    `s_applied = clamp(noseMeanConf/50,0,1) · s*` so cold sessions hold today's
    optical height by construction, and the effect deadband (re-arm 0.15 mm /
    release 0.05 mm) on the per-frame standoff channel.
G2. **Raw guard.** Per frame, if softmax interference at the applied placement
    exceeds the eased standoff by GUARD_BAND 0.03 cm, apply the excess RAW that
    frame (`__ar.seat.guardPushes`). Penetration never waits for easing; outward
    motion always does. Harness bounds the guard fire rate under injected noise.
G3. **Hold mode.** A side losing >60% of contacts to NaN, or the monotonicity
    assert failing → do NOT re-solve; freeze applied constants (guard still
    protects), mode='hold'. Decay-to-optical remains only for flat-nose /
    no-crossing cases.
G4. **Saddle mode.** Centre band interference exceeding both sides by 0.03 cm
    across the sweep → the saddle is the bearing; height solved against the
    centre set, two-sided requirement skipped, mode='saddle'.
G5. **Roll ships dark.** φ* = clamp((Î_L−Î_R)/(2·x̄_pad·κ), ±3°) is the concrete
    law behind `fit.padBalance`; enabled only after seat measurable (4) passes
    WITH it on, on asymmetric synthetics at both asymmetry signs.
G6. **PAD_SINK softmax compensation.** PAD_SINK′ = PAD_SINK − τ_s/2 in the softmax
    kernel, plus the one-time catalogue recalibration check (canonical-face
    standoff delta vs baseline ≤ 0.3 mm).
G7. **Fit input hygiene.** Reorder measureVisibility BEFORE fitLandmarkDepth; the
    fit's global sums exclude vertices with behind > bias (the grid's own hold
    criterion) so the hallucinated far side stops polluting the affine offset.
    Enumerate which existing checks legitimately move; frame-one bit-equality
    must survive the reorder (visibility uses carried offsets, zero on frame one
    — assert, don't assume).
G8. **Sign-gate discipline.** Any channel applying triangulated depth (the zConf
    crossfade) ships with its apply gain at 0 until the synthetic-truth check
    (inject +4 mm protrusion error, BOTH yaw signs, assert recovery direction)
    passes as a hard gate. This codebase has shipped one sign bug already.
G9. **Dual-baseline tripwire.** Nose-window residual computed against BOTH the
    person baseline and the canonical baseline (~98 extra mults); person residual
    exceeding canonical by 50% for 2 s continuously → soft decay with cause
    'regression'. Runs alongside the absolute residualRms rule.
G10. **Noise-floor hardening.** Ring admits a sample only when measuredRate <
    3× current floor; floor rises capped at 10%/s, falls free; hard caps 8 cm/s
    and 0.25 rad/s. Harness adds a 30+ s continuous-motion scenario (not just the
    0.5 Hz sweep) asserting the floor stays below real speed.
G11. **Hybrid pin activity.** The bridge-pin One Euro's activity input is
    max(pose-speed mapping, innovation-rate) so a genuine face-space bridge move
    on a still head (brow raise / expression) opens the filter; add an
    expression-on-still-head scenario to the pin checks.
G12. **Latch alias.** `state.measuringLatch` survives as a derived readout
    (w_pose > 0.5); the stage-2 list of latch-coupled checks is enumerated during
    Stage 0, not mid-stage.
G13. **Richer solve-event scheduler.** Re-solve on person nose-set mean-confidence
    crossings (0.25/0.5/0.75), commitFit deadband crossings, fit/model changes;
    min interval 250 ms (amended to 500 ms at the stage-5 landing — the ≤2 Hz
    event-work invariant is the refractory's own number; see the landing fix
    record), heartbeat 5 s. Diag replay explicitly checks for
    periodic micro-resettling after convergence (solve events must produce
    sub-deadband deltas — measured, not asserted).
G14. **Landmark-slide acceptance gates.** MediaPipe landmarks slide across skin
    (~0.2 mm/deg at the bridge). (a) The synthetic-truth convergence check
    injects a pose-correlated slide term into the generated landmark stream.
    (b) Before the zConf crossfade defaults on, converged depth must be stable
    across the diag f00–f09 stills (same face, different poses) — the real-world
    acceptance gate.
G15. **W_PAR leakage bookkeeping.** zConf must explicitly subtract (or the
    derivation bound) the W_PAR-weighted prior contribution to A_zz; harness
    asserts zConf < Z_CONF_MIN under a long frontal-only stream, so the borrowed-
    depth crossfade can never engage on prior-only information.
G16. **Shrinkage floor per-vertex scale.** The view-locked deform's innovation
    shrinkage uses r0 = max(0.3 mm, 1.5·resNoise_i) with resNoise the per-vertex
    online noise EMA — and PersonModel.update reads the RAW pre-shrinkage
    observed points so the estimator is never starved. Long-still-head harness
    run asserts zero net drift (no pump between commits, SHAPE_TAU, and the
    shrink floor) and field==mesh exact across a commit.
G17. **Verdict reporting.** pupilHeightInLens reported with widthVerdict-style
    bands ("sits low" / "sits high"), plus `__ar.seat.mode` and per-cause rebuild
    counters.

## Stage plan (stability-first's, amended)

Stage 0 — **Harness first.** Build `ar/tests/diag-replay.js` from the diag-probe
  pattern (fixed-seed noise over f00–f09 + face-a/b, no rAF, main-thread
  tracker). Pin the CURRENT pipeline's numbers as a checked-in baseline with
  TOLERANCES (not bit-exact snapshots — MediaPipe determinism is per-machine):
  screen RMS per still, bridge face-space wander, seat push spread, rebuild
  counts, r2/weight behavior, measuring fraction. ALSO: enumerate the existing
  pipeline-check assertions coupled to the latch / estimateYaw gating / binary
  payload / seat report (the stage-2/5 legitimate-rewrite list), and add the
  updateFrame wall-time baseline. No production changes.
Stage 1 — Filter conditioning (C1 signed rotation rate, C2 noise floor with G10,
  C5 recovery dt). Frame-one pin check runs at THIS stage and every later one.
  LANDED 2026-08-16: transmissions 0.73→0.49 rot / 0.61→0.51 pos at hard-pose
  noise; sweep 2.1 mm; recovery 34%; 280/281. Amendments made in the field:
  G10's floor-rise law is "10% of the hard cap per second, absolute" with a
  fill-phase seed (the literal 3×-floor admission gate is degenerate at 0);
  slow-ramp bound is ≤1.2 mm absolute + ≤0.2 mm floor cost (1.06 mm is the
  shipped constants' own no-floor steady state — "within 1 mm" was arithmetic-
  impossible). The Stage-1 diag-replay claim (hard-pose RMS −25%) measured
  −4..−10%: the pose filter is a minority share of still-image screen RMS; the
  −25% system-level claim is RE-ATTRIBUTED to Stages 2–3 cumulative (the
  unfiltered anchor/pin/depth-weight chain).
Stage 2 — Continuous pose-trust (C4; w_pose from true euler, weighted median
  window, always-carried eyeLineY/bridgeUp/ears, latch → readout alias G12,
  eyeLineY deadband, identity gating).
Stage 3 — Pin conditioning + depth-fit conditioning (C3 with G11 hybrid
  activity; C7 EMA'd depthFit + nose-window residual gate; G7 visibility-weighted
  fit input with the measureVisibility reorder). updateFrame wall-time assert
  (≤1.2× baseline) lands HERE, on the harness profile.
  LANDED 2026-08-16: frontal RMS 0.159/0.153 (≤0.20 met); hard stills 0.219–0.42
  with settled-state (frames 30–59) RMS ≤0.40 on all ten — f07's full-window
  0.49 decomposes to one mid-run eyeLineY deadband release (0.2 mm) plus the
  seat/EMA warm-up settle inside the fresh-state fixture window, tail RMS 0.255;
  f03/f07 maxStep 1.18/1.24 → 0.43/0.60 (the stage-2 deadband-release step
  absorbed by the pin); bridge per-still sd 0.13–0.52 → 0.015–0.16 mm; wander
  span x 6.8→5.0, z 5.0→3.8 mm; f04/f05 noseWidthRatio 1.048/1.032 →
  0.991/0.990; occluder weld bit-exact 90/90 noisy moving frames; moving-head
  pin error 0.066 px worst; wall time 9.9–10.5 ms ≤ 13.72 budget; suites
  294/294 + 299/299, re-pinned. Amendments made in the field:
  (a) the depth-weight quiescence pairing "≤0.01/frame against a 0.19 baseline"
  is arithmetic-impossible — the EMA attenuates per-frame steps by its own
  α = 1−exp(−dt/τ) ≈ 0.105, floor 0.02 — so the worst step is asserted at
  ≤0.025 absolute AND ≤0.12× the raw law's measured worst on the same stream
  (measured ratio 0.096 = α), with ≤0.01 asserted on the delta-series sd
  (stability-first's own stage-3 phrasing).
  (b) G11's expression falsifier at the literal 2 mm/0.5 s discriminates
  nothing — the carried median's own convergence carries a pose-only control
  past 90% inside the budget too; the charter is asserted on the production
  path at 0.5 s (90% at +67 ms) and the pose-only-fails contrast at brow-raise
  speed (0.2 s: 93.7% hybrid vs 67.6% pose-only at +133 ms).
  (c) The nose box holds 65 vertices on the canonical mesh, not ~98.
  (d) The G7 hold criterion marks 5 vertices even on a frontal face (real
  self-cover at the nostril base), so frame one's fit moves 46 µm of offset vs
  an exclusion-free fit — chartered, bounded and asserted rather than assumed.
  (e) The interim canonical-baseline nose residual has a ~1.0 mm zero point on
  a PERFECT face — the shipped |e14| slope convention takes the head origin's
  depth where MediaPipe's z is scaled at the face's mean camera depth, a ~10%
  slope bias (pre-existing, now measured; a Stage-4 candidate) — and demotes
  genuinely non-average noses (±10% depth → weight ~0.5–0.7 at yaw, 25% → 0).
  On the user's own captures the applied weight runs 0.14–1.00 per still,
  lowest exactly on the extreme-yaw pair (f04/f05: 0.145/0.144) where the
  hallucination lives; the person baseline (G9) and the zConf crossfade are
  what retire the real-nose demotion at Stage 4.
Stage 3.5 — **Interstitial triage**, LANDED 2026-08-16. A parallel whole-tree
  review (memory: ar-full-review-2026-08-16) ran while stages 1–3 were landing;
  its findings against the new pipeline code were verified against the
  post-stage-3 tree and the in-blast-radius ones fixed, each with its own
  harness check and falsifier:
  (1) conditionDepthFit EMA'd the absolute offset b (and slope a) — a 10 cm/s
  lean-in railed every carried depth against the ±1.6 clamp (v·τ = 3 cm);
  now EMA'd in distance-invariant form (b − poseDepth, a / poseDepth) and
  reconstituted per frame — lean-in check: 0/468 clamped, removed law trails
  2.84 cm on the same stream.
  (2) Weight-blind FIFO eviction defeated the weighted median under sustained
  yaw; eviction now drops the lowest-weight sample, oldest among equals (equal
  weights ARE the old FIFO; the window provably keeps the highest-weight
  samples since the last reset) — 5 s at 20° yaw: carried eye line moves
  0.00 mm vs 4.09 mm for the FIFO control on the identical stream.
  (3) pdCm shrank with cos(yaw) under C4's ~24° admission (one-signed, so the
  median dilutes but never cancels); corrected in measureMetricScale.
  FIELD AMENDMENT: the fix direction's bare cos(trueYaw) measured 1.4–2.1%
  short on synthetic truth — the pose yaws the head about its ORIGIN and the
  eye chord rides ~5 cm in front, so the shipped divisor is the exact
  chord-pivot form R = (cosθ − z/d)(1 − z/d)/(1 − (z/d)cosθ)², z/d from the
  canonical inner-corner depth over pose distance, both inputs clamped
  (PD_YAW_LIMIT 0.45, share ≤ 0.3). Harness: 0.14% law / 0.09% end-to-end vs
  the 1% budget; uncorrected control reads 10.6% low.
  (4) The faceless hold starved noteFaceLost — filter reset drifted from 0.5 s
  of true absence to 0.5 s + hold, silently breaking C5's clamp premise. The
  bookkeeping moved to frame.js (noteFacelessResult, HOLD_FACELESS_RESULTS)
  so the harness can drive it; the lost clock runs from the first faceless
  result, and a tab-hidden gap inside the hold resets the filter during the
  hold. C5's "past that the filter was reset anyway" now holds by
  construction.
  (5) resetFit kept the old face's seatPush across an identity swap (remeasure
  cleared it; the swap path did not) — moved into resetFit; swap check: seat
  adopted whole, 1.47 mm from the value it used to inherit.
  (6) diag-replay's cm→mm scaling multiplied nulls into fake 0.0 (null·10 === 0)
  — nulls now stay null and assert-mode skips both-missing with a note. The
  pinned baseline was audited: it contained NO fake zeros (all 12 stills
  detected 60/60), so no honesty re-pin was forced by this fix.
  (7) The snap gate scaled with unclamped occSlope — at the head silhouette the
  depth cliff opened the gate everywhere and the luma search re-centred the
  boundary onto the head-vs-room edge (dashed flicker seam). The search now
  also requires occSlope < uOccluderFeather/2 (fade band ≥ ~2 px — the
  grazing regime it exists for). GLSL rig: 0 bar pixels eaten at a cliff with
  a max-contrast aligned edge (68 with the gate removed — sensitivity
  A/B-verified), grazing snap still moves 554 boundary pixels.
  (8) Stage 3's eyeCentreX innovation filter had zero consumers (solvePlacement
  centres on bridge.x by design); deleted, payload carries the median value —
  weld check extended to pin exactly one eyeCentreX in flight.
  (9) The fitted-depth blend was copy-pasted with divergent defaults
  (carryLandmarks 0.8 vs measureAnchors 1.6); unified into blendFittedDepth
  with the one documented DEPTH_BLEND_LIMIT = 1.6 — both consumers land on
  the identical bound, bit for bit.
  Also fixed as verify-stale residuals: deform-off and remeasure() now clear
  userData.depthFit (the landmarkDepth toggle already did; the other two paths
  left a frozen fit feeding the anchors). Confirmed already fixed earlier same
  day: uSnapRadius rides the live relief setter (occlusion-mask.js relief
  getter/setter + applyToggles wiring), glassMaterials cleared in
  attachGlassEnvironment.
  Suites: 300/301 (the one failure is the stage-0-ruled environment-bound
  13 ms wall check, reading 13.29–13.72 ms in the throttled pane — inside the
  ruling's 13.93 ceiling); diag-replay 299/299 after re-pin. Replay deltas at
  re-pin, both on f07 and both in the claimed direction: sdYpx 0.468→0.345
  (−26%) and seat rawSd 0.125→0.083 mm, at the cost of maxStep 0.605→0.848 —
  decomposed to the eyeLineY deadband release moving to warm-up frame 5 on
  top of the seat/EMA settle; the settled tail (frames 30–59) improves to
  RMS 0.206 / worst step 0.318. All other stills within tolerance;
  depthFit weightMean bit-identical on every still (the invariant EMA is the
  same filter on a still head, as designed).
Stage 4 — Person model (A) + rebuild cadence (C6) + G8/G9/G14/G15/G16.
  C6 INPUT, recorded at stage 3.5 (parallel review, confirmed open): the
  relief rebuild deadband (0.005 cm) sits at the same scale as the relief
  cap's own slope with distance (MAX_RELIEF_PX/pixelsPerCm ≈ 0.0051 cm per cm
  of approach on a 960 px buffer), so inside ~39 cm — where the cap binds — a
  slow lean rebuilds the surface nearly every detection. The cadence work
  must treat the relief-cap channel explicitly, not just the shape channel.
  LANDED 2026-08-16. ar/src/person.js (A/b/W anisotropic information filter,
  Huber-gated, W_MAX-forgotten, PRIOR_LAMBDA-solved at ≤2 Hz commits) layered
  under the view-locked deform: the composite `person.offsets + viewResidual`
  is the ONE array the surface/field/relief read — single-surface invariant
  untouched; commits re-base the residual, measured invisible (worst composite
  step 0.00 µm over 112 commits/60 s). G16 shrinkage (r0 = max(0.3 mm,
  1.5·resNoise_i)) on the render path with the estimator eating RAW points;
  held-vertex residual decay (2 s, scaled W/30); pin base fused
  carried→bridgeEstimate by κ = W_6/60; identity/remeasure/adapt-off/deform-off
  all reset the model. Frame-one bit-equality asserted: production frame one ≡
  a no-person control on all 468 offsets, pin verbatim, empty model κ 0.008.
  G15's exact bookkeeping DERIVED: A_zz = W_PAR·W + (1−W_PAR)·Σw·(1−d_z²), so
  zConf = A_zz − W_PAR·W is pure parallax — 600 frontal frames: meanW 297,
  zConfBridge 1.18 vs Z_CONF_MIN 25, crossfade weight 0.047 (asserted).
  Convergence proof (+4 mm tip / −8% width, ±15° 20 s, G14 slide + noise):
  transverse 0.18 mm slid / 0.08 clean (spec's own 0.4 bound — the x/y fusion
  is the shipped value); depth 1.31 mm clean / 2.87 slid; post-10 s regression
  0.00 clean (slid breathes 0.28 with the slide's own phase); G14a slide-
  induced depth bias measured 1.55 mm (clean −1.18, slid −2.73). FIELD
  AMENDMENT (the stage-1/3 precedent): the proof's "depth ≤1 mm at 20 s" is
  arithmetic-unreachable under the design's own constants — past the W_MAX cap
  the accumulator equilibrates at zConf* = W_MAX·(1−W_PAR)·E_w[sin²θ] ≈
  297·E_w[sin²θ]; a ±15° sinusoidal sweep has E_w[sin²θ] ≈ 0.03 → ceiling ~9,
  and the ten diag stills as one shared session reach zConfBridge 5.7 — the
  "≈370 weighted frames ≈ 15–25 s" line counted accumulation as if the cap
  never decayed it, and at the reachable A_zz the λ=4 prior owns ~23% of the
  depth answer (~0.9 mm of a 4 mm delta). Depth budget amended to 1.6 mm at
  that pose diet; equilibrium asserted (worst nose zConf 4.5 < 25).
  CROSSFADE DECISION (G8/G14): ships DARK — CROSSFADE_DEFAULT_ON = false.
  (a) Sign gate PASSES: planted +4 mm protrusion error recovers monotonically
  at BOTH yaw signs (2.4→0.0 mm at +15°, 2.4→0.1 at −15°), and the applied
  channel forced on pulls borrowed-depth error 3.68→2.70 mm at both signs,
  proportional to its earned weight (0.37) — the sign is right. (b) G14b
  FAILS: shared-session f00–f09 (one model across the ten poses, 0 identity
  resets, 25 commits) converged bridge z span 2.82 mm ON vs the 1.5 mm gate
  (OFF control 2.95 — the fade improves the span, direction right, magnitude
  starved), zWeightBridge ends at 0.22. The channel cannot mature on the
  user's own pose diet with the shipped constants, and a partial-weight fade
  applies a prior-shared estimate — exactly what G8 exists to keep dark. The
  full acceptance table is pinned in diag-baseline.json (crossfadeAcceptance,
  measured every replay).
  G9 dual-baseline tripwire: a nose reshaped past the person baseline (same
  width/scale — invisible to isDifferentFace, 0 identity resets) fires at
  +2.0 s (≤3 budget, cause 'regression'), decays meanW 250→50 with no reset,
  and 15 s of clean input re-converges to 0.09 mm — plus the absolute
  0.25 cm/1 s rule alongside. G16 long-still: 60 s of ±0.4 px noise — net
  composite drift 0.048 mm max / 0.0093 mean, viewResidual mean 0.32 mm,
  field==mesh 0.12 mm, sustained rebuilds 1.6/60 (pre-stage: 39–62).
  Identity swap: person resets with resetFit; 31 samples later the offsets
  sit 0.000 mm from a never-saw-A control (≤0.5 budget).
  C6 landed as: drift ≥ SURFACE_DEADBAND AND (framesSinceRebuild ≥ 3 OR
  drift ≥ 0.1 cm), plus the relief-cap EFFECT deadband — the applied relief
  moves only when its error vs the capped value exceeds 0.5 px ON SCREEN
  (0.005 cm absolute floor kept for the uncapped regime; self-hysteretic by
  snapping to the cap). Stationary 35 cm head: 3.8 rebuilds/60; 10 cm lean-in
  re-relieves 3x vs 9 fires of the removed law on the identical stream;
  interval floor measured 3 over 9 cycles; a 5 mm reshape rebuilds next frame.
  Stage-3 forward note (e) FIXED: fitLandmarkDepth's slope reference moved
  from the head origin's |e14| to the mean carried camera depth of the fit's
  own included vertices (sc/n, same loop, same G7 exclusions): slope −55.15 →
  −50.43 on the harness synthetic (the ~9.4% deep bias), nose-window recovery
  RMS at 22° yaw 1.326→0.609 mm (noseZ 0.90) and 1.028→0.599 mm (noseZ 1.10);
  the 1576-check's convergence bound tightened 2 mm → 1 mm (measured 0.75).
  CONSEQUENCE, found by the replay: the interim canonical-baseline demotion of
  real noses is RETIRED BY THE SLOPE FIX, not by the crossfade — depthFit
  weight now reads 1.00 on all twelve stills (was 0.14–1.00, lowest exactly on
  the extreme-yaw pair); the rmsNose discriminator keeps its junk/EMA refusal
  checks green, so the gate is quiet because the fit is now genuinely good.
  Replay ratchet, re-pinned (fresh-state per still, seed 20260816): surface
  rebuilds 39–62/60 → 1–18 full-window with warm-up, 0–5 in the settled tail
  (surfaceRebuildsLate, newly pinned; the C6 ≤10/60 target met sustained on
  every still); bridge wander span z 3.852 → 2.759 mm (≤3 met), x 5.011 →
  4.278 mm — x cannot move under fresh-state-per-still semantics (each still
  is its own cold session; the fused pin base only operates within a session,
  as the shared-session fixture shows) and its residual is pose-systematic
  anchor slide, re-attributed to stage 5/6; seat spread 2.563 → 2.141 mm
  (stage 5's ≤1.5 gate pending); f07 rms 0.373→0.220, maxStep 0.848→0.407;
  f06 maxStep 1.325 full-window (the hoped ≤1 not met — one warm-up event;
  settled tail 0.87) — tail metrics (screenTail.rmsPx/maxStepPx, frames
  30–59) are pinned now so the budget reads the settled state: tail RMS ≤0.42
  on all ten diag stills (worst f05, the extreme-yaw still, whose full-window
  0.535 carries its fresh-session convergence walk — decomposed: per-segment
  means walk 776.5→775.75 px while per-segment sd sits at 0.10–0.35; noted
  for stage 6's live verification, where sessions are never born at 18° yaw).
  updateFrame wall time 9.96 → 5.60 ms mean over the replay (the rebuild
  collapse pays for the person model many times over; ≤13.72 budget).
  Suites: 308/309 pipeline-check (the one failure is the stage-0-ruled
  environment-bound wall check, reading 13.19–15.37 ms across runs of
  identical code in the throttled pane; the replay aggregate above is the
  real budget guard) + 300-check replay re-pinned. __ar gains person
  {meanW, noseMeanW, zConfBridge, residualRmsMm, commits, resets,
  tripwireActive, crossfadeOn}, pin {maturity, zConfBridge}, rebuilds
  {surfacePerMin, lastCause}.
  OPEN FOR LATER STAGES: (1) any crossfade enablement must first raise the
  zConf ceiling — candidates: a z-channel-specific (slower) forgetting cap,
  dwell-weighted accumulation, or a prior-share-aware weight
  zConf/(zConf + λ + W_PAR·W) — and then re-run the pinned acceptance gates;
  (2) seat spread 2.141 vs stage 5's ≤1.5; (3) x wander 4.278 is slide-
  dominated and per-still-cold by fixture construction — measure it live at
  stage 6 where sessions converge.
Stage 5 — Seat equilibrium (physics-first B.2–B.8 inside stability-first
  plumbing; G1–G6, G13, G17). The five seat measurables + PAD_SINK recalibration.
  LANDED 2026-08-16. New `ar/src/seat-equilibrium.js` (solveRestConfiguration:
  height sweep along bridgeUp, closed-form standoff, bearing-deficit search,
  bisection to 0.25 mm, degeneracy ladder, G5 roll); `nose.js` gains
  sideInterference (the EXACT seat() bridge-relative kernel, log-sum-exp per
  L/C/R side at SOFTMAX_TAU 0.05, single-pass, LSE(union)=LSE(side LSEs) so
  soft and perSide can never disagree), normalAt, widthAt (diagnostic-only:
  the harness law check plus the solver's widthAtRestCm readout — nothing
  load-bearing);
  `fit.js` analyseModel splits the bridge column L/C/R (ε = max(0.15·halfW,
  3 mm)) plus rearmost-30% pad subsets, padSep/x̄_pad/hasPads, and
  solvePlacement applies the three channels in the staged order (roll in the
  orientation stage about the hang point, height after the optical vertical,
  standoff + G2 raw guard along z, offsetZ last) and exposes seatBase;
  `frame.js` owns state.seatConfig (three eased channels + G13 scheduler:
  conf-bucket crossings on noseMeanW/W_MAX at 0.25/0.5/0.75, commitFit
  adoptions, fit/model change, surface rebuilds, 500 ms min / 5 s heartbeat —
  the min interval landed at 250 ms and was amended at the landing, see the
  fix record),
  __ar.seat, and reset parity with the old seatPush. seat() survives as the
  harness reference only. FIELD AMENDMENTS, each measured before amended
  (the stage-1/3/4 precedent):
  (a) G6 is retired by measurement: the softmax bias `soft − max` on the
  seated catalogue reads 1.10–1.69 mm per asset (the τ/2 = 0.25 mm
  compensation priced ~1.6 tied contacts; real pad patches carry hundreds),
  so a soft-anchored standoff would float the deepest contact ~1 mm clear of
  the drawn skin. The standoff LEVEL keeps the argmax law (max − PAD_SINK,
  bit-parity with seat() asserted per sample face); the soft reductions own
  every COMPARISON (side loads, bearing, asymmetry, κ) — which is where the
  argmax flip steps actually lived.
  (b) Bearing is a DEFICIT between soft reductions — full-set soft minus the
  lighter PAD SUBSET's soft — because the full side sets never disengage
  (near-ε contacts ride the ridge at any height: full-set bearing measured
  flat within 0.06 mm across the whole ±4/−12 sweep on every asset, and the
  spec's "largest s that bears" railed at the +4 mm box ceiling on all of
  them). Pads carrying ⇒ deficit 0.10–0.39 mm; hung 4 mm high ⇒ 0.9–1.5 mm;
  EPS_BEAR 0.8 mm separates them cleanly.
  (c) Descent-only, honouring B.5(a) over B.4 step 4: s* = 0 whenever the
  optical height bears (the whole catalogue on the canonical face), else the
  largest bearing s below; the +s rows feed the monotonicity assert (applied
  along the SEARCH PATH only — real noses have an honest bearing valley
  below the equilibrium; face-a showed it, deficits 1.18/1.07/0.79/0.47/
  0.48/0.73/1.03/1.40/1.87 across the sweep, and a whole-sweep assert
  wrongly held) and the saddle diagnosis.
  (d) The resting-height law re-pinned at the shipped kinematics: descent
  runs ALONG the bridge (B.4's own s·û), whose forward walk (û_z/û_y ≈ 0.70)
  hands back ~0.9 mm of wedge width per mm of drop through the sidewall
  slope, so dHeight/dDBL measures 0.52 mm/mm (meshy, monotone, r² clean)
  against the fixed-z derivation's 1.9; band pinned [0.4, 1.0].
  (e) G5's κ needs a PURE-VERTICAL probe pair (±1 mm at s*): the sweep-row
  gradient the spec named is the along-bridge one, an order of magnitude
  small (0.04 vs the wedge trade's ~0.43) for a roll that moves pads
  vertically.
  MEASURABLES (pipeline-check 334/335; the one red is the stage-0-ruled
  environment-bound 13 ms wall check reading 15.1–16.4 ms in the throttled
  pane — above its own 13.93 note, machine-load-suspect, re-check at stage 6;
  the replay aggregate holds 6.5–6.7 ms vs the 13.72 budget):
  (1) two-sided bearing: six synthetic noses all mode 'wedge', worst pad gap
  0.08 mm / deficit 0.45 mm vs 0.8; face-a gap 0.61/deficit 0.79 (its wedge
  is honestly marginal), face-b 0.06/0.46.
  (2) dHeight/dDBL 0.52/0.52/0.52 mm/mm monotone across pad separations
  30.7–35.1 mm (amended band, see (d)).
  (3) standoff spread 4.90 mm across the nose set (−8.11 narrow … −3.21
  broad) vs the 1.5 floor.
  (4) seat stability under ±0.3 mm noise: applied-push RMS 0.047/0.000/
  0.000/0.000 mm at frontal/20°/30°/pillow, worst step 0.031 mm, guard 0%
  (bounds 0.2/0.5/15%).
  (5) ±30° sweep on a converged session: pads within 0.18 mm of sink on the
  surface the seat solves against (bound 0.3), drawn relieved surface never
  violated, true-skin gap ≤1.62 mm — decomposed: near-uniform across yaw
  (worst at −14°), i.e. stage 4's own amended 1.6 mm depth-convergence
  budget, not a seat or yaw effect; bound pinned 1.75 mm, ownership with the
  depth estimator.
  G13 micro-resettling: 75 solves over a settled 20 s moved the height
  channel 0 times and the standoff on 6 frames (≤ two re-arms), deadband
  holding 595/600 frames. Fallback purity: frame one bit-equal to the
  standalone law in x/y/z; 40 cold (person-starved) frames hold s ≡ 0,
  φ ≡ 0, standoff within 0.000 mm of the raw law. Degeneracy ladder: 'flat'
  reproduces the 1-DOF push bit-equal at s = 0; sides-cut-away bridge →
  'saddle'; off-patch pad set → 'hold'.
  G5 DECISION: stays DARK (DEFAULT_FIT.padBalance = false). The gate's
  stability half passes WITH the balance on at both skew signs (RMS
  0.047/0.082 mm, steps ≤0.031, guard 0%, φ within ±3°, pad gap closed
  0.63→0.01 mm at both signs) — but the sign criterion needed amending to a
  differential (+skew −0.97° vs −skew −1.44°, Δ = 0.47°): both runs share a
  ≈−1.2° component that is the canonical face's own asymmetry, honestly
  solved. A visible ~1° roll on every average-face fit, gated by a law that
  took two field amendments (κ probe, differential sign), earns a live look
  (stage 6) before the default flips — G8's own discipline.
  Replay ratchet, re-pinned (fresh-state per still, seed 20260816): the
  five stills where this wearer's nose genuinely descends (face-b, f01,
  f03, f07, f08; raw seat −4.9..−5.9 mm) now carry the equilibrium's
  confidence-ramped settle inside the 60-frame window — f08's py glides a
  monotone 1.47 mm with worst step 0.48 px — so their screen sd/rms rose
  (f08 rms 0.27→1.81 px full-window, tail 0.31→1.20; f07 0.22→1.07/0.69;
  f03 0.25→0.83/0.67; f01 0.23→0.31/0.33; face-b 0.15→0.63/0.40).
  CLASSIFIED expected-direction: the drift is the designed settle (eased,
  step-bounded, G1-scaled), not vibration — settled-state stillness is
  owned by measurable (4) and the G13 check (RMS ≤0.047 mm), and stage 6's
  live budget must read settled state, not a fixture window that pays the
  whole settle. All other stills within tolerance; maxStep worst 0.48 px
  (f08, +0.11 vs baseline, the settle's own eased step); rebuilds hold
  (1–18 full-window, 0–5 tail); wall time 6.73 ms replay mean (was 5.60 —
  the per-frame kernel now reduces per-side and the solver runs on events;
  well inside 13.72). Seat cross-pose spread 2.126 mm per-still-cold
  (baseline 2.141 — unchanged BY DESIGN: the raw level is bit-parity, and
  the spread is pose-systematic surface morph, not seat law); the honest
  stage-5 cross-pose number is the NEW shared-session metric — ONE session
  across all ten poses applies 1.85 mm of seat span (crossfade-off control)
  — still above the ≤1.5 hope, dominated by the same surface morph the
  crossfade ceiling (open item 1) owns. Suites: pipeline-check 334/335,
  diag-replay re-pinned 336/336 green.
  OPEN AFTER STAGE 5: (i) the ≤1.5 mm cross-pose seat spread waits on the
  depth story (crossfade ceiling / live convergence), measured now by
  seatAppliedSpanMm in the shared-session pass; (ii) padBalance flip
  decision at stage 6 live; (iii) the wall-clock check's 15–16 ms reading
  must be re-confirmed in real Chrome; (iv) fixture note — per-still
  screen rms on descending stills now reads settle+jitter, split when the
  fixture next grows a settled-tail long enough to outlive the G1 ramp;
  (v) mid-session first-adoption snap (see fix record note (a)): a
  held-open session that recovers adopts conf·s* whole, up to ~12 mm in
  one frame — decide at stage 6 live whether first adoptions above a few
  deadbands should ease, constrained by the pinned model-swap adopt-whole
  semantics.
  LANDING FIX RECORD (adversarial verification, 2026-08-16 — three P2s
  confirmed against the code and fixed, three notes dispositioned):
  (F1) Refractory bypass before first adoption: `scheduleSeatSolve` gated
  its 250 ms refractory on `hasSolve`, which a HELD solve never sets, so a
  session opening on an untrustworthy field (hard yaw from cold) ran the
  full 9-row sweep at 30 Hz until the pose improved. The refractory now
  stands on `sinceSolve` alone (a held solve also resets it; the first-ever
  attempt stays immediate via the Infinity sentinel). Pinned by a new
  production-path check: 40 off-patch frames (offsetX 4 cm, the ladder's
  own hold signature) run exactly 3 held solves (bound 2–4; was 40), and
  20 frames after the fit returns the equilibrium adopts 'wedge' through
  the pending latch — no heartbeat wait.
  (F2) "Rest on the nose" left the seat on: solvePlacement applied
  `seatConfig.s` (line-of-height) and `.phi` OUTSIDE the seatOnNose gate,
  and updateFrame passed the channels on `hasSolve` alone — so toggling
  the control off mid-session kept the frame lowered by the solved height
  (up to 12 mm down the bridge) and rolled, with no solve and no guard
  watching it. Fixed on both sides (solvePlacement normalises seatConfig
  to null under the toggle; updateFrame withholds it), and the toggle is
  now itself a scheduler EDGE (latched off, consumed on re-enable) so
  switching back on re-solves that frame instead of waiting out the 5 s
  heartbeat. Pinned three ways: per-face bit-equality to the bare hang
  with a live seatConfig; a production-path toggle lifecycle check (20 off
  frames bit-equal to the hang while the channel still holds −2.8 mm,
  withheld not cleared; first re-enabled frame re-solves); the fitSig
  route the verifier suggested was NOT taken — the scheduler never runs
  while off, so a sig entry could never record the off state; the latch
  observes the toggle where it is actually seen.
  (F3) The ≤2 Hz event-work invariant was violated by measurement:
  SOLVE_MIN_INTERVAL 250 ms (physics-first's number, adopted in
  judgements) capped at 4 Hz, and the settled G13 fixture sustained 75
  solves/20 s = 3.75 Hz on rebuild edges alone, against the spec invariant
  and frame.js's own "≤ 2 Hz" comment. The constant is amended to 500 ms —
  the invariant is now the refractory's own number — and the G13 check
  gains the upper bound (solves ≤ 41 per settled 20 s; measures 37). The
  0.25 s of extra edge latency is a twentieth of the heartbeat and lands
  inside the channel deadbands.
  Verifier notes: (a) mid-session first-adoption snap (held-open session
  whose pose finally normalises adopts conf·s* whole, up to ~12 mm in one
  frame) — CONFIRMED as described but deliberately NOT fixed here: the
  adopt-whole semantics are pinned by the model-swap/reset channel check
  (1.12 mm adopt asserted) and the swap pop is arguably intended; folding
  a conf·|s*|-bounded ease into first adoptions would change a pinned
  behaviour, so it goes to stage 6's live look → OPEN item (v). (b)
  guard-release ≤0.3 mm single-frame step: acknowledged, bounded by the
  0.5 mm harness step bound, watch at stage 6 if a guard-heavy pose
  flicks. (c) doc drift fixed: widthAt relabelled DIAGNOSTIC-ONLY (the
  solver's widthAtRestCm readout does call it), seat-equilibrium header
  now says two contact passes per candidate height, and this spec's two
  "250 ms" mentions carry the amendment.
  Post-fix suites: pipeline-check 338/339 (four new checks; the one red is
  the same stage-0-ruled environment wall check, 16.1 ms this run) with
  every seat measurable numerically unchanged (RMS 0.047/0/0/0 mm, spread
  4.90 mm, sweep 0.18 mm/0 violations/1.62 mm). Replay re-pinned once and
  ratcheted 336/336 (stage-5 pre-fix pin kept at
  tests/diag-baseline.stage5.json.bak): the only out-of-tolerance deltas
  were f09 full-window sdY 0.195→0.265 px and rms 0.236→0.295 px —
  CLASSIFIED expected-direction: f09 is a descending still (raw seat
  −4.7 mm) whose settle re-phased under the 500 ms cadence; its TAIL rms
  (0.2796→0.2789) and maxStep (0.4305→0.4294) are unchanged, the py glide
  is monotone (0.11 mm total, worst step 0.016 mm), i.e. the same
  settle-in-window story as the five stills classified at the landing,
  open item (iv) unchanged. Shared-session seatAppliedSpanMm re-pinned
  1.85→1.97 mm crossfade-off / 2.09→2.11 on, and per-still-cold
  seatPushSpreadMm 2.126→2.17 mm — the solve lands 0.25 s later per edge
  while the surface is still converging, so per-pose applied values shift
  a few hundredths; still owned by open item (i)'s depth story. Replay
  wall 6.41 ms (budget 13.72).
Stage 6 — Budget lock + live verification in the user's real Chrome
  (claude-in-chrome + window.__ar; stillness measured as RMS while pose speed
  < 2 cm/s and 5°/s; pacing as missed vsyncs, never fps).
  STAGE 6 — LIVE SESSION FINDINGS AND HOT-FIXES (live 2026-08-16/17; this
  note reconciled into the tree 2026-08-17 — the hot-fixes below were landed
  LIVE, without harness runs, and this record plus the R0 reconciliation at
  its end is the delta record the ratchet protocol requires).
  THE LIVE VERDICT: the live session surfaced two pose-level defects the
  fixtures cannot exercise (frozen pixels have no gaze and no continuous
  motion), and the wearer VETOED further threshold-conditioning as the
  response to them. (1) GAZE-COUPLING: MediaPipe's nose-bridge landmarks
  follow the EYES (upstream: google/mediapipe issue #1786) — measured on a
  held-still head, pure gaze swung the bridge innovation 2.3 mm mean / 4 mm
  peaks and walked the drawn frame ~9 px (up to ~28 px pre-conditioning),
  while the pose MATRIX stayed sub-degree; real glasses do not move when the
  wearer's eyes do. (2) BACK-TILT JIGGLE: at deep pitch (measured to 49°,
  31 px RMS at the worst hold) landmark noise runs 2–6x frontal and every
  surface/seat consumer downstream amplified it. The gaze gate below treats
  (1); it is a threshold on a continuous phenomenon, it flapped at its own
  edge during calibration, and the wearer's veto of that whole genus is what
  triggered the anchoring-v3 rethink — see the pointer at the end.
  HOT-FIXES NOW IN THE TREE, each with its live measurement (every one
  carries a stage-6 comment at the code site):
  (1) POSE_TRUST tails widened, yaw 0.45 → 0.70 rad, pitch 0.45 → 0.60
      (frame.js). The first live hold at 31° of yaw read w = 0 and every
      trust-scaled protection switched off at once — no samples, a tripwire
      fed hallucinated residuals, the person model bled out at the exact
      pose the user calls "difficult angles". The tails now cover the live
      regime; w at the measured 31° hold ≈ 0.25 — a weak measurement, never
      a free-fire zone.
  (2) Identity streak: POSE_TRUST_IDENTITY 0.8 + IDENTITY_STRIKES 5
      (frame.js). The session reset the person model NINE times in four
      minutes — converged estimates thrown away because one half-trusted
      mid-turn sample read 12% off. The identity question is now asked only
      near-frontal and convicts only on 5 consecutive confident strikes; any
      agreeing sample acquits.
  (3) Tripwire arming bar wPose ≥ 0.6 (person.js). Round one: 62 soft-decays,
      most fired during turns — a converged estimate bled for the crime of
      being observed from the side. At the first patch's 0.3 bar, round two
      still measured 44 decays during ordinary browsing (w 0.3–0.5 residuals
      routinely read past 2.5 mm on a CORRECT model mid-turn). The tripwires
      exist for glasses-on and hairstyle changes, which are visible head-on;
      below the bar the timers FREEZE rather than reset.
  (4) Person-model learning weight wPose² (person.js). Linear w at the
      widened tails let w ≈ 0.3 turns teach the model their foreshortened,
      slid geometry fast enough to churn the surface — 203 rebuilds and 29
      guard pushes in one turn-and-return — then spend the frontal seconds
      unlearning it. Squared, a 0.35-trust observation buys an eighth of
      what it did; admission and defence are unchanged.
  (5) The gaze gate (frame.js: GAZE_FREEZE 0.08, GAZE_NEUTRAL_TAU 10,
      GAZE_STILL_CMS 1.5). Iris-vs-corner gaze measure, slow neutral EMA;
      away-from-neutral gaze ON A STILL HEAD freezes the pin's innovation
      filter. Calibrated live: eyes-still delta idled at 0.042 mean / 0.050
      max, deliberate glances 0.168 / 0.275; 0.055 false-froze 9% of an
      eyes-still hold, so the bar sits at 0.08. Cuts the ~9 px walk to the
      freeze threshold's leakage. KNOWN DEBT: a threshold on a continuum,
      exactly the genus the veto names; it has NO harness coverage (landed
      live), and none is being added — anchoring-v3 A1 deletes it whole
      rather than tuning it. The R0 gazeInjection instrument covers the
      phenomenon offline instead.
  (6) easeSeatChannels: the standoff (ζ) target FREEZES below wPose 0.3
      (frame.js). At tilted poses the view-locked skin morphs under the
      seat, the raw law reads the morph as interference, and the frame rode
      visibly forward off the nose with every tilt — "pushed forward in a
      weird way", on every asset, because the mechanism is pose-level. The
      raw guard still answers TRUE penetration every frame.
  (7) GUARD_MAX 0.4 cm + overflow counter (nose.js, fit.js). During a deep
      tilt the surging surface asked the raw guard for 1.8 cm in one frame
      and it obliged — the frame launched forward off the face. Honest asks
      measure peak < 0.1; past the cap the feathered boundary absorbs the
      overlap, and overflows are counted, not hidden.
  (8) Occluder deform trust scale (occluder.js): below wPose 0.3 the
      view-residual alpha scales down to a 0.05 floor — an untrusted pose's
      landmarks are foreshortened and half-hallucinated, and full-rate
      deform at those poses is where the guard-churn and rebuild storms
      started.
  (9) The pose smoother's trust lever wired (smoothing.js: activityEff ×
      (0.3 + 0.7·trust), the C1 third lever): at untrusted poses the speed
      signal over-reads exactly where it means least, holding the adaptive
      cutoff open on noise — the deep-tilt giggle's filter-side share.
  Open items (v) padBalance flip and (iii) real-Chrome wall-clock re-confirm
  were NOT decided in the live session (it ended on the veto); they carry
  forward into the v3 plan's live protocol.
  R0 RECONCILIATION (2026-08-17): full suite + replay run against the tree
  the hot-fixes left behind. pipeline-check 336/339 → two reds legitimately
  encoded retired constants and were UPDATED with stage-6 citations: the
  stage-2 pitched-head trust-ladder check (its "40° weighs ≤ 0.05" premise
  is the pre-widening tail; now asserts weak-but-defended ≤ 0.3 at 40°
  applied and zero past the 0.60 tail at 55°) and the identity-swap reset
  check (single-frame conviction → the 5-strike streak with an acquittal
  arm). The third red is the stage-0-ruled environment-bound 13 ms wall
  check (13.62–13.97 ms across this session's runs, within the ruling's
  regime — the stage-5 landing had recorded 15.1–16.4 in the same pane; the
  real budget guard, the replay aggregate, reads 4.89 ms vs 13.72).
  diag-replay 334/336: f04 (the 18°-yaw still) measuringFraction 0.7833 →
  1.0 — the direct arithmetic of the widened yaw tail (18° now reads
  w > 0.5 every frame) — and f04 surfaceRebuildsLate 4 → 3, the
  calmer-surface direction of (4)+(8). Both classified expected-direction;
  baseline re-pinned per the ratchet protocol (previous pin kept at
  tests/diag-baseline.stage5-final.json.bak). GREEN FLOOR after
  reconciliation and the R0 instruments: pipeline-check 340/341 (5 new R0
  checks; the one red is the ruled wall check), diag-replay 338/338 against
  the re-pinned baseline, telemetry record→replay loop validated on the
  sample-face self-capture (534 frames, 6 segments, production + rigid
  passes both run; baseline deliberately NOT pinned — it belongs to the
  real capture, and the runner refuses a baseline pinned on a different
  fixture).
  R0 OFFLINE GATE VERDICTS (measured 2026-08-17, pinned in
  diag-baseline.json under rigidMiss / gazeInjection):
  - rigidMiss: production-vs-rigid screen delta per still runs 0.30 px
    (f00) to 13.06 px mean (f04, the −18° yaw still; f02 8.04, f05 6.17) —
    the ≤ 3 px-per-still gate is NOT MET on 5 of 10 stills, largest exactly
    where the landmark pin is compensating pose-systematic anchor slide.
    The span gate IS met: rigid cross-still wander 4.37 mm norm vs
    production 5.71 (x: 1.70 vs 4.42 — the rigid pin retires most of the
    diagnosed x wander; z: 3.34 vs 2.84, slightly worse). Reading: the
    innovation term is doing real per-frame work at extreme yaw (the
    verdict's criterion-2 caveat made flesh), and μ = 1 as a flat constant
    would trade slide-compensation for cross-pose steadiness. This is the
    A-vs-B-deciding trade the telemetry capture must referee (does the live
    matrix carry the turn well enough that the compensation is vestigial?).
  - gazeInjection: injector validity MET — production reproduces the live
    coupling (rms 6.24 px / peak 15.1 px against the live ~9 px walk at
    4.22 px/mm; iris gate correctly silent, 0 frames). Forced-rigid reads
    rms 3.95 px / peak 14.9 px — NOT the ≤ 0.5 px the A1 gate expects,
    and the pin cannot be the path (matrix untouched, pin = base, base
    self-defends: the person estimator's noise gate cuts the displaced
    vertices' weight ~30-fold). The residual path is the one the plan's
    audit table filed under "bounded": the view-locked deform follows the
    displaced nose landmarks, so the SURFACE morphs at 0.5 Hz, and the
    seat stack (ζ target, s re-solves at solve events, guard) rides the
    morph into the placement of BOTH pins; the near-identical peaks
    (15.1/14.9) point at shared solve-event transients on top. CONSEQUENCE
    for A1: deleting the pin innovation alone does not reach ≤ 0.5 px on
    this injector — either the seat's per-frame surface inputs get the
    same μ treatment (a v3-plan addendum to A1's scope), or the injector's
    premise (all seven nose landmarks displace with gaze at full deform
    trust) must be checked against the real capture before it is allowed
    to fail A1. Recorded here so A1 starts from the measurement, not from
    the plan's optimism.
  R0 TELEMETRY ATTRIBUTION RUN (2026-08-17, on the real capture
  ar/tests/fixtures/telemetry-shay-2026-08-17.ndjson — 3064 frames, six
  segments; this is the A-vs-B decision run the v3 verdict's amendment 1
  moved ahead of A1, and its numbers decide the design).
  INSTRUMENTS LANDED for the run (each inert unless invoked, and the
  inertness asserted, not trusted):
  (1) pinMode 'frozen' (frame.js + occluder.js `freezeEstimators`) — the
      PURE POSE FLOOR: after a warm-up every estimator HOLDS (no sample
      admission or median commit, no identity question, no person
      accumulate/commit/tripwires, no depth-fit EMA, no seat solves or
      channel easing, no gaze-EMA, no pin-filter advance); the pin
      composes the frozen base and the screen transform is
      smoothedPose ∘ frozen-constants. The view-locked deform and the
      capped per-frame guard deliberately keep running (mask coverage and
      safety are measurements, not estimators). The replay's frozen pass
      warms up in full production behaviour through the END of the
      'still' segment (freeze at t=19209 ms; ?freeze= overrides), so the
      frozen constants are exactly what the live pipeline had converged
      to. pipeline-check pins the hold field-by-field (person
      frames/commits/ΣW, sample window, carried-anchors object, identity
      asks, seat solves/ζ/s, depth-fit EMA values, gaze EMA — all
      bit-held over 10 frozen frames) beside the existing
      omitted≡'production' bit-equality assert.
  (2) Counter bookkeeping in telemetry-replay EVENT-COUNTED, not
      level-differenced: an identity conviction (`resetFit`) zeroes the
      seat counters mid-segment and the old level deltas went NEGATIVE on
      this capture (seatSolves −35, guardPushes −449). Events are banked
      per frame against the previous frame's levels — a dropped level
      means "counter restarted" and the new level IS the events since the
      restart — so negative counts are impossible by construction.
  (3) Attribution instruments: the `__ar.identity` readout (per-ask
      comparison values in exactly `isDifferentFace`'s arithmetic, with
      MONOTONE asked/strike/acquittal/conviction counters a replay can
      difference); per-segment spans of the carried median (bridge,
      eyeLineY, widths, metricScale) and the fused pin base; per-segment
      spans of the seat's rawNeeded/applied-ζ/guard plus surface-rebuild
      counts; per-strike logs (driver + deviation values) in the replay
      output and the pinned baseline.
  THREE-MODE TABLE (still-gated screen RMS px / worst step px; still
  fraction; production ≡ the shipped pin, rigid ≡ candidate A's μ=1 with
  all estimators live, frozen ≡ the pure pose floor):
    still       prod 3.99/0.97  rigid 3.97/1.02  frozen 3.99/0.97  (85%;
      frozen ≡ production by construction — the warm-up)
    eye-circles prod 8.57/2.03  rigid 8.54/1.98  frozen 3.49/0.54  (58%)
    glances     prod 15.07/1.05 rigid 16.98/1.07 frozen 18.16/0.93 (20%)
    pitch       prod 3.88/0.48  rigid 3.99/0.34  frozen 6.29/0.35  (18%)
    yaw         prod 41.85/0.28 rigid 44.80/0.31 frozen 53.74/0.29 (2.5%
      — 11 still frames; treat as indicative only)
    browse      all null (0.3% still — never still enough to gate)
  EYE-CIRCLES ATTRIBUTION (production/rigid share one estimator path;
  their events are identical): the identity question ran on ALL 445
  frames (w = 1 through the whole segment — a held-still frontal head),
  struck 34 times, acquitted 6 streaks, and CONVICTED 4 times — the 4
  personResets, each dumping model + window + seat and re-seeding the
  carried median from the conviction frame's own gaze-displaced sample.
  EVERY strike, eye-circles and browse both (40 of 40 logged), was driven
  by metricScale ALONE: devMetricScale 12.0–17.8% against the 12% bar
  while devWidthRatio sat at 0.1–1.2% (browse ≤ 6%). The iris is the
  absolute ruler (see the scale memory note), and under deliberate gaze
  the iris leaves the corners and the ruler misreads by an eighth at FULL
  pose trust — the carried metricScale spanned 27.7% across the segment
  (1.20 ↔ 0.95 through the resets, visible in the strike log's carried
  values). The rest of the carried walk: eyeLineY 3.72 mm span (the
  median rides gaze-displaced eye landmarks), median bridge 1.39 mm,
  fused base 2.04 mm, noseWidth 1.20 mm; seat: applied ζ walked ~2.0 mm
  on a rawNeeded span of 2.1 mm, 188 surface rebuilds, guard span 0.6 mm.
  In the frozen pass all of it is zero by construction and the guard
  becomes the only surface consumer: it fires on ALL 445 frames (the
  frozen ζ no longer tracks the morphing skin) with a 2.53 mm span.
  DECISIVE NUMBERS, per the plan's own criteria:
  (a) FROZEN EYE-CIRCLES RMS 3.49 px — ABOVE the 2 px kill bar. The pose
      matrix itself carries gaze past the psychophysical target even with
      every estimator frozen, so candidate A's central premise ("the
      matrix is gaze-clean; delete the landmark path and the screen is
      clean") FAILS at the margin, and per the plan's own kill criterion
      and verdict criterion 6, CANDIDATE B (rigid-subset pose refit) IS
      PROMOTED TO PRIMARY. Honest bound: the frozen number still contains
      the guard's surface-morph following (2.53 mm span, mostly
      z-projected), so 3.49 px is an UPPER bound on the matrix's own
      share — but the bar is 2 px and the margin is 75%.
  (b) FROZEN PITCH RMS 6.29 px — the pure-pose extreme-pose floor is
      ALREADY outside the ≤ 5 px target that production meets (3.88 px):
      at pitch holds the live landmark-following estimators are doing
      real compensating work, not just adding noise. Same story stronger
      at glances (18.16 frozen vs 15.07 production) and yaw (53.74 vs
      41.85, thin gate). A frozen-constant carry is not a viable
      architecture at pose extremes on this detector — the compensation
      B re-derives per frame from personally-converged points is load-
      bearing, which is the R0 rigidMiss finding (13.06 px at −18° yaw)
      confirmed on the live capture.
  (c) EYE-CIRCLES SPLIT: pin innovation ≈ 0.03 px (8.57 → 8.54 — THE PIN
      IS VESTIGIAL; deleting it alone buys nothing measurable, exactly as
      the gazeInjection verdict warned); estimator-stack share ≈ 5.0 px
      of the 8.54 (8.54 rigid → 3.49 frozen: the resets + carried-median
      walk + seat/surface path — the DOMINANT contributor, at 1.4× the
      pose floor); pose(+guard) floor 3.49 px.
  CONSEQUENCE FOR THE V3 PLAN: B is promoted per the plan's own trigger,
  and A1 as scoped (innovation deletion + gaze-gate deletion) is dead as
  a gaze fix — but B alone does not reach ≤ 2 px either, because the
  slow-estimator ADMISSION path carries more gaze than the matrix does.
  Whatever pose carries the frame, the measurement-admission side needs
  the structural treatment the numbers point at: the iris-derived
  metricScale must not be admitted (into the median OR the identity
  question) while the iris is away from its corners — the identity
  5-strike check convicts a held-still frontal head on exactly the
  quantity gaze corrupts most, and each conviction is worth more screen
  motion than the entire pin path. This is measurement, not design: the
  orchestrator owns the architecture call.
  CAVEATS pinned with the numbers: glances/pitch still-RMS stands on
  58/57 gated frames, yaw on 11, browse on 2 — the still/eye-circles
  segments are the load-bearing ones; the frozen pass's still segment is
  production by construction (warm-up); wall means in the throttled pane
  7.0/7.2/7.6 ms per mode (reported, never asserted).
  GREEN FLOOR after this run: pipeline-check 342/342 (the new frozen-hold
  check green; the stage-0-ruled wall check itself PASSED this session at
  11.72 ms — first time under its own 13 ms bar since stage 0, consistent
  with the ruling that it is environment-bound), diag-replay 338/338
  untouched, telemetry-replay 403/403 with telemetry-baseline.json NEWLY
  PINNED on this fixture — all three modes, the diag tolerance discipline
  verbatim (spans assert on the px law), strike logs carried in the pin.
  WHAT HAPPENS NEXT lives in ar/docs/nose-v3/: v3-rethink.txt (the
  anchoring-v3 plan — candidate A converged-rigid, B rigid-subset pose
  shelved behind ?pose=fit, staged R0/A1/A2/A3) as amended by v3-verdict.txt
  (adopt-with-changes: capture moves before A1; the cold-window claim
  corrected; A2 contingent; mask-registration coverage; gazeInjection space
  pinned to normalized landmarks). Stage R0's instruments are in the tree:
  record-telemetry.html (the 90 s capture protocol — the capture IS the
  next live session), telemetry-replay.html (deterministic fixture runner
  with the production/rigid/frozen pinMode decomposition, baseline pinned
  on the real capture), diag-replay's rigidMiss + gazeInjection offline
  gates, and the __ar.stab live meter. The attribution run above is the
  amendment-1 decision point, measured: B promoted, A1's scope dead as a
  gaze fix, the admission path (metricScale under gaze) the largest
  single contributor.
  ANCHORING-V3 IMPLEMENTATION LANDING (2026-08-17, the stage the attribution
  run decided; every number below measured on the pinned fixture
  ar/tests/fixtures/telemetry-shay-2026-08-17.ndjson unless named otherwise).
  WHAT LANDED, in the order the decision doc ordered it:
  (1) GAZE-HARDENED ADMISSION (frame.js GAZE_ADMIT 0.08 — the stage-6 gate's
      live calibration re-consumed verbatim: eyes-still 0.042 mean/0.050 max,
      glances 0.168/0.275, bar at double-the-still-ceiling; the gate itself
      is deleted). While gaze sits off its slow-EMA neutral, NO sample is
      admitted (bridge, eyeLineY, widths, metricScale — the whole reading
      rides landmarks gaze corrupts) and the identity question is not asked;
      "keep previous, never assume average" is the existing null-reading
      semantics, now standing on one calibrated band for every consumer.
      MEASURED: eye-circles refusals 325/445 frames; carried metricScale
      span 27.7% → 3.0% (gate ≤5 MET); eyeLineY walk 3.72 → 0.79 mm (gate
      ≤1.5 MET); eye-circles production RMS 8.57 → 7.00 before the pose even
      changed. Unlike the deleted pin freeze the door refuses at any head
      speed — the browse strikes proved the ruler bends while moving.
  (2) IDENTITY: metricScale LEFT THE PREDICATE (isDifferentFace convicts on
      widthRatio alone). Forced by its own strike log: all 40 R0 strikes
      were metricScale-driven, and with the door in place a browse episode
      at t≈98 s STILL swung the admitted metricScale 13.5–16.5% for five
      consecutive frames at neutral-reading gaze (wPose 0.81–0.95) —
      convicting the wearer once more on his own face. An instrument whose
      measured noise exceeds its 12% conviction tolerance cannot testify;
      the proportioned-alike-adult/child case reverts to Re-measure. GATE
      MET: 0 convictions on eye/glances/browse in both production and fit
      passes (was 4+1); the pipeline-check identity swap (22% width) still
      convicts in exactly IDENTITY_STRIKES.
  (3) CANDIDATE B LANDED (ar/src/pose-fit.js, wired at the top of
      updateFrame behind ?pose=fit / poseFit option; 'shadow' arm proves
      no side channel, bit-parity asserted). Per B.1: weighted Huber
      (δ=2 px) ray refit of person.est over RIGID_SUBSET (canonical-face.js
      — 21 vertices, documented per group; nose core 4/6/129/358 excluded
      as the measured gaze carriers, eye corners 33/263/133/362 included at
      their measured 0.00 mm), 2 warm-started GN iterations on se(3)
      (centroid-parametrised, radius-normalised — the warm start is the
      bounded solved-minus-matrix DELTA, clamp-bounded by construction so a
      dropout can never stale it), scale held at the matrix's, gauge clamp
      3°/1 cm with monotone engagement counters, wSolve =
      smoothstep(Σw_i / (|S|·W_PIN_FULL/W_MAX)) · clamp(iso/ISO_REF, 0, 1)
      — every factor an existing constant or a measured value (ISO_REF
      0.08 = the fixture's own converged-frontal 6λ_min/tr(H): still
      0.0797 / eye 0.0761). B.1's r(pitch) is IN, measured both ways:
      without it the blend trusted the refit harder at deep pitch and the
      pitch RMS worsened 4.60 → 5.12 px (subset residual ~4.3 px at 30–49°)
      — the global curve stays in the mass. Composition per B.2: the
      blended pose replaces raw position/quaternion at the very top; one
      pose in flight; every measurement inversion inverts the drawn pose's
      raw twin exactly as before. Cost measured 28.7 µs/solve (pipeline-
      check, 2000-call bench, 100 µs budget).
  (4) THE PIN MACHINERY DELETED whole (the R0-measured 0.03 px): the
      innovation filter and its composition, PIN_FILTER, NOSE_LEVER_CM,
      PIN_ACTIVITY_CAP, the gaze pin-freeze block (state.gaze SURVIVES as
      the admission door's signal), InnovationFilter (smoothing.js, ~110
      lines, no consumer remained). The pin IS the fused base (carried
      median ⊕ κ·person estimate); pinMode 'rigid' collapsed into
      'production' (alias kept, collapse asserted bit-for-bit in
      pipeline-check and by diag-replay's rigidMiss ≡ 0.00 on every still);
      'frozen' survives as the pure-pose floor. Net deletion ~190 lines of
      machinery + three constants; frame.js's one-frame-shift and
      expression checks rewritten to the inverted charter (refusal at
      landmark speed, follow at estimator timescale — measured 0.7% at
      +133 ms, 60% at +0.8 s through the κ-fused base).
  (5) INSTRUMENTS: telemetry-replay's 'rigid' pass slot now runs the 'fit'
      pass; per-segment columns wSolveMean / poseFitResidualPx /
      poseFitIsoMean / clampEngagements / gazeRefusals (event-counted) /
      stabRmsMeanPx (the live __ar.stab trailing-5s law applied offline —
      the psychophysical bar's own instrument, beside the drift-inclusive
      segment-mean rmsPx); __ar.poseFit {wSolve, gnIters, residualPx,
      clampEngagedFrame, clampEngagements, subsetVisible, massFrac, iso};
      serve.py gained the one write path (PUT, the two baseline files only)
      so re-pins stop travelling by clipboard.
  OFFLINE ACCEPTANCE GATES, measured (fit pass unless named; three-mode
  table now production/fit/frozen):
    still        prod 3.97  fit 3.42  frozen 3.97   (stab-law 2.91/3.13)
    eye-circles  prod 7.00  fit 4.05  frozen 3.49   (stab-law 3.76/2.85,
                 frozen floor 2.52)
    glances      prod 16.91 fit 17.99 frozen 18.15  (~30 gated frames)
    pitch        prod 4.81  fit 4.97  frozen 6.20   (stab-law fit 2.49)
    yaw          prod 45.09 fit 46.48 frozen 53.69  (11 gated frames)
  - eye ≤2.0: NOT MET, measured unreachable as stated — the bar sits BELOW
    the pure-pose frozen floor on both metrics (segment-mean 3.49, stab-law
    2.52): no carrier can beat the wearer's own within-gate drift. The fit
    removed 84% of the segment-metric gaze increment over its own rest
    floor (production +3.03, fit +0.63) and reads 2.85 on the bar's own
    instrument vs the 2.52 floor — 0.33 px above the floor.
  - pitch ≤3.9: NOT MET — fit 4.97 / production 4.81 vs R0's 3.88, which
    stood on the deleted innovation (R0 rigid arm: 3.99) plus a 4-reset
    young-model history this stage abolished (the conviction-free model
    reaches pitch converged and stiffer). Stab-law at the holds: 2.49 px —
    inside the plan's original ≤5 px psychophysical target.
  - glances <15.07: NOT MET — production 16.91 ≈ R0's rigid 16.98: the
    deleted innovation's gaze-FOLLOWING flattered this segment's RMS by
    tracking the displaced landmarks. ~30 gated frames, indicative per R0.
  - still no worse: MET (fit 3.42 vs prod 3.97 — the refit is QUIETER than
    the matrix at rest). 0 convictions: MET. metricScale span ≤5: MET
    (3.0%). eyeLineY ≤1.5 mm: MET (0.79). B.4 clamp <1% at converged rest:
    MET (3/445 eye-circles; the cold-ramp still segment reports 61 — the
    unconverged solve wandering inside the clamp at wSolve≈0, weighted out
    by the blend). B.4 bit-parity at wSolve 0: MET (shadow ≡ off,
    asserted). B.4 wall +≤0.1 ms: MET on the deterministic bench
    (28.7 µs/solve); the pane's replay wall delta reads +0.44 ms alongside
    a frozen-pass reading 1.0 ms ABOVE production while doing strictly less
    work — the pane clock, per the stage-0 ruling, reported never asserted.
  DEFAULT DECISION: ?pose=fit stays FLAGGED (POSE_FIT_DEFAULT false in
  main.js) — the plan's own rule ("all green → default; any marginal →
  report the margin"). The margins: eye-circles 4.05 vs 2.0 (floor-bound),
  pitch 4.97 vs 3.9 (−1.07), glances 17.99 vs 15.07 (−2.9, thin gate). The
  fit is strictly better at rest and during eye motion (the complaint under
  repair) and slightly worse at pitch/yaw extremes; the maturity path
  (wSolve still-mean 0.75, eye 0.94) and the extreme-pose behaviour are the
  open items a longer capture or live session should decide.
  RESIDUAL RISK, recorded: the R0 gazeInjection instrument now reads
  8.31 px rms / 28.5 px peak on BOTH tracks (was 6.24/3.95) — the injector
  displaces nose landmarks without an iris signature, the door cannot see
  it, the pin no longer co-moves with it, and the seat/deform path rides
  the synthetic morph at full relative amplitude. A ζ gaze-hold was TRIED
  against the real fixture and reverted: it changed eye/glances RMS by
  nothing measurable and traded eased ζ for raw guard pushes (17 → 172 on
  glances) — the live segments say the seat is not the gaze path; the
  synthetic exposure stays on the books with this note.
  BASELINES: diag-baseline.json re-pinned (previous pin at
  diag-baseline.r0-final.json.bak); 8 out-of-tolerance deltas classified —
  6 improvements (f02/f05 sdX/rms, f04/face-a tail steps, f06 maxStep: the
  deleted per-frame landmark noise), f04 full-window sd/rms rose
  0.24/0.36 → 0.36/0.46 as the fresh-cold −18°-yaw session now pays the
  median's convergence walk in-window (settled tail 0.231 → 0.246, within
  tolerance; steps improved — the stage-5 settle-in-window precedent), and
  gazeInjection reclassified per the residual-risk note. Shared-session
  bridgeXSpan 4.42 → 1.81/1.83 mm, rigid cross-still span 5.71 → 3.23 mm
  norm. telemetry-baseline.json re-pinned on the same fixture (previous at
  telemetry-baseline.r0-final.json.bak) with the production/fit/frozen
  passes and the new columns; the three unreachable-as-stated RMS bars are
  report-lines in the runner (this note is their verdict), the five
  protective gates assert.
  SUITES at landing (final verification runs): pipeline-check 344/345
  (the one red is the stage-0-ruled environment wall check, 13.09–13.98 ms
  across this session's runs in the throttled pane; four new checks — gaze
  door, refit parity/engagement/correction, refit cost — green; the
  identity-swap, weld, one-frame-shift, expression and pinMode checks
  rewritten to the landed architecture with citations), diag-replay
  338/338 against the re-pinned baseline, telemetry-replay 485/485 against
  its re-pinned baseline with the gate verdicts as recorded above.
  [SUPERSEDED IN PART — every fit-pass number in this landing note was
  measured through the F1 clamp defect; the verification addendum below
  carries the corrected table and the standing verdicts.]

  ANCHORING-V3 LANDING VERIFICATION ADDENDUM (2026-08-17, same day; the
  adversarial verification pass and its fixes. Findings F1–F6, each
  verified against code and, where numeric, re-executed on the real module
  before dispositioning.)
  F1 — CONFIRMED, P1, FIXED. pose-fit.js's rotation gauge clamp read
    `copy(mpQuaternion).slerp(solvedQuaternion, k)` — a quaternion
    self-slerp: after the copy, `this` IS the slerp target, so every
    engaged frame snapped the solved rotation to the matrix EXACTLY
    (0.000° measured, where B.1 specifies a 3° cap) and erased the warm
    rotation — a ~wSolve·3° pre-smoother pose snap per rail crossing, on
    30–88% of frames in exactly the segments whose bars failed. Fixed to
    `solvedQuaternion.slerp(mpQuaternion, 1 − cap/angle)` (the argument is
    never `this`); verified capping at 3.000°, warm start preserved, rail
    crossing continuous (numeric harness: T6/T6b/T6d red → green; position
    clamp was always correct). EVERY fit-pass number above is stale; the
    corrected four-pass table (fit re-measured, frozenFit new — see F4):
                 prod    fit    frozen  frozenFit   (segment-mean RMS px)
    still        3.97    3.15    3.97    3.15
    eye-circles  7.00    4.14    3.49    3.92
    glances     16.91   16.31   18.15   12.71
    pitch        4.81    5.44    6.20    6.73
    yaw         45.09   41.44   53.69   46.39
    stab-law (trailing-5s, the bar's own instrument):
    still 2.91/2.64 · eye 3.76/2.89 (frozen floor 2.52) · glances
    17.84/16.93 · pitch 2.55/2.21 · yaw 44.93/42.40 (prod/fit). The fix
    moved the fit from worse-than-production at rest on the stab meter
    (3.13 vs 2.91) to BETTER on every segment's stab reading, and the
    segment-mean fit now beats production on four of five gated segments
    (still, eye, glances, yaw); pitch segment-mean worsened 4.97 → 5.44
    (the capped divergence rides the hold where the bugged clamp snapped
    home) while pitch stab-law improved to 2.21 — best of all four passes.
    Downstream of the fix everything got quieter: pitch personDecays
    19 → 0, guardPushes 46 → 20, carried spans down 2–20× at pitch/yaw,
    browse guardPushes 233 → 169. Engagement counters ROSE (still 61→83,
    eye 3→8, browse 397→499): a capped clamp stays at the rail and
    honestly re-engages where the bug self-reset it — the counter now
    measures rail pressure, not resets.
  F2 — CONFIRMED, P2, DEFERRED WITH THE CURVE RECORDED. The wSolve iso
    term conflates degeneracy with viewing distance: converged-frontal iso
    measured on the real solver runs ~1/z² (0.0846/0.0502/0.0278/0.0176
    at 35/45/60/75 cm — iso·z² constant within ~4%), so ISO_REF 0.08 is a
    one-point pin at this fixture's distance and a wearer at 60–75 cm gets
    isoTerm 0.35–0.22 at perfect convergence. Failure direction is
    conservative (fit fades to matrix). Not patched: the honest fix must
    be gated on a capture that exercises distance and the pinned fixture
    is single-distance — a constant chosen today could not pass its own
    gate (the kill rule). Recorded at ISO_REF's declaration; the next
    capture adds a near/mid/far ladder.
  F3 — CONFIRMED, P2, FIXED. The B.4 clamp-gate re-scope stood on
    "wSolve ≈ 0 at the cold ramp" while its own printout read 0.75 — the
    false rationale is replaced in the runner and here: the cold ramp runs
    at substantial blend weight on a still-converging person.est, a
    young-model solve rides the rail by expectation, and the wearer is
    protected by the drawn frame, which the settle gates read directly —
    now on BOTH instruments (a new stab-law still-settle gate, same
    comparative law as the segment-mean gate, no new threshold: fit 2.64
    vs production 2.91, MET post-F1; pre-F1 it would have read RED at
    3.13, which is the honest record of what the bug cost at rest).
    The B.4 alarm itself is recast by attribution (below, F4/B.4).
  F4 — CONFIRMED, P2, MEASURED. "Floor-bound" was asserted without the
    arm that isolates the carrier; the runner now has it (frozenFit:
    estimators frozen exactly as 'frozen', refit carrier live — the two
    passes differ ONLY in the carrying transform). Measured at converged
    rest (eye-circles): matrix carrier 3.49 segment-mean / 2.52 stab,
    refit carrier 3.92 / 2.86 — the refit does NOT beat the matrix at
    rest (it pays ~2 px of solve residual as carrier noise where the
    matrix pays gaze wobble), BOTH carriers sit above the 2 px bar, and
    the "measured unreachable as stated" verdict now stands on the
    measurement instead of the assertion. Elsewhere the carrier swap is
    decisive in the refit's favour: glances 12.71 vs 18.15 (stab 9.76 vs
    19.23), yaw 46.39 vs 53.69 (stab 21.58 vs 53.70) — under frozen
    estimators the refit carrier is the best glances number ever measured
    on this fixture, which bounds what the live estimators are costing.
  B.4 RE-READ POST-FIX: the literal 1%-at-rest line reads 1.8% (8/445
    eye-circles engagements) — but the frozen-model control reads 10/445
    on the same frames with feedback structurally impossible, so the
    engagements are rail-riding noise, not gauge drift, and the pre-fix
    3/445 "pass" was the bug under-counting. The asserting gate is now
    the attribution itself: live-arm engagements must not exceed the
    frozen-model control (8 ≤ 10, MET; no new threshold — arm vs
    control). Residual-trend arm: poseFitResidualPx still 1.73 / eye 1.84
    — no upward trend across the session.
  F5 — CONFIRMED AS STATED, P3, OPEN (measurement-first). The solver
    weighs by the facing ramp alone — the observation law's vis half
    (hidden-behind-geometry) stayed home, and at 30–45° yaw the far-side
    subset members are camera-facing but landmark-hallucinated, entering
    at carried weight bounded only by Huber. Recorded at the weight code;
    the experiment is defined: difference yaw-segment engagements
    (post-fix baseline: fit 422, frozenFit 423 of 445) with a
    visBehind-style hold in shadow mode BEFORE touching any weight.
  F6 — CONFIRMED, P3, FIXED. The shadow-parity check asserted only the
    terminal frame while recording "over 230 frames"; it now folds every
    frame's bridge + placement pos/quat into the equality (a decaying
    transient can no longer pass), and still reads green.
  BASELINES: telemetry-baseline.json re-pinned on the corrected numbers
    plus the frozenFit pass (previous pin at
    telemetry-baseline.v3-pre-f1fix.json.bak); 20 out-of-tolerance deltas
    classified — all F1-fix consequences: 4 engagement-counter rises
    (honest rail counting, above), 16 improvements (seat spans, guard
    pushes, person decays, carried spans). diag-replay needed NO re-pin —
    338/338 against the standing pin, confirming the fix touches nothing
    outside the flagged path. SUITES after the addendum's fixes:
    pipeline-check 344/345 (the same stage-0-ruled wall check, 13.37 ms
    this run — within its recorded pane range), diag-replay 338/338,
    telemetry-replay 643/643 against the re-pinned four-pass baseline.
    Refit correction check post-fix: +1.5° injected matrix yaw drawn to
    2.03 px off truth vs 3.82 matrix-carried; cost 33.6 µs/solve.
  DEFAULT DECISION, RE-REFEREED ON CORRECTED NUMBERS: ?pose=fit stays
    FLAGGED (POSE_FIT_DEFAULT false). The three RMS bars still read NOT
    MET as stated — eye 4.14 vs 2.0 (bar below BOTH measured carrier
    floors), pitch 5.44 vs 3.9 (−1.54, the one segment-mean the fit loses
    to production), glances 16.31 vs 15.07 (−1.24, thin gate, now BETTER
    than production's 16.91) — so the plan's own rule holds the flag. The
    corrected picture for the live referee: the fit beats production on
    every stab-law reading and four of five segment-means, and loses
    pitch segment-mean only; the live session decides by impression
    against the stab meter, per the protocol below.
  OPEN ITEMS (superseding the landing note's list):
    1. Eye-circles bar: floor-bound, now by measurement (F4) — both
       carriers above 2 px; the stab column carries the psychophysical
       number (fit 2.89 vs floor 2.52).
    2. Pitch: fit segment-mean 5.44 vs production 4.81 while fit stab
       2.21 beats everything — whether the capped rail divergence at deep
       pitch reads as an offset the wearer sees or as the steadier hold
       the stab meter says, only the live session can answer. Candidate
       refinements if it reads badly: F5's vis-hold experiment,
       iso-vs-yaw/pitch measurement, or a fitting-shaped facing ramp.
    3. wSolve maturity ramp (~20 s to saturate) — live-session question,
       unchanged.
    4. gazeInjection synthetic exposure 8.31 px rms both tracks —
       unchanged, stays on the books with the ζ-hold revert note.
    5. F2's distance domain: next capture includes a near/mid/far frontal
       ladder; ISO_REF becomes a curve or gets the measured 1/z²
       normalised out.
    6. F5's vis-hold shadow-mode experiment at yaw (baseline engagement
       counts recorded above).
    7. Carried from stage 6: padBalance flip decision and the real-Chrome
       wall-clock re-confirm (the 13 ms deform check and the fit's wall
       delta) — next live session.
  LIVE CONFIRMATION PROTOCOL (~3 min of instruction, the session that
  referees the flag; record it with record-telemetry.html so it becomes
  the next pinned fixture — including the distance ladder the current
  fixture lacks. Meters read ALOUD during, not after: __ar.stab
  (trailing-5s RMS), __ar.poseFit.wSolve and .clampEngagements at each
  segment end. Run segments 1–6 TWICE: once production (?pose=mp), once
  ?pose=fit; the wearer is not told which is which on the second run
  where practicable):
    1. COLD START + 15 s still — settle impression; watch the wSolve
       ramp (open item 3: time to ~0.75) and the cold-ramp stab (fit
       must read ≤ production ~2.9 px).
    2. 15 s deliberate eye circles, head held still — THE complaint
       under repair. Expect stab fit ~2.9 vs production ~3.8. Subjective:
       "do the glasses breathe or wobble while only your eyes move?"
    3. 10 s rapid corner-to-corner glances — subjective: "does the frame
       tug when your eyes jump?"
    4. 15 s pitch holds (up ~20°, down toward 45°) — THE segment the fit
       loses on segment-mean. Subjective A/B, asked at the hold: "is it
       stiff-but-attached or does it float/slide?" plus "any downward
       launch entering the hold?" Watch clampEngagements climb rate.
    5. 15 s yaw sweeps to ±40° — subjective: "does the frame lead or lag
       the turn? any slide across the face?" (fit expects to win: 41 vs
       45 segment-mean, 21.6 vs 53.7 frozen-carrier stab).
    6. 20 s free browse (read something, scroll) — identity counter must
       stay 0; subjective: "did anything move that shouldn't?"
    7. DISTANCE LADDER (fit mode only, F2): ~10 s frontal still at
       ~35 cm / ~60 cm / ~75 cm — read wSolve and iso aloud per rung;
       expected fade 1.0 → ~0.35 → ~0.22 confirms the conservative
       direction and captures the ISO_REF curve for open item 5.
    8. VERDICT QUESTIONS, asked last: "which run would you wear?" and
       "was any moment visibly wrong in either?" The flag flips to
       default only if fit is no-worse by impression at pitch AND yaw
       and better at eye circles — otherwise it stays flagged and open
       item 2 becomes the next stage.
    Also in this session (carried): padBalance flip A/B at rest, and the
    real-Chrome re-confirm of the 13 ms deform check + fit wall delta
    (pipeline-check + telemetry-replay once each in the user's Chrome).

2026-08-17 — SEAT-REFERENCE LANDING (the ">40° side-tilt forward push"
fix; one mechanism, per the same day's z-decomposition diagnosis; files:
src/frame.js, src/fit.js, tests/telemetry-replay.js, tests/z-decomp.*):
  THE COMPLAINT, verbatim (the wearer, live, 2026-08-17): "when the head
    is tilted to the sides (more than 40 degrees) the glasses are being
    pushed forward for no reason." Resolved by measurement to YAW — the
    fixtures reproduce it past 40° of yaw and pitch does not reproduce it
    (+0.24 mm at >40° pitch). Roll shares the fixed transmission
    analytically (roll trust zeroes w at 34.4° into the same regime) but
    has a weaker source (rollSearch keeps the detector within ±15° of
    upright and its unrotation is the identity — placement Δ 0.000 mm)
    and still no live >40°-roll fixture coverage.
  ATTRIBUTION (z-decomp, per-frame exact decomposition placeZ = bridge +
    contact + pupil + rest + seat(ζ+guard) + offset, sum-check 0 mm, all
    three fixtures, production path — ranked):
    (1) the seat chain transmitting the view-morphed surface: the raw
    standoff law rises +3.0..+6.3 mm above frontal past 40° (≈ the
    wearer's full fitted-vs-canonical pad offset), half-absorbed by ζ in
    the w 0.3–0.9 band (+0.6..+3.6 mm) then LOCKED by the stage-6 freeze,
    bridged raw by the guard at 60–100% duty (+0.8..+3.3 mm mean, 28–54
    cap overflows a segment), with solve-adopted rest slide on top
    (+0.3..+2.1 mm; 29 solves/0 holds walking sApplied down the morph
    wedge) — net +3..+6.7 mm mean, 9.9–12.3 mm worst, vs a 0.2–1.6 mm
    converged-frontal floor, decaying over seconds after return (the
    "for no reason" tail);
    (2) MediaPipe's own pose z reading +5–7 mm toward camera at angle
    (apparent-size error — real, secondary, upstream of us);
    (3) nothing else measurable: bridge/carried depth ≤ 0.7 mm, scale
    swell 0.00%, pupil slide ≤ 0.25 mm; the synthetic perfect-landmark
    sweeps are pose-invariant (≤ 0.01 mm), so the entire live push
    enters through degraded landmarks reshaping the view-locked
    composite the seat reads.
  THE FIX: the seat's surface law is ADMITTED AT POSE TRUST through the
    anchors' own grammar before anything consumes it. frame.js: the seat
    state carries `needEst` — a FIT_WINDOW-bounded window of per-frame
    `rawNeeded` readings, each admitted at wPose (refused below
    POSE_TRUST_ADMIT, exactly as a sample never earns a slot), evicted
    lowest-weight-first (oldest among equals — floodproof: no dwell at
    partial trust replaces the frontal w=1 mass; an EMA-mass variant was
    tried first and MEASURED leaking ~80% of a 95-frame w≈0.89 dwell),
    weighted-median out (`weightedScalarMedian` — medianAnchors' law for
    one scalar, same tie rule) — published as `applied.needRef`. The ζ
    channel's target IS the carried estimate (the standoff reference is a
    face-space constant at every pose); the guard's baseline (fit.js) IS
    the same estimate — the guard answers penetration against the admitted
    law, never the raw morph (a full-trust penetration now reaches it
    through the window, ~0.5 s of tuck — accepted per the stage-6 cap's
    own "lesser visual evil" ruling). The stage-6 ζ freeze below w 0.3 is
    RETIRED by the measurement that created it: it locked transit-inflated
    ζ (+2.1..2.6 mm) through the whole >40° regime; with a pose-stable
    target the low-trust ease is the recovery, not the hazard. Equilibrium
    solves are REFUSED below the same 0.3 bar (scheduleSeatSolve, counted
    apart as seat.refusals; `pending` stays latched so returning trust
    re-solves within the refractory) — the fixture measured 29 solves/0
    holds across a hard-yaw segment, each walking sApplied down the morph
    wedge (−0.7 → −4.1 mm). No new free thresholds: wPose,
    POSE_TRUST_ADMIT, FIT_WINDOW, stage-6's 0.3, GUARD_BAND/GUARD_MAX and
    the channel deadbands, all pre-existing.
  MEASURED (z-decomp, production path, all three fixtures; dPlaceZ vs each
    segment's own frontal reference, mm, + = toward camera):
    >40°-yaw mean / guard duty / cap overflows, before → after:
      shay      glances +1.51/100%/32 → −1.56/0%/0
      ab-prod   yaw +3.95/61%  → +2.40/0%/0; glances +6.65/100% →
                +0.97/0%/0; browse +5.05/89% → +1.67/0%/0
      ab-fit    yaw +2.97/84%/31 → +0.57/0%/0; glances +5.56/100%/54 →
                +2.28/0%/0; browse +6.66/86%/28 → +1.49/0%/0
    whole-segment p95: shay glances 5.46 → −0.65, yaw 1.93 → 1.50;
    ab-prod glances 8.80 → 1.27, yaw 7.40 → 4.60, browse 5.79 → 2.89;
    ab-fit yaw 7.88 → 2.60, glances 8.33 → 5.33, browse 10.51 → 4.54.
    Frontal floors unchanged: eye-circles p95 0.17 → 0.17, 1.59 → 1.53,
    1.58 → 1.44. Synthetic controls stay pose-invariant (yaw sweep
    0.01 mm, roll 0.00 mm; rollSearch round-trip exact, placement Δ
    0.000 mm — the >40° roll transmission shares the fix by construction,
    still without live >40°-roll coverage).
  ACCEPTANCE vs the diagnosis's gate: (c) MET everywhere; (b) guard duty
    ≤10% and 0 overflows MET on every segment of every fixture; the
    ≤2.0 mm mean MET everywhere except ab-prod yaw (+2.40) and ab-fit
    glances (+2.28); (a) MET on shay glances, ab-prod glances, ab-fit yaw;
    NOT MET on shay yaw (1.50 vs 0.34), ab-prod yaw (4.60 vs 3.06),
    ab-fit glances (5.33 vs 2.88). Every miss sits at w 0.87–1.0:
    POST-SWING SURFACE RECOVERY admitted at full trust — rest slide from
    full-trust solves on the still-recovering surface, plus window
    turnover through repeated full-trust transits mid-sweep. That is
    upstream of the seat's reference; refusing it needs a
    surface-settled signal, i.e. a new threshold — left undone under the
    kill rule and RECORDED as the residual, with ab-prod's pitch second
    half as the clearest exhibit (s oscillating ±4 mm at w = 1 on the
    recovering surface; its p95 3.60 → 5.77 on solve-phase shuffle — the
    one worsened number, same mechanism, not the complaint's regime).
  HARNESS GATE: telemetry-replay grows pinned per-segment placeZP95Mm,
    over40{Frames,MeanMm,GuardDuty,Overflows}, seatRefusals; acceptance
    (b) is ASSERTED on the production pass (yaw/glances/browse with ≥10
    over-40 frames), acceptance (a) is reported and ratcheted through the
    pinned p95 metric. z-decomp remains the scratch diagnostician.
  BASELINES: telemetry-baseline.json re-pinned (previous pin at
    telemetry-baseline.pre-seatref.json.bak). 201 out-of-tolerance checks
    against the old pin: 144 are the new metrics with no baseline side;
    57 behavior deltas classified — 56 improvements in the claimed
    direction (guardPushes 118–594 → 0–6 per segment across all four
    passes, guard/ζ spans collapse, solves→refusals split), 1 accepted
    cost: production/yaw maxStepPx 0.2798 → 0.3626 px (the low-trust ζ
    recovery easing — sub-pixel, far under the 2 px bar). diag-replay:
    NO re-pin needed, 338/338 against the standing pin. SUITES:
    pipeline-check 344/345 (only the stage-0-ruled wall check; 16.3 ms in
    a pane session that had just replayed four fixtures back-to-back,
    settled frame 0.98 ms — the rebuild path it measures is untouched
    here; the real-Chrome re-confirm stays carried), telemetry-replay
    791/791 against the re-pinned baseline.
  LANDER VERIFICATION (same day, adversarial re-run in the pane): the
    mechanism re-derived against the code as landed (window admission at
    wPose, eviction floodproof by construction — a lone low-trust
    arrival into a full higher-weight window evicts itself; raw-law
    bit-parity preserved for seatConfigs without `needRef`); suites
    re-run green — telemetry-replay 791/791, diag-replay 338/338,
    pipeline-check 344/345 (the env-ruled wall check alone, 16.2 ms
    pane / 0.93 ms settled); every acceptance number reproduces exactly
    (ab-prod yaw +2.40 / p95 4.60, glances +0.97 / 1.27, browse
    +1.67 / 2.89, eye-circles 1.53; ab-fit yaw +0.57 / 2.60, glances
    +2.28 / 5.33, browse +1.49 / 4.54, eye-circles 1.44; shay through
    the pinned harness metrics and gate lines; synthetic yaw 0.01 mm,
    roll 0.00 mm, rollSearch round-trip 2.8e-13 px / 4.4e-16). One
    residual ADDED to the record: the carried window spans a glasses
    MODEL swap (the standoff law legitimately changes; ~0.5–1 s of
    full-trust convergence before the new frame's reference settles) —
    deliberately NOT cleared on the model/fit edge, because an emptied
    window at a bad pose falls back to the raw morph exactly when a
    slider is moved mid-turn; the identity/remeasure path already
    clears it whole via resetFit.
  A/B VERDICT RECORD (live session, 2026-08-17): production
    (POSE_FIT_DEFAULT=false) beat the flagged ?pose=fit by impression —
    the flag STAYS OFF and production ships with this landing;
    Candidate B remains shelved behind ?pose=fit awaiting its queued
    refinements (ISO_REF z² normalisation per the measured ladder
    iso·z² ≈ 78±7%, wSolve maturity ramp) and re-enters through the
    same live protocol.
  LIVE QUESTION for the next session: hold a side turn past 40° — the
    frame must stay seated, no forward ride; then return frontal and
    judge the ~2 s settle tail (the recorded residual) by impression.

Every stage: full harness green (existing checks modulo the Stage-0-enumerated
legitimate rewrites, each tied in a comment to the diagnosis finding it retires),
plus the stage's own new checks, plus the diag-replay A/B against the pinned
baseline, plus the frame-one bit-equality pin.

Stage-0 finding, decided 2026-08-16: "the deformation fits inside the tracking
loop" (the 13 ms forced-rebuild wall-clock check) reads 13.3–13.9 ms in the
CPU-throttled hidden Browser pane — 275/276 with all logic checks green. Ruling:
environment-bound, not a regression. Stages treat 275/276-with-only-this-check
as the green floor; its number must not WORSEN vs the Stage-0 report (13.93 ms),
Stage 3's ≤1.2× wall-time assert is the real budget guard, and the check is
re-confirmed in the user's real Chrome at Stage 6.

Diag-replay ratchet protocol (per stage): run assert mode against the pinned
baseline; classify every out-of-tolerance delta as (a) improvement in the
stage's claimed direction — expected, record it; (b) regression — STOP and fix;
then, once the stage's own checks pass, re-pin diag-baseline.json at the new
numbers so the next stage diffs against the improved floor. Improvements are
ratcheted; regressions can never hide inside a stale baseline.

## Constants, measurables, instrumentation

As listed in `design-stability-first.txt` (=== CONSTANTS ===, === MEASURABLES ===,
=== INSTRUMENTATION ===), amended by: SOFTMAX_TAU = 0.05 cm for sideInterference
(physics-first B.2; stability-first's 0.03 cm SOFTMAX_TEMP is superseded),
GUARD_BAND = 0.03 cm, EPS_BEAR = 0.08 cm, TAU_REST = 0.8 s (stability-first's
value wins over physics-first's 0.4 s — settling reads as intentional), the G10
floor caps, and PAD_SINK′ = PAD_SINK − τ_s/2.

## Invariants (unchanged, load-bearing)

Frame-locked composite; no blocking scan phase (frame one bit-identical to
today's pipeline, three separate mechanisms each individually preserve it: empty
person model, first-sample-adopted-whole pin filter, zero-confidence seat
fallback — pinned per stage); single-surface (seat field, occluder depth, mask =
same triangles via surfaceOf); placement principle (position re-measured every
frame — conditioning a face-space constant's innovation is allowed, freezing
position in face space is not); 30 fps (per-frame additions O(468) or O(existing
contact loop); event work ≤2 Hz).
