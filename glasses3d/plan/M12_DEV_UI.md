# M12: a simple dev UI for paid runs, results, AR and annotated owner edits (design, 2026-10-19)

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
