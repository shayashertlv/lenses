# Photo-to-3D jobs

Run from the repository root with `glasses3d/.venv/Scripts/python.exe -m glasses3d job`.
The local job directory contains original images, explicit views, scene checkpoints, camera fits, receipts and money
ledgers. Every supplied photo is authoritative. Runtime code contains no product presets.
For the local upload, results, orbit-viewer and capture-edit pages, see the [dev UI README](../dev_ui/README.md).

Create `views.json` with exactly one entry per photo, for example:

```json
{
  "front.jpg": {"kind": "front", "side": null},
  "left.jpg": {"kind": "side", "side": "left"},
  "angle.jpg": {"kind": "angled", "side": null}
}
```

Kinds are the camera fitter's explicit view kinds; unsupported or missing kinds are refused. Side is `left`, `right`,
`both` or `null`, always the wearer's side: +X is the wearer's left and -X is the wearer's right, regardless of image position.
Original bytes are retained. EXIF-oriented photos get upright transport copies at the same oriented dimensions;
exceptionally large payloads use the same transport mechanism. Both hashes are bound into the approval.
`reflective_floor` and `matte_mode` are owner-only fields. Coding agents may label only view kind and side for
testing, checked by eye and never inferred from filenames. Floor reflections are handled by `fit_cameras`:
automatic matte selection or Astra's `mattes` override after inspecting the photos, retaining owner declarations.
Camera fitting also estimates each visible temple's opening relative to the current exported model and
reports hidden or camera-coupled angles. Those frozen deltas are used by photo comparisons, photo readings
and the temple stage; the worn AR view starts from the authored model.

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job create --images C:/photos --views C:/photos/views.json --name "Product" --description "Exact owner description" --specs C:/photos/specs.json
& glasses3d/.venv/Scripts/python.exe -m glasses3d job show JOB_ID
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID --cap 20 --settled-stop 20 --settled-only --effort high
```

Creation, inspection and estimation are local and free. Estimation writes a new run request. `job approve JOB_ID
--run-id RUN_ID --max-usd AMOUNT --owner-words "Exact authorization"` records the owner's approval for that request
only. Run it only after the owner has authorized the actual paid run and seen its estimate. `job run JOB_ID --run-id
RUN_ID` refuses missing, changed, consumed or insufficient approvals and launches a detached logged runner.

The default instruction profile is `decision-20261022`, the owner-approved rewrite from the 2026-10-22 audit
(`plan/AUDIT_20261022_ASTRA_DELIVERY.md`). It is one document: authority, reading the product, a fixed work order
(structure, cameras, shape detail, seating and print, appearance), the revision loop with `compare_look_to_photos`
after every shape or material edit, judging, specialists, best/money/finish, and a "which tool when" table. A
structural break (parts that meet in the photos but not in the model) comes before appearance work. Only this
profile offers `match_coating_colours`. `decision-20261021` stays selectable with its exact text; tool descriptions
are current for both decision profiles. To select another profile, use `job estimate JOB_ID --instruction-profile
decision-20261021` (or `legacy-20261021`, the frozen pre-rewrite instructions), then approve that request and run
it with `job run JOB_ID --run-id RUN_ID --instruction-profile` and the same name. The run option must match the
prepared profile; changing it needs a new estimate and approval. Omitting the run option uses the approved profile.
The profile is recorded in approval settings and the run configuration. The dev UI uses the default.

Tool facts added by the audit (both decision profiles unless noted):
- `scene_summary` returns `contacts`: separate parts within 1.5 mm that never come closer than 0.25 mm (about one AR
  pixel), with the smallest gap, its span and its position (`g3d/contacts.py`, BVH queries in `g3d/contacts_bpy.py`).
  The first finish call carries the same report. Information only, never a gate.
- `fit_cameras` returns `decision_table`, one compact row per photo, which stays inline when the rest is paged.
- `execute_blender_code` accepts `difference_updates`: record_difference items validated and recorded before the code
  runs; a refusal runs no code.
- `match_coating_colours` (`decision-20261022` only, product runs): the Edit page's coating search in Python
  (`agent/coating_match.py`, byte-equal to `dev_ui/lens_renderer/coating.js`), read-only.
- After the 10-22 paid run:
  - **Restarts.** A conversation renewal carries only comparisons of the current checkpoint, plus the owner photos,
    the latest tool result and photo_read sheets. That run carried 66 stale images across 4 restarts.
  - **Inline results.** `scene_summary` contact rows and `photo_read` warnings stay inline when a result is paged.
  - **Evidence binding.** A comparison counts as difference evidence on the camera set it used, not only the newest
    one; it goes stale when the model, export or photo/camera bytes change. A comparison that cannot count records
    why, and a citation of it says so instead of "unknown".
  - **Lens colour.** `decision-20261022` says the owner fine-tunes lens colour in the editor afterwards: get it close
    with one coating search and one side-by-side test, then return to structure and shape.

Standing dev UI terms (2026-10-19) use the owner's typed positive finite max budget, replacing the universal $20
cutoff for each run or edit send. Owner: "max budget (meaning the actual cutoff with no reserving), then when the
user hits a 'start a paid run' button the automation should start". Effort is high, spending is settled-only and
cap equals stop. Parent and specialist share settled spend; no request starts at or above that cutoff. The in-flight
response can cross it. There is no reservation cutoff or output shrinking; unknown charges still stop spending.
The typed amount and the owner's paid-send button press authorize that one run or edit send through a single-use
approval recording the button's words and displayed amount. Coding agents do not start paid calls for this build;
any paid run they would execute still needs the owner's explicit OK and an estimate. The $20 CLI examples here are
example amounts, not a ceiling; replace both `--cap` and `--settled-stop` with the authorized amount.
All new runs and edit rounds use settled-only spending. Job-run CLI defaults are max effort and a $20 settled cutoff;
`--cap` must equal `--settled-stop`. The `--settled-only` flag remains optional for compatibility. Old approvals
and ledgers remain readable and reconcilable, but reservation-mode approvals cannot start new requests.
This default and sole new-run money mode is committed M11 task 11 (`2340e95`), which also removed finish-cost
projections from Astra's input. The UI and Astra's money status show only settled spend, the owner's cutoff and
money left: no estimates, held amounts, projected costs or "finish soon" figures. The local CLI estimate remains
available for planning and the coding-agent authorization workflow described above.
An estimate's 60-reply forecast is an assumption, not a reply limit or a spending guarantee. `job stop JOB_ID`
requests a stop before the next request. `--foreground` runs in the invoking terminal; detached execution is the
normal path. Logs and status are under `runs/RUN_ID/`; the heartbeat is at the job root.

Instructions teach the glasses construction, evidence and stage order, with the same model requirements as
`plan/PLAN.md`. Owners' descriptions and specifications remain verbatim and approval-bound. History stays
append-only: earlier messages and images are never replaced. New tool results use bounded text with lossless
`read_report` access, and native photo comparisons compose onto pages with additional images for overflow.
In the decision profiles, main and specialist tools reference exact image bytes already present in their conversation;
an explicit `read_image` or specialist `photo_image` attaches them again. Renewal resets membership from the images carried into
the new conversation. First finish can reuse a complete delivered look only while its checkpoint, export,
camera basis, photos and source/transport files still match their hashes and every image remains in context.
Changed or incomplete evidence is captured afresh; the two-call review and all finish checks remain.
`legacy-20261021` keeps its prior transmitted behavior. No image is shrunk or recompressed by these reuse rules.
`save_checkpoint(best=True)` marks this run's draft source if it stops before accepted finish. The M9 safety valve
checks each candidate before admission: above 80% of the image/byte limit or long-context price step, it requests a
state summary and continues in a fresh conversation with every owner photo and verified current evidence. Old
conversation bytes remain unchanged. Summary requests use the same approval/money; unavailable summaries use host
facts. A mandatory seed exceeding hard limits still refuses. `job show` and delivery reports list renewal receipts.
The default profile keeps important visible differences in the run's append-only `differences.jsonl` through
`record_difference`. Each entry records evidence, status, the last correction/outcome and next action. Unknown,
unseen and stale comparison references refuse. First-finish per-photo differences map to items; an open item
prevents accepted finish until Astra records a current supported fix or limitation. Renewal carries the host list
and formal best automatically. The cutoff still publishes a draft from this run's formal best regardless of open
items. These are decision records, not appearance scores; the owner still decides likeness.
Main and edit instructions retain their 24,000-character caps. The `decision-20261022` main prompt is 9,754
characters and `decision-20261021` is about 11,200; the frozen legacy main is 16,321. Historical M12 measurements remain in their dated reports. The specialist
retains its own cap.

Job estimates use the eight complete recorded glasses runs and their measured context growth and prefix reuse.
The forecast names its corpus and limitations; it assumes no savings from image pruning. It is a planning estimate;
the live guard counts every request and stops new requests once settled spend reaches the approved cutoff.

Finished runs publish immutable snapshots under `deliveries/`, including the model, validation and seating reports,
AR evidence, contact sheets, cost and checkpoint. `deliveries/current.json` identifies the latest snapshot;
`deliverable/model.glb` and `deliverable/report.json` remain convenient copies. Prior snapshots are never replaced.

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job review JOB_ID
& glasses3d/.venv/Scripts/python.exe -m glasses3d job verdict JOB_ID changes --notes "Exact owner feedback"
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID --resume --cap 20 --settled-stop 20 --settled-only --effort high
```

