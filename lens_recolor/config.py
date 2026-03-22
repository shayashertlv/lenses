"""Configuration, model names, and defaults for lens recolor."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Model name mapping
MODEL_MAP = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
}

DEFAULT_MODEL = "nano-banana-2"
DEFAULT_INTENSITY = "medium"
DEFAULT_FINISH = "standard"
DEFAULT_PRESERVE_REFLECTIONS = True

# Image constraints
MAX_IMAGE_DIMENSION = 4096

# API key
def get_api_key() -> str:
    """Get the Gemini API key from environment."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        # Try loading from .env file
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


def resolve_model(model_alias: str) -> str:
    """Resolve a friendly model name to the actual model string."""
    if model_alias in MODEL_MAP:
        return MODEL_MAP[model_alias]
    # Allow passing the raw model string directly
    if model_alias.startswith("gemini-"):
        return model_alias
    raise ValueError(
        f"Unknown model '{model_alias}'. "
        f"Choose from: {', '.join(MODEL_MAP.keys())}"
    )
