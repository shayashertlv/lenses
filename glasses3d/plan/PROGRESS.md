# glasses3d progress log

Format (PLAN.md §4): date, WP, what was done, commit hash, $0-proof numbers, open issues, next step.
A new session reads this right after PLAN.md §0 and resumes from the first unfinished step.

## Status (updated 2026-10-07 10:50, handoff written because the session is near its usage limit)

| WP | State | Last commit |
|---|---|---|
| WP0 setup, port review | **done, proof passed** | b19a08a |
| WP1 arview | **done, proof passed** (independent image checks; closeout) | b5a20ea |
| WP3 exporter + validate_spec | **done, proof passed** (review defects fixed, mutation tests) | 6acfc5a |
| WP3b camera fitting | **done** (port reproduces the prototype to 1e-6 under scipy 1.16.0; 12-view baseline; the registration-threshold question is dissolved: WP5 uses a photometric gate) | 457daf7 |
| WP4 lens seating | **done, proof passed** (P2 FT1119 71/71; Prada seats; Oakley 0 moved; P6 junction line 0 px in AR) | b76c249, 140cc71 |
| WP5 photo print | **in progress. WAITING FOR THE OWNER'S VERDICT** on the all-angles sheets (sent 10:42). Step 6 built + tested (241 passed), NOT committed: its review fixes are running (`g3d-wp5-step6-fixes`) | 5ec9c25 |
| WP6 faceted temples | **re-diagnosis running** (`g3d-wp6-rediagnose`, in its review/fix loop, then a fresh proof): tool OK with hand-traced parameters; the automatic fit stops at STOP-C (an owner evidence decision per WP6 design) | 204f694 |
| WP2 live Astra session | stage A **built + reviewed** (324 tests), stage B **being built** (`g3d-wp2-stages-ab`); stage C **built, money exact, in fix round 1** (`g3d-wp2-stage-c`); SDK installed; NOTHING of A/B/C committed yet; D-F not started | f479660 (requirements-agent.txt) |
| WP7 instructions | **design done** (`plan/WP7_DESIGN.md`); implementation waits for WP2 stage D's registry | 78d1950 |
| WP8 paid validation | not started; owner OK per paid run (ask first: budget cap per run, suggested $15; effort max or high; smoke cap $1; regression product Oakley or INVU; plus WP7_DESIGN §4.2 #1-#2) | - |

## RESUME HERE (handoff 2026-10-07 10:50; read this first after PLAN.md §0)

### 0. The owner's standing instruction at handoff (verbatim, 10:40)
"first get me the comparisons, only after i give you my opinion you may resume to what thats written below. in addition, you are
reaching usage limits, please finish you tasks (dont kill any runs) and tell me where are we standing. note anything thats not compete,
especially things you are in the midst of!!!!! this handoff should be perfect".
=> **Do NOT start any new work (no new workflows, no new WP steps) until the owner has given the WP5 verdict on the all-angles
sheets.** Allowed meanwhile: let the five running workflows finish (NEVER kill a healthy run), read their results, run the checks
listed below and commit VERIFIED work by explicit paths. When the verdict arrives, act on it first (section 2), then resume section 5.

### 1. The owner's pending WP5 decision (asked 10:42)
- Sent four sheets (frozen copies, sha256 prefixes in brackets): `data/wp5/all_angles_sent_1042/all_angles_photos.png` [f5d3b776]
  (front, angled, top, back: each from the photo's own fitted camera, arview renderer, no face), `all_angles_photos_shape_differs.png`
  [34954165] (03 wearer's right side and 06 left side from above; model outline IoU 0.734 / 0.712 is Astra's shape, the bake's own fit
  scores the same, so "judge the paint, not the outline"), `all_angles_tryon.png` [14e2bfc8] + `all_angles_tryon_2.png` [3a0be1de]
  (11 try-on poses + on a face). Columns: your photo | Astra's texture (now) | A (new paint) | B (darker) | C (more colourful).
  The live copies in `data/wp5/all_angles/` may be re-rendered by fix round 2 of `g3d-wp5-all-angles`; if they change visibly,
  re-send them and say what changed.
- What the options are (`data/wp5/ar_calibration/calibration.json` -> `candidates`): A = `photo_angles` = the production
  `data/wp5/ft1119_bake/print.png` (gain 0.931, gamma 1.080, sat 0.996, chroma_power 0.455, chroma held >= 1, hue band 20-100),
  tool-test GLB `data/wp5/tooltest/q001_painted_TOOLTEST.glb` (sha 17d60488...); B = `near_18deg` (gain 0.831, gamma 1.168, same chroma;
  GLB `data/wp5/ar_calibration/cand_near_chroma_TOOLTEST.glb`, captures `sheet_near_chroma`); C = `all_pairs_chroma` (gain 0.931, gamma
  1.080, sat 1.378, chroma_power 0.615; GLB `cand_chroma_all_s4_TOOLTEST.glb`, captures `sheet_chroma_all_s4`). The all-angles json
  (`all_angles_sent_1042/all_angles.json`) records the GLB sha256s actually rendered. Astra = delivered q001 GLB (sha af6e9d10...).
- Told the owner: C looks almost the same as A everywhere (the real choice is Astra / A / B); same in every option and NOT the paint:
  silver metal + cream lenses (photos: gold + beige) and the tip medallion only on the +x (wearer's left) arm = Astra's model content;
  one real PAINT defect to fix next: small pale-yellow/white ragged blotches on the brow tops (clearest in `data/wp5/all_angles/rows/
  04_top.png`, zoom `verify_1/zooms/top_brow_left_x3.png`), in the production print (probably highlight handling).
- Also known (step 6 independent AR check): print matches brightness + colour only at photo 05's own angle (pitch +20); front pose
  ~+5 L* lighter/greyer = Astra's clearcoat (32.9 % of the brow fronts, +13 L*) -> a MATERIAL note for Astra, not the print.

### 2. On the owner's verdict
- If A, B or C: make that level the production level (re-run the level map with that candidate through the existing path in
  `tests/photoprint/ar_calibrate.py` / `shading.level_map`, write print.png, re-run the tool test on a temp COPY: `python -m
  glasses3d.tests.photoprint.print_tooltest --bake <temp copy>`), record the verdict in `data/ledger/owner_verdicts.jsonl` bound to the
  tool-test GLB sha (tool test, not a delivery). If "none"/"Astra's": ask nothing technical; diagnose from the owner's words.
- Then the next WP5 step (by eye, single-source print): (1) the brow-top blotches; (2) the off-angle lightness of the print's darkest
  decile (+1.6..+5.1 L*); (3) +x temple at yaw -30 grey/flat, -x temple spots smaller/rounder/more orange than photo 03; (4) move the
  bake (`tests/photoprint/bake_ft1119.py`) and AR level calibration (`tests/photoprint/ar_calibrate.py`) into a library call
  (`g3d_host/photoprint/production.py`, `inputs.job_from_dir`, no FT1119 defaults) = WP2 gate G1 / finding F2.

### 3. Running workflows at handoff (all launched from session 9e8d13e8; outputs land on disk; NONE of them commits)
How to inspect any of them: journal `C:/Users/Shay/.claude/projects/C--Users-Shay-PycharmProjects-lenses/9e8d13e8-4744-4df5-9e5f-66ecbeca8f3e/subagents/workflows/<run>/journal.jsonl` (one `started` + one `result` line per agent;
the workflow's final return value also appears in `C:/Users/Shay/AppData/Local/Temp/claude/C--Users-Shay-PycharmProjects-lenses/9e8d13e8-4744-4df5-9e5f-66ecbeca8f3e/tasks/<task>.output`, a temp folder that may be cleaned). Scripts copied to
`data/handoff_20261007/<name>-<run>.js`. `Workflow({scriptPath, resumeFromRunId})` only works inside the SAME session; a new
session that finds a workflow died mid-agent (last journal line `started`, its agent transcript `agent-<id>.jsonl` not growing for
>5 min, no result) must: read the journal results so far, `git status` + `git diff` the files that workflow owns (section 4), run
that workflow's tests, and write a fresh, smaller workflow that continues from the last completed agent (pass the earlier results in
the prompt). Never assume a dead workflow's partial edits are correct.

| Workflow (task / run) | Done at 10:41 | Running at 10:41 | Remaining steps in its script | On completion (main session) |
|---|---|---|---|---|
| `g3d-wp5-step6-fixes` (wr23ng2j9 / wf_730bf6ec-6cf), launched 09:30 | - | `fix:r1` | re-review (findings + provenance) x up to 3 rounds with fixes between | Check `print_sha_unchanged` true (print.png + tool-test GLB byte-identical); rerun every changed test + `test_step6.py`, `test_correction_round.py`, then the full WP5 suite (`glasses3d/tests/photoprint glasses3d/tests/test_print_blender.py`, ~46 min under load); commit section-4 group A. Findings input: `data/handoff_20261007/step6_findings.json` (1 major + 11 minor; full review in `step6_review.json`) |
| `g3d-wp5-all-angles` (wubw37okd / wf_81098786-301), launched 08:19 | build, step6-review, verify r1 x3, fix r1, verify r2 x3 | `fix:r2` | verify r3 x3; returns (no fix after r3) | If sheets changed visibly, re-send to the owner. Run `cd glasses3d/arview && npm test` (2 capture-stall tests flaked under load, pass alone 5/5) + `pytest glasses3d/tests/photoprint/test_all_angles.py`; prove the default pose sets byte-identical (`data/wp5/all_angles/byte_identity/`); commit group B separately. Open from r2: owner-eye said C = A visually (sheet label could say "almost the same as A"); rows 03/06 on their own sheet by design; B/C GLBs carry a stale "step-4" asset note (ar_calibrate.evaluate copies the label; fix in ar_calibrate later); calibration's "near_18deg" pairs are ~27-30 deg by viewing direction (name inaccurate, sheet says only "darker") |
| `g3d-wp6-rediagnose` (w2lnmo22g / wf_5dce193d-796), launched 2026-10-06 22:03 | capability (TOOL OK with hand-traced parameters), trace (fit-4: STOP-I fixed, decals + hinge warnings fixed; automatic relief r_C 1.46 vs hand 3.4 -> fresh proof stops at STOP-C, log `data/wp6/logs/proof_fit4.log` 09:13), review:1 (3 majors: hand trace's R4 pass graded against an unfitted control; hand r_C 3.40 is the front-bound maximum, not a photo measurement; S1's crease guides too good to detect the identifiability failure) | `fix:1` | review:2 (+ fix:2 if majors), then `result`: fresh full proof in `data/wp6/proof2` (14 workers), stop-rule verdict, sheets photo / tool test / delivered | Read the result. It will very likely end with OWNER questions (capability said "two rule-set questions only the owner can decide" + STOP-C = an owner evidence decision): show the owner labelled side/top/rear sheets and ask ONE plain question at a time. NOTE: `data/wp6/logs/pytest_fit4_parallel.log` showed an F at 09:18 during a memory-exhaustion window caused by the main session's 4 parallel test runs: if the result reports that failure, rerun it before believing it. Commit group C after its tests pass. |
| `g3d-wp2-stages-ab` (wy9jdvbyr / wf_32fc96f9-1c5), launched 07:56 | impl:A (324 passed in tests/test_wp2_*.py; restore fingerprints equal on 3 scenes; corpus 0 false positives over 214 snippets; replays Prada 19/19 etc.), review:A:1 (0 blocker/major, 1 minor) | `impl:B` | review:B:1 (+ fix:B:1, review:B:2, fix:B:2 if majors) | Run `glasses3d/.venv/Scripts/python -B -m pytest -q tests/test_wp2_*.py` (from glasses3d/); check stage-B stop rules (camera parity <= 0.01 px analytic / <= 0.1 px rendered; compare reproduces basis-01 within 0.001 IoU); commit group D (stage A and B may be one commit or two). |
| `g3d-wp2-stage-c` (wu0uwqn8f / wf_91f8f2b9-ed5), launched 08:26 | impl:money (Prada replay: row 1 reserved 719,438; settled total 14,261,424 micro-USD EXACTLY; 59 count calls = 59 creates = 59 rows), impl:estimate, impl:loop, verify r1 (network OK; money 2 majors: tamper check misses truncation, a transport-settled row can be rewritten as an owner no-charge reconcile; design 4 majors: implementation receipt (§9 snapshot) not built, paid entry never recomputes the request sha, the two-way tool contract is optional (contract=None skips it), psutil not pinned) | `fix:r1` | verify r2 x3 (+ fix:r2, verify r3) | Run `pytest -q glasses3d/tests/agent` (basetemp under data/pytest_tmp); stage-C stop rule = any non-loopback connection or any 1 micro-USD discrepancy; if psutil must be pinned, add it to `requirements-agent.txt` with its PyPI hash (installs pre-approved, safe sources only); check `tests/agent/data/*.json` hold token counts only (repo is PUBLIC); commit group E. |

### 4. Uncommitted files at handoff, by owner (commit each group ONLY with explicit paths; never `git add -A`/`.`)
- **A. WP5 step 6 (+ step6-fixes):** `glasses3d/g3d_host/photoprint/{__init__,fill,highlights,labels,project,shading}.py`,
  `glasses3d/plan/PORT_REVIEW.md` (step 6 sections), `glasses3d/tests/instruments/print_metrics.py`, `glasses3d/tests/photoprint/
  {ar_calibrate,ar_levels,bake_ft1119,correction_proof,print_tooltest,printproof,test_correction_round,test_print_bake}.py`, new
  `glasses3d/tests/photoprint/{step6_proof,test_step6}.py`, `glasses3d/tests/test_print_blender.py`, plus any file the step6-fixes result
  lists in `files_changed`.
- **B. all-angles / arview explicit-camera capture:** `glasses3d/arview/{README.md,qa/README.md,qa/capture-cli.test.mjs,qa/capture.mjs,
  qa/capture/cli.ts,qa/capture/page.ts,src/render/renderer.ts}`, new `glasses3d/arview/{qa/capture-cameras.test.mjs,qa/capture/cameras.ts,
  tests/capture-cameras.test.ts}`, new `glasses3d/tests/photoprint/{all_angles,test_all_angles}.py`. (Never touch `ar/`.)
- **C. WP6 re-diagnosis:** `glasses3d/g3d/temple/{__init__,build,fixtures,measure}.py`, `glasses3d/g3d/temple_bpy.py`,
  `glasses3d/g3d_host/{relief_fit,relief_views}.py`, new `glasses3d/g3d_host/{relief_cue,relief_hinge}.py`, `glasses3d/tests/proofs/
  {wp6_bpy_run,wp6_prada,wp6_prada_stages,wp6_s1_theta_check}.py`, `glasses3d/tests/proofs/{wp6_prada_guides,wp6_prada_s1_dependencies}.json`,
  new `glasses3d/tests/proofs/{wp6_fit4_diagnostic,wp6_handtraced,wp6_s1_floor_check,wp6_theta_frame}.py`, `glasses3d/tests/
  {test_temple_build,test_temple_design_checks,test_temple_slab_control}.py`, new `glasses3d/tests/test_temple_relief_cue.py`.
- **D. WP2 stages A-B:** new `glasses3d/agent/{__init__,astra_corpus,journal,procs,worker_client,seeing}.py`, new `glasses3d/g3d/{checkpoint,
  lib,render,rpc,safe_mode,worker}.py`, new `glasses3d/g3d_host/{blender_camera,compare}.py`, new `glasses3d/tests/blender/{wp2_camera_parity,
  wp2_synthetic_scene}.py`, new `glasses3d/tests/test_wp2_{camera,camera_parity,checkpoint,compare,corpus,fit_cameras,pick,procs,rpc,
  safe_mode,seeing,worker}.py`.
- **E. WP2 stage C:** new `glasses3d/agent/{approvals,budget,estimate,loop,offline,pricing,receipts,replay,session_inputs}.py`, new
  `glasses3d/tests/agent/**` (`__init__.py`, `conftest.py`, `support.py`, `loop_support.py`, `data/prada-8c82-tokens.json`,
  `data/studio-astra-13-tokens.json`, `test_*.py`).
- Ownership of `agent/__init__.py`, `agent/seeing.py` and `agent/session_inputs.py` is inferred (A-B vs C): confirm against each
  workflow's final report (`files` lists) before committing; if both touched a file, commit it with the later group after both pass.
- Any file NOT in these lists that appears later belongs to whichever workflow reports it; ask `git log`/journals, never guess.

### 5. Next steps after the owner's verdict (in order; each with its $0 proof, committed on `glasses3d`, no push)
1. Process the five workflow results (section 3), commit groups A-E.
2. WP5 next step (section 2), owner judges by eye; then G1: the print as a library tool.
3. WP6 per its result: owner questions in plain words with images; if the tool cannot make facets even with good parameters, stop and
   report (PLAN WP6 stop rule). Then G2 prerequisites: `g3d_temple` in the exporter allowlist, temple ops in `g3d/__init__` + `g3d/cli.py`,
   Prada coupling removed; check_relief must receive photo crease counts (WP7_DESIGN open item) and FRONT_BOUND merged into the flags.
4. WP2 stages D (tool registry + wrappers, WP5/WP6 gated), E (jobs CLI, brief, notes, owner ledger + migration, watchdog), F ($0
   proofs A + B, all 16 criteria of WP2_DESIGN §14; stop rule: proof A total off by even 1 micro-USD). Fix WP2_DESIGN §11's wrong
   sentence "deliver refuses only on §2" (export refusals also stop it).
5. WP7 implementation (`agent/instructions.py` + the contract tests of WP7_DESIGN §2) once the D registry exists.
6. WP8 (paid): ask the owner the money questions + WP7_DESIGN §4.2 #1 ("sizes from the specifications; shape, colour and finish from the
   photos") and #2 (arview lensenv); then one capped smoke turn, then Prada, FT1119, the known-good product, then 3 unseen products.

### 6. Owner decisions and preferences (all also in Claude memory)
- The owner judges by eye in AR; Claude makes all technical/engineering decisions and records them here; ask the owner ONE plain question
  with labelled images only when it is truly theirs (look right? continue/stop? money?). Lead with the verdict; unvarnished facts.
- Always say "the code/tool does X"; Astra builds models, Claude builds tools; $0 tests on COPIES are tool tests, never delivered.
- Machine: Intel Core Ultra 9 285H, 16 cores, Arc 140T GPU, 31 GB RAM + ~24 GB pagefile, dedicated: use it heavily, but MEMORY is the
  ceiling with several pools at once (each photoprint/scipy worker commits ~1.2 GB): read free commit before a pool and size workers to
  ~free_commit_GB / 1.3; a MemoryError / "paging file is too small" / BrokenProcessPool under load is an environment fault, not a code
  defect. NEVER kill a healthy slow run (check liveness with full timestamps). Workers opt out of EcoQoS.
- Package installs: pre-approved from safe sources (official PyPI/npm, exact pins with hashes, recorded). Paid API calls: owner OK per run.
- No web research by Claude (the owner runs ChatGPT research when asked; pasted research is data).
- Verdicts (`data/ledger/owner_verdicts.jsonl`, sha256-bound): Tom Ford FT1123 (0e77e11e...), Oakley OO9208 v2 (2c46877a...), INVU
  IA22403B v2 (4a44a6ac...) = "perfect" (re-judged on arview 2026-10-06). Prada = OPEN FAILURE (its old "looks great!" predates noticing
  the facets, and the old runtime drew its temples shortened).
- Every product has six real photos: front, back, left, right, top, angled (owner-generated views are real). FT1119: 05 front, 02 back,
  03 right side (wearer's right, -x temple outer), 06 left side from ~19 deg above (+x temple outer + -x inner print), 04 top, 01 angled.
  Astra's q001 names temples by the VIEWER's side (Temple_Left = -x): tools use the x sign, never names. +x = the wearer's LEFT.
- Repo shayashertlv/lenses is PUBLIC; pushing main deploys to Railway. Work on branch `glasses3d`; never push without the owner's OK.
  Never commit photos, job data, renders, GLBs, .blend or .env. Never modify `ar/` or the root app. Never `git stash apply/pop`.
  The OpenAI key is in `automation/.env`: never read or print it.

### 7. Gotchas learned
Long Bash heredocs get mangled (write scripts with the Write tool; short heredocs < ~40 lines are fine); liveness checks must compare
full timestamps; agents may sweep other workflows' files into a commit (stage paths explicitly); the session's working directory jumps
between folders on its own (always use absolute paths); a `timeout` wrapper on the 46-min WP5 suite cuts it off (run it without one, log
to a file); the SDK install added a pytest `anyio` plugin (harmless); older calibration / sheet generations are kept under
`data/wp5/pre_*` and `data/wp5/tooltest/pre_*` and the all-angles rounds under `data/wp5/all_angles/round0|round1` (nothing deleted);
WP6 temple ops are not yet in `g3d/cli.py`, `g3d/__init__.py` or the exporter allowlist; the session's scratch/task files were copied
to `data/handoff_20261007/` (step-6 result `wp5_step6.json`, step-6 review + findings, pip report + venv freezes, WP7 result, every
workflow script).

## 2026-10-05: WP0 (setup, ignore rules, port review)

Done:
- Branch `glasses3d` created from main 7e25722 (no push).
- `.gitignore`: the PLAN block, plus a deliberate re-include of `/glasses3d/tests/fixtures/**` (the global `*.png`/`*.jpg`
  rules otherwise hide fixtures) and a re-ignore of `*.glb`/`*.blend`/`*.blend1` inside fixtures, so no model binary can
  enter Git. Verified with `git check-ignore -v --no-index` on 21 sample paths (data/, output/, dist/, qa/output/,
  .venv/, venv/, glb, blend, blend1, webp, pkl, npz, arview/modeling, arview/site; fixtures png/jpg included; fixture
  glb/blend ignored; plain .py/.ts not ignored; .env ignored).
- `glasses3d/.venv` (Python 3.12.10): numpy 2.5.3, scipy 1.18.1, opencv-python 5.0.0.93, scikit-image 0.26.0,
  pillow 12.3.0, pytest 9.1.1, **PyPatchMatch 2.0.1 (Windows wheel `pypatchmatch-2.0.1-py3-none-win_amd64.whl`)**.
  Verified: `patchmatch.inpaint(image, mask, *, global_mask=None, patch_size=15)` fills a hole inside a uniform
  (200,80,40) region with exactly (200,80,40) and leaves every pixel outside the mask byte-identical.
- Reference data copied from `C:/Users/Shay/PycharmProjects/glasses3d-reference/` to `glasses3d/data/reference/`
  (ignored): projection/ 2.4 MB, junction/ 29 MB.
- Prototype constants repointed relative to `glasses3d/` (`Path(__file__).parents[3]`): make_uv_test.py `OLD`/`HERE`
  (HERE = `data/reference/projection/rerun/`), probe_fit.py and probe_project.py `HERE`, run.mjs `AR`
  (= `../../../../ar`), and all three ar_capture configs (`output` → `data/reference/ar_capture/<name>`; case paths →
  `data/reference/junction/copies/`). config-ab4.json also pointed at a dead temp folder; fixed too. The prototypes
  still import `automation/bsa` (prototype-only; the pipeline never will).

$0 proof (WP0.4): `make_uv_test.py` re-run from scratch (no cached cameras), 4 min:
- coverage Brow_L 0.727, Brow_R 0.712, Temple_L 0.833, Temple_R 0.570: identical to PLAN §1;
- re-fitted `cam_reference-0{2,3,4}.png.pkl` and `ft1119_uv_sources.png` byte-identical to the originals
  (the fit is deterministic);
- fit quality (WP3b baseline): ref-02 back IoU 0.897 / contour 5.46 px; ref-03 side: 'right' prior IoU 0.738 /
  11.47 px (the 'left' prior gives 0.384 / 34.1 px); ref-04 top: 'top' IoU 0.830 / 5.24 px ('topb' 0.699 / 10.6 px);
  earlier probe cameras: ref-05 front 0.898, ref-01 angled 0.869, ref-06 (elevated side, 'left' prior) 0.709.

- **PORT_REVIEW.md** (WP0.5): 17-agent review of the newest Studio (`source-before/blender_agent`, 52 files,
  12.5k lines): keep 6 / rewrite 23 / drop 23, five verdicts corrected by adversarial second reviewers; 53 test files
  reconciled; docs reviewed (facts carried forward); needs nothing provides listed. Full evidence in
  `plan/port_review/studio.json`. Audit-flagged gates (construction labels, selection citation gates, correction-family
  caps, source_field two-colour ramp) all dropped. No code copied yet (copies land in WP1/WP2/WP3).
- **Blender connection decision (with evidence, PORT_REVIEW §1): no MCP server; glasses3d owns a headless Blender
  worker** (`blender -b`, ephemeral-port JSON RPC, all Astra tools host-side). The community mcp-for-blender the
  Studio used bans numpy but allows bpy file I/O (the opposite of WP2's safe mode), refuses `-b`, and drops calls at
  180 s while Blender keeps running; the Lab MCP has no sandbox hook, ignores render output paths and is GPL outside
  the repo; both default to port 9876.

Facts recorded for owner decisions (PORT_REVIEW §2b): every praised Studio run used **max** effort (PLAN §2
recommends high); accepted FT1123 GLB = tomford-scratch-002/final-champagne sha 0e77e11e…; the description bug is
confirmed (studio_jobs.py:224/405-426); owner verdicts also depended on viewer settings (lens-reflection multiplier,
width, clip).

WP3 groundwork found while WP0 ran (not yet committed; lands with WP3):
- Studio scenes (8c82 agent/scene.blend, 3b8a original/scene.blend): METRIC, scale_length 0.001 (millimetres), scene
  property `blender_agent_axes` = '+Y up; +Z front'; **origin marker = scene property `mdl_bridge_underside`** (float[3]);
  every mesh has `partRole` and `LENSES_part_visibility`; Prada's scene has an "Inspection only | excluded from
  delivery" collection (camera + 3 lights).
- `g3d_host/glb.py`, `lens_id.py`, `spec.py` (validate_spec, exactly the §2 rows) + 47 passing tests. On real files:
  q001, Prada, INVU pass; ar_v4 crystal r9 fails ONLY the origin row (bridge underside at y +10.23 mm), which WP1's
  report must say.

Next step: WP1 (arview). The WP1 read-only analysis workflow (per-file fork verdicts, throw-site classification,
lens-id and capture design) is running.

## 2026-10-05 (later): WP1 / WP3 / WP3b in parallel (state for a resuming session)

- WP1 analysis (14 agents) -> `plan/port_review/arview.json`, PORT_REVIEW §7, binding decisions in `plan/WP1_DESIGN.md`.
  Stage A (verbatim copy, 135 files kept, 93 dropped) committed 1870359; `npm ci` done in `glasses3d/arview`; baseline was
  19 TS errors (imports of dropped modules). Stages B (build), C (lens id), D1 (load front-end + acceptance policy), D2
  (temples/clip/continuity/fit scale) run as one sequential workflow (implement -> adversarial review -> fix, max 2 rounds).
  Remaining after it: stage E (capture CLI), F (webcam page), G ($0 proof incl. r9 refusal recorded on main's ar/).
- WP3: `g3d/export_glb.py` (+ glb_patch, cli), `g3d_host/blender.py` (export_copy, export_and_validate), validator. $0 proof
  passed before review (flat facets, clearcoat 0.8, 64x48 embedded image, q001 + Prada scenes export and pass). The review
  (28 agents) reproduced real defects: upside-down/roll/pitch models passed; shields false-failed; unmeasurable origin
  passed; instancing ignored; validator crashed on bad indices; exporter corrupted custom normals on mirrored objects, used
  viewport evaluation, ignored excluded collections, lost relative textures silently; facet test could not detect a 40°
  re-smooth; texture test had no UVs. Calibrated thresholds (8 real models) now in WP1_DESIGN §1. Fix + re-verify running.
- WP3b: porting automation/bsa cameras into `g3d_host/` (camera, raster, cameras, matte, texture_utils, fitmesh,
  fit_views); acceptance = reproduce the prototype's three FT1119 fits exactly, then measure all 12 reference views
  (6 FT1119 vs q001, 6 Prada vs the delivery), report IoU and contour error in px and mm, then ASK THE OWNER for the WP5
  registration threshold. open3d 0.20.0 installed in the venv.

## 2026-10-05: WP3 done (6acfc5a)

- Exporter `g3d/export_glb.py` + validator `g3d_host/spec.py` (see the commit message for the full list).
- $0 proof: flat facets survive (12-gon temple, per-corner NORMAL directions read back), smooth parts stay smooth,
  sharp-edge / weighted-normal / mirrored / non-uniformly-scaled custom normals match Blender's evaluated corner normals
  within 0.025°, clearcoat 0.8 (roughness 0.05), UV-mapped 64x48 embedded image, transmission; the q001 and Prada
  scene.blend files export and pass validate_spec with their sha256 unchanged; all 41 unique Studio/live GLBs pass;
  ar_v4 r9 fails only the origin row (bridge underside +10.23 mm).
- Adversarial review: 28 agents, 25 reproduced defects (upside-down/roll/pitch models passed, shields false-failed,
  crashes on bad indices, mirrored custom normals corrupted, viewport-vs-render evaluation, excluded collections,
  relative textures lost, weak facet/texture tests). All fixed; re-verified by a separate agent; mutation tests 13/13
  killed. Suite: 228 passed, 1 skipped.
- Orientation thresholds (hard; calibrated on 8 real models): temple roots above the lens centre (real +4.0..+18.0 mm),
  left/right lens halves within 3 mm (real <= 0.16), temple slope < 15° (real -9.4..+4.7). Origin: frame + lens
  triangles sliced at x in {-1..1} mm (shields), x judged on the lens box; unmeasurable origin fails.
- Known limit: Draco/meshopt geometry is not decoded by the Python reader; affected rows become 'unverified'
  warnings only where a compressed primitive's POSITION bounds could change the result.
- Convention: KHR_materials_volume lengths are authored in METRES (Studio convention); validate_spec warns above 0.01.

## 2026-10-05: WP3b port + $0 proof done (waiting for the owner)

- Ported (PORT_REVIEW §9): `g3d_host/camera.py`, `raster.py` (open3d ray casting), `cameras.py` (ViewFit, priors incl.
  first-class top/topb and top_oblique_left/right, contour ICP, Powell, native_metrics), `matte.py` (quadratic
  background), `texture_utils.py` (de00, quantile_gain, rasterize_triangles, nearest_fill, sample_bilinear,
  visible_points), `fitmesh.py`, `fit_views.py` (driver + CLI). No runtime import of automation/reconstruction
  (a test enforces it).
- Exact reproduction: under **scipy 1.16.0 (now pinned)** the ported production path reproduces the prototype's three
  FT1119 cameras on all 21 fields within 1e-6. scipy 1.18.1 drifts up to 0.19° / 1.07 px (traced to a ~1e-8
  least_squares step difference amplified by accept/reject; the original automation code drifts identically).
- 12-view baseline (mean contour mm / p90 mm; px ÷ px/mm at the front pivot), outputs `data/wp3b/`:
  FT1119 vs q001: front 0.66/1.46, rear 0.68/1.53, top 0.69/1.84, angled 0.79/1.87, elevated side 1.05/2.81,
  side 1.44/6.16 (model shape differs: brow ~4 mm higher in the photo, fit turns to show the far temple).
  Prada vs delivery: front 0.26/0.59, top 1.24/3.33, angled 1.27/4.16, side-05 1.99/4.22, rear 2.76/11.4,
  side-01 5.58/20.6 (matte keeps floor reflections and fills the gap between temples: an upper bound; delivered
  temples also differ from the photos = the WP6 failure).
- Ref-06 (elevated side): PLAN §1's "needs a proper prior" is wrong: the photo sits at ~19° pitch; with seeds there
  the fit equals the 'left' result (IoU 0.708). The 0.71 ceiling comes from thin temples (contour error 1.05 mm).
- **Owner question (blocks WP3b "done" and WP5's stop rule): the registration threshold.** Recommended: mean contour
  <= 1.0 mm and p90 <= 2.5 mm per view used for projection (FT1119: front, rear, top, angled qualify).

## 2026-10-05: WP1 stages B-D2 committed (960ad54)

tsc 0 errors, node tests 476/476, qa 18/18, vite build OK; ar/ untouched. Each stage implemented, adversarially reviewed,
fixed. Stage D1's round-2 fix (default-scene-only parse via defaultSceneDocument/packGlb; webcam page listing failed §2
rows) was not re-reviewed inside that workflow: stage R of g3d-wp1-capture-proof reviews it and aligns arview's origin-x
and orientation checks with the fixed validator (agreement test on shared fixtures). Remaining WP1: R, E (capture CLI),
F (webcam page `arview serve`), G ($0 proof: r9 refusal on main's ar/, captures of r9 / q001 / Prada / mm copy / renamed
multi-mesh copy / temples-removed copy, Python-vs-arview agreement table, q001 vs prototype seated junction).
PORT_REVIEW §7: `tests/lens-appearance.test.ts` row still says keep (dropped in stage B with its module) - fix at the WP1 commit.

## 2026-10-05: WP4 design (plan/WP4_DESIGN.md)

Judge panel: 3 independent designs (robust 45, minimal 47, physical 42 of 60), synthesized on the minimal base. Measured facts
that decided it: all four real lenses (FT1119 q001, Prada, Oakley, INVU) are closed manifold solids with a back cap translated
along -Z and one wall ring; FT and INVU caps are toric (no sphere growth); §3 defaults as hard thresholds would fail accepted
models (FT eyewire capture 0.40 vs 0.45; Prada 65% of stations reach 0.8; INVU top rail 0.10), so pass = §3 lower bound,
target = §3 default, per station, growth only where the grown edge stays hidden; 42-45% of each shield edge is exposed and
never grown (Oakley must move 0 vertices). FT1119 browline: hide_eyewire with per-station lift (0.84 mm median) over x
11.6-56.2 mm; must match the prototype seated+rimsplit numbers (top edge inside 100%, band 0, rim split 78-102 deg).
Open risk the owner may want to know: under §3, Prada (~26% of nodes, <=0.45 mm) and INVU (~267 top nodes, <=0.9 mm) change
at HIDDEN stations inside opaque material, plus Prada's rim sharpening (intended rimsplit fix).

## 2026-10-05: WP5 STOPPED (stop rule) - report to the owner

$0 feasibility probe + 3 designs + judge (plan/WP5_DESIGN.md, verdict NO-GO for WP5 as PLAN §4 writes it). Scratch evidence:
C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp5/ (step1..4 JSON + side-by-side PNGs).
- With q001 geometry and the WP3b silhouette cameras, photo projection does NOT beat Astra's procedural texture on both
  held-out band-pass r (DoG sigma 1 px - 4 px) and gained deltaE00. Gained deltaE00 ties on views 01/04/05; band-pass r is
  above 2 sigma of the shift null only on view 04.
- Cause = registration: the brow print is 1.88-2.39 mm off between views 01 and 05 (the only pair whose print matches)
  although their silhouettes fit at 0.66-0.79 mm. ~2.5 mm is a rigid per-view offset (q001's shape biases the
  silhouette-fitted cameras), 0.6-0.8 mm non-rigid (geometry). Silhouette accuracy does NOT predict print registration.
- Needed: <= 0.8 mm print registration (0.5 mm with margin): registered, band-pass r 0.61-0.74 vs Astra 0.11-0.17; at
  0.83 mm 0.22-0.27; at 1.25 mm ~0.
- What works: a joint model (one camera per view + a bounded per-brow depth correction, silhouette guards, fit on even
  4 mm stripes, tested on odd ones): Brow_Right residual 0.00-0.21 mm, band-pass r 0.58-0.69 vs Astra 0.02-0.15, gained
  deltaE00 4.68-6.74 vs 5.50-12.76, both folds. Brow_Left and the temples still fail (~16% of the print area verified).
- The PLAN's "0.017 vs 0.066" baseline cannot be reproduced (its filter was never recorded).
- My WP3b recommendation (1.0 mm mean SILHOUETTE contour) is WITHDRAWN: it would pass views whose print is ~2 mm off.
- Owner decisions (WP5_DESIGN §12): D1 photometric print threshold 0.8 mm (target 0.5) replaces the silhouette threshold;
  D4 is WP5 acceptable with only Brow_Right pixel-verified (other regions printed from one anchor view, flagged, judged
  by eye in arview)? if not, WP5 stops on FT1119; D6 text handling (default: the decal carries brand text, photo text is
  filled). D2/D3/D5 are technical defaults.

### WP5 owner exchange (2026-10-05)
The owner found D1-D6 unclear ("I'm not really understanding what you want"). Correction of approach: D1, D2, D3, D5, D6 are
engineering choices and Claude takes them as recommended in WP5_DESIGN §12 (photometric print gate 0.8 mm / target 0.5;
per-(view, part) edge gate for colour donors; stripes-and-folds proof on registered regions; parallax used for sampling
only, discrepancy reported to Astra; brand text from the decal, photo text filled). The owner gets ONE question, with
visual evidence (data/evidence/wp5_right_brow.png, wp5_left_brow.png): continue building WP5 although only the
registered regions can be pixel-proven, the rest judged by eye in AR?

### WP5 resumed (2026-10-05, owner: "the images show promise, don't give up")
- Owner answered: continue WP5 (D4 = yes in substance). D1-D3, D5, D6 taken as engineering decisions (WP5_DESIGN §12).
- CORRECTION (owner was right): every FT1119 temple face is photographed. Image 03 (owner file "ChatGPT Image Oct 1, 2026,
  07_41_47 PM.png") = the wearer's RIGHT side (-X temple, outer face). Image 06 = the wearer's LEFT side from ~19° above
  (+X temple outer face, tip medallion) plus the -X temple's INNER face with "TOM FORD MADE IN ITALY CE". PLAN §1's "no photo
  shows its outer face, so mirror fill is needed" is wrong. Trap: Astra's q001 names temples by the VIEWER's side
  (Temple_Left = -X = wearer's right), so every tool must use the x sign, never part names.
- Model content note (for Astra, not hand-fixed): both photographed temple tips carry a gold medallion; q001 has one.
- Seams: the research solution (gain from same-surface overlaps, graph-cut seams inside near-best overlaps, multi-band
  blending) is in WP5_DESIGN §6.4; it needs aligned photos, which step 0 provides.
- Each product has six images: front, back, left, right, top, angled (FT1119: 05, 02, 06, 03, 04, 01).

## 2026-10-05: WP6 design (plan/WP6_DESIGN.md)

Panel: tool 36, measure 36, trace 39 (base). Measured on Prada: delivered temples are a slab (outer bulge <= 0.62 mm, inner face
flat); photos show 4 facets near the hinge (F1 top, F2 logo triangle, F3 front-lower, F4 rear-lower), flat inner face; facet
corners trace to 0.1-0.3 mm in 05 and 01 independently; relief (sideways offset) only to ~+-3 mm absolute (front photo bounds
|X| <= ~72.5 mm). New matte mode `glossy_floor` (removes floor reflections, only removes pixels): side-01 fit 5.58 -> 1.43 mm.
Rear camera stays unreliable unless fitted jointly with a per-photo hinge angle.
PLAN correction (technical, recorded for the owner): the 1.9-4.3 mm bulge band is kept as a REPORTED warning, not a pass gate,
because a thicker slab passes it; relief gates R1-R4 (single-plane share <= 0.5 vs delivered 0.81; crease normal change;
cross-view crease transfer within 0.7 mm; silhouette cost <= 0.1 mm) plus the plan's top-view and rear-envelope comparisons
decide, and a zero-relief slab control must fail them.

## 2026-10-05: WP1 $0 proof passes (checkpoint 6bd502e), closeout running

Independent checker looked at the images: r9 captured (translucent crystal, gold core, see-through lenses; origin +10.24 mm
reported) and refused on main's ar/ with "The rear-drop start must be forward of the accepted cap." (ar/src/render/rear-drop.ts:151,
as predicted, not :194); temples-removed copy -> "temple left/right missing" (stop rule holds); mm copy rescaled with warning;
arview == spec.py on 143 fabricated cases + real GLBs (49/49 rows on the proof models); junction: q001 lens top vs brow median
-0.27 mm with 18/24 gap stations and the pale eyewire line in 8/8 crops; seated copy +0.93 mm, 0 gaps, no line (matches
ab3-q1-vs-fixed.png); arview serve verified (COOP/COEP/CORP, camera policy, sha256 header).
Closeout (workflow g3d-wp1-closeout): Prada temple shafts drawn only to ~30-35 mm in yaw captures (investigate vs the Studio-era
runtime, fix), re-review the last unreviewed E/F fixes, flaky hair-schedule test, scale-invariant degenerate-triangle rule
(both validators), temples-missing reason text (both), junction decals on lenses, served fallback count, measured serve
bookkeeping, a stronger no-metadata arbitrary-names fixture. PLAN §1 correction: q001's lens top stops 0.09-0.46 mm below the
brow at 18/24 stations by triangle slicing (the plan's 0.2-0.9 mm included endpieces).

## 2026-10-06: WP5 stage 1 (registration) built; STOP A fired; continuing in single-source mode

Stage 1 code (g3d_host/photoprint: inputs, signals, regions, pose, edges, register, gate, check_print, report, procs, step0;
tests/instruments/print_metrics.py independent; tests/photoprint; tests/proofs/wp5_ft1119.py): 76 passed + 3 expected A0 failures.
FT1119 with q001: no region registers in both folds (Brow_Right closest: 0.28/1.76/0.66/1.22 mm; the joint fit is not unique,
starts end 3.3-11.2 mm apart); 0% pixel-verified. Area: partner views exist for 30.3%, single-view 35.8%, unseen 28.9%, end caps 5%.
The gate's decoy rule as written refused even perfect synthetic matches (instrument defect) and the A0 null-mean bound fails a
perfect render. ENGINEERING DECISIONS (owner asked not to be given technical choices; owner chose "continue, judge by eye"):
redesign the decoy null and fix the A0 bound (step 3); WP5 delivers SINGLE-SOURCE print (each region from its anchor photo, no
mixing of unregistered views, seams on creases, fill for unseen, B3 realism checks); strict pixel proof (B2) = diagnostic for
Astra, not a gate; owner judges in arview (B4). Told the owner plainly that 0% is pixel-verified.
Liveness note: a false stall alarm came from comparing times without dates; both workflows were alive.

## 2026-10-06: WP1 done (closeout)

- Prada temple cut in turned captures: cause = arm-spread pivot (rear-drop) took the first slice where the arm is <= 15 mm tall
  as the hinge; Prada's tall shield put it at z -73 mm, so the front half of each arm was never spread and sat inside the head
  proxy. The Studio-era runtime has the same pivot code (stash rear-drop.ts:173) and its own preview of the Prada delivery
  (preview/candidate__actual-ar__yaw-left/right.png) shows the same cut: the owner's "looks great!" was given on shortened
  temples in turned views. Fixed (hinge = thin-shaft boundary only within 15 mm behind the lens rear); arms now drawn to where
  they pass behind the ear (~-75 mm at yaw +-45; was -61). New report check `TEMPLE CUT` on turned captures.
- Fixed: flaky hair-schedule test (injected clock), scale-invariant degenerate-triangle rule (both validators), temples-missing
  reason (both validators; +Y up judged on the lens box when temples are missing), decals on lenses excluded from the
  junction metric, served-report fallback count, measured serve bookkeeping; a stripped no-metadata Prada copy (no extras, no
  names) captures correctly (lenses by transmission with warning, temples by position) after fixing a shared-material front
  defect it exposed.
- Re-proof: 6/7 items pass; the 7th (degenerate counts equal between q001 and its mm copy) is my own over-strict criterion
  for a warning-only metric: 206 vs 207 on one lens from float32 re-rounding at the threshold. Accepted, recorded.
- Tests: tsc 0; node 493/493; qa 104/104; spec 95/95; ar/ untouched.
- Risk noted (no change): the runtime's fixed 18 mm arm spread does not adapt to narrow frames (Prada arms enter the head
  proxy at -72..-76 mm, inside the behind-the-ear zone); changing it would depart from the Studio-era runtime.

### Owner note on the Prada verdict (2026-10-06)
The owner's Prada "looks great!" (10-01) was given BEFORE they noticed the faceted-temple problem (and, as WP1 found, on an
old runtime that drew the temples shortened in turned views). Prada is therefore NOT an accepted or known-good baseline: never
cite that verdict as evidence that a Prada result was acceptable. WP6 (faceted temples) remains a required fix, and WP8's done
criterion (owner accept on Prada's temples) stands.

### Owner re-judged the earlier accepts on arview (2026-10-06)
Shown in arview (default settings; sheets data/owner_review/*_review.png). Owner: "all three can be considered perfect." Recorded in data/ledger/owner_verdicts.jsonl, bound to the GLB sha256:
- Tom Ford FT1123-D 26E 49 (crystal): accept, sha256 0e77e11ee6ac91b0b5ddc9032e91a1cdd63f88667997868009ddd1ee2041cdd6
- Oakley OO9208 38 920844 (optical review v2): accept, sha256 2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688
- INVU IA22403B 99M (optical review v2): accept, sha256 4a44a6ac87eb92a317c9c74b4b5661f553acb5bc7c12d613777d3e8f55064df8
These are now confirmed known-good references (candidates for the WP8 regression product). Prada stays an open failure.

## 2026-10-06: WP4 build committed (e5fdf37); fixes and WP6 build running in parallel

WP4 proof: P0 core vs instrument within 0.075 mm at >= 99% of stations (FT, Prada, Oakley, INVU); P1 numpy vs open3d <= 1.2e-4 mm;
**P2 FT1119 browline 71/71** (top edge inside 100%, band 0, lift 1.029 mm, rim split 78-102 deg, 74 lens / 444 eyewire vertices moved,
only failing stations moved, validate_spec ok); P5 Oakley 0 moved and byte-identical, INVU 538 vertices = its 269 failing nodes, checks
pass after. Stop rule did not fire. Open (workflow g3d-wp4-fix): A Prada refused by the post-check (56.7 deg fold); B crystal binding check
misreads the exact bevel/groove; C growth undershoot (INVU min 0.425 vs 0.45 target; metal fixture 0.426); D nylor channel ends 0.205;
E refusal codes (narrow eyewire, notch); F chamfer/rounded/coarse edge deviation 0.014-0.031 vs 0.01 mm; G the Oakley top p95 <= 0.80
criterion contradicts design §7 (over-capture = warning) and the owner's 'perfect' verdict -> report-only (recorded reason).
WP6 build (workflow g3d-wp6-build) runs on disjoint files; integration into g3d/__init__.py, cli.py, export_glb.py is deferred.

## 2026-10-06: WP5 step 3 (host) committed (7ac7c8f)

g3d/printuv (strip + cap charts; q001: 2321x1577 at 15.95 tx/mm, 0 overlaps, 0 flips, anisotropy p95 1.66), photoprint steps 2-7 in
single-source mode, strip-shift decoy null (A0 passes on the intended rule; the literal per-view residual rule still lists three thin grazing
views 0.24-0.33 mm). FT1119 bake: photo 41.1% / PatchMatch 50.0% / mirror 2.8% / text fill 4.1% of the atlas; registered 0%. Checks: A1, A2,
A3, A7 pass; A4 fails on 3 of 4 parts (fill streaks), A5 fails at 1 texel, B3 2 of 10; A6/B5 (Blender) not built. Seen from new angles it
reads as the real havana (owner sent data/evidence/wp5_painted_vs_now.png). Defects to fix: PatchMatch wood-grain smears near hinges and on
long strips; photo 02's gold hinge baked into the endpiece backs (metal pixels pass the acetate colour test); pad-arm rivets on brow backs;
glints on curved edges; colour steps (-x temple from 03 warmer/lighter; brow-top band from 04 darker). Next: step 4 (workflow
g3d-wp5-step4): those fixes + the Blender side (print_uv, attach_print, exporter TEXCOORD/g3d_print_uv allowlist, A6, B5) + a tool-test GLB
copy captured in arview for the owner (B4).

## 2026-10-06: WP4 done

Fix round (workflow g3d-wp4-fix): A Prada now seats (fold cause = wall faces at run starts; growth steps limited to what a wall face
takes within 30 deg; post-check unchanged): every point >= 0.6038 after, max move 0.4386 mm, 190 vertices per lens all in failing
nodes, rim split p5 78.38 deg. B crystal check measures a declared V-bevel from the groove lips: 352/352. C correction pass reaches
targets (INVU node points >= 0.45). D nylor channel ends graded as TRANSITION. E notch -> E_CONCAVE gate. F front-edge fit 0.0001 mm.
G Oakley top p95 report-only (design §7 + owner's 'perfect').
Re-proof: P2 71/71; P3 34/40; P4 109/112; P5 31/32. Stop rule never fired (Oakley 0 moved; every moved set = its failing band).
ENGINEERING RULINGS on the remaining records (owner does not take technical decisions):
- P3.before_failing_where (one node at |x| 14.05 vs the design's approximate 10-14 band; describes the INPUT; instrument 100%) and
  P3.cover_unchanged (signed depth of frame behind the corner necessarily grows as the edge goes deeper; front/back covered share
  stays 100%) -> report-only, reasons above.
- Kept as recorded small shortfalls, not blocking: INVU 19 points 0.4489 vs 0.45 target and the metal fixture junction 0.4253 vs
  0.44 (point-level interpolation between nodes; both >= 0.12 mm above the 0.30 pass); the narrow-eyewire fixture seats partially
  (96 vertices, all in failing nodes) instead of refusing; 4 other P3 records listed in data/wp4/proof.json.
- Speed: INVU seat 12.7 s host vs 5 s budget (noted).
P6 (visual, arview junction captures of data/wp4/proof/glb/ft-before.glb vs ft-after.glb, yaw +-30 x pitch +-20): eyewire part-id
pixels in the 3 mm band above the lens top 85-304 in 8/8 crops before -> 0 in 0/8 after; 0 gap columns; brow lowest part 24/24
stations (was 4/24); lens top 0.72-0.80 mm inside the brow; white% 0-0.3 except the known far-side lens-highlight crop. Matches the
prototype seated+rimsplit. Owner image: data/evidence/wp4_junction_before_after.png.

## 2026-10-06: WP5 step 4 committed (10bf932); step 5 running

Step 4: photo metal/glint masks (endpiece-back metal 6.9->1.5% / 8.7->7.5%; rivets 0%), block quilting fill (A4 cannot pass even for a
real-print refill: 0.31-0.47 vs 0.15 limit; reported against the real-print baseline), gain field (region steps 3.81->2.54 dE00), mirrored
seam fill (A5 passes). Blender side: g3d.print_uv (splits only quads whose triangles fall in different charts: 77/2306 brow, 22/2114
temple quads; custom normals written back so shading changes <= 0.033 deg; remove_print_uv restores), attach_print/detach_print,
exporter g3d_print_uv + TEXCOORD; A6, B5, validate_spec pass on a tool-test copy of q001.
AR tool test (data/wp5/tooltest/owner_sheet.png, sent to the owner): better colour (hue ~20 deg like the photos vs Astra ~59) and temple
spots from turned angles; NOT right straight-on/worn: frame lighter and flatter than the photos (darkest decile L* 18-20 vs 12-17; median
24-31 vs 19-25: photo shading lit twice), brow fronts ~55% fill, streaks on the -x temple, light scratch marks on the endpieces.
Step 5 (workflow g3d-wp5-step5): shading removal + AR brightness calibration to the photos, brow-front photo share >= 80%, isotropic fill,
remove edge glints; then a new AR owner sheet. Default worker pool = cpu_count-2.

## 2026-10-06: WP6 tool test hit its stop rule (204f694); re-diagnosis running

Parallel fit: 14-worker pool, 621 s vs 6366 s serial, bit-identical (owner asked to use all cores; stopped the single-core run before the
owner said "don't force stop a good run" - nothing lost, code committed, stages resumed). Prada tool test (diagnostic past STOP-I):
outline better than the delivered GLB in side (contour 0.69/0.46 vs 1.55/1.10 mm), front, angled, top views; but the fitted relief is tiny
(A 1.04, B 0, C 2.22 mm) so the PRADA triangle facets barely show (R1 0.930 vs 0.5; R2 SHALLOW/FLAG; R3 worse than delivered); STOP-I =
hinge-angle recovery 2.86 vs 0.3 deg on the synthetic round trip; PRADA decals not drawn in arview on the tool-test temple; hinge-leaf
warnings. Re-diagnosis (PLAN WP6 stop rule): first build with HAND-TRACED good parameters (PLAN §0.2 tool test) to decide tool vs
tracing; then add a photo-measured relief cue to the fit, fix the theta instrument and the open review findings, re-run the proof fresh.

## 2026-10-07: WP5 step 5 committed (5ec9c25); step 6 and the WP2 design running

Step 5: shading removal kept (cross-photo agreement improves), AR level calibration (at photo 05's angle L* 15.6/23.8 vs 16.6/24.9),
brow-front photo share 81.8/79.0%, light marks reduced, 14-worker default. Still (AR close-ups sent to the owner): front pose lighter and
greyer than the photos (brow median 32.8 vs 22.0; chroma 2.9-4.1 vs 4.0-6.0), brow honey patches weak, -x temple ~7 L* light.
Step 6 (workflow g3d-wp5-step6): attribute contrast/chroma loss (texture vs AR material), calibrate contrast + chroma at the photos'
own angles, fix the -x temple level, residual endpiece marks. Material effects go to Astra as notes (Astra keeps material control).
WP2 design panel (workflow g3d-wp2-design -> plan/WP2_DESIGN.md) started: own Blender worker, safe mode, Agents SDK loop, budget/receipts,
jobs CLI, owner ledger, $0 fake-client proof. No paid call in WP2.

## 2026-10-07: WP2 design (plan/WP2_DESIGN.md)

Panel: loop 42 (base), worker 40, jobs 38. Facts: EEVEE renders headless on the Arc GPU (1.85 s, Suzanne 640x480), Cycles CPU 1.54 s;
glasses3d/.venv lacks openai/openai-agents/mcp (openai-agents 0.22.3 imports mcp at load) and nothing is cached: stage C needs the
owner's PyPI download OK. Pricing = the 161-line astra-only copy ($10 in / $1 cached / $12.5 cache write / $50 out per M; x2/x1.5 above
272k input). 17 Astra tools live; WP5 print and WP6 temples gated until wired (WP5 production call lives in a test driver; WP6 not in
cli/__init__/exporter allowlist). $0 proof: A = replay of a Prada session through the real SDK/guard/tools with its 59 recorded usages,
settled total must equal 14,261,424 micro-USD exactly; B = synthetic job covering timeouts, crash/resume, 429/520/incomplete, approvals,
stop file. Owner decisions (plain words, §17): PyPI download OK (needed for stage C); for WP8 only: budget cap per run (suggested $15),
effort (max like every praised run, or high), smoke cap $1. Engineering defaults recorded per run: output cap 16,384, max_turns 60.
