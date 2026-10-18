# Facet lab (WP6 v3)

The facet lab gives `gpt-6-astra` ten tools to build faceted temples against authoritative product photos.
WP6 was closed by the owner's acceptance of paid run 8 on 2026-10-13. The lab is now integrated through
the main job's `temple_stage`: it works privately from the captured current head and applies an accepted result
through a trusted worker path. The job registry imports this stage. The standalone CLI below remains available
for explicit development work; its historical forecast is not the main job estimator.

Current design: [WP6_V3](../../plan/WP6_V3.md), [ASSEMBLY](../../plan/ASSEMBLY.md), and the later M11 task reports.
[Instructions](instructions.txt) and `tools.py` are the live prompt/schema. Prada is historical proof data, never
a runtime default. Sides name the wearer: +X left, -X right. All photos remain authoritative.

## Tools

| Tool | Purpose |
|---|---|
| `facet_inspect` | Current design/fitted nodes, readings, object note, fit, creases and chart. |
| `photo_image` | A photo or native crop, optional enhancement and small-crop enlargement. |
| `photo_read` | Named multi-view corner/crossing readings with pick/camera/opening uncertainty and evidence sheets. |
| `photo_compare` | Current temple at frozen photo cameras/openings, with identity overlays, native zooms and diagnostics. |
| `facet_edit` | Edit a revision, jointly fit its readings/declarations, build and optionally apply/compare it. |
| `facet_variants` | Fit/build/render alternatives beside the current head without changing it. |
| `facet_finish` | Finish only with the current required compared evidence and a valid export. |
| `read_report` | Lossless access to paged tool reports. |
| `facet_crease_compare` | Show eligible crease-angle alternatives in the actual AR photo views with their metrics. |
| `facet_visible_angle` | Select an exactly shown angle when measurements are unresolved; certain contradictory evidence still refuses. |

The edit path is `geometry.apply_edits` → stored design → `fit.fit_spec` → `build_temple` → native worker write.
Reports distinguish measured uncertainty, built geometry and the selected visible angle. Declarations bind connected
layout/readings, supporting chains, effective fit dependencies and immutable comparison assets; unrelated changes
retain a choice, bound changes refuse it. Closed solids, thickness, declared shape and strict spec checks remain.

Files: `cli.py` (standalone package/brief/forecast/request/run), `tools.py`, `lab.py`, `geometry.py`, `readings.py`,
`state.py`, `sheets.py`, `package.py` (capture parent inputs), `stage.py` (shared-money parent integration),
`owner_stage.py` (confirmed edit specialist), `pose_basis.py`, `rigid_pose.py`, `arm_evidence.py`, and
`crease_angles.py` / `crease_ar.py` (visible choices and their evidence).

## Assembled stage

`package.prepare(scene, destination, call_id)` captures the parent's checkpoint/export and original/transport photo
hashes. It sends the parent's upright transport copies and binds originals separately. `pose_basis.build` carries
the frozen cameras, mattes, ROI and per-photo openings; no opening metadata means authored pose with no claimed
calibration. Readings disclose 0.3-degree observed and 10-degree unmeasured opening assumptions, including shared
source-fit uncertainty for transported photos. These are assumptions, not measured confidence intervals.

Owner text stays verbatim in `brief.txt`; neutral contract prose is separate. Camera conflicts, photo IDs,
colourway selections and precision notes are explicit job data. Historical Prada photo numbers and conflicts are
not general product facts. Private failures preserve their cause/progress; incomplete work is not applied to the
parent. Main and specialist share the same owner money and stop; the specialist cannot choose a budget.

## Standalone commands

From the repository root, prefix these commands with `glasses3d/.venv/Scripts/python.exe`:

```text
-m glasses3d.agent.temple_lab.cli prepare --job NEW_DIR --source-config sources.json --rig studio_dome_v1
-m glasses3d.agent.temple_lab.cli estimate --rehearsal rehearsal/rehearsal.json --out estimate.json
-m glasses3d.agent.temple_lab.cli request --job PACKAGE --run-id RUN --effort high --turns 60 --cap 20 --estimate estimate.json --k 4
-m glasses3d.agent.temple_lab.cli run --job PACKAGE --run-id RUN --effort high --turns 60 --cap 20 --estimate estimate.json --k 4
```

