# Photo-to-3D jobs

Run from the repository root with `glasses3d/.venv/Scripts/python.exe -m glasses3d job`.
The local job directory contains original images, explicit views, scene checkpoints, camera fits, receipts and money
ledgers. Every supplied photo is authoritative. Runtime code contains no product presets.

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
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID --settled-only --settled-stop 20 --effort high
```

Creation, inspection and estimation are local and free. Estimation writes a new run request. `job approve JOB_ID
--run-id RUN_ID --max-usd AMOUNT --owner-words "Exact authorization"` records the owner's approval for that request
only. Run it only after the owner has authorized the actual paid run and seen its estimate. `job run JOB_ID --run-id
RUN_ID` refuses missing, changed, consumed or insufficient approvals and launches a detached logged runner.

Standing owner terms (2026-10-16/17) are high effort and a $20 settled-only cutoff: use the explicit estimate flags
above. Parent and specialist share settled spend; no request starts at or above $20. The in-flight response can
cross it. Estimated holds do not refuse or shrink requests in this mode; unknown charges still stop spending.
Legacy defaults remain max effort, a $20 settled stop and a $23 reserve cap; old reserve-mode approvals retain
that behavior. `--cap` in settled-only requests records the request/approval amount, not a reserve ceiling.
An estimate's 60-reply forecast is an assumption, not a reply limit or a spending guarantee. `job stop JOB_ID`
requests a stop before the next request. `--foreground` runs in the invoking terminal; detached execution is the
normal path. Logs and status are under `runs/RUN_ID/`; the heartbeat is at the job root.

Instructions teach the glasses construction, evidence and stage order, with the same model requirements as
`plan/PLAN.md`. Owners' descriptions and specifications remain verbatim and approval-bound. History stays
append-only: earlier messages and images are never replaced. New tool results use bounded text with lossless
`read_report` access, and native photo comparisons compose onto pages with additional images for overflow.
`save_checkpoint(best=True)` marks this run's draft source if it stops before accepted finish. The M9 safety valve
checks each candidate before admission: above 80% of the image/byte limit or long-context price step, it requests a
state summary and continues in a fresh conversation with every owner photo and verified current evidence. Old
conversation bytes remain unchanged. Summary requests use the same approval/money; unavailable summaries use host
facts. A mandatory seed exceeding hard limits still refuses. `job show` and delivery reports list renewal receipts.

Job estimates use the eight complete recorded glasses runs and their measured context growth and prefix reuse.
The forecast names its corpus and limitations; it assumes no savings from image pruning. It is a planning estimate;
the live guard counts every request and enforces its approval-bound settled-only or legacy reserve policy.

Finished runs publish immutable snapshots under `deliveries/`, including the model, validation and seating reports,
AR evidence, contact sheets, cost and checkpoint. `deliveries/current.json` identifies the latest snapshot;
`deliverable/model.glb` and `deliverable/report.json` remain convenient copies. Prior snapshots are never replaced.

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job review JOB_ID
& glasses3d/.venv/Scripts/python.exe -m glasses3d job verdict JOB_ID changes --notes "Exact owner feedback"
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID --resume --settled-only --settled-stop 20 --effort high
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

First diagnose each requested item against its selected source and create an explicit `g3d.edit_item_map/1` mapping.
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
Plain text `--request` and `--confirm` are alternatives to verbatim UTF-8 files. No production orbit-viewer command
exists: the M10 orbit viewer is ignored local evidence; `job review` is the supported AR review path.

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
