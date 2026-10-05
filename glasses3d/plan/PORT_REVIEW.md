# PORT_REVIEW: what glasses3d copies, rewrites or drops

Written 2026-10-05 (WP0.5; WP1 adds §7). PLAN.md §2 rule: code enters glasses3d only after review, by copying it in
with tests; nothing is imported at runtime from `automation/`, `ar_v4/`, `ar/modeling/` or the stash. Every copied file
(including the `ar/` fork) is listed here as keep / rewrite / drop, and drops are deleted before the first commit that
contains the copy.

- **keep**: copy nearly as is, with its tests.
- **rewrite**: the idea is needed; the code is rewritten (what to keep and cut is in the reason and in `port_review/studio.json`).
- **drop**: not needed or harmful.

Method: a 17-agent review. Seven readers each read one cluster of Studio files in full. Seven skeptical second
reviewers re-read the same files and tried to refute each verdict; five verdicts were corrected and the corrections
are applied. One reviewer covered the MCP choice, one the docs, and a completeness critic checked for unread files,
unmapped tests and needs nothing provides. Nothing was copied in WP0; the copies happen in the WP that needs them
(WP1: arview; WP2: loop/budget/session/jobs; WP3: exporter).

## 1. Decision: how glasses3d talks to Blender

**Verdict: no MCP server. glasses3d owns a small Blender worker** (option (c) of the three reviewed). It is launched
headless:

`blender.exe -b --factory-startup --disable-autoexec <checkpoint>.blend --python glasses3d/g3d/worker.py -- --port 0 --token <T> --receipt <R>`

It speaks length-prefixed JSON RPC on an OS-assigned 127.0.0.1 port. Every Astra tool (execute_blender_code, render_views,
compare_to_photos, arview_capture, check_*, export_glb + validate_spec, the g3d helpers, the host tools) is a host-side
Agents SDK function tool. The plan already runs Blender headless for WP4-WP6 (`blender.exe -b <copy>.blend --python`).

Decisive evidence (paths in `port_review/studio.json` → `mcp`):

| Need | (a) community `mcp-for-blender` 2.1.3 (ahujasid, MIT): what the Studio used | (b) official Blender Lab MCP 1.0.0 (GPL-3.0) | (c) own worker |
|---|---|---|---|
| Safe mode: numpy allowed, file I/O denied | Opposite: numpy banned (`safe_mode.py:129-131`), bpy save/open/import/export allowed (`:23-30`); one global policy for every caller (`server.py:717-731`) | None: the "sandbox" blocks only sys.exit and four wm operators (`weak_sandbox.py:11-18`) | Our policy; trusted host calls (checkpoint, export) bypass it |
| Headless `blender -b` | Refuses (`addon.py:1266-1269`); the Studio had to run a hidden GUI | Possible, but deferred replies and screenshots drop under `-b` | Yes |
| Long operations | 180 s socket timeout drops the connection while Blender keeps executing (`server.py:147, 230, 244-249`); no framing (end of message guessed by trial `json.loads`) | 300 s ceiling (`connection.py:22`) | Per-tool timeouts; kill and restart from a verified checkpoint |
| Renders at our cameras into our job folder | Viewport draw only | Ignores the output directory (`render_viewport_to_path_toolcode.py:46`); stale EEVEE identifier | Yes |
| Port conflicts | Default 9876 | Default 9876 (incompatible frames); Lab add-on autostarts from userpref; SO_REUSEADDR lets two listeners share a port on Windows | Ephemeral port per worker |
| What the Studio actually used it for | A pipe for `execute_blender_code` plus stdout markers; its real features were host tools (`agent.py:95-122`); every call was wrapped in before/after `.blend` saves because there is no undo | - | - |

What is carried over: from (a), under MIT, the validator *mechanics* of `safe_mode.py:477-975` and its tests, with new
policy tables (allow numpy with an I/O and pickle deny-list; deny bpy file operators and `.filepath` assignment), and
optionally `describe_node_type` / `bpy_api_lookup` (`addon.py:2278-2568`) as worker methods. Everything else of (a) is
dropped (server, telemetry, trajectory, self-updater, asset clients). (b) stays only as Claude's own interactive
inspection tool, never pointed at the pipeline worker.

Risks, with mitigations: the owner cannot watch a headless worker (optional `--show` GUI mode running the same worker on
a `bpy.app.timers` poll); EEVEE under `-b` depends on the Arc GPU context (re-prove in the WP2 $0 proof; keep a Cycles CPU
fallback); the static validator is not a security boundary (restricted builtins, import allowlist, env secret scrub,
job-folder-only paths); a hard timeout loses work since the last checkpoint (one checkpoint per Astra snippet, Astra is
told plainly); `open_mainfile` inside a long-running `-b` script needs its own lifecycle test.

## 2. glasses3d layout (the targets used in §3)

| Path | What | Runs in |
|---|---|---|
| `glasses3d/__main__.py` | `python -m glasses3d job create/run/status/review` (WP2) | host venv |
| `glasses3d/g3d_host/` | host tools: `glb`, `lens_id`, `spec` (validate_spec, WP3), cameras (WP3b), projection (WP5), checks | host venv |
| `glasses3d/g3d/` | Blender-side package: worker (RPC), export_glb, checkpoint, `seat_lens`/`hide_eyewire` (WP4), `faceted_temple` (WP6), materials | Blender 5.2 Python (numpy only) |
| `glasses3d/agent/` | loop, budget + pricing, receipts, session inputs, jobs, ledger, instructions (WP2, WP7) | host venv |
| `glasses3d/arview/` | development AR fork of `ar/` (WP1) | Node / browser |
| `glasses3d/tests/` | pytest (host + headless Blender); vitest lives in `arview/tests/` | - |
| `glasses3d/data/` | ignored: jobs, reference copies, renders, GLBs | - |

The verdict tables below were written before this layout was fixed; where a target says `glasses3d/blender/...` or
`glasses3d/loop/...`, read `glasses3d/g3d/...` or `glasses3d/agent/...`.

## 2b. Facts the review established that bear on open owner decisions

