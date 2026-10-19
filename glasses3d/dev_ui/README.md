# Glasses3D dev UI

This is the local UI for starting a glasses3d run, viewing its model and asking Astra for edits.
It binds to `127.0.0.1` only. It uses the existing glasses3d pipeline; the live Lenses app and `/ar/`
deployment are separate. M12's component checks are recorded in [the test guide](../tests/README.md#m12-dev-ui-and-edit-proofs);
D1's browser/SDK/Blender integration proof and final suites are still pending as of 2026-10-19.

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
Enter **Max budget (USD)**, then press **Start a paid run** to authorize and start that one run.

**Running** shows settled spend, elapsed time, the estimate and current state. **Stop** asks to stop after the
current step; an in-flight request finishes. The page waits through finalization, then opens Results or
Understanding. If a model is available from an unfinished run, Results labels it **not finished**; a failure
without a usable model stays an explicit error. One paid run at a time is allowed in this checkout.

**Results:** drag to orbit the model, scroll to zoom, and right-drag to pan. **Model checks** expands the
recorded checks. Applied picks, skipped notes and interaction warnings appear with the model. **View in AR**
opens the existing webcam review for this exact delivery in a new tab; **Open AR viewer** appears if the
browser blocks that tab. **Edit** opens a capture draft for this delivery. The orbit viewer and worn AR view
use different rendering paths; use AR to judge the worn appearance.

**Edit:** position the model, then press **Capture and annotate** to freeze the view. In the annotation dialog:

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

An item without a preview can show **Send this item to a full paid edit**. Enter that item's own max budget;
pressing the button authorizes a separate `full_edit_item` round from the same source delivery. Astra has the
normal modeling tools and the selected item's words and capture, with instructions to edit only that item.
The result is a delivery or an unfinished draft. Its saved report measures geometry, material, UV and transform
changes, including changes outside the marked region; unresolved measurements stay explicit. The measurements
inform your judgment and do not promise confinement to every painted pixel.

**Applying picks** shows local progress and elapsed time, then opens the edited model in a fresh Edit draft.
**Open edited model** retries opening that result if the automatic navigation fails. You can capture more edits,
or use **No, back to results** after a successful apply with no new items.

## What max budget means

Your typed positive amount is this run's settled-spend cutoff, shared by the main Astra session and specialists.
Effort is **high**; cap equals stop. There is no estimated-reservation cutoff and no shrinking the output to fit
a reserve. No new request starts at or above the cutoff. The request already in flight can finish past it,
so this is not a guaranteed final invoice maximum. Unknown charges stop further spending.

The typed amount and the paid button press are your single-use approval for that one run or edit send; the
record keeps the button's words and amount. Each later paid send needs its own amount and press. The UI does
not impose a universal $20 ceiling. Legacy CLI defaults ($20 stop / $23 reserve / effort max) remain defaults
only. Coding agents still need your explicit OK and an estimate before any paid call they would run.

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

| Module | Responsibility |
|---|---|
| `__main__.py`, `server.py`, `contracts.py` | Detached CLI, local ASGI routes, configuration and HTTP boundaries. |
| `api.py`, `registry.py`, `artifacts.py`, `labels.py` | Workflow services, durable records, verified files and label warnings. |
| `launcher.py`, `status.py`, `tasks.py` | Existing paid-run preparation/approval/detach, cheap file-only polling, detached $0 apply. |
| `core_adapter.py` | Calls the agent's `owner_captures.py`, `owner_choices.py`, `owner_fallback.py` and existing job/delivery/review code. |
| `static/app.js`, `static/api.js`, `static/money.js`, `static/label-warnings.js` | Page flows, requests, decimal display and warnings. |
| `static/viewer.js`, `static/annotation.js` | Three orbit view, frozen captures, GLB anchors, footprints and labelled drawing. |
| `browser_tests/` | Chromium tests against the mock API, using existing arview dependencies. |

Focused checks from the repository root:

```powershell
glasses3d/.venv/Scripts/python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests/dev_ui -n 0
node --test glasses3d/dev_ui/browser_tests/
```

Use [the test guide](../tests/README.md#m12-dev-ui-and-edit-proofs) for core edit tests, native proof commands,
measured durations and pending D1 work. Run pool-heavy proofs sequentially with the project's detached logging
and memory rules; simulated ledger spend in a proof is separate from its actual $0 API cost.
