# Glasses3D dev UI

This is the local UI for starting a glasses3d run, viewing its model and asking Astra for edits.
It binds to `127.0.0.1` only. It uses the existing glasses3d pipeline; the live Lenses app and `/ar/`
deployment are separate. D1's historical six-scenario browser/SDK/Blender proof passed at $0 on 2026-10-19,
including the then-required same-part shading refusal and byte-exact single apply. N1 adds separate shading
picks on one part when their normal influence is disjoint. See [the test guide](../tests/README.md#m12-dev-ui-and-edit-proofs)
for proof commands/results and [current progress](../plan/PROGRESS.md) for the next action.

## Start and stop

From the repository root, with the project venv and existing `glasses3d/arview/node_modules` installed:

```powershell
glasses3d/.venv/Scripts/python.exe -m glasses3d.dev_ui
```

Open [http://127.0.0.1:8260/](http://127.0.0.1:8260/). The command starts a detached server and prints its
local address. These commands inspect or stop that server:

```powershell
glasses3d/.venv/Scripts/python.exe -m glasses3d.dev_ui --status
glasses3d/.venv/Scripts/python.exe -m glasses3d.dev_ui --stop
```

Use `--port 8261` to choose another port; include the same `--port 8261` with `--status` or `--stop`.
`--foreground` runs the server in the current terminal for debugging. Runs and local apply tasks start
separately and survive closing the browser. To stop a model run, use its page's **Stop** button; `--stop`
is the UI server control.

## Pages and buttons

**Jobs** lists saved deliveries, active runs, edit drafts and understandings. Open a listed entry to return
to it. **New run** or **Start a new run** opens the upload form.

**New run:** add product photos, choose each photo's view and wearer's side, and enter the product description.
You may add a short name and specifications as a JSON object. **Remove photo** removes that upload from the
form. The description and specifications stay as typed; the UI adds no product details or guessed view labels.
Enter **Max budget (USD)**, then press **Start a paid run** to authorize and start that one run at **max** effort.

**Running** shows settled spend against your max budget, money left, elapsed time and current state. **Stop** asks
to stop after the current step; an in-flight request finishes. The page waits through finalization, then opens Results or
Understanding. If a model is available from an unfinished run, Results labels it **not finished**; a failure
without a usable model stays an explicit error. One paid run at a time is allowed in this checkout.

**Results:** drag to orbit the model, scroll to zoom, and right-drag to pan. **Model checks** expands the
recorded checks. Applied picks, skipped notes and interaction warnings appear with the model. **View in AR**
opens the existing webcam review for this exact delivery in a new tab; **Open AR viewer** appears if the
browser blocks that tab. **Edit** opens one free appearance editor for the delivered version.

**Edit:** your photos are on the left, the AR-rendered model is in the centre, and controls for the selected
material are on the right. Click a part or choose its material under **Lenses**, **Frame** or **Temples**.
Those groups come from the model's own part roles. A material shared across roles is refused with an explanation;
lens markings and decals are excluded from lens looks. Hardware uses its declared supporting component.

For lenses, choose **Solid** or **Gradient** with start/end colours and an optional removable middle colour.
Each colour's **takes (%)** sets its region width; adjusting one distributes the remainder proportionally.
**Blend** softens the boundaries. **Centre to sides** mirrors about the bridge; **Left to right** spans the entire
lens extent; **Top to bottom** spans each connected lens surface's height. A single shield stays continuous.
**Around a point** puts the start colour in the centre and the other colours in outward rings. The same takes
and Blend controls set ring widths and softness. Move the centre on the lens or with **Left / right** and
**Up / down**; **Roundness** runs from Circle to Wide oval and increases the horizontal reach. Pairs start at
each lens's own projected centre and mirror horizontal movement about the bridge; a shield has one shared centre.
Radial colour controls link the editable lens materials, including lenses split across several materials; optical
controls still affect the selected material. A lens with an existing colour texture keeps its pattern and is
explicitly excluded from the linked rings. Markings remain excluded.
Every colour can come from a colour box or the photo picker. Other parts use solid colour; on a colour texture,
it tints the existing pattern. A gradient cannot replace an authored lens colour texture; solid tint still works.

**See-through** appears for lenses and existing transmissive parts, including crystal frames. **Mirror** darkens
what you see through. **Shine** moves from matte to glossy; frame and temple materials also have **Metal or plastic**.
**Shimmer** offers Off/Subtle/Strong, and **Choose shimmer colour** uses the coating matcher. Exact optical controls
are folded under **Details**, with the same meanings and valid ranges as the earlier lens panel. Opening a model
never clamps its values. **Undo/Redo**, **Before/After**, **Check in AR** and **Save as new version** stay visible;
leaving with unsaved changes asks for confirmation.

Click a photo to use its verified frozen fitted camera and the photo/render wipe. An unavailable camera is stated
plainly. The bottom strip lists the job's deliveries as **Astra <checkpoint>** or **your edit**; open or compare any
version. Lens looks copy between models: one source lens material supplies all target lens materials; multiple
source materials require unique matching names. Frame/temple looks copy only within the same job by material name.
Unmatched materials and marking exclusions are reported.

The preview loads the same patched GLB used by Save through arview's production renderer and fixed lighting.
Gradients append an image and planar UV data, preserving existing binary byte ranges, geometry, normals and authored
textures. The receipt lists appended records and changed bindings. **Check in AR** opens a temporary copy;
**Save as new version** runs strict specification, seating, retained-print and AR checks before immutable publication.
Seating remains diagnostic under the existing policy. A save is your edit, never an Astra finish or visual acceptance.

**Shape wrong here? Ask Astra** opens the existing capture workflow, anchored to the clicked point when available.
Save pending appearance changes first. A reusable look records owner changes by role/material name and gradient
specification. Shape work uses the verified native source; the look is reapplied to a resulting native version through
a separate checked owner publication. Missing/renamed/ambiguous materials are skipped and reported. The original
native result stays available if appearance reapplication fails; patched GLB bytes never claim a matching Blender scene.

On the shape-edit page, position the model and press **Capture and annotate** to freeze the view. In the annotation dialog:

- **Point** makes a filled circle at one click.
- **Line** adds points one by one; use **Finish line**, Enter or a double-click to finish it.
- **Shape** adds corners one by one and closes only when you click its first corner again.
- **Select A** (or another label) selects a mark; its label field lets you rename it. Every label must be unique
  across this edit session. Use those labels in your request text.
- **Undo** reverses the last drawing action; **Delete selected** removes the selected mark; **Clear** removes
  all marks in this capture. **Cancel** closes the dialog without saving the item.
- **Done annotating** opens the request text box. Type what should change, then **Done** saves that capture and
  your words as one item. Warnings identify unknown or unused labels without rewriting your words.

Back on Edit, **Save words** saves changed text and **Delete item** removes a draft item. **Yes, capture another**
adds another item. **No, send to Astra** reveals a separate budget field; **Send to Astra (paid)** authorizes one
UNDERSTAND round for all saved items. Astra reads the requests and prepares options on copies. Sending freezes
the draft. Captures show the current model and your marks; they are model evidence, never product photos.

**Understanding** shows your words, Astra's reading and either two or three previewed amounts per item or a
reason it cannot preview the item. Images are photo / before / option; click an image to inspect its full size.
**Model maps** expands the labelled target images. Choose an option for each previewed item, or **None of these**
and a note. **Apply my picks ($0.00)** applies the selected options locally; at least one pick is required.
Skipped items and their notes remain visible with the new model. A note does not send another paid request.

Apply composes the saved choices on a fresh copy in item order and publishes a new immutable delivery after
the existing checks, export and AR capture. A single pick must reproduce its preview's GLB bytes. With several
picks, geometry touched by only one pick must reproduce that preview exactly; overlapping changes are composed
in item order and their interaction is reported. A failed check refuses delivery. The source delivery stays intact.

Two shading-repair picks on one part can be applied together when their affected normal regions are disjoint.
Separate marks can still affect shared normals; those overlaps, other kinds of normal edits on the same part
and older previews that fail exact validation remain refused. Each supported pick must retain its preview
exactly in the combined result, including exported normals. Existing exactness checks remain in force.
For a refused combination, put both marks in one item when preparing the request, or choose one item's option
now, choose **None of these** for the other, and send the other again as a new edit of the resulting model.

An item without a preview can show **Send this item to a full paid edit**. Enter that item's own max budget;
pressing the button authorizes a separate `full_edit_item` round from the same source delivery. Astra has the
normal modeling tools and the selected item's words and capture, with instructions to edit only that item.
The result is a delivery or an unfinished draft. Its page and saved report show geometry, material, UV and transform
changes, including changes outside the marked region; unresolved measurements stay explicit. The measurements
inform your judgment and do not promise confinement to every painted pixel. A fresh Edit draft then offers
**Any more edits?** from that result.

**Applying picks** shows local progress and elapsed time, then opens the edited model in a fresh Edit draft.
**Open edited model** retries opening that result if the automatic navigation fails. You can capture more edits,
or use **No, back to results** after a successful apply with no new items.

## What max budget means

Your typed positive amount is this run's settled-spend cutoff, shared by the main Astra session and specialists.
Product runs use **max** effort; paid edit sends (**Send to Astra (paid)** and **Send this item to a full paid edit**)
keep **high** effort. Every run and edit send is **settled-only**; cap equals stop. There is no estimated-reservation
cutoff and no shrinking the output to fit a reserve. No new request starts at or above the cutoff. The request already
in flight can finish past it, so this is not a guaranteed final invoice maximum. Unknown charges stop further spending.

The UI shows only settled spend, your max budget and money left; it shows no estimate, held amount, projected cost
or "finish soon" figure. Astra's money status also contains only settled spend, the cutoff and money left.
M11 task 11 (`2340e95`) made settled-only the default and only mode for new runs and edits and removed the
finish-cost projection from Astra's input; this is committed M11 behavior, not a new M12 change.

The typed amount and the paid button press are your single-use approval for that one run or edit send; the
record keeps the button's words and amount. Each later paid send needs its own amount and press. The UI does
not impose a universal $20 ceiling. Job-run CLI defaults are a $20 settled cutoff and max effort, with settled-only as
the sole mode for new runs and edits. Old reservation-mode records stay readable, but cannot authorize new
reservation-mode requests. Coding agents still need your explicit OK and an estimate before any paid call they would run.

Starting the UI, browsing jobs/results, using the viewer or AR, saving annotations and **Apply my picks** cost
$0 in API calls. **Start a paid run**, **Send to Astra (paid)** and **Send this item to a full paid edit** spend
on Astra. UNDERSTAND is paid even though its model changes are previewed on copies.

## Local state

Default locations, relative to the repository root:

| Location | Contents |
|---|---|
| `glasses3d/data/dev_ui/server/<port>/` | `server.json`, `runner-status.json`, `server.log` and launch records. |
| `glasses3d/data/dev_ui/registry/` | Saved job/run/delivery/draft/edit/task handles and button retry records. |
| `glasses3d/data/dev_ui/uploads/` | Upload staging. |
| `glasses3d/data/dev_ui/captures/` | Draft capture PNGs and metadata. |
| `glasses3d/data/dev_ui/tasks/` | Detached apply-task records. |
| `glasses3d/data/dev_ui/applied/<task-key>/` | New local-apply job copies and their deliveries. |
| `glasses3d/data/dev_ui/lens-edits/<task-key>/` | Temporary AR previews and new immutable lens-colour versions. |
| `glasses3d/data/dev_ui/appearance-edits/<task-key>/` | General Edit previews and new owner-look versions. |
| `glasses3d/data/dev_ui/reapplied/<task-key>/` | Checked owner-look versions after native shape changes. |
| `glasses3d/data/jobs/<job-id>/` | Owner inputs, runs, checkpoints, money records and immutable `deliveries/<delivery-id>/`. |
| `glasses3d/data/oe/` | Short-path UNDERSTAND/edit/fallback rounds; captures are frozen under their `inputs/owner_captures/`. |

All of these are ignored local data. CLI `--state-root` and `--jobs-root` can select alternate storage; use the
same options when checking or stopping that server. Offline test mode exists only for proofs constructed in
Python with a scripted launcher and offline approvals. Its pages say **OFFLINE TEST MODE: scripted Astra, $0.00**.
There is no browser switch or ordinary launch flag that turns a real paid run into a free test.

## For agents

Read [AGENTS](../AGENTS.md), [M12's design and section 7 contracts](../plan/M12_DEV_UI.md#7-contracts-frozen-for-the-parallel-build-changes-go-through-the-coordinator),
and the dispatched brief. The contract defines pages, endpoints, DTOs, decimal-string money, opaque IDs, capture
identity and the C1/C2/C3 adapters. Browser payloads select saved item/option IDs; they never supply edit operations.
The server verifies same-origin mutations, confines served files and verifies registered artifact hashes.
Delivery responses expose verified `exactness` and `side_effects`; Results shows those measurements and the
complete side-effect report. N1 supports separate same-part `repair_shading` picks with disjoint normal influence;
overlap and other same-part normal kinds still refuse. Older previews must pass the current exact checks.
See the test guide's N1 proof record for measured coverage. UNDERSTAND evidence sealing remains slow on Prada:
C2's two-item proof took 1,147.484 s
(about 19 minutes). Task 12 (`1238a21`, accepted by Claude) repaired authenticated historical temple aliases:
40/54 temple objects verify, 14 already-reshaped objects correctly refuse, and the M10 regression export remains
byte-identical (`7d2d5b27…`). See PROGRESS for the retained proof.

| Module | Responsibility |
|---|---|
| `__main__.py`, `server.py`, `contracts.py` | Detached CLI, local ASGI routes, configuration and HTTP boundaries. |
| `api.py`, `registry.py`, `artifacts.py`, `labels.py` | Workflow services, durable records, verified files and label warnings. |
| `launcher.py`, `status.py`, `tasks.py` | Existing paid-run preparation/approval/detach, cheap file-only polling, detached $0 apply. |
| `core_adapter.py` | Calls the agent's `owner_captures.py`, `owner_choices.py`, `owner_fallback.py` and existing job/delivery/review code. |
| `lens_api.py`, `agent/owner_lens_edit.py` | Source-bound lens values, idempotent detached preview/save tasks and exact material-only GLB publication. |
| `appearance_api.py`, `appearance_flow.py` | General Edit controls, frozen photo angles, version/copy discovery, native source authority and look reapplication. |
| `agent/owner_look_edit.py`, `agent/owner_look_gradient.py` | Explicit role ownership, append-only image/UV patches, reusable looks and exact-name mapping. |
| `lens_renderer/`, `static/lens-colour.js` | Shared arview renderer bundle and local colour controls/photo eyedropper/history. Generated `.lens-renderer/` is ignored and rebuilt on source changes. |
| `static/general-edit.js` | Unified part controls, gradients, version comparisons, photo wipe and shape-flow handoff. |
| `static/app.js`, `static/api.js`, `static/money.js`, `static/label-warnings.js` | Page flows, requests, decimal display and warnings. |
| `static/viewer.js`, `static/annotation.js` | Three orbit view, frozen captures, GLB anchors, footprints and labelled drawing. |
| `browser_tests/` | Chromium tests against the mock API, using existing arview dependencies. |

The lens panel reads `GET /api/deliveries/{key}/lens-colour` and sends source SHA-256, material indices,
complete values and an idempotency key to `POST .../lens-colour/preview` or `POST .../lens-colour/save`.
Both return a task key; normal task polling returns an AR URL or a new delivery key. These routes have the
same local-host, same-origin, artifact-hash and path-confinement boundary as the other pages.

General Edit reads `GET /api/deliveries/{key}/appearance`. Source-bound `POST .../appearance-model`
returns the exact patched GLB for live preview; `.../appearance-preview` and `.../appearance-save`
return task keys for temporary AR and immutable publication. `.../appearance-copy` returns mapped
values and explicit skips. All use the existing HTTP and artifact boundaries. The free owner look
never authorizes a paid call; its native source is revalidated before the existing paid send.

Renderer parity and the native browser proof (run sequentially, after any other heavy task exits):

```powershell
node glasses3d/dev_ui/lens_renderer/parity.mjs --out glasses3d/data/m12/proofs/FRESH_LENS_PARITY
node glasses3d/dev_ui/lens_renderer/parity.mjs --out glasses3d/data/m12/proofs/FRESH_HIGH_IOR_PARITY --lens-ior 6
glasses3d/.venv/Scripts/python.exe -B -m glasses3d.dev_ui.e2e.lens_colour --out C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/m12/proofs/FRESH_LENS_UI --port 8297
```

The UI proof copies the accepted Oakley rerun delivery, blocks the paid launcher by construction, changes
the real controls, opens temporary and saved AR, saves through the real API, checks the Jobs listing and
proves original hashes and unrelated GLB data unchanged. Evidence and screenshots stay in the ignored output.

The E4 generality proof imports a private JSON manifest of `{label, model, job?, delivery_id?}` rows;
existing jobs are copied and standalone GLBs become clearly labelled test fixtures. It adds coarse
synthetic pairs and a transmissive frame, exercises all gradient directions, and records valid saves
or source-check refusals. It also checks exact owner-look reapplication on unchanged copied native
Oakley geometry. A read-only `--phase audit` can independently verify saved values and warnings.

```powershell
glasses3d/.venv/Scripts/python.exe -B -m glasses3d.dev_ui.e2e.general_edit --inputs PRIVATE_MANIFEST.json --out C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/m12/proofs/FRESH_GENERAL_EDIT --port 8297
node glasses3d/dev_ui/lens_renderer/general-parity.mjs --out FRESH_PARITY_DIR --model PATCHED_TEST.glb --materials APPEARANCE_DTO.json
```

General parity compares every RGBA channel of the panel and the unchanged production AR capture at
the same explicit camera and lighting. Repeat it for both solid and gradient patches on each required
model type; it never changes the supplied GLB.

Focused checks from the repository root:

```powershell
glasses3d/.venv/Scripts/python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests/dev_ui -n 0
node --test glasses3d/dev_ui/browser_tests/
```

Use [the test guide](../tests/README.md#m12-dev-ui-and-edit-proofs) for core edit tests, native proof commands,
measured durations and D1's completed proof. Run pool-heavy proofs sequentially with the project's detached logging
and memory rules; simulated ledger spend in a proof is separate from its actual $0 API cost.
