# Assembly plan: from separate tools to one automation (written 2026-10-13)

Written by Claude (the planner) for the coding agent that executes it (Codex). The owner decided on 2026-10-13: Claude
thinks, Codex executes. Read in this order before starting: `glasses3d/AGENTS.md`, `plan/PLAN.md` §0, this file,
`glasses3d/plan/archive/20261019/REVIEW_20261013.md` (the historical pre-assembly baseline), `plan/REVIEW_20261018.md`, current PROGRESS and
the dispatched brief, then the design section a milestone
names. Where this file and an older design disagree, this file wins; where the owner's latest words disagree with this
file, the owner wins.

**Current status, 2026-10-19:** this file retains dated plans and their original evidence. Line numbers below refer
to the code at each milestone's date, not today's source; use symbols for new references. Completed plans and
superseded terms are not instructions to repeat old work. `PROGRESS.md` records checks and the next action.

| Milestone | Current state |
|---|---|
| M0–M4, M5-prep | Assembled job/tool/stage/delivery path built; offline proofs run. |
| M5 / WP8 | Started: smoke complete; Oakley run 4 accepted 2026-10-15. Prada has six main runs but no accepted assembled product. FT1119 assembled validation and three unseen products remain. |
| M6 | Owner understand/confirm/edit flow built; M11 task 5 repairs preview, scope, recovery and immutable exact-apply delivery. |
| M7–M8 | Surface and owner-edit tools built/proven on copies (`glasses3d/plan/archive/20261019/M7_M8_RESULTS.md`); task 8 fixes later-discovered float32 temple defects. No new product acceptance implied. |
| M9 | Native composition, fresh-conversation renewal and desktop money simulation built (tasks 3/4). Item 4's owner comparison of desktop c0016 versus run-4 c0015 is not recorded as complete. |
| M10 | Diagnose/preview/exact local apply built. Combined copy accepted 2026-10-18 as an edit-tool test only; task 5 adds review/verdict-compatible deliveries with explicit tool-test scope. |
| M11 | Tasks 1–9 landed and Claude accepted; task 9 tests, remaining arview repairs and documents passed the required checks (`81ef7b2`). M11 is complete. No paid run authorized by this milestone. |
| M12 | Built with documented limits. N1 permits same-part `repair_shading` picks with disjoint normal influence; exact native/exported normals, preview equality and immutable delivery remain required. Overlapping influence, other same-part normal operation kinds and older previews failing exact validation still refuse. D1/D3 results below are historical; see the tests README's N1 proof record and PROGRESS for current results and next action. |

Standing money terms are the dated M12 amendment to D5 below. The owner parked the review-page authored-size choice until
the hard technical work is complete. The M10 combined copy is not a new product delivery: owner, 2026-10-18,
"we were testing the new tools and their optional effects, our goal wasnt to fix the model".

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
  prepend a dated entry to `plan/PROGRESS.md`: date, milestone, commits, test numbers, open issues.
- Each milestone ends with its $0 proof. If the proof fails, diagnose and fix the general method; do not weaken the
  proof. Stop and report to the owner if a milestone needs a decision listed in §4 or more than its stated scope.
- Never hard-code a product, a photo set or a photo name in runtime code. Product facts live in job data.
- Before deleting or rewriting a module, grep its callers; the review's dead-code tables are in
  `data/review_20261013/reports/`.
- Verification follows the dispatched brief. The owner's 2026-10-17 lean-verification instruction replaces the
  blanket full-suite requirement for narrow work: focused affected tests and `assembly_m1` once when relevant.
  M11 is broad and explicitly requires the full suite. Use `tests/README.md`: repo-root invocation, explicit
  `-c glasses3d/pytest.ini glasses3d/tests`, `--dist loadfile`, a memory-bounded functional pool, photoprint
  `-n 4`, and a separate serial timing pass. Budgets/timeouts remain unchanged.

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
Designs to reuse: `glasses3d/plan/archive/20261019/WP2_DESIGN.md` §10-§12 (read its 2026-10-13 note first: its G2 temple rows are obsolete).
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
1. **Rewrite WP7** (`glasses3d/plan/archive/20261019/WP7_DESIGN.md`, read its 2026-10-13 note): follow the WP6 v3 lesson (`plan/WP6_V3.md` §1:
   Astra executes the newest rule literally; teach the object, rank the evidence, give an order of work, keep rules
   few). Content: what glasses are made of (front, lenses, bridge, hinges, temples, pads), the owner's §2 table word
   for word, photos authoritative and colour from photos (not the product name), room reflections on lenses are not
   defects, small branding stays small, the stage order of §3, how to use each tool's report. Tool mechanics live in
   tool descriptions, not in the prompt. New text; never copy the retired phrases (PLAN §4 WP7).
2. **Historical image policy, superseded by M5-fix-1 and M9/task 4** (PROGRESS "Open at closing": run 8 grew from 9 to 136 images, 26k to 219k tokens):
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

### M5-fix-4. Like-for-like colour check: the AR renderer on each photo's background ($0; added 2026-10-15 by Claude)
M5-fix-3 (`763349d`) made the lens see-through (owner: "the lens is transparent after the fix which is good!").
Owner on run 3's re-export, by webcam, with a strip of the photo lens beside a strip of the AR lens: "the color
arent close", and his proposal: Astra should check the colour of its model with images on a white background, the
same as the reference photos. Claude's $0 check confirms it is the missing test: through the AR renderer's existing
explicit-camera mode (`arview capture <glb> --cameras <file> --no-face`, `arview/qa/capture/cameras.ts`: "a dev-capture
mode for comparing a model with product photos"), on the front photo's own white (#ffffff), both run 3's re-export and
the owner-accepted Oakley show a dark grey-green lens; the photo shows a light violet lens with a green centre
(`data/assembly/claude-white-check/white-check-compact.png`; approximate hand-placed camera, colour comparison only).
Astra never had this view: AR captures put a face or skin behind the lens, and `render_views` (white allowed) is
Blender's renderer, not the one the owner judges in; Astra used it once, before colouring the lens. The darkness is
Astra's own material choice (run 3: metallic 0.60, grey tint 0.50; the shader's see-through coefficient includes
`(1 - metallic)`), which it could not see. Do not change the material for Astra.
1. **A new general tool for Astra** (for example `compare_look_to_photos`), registered in the main session:
   - export the current head as `arview_capture` does, then draw it with the AR renderer's explicit-camera mode
     through each requested photo's frozen fitted camera (default: every usable camera in the job's frozen basis;
     `refs` and `basis` arguments like `compare_to_photos`), with no face, at the authored size and the one fixed
     lens-reflection setting;
   - the background is each photo's own background colour, measured from its border (median of border pixels;
     report the colour and how uniform the border is);
   - convert the frozen camera exactly (`g3d_host/camera.py`, `cameras.py`, `blender_camera.py`) into
     `arview.cameras/1` (row-major view matrix in metres, fx, fy, cx, cy, size within 1280 px), including the
     off-axis eye offsets; a perspective-0 camera becomes an equivalent distant pinhole, stated in the result;
   - return one sheet: for each photo, the photo and the AR drawing cropped to the same box side by side, plus a
     close-up pair of the lenses; report as information (never a gate) the median colour and lightness of the lens
     region and the frame region in photo and drawing, using the AR part-id masks eroded a few pixels;
   - the description says: this is the like-for-like colour check (same renderer as the owner's AR, same viewpoint
     and background as each photo); judge lens hue, lightness and see-through and frame colour here; reflection
     highlights from the AR's fixed room light are expected and are not lens colour; AR face views show the worn result.
   - **Both views in every call** (owner, 2026-10-15: "im just making sure that when astra will edit the lenses that
     it will also look at the glasses on a person (ar view) in addition to the white background view"): from the
     same export, the same sheet also shows the worn front on the existing test face (the M5-fix-3 portrait, eyes
     legible). Astra never sees the white-background check without the worn view.
2. **Instructions:** one core line naming the tool as the colour check against the photos, and saying that after
   every lens or frame colour/material change Astra judges both views: the photos' background (does it match the
   photos?) and the worn view (does it look like this product on a person, eyes visible?). Stay within the
   8,000-character limit by tightening wording elsewhere without removing any guidance; if that is impossible, stop
   and say so.
