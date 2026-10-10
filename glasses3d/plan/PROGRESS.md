# glasses3d progress

## Active resume — WP6 (2026-10-10)

**ACTIVE FULL-PROOF GOAL — 2026-10-10, owner resumption.** Work continuously
offline until the complete proof comparison is ready. No intermediate visual
review: repair the measurement chain using independent controls and retained
failures, explain the facet-check discrepancy while preserving the preferred
appearance, then run the complete proof with one memory-sized local pool.
Pipeline integration is deferred until the owner reviews those results. This
supersedes paused-goal directions below. New ignored evidence:
`data/wp6/proof_resume_20261010/`; all sealed prior artifacts remain unchanged.
First test: the existing contour-normal camera proposal on non-side views only,
under unchanged observed-angle eligibility, bounds, objective and front guard.
Its wedge/prism/valley controls improve; articulated-side regressions forbid
applying it to side views. Replay both retained S1 cases before any full fit.
STOP-I, R1, all R3 clauses, WP5 closeout and the owner's separate session-prompt
edit remain unchanged. No paid calls or push.

**Live work, 10:50 +03.** The provisional two-case replay completed PID18088,
10:15:24–10:48:49, exit0. Older input improves **.698285874 -> .684009721 mm**;
harder input **worsens .760738195 -> .772316912 mm**, despite recovery of zone
level **.040553542 mm**, r_C **.006780937 mm**, and cited angle **.002573414 deg**.
Harder input stops at the four-round cap with front bound clear; do not call it
converged. Its diagnostic maximum across all read/side pairs is **.431985618 deg**.
This is retained failed evidence, not a method promotion.

The final opt-in helper now uses the existing `normal_or_point_residual` for
directionless samples, retaining every correspondence rather than dropping it.
The failing regression is now green: **14 focused tests pass**, PID25660,
10:49:16–29, exit0. **52/52 final independent controls pass**, PID6624,
10:49:16–48, exit0; all48 non-side improvements match the prior independently
validated proposal exactly, and four side fits are unchanged. Before this
normal-only correction, **39 existing/guard checks passed**, PID8496,
10:36:29–10:45:11, exit0. A first guard-test launcher misquoted pytest's `-k`
argument (exit4, no tests); the corrected explicit-test invocation above passed.
No default fitting callback, angle eligibility or frozen rules changed.

**Now running:** `s1_normal_joint_final_run/` (started 10:50 +03; read run.json
for actual PID), both saved inputs with the final helper. One12-worker pool,
sequential cases; source hashes must remain fixed. Do not edit the registration
helper, normal callback, validation source or retained inputs while it runs.
Next: inspect every recovery/sign/floor result, then execute the preferred-shape
reconstruction and complete offline diagnostics with unchanged stops. Full
comparison/verification/owner checkpoint are still outstanding; goal active.

Preferred-shape reconstruction preflight passes, PID32804 10:33:40–44, exit0:
exact preferred spec rebuilds with **504 identical vertices/faces, zero delta**,
its perturbation is valid, and all12 expected measured lines are present.
Prepared `reconstruct_preferred.py`, `run_complete_diagnostics.py`,
`measure_preferred.py` and `package_results.py` are not yet executed beyond that
preflight. The packaging script must only run after all required reports exist,
then its real browser output needs inspection and artifact sealing. Its limits
read RR.S1_RECOVERY directly, including the unchanged **.05** F1-rise tolerance.

**Earlier work, 10:23 +03 (superseded live-run instructions below).** Camera-only replay preserves both same-input fit-11
shapes and every measured line: older floor **.698285874 -> .683036574 mm**,
newer **.760738195 -> .749785341 mm** (still fails .7). Exact-line residuals
become **.051058817 / .054652982 mm**. After the correction, new-case transfer
with measured source/exact target is **.036229337 mm**; with exact source and
measured target **.768445702 mm**. True cameras retain **.671495055 mm** on the
measured pair: target-line uncertainty dominates, with residual camera bias.
No true-camera or exact-line result substitutes for frozen measured evidence.
`normal_replay_run` PID8344, 10:12:03–33, exit0; `transfer_components/` keeps all
signs and decomposition cases. Helper port reproduces all **52/52** saved
independent controls (48 non-side improve; four side fits unchanged), PID39080,
10:14:36–59, exit0. Additional coupled replays are running, actual PID18088,
`s1_normal_joint_run/`, started 10:15:24, one pool of12; do not kill a healthy run.

The opt-in helper is **not final/promoted**. A new directionless-normal regression
reveals that the first port drops those samples; retain the red test receipt
`normal_retention_red_run/` (1 failed as intended). After the currently running
source-frozen replays finish, replace this with the existing independently tested
`relief_normal_residual.normal_or_point_residual` fallback, keeping every match.
Snapshot `normal_registration_v1.py` preserves the provisional source. Then run
the new guard tests, old affected tests, all52 control comparisons and both
retained joint cases on the final method before complete diagnostics. Do not
mistake the provisional run for final validation or modify its loaded source.

Exact preferred-mesh R1 audit: production **.693583443676**, independent Sobol
**.693576833577**; maximum plane-share discrepancy **.000016888011**. F2 is
**48.15945%** exactly coplanar, but **68.90452%** lies within its .3mm band.
F2/F4 angle **3.261194 degrees**, other pair angles **12.155–22.147 degrees**.
This explains visible facets vs failed R1 without changing either geometry or
the rule. `preferred_r1/explanation.png` visualizes the counted area. Local
plotting first lacked matplotlib; the saved failure remains and the final figure
uses existing Pillow geometry drawing with panel clipping. No dependency install.

Prepared but **not launched** `run_complete_diagnostics.py` will execute P0/P1,
fresh delivered control/P2/S1, candidate and fitted slab, S2/S3/P3/all frozen gates.
All quality stops remain recorded and binding; subsequent stages are explicitly
offline diagnostics, never a waiver or acceptance. It requires final controls
and final retained cases first. It keeps the original proof topology/inputs;
the exact preferred mesh needs its own clearly separate comparison/measurement
beside that historical-family reconstruction. Do not present a historical-family
fit as replacing the owner's preferred topology. No full proof has run yet.

**Owner preference received — 2026-10-10: "test revision is better."** The owner
prefers `revised` over `current` in **http://127.0.0.1:63259/**. Use that exact
distal-arm revision as the baseline for the next offline visual comparison:
`data/wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json`
(SHA-256 `cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce`).
The mesh remains `revised` in that sealed viewer; do not rewrite its labels/assets
or replace historical controls. Asset-bound preference:
`data/wp6/pose_arm_resume_20261010/owner_visual_verdict_revised_20261010.json`.
All 270 checkpoint artifacts verified unchanged. This is a relative visual
preference, not a statement that the full product is perfect or WP6 is complete.
The review question is answered; the goal remains paused. No new fit or paid run
launched. Remaining tip/camera residuals, STOP-I, R1 and all frozen R3 clauses
remain open; WP5 stays closed. Earlier baseline directions below are historical.

**NEW REVIEW CHECKPOINT — 2026-10-10.** Controllable distal-arm comparison:
**http://127.0.0.1:63259/**, server PID **39488**. Show it and pause the goal for
owner review, per the explicit stopping rule. Exact accepted `current` mesh is
preserved; the separate copy changes only d > 100 mm. It raises the upper/lower
tip **2.528439/.361057 mm**, moves inner x **+1.581311 mm**, changes thickness
**-2.112391 mm** (4.28 -> 2.167609), and length **-.324955 mm**. All facet planes,
nodes and proximal profiles (including thickness) remain unchanged. This is a
conditional tool-test copy, not exact physical truth, product delivery or WP6
closeout. Current visual review is pending; do not infer acceptance from the
earlier apex verdict. Evidence: `data/wp6/pose_arm_resume_20261010/`.

The six-original-view fit resolves the earlier sign conflict in tip thickness;
both black-photo checks improve under both retained camera bases. Saved-camera
distal contours: black-side **5.765920 -> 4.977340 px**, black-oblique
**8.563610 -> 6.690734 px**. Original 03/04 distal checks still worsen slightly
**4.241473 -> 4.405202 / 8.425984 -> 8.685570 px**. Camera-refit rear-arm contour
improves in **all eight** photos; black-oblique **8.100 -> 6.118846 px**. Whole
contour improves in seven, with 04 **2.586876 -> 2.601316 px** adverse. Keep these
results; no all-gates pass or perfect shape claim. The two black photos are
separate checks for this fit, not unused throughout the exploratory session.

Viewer starts at **Tip close-up**; synchronized orbit, zoom, Side/Top/End/Oblique,
lighting, surface/highlights, image export and all eight photo overlays checked.
Browser PID **6528**, **08:52:27.796–08:52:37.371 +03**, exit0. Side/oblique tip,
narrow layout and all-eight contact sheet inspected. Closed meshes, exact baseline
identity, 1x depth and private provenance denial verified. `review_checkpoint.json`
binds the assets, earlier failures, final tool source snapshots and receipts, and
verifies the prior 148-artifact checkpoint and frozen/photo/baseline inputs.
Keep gates, WP5 closeout and owner's separate session-prompt edit. No paid calls
or push. After review/resumption, address remaining tip curvature/camera residuals
and frozen S1/R1 failures; do not repeat completed fits merely to resume.

**Camera transport repair:** original cream photo 02 and the new black oblique
photo agree closely under a directly measured 2D similarity, without 3D geometry
in the registration. L60 contour mean/RMS = **1.004986/1.665719 px**; held-out
internal AB/BC lines = **.345039/.935830 px**. L45/60/75 and all nine starts remain.
Transporting the saved 02 camera gives black-oblique whole-contour **31.953996 ->
7.743727 px**, rear-arm **75.413034 -> 8.041914 px**, with the same accepted mesh.
These are two real photos, not independent viewing angles or new depth truth.
05 -> black-side transfer is weaker (L60 contour 3.3294 px; AB/AC 3.7104/5.5413 px);
keep black-side's existing camera. `camera_transport/report.json` retains all
thresholds and both original-camera bases, with saved/L60 predeclared for display.

New opt-in `g3d_host/camera_similarity.py` validates image similarities, registers
cropped mask contours bidirectionally, and transports camera scale/center/roll
exactly while preserving yaw/pitch/perspective and NormFrame. It is not imported
by the frozen fitter. **17 new tests, 56 total relevant camera/anchor/crop/mask
tests pass**, PID29572, **08:28:40.817–08:28:45.349 +03**, exit0. A final logging
correction separates pre/post ICP parameters and matched residuals; all 17 helper
tests pass again, PID48392 **08:49:22.731–08:49:25.181 +03**, exit0. Independent
nonplanar projection and independently rasterized crop/rotation/scale controls
are included; wrong-shape residuals are reported, never an acceptance pass.

Earlier initializers remain failed evidence: facet-line initialization worsens
black-side contour 7.2318 -> 15.419 px and only partly repairs oblique rear error
75.413 -> 42.544 px. Global WP3b contour initialization reaches rear 8.666 px but
has mixed recovery across 16 known cropped controls. Neither was promoted; no
frozen WP6 bounds or seeds changed. See `line_pose/` and `global_pose/`.

**Distal shape is unresolved.** Copies change only d > 100 mm; all facets and
proximal profiles remain fixed. Four-coordinate fit (top/bottom tip height,
length, inner position) on 01/03/05 improves those views but worsens held-out
02/04/black-oblique distal contours: **7.6198 -> 9.2050 / 8.1675 -> 10.4595 /
8.5597 -> 9.1837 px**. Both saved/aligned camera bases and every result remain in
`distal_arm_v2/` and `distal_validation/`. Adding tip thickness (`distal_section/`)
does not resolve the real-photo conflict and exposes a known-control optimizer
failure: negative-sign thickness error **.88184 mm**. An exact coupled geometric
proposal with descent under the unchanged image objective repairs both controls
to max **.010510/.003099 mm**, but leaves the real-photo optimum unchanged
(`distal_joint/`, termination `no_image_descent`, not a proof pass).

Paired bounded camera refits (`distal_refit/`, all eight photos) still regress
02 rear **5.216 -> 7.429**, 06 whole **4.370 -> 5.129**, and black-oblique rear
**8.100 -> 11.044 px**. Do not promote these copies or call them correct shape.
Terminal mask/photo inspection (`terminal_masks/`) follows the visible tips
reasonably; it does not establish an instrument defect. The current endpoint is
blunt in side/top views and broad in oblique views. The next predeclared diagnostic,
`distal_all_views_run`, uses the same five coordinates, all six originals and both
black-photo checks. Both camera bases start at the accepted mesh, with no best-start
choice. It completed PID41628 **08:47:22.767–08:47:51.042 +03**, exit0; this is the
reviewed copy above. A limited distal-copy test, not a full product reconstruction
or replacement S1. No new representation parameter was added to seek a pass.

All 148 earlier checkpoint hashes, baseline, photos and frozen inputs verified
unchanged by `distal_validation/report.json`. Its proximal audit mistakenly
skipped the `thickness` field under the name `thick`; the completed
`all_views_validation` corrects this audit and explicitly verifies all four
profiles, both bases, at 1001 stations through d=100. PID35068
**08:48:36.080–08:49:24.826 +03**, exit0. Final bounded checks PID2716
**08:49:47.535–08:51:04.485 +03**, exit0, reuse the eight identical accepted-model
fits and fit only the new copy. Complete R1 remains
**.693583443676 > .5**; STOP-I **.760738195495 > .7**, prior synthetic outline
**.206456 > .2**, and all R3 clauses remain binding. No full-fit/promotion claim.

