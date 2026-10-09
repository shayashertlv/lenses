# glasses3d progress

## Active resume — WP6 (2026-10-09)

WP6 offline repair resumed from `ad5105e` on branch `glasses3d`. WP5 remains closed by owner acceptance.
The original saved S1 now passes with identical inputs, but a fresh full proof regenerating S1 from the newly
fitted delivered cameras still fails the instrument floor. The camera repair is not yet robust across both cases.
Do not repeat the initial diagnosis; continue from the new failure and its completed decomposition below.
The current repair/checkpoint is described below. No paid run or push has occurred.

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

## Coupled camera repair — 2026-10-09

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
