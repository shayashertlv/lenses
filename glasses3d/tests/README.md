# Running the glasses3d tests

From the repository root, use the project venv. Run these Python suites sequentially:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests --ignore=glasses3d/tests/photoprint -m "not timing" -n 8 --dist loadfile
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests/photoprint -m "not timing" -n 4 --dist loadfile
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests -m timing -n 0
```

All three passes are required. `timing` marks the unchanged performance budgets, heartbeat cadence/overlap checks
and Blender UV/variant-render checks whose fixed subprocess deadlines are sensitive to concurrent work. The serial
pass runs them on an otherwise idle machine; it does not skip or relax them. `slow` only labels duration and never
excludes a test.

`loadfile` keeps reusable expensive fixtures on one worker. The seeing/checkpoint tests establish their own state;
the refit test creates its own comparison baseline. The photo-print bake fixtures use at most two child workers
each, so that suite runs separately with four pytest workers. Do not overlap these suites with another
process-pool workload. Eight workers is a conservative example for the first command; size it to measured free
commit memory (at most 14 here), and reduce either parallel pool if memory requires it. Follow the detached logging,
BLAS 1 and EcoQoS instructions in [AGENTS.md](../AGENTS.md). Never retry to select a favourable timing result:
investigate failures and record environment limits explicitly.

The AR viewer checks run sequentially from `glasses3d/arview`: `npm test`, `npm run test:qa`, `npm run check`, and
`npm run build`. The QA pass includes a real production capture. The $0 assembly proof runs from the repository
root: `glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.assembly_m1`; its synthetic jobs stay inside its
private proof directory, and its approvals cannot authorize production transport.

## Golden photo_v1 baseline (2026-10-18)

`test_render_dome.py` uses `data/wp6v2/golden_photo_v1/golden_20261018_task8.json`, captured with
`tests/proofs/capture_golden_render.py --tag 20261018_task8 --commit 308f4bf`. This is the reviewed task-8 code,
not uncommitted renderer changes. Old `golden_a.json` and its images remain intact. Blender 5.2.0 LTS build
`fbe6228777e7`, Intel Arc 140T / OpenGL; the capture records scene/camera hashes and Blender build metadata.

Two independent causes required recapture. Re-running the original `62b25e4` renderer on the same scene and cameras
reproduced the old baseline's environment mismatch: 33–199 changed native pixels and 30–232 clay pixels per view,
at most 6 levels. Task 8 (`4c5bf5c`) separately disabled dithering for numeric normal passes; compared with the
original renderer in this environment, 39,547–65,393 normal pixels per view change (usually one level). That
intentional change is retained. The first refreshed capture is the baseline; a second checks repeat noise.
The limits stay **0.0005 × image area changed pixels and mean error < 0.01**. Full 18-pass before/after tables,
hashes and repeat checks are in `data/review_20261018/codex/task9_golden*.json`; no render is committed.
