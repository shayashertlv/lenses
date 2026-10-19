# Running the glasses3d tests

From the repository root, use the project venv and install the pinned dependency lock:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -m pip install --require-hashes -r glasses3d/requirements.txt
```

Pytest is pinned to **9.0.3** because 9.1.1 can silently omit directory conftest autouse fixtures
when explicit file arguments interleave directories ([upstream #14635](https://github.com/pytest-dev/pytest/issues/14635)).
For agent tests this omits the offline flag, credential scrubbing, private paid-run lock root and network guards.
`test_pytest_isolation.py` reproduces agent/root/agent collection in a fresh subprocess using the real agent fixture,
and checks that the guards apply to each agent file and are restored outside that directory.
Keep that regression passing when updating pytest; setting `G3D_OFFLINE` in the launcher does not restore the other guards.

Run these Python suites sequentially:

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

These are saved initial component timings, not new D2 runs or time budgets. C2's initial partial proof reached
single-pick GLB equality and all checks, then refused publication; that historical result remains unchanged.
C2 follow-up 3 (`1a79b26`) subsequently verified immutable single-pick publication and the expected early refusal
for two same-part shading picks: 1,520.090 s overall, including 1,147.484 s (about 19 minutes) for UNDERSTAND.
Its focused batch passed 534 cases with 11 strict xfails in 120.73 s. Exact local normal writes remain future work;
the attempted writer repair failed the real Prada preview and was reverted. A successful separate same-part
dual apply is not claimed. B follow-up 2 (`fb7f5d5`) passed 20 Chromium tests in 30.287 s for facts-only money display.

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

D1's real-browser harness is `glasses3d/dev_ui/e2e/run.mjs`, driven by `tests/proofs/assembly_m12_ui.py`.
Its six scenarios passed (`b81f923`, `0da06ba`); evidence is `data/m12/proofs/d1b/proof.json` and
`data/m12/codex/m12_D1_report.md` Follow-up 1. The fresh refusal/single-result browser scenario took 162.284 s;
the native single apply was reused and reauthenticated, not rerun. Six unchanged scripted run receipts retain
31 inference requests plus 31 token counts, simulated $0.325, actual provider requests 0 and actual $0.
The audit covers new-run/AR, cutoff draft, browser survival/Stop, captures/choices/refusal/exact single apply,
fallback/new edit and duplicate/concurrent/stale refusals. The prior failed dual apply stays historical evidence.

For a fresh proof, choose an unused short output directory and an available port from 8290–8299:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -B -m glasses3d.tests.proofs.assembly_m12_ui --out C:/Users/Shay/PycharmProjects/lenses/glasses3d/data/m12/proofs/FRESH_D1 --port 8291
```

The command accepts `--plan`, `--phase prepare|browser|audit|all`, `--resume` and `--scenarios`; inspect `--help`
and the D1 report before resuming retained evidence. Check the real checkout paid-run lock before each scripted
run or Blender-heavy step; if held, wait without altering it. All native/logging/isolation rules above apply.
D3 reused D1's proof because its edits did not touch proof code. The separate pre-M12 historical temple-alias
regression introduced by M11 task 8 remains open, queued as M11 task 12; it was not repaired by this proof.

### D3 final verification record — 2026-10-19

Baseline `2340e95`; complete with documented limits. Exact commands, absolute paths, UTC/PID/exit receipts and logs are in
`data/m12/codex/m12_D3_report.md`; this record does not replace the three-pass method above.

| Check | Result |
|---|---|
| Functional, parallel, including dev UI | 5,614 passed / 2 skipped / 11 xfailed / 1 non-strict xpassed / 4 errors; 823.83 s, exit 1. |
| Unchanged camera-module retry, alone | 8 passed, 108.47 s, exit 0; all four WinError 6 fixture errors cleared. Four retry passes duplicate prior passes: 5,618 distinct functional passes. |
| Photoprint | 367 passed, 381.35 s, exit 0. Four pytest workers; at most two inner bake workers per fixture. |
| Serial timing | 29 passed, 260.41 s, exit 0; original budgets/deadlines unchanged. |
| Browser | 20 passed, 23.923 s, exit 0. |
| arview unit / QA / type check / build | 509 unit (20.559 s) / 113 QA (39.104 s) passed; check/build exit 0 (receipt intervals 1 s / 3 s); production capture 35/35 images. |
| Assembly M1 | Passed, 152.266 s, exit 0; 13 scripted requests, 26 transport receipts, 12 image blobs, 35 AR images, clean receipts, actual $0. `data/assembly/m1-proof-79a0b97e/proof.json`. |
| Final README/instruction contracts | 87 passed, 7.09 s, exit 0; supplementary checks overlap the functional pass. |

Final Python accounting: 6,014 distinct passes / 2 skips / 11 xfails / 1 non-strict XPASS across 6,028 collected cases; four duplicate camera-retry passes are not added. The first local launcher attempt exited 3 before running any test:
its temporary-directory parent was missing; only the ignored launcher was fixed. The normal-composition module
retains eleven strict expected failures for future exact local normal writes. The authored disjoint-overlap case
is `xfail(strict=False)` because task 11 observed four changed corners (880/894/939/955), max drift 4.3153763e-5,
where C2 observed a pass; source: `data/review_20261018/codex/task11_remaining_failure_diagnosis.md`. Its D3 XPASS
does not establish deterministic exactness. No assertion, tolerance, writer or hard gate changed. After D3's
required checks, the owner tries the UI and decides on paid validation.

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
