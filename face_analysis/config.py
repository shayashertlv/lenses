"""Configuration — API keys, model names, paths to optimal_config catalog."""

import os
from pathlib import Path


# Paths
BASE_DIR = Path(__file__).parent

# Path to lenses catalog (relative to face_analysis/)
CATALOG_DIR = os.path.join(os.path.dirname(__file__), "..", "lenses", "catalog")
CATALOG_JSON = os.path.join(CATALOG_DIR, "catalog.json")
CATALOG_IMAGES_DIR = os.path.join(CATALOG_DIR, "images")

# Generation models (image generation — Nano Banana)
GENERATION_MODEL_MAP = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
}

# Embedding model (shared across modules for consistent embeddings)
EMBEDDING_MODEL = "gemini-embedding-001"

# Defaults
DEFAULT_ANALYSIS_MODEL = "gemini-2.5-flash"
DEFAULT_GENERATION_MODEL = "nano-banana-pro"
MAX_IMAGE_DIMENSION = 1536
MIN_SIMILARITY_THRESHOLD = 0.3


def get_api_key() -> str:
    """Get the Gemini API key from os.environ, Windows registry, or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")

    # Fallback: on Windows, read from user/system env vars in the registry.
    # Env vars set via `setx` or System Properties aren't inherited by processes
    # spawned from a different process tree (e.g. preview tools, IDEs).
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
                key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
        except (FileNotFoundError, OSError):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as rk:
                    key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
            except (FileNotFoundError, OSError):
                pass
        if key:
            os.environ["GEMINI_API_KEY"] = key

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
            "  Railway/Render: add GEMINI_API_KEY in the service's Variables tab, then redeploy.\n"
            "  setx GEMINI_API_KEY your-key-here       (Windows — permanent)\n"
            "  export GEMINI_API_KEY='your-key-here'    (Linux/Mac)"
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
    if not os.path.isdir(CATALOG_IMAGES_DIR):
        missing.append("images/")

    if missing:
        return (
            f"Inventory catalog not found or incomplete. "
            f"Missing: {', '.join(missing)} in {CATALOG_DIR}\n"
            f"Make sure lenses/catalog/ contains catalog.json and images/.\n"
            f"Run: cd ../lenses && python catalog_manager.py build"
        )
    return None
