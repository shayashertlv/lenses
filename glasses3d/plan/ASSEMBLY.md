# Assembly plan: from separate tools to one automation (written 2026-10-13)

Written by Claude (the planner) for the coding agent that executes it (Codex). The owner decided on 2026-10-13: Claude
thinks, Codex executes. Read in this order before starting: `glasses3d/AGENTS.md`, `plan/PLAN.md` §0, this file,
`plan/REVIEW_20261013.md` (the code-level status and every defect cited here), then the design section a milestone
names. Where this file and an older design disagree, this file wins; where the owner's latest words disagree with this
file, the owner wins.

## 0. Decision: assemble now; fix only what blocks or corrupts assembly

The 2026-10-13 review read every line: WP1-WP6 are built as separate tools, each proven on one or a few products, and
nothing connects them (no job command, no tool registry, no delivery). Eight paid WP6 runs fixed Prada-specific
issues one at a time. More per-product fixing now would tune pieces whose interfaces the assembly will change. The
biggest open risk is integration (three tools already conflict when combined: review §1, §4), and the cheapest way to
find the remaining problems is a thin end-to-end pipeline run first on the known-good product, which needs no temples
and no print.

So: **M0** fixes the few defects that would make an assembled run unsafe or wrong; **M1-M3** build the job path and
the generic loop around the existing tools; **M4** wires the specialist stages one at a time; **M5** is the first paid
validation. Every milestone is $0 except M5. No paid call of any kind without the owner's explicit OK and a cost
estimate (AGENTS.md).

## 1. Working rules for this plan (in addition to AGENTS.md)
- One milestone at a time, in order. Commit after each milestone (explicit paths, branch `glasses3d`, never push), and
  append to `plan/PROGRESS.md`: date, milestone, commits, test numbers, open issues.
- Each milestone ends with its $0 proof. If the proof fails, diagnose and fix the general method; do not weaken the
  proof. Stop and report to the owner if a milestone needs a decision listed in §4 or more than its stated scope.
- Never hard-code a product, a photo set or a photo name in runtime code. Product facts live in job data.
- Before deleting or rewriting a module, grep its callers; the review's dead-code tables are in
  `data/review_20261013/reports/`.
- Run the full suite at the end of each milestone: `-n 8 --dist loadfile` for everything except `tests/photoprint`,
  then `tests/photoprint` with `-n 2` (the WP5 bakes start their own pools). Three wall-clock tests and the seeing
  module fail only under load: rerun them alone before calling anything a failure.

## 2. Milestones

### M0. Fixes that block assembly ($0, small)
1. **Paid-run lock is per folder** (`agent/budget.py:1334`, `agent/loop.py:1261`). Use one fixed lock root for every
   `JobLedger`: `glasses3d/data/.paid-run.lock`. Test: a lab package and a job dir in different folders cannot hold
   paid runs at once.
2. **Every export is validated.** `agent/seeing.py:231-260` (`export_head`) returns `validate_spec` (and
   `g3d_host.seat_check.check_glb` when any lens carries `g3d_seat`) with the export record, cached by the GLB sha.
   Tools that receive an export see the report.
3. **`facet_finish` refuses an invalid export** (`agent/temple_lab/lab.py:2632-2647`): `E_EXPORT` when
   `validate_spec` is not ok, so a paid run never ends on an invalid asset.