Review starts a detached AR server with the fixed lens-reflection default and records the delivery actually served.
The verdict command binds to that reviewed delivery even if a newer run has finished. `accept` records acceptance;
`changes` requires notes. An explicit `--delivery-id` or `--sha256` can select an asset without a prior review.
Every verdict binds the model and manifest hashes; damaged evidence is refused. Review servers expose a loopback URL
for the owner's webcam and survive the launching command.

Change notes enter the next run verbatim. `job estimate --resume` prices and binds the saved history and reviewed
delivery checkpoint; the run resumes that reviewed state when there are unsent changes notes. After authorizing
its new estimate with `job approve`, use
`job run JOB_ID --run-id RUN_ID --resume` to retain the conversation. Notes already in that saved conversation are
not inserted again. Adding a note after approval invalidates that approval, so estimate and approve again.

`job migrate-verdicts` preserves the original legacy ledger, normalizes whole-product acceptances into
`data/ledger/verdicts.jsonl`, and archives every original record with its hash. Scoped print or method decisions
remain scoped evidence. Repeating migration verifies the same outputs without duplicating records.

## Owner edits

### Dev UI captures and per-item picks

One capture cycle is one item: clean and annotated model-view PNGs, camera, labelled marks and surface anchors,
plus the owner's request verbatim. Labels are unique across the edit session; an item's text may cite any session
label. Captures are evidence of the current model, never product photos. Product photos remain authoritative for
the look. Capture bytes, labels, words, camera, anchors and source GLB identity are approval-bound, transported at
native pixels and retained through append-only history and conversation renewal.

