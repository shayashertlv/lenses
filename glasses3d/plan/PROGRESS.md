# glasses3d progress log

Format (PLAN.md §4): date, WP, what was done, commit hash, $0-proof numbers, open issues, next step.
A new session reads this right after PLAN.md §0 and resumes from the first unfinished step.

## Active resume (Codex, 2026-10-08)

**Repair session in progress, starting from clean `8007c18`.** Read the current rule/design/handoff stack and
saved G1 quality evidence. No paid calls, threshold changes, waiver, or WP6 work. Historical accepted assets remain
immutable. Local evidence directory: `data/wp5/repairs_20261008/`; `saved_failure_map.json` preserves the complete
A4/filler/A5/B3 failure map and baseline report hash. Baseline replay/case capture exited 0 and reproduced the
accepted PNG SHA-256 exactly. `cache_run.json` records PID/timing/exit; `cases/` preserves all 160 independently
drawn original A4 holes (four parts, five seeds, eight holes), with observations and truth kept separate in use.

**No active run. Current runtime checkpoint: `019d321`.** The complete frozen quality proof finished at 08:11:28,
actual exit 0, source unchanged: **A4 fails all four parts, A5 fails both offsets, B3 fails six of the same seven
eligible rows**. All six held-out views, five seeds per filler and A7 serial comparison ran without skips.
`quality_019d321/proof_summary.json` indexes the complete evidence; no original B3 eligible row was dropped.
Next: implement and independently test a researched donor-only multiscale/topology-aware fill method when the
pending owner research arrives (`data/wp5/repairs_20261008/research_request.txt`). Tested fill families still fail;
the separate gradient join cannot coordinate copied texture structure across charts. This is missing algorithmic
input, not a waiver, a claim of impossibility, or an automatic context handoff.
The current fresh AR evidence is diagnostic, not a final appearance acceptance.
Full WP5 regression at **`019d321`** finished at 07:35:20:
**340 passed, 1 expected xfail, no failures/errors/skips**, 1,252.40 s, actual exit 0. Runtime/tests were unchanged
throughout (`regression_019d321_sources.json`); `regression_019d321_run.json` records PID 31676 and timing.
The xfail remains the documented A0 periodic wrong-match case, not a quality pass.
B3 at `019d321` still has six of seven original eligible rows failing. Both seam-gradient variants finished and
failed offset 8. `seam_current_chord.py` /
`seam_current_folded.py` are a controlled pre-reflection comparison on the older within-node candidate cache;
they must not be labelled as the new gain bake. The complete quality proof, exact-PNG Blender tool test and fresh
AR/photo-angle comparisons are now complete. None is a final acceptance claim.
The earlier paired-shading and exemplar comparisons are unaccepted; do not run their prepared full candidate bake.
The completed candidate's A3/provenance pass, but A4/A5 still fail (details below). The prepared
`b3_gain_topology_*` and `b3_gain_boundary_*` names describe prior versions; do not run them on this source.
Consult the run record before
another heavy job. The front-reference experiment exited 0 at 05:30:06; both original
eligible held-out-05 brow rows still fail. Seam-support fixes are committed as `ccf88df`.
The full regression and repaired-code reuse check pass; no final quality acceptance or final AR verdict exists yet.

**Complete proof and appearance, 08:11–08:25:** `quality_019d321_run.json` records PID 31236, start 07:45:29,
finish 08:11:28, exit 0. A1, all six A2 checks, A3 and the single-source audit pass; A7 matches all six files exactly.
A3 protects **738,664 texels** (755 below the immutable baseline); readback within one level is 0.999181,
p99 dE00 0.836842. Selected radii remain quilt 0.9 mm and PatchMatch 0.3 mm for each part. Default A4 seed-0
fine errors (BL/BR/TL/TR) are **0.440921 / 0.421339 / 0.430153 / 0.237586**, seams
**2.269105 / 1.525471 / 1.561730 / 1.393089**; all four fail. Five-seed quilt pooled fine errors are
**0.461501 / 0.226873 / 0.276677 / 0.301186**. A5 ratios are **1.244709 / 1.269608** at frozen offsets 7/8;
reflection stays six texels. B3 retains only the view-01 right-brow pass; views 02/04/06 have no eligible row, not
passes. The full frozen rows and both fillers' seed-0/pooled verdicts remain in `quality_019d321/bake/bake.json`.
B2 remains diagnostic **0/11** and pixel-verified area **0%**; historical A0/B1 limitations are unchanged.

The exact PNG SHA-256 is `c00204d54d3a529c33417658e34e411d20d18c4ccf6dc01548671c98e830c7cb`.
`tooltest_019d321_run.json` exited 0 at 08:13:18: **15/15 Blender-copy checks**, source scene unchanged.
Fresh all-pose/worn and black-probe capture exited 0 at 08:15:29; runtime and independent measurements match
exactly. Geometry, materials, camera and fitting match B/accepted controls. Photo-angle capture exited 0 at
08:19:44; all historical/current capture hashes and frozen camera identities were verified. Inspected
`quality_019d321/ar_comparison.png` and `photo_focus.png`: current resembles the accepted revision; brow-top
blotch/streak structure and temple pattern differences from the real photos persist. No final owner verdict
was requested for this failed-quality candidate.

`appearance_summary.json` records every role/form and applicable group without changing eligibility. Current print
L* p50 residuals are **-5.22/-5.39** at calibration poses, **+0.28/-0.67** at trade-off poses, **+2.03/-2.31**
at held-out poses. All role-wise brow lightness verdicts fail. Held-out contrast ratios **0.650/0.824** miss the
existing 0.85–1.18 window; chroma criteria fail at every pair. Front hue residual **-5.25 degrees** misses the
5-degree criterion. Band-energy ratios at held-out +45 are brow **0.330**, temple **0.730**. Existing AR streak
indices are **1.942** (-x at +45), **7.520/11.501** (+x at -30/-45), versus accepted **1.929/7.752/11.429**;
these diagnostic indices supply no new pass threshold. All four immutable source/B/accepted hashes remain exact.

At 08:29:30, `seam_texture_phase_control.py` exited 0. This independent synthetic control constructs one smooth
strip pattern spanning two charts, then puts a phase-shifted copied pattern in an unprotected band. The existing
gradient join sees only that guide and protected observations. It preserves the coherent control (only 0.338 RGB
levels of rounding error) but reduces incorrect-fill RMSE only **9.789507 -> 9.468284**. All protected pixels remain
exact; no clipping, solver residuals below 1e-7. Inspected `seam_texture_phase_control.png`: the join smooths the
edge but leaves misplaced structure. This diagnoses a limitation of fixed-guide-gradient stitching, not an A5
verdict or a proof that WP5 is impossible. Coordinate texture choices across charts in the next method; do not
keep tuning a colour-offset solve against frozen proof offsets. No runtime change or new acceptance.

**Current repair evidence:** coordinate fix committed as `bd3b3eb`. A synthetic test exposed a one-texel error in the quilting
canvas adapter: the crop's extra border was counted as strip position. Fixed by preserving each source crop origin;
the independent test failed at two nonzero chart origins before the fix and all three pass now. A fourth test
covers repacking multiple strip/cap charts. Final focused regression: **24 passed**, 4.23 s, exit 0 at 04:41:55
(`coordinate_tests_run.json`, `.log`, `.err`). Earlier experimental-join plus step-4 checks were **26 passed**,
4.63 s. No instrument or threshold changed. These are implementation checks, not WP5 quality passes.

The failed opt-in `quilt-cut` experiment combined a minimum-error overlap cut and a screened Poisson boundary join.
It has been withdrawn from the runtime API; its full source/tests/diff and hashes are preserved in local
`cut_experiment_v2/`. Default production remains `quilt`. All five-seed experiments remain failed evidence. The fresh
candidate's own training holes selected 1.2 mm for all parts; A4 on its changed domain passes only Brow_Left,
while A5 still fails.
Its photo support decreased from 739,419 to 731,385 texels, so that isolated improvement cannot establish a repair.
On **all original fixed holes**, seed-0 fine-band errors are **0.187619 / 0.270019 / 0.261558 / 0.218841**
(BL/BR/TL/TR), each above 0.15; pooled five-seed errors are **0.211436 / 0.348960 / 0.253853 / 0.200700**.
All four median boundary ratios now pass, but A4 does not. See `candidate_bake.json`, `fixed_candidate.json` and
their exit-0 run records. Earlier `cut_probe.json`, `boundary_probe.json` and `structure_probe.json` remain local
failed controls; `fill_v1.py` / `quilt_merge_v1.py` preserve the earlier experiment before its donor-mask cost fix.

A5 localization (`seam_diagnosis.json`) identifies the +x temple strip wrap as a major contributor at both frozen
offsets. A pre-reflection intrinsic seam-field probe reduced ratios to **1.076069 / 1.174303**, still failing;
protected pixels were unchanged. A curvature-continuation probe exited 0 at 04:51:28 after 14m18s but also failed
A5 (**1.083764 / 1.120412**). Its maximum correction was 238.87 levels; the inspected strip sheet shows coloured
edge overshoots. **Rejected**, not adopted (`seam_curvature_probe.json`, `seam_curvature_crops.png`).

`pm_boundary_probe.json` and `coherent_probe.json` (all 160 fixed cases, both exit 0) also fail A4 overall.
PatchMatch plus boundary correction passes seed-0 energy only on Brow_Left; whole-hole donor retrieval passes it
on Brow_Left and Temple_Right. No method switch is accepted. A donor-only self-validation experiment hid separate
same-shaped holes in observed donors and selected a filler by prediction error there, without proof truth or proof
metrics. Ten compact/long-thin poison/invariance checks passed before running all 160 fixed cases; the experiment
exited 0 at 04:57:57. It **still fails A4**: seed-0 fine energy **0.113631 / 0.247702 / 0.187410 / 0.115291**,
pooled **0.161779 / 0.175340 / 0.223301 / 0.129627** (BL/BR/TL/TR). Base radii kept their independent selections.
`adaptive_synthetic.json` and `adaptive_probe.json` retain results; no adaptive runtime API was added.

B3 view-01 decomposition exited 0 at 04:31:39 (`b3_01_decomposition.json`, corresponding run/log files).
The frozen full-region results remain three failures and one pass. On the left brow's **projected-only** subset,
fine energy is 0.509587 gained / 0.517220 without gains, spectrum error 0.679246 / 0.666603. Thus fill/gain repair
alone does not explain that failure. The right brow still passes overall. Temple_Right's projected-only colour
error improves from 7.160260 to 4.898864 without gains but remains substantial. Subsets are diagnosis, never new
eligibility or replacement verdicts. Source/held-out crop sheet: `b3_01_crops.png`. A direct-anchor rendering diagnostic bypassed the atlas and confirmed the left brow's
projected-only detail deficit persists: fine energy **0.550280 direct / 0.517220 atlas**, spectrum error
**0.592360 / 0.666603**. The independent ray interpolation's self-control has mean absolute error 0.000126,
max 0.004811 (`b3_direct.json`; successful `b3_direct_v2_run.json`; the first run's decal-face index failure is
preserved). The +x temple has only view 06 as an eligible anchor with view 01 held out (`anchor_diagnosis.json`).
Views 03 and 05 were then decomposed with the repaired seam geometry (exit 0 at 05:12:13 / 05:18:27). Their
original three eligible rows still fail; source hashes and complete frozen rows are retained in the reports.
View 03 left brow: spectrum 0.792465, correlation-length ratio 0.416667; both projection and fill contribute.
View 05 right brow: removing gains improves whole-region spectrum **0.333445 -> 0.239447**, colour
**4.091084 -> 2.220937**, mid/fine energy **0.731144 / 0.754911 -> 0.885411 / 0.912887**. This is a diagnostic
counterfactual, not a rerun production pass. Without the explicit front photo, the current gain reference picks
rear view 06 by texel coverage. `front_reference.py` is an experimental geometric fallback using only used
cameras' alignment with the documented +Z front; six synthetic checks passed, including held-out exclusion and
preserving explicit-front selection. Its full held-out-05 bake exited 0 at 05:30:06 and still fails both original
eligible rows: left/right spectrum **0.398069 / 0.388148**, correlation ratios **0.636364 / 0.8**; colour
**3.886817 / 2.236559**. Gains become almost identity, but the right brow's fill-only fine energy remains
**0.240437**. `b3_05_front_reference_decomposition.json` preserves full and diagnostic rows without overwriting
the original experiment. No runtime policy changed.

**Seam repairs in this checkpoint:** three independent synthetic failures proved that
`chart_seam_pairs` dropped an entire valid edge when its exact UV fell into the gutter, and could select a texel
from an unrelated chart. Nearest covered texels in a bounded 3 x 3 stencil now require the declared chart.
A fourth independent failure showed reflection darkened a constant-colour rotated surface by interpolating black
gutter taps. Bilinear reflection now renormalizes only source-chart taps. The reflection band remains six texels;
quality sampling and thresholds are unchanged. Final focused regression: **27 passed**, 5.69 s, exit 0
(`seam_repairs_final_tests_run.json`). Original red tests are retained in `seam_neighbours_before*` and
`reflection_before*`. Fresh production-density bake exited 0 at 05:06:30, original B calibration and independent
radius selection (quilt 0.9 mm, PatchMatch 0.3 mm for all parts). Seam pairs increased **7,553 -> 26,466**;
A5 improved **1.288293 / 1.300100 -> 1.184261 / 1.200921**, still failed. **A3 and single-source audit pass**;
739,276 protected texels (143 fewer than the immutable baseline; not hidden). All four A4 rows still fail.
See `seam_candidate.json`, `.log`, `.err`, `_run.json`, and `seam_candidate_cache.pkl`. These fixes do not pass WP5.

The same intrinsic gradient probe on the repaired graph improved to **0.899908 / 1.043808**, still failed at 8.
A multiplicative log-radiance variant avoided all clipping (six synthetic checks passed), but also failed at 8:
**0.919714 / 1.058512**. Both are unadopted pre-reflection diagnostics, with all protected texels unchanged
(`seam_neighbour_field.json`, `seam_log_field.json`). No screen length or proof offset was changed.

Standard unscreened Poisson stitching was then tested because the prior screened equation forces each chart
back toward its mismatched guide over a borrowed patch radius. Five independent constant/offset/gauge checks
passed. The diagnostic exited 0 at 05:41:47 (`seam_poisson_v2_run.json`); **A5 still fails** at 8:
**0.887166 / 1.038095**, no clipping or protected changes, linear residuals below 1e-7, 363 anchored components.
No quality samples enter the solver. Full production ordering and appearance remain unverified and this method
is not adopted. The first synthetic invocation's sparse-boolean dtype error is preserved with its source/log;
it was corrected before the successful run. See `seam_poisson_field.json` / `seam_poisson_synthetic.json`.
Its localized review exited 0 at 05:46:59 (`poisson_review_run.json`, `seam_poisson_diagnosis.json`). At 8,
all four main strip-wrap pairs individually have ratios below 1, while the frozen pooled verdict remains failed.
The left-brow wrap contributes 643 tail samples (252 both protected, 376 one protected); right-brow strip/cap-z
also remains mismatched (ratio 1.220222). This is localization, not a replacement test or changed eligibility.
`seam_poisson_crops.png` was inspected; no coloured overshoot like the rejected curvature solve, but numeric and
final appearance acceptance still do not pass.

**Boundary-condition diagnosis:** the zero-normal-slope prototype changed 492 pixels of an already correct affine
image. Continuing the slope from two valid observed neighbours preserves it exactly and passes hidden-pixel and
outside-pixel invariance (`boundary_slope_synthetic.json`). All 160 original cases were evaluated at the saved
independently selected base radii; exit 0 at 05:25:27. Quilting+slope seed-0 fine energy is
**0.223581 / 0.156040 / 0.136428 / 0.138020**, pooled **0.279221 / 0.174727 / 0.121274 / 0.121645**.
Both temples improve, both brows still fail; this is not a production A4 pass. PatchMatch+slope's complete results
are alongside in `boundary_slope_probe.json`. The unchanged donor-only selector with this corrected boundary
exited 0 at 05:37:33; ten truth-separation checks passed, but A4 still fails overall. Seed-0 fine energy is
**0.181175 / 0.253101 / 0.189999 / 0.122190**, pooled **0.167970 / 0.166712 / 0.197718 / 0.128060**.
See `adaptive_slope_synthetic.json` / `adaptive_slope_probe.json`. Previous source is preserved in `adaptive_v1/`
and no previous result is overwritten. No adaptive runtime API is accepted.

