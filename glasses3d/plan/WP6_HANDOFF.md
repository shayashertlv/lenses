# WP6 handoff — resume diagnosis and repairs

WP5 is complete by the owner's 2026-10-08 visual acceptance. Commit `4304c1d` sets **observed-slope join +
Poisson** as the regular texture method. Its fresh production bake matches the approved texture byte-for-byte.
Code review/cleanup is committed at `d5e5917`. Offline WP6 repair resumed from the handoff commit `ad5105e`.
The saved S1 failure has been reproduced; the latest diagnosis and repair are recorded below.
Use [WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md) with PROGRESS's current Active resume.
WP6 has not passed its quality proof. Offline diagnosis and repairs remain authorized under unchanged gates.

## Current gate

**KEEP STOP-I, KEEP R1 and every frozen R3 clause.** The delegated decision was resolved on 2026-10-07.
Do not reopen the waiver question or use historical `open_owner_decisions` fields as a new instruction.
Canonical local evidence: `data/wp6/proof2/proof.json` and `data/wp6/proof2/delegated_decision.json`.
The decision record explains the rationale. Old JSON fields named `open_owner_decisions` and the old
STOP-I message predate that resolution. STOP-I continues to block acceptance/promotion; it does not block
authorized diagnosis and corrections aimed at making the unchanged proof valid and passable.

| Existing evidence | Result and implication |
|---|---|
| S1 parameter recovery | Outline 0.151779 mm, zone level 0.129544 mm, r_C 0.056853 mm, common-frame theta 0.190497°. These recoveries pass their recorded tolerances. |
| S1 true-model transfer with realistic guide error | **0.910923 mm**, above the frozen R3 absolute bound **0.7 mm**. The instrument floor still triggers STOP-I. Exact guide lines give 0.274915 mm. |
| R1 calibration | Synthetic faceted truths score 0.668/0.708 versus the frozen 0.5 maximum; this remains a calibration issue, not authority to raise the bound. |
| R3 relative bound | Delivered 0.693722 mm minus the required 0.5 mm gives 0.193722 mm, below the recorded exact-line truth floor. Both comparator clauses remain binding. |
| Hand-traced candidate | Fails G1/G2/R1/R3/R4. Its r_C=3.35 mm is a front-bound cap, not measured relief. No candidate is promoted. |

### Latest offline diagnosis and repair (2026-10-08)

The saved measured and exact-line values above reproduce to better than 1e-6 mm, without refitting. New evidence
is under `data/wp6/s1_floor_resume_20261008/`; earlier evidence is unchanged. At the saved fitted cameras:

| 05 source lines | 02 target lines | True-model transfer |
|---|---|---|
| Measured | Measured | 0.910923 mm |
| Measured | Exact diagnostic | 0.256265 mm |
| Exact diagnostic | Measured | 0.929841 mm |
| Exact diagnostic | Exact diagnostic | 0.274915 mm |

The angled guide endpoints carry about 3–7 px error. All 16 sign combinations of those same magnitudes were
replayed: at fitted cameras **6/16** exceed 0.7 mm (range **0.411172–0.945888 mm**); even at true cameras and true
theta **2/16** exceed it (range **0.498779–0.735411 mm**). The original measured sign case at true cameras is
0.606088 mm. These oracle/error-decomposition cases are diagnostics only, not additional acceptance gates.
No favourable sign or camera is used
to replace the recorded S1 gate, and no comparator clause is waived.

An independent planar control exposed a separate R3 omission defect: one paired crease could miss the model,
be discarded from the mean, and leave an almost-zero score from the other crease. The same loss of evidence
could reduce the fit objective. The repair makes any failed paired transfer invalidate the mean and refuse the
objective; incomplete evidence is not photo curvature for sigma. Reports retain required/missing/unpaired
crease names and per-crease results. A missing measured floor no longer falls back to an ideal-line score.
All valid scores keep their existing arithmetic and weighting. **FIT_VERSION is fit-5**; old fit-4 S1 caches
are historical replay inputs, not resumable current acceptance evidence.

Validation: **110 distinct affected tests passed, 2 existing expected failures, no skips**, across the 340.26 s
regression, focused rerun and 24 line-calibration cases. The regression's only unexpected failure was a stale
fit-4 version assertion; it now expects fit-5 and passed its focused rerun. The serial/pool equivalence tests
passed with their existing three-worker pool. Logs/PIDs/times/exit codes are in `regression/`,
`regression_followup/`, `calibration/` and `process_note.json` under the new evidence folder.