Capture UNDERSTAND is admitted unresolved without a coverage file or guessed operation mapping. Its separately
approved paid round interprets the items. `labelled_model_map(owner_labels=["A"])` focuses candidates associated
with that owner label and overlays its resolved source polygons. Ambiguous, background or unmatched anchors stay
unresolved. Marks alone grant no mutation authority: actual operations still need authenticated, drawn supports.

Each item returns an interpretation and either two or three sealed, previewed options with readable `amount_text`,
or `cannot_preview`, `needs_clarification` or `unsupported` with a reason. Comparisons show photo / before / option
at native pixels in views that reveal the change. Mixed previewed and blocked items are valid.

**Apply my picks** is $0 and sends no model request. It accepts only the sealed understanding hash and item/option
IDs, plus skipped items and their notes; the browser cannot supply new operations or amounts. Every item must be
picked or skipped, with at least one pick. The host composes the chosen operations on a fresh copy in original item
order, runs the existing scope and physical checks, strict export, retained-print validation and AR capture, then
publishes a new immutable delivery with `origin:applied_picks`. The source delivery stays unchanged. One pick must
match its preview's scene fingerprint and GLB bytes exactly. With several picks, disjoint changes reproduce each
item's preview exactly and untouched values match the source. Overlapping changes compose in item order, with the
affected items and objects reported on the page and in the receipt; no isolated-preview equality is claimed there.
Ambiguous shared topology changes refuse. "None of these" applies nothing for that item and retains its note.
Applied deliveries record zero requests and cost, `accepted_finish:false` and no visual acceptance; the owner can
review the result and record a verdict.