Run receipts in the new root record arguments, environment, timestamps and exit
codes. Completed actual PIDs: similarity46764, camera transport6492, four-DOF36500,
validation18624, five-DOF29480, coupled repair40336, refit41756, masks23896; all
exit0. Masks **08:42:31.873–08:42:37.027 +03**. The original four-DOF launcher
failed before fitting (PID49892, pickling `__main__`); its code/log are preserved,
and the corrected runner uses `run_module(..., alter_sys=True)`. First line-fit
receipt has only launcher PID7840 (actual PID not captured); global actual PID26972
was separately observed. Later receipts distinguish launcher and actual Python.
No healthy run was killed; one pool at a time, measured free commit, BLAS1,
AboveNormal and EcoQoS opt-out. Existing review servers remain available.

**Owner review received — 2026-10-10: "both look perfect".** Both `current` and
`revised` meshes in the actual-geometry comparison at **http://127.0.0.1:53427/**
are visually accepted at this checkpoint, with no relative preference expressed.
Asset-bound verdict: `data/wp6/anchored_shape_resume_20261010/owner_visual_verdict_both_20261010.json`.
Keep the previously preferred `current` mesh as the baseline. The comparison goal
remains paused; the pending visual question is answered. Camera/arm ambiguity,
adverse cross-view checks and frozen numerical failures remain open; this is not
WP6 closeout or a gate waiver. Next technical work is resolving those issues,
not another apex appearance comparison. No new fit or paid run was launched.

**New actual-geometry review checkpoint — 2026-10-10.** Viewer
**http://127.0.0.1:53427/**, server PID **43664**. Pause the goal when the verified
comparison is shown, as the owner requested. Exact preferred `current` mesh remains
the baseline; the other panel is a **conditional test copy**, not a replacement or
delivered product. Only C's height moves **+0.909491996 mm**; outline, base, arm,
relief values, all other nodes and the broad upper plane stay fixed. Adjoining
facets change slope. Use the synchronized **Apex close-up** to see the smaller cut
across the logo triangle's lower tip. All eight same-camera photo comparisons and
the conflicting measurements are included. No full-shape/depth recovery claimed.
Final browser check PID28092 **06:44:08–12 +03**, exit0; desktop/apex/narrow views
inspected after fixing an overflowing control panel. Final checkpoint binds 148
artifacts and preserves the earlier checkpoint. Its completed receipt/log are
excluded from self-hashing; a preliminary self-hash attempt is retained separately.

New evidence: `data/wp6/anchored_shape_resume_20261010/`. The preferred virtual C
is **1.580207 mm below** its outline. Photos also show a rounded cut; do not force
a pointed intersection. The new opt-in `relief_junction.triangle_outline_gap`
measures that cut along C -> midpoint(A,B), normalized by that length. It cancels
image translation/rotation/zoom. Equal-weight 01/05/black-side measurements fit one
C-height coordinate; black-oblique is held out. Six retained camera/mask cases
give **+0.909492 to +0.951124 mm**. The predeclared saved-camera/L60 result changes
gap fraction from about **5.97% to 2.62%**. Observed 01/05/black-side/black-oblique:
**4.229/2.190/1.456/2.659%**. The held-out signed error changes **+3.3291 to
-0.0289 percentage points**. All 64 angular-box corner cases are retained; the
gap remains near 5.94–5.99% before and 2.61–2.63% after. Actual raster silhouette
checks differ from analytic contacts by .11–.98 px (pixel-boundary quantization).

**Mixed result, no promotion.** At saved cameras, new side AC worsens **2.2573 ->
3.6549 px**, BC improves **3.3242 -> 1.0667**; oblique AC worsens **1.6480 ->
3.8389**, BC improves **2.4968 -> 1.5235**. Original 01/02/05 crease checks also
worsen; every result is retained in `junction_validation/report.json`. Image
similarity aligned to unchanged A/B improves revised C in all three side views,
but worsens it in the earlier ABC-calibrated oblique camera. It is not independent
depth evidence. The fixed front model is approximate and its cropped landmarks
are not established as independent calibration truth. Larger arm mismatch remains.

Camera diagnosis: 24 visible-anchor cropped controls recover exact camera/observed
hinges with noiseless points, but .5 px noise can move unobserved-frame projections
by **17.49 px** despite full rank. `relief_anchors.fit_anchors` now reports scaled
SVD uncertainty, probe-coordinate uncertainty and unobserved hinge sides. It is
opt-in, never connected to the frozen fitter; full rank is not a precision pass.
All original failures remain. **17 new independent tests + 102 relevant existing
geometry/mask/ridge tests = 119 pass**, PID50260, **06:38:58–06:39:01 +03**, exit0.
The initial NumPy 2-vector-cross failure (unit PID20148 / diagnostic PID38724) and
accidental audit retry's FileExistsError are retained; corrected runs pass.

Complete R1: preferred **.693583443676**, revision **.674423483648**, both **> .5**.
`r1_complete.json` supersedes the first audit's LSQ-only revised lower bound
**.575734444798**, which was mislabeled R1; no pass was claimed. STOP-I stays open:
saved fit-11 floor **.760738195495 > .7**, prior preferred synthetic outline
**.206456 > .2**, all frozen R3 clauses unchanged. No full product fit or runtime
promotion. Fit PID12204 **06:30:02–04**; eight-view audit PID20048 **06:31:35–59**;
complete R1 PID5476 **06:40:03**, all exit0. Earlier checkpoint hashes are preserved.
No paid calls, subagents, pushes or WP5 reopening; owner session-prompt edit stays
separate. On resumption, resolve the remaining pose/shape ambiguity before calling
this local cut correction correct geometry; retain the preferred mesh and all
adverse cross-view results. Do not repeat completed diagnostics merely to resume.

**Owner resumed: actual shape comparison required.** The owner explicitly requests
a new goal and continuous work. A new active goal replaces the finished camera-only
review cycle: diagnose the cropped-camera failures, constrain pose using visible
frame/hinge evidence, test supported multi-view geometry corrections on copies,
validate tools independently, then show an actual revised mesh beside the exact
preferred `current` mesh and pause for review. Camera-only findings are progress
updates, not review checkpoints. Work sequentially, no subagents; no paid calls
without fresh estimate/approval, no push, WP5 closed, all gates unchanged.
New evidence root: `data/wp6/anchored_shape_resume_20261010/`. Starting from
2669bea. The first audit separates visible-frame camera drift from large errors
on genuinely unseen cropped arms; it retains every prior control and metric.
No product geometry change yet. Preserve the owner's session-prompt edit.

**NEW OWNER REVIEW CHECKPOINT — 2026-10-09 22:25 +03.** The approved steps 1–4
have produced a camera-only comparison at **http://127.0.0.1:58668/#photo-lab**,
server PID **28704**. Pause the active goal when shown, under the owner's explicit
rule. `data/wp6/camera_shape_resume_20261009/review_checkpoint.json` binds 128
artifacts; the earlier checkpoint, preferred mesh, all eight photos and frozen
inputs are unchanged. No supported real-depth/shape edit was established. The
3D before/after panels intentionally contain the identical preferred mesh; the
photo slider shows the actual camera/hinge change. All eight photo sheets, slider,
rotation, zoom, lighting, face highlights, export and narrow layout checked; final
screenshots and all eight overlays inspected. Open request queued in Codex.

Findings with corrected masks, predeclared middle L60 sensitivity: side contour
**7.231829 -> 6.333274 px**; oblique **31.953996 -> 10.425583 px**. Original
01–06 contours all improve slightly (03 only .05234 px). Some held-out facet lines
worsen: new side AC **2.25727 -> 5.06920**, BC **3.32420 -> 5.52069**; oblique
AB **1.31290 -> 9.93149**, BC **2.49676 -> 7.09574 px**. Oblique upper/rear
BK/BE improve **6.39519/8.51066 -> 4.85499/7.36023 px**. Its camera reaches yaw,
perspective, scale and vertical-shift bounds; old side views 01/05 reach roll bounds.
These residuals do not uniquely identify a shape error. All L45/60/75 rows and
ordinary/cropped objective comparisons are retained; no best-threshold selection.
Photo edges were excluded from this alignment but informed earlier shape/camera
construction, so this is not independent real-depth validation.

Two opt-in diagnostic helpers are added, without changing production imports or
frozen fitter math: `relief_crop.CroppedPhotoLevel` handles image cuts, and
`relief_photo_mask.dark_studio_mask` handles bright colored cropped lenses and
small logo/highlight holes on dark products against white. Independent tests:
**9 + 6 pass**, plus **31 existing matte/input regressions pass**. Camera recovery
controls remain mixed: **2/8 improve, 3/8 worsen, 3/8 unchanged** in complete-shape
projection error. A corrected viewport score does not establish pose recovery;
do not promote this diagnostic or infer correct depth from silhouette descent.

Receipts: initial alignment PID31464 **22:14:28–22:15:55**, repaired-mask matrix
PID43016 **22:19:23–55**, exit0, one 12-worker pool at a time (20,957,964 / 21,201,176
KB free commit). Final analysis PID41680 **22:21:12–17**, existing regressions
PID6288 **22:21:12–22**, browser QA PID41136 **22:22:38–44**, all exit0. Saved
`full_fit6` S1 replay PID16976 **22:17:10–16** reproduces **.763662908288 mm > .7**,
exact **.069957948109**, all 16 sign cases and truth/slab retained. The separate
fit-11 **.760738195495** failure stays; no new full shape fit. Preferred synthetic
outline **.206456 > .2**, R1, STOP-I and every frozen R3 clause remain unchanged.

Next only after owner review/resumption: retain the preferred geometry and resolve
the camera/shape identifiability conflict with frame/hinge constraints across views,
including cropped controls that currently worsen. Do not infer geometry corrections
from a local-bound residual or relabel this as a reconstruction pass. No paid calls,
pushes, WP5 reopening or public integration. Owner session-prompt edit stays separate.

**Owner resumed steps 1–4 (2026-10-09, 22:11 +03 onward).** A new continuous
goal is active, with the same pause-when-comparison-is-shown rule. Preserve the
preferred `current` mesh, evaluate fixed-shape camera/hinge registration on all
eight photos without using facet edges, identify cross-view residuals, and repair
only supported measurement defects with independent controls. New local evidence:
`data/wp6/camera_shape_resume_20261009/`; `baseline/manifest.json` binds the exact
spec, fit, reviewed meshes, all eight photos and frozen files. Preparation PID13372
finished successfully. No shape change, paid call, push or gate change.

The two cropped close-ups violate the old matte's clear-border assumption; its
contours visibly include white background/floor reflection. The ordinary camera
loss also penalizes geometry outside the image. A separate opt-in cropped-photo
level keeps the full viewport, ignores image-cut edges as object contours and
does not penalize unseen geometry. Nine independent cropped-box/uncropped identity
checks pass (PID13216, 22:13:10–11, exit0). It is **not** connected to the frozen
fitter. Eight analytic cropped-wedge controls precede all real-photo diagnostics;
12 workers, free commit 20,957,964 KB, run PID31464 at 22:14:28. New-photo dark-mask
sensitivity L45/60/75 is predeclared; retain every row, display L60 independently
of facet results. All old camera/hinge bounds and front guards remain. No full
product shape fit is running; earlier review checkpoints below are preserved.

**Owner review — current method preferred (2026-10-09).** Exact feedback:
"current method looks the best". Record this as the visual preference for the
`current` reconstruction in the latest rear-triangle + upper-face comparison.
Use that exact reconstruction as the visual baseline on resumption. Asset-bound
record: `data/wp6/rear_triangle_resume_20261009/owner_preference_current_20261009.json`.
This is not confirmation of correct real-product shape/depth, a gate waiver or WP6
completion. The outline failure and all frozen gates remain. No new fit or runtime
promotion was launched in response; the review checkpoint remains the stopping point.

**OWNER REVIEW CHECKPOINT — 2026-10-09 17:32 +03. Stop for visual review.**
The new comparison is ready at **http://127.0.0.1:53458/**, server PID **13264**.
Pause the new goal when shown, under the owner's explicit same-pausing-rule request.
Evidence: `data/wp6/rear_triangle_resume_20261009/`, including `review_checkpoint.json`.
Six meshes are selectable: combined candidate, previous extent, previous upper-face
candidate, flat control, current reconstruction and experimental reconstruction.
Separate orange upper-face / green rear-triangle highlights, synchronized angles,
lighting, export and all eight photo overlays passed browser checks; screenshots
were inspected. All earlier hash-bound artifacts and frozen files are unchanged.
The owner now prefers the current reconstruction; real-product acceptance remains open.

Both matched additional synthetic reconstructions completed at the four-round cap,
with final cameras for their returned meshes. Current: outline **.206456 > .2 mm**,
base **.375843 <= .5**, logo depth **.024217 <= .5**, upper slope **.018186 <= .05**.
Experiment: outline **.203739 > .2 mm**, base **.289553 <= .5**, logo depth
**.124452 <= .5**, upper slope **.038921 <= .05**. Both front constraints and R2
checks pass for this synthetic case. Neither passes all four recovery limits or
claims convergence. Measured/exact truth transfer: current **.086407/.093298 mm**,
experiment **.069792/.077475 mm**. These are not replacements for frozen S1/R3.
Run PID **29040**, 17:17:08–17:30:58, exit0, sequential 13-worker pools. Identical
inputs, original views, seed7, guide perturbations and caps for both methods; all
12 guided lines were detected. Negative-A bounds are explicit for this extra local
family; production bounds remain unchanged. A +.04 rise perturbation scales r_A
by the same ratio to preserve the zero line without deepening the indentation.

