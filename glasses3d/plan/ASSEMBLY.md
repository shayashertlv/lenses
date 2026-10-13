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
