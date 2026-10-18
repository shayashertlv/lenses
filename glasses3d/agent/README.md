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
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID
```

Creation, inspection and estimation are local and free. Estimation writes a new run request. `job approve JOB_ID
--run-id RUN_ID --max-usd AMOUNT --owner-words "Exact authorization"` records the owner's approval for that request
only. Run it only after the owner has authorized the actual paid run and seen its estimate. `job run JOB_ID --run-id
RUN_ID` refuses missing, changed, consumed or insufficient approvals and launches a detached logged runner.

Defaults are max reasoning effort, no new request at $20 settled, and no reservation above $23. The settled stop
and reservation cap are separate; an already admitted request may settle above the stop within the reserved cap.
An estimate's 60-reply forecast is an assumption, not a reply limit or a spending guarantee. `job stop JOB_ID`
requests a stop before the next request. `--foreground` runs in the invoking terminal; detached execution is the
normal path. Logs and status are under `runs/RUN_ID/`; the heartbeat is at the job root.

Instructions teach the glasses construction, evidence and stage order, with the same model requirements as
`plan/PLAN.md`. Owners' descriptions and specifications remain verbatim and approval-bound. History stays
append-only: earlier messages and images are never replaced. New tool results use bounded text with lossless
`read_report` access, and native photo comparisons compose onto pages with additional images for overflow.
`save_checkpoint(best=True)` marks this run's draft source if it stops before accepted finish. M9's planned
fresh-conversation safety valve is not yet implemented; the current payload limit can still end a long run.

Job estimates use the eight complete recorded glasses runs and their measured context growth and prefix reuse.
The forecast names its corpus and limitations; it assumes no savings from image pruning. It is a planning estimate;
the live budget guard still uses the actual token count and worst-case reservation for every request.

Finished runs publish immutable snapshots under `deliveries/`, including the model, validation and seating reports,
AR evidence, contact sheets, cost and checkpoint. `deliveries/current.json` identifies the latest snapshot;
`deliverable/model.glb` and `deliverable/report.json` remain convenient copies. Prior snapshots are never replaced.

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d job review JOB_ID
& glasses3d/.venv/Scripts/python.exe -m glasses3d job verdict JOB_ID changes --notes "Exact owner feedback"
& glasses3d/.venv/Scripts/python.exe -m glasses3d job estimate JOB_ID
```

Review starts a detached AR server with the fixed lens-reflection default and records the delivery actually served.
The verdict command binds to that reviewed delivery even if a newer run has finished. `accept` records acceptance;
`changes` requires notes. An explicit `--delivery-id` or `--sha256` can select an asset without a prior review.
Every verdict binds the model and manifest hashes; damaged evidence is refused. Review servers expose a loopback URL
for the owner's webcam and survive the launching command.

Change notes enter the next run verbatim. After authorizing its new estimate with `job approve`, use
`job run JOB_ID --run-id RUN_ID --resume` to retain the conversation. Notes already in that saved conversation are
not inserted again. Adding a note after approval invalidates that approval, so estimate and approve again.

`job migrate-verdicts` preserves the original legacy ledger, normalizes whole-product acceptances into
`data/ledger/verdicts.jsonl`, and archives every original record with its hash. Scoped print or method decisions
remain scoped evidence. Repeating migration verifies the same outputs without duplicating records.

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
