# glasses3d progress

Started fresh on 2026-10-10 when WP6 was rebuilt. The previous log (to 62b25e4), the WP6 v1 handoff, design,
session prompt and the Astra temple lab plan are in [archive/20261010](archive/20261010/README.md); older snapshots
are in [archive/20261008](archive/20261008/README.md). Archived files are evidence, never instructions.

## Status

| WP | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b | Complete and tested |
| WP4 | Complete and tested. The real-product proof is **stale** since the WP5 exporter change `10bf932`: 11 of 24 `test_seat_real` tests skip as stale. Re-run `python -m glasses3d.tests.proofs.wp4_proof` (~3 min), then `test_seat_real` |
| WP5 | Complete by owner visual acceptance 2026-10-08, FT1119 only: observed-slope join + Poisson is the default (`4304c1d`). Not an all-proofs pass: A4/A5/B3 failures stay recorded ([WP5_HANDOFF](WP5_HANDOFF.md)) |
| WP6 | **v2 in progress** per [WP6_FACETS.md](WP6_FACETS.md). v1 retired 2026-10-10 (tag `wp6-v1-final`) |
| WP2 | A–C verified; D–F not built |
| WP7 | Design only ([WP7_DESIGN.md](WP7_DESIGN.md)) |
| WP8 | Not started (paid; owner OK per run) |

## WP6 v2 build order (WP6_FACETS §6)

1. **Archive + docs (§7)** — done 2026-10-10 (track F), committed in the archive commit after `62b25e4` (see git log).
   **Seeing (§3)** in parallel: render.py, seeing.py, compare.py, pose.py (new) and their tests.
