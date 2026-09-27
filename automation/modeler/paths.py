"""Locations and external programs used by the modeler (no network, no credentials)."""
from __future__ import annotations

import os
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parents[1]
REPO = AUTOMATION.parent
AR = REPO / "ar"
DATA = AUTOMATION / "data"
MODELER_DATA = Path(os.environ["MODELER_DATA_DIR"]).resolve() if os.environ.get("MODELER_DATA_DIR") else DATA / "modeler"
JOBS = MODELER_DATA / "jobs"
BLENDER_DIR = Path(__file__).resolve().parent / "blender"          # scripts that run INSIDE Blender

DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


def blender_executable() -> Path | None:
    """The Blender binary: ``MODELER_BLENDER`` (env) or the default install; None when absent."""
    candidate = Path(os.environ.get("MODELER_BLENDER", str(DEFAULT_BLENDER)))
    return candidate if candidate.is_file() else None
