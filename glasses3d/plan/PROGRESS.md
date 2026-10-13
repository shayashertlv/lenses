# glasses3d progress

Started fresh on 2026-10-10 when WP6 was rebuilt. The previous log (to 62b25e4) and the WP6 v1 design are in
[archive/20261010](archive/20261010/README.md); older snapshots are in [archive/20261008](archive/20261008/README.md).
Archived files are evidence, never instructions. The WP6 v1 handoff, the two session prompts and the Astra temple lab
plan were deleted on 2026-10-13 (tag `review-20261013-pre-cleanup`).

**Full-codebase review 2026-10-13: [REVIEW_20261013.md](REVIEW_20261013.md)** (status per WP from the code, the
automation gaps, ranked defects, cleanup). Read it before starting WP2 D–F, WP7 or WP8.

**Active plan from 2026-10-13: [ASSEMBLY.md](ASSEMBLY.md)** (owner: Claude plans, Codex executes). Assemble the tools
into one automation in milestones M0 (blocking fixes) → M1 (job intake, registry v1, offline end-to-end run) → M2
(instructions, image policy) → M3 (delivery, owner review ledger) → M4 (seating, temples, print as stages) → M5 (paid
validation, owner OK per run). M0 and M1 are complete; next action: M2.

## Assembly M0 (2026-10-13, $0; complete, implementation commit `27db2c0`)

Started in the requested repository checkout on branch `glasses3d`, at `02b0a16`; no worktree, push or paid API call.
Implemented all eight M0 items:
- one fixed paid-run lock under `glasses3d/data/` for lab packages and ordinary jobs;
- every head export carries `validate_spec`, declared-seat checking and the validation's GLB sha256; cached by
  actual bytes within a Seeing instance, with old export indexes validated again (never trusting old reports);
- facet finish refuses an invalid export as `E_EXPORT` and keeps the run open;
- a bounded Git diff pathspec preserves snapshot exclusions/deletions without Windows argv growth;
- three free token-count attempts with 0.5/1-second backoff; inference still never retries;
- `turns_used` comes from the interaction log in `finally`, including abnormal exits;
- print fixture pools capped at two, with the split/loadfile full-suite commands documented;
- approval-bound `RunSettings.settled_stop_usd`, separate from the reservation cap. The generic job path will set
  $20 settled / $23 reserve; legacy requests keep their existing settings when the new field is absent.

Technical choices: validation failures remain inspectable by seeing tools; finish refuses them. A malformed declared
seat cannot silently skip checking. Offline tests isolate the fixed lock constant inside each test, while separate
job folders within a test still contend. No gate or numerical threshold changed.

Initial checks: export validation 6/6; snapshot regressions plus existing dirty-source check 4/4; facet-finish checks
8/8. Budget/loop/ledger targeted run: 187 passed, one new assertion corrected, then 8 stop/cap/approval cases passed.
Full suite: **2,465 passed after isolated reruns, 11 skipped**. Main run: 2,224 passed, 11 skipped, 4 failed in
603.37 s (8 workers/loadfile). The head-comparison, fit-speed and 28-image render timing checks passed alone;
the seeing module plus the two timing cases passed 24/24 in 83.71 s. The fourth failure was the local test launcher's
descendant EcoQoS opt-out changing the initial state that `test_opt_out_self_reads_back_and_is_not_inherited`
measures; without that external watcher it passed 1/1 in 0.25 s. No test was weakened. Print suite: 237/237 in
197.37 s (2 workers/loadfile, nested bake pools capped at 2). Arview: 497/497 unit, 103/103 QA, TypeScript clean.

Evidence: `data/assembly/m0_{main,rerun,qos,print,ar_unit,ar_qa,tsc}.log`, with arguments, UTC timestamps, PIDs and
exit codes in adjacent receipt/status JSON files. BLAS threads = 1, EcoQoS opt-out, no overlapping heavy pools.
Open: the 11 pre-existing stale WP4 proof skips remain for M4a; WP5/WP6 owner closeouts and historical quality
limitations are unchanged. No open M0 defect. Next: M1 job intake, generic scene hooks and registry, offline proof.

## Assembly M1 (2026-10-13, $0; complete, implementation commit `92c0187`)

Added the generic job CLI (`create`, `show`, `estimate`, `approve`, `run`, `stop`), explicit-view photo intake,
verbatim owner facts/model requirements, an empty millimetre template, persistent scene hooks and fourteen async
tools. Runs require a single-use bound approval; defaults are max effort, $20 settled stop and $23 reserve cap.
Photos retain their original bytes and hashes; large transport copies are also bound. Intake rejects missing views,
duplicates and colliding camera stems. EXIF orientation now agrees across intake, fitting, comparison and image reads.

The scene records mutations and per-tool journal receipts, recovers interrupted mutations from the checkpoint index,
and verifies the exit checkpoint. Finish requires a validated current export and successful fixed-scale AR capture;
delivery checks the finished checkpoint again. Worker failures propagate instead of becoming ordinary compare results.
Reference rendering now supports arbitrary filenames, spaces, Unicode and canonical-name collisions, using safe
output aliases while retaining the actual photo identity. Canonical camera names and path guards are unchanged.
Camera pools use free commit memory (1.3 GiB/worker with 2 GiB spare), CPU count and a ceiling of 16; BLAS threads = 1.

