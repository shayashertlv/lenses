# Running the glasses3d tests

From the repository root, use the project venv. Run these Python suites sequentially:

```powershell
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest glasses3d/tests --ignore=glasses3d/tests/photoprint -n 8 --dist loadfile
& .\glasses3d\.venv\Scripts\python.exe -B -m pytest glasses3d/tests/photoprint -n 2 --dist loadfile
```

`loadfile` keeps tests using a shared Blender session on the same worker. The photo-print bake fixtures use at most
two child workers each, so the photo-print suite runs separately with two pytest workers. Do not overlap these
suites with another process-pool workload. Check free commit memory and follow the background-run logging and
BLAS/EcoQoS instructions in [AGENTS.md](../AGENTS.md). If load-sensitive wall-clock tests or the seeing module fail,
rerun the affected tests alone before diagnosing a product failure.

The AR viewer checks run from `glasses3d/arview`: `npm test`, `npm run test:qa`, and `npm run check`.