The 24 independent analytic blurred-step cases prove that guide correction is not residual error: correction
**4.169228–6.060273 px**, actual residual **0–0.084030 px**, over three orientations, two blur widths and every
sign pair of 6 px guide displacement. This does **not** estimate the real photos' systematic errors or justify
lowering S1's injected errors. Ambiguous edges, highlights, decals and occlusion still need independent controls.

Historical real-photo R3 comparators were replayed without refitting: handtrace **1.010717 mm**, fitted slab
**0.864184 mm**, delivered **0.693722 mm**, all exactly unchanged with both AB and BC measured at 25/25 samples.
The binding limit is still **0.193722 mm**. See `comparators.json` for per-crease values and input hashes.
The frozen files below remain unchanged. No full product fit, geometry promotion, integration enablement,
paid run or push has occurred. All recorded checks have exited; no long run is left active.

Frozen files remain byte-identical:

- `g3d_host/relief_rules.py`: `7c22eab7b32327f61140fc7982883db9d6be3bbd2c0fbdce8b04d25a7da062cc`
- `tests/proofs/wp6_prada_s1_dependencies.json`: `860500579b76d0cdfe1aa717b5673b4ecadf3f0ab19fc352d6577d7053f36c1b`

## Code map

| Area | Actual implementation |
|---|---|
| Numpy-only temple construction | `g3d/temple/{spec,arrange,planes,build,measure,report}.py` |
| Section and loft helpers | `g3d/temple/sections.py`, `g3d/temple/loft.py` |
| Blender scene adapter | `g3d/temple_bpy.py` (direct proof use; public integration still gated) |
| Host measurements, cameras, tracing, fitting | `g3d_host/relief_{check,views,trace,fit,cue,hinge}.py` |
| Frozen gates | `g3d_host/relief_rules.py` and the S1 dependency table above |
| Proof and instrument-floor logic | `tests/proofs/wp6_prada.py`, `wp6_prada_stages.py`, `wp6_theta_frame.py` |
| Saved diagnostics | `tests/proofs/wp6_s1_floor_check.py`, `wp6_handtraced.py`; results under ignored `data/wp6/` |
| Explicit read-only replay | `tests/proofs/wp6_s1_replay.py`; new output folder, hashes, camera/line decomposition and all target-error signs |

The proposed names `g3d_host/temple_views.py`, `temple_trace.py`, `temple_fit.py` and `g3d/geom/` in the original
design are historical names, not missing files to recreate. Public `g3d`/CLI temple operations, exporter
`g3d_temple` metadata and Astra wrappers remain deliberately gated. WP5 completion does not enable them.

## Review and cleanup

- Batch CLI receipts now propagate refused saves and failed/refused exports. Unknown operations stop before
  subsequent steps or output writes. Importing the CLI no longer executes it. The documented seat-step behavior
  remains unchanged; callers must still inspect individual seat receipts.
- A missing, directory-valued or empty explicit WP6 camera path refuses before loading the product. Fixture
  fallback remains available only when no camera path was requested.
- A local ignore rule keeps Python caches out of Git even within the root's fixture allowlist.
- Current resume instructions point here and to closed WP5 evidence, without reopening old experiments.
- R1's strict expected-failure label now reflects the resolved KEEP R1 decision; its failing measurement and
  threshold are unchanged.

Reproducers: seven failures before the fixes, all nine focused controls passing afterward. Full WP6/CLI
regression: **227 passed, 2 existing strict xfails, no skips**, 541.85 s. AR type-check passes; unit tests
**500/500**, capture/CLI QA **106/106**. The corrected R1 label was separately rechecked: one expected failure.
All runs are logged in `data/readiness_20261008/` and have exited. Runtime and frozen-input hash audits pass.
The static review covers **294 Python files / 108 runtime modules**, with no syntax/import-boundary issues,
conflict markers or prohibited tracked assets. The complete cleanup diff/new files were re-reviewed.
These checks do not replace the retained WP6 product-quality failures above.

## Independent contaminated-line calibration (2026-10-09)

