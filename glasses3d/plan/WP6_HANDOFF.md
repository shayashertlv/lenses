# WP6 handoff — resume diagnosis and repairs

WP5 is complete by the owner's 2026-10-08 visual acceptance. Commit `4304c1d` sets **observed-slope join +
Poisson** as the regular texture method. Its fresh production bake matches the approved texture byte-for-byte.
Code review/cleanup is committed at `d5e5917`. The owner now requests resuming WP6 in another session.
This handoff authorizes offline diagnosis and repairs; no new experiment has been started in this session.
Use [WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md). No process is active. WP6 has not passed its quality proof.

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

## First work in the next session

Start from the saved S1 instrument-floor failure. Review `wp6_prada.stage_s1`, `wp6_theta_frame.instrument_floor`
and the recorded guide/camera errors before choosing a repair. Establish an independent reproducer and improve
the evidence or method under unchanged gates; do not start another full product fit without resolving the
instrument issue. A future rule-set redesign needs independent calibration and design review before fitting.

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

First reproduce the stored true-model transfer **0.910922590046477 mm** (AB **0.9860298146578254**, BC
**0.8358153654351288**) and exact-line result **0.27491460569920634 mm** from the same saved fits. Read
`wp6_s1_theta_check.rebuild_s1`, `wp6_s1_floor_check.exact_from_measured`, `wp6_theta_frame.instrument_floor`
and `relief_fit.transfer_all`; check the saved camera/truth hashes before using them. The rebuild helper reads
the default Prada camera basis, so verify that it is exactly the recorded basis above.

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