3. **`finish` includes the front-photo comparison** from this tool in its report and the delivery, as information
   for Astra and the owner, not a finish requirement.
4. **Proof ($0):**
   - (a) camera conversion: on the owner-accepted Oakley with the frozen basis `data/assembly/m5fix1-camera-roll-a`
     basis 01, the AR part-id silhouette and the Blender silhouette at the same camera agree for every usable photo
     (report IoU and the largest edge offset; a disagreement is a conversion defect to fix, not a likeness result);
   - (b) the sheets for all usable photos on the accepted Oakley and on run 3's re-export
     (`data/assembly/m5fix3-copy-a/.../run3-reexport.glb`);
   - (c) `tests.proofs.assembly_m1` and the affected suites as §1 describes.
5. Show the owner (b), the front photo first. Change no material, colour, coating or geometry; start no paid run.

**Status 2026-10-15:** M5-fix-3 and M5-fix-4 done (`763349d`, `179ac43`). Oakley run 4 (`20261015T062433-662a82`,
$10.61, 60 requests, finished by itself) was accepted by the owner: "as far as i can see the model is perfect"
(job `reviews/verdicts.jsonl`, GLB SHA `98131458…`). **M5 step 1 is closed.** Run 4's GLB is the accepted
assembled Oakley; keep it unchanged as the regression reference.

### M5-step-2 prep. Prada job, ready for an owner decision ($0; added 2026-10-15 by Claude)
Prada tests what Oakley did not: `temple_stage` (WP6's faceted temples) inside a full job. No Prada job exists yet.
1. Create it with `python -m glasses3d job create` from the Prada photos WP6 used (list each source path and SHA).
   Description: the owner's own earlier Prada note, verbatim, naming where it came from; if none exists, stop and
   ask the owner for one. Specifications: only the owner's. Nothing of ours goes into owner-verbatim fields.
2. Check every photo's view and wearer's-side label by eye against the photo.
3. Print the exact brief, instructions and tool descriptions Astra will receive; confirm `temple_stage`'s stated
   scope matches Prada's temples as the photos show them, and that the money figures are the owner's $20 / $23.
4. Make the run request and estimate (fresh run, effort max, $20 settled stop, $23 maximum reserve), then stop for
   the owner's approval. Start no paid run.

### M5-fix-5. After Prada run 1: Astra must see close up, finish on a real look, arms posed per photo ($0; added 2026-10-15 by Claude)
Prada run 1 (`prada-beige-86248315`, request `20261015T083404-b50f0d`, $11.90, finished by itself with ~$8 left).
Owner: "the glasses look like shit, it looks like some half assed the job. i feels like astra doesnt even look
because the result makes no sense". He named front shape, arms and lens details (not colour). Claude's diagnosis
(two read-only reviews of the run records, file/call references below):
- **Astra judged from thumbnails** (Claude's plan error, the M2 image policy): every tool returns one image of at
  most 1024 px, so the six-photo `compare_look_to_photos` sheet is 1024x990 with 24 tiles (~200 px per pair).
  `read_image` can open any full-size panel, but in 81 calls Astra zoomed 4 times and never on its own model beside
  a photo. It called the bright white lens mark "one subtle lens wordmark".
- **Front:** raw polygon rings (calls 0002, 0044): a flat block 6.6-8.8 mm deep, a continuous 1.45 mm ledge around
  the outside (meant as "broad corner facets"), the lens set 2.1 mm behind a 90° wall, a 0.22 mm 3-segment bevel,
  then every edge >20° marked sharp (0050). Depth never measured. Astra noted "not rounded enough" (7), "too sharp
  edges ... I should round the corners" (52) and then moved on; at 77 the clay render shows the ledge and it wrote
  "It's all coming together nicely!". `execute_blender_code` documents only `g3d.lens_coating`; outline
  comparisons cannot see rounding; the 100k-triangle limit was hit at 125,130 (0064).
- **Gold band in the rims:** the acetate inner wall in Astra's strongly yellow frame colour (0050), visible on
  shaded walls; the look tool reports one frame median, which matched on the lit face.
- **White lens mark:** tagged partRole `lens` because the tool text says "Tag hardware and logos with their
  supporting component's role" (`agent/tools/__init__.py:318-319`); the viewer turns tone mapping off for lens parts
  (`arview/src/render/renderer.ts:734`), so a grey non-transmitting marking clips to white.
- **Finishing early:** reasoning "I don't need to use it all" (74); `agent/instructions.py:118-119` says "listing
  remaining differences is better than running out"; the finish summary omitted the front edges Astra had flagged.
- **Arms:** the stage's cameras assume the as-authored arm opening in every photo ("hinge opening is not
  calibrated", `agent/temple_lab/rigid_pose.py:112-118`); real photos open the arms differently (run 8's accepted
  basis had 03 -2.0°, 06 +8.1°, 02 +6.7/+2.2°; 10° moves a tip ~20 mm). The specialist widened the tail and found it
  "cannot be reconciled ... at the supplied fixed cameras/authored pose"; nothing can fit an opening now (WP6's
  `fit_theta`, removed in `c102efb`, had a recorded occlusion defect). The parent's cameras were fitted once to its
  first guess with `default` mattes (01 includes floor reflection; fits 06 0.787, 03 0.799, 01 0.684 vs run 8's
  0.922/0.880/0.873). The arm plan curve and hook were never measured. `splice_chain` leaves steps
  (`agent/temple_lab/geometry.py:226-242`). `compare_look_to_photos` draws authored arms, not each photo's opening.
Items, in this order (A before B):
**A. See, and finish on a real look**
1. **Full-size pairs.** `compare_look_to_photos` (and `finish`) return one image per photo: the photo and the AR
   drawing cropped to the same box, side by side at the largest size the image limit allows (a call may return
   several images; history stays append-only). Add automatic close-up pairs at native resolution from the part-id
   masks: frame outer edge/corner, inner lens rim, lens markings, hinge/arm root, arm tip. Report colours of shaded
   walls and inner rims as well as the lit face (information). Keep the worn front view in every call. Find how the
   provider handles `detail: "high"` images for `gpt-6-astra` (installed SDK/docs and the official OpenAI web
   documentation, cited with URL and date; web research allowed by the owner on 2026-10-15); if still unknown,
   write the exact question for the owner.
2. **Finish after a real look** (procedure, not a likeness gate): `finish` first returns the full-size pairs and
   close-ups for every usable photo and asks, per photo, what still differs; it accepts only on a second call that
   lists those differences (or "none") per photo. Replace `instructions.py:118-119`'s "listing remaining differences
   is better than running out" with: remaining money is for visible differences from the photos; before finishing,
   revisit every difference you noted during the run and fix it or report it; keep enough to finish.
3. **Lens markings are not lens glass:** the viewer keeps tone mapping for lens-tagged parts that do not transmit
   (general rule in `glasses3d/arview`, never `ar/`); fix the tool sentence so markings get a sensible role.
4. **Front cross-section capability:** document the existing `g3d` helpers Astra can use in `execute_blender_code`'s
   description; if none sweeps a rounded acetate section (crowned face, rounded outer and inner edges, facets only
   where named) along a rim outline, add one, proven on a synthetic outline. Instructions: measure front depth and
   edge rounding from side/top/angled photos (`photo_read`) and reproduce the photo cross-section; facets are local
   where the photos show them.
5. **Cost:** measure on Prada run 1's saved requests what the larger images add per look call and per run.
**B. Arms posed per photo**
6. **Per-photo arm opening:** fit each photo's left and right arm opening with its camera in `fit_cameras` (port WP6's
   opening fit from history, fix its recorded occlusion defect; hold an arm with no visible pixels fixed); pass the
   openings to `temple_stage` and draw each photo's opening in `compare_to_photos`, `compare_look_to_photos` and
   `photo_read` (`temple_side`). The former "Set `glossy_floor` mattes at intake" instruction is superseded by
   M5-fix-6: coding agents add no floor details; `fit_cameras` handles floor reflections automatically.
