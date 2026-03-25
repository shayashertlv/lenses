"""Configuration, API keys, model names, paths, and defaults."""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
CATALOG_DIR = BASE_DIR.parent / "lenses" / "catalog"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
EMBEDDINGS_NPY = CATALOG_DIR / "embeddings.npy"
EMBEDDING_INDEX_JSON = CATALOG_DIR / "embedding_index.json"
CATALOG_IMAGES_DIR = CATALOG_DIR / "images"

# Model name mapping (Nano Banana)
MODEL_MAP = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
}

# Embedding model
EMBEDDING_MODEL = "gemini-embedding-001"

# Query interpreter model
QUERY_INTERPRETER_MODEL = "gemini-2.5-flash"

# Defaults
DEFAULT_MODEL = "nano-banana-pro"
DEFAULT_TOP_K = 3
MAX_IMAGE_DIMENSION = 4096
MIN_SIMILARITY_THRESHOLD = 0.3


def get_api_key() -> str:
    """Get the Gemini API key from os.environ, Windows registry, or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")
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


def resolve_model(model_alias: str) -> str:
    """Resolve a friendly model name to the actual model string."""
    if model_alias in MODEL_MAP:
        return MODEL_MAP[model_alias]
    if model_alias.startswith("gemini-"):
        return model_alias
    raise ValueError(
        f"Unknown model '{model_alias}'. "
        f"Choose from: {', '.join(MODEL_MAP.keys())}"
    )
