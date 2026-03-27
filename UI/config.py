"""
Shared configuration: paths, constants, and in-memory session store.
"""

import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FACE_ANALYSIS_DIR = PROJECT_ROOT / "face_analysis"
CATALOG_DIR = str(PROJECT_ROOT / "lenses" / "catalog")
CATALOG_JSON = PROJECT_ROOT / "lenses" / "catalog" / "catalog.json"
CATALOG_IMAGES_DIR = PROJECT_ROOT / "lenses" / "catalog" / "images"

# face_analysis modules use sibling imports (from config import ...),
# so we add the directory to sys.path once.
if str(FACE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(FACE_ANALYSIS_DIR))

# ── Free Search constants (inlined to avoid config.py import conflicts) ──────
FS_MODEL_MAP = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
}
FS_DEFAULT_MODEL = "nano-banana-pro"
FS_MAX_IMAGE_DIM = 1536

# Default generation model alias (used by Smart Fit pipeline)
DEFAULT_GENERATION_MODEL = "nano-banana-pro"

# ── Lens Recolor constants ───────────────────────────────────────────────────
RECOLOR_MODEL_ALIAS = "nano-banana-pro"
RECOLOR_MODEL_NAME = "gemini-3-pro-image-preview"

# ── In-memory session store ──────────────────────────────────────────────────
sessions: dict[str, dict] = {}