The combined candidate preserves the previous F4 plane and domain exactly, but
does not establish the real shape. Its conditional top-strip RMS is **1.75671 mm**;
side BJ/BE errors **5.46558/2.78844 px**, held-out oblique **4.61003/9.29661 px**.
All six originals use saved cameras without refitting, not R3 scoring. R1 still
fails: **.75147175 > .5**. Real camera/depth/top-strip assumptions and arm alignment
remain unresolved; the earlier upper-exit refusal is also preserved, not fixed by
this new case. Nine analytic upper-exit/preservation checks passed (PID48120,
17:16:26, exit0); tests committed **500318f**. No production runtime change in this
pass. No paid calls or pushes. Keep the owner's WP6_SESSION_PROMPT.md edit separate.
Next on resumption: use the current reconstruction as the preferred visual baseline,
then reconcile photo camera/depth evidence without sacrificing preferred facets or
waiving any frozen gate. Do not rerun these completed comparisons merely to resume.

**Feedback and pass history:** the owner says the rightmost triangle below
the broad upper face was already present in the previous rear-extent candidate.
They request another continuous goal with the same pause-for-review rule. The
preceding comparison goal was fulfilled and is closed; the new goal pauses only
when a new controllable comparison and matched reconstruction results are shown.
Evidence: `data/wp6/rear_triangle_resume_20261009/`. The upper pass had also changed
base slope, thickness and shared nodes; the lower/rear F4 normal changed 2.51177°.
A separate preservation probe retains the previous F4 domain, plane and normal
exactly, with unchanged outline/base/arm, and measures an added upper plane from
all prior top-strip samples. It builds cleanly without warnings, but has a larger
conditional top-strip misfit (1.75671 mm); this is not a correct-product claim.
Previous viewers and evidence stay immutable. WP5 and every WP6 gate stay unchanged.

**Earlier review checkpoint — 2026-10-09 17:00 +03 (superseded above).**
Offline tool changes and their independent tests are committed at **cea671c**.
The authorized broad-upper-face comparison is ready at **http://127.0.0.1:53328/**,
local server PID **50548** (`top_facet_resume_20261009/review_viewer_server.json`).
Pause the active goal when this viewer is shown, as the owner explicitly requested.
No owner appearance verdict on these new assets yet. WP6 is not accepted or complete.
The earlier synthetic acceptance remains separate; WP5 remains owner-closed.

The new candidate, previous candidate, relief-free control, current method's **last
valid state (incomplete)** and completed experimental reconstruction are all selectable.
The broad F1 face has optional orange identification, with unchanged depth/scale.
Rotation, zoom, light/material controls, all five meshes, image export, eight photo
overlays and narrow layout checked; final screenshots inspected. Mesh exports match
the source vertices/indices. `review_checkpoint.json` binds the reports, assets and
unchanged frozen files. All required runs have ended; only local viewers stay running.

**New reconstruction results:** current failed at the final outline retrace, K100.27
outside zone100; its pre-retrace state has outline error .230496 mm, base level .209421,
logo relief .631657, upper slope .045509. These are **incomplete-state diagnostics**,
not final gate measurements. The experiment, after the independent directionless-
normal repair below, completed at the four-round cap: outline **.203919 > .2 mm**,
base level **.309778 <= .5 mm**, logo relief **.578464 > .5 mm**, upper slope
**.027667 <= .05**. Final front constraint **fails at .550339 mm**. Measured/exact
truth transfer .203289/.153645 mm is from this additional test, not a replacement
S1 pass. Run PID44796, 16:51:32–16:59:14, exit0; all source hashes unchanged.
Truth/start/guides/seed/views/caps match the captured current case exactly. The
original experiment's assertion failure and all invalid-start/retrace failures remain.

Validation: 91 affected checks pass with 1 existing xfail; 6 exit/boundary checks
pass (five overlap that selection); 8 new normal-residual controls pass. Twelve
historical fixture specs are identical. R1, real-photo depth/camera/arm-transition
issues, STOP-I and every frozen R3 clause remain open and unchanged. No paid call,
push, public integration, Astra wrapper or product delivery occurred.
Next after owner feedback/resumption: jointly handle valid outline/facet updates
at the rear exit, then resolve actual photo camera/depth and rear-arm geometry.
Do not repeat the completed runs or label the photo-conditioned candidate correct.

**Upper-face pass history (16:11 +03 onward):** the owner authorized another offline
candidate and reconstruction comparison, then explicitly identified marked **region 1,
the broad upper face above the logo triangles**, not the narrow top-edge strip. Work
until the controllable comparison is ready and shown, then pause for their review.
Evidence lives in `data/wp6/top_facet_resume_20261009/`; preserve the preceding viewer.
New separate edge-candidate extraction keeps competing ridges instead of choosing a
topology. Independent analytic tests plus unchanged builder/trace checks: 80 passed,
1 existing xfail (PID 35692, 16:17:27–16:18:20, exit 0). Plane/ray helpers: 6 passed.
Both photos contain a coherent BJ boundary (side RMS .243 px; oblique .232 px); no
coherent GB edge was detected, which does not prove coplanarity. Conditional on the
retained camera/inner plane and the top-strip edge identities, fitting both top
branches jointly gives an upper plane RMS .15746 mm. Its shallow rise is retained;
the logo depth 3.97978 mm remains an assumption, not a new measurement. The old fixed
base was incompatible (1.64356 mm RMS); it is not used to manufacture a candidate.
The builder now has an explicit `top_zero_direction="rearward"` option and supports
virtual A ahead of the front clipping boundary. Historical defaults are unchanged:
all 12 fixture specs compare identical to 292b6b7. Independent analytic solid/exit
checks include three relief scales and refusal beyond the unchanged zone. Final
affected regressions: **91 passed / 1 existing xfail**, PID 38328, 16:40:16–16:41:25,
exit 0; added analytic boundary check included in 6/6 tests at 16:45:01–16:45:02.

`upper_comparison/` is built and inspected: upper rise **+.03647928**, upper boundary
exit d **94.76403 mm**, E **76.51244 mm**, zone 100 mm. The side BJ line is exact by
construction, not validation. At the oblique ABC-calibrated camera, held-out BJ RMS
is **3.56885 px**, BE **12.51659 px** (previous candidate 9.29236 at its own camera).
Both new and all six original photo overlays are preserved and inspected. The new
copy's R1 share **.74672298 > .5** (previous .72943824); no threshold changed. A
**38.216-degree arm-normal warning** identifies the unresolved rear transition.
Do not call this a correct real-product model. Optional orange F1 highlighting in
the new viewer identifies region 1 without exaggerating geometry.

New synthetic reconstruction is an **additional test**, not a replacement frozen
S1: same original five fitting views, six generated views, seed 7, 1 px camera
perturbation and the existing guide perturbation. Explicit positive-rise bounds
are local to this new family. Initial runs refused invalid starts; the revised
start scales the entire upper relief plane and tapers the top-outline perturbation
to zero at its exit-bearing zone knot. No quality threshold was searched or changed.
`upper_reconstruction_r3_run/` reached four current-method rounds then correctly
refused a final retrace whose upper exit moved to **100.27 mm > 100 mm**. Preserve
that failure. `upper_reconstruction_capture_run/` (PID **27016**, started
16:43:21 +03) reran with diagnostic-only pre-retrace snapshots, then tested the
existing side-camera experiment. The recording callback does not alter fitting.
The capture replay ended 16:48:47, exit 1: current reproduced the same final refusal
and saved its last valid state; the experiment then failed on its contour-normal
assertion before a shape round. That assertion assumed every gradient direction
was defined. Independent thin-mask controls reproduce cancellation to zero. The
new `normal_or_point_residual` retains the full point error for directionless
samples and exactly preserves the prior normal arithmetic otherwise. It neither
drops samples nor changes image-loss acceptance, bounds or front guard. Eight
independent controls pass (PID 7484, 16:51:23, exit 0). Only the local experimental
callback uses it; no production camera integration or frozen dependency change.
`upper_reconstruction_repaired_run/` completed as PID **44796**, 16:51:32–16:59:14,
13 workers. It ran the experiment only; the current result was reused unchanged.
Identical truth/start/guides/seed/view inputs were verified before final packaging.
The ready comparison and remaining failures are recorded at the top of this section.

**RESUMED by the owner, 2026-10-09 15:01 +03.** A new active goal is to work continuously until new
results are ready and shown for comparison and visual review. The owner explicitly rejects the current
shape as a representation of the real temple, based on the clearer new colour-variant photos. The earlier
positive review applies only to synthetic reconstruction accuracy. Fix the confirmed hidden-arm angle
defect; reassess the shape using the new photos plus every original view; validate the general method
under unchanged gates and produce labelled photo/model comparisons and a controllable diagnostic view.
WP6 is incomplete; WP5 remains owner-closed. Earlier pause entries below are historical.

New evidence: `data/wp6/new_reference_resume_20261009/references/` preserves both attached photos
byte-for-byte with source paths, hashes and the owner's geometry correction. Runtime **fit-11** repairs
the angle objective to compare the photo with the whole scene, holding the other parts at their input
pose. A fully hidden arm no longer supplies invented silhouette evidence. The same ROI, angle grid,
range, 0.002 observability threshold, side eligibility and front guard remain in force. Independent
analytic-box controls failed 4/8 before; the repair passes the original and held-out cases (both sides,
four yaws, visible/hidden, nonzero initial/fixed poses). `occlusion_after_run/`: 21 passed, PID 27872,
15:02:24–15:05:48 +03, exit 0. Wider `occlusion_regressions_run/`: 76 passed, PID 48372,
15:07:02–15:17:59, exit 0. Both runs have one benign pre-imported Dash pytest warning.
Keep the separate contour-normal and free-side-angle proposals unintegrated. The current-method-only
fixed-input replay of the newer failing S1 case completed in `s1_fit11_fixed_input_run/`: PID 29844,
15:21:26–15:36:50 +03, exit 0, 12 workers. All original lines, five protocol views and 16 sign controls
are retained. Measured transfer is **0.760738195495 mm**, exact **0.067896192015 mm**, unchanged from the
earlier current-method diagnostic. Round cap; final front bound clear. The independently valid hidden-arm
repair does **not** resolve this saved instrument-floor failure. No S1 or downstream acceptance.

**New-photo shape evidence:** `photo_edge_audit/` preserves all traced lines and failures from both new
photos. The rear boundary BE is clear in both. `rear_facet_extent/` measures its end in the side photo:
the saved shape fixes E at station 55 mm, but this image locates it near 76.44 mm using the retained side
camera and saved base surface. Side-camera transfer is calibrated on A/B/C (1.20–1.66 px residual), not an
independent camera proof. Background thresholds 45/60/75 L* and base offsets -2/0/+2 mm give 76.33–76.55 mm;
this is a sensitivity range, not a confidence interval. The saved E projects about 185 px from the new
observed E. The inspected overlay shows the shape-family mismatch directly. This does not measure facet
depth or establish a new top-strip tilt.

**New comparison built (15:25–15:34 +03):** `extent_comparison/` calls the existing pyramid builder on
a copy. E becomes **76.456748 mm**; the affine base extends to the next existing outline station,
80 mm (maximum base change 1.404152 mm). Original A/B/C world coordinates, outline and **3.979779 mm**
relief remain unchanged. This is an isolated extent correction, not reconstructed real-product depth.
All three solids (old/new/relief-free) pass core closed-manifold/planarity checks and exported meshes
round-trip exactly. Native side-photo BE line RMS: **86.750832 -> 2.788437 px**. In the second photo,
ABC alone calibrates a diagnostic orthographic camera and BE is held out: **32.237786 -> 9.292364 px**.
All nine camera starts remain recorded. The remaining mismatch is material. The endpoint correction is
partial, and these pixel diagnostics are not R3 acceptance scores. All six original-view overlays are
retained at the saved delivered cameras, without refit. Both new overlays and the original-view contact
sheet were inspected; original-camera contour mismatches remain visible.

**Depth diagnosis:** `photo_depth_probe/` shows an A/B/E coplanar-base, orthographic-camera attempt hits
its roll/depth bounds and cannot explain C. Its first script run failed because finite-surface lifting
excluded virtual A; the second uses the extended affine plane and completes. Do not interpret its raw
out-of-band depth as a measurement. `joint_photo_geometry/` then permits perspective, retaining every
start and threshold. Three known-scene controls recover the truth from at least one start, but other
starts can be degenerate (all saved). The real-photo local optimum near 2 mm has a near-zero Jacobian
singular value. `photo_depth_profile/` explicitly profiles 0/.5/1/2/3/4/5/6/7 mm, each with its own camera:
RMS is 1.740415 px at 2 mm and 1.804458 px at 4 mm, only **0.064043 px** apart. The four-point fit is
not independent validation and does not establish real depth. No depth change was promoted into the
comparison copy. A/B/E coplanarity, side-camera calibration and perspective remain assumptions to resolve
against the original front/top/rear evidence before a full product fit.

**New controllable viewer:** `http://127.0.0.1:54686/`, actual detached server PID **40292**, started
15:32:08 +03; receipt `new_reference_resume_20261009/review_viewer_server.json`. `review_viewer/` contains
the copied meshes, rotation/zoom/pan, shared camera/light controls, previous-shape/flat comparison, clay
or black material, image export and eight photo overlays. Depth is labelled retained/unresolved throughout.
Browser interaction, image loading, local-only requests, private-file refusal and narrow layout pass;
`review_viewer_qa_r2/` screenshots inspected. Initial packaging read UTF-8 as a Windows code page; explicit
UTF-8 fixes the badge/labels, and all browser checks were repeated. The app open request returned queued;
give the owner the direct URL. The earlier synthetic viewer remains available separately. No owner verdict
on these new assets yet; WP6 remains open and all gates remain unchanged.

