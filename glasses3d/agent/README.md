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
`both` or `null`. Original bytes are retained. Exceptionally large reference payloads get a transport copy at the
same pixel dimensions; both hashes are bound into the approval.

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
`plan/PLAN.md`. Owners' descriptions and specifications remain verbatim and approval-bound. The job image policy
refreshes the request before request 1 and every three requests thereafter: source photos and crops remain,
superseded generated images become labelled notes, and the latest evidence per view and the selected best
checkpoint's images remain. `save_checkpoint` can mark that best checkpoint. The saved session keeps all images.

Job estimates use the eight complete recorded glasses runs and their measured context growth and prefix reuse.
The forecast names its corpus and limitations; it assumes no savings from image pruning. It is a planning estimate;
the live budget guard still uses the actual token count and worst-case reservation for every request.

For the $0 assembly proof:

```powershell
& glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.assembly_m1
```

It uses the real SDK with a scripted transport, blocks non-loopback networking, and runs real Blender, camera
fitting and fixed-scale AR capture. Its synthetic approval cannot authorize a production client. Proof artifacts
are local data and must never be committed. See [the test commands](../tests/README.md) for the full suite.