Proof: `python -m glasses3d.tests.proofs.assembly_m1` built synthetic wrapped glasses, rendered three truth photos in
Blender, created a fresh job, and drove the real SDK through eight scripted calls: build, summary, fit, reference
render, compare, validated export, AR, finish. The final proof took **71.735 s** (`m1_proof3.log`), delivered job
`synthetic-assembly-proof-926ab6dc`, and verified every reference render used its fitted camera (including `front.png`
and `angle photo.png`). All original fit gates passed: IoU >0.97, contour <1.5 px, yaw/pitch errors <1.5 degrees.
Receipt audit: 16 transport files, 8 successful tool journals, 8 settled mock requests, 9 verified image blobs and
14 AR images; no unknown holds, raw image URLs or fake key in text receipts. Mock settlement was $2.542402;
**actual API spend was $0**, with non-loopback networking and production transports blocked.

The first proof used native antialiased photo fringes and failed side-view IoU (0.9521, angle error <0.1 degrees).
The known-truth fixture now colours Blender's hard silhouette, matching the existing synthetic fit fixture;
no fit gate changed. The three overlaps on the passing run before filename expansion were 0.9972, 0.9972, 0.9881.

Full Python suite: **2,588 unique tests passed after isolated/added-regression runs, 11 pre-existing WP4 skips**.
Main (8 workers/loadfile): 2,307 passed, 25 failed, 11 skipped in 730.22 s. Failures were one timing check, the
launcher's EcoQoS interference, a 300-second shared renderer timeout and its 20 follow-on failures, the lab's variant
image check under load, and one outdated assertion that `budget_status` had no timeout entry. The assertion now
checks the new tool deadlines and still refuses an unknown tool. Isolated rerun: 58 passed in 65.79 s; EcoQoS:
1 passed in 0.29 s. After the filename fix, the full Seeing module, new routing regressions and registry tests:
74 passed in 85.49 s. Print (2 workers/loadfile): 237 passed in 184.16 s. Arview: **497 unit + 103 QA passed**;
TypeScript clean. No timeout, numeric gate or quality threshold was increased.

Evidence: `data/assembly/m1_{main,rerun,qos,print,ar_unit,ar_qa,tsc,seeing,proof3}.log`, with UTC timestamps, arguments,
PIDs and exit codes in adjacent receipt/status files. No push or paid call. The first failed proof's temporary
fixture is local/untracked at repository-root `data/assembly/m1-proof-b8bb88d4`; automatic approval review blocked
its cleanup move with the stated reason "blocked by policy". It was excluded from the explicit-path commit.
Open: M2 instructions/image policy/estimate, M3 owner review, M4 specialist stages, and the existing WP4 skips.
A pre-existing library-only compare crop-name collision (`crop-01` photo plus generated `crop-01`) is not exposed by
the generic registry (which has no crop argument); keep it in view when assembling M4 packages. No open M1 tool defect.

## Status

| WP | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b | Complete and tested |
| WP4 | Geometric tools complete and tested; the visual half of `check_lens_seating` (junction captures) and photo-derived hide ranges were never built. The last proof grades P3 34/40, P4 109/112 (+2 owner pending), P5 31/32 with 5 open owner decisions (review 2026-10-13, §5). The real-product proof is **stale** (whole-file hashes; changed: `g3d/__init__.py`, `g3d/export_glb.py`, `g3d/seat/geom.py`, all inert for WP4): 11 of 24 `test_seat_real` tests skip. Re-run `python -m glasses3d.tests.proofs.wp4_proof` (~3 min), then `test_seat_real` |
| WP5 | Complete by owner visual acceptance 2026-10-08, FT1119 only: observed-slope join + Poisson is the default (`4304c1d`). Not an all-proofs pass: A4/A5/B3 failures stay recorded ([WP5_HANDOFF](WP5_HANDOFF.md)) |
| WP6 | **Complete by owner acceptance 2026-10-13** ("you know what. im satisfied with wp6, lets wrap this"), after paid run 8 `facet-astra-008` on the v3 tools ([WP6_V3.md](WP6_V3.md), section "WP6 v3" below). Result: run 8's best checkpoint c0015, revision 25 (`data/wp6v2/viewer/new_astra_run8.glb`). Left open at closing: the hinge-side face reads too faint (built 2.2° off the logo) and Astra's images pile up unlabelled ("Open at closing" below). (v2 stopped by the owner after run 7; v1 retired 2026-10-10, tag `wp6-v1-final`) |
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
- Owner sheet: `data/wp6v2/result_facet-astra-002/` (tests/proofs/wp6v2_result_sheet.py, deleted 2026-10-13). The
  owner's verdict is pending.

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

