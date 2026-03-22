"""Configuration — API keys, model names, paths to optimal_config catalog."""

import os
from pathlib import Path


# Paths
BASE_DIR = Path(__file__).parent

# Path to lenses catalog (relative to face_analysis/)
CATALOG_DIR = os.path.join(os.path.dirname(__file__), "..", "lenses", "catalog")
CATALOG_JSON = os.path.join(CATALOG_DIR, "catalog.json")
EMBEDDINGS_NPY = os.path.join(CATALOG_DIR, "embeddings.npy")
EMBEDDING_INDEX = os.path.join(CATALOG_DIR, "embedding_index.json")
CATALOG_IMAGES_DIR = os.path.join(CATALOG_DIR, "images")

# Analysis model (text/vision — NOT image generation)
ANALYSIS_MODEL = "gemini-2.5-flash"

# Embedding model
EMBEDDING_MODEL = "gemini-embedding-001"

# Generation models (image generation — Nano Banana)
GENERATION_MODEL_MAP = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
}

# Defaults
DEFAULT_ANALYSIS_MODEL = "gemini-2.5-flash"
DEFAULT_GENERATION_MODEL = "nano-banana-2"
MAX_IMAGE_DIMENSION = 4096
MIN_SIMILARITY_THRESHOLD = 0.3


def get_api_key() -> str:
    """Get the Gemini API key from environment."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            load_dotenv(BASE_DIR.parent / ".env")
            key = os.environ.get("GEMINI_API_KEY", "")
        except ImportError:
            pass
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Get your API key from: https://aistudio.google.com/apikey\n"
            "Then set it:\n"
            "  export GEMINI_API_KEY='your-key-here'  (Linux/Mac)\n"
            "  set GEMINI_API_KEY=your-key-here       (Windows)"
        )
    return key


def resolve_generation_model(model_alias: str) -> str:
    """Resolve a friendly generation model name to the actual model string."""
    if model_alias in GENERATION_MODEL_MAP:
        return GENERATION_MODEL_MAP[model_alias]
    if model_alias.startswith("gemini-"):
        return model_alias
    raise ValueError(
        f"Unknown generation model '{model_alias}'. "
        f"Choose from: {', '.join(GENERATION_MODEL_MAP.keys())}"
    )


def resolve_analysis_model(model_alias: str) -> str:
    """Resolve analysis model name."""
    if model_alias.startswith("gemini-"):
        return model_alias
    raise ValueError(
        f"Unknown analysis model '{model_alias}'. "
        f"Must be a gemini- model string (e.g. 'gemini-2.5-flash')."
    )


def validate_catalog_exists() -> str | None:
    """
    Check that the optimal_configuration catalog is set up.
    Returns an error message string, or None if everything is present.
    """
    missing = []
    if not os.path.isfile(CATALOG_JSON):
        missing.append("catalog.json")
    if not os.path.isfile(EMBEDDINGS_NPY):
        missing.append("embeddings.npy")
    if not os.path.isfile(EMBEDDING_INDEX):
        missing.append("embedding_index.json")
    if not os.path.isdir(CATALOG_IMAGES_DIR):
        missing.append("images/")

    if missing:
        return (
            f"Inventory catalog not found or incomplete. "
            f"Missing: {', '.join(missing)} in {CATALOG_DIR}\n"
            f"Make sure lenses/catalog/ contains catalog.json, "
            f"embeddings.npy, and embedding_index.json.\n"
            f"Run: cd ../lenses && python catalog_manager.py build"
        )
    return None
