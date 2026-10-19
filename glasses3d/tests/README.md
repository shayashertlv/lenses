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

The first pass includes `glasses3d/tests/dev_ui` and the M12 capture/choice/fallback tests under
`glasses3d/tests/agent`; do not omit them when splitting the suite into explicit paths.

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

## M12 dev UI and edit proofs

The [dev UI README](../dev_ui/README.md) describes the owner flow and module map. For focused backend and browser
checks, run these from the repository root, sequentially:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest -c glasses3d/pytest.ini glasses3d/tests/dev_ui -n 0
node --test glasses3d/dev_ui/browser_tests/
```

The browser command uses Chromium and the existing Three/Playwright packages in `glasses3d/arview/node_modules`.
It tests the pages and annotation tools against a mock API; it does not start a paid run. Core cases under
`glasses3d/tests/agent/` are `test_owner_captures.py`, `test_m12_capture_transport.py`, `test_m12_mark_targets.py`,
`test_m12_choices_*.py` and `test_m12_fallback*.py`.

Recorded M12 measurements from 2026-10-19, retained in `data/m12/codex/m12_{A,B,C1,C2,C3}_report.md`:

| Check | Recorded result and duration |
|---|---|
| A backend/API/lifecycle, `tests/dev_ui`, `-n 4 --dist loadfile` | 249 passed, 11.18 s. Includes detached UI start/status/stop. |
| B browser tests, final follow-up | 16 passed, 22.672 s. Mock API, real Chromium and capture assertions. |
| C1 capture/core regressions, serial | 541 passed, 70.56 s; later instruction/transport wording checks: 104 passed, 8.80 s. |
| C2 choices/core regressions, serial | 459 passed, 50.69 s before final repairs; final sealing/apply batch: 141 passed, 16.89 s; final cleanup/legacy-apply batch: 120 passed, 6.27 s. These batches overlap. |
| C3 fallback plus capture regressions, final follow-up, serial | 244 passed, 36.01 s. |
| `tests/proofs/assembly_m12_fallback.py` | Passed, 113.672 s: real scripted SDK/Blender/AR, finished fallback, side effects and simulated-cutoff draft. |
| `tests/proofs/assembly_m12_choices.py` and retained C2 diagnostic harnesses | Partial native proof: mixed UNDERSTAND 407.407 s; exact single-pick apply 139.093 s before publication refusal. A successful two-previewed-item native apply remains unproved in the C2 report. |

These are saved timings, not new D2 runs or time budgets. C2's partial proof reached exact single-pick GLB
equality and all checks, then refused the application schema at publication; its report assigns that hook to C3.
That record is not a completed immutable delivery or a successful multi-pick proof. Later integration must be
reported separately. Component counts do not establish full browser-to-pipeline coverage.

The M12 native entry points are `tests/proofs/assembly_m12_*.py`. Inspect their actual arguments before running:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -B -m glasses3d.tests.proofs.assembly_m12_choices --help
& .\glasses3d\.venv\Scripts\python.exe -B -m glasses3d.tests.proofs.assembly_m12_fallback --help
```

Choices requires an audited `--plan` JSON and `--out`; `--phase` is `prepare`, `understand`, `apply` or `all`.
Fallback requires `--out` and accepts `--source`. Use fresh, short output paths under `glasses3d/data/m12/`,
copies of saved deliveries, scripted Astra and offline approvals only. Run native proofs one at a time with
BLAS threads 1, detached logs/PIDs/exit receipts, the memory limits in AGENTS, and no timeout wrapper. Their
simulated ledger amounts are not actual spending or paid-run forecasts.

D1 owns the real-browser integration harness at `glasses3d/dev_ui/e2e/` and its UI assembly proof under
`tests/proofs/assembly_m12_*.py`. **Pending as of 2026-10-19:** that end-to-end result and duration, the final
three Python passes, arview suites/check/build and M1 for the completed M12 integration. Those paths are D1's
assigned destinations; do not infer a runnable command or a pass until its harness and report are available.
Then the owner tries the UI; paid validation remains the owner's decision.

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