7. **Stage inputs:** the stage brief states camera conflicts from reading residuals as facts; the stage warns when a
   photo's arm fit is poor; give the stage arm plan-curve/hook readings (tip crossings, outline fits from 03/06/01
   with each photo's opening free); `splice_chain` blends or warns on steps. Parent instructions: if the stage ends
   stuck on a camera/pose conflict, refit cameras with openings and run it again.
8. **Proofs ($0):** (a) opening fit on synthetic known openings, then on the Prada photos compared with run 8's
   fitted openings as an independent reference; (b) the new full-size pairs and close-ups on Prada run 1's delivered
   GLB, shown to the owner (they must make the boxy front, rim band and white mark obvious); (c) the lens-mark viewer
   fix on that GLB; (d) `tests.proofs.assembly_m1` and the affected suites as §1 describes; (e) the cost measurement.
   Change no product model; start no paid run.

### M5-fix-6. No agent-added job details: floor reflections handled automatically ($0; added 2026-10-15 by Claude)
Owner, 2026-10-15, on the plan to declare `reflective_floor` for Prada photos 01/03/05/06: "i dont want codex to add
details, this makes the test unrelyable"; and "for the testing its okay for you to label the view but not relection,
etc". Rule recorded in `glasses3d/AGENTS.md` ("Jobs hold only the owner's inputs"). The existing Prada job
`prada-beige-86248315` already complies (views and sides only), so run 2 uses it unchanged; no new job.
1. **Automatic floor-reflection handling in `fit_cameras`.** When the owner declared no matte for a photo, the fit
   decides per photo between `default` and `glossy_floor` itself (from the photo, or by fitting both and keeping
   the mask the camera fit explains better; your design, stated in one line), reports both results, the choice
   and why. Add an optional per-photo `mattes` argument so Astra can choose after looking at the photos. Replace
   the description text telling anyone to declare `reflective_floor` at intake (`agent/tools/__init__.py:421-422`).
   The intake field stays for the owner only.
2. **Proof ($0):** on the unchanged Prada job photos, report the automatic choice per photo beside your earlier
   visual picks (01/03/05/06 glossy, 02/04 default) as a held-out reference; on the Oakley job photos, the choice
   must reproduce what run 4 used. Do not tune the method to the reference: report any disagreement. Report the
   fit-time change. Then `tests.proofs.assembly_m1` and the affected suites as §1 describes.
3. **Prada run 2 prep** on `prada-beige-86248315`: check exactly what Astra will receive, make the fresh request
   and estimate (effort max, $20 settled stop, $23 maximum reserve), and stop for the owner's approval unless he
   has already approved it with a condition the estimate meets.

### M5-fix-7. The temple's hinge-side triangle facet (investigate first, $0; added 2026-10-15 by Claude)
Prada run 2 (`20261015T142422-a3e947`, $16.23, xhigh, c0017). Owner: "the issue i see is that the facets are missing
the triangle which is mostly close to the lenses frame. it is seen a bit in some view but barely, not like it should
be, theres a noticeable difference between the triangles in the reference images. other than that im pretty happy
with the results". Then: "let codex investigate please". This is the open WP6 item "Visible faces" (PROGRESS "WP6 v3":
an edge the photos show crisply can be measured at only a few degrees and is then built too faint; run 8's
hinge-side face; the fit refused Astra's ridge declaration; proposal: a declaration with a visible minimum angle,
chosen from renders and honoured over the readings, plus one prompt rule). The owner's request reopens that item.
1. **Investigate ($0, read-only on the delivery):** in run 2's two temple stages, what Astra declared, measured
   (readings, fold angle and its error) and built for that triangle; what the fit or builder refused or flattened;
   how deep/angled the face is in the delivered GLB versus what the photos show (native `hinge_root` and side/angled
   pairs); whether Astra saw the difference in its close-ups and what it said. Separate geometry from lighting: render
   the delivered temple with the face at its built angle and at a few larger angles in the AR viewer at the photo
   cameras, beside the photos.