4. **Implementation snapshot command line** (`agent/snapshot.py:63-66`): it passes every tracked path to `git diff`
   (~23k of Windows' 32k limit). Use `git diff HEAD --binary -- glasses3d` with exclude pathspecs or
   `--pathspec-from-file`. Test with a synthetic 2,000-path list.
5. **Free token-count failures end paid runs** (`agent/budget.py:984-986`): bounded retry (3, backoff) for the $0
   `/responses/input_tokens` count before failing.
6. **`run_astra` result `turns_used`** (`agent/loop.py:1206-1228`): compute in `finally` from `log.interactions`.
7. **Test hygiene:** add `--dist loadfile` to the documented run (or an xdist group for `test_wp2_seeing.py`); cap the
   pool size of the WP5 bake fixtures in tests (`tests/photoprint/test_print_bake.py:32, 215`,
   `test_correction_round.py:499`) to 2.
8. **Settled-spend stop** (owner D5: "$20 hard stop, $23 max reserve"): a `RunSettings` field that ends the run
   (`budget_limit`) before starting any request once settled spend reaches it, separate from the guard's reserve cap.
   Test with the scripted transport: at a $20 stop / $23 cap a run never starts a request at ≥ $20 settled and never
   reserves past $23.
Proof: the affected tests plus new ones; full suite.

### M1. Job intake, generic scene hooks, tool registry v1, one offline end-to-end run ($0)
Designs to reuse: `plan/WP2_DESIGN.md` §10-§12 (read its 2026-10-13 note first: its G2 temple rows are obsolete).
1. **Job data** (`agent/jobs.py`, schema `g3d.job/1`): `job create --images DIR --views views.json --name N
   --description TEXT [--specs specs.json]`. Copies images into `data/jobs/<id>/images/` with sha256; `views.json`
   maps every image to a view kind (front, side, angled, top, rear, top_oblique...) and side; refuse a missing or
   unknown kind (no inference). Every image is authoritative (AGENTS.md).
2. **CLI** `glasses3d/__main__.py` → `agent/cli.py`: `job create | show | estimate | approve | run | stop`. `approve`
   writes the single-use approval bound to the request sha (`agent/approvals.py` already has the logic; there is no
   CLI today). `run` refuses without an approval.
3. **Brief** (`agent/brief.py`): description, specs, the owner's §2 model requirements, and each photo with its view
   kind; no product-specific sentences in code.
4. **Starting scene**: a template `.blend` built by a script (units mm, +Y up, +Z front, an empty
   `mdl_bridge_underside` at the origin, empty collections for frame / lenses / temples). Read
   `g3d/export_glb.py` for the exact origin property and `partRole` conventions before writing it.
5. **Generic `SceneHooks`** over `agent/journal.BlenderSession` (`agent/job_scene.py`): start / restore / checkpoint,
   `exit_checkpoint` (copy the lab's), `worker_info` with CPU (`WorkerClient.cpu_seconds` exists), a mutating flag
   derived from `checkpoint_after != checkpoint_before` (needed by `plan_rollback`, `loop.py:922-923`), and
   `context_stubs`.
6. **Registry v1** (`agent/tools/`, async function tools with `timeout=sdk_timeout_s(name)` and fatal worker errors,
   returning JSON text plus `ToolOutputImage`): `execute_blender_code` (`BlenderSession.execute`), `scene_summary`,
   `save_checkpoint` / `list_checkpoints` / `restore_checkpoint`, `fit_cameras`, `render_views`,
   `compare_to_photos`, `pick_surface_point` (all in `agent/seeing.py`), `export_glb` (with M0.2's report),
   `arview_capture` (wrap `node arview/bin/arview.mjs capture`; **always `--fixed-scale`** for Astra because the
   default auto-fit hides wrong sizes; a small pose subset; a contact sheet; map exit codes 0/1/2/3/4; kill the whole
   Chromium tree on timeout), `read_image`, `budget_status`, `finish` (accepted only with a validated export of the
   current head). Remove the seat names from the snippet facade (`g3d/lib.py:18`) when M4a adds the trusted path.
7. **Proof (WP2 F, Proof B):** build a synthetic product (a simple GLB made by a script), render its "photos" with
   `render_views`, `job create` from them, then `job run` with the scripted transport (`agent/replay.py`, 0 network):
   Astra's scripted replies build a frame + lenses with code, fit cameras, compare, export (validated), capture AR,
   finish. Output: `data/jobs/<id>/deliverable/model.glb` + `report.json`; receipts and money ledger complete.

### M2. Instructions and the image policy ($0)
1. **Rewrite WP7** (`plan/WP7_DESIGN.md`, read its 2026-10-13 note): follow the WP6 v3 lesson (`plan/WP6_V3.md` §1:
   Astra executes the newest rule literally; teach the object, rank the evidence, give an order of work, keep rules
   few). Content: what glasses are made of (front, lenses, bridge, hinges, temples, pads), the owner's §2 table word
   for word, photos authoritative and colour from photos (not the product name), room reflections on lenses are not
   defects, small branding stays small, the stage order of §3, how to use each tool's report. Tool mechanics live in
   tool descriptions, not in the prompt. New text; never copy the retired phrases (PLAN §4 WP7).
2. **Image policy for long loops** (PROGRESS "Open at closing": run 8 grew from 9 to 136 images, 26k to 219k tokens):
   label every image Astra receives (call id, checkpoint/revision, view), and every few requests replace superseded
   images with short text notes, keeping the photos, the latest compare per view, current pick sheets and the best
   checkpoint's images. Generalise `ContextGuard` + `SceneHooks.context_stubs` (`agent/loop.py:583-640`); the current
   guard fires once, one request late.
3. **Contract test:** every tool the instructions name exists in the registry; removed phrases absent; size budget.
4. **Recalibrate the estimate** (`agent/estimate.py`) on the g3d ledgers (run 8 grew 4,950 tokens/request against the
   Studio corpus's 2,360).

### M3. Delivery and the owner's review loop ($0)
1. `deliver`: copy the finished GLB, `report.json` (validate_spec, seating, arview report, cost, checkpoint), an AR
   contact sheet.
2. `job review <id>`: starts `arview serve` on the deliverable (detached; preview servers kill child runs) for the
   owner's webcam.
3. Verdict ledger `g3d.verdict/1` bound to the GLB sha256 (`accept` or `changes` + notes); migrate
   `data/ledger/owner_verdicts.jsonl`.
4. `changes` notes become the next run's input (`job run --resume` with the notes in the brief): this is also where the
   owner's planned "more edits" box plugs in (PROGRESS 2026-10-13).

### M4. Wire the specialist stages, one at a time ($0 tool tests on COPIES)
Order is fixed by the conflicts in review §4: temples before seating, print last.
a. **WP4 lens seating:** a trusted worker method `g3d_call{op, kwargs}` with an allowlist (`seat_lens`,
   `hide_eyewire`, `check_lens_seating`, `unseat_lens`, `unhide_eyewire`) plus tool schemas. Fix the rollback leak
   first (`g3d/seat_bpy.py:1022-1038` must call `remove_profile_bpy`). Narrow the WP4 proof's staleness hashes to
   seat code (`tests/proofs/wp4_proof.py:51-54`) and re-run the proof (~3 min). Needs owner decision D4 for crystal.
b. **WP6 as a stage, not a second loop:** a `temple_stage` tool: export the job's head → `template_from_mesh` → a
   per-job package built from the job's photos and cameras (move every Prada constant in
   `agent/temple_lab/cli.py:76-131` into job data; replace the Prada-only `tests/proofs/wp6v2_basis.py` with a general
   pose-basis builder) → run the facet lab with its own prompt and tools under a sub-budget of the job's approval →
   apply the fitted temples onto the job head through a trusted worker method. Required fixes: the lab must build on
   the job's head, not restore its own start (`lab.py:1337-1346`); carry the old UVs over by chart position instead of
   the fixed planar map (`g3d/temple_bpy.py:525-528`); the fit's inner-table bug (`g3d/temple/fit.py:2160`, use
   `cur_ps.inner`; the repro is in review report E); join all floating facet components in one pass
   (`g3d/temple/arrange.py:180-193`). Proof: Prada's T5 rehearsal still passes, plus the same stage on a copy of a
   second product's temples.
c. **WP5 print as the last stage** (open to Astra, Astra decides when a product needs it: D3): `photo_print` tool =
   `production.bake_print` with the job's cameras → `print_uv` / `attach_print` via `g3d_call` → export →
   `manifest.check_glb`. Required fixes first: manifest carries `texture_parts`, `attach_print` refuses no-texture
   parts (`project.py:966-976`, `print_bpy.py:739`); `job_from_dir` keeps only target-colourway photos
   (`inputs.py:301-317`); exact object names, not `.001`-stripped (`project.py:136`); zero-area faces do not crash the
   layout (`printuv/charts.py:180-183`); add a test pinning the owner-accepted FT1119 print (sha `ac4280e9…`, inputs in
   `data/wp5/slope_default_20261008/run.py`). **Before any non-havana product:** generalise the donor colour model
   (`photoprint/metal.py:91, 161-208`: blue/green/grey patterned acetate lose 89-99.9 % of donor pixels today).
d. **Camera fitting for new photo sets** (WP3b, needed by every stage): per-photo failure records instead of aborting
   all fits (`fit_views.py:381-382`); a rear three-quarter view kind; switch on the lens-optional loss for rimless /
   semi-rimless (`cameras.py:436`); honour the per-photo matte mode (`fit_views.py:317`).

### M5-prep. Make the first paid run worth its money ($0; added 2026-10-13 after reviewing M0-M4)
Claude's review of M0-M4 (`27db2c0`..`bd3dcf4`) found no blocker in the assembled offline path, and two money risks
for the first paid run. Do these before M5:
1. **Astra plans against the wrong limit.** The tool footer (`agent/loop.py:822-851` `RunStatus`/`footer_text`) and
   `budget_status` (`agent/tools/__init__.py:373-379`, `guard.summary()`) show the $23 reserve cap as "cap", but the
   run stops at $20 settled (`agent/job_run.py:21`); the first message's run line says "This run's budget cap: $23"
   too (`agent/loop.py:1233-1235` `invocation_text`), and `temple_stage` accepts any `budget_usd` up to the $23
   reserve cap (`agent/tools/temple.py:30-33`), so a stage can be given money the run cannot spend and be cut off
   with its temples unapplied. Check the stage allowance against the remaining money to the settled stop. Report the
   settled stop as the working limit in the run line and in the footer:
   "settled $X of $20.00 stop (reserve cap $23)", and `budget_status` adds `settled_stop_usd` and
   `remaining_to_stop_usd` (stop minus settled minus open holds). From 75 % of the stop, every tool result's footer
   adds one plain line: about $N left; export, AR-capture and finish your best checkpoint soon. Add one sentence to the
   instructions' `finish` guidance: plan to finish with the best validated checkpoint before the settled stop; a
   finish that lists remaining differences is better than running out. Scripted-transport test: a run whose spend
   crosses 75 % sees the notice, and the numbers match the guard.
2. **A run that stops without `finish` delivers nothing** (`agent/delivery.py:321-324` returns `not_finished`), so
   the owner would have paid and have nothing to judge. Add a **draft delivery**, at $0 and with no model request:
   when a run ends `budget_limit`, `turn_limit`, `stopped`, `ended_without_finish` or `output_limit`, the host takes
   the checkpoint marked best (else the head), exports it, runs `validate_spec` + the seating check and a fixed-scale
   AR capture, and publishes an immutable delivery marked `finished: false` with the run status, the checks' results
   (failing checks are reported, not hidden) and Astra's last summary if any. `job review` and `job verdict` work on
   drafts, so the owner's notes feed the resume. A draft is never an accepted finish and never certifies a check.
   Tests: each end status produces a draft or a clear "no usable checkpoint" record; a draft with a failing check
   is labelled so in its report and in `job show`.
3. **Smoke turn** (WP8 step 0): `job run --smoke <job>`: one capped real request (settled stop and reserve cap both
   the owner's smoke cap) with the job's real instructions, brief and photos, `tool_choice=required`, allowed to call
   one read-only tool (`scene_summary` or `budget_status`) and then end. It proves the real API, tool call, receipts,
   settlement and ledger end to end. Its own approval request and estimate (`agent/estimate.py:268`
   `smoke_estimate`). Offline test with the scripted transport; the real smoke waits for the owner's OK.
4. **The Oakley OO9208 v2 job, ready to approve** ($0): `job create` from the six photos in
   `automation/data/blender_agent/studio/jobs/3dff867466d449c9a6369273f0a14631/images/` (copied, sha256-bound), with a
   `views.json` you label by looking at each photo (view kind and side; ignore any "supplementary/unverified" labels in
   old job files, PLAN §0.3) and the owner's description from that job's `job.json` / `brief.txt`, verbatim. Then
   `job estimate` (record the estimate in PROGRESS) and one scripted $0 dry run on this real job that reads each photo
   (`read_image`), calls `scene_summary` and `budget_status`, and stops: it proves intake, brief, photo handling and
   the approval binding on real photos. Show the owner the six photos with their view labels in the report.
Then the smoke turn, which the owner approved in advance on 2026-10-13 ("Yes, cap $1"): run it once on the Oakley
job with a $1 settled stop and $1 reserve cap, only after items 1-4 pass and only if its fresh estimate is under $1;
record the owner's words in the approval. Then stop and report: the smoke result (spend, receipts, ledger), the
estimate for Oakley run 1, and the draft-delivery and runway tests. Oakley run 1 needs the owner's separate OK.

### M5-fix-1. Repairs after Oakley run 1 ($0; added 2026-10-14 by Claude from Codex's run review)
Oakley run `20261013T173404-2accf4` finished (accepted `finish`, $19.16, $0.84 left before the stop) but is not
complete in likeness: no green/cyan coating (the owner's stated priority), central vents ~79 % smaller after seating
repairs, no usable top-photo registration. The old Studio route built the owner-accepted Oakley for **$8.92**
(63 requests; studio job 3dff8674, runs 20261001T084925-d6d192 + 20261001T085658-b197ec). Same work volume, double
the cost. Measured from both runs' usage records:

| | old Studio (accepted) | new run |
|---|---:|---:|
| requests | 56 (+7) | 65 |
| requests that lost the prompt cache | 0 | 3 (51, 61, 64) |
| cache writes | $1.63 | $10.30 |
| cached reads / output | $4.75 / $2.06 | $5.90 / $2.87 |

The three cache misses are our image policy (ASSEMBLY M2 item 2, `ContextGuard` replacing old images) rewriting the
history prefix: $7.48 (39 %) of the run. The old Studio never edited history. In order:
1. **Append-only history.** Stop the periodic image replacement (`context_guard_every_requests` and the pressure
   epochs) for job runs; never edit earlier items of a live conversation. Keep the run under the long-context
   threshold by making tool results smaller instead (one compact contact sheet per call, not many full panels; no
   repeated full-size images). Prove with a $0 replay that re-prices this run's saved requests under the new policy
   (cache-miss count and cost), and that the six photos stay in every request.
2. **One `finish` call does the finalization.** `finish` itself exports the current head, runs `validate_spec` and the
   seating check, runs the fixed-scale AR capture and returns everything; a refusal lists what failed with the
   images. Today calls 58-65 (unchanged model) cost $7.37 across separate model turns at ~230k tokens.
3. **Lens coating that survives export.** The exporter keeps thin-film iridescence only with a glTF Material Output
   group carrying the exact `Iridescence Factor` socket and an `Occlusion` socket (Codex's four-case proof,
   `data/assembly/m5oakley/thorough-review/material-probe/`). Add a host helper `g3d.lens_coating(...)` (or a trusted
   tool) that builds that group correctly from Astra's parameters (factor, IOR, thickness range, tint), make the
   exporter warn whenever an authored thin film, transmission or other optical effect is dropped, and describe the
   helper in the tool docs. Check the exported coating in the fixed arview renderer at several poses.
4. **Baseline the instruments on the owner-accepted Oakley** ($0): run `fit_cameras` + `compare_to_photos` +
   `check_lens_seating` + `arview_capture` on the accepted Studio GLB
   (`automation/data/blender_agent/studio/jobs/3dff867466d449c9a6369273f0a14631/agent/ar/h_detailed_closed_shield-6c7d3bd5/model.glb`,
   or the asset bound in `data/ledger/owner_verdicts.jsonl`). Report its contour numbers per photo next to run 1's,
   and whether the accepted model **passes the seating check**. If it fails, that is evidence for AGENTS.md "Gates":
   show the owner the vents before/after and ask whether seating stays a finish requirement or becomes a report
   Astra weighs against the photos. Do not change the gate without that answer.
5. **Top photo registration:** let camera search cover image-plane roll for top/bottom views (seeds at ±90° and 180°,
   or roll bounds ±180° for those kinds), and report a fit with IoU below a usable level as `unusable`, not `ok`
   (a status label, not a new likeness gate). Proof: the Oakley top photo registers on the accepted GLB.
6. **`temple_stage` refusals that say the real cause:** pass through the export refusal text instead of "parent
   export does not match its current checkpoint" (`agent/temple_lab/package.py:83`); state the hinge-knuckle size
   heuristic (3.2 mm, `g3d/temple/measure.py:28,66`) in the refusal and the tool description so Astra does not spend
   turns on it; list `partRole` values the exporter accepts in the `execute_blender_code` description.
Then estimate Oakley run 2 with the new policy and stop for the owner's approval.

### M5-fix-2. After Oakley run 2 ($0; added 2026-10-14 by Claude; owner-agreed items only)
Run 2 (`20261014T084140-272538`, $15.47, 80 requests, draft) proved every M5-fix-1 repair works: coating exported,
vents kept through 8 seating operations, top photo registered, no cache losses, spec and seating pass. Its frame is
close to the owner-accepted Studio Oakley (AR sheets side by side); the visible gap is the lens tint (pale
lavender-grey with cyan spots vs the photos' dark green/purple). It stopped at $14.46 settled because one request's
worst-case hold was $8.35 (284,724 input tokens over the 272k long-context price step, assumed uncached, plus a
16,384-token reply) against the owner's $23 maximum; that request really cost $1.01. The context was that large
mainly because `check_lens_seating` returned an 848-row table (~100,599 tokens). Owner rules (2026-10-14): **no
capability is taken away from Astra; only the owner decides money.**
1. **Astra never chooses money.** Remove `budget_usd` from `temple_stage` (`agent/tools/temple.py:17-51`): a stage
   runs inside the run's remaining money under the owner's stop and maximum. Astra currently set $4 and $6 itself.
2. **No tool floods the conversation.** Cap every tool result's text (for example 12,000 characters). Long data is
   summarised (counts, worst cases, the failing items first) and the full record is saved to a file; tools with big
   tables (seating stations, fit details, compare metrics) take a page or filter argument so Astra can read any part
   on request. Nothing is hidden, only paged. This keeps requests under the 272k price step, so the worst-case hold
   (`agent/budget.py:615-626`, which is what guarantees the owner's $23 maximum and stays as it is) remains small and
   the owner's $20 is actually usable. Prove with a $0 replay of run 2's requests: peak input and the largest hold.
3. **Better information, not fewer tools.** Every specialist tool's description states exactly what it handles and
   what it refuses (temple stage: the temple types and hinge geometry it can capture, `g3d/temple/measure.py:28,66`;
   seating tools: frame types; print: patterned surfaces). The prompt's specialist lines (`agent/instructions.py`
   ~74, 124) say to check that description against the product's photos before calling, instead of "use
   `temple_stage` for faceted temples". All tools stay registered on every run.
4. **Plain money reporting** in every status and report: "spent $X (settled)" and, separately, "held for the request
   in flight $Y (released after it settles)". Never one combined "committed" number to the owner.
5. **Lens seating becomes a report, not a finish requirement** (owner, 2026-10-14: "i agree with the seating part").
   `finish` still runs the seating check and returns its full result (paged per item 2), the delivery report and the
   owner's report show it, and every seating tool stays available; `finish` no longer refuses because seating fails.
   The instructions say to weigh the seating report against the photos (a lens edge or support the photos show must
   look like the photos). This reverses D4's "required" part only; D4's "apply the check to all frames" stays.
6. **Give the main session the photo-measuring tool** (more capability). For ordinary temples the main Astra has
   Blender code, `g3d.loft_sections` / `polygon_section` / `check_round_corners`, compares at the fitted cameras and
   `pick_surface_point` (a point on its own model). It cannot read a 3D position from the photos. The facet lab's
   `photo_read` (`agent/temple_lab/readings.py`: a corner or edge crossing marked in 2+ photos with cameras,
   triangulated with an error estimate) is general, but only the temple stage has it. Register a general
   `photo_read` in the main registry over the job's frozen camera basis (hinge position, temple length and bend,
   bridge height, lens corners, vents), with the facet lab's honest error model and refusal of ill-conditioned photo
   pairs. Also state in `arview_capture`'s description that AR redraws temples (spread, clip at the ear,
   `arview/src/render/rear-drop.ts`, `temple-clip.ts`), so temple length and shape are judged in `compare_to_photos`,
   not in AR images. Proof: on the Oakley job's photos with the accepted model's basis, `photo_read` of the two hinge
   pods and the temple tips agrees across photo pairs within its reported error.
7. **A worn side view Astra can understand** (owner, 2026-10-14: hair off, a close ~50° yaw view of the glasses
   worn). Facts: hair is already off in Astra's captures (`arview/qa/capture/page.ts:246`); the 7 default poses go to
   ±45°; there is no visible face (plain skin background, `--no-portrait`), yet an invisible head proxy hides the
   temple parts behind the head and ears and the renderer spreads and clips the arms there
   (`arview/src/render/rear-drop.ts`, `temple-clip.ts`, `temple-visibility.ts`, `temple-head-shell.ts`,
   `face-surface.ts`), so Astra sees temples end with no visible reason. Add to `arview_capture` two close worn views at
   yaw +50° and -50°, cropped from the lens edge to past the ear, hair off, with the head the renderer already uses
   for hiding drawn as a visible plain skin-shaded head (any angle, no new face image). Keep the 7 existing poses and
   the fixed scale. The description says these show how the temples sit when worn; temple length and shape against
   the product are judged with `compare_to_photos` and `photo_read`. Proof: on the owner-accepted Oakley the two
   views show each temple meeting the visible head exactly where its hidden part starts.
Then the full suite, commit, PROGRESS, and stop: no paid run without the owner's approval.

**Status 2026-10-14 (Claude's review of `6fb020e`, `3614879`):** items 1-5 and 7 done as written. Claude
reproduced the item 2 replay (peak 87,477 tokens, largest hold $1.912663), reran `tests.proofs.assembly_m1`
(passed, 92.7 s, 11 requests, $0), printed the exact run-3 inputs on a copy of the job (instructions 7,835 chars,
brief unchanged, 23 tools, no dollar argument anywhere, Astra sees only the owner's $20 stop and $23 reserve cap)
and reran the camera, report, measuring and money modules (166 passed, 2 skipped). Repricing run 2's 80 requests
with capped outputs gives about $10.64 instead of $15.47, of which $5.10 was the two `temple_stage` attempts that
the new description now scopes away from Oakley's multi-material, branching arms.
**Item 6: the tool is delivered; its proof criterion was Claude's error and is withdrawn.** The criterion asked
every photo pair to agree within the reported error. The owner-accepted Oakley's own frozen cameras fail it (same
hinge logo 7.4-9.3 mm apart between front+side and front+top; top photo IoU 0.64-0.75), so it measured how far the
photos' fitted cameras agree, not whether the tool works. The tool reports that disagreement to Astra
(`W_PAIR_DISAGREEMENT`, `W_RESIDUAL`, "camera calibration can limit accuracy"), which is the honest behaviour.
The off-axis camera option stays as an explicit choice (default unchanged). Calibration research stops here: it
needs independent calibration evidence these photos do not contain and it does not touch what stopped run 2.
Next: Oakley run 3 on the same job, only with the owner's explicit approval and estimate.

### M5-fix-3. Lens transparency after Oakley run 3 ($0; added 2026-10-14 by Claude)
Run 3 (`20261014T142845-c99f84`) finished by itself at $6.48 (43 requests, no `temple_stage`). Owner, by webcam:
"the color of the lenses isnt right (its close tho) and theyre have no transparency". Codex's $0 review (PROGRESS
"Oakley run 3: owner colour/transparency review", `f4d34f3`) reproduced it and isolated the cause; Claude checked its
images (`data/assembly/m5oakley3-review/transparency/portrait-eyes-comparison.png`, `front-checker-comparison.png`):
the eyes are hidden in run 3 and come back when only `/materials/6/doubleSided` changes from true to false
(background response 0.016 → 0.121; accepted Oakley 0.134). Cause: Astra's closed shield lens exported
double-sided (Blender follows `use_backface_culling`, which Astra never set), and Three draws the back faces of a
double-sided transmissive material into the transmission input on this GPU, so the front samples an image the back
already darkened. Astra could not see this: `arview_capture` renders only skin backgrounds with `--no-portrait`
(`agent/tools/arview.py:119-121`), so no eyes or pattern were ever behind its lenses.
1. **The exporter writes closed lenses single-sided** (general, in `g3d/export_glb.py`, where lens-role material
   copies are made, ~685-705, or in the post-export JSON patch, ~729+). For a see-through material (exported
   transmission > 0: lenses, and crystal/translucent frame parts, which hit the same Three behaviour) whose every
   mesh is a closed 2-manifold with outward normals (positive signed volume), export `doubleSided: false`; a closed
   solid never needs its back faces drawn. An open or inward-facing mesh keeps its authored setting and the
   export report says why in one plain note, so Astra can fix its mesh. Opaque materials are untouched.
   The export receipt records which materials were changed. Do not edit the viewer: the GLB must be right for any viewer.
   (Amended 2026-10-15 by Claude: from lens-role only to every see-through material; same cause, same rule.)
2. **Astra sees eyes and a pattern behind the lenses.** Add to `arview_capture` (and therefore `finish`) the front
   worn portrait on the CLI's existing pinned test face (`--no-portrait` removed) and one front view on the existing
   checker background. Keep everything it has now (the 7 skin poses, the ±50° worn views, fixed scale, hair off,
   the one fixed lens-reflection setting). Lay the sheet out so the eyes behind the lens are clearly legible; all
   native images stay in the report and the delivery. Report the lens background response you measured in this
   review (same method as `analyse.py`) per captured view as information, not a gate. The description says these
   show how see-through the lenses look when worn, to judge against the photos.
3. **Proof ($0):**
   - (a) re-export run 3's checkpoint c0007 in a copy job with the fixed exporter: only the lens `doubleSided`
     changes (plus receipt/note); its capture shows the eyes and its response matches the diagnostic copy (~0.12);
   - (b) synthetic cases: a closed outward lens and a closed outward crystal frame part become single-sided; a
     closed inward lens and an open lens surface keep their setting with the note; opaque double-sided materials
     are unchanged;
   - (c) `tests.proofs.assembly_m1` and the affected suites as §1 describes.
4. **Show the owner.** Serve the (a) re-export in the webcam review, labelled as "run 3 re-exported with the lens
   fix, same Astra model, not a new delivery", so the owner can judge the lens colour with transparency restored.
   Ask him one question: is the lens colour now right, close, or wrong? Record his words verbatim. Do not change the
   colour, coating or any geometry, and do not start a paid run.

### M5. Paid validation (WP8; owner OK and estimate before every run)
0. One capped smoke turn (a few cents) through the real API.
1. The known-good product, Oakley OO9208 v2 (owner, D5): frame + lenses + seating only. This
   tests the assembly, not a hard feature.
2. Prada (temples stage), then FT1119 (seating + print), without regressing the known-good product.
3. Three products the pipeline has never seen (acetate, metal, mirror lens).
The owner judges every delivery live in AR; every verdict goes into the ledger.

## 3. The job's stage order (review §4)
Frame and lenses (Astra's code) → faceted temples (stage on the job head) → lens seating → photo print (last, on the
final geometry) → export + validate + seating check → AR capture → finish → deliver → owner review. After every change
Astra compares renders at the photos' cameras with the photos and revises.

## 4. Owner decisions (recorded 2026-10-13, before the work starts)
- **D1 description vs photos (M2): photos for the look.** Photos win on shape and colour; the description wins only on
  facts a photo cannot show (exact sizes, materials, model number). Astra notes every conflict in its report.
- **D2 lens reflection strength (M3): one fixed default** for every product (the look the owner has been judging on).
- **D3 print tools (M4c): open to Astra, Astra decides.** Owner, verbatim: "astra can use it anytime it see fits. but
  we need the instructions to be good enough so astra will be able to decide to use the tool when it needs to (i.e. a
  tortoiseshell print and others means the tool needs to be used, while other frames dont need a print because their
  visuals arent complex, editing their material and color and finish is good)". So: the WP7 instructions must teach
  when a print is needed (complex patterns such as tortoiseshell) and when material, colour and finish edits are
  enough (plain frames); the print tool is in registry v1's plan for M4c without a gate; and generalising the donor
  colour model (`photoprint/metal.py`) is REQUIRED before any non-brown patterned product, because Astra may now call
  the tool on one.
- **D4 crystal frames (M4a): apply the seating check to all frames**, crystal and translucent included (the seating
  tool's V-bevel and groove for crystal, PLAN §3). The accepted FT1123 crystal fails the check as it is; that is
  accepted. The five open WP4 proof items (metal junction 0.425 < 0.44, INVU 0.4489 < 0.45, FOLD cascades, the
  narrow-eyewire knife edge, `CORRECTION_AIM_MM`) stay technical: prepare the evidence and a recommendation; ask the
  owner only if a gate would be loosened (AGENTS.md "Gates").
- **D5 money and the test product (M5):**
  - per paid run: **$20 settled is a hard stop (no new request starts at or above it), $23 is the most the budget
    guard may ever reserve** (owner: "20$ hard stop 23$ max reserve"). Build the settled-spend stop into the loop
    (`RunSettings`), not as a separate watcher script like `stop_at_18_008.py`;
  - reasoning effort **max**;
  - known-good regression product: **Oakley OO9208 v2** (job 3dff8674; the M5 step 1 product);
  - total WP8 budget and the smoke-turn cap: **not decided now** (owner: "this will be a thought when we'll have a full
    operational automation"). Ask before M5. Every paid run still needs the owner's OK and an estimate.
