# WP2 design: the live Blender session and the Astra loop (final)

Written 2026-10-07 by the judge of the WP2 design panel (workflow g3d-wp2-design). Design only: $0, no OpenAI call, no repo file
changed except this one. Inputs: PLAN §0/§2/§4 (WP2, WP7, WP8); PORT_REVIEW §1/§2/§2b/§3/§5 and `port_review/studio.json`; the
WP2 inventory; three panel designs ("worker", "loop", "jobs") with prototypes under
`C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp2-design/` (`worker_angle/`, `loop/` + `worker/`, `worker_proto/` + `sdk_proto/`).

> **Historical design; status updated 2026-10-18.** Assembly M0–M4 implemented stages D–F, trusted `g3d_call`,
> delivery and the private temple stage. The shared paid-run lock is `agent/budget.py:PAID_RUN_LOCK_ROOT`.
> M11 tasks 1–8 repair recovery, evidence, renewal and specialist behavior. Use ASSEMBLY, REVIEW_20261018 and
> PROGRESS for current work. §10 is a capability map, not the live callable signature/schema; those live in
> `agent/tools/` and `agent/temple_lab/tools.py`. §11's proposed CLI is superseded by `agent/README.md`.

## 0. Verdict

- **PORT_REVIEW §1 holds; every risk it listed is now measured here** (own headless worker, stock SDK `Runner`, Studio
  BudgetGuard): on Prada, worker ready 1.5-2.6 s, checkpoint 11-33 ms, verified restart 1.6-2.5 s, EEVEE `-b` 0.32-3.4 s/frame,
  camera parity ≤0.0012 px, the real SDK over `httpx2.MockTransport` with our hooks and 0 network.
- **Base: "loop"** (highest score, §18) for the money path, resume and crash consistency. Grafted: "worker"'s worker, checkpoint,
  render and process internals; "jobs"' owner layer and the Prada exact-usage proof.
- **Safe mode passes Astra's real code once file I/O becomes tools.** Of 214 Astra-written snippets (445 journaled; the rest
  host-issued) every failure was a file-I/O line: 49% saved `.blend` files by hand, 26% rendered their own inspection images, a
  few loaded fonts or wrote text blocks. Hence `save_checkpoint`, `render_views` (custom cameras), `load_font`. With only those
  lines removed, chained replays rebuild the accepted scenes exactly: Oakley 21/21, INVU 20/20, Prada 19/19.
- **Defects found by the prototypes (handle, never inherit):** (1) `export_glb.snapshot` is not restore-stable (VIEWER "Render
  Result" image, filepath); (2) pixel edits on an unpacked generated image are lost on save; (3) every save drops zero-user
  datablocks (9/51 Studio replays failed on unassigned materials); (4) Blender leaves no Python SIGINT handler under `-b`, so
  `_thread.interrupt_main()` does nothing until we install one; (5) numpy C calls ignore the soft timeout (51.65 s vs 1 s), so a
  hard kill is mandatory; (6) the community mechanics let `from numpy import load` through; (7) a worker with stdout on an unread
  PIPE hangs once EEVEE's log fills it. SDK facts: 0.22.3 persists whole turns only (a crash mid-tool loses the paid turn from
  the session while its scene effect remains); an `incomplete` response raises `ModelBehaviorError` (FT1119 quality-001 peaked
  at 7,992/8,192 output tokens).

## 1. Conflicts resolved

| Question | Decision | Why |
|---|---|---|
| One Blender process or live + viewer | **One worker per run**; renders use a temporary scene in it; an older checkpoint is exported by the existing one-shot `g3d_host/blender.export_copy` | The temporary scene is measured fingerprint-neutral and a checkpoint follows every mutating call, so a render crash costs a 2 s restart and no work. A second process is a second lifecycle |
| Checkpoint before or after a call | **After, fingerprint-gated**; call N's head is call N+1's `checkpoint_before` | One save per change; the fingerprint compare makes reuse safe (PORT_REVIEW evidence-geometry defect) |
| What a failed call leaves | **Any non-ok result (error, timeout, refused) leaves the scene at `checkpoint_before`** | Same as a hard kill; Astra always knows the scene state |
| Camera convention | **`sensor_fit='AUTO'`; lens, shift and `ortho_scale` in units of `big = max(W,H)`; `shift_x = -(cx+0.5-W/2)/big`, `shift_y = (cy+0.5-H/2)/big`** | Both prototypes are self-consistent (HORIZONTAL with everything in widths also works); AUTO is the one tested on portrait, ortho and pitch-89.5° cameras. The parity test arbitrates |
| Worker environment | **Allowlist**: SYSTEMROOT, WINDIR, TEMP/TMP → run temp, PATH, PATHEXT, COMSPEC, NUMBER_OF_PROCESSORS, USERPROFILE, LOCALAPPDATA, APPDATA, `G3D_WORKER_TOKEN`, `PYTHONDONTWRITEBYTECODE` | Stronger than stripping `*_API_KEY/_TOKEN/_SECRET` |
| Orphans | **Runner-owned kill-on-close Job Object** (Blender, node, pools); PID + create_time + exe + `--receipt` cleanup as second line | Measured: the child died with its killed runner |
| Settlement | **Every 200 response settled from its own JSON in the transport hook**; `on_llm_end` only cross-checks | Covers every path where the SDK raises first; the Studio did it only for `incomplete` (agent.py:326-339) |
| Paid gate | **Single-use approval bound to a request sha** (`estimate` → `approve` → `run`) | Joins the three designs' grants and stale-input check |
| Stranded cap | **No paid closeout, no `BudgetAwareModel`**; delivery is a $0 host action on any checkpoint | A budget stop never strands a deliverable; simpler |
| Output cap default | **16,384** (engineering call, recorded per run) | Observed peak 7,992/8,192 and an incomplete ends a turn; costs nothing unless used; tail hold +$0.41 |
| `max_turns` default | **60** | Accepted runs used 56-59; median growth reaches ~164k tokens at turn 60 (p90 ~229k), below the 272k price step |
| Proof product | **Prada seeded with its frozen basis and 59 recorded usages (A); synthetic from-zero job for edges (B)** | Exact real money arithmetic, real geometry, known-truth camera fit |

## 2. Processes and channels

```
python -m glasses3d job run <id> [--detach]                      glasses3d/.venv (Py 3.12)
 └─ runner agent/loop.py  (EcoQoS opted out; kill-on-close Job Object; keep-awake; heartbeat thread)
     ├─ Runner → OpenAIProvider → AsyncOpenAI(http_client=DefaultAsyncHttpxClient(transport=None|MockTransport,
     │        event_hooks: ReviewLog.before_request [BudgetGuard reserve → archive] / after_response [archive → settle]))
     ├─ function tools agent/tools/* ──WorkerClient── TCP 127.0.0.1:<ephemeral>, token, LP-JSON ──┐
     │                              ── host: fit_views pool (14), raster/compare, spec, seat_check, photoprint
     │                              ── node qa/capture.mjs | qa/serve.mjs (cwd glasses3d/arview)
 blender.exe -b --factory-startup --disable-autoexec <head>.blend --python glasses3d/g3d/worker.py -- --receipt R --root J
     (in the Job Object; EcoQoS opted out via handle; stdout/stderr → worker.log, never a PIPE; cwd = empty temp dir)
```

