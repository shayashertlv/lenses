# M12: a simple dev UI for paid runs, results, AR and annotated owner edits (design, 2026-10-19)

**Build status, 2026-10-19:** complete with documented limits (ASSEMBLY M12 ledger). D1's six real-browser scenarios passed
at $0 (`b81f923`, `0da06ba`), including C2 follow-up 3's same-part shading refusal and immutable byte-exact single
apply. Separate same-part normal composition remains future work; the historical temple-alias regression and
slow Prada sealing remain open. Final suite: 6,014 distinct Python passes / 2 skips / 11 xfails / 1 non-strict XPASS; 20 UI browser / 509 arview unit / 113 QA passes; type check, build and M1 passed. The four parallel camera setup errors cleared in isolation. Next: the owner tries the UI and decides on any
paid run. See PROGRESS for results and current money terms; the dated design/contracts below remain preserved.

Written by Claude (planner) from the owner's request of 2026-10-19. The coordinator session refines this into tasks after a
read-only mapping of the current code (section 5), reviews its own plan, and has Codex build it. The owner said not to
pause for plan approval: "i dont need you to show me the plan, you can follow what you wrote before building, then review
it (claude) and go and build it. no reason to pause it at me." and "for fastest time please max utilize this machine".

## 1. The owner's request (verbatim, 2026-10-19)
> i want you to think/plan a simple ui which allows me to input a product images and a product description, max budget
> (meaning the actual cutoff with no reserving), then when the user hits a 'start a paid run' button the automation
> should start, then loading (nothing fancy this is just for dev) where the overall used budget and the time since
> starting are shown, then when the results are in the 3d model should be shown in a 3d viewer, at the results page
> other than the 3d viewer there should be a button for viewing the pair in ar and a button to move to the edit page. in
> the edit page there should be the 3d model in the 3d viewer, and a "capture and annotate" button, the user should be
> able to hit that button and then a screenshot of the 3d viewer is being taken and pops-up for the user with a tiny bit
> of annotation tools (i.e. "mark a point" tool which is just a full circle painter, with it you can click on the image
> with this tool on a point you want edited and the tool creates a full circle at the point, the same for lines and
> shapes (i dont want template shapes but shapes where the user needs to mark point by point and only when the user
> closes the shape (marks the same point twice) the shape is created on the image), and there should be a "done
> annotating" button which "captures" the annotated image and popsup an input box for the user to type what he wants
> astra to edit, then when the user hits done he should be taken back to the edit page and be asked "any more edits?"
> if the user has anything else to ask for edit he should go through the process again, if he hits no more edits, his
> edits and annotations should be sent to astra for extra editing session. +each label shape/circle/annotation_entity
> should be labeled by the user, then the user should use the labels to be specific when his typing his edit inquiry.

The owner's two example annotations are in `glasses3d/data/m12/owner_examples/` (git-ignored):
`front-corners-annotated.webp` (two closed shapes around the front's upper outer corners) and
`temple-triangle-annotated.webp` (a closed triangle on the temple's frame-side facet plus a filled point at its lower
corner).

## 2. Decisions already made
- **Edit flow = option A with B as fallback** (owner, 2026-10-19: "option a is good"): after "no more edits", one paid
  UNDERSTAND round reads every item and returns Astra's per-item interpretation plus 2-3 previewed amounts on copies; the
  page shows, per item, the photo / current model / options in the views that show the change; the owner clicks one
  option per item (or "none of these" + note); the pick is applied exactly at $0 (the existing M10 local apply), the new
  model appears, then "any more edits?". An item Astra cannot preview is shown as such with a "send this item to a full
  paid edit" button with its own cutoff.
- **Money:** the run page's max budget is the settled-only cutoff (no reservation cutoff, no output shrinking): no new
  request starts at or above it; the response in flight can cross it, and the page says so in one line. The code's
  `<= $20` refusal for job runs (`agent/job_run.py` prepare_run) gives way to the owner's typed number (the owner decides
  money). Every paid edit send (UNDERSTAND, and a fallback full edit) has its own cutoff field. Effort stays the standing
  owner term (high) unless the brief says otherwise. "Start a paid run" (and each paid edit send) is the owner's
  approval: it creates and consumes the single-use approval for exactly what is shown.
- **Photos:** each uploaded photo gets a view dropdown (the camera fitter's view kinds) and a wearer's-side dropdown; the
  pipeline does not guess views today and jobs hold only owner inputs. Description text verbatim; optional specs.
- **Annotations carry 3D anchors.** Each capture stores the viewer camera (pose, fov, size) and the GLB sha; every point,
  every line end and every shape corner stores the model surface point under the click (object, face, barycentric,
  position mm) or "background". Labels are unique across the whole edit session (default A, B, C ... editable); the text
  box warns about a label that does not exist and about labelled marks the text never mentions.
- **Astra must never mistake a capture for a product photo.** Captures enter the edit round as "owner's marks on a
  picture of the current model" with the camera, labels, anchors and the owner's words verbatim, separate from the
  product photos, which stay authoritative for the look. Append-only history, native pixels, existing renewal.
- **Runs survive the browser:** the UI server and every paid run start detached (WMI/start; preview servers kill
  children); one paid run at a time (existing machine-wide lock); the loading page shows settled spend, elapsed time and
  a Stop button (existing `job stop`). A run that ends without finishing shows its draft labelled "not finished".
- **Viewer and AR:** a 3D orbit viewer (three.js from `arview/node_modules`, or arview's renderer if the mapping shows it
  fits) on the results and edit pages; "View in AR" opens the existing `job review` / `arview serve` webcam page for the
  shown delivery. The review page's true-size question stays parked (owner).
- **Dev scope:** local only (127.0.0.1), no accounts, plain pages, nothing fancy. Never touch `ar/` or the root app.

## 3. Screens (minimum)
1. **New run:** photo upload with per-photo view + side, description, optional specs, max budget, "Start a paid run".
2. **Running:** settled spend, elapsed time, Stop; links to the run log if useful.
3. **Results:** 3D viewer, "View in AR", "Edit"; draft label when unfinished; the run's spend.
4. **Edit:** 3D viewer, "Capture and annotate" -> annotation dialog (tools: point = filled circle; line = point by point;
   shape = point by point, created only when the first point is clicked again; per-entity label; undo/delete) ->
   "Done annotating" -> request text box -> back to Edit with "Any more edits?" (Yes: capture again; No: send, with a
   cutoff field) -> Understanding page (per item: Astra's reading, previews photo-first, pick buttons, fallback button)
   -> apply at $0 -> Edit page with the new model.
5. A plain job list to reopen earlier jobs and deliveries.

## 4. How it is built and proven
- Backend: a small local Python server over the existing modules (`agent/cli.py`, `jobs`, `job_run`, `delivery`,
  `edits`, `owner_apply`, `review`, `verdicts`), not a reimplementation. Any web dependency is pinned from PyPI and
  recorded (installs pre-approved). Frontend: static HTML/JS served by it.
- $0 proofs: every flow end to end with the scripted Astra (`agent/replay.py`, offline approvals) and Blender, including
  a scripted UNDERSTAND with previews, a pick and a $0 apply on a copy of a real delivery (e.g. Prada c0016 copy as in
  `tests/proofs/assembly_m10_edits.py`); browser tests of the annotation tool (point, line, closed shape, labels,
  anchors); a run that hits the cutoff shows its draft. No paid call; the owner presses the real button.
- Full suite once at the end (`tests/README.md` method), arview suites, `assembly_m1`.

## 5. Read-only mapping first (Codex, before the plan is finalized)
Report the exact contracts, with file:line: (1) how an edit round takes the owner's request today (text only?) and how
to add captures + anchors without breaking approval binding and append-only history; (2) the UNDERSTAND output
(interpretations, labelled map, preview receipts, option ids) that the pick page needs; (3) how a single item's full paid
edit is started; (4) live run status, settled spend and stop for a detached run; (5) viewer options (arview renderer vs
plain three.js), screenshot and raycast to model surface points; (6) instruction size headroom (main 15,950/16,000; edit
prompts) and what raising the self-imposed caps touches; (7) every place that enforces the $20 job cap; (8) job create
inputs (views.json schema, view kinds, sides, transport copies). Then the coordinator writes the task list (parallel
where file ownership is disjoint), reviews it, and builds.

## 6. Coordinator decisions after the mapping (Claude, 2026-10-19)
The mapping is `data/m12/codex/m12_0_mapping.md` (git-ignored). These decisions refine section 2 and are what the
tasks build.
1. **Admission:** a UI edit is admitted to a paid UNDERSTAND with its items unresolved. Reading each item is Astra's
   job in that round; an item no tool can do comes back `cannot_preview` with a reason (section 2's fallback button).
   The CLI's `--coverage` path for text edits stays; EDIT/apply readiness stays strict; no guessed "ready" mapping is
   ever written.
2. **Item = one capture cycle:** one capture (clean + annotated PNG, camera, labelled marks) plus the owner's words
   verbatim. Labels are unique across the edit session; an item's words may cite any label of the session.
3. **Per-item options:** UNDERSTAND seals 2-3 options per item (or `cannot_preview` / `needs_clarification` with a
   reason), each option's operations bound to its item and previewed (photo / before / option, native pixels) in the
   views that show the change.
4. **Apply = one $0 button** ("Apply my picks"): the chosen per-item sealed operations are composed on a fresh copy of
   the source in item order; every existing hard check, export, AR capture and a new immutable delivery follow; the
   source delivery never changes. A single pick keeps today's GLB-byte equality with its preview. With several picks,
   an item whose change touches geometry no other pick touches must reproduce its preview exactly there; picks that
   touch the same geometry are composed in item order and the interaction is reported on the page and in the
   receipt, never silently. "None of these" + note applies nothing for that item; the note is shown with the new model.
5. **Fallback = new mode `full_edit_item`:** a separately approved paid round seeded from the same immutable source
   delivery, with the main toolset, the product photos, that item's capture evidence and words verbatim, and an
   "edit only this item" instruction. Its result is an ordinary delivery or draft of that round; changes outside the
   item's marked region are measured and reported as side effects (the owner judges by eye); hard refusals unchanged.
6. **Marks to model:** each vertex anchor records the nearest surface hit (GLB node index and exact node name, mesh,
   primitive, primitive-local triangle, barycentric, position in mm, lens flag, and the first non-lens hit behind a
   lens) or `background`; each mark also records its footprint (visible triangles under the dot, stroke or shape
   interior at capture resolution). Core resolves these to source objects/polygons where the export correspondence
   is unique and gives Astra a tool to see the marked regions on its model map and select authenticated targets from
   them; anything unresolved stays labelled unresolved.
7. **Money:** the typed cutoff replaces the $20 preparation ceiling for jobs and edit rounds (positive finite,
   settled-only, effort high, cap = stop). Each paid send (run, UNDERSTAND, fallback) has its own typed cutoff; the
   press writes the approval (owner words record the button and the displayed amount) and launches once (idempotent).
8. **Server:** Starlette + Uvicorn (already pinned in `requirements-agent.txt`); `python -m glasses3d.dev_ui` starts it
   detached on `127.0.0.1:8260` (`--foreground` worker, `--stop`). Heavy $0 work runs as detached tasks; paid runs use
   the existing detached `job run`. Offline test mode exists only when Python code builds the app with the scripted
   launcher (never an HTTP flag); its pages then say "OFFLINE TEST MODE: scripted Astra, $0".
9. **Viewer:** plain three.js 0.185.1 from `arview/node_modules` (OrbitControls, GLTFLoader, RoomEnvironment), GLB
   root untouched (metres x 1000 = bridge-relative mm). A capture re-renders the current view at the canvas aspect,
   long side 1600 px. "View in AR" opens `review.review_job(..., port=0)`'s URL in a new tab.
10. **Instruction caps:** main and edit raised from 16,000 to 24,000 characters; guidance is never trimmed to fit.

## 7. Contracts (frozen for the parallel build; changes go through the coordinator)
**Pages** (A serves, B builds): `/` job list, `/new`, `/run/<run_key>`, `/results/<delivery_key>`, `/edit/<draft_id>`,
`/understand/<edit_key>`, `/task/<task_key>`. Static `/static/*` from `dev_ui/static/`; vendor `/vendor/three/*` (`.js`
under `arview/node_modules/three/build/` and `examples/jsm/` only, path-confined). Import map: `"three":
"/vendor/three/build/three.module.js"`, `"three/addons/": "/vendor/three/examples/jsm/"`.

**API** (JSON UTF-8; money as decimal strings; UTC ISO times; keys are opaque server-issued `[A-Za-z0-9_-]{6,64}`;
errors `{"error":{"code","message"}}` with a plain owner-facing message; every POST/PATCH/DELETE must be same-origin):

| Call | Body | Returns |
|---|---|---|
| `GET /api/config` | | `{schema:"g3d.dev_config/1", offline_test_mode, view_kinds:[...], sides:["both","left","right","none"], effort:"high"}` |
| `GET /api/jobs` | | `{jobs:[{job_id, name, created_utc, deliveries:[{delivery_key, label, created_utc}], runs:[{run_key, kind, phase}], drafts:[{draft_id}], edits:[{edit_key, phase}]}]}` |
| `POST /api/jobs/start` | multipart: `manifest` = `{name, description, specs_text, cutoff_usd, idempotency_key, photos:[{field, filename, kind, side}]}` + one file part per photo named by `field` | `{run_key, job_id, run_id, cutoff_usd}` |
| `GET /api/runs/{run_key}` | | RunDTO |
| `POST /api/runs/{run_key}/stop` | `{}` | RunDTO |
| `GET /api/deliveries/{delivery_key}` | | DeliveryDTO |
| `GET /api/deliveries/{delivery_key}/model.glb` | | GLB bytes (hash-verified) |
| `POST /api/deliveries/{delivery_key}/ar` | `{}` | `{url}` |
| `POST /api/edit-drafts` | `{delivery_key}` | DraftDTO |
| `GET /api/edit-drafts/{draft_id}` | | DraftDTO |
| `POST /api/edit-drafts/{draft_id}/items` | multipart: `capture` (JSON below), `viewer` (PNG), `annotated` (PNG) | `{item: ItemDTO, warnings}` |
| `PATCH /api/edit-drafts/{draft_id}/items/{item_id}` | `{request_text}` | `{item, warnings}` |
| `DELETE /api/edit-drafts/{draft_id}/items/{item_id}` | | DraftDTO |
| `POST /api/edit-drafts/{draft_id}/send` | `{cutoff_usd, idempotency_key}` | `{run_key, edit_key}` |
| `GET /api/edits/{edit_key}/understanding` | | UnderstandingDTO |
| `POST /api/edits/{edit_key}/apply` | `{understanding_sha256, choices:[{item_id, option_id}], skipped:[{item_id, note}], idempotency_key}` | `{task_key}` |
| `GET /api/tasks/{task_key}` | | TaskDTO |
| `POST /api/edits/{edit_key}/items/{item_id}/full-edit` | `{cutoff_usd, idempotency_key}` | `{run_key}` |
| `GET /api/artifacts/{sha256}` | | image bytes the server registered for a DTO |

- Warning: `{code:"unknown_label"|"unused_label"|"empty_text"|..., label, message}` (warnings never rewrite text).
- RunDTO `g3d.dev_run/1`: `run_key, kind:"job"|"understand"|"full_edit_item", job_id, run_id,
  phase:"starting"|"running"|"finalizing"|"ended"|"failed", loop_state, started_at, updated_at, elapsed_seconds,
  settled_usd, held_usd, unknown_usd, cutoff_usd, estimate_usd, terminal_status, stop_requested, delivery_key,
  edit_key, error, offline_test_mode`.
- DeliveryDTO `g3d.dev_delivery/1`: `delivery_key, job_id, delivery_id, glb_sha256, model_url, finished,
  accepted_finish, run_status, label:"finished"|"not finished", run_settled_usd, created_utc,
  checks:[{name, ok, detail}], origin:"run"|"applied_picks"|"full_edit_item", applied:[{item_id, option_id,
  amount_text}], notes:[{item_id, note}], warnings`.
- DraftDTO `g3d.dev_edit_draft/1`: `draft_id, source:{delivery_key, glb_sha256}, items:[ItemDTO], labels, frozen`.
  ItemDTO: `item_id:"I1", capture_id:"C1", labels, request_text, viewer_url, annotated_url, warnings`.
- UnderstandingDTO `g3d.dev_understanding/1`: `edit_key, understanding_sha256, source:{delivery_key, glb_sha256},
  run_settled_usd, items:[{item_id, number, labels, request_text, annotated_url, interpretation,
  disposition:"previewed"|"cannot_preview"|"needs_clarification"|"unsupported", reason, fallback_allowed,
  options:[{option_id, label, amount_text, images:[{url, view, panels:["photo","before","option"]}]}]}],
  map_images:[{url, label}], limitations`.
- TaskDTO `g3d.dev_task/1`: `task_key, kind:"apply_picks", phase:"queued"|"running"|"done"|"failed", started_at,
  elapsed_seconds, progress, result:{delivery_key}|null, error`.

**Capture** (`g3d.owner_capture/1`; B produces, core validates and stores it with both PNGs under the round's inputs;
it is never a product photo; pixel coordinates are in the PNG frame, not CSS pixels):
```json
{"schema":"g3d.owner_capture/1","capture_id":"C1","source":{"delivery_key":"k","glb_sha256":"<64hex>"},
 "image":{"width_px":1600,"height_px":900},
 "camera":{"projection":"perspective","fov_y_deg":35,"aspect":1.7778,"near_m":0.001,"far_m":10,
   "position_m":[0,0,0.4],"quaternion_xyzw":[0,0,0,1],"target_m":[0,0,0],
   "matrix_world":["16 numbers, column-major"],"projection_matrix":["16 numbers, column-major"]},
 "marks":[{"label":"A","kind":"point|polyline|closed_shape","radius_px":9,"stroke_px":4,
   "vertices":[{"xy_px":[412,238],"anchor":{"kind":"surface","node_index":3,"node_name":"exact glTF node name",
     "mesh_index":2,"primitive_index":0,"triangle_index":42,"barycentric":[0.2,0.3,0.5],
     "position_mm":[12.0,6.0,-24.0],"normal":[0,0,1],"is_lens":false,"behind":null}},
     {"xy_px":[5,5],"anchor":{"kind":"background"}}],
   "footprint":{"pixels":1830,"faces":[{"node_index":3,"mesh_index":2,"primitive_index":0,
     "triangles":[[40,44],[97,97]]}]}}],
 "request_text":"verbatim owner words"}
```
`point` has one vertex, `polyline` at least two, `closed_shape` at least three distinct vertices and is closed only by
clicking its first vertex again (no duplicate closing vertex stored). `triangles` are inclusive index ranges.
`behind` is the first non-lens hit when the nearest hit is a lens (lens = material with transmission > 0 or opacity
< 1), else null. All vertices of one capture refer to one `source.glb_sha256`; a new delivery starts a new draft.

**Core façades** (Python; A calls them only through `dev_ui/core_adapter.py`, with a fake for its own tests; exact
signatures follow the code and are reported by each task):
- C1 `agent/owner_captures.py`: `validate_capture(payload, viewer_png, annotated_png, source_glb) -> dict`;
  `create_capture_understand(source_root, delivery_id, items, destination=None) -> Path` (items `[{item_id,
  request_text, capture_dir}]`; returns the UNDERSTAND round root, ready to prepare). `job_run.prepare_run` accepts
  any positive finite settled stop.
- C2 `agent/owner_choices.py`: `read_understanding(round_root) -> dict` (UnderstandingDTO with file paths + sha256
  instead of URLs); `apply_choices(round_root, understanding_sha256, choices, skipped, confirmation_text,
  destination=None) -> Path` (new delivery; $0; no model request).
- C3 `agent/owner_fallback.py`: `create_full_edit_item(round_root, item_id, destination=None) -> Path` (fallback round
  root, ready to prepare).
- Every paid send: `prepare_run` (settled-only, effort high, stop = cap = typed cutoff) -> `write_approval` (owner
  words: `Dev UI: owner pressed "<button>" with max budget $<N> for <job or edit>`) -> `python -m glasses3d job run
  <root> --run-id <R>` (detached). Offline test mode substitutes the offline approval and scripted client instead.