2. **Report to the owner in plain words** with labelled images (photo first), the cause, and a recommended general
   fix to tools/instructions. Change no product model and no gate; start no paid run.

### M5-fix-8. Facet edges the photos show crisply: angle chosen by the look when measurement can't decide ($0; added 2026-10-15 by Claude)
Codex's M5-fix-7 review (`2d6a485`, `glasses3d/plan/archive/20261019/REVIEW_20261015_PRADA_FACET.md`): the hinge-side triangle is built at
1.73° (~0.32 mm). Stage 1's convex ridge was refused by the reading check and a 3.65° valley kept; stage 2 measured
1.73° ± 2.31° (unresolved), the tool printed "flat", and Astra left it undeclared; declaring it passes the checks but
gives only ~2.5°. Larger folds (+5/+10/+15°) read more clearly in AR room light, most in photo 02; lighting also
limits it. **Owner decision, 2026-10-15**, asked "When the photo measurements can't decide how sharp a facet edge
is, may Astra choose the angle from how the photos look (comparing a few angles side by side), instead of building
it nearly flat?", answered **"Yes, by the look (Recommended)"** (option text: Astra compares several angles against
the photo close-ups and picks the one that looks right; measurements that are certain still win, and every other
check stays). Items:
1. **Honest labels:** report a crease's built angle separately from its measurement state (sign-certain /
   unresolved / flat within error). Never print "flat" for an unresolved measurement.
2. **Crease-angle comparison** in the temple specialist: for one named crease, build 3-5 local alternatives
   (preserving adjacent faces as far as the builder allows), draw each in the AR viewer at the frozen photo cameras
   where the face is visible, beside the native photo close-ups, in the AR room light the owner judges in (other
   lighting as information). Report reading residuals, wall thickness and affected geometry per alternative.
3. **Visible-angle declaration (owner-approved policy):** when a crease's measurement is unresolved, Astra may
   declare the angle it chose from that comparison; the builder honours it over the unresolved readings and the
   report records the declaration and its comparison. A sign-certain measurement that contradicts it still refuses.
   Every hard refusal stays (closed solid, thickness, declared ridge/valley = built, strict spec validity).
4. **Instructions** (specialist and one parent line): a facet edge the photos show crisply but the build shows
   faintly is a difference to fix with items 2-3; carry any unresolved named facet mismatch, with its crop, into the
   stage summary and the finish review.
5. **Proof ($0, copies only):** on a copy of Prada run 2's temple stage state, the comparison for the hinge-side
   triangle; a declared visible angle that builds, passes the hard checks and shows the triangle in the AR pairs; a
   conflicting declaration against a sign-certain reading still refused; `tests.proofs.assembly_m1` and the affected
   suites as §1 describes. Show the owner photo-first images. Change no delivered model; start no paid run.