Signed energy diagnosis over all 160 cases exited 0 at 05:39:16 (`a4_signed_diagnosis.json`). Baseline median
signed fine error is **-0.233180 / -0.248821 / -0.127892 / -0.033209**; observed-slope correction leaves
**-0.149107 / -0.105602 / -0.007706 / -0.011735**. The brows predominantly lose detail; this does not replace
absolute-error grading. Inspected labelled crop sheet `a4_signed_brow_0.png` shows curved colour features replaced
by flatter copied fills. The conditional-texture experiment fits an affine trend and spatial covariance
by likelihood on local observed donors, then conditions a seeded texture draw on them. It never fits to proof
truth/metrics; its mean-only output is a diagnostic control. Both compact and long-thin poison tests precede the
full fixed-case run. That experiment exited 0 at 05:46:26, with nine synthetic checks passed, but failed A4 broadly:
conditional-draw pooled fine error **0.410778 / 0.377733 / 0.372039 / 0.671167**; mean-control pooled
**0.234897 / 0.188047 / 0.215485 / 0.273922**. `conditional_brow_0.png` shows invented ripples, so the
conditional-draw model is rejected. Reports/source remain local in `conditional_texture*` / `conditional_cases/`.
No production fill method or thresholds have changed. The next selector experiment uses the existing
`fill.knock_stats` / production radius-selection objective on separate donor training holes; the pointwise MSE
objective favours smooth means and does not express texture preservation. It never reads proof truth to select.
The texture-objective selector exited 0 at 05:58:43, ten truth-separation checks passed. A4 still fails as a set:
seed-0 fine error **0.124445 / 0.203384 / 0.193901 / 0.119204**, pooled
**0.174539 / 0.143471 / 0.176289 / 0.118601** (`texture_validation_probe.json`). No selector is adopted.

**Gain-field topology defect:** the documented 1 mm node smoothness edges were counted once by grid adjacency
and again for every rasterized texel pair / seam sample, whereas boundary edges were deduplicated. Thus the same
physical surface changed correction with atlas density or repeated identical seam samples. Two independent
synthetic tests fail before the repair and pass after deduplicating undirected node constraints (kinds retained).
No weight, clip or threshold changed. Focused regression: **29 passed**, 3.93 s, exit 0 at 05:52:11
(`gain_topology_regression_run.json` / `.log`). Original source hash
`9aa6d128c1f94539fa511f50f7e1155c186e643a71ada9e33b86858ae40b6190` and red-test logs are preserved in
`gains_before_topology.py` / `gain_topology_before*`. Fresh production bake exited 0 at 06:04:13, default quilt
0.9 mm and PatchMatch 0.3 mm independently selected on every part. **A3 and provenance pass**, protected photo
support 739,664; **all A4 rows still fail**. A5 worsened to **1.348161 / 1.345189**, retained explicitly rather
than calling the implementation tests quality passes. `gain_topology_candidate.json` / `_cache.pkl` / run/log
records preserve the full report. Neither this candidate nor the earlier seam candidate is accepted.

The zero-gradient seam prototype also changed 12 texels of an already correct synthetic log-affine planar
surface, because its seam pairs are distinct neighbouring centres. A local tangent-gradient estimate accounts
for their physical separation; four independent synthetic checks pass, including exact affine preservation and
removal of a chart exposure offset (`seam_gradient_check_run.json`, exit 0 at 05:57:55). The prepared experimental
`seam_gradient_field.py` / `seam_gradient_base.py` use no proof samples or tuned decay length. A fifth
all-protected no-op check also passed (`seam_gradient_synthetic_v2.json`). On the new gain-topology bake,
the diagnostic exited 0 at 06:05:49 and still fails A5 at 8: **0.967042 / 1.062656**, no clipping or protected
changes. This is a pre-reflection diagnostic, not a full-production quality pass or runtime change.

Contour transport (OpenCV Navier-Stokes, fixed three-pixel stencil) passed six synthetic invariance checks and
ran all 160 original holes, exit 0 at 06:08:32; **still fails A4**. Seed-0 fine error
**0.341929 / 0.088727 / 0.303084 / 0.436511**, pooled **0.236090 / 0.307809 / 0.270293 / 0.232576**.
This is a fixed-stencil diagnostic, not an own-radius filler comparison or an accepted method.
`isophote_fill_probe.json` / `isophote_synthetic.json` retain the result.

The training-view anchor inventory exited 0 at 06:10:24 (`anchor_consistency_diagnosis.json`). With 01 or 05
omitted, the two eligible brow-front source views have only their one mutual spectral comparison: it cannot
identify which source is better. With 03 omitted, the left brow has three mutually compared candidates; median
normalized-band profile distance is 0.165800 (01), 0.176475 (04), 0.210407 (05, current geometric anchor).
This is diagnosis using remaining training views only, not a changed source policy or B3 result. No source
admission, eligibility or frozen quality threshold changed.

**Common-boundary gain defect (this checkpoint):** a synthetic continuous log-affine surface was altered merely by
splitting it between two view labels. The leveller compared the interiors of adjacent 1 mm nodes, attributing
their natural spatial gradient to exposure. It now fits same-group tangent slopes and compares both groups at
their shared physical boundary. The original test fails before the correction; invariance to a common gradient,
response to a real exposure step and rigid-world-coordinate invariance pass after it. Focused regression:
**32 passed**, 3.95 s, exit 0 at 06:13:11 (`gain_boundary_regression_run.json`). Original source/red tests are
retained as `gains_before_boundary.py` / `gain_boundary_before*`; the earlier 20-test invocation is also retained.
That candidate exited 0 at 06:17:46. A5 still fails (**1.257212 / 1.204647**); every A4 part still fails,
including Temple_Right whose energy passes but boundary ratio does not. This version estimated slopes only
between nodes. A further independent one-cell-wide-group test exposed its missing transverse derivative
(`gain_thin_group_before*`: one failed, five passed). The correction now estimates tangent slopes from the
node's own observed texels. **33 focused tests pass**, 3.93 s, exit 0 at 06:21:36
(`gain_boundary_texels_regression*`). The node-neighbour version is retained as `gains_boundary_nodes_v1.py`.
The earlier bake's end-of-run source hash includes comment/dead-lookup cleanup performed during that run;
numeric code did not change. Runtime source stayed fixed for the within-node bake. No quality pass claimed.
The within-node bake exited 0 at 06:26:05 (`gain_boundary_texels_candidate*`): A3 within-one-level share
0.999223289, p99 dE00 0.836842, all 739,014 protected texels covered; single-source provenance passes.
Protected support is 405 below the immutable baseline, reported rather than excluded. Quilt/PatchMatch still
select 0.9 / 0.3 mm on every part. A5 **1.301842 / 1.289024**, both failed. A4 seed-0 fine errors
**0.566482 / 0.475355 / 0.246140 / 0.290558** and seam ratios **2.260464 / 1.725944 / 1.494812 / 1.472907**
(BL/BR/TL/TR): all four fail. This corrects a proved gradient-estimation defect, not the remaining quality failures.

Fresh B3 view 01 retains the right-brow pass (spectrum 0.118276, correlation 1.1). Left-brow spectrum 0.597883 /
correlation 1.75 fails; left-inner-temple correlation 1.4 fails; right-outer-temple spectrum 0.386111, correlation
1.6 and colour 7.406334 fail. View 03's left-brow spectrum 0.794991 / correlation 0.416667 still fails.
Reports `b3_01_gain_boundary_texels_decomposition.json` / `_03_...` retain all frozen rows, source hashes and
diagnostic subsets. No subset replaces eligibility or grading.
View 05 exited 0 at 06:35:42: both eligible rows fail. BL spectrum/correlation/colour
**0.390399 / 0.454545 / 4.853392**; BR **0.346649 / 0.7 / 4.021491**. C3 passes on BL, fails on BR.

Two offline model diagnostics have independent synthetic controls (`model_controls.json`, exit 0):
raw-context exemplar copying has RMSE 18.784 on a known brightness/contrast-transformed patch, versus 0.217
after fitting one contrast and three offsets on observed context. Eight compact/long-thin support and poisoning
checks pass (`affine_donor_synthetic.json`). Separately, independent SH fitting calls a shared albedo pattern
shading (factor p5/p95 0.595 / 1.708), while same-surface photo differences correctly fit zero. Four checks for
known relative shading, shared material, masked-input invariance and coordinate rotation pass
(`paired_shading_synthetic.json`). These prototypes change no runtime API, thresholds or assets; their real-photo
results follow. All original A4 holes were retained in the exemplar comparison.
The affine-exemplar run exited 0 at 06:38:24, still failing A4 as a set: seed-0 fine error
**0.148088 / 0.345833 / 0.190431 / 0.117604**, pooled **0.230686 / 0.228365 / 0.162567 / 0.149412**.
`affine_donor_probe.json`, `affine_donor_cases/` and the labelled crop review preserve the result. Inspected crops
remove the sharp paste edges but still lose observed fine structure, especially the left-temple stripes.
Regression contrast shrinks a donor whose pattern phase differs from the target. A second local diagnostic used
the observed context's mean and standard deviation instead, with the same donor admission/radius/boundary join;
eight poisoning/support checks pass (`moment_donor_synthetic.json`). No method is accepted.
That run started at 06:44:05 (PID 49724), exited 0 at 06:45:02, and still fails A4 overall:
seed-0 fine error **0.167347 / 0.117175 / 0.209569 / 0.085982**, pooled
**0.223828 / 0.159946 / 0.199490 / 0.110998**. `moment_donor_probe.json` / `moment_donor_cases/` retain every case.

The unregularized paired-SH probe exited 0 at 06:36:42 but worsened the frozen keep test (pooled dE00 drop
**-1.473**, |dlogY| drop **-0.3283**). Narrow normal coverage produced huge extrapolated coefficients and every
view saturated the fixed factor clip. A bounded fit enforces the existing [1/3, 3] range on mean-centred observed
normal predictions during the solve, retaining the production median normalization/clip afterward; four
synthetic checks pass (synthetic comparison tolerance 1e-4, not a changed quality gate). It exited 0 at 06:43:00:
pooled drops **0.365 / 0.0567**, but the unchanged leave-one-pair-out rule still fails (worst dE00 drop **-0.140**
without 04-05). `paired_shading_probe.json` and `paired_shading_bounded_probe.json` retain both failures. Neither
model is applied or adopted. The prepared full candidate would simply keep flatten off and is not run.
The next per-channel bounded fit tests chromatic directional illumination: on an independent synthetic example,
the achromatic model's maximum log error is 0.246438 while the colour fit recovers it within 0.000006875;
four recovery/material/masking checks pass (`paired_shading_rgb_synthetic.json`). This is still an experimental
local model. Its channel factors require an explicit integration adapter before any production bake/A3 proof;
do not pass these fits into the unchanged scalar runtime factor helper.
The RGB fit exited 0 at 06:49:47 but also fails keep: pooled drops **0.240 / 0.0579**, worst leave-one-pair-out
dE00 drop **-0.235**. `paired_shading_rgb_probe.json` retains this rejected result. No adapter or runtime change
is needed for it, and no full bake is run.

Context-matched donor validation keeps the earlier four fill models and unchanged texture-scoring objective,
but selects its observed training holes by their visible context, instead of coordinate proximity alone.
Four independent selection/poisoning controls pass (`training_context_synthetic.json`). Its first invocation
exited 1 before any real-photo case: the long-thin synthetic shape had no supported within-strip training patch.
The selector now falls back to complete global donors when that strip restriction yields none, matching the
existing donor-search fallback policy. Failed source/logs are retained as `training_context_v1.py` and
`context_validation_probe*`; the fresh `_v2` run passed all ten full filler poison/contract checks before
processing the 160 original cases. No runtime fill API or quality threshold changed.
The v2 run exited 0 at 07:02:42 and still fails A4 overall: seed-0 fine error
**0.182503 / 0.238192 / 0.145904 / 0.104593**, pooled **0.207527 / 0.131632 / 0.170542 / 0.119096**.
`context_validation_v2_probe.json` preserves every case and training-selection record. No filler is adopted.
An asynchronous research question on donor-only multiscale texture inpainting was sent to the owner because
AGENTS.md forbids web research here. It requests an implementable source-backed method, not new thresholds or
permission to stop; seam/gain work continues independently while any reply is pending.

**Folded-surface gradient defect (this checkpoint):** an independent right-angle folded surface showed the seam
prototype's averaged tangent gradients over a 3D chord halved the true intrinsic derivative (log error 0.025).
Comparing each side at the intersection of its tangent planes fixes this; four planar/fold/rigid-placement/solve
checks pass (`seam_gradient_folded_synthetic.json`). This also exposed a defect in the committed gain leveller:
its common chord midpoint lies inside the material at a crease. A new continuous-material fold test failed
before correction (`gain_folded_before*`: one failed, six passed). The gain solver now finds the closest common
tangent-plane point, with unconstrained fallback for a node lacking two spatial directions. The same test passes
at 30, 90 and 150 degrees after rigid rotation/translation. **34 focused tests pass**, 4.18 s
(`gain_folded_final_regression*`); added displacement reporting also passes **34 tests**, 4.01 s, exit 0 at
07:02:50 (`gain_folded_report_regression*`). `gains_before_fold.py` retains the prior source. No weight, clip or
proof changed. Runtime source stayed fixed for the production bake, which exited 0 at 07:07:22
(`gain_folded_candidate*`). A3/provenance pass, within-one-level share 0.999180954, p99 dE00 0.836842,
738,664 protected texels (755 fewer than baseline, all covered). Quilt/PatchMatch radii remain 0.9 / 0.3 mm
everywhere. **A5 1.244709 / 1.269608**, both failed. A4 fine errors **0.440921 / 0.421339 / 0.430153 / 0.237586**,
seam ratios **2.269105 / 1.525471 / 1.561730 / 1.393089**, all four failed. Gain comparison-point shifts have
p95/max **0.109326 / 0.555875 mm**; this is diagnostic geometry, not a quality criterion or pass.

The separate seam-texel geometry audit (`seam_edge_geometry.json`, exit 0) finds p95/max plane-intersection
shifts **0.028648 / 0.370311 mm**, none over 1 mm. The old chord-gradient Poisson variant on the SAME
within-node cache as the folded variant exited 0 at 07:09:48: **A5 0.971405 / 1.055846**, still failed
at 8. Protected observations are unchanged; `seam_current_chord.json` records solver/source identities and
channel residuals. Both remain pre-reflection diagnostics, not complete production proofs.
The folded variant exited 0 at 07:11:34: **A5 0.981730 / 1.061984**, still failing offset 8 and slightly worse
on this controlled comparison. Channel residuals are below 1e-7; all 347 graph components are anchored, no
clipping or protected-pixel changes. The labelled `seam_current_review.png` compares original/chord/folded strip
crops using the same calibration; inspected crops show no obvious coloured overshoot at that scale. This is
neither an AR review nor appearance acceptance. Both methods remain unadopted.

