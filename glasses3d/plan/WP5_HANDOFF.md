# WP5 closeout — owner accepted 2026-10-08

**Current status: WP5 done by explicit owner acceptance.** The owner selected **observed-slope join + Poisson**
and said "set it and mark wp5 as done". That latest direction supersedes earlier all-proofs-pass completion
instructions in the archived entries. It does not turn failed numerical measurements into passes.
`photoprint/boundary.py` now supplies the observed-slope screened join after regular block quilting;
the separate unscreened chart Poisson solve, gains, calibration and small-hole PatchMatch stay as approved.

Accepted evidence: `data/wp5/quilt_joins_20261008/review/more_angles/index.html`, slope column, 19 angles.
Approved PNG SHA-256 `ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`; TOOLTEST GLB
`09a421be248f43b7dd888d48e10c1ffb8fa3297931743b1ef5599df0e6dac9eb`. The exact owner verdict is appended to
`data/ledger/owner_verdicts.jsonl`; its asset/viewer hash record is `data/wp5/slope_default_20261008/owner_acceptance.json`.
The visual experiment already passed its output audit and 15 Blender/export checks. New runtime controls:
**54 passed in 26.53 s** (boundary, Poisson, quilt-coordinate and production API checks). Production replay
is logged in `data/wp5/slope_default_20261008/run.json`, with results in `result.json`.
Replay took **243.53 s**, with exact approved PNG, provenance maps, layout, registration and gain field.
A1/A3 match and pass (729,621 protected texels). The post-bake local wrapper failed on an unused wrong report
filename; a corrected readback audit passes without rebaking. The manifest differs only in its verified output
PNG path. Initial wrapper/readback failures remain recorded; no runtime failure or process remains active.

Full independent A4/A5/B3 and full regression have not run for the selected variant. The prior regular-Poisson
failures (A4 all four; A5 8 fails / 7 passes; B3 six of seven) remain attached to their original runtime and assets.
No thresholds, seeds, eligible rows or instruments changed. G1 remains verified; do not redo it.
WP5 default/closeout committed as `4304c1d`. Next work is recorded in [WP6_HANDOFF.md](WP6_HANDOFF.md).
WP6 remains **STOP-I**; R1 and frozen R3 remain binding. The next session resumes WP6 repairs under its current
handoff; WP5 stays closed.

## Historical evidence

The complete old repair handoff is [archived](archive/20261008/WP5_HANDOFF.md). Its pending verdicts,
all-proofs-pass completion instructions and WP5 resume commands are superseded. Measurements and source assets
remain intact. Use [WP6_SESSION_PROMPT.md](WP6_SESSION_PROMPT.md) for the next session.
