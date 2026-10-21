"""Short, isolated process directories for native lab captures on Windows.

The production helper already takes its process/log directory separately from
the CLI's image output directory. Reuse that interface; do not patch production
globals, browser flags, rendering, timeouts or image destinations.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile

from .harness import DATA, now


def short_run_capture(argv: list[str], out: Path, *, timeout_s: float):
    from glasses3d.agent.tools.arview import run_capture

    destination = Path(out).resolve()
    base = DATA.resolve()
    if destination == base or not destination.is_relative_to(base):
        raise ValueError("capture process output must stay inside lens-lab data")
    destination.mkdir(parents=True, exist_ok=True)
    scratch_base = (base / "_capture").resolve()
    if not scratch_base.is_relative_to(base):
        raise ValueError("capture scratch directory escaped lens-lab data")
    scratch_base.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="c-", dir=scratch_base)).resolve()
    record = {"schema": "g3d.lens_lab.capture_process/1", "started_at": now(),
              "process_directory": str(scratch), "output_directory": str(destination),
              "temp_directory": str(scratch / "temp"), "timeout_s": timeout_s}
    try:
        result = run_capture(argv, scratch, timeout_s=timeout_s)
        record.update(exit_code=result[0], timed_out=result[1])
        return result
    except BaseException as error:
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # The native helper has closed its kill-on-close process job before this
        # point. Preserve its log verbatim, then remove only our checked scratch.
        log = scratch / "capture.log"
        if log.is_file():
            # look.capture reuses one process directory for several native
            # batches; preserve the production helper's append semantics.
            with log.open("rb") as source, (destination / "capture.log").open("ab") as target:
                shutil.copyfileobj(source, target)
        record["finished_at"] = now()
        resolved = scratch.resolve()
        if resolved != scratch_base and resolved.is_relative_to(scratch_base):
            try:
                shutil.rmtree(resolved)
                record["scratch_removed"] = True
            except OSError as error:
                record.update(scratch_removed=False, cleanup_error=f"{type(error).__name__}: {error}")
        else:
            record.update(scratch_removed=False, cleanup_error="scratch containment changed")
        (destination / f"lab-capture-process-{scratch.name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