**Review checkpoint verified (15:37 +03):** `review_checkpoint.json` binds the new meshes, images,
reports and successful run receipts. All launched required checks finished, current replay input/source
hashes are unchanged, viewer geometry matches the verified export, and both frozen file hashes remain
unchanged. The requested work-until-review checkpoint is ready; the owner still needs to judge the new
comparison. This is not WP6 completion or a claim that the real temple is fully repaired. Next technical
work: resolve camera/depth and remaining shape-family assumptions with the original front/top/rear
evidence, while continuing the saved STOP-I diagnosis. No paid run, push or WP5 reopening occurred.

**Owner visual review received (2026-10-09):** “both current method and experiment showed very good
reconstruction with no noticeable differences between the results.” This applies to the synthetic
`full_fit6` comparison in the viewer. The asset-bound record is
`data/wp6/line_evidence_20261009/s1_joint_interactive_owner_review.json`; source/export hashes verified.
No visual preference, real-product acceptance, experimental-method promotion or gate waiver is implied.
The owner supplied two clearer black-colour photos of the same Prada model and asked what comes next.
Recommended next pass on resume: inspect their facet correspondence and compatibility with the original
photos; repair the independently reproduced occlusion/angle defect and validate it under unchanged gates;
then use the evidence audit to justify any shape-family change before another full product fit. Keep the
current method as baseline and the experimental proposal separate. The later explicit instruction above
resumes the goal and supersedes this earlier discussion checkpoint.

Owner-requested extra depth views (14:25 +03) are in
`data/wp6/line_evidence_20261009/s1_joint_edge_views/{top_edge,hinge_end}.png`.
Both inspected orthographic images show the unchanged saved `full_fit6` experimental mesh beside a
copy of its own base with relief removed, at identical scale and without depth exaggeration. This
explanatory control differs from the earlier truth-based control. Source hashes verified unchanged;
receipt `s1_joint_edge_views_run/`, PID 47216, 14:25:30–14:25:33, exit 0. Visualization only; no fitting,
runtime repair, acceptance change or goal resumption.

Owner-requested interactive viewer (14:31 +03): `http://127.0.0.1:52003/`, detached local server
PID 8252; receipt `line_evidence_20261009/s1_joint_interactive_server.json` records its current URL.
The ignored `s1_joint_interactive/` folder contains the saved experimental/current/truth meshes and
experimental relief-free control, with rotation, zoom/pan, light controls and synchronized comparison.
Export verified unchanged source hashes and exact triangle-index/position round trips (common translation
only). Browser interaction checks passed with no external requests or page errors; screenshots inspected
under `s1_joint_interactive_qa/`. Restart only the viewer if needed with `serve_joint_viewer.py` in the same
evidence folder; it selects an available loopback port. The visualization server remains available while
the WP6 goal stays paused. No fitting or runtime repair occurred.

The occlusion reproducer and repair are now complete as recorded above. Preserve the owner's separate
`WP6_SESSION_PROMPT.md` edit. Historical before-fix failures remain evidence; they are not current failures.

WP6 offline repair resumed from `ad5105e` on branch `glasses3d`. WP5 remains closed by owner acceptance.
The original saved S1 now passes with identical inputs, but a fresh full proof regenerating S1 from the newly
fitted delivered cameras still fails the instrument floor. The camera repair is not yet robust across both cases.
Do not repeat the initial diagnosis; continue from the new failure and its completed decomposition below.
The current repair/checkpoint is described below. No paid run or push has occurred.

**Latest checkpoint (13:28 +03): the four-cell fixed-input diagnostic is complete**, with input/source
hashes unchanged and exit 0. On the newer failing inputs, the side-angle counterfactual gives measured
transfer **0.760738 -> 0.686086 mm**, exact **0.067896 -> 0.038064 mm**, and mesh-depth RMS
**0.048359 -> 0.042315 mm**. It converges in four rounds with a clear front bound. The older-input pair
improves transfer but worsens depth RMS; retain that mixed outcome. This is **not a frozen-protocol S1 pass**:
production side-angle eligibility/dependencies are unchanged. R1, P2 and the real-photo gates remain open.
Full audits and inspected rotating-view/moving-light visuals are in `data/wp6/line_evidence_20261009/`
under `s1_joint_audit/`, `s1_joint_geometry_audit/`, `s1_joint_visuals/`. The follow-up controls are complete
below; do not rerun the matrix. Work is paused at the owner's request.

Read [WP6_HANDOFF.md](WP6_HANDOFF.md) fully, then [WP6_DESIGN.md](WP6_DESIGN.md). Use
[WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md) to begin the next session. The next agent is authorized to
continue offline WP6 diagnosis and repairs under unchanged gates. Independent line calibration and camera
attribution are complete. Bounded joint camera/hinge alignment is validated and committed at `640164f`.
The fresh complete S1 gives **0.691874 mm** measured floor against **0.7 mm**, with all four tested recoveries
passing. All original line endpoints/errors and input hashes are unchanged. The fresh full-proof S1 gives
**0.763663 mm > 0.7** despite all four recovery checks passing. Its true-camera diagnostic is 0.671495 mm;
camera/shape coupling still contributes error. Continue offline diagnosis; public integration stays gated.
**STOP-I blocks acceptance and downstream promotion, not diagnosis or repairs intended to resolve it.**
KEEP R1 and every frozen R3 clause; no waiver. Do not repeat the resolved waiver decision.

## Current status

| Work | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b / WP4 | Complete and tested |
| A–E repairs, option-B promotion | Complete and verified; do not redo |
| WP5 reusable library G1 | Complete and verified at `f020b57` |
| WP5 | Complete by owner visual acceptance; observed-slope join + Poisson default at `4304c1d`. Historical numerical failures remain recorded. |
| WP6 | Original same-input S1 passes; fresh full-proof S1 still fails STOP-I at 0.763663 mm. P2 A agreement also fails. R1 and all real-photo gates remain open. No quality promotion. |
| WP2 | A–C verified; D–F and agent wrappers remain unfinished/gated |
| WP7 / WP8 | Design only / not started |

## Current independent diagnosis — 2026-10-09

The preceding runtime **fit-10**: `relief_cue` now compares corresponding valid offsets/positions on both sides
of a line before taking the existing difference of medians. Independent side masks previously compared
different parts of a longitudinal lighting gradient and could invent a cross-line step, even with zero
paired positions. Off-image samples also reused clipped border pixels. The photo sampler now refuses
insufficient paired evidence and off-image locations; the model sampler uses the same pairing. Full valid
support keeps the original arithmetic, offsets, minimum count and thresholds. No guide/error/line changes.

Independent known-field reproducer: **25 fail / 12 pass** before (`cue_pairing_before/`, PID 6312,
10:54:05–10:54:06 +03, exit 1). After repair, **61 affected checks pass / 1 existing xfail**, including
synthetic cue/trace/fit and cache validation (`cue_pairing_after/`, PID 36324, 10:55:27–10:56:34, exit 0).
Review moved photo-only spread computation out of the repeated model path; the same final selection passes
**61 / 1 xfail**, 65.69 s (`cue_pairing_review/`, PID 18824, 10:58:25–10:59:31, exit 0).
All **24 held-out diagonal RGB-ramp controls** pass through real lightness conversion and Gaussian blur:
the largest false step drops from **13.168581 L*** to **0**, and disjoint support is missing, not a zero
measurement (`cue_pairing_audit_run/`, PID 49796, 10:57:26–10:57:28, exit 0). The original implementation is
loaded from `cce8f7b` for comparison. Both real-photo records reproduce the original topology measurements
exactly; all 16 stored crease steps and the topology verdict remain unchanged after repair. This is a general
instrument defect, **not the cause of Prada's retained P2/R1/STOP-I failures**. Old caches remain diagnostic
inputs, not current-version acceptance proof. No model is promoted; no paid run or push occurred.
The fixed-input S1 cue audit also retains both saved cases, all three crease views and truth/recovered/slab
models (18 rows): photo records unchanged; maximum model shade-step change **2.105e-8**. Receipt
`s1_cue_pairing_audit_run/`, PID 28552, 11:03:10–11:03:17 +03, exit 0. This does not justify another S1 fit
merely to try the sampling repair; it is not evidence that STOP-I has passed at fit-10.

The previous checkpoint made progress: cap-hit corner repair `615c7e8` (fit-9), fixed-family R1 bound and
resolved reporting `ff96e1f`. The owner has seen the labelled synthetic before/after diagram. No quality
acceptance is implied, and their request to see multi-angle/lighting results remains active.

`line_evidence_20261009/corner_stereo_v2/` rules out replacing the side-view lift with unconstrained mirrored
stereo at the retained cameras. The real 05/01 corner-ray angles are only **0.0377–0.3480 degrees**; largest
local sensitivities are **25.0–738.9 mm per input corner pixel**. Tiny ray residuals can coexist with depth
estimates hundreds of millimetres from the temple. 05/02 gives B a **62.3–62.7 degree** angle and about
**0.17 mm/pixel** sensitivity; A/C lack the required intersecting lines in 02. Opposite-side pairing also
assumes the current model's mirror symmetry, not independently measured product symmetry. This diagnostic
does not replace P2 or violate the unchanged rule against taking its (d,y) from 02.

Ten independent known-point controls check the ray solver: coincident mirrored views are rank deficient;
other declared baselines recover the point within 1e-8 mm. In both saved S1s, ideal projected lines at true
cameras recover the known nodes, whereas small tracer residuals or fitted-camera errors become huge at the
side pair's weak angle. Keep three line conditions distinct: ideal geometric projection, recorded lines with
injected errors removed (still carrying tracing residual), and measured lines. Every original 16 target-error
sign case is retained per S1. The differential mm/pixel values are sensitivity, **not uncertainty intervals**.
No inferred point is selected, clamped, written to a model or promoted. Drivers/receipts: `probe_corner_stereo.py`
(PID 49508, 10:29:05–10:29:09 +03) and `probe_corner_stereo_v2.py` (PID 36412, 10:29:55–10:29:59), both exit 0.

`probe_camera_joint_objective.py`, recorded before its run in `camera_joint_objective/plan.json`, tests
24 independent analytic-prism cases covering both sides, two camera yaws, three articulation states and exact/
0.5-pixel endpoint errors. It tests a bounded camera step using the **existing** silhouette-plus-crease shape
objective and weights, with the original camera box/front guard and fixed side theta zero. The hypothesis is
that the camera/shape objective mismatch can leave pose information unused. No R3 score selects the camera;
known projections grade recovery separately. This is an offline diagnostic, not integrated or tried on S1.
Baseline: **20 improve / 2 worsen / 2 unchanged** in aggregate known-point projection; largest increase
0.016102 px. Held-out controls change depth, pitch, yaw, articulation, noise and perturbation direction:
**12 improve / 8 worsen / 4 unchanged**, largest increase **0.362931 px**. All eight regressions are articulated
and evaluation-limited. Baseline PID 32100, 10:33:40–10:35:52 +03; holdout PID 7224, 10:37:45–10:39:51; exit 0.

`probe_camera_joint_convergence.py` retains **all 48 cases** and continues only accepted budget-limited
proposals using the same bounded objective. Stopping uses relative objective change (1e-4), no progress or
the original front guard, never known-truth error or R3. All finish before the reported 50-round cap (maximum
15 rounds). Baseline **19 improve / 3 worsen / 2 unchanged**; held-out **13 improve / 7 worsen / 4 unchanged**,
largest held-out increase **0.791892 px**. Every aggregate regression is articulated. The near arm itself
worsens in nine held-out cases, by up to **1.209075 px**, so this is not merely an invisible far-arm tradeoff.
The earlier baseline's `near`/`far` component labels meant fixed +arm/-arm; the convergence report and summary
correct their names and interpret near by view side. The aggregates and original files are unchanged.
Convergence receipt: `camera_joint_convergence_run/`, PID 39592, **10:45:36–10:49:55 +03**, exit 0; one
12-worker pool after 24,314,264 KB free commit, BLAS 1/EcoQoS opt-out. Read-only summary PID 17256, 10:51:03,
exit 0, `camera_joint_convergence_summary/`. **Do not integrate this camera step or try it on S1.** Increasing
optimization effort does not resolve the independent regressions. No camera, weight, side theta or gate changed.

The mask hypothesis produced the independently verified repair above, but no changed real-photo evidence
supporting another topology. Continue the representation/observability diagnosis under the frozen side-theta
rule; do not select an alternative topology merely because R1 fails or repeat the rejected camera experiment.

`side_pose_assumption/` is an explicit **counterfactual diagnostic**, not a production change: a local copy
of the silhouette aligner frees both side hinges on the same 24 unique baseline/held-out images. The 48
source rows are retained by aliases; their two line-noise levels have identical image/camera inputs, and no
crease line enters this fit. Only the copied function changes, with the exact source saved; production,
frozen dependencies and gates remain untouched. Baseline 8/12 projection errors improve, 4 unchanged;
held-out 12/12 improve. But mean errors remain **1.872175 / 1.017494 px**, and some hinge estimates remain
far from their known values. Two baseline cases hit the 50-iteration cap; several stop without image descent.
This is neither a complete pose solution nor evidence authorizing a side-theta rule change. Receipt
`side_pose_assumption_run/`, PID 17704, 11:07:13–11:07:34 +03, exit 0, 12 workers after 22,329,760 KB free commit.

