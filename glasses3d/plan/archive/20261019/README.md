# Archive: 2026-10-19 cleanup

These dated reviews, port inventories and original designs are evidence, not current execution instructions.
The twelve moved documents retain their original bytes. Their relative links and historical line numbers refer
to their former locations; use the path table below when following an old reference. Current findings and next
actions are in [ASSEMBLY](../../ASSEMBLY.md), [REVIEW_20261018](../../REVIEW_20261018.md),
[PROGRESS](../../PROGRESS.md), and the detailed reports under `glasses3d/data/review_20261018/`.
Current usage is documented in [agent/README](../../../agent/README.md),
[arview/README](../../../arview/README.md) and [tests/README](../../../tests/README.md).

## Preserved documents

All former paths in this table start at `glasses3d/plan/`. M01 records completed reviews and results; M02 records
the superseded port/design work and its provenance. WP4/5/6 technical contracts and PLAN remain active outside
this archive. PORT_REVIEW's machine-readable companions remain at
[port_review/arview.json](../../port_review/arview.json) and [port_review/studio.json](../../port_review/studio.json).

| ID | Former path | Archived record |
|---|---|---|
| M01 | `INSTRUCTIONS_REVIEW_20261016.md` | [Instruction review](INSTRUCTIONS_REVIEW_20261016.md) |
| M01 | `INSTRUCTIONS_REVIEW_6F_20261016.md` | [6f instruction review](INSTRUCTIONS_REVIEW_6F_20261016.md) |
| M01 | `M7_M8_RESULTS.md` | [M7/M8 results](M7_M8_RESULTS.md) |
| M01 | `REVIEW_20261015_PRADA_FACET.md` | [Prada facet investigation](REVIEW_20261015_PRADA_FACET.md) |
| M01 | `REVIEW_OAKLEY_RUN_20261013.md` | [Oakley run review](REVIEW_OAKLEY_RUN_20261013.md) |
| M01 | `REVIEW_20261013.md` | [Pre-assembly review](REVIEW_20261013.md) |
| M02 | `PORT_REVIEW.md` | [Port provenance and findings](PORT_REVIEW.md) |
| M02 | `RULES_AUDIT.md` | [Original rules audit](RULES_AUDIT.md) |
| M02 | `WP1_DESIGN.md` | [Original viewer design](WP1_DESIGN.md) |
| M02 | `WP2_DESIGN.md` | [Original agent design](WP2_DESIGN.md) |
| M02 | `WP7_DESIGN.md` | [Original instruction design](WP7_DESIGN.md) |
| M02 | `wp7/evidence.md` | [Instruction provenance](wp7/evidence.md) |
| M03 | `PROGRESS.md`, pre-M11 tail | [Progress through 2026-10-17, with heading index](PROGRESS_2026-10-05_to_2026-10-17.md) |

M03 preserves the old tail verbatim, including its undated appendices and later annotations. Current-state facts
and unresolved limits were lifted to the working PROGRESS before the split; all M11 entries remain there. Earlier
October 5–8 progress records already live in [the 20261008 archive](../20261008/).

## Removed historical scripts

P02's scripts have no caller in the maintained tests/proofs or current execution instructions. Their references
in dated progress/review evidence remain valid historical records. The local tag `pre-cleanup-20261019` preserves
their exact source; restoring a script alone does not make its historical dependencies current. No saved evidence
or owner asset is deleted by these source removals.

- `assembly_m4b.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m4b.py`.
- `assembly_m4c.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m4c.py`.
- `assembly_m4d.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m4d.py`.
- `assembly_m5_hinge.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5_hinge.py`.
- `assembly_m5_stage_evidence.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5_stage_evidence.py`.
- `assembly_m5fix1_baseline.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix1_baseline.py`.
- `assembly_m5fix1_camera_roll.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix1_camera_roll.py`.
- `assembly_m5fix1_coating.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix1_coating.py`.
- `assembly_m5fix1_finish.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix1_finish.py`.
- `assembly_m5fix1_history.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix1_history.py`.
- `assembly_m5fix2_reports.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix2_reports.py`.
- `assembly_m5fix2_worn.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix2_worn.py`.
- `assembly_m5fix3.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix3.py`.
- `assembly_m5fix5_cost.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix5_cost.py`.
- `assembly_m5fix5_pose.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix5_pose.py`.
- `assembly_m5fix9_runway.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5fix9_runway.py`.
- `assembly_m5prep_draft.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5prep_draft.py`.
- `assembly_m5prep_intake.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m5prep_intake.py`.
- `assembly_m7_surface_ops.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m7_surface_ops.py`.
- `assembly_m8_edits.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m8_edits.py`.
- `assembly_m8_health.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m8_health.py`.
- `assembly_m8_health_diagrams.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m8_health_diagrams.py`.
- `assembly_m8_radius.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m8_radius.py`.
- `assembly_m8_scale_diagram.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_m8_scale_diagram.py`.
- `assembly_normal_audit.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_normal_audit.py`.
- `assembly_review_gallery.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/assembly_review_gallery.py`.
- `checkpoint_journal_replay.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/checkpoint_journal_replay.py`.
- `rim_blender.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/rim_blender.py`.
- `surface_gallery.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/proofs/surface_gallery.py`.

A03 unsupported planarizer and its dedicated test (historical diagnostic dependencies):

- `planarize.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/g3d/temple/planarize.py`.
- `test_temple_planarize.py`: removed in CLEANUP_COMMIT_PENDING; recover with `git show pre-cleanup-20261019:glasses3d/tests/test_temple_planarize.py`.