Separate `repair_shading` picks on one source object can compose when their actual normal influence regions are
disjoint and both sealed previews pass exact validation. Overlapping influence, other same-part normal operation
kinds and unproved older previews still refuse before destination handling, copying, worker startup or
publication. Classification authenticates the source and selected preview snapshots; material-only operations
do not count, and coordinate edits count when the sealed preview changes intended normals, smoothing or topology.
One item containing both marks, one selected item, and shading changes on different source objects retain the
existing checked apply path. For a refused combination, put both marks in one item when preparing the request,
or apply one now, choose **None of these** for the other, and send it as a new edit from the resulting delivery.
Native stored and decoded normals and exported NORMAL rows outside each shading influence remain exact.
The normal-composition tests now run without expected-failure marks; existing assertions and bounds remain.

D1's historical six browser scenarios passed at $0 under the earlier refusal, including immutable single-pick
preview-byte equality. N1's fresh evidence is recorded in [the test guide](../tests/README.md#n1-exact-local-normals--2026-10-19).
UNDERSTAND evidence sealing remains slow on Prada (C2's two-item proof: 1,147.484 s, about 19 minutes).
Task 12 (`1238a21`, accepted by Claude) repaired authenticated historical temple aliases; already-reshaped
objects correctly refuse. See [current progress](../plan/PROGRESS.md) for suite results and remaining scope.

An unpreviewable item can go to a separately approved **full paid edit** (`full_edit_item`) with its own typed
cutoff. This starts from the same immutable source delivery with the main toolset, product photos, only the selected
item's capture and words, and its sealed interpretation. It does not reuse the UNDERSTAND approval. Astra is told to
edit only that item; existing physical refusals remain. The result is an ordinary finished delivery or a clearly
labelled unfinished draft with `origin:full_edit_item` and the item ID. Its immutable side-effect report measures
geometry, materials, UVs, transforms and changes outside the resolved marked source polygons. Polygon resolution
does not prove pixel-level confinement; topology changes or ambiguous correspondence stay explicitly unresolved.
Unavailable measurements are reported. These are information for the owner's judgment, not new quality gates.

The CLI can prepare that fallback from a delivered capture UNDERSTAND round:

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job edit-item UNDERSTAND_ROUND --item I1 --cap 35 --settled-stop 35 --settled-only --effort high
```

This prepares only a fresh request and estimate. Use `job approve` and `job run` with the returned `round_path` and
run ID after the owner's separate authorization. `--output` optionally chooses a new fallback round directory.

### CLI text edits

For a text-only CLI request, first diagnose each item against its selected source and create an explicit
`g3d.edit_item_map/1` mapping.
The mapping records the exact request, labelled item spans and available operation kinds; matching words alone is
not a diagnosis. The $0 preflight creates no worker, estimate, approval or API call.

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job edit-check JOB_ID --request-file C:/edits/request.txt --mapping C:/edits/mapping.json --output C:/edits/coverage.json
& glasses3d/.venv/Scripts/python.exe -m glasses3d job edit JOB_ID --request-file C:/edits/request.txt --coverage C:/edits/coverage.json --cap 20 --settled-stop 20 --settled-only --effort high
& glasses3d/.venv/Scripts/python.exe -m glasses3d job show JOB_ID --edit-id EDIT_ID --round u1
```

`job edit` prepares an UNDERSTAND round and estimate; it grants no spending authority. Approve its reported run ID
only after the owner's authorization, then run it with the same edit/round selectors. Astra produces a labelled map,
per-item diagnosis and two or three shown amounts for every executable group, including moves, then stops. Review
that understanding with `job review JOB_ID --edit-id EDIT_ID --round u1` (use the returned round name for archived
or explicitly located rounds). Blocked items carry reasons
and must be resolved in a new understanding before a paid EDIT round.

When the owner picks an exact shown option, apply it without another paid interpretation:

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job edit-apply JOB_ID --edit-id EDIT_ID --option B --confirm-file C:/edits/confirmation.txt
```

The host checks the sealed operations, scope, physical constraints, print freshness, exact preview GLB bytes and AR
evidence on a fresh copy, then publishes an immutable `owner_applied` delivery. This is not Astra `accepted_finish`
or visual owner acceptance. Review and record a verdict using the returned round path (or edit/round selectors).
`--output` chooses a new copy destination; `--tool-test` keeps illustrative copies and their verdicts outside product
acceptance/resume. The M10 combined Prada copy was explicitly a tool test, not a delivered product.

For a separately approved paid EDIT round, `job edit JOB_ID --edit-id EDIT_ID --confirm-file FILE --option B
--cap 20 --settled-stop 20 --settled-only --effort high` prepares its own request. `--correct` and `--correct-file`
record a correction on that EDIT round; they do not broaden sealed authority or start a new understanding.
A changed target/support needs a fresh request, coverage, understanding and approval.
Plain text `--request` and `--confirm` are alternatives to verbatim UTF-8 files. The dev UI provides the orbit viewer;
`job review` remains the supported AR review command. The older M10 orbit viewer is ignored local evidence.

## CLI reference

`--help` is available at each level. `job --jobs-root DIR` precedes its subcommand and changes the local job root.
Job IDs may also be absolute job-directory paths. Each row lists flags accepted by that subcommand.

| Command | Flags |
|---|---|
| `job create` | `--images`, `--views`, `--name`, `--description` required; `--specs` optional |
| `job show` | `--edit-id`, `--round` |
| `job estimate` | `--edit-id`, `--round`, `--smoke`, `--resume`, `--effort` (`high`, `xhigh`, `max`), `--turns`, `--max-output-tokens`, `--cap`, `--settled-stop`, `--settled-only` |
| `job approve` | `--edit-id`, `--round`, `--run-id`, required `--max-usd` and `--owner-words` |
| `job run` | `--edit-id`, `--round`, `--run-id`, `--smoke`, `--resume`, `--foreground`, `--env-file` |
| `job stop` | `--edit-id`, `--round` |
| `job review` | `--edit-id`, `--round`, `--delivery-id`, `--port` (default 8251) |
| `job verdict` | positional `accept` or `changes`; required `--notes`; `--edit-id`, `--round`, `--delivery-id`, `--sha256` |
| `job migrate-verdicts` | `--source`, `--ledger` |
| `job edit` | `--delivery-id`, `--source-runtime`, `--edit-id`, `--option`, `--coverage`, one of `--request`, `--request-file`, `--confirm`, `--confirm-file`, `--correct`, `--correct-file`; `--effort`; required `--cap`, `--settled-stop`; `--settled-only` |
| `job edit-check` | `--request`, `--request-file`, `--mapping`, `--output` |
| `job edit-apply` | `--edit-id`, required `--option` and one of `--confirm`, `--confirm-file`; `--output`, `--tool-test` |
| `job edit-item` | positional delivered capture UNDERSTAND round path; required `--item`, `--cap`, `--settled-stop`; optional compatibility flag `--settled-only`; `--effort` (`high` only), `--output` |

`--turns` is an optional real reply cap; omitting it on ordinary estimates leaves no cap. `--max-output-tokens`
sets the response ceiling. `--smoke` prepares/runs a distinct one-request read-only smoke and requires matching
approval; it is not an ordinary-run shortcut. `--resume` must match the approved estimate. `--env-file` is passed
only to the runner's credential loader; never print or inspect its contents. `--source-runtime` selects an archived
runtime containing the exact delivery checkpoint/cameras. `--round` requires `--edit-id`.

## Offline desktop and assembly tests

The optional free desktop harness is `tests/proofs/desktop_automation.py`: `run_fresh` uses a fresh photo job,
an explicit file mailbox and user-supplied desktop responses at xhigh. It runs real tools with production-network
tripwires and unspendable offline approvals. Token counts, reasoning (unavailable, simulated as zero), cache usage
and money are explicitly simulated at the frozen tariff, including the settled stop and conversation renewal;
they are not measured subscription charges. Its module help documents the explicit offline-start switch and
mailbox parameters. This is a test harness, not the production `job run` transport.

For the $0 assembly proof:

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.assembly_m1
& glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.assembly_m3
```

It uses the real SDK with a scripted transport, blocks non-loopback networking, and runs real Blender, camera
fitting and fixed-scale AR capture. Its synthetic approval cannot authorize a production client. Proof artifacts
are local data and must never be committed. See [the test commands](../tests/README.md) for the full suite.
The M3 proof also reviews the immutable delivery through a real detached server, records synthetic change notes,
and resumes twice to verify exact-once note insertion and preservation of the original asset.