`prepare` is local: source config must name `source_scene`, `scene_sha256`, `reference` and `basis_json`.
Optional photo IDs, colourways, camera conflicts and precision notes have no product default. The prepared package
binds code, instructions, source scene, photos and basis. `request` writes the exact single-use approval request;
it is not approval. `run` refuses missing, changed, consumed or insufficient approval, and needs a clean package.
An implementation change requires fresh preparation and request. Do not run from a rehearsal directory.

The standalone CLI has no approval command. After explicit owner authorization and an estimate, the coordinator
records it with the existing `glasses3d.agent.approvals.write_approval` API, passing the request's run directory,
`max_usd`, verbatim `owner_words` and truthful `recorded_by`. Do not fabricate authorization or use offline-only
test approvals for production. The assembled job CLI provides the normal owner approval workflow.

| Standalone option | Meaning |
|---|---|
| `--job`, `--source-config`, `--rig` | Package destination/config and matched rig for prepare. |
| `--rehearsal`, `--out` | Rehearsal input and forecast output for estimate. |
| `--estimate` | Forecast table read by request/run; default `data/temple_lab/estimate_v3.json`. |
| `--run-id`, `--effort` | Bound request identity and `high`, `xhigh` or `max` effort. |
| `--turns`, `--no-turn-cap` | Mutually exclusive explicit reply cap or no cap; one is required for request/run. |
| `--cap`, `--k` | Required standalone reserve cap and required distinct compared heads (K defaults to 4). |
| `--fit-output-to-budget` | Explicit legacy option to reduce response ceiling to remaining reserve. |
| `--env-file` | Optional credential-loader path; its contents must never be printed. |
| `--ignore-load` | Explicitly permit competing Blender/pytest load, still reported. Avoid it for timing proofs. |
| `--help` | Parser reference, with no package or paid work. |

The standalone parser has no settled-only switch or resume mode. Its reserve/output-fit controls are legacy
behavior, not the owner's current standing $20 settled-only/high terms. Use the assembled stage for those terms;
any different standalone paid policy needs explicit owner authorization. This README is not such authorization.

## Evidence, forecast and tests

Runtime conversations are append-only. The common loop now renews into a fresh conversation at request pressure
with every owner photo, summary/factual fallback and verified current evidence; it never rewrites old history.
Legacy context-guard fields remain in standalone request/forecast compatibility records, but do not describe a
live image-replacement operation. The standalone forecast still models the historical mix/guard approximation;
it has not been recalibrated from v3 runs or the renewal policy. Run 8 and assembled v3 stages do exist.
High/xhigh forecasts use max-effort output assumptions without a claimed discount. No forecast is a guarantee.

Finish evidence comes from `state.finish_missing`: K distinct applied compared heads, the required sufficiently
distinct variants, comparisons of the selected best revision at every camera photo, an object note, and the
additional stuck-case judgement/variant evidence. Only delivered evidence counts; read the live tool for exact
argument and refusal details. The prompt contract caps the specialist at 19,000 characters.

The offline T5 proof is `glasses3d.tests.proofs.astra_temple_lab rehearse` with an explicit new output directory.
It exercises the real loop, worker and seven scripted tools, including conversation renewal; the other three tools
are covered by separate report and visible-angle proofs. Task 8's fresh T5 passed 49/49 checks. Tests in
`tests/agent/test_temple_rehearsal_contract.py` check that partition against the current ten-tool registry.
These are tool proofs, not new product acceptances. Use [test commands](../../tests/README.md) for suite policy.

Limitations remain visible: the mirrored spec controls supported slab-like temples, not arbitrary branching or
multi-material arms; photos/cameras can disagree; no render light reproduces every photograph's facet tone;
unsupported nearby markings are reported rather than silently moved. Inspect actual full-size photo comparisons
and diagnostics. The owner judges likeness.
