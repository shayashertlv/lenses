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
| WP6 | **v2 built; awaiting the owner's OK for one paid Astra run** per [WP6_FACETS.md](WP6_FACETS.md). v1 retired 2026-10-10 (tag `wp6-v1-final`) |
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

Phase 3 and pre-flight results (2026-10-11):
- **Astra facet tools** (`agent/temple_lab`, rebuilt):
  - 7 tools, the strict edits union, judgements and finish preconditions;
  - the worker's non-mutating `temple_variants_render`;
  - loop settings bound in the approval: tool_choice required, finish-gated stop, context guard, timeouts and E_BUSY;
  - the six pending v1 relief modules are removed.
- **T5 rehearsal** (MockTransport, real Blender, 0 network): 22/22 checks. **T5b**: the preferred layout is reached from
  revision 0 in 23 edit ops.
- **Finish rule "distinct head"** (preconditions 1 and 4): two attempts differ only when the built temple surface
  (normal distance) or outline moves ≥ 0.2 mm. Five review rounds found ways to meet label-, layout- and
  crease-threshold rules with near-zero geometry. Crease comparison is now reported, never decisive.
- The light is the owner's choice C (`studio_dome_v1`). Sheet #2 is in `data/wp6v2/sheet2/`. The lighting sheet is in
  `data/wp6v2/light_choice/owner/`.
- **Estimate** (from T5 tool outputs): 40 replies $7.26 (cap $15); 60 replies $13.78, edit-heavy $16.56 (cap $20);
  90 replies $22.19 (cap $50). Package: `data/wp6v2/lab/package`. The request `runs/facet-astra-001` is UNAPPROVED.

**Paid run `facet-astra-002` (2026-10-11).**
- Approval, in the owner's words: "yuo can run it with 20$ max (dont use reserve spending, just cut it by 18$). dont
  have a replies limit."
- Settings: effort max, no reply cap, cap $20, no output squeezing.
- A host watcher (`data/wp6v2/lab/stop_at_18.py`) would have stopped the run at $18 settled. Until 10:29 it counted the
  guard's per-request hold as spend; it was corrected while running, and it never fired.
- Result: 10:03:29–10:30:09, **33 requests, $6.679752 settled**, Astra finished on its own via `facet_finish`
  "matched".
  - Best checkpoint c0013, revision 15. Export `head-10ab74e8a162dded.glb`; validate_spec ok (2 warnings).
  - Layout: logo triangle, crown, hinge flank and tail flank fading at 55–85 mm, roll 0.25 mm. Every crease was
    declared a ridge.
  - 3 variant sheets, 6+ builder refusals that Astra fixed itself.
- Owner sheet: `data/wp6v2/result_facet-astra-002/` (tests/proofs/wp6v2_result_sheet.py). The owner's verdict is
  pending.

**Owner feedback on facet-astra-002, verbatim:**
- "yes it did miss the red line."
- "the top triangle facet is barely noticeable. every other triangle also faces the same issue which is not being
  pulled enough outwards"
- an unwanted line from the logo apex
- "the each triangle line is very soft wheres it needs to be sharp"

**Fixes:**
- `c7362c4` builder: flat cells before a fade, sharp creases, E_FADE_CLIFF.
- `b34df09` black photos B1–B3 for Astra, plus clearer instructions.
- `cc38cbe` pre-flight P1: the start-model "camera error" sentence had kept the facets shallow.
- A three-lens pre-flight review gave GO.

**Paid run `facet-astra-003` (2026-10-11).**
- Approval: "yes, make the fixes and run it again", with the same terms. 13:27:20–13:42:02, 15 requests, **$3.598669**.
- Astra finished on its own as "matched". Best checkpoint c0011, rev 12; export `head-bad259c0eb030eff.glb`; spec ok.
- Layout: logo, heel, crown, lower_aft and tail. The crease behind the logo is now present (valley, 12°). Creases run
  12–19° (run 2: 7–8°). Roll 0.15 mm.
- Crown and tail both fade 47→76 mm, so the upper-back triangle does not read as its own flat face.
- Sheet: `data/wp6v2/result_facet-astra-003/` (faces_run3.png). The owner's verdict is pending; first remark: "it still
  looks like the top triangle isnt being recognized right?"

No further paid call is authorized. The next one needs a cost estimate and the owner's explicit OK.

## Decisions (owner's words verbatim)

- **2026-10-11, light for photo-matched renders:** "c is definitely the best". The owner was shown A/B/C on the
  preferred and original models at all 7 photos, with neutral letters and identical lenses. C = `studio_dome_v1`
  (A = photo_env, B = catalog_band_v1). Record: `data/wp6v2/light_choice/owner_choice.json`. `studio_dome_v1` is
  the job rig for Astra and owner sheets. Its facet tone order is still not the photos' (WP6_FACETS §3.2), so
  shading stays a qualitative aid.

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
- `4d84cf1` archive WP6 v1 + start docs; `fa857b0` seeing (pose basis, matched compare, white balance, photo_env);
  `04f8ceb` builder g3d.temple/2 + template + drawings + Blender layer; `c102efb` builder review fixes;
  `cf9cf1a` Astra facet tools, loop finish settings, rehearsals, light choice; `831a8a2` pre-flight (owner light C,
  gate/timeout fixes); next: surface-only distinct-head rule, rebuilt package and estimate.