While the full regression held runtime/test sources fixed, local `priority_exemplar.py` prepared an alternative
fill diagnostic: confidence/isophote-priority front propagation copies complete original observed patches and
never averages overlapping predictions. Nine independent periodic-structure/constant/poisoning/repeatability
checks pass on compact and long-thin holes. The existing quilt also reconstructs those periodic controls exactly
(`priority_exemplar_control.json`), so these controls do not establish a superiority or a new runtime defect.
`priority_exemplar_probe.py` evaluated all 160 original holes, at their saved radii, with raw and observed-slope
joined outputs specified before grading. It exited 0 at 07:38:57 with unchanged source; all 160 cases remain.
The joined method's fine-band errors (BL/BR/TL/TR) are seed-0 **0.086120 / 0.362398 / 0.204971 / 0.257512**,
pooled **0.130544 / 0.266228 / 0.280017 / 0.218029**. Only BL passes the seed-0 and pooled A4 rows; raw priority
copying fails every part. The fixed first two seed-0 crops per part in `priority_exemplar_review.png` show
visible block joins, including with boundary correction. **Rejected**, no runtime API change.
The bilinear-footprint seam audit exited 0 at 07:39:25. At offset 8, 5,162 of 28,952 pairs have both samples
fully protected. In the folded variant's upper-5% tail, 632 pairs are fully protected, 568 have one protected
sample and 248 neither. The all-sample across-p95 lower bound if editable pairs became zero is 5.993749 dE00,
below the current within p95 11.470301. Thus protected observations do **not** establish unavoidable A5 failure;
this is a localization bound, not a replacement verdict or instruction to zero errors. Complete original A5
reports are preserved in `seam_protected_audit.json`.
The current-code check `reuse_019d321.py` exited 0 at 07:44:44 with source unchanged. All nine checks pass:
source/tier/class maps, protected mask, all **738,664 protected colours**, A1 and A3 remain identical/valid on
the existing frozen renamed job. **1,323,383 print pixels differ** because existing fill seeds include names;
whole-PNG identity is not a rename criterion. This compares the new folded-gain bake and does not repeat G1's
old accepted-identity migration, overwrite its evidence, or prove an unseen product. See `reuse_019d321.json`.
The labelled `gain_folded_review.png` was generated and inspected: original B, accepted revision, current repaired
atlas each use their own chart bounds and already levelled PNG. The broad blurred/smeared fill areas remain;
this review does not establish an appearance improvement or replace final AR evidence. Source hashes are in
`gain_folded_review.json`; no owner acceptance question is warranted for this failed-quality candidate.

Initial diagnosis recorded at session start (not a claimed fix): every part passes A4's calibrated mean-colour criterion, but fails
fine-scale energy and the hole-boundary gradient ratio. Quilting matches context then pastes only inside the hole;
it has no boundary-value constraint. Test a general local boundary correction that preserves copied texture detail
and never edits protected observations, against independently saved fixed holes (all five original seeds), before
changing production. A5 must be localized by chart/source class; independent fills across chart seams are a second
candidate cause, not grounds for extending the reflection band. B3 failures are heterogeneous: one row passes;
01/-x inner temple fails correlation length alone; the other five fail spectrum plus combinations of energy,
correlation length and colour. No recalibration can substitute for their pre-level held-out proof.

The remaining historical state and preserved proofs below remain valid. WP5 is incomplete; further repairs,
independent proofs, fresh regression/reuse checks and final appearance review are still required.

**Historical 2026-10-07 owner-requested handoff (retained): finish all remaining WP5 issues.** The owner said:
"i want to complete all the issues you mentioned" and asked for a new session to work on WP5 "until its good".
The earlier repair-versus-waiver question
is resolved: **continue repairs; no scoped completion or quality waiver.** Preparation only in that handoff turn;
no additional algorithm repair, paid call or proof run. Read [WP5_HANDOFF.md](WP5_HANDOFF.md) fully for the issue
list, evidence, protocols, run instructions and completion criteria. This is an explicit requested handoff,
not a claim that the conversation reached 80% context usage.

**Handoff preparation verification (commit containing this entry):** reviewed the active handoff/rule stack and
re-read the complete documentation diff and both new files. Corrected stale A–E/WP6/scope actions, stop-rule
meaning, the healthy-run versus two-minute-kill conflict, and the docs-before-commit order. The design now points
to the existing A4/A5/B3 protocols instead of presenting historical criteria as current. Added the remaining issue
map, preserved-asset identities, run commands, final proof requirements and `WP5_SESSION_PROMPT.md`.
Local `data/wp5/handoff_review_20261007/audit.json`: **49/49 documentation/evidence checks passed**, including
all four reference SHA-256 identities, evidence paths, links, CLI flags, reported failures and branch/file scope.
Only documentation changed; no runtime tests or expensive proofs were needed for this pass. No assets or frozen
thresholds changed. The ready-to-paste prompt is [WP5_SESSION_PROMPT.md](WP5_SESSION_PROMPT.md).

| Work | Current state |
|---|---|
| WP0 / WP1 / WP3 / WP3b / WP4 | Complete and tested |
| A–E repairs; WP5 option B promotion | Committed and verified; do not redo |
| WP5 reusable library (G1 dependency) | Complete and verified at `f020b57` |
| WP5 overall | Incomplete: repair A4 fill, A5 seams, B3 held-out realism and review final appearance |
| WP2 | A–C verified; agent wrappers remain gated |
| WP6 | KEEP STOP-I; no waiver; outside this task |
| WP7 / WP8 | Design only / not started |

**G1 reusable-library proof is complete (16:46; `f020b57`). WP5 remains incomplete under
its unchanged quality gates.** No test/proof processes remain running. The owner already accepted the revised
appearance for the exact revised asset: do not re-ask that historical verdict. A materially changed final candidate
needs its own labelled comparison and verdict. No paid call, push, product delivery or threshold relaxation.

Implemented `photoprint/production.py` (`bake_print`, `calibrate_level`, explicit CLI), `inputs.job_from_dir`,
job-specific AR calibration/measurement and runtime metrics. Fixture bake/calibration drivers delegate to the
library; no runtime import of tests or implicit FT1119 input remains. Named frozen camera/photo/matte identities
are verified; stale registration/bake inputs, unknown parts, ambiguous photo names, shared probe images,
double levelling and overwriting a calibration refuse. Owner-selected B remains bound to its original decision.
The library receipt explicitly says independent quality proof `not_run`; a successful bake cannot grant acceptance.

All evidence is local-only under `data/wp5/g1_20261007/`:

- **Canonical proof:** exit 0, 15:53:36-16:15:59, three workers, no timeout; 12,202,060 KB free commit at launch.
  All four maps match the accepted revised bake byte for byte. Print SHA-256
  `c39869cee1e1caf11f99141a3f93983485806900c73c8a7f977588cf2e11f9c8`.
  A1, six A2 round trips, protected-photo A3 and provenance pass. A7's serial/parallel outputs match on all six
  files (four maps, registration, stable report). Five A4 seeds and all six held-out bakes ran; failures below
  stay recorded in `proof/bake/bake.json`. Fresh Blender copy proof **15/15**, original scene unchanged; GLB
  `5497ed451c3409d0e780fe6f39d6d9528cf1efc5cf7554118e4334c0a15a554d`, identical to the accepted tool-test GLB.
- **Renamed-input proof:** exit 0. Four parts renamed `component.00`-`.03`; all six photo names changed.
  Source/tier/class maps and all **739,419 protected photo texels** remain identical. Fill differs at 1,324,055
  pixels because existing fill seeds include part names; whole-PNG equality is not claimed for this case.
  This is a reuse proof on the same geometry/photos, not an unseen-product accuracy claim. The proof exposed
  first-period name truncation in fitmesh/material lookup; only a three-digit Blender suffix is stripped now.
  Seven fitmesh tests pass, including exact original-model assembly parity. Failed attempts are preserved in
  `failed_names_*` / `failed_fixture_*` and the two archived job directories; the fixture's suffix handling was fixed too.
- **Fresh runtime AR capture:** exit 0, all poses plus portrait and a black-print control. Measurements agree
  exactly with the independent legacy instrument (`proof/capture_parity.json`); no new appearance approval implied.
- **Full WP5 regression:** required-data mode, three workers, no timeout; 16:25:30-16:45:21, **1,191.70 s**.
  **317 passed, 1 expected xfail, 6 fixture setup errors, no skips.** All **30 new G1 cases passed**. The six
  errors were one region fixture still omitting the now-required camera argument. Fixed it and five equivalent
  all-angles driver calls. Focused final rerun: **34 passed, no skips/errors**, 1.96 s, 16:45:39-16:45:42
  (`repair_tests.log`, `repair_tests_run.json`). Thus all 324 collected cases are covered after repair: 323 passes
  and the one expected xfail; this does not mislabel the first run's exit 1 as a clean full-suite exit.
- **Review/audit:** full source/test diff and every new file re-read before commit. Migration audit verifies
  unchanged executable ASTs for 36 calibration, 26 measurement and 30 runtime-metric functions; independent
  metrics remain unchanged. Changed functions cover explicit inputs, isolation, optional roles and naming.
  Fixed local-name capture, cross-job side caching and silent unknown-part handling. `final_audit.json` binds
  source/evidence hashes, exact accepted outputs, fresh capture parity, scene preservation and B-baseline preservation.
  Static call audit finds only the intentional negative test omitting a camera input. Git data exclusions verified.

**Remaining WP5 stop:** A4 fill fails on all four parts; A5 p95 across/within ratios **1.288293 / 1.300100** at
7/8 texels fail; B3 **6 of 7 eligible held-out rows fail**. Registration is still **0% pixel-verified**. Owner
appearance acceptance permits continuation but is not an explicit A4/A5/B3 waiver. The preserved production-B PNG
is still `b52f31129af3e760195dc2fcde59fafc891340efad9dd0b9b6598c4fae8813b6`.

**Exact next action:** read `data/wp5/g1_20261007/proof/bake/bake.json` A4, A4_fillers, A5, B3 and STOP_B with the
independent instruments. Localize failures by part, hole/seam, view and source class; form a cause-based repair
hypothesis before editing or launching another full run. Follow WP5_HANDOFF.md's ordered work and closeout checks.
STOP B blocks completion, not the authorized $0 repair work. Do not ask for a waiver, repeat G1, or start WP6.
WP0/1/3/3b/4 remain complete
and tested; WP2 A-C are verified, G1's library dependency is ready but agent wrappers remain gated; WP6 retains
STOP-I, WP7 is design only, WP8 has not started. Ask before every paid run; never push.

**Context policy updated by the owner, 2026-10-07:** hand off only at reliably established 80% usage of this
conversation's current context window. Cumulative goal/account usage and summarization or compaction do not
trigger a handoff. If current usage is unavailable, continue and keep progress notes. PLAN §0 rule 10 and
AGENTS.md now agree; this supersedes the earlier compaction-triggered handoff below. The owner explicitly requested
this handoff and resolved the scope question in favour of repairs; context compaction is not the reason for stopping.

The historical handoff below is preserved. The owner initially requested RESUME HERE section 5 step 1:
finish and verify sections 3.1-3.5, then commit A-E separately with explicit paths, no push, and approval
before every paid call. Work is on `glasses3d`, performed sequentially without subagents per AGENTS.md.
**Section 5 step 1 is complete:** the stopped work in 3.1-3.5 is verified and committed as groups A-E.
The owner then said "continue": section 5 step 2's already-selected B promotion is also complete (entry below).
The historical status table and RESUME HERE text below describe the original stop; this active entry supersedes them.

- **Group A committed: `e3fd8fe` (11:39).** All 12 stored step-6 findings reviewed against the full diff,
  followed by a fresh full-diff re-review. Full WP5 suite: **276 passed, 1 expected xfail**, no skips,
  exit 0, 11:19:05-11:38:48 (1,182.86 s); detached without a timeout, default pool limited to 3 for memory.
  `data/codex_resume_20261007/wp5.log` and `wp5_run.json` retain output and exact arguments.
  The independent provenance audit passes **200 checks** (`wp5_evidence_audit.json` in the same folder).
  Production print SHA-256 remains `d2e153a3d540e608e8eeaf3bca13b22ec8de8072c85c25ea48a1d59f934928af`;
  tool-test GLB remains `17d604886c126e06a61ffe41df3b7dd3877043a24c9e29d36d0e4f5fcbf98095`.
  Corrected five stale calibration-count claims in PORT_REVIEW (29 captures, not 30). Its WP2 section 11
  belongs to D and is kept out of A. No production level change; owner B promotion remains section 5 step 2.
- **Group B committed: `9a9e540` (11:49).** Re-read the arview camera-mode diff, new camera parser/tests,
  and the complete all-angles generator against the stored two review rounds. Fixed measurement ignoring
  `--workers`; nonpositive limits now fail before work, with CLI regressions. Corrected the metres-to-centimetres
  matrix comment. No owner sheets needed regeneration; the sent comparison remains valid.
  Fresh checks: **500 Node tests, 106 QA tests (including GPU explicit-camera and stall tests), 27 Python tests**
  passed; TypeScript check passed. Logs and commands: `data/codex_resume_20261007/b_runs.json` and `b_*.log`.
  New ordinary `--poses all --backgrounds skin` capture: **100/100 images byte-identical** to both clean-commit
  baseline captures (`d005c321ea50e9b787c02f7631480b4e6efa43b9`) and the stored changed-renderer capture.
  Audited actual PNG hashes against each report, **400 files total** (`pose_identity_audit.json`). The fresh
  report retains the three existing lens-degenerate-triangle / portrait-auto-fit warnings; exit 0, spec passes.
  Fresh full-diff re-review complete; no remaining major in B.
- **Group C committed: `f6fa360`.** Fresh hand trace completed at 12:02:48, exit 0, 813 s:
  `data/wp6/handtraced_codex_20261007/handtraced.json`. Trace did not converge in four lifts; r_C 3.35 mm is a
  front-bound cap, not a measured depth. Failed gates G1/G2/R1/R3/R4; the fitted slab is now the comparator.
  Full WP6 suite: **216 passed, 2 expected xfails**, no skips, 553.31 s, exit 0 (`c_tests.log` under
  `data/codex_resume_20261007`). The named/merged R2 confirmations regression is present and passed.
  Fresh full-diff re-review completed; diagnostic exits now also tolerate an absent prior S1 record while
  reporting stage errors/source changes as exit 1 (seven targeted checks passed after that repair).
  Fresh full proof into `data/wp6/proof2` completed **STOP-I**, exit 0, 12:20:57-12:38:32 (1,054.64 s),
  detached with three workers, no quick mode or waiver. Log and exact arguments:
  `data/codex_resume_20261007/c_proof.log`, `c_proof_run.json`.
  Frozen relief_rules SHA-256 remains `7c22eab7b32327f61140fc7982883db9d6be3bbd2c0fbdce8b04d25a7da062cc`.
  Dependency table SHA-256: `860500579b76d0cdfe1aa717b5673b4ecadf3f0ab19fc352d6577d7053f36c1b`;
  its stopped-work changes describe the actual common-frame theta dependency, with no relaxed numeric gate.
  Source scene SHA-256 is unchanged in both runs: `56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06`.
  Delegated decision and evidence are recorded below and in both proof records. **WP6 remains gated.**
- **Group D committed: `b3df813`.** Full worker/camera suite: **362 passed**, no skips, exit 0,
  275.69 s, 12:39:12-12:43:49, G3D_REQUIRE_DATA=1. Fit tests use three workers after the WP6 pool ended
  (12,505,704 KB free commit at launch); logs/arguments: `d_tests.log`, `d_tests_run.json` in the audit folder.
  Fresh full-diff re-review against impl:A/review:A:1/impl:B complete, including all new files. Ownership confirmed:
  `agent/__init__.py` is impl:A, `agent/seeing.py` is impl:B; `session_inputs.py` is impl:loop and belongs to E.
  Fixed host-only output directory creation and junction escape, with parts-only and metrics-only regressions;
  reopened checkpoints now require both recorded file SHA-256 and scene fingerprint (refuse or verified-parent
  fallback). Five added regressions passed. Stage-A's soft-timeout wording minor is reconciled to the implemented
  0.5-600 s range in the WP2_DESIGN update in E; default 120 s and all stop thresholds remain unchanged.
  Analytic camera parity <= 0.00103 px (limit 0.01); rendered <= 0.0313 px (0.1). All six Prada baseline deltas are
  zero for IoU and contour. Exact corpus replays: Prada 19/19, Oakley 21/21, INVU 20/20. Renders: 28 PNGs in 11.1 s,
  fingerprint unchanged; Blender/host silhouette IoU 0.99908-0.99960. No remaining major in D.