Astra's code arrives only through `exec` and is always validated. Trusted host work (checkpoint, restore, export, render, pick,
print, font) calls named worker methods with JSON parameters and never sends code text: there is no `exec_trusted(code)` hole.

## 3. Worker (`g3d/worker.py`, `g3d/rpc.py`, `agent/worker_client.py`, `agent/procs.py`)

**Startup.** Token = 32 random bytes in `G3D_WORKER_TOKEN`; the worker pops it and keeps only its sha256, binds `127.0.0.1:0`
with `SO_EXCLUSIVEADDRUSE`, and writes `receipt.json` atomically (`g3d.worker/1`: port, pid, create_time, blender, python, file,
token_sha256, gpu {vendor, renderer, backend}, 64×64 EEVEE probe {seconds, nonempty_px}). The client checks pid == `Popen.pid`
and create_time. The first frame must be `{"method":"hello","token":T}` (`hmac.compare_digest`); a bad token closes only that
connection; after the first authenticated client the listener closes. Ready timeout 60 s, then the Job Object kills the child
(fixes the session.py:88-99 orphan). **Frames:** 4-byte big-endian length + UTF-8 JSON, ≤64 MiB, `allow_nan=False`; request
`{id, method, params, deadline_s}`, response `{id, ok, result | error{type, message, traceback}, elapsed_s}`; ids strictly
increase and must match. Images never cross the socket: methods write under `--root` and return paths + sha256.

| Method | Channel | What |
|---|---|---|
| `hello`, `ping`, `shutdown` | — | handshake, liveness, clean exit |
| `exec{code, soft_timeout_s}` | Astra; validated on host and in worker | §3.1 |
| `scene_summary`, `fingerprint` | trusted, read-only | §5 |
| `checkpoint_save`, `checkpoint_restore` | trusted | §5; the only `wm` file operators in the system |
| `export_glb{path}` | trusted | `g3d/export_glb.export_glb`, path confined to `work/exports/` |
| `render{spec}`, `pick{rays}` | trusted, read-only | §6; render asserts the fingerprint unchanged; pick = `scene.ray_cast` on the evaluated depsgraph |
| `g3d_call{op, kwargs}` | trusted, allowlisted names | WP4 `seat_lens, hide_eyewire, unseat_lens, unhide_eyewire, check_lens_seating`; `configure_lens_coating`; gated (§10): WP6 temple ops and WP5 `print_uv, attach_print, detach_print, remove_print_uv` (report paths confined to `work/prints/`) |
| `load_font{family}` | trusted | allowlist under `C:/Windows/Fonts` (arial, arialbd, times, timesbd, calibri, segoeui), packed |
| `exec_unvalidated` | `--selftest` only | tests the runtime layer; a test asserts it is refused in run mode |

**3.1 Snippets.** Fresh namespace per call: restricted builtins, `__name__="__g3d_snippet__"`, prebound `bpy, bmesh, mathutils,
np`, the `g3d` facade, `result=None`. Python variables do not persist between calls, only scene state (as in the Studio,
README.md:89-91); durable notes go in scene custom properties. Returned: `status` ok|error|timeout|refused|refused_runtime;
`result` (JSON, mathutils/numpy converted, ≤20,000 chars); `stdout` ≤20,000 and `stderr` ≤5,000 with truncation flags;
`traceback` (last 6 frames, snippet line numbers); `violations[{line, message, use_instead}]` (all); `scene_delta {added, removed,
changed[{object, vertices, triangles, bbox_mm before/after}], materials_added/removed}`; `checkpoint`, `restored_to`, `seconds`.
**Soft timeout:** the worker installs a SIGINT handler (none exists under `-b`) that raises `SoftTimeout` only while an exec is
armed; a `threading.Timer` calls `_thread.interrupt_main()` (measured 2.02-2.03 s on 2 s); the done flag is set before result
formatting and the serve loop swallows a late interrupt. **Hard timeout:** host socket deadline → Job Object kill → restart;
mandatory because C calls ignore the soft timeout.