`data/wp6/line_evidence_20261009/` holds 288 baseline and 288 held-out controls from
`tests/fixtures/line_evidence.py`, run by `tests/proofs/wp6_line_evidence.py`. All predeclared signs and seeds,
missing lines and wrong edges remain in the reports. Parallel edges and highlights can produce a wrong line
with RMS below 0.25 px (30 baseline cases); maximum extrapolated endpoint error is 24.163897 px. Neither RMS nor
guide correction alone is a calibrated residual uncertainty.

The independent corrupt-image controls support an optional `refine_line(..., valid_mask=...)` repair: no blur,
gradient, interpolation or step sample may use excluded pixels. All 75 focused mask checks pass; 152 affected
tests passed with the existing F1-rise xfail retained (75.29 s, no skips). Different validation angles, blur
widths and noise seeds retain all 72 masked corruption cases with <=0.250001 px endpoint error. Unmasked
parallel edges/highlights still fail. Production tracing uses its unchanged default; no S1 error, line,
eligibility, gate or fit version is replaced by this optional path.

The read-only real-photo audit reproduces all eight saved P2 lines. Existing masks change and shorten angled AB
(57 to 43 profiles); seven other lines, including angled BC (41), are unchanged. AC in 02 remains missing.
See `photo_audit_v2/report.json` and its labelled sheets. This does not establish real-photo accuracy or solve
STOP-I. Inputs were hashed before/after, and no comparator was refitted. An initial missing-matplotlib failure
is retained in `photo_audit_run/`; the Pillow version completed in `photo_audit_run_v2/`.

`camera_components/report.json` isolates every saved 05/02 camera parameter and theta using labelled oracle
substitutions. One warning example: true 02 yaw lowers measured R3 from 0.910923 to 0.559053 mm but worsens
exact-line R3 from 0.274915 to 0.379588 mm. Error cancellation is not a repair criterion. The next experiment is
camera recovery on known synthetic geometry, graded against its silhouette objective and truth separately;
all original line errors stay fixed. Do not select an oracle substitution because it passes R3.

The camera-only known-geometry calibration has completed (four workers, 49.13 s):
`truth_camera_refit/report.json` gives **0.863395 mm measured / 0.234553 mm exact-line** R3 for ordinary fitting
on the exact synthetic geometry. Giving the solver true hinges gives **0.627842 / 0.192404 mm**; full oracle
cameras/hinges remain **0.606088 / 0.023917 mm**. This separates the nuisance-fit error from shape error. No
production fit or gate has changed. Next, test bounded coupled camera/hinge alignment against the image objective
and independent known-truth controls; do not tune it to R3.

## Next repair step

The saved S1 replay and contaminated-line calibration above are complete. Continue camera/hinge recovery
diagnosis using the saved S1 geometry and fixed measured lines. Then establish whether a general solver repair
improves independently known camera evidence, without rewarding noisy-line cancellation. The analytical transfer
controls pass; changing the R3 distance formula or further fitting a product is not supported. Masking does not
resolve line identity in unmasked parallel edges or highlights. Retain every original guide-error stress case;
do not lower injected errors, select signs or substitute exact/oracle measurements for gate evidence. A future
rule-set redesign still needs independent calibration and design review before fitting.

Keep earlier proof folders immutable and write new evidence to a new folder. Preserve the fitted slab comparator,
every view, thresholds and the full R3 conditions. Size one CPU pool to free commit memory, run long jobs detached
without timeouts, and record real PIDs/timestamps/exit codes. Paid calls require an estimate and approval each time.
Continue these authorized offline repairs autonomously. Ask the owner for actual missing input, a new visual
verdict or a paid-run approval with an estimate, not for permission to resume the agreed repair task.

## Exact evidence and replay entry points

Paths below are relative to `glasses3d/`; do not resume a writer in an old evidence directory.