- **Group E committed: `82c5623`.** All six stored majors and the money/network/design minors
  were fixed, followed by a fresh full source/test/design diff re-review against the three stored reviews.
  Final offline suite: **399 passed**, no skips, **65.17 s**, exit 0, 12:58:12-12:59:18
  (`data/codex_resume_20261007/e_tests_r4.log`, `e_tests_run_r4.json`). No remaining blocker/major in E.
  Closed-run anchors and transport high-water checks reject truncation; prior/final row hashes bind reconciliation
  provenance. The consumed approval bounds the ledger cap; current job/brief/photo/notes bytes are rebound before
  consumption. Every run requires explicit effort and the two-way tool contract. Implementation receipts now archive
  and hash exact source bytes, including dirty source, while excluding inputs, environments and dependencies.
  Pinned installed psutil 7.2.2 with its official PyPI wheel SHA-256; no version upgrade.
  Additional fixes: archive-owned response reconciliation, input-count mismatch blocks further paid work, preserved
  partial-ledger recovery, approved-cap default, upward-cent display, initial-message order, HTTP-only SDK transport,
  image-bearing text moved out of receipts/session rows, known-unsent failures recorded at $0 without retry, and
  rollback/session repair only after lock/approval consumption. Snapshot failures leave a result and release locks.
  The final review aligned the loop's soft-timeout floor with the worker's 0.5 s floor and verified its boundary test.
  Exact Prada proof through the real SDK/hooks and the full loop: **59 counts = 59 creates = 59 rows**,
  row 1 reserve **719,438 micro-USD**, settled **14,261,424 micro-USD**, peak committed **17,164,748 <= 18,000,000**.
  These are replayed historical amounts; actual spend is $0. Recorded-data comparisons all ran and passed.
  Public-fixture audit: 59 Prada rows and 655 rows across 13 corpus runs, integer token counts plus short metadata;
  no photos, prompts, transcripts, paths or secrets. `e_final_review.json` records fixture/source hashes and evidence;
  `e_commit_paths.json` lists the 35 explicit E paths (both local-only under the audit folder).

All work was **$0**, with zero paid calls and no push. Only pre-approved exact-version PyPI metadata was fetched.
All launched test/proof processes have finished. WP0/1/3/3b/4 retain their prior completed status; WP5 step 6 and
WP2 stages A-C are verified, but WP5 and WP2 as a whole are not complete. WP6 remains gated by STOP-I; WP7 is design
only and WP8 has not started.

### WP5 option B promotion, 2026-10-07 (separate commit with this entry)

**B is now the production level; WP5 remains incomplete.** Reason: "owner chose near_18deg 2026-10-07".
All **67** original A bake files were copied into `data/wp5/ft1119_bake/pre_owner_B/` and hash-verified before and
after promotion. The original A tool-test GLB remains unchanged. The new production `print.png` SHA-256 is
`b52f31129af3e760195dc2fcde59fafc891340efad9dd0b9b6598c4fae8813b6`: **byte-identical** to the PNG embedded in
the owner-selected B GLB. It was computed once from the unlevelled `ft1119_bake_radiance_s6b` through the existing
level-map path, not applied over A. Calibration retains its original fitted recommendation/diagnostics and adds a
`production_selection` bound to candidate parameters and verdict row 4. The bake honors that selection; malformed
or changed selections refuse, and recalibration cannot overwrite the choice in place.

The temporary bake-copy proof passed **15/15**, exit 0, **22.6 s** (13:41:52-13:42:16). New TOOL TEST GLB:
`data/wp5/owner_B_20261007/tooltest/q001_painted_TOOLTEST.glb`, SHA-256
`b5c07b783083e1fb8983c9e737744dee28499aa921b1c363354356ce1f41c769`. The export has fresh provenance, so its whole-file
hash differs from the earlier candidate. Its texture matches the production manifest; geometry/UV/spec/invariance,
detach/remove, six photo-camera render comparisons and original scene preservation all pass. This is a tool test,
not an Astra delivery. Owner-ledger row **5** binds the existing preference to this proof; `new_owner_review=false`.

Focused regression checks: **65 passed, 1 slow test deliberately deselected**, 13.79 s, followed by **27 all-angles
tests passed**, 1.07 s. The full WP5 suite already passed in A; this small selection/provenance change was checked
with the focused suite and fresh Blender proof. Four part sheets plus the overview were regenerated for B; old
held-out panels were excluded. Original held-out and serial artifacts remain under `heldout_option_A/` and
`a7_serial_option_A/` as well as the intact backup. Prior quality checks are labelled historical, STOP B failures
remain open, and `step6.json` points explicitly to the prior A proof. The frozen all-angles verifier now checks A
against the preserved A bake; a fresh identity audit revalidated all four original options without rewriting the
owner's sent sheets. Fresh full-diff re-review completed before commit.

Evidence: `data/wp5/owner_B_20261007/{promotion.json,tool_proof_run.json,tool_proof.log,tests_run.json,tests.log,
sheets_run.json,historical_options_identity.json}`. Preparation/finalization scripts are retained in
`data/codex_resume_20261007/`. All work was $0; no paid calls or pushes. All launched processes finished.

**Next action recorded at the B-promotion commit (superseded by the appearance entry below):**
Read the frozen top comparison `data/wp5/all_angles_sent_1042/`, `data/wp5/all_angles/rows/04_top.png` and
`data/wp5/all_angles/verify_1/zooms/top_brow_left_x3.png`; trace those texels back through the current B print's
classes/source maps and highlight handling before proposing a measured tool correction. Preserve the new B
baseline and test corrections on copies. Then address off-angle darkness (+1.6..+5.1 L*), grey/flat +x temple at
yaw -30 and -x spot shape/colour versus photo 03, with owner visual judgment. Finally build the reusable library
entry (`g3d_host/photoprint/production.py`, `inputs.job_from_dir`, no FT1119 defaults) and its G1 proof. None of
these further corrections or G1 work had started at that commit.
WP0/1/3/3b/4 are complete and tested; WP2 A-C only are verified; WP5 is in progress; WP6 gated; WP7 design only;
WP8 not started. Do not repeat A-E or the B promotion.
The delegated WP6 decisions are already resolved below; do not re-ask them or loosen frozen gates. Before any paid
call, obtain the owner's explicit approval for that run; ask the fresh WP8 questions when WP8 is reached.

### WP5 brow-top appearance diagnosis, 2026-10-07 (commit containing this entry)

**WP5 is still incomplete. The owner accepted the revised print as close enough and requested the reusable library
proof next.** A conservative pigment-mask correction is verified on a tool-test copy; the selected B baseline is
preserved. The frozen top-brow blotches trace mainly to protected photo-04 anchors, not invented fill.
The colour-arm classifier peppered smooth amber edges with isolated pale seeds; dilation and fill then cut dark,
ragged holes around the light cores. Existing view gains also brighten this region (about 1.4-1.55). The gain-fitting
algorithm and owner-selected level parameters were not changed; fitted gains were recomputed from the revised
donor support, so their values do change. The change is not restricted to the two diagnosed patches.

`metal.supported_pale` now requires spatial/hardware/achromatic support before excluding a small colour-only
outlier. It reuses the existing 8 mm hardware distance, 2 mm2 island area, 0.85 B/R neutral limit and 0.4 mm
opening / 12.5 luminance / 0.6 blue-to-red glint-shoulder limits. The old step-4 diagnostic is unchanged. No product
coordinates enter the production rule. `pale_colour_only` is reported separately; hardware, coherent contamination,
neutral highlights and additive-white glints remain excluded. This is a partial appearance repair, not a new
acceptance threshold or a delivered model.

All evidence below is local-only under `data/wp5/resume_20261007/`:

- Fresh diagnostic bake: **exit 0**, 13:58:48-14:03:38, four workers, 2321x1577, 15.94969 texels/mm. Exact arguments
  are in `bake_run.json`; held-out/B3 was explicitly skipped, A4 used one seed. Print SHA-256:
  `c39869cee1e1caf11f99141a3f93983485806900c73c8a7f977588cf2e11f9c8`.
  A1, A2 in six views, protected-photo A3 and provenance pass. A4 and A5 failures remain reported. Registration
  remains **0% pixel-verified**; this run is not a completed WP5 proof.
- Fresh Blender copy proof: **15/15**, exit 0, 20.4 s. Tool-test GLB:
  `tooltest/q001_painted_TOOLTEST.glb`, SHA-256
  `5497ed451c3409d0e780fe6f39d6d9528cf1efc5cf7554118e4334c0a15a554d`.
  Original scene unchanged; geometry/UV/normals/spec, material control, undo and all six render checks pass.
  Production B's PNG is still `b52f31129af3e760195dc2fcde59fafc891340efad9dd0b9b6598c4fae8813b6`.
- `tests/photoprint/appearance_proof.py` reproduces the source-photo diagnostic and frozen-camera comparisons.
  Two source-photo crop boxes were fixed before repair. In-sample 1-4 native-pixel band-pass correlation improves
  **0.856037 -> 0.928199** (4,894 pixels) and **0.777065 -> 0.893445** (4,520 pixels). These are NOT held-out accuracy.
  `evidence/asset_audit.json` verifies geometry/material invariance, identical sampler/UV selection and exact
  embedded PNG hashes; both capture inputs/cameras and **24/24** PNG hashes verify. Six photo comparisons and
  `evidence/owner_focus.png` show the improvement and remaining defects.
- **Confirmed the recorded A5 failure in the existing production B baseline:** independent read-back at production density,
  graded 7/8 pixels beyond the 6-pixel reflected seam band, fails BEFORE and AFTER. Across/within p95 ratios are
  B **1.363681 / 1.290669**, revised **1.288293 / 1.300100**. Absolute across p95 is B **15.19674 / 16.41417**,
  revised **15.52555 / 16.88483**. `seams_comparison.json` retains both full measurements. The 8-texel/mm regression
  fixture's seam pass does not establish the production-density gate. No thresholds were weakened.
- Turned-view material diagnostics (`ar/levels.json`, `ar/provenance.json`) retain both normal and black-print
  controls, plus clearcoat-off copies labelled TOOLPROBE. Clearcoat-off alone does not solve the appearance:
  front temple p10 improves +3.67 to +0.79 L*, but its median chroma remains 0.93 versus the photo's 3.51; pitched
  brow levels become darker. No material change was promoted. The -x arm still has rounder/oranger spots than
  photo 03, and the +x arm remains flatter in turned views.
- Full WP5 regression suite, detached with three workers and no timeout: **287 passed, 1 expected xfail,
  3 skipped, 1 failure**, exit 1, 14:04:44-14:24:08, **1,163.65 s** (`tests.log`, `tests_run.json`). The failure was
  the known-state A4 table: PatchMatch on Brow_Left now fails too. Deliberately recorded this regression without
  changing frozen A4 criteria; quilting still fails all four parts. Detailed repeat measurement: 8 holes,
  fine-band log energy **0.271392 > 0.15**, coarse **0.112324**, mean colour **1.353613 < 3.469649** ring limit,
  seam ratio **1.023154 < 1.2**. `a4_focused.json` retains all methods/parts/holes.
  The three manifest tests had skipped because they still opened the old A GLB. Fixed their fixture to follow the
  current bake's hashed tool-test record; required-data mode now fails instead of skipping missing/stale assets.
  Focused rerun: **16 passed, no skips**, exit 0, 14:26:46-14:27:50, **63.98 s** (`focused.log`, `focused_run.json`),
  including the changed A4 record, all manifest tests and all five new pigment-mask regressions. Two of those five
  were added after the full suite collected; the focused run covers them. The full suite was not repeated after
  these test-only corrections. This is not a claim that the A4/A5 quality gates pass.

**Owner verdict:** "Close enough—continue with the library proof", in response to `evidence/owner_focus.png`
(Photo / Selected B / Revised mask -- TOOL TEST). Recorded in owner-ledger row 6, bound to the revised GLB/PNG and
shown sheet hashes. This accepts the appearance for continuation; it is not a delivered model or a numeric-gate
waiver. No additional visual question is pending. B's preserved production files were not overwritten.

**Next action at the appearance commit (superseded by the G1 completion above): G1**, the reusable library entry (`g3d_host/photoprint/production.py`, `inputs.job_from_dir`,
no FT1119 defaults), including migration of the bake driver and AR level calibration out of tests. Prove it on
the accepted revised print through the real host tools, then run/record the required WP5 proof with existing
A4/A5/held-out failures explicit. **G1 has not started.** The source/class/gain and AR diagnostic scripts remain
available if later work needs them. Owner acceptance cannot turn failed numeric gates into passes.
WP0/1/3/3b/4 remain complete and tested; WP2 A-C are verified; WP6 remains gated; WP7 design only; WP8 not started.
Fresh full-diff re-review completed; all launched processes finished. No paid call or push occurred. Do not repeat
A-E or option B promotion. Ask before every paid run. The handoff at this point followed the former compaction
trigger; the owner's updated 80%-of-current-conversation rule above supersedes that reason for stopping.

### WP6 delegated decision, 2026-10-07 (group C)

**KEEP STOP-I, KEEP R1's frozen definition/0.5 threshold, and KEEP every frozen R3 clause. No waiver.**
This was decided by the coding agent (Codex) under the owner's delegation of 2026-10-07.

- Fresh S1 recovers its tested parameters: outline 0.151779 mm (limit 0.2), zone level 0.129544 mm (0.5),
  r_C 0.056853 mm (0.5), and cited common-frame theta 0.190497 degrees (0.3). However, the TRUE temple's
  transfer with realistic guide errors is **0.910923 mm > 0.7 mm** (AB 0.986030, BC 0.835815). Exact ideal
  lines give 0.274915 mm. R2 distinguishes truth (pass) from slab (fail), but this cannot rescue R3's instrument.
- R1's synthetic faceted truths score 0.668/0.708, and the stored band search bottoms at 0.596, all above 0.5.
  That is evidence that calibration needs review, not grounds to change a frozen gate to pass this product.
- R3's relative clause is also unresolved: in the fresh hand-trace record the delivered transfer is 0.693722 mm,
  making delivered-minus-0.5 only 0.193722 mm, below even fresh S1's exact-line truth floor. Dropping the clause
  or reducing its margin would redefine success. Keep it and report failure.
- The hand trace did not converge after four lifts; **r_C 3.35 mm is a front-bound cap, not measured depth**.
  Its crease result is conditional (AB reaches the 6-degree floor only above r_C 2.675491 mm). It fails
  G1/G2/R1/R3/R4 against the FITTED slab; top contour 1.139788 vs slab 0.835620 mm, hinge offsets
  2.710403/2.347892 mm against 0.6. The top/front thickness-level conflict remains; it is not proven to be
  solely a delivered-frame defect. The withdrawn 5.3 +/- 0.6 mm triangulation is not reinstated.
- Alternatives considered: waive the instrument and carry a conditional diagnostic; redefine R1 as the old
  least-squares-plane metric or raise its threshold; relax R3's margin. Rejected for this repair. A future rule
  set needs independent calibration and design review before fitting, or better guide/camera/geometry evidence
  followed by the unchanged proof. These sheets are geometry diagnostics, not owner-visible AR validation;
  the waiver's AR condition has not been met. No hand-authored geometry was promoted.

Machine-readable decision, alternatives and image hashes: `data/wp6/proof2/delegated_decision.json`, also embedded
as `delegated_decision_review` in `proof2/proof.json` and `handtraced_codex_20261007/handtraced.json`. Their
automated `open_owner_decisions` fields are preserved as historical evidence, superseded by this decision.
Labelled **photo | hand-traced TOOL TEST | delivered geometry** side/top/rear comparisons were generated from the
fresh hand trace and each model's own fitted camera, visually checked, and kept in
`data/wp6/handtraced_codex_20261007/owner_comparisons/` (overview plus three detailed sheets). Shading is the
instrument's fixed key/fill; brown lines indicate model normal changes of at least 6 degrees, not texture.

## Historical status (2026-10-07 ~11:00; superseded by Active resume)

The following table and RESUME HERE block preserve the old stop state. Their pending actions, approvals and
workflow commands are historical; follow Active resume and WP5_HANDOFF.md instead.