The poor rigid controls motivated an **unchanged-rule** diagnostic: `probe_camera_base_restart.py` compares
the current result with the same existing aligner started directly at the original base camera, side theta
still zero. Selection uses a common full-scene image-loss grid and the original front guard, never truth or
R3. All 24 unique images / 48 source rows are retained. Rigid true-camera loss witnesses are evaluated only
after selection. Baseline 4 improve / 0 worsen / 8 unchanged; held-out 8 / 1 / 3. The held-out regression is
source rows 12/13 (same image): aggregate **0.850678 -> 1.847879 px**, near arm **1.009672 -> 3.098374 px**,
despite lower common image loss and a clear front guard. **Not integrated or tried on S1.** Output
`camera_base_restart/`, PID 12104, 11:11:48–11:12:18 +03, exit 0; 12 workers after 22,296,892 KB free commit.

`camera_crop_loss/` separates a control-set confound: four of eight distinct rigid images clip a temple at
the canvas edge. At their exact true cameras, native masks match byte-for-byte and registration loss is zero,
but the existing outside-ROI penalty is **0.015702–0.020519**. The other four rigid controls have zero true
loss. A poorly recovered rigid case (held-out row 20, 4.01 px) is **unclipped**, so clipping is not the whole
explanation. Every view of **both S1s** has zero true-camera registration loss, zero outside penalty, no
foreground on the canvas boundary and identical native masks. Thus cropping is not the saved S1 failure's
cause. Receipt PID 50044, 11:14:23–11:14:38 +03, exit 0. No penalty, view or frozen input was changed.
`camera_uncropped/` adds **every** independent image on a canvas padded by 200 native pixels per edge,
retaining all cropped results. All 24 original images match their corresponding native crop byte-for-byte;
every extended image lies inside the canvas, and all true-camera image losses are zero. Refit each current
camera, then compare the original-camera restart using the unchanged objective/bounds/front guard/side theta.
Baseline: **5 improve / 3 worsen / 4 unchanged**, maximum increase **1.173446 px**; held-out **7 / 1 / 4**,
maximum increase **0.587855 px**. One small rigid regression and three articulated regressions remain.
This rules out cropping as the sole cause and does not support integrating the restart as a recovery fix.
Receipt `camera_uncropped_run/`, PID 20172, **11:18:15–11:20:14 +03**, exit 0; 12 workers after 21,858,468 KB
free commit, BLAS 1/EcoQoS opt-out. Every task finished; no pool remains active. No S1 or product fit was run.

`uncropped_side_pose/` completes that counterfactual from **both fitted and original camera starts**, with
side hinges initially zero and production fitting retained as fallback. Selection uses one common full-scene
image-loss grid across all posed candidates and the original front guard, never known projections or R3.
All 24 fully visible images / 48 source aliases are retained. Baseline **11 improve / 0 worsen / 1 unchanged**,
mean projection error **1.676603 -> 0.144647 px** (maximum final 0.686331); held-out **11 / 0 / 1**,
mean **0.954629 -> 0.142422 px** (maximum 0.327381). This is stronger independent evidence of a pose-model
limitation, but raw hinge angles are not guaranteed recovered. The copied diagnostic aligner alone frees
side hinges; production and the frozen dependency table remain untouched. Receipt PID 37464,
**11:24:02–11:25:11 +03**, exit 0; 12 workers after 22,063,324 KB free commit.

`side_pose_independent/` validates that exact diagnostic on **48 new predeclared controls**: both sides,
yaw 77/97 degrees, pitch -6/22, perspective 0.08/0.29, 4.7 px/mm, roll -1.7, zero and asymmetric hinge
pairs (near/far 2.25/-0.75 and -2.25/3.5 degrees). The existing valley fixture adds a different outline and
surface; prism depths also change. All images are fully visible with zero true-pose image loss. The same
two starts, objective, bounds and front guard select poses before either common-frame hinge is graded.
Prism: **20 improve / 0 worsen / 4 unchanged**, mean projection **1.155085 -> 0.172933 px**;
valley: **21 / 0 / 3**, **1.255342 -> 0.109694 px**. Maximum final projection: **0.843574 / 0.619401 px**.
Common-frame near-hinge error still exceeds 0.3 degrees in **seven** cases; maxima **1.065444 / 0.936097
degrees**, far-hinge maxima **2.147570 / 0.117227 degrees**. All seven near-angle failures terminate at
`no_image_descent`, not the iteration cap. These are noise-free known-geometry controls, not recovery of
unknown product geometry. The result supports the diagnosis but **does not establish a complete repair**.
No production or frozen dependency changes and no S1/quality acceptance claim. Receipt PID **37048**,
**11:32:19–11:39:42 +03**, exit 0; 12 workers after **22,047,212 KB** free commit.

`side_pose_loss_paths/` checks all 48 selected-to-truth oracle paths at ten predeclared fractions with a
common image-loss grid. All samples stay inside the original camera box and pass the original front guard.
**47/48** sampled paths are nonincreasing; **43/48** have a lower loss within the first 10% of the path.
All seven near-angle failures have such a small improving step; one has a small intervening pixel-loss
increase. Every true-pose endpoint has zero image loss. This points to an optimizer limitation in these
controls, but finite sampled paths do not prove global convergence or an uncertainty bound. No oracle
pose is selected or supplied to a gate. PID **50628**, **11:40:06–11:40:30 +03**, exit 0; 12 workers after
**22,068,104 KB** free commit.

`contour_normal_steps/` tests that optimizer hypothesis in **96 predeclared rows**: all 48 controls in
fixed-side production mode and free-side counterfactual mode. A local function copy constructs proposals
from contour-normal residuals (one grid-pixel derivative smoothing), avoiding penalties for tangent motion
along an edge. The **existing** whole-image score still selects steps, with original bounds/front guard/
stopping. Fixed-side mode gives **37 improve / 3 worsen / 8 unchanged**; largest projection increase
**0.146695 px**. All three regressions are articulated valley cases (41, 44, 47). Thus do not integrate this
as a camera-only repair. Free-side mode gives **47 improve / 0 worsen / 1 unchanged**, mean projection
**0.141314 -> 0.013627 px**. Maximum common-frame angle error is **0.060634 degrees near / 0.061121 far**;
all 48 recover both angles to the existing 0.3-degree numerical tolerance. This is still a counterfactual
known-geometry diagnostic, not a frozen-protocol S1 pass. PID **49136**, **11:42:57–11:43:54 +03**, exit 0;
12 workers after **22,089,924 KB** free commit.

`contour_normal_holdout/` then applies the **unchanged** proposal to all 24 earlier fully visible controls
(48 line-noise aliases), held out from contour-normal development. Every case improves, none worsens.
Baseline mean **0.144647 -> 0.052773 px**, held-out mean **0.142422 -> 0.075795 px**; maximum common-frame
angle errors over both temples **0.110666 / 0.157016 degrees**. Inputs/runtime/frozen hashes remain unchanged.
PID **46840**, **11:46:11–11:46:22 +03**, exit 0; 12 workers after **22,102,656 KB** free commit.

`s1_side_pose_counterfactual/` now applies the validated diagnostic to both retained S1s, at each saved
recovered geometry and in a separate true-geometry control. It fits only 01/05 camera/pose, selects from
the saved and original-camera starts using common-grid image loss and the original front guard, and
retains every other view, measured/exact line and all 16 error signs. No shape refit or product fit.

| Retained S1 | Saved measured / exact mm | Recovered-geometry pose diagnostic | True-geometry pose diagnostic |
|---|---|---|---|
| Original-input `s1_fit6` | 0.691874 / 0.062110 | **0.651047 / 0.028755** | **0.596224 / 0.042336** |
| Fresh-input `full_fit6` | 0.763663 / 0.069958 | **0.723686 / 0.032661** | **0.669760 / 0.043069** |

The fresh failure therefore **persists above 0.7 mm at the recovered geometry**, even in the counterfactual.
True-model R2 remains true and zero-relief-control R2 false in every variant. Original-input error signs
over 0.7: saved 2/16, recovered-geometry 1/16, true-geometry 3/16; fresh-input: **6/16, 6/16, 4/16**.
No error sign is selected. Common-frame near-side angles improve, but some far-side angles at recovered
geometry still exceed 0.3 degrees (about 0.36–0.37 in 01 and 0.35 in fresh 05). Thus additional side-angle
dependencies must not be silently omitted. The measured/exact scores move differently: a lower measured
score does not alone establish a more accurate pose. All source/runtime/frozen hashes remain unchanged.
Receipt PID **34080**, **11:49:59–11:53:36 +03**, exit 0; eight workers after **22,080,184 KB** free commit.
No current process or pool remains active. The production fresh S1 still stands at **0.763663 mm STOP-I**.

Next action: investigate unknown-shape/camera coupling with this independently checked proposal, before
considering a production change. A fixed-input joint synthetic diagnostic can retain the saved measured
crease lines, original start/family, all five fitting views, original four-round/evaluation limits and
retrace/front/hinge constraints; use the free-side contour method only in an explicit local diagnostic
camera callback. Grade every 01/05 side angle as well as the original 02+x pair, and retain both S1s and
the zero-relief/error-sign controls. Do not change production eligibility or the frozen dependency table,
call this a current-protocol S1 pass, run a full product fit, or accept an oracle-geometry result as recovery.

Joint-diagnostic preflight found an S1 **reporting** defect: `stage_s1` used raw `params.get(name, 0)` for
the starting parameter table, so an omitted `t_ze` appeared as **0 mm** although `prada_family` actually
initializes it to `t_zone` (**5 mm** in both retained cases). `s1_parameter_values` now snapshots the family's
effective values for the start/truth tables and retains raw start parameters separately. No fitting,
geometry, recovery computation, threshold, dependency or historical JSON changes; runtime remains fit-10.
The new independent mesh-reconstruction check has **4 failures / 3 passes** with the old record arithmetic
(`s1_parameter_record_before_v2/`, PID 14760, 12:04:41–12:04:42 +03). With the correction, the seven checks
plus seven cache/provenance checks give **14 passes** (`s1_parameter_record_after/`, PID 47632,
12:05:28–12:05:29, exit 0). The first draft test mutated its own setup; that confounded receipt is retained
as `s1_parameter_record_before/` and is not the defect reproducer.

Prepared `run_s1_joint_counterfactual.py` / `diagnostic_side_camera.py`: a 2x2 fixed-input synthetic
comparison, both saved S1 input sets with fresh current-method and free-side diagnostic fits. Each uses
one bounded pool, original starting family and measured crease endpoints, all five views, original
four-round/evaluation limits, outline retracing and front/hinge constraints. Every side angle is graded
regardless of the old observability flag. The first preflight stopped before fitting on the misleading
historical start table (PID 24344, 12:01:49–12:01:51, exit 1). The driver now verifies legacy metadata
against its raw inputs and reconstructs the actual defaulted family; it never starts from the false zero.
Corrected preflight passes (`s1_joint_preflight_v2/`, PID 48700, **12:06:43–12:06:48 +03**, exit 0).
Both input families and all source hashes validate. Reporting correction committed at **a2e67fc**.
The matrix completed: `s1_joint_counterfactual_run/run.json`, actual PID **42452**, ran
**12:07:44–13:26:40 +03**, exit 0. First cell `s1_fit6_current` started 12:07:51 with **12 workers / 23,736,996 KB** available
commit (Windows GlobalMemoryStatusEx); the shell's free-virtual check before launch was 23,973,836 KB.
Order: original-input current, original-input counterfactual, fresh-input current, fresh-input counterfactual.
Each cell writes its own receipt and measurements; `partial_report.json` appears after the first complete
cell. No timeout. The final receipt confirms completion; the main PID is gone. Do not restart the matrix.

First cell completed **12:22:48 +03**: current method on `s1_fit6`, measured **0.698285873831 mm**,
exact **0.069303861394 mm**, truth R2 true / zero-relief R2 false. Recovery: outline **0.123519334 mm**,
zone **0.042794696 mm**, r_C **0.011102843 mm**, original 02+x angle **0.037680425 degrees**.
All 16 sign cases measured; **2 exceed 0.7**, maximum **0.745211468 mm**. Final front bound clear;
four-round cap reached. The extra side-angle audit gives maximum **0.343955406 degrees** at 05+x;
side angles are not the frozen protocol's fitted theta pair. This fixed-line diagnostic is not a new full
S1 acceptance record and does not replace the historical fit-6 proof. Its current-method baseline is retained
as calculated; no result selection. Second cell, `s1_fit6_free_side_counterfactual`, started **12:22:50**
with a fresh sequential pool of 12 workers after **23,800,452 KB** available commit; parent PID was 42452.

The older-input experimental cell finished **12:44:58 +03**:
measured transfer **0.623282348529 mm**, exact **0.038949400278 mm**; truth R2 passes and zero-relief R2 fails.
Outline error improves to **0.017566817542 mm** and the maximum across all five audited angle pairs is
**0.228094533967 degrees** (original 02+x **0.066886181346 degrees**). Zone-level and r_C errors increase to
**0.090146528450 / 0.056867456815 mm**, still within their existing limits. All 16 sign cases remain measured;
**3 exceed 0.7**, maximum **0.750531925098 mm**, versus 2 / 0.745211467529 in the current-method baseline.
Final front bound clear, four-round cap reached. Thus this is a mixed diagnostic improvement, not a universal
error reduction or proof of convergence. The side-angle protocol remains counterfactual, not integrated.

