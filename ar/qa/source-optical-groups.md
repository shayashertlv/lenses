# Source-preserving optical group checks

> Historical prototype evidence. The production implementation is `src/render/effective-optical-topology.ts`
> and `src/render/nearest-optical-groups.ts`; see [production-optical-groups.md](production-optical-groups.md).

These are isolated geometry/transport experiments. They use explicit control
materials and unverified source group identities; no photo appearance is recovered.
Production rendering and the original synthetic harness are unchanged.

Build a new private bundle from `automation/`:

```powershell
python -m qa.source_optical_groups --output data/source-optical-groups-reproduced
```

From `ar/`, run the original arithmetic or the explicit fixed Taylor24 override
against that same bundle, with a new output directory for each:

```powershell
node qa/source-optical-groups.mjs --bundle=../automation/data/source-optical-groups-reproduced --output=qa/output/source-groups-builtin-reproduced
node qa/source-optical-groups.mjs --bundle=../automation/data/source-optical-groups-reproduced --angle-math=taylor24 --output=qa/output/source-groups-taylor-reproduced
```

The builtin run retains its report and exits nonzero on the tested backend because
four color cases miss the fixed tolerance. The Taylor run passed all thirty saved
real-source cases. Both use the same CPU oracle and thresholds: 5e-5 linear RGB
and 2e-6 depth. Missing group visibility, perspective and real-source light/receiver
coverage remain explicitly unmeasured. Timings collected during other CPU work
are diagnostic, not performance benchmarks.

The override requires the exact known optical-fragment expression, records
original/transformed/helper hashes and verifies that Vite applied it. The helper
uses the coefficients fixed in the separate million-sample angle probe; it is not
a product-specific correction.

Saved evidence:

- `qa/output/source-optical-groups-v2/`: original arithmetic, strict color failure.
- `qa/output/acos-precision-v1/`: raw float32 inputs/outputs and fixed-formula error.
- `qa/output/source-optical-groups-taylor24-v2/`: all thirty real-source cases pass.
- `qa/output/effective-optical-groups-taylor24-v1/`: all synthetic color checks
  pass; light depth still fails its unchanged threshold.

See `automation/plan/OPTICAL_GROUPS.md` for the representation contract, numerical
limits and requirements before production adoption.