**Owner feedback on facet-astra-003, verbatim:** "i meant 4 and the results look like it really didnt recognize it"
(face 4 = the thin band above the logo, the top of the temple's front block). Evidence: run 3's top photo (03)
outline, photo minus model, both sides: d30 +1.5, d40 +0.7, d50 −1.7, d60 −2.1 against a common −0.6. From above the
real front block is broad and widest near d 25–30; run 3 is slim there and widest at d 50–60. Astra's own summary
named "minor top-view shoulder/transition offsets (about 1–2 mm)" and still finished "matched".

**Fixes for run 4** (owner: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
stop"):
- Finish precondition 5 (`matched` only): the top photo's outline measured and within 1 mm of its common offset at
  every station; every compare that shows the top photo prints its outline line (WP6_FACETS §4.1).
- Instructions: judge top and bottom faces from the top photo (edge-on from the side); the outline line's stations
  are shape, not camera error; the top photo may be oblique (a station is one image row); a corner in the top
  outline needs separate facets meeting at a node; a thin band takes a crease + rise plane; finish before the cap.
- Builder `W_FADE_EARLY` (core wp6-core-2.2): a fade that leaves its facet flat for under 5 mm and under a quarter of
  its length (run 3's tail: 0.1 mm).
- Tool test ($0, a COPY of run 3's package, `data/wp6v2/tooltest_face4/`, never Astra input; photo-minus-model mm at
  the real top-photo compare):
  - run 3's best head: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5 off;
  - its layout with node depths A, C, D and the crown's rise searched (45 tries): best d30 +1.3, d60 −1.2;
  - face 4 split from the upper-back face (A-B-C and B-F-C), depths and rises searched (~50 tries): best d30 +1.3
    alone; 35 tries with face 4 through its three nodes were refused E_ILL_CONDITIONED;
  - a fit on the mesh's widest point per d (ignoring the oblique top camera, pitch 60°) passed on the mesh but read
    d30 +1.8, d40 +3.4, d50 +2.8, d60 +2.2 at the camera.
  No tested head met the rule. Whole models at the same compare: the original and the owner's earlier "preferred"
  shape fail it (preferred d30 +2.2, d40 +2.9, d50 +1.8); only Astra's rejected c0003 passes, with d40 unmeasured
  (now refused as unmeasured). Told to the owner before the run: the rule pushes Astra to work on the top view;
  passing it does not make a shape good; Astra may finish "stuck".
- Pre-flight review (5 reviewers + 5 verifiers): fixed the T5 outline check, unmeasured stations passing, 0.1 mm
  deviations, the oblique-camera wording, W_FADE_EARLY's length rule and wording, this paragraph's claims, and the
  money plan: the owner named one number, so the cap is $15 (the budget guard), which spend can never pass; the
  guard keeps room for the reply in flight, so the run ends near $13 at the latest. No separate watcher.

**Paid run `facet-astra-004` (2026-10-11).**
- Approval in the owner's words: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
  stop". Cap $15 (the budget guard), effort max, no reply cap, K 4. Code `e138736`; T5 `t5_004` 27/27.
- 15:01:06–15:30:05, 32 requests, **$8.209531** settled. Astra finished on its own as **"stuck"** (the outline rule
  was never met; no finish refusals). Best checkpoint c0019 (revision 17); export `head-38c91e136fe6ab7b.glb`
  (sha 0857568818…); validate_spec ok.
- Layout: hinge, logo, sweep, tail and two upper faces. **Face 4 is its own facet** (`upper_front`: hinge top, the
  top-outline peak, the logo corner; crease + rise −0.59, steeper than run 3's −0.35). The upper back is a separate
  face (`upper_rear`), with a ridge between them. Fades start at 77 mm, so no face fades from its own corner.
  Seven creases declared; roll 0.12 mm.
- Top photo outline of the best head: only d30 +1.45 beyond 1 mm (run 3: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5).
  Astra's summary: about 1.4 mm short at d30; its last three distinct changes moved the error elsewhere.
- Sheets: `data/wp6v2/result_facet-astra-004/` (faces_run4.png, top_run4.png, reference-0N.png, black-side.png); the
  3D viewer's default is run 4.

**Owner feedback on facet-astra-004, verbatim:** "the only issue that i still see is the top triangle. please
investigate. (its split into two)". Found: the real top triangle (face 4) is ONE long thin face (hinge top, the
top-outline peak, a point far back; its lower edge one straight line through the logo's back corner). Run 4 added a
crease from the peak down to the logo corner that no photo shows (`upper_front` | `upper_rear`). Contributing causes:
the run-4 instruction "A corner in the top photo's outline is a corner of the faces …" (now removed), the
coordinator's own split tool test, and layout decided from one viewing direction; run 2 reached the same split
through a builder refusal (a single face 4 refused because every crease at the logo corner was declared a ridge, and
"do not redeclare" left splitting as the way out). Evidence: `result_facet-astra-004/top_triangle.png`; run 4's creases drawn on the black angled
photo with a transported camera cross the top face where the photo shows no edge (review notes).

**Review of Astra's guidance (2026-10-11).** 26 of 106 instruction sentences were not general (20 added from this
product's failures); Prada assumptions also sit in code (packaging defaults, "acetate" in briefs, gloss-black for
every colourway, fixed section stations, the top-only outline rule). Why the layout rests on one image: the prompt sent
every position reading to the side photos ("never from oblique views") and asked for no check from another angle;
the "two photos agree" rule compared one direction with itself (B1 uses 05's camera, 01 is its mirror); the black
angled photo had no camera; in run 4 the layout was fixed before Astra first saw an angled view, and the top-photo
rule kept it chasing one photo's numbers (13 of 14 judgements of 03). Not money, resolution or context.

**Owner decisions after the review, verbatim:** "yes, build 1-3 and make the rule information only" (1: general
instructions with per-job facts in the brief and a confirm-from-another-angle habit; 2: cameras for the black angled
and front photos; 3: variant sheets that show black photos with each variant's crease lines). "please max utilize
local machine for faster work" (pytest-xdist pinned; builds and reviews run in parallel). The outline rule is
information only since `2ca5be9`.

**Built for run 5 (2026-10-11; build workflow, review, fix workflow, re-review; all $0):**
- **General instructions** (`instructions.txt`, 12.8k chars): no product or photo-set facts; lay out in the square-on
  views, then confirm (keep / remove / add creases) in a calibrated view that sees the face at a moderate angle; a
  crease only where two directions show it; never split a face no photo shows divided; offsets and the outline line
  are information; the corner rule (one face straight through a corner: its creases cannot all be ridges; keep the
  face whole; decide with variants at the moderate and glossy views when no silhouette shows it).
- **Per-job brief** (`cli.brief_text`): each photo's direction, angle band to the outer face and points_px error per
  mm of depth, direction groups, glossy photos, number lines, camera-fit text, render light, revision 0, roll unknown.
- **Cameras for B2/B3** (black angled / front) by similarity transport from 02/04 (`g3d_host/camera_similarity.py`
  restored from wp6-v1-final; `tests/proofs/wp6v2_basis.py`): B2 0.82 px / IoU 0.992 (model check 0.934, temple
  0.875); B3 1.09 px / 0.992 (0.977 / 0.886) after the per-photo matte fix (white-background matte for B2/B3, B1 keeps
  its dark matte). Existing basis records byte-identical (`data/wp6v2/basis-pre-b2b3`, `basis-pre-b3fix` kept).
- **facet_variants** takes any photo with a camera (1..4, at most 3 targets), renders other colours in gloss black,
  and draws each model's crease lines (the compare overlay's rule, host-side, matches it to 0-2 px).
- **Builder:** E_CREASE_SHAPE names a node a facet runs straight through ("one is a valley or two of those faces are
  one ...; keep X whole").
- **Estimate:** output tokens per reply from runs 2-4 (1852 expected, 2064 high); up to 4 variant sheets.
- T5 rehearsal 31/31; reviews: no P1 left. Owner, verbatim: "you may do a paid run, only when your ready, max 15$".

**Paid run `facet-astra-005` (2026-10-11).** Approval in the owner's words: "you may do a paid run, only when your
ready, max 15$". Cap $15 (guard), effort max, no reply cap, K 4; code `bf0b699`; T5 `t5_005` 31/31; estimate_005
(40 replies $10.63, up to $13.78).
- 19:3x–19:54, 30 requests, **$6.766442** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0017
  (revision 14); export `head-dd42b6a10e649032.glb` (sha 7895814159…); validate_spec ok.
- Behaviour: the first compare included the angled view; Astra opened a crop of the black angled photo (B2) before
  laying out; every one of its variant sheets included B2. **The top triangle stayed one face** (`crown` A-P-R-B, no
  crease from the peak to the logo corner); one valley declared at the logo's back corner (`lower_sweep|tail_face`);
  `crown|logo` declared a valley; two thin bands along the top edge (`top_front_band`, `top_rear_band`, under 0.5 mm
  tall from the side) with a ridge at the peak. Roll 0.2 mm.
- Still off, by Astra's summary and the images: from above, the front block is about 1.9-2.5 mm too slim at d30-40
  (run 4 was closer there); a small lip at the crown in the front view.
- Sheets: `data/wp6v2/result_facet-astra-005/` (top_triangle_run5.png, faces_run5.png, top_run5.png,
  angled_run5.png, reference-0N.png); the 3D viewer's default is run 5. The owner's verdict is pending.

**Owner feedback on facet-astra-005, verbatim:** "the inward edge is the one thats really bugging me since the layout
is perfect. what caused it?" Found ($0; `data/wp6v2/tooltest_crown_ridge/`, review notes): the crown|logo crease is a
ridge in the photos (~80%; depth read from the angled views gives a ridge with probability 0.96). Run 5 built an 8°
valley because the logo's back corner (B) had t solve, so the builder took it from the crown's extrapolated plane
(5.41 against ~7.0-7.5 in the photos); a bracket chasing the top photo's outline (var013: five changes at once and an
explicit valley declaration in every arm) then flipped it. Astra never saw solved values or the flip against the head
(applying the variant said "no change"), and had no way to measure t from the angled photos (points_px gives the
model's t). Owner, verbatim: "ok, fix it and run again, max 15$".

**Built for run 6 (2026-10-11, $0; build, two review rounds, fixes):**
- facet_inspect prints solved node values (`solve -> 5.41`) and, per crease, a host margin in the builder's own
  planes (each face's corner farthest across the crease line, its offset in X = x_in(d) + t from the other face's
  plane, + = ridge; solved setting corners named). Receipts list `solved` moves and `margins` (flips, solved or
  fragile creases); `crease_vs_head` / `diff_vs_head` state shape flips against the head, also when a variant is
  applied with no edits and on facet_restore.
- photo_compare `depths`: t measured from a photo at (d, y) and a pixel (exact inverse projection; px per mm; miss);
  front/rear views pick the temple nearest at the model's t. Run 5's logo corner reads 7.0-7.4 in B2, 02 and B3.
- E_CREASE_SHAPE names the crease's solved corners; E_RELIEF_STEP names the rise_per_mm that closes it.
- Instructions (13.9k chars, cap 14,000): decide ridge/valley from evidence about that crease (measured corner t,
  read the host margin, never extrapolate t by hand; views that look along it); measured t for the corners that set a
  decided crease; one bracket answers one question; never change a crease's shape for an outline number; on
  E_CREASE_SHAPE first measure its solved corners. T5 38/38 (`t5_wip6e`).

**Paid run `facet-astra-006` (2026-10-12).** Approval in the owner's words: "ok, fix it and run again, max 15$". Cap $15,
effort max, no reply cap, K 4; code `7631a31`; T5 `t5_006` 38/38; estimate_006 (40 replies $10.77, up to $13.80).
- 00:15-00:34, 26 requests, **$5.075421** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0013
  (revision 13); export `head-8e43a634deb82aa3.glb` (sha daa5beb96b…); validate_spec ok.
- Fixed: the top triangle stays one face and the **crown|logo crease is a ridge**; Astra measured A, C, D, H and F
  with depths (C 7.49) and declared what the measured values built.
- New problems: the back faces fade from d 48 (Crown, Lower and Tail fade 48-82; Tail is flat 0.7 mm before its fade,
  W_FADE_EARLY fired 4 times and Astra kept the early fade from a bracket judged on B2 smoothness and the top
  silhouette), so the upper-back and lower-back triangles do not read as crisp faces; Front|Logo (the face beside the
  hinge against the logo) is a valley from the measured depths, a ridge in runs 3-5; the top triangle's rise -0.85
  renders it bright in the side view. Top view still 1-2.5 mm slim at d10-40 (Astra's summary).
- Sheets: `data/wp6v2/result_facet-astra-006/` (top_triangle_run6.png, faces_run6.png, angled_run6.png, top_run6.png);
  the 3D viewer's default is run 6. The owner's verdict is pending.

**Owner feedback on facet-astra-006, verbatim:** "run 6 looks closer, but as you said theres this inward issue and the
top triangle is still not one piece and being smoothen and not flat. please review evidence thoroughly and the tools
and instructions. please plan heavily for yourself (no need to stop to share the plan) and fix, after the fixes come
back when youre ready for a paid run and illl authorize it. run 6 is better than run 5 but there are still issues as
mentioned above.."

**Found ($0; corners triangulated from photo pairs with the job's own cameras; `data/wp6v2/tooltest_run7/`):**
- The smoothed, "not one piece" top triangle is the fade. Crown, Lower and Tail fade over d 48-82, so the crown is
  flat only over d 0-48 and blends into the arm behind. Astra faded to hide a wing:
  - the crown's tip E was solved from the crown's plane at t 9.8 (d 90), about 4 mm proud of the arm;
  - top view −3.2 / −4.3 mm at d 70 / 80; B2 "an over-raised wing";
  - the crown's lower edge A-C-E is one straight line, so with A 3.55 and C 7.49 the tip took C's height.
- A 3.55 was a one-photo reading at an assumed (d 0, y 10.2).
  - In B2 a d error moves the pixel along the depth line, and the inner face turns 0.78 mm per mm of d at the hinge.
  - The same corner picked in B2 and B1 sits at d −1.58, X 70.71: t 4.77 at d 0.
  - The hinge-bottom corner H reads 4.8-5.4 the same way (Astra 5.52).
- The inward hinge-side edge (Front|Logo valley, H 1.6 mm outside the logo's plane) came from the same A reading.
  With A and H read in two photos and C solved from one flat crown, it is a 1.9° crease on the ridge side
  (H +0.4 / C +1.2), within the readings' error.
- Readings of one corner differ by 1-2 mm between cameras (02's fit: mean contour 7.2 px).
  - C reads 7.5-7.9 from the angled photos and at most 6.9 from the top photo's outline.
  - One flat crown through A, T and the measured tip (t 5.25 at d 81) solves C at 5.8.
  - The top view then has no wing (d 60-110 within 0.7 mm).
- My run-6 rule "measure, never solve, the corners that set a crease" pushed C's thick reading in and the tip out.
- Every paid run (2-6) faded the faces on both sides of a crease that runs on (the crown's back crease near d 90;
  runs 3 and 6 from d 47-48). The owner's earlier remarks match: run 2 "the each triangle line is very soft", run 6
  "smoothen and not flat".

**Built for run 7 ($0):**
- photo_compare `depths` items take `also`: the same point in 1-3 more photos with a camera (`lab.corner_reading`).
  - The host finds the point by least squares over the pixels.
  - It prints first the t to enter at the reading's own (d, y) ("t 4.77 here"), then where the point is, then each
    photo's residual.
  - It refuses photo pairs that see the point along nearly one line, using the pixel Jacobian's condition number
    over 20 (B2+B1 1.9; the opposite side views 05+01 about 940).
  - A one-photo reading says where the inner face turns fast.
- Builder (core `wp6-core-2.3`):
  - A crease + rise plane may hold the ends of a straight run of its polygon's edges (`spec.crease_run`, corners
    within 0.5 mm). This is how a thin band such as the crown gets its tilt from a rise, with both ends measured.
  - A run corner off the line that another facet meets on both run edges is `E_SPEC`, checked before and after snaps.
  - `E_FADE_CREASE`: a crease stands to its ends. Two facets both faded anywhere along their shared crease are
    refused. The fix offered: start the fades where it ends, or let one stand.
  - `E_FADE_CLIFF` and `W_FADE_EARLY` no longer advise fading both facets of a crease alike.
- Refusal notes:
  - `E_FACET_NOT_PLANAR` lists each given corner solved alone and its move, smallest first ("C -> 5.84 (−1.90),
    E -> 8.45 (+3.20)"). It also names the straight run a crease plane can hold ("; or a crease plane on the run A-E
    (through C)"). For one given corner well off a crease line, it names the rise_per_mm that meets it.
  - `E_ILL_CONDITIONED` names the corners without a value and the straight runs.
- Instructions (13,974 chars, under the 14,000 cap by cutting wording):
  - read corners also in a photo from another direction, and enter the t printed for your (d, y);
  - a crease plane on a straight run's two ends;
  - when a face's measured corners fit no plane, take the run or rise named, else solve the corner that moves least,
    and keep it solved;
  - a crease stands to its ends; never fade to fix a thickness (read the end corner instead);
  - a crease left open by its readings and by the views along it: enter values within the readings' error that
    build a ridge, unless a bracket shows a valley;
  - a crease built flat stays undeclared.
- Tool test (labelled copy of package_006, local Blender): run 6's layout with the two-photo corners, the crown on
  A-E and no fades, builds. In the black side view the top triangle is one flat band to its tip and the back faces are
  crisp (`tooltest_run7/owner_top_triangle.png`).
- Reviews:
  - round 1 (4 reviewers, 4 verifiers): 3 P1 and about 10 P2, all fixed. The P1s were:
    - the guard could be passed by moving a crease end inside the outline;
    - the corner text printed the misleading t first;
    - the tie-break, flat-crease and solve rules looped;
  - round 2 (2 + 2): 1 P1 (the "end it at a node, fade from there" advice rebuilt the smooth surface through a base
    strip) and 7 P2/P3, all fixed;
  - a final check of the round-2 fixes: 1 P1 (a printed rise missed by rounding), 1 P2 (the run named with no values
    at its ends) and 2 P3, all fixed.
- Tests:
  - T5 `t5_wip7b` 39/39 (new: `corner_reading_from_two_photos`).
  - Full suite from the repo root: 2295 passed. 4 failed: the stale WP5 test and three load-sensitive tests, which
    pass alone. Builder, lab and CLI suites rerun after the final fixes: 379 passed.
  - `FX.fade`'s ridge now ends where its fade starts. The former dying ridge (`ridge_to=60`, `legacy_fade`) is kept
    for the fade-value tests with the guard off.
  - Recorded runs 2-6 revisions with both-faded creases are now `E_FADE_CREASE`.

**Prepared for run 7 ($0, code `9fb64a5`):** T5 `t5_007` 39/39; `estimate_007` (40 replies at effort max: expected $10.82,
up to $13.84; runs 5 and 6 cost $6.77 and $5.08); `package_007` and the request `facet-astra-007` (cap $15 by the budget
guard, effort max, no reply cap, K 4), unapproved until the owner's words are recorded.

**Paid run `facet-astra-007` (2026-10-12).** Approval in the owner's words: "yes, run it with the $15 cap". Cap $15
(budget guard), effort max, no reply cap, K 4; code `9fb64a5`; T5 `t5_007` 39/39; estimate_007 (40 replies $10.82,
up to $13.84).
- 08:11-08:27, 20 requests, **$3.846039** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0007
  (revision 7); export `head-0a4b0e841a2bfdb2.glb` (sha f5895ca520…); validate_spec ok.
- Astra read every corner in B2 + B1 with `also` (the hinge corner: t 4.42 here, against run 6's 3.55). On
  `E_FACET_NOT_PLANAR` it solved the logo back corner for the crown's plane (6.15; its readings 7.6-7.8) and kept
  the crown whole, with the peak node at the triangulated position.
- **No fades.** The top triangle (crown A-K-E-H) is one flat face to its tip. Every crease on the front block is a
  ridge, including the hinge-side edge (logo|nose ridge 3.3°). As in runs 3-6, the edge between the two back
  triangles is a valley (7.6°).
- Still off, by Astra's summary: the logo's diagonals are too shallow; the top view is short near d 30-40 and wide
  near d 80; the rear arm is broad around d 90-110.
- Sheets: `data/wp6v2/result_facet-astra-007/` (top_triangle_run7.png, faces_run7.png, angled_run7.png, top_run7.png);
  the 3D viewer's default is run 7. The owner's verdict is pending.

No further paid call is authorized. Each one needs a cost estimate and the owner's explicit OK.

## WP6 v3 (2026-10-12/13)

**Investigation (owner, 2026-10-12: "please work on wp6 ... review all code related to wp6 very very thoroughly ...
review all the evidence of the runs ... what tools are actually good, how to write the prompt ... what astra did good
but our tools werent good for it. come back with findings").** Two review rounds, 117 agents, $0. Findings:
`data/wp6v2/review_20261012/FINDINGS.md` (reports beside it). In short: the real top triangle is not one plane (K
+1.5..+2.0 mm off the plane of its other corners, a 4.1-4.4 deg kink in the angled photos), the exact-planar builder
had to put that misfit in one place, and each run's newest rule chose the place (fold, proud tip + fades, K pushed
in); every v2 run put the crown's tip 8-20 mm too far back; photo 03's front-block outline cannot be matched with the
other photos under the camera model (up to +2.2 mm) and was printed as shape; no render light reproduces the photos'
facet tones; Astra measured crisp corners well (0.2-0.5 mm) and executed the newest rule literally.

**Corrections to this file (evidence in FINDINGS.md §5 and `review_20261012/w2/root-cause.md` §4, with
`w2/critic-skeptic.md`'s fixes):** the stop entry's "underlying limit was camera disagreement of 1-2 mm per corner"
is wrong for the crown (its readings agree within 0.3-0.45 mm; the run's 1-2 mm pair differences were mostly picks on
different features); "face 4's lower edge runs straight through the logo's back corner" holds only in the side views;
"front block slim from above at d30-40" (runs 3-7) is mostly the 03 camera-model conflict; the "true A 4.77 at d 0" is
the logo's front corner, not the crown's vertex, and the "measured tip t 5.25 at d 81" is 4-5 mm too far back; the
run-7 tool test log also showed Logo|Lower flat 1.7 deg; Astra did see B's solved value in run 5's charts; "fading
diagonals" (v1 entry, Decisions) were Astra's own reading of the cream photos, and the diagonals end where two edges
cross.

**Rebuild (owner, 2026-10-12: "please fix wp6. do very very hard work!!! come back to me when you think we are good
to go with a paid run. please do your work with the conclusions from before. +max utilize this machine for faster
runs").** Design [WP6_V3.md](WP6_V3.md). Built in three phases of parallel tracks with adversarial review after each
(phase 1: 17 agents; phase 2: 21; phase 3: 29; plus a consistency pass):
- builder: `{"coef"}` and `"warp"` (mean value coordinates) facets, soft facet|base borders, one planarity tolerance,
  facet|base shape against the base, `model_edges`; every existing spec builds bit-identically;
- `g3d/temple/fit.py`: joint weighted least squares over read / typed / solved corners, one camera-theta turn per
  camera fit, declared shapes as constraints accepted within chi2 +4, refusals and warnings as facts;
- `agent/temple_lab/readings.py`: corner and crossing readings (traced edges intersected), pick and camera error,
  slide-along-edge facts, pick sheets;
- seeing: native-scale zooms with a light-independent geometry view, the model's creases drawn by identity with
  their angles, side-view outline offsets;
- lab/tools: `photo_read`, object note, fit receipts, finish rules; removed fades, crease+rise planes, solve notes,
  one-photo depths, the 03/06 offset tables, the outline line and facet_restore; VARIANTS_MAX 4;
- prompt `agent/temple_lab/instructions.txt` rewritten (8,972 characters, was 13,974): the object, the evidence
  ranking, an order of reasoning, few rules;
- run safety: the context guard stubs only outputs present when it fires, a request payload limit, a machine-load
  preflight before a paid run.
Engineering choices (coordinator): ACCEPT_DCHI2 4, TYPED_SD 0.5 mm, PRIOR_SD 5 mm, MIN_CREASE 2.5 deg, THETA_SD 0.3 deg,
CAMERA_PX_SD 1 px, warp = mean value coordinates (a d-ruled surface gives the same turn between H and K), prompt cap
9,000 characters.
Proofs ($0): T5 v3 47/47 (`data/wp6v2/lab/t5_v3b`); full suite from the repo root 2642 passed, 7 Blender/timing
failures that pass alone, and the stale WP5 test_a4 (untouched, WP5 closed); estimate `data/wp6v2/lab/estimate_v3.json`
at effort max: 40 replies $15.02 (up to $21.67), 60 replies $32.22 (up to $46.11). Commits 8378bc4, 4c24124.

**Blind stand-in rehearsal (2026-10-13, $0, TOOL TEST).** A Claude agent played Astra with only instructions.txt, the
brief, the photos and the real registry (harness `review_20261012/v3/standin/drive.py`, report STANDIN_REPORT.md). It
found the photos' layout itself (logo, four faces around it, the top triangle as one warp face because its lower edge
bends at the logo corner, the faceting ending at d 74) and finished "matched" in 31 calls (c0013); its one typed depth
(the top triangle's peak P) made the top ridges 44.5/55.3 deg and the band render white. Prompt additions it showed
were missing: faces clipped by corners beyond a curved outline; the top strip's boundary next to the outer side is
that side's edge; an open edge sign is declared a ridge (the convex prior fills open signs only). With them it read P
(t 5.45; the review's independent 5.29-5.50) and finished again (c0015): top triangle|logo 22.6, top|upper-back 32.8,
logo back diagonal 6.5, hinge-side edge a 2.5 ridge (readings -3.5 +- 3.0: open). Owner-complaint checklist
(WP6_V3 §8, `data/wp6v2/result_v3_rehearsal2/checklist.md`): c0015 holds 9 of 10 (the tip at d 74.0, 1.5 mm short:
F unread), run 7 7 of 10. Options sheet: a fold adds a 10.6 deg line inside the top triangle; flat brings back the
logo corner 1.75 mm in and a 3.2 deg back diagonal. Sheets `data/wp6v2/result_v3_rehearsal2/`; 3D viewer default
`viewer/v3_rehearsal.glb`.
Final checks: full suite (10 workers) no WP6 failure (load-only failures pass alone; stale WP5 test_a4 untouched); T5
`t5_v3c` 47/47; estimate `estimate_v3.json` 40 replies $15.06 (up to $21.71), 60 replies $32.27 (up to $46.18).
Package `data/wp6v2/lab/package_008` and request `facet-astra-008` (cap $30, effort max, no reply cap, K 4) prepared,
UNAPPROVED (request sha 6d205b95…, registry 5613828a…).

**Paid run `facet-astra-008` (2026-10-13, the first v3 run).** Approval in the owner's words: "you may run it with max
18$ (20$ hard cap). im waiting here for the results, please let me see them in a 3d viewer". Cap $20 (budget guard), a
watcher stopping at $18 settled (`data/wp6v2/lab/stop_at_18_008.py`, never fired), effort max, no reply cap, K 4;
package_008 (code 7a3f295, request 6d205b95…; the earlier $30 request set aside unused under `runs-unused/`).
- 07:26-07:57 local, 40 requests, **$8.993582** settled. Astra finished on its own as **"stuck"**; best checkpoint
  c0015 (revision 25); export `head-681a8190c2cd3fd0.glb` (sha d8e6b88a…); validate_spec ok. In the 3D viewer as
  "Astra run 8 (v3 tools)", the default (`data/wp6v2/viewer/new_astra_run8.glb`).
- Behaviour: its object note named the photos' five faces (fore, logo, upper = the top triangle "one face even if it
  needs warp", lower, tail with a soft border into the arm), the top strip as the top surface, and the 03/06 conflicts
  as "not depth targets". It read all 7 corners as edge crossings in B1+B2 (0 typed values), built the top triangle as
  one warp face (1.48 mm off one plane), declared the open back-triangle line a ridge (2.5°), tried a ridge on the
  hinge-side edge and left it undeclared when the fit refused it (valley 2.2°, open), then spent its last third on the
  arm's end ("the oblique hook/end-cap shape in 02/B2" is its named remaining blocker).
- Final creases: top triangle|logo ridge 28.1° (26.6-38.8), logo back diagonal 7.9°, back-triangle line ridge 2.5°
  (declared, open), hinge-side valley 2.2° (open), tail|arm soft and flat 1.5°.
- Owner's review: the triangle closest to the lenses (the hinge-side face) does not show, its plane being barely
  different from the logo's (Decisions). Measured 2-4° from the readings; the photos' light turns that into a large
  tone step, our renders cannot.

**Closed (2026-10-13).** Owner: "you know what. im satisfied with wp6, lets wrap this". WP6 is complete on run 8 (c0015, revision 25). This
is the owner's acceptance, not an all-runs pass.

Open at closing (proposed after run 8, not built; the owner closed WP6 without them):
- **Image pile.** Astra's input is the whole conversation on every request. In run 8 it grew from 9 to 136 images
  (26k to 219k tokens) by request 40; images carry no revision or compare id and no current/superseded mark, and
  the context guard (240k tokens) never fired. Proposal: label every image, and replace superseded ones with short
  text notes every few replies (keep the photos, the latest compare per view, current pick sheets, the best
  checkpoint). This matters for any long Astra loop (WP7/WP8), not only WP6.
- **Visible faces.** An edge the photos show crisply can be measured at only a few degrees and is then built too
  faint to see (run 8's hinge-side face; the fit refused Astra's ridge declaration). Proposal: a declaration with a
  visible minimum angle, chosen from renders and honoured over the readings, plus one prompt rule.
- Astra's named blocker at its finish: the arm's end hook in photos 02/B2.
- Owner's later plan: a natural-language "more edits" box at the end of the automation, where Astra edits the
  model by the user's request (instead of parameter sliders).
- Leftovers, git-ignored: the $0 hinge-face tool test the owner stopped (`data/wp6v2/result_run8_hinge_face/`,
  with its never-committed script `wp6v3_hinge_face.py`) and its `run8_hinge_face_*.glb` entries in the viewer.

## Decisions (owner's words verbatim)

- **2026-10-13, decisions taken up front for the assembly (`ASSEMBLY.md` §4):** description vs photos: "Photos for the
  look" (photos win on shape and colour, the description on facts photos cannot show). Print tools: "astra can use it
  anytime it see fits. but we need the instructions to be good enough so astra will be able to decide to use the tool
  when it needs to (i.e. a tortoiseshell print and others means the tool needs to be used, while other frames dont need
  a print because their visuals arent complex, editing their material and color and finish is good)". Crystal frames:
  "Apply the check to all frames". Known-good product: "Oakley OO9208 v2". Per-run money: "20$ hard stop 23$ max
  reserve". Effort: max. Lens reflections: one fixed default. Total WP8 budget: "theres no reason to talk about this at
  the moment. this will be a thought when we'll have a full operational automation".
- **2026-10-13, process:** Claude plans, Codex executes (`ASSEMBLY.md`).

- **2026-10-13, WP6 closed:** "you know what. im satisfied with wp6, lets wrap this".
- **2026-10-13, after run 8:** "the closest triangle to the lenses is not visible since its plane is barely
  different than the logo. you can also see in the evidence that our algorithm didnt catch that triangle in any of
  the layout images. correct me if im wrong." Then, on edits: "lets focus on astra, what if we just gave it more
  images? at the end of the entire automation i plan to add "more edits" iput box where the user can say "this
  facets meeting point should be further out, so the left facet would have more degrees " and then should edit buy
  the user request. i like this combo better, what do you think?"; and, stopping the $0 hinge-face variants:
  "focus. stop all youre doing i dont need a fixed temple i need wp6 to work!!!!!".
- **2026-10-13, run 8:** "you may run it with max 18$ (20$ hard cap). im waiting here for the results, please let me
  see them in a 3d viewer".
- **2026-10-12, after the investigation:** "please fix wp6. do very very hard work!!! come back to me when you think
  we are good to go with a paid run. please do your work with the conclusions from before. +max utilize this machine
  for faster runs". WP6 resumed as v3 (above).

- **2026-10-12, after run 7: STOP.** Owner, verbatim: "its not fucking good. i dont now what to tell you anymore, i dont
  think this is fucking possible anymore and youve given me zero reasons to believe that you can make it work. every
  model has an other defect this is fucking exhausting", then, on the offer of one $0 feasibility check: "it sounds
  stupid, because how can we improve on astras understanding?????? we cant so stop fucking easting my money and
  time?!!!!!!!!!!!!!!!!". WP6 Astra facet runs are stopped: no further paid runs, no further work on this approach,
  no new proposals unless the owner asks. Runs 2-7 each fixed the defect named last and showed another; the
  underlying limit was camera disagreement of 1-2 mm per corner against facets whose shape hinges on that much.

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
The owner's 2026-10-13 WP6 acceptance (run 8's temples) closes WP6; it is not a whole-asset Prada verdict.

## Archive step (track F, 2026-10-10)

Removed (`git rm`): `g3d_host/relief_{anchors,crop,cue,edges,fit,hinge,junction,photo_mask,ridge,transition}.py`,
`g3d_host/camera_similarity.py` (restored 2026-10-11 in `bf0b699` for the B2/B3 camera transport of
`tests/proofs/wp6v2_basis.py`), every `tests/proofs/wp6_*` except the Blender driver, the camera/occlusion evidence
fixtures, and 33 test files that only exercised v1 code. Renamed `tests/proofs/wp6_bpy_run.py` →
`tests/proofs/temple_bpy_run.py` (the Blender driver of `test_temple_blender.py`; it imports no v1 module). Kept and
stripped: `test_temple_blender.py` (P0 instrument grade dropped), `test_temple_real.py` (glossy WP3b table test only),
`test_temple_design_checks.py` (builder warnings + the decal-standoff test moved in from `test_temple_relief_cue.py`).

The six relief modules then pending (`relief_trace`, `relief_check`, `relief_views`, `relief_rules`,
`relief_registration`, `relief_normal_residual`) were removed in build step 3. The stale doc links listed here were
fixed in the 2026-10-13 cleanup ([REVIEW_20261013.md](REVIEW_20261013.md) §7).

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
- `8378bc4`, `4c24124` WP6 v3 build (fit, warp faces, crossing readings, seeing, lab, prompt); `be8d56d`,
  `7a3f295` blind stand-in rehearsal fixes + owner sheets; `7fcf037` paid run 8; then WP6 closed by the owner.