The mesh audit on both completed cells (`s1_joint_geometry_partial_2/`) retains **2,121/2,121** points each.
Experimental X-error RMS is **0.061507126426 mm**, worse than current **0.040576901405 mm**; mean signed/absolute
error **+0.051935012471 mm**, maximum **0.108787221321 mm**. This outer-depth bias is retained beside the better
outline/transfer results. Receipt PID **7328**, **12:45:41–12:45:42 +03**, exit 0, one process/no pool.
Input/source snapshots are retained automatically.

`full_fit6_current` ran **12:45:00–13:01:35 +03** with 12 workers after
**23,811,080 KB** available commit. It reproduces the newer-input failure: measured **0.760738195495 mm > 0.7**,
exact **0.067896192015 mm**. Truth R2 passes / zero-relief R2 fails. Recovery errors are outline
**0.122116492259 mm**, zone **0.061137082447 mm**, r_C **0.020220579658 mm**, original 02+x
**0.026137111802 degrees**. All 16 sign cases measured; **5 exceed 0.7**, maximum **0.782665889635 mm**.
Extra side-angle maximum **0.303575244634 degrees** is retained separately. Front bound clear; four-round
cap reached, not convergence. Final cell `full_fit6_free_side_counterfactual` ran
**13:01:37–13:26:40 +03**, 12 workers after **21,728,856 KB** available commit. It reports **converged**
after four rounds, final front bound clear, measured **0.686085984544 mm**, exact **0.038064252394 mm**.
Truth R2 passes / zero-relief R2 fails. Recovery: outline **0.007874772163 mm**, zone
**0.065285077025 mm**, r_C **0.059600811853 mm**, original 02+x **0.055865968087 degrees**;
maximum across all five audited angle pairs **0.076532148909 degrees**. All are within the existing
numeric recovery tolerances, but the side-angle protocol is counterfactual. All 16 sign cases measured;
**5 exceed .7**, maximum **0.788291123299 mm** (baseline 5 / .782665889635). No stress case was discarded.

The older-input depth-bias decomposition is read-only (`s1_joint_depth_bias_older/`, PID 40804,
**12:49:51 +03**, exit 0). At the same 2,121 points, experimental mean base-surface error is
**+0.065487974100 mm** and relief-over-base error **-0.013552961628 mm**, giving the retained
**+0.051935012471 mm** mean total. r_C is **3.943132543185 mm**, versus the 4 mm truth and baseline
3.988897156583 mm. Both fits still mark the five zone/depth parameters `penalty_active`; these are not
unconstrained uncertainty estimates. The decomposition is algebraic, not causal attribution to an objective
term, and it authorizes no parameter correction. Saved objective terms and every round's parameters remain
in the report, with input/source hashes and the source snapshot. No refit or gate change occurred.

Readout/visual scripts completed under the same ignored evidence folder:
`summarize_s1_joint_counterfactual.py` audits all four results and image-only candidate selection;
`audit_s1_joint_geometry.py` measures outer-mesh depth at a fixed truth-chart grid (101 stations x 21 height
fractions), preserving missing samples; `visualize_joint_diagnostic.py` renders the fresh failing input set
from four directions under opposite lights, plus rotating-view and moving-light GIFs. The mesh-depth readout
passes four independent plane/triangulation/winding controls at 1e-12 mm and retains an outside point as missing
(12:22:54, exit 0). Full selection/provenance audit passed, PID **13864**, **13:27:32 +03**, exit 0;
`s1_joint_audit_run/`. It retains all five view poses, image-only selections, original bounds/front guards,
sigma flags and penalties at the fitted point. Both older fits have zero penalty at the fitted point;
`penalty_active` can describe nearby stencil samples and does not establish the cause of their depth bias.
Full mesh audit passed, PID **43764**, **13:27:46 +03**, exit 0; `s1_joint_geometry_audit_run/`.
All four cells cover **2,121/2,121** points. Newer-input current/experimental RMS:
**0.048358608425 / 0.042314938226 mm**; mean absolute **0.040589812753 / 0.032854111921 mm**;
maximum **0.095540659390 / 0.082231665598 mm**. Older-input RMS remains **.040577 / .061507 mm**.
Rendering completed, PID **34960**, **13:28:06–13:28:35 +03**, exit 0; `s1_joint_visuals_run/`.
Both four-view light sheets and eight quarter-cycle animation frames were inspected. Actual geometry,
common scale/light/view, no depth exaggeration; the zero-relief control is visibly distinguishable.
Files: `upper_light.png`, `lower_light.png`, `view_orbit.gif`, `light_orbit.gif`, plus provenance and QA frames.
These are synthetic instrument visuals, not a product model or owner acceptance.

An additional independent camera check completed as `probe_normal_current_eligibility.py`.
It applies the unchanged contour-normal proposal after the current fit, freeing only the already-observed
angles permitted by production. It retains 16 front/top/oblique controls (the earlier held-out pose roles,
both perturbation signs) plus four rigid side controls, on fully visible 900 x 1200 images of the analytic
wedge fixture. Side/unobserved angles must remain unchanged; selection uses the common full-scene image
loss and original front guard. Every hidden-vertex error and common-frame angle is retained, including
regressions. No product/S1 geometry or crease score enters the control fits. The script refuses a pool until
the complete matrix report exists. First audit/show the matrix visuals, then use this to test whether the
proposal helps within existing eligibility; it does not establish that the fresh S1 floor can be solved.
Generation preflight: **20/20 images fully visible, true-camera image loss zero**, no optimization,
single process, PID **37088**, **13:06:39–13:06:45 +03**, exit 0. Receipt
`normal_current_eligibility_preflight_run/`, preserved report/source in `normal_current_eligibility_preflight/`.

The full 20-case run completed, PID **36008**, **13:31:12–13:32:55 +03**, exit 0,
`normal_current_eligibility_run/`; 12 workers after **21,445,984 KB** available commit. All 16 non-side
cases improve aggregate projection RMS, mean **1.316866627505 -> 0.120230471188 px**; all four rigid-side
cases improve, mean **0.083423280494 -> 0.019107687817 px**. No individual front/arm RMS increases by
more than 1e-8 px. Four normal alignments terminate on image-loss convergence and 16 on no image descent.
Retain the limitation: three common-frame angles still exceed .3 degrees; the largest is **1.280891980039**
on row 12, yaw 78, far arm, versus 1.649334118379 before. Its unexplained placement is .103610575149 mm.
The original detector marks this arm observed; the improvement does not certify its observability.

`normal_eligibility_holdout/` repeats the **exact same trial implementation** on 32 predeclared prism/valley
cases with different poses, hinges, perspective and perturbations. All 32 generation checks show fully
visible scenes and zero true-camera image loss. All 16 prism and all 16 valley cases improve aggregate
projection: **0.123536998693 -> 0.011031191901 px**, **0.127096950524 -> 0.013684032471 px** respectively.
Maximum common-frame errors **0.016437705125 / 0.009813589786 degrees**. Receipt
`normal_eligibility_holdout_run/`, PID **31240**, **13:36:44–13:41:46 +03**, exit 0; 12 workers after
**21,733,432 KB** free commit. All rows and hashes retained. No normal-step production integration occurred.

The remaining angle issue prompted an independent occlusion test. `hidden_arm_observability/` uses opaque
analytic boxes, both sides, yaws 60/78, plus visible positive controls. In **all four hidden cases**, the far
arm has **zero visible pixels** and all 17 complete-scene angle-grid images are byte-identical, yet
`fit_theta` reports observed and shifts it by **-3.872504404322 / -3.454915028125 degrees**. Its isolated-arm
grid ranges are .039114587963 / .017185148018. All four visible controls respond to angle and recover an
image-identical solution. This proves a detector defect independently of S1; it does not establish its
contribution to the S1 floor. Receipt `hidden_arm_observability_run/`, PID **14632**,
**13:42:37–13:42:53 +03**, exit 0, one process.
The new repository regression reproduces it: **4 failed / 4 passed**, PID **27656**,
**13:45:07–13:45:23 +03**, exit 1, `theta_occlusion_before_run/`. The owner requested pausing after results
were ready before any repair was applied. Runtime remains fit-10; frozen files are unchanged.

The first completed cell also has a read-only mesh audit: **2,121/2,121** fixed truth-chart points covered,
outer X error **0.040576901405 mm RMS**, **0.034576549908 mm** mean absolute, **0.073704802220 mm** maximum.
This is a point-grid diagnostic, not an area integral, Hausdorff distance, uncertainty interval or new gate.
Receipt `s1_joint_geometry_partial_1_run/`, actual PID 25436, **12:28:13 +03**, exit 0 (single process, no pool).
Output `s1_joint_geometry_partial_1/` includes exact snapshots of its input partial report and audit source;
both snapshot hashes match the original audit record, preserving provenance when later cells append results.
The final visualization now requires the full geometry audit and shows its depth errors beside the actual
meshes, separately from transfer errors. Final visualizations and the four-cell audits are now complete above.

The static protocol review confirms why the camera variant remains diagnostic: production fixes 01/05
theta in `_initial_view_fit`, excludes it in `align_view`, and also forces zero in the cross-camera comparison.
`crease_fit` then supplies those values to R2/R3. The frozen dependency table and its test explicitly grade
only 02+x. Expanding the fitted side angles would need an explicit dependency/procedure review, including
all quantities the measurements read; it cannot be silently installed or treated as an existing-protocol pass.
No production rule, dependency file, threshold or observability flag has been changed by this experiment.

## R1 area and fixed-family feasibility audit — 2026-10-09

The preceding **fit-9** repair: the independent virtual-corner control exposed a cap-hit error in fit-8's
direct intersection lifting. Two mirrored exact-geometry/exact-line cases hit the top cap instead of the
virtual corner, producing **4.764397 mm** error. The geometric outward-normal check now runs in the un-opened
temple frame and rejects cap/inner-wall hits; those corners retain line extension. Before repair, the 32
controls plus four opened-temple cases give **6 failures / 30 passes** (`virtual_corner_before/`, PID 36056,
10:20:23–10:20:29 +03). Afterward **78 affected checks pass / 1 existing xfail**, 71.06 s
(`virtual_corner_after/`, PID 41176, 10:21:04–10:22:16, exit 0), including the earlier surface-corner controls,
synthetic trace/fit, cue and cache checks. No frozen threshold changes; earlier fit caches are diagnostic only.

The identical 96-case diagnostic is retained before/after (`virtual_corner_controls/` and
`virtual_corner_controls_fit9/`, actual PIDs 5196 and 43752, exit 0): exact-surface maximum error drops to
**3.642e-6 mm**, with all 32 corners recovered. Supplying a wrong relief surface still causes up to
**1.457203 / 1.247151 mm** error at half/1.5-times true relief. This template dependence remains a limitation,
not a second claimed fix. All cases, original lines and cameras are retained. Fixed-input historical/fresh P2
replay (`p2_corner_lift_fit9/`, PID 36760, 10:21:31–10:21:32, exit 0) is unchanged from fit-8: **fresh A remains
0.535002 mm > 0.5**. STOP-I and the fixed-family R1 limitations below remain unresolved. A labelled coordinate
diagram is saved in `virtual_corner_visual_v2/before_after.png`; this is a synthetic measurement control, not
a changed product shape or a WP6 quality pass. Continue with photo-supported tracing/representation evidence.

**R1 is measuring a real limitation of the current fixed-outline pyramid family.** It is not an area-integration
bug, and no threshold or candidate plane has changed. Independent area sampling on five predeclared meshes
agrees with production to at most **0.000033543**. Both retained S1 pyramid truths are the same spec and score
**0.648234358**; about **0.488733** is exactly on F2, and neighbouring surface inside its 0.3 mm band raises the
share above 0.5. Do not confuse this truth with the F1 calibration fixtures (0.668233 / 0.708403) or old prototypes.
The four fixed Sobol replicates per case are numerical checks, not confidence intervals. The first driver
failed before computation because it compared two different spec-hash serialization protocols; the corrected
driver verifies the proof's raw-spec hash. Both receipts are retained (`r1_surface_area_run/`, exit 1;
`r1_surface_area_v2_run/`, PID 7916, 10:03:04–10:03:07 +03, exit 0).

The analytical audit covers the **whole allowed parameter box**, not a search for a favourable fitted shape.
For fixed outlines/nodes, the zone surface is a base plane plus `r_C * phi_i(d,y)` on each facet. A guaranteed
subset of F2's tolerance band and extrema of each facet's area scale give these conservative lower bounds:

| Fixed family | R1 lower bound over all free parameters |
|---|---|
| Retained S1 truth outline/nodes | 0.5434782359 |
| Historical P2 outline/nodes | 0.5635233215 |
| Historical P2 with fit-8 fixed-line corner mapping | 0.5635226637 |
| Fresh full-proof P2 outline/nodes | 0.5629907642 |
| Fresh P2 with fit-8 fixed-line corner mapping | 0.5629903038 |

All exceed **0.5**; depth/level fitting alone cannot make these fixed families pass R1. Later retracing can
change the outline, so this is not proof that all WP6 representations are impossible. The bound is numerically
evaluated, not a machine-verified interval proof. A separate 60-digit Decimal calculation on an expanded box
agrees within 2e-13. Five known clipping controls and **165 predeclared parameter-boundary/midpoint cases**
verify the area formula within **1.855e-14**; adding region-grown planes changes the initial scores by at most
1.211e-14. No failed/invalid case was discarded and no parameter point is promoted.

