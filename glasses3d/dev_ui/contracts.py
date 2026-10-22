"""Shared configuration and boundary validation for the frozen M12 HTTP contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[2]
KEY = re.compile(r"[A-Za-z0-9_-]{6,64}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass
class Config:
    port: int = 8260
    repo_root: Path = REPO
    state_root: Path = REPO / "glasses3d/data/dev_ui"
    jobs_root: Path = REPO / "glasses3d/data/jobs"
    static_root: Path = Path(__file__).parent / "static"
    vendor_root: Path = REPO / "glasses3d/arview/node_modules/three"
    python: Path = REPO / "glasses3d/.venv/Scripts/python.exe"
    # Passed by path to `job run --env-file`; the UI never reads it. Without it a run depends on whoever started the
    # server having OPENAI_API_KEY in their environment (10-22: a server restarted from a coding shell refused runs).
    env_file: Path | None = REPO / "glasses3d/.env"
    max_request_bytes: int = 128 * 1024 * 1024
    max_photo_bytes: int = 24 * 1024 * 1024
    max_png_bytes: int = 20 * 1024 * 1024
    max_capture_pixels: int = 40_000_000

    def __post_init__(self):
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        for name in ("repo_root", "state_root", "jobs_root", "static_root", "vendor_root", "python"):
            setattr(self, name, Path(getattr(self, name)).resolve())
        if self.env_file is not None:
            self.env_file = Path(self.env_file).resolve()

    @property
    def data_root(self):
        return self.state_root


class APIError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def cutoff(value):
    if not isinstance(value, str) or len(value) > 100:
        raise APIError("invalid_cutoff", "Max budget must be a positive finite decimal amount.")
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise APIError("invalid_cutoff", "Max budget must be a positive finite decimal amount.") from None
    return str(number)


def json_object(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def bad(value):
        raise ValueError("non-finite JSON value")
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=bad)
    except (ValueError, TypeError, UnicodeError):
        raise APIError("invalid_json", "Send a valid JSON object with unique keys and finite numbers.") from None
    if not isinstance(result, dict):
        raise APIError("invalid_json", "Send a JSON object.")
    return result


def text_field(value, name, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise APIError("invalid_input", f"{name} must be {'nonempty ' if nonempty else ''}text.")
    return value


def opaque(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise APIError("not_found", "That item was not found.", 404)
    return value


def confined(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise APIError("not_found", "File not found.", 404)
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or any(p.startswith(".") for p in path.parts):
        raise APIError("not_found", "File not found.", 404)
    candidate = root / path
    if candidate.resolve() != candidate.absolute() or not candidate.is_file() or not candidate.resolve().is_relative_to(root):
        raise APIError("not_found", "File not found.", 404)
    return candidate
