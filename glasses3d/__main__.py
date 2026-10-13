"""The local photo-to-3D job command."""
import os

# Fit workers parallelize across processes, so BLAS must not multiply their CPU allocation.
for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

from .agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