Evidence: `line_evidence_20261009/r1_family_bound/` and `r1_family_verification/`, drivers
`audit_r1_family_bound.py` / `verify_r1_family_bound.py`. Actual PIDs 32420 (10:10:21–10:10:22) and 36120
(10:12:36–10:12:48), exit 0. All recorded source hashes were unchanged across those runs, which used **fit-8**
(surface-corner repair at `f0526a5`); no new S1/candidate acceptance run has occurred.
Next, investigate remaining tracing/representation limitations with independent controls and photo evidence;
do not change the shape merely to beat R1, retry arbitrary contour weights, or reopen resolved waiver questions.
Proof/diagnostic reporting now preserves calibration evidence under `resolved_gate_decisions`, leaves the
compatibility `open_owner_decisions` list empty, and removes the obsolete STOP-I waiver invitation. This is
reporting only: no gate arithmetic, evaluator, parser or saved historical JSON changes. **21 affected checks
pass**, 0.19 s (`resolved_gate_reporting/`, PID 41424, 10:14:51 +03, exit 0). The owner reiterated that useful
results should be shown: retain the rotating/multiple-angle and changing-light comparisons, label experimental
versus proof-passing evidence clearly, and obtain their final appearance judgment before completion.

## Coupled camera repair — 2026-10-09

- **Fit-8 surface-corner repair.** An independent tracing defect is repaired: an image-line intersection that
  hits the visible temple is now lifted directly, rather than intersecting straight fits to two potentially
  bent surface paths. Off-surface corners retain the existing chart-line extension; reports name the method.
  Parallel image lines no longer create a spurious finite corner from the curved lifted paths.
  `corner_intersection_lift/` isolates 0.114–0.510 mm error in eight analytic surface-point cases, versus
  at most 4.68e-6 mm for direct ray lifting. Sixteen mirrored/pose/height controls plus off-surface and
  parallel-line cases give **17 failures / 1 pass before repair** (`corner_lift_before/`, PID 33660,
  09:52:47–09:52:50 +03). Afterward **42 pass / 1 existing xfail**, 69.53 s (`corner_lift_after/`, PID 42928,
  09:53:32–09:54:41, exit 0), including synthetic trace/fit, cue and cache regressions. No thresholds changed.
- Fixed-input P2 replay (`p2_corner_lift/`, PID 39000, 09:54:27, exit 0) improves B spread from
  **0.343638 to 0.336089 mm** in the fresh case (historical 0.384845 to 0.376714). A and C lie outside the
  surface and retain extension; **A remains 0.535002 mm > 0.5**, and historical A remains 0.583248.
  Thus the verified tracing fix does not resolve P2 or STOP-I. Cameras, lines, surfaces and all source
  hashes remain unchanged; historical cameras are diagnostic inputs, not fit-8 acceptance evidence.
- Rigid-part camera diagnostics were completed and **not integrated**. `rigid_part_controls/` retains
  all 18 independent articulated-box/wedge cases: nine proposals fail the original front guard, one makes
  no progress, and eight improve near-part projection while worsening whole-image fit.
  `front_anchor_controls/` uses the same visible-contour method on the fixed front: camera-only projection
  improves in 12 cases, worsens in five (up to 6.306 px RMS), and is unchanged in one. Every articulated
  case's whole-image loss worsens. Neither method justifies a product trial or replacing current cameras.
  Receipts: PID 29148, 09:46:12–09:46:20; PID 18916, 09:48:11–09:48:19; both exit 0. Keep all failed cases.
  Further camera work needs an independently justified source of pose information, not additional
  contour-weight searches. Continue with the remaining R1/instrument evidence under unchanged gates.

- The subsequent source-camera diagnosis preserves the new STOP-I failure. `full_camera_diagnosis/`
  (PID 28444, 09:18:43–09:20:52 +03, exit 0) refits the **exact known geometry** using silhouettes only:
  measured transfer **0.730990 mm**, exact **0.039587 mm**. Shape bias is only part of the failure.
  Oracle source-05 camera/theta replacement gives 0.674045 mm; target-02 replacement gives 0.761184 mm.
  These substitutions and all error-sign rows are diagnostic, never replacement gate inputs.
- `camera_boxes/` (PID 25316, 09:23:55–09:24:03, exit 0) rules out downsampling: every relevant view
  uses native pixels. The true 05 camera is 0.286032 native pixels outside the original packed shift_x box
  in both S1 cases. Bounds and synthetic inputs stay unchanged; this alone does not prove that an equivalent
  allowed camera is impossible, because the side fit fixes both hinge angles at zero.
- `side_camera_capacity/` (PID 15512, 09:26:02–09:26:06, exit 0) fits three predeclared groups of oracle
  point correspondences inside that same box, retaining every case. Near-only / front+near / all three parts
  give measured transfers **0.679998 / 0.677673 / 0.712055 mm**, but whole-image losses
  **0.036799 / 0.012942 / 0.010486**, all worse than the known-geometry camera's **0.007991**.
  All optimizations succeed and stay inside the box; none is a production method (hidden points included).
  Passing one selected transfer would not justify changing the objective or discarding far-part evidence.
- `visible_contours/` (PID 3760, 09:30:44–09:30:48, exit 0) attributes those fixed cameras' errors to
  known visible parts. The known-geometry camera mismatches only 11 of 11,827 directed contour matches to
  another part. Its near/far mean contour errors are about **0.414 / 0.209 px**; the front+near oracle gives
  **0.137 / 0.987 px**. This supports a whole-scene fitting compromise, not wholesale part confusion.
  No evidence supports arbitrary image weights or dropping far-part evidence.
- `post_alignment/` tests one final bounded Powell camera step with the existing objective/budget, after
  joint alignment. Of 16 independent analytic cases, image loss improves in 14 (two unchanged); aggregate
  corner projection improves in 11, worsens in three, and is unchanged in two. All front guards pass.
  The identical camera-only test on both retained S1 cases (`post_alignment_s1/`, PID 30612,
  09:37:55–09:38:52, exit 0) worsens measured floors to **0.729676 / 0.800290 mm** and exact-line diagnostics
  to **0.096829 / 0.102529 mm**. All five views and all 16 guide-error sign cases are retained per S1.
  This extra optimizer step is **not integrated**: lower silhouette loss has not established better depth.
  The independent side-articulation test below investigates the representation instead.
- `side_articulation/` retains all 18 independent box/wedge controls: both side cameras, zero articulation,
  and every sign combination of 1 and 3 degrees. Actual fits keep side theta zero. True cameras lie inside
  every original box. Oracle point fits converge to **1.0–1.4e-13 px** RMS for zero articulation, but
  **0.257–2.972 px** otherwise (local bounded optima, not a global minimum proof). PID 36616,
  09:40:35–09:41:24, exit 0. `articulation_projectivity/` independently checks the homogeneous projective
  camera equations: zero-angle whole scenes and each individual rigid part have a camera nullspace;
  every articulated whole scene has rank 12 (minimum/maximum singular ratio **0.000174–0.002321**).
  A single camera therefore cannot exactly reproduce all those articulated corner correspondences.
  This includes hidden corners and is **not** proof that silhouette matching or the current gates are
  impossible. The subsequent rigid-part diagnostics above did not provide a reliable repair.
- A separate initial-refit defect accepted an unmeasured front guard (`inf <= inf`, or a finite result
  against an infinite baseline). `test_temple_refit_guard.py` reproduces **2 failures / 5 passes** before
  repair. Both guard measurements must now be finite, matching joint alignment's existing rule. The seven
  cases plus input/cache controls give **17 passes**, 1.70 s (`refit_guard_after_corrected/`, PID 43668,
  09:33:43–09:33:45, exit 0). An earlier after-run used a nonexistent test path and exited 4 with no tests;
  its receipt remains in `refit_guard_after/`. That repair introduced **fit-7**, invalidating old acceptance
  caches. After the version update, **19 checks pass**, 2.10 s (`refit_guard_version_checks/`, PID 45236,
  09:38:23–09:38:25, exit 0), including trace order and frozen dependency assertions. The numerical method
  for finite guards is unchanged; existing S1 failures are not explained away.

- **Fresh full proof stopped at STOP-I:** `line_evidence_20261009/full_fit6_run/run.json`, actual PID **16348**,
  **08:53:09–09:13:05 +03** on 2026-10-09 at `90c6a8d`; exit 0 means the experiment completed, not quality passed.
  Twelve workers, BLAS 1, EcoQoS opt-out. The explicit driver
  `run_full_fit6.py` records 140 input hashes, uses `wp3b_glossy/cameras.json` and a new `full_fit6/` output.
  P0 (12 fixtures/16 refusals), P1 including prism share, delivered rear family and delivered crease-segment
  control pass. P2 fails only corner A: **0.535002 mm > 0.5** (old 0.583248); B 0.343638, C 0.849659 mm,
  in-view outline 0.143755 and held-out outline 0.234398 mm pass. All real line endpoint moves are unchanged.
  The regenerated S1 fails only its measured floor: **0.763662908288 > 0.7 mm**, exact-line diagnostic
  **0.069957948109 mm**. Outline 0.124900 mm, zone 0.055750 mm, r_C 0.021755 mm and theta 0.018422 deg all pass;
  truth R2 passes and slab R2 fails. Four-round cap reached, final front bound clear. S1 took **977.52 s**.
  All 140 recorded inputs are unchanged. No candidate/slab fit ran. Original `s1_fit6/` remains immutable.
  The log's historical OPEN OWNER DECISION text is obsolete: KEEP STOP-I is resolved; do not ask for a waiver.
- The completed read-only replay (`full_fit6_replay/`, receipt `full_fit6_replay_run/`, PID 50864,
  09:15:11–09:15:18 +03, exit 0) reproduces both scores. At true cameras/theta, measured transfer is
  **0.671495054504 mm**, exact **0.025290262104**. At fitted cameras, measured source/exact target gives
  **0.051522530850**, exact source/measured target **0.782335422952**. All 16 target-error sign cases remain:
  6 exceed 0.7 at fitted cameras (maximum 0.784928), 4 at true cameras (maximum 0.775548). No sign is selected.
- A separate orchestration control (`p2_acceptance_probe/`) proves that `run_rest` can return PASS when P2
  fails or has no verdict. After the hashed proof exited, final acceptance was repaired to return STOP-P
  when P2 has not passed. Diagnostic fits still run; existing earlier failures retain their evidence.
  The new `test_temple_proof_completion.py` reproduces **3 failures / 3 passes** before the runtime repair
  (`p2_completion_before/`, 0.46 s). Afterward **13 completion/cache tests pass**, 0.14 s, exit 0,
  `p2_completion_after/`. This does not change P2's measurements or limits.
- P2 cause isolation: 12 independent analytic-prism camera/height cases (`corner_projection/report.json`)
  show the line-lifting arithmetic agrees with closed-form ray/plane intersections within **1.66e-6 mm**.
  A wrong lifting surface alone can cause **1.91723 mm** cross-view disagreement with exact lines/cameras.
  On fixed real lines (`photo_corners/report.json`), new cameras give A spread **0.484282 mm** on the delivered
  surface versus **0.535002** on the traced starting surface; zero relief of that start gives **0.521637**.
  These are diagnostic substitutions, not selected replacement gate inputs. Test camera/surface coupling
  independently before changing the tracing method. The recorded P2 failure remains binding.
- The fixed-line delivered comparator audit (`delivered_transfer_fit6/report.json`) reproduces historical
  transfer **0.693721950205 mm** and measures **0.556717676040 mm** at the fresh delivered fits. Thus its
  unchanged R3 margin now demands candidate transfer **<= 0.056717676040 mm**. Preserve the old comparator,
  the new one and the fitted-slab clause; the improved baseline is not evidence that a candidate passes.
- Owner requested more angles and lighting to see depth. `s1_depth_views/` contains upper/lower-light sheets
  and `view_orbit.gif`, rendered from the original same-input S1 truth, recovered mesh and zero-relief truth
  control. Actual geometry, no exaggeration, identical camera/material/light within each comparison; front
  60 mm close-ups. These are synthetic tests, not a real-photo candidate or the fitted slab comparator.
  Input hashes unchanged; `depth_views_run/`, PID 30680, 09:14:36–09:14:52 +03, exit 0. Both sheets inspected.

- Before the next full proof, review found that P1 reported analytic prism plane-area share without grading
  its accuracy. P1 now includes the existing independent dense-surface reference (0.01 mm grid, unchanged
  0.003 numerical tolerance) for every candidate plane and the maximum. This does not alter R1 or its 0.5
  threshold. Valid, corrupt, missing and nonfinite share controls pass: **24 focused tests**, 9.93 s, exit 0,
  `p1_guard_tests/run.json`, actual PID 27372, 08:48:59–08:49:09 +03. The proof CLI now accepts explicit
  `--cameras`; its Blender helper no longer imposes a timeout on healthy long checks. New proof evidence must
  use a fresh folder; retain the original passing same-input S1 separately.

- The old one-pass camera/hinge fit leaves coupled alignment error even with exact geometry. A joint contour
  proposal now optimizes camera plus observable hinges together, selecting steps only by the existing silhouette
  loss. It cannot read crease lines, R3, true cameras or true hinges. The original camera box and 0.1 mm front
  guard remain binding; side/unobserved hinges stay fixed and rear-family alpha refits keep their prior method.
- Independent analytic box/wedge controls cover 16 yaw/sign cases, without Prada assets. All improve aggregate
  projected-corner error and image loss within bounds. **Five of 48 component errors worsen**; the hidden arm can
  remain 6.59176 px wrong. Every component is retained. This supports improved alignment, not perfect recovery.
