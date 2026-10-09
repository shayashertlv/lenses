# glasses3d progress

## Active resume — WP6 (2026-10-09)

WP6 offline repair resumed from `ad5105e` on branch `glasses3d`. WP5 remains closed by owner acceptance.
The saved S1 failure has now been reproduced and decomposed; do not repeat the initial diagnosis or start a
product fit. The current repair/checkpoint is described below. No paid run or push has occurred.

Read [WP6_HANDOFF.md](WP6_HANDOFF.md) fully, then [WP6_DESIGN.md](WP6_DESIGN.md). Use
[WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md) to begin the next session. The next agent is authorized to
continue offline WP6 diagnosis and repairs under unchanged gates. Independent contaminated-line calibration is
now recorded below. Next, isolate camera/hinge recovery error using known synthetic geometry while retaining
every original measured line and guide-error case. Masking alone does not resolve the saved failure.
The original measured S1 still fails; do not start another full product fit yet.
**STOP-I blocks acceptance and downstream promotion, not diagnosis or repairs intended to resolve it.**
KEEP R1 and every frozen R3 clause; no waiver. Do not repeat the resolved waiver decision.

## Current status

| Work | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b / WP4 | Complete and tested |
| A–E repairs, option-B promotion | Complete and verified; do not redo |
| WP5 reusable library G1 | Complete and verified at `f020b57` |
| WP5 | Complete by owner visual acceptance; observed-slope join + Poisson default at `4304c1d`. Historical numerical failures remain recorded. |
| WP6 | STOP-I still fails; saved S1 reproduced. Incomplete transfers fail closed. Contaminated-line controls support optional mask handling, not lower S1 errors. Camera recovery is under diagnosis. |
| WP2 | A–C verified; D–F and agent wrappers remain unfinished/gated |
| WP7 / WP8 | Design only / not started |

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
  a missing measured floor cannot fall back to ideal-line evidence. `FIT_VERSION` is now **fit-5**, so old fit-4
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