**3.2 Kill and restart.** Triggers: hard timeout, EOF, process exit, frame error, the CPU-idle rule (§7). Kill the Job Object,
start a new worker on the head checkpoint, verify `fingerprint == head.fingerprint` (else the parent), and return `{status:
"worker_restarted", lost_call, restored_to, reason}` (1.6-2.5 s). At most 2 automatic restarts per call (then "this code hangs
Blender; scene at cNNNN", not retried); 3 restarts in 10 min end the run `failed_worker` at the verified head. A mutation is
never replayed blindly (upstream issue #279).

## 4. Safe mode (`g3d/safe_mode.py`, stdlib only, importable on host and in Blender)

**Mechanics** from community `safe_mode.py:477-975` (MIT, notice kept: `_attr_chain`, `_path_is_blocked`, `_collect_bindings`,
`_Validator`, `_guard_source`, `_check_depth`), changed to: collect **all** violations; canonicalise `import numpy as np` (bpy
aliases stay denied); lambda allowed as a value, never a call target; deny any `_`-prefixed attribute; check from-import names
against the denied-attribute set too (closes the `from numpy import load` hole); `.format` only on a literal whose fields reach no
`_` name; limits 200 KB, 40,000 AST nodes, depth 32.

**Policy.**
- Allowed modules: bpy, bmesh(.ops/.types/.utils/.geometry), mathutils(.geometry/.bvhtree/.kdtree/.noise/.interpolate),
  numpy(.linalg/.random/.fft/.polynomial/.ma/.lib.stride_tricks), math, cmath, random, colorsys, json, itertools, functools,
  collections(.abc), statistics, string, re, enum, dataclasses, typing, decimal, fractions, textwrap, unicodedata, copy, heapq,
  bisect, array, time, bpy_extras(.object_utils/.mesh_utils/.view3d_utils), and `g3d.lib`: pure geometry only (`seat_lens,
  hide_eyewire, check_lens_seating, unseat_lens, unhide_eyewire` — `seat_bpy` does no I/O — `loft_sections, polygon_section,
  check_round_corners`). Helpers with internal I/O are trusted methods only (inventory F4).
- Denied names, anywhere in a chain and as from-imports: numpy load, save, savez*, savetxt, loadtxt, genfromtxt, fromfile, tofile,
  memmap, open_memmap, fromregex, DataSource, dump, ctypes, ctypeslib, f2py, testing, distutils, show_config, show_runtime, info,
  get_include, `lib` (except stride_tricks); bpy save_render, save_as, pack, unpack, reload, save_mainfile, save_as_mainfile,
  open_mainfile, preferences, environ; the community persistence list (driver_add, handlers, timers, texts, libraries,
  register_class, as_pointer, …). **Not `dumps`:** a bare-name rule hit `json.dumps` 47 times.
- Denied operators: `bpy.ops.{wm, render, image, file, sound, clip, sequencer, cachefile, screen, buttons, text, script,
  preferences, extensions, console, ed, ptcache}`, `import_*`, `export_*`, `font.open`, `node.add_file`, `object.bake_image`,
  `outliner.lib_operation`. Denied paths: `bpy.app.{driver_namespace, handlers, timers, binary_path, tempdir}`, `bpy.utils`,
  `bpy.data.{texts, libraries}`, `bpy.props`, `bpy.msgbus`, `bpy.context.preferences`, `bpy.path`. Denied stores: filepath,
  filepath_raw, directory, filename, expression, script, use_autopack, use_scripts_auto_execute. Denied keyword arguments on any
  call: filepath, filepath_raw, filename, directory, files, relative_path.

**Runtime layers** (the static walk is not a security boundary): (1) restricted builtins without open, eval, exec, compile,
`__import__`, globals, vars, dir, memoryview, super, object, input, breakpoint; (2) guarded `__import__` and wrapped
get/set/has/delattr checking computed names against the same deny sets (measured: `getattr(bpy.data, 'te'+'xts')` caught);
(3) a `sys.addaudithook` armed only during Astra code on the main thread, refusing write opens anywhere, read opens outside the
Blender install, stdlib and `g3d`, and subprocess, socket, ctypes and winreg events; (4) a post-call active-file and save-handler
check that turns C-level `.blend` I/O into `refused_runtime` (the host restores `checkpoint_before`, deletes the stray file and
records its path); (5) the secret-free environment, job-root path confinement, read-only checkpoints, an empty cwd.

**Refusal hints** name the replacement: save → automatic checkpoints / `save_checkpoint` / `restore_checkpoint`; render or
`.filepath` → `render_views` / `compare_to_photos`; `export_scene` → `export_glb` / `deliver`; `fonts.load` → `load_font`;
`texts.new` → `scene['notes']`; `img.save` → `img.pack()`.

## 5. Checkpoints (`g3d/checkpoint.py`, `agent/journal.py`)

**Fingerprint.** Refactor `g3d/export_glb.snapshot` (:198) into `scene_state()`, a dict of component digests (scene props and
units, view layers, collections, per-object matrix/visibility/modifiers/slots/custom props, per-mesh `_mesh_digest`, curves,
materials, node groups, image pixels); `fingerprint = sha256(canonical JSON)` after `view_layer.update()`. Excluded:
`bpy.data.filepath`, images with `source == "VIEWER"`, `use_fake_user` flags (defect 1, verified). The dict yields each call's
`scene_delta`. Cost 16-50 ms on Prada; add a counts + coordinate-hash fast mode if a dense scene exceeds 0.5 s.

**Per mutating call** (snippets, g3d calls, gated print calls, fonts, restores): (1) `fp0 = fingerprint()`; if it differs from the
head, save a `drift` checkpoint and log a defect. (2) Run. (3) If ok, **protect**: pack every generated, dirty or pixel-edited
image (defect 2) and set `use_fake_user` on zero-user materials, meshes, node groups, images, curves, textures, fonts, lights and
cameras (defect 3), both listed in the receipt; then `fp1`; if `fp1 == fp0` the call was read-only and nothing is saved, else
save `c(N+1)` as head. (4) If not ok, restore `checkpoint_before`, verify, return `restored_to`. About 0.15 s per mutating call
(the Studio paid 0.64 s).

**Files.** `checkpoints/cNNNN.blend` via `save_as_mainfile(copy=True, compress=True)`: `.part` → `replace_with_retry` → chmod
read-only → sha256; `index.jsonl` rows `{id, parent, call_id, label, fingerprint, sha256, bytes, packed_images, fake_user_added,
created}`. No code path calls `save_mainfile`; restore opens the read-only checkpoint itself (`open_mainfile(load_ui=False,
use_scripts=False)`; the socket survives, measured) and later `copy=True` saves leave it untouched (measured), which defuses
isolated.py:86-87 twice. **Retention:** keep everything during a run (2.4 MB each at Prada size, ~70-150 MB per run); owner-run
`job prune` keeps c0000, labelled ones, the last 10 and everything an export, deliverable or ledger row references.

## 6. Cameras, renders, comparison, picking

**Converter** (`g3d_host/blender_camera.py`, numpy only): WP3b `Camera{yaw y, pitch p, roll r, perspective k, scale s, center_x,
center_y}` + `NormFrame{C, E}` + scene origin `o` (`mdl_bridge_underside`, mm). right = (cos y, 0, −sin y); up = (−sin p sin y,
cos p, −sin p cos y); toward = (cos p sin y, sin p, cos p cos y); e_u = cos r·right − sin r·up, e_v = sin r·right + cos r·up;
rotation columns (e_u, e_v, toward). Perspective: location = C + o + toward·E/k, `lens = (s/k)·36/big`. Ortho (k = 0): location =
C + o + toward·4E, `ortho_scale = big·E/s`. Native W×H; integer pixel = pixel centre; crops via `cameras.resized_camera`.

**`render`** (worker, trusted, read-only): a temporary `g3d_render` scene links the user collections and loose objects (same mm
units; own camera, world and lights; `film_transparent`; Standard view transform; RGBA PNG), is deleted on exit, and the
fingerprint is asserted unchanged. Passes: `native`; `clay` (override, grey, roughness 0.55); `normal` (world normal ×0.5+0.5
emission); `silhouette` (alpha); `parts` (host raster of the cached export at the same camera, so no pass indices are mutated).
Light rig `photo_v1` (world 0.8 + 2 area lights), versioned. EEVEE (`BLENDER_EEVEE`, 16 spp); Cycles CPU 16 spp automatically if
the probe sees a software renderer or EEVEE throws; engine recorded per image.

**`render_views(views, passes=("native","clay"), size_px=1024, background, target)`**: canonical ortho views framed to the
render-enabled bbox +5% with px/mm (front, rear, left, right, top, bottom, angled_left, angled_right); `reference-NN` at the frozen
basis camera; custom `{yaw, pitch, target_mm, width_mm, perspective, roll}` (replaces Astra's own inspection cameras); `target` =
part or `point_mm` + `radius_mm`. Returns one labelled contact sheet per ≤6 views (≤2048 px), frame paths and a manifest
{camera, engine, gpu, seconds}.

**Basis.** `fit_cameras(reason, refs=None)` exports the head and runs `fit_views(glb, images, views, cameras/basis-K, workers=14)`
(394-447 s on 16 cores); it freezes `cameras.json`, NormFrame, front pivot, skip, front_parts, photo mattes (computed once),
overlays and `basis.json {model_sha256, reason, call_id}`, and reports its residuals. A refit is basis-K+1; metrics and deltas
compare only within one basis (the GEOMETRY_COMPARISON frozen-baseline lesson).

**`compare_to_photos(refs="all", parts="all"|"front", passes, crops=None, size_px=1024, basis=None)`**: export the head (cached by
fingerprint); `fitmesh.load_fit_mesh(glb, skip=basis.skip, frame=basis.frame)` (`fitmesh.py:73`; the frozen frame is mandatory,
the default recomputes it per revision); per reference, host raster mask at the frozen camera vs the cached matte → IoU, contour
mean/p90 in px and mm at the frozen pivot (`fit_views.contour_summary` definitions), `model_only_px`, `photo_only_px`, for all
parts and front parts; deltas vs the previous compare on the same basis with separate `model_bytes_identical` /
`geometry_identical` flags; one image per reference: photo | native | clay | overlay (photo edge green, model edge magenta);
crops from a part's projected bbox +15% or `box_px`; full JSON in `work/compare/<call>/report.json`. Top and rear are in by
default (a 3 mm temple splay moved top IoU 0.091, side 0.006). Without a basis: `E_NO_BASIS`, naming `fit_cameras`. Cost:
metrics 1.0 s + renders 10-25 s.

**`pick_surface_point(view, points[[u,v]] ≤64)`**: the host builds rays through pixel centres at the frozen, canonical or
manifest camera; the worker ray-casts; returns `{hit, object, partRole, point_mm, asset_mm, normal, face, distance}` (for
`hide_eyewire` ranges and WP6 tracing).

## 7. Loop (`agent/loop.py`, `agent/receipts.py`, `agent/watchdog.py`)

**Construction** (Studio agent.py:371-514 plus one seam, `model_clients(run)`). Production: `AsyncOpenAI(base_url=
"https://api.openai.com/v1", max_retries=0, timeout=900, http_client=DefaultAsyncHttpxClient(event_hooks=...))` plus a separate
counting client (timeout 60), built only after an approval is consumed (§8), key from an explicit dotenv path or the environment,
never printed. Offline: the same classes over `httpx2.MockTransport`, refused unless `G3D_OFFLINE=1` and the key starts
`sk-offline-`. Guard, ledger, hooks, model class and tools are identical, so the proof exercises everything but the socket.
`Agent(name="Astra", model="gpt-6-astra", instructions=WP7, tools=registry.available())`; `ModelSettings(parallel_tool_calls=False,
max_tokens=run.max_output_tokens, retry=ModelRetrySettings(max_retries=0), extra_body={"service_tier":"default"},
reasoning=Reasoning(effort=run.effort, summary="auto"))`; `Runner.run(..., max_turns, session=SQLiteSession("astra",
job/session/session.sqlite), hooks=ReviewLog, run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False,
tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1), model_provider=OpenAIProvider(openai_client=client,
use_responses=True, use_responses_websocket=False)))`; `set_tracing_disabled(True)`. Preflight requires the two-way contract: every tool named in WP7 is registered,
every registered tool is described, gated tools are neither, and no removed phrase appears in instructions, brief or docstrings.

**Statuses** (`result.json`): completed, turn_limit, budget_limit, stopped, provider_rejected (4xx, $0), provider_unknown (5xx,
non-JSON, connection error or timeout; full hold kept), output_limit, failed_worker, failed (with the exception type). The exit
checkpoint is the head with its sha re-verified; the worker shuts down only after that. **Incomplete:** settled from the
transport; the SDK raises; the loop runs up to 2 continuation `Runner.run` calls on the same session with the host note "Your
previous response was cut off at the output limit and was not recorded. Continue." (each counts against `max_turns`). No tool
ran for the lost turn, so the scene is unchanged: a new request after a settled outcome, not a retry of an uncertain one.

**Resume and crash consistency.** Inputs are planned with the kept `plan_session_input` against `session.get_items()` (a resume
sends 0 images and no unchanged brief); unsent owner notes are appended verbatim, their shas recorded in `input-receipt.json`
(each sent once). **Rollback rule:** let L be the call id of the last `function_call_output` in the session; if any journaled
mutating call follows L, restore the `checkpoint_before` of the first such call and say so in the invocation text ("Your previous
run ended during `<tool>` (call `<id>`); its effect was rolled back; the scene is checkpoint cNNNN."). Backstop: append an
"interrupted" output to any dangling `function_call` (0.22.3 never leaves one; a test pins that). The worker always reopens
exactly the checkpoint the journal names, verified by sha and fingerprint (studio_jobs.py:534-537).

**Stop, heartbeat, stalls.** `job stop` writes `stop.requested`; `ReviewLog.check_stop` cancels at the next hook or tool
boundary; the loop takes the exit checkpoint and writes `result.json`; Ctrl-C writes the stop file and waits, a second Ctrl-C
kills after one checkpoint attempt. A daemon thread writes `heartbeat.json` every 2 s (`{state: model_wait|tool:<name>|idle,
since, request_seq, tool_call_id, worker_pid, worker_cpu_s, context_tokens, settled_usd, reserved_usd}`); any state ≥30 s prints
one line, repeated every 30 s; `job status` says "runner not responding" past 30 s. The SDK `function_tool` timeout is hard + 10 s
(verify the exact `timeout_behavior` value on 0.22.3 so Astra receives an error result); a tool still running at hard + 30 s is
killed through the Job Object. **CPU-idle rule:** the worker or a host subprocess tree (node with its Chromium children, pools)
with no reply and <0.5 s CPU gained in 60 s is killed and reported (60-75 s). **Model wait:** reported from 120 s, cancelled at
the 900 s client timeout (observed maximum 165 s); the hold becomes `unknown`, status `provider_unknown`, no automatic paid retry.

| Hard timeout (s) | execute_blender_code | seat/hide | check_lens_seating | render_views | compare | export_glb | checkpoints | arview_capture | fit_cameras | project_photo_texture | deliver | pick, read_*, scene_summary |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | soft + 30 (soft 120, 0.5-600) | 150 | 120 | 300 | 300 | 120 | 60 | 300 | 1,800 | 1,800 | 600 | 30 |

## 8. Budget (`agent/pricing.py`, `agent/budget.py`, `agent/estimate.py`)

**Pricing:** byte copy of `automation/modeler/agentic/pricing.py` (161 lines, main 7e25722, astra only, no luna) with tests: per
million tokens input $10, cached $1, cache write $12.5, output $50; above 272k input ×2 input/cache and ×1.5 output; integer
micro-USD, every division rounding up; `reserve_micro` = all input at the cache-write rate + the full output cap.

**BudgetGuard** (Studio budget.py:80-331 kept: count, worst-case reserve, persist, then send). Refusals unchanged: anything but POST
`https://api.openai.com/v1/responses`; retry count ≠ 0; model ≠ gpt-6-astra; `service_tier` ≠ default; stream or background;
fields outside `COUNT_FIELDS`/`CREATE_FIELDS` (now named in the error; 0.22.3 sends `include, input, instructions,
max_output_tokens, model, parallel_tool_calls, prompt_cache_key, reasoning, service_tier, tools`, all allowed); non-function
tools; missing `max_output_tokens`. Edits: (1) ledger `g3d.run_budget/1`, rows `{sequence, time, input_tokens, max_output_tokens,
reserved_micro_usd, outcome: held|settled|rejected_4xx|unknown|reconciled|not_sent, request_id, response_id, http_status,
settled_micro_usd, usage, settled_from: transport|owner|host, reason}`; `not_sent`/`host` records a request-hook failure
after reservation but before HTTP send at $0 and forbids retry in that run; failed persistence retains its hold.
(2) `after_response` settles every 200 that names gpt-6-astra
with an id and status completed or incomplete from its own usage, and `on_llm_end` cross-checks id and usage, blocking on a
mismatch; disagreement with the counted input records actual cost and blocks later paid work. (3) 4xx (429 included) →
`settle_rejected` at $0; 5xx, non-JSON, connection error or timeout → `mark_unknown` with the
full hold; (4) one open request: `before_request` refuses while the previous row is `held`; (5) `planning()` returns numbers only
(the prose at budget.py:165-169 steered Astra, RULES_AUDIT); (6) owner reconcile: `reconcile_unknown(seq, owner_words, usage |
no_charge)` (modeler/agentic/budget.py:179-214, :289-310) and `reconcile_from_transport(seq)`, which settles from an archived 200
with validating usage and matching id (on Oakley run 1 it would have released $0.67 of a $0.707 hold); (7) Windows replace retry
.01, .025, .05, .1, .2, .4, .8, 1.6 s (~3.2 s; the Studio's 0.8 s failed on Oakley run 1).

**JobLedger** (rewrite of trial_budget.py, `budget/job-ledger.json`): non-blocking OS lock (trial_budget.py:61-79) plus a
machine-wide `data/jobs/.paid-run.lock {pid, create_time, exe}` re-checked with psutil (one paid run at a time); totals rebuilt
from `runs/*/budget.json`, re-priced and tamper-checked (:165-210); unknown holds count against the job until reconciled; a run
dir without `budget.json` counts $0 with no transport files (`pending_approval` when it has an estimate, otherwise
`aborted_before_paid`) and blocks the job until reconciled with
some (no oakley-scratch-001 lockout). Order: (1) $0 preflight: job files and image decodes, instructions/tool contract, key
present, worker started on the expected checkpoint (c0000 saved and verified), arview built, disk space; (2) consume the
approval and create `budget.json` by exclusive create (`open('x')` + fsync) in one transaction; (3) only then the first POST.

**Approvals.** `job estimate <id>` writes `runs/<new>/approval-request.json` (estimate, range, suggested cap, effort,
`max_output_tokens`, `max_turns`, and `request_sha256` = sha of job.json, input shas, unsent owner notes, instructions sha,
tool-registry sha, settings). `job approve <id> --run R --max-usd X --owner-words "<the owner's exact chat words>"` writes
`approval.json`. `job run` exits 3 with 0 requests when the approval is missing, stale (sha), consumed or below the requested
cap. The default cap is the owner's approved amount, which may exceed the estimate; an explicit lower cap is allowed.
The loop recomputes the request from current files and exact brief/photo descriptors/notes before consumption; a cached
caller sha is insufficient. Closed-run row hashes and transport request numbers detect truncation or changed outcomes;
an unknown-row reconciliation binds prior/final row hashes, source, usage and exact owner words. Archive reconciliation
reads its own response and receipt hash. A partial ledger creation can be owner-reconciled at $0 only without transport
evidence or prior recorded rows; the invalid bytes remain preserved and the approval remains consumed.
This is an audit trail the coding agent records from chat, not cryptography.

**Estimate** = `Σ_{i<N} settle(C0 + g·i, cache hit h, output o)` priced with the Tariff including the 272k step; C0 = text
chars/4 + 1,500 per image; g, h, o = medians of the 13 Studio astra ledgers (2,360 tokens/turn, 0.971, 850), range from the
p10-p90 of g; leave-one-out median absolute error 8% (range −33..+32%, Prada underestimated). The unspendable tail is the
worst-case hold at the last request's size. Display amounts round upward to cents. Example C0 25k, N 60: $10.12 (range $8.91-12.69); tail $2.47 at 8,192 output, $2.88
at 16,384. **Smoke (WP8 step 0):** `job run --smoke` = 1 image, `max_output_tokens` 4,096, `max_turns` 2, cap $1.00; at ~12k
context the hold is ~$0.36 and the spend ~$0.10-0.30 ("spend: cents; hold: up to $0.40").

## 9. Receipts

`ReviewLog` (port of agent.py:181-339): `runs/<r>/transport/NNNN-{request,response}.json` with data-URL images replaced by
`{sha256, bytes, mime}` and stored once in `blobs/`; embedded or non-base64 image URLs inside text use compressed UTF-8 blobs
(the session restores the exact original text); encrypted content and signatures masked, key redacted; `events.jsonl` (hook,
raw-body sha/size, request_id, response_id, status, usage); `reasoning-summaries.jsonl` (kept `summary_record`); **`transcript.md`**,
the owner-readable record (Astra's text, summaries where present — only ~16% of responses have one — each tool call's arguments
with code fenced, each result's status, key numbers and image links); per call `tools/NNNN-<tool>/{arguments,result,journal}.json`
(+ `issued-code.py`; journal `{checkpoint_before, fp before/after, seconds, status}`). Implementation receipt (`agent/snapshot.py`):
`git ls-files -co --exclude-standard glasses3d` minus `data/`, `.venv`, `node_modules`; HEAD; sha of `git diff HEAD`; per-file
shas + `src.tar.gz`; Blender, venv package (`importlib.metadata`) and Node versions; arview `package-lock` sha. Binding:
`deliverable/NN/report.json` and every ledger row carry the GLB sha, scene.blend sha, checkpoint id, run `budget.json` sha,
implementation-receipt sha and arview recipe.

## 10. Tool catalogue (docstrings written fresh under WP7; registry sha in run-config)

| Tool | Runs in | Mutates | Returns |
|---|---|---|---|
| `execute_blender_code(code, soft_timeout_s=120, label="")` | worker `exec` | yes (§5) | §3.1 |
| `scene_summary(objects="all")` | worker | no | units/axes/marker; objects {partRole, verts, tris, materials, bbox_mm, closed, loose_parts}; materials; warnings (missing partRole, material shared by lens and non-lens) |
| `save_checkpoint(label)`, `list_checkpoints()`, `restore_checkpoint(id)` | worker | restore | ids, deltas, references; a label saves only if the scene drifted |
| `seat_lens`, `hide_eyewire`, `unseat_lens`, `unhide_eyewire` / `check_lens_seating` | worker `g3d_call` | yes / no | WP4 receipts unchanged (`seat_bpy.py:833/1488/1709/1740/339`) |
| `g3d.lens_coating(...)` via `execute_blender_code` | worker | yes | Blender helper, not a standalone tool; authored optical export evidence |
| `load_font(family)` | worker | yes | font name |
| `render_views(...)`, `pick_surface_point(...)` | worker (+ host rays) | no | §6 |
| `fit_cameras(reason, refs=None)` | host pool 14 | writes basis-K | per ref {view_kind, status, iou, contour mm, flags} + overlay sheet |
| `compare_to_photos(...)` | host raster + worker | no | §6 |
| `export_glb()` | worker export → `validate_spec` → `seat_check.check_glb` | no | path, sha256, compact receipt, spec rows, `live_site_would_fail`, seating; cached by fingerprint |
| `arview_capture(poses="default", backgrounds=("skin",), lensenv=None, clip_m=None, portrait=True)` | export → `node qa/capture.mjs <glb> --out work/arview/NNNN` | no | exit 0 ok / 3 spec fail (images still returned) / 4 refused / 1 infra; compact report; sheets (14 → 1, 36 → 2, 133 → 8 with a token warning) + portrait; image shas checked against report.json |
| `read_image(path, region=None)` | host | no | EXIF transpose on full frames and crops (fixes observations.py:55-56); job-relative paths; ≤2048 px |
| `read_report(path, pointer="", key_offset=0)` | host | no | evidence_json.py:129-190 core (4 MiB, 20,000 chars); `runs/`, `transport/`, `budget/`, approvals, `notes/`, `session/` and the ledger unreachable |
| `budget_status()` | host | no | `{cap, settled, reserved, unknown_holds, remaining, next_hold, requests}` |
| `finish` | host + worker + AR | no geometry mutation | two-call evidence review, strict spec/print/integrity checks; seating diagnostic; accepted finish triggers host delivery. There is no Astra `deliver` tool. |
| `photo_print` | host `production.bake_print` + trusted worker `print_uv`/`attach_print` | yes | integrated M4c; open to Astra under the 2026-10-13 owner decision. WP5 remains owner-closed with its historical quality limitations; task 7 pins the accepted output and repairs generality. |
| `temple_stage` | private specialist + trusted parent application | yes | integrated M4b on the captured job head, with its own ten-tool v3 registry, photos/cameras and shared owner money. Retired v1 relief/trace/fit tools are not registered. |

The proposed `agent/tools/gates.json` never became the runtime contract. Current instruction/tool contracts inspect
the real registries; specialist tools retain their actual physical and evidence refusals.

## 11. Jobs, CLI, brief, delivery (`glasses3d/__main__.py`, `agent/{jobs,brief,deliver}.py`)

**Commands** (Claude runs them; the owner sees plain sentences, sheets and an AR link):
- `job create --name N --images DIR --views views.json (--description T | --description-file F) [--specs F] [--seed-blend B]`:
  decodes every image, checks MIME vs decoded format and ≤40 MP, refuses duplicates by decoded sha, records EXIF orientation,
  requires an image↔view bijection (vocabulary = exactly `fit_views.view_candidates`), stores specs verbatim as an open object,
  creates no budget state.
- `job show|status <id> [--watch]` ("run 2 running 23 min, 31 requests, $5.12 of $15; last activity 12 s ago: compare_to_photos");
  `job estimate`; `job approve`; `job run <id> [--resume] [--detach] [--smoke]` (`--detach` is the default for paid runs:
  breakaway start, WMI `Win32_Process.Create` fallback, argv/env through a spec file; port `modeler/detach.py` + `winqos.py`
  after adding their PORT_REVIEW entries; never `preview_start`); `job stop`; `job note <id> --text` (append-only).
- `job deliver <id> --checkpoint cNNNN|head`; `job review <id> --open` (`node qa/serve.mjs <deliverable> --detach` with the
  recorded lensenv/clip, prints the URL); `job review <id> accept|changes|reject|borderline --owner-words "..." [--note]`
  (`changes` also appends an owner note); `job reconcile <id> --run R --seq S (--from-transport | --usage J | --no-charge)
  --owner-words "..."`; `job prune`; `ledger migrate`; `doctor` (no API call: pins, Blender, worker selftest, GPU renderer,
  arview, ledger schema, key present).

**Job directory** `glasses3d/data/jobs/<slug>-<8hex>/` (ignored): `job.json` (`g3d.job/1`: owner description verbatim + sha,
specs verbatim + sha, images[{index, file, view, fit_kinds, sha256, decoded_sha256, w, h, mime, exif_orientation, sent_file,
sent_sha256}], seed, bases, deliverables, runs; no provenance field); `inputs/images/reference-NN.*` (read-only),
`inputs/brief.txt`; `notes/owner_notes.jsonl`; `cameras/basis-K/`; `session/session.sqlite`; `checkpoints/` + `index.jsonl`;
`blobs/`; `work/{renders,compare,exports,arview,prints}/`; `runs/<utc>-<hex6>/` (§9 files, `approval*.json`, `run-config.json`,
`input-receipt.json`, `budget.json`, `worker/`, `execution.json` restart marker with runner and worker identity, `result.json`);
`deliverable/NN/{model.glb, scene.blend, report.json, sheets/}` + `deliverable/current.json`; `heartbeat.json`;
`budget/job-ledger.json`.

**Brief** (first user message, through the kept `plan_session_input` minus `_LEGACY_BRIEF`): "Blender product brief (verbatim):"
+ "Product: <name>" + "Owner's job description (authoritative, verbatim):" text + "Owner's specifications (authoritative,
verbatim):" JSON + "Every reference image below is an authoritative photo of this product."; then per image "Reference k of N:
view '<view>', images/reference-0k.ext, W×H px, sha256 <12hex>" **immediately followed by** its `input_image` (detail high;
original bytes, or above the 20 MiB API limit a same-size PNG, JPEG q95 if still too big, both shas recorded); then the
invocation text (run id; scene empty / resumed at cNNNN / owner-supplied seed; this run's cap; owner notes since the last run,
verbatim). No provenance, "supplementary", "unverified" or "hypothesis" text anywhere.

**`deliver` / `job deliver`** ($0, deterministic, explicit checkpoint, never a newest-file fallback): copy `cNNNN.blend` to
`deliverable/NN/scene.blend` (already packed); one-shot `export_copy` → `model.glb`; `validate_spec` (**the only refusal is a §2
failure**, the owner's own table); `seat_check.check_glb`; `arview capture --poses default` + portrait on the same bytes with
lensenv/clip; compare metrics if a basis exists; `report.json` (`g3d.deliverable/1`: job, n, checkpoint {id, sha256,
fingerprint}, glb {sha256, bytes, objects, triangles}, scene sha, export/spec/seating/compare/arview checks, viewer {lensenv,
clip_m}, `astra_note` verbatim, costs, implementation and budget shas); update `current.json`. Deliverables are immutable.

## 12. Owner ledger (`glasses3d/data/ledger/verdicts.jsonl`, append-only, `g3d.verdict/1`)

Row: `{schema, verdict_id, when (ISO + offset), verdict: accept|changes|reject|borderline, owner_words (verbatim), note, product,
job_id, deliverable, run_ids, checkpoint_id, glb_path, glb_sha256, scene_sha256, viewer {tool: arview serve|arview
capture|Blender Studio, arview_impl_sha256, served_url, lensenv, clip_m, width_mm, poses, report_sha256, sheets[]},
baseline_glb_sha256, budget_ledger_sha256, implementation_sha256, cost_usd_settled, source: glasses3d | Blender Studio (Astra),
pre-glasses3d | modeler, pre-glasses3d, authorized_by: owner, recorded_by: claude via job review, migrated_from}`.

`viewer.*` comes from arview's own records, never from what Claude believes was shown: the serve state file
`data/arview/serve/state/port-<port>.json` (`arview.serve-state/1`) + `/__arview/report.json` for webcam reviews, capture's
`report.json` for sheet reviews; `lensenv: null` = arview default (effective value from the report). `job review` refuses when
`glb_path` does not hash to `glb_sha256`. A GLB without a row reads "awaiting owner"; a new sha needs a new verdict.
**Migration** (sources untouched): the old `owner_verdicts.jsonl` minus its 34 `demo-synthetic` rows → 16 rows (8 accept, 4
reject, 3 changes_requested → `changes`, 1 borderline; `note`/`text` → `owner_words`, `asset_*` → `glb_*`, `medium` →
`viewer.tool`, the rest in `migrated_from.original`, unrecorded viewer settings `null`); the 3 rows of
`data/ledger/owner_verdicts.jsonl` (`date` → `when`, viewer text kept, `viewer.tool` = arview capture, `lensenv` null).

## 13. Module layout, ports, tests

```
glasses3d/__main__.py → agent.cli
glasses3d/agent/  cli jobs brief session_inputs loop receipts journal budget pricing estimate snapshot tool_validation reasoning_evidence
                  worker_client procs (Job Object, EcoQoS, detach, identity) watchdog ledger deliver images replay instructions (WP7)
                  tools/{__init__ (registry + gates.json), blender, seeing, cameras, arview, export, read, prints, temples, budget_tool, deliver}
glasses3d/g3d/    worker rpc safe_mode checkpoint render lib fonts materials bootstrap seed_prepare (+ export_glb, seat_bpy, print_bpy, temple_bpy)
glasses3d/g3d_host/ blender_camera compare
glasses3d/requirements-agent.txt  openai==3.22.1 openai-agents==0.22.3 httpx2==2.13.1 httpcore2==2.13.1 mcp==1.30.0 + the Studio-venv
                  versions of their dependencies (inventory §A), python-dotenv==1.1.1; proven by a clean install + pip check + `import agents`
```

**Ports:** exactly the PORT_REVIEW §3 keep/rewrite verdicts, plus: bootstrap writes **no** default `mdl_bridge_underside`
(validate_spec checks the origin geometrically, PORT_REVIEW :579); tool_validation gets a test pinning the "Invalid JSON input for
tool" prefix; the `settled()` shield (isolated.py:26-49) is kept; community safe_mode + `test_safe_mode.py` are adapted (MIT); the
Studio `ScriptedModel`/`PersistentOfflineBlender` are replaced by the real Runner over MockTransport and the persistent worker.

**Tests** (data-dependent ones fail, not skip, under `G3D_REQUIRE_DATA=1`). Host: pricing, budget guard (22 ported + every edit
in §8), job ledger, transport, loop offline (pins whole-turn persistence), estimator (leave-one-out median abs error ≤10%),
session_inputs, brief, tools contract, jobs CLI, ledger (16 + 3 migration, serve-state binding), watchdog (gap ≤3 s, CPU-idle
kill ≤75 s), snapshot, rpc, procs (Job Object reaps; EcoQoS reads back (1, 0)). Safe mode: the community suite inverted; the
union of the panel's adversarial sets (32 + 47 + 38) all denied; **corpus gate: 0 false positives on non-I/O lines across the 214
Astra snippets** (from ignored `data/reference/astra_corpus/`; commit only machine-path-free excerpts, the repo is public).
Blender: worker lifecycle, exec (soft timeout ≤ deadline + 0.5 s; C-level op takes the hard path; `--selftest` runtime layer),
checkpoint (stable on synthetic, Prada, q001; defects 1-3), corpus chained replays (Oakley 21/21, INVU 20/20, Prada 19/19),
camera parity (12 frozen + 4 synthetic; analytic ≤0.01 px, rendered ≤0.1 px), render (fingerprint-neutral, forced Cycles
fallback, crop = full-render region ≤1 px), Prada chain (Blender vs host raster IoU ≥0.998), pick (≤0.05 mm, right object),
trusted-path confinement (junction refused).

## 14. The $0 proof (WP2 is done only when every criterion passes; numbers go in PROGRESS.md)

Real SDK, OpenAI client, BudgetGuard, ReviewLog, tools, Blender and arview; only the transport is faked (`agent/replay.py` over
`httpx2.MockTransport` answering `/v1/responses/input_tokens` and `/v1/responses`, checking every `function_call` has its
output). `OPENAI_API_KEY` removed, `G3D_OFFLINE=1`, key `sk-offline-…`, `socket.connect` and `getaddrinfo` patched to 127.0.0.1.

**Proof A — Prada full session.** `job create` from the 6 Prada references (side, angled, top, front, side, rear) with
`--seed-blend` = a copy of `8c82…/agent/scene.blend`; frozen `data/wp3b/prada/cameras.json` imported as basis-01; approval $18,
effort max, output 8,192, turns 60. 59 scripted responses carry the 59 recorded usages of 8c82's `budget.json` verbatim; the count
endpoint returns the recorded `input_tokens`. Script: `scene_summary`; real snippet `20261001T144143…` (units/props);
`compare_to_photos`; a numpy edit splaying both temples 3 mm; `compare_to_photos`; `restore_checkpoint` + `compare_to_photos`;
`render_views` (8 canonical + 6 reference views, native + clay); `pick_surface_point`; `read_image`; `read_report`;
`check_lens_seating`; `export_glb`; `arview_capture` default; `save_checkpoint("candidate A")`; `budget_status`; `deliver(head)`;
final message; remaining responses `budget_status`/`scene_summary`. Then `job review --open` + `accept`; then run 2: `review
changes --note`, estimate, approve, `run --resume`, 3 responses.

**Proof B — synthetic from-zero job and edges.** The synthetic glasses of `tests/test_fit_views_synthetic.py`, 3 photos rendered at
the TRUTH cameras; from an empty bootstrap scene a snippet builds the model with numpy + bmesh; `fit_cameras` (workers=14);
`compare_to_photos`. Edges: `while True` (soft 2 s); numpy SVD 2600² (soft 2 s, hard 10 s); `np.save` and `from numpy import
load`; a snippet that creates an object then raises; provider 429, 520, connection drop, incomplete + continuation,
`budget_limit`; approval missing, stale, consumed; `reconcile --from-transport`; the runner killed during a mutating call, then
`--resume` with an owner note; a stop run (30 s fake host tool, stop written at 5 s).

**Pass criteria**
1. **Network:** 0 non-loopback connects or DNS lookups; 0 production-client constructions.
2. **Requests:** successful replay runs have creates = count calls = ledger rows; every create has retry count 0,
   `service_tier` default, model gpt-6-astra, `max_output_tokens` set, function tools only. A refused reservation may have
   a count call without a ledger row or create. A host-hook failure after reservation has a `not_sent` row and no create;
   its known-unsent hold is released and the run ends without retry.
3. **Proof A money:** 59 rows, each settled once from the transport; each `reserved_micro_usd` equals the recorded value (row 1 =
   719,438); **Σ settled = 14,261,424 micro-USD exactly** (the recorded Prada total); 0 unsettled; peak committed ≤ $18.
4. **Proof B outcomes:** 429 → `rejected_4xx` $0; 520 and drop → `unknown` full hold, exactly 1 request sent, `provider_unknown`;
   incomplete → settled, 1 continuation, completed; `budget_limit` → 0 requests after the refusal; missing/stale/consumed approval
   → exit 3, 0 requests; `reconcile --from-transport` settles the archived usage exactly.
5. **Brief:** request 1 holds the description byte for byte, the specs JSON verbatim and 6 `input_image` items whose decoded sha
   equals the job images, each immediately preceded by its view text; removed phrases and supplementary/unverified/hypothes occur
   0 times in instructions, brief and docstrings.
6. **Receipts:** 2N transport files per run; each image once in `blobs/`; 0 occurrences of `data:image` or the fake key under the
   job dir; `transcript.md` and `events.jsonl` record every tool call.
7. **Checkpoints:** count = changed mutating calls + 1; all read-only with sha and fingerprint; every restore fingerprint-verified;
   the raising snippet and both refusals leave the head fingerprint and 0 new checkpoints.
8. **Timeouts:** `while True` → `timeout` in ≤3.0 s, same worker pid; SVD → `worker_restarted` in ≤15 s, new pid, head verified.
9. **Crash/resume:** the session lacks the lost turn; the scene equals the lost call's `checkpoint_before`; the invocation text
   names that call id; `images_sent == 0`; the owner note sent exactly once.
10. **Renders:** 28 non-empty PNGs (alpha coverage 0.5-90%), fingerprint unchanged, ≤60 s on EEVEE.
11. **Compare (A):** unedited seed per reference IoU within 0.001 of `cameras.json`, contour mean within 0.02 mm; after the splay
    top IoU falls ≥0.05 and `model_bytes_identical` is false; after the restore the metrics equal the first compare bit for bit.
12. **Fit (B):** every photo IoU >0.97, contour <1.5 px, |Δyaw| and |Δpitch| <1.5° vs TRUTH (test_fit_views_synthetic.py:127-129).
13. **Delivery:** `validate_spec.ok`; arview exit 0 with every image sha matching its report; `deliverable/1/model.glb` sha = export
    receipt = `report.json` = ledger `glb_sha256`; `viewer.lensenv` and `clip_m` equal the serve state file's.
14. **Liveness:** heartbeat gap ≤3 s throughout (including the 30 s tool); a status line for every state ≥30 s; the stop run ends
    `stopped` with exit checkpoint = head and the worker pid dead.
15. **Determinism:** a second Proof A run gives identical ledger amounts and identical request bodies with ids and timestamps masked.
16. **Wall time:** Proof A ≤10 min, Proof B ≤15 min; `pytest -m proof` exits 0.

**Stop rule:** stop and re-diagnose before WP7 if criterion 3 is off by even 1 micro-USD, a chained corpus replay drops below its
full step count, or any adversarial snippet passes.

## 15. Build order (each stage committed on `glasses3d` with its tests; no push)

| Stage | Contents | Stop rule |
|---|---|---|
| A (no SDK) | rpc, worker, safe_mode, checkpoint, worker_client, procs + lifecycle/exec/checkpoint/safe-mode/corpus tests | a restore fingerprint mismatch on any of 3 scenes; corpus false positives >0; any adversarial pass |
| B | blender_camera, render, pick, compare, fit_cameras wiring | parity >0.01 px analytic or >0.1 px rendered; compare not reproducing basis-01 within 0.001 IoU |
| C (needs the PyPI OK) | clean install of the pins; pricing, BudgetGuard, JobLedger, approvals, estimator, receipts, loop on MockTransport | any non-loopback connection; any 1 micro-USD discrepancy |
| D | registry + wrappers (export/validate/seating/coating/font/arview/read/budget/deliver); WP5/WP6 stay gated | — |
| E | jobs CLI, brief, notes, ledger + migration, watchdog, detach/EcoQoS/Job Object | — |
| F | Proofs A and B + determinism, recorded in PROGRESS.md | WP2 done only when all 16 criteria pass |

Stages A and B need no download, so they can start while the PyPI permission is pending.

## 16. Open risks

1. **Safe mode is not a sandbox:** layered runtime checks, corpus and adversarial gates; C-level `.blend` writes are caught only
   after they happen (restore + delete).
2. **EEVEE on the Arc driver in long sessions** (TDR; timings swing 2.2x with machine state): automatic Cycles fallback, `doctor`.
3. **Fake fidelity / SDK drift:** the real API may reject what MockTransport accepts (the WP8 smoke turn catches it); exact pins and
   tests on persistence, incomplete handling, request fields and the tool_validation prefix.
4. **Context cost:** Prada's request 44 alone cost ~$2.10 (cache miss); input above 272k doubles in price. One sheet per call,
   crops via `read_image`, `max_turns` 60, the estimate shown as a range; run `fit_cameras` early (cache expiry unmeasured).
5. **An early basis absorbs model error:** residuals shown at fit time; a refit is an explicit new basis.
6. **The CPU-idle rule could kill a GPU-bound step** (frames ≤3.4 s vs 60 s of near-zero CPU): measured on a real capture in tests.
7. **Disk:** ~70-150 MB of checkpoints per run, up to ~1 GB with packed 4K prints; owner-run `job prune`.
8. **WP5/WP6 APIs may still change:** gated; WP2 wraps only stable surfaces.

## 17. What only the owner decides (plain words)

1. **Budget cap per run.** Suggested $15 for up to 60 Astra replies: expected about $10 (range $9-13); your three accepted Studio
   runs cost $8.45, $8.64 and $14.26. The last ~$3 of any cap cannot be spent, because each reply must fit its worst case.
2. **Thinking effort.** Every Blender run you praised (Tom Ford crystal, Prada, Oakley, INVU) used the maximum setting; the one
   maximum run that got "changes requested" (09-30, $16.83) was a different route, the old modeler. Keep the maximum, or try
   "high" to spend less?
3. **WP8 smoke turn cap: $1.** It will spend cents; the hold is up to $0.40.
4. **Permission to download the OpenAI SDK packages from PyPI** into `glasses3d/.venv` (stage C; nothing is cached locally).
5. **Research question for ChatGPT** (optional): "Does the OpenAI Responses API accept `prompt_cache_retention` for gpt-6-astra,
   with which values, and how is it priced?"

For WP8 (not WP2) the known-good regression product is still open: Oakley 3dff8674 or INVU c8e81c72. Not owner decisions
(engineering, recorded per run): output cap 16,384, `max_turns` 60, one worker, rollback on error, the camera convention.

## 18. Panel scores (0-10)

| Design | Correctness / safety | Robustness | Completeness | $0 testability | Simplicity | Total |
|---|---|---|---|---|---|---|
| loop (base) | 9 | 9 | 8 | 9 | 7 | **42** |
| worker | 8 | 8 | 9 | 8 | 7 | 40 |
| jobs | 7 | 7 | 9 | 9 | 6 | 38 |

Evidence (scratch): `worker_angle/` (client_prada2, corpus_result, camp_*, chain_result, fake_client_probe, jobobj_test, fp_diff,
pack_test), `loop/` (proto_loop, proto_persist, estimator), `worker/` (test_worker, chain_replay, corpus_replay, ckpt_cost),
`worker_proto/` (camconv, parity, compare_proto), `sdk_proto/fake_transport.py`, `corpus_results.json`, `adversarial.py`.
