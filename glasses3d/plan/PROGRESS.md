# glasses3d progress

## Active resume — WP6 (2026-10-09)

**PAUSED at the owner's request, 13:46 +03.** The owner edited the goal to pause once results were ready
to view. The completed synthetic comparisons were shown; `s1_joint_visuals/view_orbit.gif` and
`light_orbit.gif` are ready for review. All launched checks have terminal receipts. Do not resume work
until the owner asks. WP6 is not complete; WP5 remains owner-closed.

Owner-requested extra depth views (14:25 +03) are in
`data/wp6/line_evidence_20261009/s1_joint_edge_views/{top_edge,hinge_end}.png`.
Both inspected orthographic images show the unchanged saved `full_fit6` experimental mesh beside a
copy of its own base with relief removed, at identical scale and without depth exaggeration. This
explanatory control differs from the earlier truth-based control. Source hashes verified unchanged;
receipt `s1_joint_edge_views_run/`, PID 47216, 14:25:30–14:25:33, exit 0. Visualization only; no fitting,
runtime repair, acceptance change or goal resumption.

The next repair has an independent reproducer, but **no runtime fix has been applied**. Two complete,
uncommitted test files remain in the worktree: `tests/fixtures/occlusion_evidence.py` and
`tests/test_temple_theta_occlusion.py`. They deliberately expose the current hidden-arm defect; do not
mistake their four failures for a failed attempted repair. Preserve the owner's separate
`WP6_SESSION_PROMPT.md` edit. On resume, repair the isolated-arm angle objective to account for the
other scene components, with unchanged angle range/observability threshold, and validate independently.
Keep the contour-normal method separate until that repair and its effects have been checked.

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

Latest runtime is **fit-10**: `relief_cue` now compares corresponding valid offsets/positions on both sides
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