| Evidence | Path / use |
|---|---|
| Latest full proof | `data/wp6/proof2/proof.json`: `result=STOP-I`, `stages.S1.instrument_floor`, `stages.S1.instrument_floor_exact_lines`, `stages.S1.lines`, `stages.S1.guide_plan`, `stages.S1.cue`, `stages.S1.theta_cases` |
| Saved fitted inputs | `data/wp6/proof2/s1_fit.pkl` and `fits_delivered.pkl`; load only these trusted local caches when reproducing the recorded case |
| Resolved decision | `data/wp6/proof2/delegated_decision.json` and the proof's `delegated_decision_review` |
| Latest hand trace | `data/wp6/handtraced_codex_20261007/handtraced.json`, plus `owner_comparisons/`; not the older crashed `handtraced/` run |
| Original full-run receipt | `data/codex_resume_20261007/c_proof_run.json` and `c_proof.log`: 2026-10-07, 1054.64 s, three workers, exit 0 with STOP-I; process exit 0 does not mean quality passed |
| Camera basis | `data/wp6/wp3b_glossy/cameras.json`, SHA-256 `e20a75299be7cbcbfaacb45cd03ecd60e36024a387f763da602857f730965425` |
| Photo/model inventory | `data/reference/prada/manifest.json`; all six photo roles are in PLAN §5 |
| Source scene | `automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend` relative to the repository root; SHA-256 `56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06` |

The completed replay reproduced the stored true-model transfer **0.910922590046477 mm** (AB **0.9860298146578254**, BC
**0.8358153654351288**) and exact-line result **0.27491460569920634 mm** from the same saved fits. Read
`wp6_s1_theta_check.rebuild_s1`, `wp6_s1_floor_check.exact_from_measured`, `wp6_theta_frame.instrument_floor`
and `relief_fit.transfer_all`; check the saved camera/truth hashes before using them. Prefer the new explicit
replay, which supplies and verifies the camera basis rather than relying on the helper's legacy default:

```powershell
& C:/Users/Shay/PycharmProjects/lenses/glasses3d/.venv/Scripts/python.exe -u -B -m glasses3d.tests.proofs.wp6_s1_replay --proof-dir C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/wp6/proof2 --cameras C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/wp6/wp3b_glossy/cameras.json --out-dir NEW_ABSOLUTE_DIRECTORY
```

Set BLAS thread limits before imports and use the logged detached runner convention below. Successful exit 0
means the diagnostic reproduced the record, not that STOP-I passed. Do not rerun simply to resume this checkpoint.

**Legacy script caveat:** bare `python -m ...wp6_s1_floor_check` or `...wp6_s1_theta_check` reads older
`data/wp6/proof/` and writes fixed historical output paths. Do not run those bare commands for this resume.
Use the functions with `proof2` inputs in a new logged driver/output directory. No need to regenerate synthetic
fits just to reproduce a stored measurement. Keep measured and exact-line cases distinct; do not choose a
favourable error sign pattern, drop a crease, or relabel a bound as a measured depth.

After isolating the cause: add an independent reproducer, correct the general method or evidence, run affected
synthetic checks, then a fresh production proof only when warranted. Keep all comparisons against both delivered
and fitted-slab models. Keep public CLI/exporter integration gated until the WP6 proof prerequisites pass.
Then complete G2 integration and its checks before enabling Astra wrappers. Remaining G2 work includes removing
Prada coupling, preserving photo-crease counts in `check_relief` and propagating FRONT_BOUND flags; see
WP2_DESIGN's G2 row and WP7_DESIGN's open items.

## Running checks when changes justify them

Use `C:/Users/Shay/PycharmProjects/lenses/glasses3d/.venv/Scripts/python.exe`, with BLAS thread limits set before
imports. From the repository root, all `glasses3d/tests/test_temple_*.py` are the WP6 regression suite; select
the affected files first. CLI receipt controls are `tests/test_cli_receipts.py`; camera-input controls are
`tests/test_temple_inputs.py`. The exact last suite selection and commands are recorded in
`data/readiness_20261008/run_checks.py` and `phases.json`; preserve that runner and its reports.

The full proof entry is `-u -B -m glasses3d.tests.proofs.wp6_prada --workers N --out-dir NEW_ABSOLUTE_DIR`.
Do not run it as the first step, use `--quick` for acceptance, or pass `--owner-waiver`. Pick N from current free
commit memory (about 1.3 GB per worker; cap at the 16 logical CPUs and leave room for the host). Use one pool,
EcoQoS opt-out, BLAS 1 and AboveNormal where supported. Twelve workers were safe with about 22 GB free commit
for recent print work; that is not a guarantee for every WP6 workload. Save logs, arguments, actual PID,
start/end timestamps and true exit code, with no elapsed-time kill. No paid run is authorized by this handoff.