| WP | State | Last commit |
|---|---|---|
| WP0 setup, port review | **done, proof passed** | b19a08a |
| WP1 arview | **done, proof passed** (independent image checks; closeout) | b5a20ea |
| WP3 exporter + validate_spec | **done, proof passed** (review defects fixed, mutation tests) | 6acfc5a |
| WP3b camera fitting | **done** (port reproduces the prototype to 1e-6 under scipy 1.16.0; 12-view baseline; the registration-threshold question is dissolved: WP5 uses a photometric gate) | 457daf7 |
| WP4 lens seating | **done, proof passed** (P2 FT1119 71/71; Prada seats; Oakley 0 moved; P6 junction line 0 px in AR) | b76c249, 140cc71 |
| WP5 photo print | **in progress. OWNER CHOSE OPTION B ("darker")** on the all-angles sheets (2026-10-07 ~11:10, ledger row 4). Next: make B the production level (section 2). Step 6 built + full suite green at 09:27 (241 passed), NOT committed; its review-fix workflow was STOPPED mid-verification (section 3.1) | 5ec9c25 |
| WP6 faceted temples | re-diagnosis STOPPED in its fix round (section 3.3). Latest proof (10:36) ends at STOP-I; tool OK with hand-traced parameters. **The owner DELEGATED the WP6 decisions to Claude** ("review the issue of wp6 and decide by itself"): review and decide (section 3.3) | 204f694 |
| WP2 live Astra session | stage A built + reviewed (324 tests); stage B built, its review STOPPED mid-way; stage C built (money exact), its fix round STOPPED before any edit; SDK installed; NOTHING of A/B/C committed; D-F not started | f479660 (requirements-agent.txt) |
| WP7 instructions | **design done** (`plan/WP7_DESIGN.md`); implementation waits for WP2 stage D's registry | 78d1950 |
| WP8 paid validation | not started; owner OK per paid run. The owner said "let the new model ask me again": ask the questions fresh when WP8 is reached (budget cap per run, suggested $15; effort max or high; smoke cap $1; regression product Oakley or INVU; WP7_DESIGN §4.2 #1-#2) | - |

## Historical RESUME HERE (2026-10-07 ~11:00; do not execute as the current resume)

### 0. The owner's instructions at handoff (verbatim)
- 10:40: "first get me the comparisons, only after i give you my opinion you may resume to what thats written below. in addition, you are
  reaching usage limits, please finish you tasks (dont kill any runs) and tell me where are we standing. note anything thats not compete,
  especially things you are in the midst of!!!!! this handoff should be perfect (i define perfect here as - the next ai app resuming your
  work with no possibility of the transition to screw your work."
- 10:52: "is it possible to end your current runs? and that the next model will resume your work? id so, plesase do that before we run out
  of usage completely. be very very elaborate with info, include tiny details!"
- ~11:10, the owner's answers (verbatim): "1. which option is best - b  2. i want the new model to review the issue of wp6 and decide by
  itself on what to do  3. let the new model ask me again".
  => (1) WP5 verdict = **B** (recorded: `glasses3d/data/ledger/owner_verdicts.jsonl` row 4, `prefer_option_B`, GLB sha bad9c861...);
  (2) the WP6 owner decisions (STOP-I waive-or-keep and the two rule-set questions) are DELEGATED to the next model: review the evidence
  and decide yourself, record the decision + reasons in this file, and tell the owner the outcome in plain words (do not ask them);
  (3) the pre-WP8 questions (money, effort, smoke cap, regression product, WP7_DESIGN §4.2 #1-#2) are NOT answered: ask the owner
  again, fresh, when WP8 is reached. **The verdict has been given, so the next model MAY resume work** (sections 2, 3, 5).
=> All five workflows were stopped with TaskStop at ~10:51 and every child process (pytest, Blender, node/Chromium, proof pools) is gone
(checked: only unrelated Claude-extension and Codex processes remain). **Do NOT start new work until the owner gives the WP5 verdict on the
all-angles sheets.** [Superseded ~11:10: the verdict (B) is in; resume.] Order: act on the verdict first (section 2), finish and commit
the stopped work (section 3 / 4), then section 5.

### 1. The WP5 decision (asked 10:42: which option looks most like your glasses? ANSWERED ~11:10: **"b"**)
- Sent four sheets; frozen copies in `glasses3d/data/wp5/all_angles_sent_1042/` (sha256 prefixes): `all_angles_photos.png` [f5d3b776]
  (front, angled, top, back: each from the photo's own fitted camera, arview renderer, no face), `all_angles_photos_shape_differs.png`
  [34954165] (03 wearer's right side and 06 left side from above; model outline IoU 0.734 / 0.712 is Astra's shape, the bake's own fit
  scores the same, so "judge the paint, not the outline"), `all_angles_tryon.png` [14e2bfc8] + `all_angles_tryon_2.png` [3a0be1de] (11 try-on
  poses + on a face). Columns: your photo | Astra's texture (now) | A (new paint) | B (darker) | C (more colourful). The live copies in
  `glasses3d/data/wp5/all_angles/` are byte-identical to the sent ones (png mtimes 10:35:37/10:35:45; the stopped fix round 2 only re-ran
  its measure stage, it never rewrote a sheet).
- Measured (fix2_run.log 10:35:32): option differences against A: B 0.763 of frame px over 6/255, mean dE76 4.34 (VISIBLE); C 0.005 / 1.14
  (NOT visible). Told the owner: C looks almost the same as A everywhere, the real choice is Astra / A / B.
- Options (`glasses3d/data/wp5/ar_calibration/calibration.json` -> `candidates`): A = `photo_angles` = production `data/wp5/ft1119_bake/
  print.png` (gain 0.931, gamma 1.080, sat 0.996, chroma_power 0.455, chroma held >= 1, hue band 20-100), tool-test GLB `data/wp5/tooltest/
  q001_painted_TOOLTEST.glb` (sha 17d60488...); B = `near_18deg` (gain 0.831, gamma 1.168, same chroma; GLB `data/wp5/ar_calibration/
  cand_near_chroma_TOOLTEST.glb`, captures `sheet_near_chroma`); C = `all_pairs_chroma` (gain 0.931, gamma 1.080, sat 1.378, chroma_power
  0.615; GLB `cand_chroma_all_s4_TOOLTEST.glb`, captures `sheet_chroma_all_s4`). `all_angles_sent_1042/all_angles.json` records the GLB
  sha256s actually rendered. Astra = delivered q001 GLB (`automation/data/blender_agent/studio/jobs/3b8a1c7a.../original/model.glb`, sha
  af6e9d10...). NOTE: the "near_18deg" pairs are ~27-30 deg by viewing direction (name inaccurate; the sheet says only "darker"); B/C GLBs
  carry a stale "WP5 step-4" asset note (ar_calibrate.evaluate copies the source label through swap_image; fix later).
- Told the owner, same in every option and NOT the paint: silver metal + cream lenses (photos: gold + beige), tip medallion only on the +x
  (wearer's left) arm = Astra's model content; one real PAINT defect to fix next: small pale-yellow/white ragged blotches on the brow tops
  (`data/wp5/all_angles/rows/04_top.png`, zoom `data/wp5/all_angles/verify_1/zooms/top_brow_left_x3.png`), in the production print
  (probably highlight handling). Step-6 AR facts: the print matches brightness + colour only at photo 05's own angle (pitch +20); the
  front pose is ~+5 L* lighter/greyer = Astra's clearcoat (32.9 % of the brow fronts, +13 L*) -> a MATERIAL note for Astra.

### 2. The owner's verdict is B ("darker"): what to do
- The owner picked **B** = candidate `near_18deg`: gain 0.831103704589155, gamma 1.1678124999999997, sat 0.996044489936834, chroma_hold
  true, chroma_hue [20, 100], chroma_power 0.455 (exact values in `data/wp5/ar_calibration/calibration.json` -> `candidates.near_18deg`;
  B's print.png sha b52f3112...; GLB `data/wp5/ar_calibration/cand_near_chroma_TOOLTEST.glb` sha bad9c861...). Make B the production
  level: the production `data/wp5/ft1119_bake/print.png` (sha d2e153a3..., = A) must become B's print; keep A's files (copy them to
  `data/wp5/ft1119_bake/pre_owner_B/` first - never delete). The code default that chose A (the `photo_angles` choice in
  `tests/photoprint/ar_calibrate.py` / the bake's level step) must take the owner's choice (record "owner chose near_18deg 2026-10-07" as
  the reason, not a fit). Prove: the new print.png is byte-identical to B's print (b52f3112...) or explain any difference; re-run the
  tool test on a temp COPY of the bake (`python -m glasses3d.tests.photoprint.print_tooltest --bake <temp copy>`, 15/15) and record the new
  tool-test GLB sha. Do this AFTER verifying/committing group A (section 3.1) so the step-6 fixes and the level change are separate commits.
- (General rule kept for later choices) If A, B or C: make that level the production level (re-run the level map with that candidate through the existing path in
  `glasses3d/tests/photoprint/ar_calibrate.py` / `shading.level_map`, write print.png, re-run the tool test on a temp COPY: `python -m
  glasses3d.tests.photoprint.print_tooltest --bake <temp copy>`), record the verdict in `glasses3d/data/ledger/owner_verdicts.jsonl` bound to
  the tool-test GLB sha (a tool test, not a delivery). If "none"/"Astra's": ask nothing technical; diagnose from the owner's words.
- Next WP5 step (by eye, single-source print): (1) the brow-top blotches; (2) the darkest decile too light off the calibration angle
  (+1.6..+5.1 L*); (3) +x temple at yaw -30 grey/flat, -x temple spots smaller/rounder/more orange than photo 03; (4) move the bake
  (`tests/photoprint/bake_ft1119.py`) and the AR level calibration (`tests/photoprint/ar_calibrate.py`) into a library call
  (`g3d_host/photoprint/production.py`, `inputs.job_from_dir`, no FT1119 defaults) = WP2 gate G1 / finding F2.

### 3. The five STOPPED workflows: exactly where each was cut off and what to do
Durable copies (the .claude and Temp folders may be cleaned): `glasses3d/data/handoff_20261007/results_<name>.json` = every completed agent's full result + the
interrupted agent's id/label + the files it had edited; `glasses3d/data/handoff_20261007/interrupted_agents.json` = each interrupted agent's last texts and last 14
tool calls; `glasses3d/data/handoff_20261007/<workflow>-<run>.js` = every workflow script of the session; `glasses3d/data/handoff_20261007/scratchpad_copy/` (4.7 GB) = the session scratchpad
(agents' helper scripts, e.g. `wp6fix3/patch_*.py`, `probe_costs*.py`, `wide_search.py`; delete after the next session has verified
everything). Originals: journals `C:/Users/Shay/.claude/projects/C--Users-Shay-PycharmProjects-lenses/9e8d13e8-4744-4df5-9e5f-66ecbeca8f3e/
subagents/workflows/<run>/journal.jsonl` + `agent-<id>.jsonl`. `Workflow({resumeFromRunId})` does NOT work from a new session: continue
each with a fresh, smaller workflow that is given the completed results (from results_*.json) and the section-4 file group.

**3.1 `g3d-wp5-step6-fixes` (run wf_730bf6ec-6cf; results_wp5_step6_fixes.json).** Input = the 12 findings of the step-6 final review
(`glasses3d/data/handoff_20261007/step6_findings.json`: 1 major + 11 minor; full review `glasses3d/data/handoff_20261007/step6_review.json`). Its only agent `fix:r1` (a3274dbb9c5c860b4, 207
tool calls) HAD FINISHED EDITING and was waiting on the full WP5 suite it launched (log under its own basetemp; the test running at the
stop was `test_bake_with_flatten_and_level_reads_back`, started 10:32) when it was stopped. Files it edited (on top of the uncommitted step-6
changes): `tests/photoprint/{ar_levels,ar_calibrate,correction_proof,printproof,step6_proof,test_step6}.py`, `tests/instruments/print_metrics.py`,
`g3d_host/photoprint/{fill,__init__}.py`, `plan/PORT_REVIEW.md`; new data under `glasses3d/data/wp5/step6_fixes/` (if present). Its
structured report was never returned, so NOTHING about these edits is verified. TO DO: (1) `git diff` group A and read every change
against the 12 findings (expected: the major rewritten per the stored data - the -x temple at yaw +45 is INSIDE the window, the row
fails on its brow group graded from photo 03's 9 % side-view brow pixels reweighted to 49 %, option C was fitted to that row; a
minimum photo-side group share excluded from verdicts/fits; provenance json; per-region temple rows; tests that check what they claim;
pool + progress in step6_proof; x-sign SIDES; unlevel convergence report); (2) prove print.png and `data/wp5/tooltest/
q001_painted_TOOLTEST.glb` are byte-identical to before (sha256 of print.png in `data/wp5/ft1119_bake/report.json` / bake.json and the
GLB sha 17d60488...); (3) run the full WP5 suite WITHOUT a timeout wrapper, log to a file: `glasses3d/.venv/Scripts/python.exe -m pytest
-rfE -v -p no:cacheprovider --basetemp=C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/pytest_tmp/<new> glasses3d/tests/photoprint
glasses3d/tests/test_print_blender.py` (~46 min alone; 241 passed + 1 xfailed `test_x3d_periodic_wrong_match_is_refused` before the
fixes); (4) one independent re-review of the fixes + a provenance audit (every number in the step-6 PORT_REVIEW sections has a stored
file); (5) commit group A.

**3.2 `g3d-wp5-all-angles` (run wf_81098786-301; results_wp5_all_angles.json).** Completed: step6-review (-> 3.1), build, verify r1 x3,
fix r1, verify r2 x3 (owner-eye r2: "C cannot be told apart from A" = major; code-review r2 ok; pose-identity r2 in the results file).
Interrupted: `fix:r2` (a6be2097f2c203ba6, 100 tool calls) had EDITED `tests/photoprint/all_angles.py` + `test_all_angles.py` (a `wide`
camera-search flag in refine_check; measure runs both checks in one pool; FIX_ROUND_2 summary block), ran unit tests + pyflakes, recorded
sheet hashes, and was re-running `python -B -u -m glasses3d.tests.photoprint.all_angles --stage measure` (then sheets, summary) when
stopped (`data/wp5/all_angles/fix2_wide_search.log`, `fix2_npm_test.log`, `fix2_look/`). TO DO: run `pytest glasses3d/tests/photoprint/
test_all_angles.py`, `cd glasses3d/arview && npm test` (2 capture-stall tests flaked under machine load, pass alone 5/5), re-run the
full pipeline `python -B -m glasses3d.tests.photoprint.all_angles` ONLY if the owner still needs sheets (the sent ones are valid), prove
the default pose sets byte-identical (`data/wp5/all_angles/byte_identity/`), one code review of the arview change, commit group B.

**3.3 `g3d-wp6-rediagnose` (run wf_5dce193d-796; results_wp6_rediagnose.json).** Completed: `capability` (VERDICT: TOOL OK - with
hand-traced parameters `g3d.faceted_temple` puts the creases where the photos show them; frozen crease gate R2 passes; four gates fail
for reasons outside the tool: two rule-set questions only the owner can decide + a level/thickness disagreement between views), `trace`
(fit-4 relief cue = crease brightness steps; STOP-I instrument fix, PRADA decals in arview, hinge warnings fixed; automatic relief r_C
1.46 vs hand 3.4 -> proof stopped at STOP-C, `data/wp6/logs/proof_fit4.log` 09:13; relief_rules.py unchanged, sha 7c22eab7...), `review:1`
(3 majors: the hand trace's R4 pass was graded against an unfitted control slab_of(spec_H); the hand r_C 3.40 is the largest relief the
front bound allows, not a photo measurement; S1's crease guides are much better than the real ones so S1 cannot detect the
identifiability failure). Interrupted: `fix:1` (a62c619b5164fb6d2, 136 tool calls) had edited `g3d_host/relief_cue.py`,
`g3d_host/relief_fit.py`, `tests/proofs/{wp6_prada,wp6_prada_stages,wp6_s1_floor_check}.py`, `tests/{test_temple_relief_cue,
test_temple_design_checks}.py` (it wrote and then removed `tests/proofs/wp6_s1_lines_diagnostic.py`; it was about to "add tests" for the
new R2 by-crease naming in stage_gates). It re-ran the proof fresh (10:18-10:36, `data/wp6/logs/proof_fix3.log`, outputs in
`data/wp6/proof/`): **result STOP-I: "S1 cannot recover a quantity that R2 / R3 read (frozen table wp6_prada_s1_dependencies.json); OPEN
OWNER DECISION: waive with --owner-waiver KEY=TEXT or keep the stop"**; wrote `data/wp6/s1_floor_check.json` + `s1_theta_check.json`
(10:39); archived the previous hand-traced outputs to `data/wp6/logs/handtraced_before_fix3/` and started a hand-traced re-run with a
strict r_C stability rule, which my stop KILLED: `data/wp6/logs/handtraced_fix3.log` ends "result: TRACE crashed / exit 1" at 329 s and
`data/wp6/handtraced/handtraced.json` (10:50:53) records that crashed run - IGNORE it; the last good hand-traced outputs are in
`data/wp6/logs/handtraced_before_fix3/` and `data/wp6/handtraced_run1_zone80/`. TO DO: read fix:1's edits (git diff group C), add the
missing tests, re-run `python -u -B -m glasses3d.tests.proofs.wp6_handtraced` (pool of ~14 sized to free memory), run the WP6 tests
(`glasses3d/tests/test_temple_*.py` + the proofs' unit tests), one review round, then the fresh full proof into `data/wp6/proof2`. Then
DECIDE YOURSELF (the owner delegated it ~11:10: "review the issue of wp6 and decide by itself on what to do"): (a) the STOP-I
waiver-or-keep and (b) the two rule-set questions from `capability` (read them in `glasses3d/data/handoff_20261007/results_wp6_rediagnose.json`, `capability` result,
and WP6_DESIGN). Decide on evidence, not convenience: never loosen `relief_rules.py` thresholds to make a pass (frozen; sha 7c22eab7...),
never tune toward a pass; a waiver is acceptable only if the evidence shows the stopped quantity cannot be measured from these photos AND
the decision is visible to the owner in AR. Record the decision, the evidence and the alternatives in this file (a dated WP6 entry) and in
the proof record (the `--owner-waiver KEY=TEXT` text must say "decided by Claude under the owner's delegation of 2026-10-07"); then tell
the owner the outcome in plain words with labelled images (photo | tool test | delivered; side/top/rear) - inform, do not ask. Commit group C once its tests pass (the code, not a verdict).
`data/wp6/logs/pytest_fit4_parallel.log` showed an F at 09:18 during a memory-exhaustion window: rerun before believing it.

**3.4 `g3d-wp2-stages-ab` (run wf_32fc96f9-1c5; results_wp2_stages_ab.json).** Completed: `impl:A` (324 passed in tests/test_wp2_*.py in
1 min 55 s; restore fingerprints equal on synthetic/Prada/q001; safe-mode corpus 0 false positives over 214 snippets (68 pass, 146
refused, all on file-I/O lines); accepted-run replays Prada 19/19 etc.), `review:A:1` (0 blocker/major, 1 minor), `impl:B` (camera
parity, render, pick, compare, fit_cameras; its report is in the results file). Interrupted: `review:B:1` (a85c6e082eb3b7325, 33 tool
calls, REVIEW ONLY - it edited nothing in the repo; its scratch `C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp2-review-B/parity_*.py`
was mid independent parity check and reading the exporter's viewport/render mismatch logic). TO DO: run `cd glasses3d &&
.venv/Scripts/python -B -m pytest -q tests/test_wp2_*.py`, re-run the stage-B review (stop rules: parity <= 0.01 px analytic / <= 0.1 px
rendered; compare reproduces basis-01 within 0.001 IoU), fix majors, commit group D.

**3.5 `g3d-wp2-stage-c` (run wf_91f8f2b9-ed5; results_wp2_stage_c.json).** Completed: `impl:money` (Prada replay job 8c82d986 run
20261001T144107-ad29ba, 59 recorded usages, $18 approval, through the real AsyncOpenAI client + httpx2 hooks + BudgetGuard + JobLedger:
row 1 reserved 719,438 micro-USD; settled total 14,261,424 micro-USD EXACTLY; 59 count calls = 59 creates = 59 rows), `impl:estimate`,
`impl:loop`, `verify:network:r1` (ok), `verify:money:r1` (2 majors: the tamper check misses truncation of a closed run's budget.json
rows; a transport-settled row can be rewritten as an owner no-charge reconcile and still validate), `verify:design:r1` (4 majors: the
§9 implementation receipt (`agent/snapshot.py`) is not built and §13's snapshot test is missing; the paid entry point never recomputes the
request sha from the job; the §7 two-way tool contract is optional (contract=None, incl. production, skips it); psutil is used by the
paid-run lock but not pinned). Interrupted: `fix:r1` (ad623ca78fcbfba2a, 37 tool calls) was still READING (Studio run_snapshot.py,
PORT_REVIEW snapshot notes, file counts for the receipt, psutil wheel/cache) - it edited NOTHING. TO DO: fix those 6 majors (+ minors in
the results file), pin psutil in `glasses3d/requirements-agent.txt` with its PyPI sha256 (installs are pre-approved from pypi.org; the
venv already has psutil transitively - pin the installed version), re-run verify (money, network, design), `pytest -q glasses3d/tests/agent`
(basetemp under data/pytest_tmp), confirm `tests/agent/data/*.json` hold token counts only (repo PUBLIC), commit group E.

### 4. Uncommitted files at the stop, by owner (commit each group ONLY with explicit paths; never `git add -A`/`.`)
Full backup of all of it: `glasses3d/data/handoff_20261007/uncommitted_tracked.patch` (`git diff HEAD --binary`, 445 KB) + `glasses3d/data/handoff_20261007/uncommitted_untracked.tar.gz` (72
untracked files) taken at HEAD `2ba35a8`, listing in `glasses3d/data/handoff_20261007/git_status_at_stop.txt` (112 lines). Restore if needed with `git apply` / `tar -xzf`.
- **A. WP5 step 6 (+ step6-fixes, unverified):** `glasses3d/g3d_host/photoprint/{__init__,fill,highlights,labels,project,shading}.py`,
  `glasses3d/plan/PORT_REVIEW.md`, `glasses3d/tests/instruments/print_metrics.py`, `glasses3d/tests/photoprint/{ar_calibrate,ar_levels,
  bake_ft1119,correction_proof,print_tooltest,printproof,test_correction_round,test_print_bake}.py`, new `glasses3d/tests/photoprint/
  {step6_proof,test_step6}.py`, `glasses3d/tests/test_print_blender.py`.
- **B. all-angles / arview explicit-camera no-face capture:** `glasses3d/arview/{README.md,qa/README.md,qa/capture-cli.test.mjs,qa/capture.mjs,
  qa/capture/cli.ts,qa/capture/page.ts,src/render/renderer.ts}`, new `glasses3d/arview/{qa/capture-cameras.test.mjs,qa/capture/cameras.ts,
  tests/capture-cameras.test.ts}`, new `glasses3d/tests/photoprint/{all_angles,test_all_angles}.py`. (Never touch `ar/`.)
- **C. WP6 re-diagnosis:** `glasses3d/g3d/temple/{__init__,build,fixtures,measure}.py`, `glasses3d/g3d/temple_bpy.py`,
  `glasses3d/g3d_host/{relief_fit,relief_views}.py`, new `glasses3d/g3d_host/{relief_cue,relief_hinge}.py`, `glasses3d/tests/proofs/
  {wp6_bpy_run,wp6_prada,wp6_prada_stages,wp6_s1_theta_check}.py`, `glasses3d/tests/proofs/{wp6_prada_guides,wp6_prada_s1_dependencies}.json`,
  new `glasses3d/tests/proofs/{wp6_fit4_diagnostic,wp6_handtraced,wp6_s1_floor_check,wp6_theta_frame}.py`, `glasses3d/tests/
  {test_temple_build,test_temple_design_checks,test_temple_slab_control}.py`, new `glasses3d/tests/test_temple_relief_cue.py`.
  (`wp6_prada_s1_dependencies.json` is the FROZEN dependency table STOP-I reads - check the diff did not loosen it.)
- **D. WP2 stages A-B:** new `glasses3d/agent/{__init__,astra_corpus,journal,procs,worker_client,seeing}.py`, new `glasses3d/g3d/{checkpoint,
  lib,render,rpc,safe_mode,worker}.py`, new `glasses3d/g3d_host/{blender_camera,compare}.py`, new `glasses3d/tests/blender/{wp2_camera_parity,
  wp2_synthetic_scene}.py`, new `glasses3d/tests/test_wp2_{camera,camera_parity,checkpoint,compare,corpus,fit_cameras,pick,procs,rpc,
  safe_mode,seeing,worker}.py`.
- **E. WP2 stage C:** new `glasses3d/agent/{approvals,budget,estimate,loop,offline,pricing,receipts,replay,session_inputs}.py`, new
  `glasses3d/tests/agent/**` (`__init__.py`, `conftest.py`, `support.py`, `loop_support.py`, `data/prada-8c82-tokens.json`,
  `data/studio-astra-13-tokens.json`, `test_*.py`).
- Ownership of `agent/__init__.py`, `agent/seeing.py`, `agent/session_inputs.py` is inferred: confirm against impl:A/impl:B/impl:loop
  `files` lists in results_*.json before committing. Any other file belongs to whichever results file lists it; never guess.

### 5. Next steps after the owner's verdict (in order; each with its $0 proof, committed on `glasses3d`, no push)
1. Finish 3.1-3.5 and commit groups A-E (each verified as listed).
2. WP5 next step (section 2), owner judges by eye; then G1: the print as a library tool.
3. WP6: decide the STOP-I waiver-or-keep and the two rule-set questions YOURSELF (owner delegation ~11:10, section 3.3) and report the
   outcome to the owner with images; if the tool cannot make facets even
   with good parameters, stop and report (PLAN WP6 stop rule). Then G2 prerequisites: `g3d_temple` in the exporter allowlist, temple ops in
   `g3d/__init__` + `g3d/cli.py`, Prada coupling removed; check_relief must receive photo crease counts (else SLAB fires on smooth temples)
   and FRONT_BOUND merged into the flags (WP7_DESIGN open items).
4. WP2 stages D (tool registry + wrappers; WP5/WP6 gated), E (jobs CLI, brief, notes, owner ledger + migration, watchdog), F ($0 proofs A + B,
   all 16 criteria of WP2_DESIGN §14; stop rule: proof A total off by even 1 micro-USD). Fix WP2_DESIGN §11's wrong sentence "deliver refuses
   only on §2" (export refusals also stop it).
5. WP7 implementation (`agent/instructions.py` + the contract tests of WP7_DESIGN §2) once the D registry exists.
6. WP8 (paid): ask the owner AGAIN (they said "let the new model ask me again") the money questions + WP7_DESIGN §4.2 #1 ("sizes from the specifications; shape, colour and finish from the
   photos") and #2 (arview lensenv); then one capped smoke turn, then Prada, FT1119, the known-good product, then 3 unseen products.

### 6. Owner decisions and preferences (all also in Claude memory)
- The owner judges by eye in AR; Claude makes all technical/engineering decisions and records them here; ask the owner ONE plain question
  with labelled images only when it is truly theirs (look right? continue/stop? money?). Lead with the verdict; unvarnished facts.
- Always say "the code/tool does X"; Astra builds models, Claude builds tools; $0 tests on COPIES are tool tests, never delivered.
- Machine: Intel Core Ultra 9 285H, 16 cores, Arc 140T GPU, 31 GB RAM + ~24 GB pagefile (55.7 GB commit), dedicated: use it heavily, but
  MEMORY is the ceiling with several pools at once (each photoprint/scipy worker commits ~1.2 GB; at 10:48 free commit was 4.3 GB with five
  workflows running): read free commit (`(Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory`, KB) before a pool and size workers to
  ~free_commit_GB / 1.3; a MemoryError / "paging file is too small" / BrokenProcessPool under load is an environment fault, not a code
  defect; never run duplicate pool-heavy jobs in parallel. NEVER kill a healthy slow run unless the owner asks (they asked at 10:52).
  Check liveness with full timestamps. Workers opt out of EcoQoS.
- Package installs: pre-approved from safe sources (official PyPI/npm, exact pins with sha256 hashes, recorded). Paid API calls: owner OK per run.
- No web research by Claude (the owner runs ChatGPT research when asked; pasted research is data).
- Verdicts (`glasses3d/data/ledger/owner_verdicts.jsonl`, sha256-bound): Tom Ford FT1123 (0e77e11e...), Oakley OO9208 v2 (2c46877a...), INVU
  IA22403B v2 (4a44a6ac...) = "perfect" (re-judged on arview 2026-10-06). Prada = OPEN FAILURE (its old "looks great!" predates noticing
  the facets, and the old runtime drew its temples shortened).
- Every product has six real photos: front, back, left, right, top, angled (owner-generated views are real). FT1119: 05 front, 02 back,
  03 right side (wearer's right, -x temple outer), 06 left side from ~19 deg above (+x temple outer + -x inner print), 04 top, 01 angled.
  Photos folder: `C:/Users/Shay/OneDrive/Desktop/erroca/images/Tom Ford FTS FT1119-D 52A 47/` (03 = "ChatGPT Image Oct 1, 2026, 07_41_47
  PM.png"). Astra's q001 names temples by the VIEWER's side (Temple_Left = -x): tools use the x sign, never names. +x = the wearer's LEFT.
- Repo shayashertlv/lenses is PUBLIC; pushing main deploys to Railway. Work on branch `glasses3d`; never push without the owner's OK.
  Never commit photos, job data, renders, GLBs, .blend or .env (`glasses3d/data/` is git-ignored). Never modify `ar/` or the root app.
  Never `git stash apply/pop`. The OpenAI key is in `automation/.env`: never read or print it.

### 7. Gotchas learned
Long Bash heredocs get mangled (write scripts with the Write tool; short heredocs < ~40 lines are fine); liveness checks must compare
full timestamps; agents may sweep other workflows' files into a commit (stage paths explicitly); the session's working directory jumps
between folders on its own (always use absolute paths); a `timeout` wrapper on the 46-min WP5 suite cuts it off (run it without one, log
to a file; a `| tail` pipe hides pytest's exit code); the SDK install added a pytest `anyio` plugin (harmless); a workflow launched with
`args` must receive the real data (a placeholder string reached the agents once; it was stopped within a minute, no edits); older
calibration / sheet generations are kept under `data/wp5/pre_*` and `data/wp5/tooltest/pre_*` and the all-angles rounds under
`data/wp5/all_angles/round0|round1` (nothing deleted); WP6 temple ops are not yet in `g3d/cli.py`, `g3d/__init__.py` or the exporter allowlist.

## 2026-10-05: WP0 (setup, ignore rules, port review)

Done:
- Branch `glasses3d` created from main 7e25722 (no push).
- `.gitignore`: the PLAN block, plus a deliberate re-include of `/glasses3d/tests/fixtures/**` (the global `*.png`/`*.jpg`
  rules otherwise hide fixtures) and a re-ignore of `*.glb`/`*.blend`/`*.blend1` inside fixtures, so no model binary can
  enter Git. Verified with `git check-ignore -v --no-index` on 21 sample paths (data/, output/, dist/, qa/output/,
  .venv/, venv/, glb, blend, blend1, webp, pkl, npz, arview/modeling, arview/site; fixtures png/jpg included; fixture
  glb/blend ignored; plain .py/.ts not ignored; .env ignored).
- `glasses3d/.venv` (Python 3.12.10): numpy 2.5.3, scipy 1.18.1, opencv-python 5.0.0.93, scikit-image 0.26.0,
  pillow 12.3.0, pytest 9.1.1, **PyPatchMatch 2.0.1 (Windows wheel `pypatchmatch-2.0.1-py3-none-win_amd64.whl`)**.
  Verified: `patchmatch.inpaint(image, mask, *, global_mask=None, patch_size=15)` fills a hole inside a uniform
  (200,80,40) region with exactly (200,80,40) and leaves every pixel outside the mask byte-identical.
- Reference data copied from `C:/Users/Shay/PycharmProjects/glasses3d-reference/` to `glasses3d/data/reference/`
  (ignored): projection/ 2.4 MB, junction/ 29 MB.
- Prototype constants repointed relative to `glasses3d/` (`Path(__file__).parents[3]`): make_uv_test.py `OLD`/`HERE`
  (HERE = `data/reference/projection/rerun/`), probe_fit.py and probe_project.py `HERE`, run.mjs `AR`
  (= `../../../../ar`), and all three ar_capture configs (`output` → `data/reference/ar_capture/<name>`; case paths →
  `data/reference/junction/copies/`). config-ab4.json also pointed at a dead temp folder; fixed too. The prototypes
  still import `automation/bsa` (prototype-only; the pipeline never will).

$0 proof (WP0.4): `make_uv_test.py` re-run from scratch (no cached cameras), 4 min:
- coverage Brow_L 0.727, Brow_R 0.712, Temple_L 0.833, Temple_R 0.570: identical to PLAN §1;
- re-fitted `cam_reference-0{2,3,4}.png.pkl` and `ft1119_uv_sources.png` byte-identical to the originals
  (the fit is deterministic);
- fit quality (WP3b baseline): ref-02 back IoU 0.897 / contour 5.46 px; ref-03 side: 'right' prior IoU 0.738 /
  11.47 px (the 'left' prior gives 0.384 / 34.1 px); ref-04 top: 'top' IoU 0.830 / 5.24 px ('topb' 0.699 / 10.6 px);
  earlier probe cameras: ref-05 front 0.898, ref-01 angled 0.869, ref-06 (elevated side, 'left' prior) 0.709.

- **PORT_REVIEW.md** (WP0.5): 17-agent review of the newest Studio (`source-before/blender_agent`, 52 files,
  12.5k lines): keep 6 / rewrite 23 / drop 23, five verdicts corrected by adversarial second reviewers; 53 test files
  reconciled; docs reviewed (facts carried forward); needs nothing provides listed. Full evidence in
  `plan/port_review/studio.json`. Audit-flagged gates (construction labels, selection citation gates, correction-family
  caps, source_field two-colour ramp) all dropped. No code copied yet (copies land in WP1/WP2/WP3).
- **Blender connection decision (with evidence, PORT_REVIEW §1): no MCP server; glasses3d owns a headless Blender
  worker** (`blender -b`, ephemeral-port JSON RPC, all Astra tools host-side). The community mcp-for-blender the
  Studio used bans numpy but allows bpy file I/O (the opposite of WP2's safe mode), refuses `-b`, and drops calls at
  180 s while Blender keeps running; the Lab MCP has no sandbox hook, ignores render output paths and is GPL outside
  the repo; both default to port 9876.

Facts recorded for owner decisions (PORT_REVIEW §2b): every praised Studio run used **max** effort (PLAN §2
recommends high); accepted FT1123 GLB = tomford-scratch-002/final-champagne sha 0e77e11e…; the description bug is
confirmed (studio_jobs.py:224/405-426); owner verdicts also depended on viewer settings (lens-reflection multiplier,
width, clip).

WP3 groundwork found while WP0 ran (not yet committed; lands with WP3):
- Studio scenes (8c82 agent/scene.blend, 3b8a original/scene.blend): METRIC, scale_length 0.001 (millimetres), scene
  property `blender_agent_axes` = '+Y up; +Z front'; **origin marker = scene property `mdl_bridge_underside`** (float[3]);
  every mesh has `partRole` and `LENSES_part_visibility`; Prada's scene has an "Inspection only | excluded from
  delivery" collection (camera + 3 lights).
- `g3d_host/glb.py`, `lens_id.py`, `spec.py` (validate_spec, exactly the §2 rows) + 47 passing tests. On real files:
  q001, Prada, INVU pass; ar_v4 crystal r9 fails ONLY the origin row (bridge underside at y +10.23 mm), which WP1's
  report must say.

Next step: WP1 (arview). The WP1 read-only analysis workflow (per-file fork verdicts, throw-site classification,
lens-id and capture design) is running.

## 2026-10-05 (later): WP1 / WP3 / WP3b in parallel (state for a resuming session)

- WP1 analysis (14 agents) -> `plan/port_review/arview.json`, PORT_REVIEW §7, binding decisions in `plan/WP1_DESIGN.md`.
  Stage A (verbatim copy, 135 files kept, 93 dropped) committed 1870359; `npm ci` done in `glasses3d/arview`; baseline was
  19 TS errors (imports of dropped modules). Stages B (build), C (lens id), D1 (load front-end + acceptance policy), D2
  (temples/clip/continuity/fit scale) run as one sequential workflow (implement -> adversarial review -> fix, max 2 rounds).
  Remaining after it: stage E (capture CLI), F (webcam page), G ($0 proof incl. r9 refusal recorded on main's ar/).
- WP3: `g3d/export_glb.py` (+ glb_patch, cli), `g3d_host/blender.py` (export_copy, export_and_validate), validator. $0 proof
  passed before review (flat facets, clearcoat 0.8, 64x48 embedded image, q001 + Prada scenes export and pass). The review
  (28 agents) reproduced real defects: upside-down/roll/pitch models passed; shields false-failed; unmeasurable origin
  passed; instancing ignored; validator crashed on bad indices; exporter corrupted custom normals on mirrored objects, used
  viewport evaluation, ignored excluded collections, lost relative textures silently; facet test could not detect a 40°
  re-smooth; texture test had no UVs. Calibrated thresholds (8 real models) now in WP1_DESIGN §1. Fix + re-verify running.
- WP3b: porting automation/bsa cameras into `g3d_host/` (camera, raster, cameras, matte, texture_utils, fitmesh,
  fit_views); acceptance = reproduce the prototype's three FT1119 fits exactly, then measure all 12 reference views
  (6 FT1119 vs q001, 6 Prada vs the delivery), report IoU and contour error in px and mm, then ASK THE OWNER for the WP5
  registration threshold. open3d 0.20.0 installed in the venv.

## 2026-10-05: WP3 done (6acfc5a)

- Exporter `g3d/export_glb.py` + validator `g3d_host/spec.py` (see the commit message for the full list).
- $0 proof: flat facets survive (12-gon temple, per-corner NORMAL directions read back), smooth parts stay smooth,
  sharp-edge / weighted-normal / mirrored / non-uniformly-scaled custom normals match Blender's evaluated corner normals
  within 0.025°, clearcoat 0.8 (roughness 0.05), UV-mapped 64x48 embedded image, transmission; the q001 and Prada
  scene.blend files export and pass validate_spec with their sha256 unchanged; all 41 unique Studio/live GLBs pass;
  ar_v4 r9 fails only the origin row (bridge underside +10.23 mm).
- Adversarial review: 28 agents, 25 reproduced defects (upside-down/roll/pitch models passed, shields false-failed,
  crashes on bad indices, mirrored custom normals corrupted, viewport-vs-render evaluation, excluded collections,
  relative textures lost, weak facet/texture tests). All fixed; re-verified by a separate agent; mutation tests 13/13
  killed. Suite: 228 passed, 1 skipped.
- Orientation thresholds (hard; calibrated on 8 real models): temple roots above the lens centre (real +4.0..+18.0 mm),
  left/right lens halves within 3 mm (real <= 0.16), temple slope < 15° (real -9.4..+4.7). Origin: frame + lens
  triangles sliced at x in {-1..1} mm (shields), x judged on the lens box; unmeasurable origin fails.
- Known limit: Draco/meshopt geometry is not decoded by the Python reader; affected rows become 'unverified'
  warnings only where a compressed primitive's POSITION bounds could change the result.
- Convention: KHR_materials_volume lengths are authored in METRES (Studio convention); validate_spec warns above 0.01.

## 2026-10-05: WP3b port + $0 proof done (waiting for the owner)

- Ported (PORT_REVIEW §9): `g3d_host/camera.py`, `raster.py` (open3d ray casting), `cameras.py` (ViewFit, priors incl.
  first-class top/topb and top_oblique_left/right, contour ICP, Powell, native_metrics), `matte.py` (quadratic
  background), `texture_utils.py` (de00, quantile_gain, rasterize_triangles, nearest_fill, sample_bilinear,
  visible_points), `fitmesh.py`, `fit_views.py` (driver + CLI). No runtime import of automation/reconstruction
  (a test enforces it).
- Exact reproduction: under **scipy 1.16.0 (now pinned)** the ported production path reproduces the prototype's three
  FT1119 cameras on all 21 fields within 1e-6. scipy 1.18.1 drifts up to 0.19° / 1.07 px (traced to a ~1e-8
  least_squares step difference amplified by accept/reject; the original automation code drifts identically).
- 12-view baseline (mean contour mm / p90 mm; px ÷ px/mm at the front pivot), outputs `data/wp3b/`:
  FT1119 vs q001: front 0.66/1.46, rear 0.68/1.53, top 0.69/1.84, angled 0.79/1.87, elevated side 1.05/2.81,
  side 1.44/6.16 (model shape differs: brow ~4 mm higher in the photo, fit turns to show the far temple).
  Prada vs delivery: front 0.26/0.59, top 1.24/3.33, angled 1.27/4.16, side-05 1.99/4.22, rear 2.76/11.4,
  side-01 5.58/20.6 (matte keeps floor reflections and fills the gap between temples: an upper bound; delivered
  temples also differ from the photos = the WP6 failure).
- Ref-06 (elevated side): PLAN §1's "needs a proper prior" is wrong: the photo sits at ~19° pitch; with seeds there
  the fit equals the 'left' result (IoU 0.708). The 0.71 ceiling comes from thin temples (contour error 1.05 mm).
- **Owner question (blocks WP3b "done" and WP5's stop rule): the registration threshold.** Recommended: mean contour
  <= 1.0 mm and p90 <= 2.5 mm per view used for projection (FT1119: front, rear, top, angled qualify).

## 2026-10-05: WP1 stages B-D2 committed (960ad54)

tsc 0 errors, node tests 476/476, qa 18/18, vite build OK; ar/ untouched. Each stage implemented, adversarially reviewed,
fixed. Stage D1's round-2 fix (default-scene-only parse via defaultSceneDocument/packGlb; webcam page listing failed §2
rows) was not re-reviewed inside that workflow: stage R of g3d-wp1-capture-proof reviews it and aligns arview's origin-x
and orientation checks with the fixed validator (agreement test on shared fixtures). Remaining WP1: R, E (capture CLI),
F (webcam page `arview serve`), G ($0 proof: r9 refusal on main's ar/, captures of r9 / q001 / Prada / mm copy / renamed
multi-mesh copy / temples-removed copy, Python-vs-arview agreement table, q001 vs prototype seated junction).
PORT_REVIEW §7: `tests/lens-appearance.test.ts` row still says keep (dropped in stage B with its module) - fix at the WP1 commit.

## 2026-10-05: WP4 design (plan/WP4_DESIGN.md)

Judge panel: 3 independent designs (robust 45, minimal 47, physical 42 of 60), synthesized on the minimal base. Measured facts
that decided it: all four real lenses (FT1119 q001, Prada, Oakley, INVU) are closed manifold solids with a back cap translated
along -Z and one wall ring; FT and INVU caps are toric (no sphere growth); §3 defaults as hard thresholds would fail accepted
models (FT eyewire capture 0.40 vs 0.45; Prada 65% of stations reach 0.8; INVU top rail 0.10), so pass = §3 lower bound,
target = §3 default, per station, growth only where the grown edge stays hidden; 42-45% of each shield edge is exposed and
never grown (Oakley must move 0 vertices). FT1119 browline: hide_eyewire with per-station lift (0.84 mm median) over x
11.6-56.2 mm; must match the prototype seated+rimsplit numbers (top edge inside 100%, band 0, rim split 78-102 deg).
Open risk the owner may want to know: under §3, Prada (~26% of nodes, <=0.45 mm) and INVU (~267 top nodes, <=0.9 mm) change
at HIDDEN stations inside opaque material, plus Prada's rim sharpening (intended rimsplit fix).

## 2026-10-05: WP5 STOPPED (stop rule) - report to the owner

$0 feasibility probe + 3 designs + judge (plan/WP5_DESIGN.md, verdict NO-GO for WP5 as PLAN §4 writes it). Scratch evidence:
C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp5/ (step1..4 JSON + side-by-side PNGs).
- With q001 geometry and the WP3b silhouette cameras, photo projection does NOT beat Astra's procedural texture on both
  held-out band-pass r (DoG sigma 1 px - 4 px) and gained deltaE00. Gained deltaE00 ties on views 01/04/05; band-pass r is
  above 2 sigma of the shift null only on view 04.
- Cause = registration: the brow print is 1.88-2.39 mm off between views 01 and 05 (the only pair whose print matches)
  although their silhouettes fit at 0.66-0.79 mm. ~2.5 mm is a rigid per-view offset (q001's shape biases the
  silhouette-fitted cameras), 0.6-0.8 mm non-rigid (geometry). Silhouette accuracy does NOT predict print registration.
- Needed: <= 0.8 mm print registration (0.5 mm with margin): registered, band-pass r 0.61-0.74 vs Astra 0.11-0.17; at
  0.83 mm 0.22-0.27; at 1.25 mm ~0.
- What works: a joint model (one camera per view + a bounded per-brow depth correction, silhouette guards, fit on even
  4 mm stripes, tested on odd ones): Brow_Right residual 0.00-0.21 mm, band-pass r 0.58-0.69 vs Astra 0.02-0.15, gained
  deltaE00 4.68-6.74 vs 5.50-12.76, both folds. Brow_Left and the temples still fail (~16% of the print area verified).
- The PLAN's "0.017 vs 0.066" baseline cannot be reproduced (its filter was never recorded).
- My WP3b recommendation (1.0 mm mean SILHOUETTE contour) is WITHDRAWN: it would pass views whose print is ~2 mm off.
- Owner decisions (WP5_DESIGN §12): D1 photometric print threshold 0.8 mm (target 0.5) replaces the silhouette threshold;
  D4 is WP5 acceptable with only Brow_Right pixel-verified (other regions printed from one anchor view, flagged, judged
  by eye in arview)? if not, WP5 stops on FT1119; D6 text handling (default: the decal carries brand text, photo text is
  filled). D2/D3/D5 are technical defaults.

### WP5 owner exchange (2026-10-05)
The owner found D1-D6 unclear ("I'm not really understanding what you want"). Correction of approach: D1, D2, D3, D5, D6 are
engineering choices and Claude takes them as recommended in WP5_DESIGN §12 (photometric print gate 0.8 mm / target 0.5;
per-(view, part) edge gate for colour donors; stripes-and-folds proof on registered regions; parallax used for sampling
only, discrepancy reported to Astra; brand text from the decal, photo text filled). The owner gets ONE question, with
visual evidence (data/evidence/wp5_right_brow.png, wp5_left_brow.png): continue building WP5 although only the
registered regions can be pixel-proven, the rest judged by eye in AR?

### WP5 resumed (2026-10-05, owner: "the images show promise, don't give up")
- Owner answered: continue WP5 (D4 = yes in substance). D1-D3, D5, D6 taken as engineering decisions (WP5_DESIGN §12).
- CORRECTION (owner was right): every FT1119 temple face is photographed. Image 03 (owner file "ChatGPT Image Oct 1, 2026,
  07_41_47 PM.png") = the wearer's RIGHT side (-X temple, outer face). Image 06 = the wearer's LEFT side from ~19° above
  (+X temple outer face, tip medallion) plus the -X temple's INNER face with "TOM FORD MADE IN ITALY CE". PLAN §1's "no photo
  shows its outer face, so mirror fill is needed" is wrong. Trap: Astra's q001 names temples by the VIEWER's side
  (Temple_Left = -X = wearer's right), so every tool must use the x sign, never part names.
- Model content note (for Astra, not hand-fixed): both photographed temple tips carry a gold medallion; q001 has one.
- Seams: the research solution (gain from same-surface overlaps, graph-cut seams inside near-best overlaps, multi-band
  blending) is in WP5_DESIGN §6.4; it needs aligned photos, which step 0 provides.
- Each product has six images: front, back, left, right, top, angled (FT1119: 05, 02, 06, 03, 04, 01).

## 2026-10-05: WP6 design (plan/WP6_DESIGN.md)

Panel: tool 36, measure 36, trace 39 (base). Measured on Prada: delivered temples are a slab (outer bulge <= 0.62 mm, inner face
flat); photos show 4 facets near the hinge (F1 top, F2 logo triangle, F3 front-lower, F4 rear-lower), flat inner face; facet
corners trace to 0.1-0.3 mm in 05 and 01 independently; relief (sideways offset) only to ~+-3 mm absolute (front photo bounds
|X| <= ~72.5 mm). New matte mode `glossy_floor` (removes floor reflections, only removes pixels): side-01 fit 5.58 -> 1.43 mm.
Rear camera stays unreliable unless fitted jointly with a per-photo hinge angle.
PLAN correction (technical, recorded for the owner): the 1.9-4.3 mm bulge band is kept as a REPORTED warning, not a pass gate,
because a thicker slab passes it; relief gates R1-R4 (single-plane share <= 0.5 vs delivered 0.81; crease normal change;
cross-view crease transfer within 0.7 mm; silhouette cost <= 0.1 mm) plus the plan's top-view and rear-envelope comparisons
decide, and a zero-relief slab control must fail them.

## 2026-10-05: WP1 $0 proof passes (checkpoint 6bd502e), closeout running

Independent checker looked at the images: r9 captured (translucent crystal, gold core, see-through lenses; origin +10.24 mm
reported) and refused on main's ar/ with "The rear-drop start must be forward of the accepted cap." (ar/src/render/rear-drop.ts:151,
as predicted, not :194); temples-removed copy -> "temple left/right missing" (stop rule holds); mm copy rescaled with warning;
arview == spec.py on 143 fabricated cases + real GLBs (49/49 rows on the proof models); junction: q001 lens top vs brow median
-0.27 mm with 18/24 gap stations and the pale eyewire line in 8/8 crops; seated copy +0.93 mm, 0 gaps, no line (matches
ab3-q1-vs-fixed.png); arview serve verified (COOP/COEP/CORP, camera policy, sha256 header).
Closeout (workflow g3d-wp1-closeout): Prada temple shafts drawn only to ~30-35 mm in yaw captures (investigate vs the Studio-era
runtime, fix), re-review the last unreviewed E/F fixes, flaky hair-schedule test, scale-invariant degenerate-triangle rule
(both validators), temples-missing reason text (both), junction decals on lenses, served fallback count, measured serve
bookkeeping, a stronger no-metadata arbitrary-names fixture. PLAN §1 correction: q001's lens top stops 0.09-0.46 mm below the
brow at 18/24 stations by triangle slicing (the plan's 0.2-0.9 mm included endpieces).

## 2026-10-06: WP5 stage 1 (registration) built; STOP A fired; continuing in single-source mode

Stage 1 code (g3d_host/photoprint: inputs, signals, regions, pose, edges, register, gate, check_print, report, procs, step0;
tests/instruments/print_metrics.py independent; tests/photoprint; tests/proofs/wp5_ft1119.py): 76 passed + 3 expected A0 failures.
FT1119 with q001: no region registers in both folds (Brow_Right closest: 0.28/1.76/0.66/1.22 mm; the joint fit is not unique,
starts end 3.3-11.2 mm apart); 0% pixel-verified. Area: partner views exist for 30.3%, single-view 35.8%, unseen 28.9%, end caps 5%.
The gate's decoy rule as written refused even perfect synthetic matches (instrument defect) and the A0 null-mean bound fails a
perfect render. ENGINEERING DECISIONS (owner asked not to be given technical choices; owner chose "continue, judge by eye"):
redesign the decoy null and fix the A0 bound (step 3); WP5 delivers SINGLE-SOURCE print (each region from its anchor photo, no
mixing of unregistered views, seams on creases, fill for unseen, B3 realism checks); strict pixel proof (B2) = diagnostic for
Astra, not a gate; owner judges in arview (B4). Told the owner plainly that 0% is pixel-verified.
Liveness note: a false stall alarm came from comparing times without dates; both workflows were alive.

## 2026-10-06: WP1 done (closeout)

- Prada temple cut in turned captures: cause = arm-spread pivot (rear-drop) took the first slice where the arm is <= 15 mm tall
  as the hinge; Prada's tall shield put it at z -73 mm, so the front half of each arm was never spread and sat inside the head
  proxy. The Studio-era runtime has the same pivot code (stash rear-drop.ts:173) and its own preview of the Prada delivery
  (preview/candidate__actual-ar__yaw-left/right.png) shows the same cut: the owner's "looks great!" was given on shortened
  temples in turned views. Fixed (hinge = thin-shaft boundary only within 15 mm behind the lens rear); arms now drawn to where
  they pass behind the ear (~-75 mm at yaw +-45; was -61). New report check `TEMPLE CUT` on turned captures.
- Fixed: flaky hair-schedule test (injected clock), scale-invariant degenerate-triangle rule (both validators), temples-missing
  reason (both validators; +Y up judged on the lens box when temples are missing), decals on lenses excluded from the
  junction metric, served-report fallback count, measured serve bookkeeping; a stripped no-metadata Prada copy (no extras, no
  names) captures correctly (lenses by transmission with warning, temples by position) after fixing a shared-material front
  defect it exposed.
- Re-proof: 6/7 items pass; the 7th (degenerate counts equal between q001 and its mm copy) is my own over-strict criterion
  for a warning-only metric: 206 vs 207 on one lens from float32 re-rounding at the threshold. Accepted, recorded.
- Tests: tsc 0; node 493/493; qa 104/104; spec 95/95; ar/ untouched.
- Risk noted (no change): the runtime's fixed 18 mm arm spread does not adapt to narrow frames (Prada arms enter the head
  proxy at -72..-76 mm, inside the behind-the-ear zone); changing it would depart from the Studio-era runtime.

### Owner note on the Prada verdict (2026-10-06)
The owner's Prada "looks great!" (10-01) was given BEFORE they noticed the faceted-temple problem (and, as WP1 found, on an
old runtime that drew the temples shortened in turned views). Prada is therefore NOT an accepted or known-good baseline: never
cite that verdict as evidence that a Prada result was acceptable. WP6 (faceted temples) remains a required fix, and WP8's done
criterion (owner accept on Prada's temples) stands.

### Owner re-judged the earlier accepts on arview (2026-10-06)
Shown in arview (default settings; sheets data/owner_review/*_review.png). Owner: "all three can be considered perfect." Recorded in data/ledger/owner_verdicts.jsonl, bound to the GLB sha256:
- Tom Ford FT1123-D 26E 49 (crystal): accept, sha256 0e77e11ee6ac91b0b5ddc9032e91a1cdd63f88667997868009ddd1ee2041cdd6
- Oakley OO9208 38 920844 (optical review v2): accept, sha256 2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688
- INVU IA22403B 99M (optical review v2): accept, sha256 4a44a6ac87eb92a317c9c74b4b5661f553acb5bc7c12d613777d3e8f55064df8
These are now confirmed known-good references (candidates for the WP8 regression product). Prada stays an open failure.

## 2026-10-06: WP4 build committed (e5fdf37); fixes and WP6 build running in parallel

WP4 proof: P0 core vs instrument within 0.075 mm at >= 99% of stations (FT, Prada, Oakley, INVU); P1 numpy vs open3d <= 1.2e-4 mm;
**P2 FT1119 browline 71/71** (top edge inside 100%, band 0, lift 1.029 mm, rim split 78-102 deg, 74 lens / 444 eyewire vertices moved,
only failing stations moved, validate_spec ok); P5 Oakley 0 moved and byte-identical, INVU 538 vertices = its 269 failing nodes, checks
pass after. Stop rule did not fire. Open (workflow g3d-wp4-fix): A Prada refused by the post-check (56.7 deg fold); B crystal binding check
misreads the exact bevel/groove; C growth undershoot (INVU min 0.425 vs 0.45 target; metal fixture 0.426); D nylor channel ends 0.205;
E refusal codes (narrow eyewire, notch); F chamfer/rounded/coarse edge deviation 0.014-0.031 vs 0.01 mm; G the Oakley top p95 <= 0.80
criterion contradicts design §7 (over-capture = warning) and the owner's 'perfect' verdict -> report-only (recorded reason).
WP6 build (workflow g3d-wp6-build) runs on disjoint files; integration into g3d/__init__.py, cli.py, export_glb.py is deferred.

## 2026-10-06: WP5 step 3 (host) committed (7ac7c8f)

g3d/printuv (strip + cap charts; q001: 2321x1577 at 15.95 tx/mm, 0 overlaps, 0 flips, anisotropy p95 1.66), photoprint steps 2-7 in
single-source mode, strip-shift decoy null (A0 passes on the intended rule; the literal per-view residual rule still lists three thin grazing
views 0.24-0.33 mm). FT1119 bake: photo 41.1% / PatchMatch 50.0% / mirror 2.8% / text fill 4.1% of the atlas; registered 0%. Checks: A1, A2,
A3, A7 pass; A4 fails on 3 of 4 parts (fill streaks), A5 fails at 1 texel, B3 2 of 10; A6/B5 (Blender) not built. Seen from new angles it
reads as the real havana (owner sent data/evidence/wp5_painted_vs_now.png). Defects to fix: PatchMatch wood-grain smears near hinges and on
long strips; photo 02's gold hinge baked into the endpiece backs (metal pixels pass the acetate colour test); pad-arm rivets on brow backs;
glints on curved edges; colour steps (-x temple from 03 warmer/lighter; brow-top band from 04 darker). Next: step 4 (workflow
g3d-wp5-step4): those fixes + the Blender side (print_uv, attach_print, exporter TEXCOORD/g3d_print_uv allowlist, A6, B5) + a tool-test GLB
copy captured in arview for the owner (B4).

## 2026-10-06: WP4 done

Fix round (workflow g3d-wp4-fix): A Prada now seats (fold cause = wall faces at run starts; growth steps limited to what a wall face
takes within 30 deg; post-check unchanged): every point >= 0.6038 after, max move 0.4386 mm, 190 vertices per lens all in failing
nodes, rim split p5 78.38 deg. B crystal check measures a declared V-bevel from the groove lips: 352/352. C correction pass reaches
targets (INVU node points >= 0.45). D nylor channel ends graded as TRANSITION. E notch -> E_CONCAVE gate. F front-edge fit 0.0001 mm.
G Oakley top p95 report-only (design §7 + owner's 'perfect').
Re-proof: P2 71/71; P3 34/40; P4 109/112; P5 31/32. Stop rule never fired (Oakley 0 moved; every moved set = its failing band).
ENGINEERING RULINGS on the remaining records (owner does not take technical decisions):
- P3.before_failing_where (one node at |x| 14.05 vs the design's approximate 10-14 band; describes the INPUT; instrument 100%) and
  P3.cover_unchanged (signed depth of frame behind the corner necessarily grows as the edge goes deeper; front/back covered share
  stays 100%) -> report-only, reasons above.
- Kept as recorded small shortfalls, not blocking: INVU 19 points 0.4489 vs 0.45 target and the metal fixture junction 0.4253 vs
  0.44 (point-level interpolation between nodes; both >= 0.12 mm above the 0.30 pass); the narrow-eyewire fixture seats partially
  (96 vertices, all in failing nodes) instead of refusing; 4 other P3 records listed in data/wp4/proof.json.
- Speed: INVU seat 12.7 s host vs 5 s budget (noted).
P6 (visual, arview junction captures of data/wp4/proof/glb/ft-before.glb vs ft-after.glb, yaw +-30 x pitch +-20): eyewire part-id
pixels in the 3 mm band above the lens top 85-304 in 8/8 crops before -> 0 in 0/8 after; 0 gap columns; brow lowest part 24/24
stations (was 4/24); lens top 0.72-0.80 mm inside the brow; white% 0-0.3 except the known far-side lens-highlight crop. Matches the
prototype seated+rimsplit. Owner image: data/evidence/wp4_junction_before_after.png.

## 2026-10-06: WP5 step 4 committed (10bf932); step 5 running

Step 4: photo metal/glint masks (endpiece-back metal 6.9->1.5% / 8.7->7.5%; rivets 0%), block quilting fill (A4 cannot pass even for a
real-print refill: 0.31-0.47 vs 0.15 limit; reported against the real-print baseline), gain field (region steps 3.81->2.54 dE00), mirrored
seam fill (A5 passes). Blender side: g3d.print_uv (splits only quads whose triangles fall in different charts: 77/2306 brow, 22/2114
temple quads; custom normals written back so shading changes <= 0.033 deg; remove_print_uv restores), attach_print/detach_print,
exporter g3d_print_uv + TEXCOORD; A6, B5, validate_spec pass on a tool-test copy of q001.
AR tool test (data/wp5/tooltest/owner_sheet.png, sent to the owner): better colour (hue ~20 deg like the photos vs Astra ~59) and temple
spots from turned angles; NOT right straight-on/worn: frame lighter and flatter than the photos (darkest decile L* 18-20 vs 12-17; median
24-31 vs 19-25: photo shading lit twice), brow fronts ~55% fill, streaks on the -x temple, light scratch marks on the endpieces.
Step 5 (workflow g3d-wp5-step5): shading removal + AR brightness calibration to the photos, brow-front photo share >= 80%, isotropic fill,
remove edge glints; then a new AR owner sheet. Default worker pool = cpu_count-2.

## 2026-10-06: WP6 tool test hit its stop rule (204f694); re-diagnosis running

Parallel fit: 14-worker pool, 621 s vs 6366 s serial, bit-identical (owner asked to use all cores; stopped the single-core run before the
owner said "don't force stop a good run" - nothing lost, code committed, stages resumed). Prada tool test (diagnostic past STOP-I):
outline better than the delivered GLB in side (contour 0.69/0.46 vs 1.55/1.10 mm), front, angled, top views; but the fitted relief is tiny
(A 1.04, B 0, C 2.22 mm) so the PRADA triangle facets barely show (R1 0.930 vs 0.5; R2 SHALLOW/FLAG; R3 worse than delivered); STOP-I =
hinge-angle recovery 2.86 vs 0.3 deg on the synthetic round trip; PRADA decals not drawn in arview on the tool-test temple; hinge-leaf
warnings. Re-diagnosis (PLAN WP6 stop rule): first build with HAND-TRACED good parameters (PLAN §0.2 tool test) to decide tool vs
tracing; then add a photo-measured relief cue to the fit, fix the theta instrument and the open review findings, re-run the proof fresh.

## 2026-10-07: WP5 step 5 committed (5ec9c25); step 6 and the WP2 design running

Step 5: shading removal kept (cross-photo agreement improves), AR level calibration (at photo 05's angle L* 15.6/23.8 vs 16.6/24.9),
brow-front photo share 81.8/79.0%, light marks reduced, 14-worker default. Still (AR close-ups sent to the owner): front pose lighter and
greyer than the photos (brow median 32.8 vs 22.0; chroma 2.9-4.1 vs 4.0-6.0), brow honey patches weak, -x temple ~7 L* light.
Step 6 (workflow g3d-wp5-step6): attribute contrast/chroma loss (texture vs AR material), calibrate contrast + chroma at the photos'
own angles, fix the -x temple level, residual endpiece marks. Material effects go to Astra as notes (Astra keeps material control).
WP2 design panel (workflow g3d-wp2-design -> plan/WP2_DESIGN.md) started: own Blender worker, safe mode, Agents SDK loop, budget/receipts,
jobs CLI, owner ledger, $0 fake-client proof. No paid call in WP2.

## 2026-10-07: WP2 design (plan/WP2_DESIGN.md)

Panel: loop 42 (base), worker 40, jobs 38. Facts: EEVEE renders headless on the Arc GPU (1.85 s, Suzanne 640x480), Cycles CPU 1.54 s;
glasses3d/.venv lacks openai/openai-agents/mcp (openai-agents 0.22.3 imports mcp at load) and nothing is cached: stage C needs the
owner's PyPI download OK. Pricing = the 161-line astra-only copy ($10 in / $1 cached / $12.5 cache write / $50 out per M; x2/x1.5 above
272k input). 17 Astra tools live; WP5 print and WP6 temples gated until wired (WP5 production call lives in a test driver; WP6 not in
cli/__init__/exporter allowlist). $0 proof: A = replay of a Prada session through the real SDK/guard/tools with its 59 recorded usages,
settled total must equal 14,261,424 micro-USD exactly; B = synthetic job covering timeouts, crash/resume, 429/520/incomplete, approvals,
stop file. Owner decisions (plain words, §17): PyPI download OK (needed for stage C); for WP8 only: budget cap per run (suggested $15),
effort (max like every praised run, or high), smoke cap $1. Engineering defaults recorded per run: output cap 16,384, max_turns 60.
