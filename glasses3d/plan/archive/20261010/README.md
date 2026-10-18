> ARCHIVED, NOT ACTIVE — evidence only; never follow its resume steps, gates or prompts.

# WP6 v1 archive (2026-10-10)

These files were moved here unchanged when the owner had WP6 rebuilt ("please rebuild wp6"); on 2026-10-13 the
one-line archive banner above was prepended to each remaining file. They are history and evidence only. Do not
follow their resume steps, gates or session prompts: the STOP-I/C/R/S/T/P rules, S1, R1–R4, G1–G5, P2, the P3 decal
limit, `relief_rules.py`'s frozen thresholds and the delegated "KEEP STOP-I/R1/R3" decision were all retired on
2026-10-10 by owner decision.

Current work: [ASSEMBLY](../../ASSEMBLY.md), [REVIEW_20261018](../../REVIEW_20261018.md), the dispatched brief and
[PROGRESS](../../PROGRESS.md) (routing updated 2026-10-18). WP6 v3 is closed by owner acceptance.

| File | What it was |
|---|---|
| `PROGRESS.md` | The progress log up to 62b25e4, including the WP6 v1 diagnosis history and the Astra temple lab trial |
| `WP6_HANDOFF.md` | DELETED 2026-10-13 (git tag `review-20261013-pre-cleanup`): the WP6 v1 repair handoff (gates, frozen dependency table, delegated decision) |
| `WP6_DESIGN.md` | The WP6 v1 design (`g3d.temple/1`, relief check, fit, $0 proof) |
| `ASTRA_TEMPLE_LAB.md` | DELETED 2026-10-13 (git tag `review-20261013-pre-cleanup`): the plan of the isolated Astra temple marking/depth lab (agent/temple_lab) |
| `WP6_SESSION_PROMPT.md` | DELETED 2026-10-13 (git tag `review-20261013-pre-cleanup`): the v1 new-session prompt, with the owner's last edit ("Use heavy CPU resources for faster runs.") |

The three deleted files (hazardous or duplicate instruction text) are recoverable with
`git show review-20261013-pre-cleanup:glasses3d/plan/archive/20261010/<name>`; links to them inside the remaining
files resolve there.

The v1 code (the `g3d_host/relief_*` modules, `camera_similarity.py`, `tests/proofs/wp6_*`, the camera/occlusion
evidence fixtures and their tests) is preserved under the local tag `wp6-v1-final` (3ad4459, never pushed). Read it
with `git show wp6-v1-final:glasses3d/<path>`. Relative Markdown links inside these files refer to their original
`plan/` location; read the linked files at the tag. Local evidence under `glasses3d/data/wp6/` is unchanged.