### M5-fix-9 + M6. A finish that is never stranded, and an owner edit mode ($0 build; added 2026-10-16 by Claude)
Prada run 3 (`20261015T185027-2f6756`, xhigh, $16.70) ended as a **draft**: the second `finish` call (the full-size
review) counted 273,293 input tokens, over the 272k price step; its worst-case hold $8.06 exceeded the $6.30 reserve
room, so request 89 was refused before sending. Astra chose 5° for the hinge-side triangle (comparison 5/10°; 15°
refused by a sign-certain neighbour), still faint. Owner, 2026-10-16: "ok this is the next run a i want to try. id like
to use effort=high and id like for you and codex to add an edit option at the end where the user will be able to talk
about specific stuff (i.e. "on the temple, the triangle facet which is nearest to the frame should be more angled, its
boarder with the logo facet should be more outwards and its left boarder should be more inward. in addition the top of
the temples should be going narrower towards the top point of the top triangle facet .")". This revives the owner's
earlier "more edits" box (PROGRESS "WP6 v3"). A one-request "Edit my model" mode was built and removed in Sept 2026
(modeling_auto); this one is a full tool session with a confirm step.
**A. Finish never stranded (M5-fix-9)**
1. `budget_status`, the footer and the first `finish` result state the reserve the second finish call will need at
   the current context, using `agent/pricing.py:Tariff.reserve_micro`, and warn when the remaining
   reserve room approaches it; one instruction line says to start the finish review while that room remains. No
   change to the guard, the $20 stop, the $23 maximum, the tariff or caching. Prove on run 3's saved requests that
   the warning would have fired before request 88.
**B. Owner edit mode (M6)**
2. **`job edit`** starts an edit session from a chosen delivery or draft of a job: the delivered checkpoint, the same
   photos, frozen cameras with arm openings, all tools and the temple specialist, fresh conversation. The owner's
   request text goes in verbatim as "Owner's edit request". Each round is a separate request with its own estimate
   and the owner's approval; money terms are the owner's.
3. **Step 1, understand (no geometry change):** Astra restates each requested change separately; marks the exact
   faces, edges or points it means on labelled images (numbered labels on full-size model views beside the same-view
   photo; for temples, the specialist's own facet/edge/corner names); states the direction and a proposed amount, or
   2-3 rendered options for ambiguous amounts; lists anything it cannot do and why. Then it stops. Add the general
   labelled-map tool this needs (numbered faces/edges for any part; the owner can reuse the numbers later).
4. **Step 2, edit** after the owner confirms or corrects (his reply goes in verbatim): Astra makes only the confirmed
   changes, then shows before/after full-size pairs, close-ups and the worn view, and reports what changed, by how
   much, and anything not done. Unrequested geometry stays unchanged (report any side effect).
5. **The owner's explicit request is the target for that feature:** it outranks photo readings and earlier choices
   (the temple specialist must accept a requested change that contradicts a reading, recording it as the owner's
   request); hard refusals stay (closed solid, minimum thickness, strict spec validity, no reversed physical part).
6. **Proof ($0, scripted offline Astra):** on a copy of Prada run 3's draft, the owner's example request goes through
   both steps: understand produces the labelled images and an interpretation per item; a scripted edit applies the
   four confirmed changes through the real tools; before/after images and the change report are produced. Then
   `tests.proofs.assembly_m1` and the affected suites as §1 describes.
6b. **Show the owner the edit mode before any paid run** (owner, 2026-10-16: "before the paid  run i want codex to
   show me the edit mode and an example of how astra reacts to an edit (codex should mimic what astra will return
   just to check everything works"): walk through item 6 as the owner will experience it, in order: the exact command
   or message he writes; the understand reply exactly as he would receive it (text and labelled images); a sample
   confirmation; the edit result (before/after images and change report). Astra's replies are scripted by Codex and
   must be labelled "scripted by Codex, not Astra"; every image and geometry change must come from the real tools.
   Then stop and wait for the owner's go before item 7.
6c. **Before the first real edit round** (Claude's review of the walkthrough `56baab1`, 2026-10-16): (a) the labelled
   map shows raw mesh ids ("F127388 front"); give the owner short labels per round (1, 2, 3 ... or T1, E2, P3) with a
   plain-words legend ("1 = triangle facet nearest the frame, left arm") and labels large enough to read on the
   full-size image; (b) in the demo's after images the unchanged PRADA/MILANO lettering is buried by the moved
   temple surface: markings attached to or lying on an edited surface must follow that surface by default, and the
   understand reply must list them; never leave a marking silently hidden. Prove both on the walkthrough copy.
6d. **Instruction size limits (Claude, 2026-10-16):** `MAX_INSTRUCTION_CHARS = 8000` (`agent/instructions.py:8`,
   from `glasses3d/plan/archive/20261019/WP7_DESIGN.md:107`) and the specialist's 9,500 are self-imposed focus budgets, not provider limits;
   they cost almost nothing (cached input) and recent fixes squeezed wording to fit (7,989/8,000). Raise them to
   16,000 and 19,000. Do not shorten guidance to make room; where M5-fix-5/8 and M6 compressed wording only to fit,
   restore the clearer wording, with no change of meaning. Keep the contract tests (named tools exist, retired
   phrases absent, size within the new limits).
   **Owner, 2026-10-16: "lets have codex fix the problems and only run then"** — 6c and 6d before run 4.
6e. **Recreate and review the instructions (owner, 2026-10-16: "in the prompt i dont want a paid run to start, i want
   codex to fix the problems, to recreate the instructions, to review the code and instructions to make sure the
   instructions are very good and is perfect.")** Rewrite Astra's main instructions, the temple specialist's
   instructions and the edit-mode (understand/edit) instructions from scratch within the new limits, ordered by the
   workflow Astra actually follows, plain and unambiguous, each rule stated once. Review standard, all required:
   - **Matches the code:** every statement about a tool, argument, report field, refusal, check, money figure or
     finish step is verified against the current code and its tool description; nothing promises behaviour the code
     lacks; nothing the code requires is missing.
   - **No contradictions** between the main instructions, specialist instructions, edit-mode instructions, tool
     descriptions and the brief.
   - **Covers every owner decision** that affects Astra (ASSEMBLY §4 D1-D5 and every later "owner" decision in
     M5-fix-2..M6) and every lesson from Oakley runs 1-4 and Prada runs 1-3 that Astra can act on (e.g. judge from
     full-size pairs and close-ups, measure depth and rounding instead of guessing, arm openings per photo, visible
     facet angles when measurement is unresolved, lens markings are not glass, finish only after a real look).
   - **Product-independent:** no product names, measurements or fixture-specific remedies.
   - **Independent review:** a separate reviewer reads the new texts against the code and lists every mismatch,
     gap, contradiction or unclear sentence; fix and repeat until the review finds none.
   Show the owner the full new texts and a short list of what changed and why. No paid run until he says so.
6f. **Claude's review of 6c-6e (`051b5e7`, 2026-10-16):** labels ("T2 = triangle facet nearest the frame, left
   arm") and following markings work (PRADA/MILANO intact in the after images); the rewrite is clear, ordered and
   product-independent. Three gaps before the paid run:
   (a) the rewrite dropped the explicit rule that remaining money is for visible differences (Prada run 1: "I don't
   need to use it all"); state plainly: do not finish while a visible difference you can still fix remains and the
   finish reserve allows another fix;
   (b) the main text ties `compare_look_to_photos` (full-size pairs + close-ups) to colour/material edits only and
   sends shape edits to `compare_to_photos`; Prada's failures were shape detail (edges, rounding, rims, facets):
   after shape edits too, inspect the full-size pairs and close-ups;
   (c) edit mode's understand step cannot show a proposed amount, only describe it ("no general deformation-preview
   tool"), so the owner first sees "how much" after a paid edit round. Add a read-only preview: apply the proposed
   operations (2-3 amounts) to a private copy and return photo/before/option pairs and close-ups, leaving the source
   unchanged; the owner picks an option in his confirmation. Prove on the walkthrough copy.
7. **Historical next paid run (completed; money later superseded by D5's amendment):** fresh Prada run 4 on the unchanged job at effort `high`, $20 settled stop, $23 maximum
   reserve, only with the owner's approval and an estimate; then the owner's edit rounds, each approved separately.

### M7. Surface quality: flat facets, clean front corners, smooth lenses ($0 first; added 2026-10-16 by Claude)
Prada run 4 (`20261016T092442-0c5459`, effort high, $16.21, c0015, finished normally). Owner's edit request,
verbatim: "the logo triangle has a vertical outward line from its bottom center to its middle. the facets on the
temple arent flat triangles, their angles look good but they should be each a flat plane. at the top outer corners
of the front frame theres a weird line which makes the front incorrect (visibility depends on lighting). the lenses
are not smooth, when the light hits them you can see rigdeness." The paid UNDERSTAND round spent $6.30 and produced
nothing: $1.90 lost to a Windows path-length bug (fixed `fd09595`), $4.39 to `understand_finish` refusing an honest
"unsupported" result (fixed `d6d163b`); translate/rotate/taper cannot express any of the four requests.
These are surface-quality defects that will recur on every product, so fix them where they are made, not only in
paid edit rounds:
1. **Diagnose all four at $0** on the delivered c0015 GLB, photo first: for each, reproduce it in the AR renderer
   under the review light and at least one other light, and establish the cause with measurements: facet planarity
   (best-fit-plane residual per named facet), shading normals (smooth vs split across facet edges, custom normals,
   the 464 zero-area temple triangles), lens tessellation density and normal continuity, and what geometry or normals
   create the front-corner line. Name which tool, builder step, exporter step or Astra edit produced each cause
   (e.g. run 3's request 70 made all temple polygons smooth and cleared custom normals).
2. **Fix the causes in the producing tools** (general, product-independent): e.g. faceted bodies keep flat faces
   and split normals at declared facet edges; the temple builder never leaves degenerate triangles; lenses export
   with smooth, continuous normals and enough tessellation for their curvature; whatever creates the corner line.
   Never edit this delivery by hand.
3. **Astra sees surface quality in the main run:** a surface-quality check (planarity per facet, shading seams,
   lens ripple) reported with close-ups under a raking light, in `compare_look_to_photos` and the finish review,
   as information, not a gate.
4. **Owner edit operations** (Codex's proposal): flatten a selected face onto its plane (keeping borders, angles and
   attached markings), repair shading without moving geometry, and refine a curved surface (keeping curvature,
   thickness, outline and seating); each with labelled selection, scratch-copy previews, confirmation and checks.
5. **No more wasted paid edit rounds:** before any paid UNDERSTAND/EDIT round, a $0 dry run checks that every
   requested item maps to an available operation; if not, build it first.
6. **Proof ($0):** items 1-4 shown photo-first on a copy of c0015 (the four defects before/after); `assembly_m1` and
   the affected suites. Then stop for the owner.

### M8. More owner-edit operations and mesh-health helpers (after M7; added 2026-10-16 by Claude)
Owner asked: "any other mesh editing tools astra might need?". In main runs Astra already has full Blender through
`execute_blender_code`; the gaps are (a) owner-edit mode, which today allows only translate/rotate/taper (plus M7's
flatten/shading/refine) with materials, UVs and topology frozen (`g3d/owner_mesh.py:386`), and (b) reliable helpers
for recurring mesh faults. Each new edit operation gets the same labelled selection, scratch-copy previews of 2-3
amounts, owner confirmation, scoped checks and following markings. In priority order:
1. **Colour/material adjust** (edit mode): change tint, lightness, transmission, gloss or coating of a named
   material, previewed with `compare_look_to_photos` (photo background + worn view). Owners will ask "lens a bit
   darker" / "frame warmer"; today that is refused.
2. **Round or sharpen edges** (edit mode): change the rounding radius of a selected edge run, keeping the adjoining
   faces; allowed to change topology inside the selected region only.
3. **Thicker / thinner** (edit mode): offset a region along its surface normal, keeping the opposite side,
   minimum thickness and attached markings.
4. **Scale a part or region about an anchor** (edit mode): bigger/smaller, wider/narrower (lens opening, bridge,
   temple height), keeping joins to neighbours.
5. **Bend with falloff** (edit mode): gradual bend of a region about an axis (temple tip drop, frame wrap).
6. **Move / resize / rotate a marking on its surface** (edit mode): logo size and placement.
7. **Mesh health for Astra** (main run, also usable in edit mode): remove degenerate triangles and duplicate
   vertices, make normals consistent; symmetrize a part about X=0 (left/right copies that drift apart);
   controlled decimation that keeps silhouette, sharp edges and markings when a model grows heavy.
Each with a $0 proof on a copy (before/after, photo first), the instruction lines, and the M7 dry-run mapping.

### M9. The image wall, and a desktop test that behaves like a paid run ($0; added 2026-10-17 by Claude)
M7/M8 are done (`d39f954`..`790f85e`, `glasses3d/plan/archive/20261019/M7_M8_RESULTS.md`): all four defects traced to run 4's own edits
(request 63's thickness deformation bent the facets; request 73's corner cut + sharp shading made the line; six-ring
lenses), tool fixes and scoped edit operations built and proven on copies. The owner's free full test ("desktop gpt
... astra xhigh ... no other convos memory") ran 216 responses / 15 h 26 m on fresh job
`prada-fresh-desktop-m7-m8-v2-9488f012` and ended `payload_limit`: the next request carried **1,591 images /
157.1 MB**, over the provider's **1,500-image** request limit, with no finish. Every full-size pair and close-up stays
in the append-only history (25-41 new images per look call, 56 per first finish), so long runs and edit sessions
will hit this wall. The desktop test also had no money stop, so it never felt the finish runway.
1. **Fewer images, same pixels:** each photo's full pair and its close-ups become one composed image within the
   documented high-detail patch budget (close-ups at native pixels, never downscaled), so a look call adds about one
   image per photo. History stays append-only. **No part is ever shrunk** (owner, 2026-10-17, recalling Prada run 1's
   1024 px collage: "didnt we had a problem where we already sent collages instead of images and astra didnt zoom
   in?"): if a photo's pair and close-ups do not fit the budget, use a second image instead of scaling. Proof: every
   part of every composed image is pixel-identical to the separate image it replaces, and the provider's documented
   resizing leaves the composed image unchanged.
2. **Safety valve — DONE 2026-10-18 (`ded8b77`).** When the next request would pass 80% of the image limit,
   payload limit or the 272k price step,
   the host asks Astra for a written state summary (current best checkpoint, open differences, decisions) and continues
   in a fresh conversation with the photos, that summary and the latest evidence; record it (one cache rebuild).
3. **Desktop parity — DONE 2026-10-18 (`ded8b77`).** The desktop test applies the paid run's money terms
   with the local token estimate and the real
   tariff (simulated settled spend, $20 cutoff, finish runway), so it ends the way a paid run would.
4. **Show the owner, at $0:** the desktop test's best checkpoint c0016 beside run 4's c0015 and the photos (full-size
   pairs front first, side-light close-ups, worn view), so he can judge whether the new tools helped.
Proofs on saved requests/copies; `assembly_m1` and the affected suites. No paid run.

### M10. Edits you can see before you pay ($0; added 2026-10-17 by Claude)
Prada run 6 (`20261017T090817-2b59b9`, high, settled-only $20, **$18.52**, c0016) is the best Prada so far (front,
angled and side pairs close; flat facets; the near-frame triangle visible). Owner's edit request (verbatim in
`data/assembly/prada6-edit1-ops/owner-request.txt`): front upper outer corners "visibly different texture"; near-frame
temple triangle's lower-left point (right-side view) "more inward", clarified "both temples ... inwards means towards
the head". UNDERSTAND cost **$7.22** (45 requests); the editor ran the ordinary revision workflow, not sealed M6
operations, cost **$4.79**: temple junctions moved 1 mm toward the head (perpendicular to the side view, so barely
visible there) and front corners got a 50% shading-normal blend only. Owner: **"it looks like nothing changed"**.
Causes: no amount was ever shown to the owner before spending; the amount was chosen for him; the $0 mapping matched
"texture" to `repair_shading` without diagnosing the corner; understanding two sentences took 45 requests.
1. **Diagnose before mapping ($0):** for each requested item, measure what produces the visible effect on the
   source model (as in M7) before choosing operation kinds; never map by keyword.
2. **Previews in the views that show the change:** each item gets 2-3 clearly different amounts (for a move: e.g.
   1/3/5 mm) on scratch copies, shown in the views where that direction is visible (toward the head: top and rear
   as well as side), photo first; the owner picks one or asks for others.
3. **Apply the picked option without another paid round:** once the owner picks a previewed option, the host applies
   exactly that operation (it is the owner's own edit through the tool, already previewed and checked) and shows
   the result; a paid Astra round is needed only to interpret a new or unclear request.
4. **Short understanding:** the understand step produces the labelled map, per-item interpretation and previews
   only, no whole-model review; report its request count and cost.
5. **Proof ($0)** on a copy of run 6's c0016 with the owner's two items: diagnosis, previews (photo first), apply of
   one picked option. Lean verification (owner, 2026-10-17): focused tests + `assembly_m1` once, no full suite.

### M11. Fixes from the 2026-10-18 review ($0; added 2026-10-18 by Claude)
Source: `plan/REVIEW_20261018.md` (section 7 is the order; finding IDs point to the detailed reports in
`data/review_20261018/reports/`). From 2026-10-18 Claude dispatches Codex directly (owner: "every token used by you
should go managing this project, and you should use codex for the actual labor"); each task's brief is saved in
`data/review_20261018/briefs/`. One Codex task at a time; each ends with its new tests, the full suite (section 1),
`tests.proofs.assembly_m1`, a PROGRESS entry and a commit with explicit paths. No paid run.
1. **Task 1, run endings, money and crash safety:** drafts for every unfinished end; nested `temple_stage` never fatal;
   trusted worker results never end a run on NaN; finish runway against the settled stop; temple-stage failure and
   money handling; `encoding="utf-8"` for the AR report.
2. **Task 2, resume, sessions and timeouts:** best checkpoint per run, resume from the draft's checkpoint, resume
   estimates; rollback, drift and export-cache recovery defects; tool timeouts from their real inner steps.
3. **Task 3, what Astra sees and reads:** EXIF-upright photos, one wearer's-side convention in every report, sandbox
   hints and fonts, `read_image` on camera overlays, the over-length summariser, stage photo transport copies,
   capture exit codes, `agent/README.md`.
4. Then review section 7 steps 2-7 (M9 safety valve, owner edit mode, arm openings, FT1119 readiness, builder,
   tests and documents), one task each.

**Task ledger (2026-10-18):** tasks 1 (`9ef5cea`), 2 (`62638eb`), 3 (`25dcd54`), 4 (`ded8b77`, `74426bf`),
5 (`46a7054`), 6 (`91487b7`), 7 (`df9fe23`) and 8 (`4c5bf5c`) landed and were accepted by Claude. Task 4 implements
M9 renewal and desktop parity; task 5 fixes owner edits; task 6 cameras/openings; task 7 print/seating; task 8 the
builder/fit/stage and T5 proof. Task 9 closes the assigned tests/arview/document findings. The detailed task reports
under `data/review_20261018/codex/` retain each scope limit and failed or deferred check; this is not a claim that
every P3 in the review was fixed. CC-12 is partial, CC-13/17 remain, WP4's historical quality failures and WP5's
A4/A5/B3 limitations remain, and no unseen-product validation has happened.

### M12. The owner's dev UI ($0 build; added 2026-10-19 by Claude)
Source: `plan/M12_DEV_UI.md` (the owner's request verbatim, decisions in sections 2 and 6, frozen contracts in
section 7). Briefs in `data/m12/briefs/` (each with `m12_common.md`), reports in `data/m12/codex/`. Owner: "i dont need
you to show me the plan ... go and build it" and "for fastest time please max utilize this machine". Tasks with
disjoint files run as parallel Codex processes that do not commit; Claude reviews and commits each with explicit paths.
No paid call is made by the build; the owner presses the real "Start a paid run" himself.
1. **A, backend** (parallel): new `dev_ui/` Starlette server on 127.0.0.1:8260, detached start/stop, API of section 7,
   draft registry, idempotent prepare/approve/launch, cheap run status, stop, deliveries, AR link, $0 apply tasks,
   offline test mode by construction only.
2. **B, pages, viewer and annotation tool** (parallel): new `dev_ui/static/` and `dev_ui/browser_tests/`; orbit viewer,
   capture, point/line/closed-shape marks with labels and anchors, item loop, run/results/understand pages; headless
   Chromium tests against a mock API.
3. **C1, core intake** (parallel): owner captures as labelled model evidence (never product photos) bound into approval
   and renewal; unresolved UNDERSTAND admission; marks resolved to model targets for Astra; typed cutoff instead of the
   $20 ceiling; instruction caps 24,000 and the capture text.
4. **C2, per-item options and the $0 composed apply**, then **C3, the `full_edit_item` fallback** (after C1; in
   parallel only if their files are disjoint).
5. **A/B wiring** to the real façades, then **D, integration**: $0 end-to-end proofs in a real browser with the scripted
   Astra and real Blender, documents (AGENTS money wording, READMEs, PROGRESS), full suite, arview suites, `assembly_m1`.

**Task ledger, 2026-10-19 (committed work):**

| Task | Commits | Recorded check / remaining boundary |
|---|---|---|
| A backend | `248d2a1` | 249 focused tests, 11.18 s; detached server start/status/stop included. |
| B pages/viewer/annotation | `3ea7218`, `a1acafd` | Final follow-up: 16 browser tests, 22.672 s; mock API. |
| C1 captures/cutoff | `a4600de` | 541 focused tests, 70.56 s; final text/transport check 104, 8.80 s. |
| C2 options/apply | `76639f4` | 459 focused regressions, then 141 sealing/apply and 120 apply regressions after repairs; 144 new cases. Native mixed UNDERSTAND and exact single pick, no completed two-item Prada apply. |
| C3 fallback | `e0873f0`, `275b37c` | 121 initial focused tests; follow-up 244 including capture regressions; SDK/Blender fallback proof 113.672 s. |
| B money follow-up 2 | `fb7f5d5` | 20 browser tests, 30.287 s; only settled spend, cutoff and money left are displayed. |
| C2 follow-up 3 | `1a79b26` | 534 passed / 11 xfailed, 120.73 s; native proof 1,520.090 s. Same-part shading picks refuse early; immutable single pick equals preview bytes. |
| D1 integration | `b81f923`, `0da06ba` | All six browser scenarios passed at $0; final proof `data/m12/proofs/d1b/proof.json`. Separate historical alias regression remains open. |
| D2 documents | `036a156` | README/instruction contracts: 87 passed, 8.19 s; historical D2 report retained. |
| D3 final verification/documents | Baseline `2340e95`; no D3 commit | complete with documented limits. Functional: 5,618 distinct passes after unchanged camera retry, 2 skips / 11 xfails / 1 non-strict xpass. Remaining passes: 367 photoprint (381.35 s), 29 timing (260.41 s), 20 UI browser (23.923 s), 509 arview unit (20.559 s), 113 QA (39.104 s), check/build exit 0, M1 152.266 s. |

**Historical C2/D1/D3 evidence:** the focused batches overlap and are not added to the D3 suite total. Reports are
`data/m12/codex/m12_{A,B,C1,C2,C3,D1,D2,D3}_report.md`. D1's follow-up superseded its incomplete initial proof:
publication, byte-exact single apply, the then-required same-part refusal and fallback-to-new-capture integration
were verified. The original native dual-apply failure remains evidence. The earlier normal-writer repair
regressed the real Prada preview and was reverted; that historical result is not replaced by N1's new evidence.
At D3, the pre-M12 M11-task-8 historical temple-alias regression was queued as M11 task 12. Task 12 (`1238a21`)
has since been accepted: 40/54 temple objects verify, 14 already-reshaped objects correctly refuse, and the M10
regression export stays byte-identical (`7d2d5b27…`). UNDERSTAND evidence sealing remains slow on Prada
(1,147.484 s for C2's two-item proof). No owner appearance verdict or paid validation is claimed.

**Built edit contract (M12):** capture UNDERSTAND starts unresolved, with no fabricated coverage file; the CLI
text-edit `--coverage` path remains. Clean and annotated captures are native model evidence, never product photos.
The owner's words, item order, labels, camera and source GLB are bound to the round and approval; product photos
retain authority. `labelled_model_map(owner_labels=[...])` resolves and overlays marks; unresolved/background
portions remain explicit. Marks alone grant no mutation authority: actual targets still need authenticated supports.

Each previewable item has two or three sealed options with readable amounts and native photo/before/option images.
Blocked items retain a reason and no options; mixed results are valid. **Apply my picks** is one $0 local action:
compose the selected sealed operations on a fresh copy in original item order, retain skipped notes, run scope,
physical, strict export, retained-print and AR checks, and publish a new immutable delivery without changing the
source. One pick must equal its preview GLB bytes. Disjoint picked geometry must reproduce each preview exactly;
overlaps are composed in item order and reported, without claiming isolated-preview equality there. Failed checks
refuse delivery. Applied-picks provenance records zero API requests/spend, picks, notes, overlap warnings and
exactness; it is not a new Astra finish or visual acceptance. D1 verified immutable publication and the exact
single-pick result. N1 permits separate `repair_shading` picks on the same source object only when their normal
influence is disjoint. Native raw codes, decoded vectors and exported NORMAL values outside each influence
remain exact, and separate and combined application must agree. Overlapping normal influence, other same-part
normal operation kinds and older previews that fail the exact checks still refuse before publication. For those
cases, put both marks in one item, or apply one and send the other as a new edit from the resulting delivery.
Existing exact assertions, quantization bounds, scope and physical gates remain in force. N1 proof results are
recorded in the tests README and PROGRESS; D3's eleven strict xfails and one non-strict authored overlap xfail
remain historical evidence, including task 11's four-corner drift report.

An unpreviewable item can instead use **full_edit_item**, a separately approved paid round with its own cutoff,
seeded from the same immutable source, the main toolset, product photos and only the selected item's capture and
words. Ordinary finish/draft publication reports geometry, materials, UVs, transforms and outside-region changes
as side effects for the owner to judge. Ambiguous correspondence/remeshing stays unresolved; no pixel-level
confinement or new quality gate is claimed. All existing hard refusals stay. D1 verified visible side-effect
reporting and a new capture/UNDERSTAND round from the fallback delivery.

**Current money behavior:** B follow-up 2 (`fb7f5d5`) shows only settled spend, the owner's max budget/cutoff and
money left. M11 task 11 (`2340e95`) makes settled-only the default and sole new-run/edit mode and removes
finish-cost projections from Astra's input. Neither the UI nor Astra's money status shows estimates, held
amounts, projected costs or finish-soon figures. CLI planning estimates remain available. Current job-run CLI defaults
are a $20 settled cutoff and max effort; historical reservation-mode records remain readable, not new-run
authority. These committed M11 terms supersede the legacy-default sentence in the dated D5 M12 amendment below.

Main and edit instruction caps are **24,000 characters**, superseding M6's historical 16,000. C1 measured
main 16,430 / UNDERSTAND and EDIT 17,478; C2 measured main 16,430 / UNDERSTAND and EDIT 19,455; C3 measured
main plus fallback 17,770. These are dated report counts; the ordinary temple specialist's cap remains 19,000.

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
- **D5 original money and test-product decision (2026-10-13; money/effort superseded below):**
  - per paid run: **$20 settled is a hard stop (no new request starts at or above it), $23 is the most the budget
    guard may ever reserve** (owner: "20$ hard stop 23$ max reserve"). Build the settled-spend stop into the loop
    (`RunSettings`), not as a separate watcher script like `stop_at_18_008.py`;
  - reasoning effort **max**;
  - known-good regression product: **Oakley OO9208 v2** (job 3dff8674; the M5 step 1 product);
  - total WP8 budget and the smoke-turn cap: **not decided now** (owner: "this will be a thought when we'll have a full
    operational automation"). Ask before M5. Every paid run still needs the owner's OK and an estimate.

**D5 amendment, 2026-10-16/17 (cutoff superseded for the dev UI by M12 below):** effort **high**, **$20 settled-only cutoff**, shared by parent
and specialist. Owner, 2026-10-16: "effort=high, cutoff 20$ reserve doesnt matter". Owner, 2026-10-17:
"after youre done fixing, rerun. no bs money reserve this time, i dont care about astras possible responses, only as
we pass 20$ cutoff the run not a cent before!" No estimated reserve refusal or output shrinking applies in this
mode. The last in-flight response can cross $20; no next request starts at or above it, and unknown outcomes still
stop spending. Explicitly prepare `--settled-only --effort high`; legacy defaults and old approvals retain their
old policy. Each paid run still needs its own authorization and estimate. The smoke was approved on 2026-10-13
("Yes, cap $1") and completed that day. No blanket total WP8 budget is approved. Oakley OO9208 v2 remains the
regression product, now its accepted assembled run-4 asset (`98131458…`).

**D5 M12 amendment, standing dev UI terms, 2026-10-19:** owner, verbatim: "max budget (meaning the actual cutoff
with no reserving), then when the user hits a 'start a paid run' button the automation should start". The owner's
typed positive finite max budget replaces a universal $20 for dev UI runs and edit rounds. Effort is **high**,
**settled-only**, cap = stop, shared by parent and specialist, with no reservation cutoff or output shrinking.
No new request starts at or above the typed cutoff; the in-flight response can cross it. Unknown charges still stop.
Each press of "Start a paid run", "Send to Astra (paid)" or "Send this item to a full paid edit", with its displayed
typed amount, is his OK for exactly that run or edit send. The single-use approval records the button's words and
amount. Coding agents never start paid calls under build tasks; anything they would run still needs his explicit
OK and an estimate before that run. Legacy CLI defaults ($20 stop / $23 reserve / effort max) remain defaults only.
No total WP8 budget is approved, and no build/proof authorizes a paid validation run.