- A separate **16-case held-out pose set**, including front/top and different pitch, perspective and hinges,
  also improves every aggregate while respecting bounds and the measured front guard. Two of 48 component RMS
  errors worsen; a nearly hidden arm still has **24.431427 px** maximum error. All terminate by image descent
  or relative loss convergence, never the iteration cap. Report: `camera_holdout/report.json`; 12 workers,
  62.18 s, exit 0. Do not treat silhouette alignment as complete pose recovery.
- Six-round proposals were still descending (saved-shape noisy/exact floor 0.715770 / 0.087870 mm). Iterating to
  image convergence, using the existing Powell relative loss tolerance and a reported computational cap, gives
  **0.660145 / 0.036889 mm**. On exact geometry it gives **0.666753 / 0.040730 mm**. Neither uses R3 for selection.
  All four camera tasks stop on no further image descent, within bounds and the front guard.
- All 16 target-error sign cases remain recorded. **Two still exceed 0.7 mm** after alignment (saved-shape maximum
  0.723932 mm; known-geometry maximum 0.715255 mm). These diagnostics neither replace nor add acceptance gates.
- Evidence: `data/wp6/line_evidence_20261009/{independent_camera,joint_camera_probe_converged}/report.json`.
  Runtime `relief_registration.py` integrates the repair as **fit-6**, preserving initial and final fit evidence;
  its independent controls and affected regression passed. Fresh S1 must preserve
  every saved guide extent, signed error, synthetic camera, contour perturbation, and frozen dependency.
- Runtime controls initially gave **17 passes and one test-harness failure**: the outside-box test attempted
  to mutate the immutable Camera dataclass. It now constructs a replacement camera. Review also made the new
  alignment step reject an unmeasurable front guard instead of accepting inf <= inf; its refusal test and the
  corrected boundary test passed in `registration_regression/`: **81 passed, 2 retained expected failures**
  (R1 calibration and F1-rise identifiability), no ordinary skips, **602.22 s**, exit 0. Serial/pool identity and
  rear-family checks pass. The 16 independent camera controls passed in the initial run.
- Proof-cache review found that `fits_delivered.pkl` was reused without a fitter version. A full proof now requires
  its version/content/camera-basis/source-scene provenance; stale or legacy caches are refused, not overwritten.
  This does not prohibit using historical fits explicitly as fixed synthetic-photo inputs in a diagnostic.
  **Eight cache/dependency controls passed**, exit 0, in `proof_cache_tests/`. The S1 input preflight also passed.
  All changes were reviewed against the preserved gates before the fresh S1.
- **Fresh S1 passed:** `s1_fit6_run/run.json`, actual PID **17824**, **08:25:38–08:41:05 +03** on 2026-10-09,
  12 workers, BLAS 1, EcoQoS opt-out, exit 0. Stage time **921.48 s**. `s1_fit6/proof.json` records outline
  **0.126623 / 0.2 mm**, zone level **0.044735 / 0.5 mm**, relief depth **0.022681 / 0.5 mm**, and common-frame
  hinge placement **0.000297 / 0.3 deg** (error / unchanged limit). Truth R2 passes; slab R2 fails.
  Measured true-model R3 is **0.691874467104 mm**; exact-line diagnostic **0.062110338854 mm**.
- **Limits retained:** the four-round cap was reached; this is not a convergence claim. The measured-floor margin
  is only **0.008126 mm**. F1 rise remains inapplicable to the pyramid; fixed r_B is still not fitted, as before.
  All 16 target-error sign cases remain measurable; two exceed 0.7 mm, maximum **0.738453 mm**. These remain
  diagnostic stress cases, not replacement acceptance gates. No error sign was selected for fitting.
- Final-camera audit: all five alignments are kept inside their original boxes with measured front guards;
  all stop on no image descent, without the 50-round cap. 05 is near its original shift_x bound. The final
  front bound is clear. Sigma status remains `penalty_active` for x_in0, x_in_ze, t_zone, t_ze and r_C; do not
  relabel these as unconstrained uncertainty estimates. Full audit: `s1_fit6_summary/fit_audit.json`.
- The comparison audit (`s1_fit6_summary/summary.json`) verifies **all eight line endpoints are numerically
  identical** to the saved S1 and every signed error is unchanged. `s1_fit6_summary/comparison.html` presents
  the numerical comparison; synthetic silhouette diagnostics are under `s1_fit6_visuals/`. Remaining real-photo
  R3 comparator clauses and R1 are unpassed. The S1 result does not promote a model or enable public integration.

## Independent line evidence — 2026-10-09

- Started at `22b2a2f`; the owner's uncommitted session-prompt edit is preserved. No paid calls, push, product fit,
  geometry changes or public integration. Evidence: `data/wp6/line_evidence_20261009/`.
- **288 predeclared analytic controls** cover clean/noisy steps, equal/stronger parallel edges, highlights,
  clipping, lettering, occlusion and absent evidence. Three orientations, two blur widths, all four signs of
  6 px endpoint displacement, and four fixed noise seeds are retained. The original tracer returned **30 wrong
  lines (>1 px endpoint error) with RMS <0.25 px**. Maximum endpoint error over the matrix was **24.163897 px**.
  Low line scatter is not evidence that the chosen ridge is the intended edge.
- **Repair:** `refine_line(..., valid_mask=...)` excludes the full blur/derivative/interpolation footprint and
  refuses insufficient valid evidence. Its default path and production `trace_creases` remain unchanged;
  automatic mask integration is not justified by the photo audit. All **75 mask controls** pass, including
  invariance to arbitrary excluded-pixel values. The affected regression is **152 passed, 1 existing xfail**
  (F1-rise identifiability), no skips, **75.29 s**; `affected_regression/run.json` records exit 0.
- **Held-out validation:** another 288 cases use different orientations, blur widths and noise seeds. All 72
  masked clipping/lettering/occlusion cases retain a line with maximum endpoint error **0.250000 px**; unmasked
  parallel edges and highlights still fail. This is synthetic calibration, not real-photo uncertainty or a
  reason to reduce the retained S1 injection. Reports keep every missing and wrong result.
- **Photo audit:** all eight legacy lines reproduce the saved P2 endpoints. Masks leave seven unchanged, shorten
  and slightly change 02 AB (57 to 43 profiles), and leave 02 BC identical (41 profiles); 02 AC remains missing. The mask uses the
  existing matte/silhouette, highlight and decal constants. `photo_audit_v2/report.json` verifies 11 unchanged
  input files; its sheets are diagnostic only. An initial visualization attempt lacked matplotlib; it exited 1
  and was replaced by a Pillow diagnostic sheet, with the failed receipt retained.
- **Camera attribution:** `camera_components/report.json` keeps all saved lines fixed. Substituting only the true
  02 yaw changes measured transfer **0.910923 -> 0.559053 mm** while exact-line transfer worsens
  **0.274915 -> 0.379588 mm**. Thus optimizing the noisy score can reward error cancellation. No oracle case is
  a gate replacement. Next, assess the camera solver on known geometry and its own silhouette objective.
- **Known-geometry calibration:** four camera-only tasks (05/02, normal hinge fitting vs supplied true hinges)
  completed in **49.13 s**, four workers. Exact geometry with normal fitting still transfers **0.863395 mm**
  (exact lines **0.234553**); supplied true hinges give **0.627842 mm** (exact **0.192404**). Full oracle cameras
  and hinges reproduce **0.606088 / 0.023917 mm**. The shape is never optimized. Saved inputs stay unchanged.
  Next hypothesis: the one-pass camera/hinge fit leaves coupled error. Investigate a bounded coupled image
  alignment step using the existing silhouette objective, never selecting by the noisy R3 score.

## Offline repair checkpoint — 2026-10-08

- **Diagnosis:** `data/wp6/s1_floor_resume_20261008/final_replay/replay.json` reproduces the measured truth
  **0.910922590046477 mm** and exact-line **0.2749146056992064 mm** without fitting. At the saved cameras,
  exact 02 lines with measured 05 lines give **0.256265 mm**; measured 02 with exact 05 gives **0.929841 mm**.
  All 16 signed 02 endpoint-error combinations are retained: **6/16** exceed 0.7 mm at fitted cameras
  (max **0.945888 mm**); even true cameras/theta leave **2/16** over 0.7 (max **0.735411 mm**).
  Oracle cameras and the sign sweep are diagnostics, not replacement evidence or additional acceptance gates.
- **Proven defect and repair:** an independent planar fixture with closed-form 10 px/mm projection showed that
  losing one of two paired creases produced an almost-zero R3 mean and fit loss. `transfer_all` now retains all
  paired photo creases, rejects incomplete/nonfinite measurements, and makes their fit objective infeasible.
  Sigma cannot treat that refusal as measured curvature. S1/GATES reports retain per-crease completeness;
  a missing measured floor cannot fall back to ideal-line evidence. That repair introduced **fit-5**, so old fit-4
  proof caches cannot be resumed as current proof. Valid measurements retain their formula and equal weighting.
- **Replay entry point:** `tests/proofs/wp6_s1_replay.py` requires explicit proof/camera/new output paths, checks
  source/camera/rules/dependency and rebuilt-truth hashes, and writes no historical inputs. It is a diagnostic,
  not a proof or a waiver. `rebuild_s1` now accepts an explicit camera path.
- **Validation:** 12 focused integrity controls passed after the fixes. The affected regression in
  `data/wp6/s1_floor_resume_20261008/regression/` first recorded 85 passes, 2 existing expected failures and one
  stale fit-4 version assertion. Updating that assertion for fit-5 and rerunning it with the new controls gave
  **18 passed** (`regression_followup/`). The 24 independent line-calibration cases below also passed:
  **110 distinct affected tests passed, 2 retained expected failures**, no skips. The long run took 340.26 s;
  its three-worker determinism checks passed. No unrelated WP5/AR suite was rerun.
- **Independent line calibration:** 24 analytic blurred-step controls cover three orientations, two blur widths
  and every sign pair of 6 px guide displacement. Actual residual error is **0–0.084030 px** while the existing
  click-to-snap measure reports **4.169228–6.060273 px**. This proves these are different quantities, not the
  real photos' uncertainty. Real highlights, decals and parallel edges remain uncalibrated; the conservative
  S1 error injection is unchanged. See `tests/test_temple_line_error_calibration.py` and `calibration.xml`.
- **Comparators preserved:** `comparators.json` replays the historical handtrace **1.010717 mm**, fitted slab
  **0.864184 mm** and delivered **0.693722 mm** exactly. All retain AB and BC, both 25/25 hits. The real-photo
  R3 binding limit remains **0.193722 mm**. Source/evidence hashes match; no comparator was fitted again.
- **Decision:** retain STOP-I, R1, all R3 clauses, every view and both real-photo comparators. The valid transfer
  arithmetic agrees with analytic controls; no numerical ray-transfer defect was found. A fresh product fit
  is not justified while the original measured S1 still fails. Both camera and line errors contribute; next
  work must establish independently
  supported line evidence beyond the clean analytic cases (parallel edges, highlights, decal contamination,
  occlusion and image noise), distinguish guide correction from residual measurement error, and review
  any proposed method against the full retained error cases before rerunning S1. Do not reduce the injected
  errors, select signs, widen thresholds, or substitute oracle cameras to obtain a pass.

## Latest committed checkpoints

- `640164f`: bounded joint camera/hinge alignment, independent controls and proof-cache provenance guards (fit-6).
- `e983b29`: independent contaminated-line calibration, optional validity masks, photo audit and camera attribution.
- `22b2a2f`: incomplete-transfer repair, explicit S1 replay, clean-step calibration and unchanged comparators.
- `4304c1d`: exact owner-selected observed-slope + Poisson default and WP5 closeout.
- `d5e5917`: code review/cleanup for WP6: accurate CLI failure receipts, explicit-camera refusal, cache ignore
  correction, current module map and resolved R1 diagnostic wording.
- `ad5105e`: documentation-only resume handoff after `d5e5917`; active guidance now targets WP6;
  superseded progress/resume text is archived, the obsolete WP5 session prompt is removed, and WP2's stale WP5
  prerequisite wording is corrected. Runtime, thresholds and local evidence are unchanged.

## Validation already completed

WP5 default: **54 focused tests passed**, followed by a production bake reproducing the approved PNG exactly
in **243.53 s**. The viewed TOOLTEST copy already passed all 15 Blender/export checks. See
[WP5_HANDOFF.md](WP5_HANDOFF.md) for the closed record and honest proof limitations. Do not redo G1 or reopen WP5.

WP6 readiness at `d5e5917`: **227 Python tests passed, 2 existing strict xfails**, no skips (541.85 s).
AR type-check passed, **500/500 unit tests** and **106/106 QA tests** passed. R1 wording was separately rechecked:
one expected failure, exit 0. The xfails preserve R1 calibration and F1-rise identifiability failures; they are
not quality passes. Static review covered 294 Python files / 108 runtime modules. Runtime/frozen-input and
approved-asset hash audits passed. Logs: `data/readiness_20261008/`; PID 3344 finished 19:46:42 +03, exit 0.
Do not rerun the completed suites merely to resume; rerun affected checks after an implementation change.

## Handoff verification and history

**48 handoff checks passed.** `data/handoff_20261008/audit.json` records documentation links, snapshot fidelity,
frozen/evidence hashes, saved S1 values, runtime unchanged and current instruction routing. No algorithm test
or paid run was needed for this documentation-only handoff. Review the commit and working-tree status before
beginning work.

The long mixed progress log and superseded prompts are preserved in [the archive](archive/20261008/README.md).
Historical instructions are not active; consult specific evidence there only when needed. Never apply an old
"RESUME HERE", start a retired workflow, or revert to an earlier texture by following an archived instruction.