2. Owner sheet #1 (after seeing). **Builder v2 (§2)** in parallel: g3d/temple/*, temple_bpy.py, core temple tests.
3. **Lab tools (§4)**: agent/temple_lab/*, worker method for variant renders, loop.py settings, tests, T5, T5b;
   remove the pending v1 modules below. Owner sheet #2.
4. Adversarial review of the whole diff, fixes, commit. Cost estimate. Owner OK. One paid run.

Phase 1–2 results (2026-10-11):
- Seeing is built: pose basis from fits.json, photo-matched compare, white balance and the photo_env light fit.
- **T2 rig check: no rig passes.** photo_env shows the gloss change 2.6 L8/mm against the 10 target. studio_dome_v1
  passes the size target but reverses the facet tone order. Details are in WP6_FACETS §3.2 (amended).
- The builder `g3d.temple/2` is built: 268 tests, v1 equivalence 3e-14 mm, template p95 0.30 mm.
- The v1 lab is broken until step 3 (it writes /1 specs).

No paid call is authorized now. The next one needs a cost estimate and the owner's explicit OK.

## Decisions (owner's words verbatim)

- **2026-10-10, WP6 rebuild:** "please rebuild wp6" (reply to the review that proposed judging WP6 by eye on
  photo-matched renders); "i just want astra to be able to handle facets and such, thats it. you can do whatever,
  even rebuild from scratch". Recorded design: [WP6_FACETS.md](WP6_FACETS.md) (62b25e4).
- **2026-10-10, Astra temple lab trial rejected:** "ok what did you learn? the results are still not good".
  Run `real-astra-001` (35 replies, 34 tool calls, $5.599202 settled of a $10 cap) exported c0003; one layout at
  two depths, hard full-length construction edges where the photos show fading diagonals. Evidence:
  `data/wp6/astra_temple_lab_20261010/` (owner-review-20261010.json, real-astra-report.md).
- **2026-10-08, WP5 closed:** "Observed-slope join + Poisson is the best, set it and mark wp5 as done".
- **Owner's edit to the v1 session prompt** (kept in the archived copy): "Use heavy CPU resources for faster runs."
  Process pools within free-commit memory (glasses3d/AGENTS.md).
- **Process (coding agent, 2026-10-10):** Codex sessions work sequentially; Claude sessions with ultracode on may run
  workflows with disjoint file ownership; the coordinator commits with explicit paths.

## Retired WP6 v1 gates (2026-10-10, owner decision)

S1, R1–R4, G1–G5, P2, the P3 decal limit, the STOP-I/C/R/S/T/P rules, `relief_rules.py` with its frozen thresholds
and the frozen S1 dependency table, and the delegated "KEEP STOP-I/R1/R3" decision. The review showed the true or
owner-preferred temple fails R1, R3 and P3, and that facet depth is not measurable from these photos through
silhouettes (WP6_FACETS §0). The v1 code is preserved under the local tag **`wp6-v1-final`** (3ad4459, never
pushed): `git show wp6-v1-final:glasses3d/<path>`.

The general rule stays: never loosen a gate to get a pass; if a gate is wrong, prove it (the true or owner-accepted
answer fails it) and ask the owner.

## Known-good references

FT1123 crystal, Oakley OO9208 v2 and INVU IA22403B v2, re-accepted by the owner on 2026-10-06 (asset-bound verdicts
in `data/ledger/owner_verdicts.jsonl`). **Prada PR 17WS is an open failure**: its older "looks great" verdict
predates noticing the facets and the shortened-temple rendering defect.

## Archive step (track F, 2026-10-10)

Removed (`git rm`): `g3d_host/relief_{anchors,crop,cue,edges,fit,hinge,junction,photo_mask,ridge,transition}.py`,
`g3d_host/camera_similarity.py`, every `tests/proofs/wp6_*` except the Blender driver, the camera/occlusion evidence
fixtures, and 33 test files that only exercised v1 code. Renamed `tests/proofs/wp6_bpy_run.py` →
`tests/proofs/temple_bpy_run.py` (the Blender driver of `test_temple_blender.py`; it imports no v1 module). Kept and
stripped: `test_temple_blender.py` (P0 instrument grade dropped), `test_temple_real.py` (glossy WP3b table test only),
`test_temple_design_checks.py` (builder warnings + the decal-standoff test moved in from `test_temple_relief_cue.py`).

**Pending removal in build step 3** (still imported by `agent/temple_lab/lab.py:235-236`, which belongs to the lab
track): `relief_trace.py` and `relief_check.py`, plus their import closure `relief_views.py`, `relief_rules.py`,
`relief_registration.py` and `relief_normal_residual.py`. Remove all six with lab.py's imports.
`relief_views.rot_about_hinge` moves to `g3d_host/pose.py` (seeing track).

Stale doc links for the owning tracks (not edited here): `agent/temple_lab/README.md:7` links
`plan/ASTRA_TEMPLE_LAB.md` (now `archive/20261010/`); the g3d/temple module docstrings and `g3d_host/matte.py:59`
cite `WP6_DESIGN.md` (now `archive/20261010/`); `g3d/temple/measure.py:32` and `fixtures.py:390` name archived modules.

Checks (basetemp `data/pytest_tmp/f_archive`): `pytest --collect-only glasses3d/tests` collects 1904 tests with 0
errors; temple/spec/glb/lens_id suites 290 passed; `tests/agent` 420 passed; `test_wp2_*` 363 passed;
`test_temple_blender.py` 7 passed (renamed driver); all 105 host-importable modules of g3d, g3d_host and agent
import, and the 7 Blender-only modules import in Blender 5.2.

## Ledger (2026-10-10)

- `3e8e247` camera uncertainty + apex-gap diagnostics; `c9ad82f` owner accepted both apex comparison variants.
- `b46bda8` opt-in camera transfer + arm review checkpoint; `e846e0f` owner preference for the distal-arm revision.
- `dbebc7c` opt-in contour-normal refinement; `c97df92` retained replay failures + full diagnostic run;
  `db95572` fresh S1 floor + preferred export coverage audit; `8d6e006` full-proof progress; `e6b31a7` completed
  proof saved for the owner's review pause.
- `c99da5c` owner rejection + fresh-chat handoff; `70c9e90` opt-in smooth facet-to-arm comparison tool;
  `6c72c9f` owner decision to retain the preferred reference.
- `f0205a3` facet normals + complete P3 export checks; `b5add8f` preferred recovery + live photo diagnostic;
  `8a2113c` native recovery + R1 preservation checks; `b1c8651` all exported decals graded + preferred comparison.
- `166de93` isolated Astra temple lab; `008fc73` beige references + flat-temple views; `ddd6658` owner-authorized
  test without a reply cap; `62662f0` observed Astra test documented; `3ad4459` owner rejection and lessons
  (= `wp6-v1-final`).
- `62b25e4` WP6 v2 facets design (owner-approved rebuild).
- Next commits: the archive + docs step (track F), then the seeing track after its fixes.