- **Reasoning effort.** Every Blender Studio run the owner praised used **max** effort: tomford-scratch-002 ("looks
  perfect!"), Prada 8c82, Oakley v2 3dff8674 and INVU v2 c8e81c72 (`trial-result.json` / `job.json`). PLAN §2's
  recommendation of "high" rests on one max run (09-30, $16.83) that got "changes requested". Still the owner's call.
- **The accepted FT1123 asset** is `tomford-scratch-002/final-champagne/model.glb`, sha256 `0e77e11e…cdd6`; per its
  `material-receipt.json` the only post-run change was the lens `baseColorFactor`.
- **The description bug is confirmed**: `studio_jobs.py:224/233` stores the owner's description; the prompt composer
  `:405-426` never includes it, and adds "Treat ambiguous reflected photo streaks as hypotheses" (`:423-424`).
  For imported jobs `description` holds the agent's own summary; for Prada 8c82/ff56, Oakley 3dff8674 and INVU
  c8e81c72 it is empty. The new job schema has a dedicated owner-description field.
- **Owner verdicts depend on viewer settings**, not only on GLB bytes: the Studio's lens-reflection multiplier
  (`lensenv`, e.g. 3.0 on Oakley job 3191f92e), width and temple clip were applied at view time
  (`studio_server.py:331-340`). arview either has no hidden multiplier or records it in every ledger row.
- **Budget arithmetic**: BudgetGuard holds the worst-case cost of every request before sending it; with ~246k context
  and 8,192 output tokens the spendable amount is the cap minus about $3.5. The WP8 "few cents" smoke turn therefore
  needs a small prompt and a low output cap, or its cap refuses the first request.

## 3. Blender Studio file inventory (source-before/blender_agent, 52 files)

Source: `C:/Users/Shay/PycharmProjects/lenses-recovery/automation-cleanup-20261003T071535Z/source-before/blender_agent/` (newest complete Studio copy; committed older copy on `codex/perfecto_automation_agentic`). Every file was read in full by one reviewer and re-checked by a second, adversarial reviewer; corrections are applied and marked. Full per-file evidence (file:line), defects and test lists: `port_review/studio.json`.

Totals: keep 6, rewrite 23, drop 23 (13206 lines reviewed; 4 verdicts corrected by the second reviewer).


### agent-loop

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `bootstrap.py` | 66 | **keep** | glasses3d/blender/bootstrap.py (WP2); the mdl_bridge_underside key and the mm/axes tags feed the WP3 exporter | Small, correct and real-Blender tested. It defines the mm, +Y up, +Z front and bridge-marker conventions that WP3 relies on. |
| `session_inputs.py` | 191 | **keep** | glasses3d/agent/session_inputs.py (WP2), with a view field added and the legacy regex removed | WP2 needs exactly this for resumes: no duplicate images or tokens, no stale brief. |
| `tool_validation.py` | 46 | **keep** | glasses3d/agent/tool_validation.py (WP2) | Generic and small. It gives Astra actionable argument errors while tool-data logging stays off, which every new g3d/host/check tool can use. |
| `__main__.py` | 4 | **rewrite** | glasses3d/__main__.py (WP2): job create/run CLI, plus doctor | A module entry point is needed, but PLAN WP2 specifies `python -m glasses3d job create --images --views --name --description [--specs]` and `job run <id>`, not the agent's doctor/run argparse. |
| `agent.py` | 558 | **rewrite** | glasses3d/agent/loop.py + glasses3d/agent/receipts.py (WP2): SDK Runner on gpt-6-astra with SQLiteSession; ReviewLog transport receipts; stop file; checkpoint_… | Keep the loop skeleton, which is the stock SDK runner and not a custom loop: gpt-6-astra via OpenAIProvider with retries off, serial tools, service_tier default and reasoning summary auto; SQLiteSession resume behind an explicit --resume; … |
| `isolated.py` | 277 | **rewrite** | glasses3d/blender/checkpoint.py (WP2): checkpoint/restore tool + settled(); the reopen-and-re-export fingerprint check moves into export_glb (WP3) | Its only callers are the dropped gate tools: ConstructionTools prototype_construction, EpisodeTools correction batches and SourceFieldTools. |
| `requirements.txt` | 6 | **rewrite** | glasses3d/requirements.txt (WP0/WP2) | glasses3d needs one requirements file merged with the WP0 venv (numpy, scipy, opencv, scikit-image, pillow, PyPatchMatch, pytest) plus openai-agents/openai and the MCP package chosen in WP0. |
| `session.py` | 103 | **rewrite (was keep)** | glasses3d/blender/session.py (WP2): live-Blender launcher, with EcoQoS opt-out, detach and the WP0 MCP add-on choice | Change to rewrite (small; keep the structure). |
| `__init__.py` | 5 | **drop** | - | No code. The new glasses3d package writes its own __init__. |
| `claude_budget.py` | 660 | **drop** | - | It exists only for the dropped Claude adapter. |
| `claude_model.py` | 434 | **drop** | - | Out of scope. PLAN §0.2 says Astra (OpenAI gpt-6-astra) builds the models and Claude builds tools, so a Claude-as-modeler transport is not needed. |

### budget

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `budget.py` | 331 | **keep** | glasses3d/budget.py (BudgetGuard core) + glasses3d/pricing.py (copied from modeler/agentic/pricing.py), tests in glasses3d/tests/test_budget.py; WP2 | This is the real Studio spending gate, and the core works: exact count, then worst-case reserve, then a durable write before the request goes out. |
| `reasoning_evidence.py` | 21 | **keep** | glasses3d/loop/evidence.py (transcript/reasoning summaries in the session driver) + glasses3d/tests/test_reasoning_evidence.py; WP2 | A small, correct pure function with a test. |
| `run_snapshot.py` | 35 | **rewrite (was keep)** | glasses3d/receipts.py (snapshot_implementation, recursive over the glasses3d package) + glasses3d/tests/test_receipts.py; WP2 | The idea (copy the code a run executes, hash it and record versions) is needed, but the reviewer's 'two edits' amount to a rewrite. |
| `trial_budget.py` | 249 | **rewrite** | merged into glasses3d/budget.py as a JobLedger (per-job lock, prior-run re-validation, per-run owner grant, owner reconcile); WP2 | A cap and a ledger spanning several invocations is needed: one job, several runs, each run approved by the owner. |
| `studio_research_budget.py` | 290 | **drop** | - | There is no research stage in glasses3d. Owner rule §0.4: the owner runs research in ChatGPT and pastes the results. |

### studio-app

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `studio_jobs.py` | 874 | **rewrite** | WP2: glasses3d/jobs.py (JobStore: create/run/stop/resume, receipts, restart marker, checkpoint binding, cost summary) plus the glasses3d/__main__.py CLI (`job … | The job model, the paid-run lifecycle and the process-safety code are needed for WP2, and several pieces are solid. |
| `review.py` | 78 | **drop** | - (drop). Replaced by the arview webcam page (?model=, WP1.5) and `python -m glasses3d job review <id> accept\|changes --note` writing a JSONL ledger row bound … | It has no verdict capture, no ledger and no sha binding beyond printing the digest. |
| `studio_fit.py` | 101 | **drop** | - (drop). Fold width and temple-extent measurement into the arview report.json warnings (WP1 step 2) and validate_spec (WP3). | It encodes live-site acceptance (RULES_AUDIT §C), which PLAN §2/WP1 reduces to 'would fail live' warnings in arview, never refusals. |
| `studio_server.py` | 579 | **drop** | - (drop). If the owner wants an accept/changes button on the arview webcam page, copy only guards()/body() (studio_server.py:384-444) into a tiny arview review… | Its distinctive functions are all excluded by the plan. |
| `studio_web/app.js` | 351 | **drop** | - (drop). Web UI not ported: WP2 uses the CLI plus the arview webcam page (WP1.5); the review ledger is written by CLI (WP2). | A CLI is the better fit. (1) PLAN WP2 entry points are CLI commands. |
| `studio_web/app.test.mjs` | 180 | **drop** | - (drop) | It tests a UI that is not being ported, against a mocked server, so it proves nothing about real server behaviour. |
| `studio_web/index.html` | 59 | **drop** | - (drop) | Goes with app.js. The copy also states rules the owner has revoked: generated views as weaker evidence, research by Gemini, fixed maximum effort. |
| `studio_web/style.css` | 50 | **drop** | - (drop) | Goes with the dropped UI. It is minified one-line CSS with a light-only palette and no dark mode, so it has no reuse value for arview or a CLI. |

### instructions

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `instructions.py` | 184 | **rewrite** | WP7: new glasses3d instructions module written fresh. | Most of the text instructs Astra on the audit-flagged bookkeeping tools: correction episodes, construction handoff, select_candidate binding, declare_lens_expectations and source-field annotation. |
| `studio_materials.py` | 292 | **rewrite** | WP3 exporter/validator: read_glb becomes the GLB reader inside validate_spec (warn on external URIs, no size cap, plus BIN/byteLength checks); the colour helpe… | KEEP read_glb as the base of the WP3 validate_spec GLB reader (strict magic/version/length/chunk checks). |
| `studio_description.py` | 418 | **drop** | - (drop). WP2 job create: write a fresh ~10-line dotenv credential loader and accept owner-pasted --specs as authoritative data. | PLAN §0.4 rules out automated web research: the owner researches in ChatGPT and pastes the results as data. |
| `studio_quality.py` | 41 | **drop** | - (drop). WP2 owner review ledger: rows keyed by GLB sha256 (format from owner_verdicts.jsonl); a GLB with no row reads as 'awaiting owner'. | The status vocabulary comes from audit-flagged bookkeeping (declare_lens_expectations features, delivery_evidence checks), and it ranks Astra's self-review as the success state. |
| `studio_sources.py` | 203 | **drop** | - | It exists only to filter studio_description's Gemini output. |
| `tom_ford_brief.txt` | 19 | **drop** | -. Real guards go into the WP7 text (reworded); the export facts (bridge marker, hidden guides, thickness group) go into the WP3 exporter and its tests. | A product-specific trial brief, superseded by WP2 job create (--description/--specs). |
| `tom_ford_scratch_brief.txt` | 19 | **drop** | -. Guards are reworded into WP7; the coordinate and hidden-guide/realize-instances facts become WP3 exporter checks. | A product-specific trial brief, superseded by job create. |

### export-preview

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `inspect_export.py` | 67 | **rewrite** | WP3: merged into glasses3d/spec/validate.py (validate_spec report: materials/extensions table per part), path confinement kept in the WP2 tool layer | WP3 needs this, because validate_spec's report goes to Astra and should show the actual exported material/extension values. |
| `native_export.py` | 271 | **rewrite** | WP3: glasses3d/blender/export_glb.py (Blender-side script generator) + host wrapper calling validate_spec; exposed as the export_glb tool in WP2 | This is the right WP3 reference: native exporter, evaluated copies, baked transforms, partRole extras, scene invariance and cleanup in finally. |
| `native_state.py` | 459 | **rewrite** | WP3: glasses3d/spec/glb_reader.py (decoder only), used by validate_spec; owner ledger uses plain sha256 (digest) only | validate_spec needs a fail-closed GLB decoder, and this one is good: duplicate-key/NaN rejection, bounds, stride/normalized decoding, finite checks. |
| `optical_materials.py` | 213 | **rewrite (was keep)** | WP2 tools: glasses3d/blender/g3d/materials.py as g3d.configure_lens_coating (Blender helper), round-trip proof run with the WP3 exporter | The Blender-side script and the scalar validation can be copied nearly verbatim. |
| `portrait.py` | 141 | **rewrite** | WP1.4: arview capture 'worn' front portrait (glasses3d/arview/qa/portrait-preview.mjs port + host wrapper glasses3d/arview/capture.py); called through arview_c… | PLAN WP1.4 keeps exactly one worn front portrait on face-a via a port of 75ae133:ar/qa/portrait-preview.mjs. |
| `preview.py` | 345 | **rewrite** | WP2: glasses3d/tools/arview_capture.py and export_glb tool wiring, on top of the WP1 'arview capture' CLI; image pinning helper to glasses3d/tools/images.py | WP2 needs the idea: export the live scene, then render that exact GLB in AR and return pinned images even on failure. |
| `optical_quality.py` | 479 | **drop** | - | Bookkeeping that the audit says to remove: a citation gate on expectations, labels such as 'uncertain'/'not_assessed'/'unmeasured' that demote Astra's own judgement, and a newest-file 'deliverable' choice. |
| `part_visibility.py` | 139 | **drop** | - | It is a contract only for the stash's untracked AR runtime: git grep finds the key in neither main:ar/src nor 75ae133:ar/src, only in stash@{0}^3:ar/src/eyewear/part-visibility.ts:5. |

### evidence-geometry

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `evidence.py` | 313 | **rewrite** | WP2 session driver: checkpoint/journal wrapper around the live-Blender execute/export calls (one save per call, reuse the previous post-checkpoint as the next … | The Blender MCP has no undo (memory: blender-mcp-official-server), so a verified checkpoint around every mutating call is valuable for the WP2 session driver. |
| `evidence_json.py` | 208 | **rewrite** | WP2 Astra tool read_report(path, pointer, key_offset): job-dir-bounded, output-capped JSON pointer reader (port lines 129-190 with tests); no kind allowlist, n… | The core idea is useful: a big host report stays on disk and Astra reads it in bounded pages by JSON pointer instead of having it dumped into the tool response. |
| `geometry_camera.py` | 357 | **rewrite** | WP3b glasses3d camera module: merge with the bsa cameras.py port as the single Camera type (physical fields + converter from the silhouette-fit parameters). | This is the cleanest and best-tested camera code in the cluster. |
| `geometry_comparison.py` | 450 | **rewrite** | WP2 host tool compare_to_photos(views='all'): new code that renders native/clay/normals/structure passes in the live Blender (or arview) at the WP3b fitted cam… | compare_to_photos is needed, but this file cannot be its base. |
| `geometry_evidence.py` | 205 | **rewrite (was keep)** | WP3b photo loading (image_identity) and WP2 compare_to_photos presentation (panel_sheet, adapted build_panels/write_viewer); surface_distance into a glasses3d … | 'Keep' means copying nearly as is, and that does not fit this file. |
| `observations.py` | 231 | **rewrite** | WP2 Astra tool read_image (keep inspect_image/ImageRegion/evidence_path; move image_url into a glasses3d util; fix the EXIF inconsistency). | read_image is an essential 'seeing' tool for Astra (zoom on a photo detail or render crop at native resolution) and is clean: keep inspect_image, ImageRegion and evidence_path nearly verbatim. |
| `delivery_evidence.py` | 70 | **drop** | - | This is a bookkeeping gate. It never measures the model; it checks that the other Studio artifacts exist and are hash-current, and studio_quality turns 'incomplete' into a non-'agent_reviewed' status. |

### gates

| File | Lines | Verdict | glasses3d target | Why |
|---|---|---|---|---|
| `seed_prepare.py` | 291 | **keep** | glasses3d/blender/seed_prepare.py (WP2 tooling; the WP4 regression proof uses it to import the Oakley/INVU final GLBs; WP3 uses its normal-bake code as referen… | Self-contained and gate-free. Its test checks it against an independent glTF-space oracle covering nonuniform parent scale, mirrored nodes, custom normals, UVs, transmission/IOR/volume and an embedded PNG. |
| `source_tools.py` | 411 | **rewrite** | WP2 glasses3d session package: Blender transport (call_code/script_output) plus a checkpoint/transaction module (packed save/restore, pending marker, recover, … | Part (b) is the host side of the pattern tool, citation gates included, and goes with source_field.py (drop). |
| `construction.py` | 382 | **drop** | - (idea only: WP2 check_topology / WP5 UV check re-derive _PROBE's edge and UV counts fresh) | This is the audit's 'construction labels' gate. |
| `episodes.py` | 411 | **drop** | - (checkpoint/activate/recover wrappers re-created uncapped in the WP2 checkpoint module; see source_tools.py) | This is the audit's 'correction-family caps (4+4)'. |
| `selection.py` | 578 | **drop** | - (WP2 writes deliverable/model.glb + report.json and an owner ledger keyed by GLB sha256 fresh) | This is the audit's 'selection citation gates'. |
| `source_field.py` | 461 | **drop** | - | This is the audit §A5 two-colour ramp. Only the two palette endpoints ever reach the texture, so every tortoiseshell is invented (PLAN §1 failure 2). |

### Defects found in kept or rewritten files (must not be inherited)

- **agent.py**
  - agent.py:260 and :330 import modeler.agentic.pricing (automation/modeler) at runtime; budget.py:25 and trial_budget.py:17 do the same.
  - agent.py:528-529 the default --mcp-command is hard-coded to <automation>/data/blender_agent/venv/Scripts/mcp-for-blender.exe
  - agent.py:349 + requirements.txt:3 the server is ahujasid's mcp-for-blender 2.1.3 (METADATA: 'not affiliated with Blender Foundation'), not the official Blender Lab server.
  - agent.py:351 BLENDER_MCP_SAFE_MODE=1 is enforced inside the MCP server process (venv blender_mcp/server.py:717-724).
  - agent.py:540 defaults --reasoning-effort to max and agent.py:543 defaults --max-output-tokens to 8192.
  - agent.py:42-43 export_scene is exposed to Astra, a GLB route that bypasses the native exporter and any validate_spec
- **session.py**
  - session.py:78-81 plain subprocess.Popen: no EcoQoS opt-out (it exists only in modeler/winqos.py) and no detach; memory: detached runs are 2.2x slower and preview servers kill children
  - session.py:87-98 readiness timeout fixed at 30 s (60 x 0.5 s)
  - session.py:21 default port 9876 is the official Lab add-on's port (incompatible frames, memory blender-mcp-official-server)
  - session.py:54 hard-wired to the ahujasid package's bundled add-on
  - session.py:22-23 Blender 5.2 path hard-coded as the default (correct for this machine; make it configurable)
- **session_inputs.py**
  - session_inputs.py:22-30 + 91-92 _LEGACY_BRIEF regex hard-codes the old host suffix text ('This invocation allows up to N model responses...'): legacy-only, cut
  - session_inputs.py:35-42 ReferenceInput has only label/path/image_url/detail and no view; the labels came from filenames (agent.py:446)
  - session_inputs.py:20 'Blender product brief (verbatim)': correct only if the composer includes the description, which the old one never did (studio_jobs.py:405-426)
- **bootstrap.py**
  - bootstrap.py:15-23 seed mode verifies nothing: units, axes and the bridge marker are not checked; test_blender_agent_session.py:88-89 accepts a seed with scale_length 1.0, and the receipt bridge_origin may be None (bootstrap.py:39)
  - bootstrap.py:26 + 61 port stored twice: an ID property before register, the RNA property after
  - bootstrap.py:21 the origin marker key is 'mdl_bridge_underside'; WP3's exporter must read this key and refuse when it is absent
- **isolated.py**
  - isolated.py:130 + 135-136 imports blender_mcp.safe_mode (third-party; numpy banned) at host runtime to validate artist code
  - isolated.py:117 + 173 string-patches generated code with .replace('copy=True','copy=False')
  - isolated.py:258-277 _EMPTY removes every object, every other scene and every collection from the live file
  - isolated.py:86-87 after a restore, the incumbent recovery copy becomes Blender's active file, so a later plain save_mainfile overwrites that checkpoint
  - isolated.py:156-191 one edit = about 7 MCP calls, including 3 packed .blend saves and 2 GLB exports
  - isolated.py:101-102 + 150 + 221-223 + 233-255 coupled to the evidence registry and the candidate/selection gate ('quality_certified': False)
- **tool_validation.py**
  - tool_validation.py:26-27 depends on the SDK's error text prefix 'Invalid JSON input for tool' (openai-agents pinned 0.22.3 at requirements.txt:1); a version bump silently disables the diagnostics
  - tool_validation.py:8 imports agents.function_schema internals
  - tool_validation.py:21 builds a second schema (strict_json_schema=True) that must stay identical to function_tool's
- **requirements.txt**
  - requirements.txt:6 pulls in the automation-level requirements file
  - Pillow, used at agent.py:36, arrives only through the parent file
  - httpx2, imported at test_blender_agent_transport.py:13, is not listed
  - requirements.txt:3 the pin follows the server choice that WP0 has not made yet
- **budget.py**
  - blender_agent/budget.py:25 runtime import of modeler.agentic.pricing; this violates the 'never import from automation/' rule, so pricing.py must be copied in
  - blender_agent/budget.py:178-221 no way to settle a 4xx rejection (e.g.
  - blender_agent/budget.py:317-323,199-200 ledger rows carry no request_id/response_id, so the 'receipt' cannot be matched to the transport files or the provider dashboard
  - blender_agent/budget.py:165-170 prose instructions to Astra live inside the accounting module and are injected into every observation via agent.py:73-92; a 'finalize_now' status can push Astra to stop early.
  - blender_agent/budget.py:279-280 any new SDK request field aborts the paid run ('unreviewed fields').
  - blender_agent/budget.py:271-272 only gpt-6-astra is accepted, even though pricing.py:38-44 also prices gpt-6-luna; acceptable, but must be a deliberate choice
- **trial_budget.py**
  - blender_agent/trial_budget.py:92-96 + studio_jobs.py:461-465 frozen cap: no owner top-up for a change round on the same session; the new pipeline needs per-run owner grants
  - blender_agent/trial_budget.py:135 os.replace without the Windows sharing-denial retry that budget.py:236-244 has (Defender denies ~1% of renames on this machine)
  - blender_agent/trial_budget.py:85-90,98-101 error messages demand a 'legacy-budget reconciliation' that no code implements (dead end)
  - blender_agent/trial_budget.py:100 coupled to the Agents SDK SQLiteSession file name 'session.sqlite' (agent.py:416)
  - blender_agent/trial_budget.py:198-199 unknown holds are permanent; with no reconcile path, a non-billable 429 hold (e.g.
  - blender_agent/trial_budget.py:170 v1 ledgers (no settlement, 'conservative_reservations_never_refunded') are still accepted; legacy-only
- **run_snapshot.py**
  - blender_agent/run_snapshot.py:19 non-recursive, package-local glob misses code the run actually executes in other packages (modeler/agentic/pricing.py imported by budget.py:25) and non-.py inputs (studio_web/, *.txt briefs), so the receipt is incomplete
  - blender_agent/run_snapshot.py:26 hard-coded package list; no Blender version, add-on version or git commit recorded
- **reasoning_evidence.py**
  - blender_agent/reasoning_evidence.py:16 only 'function_call' items are listed as actions; fine while only local function tools are allowed (budget.py:281-285), but any hosted/MCP call type would be silently omitted
- **studio_jobs.py**
  - CONFIRMED and test-enforced: the owner description is never sent to Astra (studio_jobs.py:224/:233 store, :405-426 prompt omits it).
  - Output token cap fixed at 8192 per turn alongside max effort and 120 turns (studio_jobs.py:545); agent.py:322-327 already handles terminal 'incomplete' responses, so the cap is reachable
  - Effort is always 'max': stored at :236 but unused, hard-coded at :545; test_blender_studio_jobs.py:117 pins it
  - Owner rule §0.3 violated: provenance strings reach Astra (:406-407); imports demote views (:769-770, :857-858).
  - Selection gate (audit-flagged): finish withholds the model without an explicit selection (:651-656)
  - Stuck state: if the server dies during a first-run Blender launch (status 'starting', no execution.json yet), get() only sets the :347 message and keeps the status; check_start (:447) then blocks forever and no API clears it
- **instructions.py**
  - instructions.py:46, :49-50, :52-54, :95 audit-flagged rules (hypotheses, bands-are-reflections, keep-simple, generated-view demotion)
  - instructions.py:41 'Work autonomously within the supplied brief': the brief (studio_jobs.py:405-426 _prompt) never includes job['description'], which is stored at studio_jobs.py:224/233, so the owner's job text never reached Astra
  - instructions.py:57-83, :101-103, :161-177 bookkeeping gates (correction caps, handoff, select_candidate, declare_lens_expectations, review binding)
  - instructions.py:32-36 one-way tool contract: a tool named in the text but never registered passes preflight
  - instructions.py:27 hard-codes ahujasid community-MCP tool names; this blocks switching to the official Blender MCP server
  - instructions.py:178-183 safe-mode text forbids numpy; WP2 allows numpy without file I/O
- **studio_materials.py**
  - studio_materials.py:136-137 _roles indexes meshes[node['mesh']] without a bounds check and assumes extras is a dict, so a malformed GLB raises IndexError/AttributeError (HTTP 500) instead of ValueError
  - studio_materials.py:119-126 the chunk walk never checks that chunk 2 is BIN or that buffers[0].byteLength fits; it is not a full validator
  - studio_materials.py:204 has_textures looks only at pbrMetallicRoughness keys and misses normal, occlusion, emissive and extension textures
  - studio_materials.py:269 writes iridescenceThicknessMinimum even when the source lacks it (adds a field)
  - studio_materials.py:116-118 the hard refusal conflicts with the §2 'prefer self-contained' wording
- **native_export.py**
  - native_export.py:245 use_mesh_edges=True and use_mesh_vertices=True.
  - native_export.py:219-221 partRole is written only if role_of finds one.
  - native_export.py:228-234 material copies are keyed by material.name only, so one material used by a lens and a frame part stays shared.
  - native_export.py:29-32 volume Thickness is a unit trap: in an mm scene Astra must hand-write metres, and nothing checks it.
  - native_export.py:62-66 'error if origin missing' never fires for Studio scenes: bootstrap.py:21 and seed_prepare.py:249 always preset [0,0,0].
  - native_export.py:175-191 LENSES_part_visibility all-or-none gate: if one object is tagged and another is not, the whole export fails (native_export.py:181-189).
- **native_state.py**
  - native_state.py:266-285 _transform validates and hashes node TRS/matrix but never requires identity, so as-is it cannot enforce the §2 'effective transform = identity' rule
  - native_state.py:191-192, 197-199, 223-225 reject any hierarchy, root extensions and Draco/meshopt declarations (test at tests/test_blender_agent_native_state.py:312).
  - native_state.py:239 requires indices on every primitive and :244-245 requires NORMAL.
  - native_state.py:435-452 base_texture exists only for source_field's two-colour ramp (RULES_AUDIT §A5: no photo pixels).
  - native_state.py:67-71 size is checked twice (stat, then after read): redundant
  - No check of units/orientation/origin (expected: it is an identity tool, not a spec check)
- **preview.py**
  - preview.py:110 caps views at 6, but the WP1 'all' pose set (front, yaw ±30/±45, pitch ±20, 4 junction crops) is 11
  - preview.py:292-296 no top view and no lens–brow junction close-ups (RULES_AUDIT §B: 'Nothing ever looks at the lens–brow junction')
  - preview.py:43-49 continuity and synthetic-fit outcomes are hard failures.
  - preview.py:22 runtime import of bsa.archeck (automation/), which runs qa.provider_comparison against the live ar/ tree (bsa/archeck.py:265-266).
  - preview.py:228, 288 and optical_quality.py:375 import each other lazily (circular coupling); optical expectations drive which views render (preview.py:288-291)
  - preview.py:183-185 capture_existing trusts any export-receipt.json lying next to the source GLB
- **portrait.py**
  - portrait.py:100 qa/portrait-preview.mjs does not exist on main (git: main lacks it; only 75ae133 has it, stash@{0} has portrait-preview.html), so this module is broken against the current ar/ tree
  - portrait.py:88 clamps the stated width to 60–250 mm.
  - portrait.py:58-69 'No optical meshes', 'fitting did not settle' and continuity failures are hard failures.
  - portrait.py:13 hard-coded path into the live ar/ tree.
  - portrait.py:10-11 runtime import from automation/bsa (forbidden by PLAN §2)
- **inspect_export.py**
  - inspect_export.py:12, 39 calls part_visibility.inspect_part_visibility, which imports reconstruction.mesh at runtime (part_visibility.py:35): an automation/ dependency
  - inspect_export.py:17-22 does not validate the BIN chunk, alignment or duplicate keys; bounds at :33 are the writer's accessor min/max, never decoded.
  - inspect_export.py:10 coupled to the OpenAI agents SDK function_tool
- **optical_materials.py**
  - optical_materials.py:13, 45-75 coupled to the OpenAI agents SDK function_tool; re-wire it to the new tool registry
  - optical_materials.py:90 accepts only '' or 'Code executed successfully:' as the receipt prefix, which is the community MCP's wrapper format.
  - optical_materials.py:125-126 handles only a direct Principled surface; mixed glass+mirror shader graphs are refused (intentional, but Astra must know)
- **geometry_camera.py**
  - blender_agent/geometry_camera.py:43 Euler pitch is capped at +/-90, and at 90 yaw and roll are interchangeable: test_blender_agent_geometry_camera.py:192-199 confirms a pose fit at a top view is refused (rank<6).
  - blender_agent/geometry_camera.py:51-52 the 1600 px cap forces geometry_comparison to downsample photos (geometry_comparison.py:247), which throws away registration resolution needed for mm-level checks.
  - blender_agent/geometry_camera.py:90-101 duplicates the reconstruction/camera.py:34-44 basis with a different parameterization, so porting both would give two camera types.
  - blender_agent/geometry_camera.py:79 the pose fit is a +/-15 deg local refine: it cannot recover a view from a poor prior and depends on someone (Astra) typing a starting camera.
- **geometry_comparison.py**
  - blender_agent/geometry_comparison.py:375 no quantitative comparison at all: no IoU/contour/mm, so Astra's 'it matches' was never measured.
  - blender_agent/geometry_comparison.py:280-285 no camera is fitted to the photo; a front ortho guess is the default and Astra must invent yaw/pitch for top/rear, which it never did (instructions.py:85-86).
  - blender_agent/geometry_comparison.py:18-19 runtime imports from automation/bsa and automation/reconstruction (PLAN §2 forbids).
  - blender_agent/geometry_comparison.py:25,177-179 depends on ar/qa/geometry-comparison.mjs/.html/-core.mjs, which exist only in stash@{0}^3 (8 files); the tool cannot run on main.
  - blender_agent/geometry_comparison.py:124-147,251-276 frozen-series and implementation-hash refusals block re-comparing after any code change: an audit-style bookkeeping gate.
  - blender_agent/geometry_comparison.py:225,291-292,405 the source_kind 'generated' demotion warning conflicts with 'every owner image is a real photo'.
- **geometry_evidence.py**
  - blender_agent/geometry_evidence.py:20-24 the file is read twice for the size check (stat, then len(raw)): harmless redundancy.
  - blender_agent/geometry_evidence.py:43-78 surface_distance loops over points in Python with full triangle arrays per point: fine for <=24 landmarks, too slow for dense lens-edge sampling (use a BVH/KD-tree for check_lens_seating).
  - blender_agent/geometry_evidence.py:116-117 build_panels assumes exactly the four stash-renderer passes; write_viewer (186-189) hard-codes the same file names.
  - blender_agent/geometry_evidence.py:192 write_viewer embeds every image as base64, so viewer.html grows by about 1.33x the size of all passes, with no size cap.
- **observations.py**
  - blender_agent/observations.py:55-56 with region=None the RAW file (not EXIF-transposed) is sent, while metadata reports the oriented size (50) and region crops are in oriented space (57-65): EXIF-rotated phone photos are shown unrotated full-frame but rotated when cropped.
  - blender_agent/observations.py:33 Path(path).resolve uses the process CWD for relative paths, not the output directory.
  - blender_agent/observations.py:16 hard coupling: an image viewer imports the optical-quality lens-review module at import time.
  - blender_agent/observations.py:41 and geometry_comparison.py:431 circular imports of agent.image_url.
  - blender_agent/observations.py:56 the full original (up to 20 MiB) is base64-inlined as a data URL with no downscale.
- **evidence.py**
  - blender_agent/evidence.py:109,132 two full .blend saves per protected call; the 'after' of call N and the 'before' of call N+1 are normally the same scene: a redundant save on every call.
  - blender_agent/evidence.py:303 compress=True on every checkpoint slows each save, and there is no pruning: one checkpoint pair per call accumulates on disk (the recovery move was 66.7 GB).
  - blender_agent/evidence.py:259,286-287 objects/materials/groups past 128 are silently missing from before/after, so delta.json cannot report changes to them.
  - blender_agent/evidence.py:117-119 error status depends on exact upstream MCP message prefixes.
  - blender_agent/evidence.py:78-80 a snapshot timeout (120 s) leaves the probe possibly still running inside Blender while the journal continues.
  - blender_agent/evidence.py:20,150 tied to the openai-agents SDK MCPServerStdio class.
- **evidence_json.py**
  - blender_agent/evidence_json.py:106 stale error text 'Only preview, review, inspection and comparison' although 9 kinds exist.
  - blender_agent/evidence_json.py:114-126 each instance rewrites the index from its own cached entries with no lock or reload, so two registry instances in one run (preview.py:104 and observations.py:207 fall back to new instances) can lose each other's registrations.
  - blender_agent/evidence_json.py:124-125 temporary.replace(index) has no retry: Windows Defender denies about 1% of renames on this machine (memory modeling-auto-windows-rename-denial).
  - blender_agent/evidence_json.py:112-113 a 4096-entry cap can abort a long run mid-session ('entry limit reached').
  - blender_agent/evidence_json.py:155-160 an oversized read returns data=None, so callers re-read the raw file outside the hash check (geometry_comparison.py:93-94).
- **source_tools.py**
  - source_tools.py:16 ImageOps imported, unused
  - source_tools.py:34 the envelope prefix is specific to one MCP server; must match the WP0 choice
  - source_tools.py:69 pack_all() packs images into the LIVE session as a side effect of taking a copy
  - source_tools.py:118,225,235 calls IsolatedScene._call (private) across classes
  - source_tools.py:123,237 a full native export on every inspect/prepare/apply
  - source_tools.py:147-150,205-211 citation gates (supplied-original references, interpretation length)
- **seed_prepare.py**
  - seed_prepare.py:283 hard-coded default Blender path (make it config)
  - seed_prepare.py:19,80-81 depends on native_export.export_code (to be replaced by the WP3 exporter)
  - seed_prepare.py:116-120 raises RuntimeError inside finally, which would mask an original exception
  - seed_prepare.py:242-245 deletes every non-mesh object, so extras stored on parent empties (e.g.

### Tests (source-before/tests): reconciled verdicts

Tests for dropped modules are dropped. Contradictory verdicts across entries were reconciled explicitly (reason column).

| Test | Final | Per-entry verdicts | Reason |
|---|---|---|---|
| `app.test.mjs` | **drop** | studio_web/app.js:drop, studio_web/index.html:drop, studio_web/style.css:drop | consistent across entries |
| `test_blender_agent_budget.py` | **keep** | budget.py:keep | consistent across entries |
| `test_blender_agent_claude_budget.py` | **drop** | claude_budget.py:drop, budget.py:drop | consistent across entries |
| `test_blender_agent_claude_model.py` | **drop** | agent.py:drop, claude_model.py:drop, claude_budget.py:drop, session_inputs.py:drop, budget.py:drop, trial_budget.py:drop | consistent across entries |
| `test_blender_agent_claude_wiring.py` | **drop** | agent.py:drop, claude_model.py:drop, trial_budget.py:drop, construction.py:rewrite | the Claude modeler branch is dropped |
| `test_blender_agent_claude_workflow.py` | **drop** | agent.py:drop, claude_model.py:drop, claude_budget.py:drop, trial_budget.py:drop | consistent across entries |
| `test_blender_agent_closeout.py` | **drop** | episodes.py:rewrite | closeout second run is dropped; the restore-on-failure idea is covered by the new checkpoint tests |
| `test_blender_agent_construction.py` | **drop** | isolated.py:drop, native_state.py:drop, preview.py:drop, evidence_json.py:drop, construction.py:drop | consistent across entries |
| `test_blender_agent_delivery_evidence.py` | **drop** | part_visibility.py:drop, geometry_comparison.py:drop, geometry_evidence.py:drop, delivery_evidence.py:drop | consistent across entries |
| `test_blender_agent_episodes.py` | **drop** | native_state.py:drop, preview.py:drop, evidence_json.py:drop, episodes.py:drop | consistent across entries |
| `test_blender_agent_evidence.py` | **rewrite** | evidence.py:rewrite | consistent across entries |
| `test_blender_agent_evidence_json.py` | **rewrite** | evidence_json.py:rewrite | consistent across entries |
| `test_blender_agent_geometry_adversarial.py` | **drop** | geometry_camera.py:drop, geometry_comparison.py:drop | consistent across entries |
| `test_blender_agent_geometry_camera.py` | **rewrite** | geometry_camera.py:rewrite | consistent across entries |
| `test_blender_agent_geometry_comparison.py` | **rewrite** | geometry_camera.py:rewrite, geometry_comparison.py:rewrite, observations.py:rewrite | consistent across entries |
| `test_blender_agent_geometry_versions.py` | **drop** | geometry_comparison.py:drop, geometry_evidence.py:drop | consistent across entries |
| `test_blender_agent_inspect_export.py` | **rewrite** | inspect_export.py:rewrite | consistent across entries |
| `test_blender_agent_instructions.py` | **rewrite** | instructions.py:rewrite | consistent across entries |
| `test_blender_agent_isolated.py` | **rewrite** | native_state.py:drop, preview.py:drop, source_tools.py:rewrite | becomes the checkpoint/restore test |
| `test_blender_agent_lifecycle.py` | **rewrite** | budget.py:rewrite | ported with budget.py/receipts; adapt to the new loop |
| `test_blender_agent_mcp_isolation.py` | **rewrite** | bootstrap.py:rewrite, isolated.py:rewrite, native_state.py:drop, preview.py:drop, evidence_json.py:rewrite, source_tools.py:rewrite | becomes the worker lifecycle test (owned-PID teardown, ephemeral port, secret scrub, reconnect after reopen) |
| `test_blender_agent_native_export.py` | **rewrite** | native_export.py:rewrite, inspect_export.py:rewrite | consistent across entries |
| `test_blender_agent_native_state.py` | **rewrite** | native_state.py:rewrite | consistent across entries |
| `test_blender_agent_observations.py` | **rewrite** | observations.py:rewrite | consistent across entries |
| `test_blender_agent_optical_materials.py` | **rewrite** | native_export.py:keep, inspect_export.py:keep, optical_materials.py:keep | a covered module ended as rewrite (second-reviewer correction), so its test is rewritten too |
| `test_blender_agent_optical_quality.py` | **drop** | optical_quality.py:drop, observations.py:drop | consistent across entries |
| `test_blender_agent_part_visibility.py` | **drop** | part_visibility.py:drop | consistent across entries |
| `test_blender_agent_portrait.py` | **rewrite** | portrait.py:rewrite | consistent across entries |
| `test_blender_agent_preview.py` | **rewrite** | native_export.py:rewrite, preview.py:rewrite | consistent across entries |
| `test_blender_agent_quality_workflow.py` | **rewrite** | isolated.py:rewrite, studio_jobs.py:rewrite, instructions.py:rewrite, native_state.py:drop, preview.py:drop, observations.py:rewrite, evidence_json.py:rewrite,  | becomes the WP2 scripted-replay proof (fake model client, real budget/receipt path) |
| `test_blender_agent_reasoning_evidence.py` | **keep** | reasoning_evidence.py:keep | consistent across entries |
| `test_blender_agent_run_snapshot.py` | **rewrite** | run_snapshot.py:keep | a covered module ended as rewrite (second-reviewer correction), so its test is rewritten too |
| `test_blender_agent_run_wiring.py` | **rewrite** | trial_budget.py:rewrite, run_snapshot.py:rewrite, geometry_comparison.py:rewrite, observations.py:rewrite, evidence_json.py:rewrite, construction.py:rewrite, se | consistent across entries |
| `test_blender_agent_scratch.py` | **rewrite** | native_export.py:keep | a covered module ended as rewrite (second-reviewer correction), so its test is rewritten too |
| `test_blender_agent_seed_prepare.py` | **rewrite** | native_export.py:drop, part_visibility.py:drop, seed_prepare.py:keep | a covered module ended as rewrite (second-reviewer correction), so its test is rewritten too |
| `test_blender_agent_selection.py` | **drop** | native_state.py:drop, optical_quality.py:drop, evidence_json.py:drop, delivery_evidence.py:drop, selection.py:drop | consistent across entries |
| `test_blender_agent_source_field.py` | **drop** | source_field.py:drop | consistent across entries |
| `test_blender_agent_source_tools.py` | **drop** | native_state.py:drop, preview.py:drop, inspect_export.py:drop, part_visibility.py:drop, evidence_json.py:drop, source_field.py:rewrite, source_tools.py:rewrite | pattern-tool host side is dropped; the call_code transport case moves into the worker tests |
| `test_blender_agent_source_transactions.py` | **rewrite** | isolated.py:drop, native_state.py:drop, evidence_json.py:drop, source_field.py:rewrite, source_tools.py:rewrite | its transaction scenarios (blocked before execution, partial edit, cancellation) test the new checkpoint module |
| `test_blender_agent_tool_validation.py` | **rewrite** | construction.py:rewrite, episodes.py:rewrite | consistent across entries |
| `test_blender_agent_transport.py` | **rewrite** | budget.py:rewrite, reasoning_evidence.py:rewrite | budget/receipt transport test (not MCP); ported with budget.py |
| `test_blender_agent_trial_budget.py` | **rewrite** | trial_budget.py:rewrite | consistent across entries |
| `test_blender_studio_candidate_review.py` | **drop** | studio_jobs.py:drop, studio_server.py:drop, studio_description.py:drop, native_state.py:drop, evidence_json.py:drop, selection.py:drop | consistent across entries |
| `test_blender_studio_description.py` | **drop** | studio_research_budget.py:drop, studio_description.py:drop, studio_sources.py:drop | consistent across entries |
| `test_blender_studio_fit.py` | **drop** | studio_server.py:drop, studio_fit.py:drop, studio_materials.py:drop | consistent across entries |
| `test_blender_studio_jobs.py` | **rewrite** | studio_jobs.py:rewrite | consistent across entries |
| `test_blender_studio_materials.py` | **rewrite** | studio_materials.py:rewrite | consistent across entries |
| `test_blender_studio_quality.py` | **drop** | studio_quality.py:drop, delivery_evidence.py:drop | consistent across entries |
| `test_blender_studio_research_budget.py` | **drop** | studio_research_budget.py:drop | consistent across entries |
| `test_blender_studio_selection.py` | **drop** | studio_jobs.py:drop, selection.py:drop | consistent across entries |
| `test_blender_studio_server.py` | **drop** | studio_server.py:drop, studio_description.py:drop, studio_materials.py:drop, studio_quality.py:drop | consistent across entries |
| `test_blender_studio_sources.py` | **drop** | studio_sources.py:drop | consistent across entries |
| `test_blender_studio_stop.py` | **rewrite** | studio_jobs.py:rewrite | consistent across entries |

Shared fixtures to carry with the rewritten tests: `tests/_blender_quality_fixture.py` (rewrite: no hard-coded Blender path, no community safe_mode, no part_visibility tags), `PersistentOfflineBlender`, `ROUGH_PRODUCT` and `handoff` from `test_blender_agent_construction.py:22-70, 330-374` (move into a fixture module). `test_harness_preview.py` and `test_preview_engine_labels.py` test automation/modeler, not the Studio: drop (note kept: CPU Cycles 33.2 s vs EEVEE-on-llvmpipe 190.3 s for 13 renders).


## 4. Studio docs

| Doc | Verdict | Why |
|---|---|---|
| `README.md` | **rewrite** | Do not copy this doc. About half of it describes the bookkeeping machinery that PLAN drops: construction-handoff labels, correction batches capped at 4 with switch/final-batch rules, closeout recovery, select_candidate evidence bundles that fail closed, decla… |
| `STUDIO.md` | **drop** | Do not copy. It documents the web UI, whose main features are a Gemini research stage that gates the build (STUDIO.md:49-52, 67-92), a select_candidate delivery gate (156-163) and the 'generated views are weaker evidence' labelling (43-44). |
| `RESEARCH.md` | **drop** | Do not copy. Most of it is web, social and GitHub research by Claude researchers (RESEARCH.md:28-58, 205-267), which PLAN §0.4 forbids and which is unverified. |
| `RESULTS.md` | **rewrite** | Do not copy as a doc: its lessons are phrased as prompt and instruction changes and as observation labels, which PLAN §0.6 and the audit reject. |
| `GEOMETRY_COMPARISON.md` | **drop** | Do not copy. Its core contract is landmark-only camera fitting with no masks or segmentation (GEOMETRY_COMPARISON.md:32-34, 43-52), plus 'never turn a residual into an acceptance decision' (73-76). |

Facts carried forward (each verified by the docs reviewer against code or job files; claims PLAN contradicts are listed after):


**README.md**

- MCP server actually used by the Studio: ahujasid community `mcp-for-blender` (README.md:22-24; pinned 2.1.3 commit 60d2a31b per RESEARCH.md:32), loaded at session.py:54 and agent.py:529. Its safe mode is `blender_mcp.safe_mode.validate_code` (isolated.py:130, construction.py:124, episodes.py:199/305, source_tools.py:40), the validator that bans numpy (RULES_AUDIT §B). Memory blender-mcp-official-server.md describes Claude's OWN Blender Lab MCP, which uses TCP 9876 with a frame format incompatible with the community server. The Studio ran its Blender on --port 9877 (README.md:221-222, 416). Whichever server WP0.5 keeps, glasses3d must use a non-default port and its own numpy-allowing, no-file-I/O validator.
- Python variables do not persist between MCP calls; scene state does (README.md:89-91). Only one client per Blender session (README.md:228-230).
- Origin marker key is the scene custom property `mdl_bridge_underside` (README.md:371-373). native_export.py:63-66 raises if it is missing; bootstrap.py:21, seed_prepare.py:249 and isolated.py:275 set it to [0,0,0]. --empty sessions factory-reset to numeric millimetres, +Y up / +Z front, bridge origin (0,0,0), zero objects/cameras/lights (README.md:216-226). This answers PLAN WP3 'find the key in native_export.py'.
- Export method for WP3: export temporary evaluated copies with baked world transforms, scale and bridge origin; check the live scene for invariance and never rebuild it (README.md:346-350). hide_render excludes an object from export, viewport isolation does not; realize collection/GN instances first (README.md:374-376). partRole values are frame/temple/lens (README.md:374-375).
- Unit conventions for WP3: vertices are baked to metres but shader lengths are untouched. glTF Thickness is in metres (1.5 mm lens = 0.0015); attenuationDistance is in metres; Volume Absorption Density exports as its reciprocal (density 50 gives 0.02 m); thin-film thickness is in nm; a thickness texture's green channel multiplies the metre factor (README.md:352-363).
- Blender 5.2 exporter trap: a custom 'glTF Material Output' group with only a Thickness input disappears during shader inlining. Use the standard group with Occlusion + Thickness inputs and Group Input/Output, then verify KHR_materials_volume in the file (README.md:365-369; it happened in the real trial, RESULTS.md:267-271). Thin-film needs the same Occlusion marker; uniform film thickness gives an angular colour shift, never a spatial gradient (README.md:130-135).
- Budget mechanism to port in WP2 (README.md:240-261): count exact input tokens before each request; durably reserve the worst-case cost at max output; settle from provider usage; failed/unknown outcomes keep their full hold. Retries are disabled AND enforced: budget.py:263-264 rejects x-stainless-retry-count != 0; agent.py:352, 386, 392 set max_retries=0. The trial-wide cap is frozen in trial-budget.json across resumes, with a process lock per output dir. Launcher/doctor/help make no API call; OpenAI tracing and MCP telemetry are off (README.md:260-261).
- Evidence to port: archive every API request/response with image bytes stored by hash and auth headers stripped (README.md:270-275). Take an automatic compressed before/after .blend for every arbitrary script; a failed pre-save blocks the edit (README.md:277-282). This is required because neither MCP pushes undo. Save stop-checkpoint.blend on completion or budget stop (README.md:289-292). Record reasoning.summary='auto' to reasoning-summaries.jsonl (README.md:161-166).
- Implementation snapshot per invocation: source hashes plus dependency versions under implementation/ (README.md:156-159). Keep it. The code behind the modeler's legacy accepts was lost because nothing snapshotted it (memory lenses-3d-attempts-review-2026-10-04).
- Resume must compare inputs against the items persisted in the SQLite session and must not resend an unchanged brief or images (README.md:294-300). Without this, scratch-002 duplicated about 13,818 tokens on retry (RESULTS.md:369-370).
- Session launcher: a dedicated Blender GUI process with the addon registered only in that process. Screenshots need a GUI/GPU context even when the window is hidden (README.md:177-185).
- preview_ar design for arview_capture: natively export the live scene, run that exact file through the TryOnRenderer QA harness, keep full diagnostics on disk, and return a compact summary plus images (README.md:304-321). The compact summary MUST carry continuity/fit failures (README.md:311-313). The real run omitted them (RESULTS.md:367-369).
- preview_portrait on face-a uses the actual detector, fitter, hair mask, shadows and renderer, and returns a clean eye crop, the AR eye detail and a second eye detail under fixed illumination (README.md:323-335). Use it for the PLAN WP1.4 worn portrait.
- read_image: a normalized crop of a saved render or original reference at native pixels (README.md:92-93). A cheap tool worth rebuilding.
- If the exporter writes LENSES_part_visibility, the schema is {version:1, region: front\|temple_left\|temple_right\|lens}, and temple_left owns the arm at native NEGATIVE X (README.md:378-384).
- Provider-GLB import math, needed only if a provider mesh becomes a hidden measuring reference (RULES_AUDIT §B): undo the importer's Y-up to Z-up conversion, then apply scale_mm*Rxyz*(p-anchor). Custom normals are transformed; shader lengths are not rescaled (README.md:187-209).
- Documented run settings: gpt-6-astra; default effort max; default output ceiling 8192; 25000 for the first max trial because reasoning shares the ceiling (README.md:263-268). From-zero Tom Ford flags: --max-usd 17 --max-turns 120 --max-output-tokens 8192 --mcp-port 9877 (README.md:416).
- The Claude backend (README.md:3-15) ran 2026-10-03 as tomford-claude-001. Cost $5.583114 ($3.046227 of it failed setup), max effort, 32768 then 128000 output tokens. Outcome 'failed_visual_fidelity_user_rejected'; the blind review preferred the prior Astra model (moved/data/blender_agent/tomford-claude-001/RESULTS.json). This supports PLAN §0.2.

*Contradicted by PLAN (PLAN wins):*
- README.md:3-15 offers Claude Opus as an alternative builder. PLAN §0.2: Astra (gpt-6-astra) builds the models.
- README.md:17-20 runs automatic Gemini web research before modeling. PLAN WP2 job create takes the owner's --description/--specs, with no research stage; any extra paid provider call needs owner OK (§0.7).
- README.md:34-71 describes source-handoff claim labels ('Assumptions remain uncertain'), correction batches of at most four alternatives ('two ineffective batches permit one justified representation switch and one final batch'), closeout recovery, and a select_candidate bundle that 'fails closed'. PLAN §2/WP0.5 drops construction labels, selection citation gates and correction-family caps.
- README.md:45-52: the source-field helper 'does not bake photographed highlights, infer unseen detail' and paints a two-colour ramp (RULES_AUDIT §A5). PLAN WP5 replaces it with photo projection plus mirror and PyPatchMatch fill.
- README.md:117-121: instructions require 'major component shape before decorative details'. RULES_AUDIT §A4 (applied by PLAN §1) removes 'prioritize silhouette before decorative detail' in favour of modelling every photographed feature at its observed size (WP7).
- README.md:125-128 and 143-149: declare_lens_expectations and lens_review keep 'uncertain' labels. These are bookkeeping; drop them.
- README.md:143-144: default preview views are front/quarter/rear. PLAN WP1.4 adds yaw ±30/±45, pitch ±20 and junction crops; WP2 render_views/compare_to_photos must include top and rear.
- README.md:263-264 makes max the default effort. PLAN §2 recommends 'high' (owner unconfirmed; see the RESEARCH.md flag on what the $16.83 evidence really is).
- README.md:237 and 265 keep the 8192 output ceiling as the default. RULES_AUDIT §B: raise it for construction turns.
- README.md:388-396 requires part-visibility profiles on every delivery object or none, and forbids 'an inferred position cutoff' (385-386). PLAN §2 requires no tags (lenses are distinguished by material); RULES_AUDIT §C is live-site only, so arview only warns. PLAN WP1.2 itself finds temples by position (\|x\| > 45 mm).
- README.md:398-404: delivery_evidence coverage keeps a model from becoming 'reviewed'. This is a bookkeeping gate; PLAN uses an owner ledger bound to the GLB sha256.
- README.md:187-214 and 430-433 treat provider-GLB seeding and 'generated donors can save work on complex opaque structures' as a route. PLAN §1: provider meshes got 0 accepts; only a hidden measuring reference is allowed (RULES_AUDIT §B).
- README.md:196-197: an estimated scale 'should be labeled as such in the agent's modeling brief'. PLAN drops the label bookkeeping.

**STUDIO.md**

- Ports: Studio at 127.0.0.1:8767, AR Vite at 8240, hash-pinned review servers at 8796-8798 (STUDIO.md:17-18; README.md:460; RESULTS.md:212-216, 403).
- The owner judged Studio results on the working-tree ar/ served by Vite on 8240 (STUDIO.md:17-18), not on main or ar/site. This supports PLAN WP1.1: base arview on 75ae133 plus the chosen stash edits.
- Lens reflection is a viewer-level multiplier saved with the project and reapplied in Studio 3D/AR URLs; an exported GLB alone does not carry it (STUDIO.md:127, 140-142). Owner verdicts given in the Studio may depend on it, so arview must either have no hidden multiplier or record it in every ledger row.
- Appearance revisions patch only allowlisted GLB material factors, preserve geometry, textures and the original, and write a receipt with both hashes and the changed fields (STUDIO.md:135-138). Controls exist only for features already authored (STUDIO.md:115, 131-132). Any material edit invalidates the earlier review (STUDIO.md:167-169). This fits the PLAN ledger: a new sha needs a new verdict.
- Accepted-asset identity, verified on disk: the Tom Ford FT1123 'looks perfect!' (PLAN §1, 09-30) is on the revised GLB tomford-scratch-002/final-champagne/model.glb, sha 0e77e11ee6ac91b0b5ddc9032e91a1cdd63f88667997868009ddd1ee2041cdd6. Per material-receipt.json, the only change is materials[0].baseColorFactor of 'Brown lenses • homogeneous sun tint', linear (0.600,0.455,0.305) to (0.72,0.65,0.50), with all binary chunks identical. It is not the RESULTS.md delivery 756e43f6…. The tint change was made outside Astra, so seed the ledger with 0e77e11e… and note it (PLAN §0.2).
- Gemini research facts, for the record only: gemini-3.1-pro-preview with Google Search plus flash-lite syntax repair; $2 allowance per job; at most 2 cycles / 6 requests per Start; Google has no per-request query cap (STUDIO.md:36-39, 58-75). Actual cost: $0.21827 per FT1119 job (specifics.json) and $0.890136 for Prada ff56 (job.json).
- Security pattern for any local server glasses3d builds: bind loopback only, validate Host and Origin, require a random CSRF token on mutations, serve allowlisted artifacts only (STUDIO.md:196-199). Open the camera only on request; the bridge validates origin, a random channel and the model SHA (STUDIO.md:145-147).
- Data layout and an ended-run candidate: jobs live in automation/data/blender_agent/studio/jobs/<id>/ (STUDIO.md:151). '--import-trial data/blender_agent/tomford-quality-001 --import-candidate 0916a09c5310' (STUDIO.md:186) is FT1119 quality-001, job 3b8a1c7a: run_status budget_limit, spent $16.522789 (job.json).
- Defect seen in job.json: 'description' holds the agent's import summary for imported jobs (d4b716a6: 'Built from zero and saved the reviewed candidate…'; 3191f92e: 'Built from scratch and saved…') and is empty for Prada 8c82/ff56, Oakley v2 3dff8674 and INVU v2 c8e81c72. The new job schema needs a dedicated owner-description field that is actually sent to Astra (PLAN §1: studio_jobs.py:224, 405-426).

*Contradicted by PLAN (PLAN wins):*
- STUDIO.md:43-44 says 'Generated auxiliary views are weaker evidence than original product photographs'. PLAN §0.3: every owner image is a real photo; never demote it.
- STUDIO.md:49-52 and 83-92: research stops the build when sources are missing, and 'visual estimates are rejected'. RULES_AUDIT §B allows a visual reading from the photos; PLAN WP2 takes owner-supplied specs and has no automated research stage.
- STUDIO.md:96-98: 'new projects need only their exact name and images', and the description never reaches Astra (studio_jobs.py:224, 405-426). PLAN WP2: send the owner's job description, specs and every reference image as authoritative.
- STUDIO.md:46-48 defaults to $20, max effort and an 8192-token ceiling. PLAN §2 recommends high effort and leaves the cap unconfirmed; RULES_AUDIT §B says raise the output ceiling for construction turns.
- STUDIO.md:156-163 requires select_candidate before delivery; without a selection the run stays unfinished. This is a selection gate; PLAN drops it.
- STUDIO.md:5-6 and 49 describe research before every build as part of the workflow. PLAN §0.4: research is done by the owner and pasted in as data.

**RESEARCH.md**

- Agents SDK architecture: agent.py uses Agent, Runner.run, SQLiteSession and MCPServerStdio (RESEARCH.md:188). Keep MCP image forwarding on, serialize mutations, never auto-retry a mutation (RESEARCH.md:45-47).
- Upstream ahujasid issue #279: commands can complete AFTER a timeout. After an uncertain mutation, inspect live state and never replay blindly (RESEARCH.md:264-267). The Lab MCP has the same exposure with its 300 s client ceiling (memory blender-mcp-official-server).
- MCP options for the WP0.5 decision (RESEARCH.md:30-37): Blender Lab MCP needs Blender >= 5.1; blend-ai has no MCP undo; the Astra Blender Harness was rejected (LiteLLM phase loop, three-reference limit, scripted-provider validation only). No server has a measured eyewear benchmark (RESEARCH.md:42-43).
- AR audit of main's runtime, for WP1.2/1.3 (RESEARCH.md:130-135): a closed native lens kept all 12 box triangles and its native material. An explicitly tagged crystal FRAME was misclassified as optical when it had no canonical descriptor (optical-material.ts classifyAssetMaterials, RESEARCH.md:139-141); this was fixed only on the Studio branch (RESULTS.md:262-265). A 1 mm object translation trips the rear-drop.ts createRearDrop identity rejection (RESEARCH.md:135, 142-144).
- Copying a canonical lens descriptor makes the runtime replace the lens material (lens-material.ts), discarding the native shader (RESEARCH.md:145-147). arview must keep native lens materials native.
- Front-facing normals, single-valued surfaces and intrinsic vertical UVs are front-sheet-shader requirements, not browser limits (RESEARCH.md:149-153). arview must not apply them to native lens solids.
- Renderer optics: Three.js native transmission is a screen-space raster approximation. The canonical lens path samples transmitted content without refraction, and canonical crystal look-through removes refraction/roughness blur (RESEARCH.md:155-160).
- TF003 r0003 lens lesson: the lenses were curved (26.94° horizontal / 16.94° vertical normal variation, 0.83 mm RMS from a plane, 2.92 mm residual range, base curve 4), yet the delivery cut the 1.65 mm closed lens down to its front surface with a zero-thickness canonical material. That is a credible cause of the flat look (RESEARCH.md:172-184). Keep full lens solids (RULES_AUDIT §C).
- Provider-mesh failure facts: cached Tripo meshes were about 1.94 M triangles of connected opaque geometry; loose-part separation was insufficient; segmentation missed a thin Miu optical strip; five products loaded in AR and none was accepted (RESEARCH.md:82-89). A gold line painted inside an opaque generated temple does not become a real wire when the temple is made transparent (RESEARCH.md:103-106).
- Still-valid checklist: seat curved lenses in the real 3D aperture without gaps (WP4); avoid global remeshing that destroys thin rims or UVs; separate camera perspective and temple articulation from shape error (WP3b); supplied dimensions beat image-derived mm; inspect the exact exported asset in AR, not only in Blender (RESEARCH.md:93-101).
- The seed-editing trial cost about $6.10 (RESEARCH.md:196). RESEARCH.md:280-281 said no quality gain from max effort had been measured as of 09-30. Since then, every Blender Studio run the owner praised used max: scratch-002 trial-result.json reasoning_effort=max; Prada 8c82, Oakley v2 3dff8674 and INVU v2 c8e81c72 job.json effort=max.

*Contradicted by PLAN (PLAN wins):*
- RESEARCH.md:66-80 and 116-121 propose an initializer comparison and rate 'Complex opaque sports frame: Generated donor is promising' (RESEARCH.md:73). PLAN §1: provider meshes got 0 accepts, and the owner chose Astra building in Blender.
- RESEARCH.md:139-144 recommends normalizing valid static glTF transforms on import. PLAN §2 requires baked identity transforms: the exporter bakes, validate_spec enforces, and arview keeps the identity refusal as an owner-table refusal (WP1.2, rear-drop :144-145).
- RESEARCH.md:28-58 and 205-267 are GitHub/Reddit/LinkedIn/X research by Claude. PLAN §0.4: no web research by Claude; this material is unverified and must not drive decisions.
- RESEARCH.md:271-276 says to require 'alternative physical explanations for photo lines' and to check profiles 'before decorative details'. RULES_AUDIT §A4 removes 'a band may be a reflection' and 'silhouette before decorative detail'; PLAN §0.6 says a prompt sentence alone is never a fix (WP6 check_relief and top/rear views replace it).
- RESEARCH.md:280 says to default future runs to max effort; PLAN §2 recommends high (unconfirmed). Correction for that decision: PLAN §2's evidence ('the 09-30 max run cost $16.83 and got changes requested') is the MODELER route, not the Blender Studio. That run is moved/data/modeler/agentic/studio/tom_ford_ft1123_d_26e_49-003, settled_usd 16.825948; owner_verdicts.jsonl shows changes_requested at 2026-09-30T04:34 and 04:38, 'finish ... matte, needs shiny'. The Blender Studio's max run on the same product (scratch-002, $15.89 settled + $1.47 hold) got 'looks perfect!' after a tint change. Put this in front of the owner when the effort default is decided.

**RESULTS.md**

- tomford-scratch-002 (FT1123-D 26E 49; empty scene + 6 photos; max effort on every request): construction ran 86 responses at a 25,000 output ceiling, closeout 3 responses at 8,192 (RESULTS.md:311-316). Settled $15.894722 plus a $1.465363 unresolved hold from an HTTP 429 account-limit rejection, $17.360085 of $20 in total (RESULTS.md:318-321). It stopped because the next worst-case request needed $3.701325 with $3.552678 left, so the host had to run a closeout for the handoff (RESULTS.md:321-325).
- scratch-002 delivery: GLB 2,283,500 bytes, sha 756e43f6…. It passed six-view AR, temple continuity and portrait checks; 32 product objects; closed lens solids with about 3.458 mm nonplanarity; packed blade texture (RESULTS.md:335-342). The rim repair is measured: the endpiece fold's maximum adjacent normal change fell from 177.90° to 14.80°, and its boundary edges from 24 to 0 (RESULTS.md:344-346). The owner later accepted the champagne-tint revision, sha 0e77e11e… (see STUDIO.md entry).
- Limits the engineer review stated before the owner verdict: crystal rim depth/contrast weaker than the references; distal gold core much less visible in AR than in Blender; hinge and logo inferred; inner-arm/tip inscriptions omitted (RESULTS.md:350-354).
- AR opacity failure, for WP1.2: alpha-blending the crystal made the core visible, but the runtime left the acetate shell in place while splaying the core by up to 17.468 mm, because opacity changes fitting bounds, continuity and overlays (RESULTS.md:358-361). Native crystal transmission also excludes posterior hardware (RESULTS.md:364-365). So arview must find temples by position, not opacity, and crystal frames must show their embedded cores.
- scratch-002 run evidence: 3 invocations, 90 API requests (89 succeeded), 88 tool calls, 35 edit journals, 3 reviews, 16 preview receipts (RESULTS.md:388-390).
- tomford-scratch-001 (same product, high effort, 8192 ceiling): 75 + 16 responses, $14.418763 of $17 (invocations $10.778566 and $3.640197 per trial-cost.json). It stopped needing $2.883663 with $2.581237 left (RESULTS.md:170-175). The run was assisted: Blender crashed during subdivision, and the host restored a checkpoint and supplied an AR-focused refinement brief (RESULTS.md:177-182).
- scratch-001 delivery: 10,066,976 bytes, sha f4bab3b6…; front mesh 118,226 vertices / 236,456 triangles (RESULTS.md:198-206), over the live 100k limit (RULES_AUDIT §C). The lenses were curved (3.30/3.28 mm residual depth), but AR showed a pale/milky crystal, uniformly brown oblique lenses and weak temple cores (RESULTS.md:152-158).
- Geometry-regression lesson, for a WP2 check_surface measurement: SIMPLE subdivision of an irregular n-gon cap (3- to 17-sided faces), plus a crown that moved only vertices with \|local_depth-2.65\| < 0.025, turned grooves into ridges (RESULTS.md:41-49). Raycast front-depth change over a 0.1 mm step at x=45, y=9.8/9.9 went from 0.0133 mm before to 0.6083 mm after (RESULTS.md:22-26). Adjacent front triangles above 60° went 79/21,093, then 747/86,433, then 999/83,747 (RESULTS.md:33-38). Watertightness and smooth shading did not prove smooth geometry; opaque neutral close-ups and triangle-normal statistics did (RESULTS.md:5-8, 79, 92-96).
- The bands were real geometry, not reflections: 'A lighting-only interpretation was too broad' (RESULTS.md:51-56). This is direct evidence for removing 'a band may be a reflection' (RULES_AUDIT §A4).
- Temple-core simplification, the same failure class as the Prada flat temples: Astra built a constant 0.66 x 0.96 mm rounded-rect wire and a 1.15 x 2.7 x 19 mm box paddle, while the photos show a narrowing core with a shaped transition. No matched reference/model close-up of paddle, transition, stem or tip was ever made (RESULTS.md:58-70). compare_to_photos needs matched per-component crops.
- Edit outcomes (RESULTS.md:74-86): an unclamped 1.05 mm bevel came out jagged; a native rounded curve profile fixed the worst of it; analytic custom normals did not remove the bands; a 0.34 mm crown crashed Blender (recovery 'crown None'); lowering lens specular reduced AR hotspots; world/lamp changes were inspection experiments only. A later checkpoint is not automatically the best (RESULTS.md:97-100), so keep every checkpoint and support rollback.
- Observation density: 9 preview_ar calls (1 rejected for invalid rear angles), 23 views at 720x480. One compact response shrank from 18,370 to 1,634 JSON characters. Context grew from 15,667 to 197,395 input tokens. The largest output at high effort was 3,410 tokens, under the 8,192 ceiling (RESULTS.md:132-139).
- Portrait harness: 1024x1024 native, eye crops 768x376 under normal and fixed diagnostic illumination; numeric lighting stays host-side (RESULTS.md:112-118).
- tomford-agentic-001 (seed TF003 r0003, sha 530be4b8…; high effort): 45 responses, $6.103384 of $20 (RESULTS.md:242-244, 297-300). GLB 1,677,968 bytes, 28 meshes, 89,916 triangles, sha 3876b728…, with clearcoat, transmission, volume, specular and IOR (RESULTS.md:293-295). Edits: front-rim crown up to 0.72 mm; 0.65 mm quadratic lens crown; volume thickness frame 0.0052 m, temples 0.0036 m, lenses 0.00165 m (RESULTS.md:253-258). Verdict: the workflow works but quality was not reached; Blender renders looked more convincing than AR (RESULTS.md:227-232).
- Budget defect lesson: holding the max reservation for every finished response stopped invocation 1 after 22 responses at about $2.04 used. The fix was per-response settlement, and the run resumed with $17.955590 of $20 (RESULTS.md:273-277; trial-cost.json invocation_1_settled_micro_usd 2044410).
- End-of-cap stranding, measured: the worst-case reservation exceeded remaining funds at $2.58 of $17 (scratch-001), $3.55 of $20 (scratch-002) and $3.48 of $20 (FT1119 quality-001, final-evaluation.json next_request_reservation_usd 3.530563). That leaves 15-18% of each cap unusable and no Astra-written handoff. The WP2 budget needs a reserved closeout allowance or a smaller final-turn ceiling, as scratch-002's closeout used (RESULTS.md:321-325).
- Other Blender Studio costs on disk (not in the docs): FT1119 quality-001 $16.522789 of $20, max effort, 8192 ceiling (tomford-quality-001/run-audit.json); FT1119 scratch-001 $9.194694 + $1.44775 hold of $16, max, 8192 (completion-review.json); FT1119 seed-001 $8.004060 of $10, max (completion-review.json).

*Contradicted by PLAN (PLAN wins):*
- RESULTS.md:350 ('This is not a perfect AR result') and RESULTS.md:152 ('photographic fidelity was not reached') describe scratch-002. The owner later said 'looks perfect!' (PLAN §1, 09-30) on the champagne-tint revision, sha 0e77e11e… (final-champagne/material-receipt.json). The owner verdict wins, and the accepted sha is 0e77e11e…, not 756e43f6… (RESULTS.md:336).
- RESULTS.md:92-101 and 105-109 frame the fixes as instruction changes ('alternative physical explanations', 'major component form before decoration', 'rollback'). PLAN §0.6 says a prompt sentence alone is never a fix; WP3b top/rear views, WP6 check_relief and WP7 replace them.
- RESULTS.md:96-99 asks Astra to record which features are 'physical boundaries, uncertain interior structure or moving highlights'. That is observation-label bookkeeping, which PLAN drops.
- RESULTS.md:108-110 says 'Future runs default to max reasoning'. PLAN §2 recommends high (unconfirmed; see the RESEARCH.md correction about the $16.83 run).
- RESULTS.md:352-354 accepts that 'small inner-arm/tip inscriptions are omitted' and that hidden details stay inferred. PLAN WP5 projects photo pixels, including the one-sided FT1119 inner-temple print, so omitting a feature a photo shows is no longer acceptable.

**GEOMETRY_COMPARISON.md**

- Circular-fit failure in the legacy tools: cameras were fitted to photo mattes and renders were then compared against the same mattes. A 'locked' camera could still refit scale and image shift on a new revision, hiding geometric change (GEOMETRY_COMPARISON.md:16-19). Rule for WP3b: fit once, then freeze every camera parameter for before/after comparisons. A refit is a new comparison basis, and normalization must never be recomputed from each revision's bounds (GEOMETRY_COMPARISON.md:58-62).
- Matte hazards: clear-frame mask completion filled a real background wedge between crossing temples, and small reflection fragments changed inferred side dimensions (GEOMETRY_COMPARISON.md:12-15). Semantic masks counted reflected products as extra lenses and included hardware in lens masks (20-23). Projection overlap does not establish the identity of a physical surface (24-27). The WP3b matte and the WP5 leave-one-view-out check_print must guard against these.
- Temple articulation differs between front, rear and side photos. Do not bend the static product to fit incompatible open/folded configurations (GEOMETRY_COMPARISON.md:89-91). The camera fit must allow per-view temple articulation or exclude the temples from the fit.
- Source resolution limits detail: the INVU front photo is 194x259 px and was flagged as insufficient for fine detail (GEOMETRY_COMPARISON.md:96-97, 162-164).
- Complementary panels: raw photo, native-material render, neutral clay, normals and structure lines, with matched crops in identical coordinates (GEOMETRY_COMPARISON.md:63-67). Clay and normals views are what exposed the scratch-001 rim corrugation (RESULTS.md:13-17), so compare_to_photos should include them.
- Bind evidence to the GLB and reference hashes, camera parameters and crop boxes (GEOMETRY_COMPARISON.md:68-72). Report identical model bytes and identical decoded geometry separately, so an unchanged shape is never mistaken for progress (146-147).
- Landmark fitting performed poorly in practice: framing mode fixes pose/focal and fits only magnification and offsets; a full pose needs at least 8 distributed nonplanar anchors (GEOMETRY_COMPARISON.md:135-141). In the Prada exercise, three approximate manual correspondences gave 'ambiguous/poor anchor agreement' (165-169). The WP0 silhouette fits reached IoU 0.71-0.90 (PROGRESS.md:38-41), which supports PLAN's choice.
- Validation worth replicating: a twelve-camera cross-check against the actual Three.js camera code (GEOMETRY_COMPARISON.md:151-156). Use it for arview_capture vs Blender render_views conventions. A frozen baseline reproduced the camera and the image byte-for-byte (168-169).
- The comparison decoder requires static ordinary triangle geometry; compressed, instanced, skinned or sparse inputs fail explicitly (GEOMETRY_COMPARISON.md:185-188).

*Contradicted by PLAN (PLAN wins):*
- GEOMETRY_COMPARISON.md:32-34 and 43-52 ('removing segmentation'; 'optional fitting uses named 2D/3D landmark correspondences, not image masks'). PLAN WP3b ports bsa/cameras.py: multi-start + Powell on silhouette IoU with the quadratic-background matte. PLAN wins; keep only the frozen-baseline rule.
- GEOMETRY_COMPARISON.md:80-84 ('Original product photographs have stronger evidential value than generated supplementary views … must not override contradictions in genuine photographs'). PLAN §0.3: all owner images are authoritative photos.
- GEOMETRY_COMPARISON.md:73-76 ('Do not … convert a residual threshold into an acceptance decision'). PLAN WP3b/WP5/WP6 use measured thresholds as pass criteria and stop rules: registration in mm, top-view IoU >= delivered, band-pass r beating procedural.
- GEOMETRY_COMPARISON.md:5-8 ('The owner explicitly lowered the acceptance bar … accepted/rejected labels must not set the new quality bar') is unsourced. PLAN §1 and WP8 treat owner verdicts as the acceptance criterion.
- GEOMETRY_COMPARISON.md:53-57 and 86-88 ('Hidden or ambiguous features stay unknown'). PLAN WP6 builds the faceted temples from top/rear views, and WP5 fills unseen texels by mirroring and PyPatchMatch instead of leaving them unknown.

## 5. What no Studio file provides (written fresh)

- WP2: a safe mode for execute_blender_code that allows numpy and blocks file I/O. Nothing in blender_agent provides it. agent.py:351 turns on the community server's own safe mode (BLENDER_MCP_SAFE_MODE=1), and instructions.py:179-182 describe it as bpy/bmesh/mathutils plus pure-Python imports only (RULES_AUDIT §B: it bans numpy). isolated.py:130 and source_tools.py:40 import blender_mcp.safe_mode.validate_code from the external package. Needed: a new AST validator plus enforcement inside Blender. It must allow numpy, block np.load/save/fromfile/tofile/memmap/loadtxt/savetxt/genfromtxt, open, os, pathlib, io, subprocess, socket, exec/eval, importlib and dunder access, and come with its own tests.
- WP2: a pricing/tariff module. budget.py:25 and trial_budget.py:17 import Tariff, MICRO, MODEL, PricingError, usd_text and validate_usage from modeler/agentic/pricing.py, which is in no inventory. Port it (gpt-6-astra standard rates: $10 input, $1 cached, $12.5 cache write, $50 output per million tokens; ×2 / ×1.5 above 272k input tokens) without the gpt-6-luna fallback, and add tests. Otherwise budget.py and the kept test_blender_agent_budget.py:15 cannot import.
- WP2: an owner approval gate for each paid run, with a cost estimate. Today the only gate is a --run-paid flag (agent.py:366, :408-409, :549) plus a --max-usd cap defaulting to '20' (:544). The Studio passed --run-paid itself (studio_jobs.py:546). Nothing produces a pre-run estimate or records the owner's OK. Also needed: a one-turn capped smoke mode for WP8 step 0.
- WP2: g3d Blender helpers (WP4 seat_lens and hide_eyewire; WP6 faceted_temple, polygon sections with sharp creases, per-station lofts) and a way to load them into the live session. bootstrap.py:56-61 registers only the MCP addon. The Studio had no helper library: Astra wrote raw bpy (instructions.py:178-183). The new safe mode must allowlist the helper package.
- WP2: render_views, Blender renders at all canonical views (front, rear, left, right, top) and at each fitted reference camera (clay, textured, normals). No such tool exists. The Studio used only the MCP get_viewport_screenshot (agent.py:42-43), AR previews limited to yaw ±80 / pitch ±60 with 'asset-back' as the only rear view and no top view (preview.py:28-33), and a Three.js renderer that exists only in stash^3 (geometry_comparison.py:25, :178).
- WP2: compare_to_photos across every reference view (top and rear included), using the WP3b silhouette-fitted cameras, with overlays, IoU and contour error in px and mm. geometry_comparison.py compares ONE photo per call, using landmark correspondences Astra supplies (:401-408, :295-312), and depends on bsa.archeck and reconstruction.mesh (:18-19). geometry_camera.py fits landmarks with Euler yaw/pitch, and its test refuses the top view (test_geometry_camera:192-199). Only the pinned-identity, panel and viewer ideas transfer (geometry_evidence.build_panels/write_viewer).
- WP2: an arview_capture tool wrapper around the WP1 `arview capture <glb> --poses default\|junction\|all` CLI, returning its PNGs and report.json warnings. preview.py drives bsa.archeck.run (:22, :120, :188) with checker or solid backgrounds only (:112) and no junction crops. It also imports dropped modules: episodes.runtime_identity (:108, :165, :249), delivery_evidence.compact_part_visibility (:25), optical_quality (:228, :288-291). portrait.py runs ar/qa/portrait-preview.mjs through bsa (:10-14, :100).
- WP2: host-tool wrappers for project_photo_texture (WP5) and the camera fits (WP3b port of bsa/cameras.py, not geometry_camera.py), plus the Blender-side texture attach step. The only reusable pieces are the live-export mechanism (preview.export_current :136-157) and the packed-copy/restore transaction helpers (source_tools.py:39-86, isolated.py:26-92).
- WP2: function tools for check_lens_seating, check_print and check_relief. No equivalent exists in either codebase (PLAN §1: 'Neither codebase has any lens-seating rule or check').
- WP2: a job brief builder that sends the owner's description verbatim, the specs, and every reference image with its view label, all marked authoritative, plus a test that inspects the first request payload. studio_jobs.py:224 stores the description but _prompt (:405-426) omits it. The import paths label owner-generated views 'supplementary/unverified' (:770, :858). _prompt also carries rules that must go: 'Do not import donor models' (:409) and 'Treat ambiguous reflected photo streaks as hypotheses' (:422-423). session_inputs.plan_session_input (keep) only deduplicates and resends.
- WP2: the CLI `python -m glasses3d job create --images <dir> --views <json> --name --description [--specs]`, `job run <id>`, and status/review subcommands. Nothing provides this: __main__.py:1-4 calls agent.main (doctor and run with --prompt-file/--photo only). JobStore.create takes HTTP base64 data_urls (studio_jobs.py:125-151, :222-244), and run orchestration happens in a web worker thread (:473-584) with effort fixed at max, 120 turns and 8192 output tokens (:545). Also needed: --views validation that maps each image to a WP3b prior (front, side, top, rear, angled, top-oblique), the job directory layout glasses3d/data/jobs/<id>/, and the deterministic final step that writes deliverable/model.glb plus report.json (export, validate_spec, arview report, checks, costs). The Studio's 'delivery' was the dropped selection.py or the latest-preview fallback (studio_jobs.py:640-674).
- WP2: an owner review ledger (accept or changes, with notes) bound to the GLB sha256. Nothing provides it. Despite its name, review.py is a local HTTP server for AR links (review.py:1, :14-74); it imports the dropped studio_fit (:11) and hard-codes the AR app at 127.0.0.1:8240 (:19). studio_jobs, studio_server and app.js contain no verdict ledger (grep finds none). Use the format of moved/data/modeler/calibration/owner_verdicts.jsonl (fields when, verdict, medium, note, job, product_id, candidate, asset_path, asset_sha256, ...). It has 50 rows, of which 34 are demo-synthetic and dropped, leaving 16.
- WP2: the $0 replay through a scripted fake model client that exercises the budget and receipt path. ScriptedModel (tests/_blender_scripted_model.py:12-33) returns Usage() and bypasses the HTTP hooks. test_blender_agent_quality_workflow.py:51-60 and :170-186 run ReviewLog without a budget and script the dropped tools. The new replay (code, render_views, compare_to_photos, arview_capture, export_glb, validate_spec, ledger row) needs a fake transport (httpx MockTransport answering /responses and /responses/input_tokens) so that BudgetGuard.before_request and settle_latest write a real ledger.
- WP2: a run watchdog and heartbeat (PLAN §0.5: no silent stalls). Today there are only 300 s MCP and 1800 s HTTP timeouts (agent.py:353, :392-398); the Studio showed progress only through its web UI (studio_jobs.py:379-394). Also an EcoQoS opt-out and detached launch for the Blender and agent child processes: process_options only hides the window (studio_jobs.py:165-171).
- WP2: a glasses3d requirements file, and run_snapshot coverage of subpackages. blender_agent/requirements.txt:6 includes ../requirements.txt (automation's file). run_snapshot.py:34 globs top-level *.py only and records neither the git commit, the Blender version nor the arview commit.
- WP3: validate_spec(glb), checking exactly the §2 table on every export and returning its report to Astra. NativeState provides only the decoder pieces: finite accessors (:343-344), triangles via mode==4 (:236-238), static (:191-192, :229-230) and a single embedded buffer (:202-204). It never requires identity node transforms (:266-285 only parses them) and never checks metre units, bridge-underside origin, orientation (+Y up, +Z forward, temples along −Z, arms on opposite sides of X) or that lenses are distinguished by material. It rejects any node hierarchy (:223-225), so it cannot validate the external multi-mesh GLBs in the WP1 proof. inspect_export.glb_summary only summarises (:15-39).
- WP3: exporter fixes that native_export.py does not have. (1) use_mesh_edges=True and use_mesh_vertices=True (:245) export loose edges and vertices as line/point primitives, which breaks 'triangulated' and which NativeState rejects (:236-238). (2) mesh.flip_normals() under a negative determinant (:203-204) discards authored custom split normals; seed_prepare.py:204-237 shows the correct capture-and-rebind. (3) Materials are shared by name across parts (:228-234), with no per-role duplication or check that lens and frame materials differ. (4) No texture-size check (≤4096) or image-format setting. (5) The part_visibility validator injection must be cut (:14, :43, :53, :127-129, :173-191). (6) agent.py:42-43 exposes the MCP's own export_scene, which bypasses this exporter. Fresh $0 tests: faceted-object normals read back from the GLB; quality-001 passes validate_spec; a check that the Studio scenes are mm with +Y up / +Z front (bootstrap.py:18-22 and isolated.py:275 set mdl_bridge_underside, the origin key read at native_export.py:63-66).
- WP7: the Astra instructions text and the brief template, written fresh. The old text carries every removed phrase: 'Generated product views are hypotheses' (instructions.py:45-46), 'a band may be ... reflection' (:49-50), 'Keep unobserved details simple' (:52-53), plus bookkeeping (:56-98 candidates and corrections, :136-145 part_visibility, :147-159 source field, :178-183 the no-numpy safe mode). Needed: the seating, relief and print rules with the §3 numbers (frame-type aware), each naming its tool and check, and the real guards (§2 spec, closed parts, small branding stays small, colour from photos not the product name, room reflections on lenses are not defects).
- WP7: a two-way instructions contract test. validate_artist_tools (instructions.py:16-36) is one-way: every tool named in a set must exist. Needed: assertions that the removed phrases are absent from both the instructions and the brief, that every rule names an existing tool AND check, and that every registered tool is described.

### Needs the second reviewers found missing from the first inventory

- (agent-loop) A session lifecycle (start/status/stop/restart-from-checkpoint) with PID-ownership-verified teardown (port test_blender_agent_mcp_isolation.py:34-60 logic into the driver; psutil). Also terminate the child on readiness failure
- (agent-loop) execute_blender_code must be a host function tool, or an MCPServer.call_tool override, that runs the forked numpy-allowing validator. The host's own trusted calls (stop checkpoint agent.py:165-170, export_glb, checkpoint/restore) need a separate path that may use bpy file operators, which Astra's policy bans
- (agent-loop) validate_spec must verify the origin from geometry (bridge underside actually at 0,0,0), because the bridge marker is always present with a default value (bootstrap.py:21, isolated.py:275)
- (agent-loop) Port instructions.validate_artist_tools (instructions.py:32-36) as the WP7 preflight: 'every instructed tool exists', run at loop start like agent.py:476-477
- (agent-loop) The WP2 $0 proof must exercise the httpx transport hooks (reserve/archive/settle) as well as tool flow. A ScriptedModel (tests/_blender_scripted_model.py) bypasses ReviewLog.before_request/after_response, so pair it with a MockTransport test like test_blender_agent_transport.py, and give the loop a model-injection point (agent.py:372-403 hard-builds AsyncOpenAI)
- (agent-loop) The new job composer should label each reference block with its view ('Reference N (top)'); session_inputs' free-text label already carries it. The Studio put view names only in the brief text (studio_jobs.py:406-407) while images carried filenames (agent.py:446)
- (agent-loop) If the community MCP server is kept, enforce and test telemetry-off (BLENDER_MCP_DISABLE_TELEMETRY in both the server env agent.py:351 and the Blender env session.py:64), because tool calls carry code and user_prompt to its trajectory uploader
- (agent-loop) Create and validate trial-budget/session state atomically after all preflight checks (key, prompt, images), so a failed first invocation does not lock the job directory
- (agent-loop) glasses3d/.venv must be an isolated venv (no --system-site-packages), with requirements proven by a clean install
- (agent-loop, defect) agent.py:367 vs :412-421/:437: TrialBudget freezes trial-budget.json (trial_budget.py:98-102) before the key, prompt and image checks and before SQLiteSession creates session.sqlite. Any failure in between (for example no OPENAI_API_KEY) leaves an output dir that refuses a fresh run (trial_budget.py:98-99) and also refuses --resume (agent.py:417-418)
- (agent-loop, defect) agent.py:38/:425/:473 send the Studio INSTRUCTIONS on every run. They contain removed phrases: instructions.py:46 'Generated product views are hypotheses', instructions.py:49-54 'a line or band may be ... reflection ... Keep unobserved details simple'
- (agent-loop, defect) agent.py:474 hands the raw MCP execute_blender_code to the model, so a host-side (numpy-allowing) validator cannot intercept calls unless the tool is wrapped or MCPServer.call_tool is overridden (EvidenceMCPServer, agent.py:343-346, is the only hook)
- (agent-loop, defect) agent.py:170/:520 pass the community server's user_prompt argument. server.py:707-709 captures it with the code for trajectory upload (trajectory.py:578, :1431) whenever telemetry is enabled
- (agent-loop, defect) session.py:88-99 orphans the launched Blender on a receipt mismatch (session.py:35/:37) or on timeout; session.py has no stop or teardown, and verified teardown exists only in test_blender_agent_mcp_isolation.py:34-60
- (agent-loop, defect) session.py 'real-Blender test' claim is overstated: test_blender_agent_session.py:94 runs only bootstrap.initialize_scene in --background; session.main is only exercised by test_blender_agent_mcp_isolation.py:87
- (agent-loop, defect) bootstrap.py:21 (and isolated.py:275) always write a default mdl_bridge_underside=[0,0,0], so WP3's 'error if marker missing' can never fire for pipeline scenes; a present marker does not prove the bridge is at the origin
- (agent-loop, defect) isolated.py:262-276 _EMPTY creates a scene without bootstrap's METRIC/0.001/MILLIMETERS units or axes tag (bootstrap.py:18-22)
- (agent-loop, defect) isolated.py:185 the fingerprint check reopens the candidate in the live session (changes the active file); copying it into every export_glb repeats the :86-87 hazard
- (agent-loop, defect) bootstrap.py:40-42 receipt rename has no Windows transient-error retry (memory: Defender denies about 1% of renames)
- (agent-loop, defect) Inventory error: bootstrap.py:26 + :61 'port stored twice' is not a defect. :26 persists the dedicated port into the checkpoint so the add-on auto-start (addon.py:89-95) keeps it after reopen (asserted at test_blender_agent_session.py:72,78)
- (agent-loop, defect) Inventory error: httpx2 is not missing; it is a hard Requires-Dist of openai 3.22.1 and openai-agents 0.22.3
- (agent-loop, defect) requirements.txt was never validated in isolation: the Studio venv uses include-system-site-packages = true, so dotenv, psutil, Pillow and numpy come from the system Python
- (agent-loop, defect) Inventory undercount: dropping the legacy regex touches at least 9 tests via the legacy() fixture (test_blender_agent_session_inputs.py:46, :61, :83, :90, :101, :124, :132, :137, :145), not just :46 and :61-66
- (budget) WP8 step 0 says 'ONE capped Astra turn (a few cents)'. Under BudgetGuard's worst-case hold, a cap of a few cents refuses the first request. tomford-quality-001's first request (22,012 input, 8,192 output) reserves about $0.685, and tomford-scratch-002's first reserved $1.465 (17,229 in, 25,000 out). The smoke cap must be at least about $1, or max_output_tokens lowered for that turn. The owner's estimate should say 'spend: cents; hold: about $0.7-1.5'.
- (budget) Per-run owner cost estimates (PLAN §0.7) must state effective spendable = cap minus the final worst-case reservation: about $3.5 at about 246k context with 8,192 output tokens. That grows by about $1.19 per reservation if RULES_AUDIT §B raises output to 32k. Past runs left 13-42% of their cap unspent at budget_limit.
- (budget) The job ledger must handle a run that fails before its ledger exists. Either create the ledger atomically as the first run artifact, or treat a ledger-less run with no transport files as zero spend. Freeze no budget state before inputs validate.
- (budget) Port the terminal-'incomplete' settlement path (agent.py:326-339). The SDK can raise before on_llm_end, and the new session driver must settle or classify each response before sending the next. This relies on one request at a time: parallel_tool_calls=False at agent.py:399 and max_function_tool_concurrency=1 at agent.py:483.
- (budget) Owner-review transcript: reasoning summaries are returned for only about 16% of responses (30 of 192 across five runs), so the session driver must also persist Astra's message text and function-call arguments in a readable transcript, not only in transport/*.json.
- (budget) The run receipt should be built from git (ls-files, HEAD, dirty-diff hash) and include the arview Node code that arview_capture runs, the Blender version and the venv's package versions. The owner verdict row should bind the GLB sha256 together with the budget-ledger sha256 and the implementation-receipt sha256.
- (budget) Owner reconcile must cover 5xx and connection-error holds (the oakley 520, $0.923; the tomford-ft1119-scratch-001 APIConnectionError, $1.448), not only 4xx settle-at-zero. modeler/agentic/budget.py:230-245 (kinds) and :289-310 (reconcile_unknown) are the reference semantics.
- (budget, defect) trial_budget.py:97-102 + agent.py:367,417,421: the trial manifest is frozen before input validation, so a preflight failure leaves an output that can neither start fresh (trial_budget.py:98-99) nor resume (agent.py:417, no session.sqlite). This happened for real: moved/data/blender_agent/oakley-scratch-001/preflight-failed-agent/ (trial-budget.json, run.stderr.log 'Expected a PNG, JPEG, WebP, or GIF image').
- (budget, defect) trial_budget.py:167-168,150 + agent.py:423-445 vs agent.py:394: the run directory is created, then the snapshot is taken and the photos copied, before the ledger exists. Any exception in that window leaves runs/<id> without budget.json, and every later resume fails with 'Missing or malformed budget evidence'.
- (budget, defect) Reservation cliff observed in real runs, not just in theory (pricing.py:137-151 with budget.py:312). tomford-quality-001 stopped at budget_limit with $3.477 of $20 unspent (average settled $0.192 vs average reserved $1.842 per response; 97.5% of input cached). tomford-scratch-002 run 125702 left $3.55 unspent and tomford-scratch-001 run 112741 left $2.58.
- (budget, defect) budget.py:139-170 planning() status drives episodes.py:320-331 closeout, the correction-batch recovery being dropped. Astra's reasoning shows it plans around the budget hint: tomford-ft1119-seed-001 run 170018, interaction 12, 'avoid long outputs'; interaction 20, 'finalize about 30 responses for a budget of 8.35'.
- (budget, defect) Inventory evidence error: agent.py:81-85 with_budget_context injects only numbers plus planning_status, and only into 5 tools (agent.py:110,115-118). The prose 'action' text reaches Astra through the budget_status tool (agent.py:64-70) instead.
- (budget, defect) There were four unsettled-hold v2 runs, not three. oakley-scratch-001 run 20260930T154556 failed on a Cloudflare 520 InternalServerError with a $0.923 hold. That is a 5xx, so a 4xx settle-at-zero does not clear it; only an owner reconcile does.
- (budget, defect) budget.py:261-262 refuses any request through the hooked client except POST /v1/responses. The inference client must be single-purpose; uploads, retrieves or compaction through it are refused.
- (budget, defect) run_snapshot.py:19 means every Studio receipt omitted modeler/agentic/pricing.py, which priced its reservations (tomford-quality-001 implementation/receipt.json lists 42 blender_agent files only).
- (budget, defect) tests/test_blender_agent_budget.py has 22 test methods, not 20.
- (studio-app) Temple clip as a renderer input. arview needs a per-model temple clip z (and width) at handover and capture time, not only as a warning. external.ts:2, :11 (default -0.11) and :47-48, catalog.ts:94 and :100, and renderer.ts:270, :298, :401, :452 and :458 consume it. Port the rule from studio_fit.py:94-98 / stash ar/qa/temple-cutoff.mjs: cutoff = max(-0.14, each side's tip z + 1e-6), nearest -0.045. Find temples by position (behind the lens at \|x\| > 45 mm) instead of part-visibility tags, and write the value to report.json.
- (studio-app) Lens reflection intensity (lensenv 0.3-4, external.ts:24 and :49; studio_server.py:332; index.html:51) is a per-model setting the owner actually used: lens_reflection 3.0 on Oakley job 3191f92e, revisions/3b6ca0c7aa5144cb/settings.json, 2026-09-30. The new pipeline must record a lensenv per deliverable (chosen by Astra or set by the owner) and pass it to arview_capture and the webcam page. WP8 includes a mirror-lens product.
- (studio-app) Owner verdict binding. The worn appearance depends on the GLB bytes plus the handover parameters width, clip and lensenv (studio_server.py:331-340). Ledger rows should record these next to glb_sha256, plus the baseline sha when an A/B comparison was shown (review.py:22-23).
- (studio-app) A channel for owner change notes. `job review <id> changes --note` must reach Astra on the next run as an appended user message, because the old brief freeze (studio_jobs.py:249-250) and resume (:543-550) had no such path. Keep the frozen original brief, but add append-only owner notes recorded in the run receipt.
- (studio-app) The scene contract carried by the cut _prompt. Scene property `mdl_bridge_underside` and partRole frame/temple/lens (studio_jobs.py:420-421) are what native_export.py:63-66 and bootstrap.py:21 read and write. WP3's exporter and WP7's text must use the same key and roles, so record this in PORT_REVIEW.
- (studio-app) Mirror-lens domain guidance (studio_jobs.py:417-419): the documented base/transmission colour is not the front reflection colour; match the coating's angular look against the photos; do not turn a rose base into an opaque rose shield. No RULES_AUDIT list removes it. WP7 should restate the idea in fresh words for WP8's mirror-lens product.
- (studio-app) A fixed view vocabulary for `--views` that matches the WP3b priors (front, angled, side-left, side-right, top, rear, top-oblique), with each view label placed next to its image in the first user message, instead of a numbered list in a separate brief (studio_jobs.py:406-407 vs agent.py:446).
- (studio-app) Per-job --max-turns (hard-coded to 120 at studio_jobs.py:545, while agent.py:542 defaults to 40), alongside --effort and --max-output-tokens, recorded in the run receipt.
- (studio-app) Add to the studio_jobs port list: the single-active-paid-run guard (studio_jobs.py:449-452), the check on resume that Blender reopened the exact checkpoint (:534-537), and test tests/test_blender_studio_jobs.py:191-210 (Blender cleanup only after the runner exits and the checkpoint is verified).
- (studio-app) CLI stop semantics. Ctrl-C in `job run` should write stop.requested and wait for the exit checkpoint (studio_server.py:570-573; agent.py:159-178 checkpoint_on_exit) rather than kill the runner.
- (studio-app) If the arview webcam page loads GLBs from a separate loopback server, reuse the single-origin CORS pattern in studio_server.py:398-405 and :455-466, not review.py:64 '*'.
- (studio-app) The web UI decision should be put to the owner, not settled silently. The owner used the Studio model tab's material panel (job 3191f92e, two revisions on 2026-09-30), so PORT_REVIEW should state what replaces it: at least a lensenv setting, plus an optional live material preview in arview.
- (studio-app, defect) studio_jobs.py:348-365 with :572 and :579-580: after a run whose final checkpoint is not confirmed, resume_checkpoint is popped. Every later get() then re-runs _checkpoint (which raises), sets blender_cleanup.recovery_needed and re-saves job.json. The UI polls every 2500 ms (app.js:208), so job.json is rewritten on every poll, and no API ever closes the retained Blender.
- (studio-app, defect) studio_jobs.py:527 launcher subprocess.run(timeout=90): on TimeoutExpired the job fails (:567) and, if session.json was not written, _cleanup_blender returns 'No owned Blender process was recorded' (:591-592), so a Blender the launcher already spawned is orphaned (PLAUSIBLE; depends on session.py timing).
- (studio-app, defect) studio_jobs.py:131 the regex accepts \r\n inside the base64 data URL, but :135 base64.b64decode(validate=True) rejects non-alphabet characters, so a line-wrapped data URL fails as 'Invalid or oversized image'.
- (studio-app, defect) studio_jobs.py:624 finish() treats status 'completed_with_unresolved_recovery' as an error, while import_candidate_review (:752) and import_completed (:823) accept it as a normal ended state.
- (studio-app, defect) View vocabulary drift: app.js:80 offers 'angle', but studio_jobs.py:769/:857 and tests/test_blender_studio_jobs.py:25 use 'angled'. No value separates left from right side or names a top-oblique view.
- (studio-app, defect) View labels are never next to their images. _prompt lists 'N. original_name: view' (studio_jobs.py:406-407), while agent.py:446 labels each attachment 'Reference N (path.name)', where path.name is the stored file name: reference-NN, or a random 12-hex name after _replace_images (studio_jobs.py:288). Matching a view to its image relies on list order alone.
- (studio-app, defect) studio_jobs.py:449-452 with :301-303: check_start calls list() under the lock, and each get() can terminate Blender with waits of up to 8 s + 3 s (:607-611). A Start request can block HTTP threads for seconds per job.
- (studio-app, defect) studio_jobs.py:249-250 with :543-550: once a run has started, the owner cannot add notes for Astra. update() refuses, and resume resends no owner text, so an owner 'changes requested' verdict has no path to Astra.
- (studio-app, defect) tests/test_blender_studio_jobs.py:66 pins the old mirror sentence ('not the front reflection colour') to the old prompt; it must not be ported as is.
- (studio-app, defect) Inventory errors (not code defects): studio_fit does not require tags on every mesh, because untagged GLBs take the legacy path (part_visibility.py:36; studio_fit.py:57, :67-70, :75-76). The studio_server 145/-0.14 fallback feeds only the 3D viewer_url (:342, :347), while ar_url is None (:346).
- (instructions) WP7 baseline: record in PORT_REVIEW the archived accepted-run texts (tomford-scratch-002 prompt.txt + instructions.txt fc4df47c; 3dff8674/c8e81c72 4733a5d8; 8c82 0ae0ed87) as the reference of what produced accepts, and add 'preserve a simple plausible shape' (branch agent.py:52) to the WP7 removed-phrase test
- (instructions) WP3 exporter (or WP1 arview) must supply LENSES_part_visibility deterministically, with the region taken from partRole plus the sign of X, if WP1 ports the stash runtime. It is consumed at stash@{0}:ar/src/render/rear-drop.ts:124, temple-clip.ts:65, temple-terminal-fit.ts:69, temple-visibility.ts:140, hair-occlusion.ts:27, eyewear-shadow.ts:480 and optical-material.ts:50/86, and native_export.py:180-191 enforces all-or-none. Removing it from the Astra text without this silently drops arview to legacy temple handling
- (instructions) WP2 safe-mode exec must be its own validator, independent of the MCP server choice: today the only sandbox is the ahujasid blender_mcp/safe_mode.py, and the official server execs code into {'result': {}} per memory blender-mcp-official-server.md. Keep the no-file-I/O and no-network bans, allow numpy, and drop the lambda ban unless it is justified
- (instructions) WP2 credential handling: a fresh dotenv loader plus the secret-stripping of the Blender child environment (session.py:61-62; studio_jobs.py:155-157), since Astra-authored code runs inside Blender
- (instructions) WP3 validate_spec GLB reader: read_glb's container checks plus accessor and node decoding taken from reconstruction/mesh.py:52-110 (byteLength :87, ranges :103-104) or the in-tree prototype glbload.py:14-50. Turn _roles (studio_materials.py:130-141) into the §2 check that no material is shared between a lens and a non-lens part
- (instructions) WP3 exporter: keep the legacy obj['part'] role fallback (native_export.py:108-118; tom_ford_brief.txt:9) and convert or validate KHR volume thickness from mm to m (native_export.py:30) instead of asking Astra to author metres
- (instructions) WP2 job create: an open --specs schema (not the studio_jobs.py:94-96 15-key allowlist), with owner specs sent as authoritative and no per-image provenance labels (studio_jobs.py:406, studio_description.py:256-257)
- (instructions, defect) instructions.py:16-29 vs text: 10 of the 30 REQUIRED_ARTIST_TOOLS never appear in INSTRUCTIONS, not 8 (the inventory's own list names 10)
- (instructions, defect) instructions.py:144-145 delivery gate 'inspect_export must report part_visibility.ready for final delivery' forces the live-site LENSES_part_visibility tag on Astra; the inventory's gate list omits it
- (instructions, defect) instructions.py:178-183 the safe mode described is the ahujasid package's validator (agent.py:351 BLENDER_MCP_SAFE_MODE=1; venv blender_mcp/safe_mode.py:129 no numpy, :454 no lambda). Switching to the official Lab server removes the sandbox entirely; it is not just a text mismatch
- (instructions, defect) instructions.py:92/:172/:173 name parameters (baseline_report, lens_review, geometry_comparisons), so a naive two-way 'names in text = registered tools' preflight would false-fail
- (instructions, defect) Accepted-run provenance: the source-before INSTRUCTIONS (sha c3de8791) ran only on tomford-quality-001 (FT1119 failure) and tomford-claude-001. The three owner accepts used fc4df47c (branch agent.py:41), 4733a5d8 and 0ae0ed87, all of which also contain flagged rules (branch agent.py:52 'preserve a simple plausible shape'; 'Generated product views ... hypotheses' in 4733a5d8/0ae0ed87 line 8)
- (instructions, defect) tom_ford_scratch_brief.txt (source-before and branch) is not the prompt the accepted tomford-scratch-002 run used (lines 3 and 15 differ)
- (instructions, defect) studio_materials.py:116-117 non-dict buffers/images entries raise TypeError, not ValueError; string entries get a substring 'uri' test
- (instructions, defect) studio_materials.py:102-127 read_glb never decodes accessors or nodes, so it cannot check any §2 row by itself
- (instructions, defect) native_export.py:30 volume thickness must be authored in metres in a millimetre scene and the exporter does not convert it (the brief's rule at tom_ford_scratch_brief.txt:13 is a workaround)
- (instructions, defect) studio_jobs.py:94-96 owner specs limited to the 15 SPEC_FIELDS keys; any other owner fact is rejected
- (instructions, defect) Minor line refs: the _grounding call is studio_description.py:365 (not :366); the addon lookup is session.py:53 (not :54)
- (export-preview) WP3 export_glb must restore the authored material and node names in the GLB, or write sourceMaterial/sourceObject extras and report the mapping. validate_spec's material table and arview's name-token lens fallback (WP1.3 step 2) should see the authored names, not '.001' copies.
- (export-preview) The per-(material, role) copies should also write partRole into material extras. WP1.3 lists material extras as a primary lens signal, and native_export.py:232-233 strips every material property. validate_spec should flag any material index used by both lens and non-lens primitives (§2 Components row).
- (export-preview) Before export, export_glb should check the scene convention: unit_settings METRIC/0.001/MILLIMETERS and blender_agent_axes '+Y up; +Z front'. Derive or verify meters_per_unit from the scene instead of trusting a host default.
- (export-preview) export_glb must refuse or warn on, by name, render-visible objects that have no triangles, and must set use_mesh_edges/use_mesh_vertices to False.
- (export-preview) WP0's MCP decision has to cover a receipt protocol for all generated Blender code (export, coating, g3d helpers): under the official Lab MCP, assign `result = receipt` and parse the dict, not stdout text lines.
- (export-preview) The validate_spec decoder must keep native_state's in-loop validations (:110-149) and image_bytes (:360-362, for the ≤4096 texture-size warning), and should report the decoder's size caps as limits or warnings rather than §2 violations.
- (export-preview) WP1 arview must tolerate LENSES_part_visibility already present in accepted deliveries (the Prada delivery carries it on every node), and its position-based temple detection replaces the stash's region tags for crystal temples (stash@{0}:ar/README.md:153-158).
- (export-preview) The arview_capture rewrite should keep a frozen capture recipe (environment preset, background, views) saved with the images, as preview.py:171/187/199 does, so before/after comparisons stay matched.
- (export-preview) The portrait/arview width must be measured with the glasses3d decoder, never through bsa/reconstruction, with a loud warning for implausible widths instead of the 145 mm fallback or the 60-250 clamp.
- (export-preview) The tests ported for optical_materials/native_export need a replacement for glb_summary and must not depend on the agents SDK or the community blender_mcp.safe_mode, or they skip silently.
- (export-preview, defect) native_export.py:213 and :230: duplicate objects and material.copy() collide with the live datablock names, so every exported node and material gets a '.001' suffix. Confirmed on the accepted Prada delivery GLB ('Lens \| light brown neutral transmission.001', 'Front \| continuous milled acetate.001'), and tests/test_blender_agent_scratch.py:175 works around it with startswith. The receipt (:239) records the unsuffixed names, so it disagrees with the GLB.
- (export-preview, defect) native_export.py:19/69: meters_per_unit is a host argument that is never checked against scene.unit_settings.scale_length/length_unit or scene['blender_agent_axes'] (set by bootstrap.py:18-22). A scene authored in metres exports 1000x too small with no error.
- (export-preview, defect) native_export.py:207-211: the only nonempty/finite-geometry check is inside `if profiled`. Removing part visibility removes it, and faceless render-visible objects (an unbevelled curve, a mesh of loose edges, :166) then export as LINES (use_mesh_edges=True) or as empty meshes that native_state rejects later with an opaque error.
- (export-preview, defect) Receipt transport across native_export.py:265, preview.py:142-152 (via source_tools.py:34-36) and optical_materials.py:68-92 assumes the community blender-mcp: print to stdout, the 'Code executed successfully: ' envelope and a 'user_prompt' argument. The installed official Blender Lab MCP (blmcp/tools/execute_blender_code.py:24-35, tools_helpers/connection.py:35-38) returns a JSON dict {status,result,message,stdout,stderr} and expects `result = {...}`. Every receipt parser would fail or find nothing.
- (export-preview, defect) native_state.py:30, :187, :232, :240-242 and :258: hard caps (3M per accessor count, ~1M triangles, 32M scalars, 100k rows, 10k primitives) would make validate_spec call a dense model 'invalid'. §2 has no triangle limit, and arview only warns above 300k.
- (export-preview, defect) native_state.py:110-149: the index, attribute-type, alignment, skin and UV/colour/tangent validation sits inside the hashing loop in __init__, so cutting the hashing as the inventory proposes would silently delete it.
- (export-preview, defect) optical_materials.py:37/157/199: thickness_min_nm is required and validated, but Blender never exports it for untextured film (tests/test_blender_agent_optical_materials.py:167-171; receipt note :208). optical_materials.py:209 points Astra at the dropped tools preview_ar/inspect_export.
- (export-preview, defect) tests/test_blender_agent_optical_materials.py:11 pytest.importorskip('agents') at module level: without the agents SDK the whole file skips, including the real-Blender iridescence/volume/texture export proofs (:104-224), giving a false green.
- (export-preview, defect) bsa/archeck.py:66-71: the width fed to the AR harness falls back silently to 145 mm when front_width_mm throws, then is clamped to 60-250. The inventory cited only the clamp, at :69-70; it is at :71, and the fallback is the worse defect.
- (export-preview, defect) main:ar/src/eyewear/optical-material.ts:69 applies frame classification by partRole only when `canonical` (a LENSES_lens_appearance descriptor) is present, and native_export never writes descriptors (native_export.py:3-5; tests/test_blender_agent_native_export.py:127). On main's runtime the Studio's partRole has no classification effect, so the inventory's appeal to optical-material.ts:31-33 for per-role copies is weaker than stated. The real reasons are §2 and WP1.3.
- (export-preview, defect) RULES_AUDIT.md:45 (§C) says the live AR page needs 'Studio part-visibility tags: on every mesh or none'. main and 75ae133 ar/src never read LENSES_part_visibility; only stash@{0}:ar/src/eyewear/optical-material.ts:92 and stash@{0}^3:ar/src/eyewear/part-visibility.ts do.
- (evidence-geometry) A render-pixel to mesh-surface picker. geometry_comparison.py:62-72,340-341,370,413-415 point_queries returned the actual surface point under a normalized render pixel. WP2 landmark refinement, WP4 hide_eyewire x-ranges and WP6 tracing through fitted cameras need a 'pick_surface_point(view, uv)' tool, implemented with scene.ray_cast or a BVH at the WP3b camera in the live Blender. The inventory dropped it silently.
- (evidence-geometry) A Python-to-Blender camera converter plus a parity test. geometry_camera's vertical span/fov and fractional offsets differ from Blender's ortho_scale/sensor_fit/shift, and render_views and compare_to_photos must project identically to the WP3b fit.
- (evidence-geometry) A WP2 replay corpus. The old journals hold real Astra code (27 issued-code.py under Prada 8c82 agent/runs, plus the other jobs), which can seed the scripted fake-client replay with realistic calls instead of invented ones.
- (evidence-geometry) GLB-loader semantics for compare_to_photos and validate_spec. geometry_comparison.py:241 load_glb_bytes(include_zero_alpha=True, require_explicit_geometry=True) included zero-alpha geometry and refused unrealized EXT_mesh_gpu_instancing (test file 165 'Realize GPU instances'). The glasses3d loader and validate_spec (§2: transforms baked) need the same refusal.
- (evidence-geometry) Checkpoint retention plus a fingerprint-based skip. Keep the evidence.py fingerprint complete enough to decide whether the scene changed since the last checkpoint. Keep compression, and keep the last N checkpoints plus every exported candidate.
- (evidence-geometry) Duplicate-reference detection for 'job create'. image_identity's decoded_sha256 (geometry_evidence.py:33; used at geometry_comparison.py:159-166) detects the same photo re-encoded.
- (evidence-geometry) The ledger row's sha256 binding can reuse observations.py:179-191 (hash the GLB, checkpoint and evidence images) once record_review is dropped.
- (evidence-geometry, defect) geometry_camera.py:283-286 + 339-348: yaw is bounded at +/-180 with no wrap. A rear view at the plan's back=180 prior can only move to lower yaw; if the true yaw is past 180 the fit pins at the bound and pose mode discards it. Rear refinement is broken as well as top.
- (evidence-geometry, defect) geometry_camera.py:336: anchor_stats['rms_uncertainties'] is None when no anchor projects validly at the candidate, so 'None > 3' raises TypeError instead of returning a report.
- (evidence-geometry, defect) geometry_camera.py:51-52: the 1600 px cap already affects a real reference: Prada 8c82 images/reference-02.webp is 2048x1365.
- (evidence-geometry, defect) geometry_comparison.py:225,405 + 291-292: source_kind defaults to 'unknown' and the demotion fires for anything other than 'original'. All 9 Prada comparison.json files on disk carry 'Reference provenance is unknown; do not use it to override an original photograph'.
- (evidence-geometry, defect) Disk check of 47 saved comparisons (studio jobs + lenses-recovery/moved): max \|pitch\| 22 deg and yaw never near 180. Prada compared only refs 02/04/05, never 03 (top) or 06 (rear).
- (evidence-geometry, defect) tests/test_blender_agent_geometry_comparison.py:137-144 (test_photo_pixels_never_drive_camera_or_geometric_residual) encodes the photo-pixel ban, and the inventory proposed keeping its idea. It must be dropped or inverted for compare_to_photos.
- (evidence-geometry, defect) geometry_evidence.py:204 write_viewer needs report['series_id'] (the frozen series being cut). Lines 1/146/196 print 'no photo segmentation' text that would be false in the new tool.
- (evidence-geometry, defect) geometry_evidence.py:34-36 image_identity discards alpha (composited onto white, RGB returned), losing a free silhouette mask from cutout photos. panel_sheet (85) raises on an empty panel list.
- (evidence-geometry, defect) evidence.py:110-113,123-124: dispatched=True is set before `executing` is assigned and before the tool call. If the journal write at 111 raises, an UnboundLocalError masks the original error, the status stays 'executing', and a post-snapshot is taken for a call that never ran.
- (evidence-geometry, defect) evidence.py:24,93-94: PROTECTED_TOOLS lists only the mutating tools and passes everything else through. It matches the Community Blender MCP (agent.py:42-43, 349-353). With the official Lab server, execute_blender_code_for_cli and render_*_to_path would bypass the journal, and the 'Rejected by safe mode' prefix (118) would never match.
- (evidence-geometry, defect) evidence.py:192-195,249,267-272: the probe hashes every mesh attribute through Python tuple(array) for source and evaluated meshes, and counts triangles in a Python loop, twice per call. It scales badly once dense numpy edits are allowed.
- (evidence-geometry, defect) The inventory contradicts itself on evidence.py:303: it recommends uncompressed checkpoints while citing disk pile-up. Prada's 27 compressed (zstd) checkpoint pairs already total 122 MB.
- (evidence-geometry, defect) The inventory proposes reusing the post-checkpoint as the next pre-checkpoint. In a live session that is unsafe without a fingerprint compare, because host tools and the owner can edit the scene between calls.
- (evidence-geometry, defect) evidence_json.py:199 + tests 40-54: the allowlist's real purpose was hiding transport logs. A read_report bounded only by the job directory would expose transport logs, receipts and the owner ledger to Astra.
- (evidence-geometry, defect) geometry_comparison.py:416,429 and instructions.py:94: tool docstrings and instructions carry old rules ('never guess a 3D point from a photo', 'record_review', 'Do not copy ... a generated view into physical geometry'). WP7's 'written fresh' applies to tool docstrings too.
- (gates) WP2 deliverable: keep the packed scene.blend paired with model.glb (selection.py:315-316; verified pair from isolated.py:177-193) and record both sha256s in report.json and the owner ledger. The owner's 'changes with notes' loop and resume (studio_jobs.py:636-638) need the editable scene; re-importing the GLB is lossy (split vertices).
- (gates) WP2: delivery must be an explicit 'deliver this checkpoint' action that never falls back to the newest export (studio_jobs.py:660 and optical_quality.py:459-467 fell back to the mtime-newest preview).
- (gates) WP2 --specs schema and a WP7 rule: nominal box dimensions (aperture width, bridge, temple length; e.g. 47/21/145) do not define overall front width (construction.py:50, :250, :317-318; instructions.py:71-72). The inventory dropped this along with construction.py.
- (gates) WP2 $0 proof: keep PersistentOfflineBlender (tests/test_blender_agent_construction.py:22-70). It is the only real-Blender test double that keeps active-file identity across calls (OfflineBlender spawns a new Blender per call: _blender_quality_fixture.py:21-42), and checkpoint/restore tests and the fake-client replay need it. Move it with ROUGH_PRODUCT and adapt it to WP0's transport.
- (gates) WP2: a light pre-mutation snapshot around every Astra execute_blender_code. In the Studio a plain execute_blender_code was unprotected: agent.py:266-271 only blocks tools while a transaction is pending, and checkpoints existed only inside host tools. The full reopen/re-export verification (isolated.py:177-193) belongs at checkpoint and deliver time only.
- (gates) WP2 transport: rewrite call_code and the receipts for WP0's chosen MCP server (official: {code} -> result dict). The real-MCP isolation witness (tests/test_blender_agent_mcp_isolation.py:32,64) currently requires the community mcp-for-blender.exe.
- (gates) WP3: read the legacy keys scene['mdl_bridge_underside'] and '+Y up; +Z front' when importing old Studio scenes (seed_prepare.py:249-250; native_export.py:63-66). validate_spec should check geometrically that the origin sits at the bridge underside, because the Studio auto-set the marker to [0,0,0] (isolated.py:275).
- (gates) WP4 regression: first export agent/scene.blend of Oakley 3dff8674 and INVU c8e81c72 and compare its fingerprint with the final GLB. Use seed_prepare only if they differ, and then weld split vertices (preserving custom normals) or make seat_lens and check_lens_seating tolerate glTF split vertices.
- (gates, defect) source_tools.py:34,42 plus venv blender_mcp/server.py:736: the transport is tied to the community mcp-for-blender 2.1.3 protocol (envelope prefix, a 'user_prompt' argument, stdout MARKER receipts). The official Blender Lab server's execute_blender_code takes only {code} and returns a 'result' dict, so the transport and every generated receipt need rewriting, not an envelope tweak.
- (gates, defect) venv blender_mcp/safe_mode.py:129-131 bans numpy, and :23-30 allows save/open of .blend files, every import/export operator and datablock .load(). call_code (source_tools.py:40-41) and IsolatedScene.run (isolated.py:135) enforce exactly the policy PLAN WP2 inverts. Host scripts need file I/O, so WP2 needs separate host and Astra channels.
- (gates, defect) isolated.py:86-87: restore_code (source_tools.py:79-86) makes the checkpoint the active file, so a later save_mainfile overwrites the recovery copy. Only run() and activate() re-save, by string-replacing 'copy=True' (isolated.py:117,173).
- (gates, defect) construction.py:302: the inventory's 'check can never fail' claim is wrong. NativeState raises on non-finite data at construction.py:160 (native_state.py:289-290, 343-344), so the hard-coded True is redundant, not a silent pass.
- (gates, defect) construction.py:21,282 -> inspect_export.py:39 -> part_visibility.py:35-36 'from reconstruction.mesh import': a transitive runtime dependency on automation/reconstruction that the inventory did not list.
- (gates, defect) episodes.py:136-141: checkpoint() checks pending and takes the packed copy before isolated.run acquires isolated.lock (isolated.py:139), a check-then-act race. One checkpoint costs about 10 Blender round trips (isolated.py:156-206).
- (gates, defect) episodes.py:328-332: closeout cost is max(Astra estimate, the budget's next reservation) with the budget guard authoritative. The inventory's claim that it 'trusts Astra's estimate' is overstated.
- (gates, defect) optical_quality.py:75-81: latest_expectations picks the newest report by self-reported created_at, not by file mtime as the inventory says. It is still an implicit latest-wins choice.
- (gates, defect) seed_prepare.py:134 with Blender 5.2 io_scene_gltf2/__init__.py:1930-1939 (merge_vertices default False, cannot merge different normals) and seed_prepare.py:256-258 (no topology change allowed): GLB-imported meshes have open boundaries at every UV seam and hard edge, which breaks boundary-based seat_lens and closed-part checks on seed_prepare output.
- (gates, defect) seed_prepare.py:80-81 -> native_export.py:177-189: the round-trip export raises on any GLB where only some meshes carry LENSES_part_visibility (Studio tag gate). native_export.py:107-118 also drops partRole values other than frame/temple/lens.
- (gates, defect) The inventory's claim that the Oakley 3dff8674 and INVU c8e81c72 finals 'exist only as GLBs (PLAN §6)' is wrong. Both jobs have agent/scene.blend and checkpoint*.blend. Oakley's scene.blend (12:32:29) post-dates its final GLB (12:27:19) by 5 min; INVU's (13:04:09) pre-dates its final GLB (13:05:57) by 2 min.
- (gates, defect) isolated.py:275: _EMPTY auto-sets scene['mdl_bridge_underside']=[0,0,0] in every scene built from empty (ROUGH_PRODUCT does the same), so the Studio 'bridge marker' is a default, not a measured point, and native_export's missing-origin error (native_export.py:63-64) never fires.
- (gates, defect) source_tools.py:240-241 with native_state.py:150-155: the 'only the material changed' check would refuse adding a UV layer, so it cannot serve unchanged as the model for WP5's attach helper, which adds a dedicated UV layer per part.
- (gates, defect) source_field.py:335-336: with no base texture, the whole texture starts at the palette's dark endpoint. source_field.py:450 reports alpha_preserved True unconditionally.

## 6. Runtime dependencies outside blender_agent (to port or replace)

These modules sit OUTSIDE blender_agent/, are not in any inventory, and are still required at runtime by files marked keep or rewrite. modeler/agentic/pricing.py (194 lines) is imported at budget.py:25, trial_budget.py:17, agent.py:260 and :330, and by the kept test test_blender_agent_budget.py:15. bsa/archeck.py is imported at preview.py:22, portrait.py:10 and geometry_comparison.py:18; bsa/core.AUTOMATION at portrait.py:11. reconstruction/mesh.py load_glb_bytes is imported at geometry_comparison.py:19, studio_fit.py:18 and part_visibility.py:35. blender_mcp.safe_mode (community MCP package) is used at isolated.py:130 and source_tools.py:40. The mcp-for-blender addon is used at session.py:54, and agent.py:528-529 hard-codes the path automation/data/blender_agent/venv/Scripts/mcp-for-blender.exe. Node harnesses: ar/qa/geometry-comparison.mjs (geometry_comparison.py:25,:178; present only in stash@{0}^3), ar/qa/portrait-preview.mjs plus fixtures/face-a.jpg (portrait.py:13-14,:100), and the provider-comparison harness reached through archeck.run (preview.py:120,:188). PLAN WP1 deletes the provider harnesses, so preview.py would lose its renderer.


## 7. The `ar/` fork (arview, WP1)

Filled in WP1: per-file decisions for 75ae133 + the chosen stash@{0} edits, the throw-site classification and the
list of every copied file. Diff stats recorded in WP0:

- `git diff --stat main 75ae133 -- ar/`: 45 files, +2362 / -138 (main is the merge-base, so 75ae133 is strictly ahead).
- `git diff --stat 75ae133 'stash@{0}' -- ar/`: 20 files, +350 / -87 (optical-material, continuity, eyewear-shadow,
  hair-occlusion, rear-drop, renderer, temple-clip, temple-terminal-fit, temple-visibility, main, README, 4 qa files,
  5 tests).
- `stash@{0}^3` (untracked when stashed) under `ar/`: 25 files (qa diagnostics, `src/eyewear/part-visibility.ts`,
  `tests/part-visibility.test.ts`).

## 8. Plan prototypes (`plan/prototypes/`)

Reference code from the planning session, not pipeline code: `projection/` (make_uv_test, probe_fit, probe_project,
glbload) and `ar_capture/` (seat_copy, rim_split, junction_geom, band_metrics, wire_metric, glbmesh, run.mjs, page.js,
configs). They still import `automation/bsa` and `ar/node_modules` at runtime, which is allowed for prototypes only.
WP0 repointed their paths to `glasses3d/data/reference/` and reproduced make_uv_test's coverage exactly. Known
prototype defects (do not port as is): `glbmesh.meshes()` ignores node transforms; `glbload` has no sparse-accessor or
external-buffer support. The new host reader is `g3d_host/glb.py` (written fresh, tested).
