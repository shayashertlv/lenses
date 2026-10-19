"""UTF-8 at CLI/process boundaries, independent of the Windows console locale."""
from __future__ import annotations

import os
import sys


def configure_utf8_stdio() -> None:
    """Configure real text streams; StringIO/test capture already accepts Unicode.

    Changing PYTHONIOENCODING after startup does not change existing streams.
    Keep stderr's escaping of malformed Unicode, but preserve every valid character.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name)
        reconfigure = getattr(stream, "reconfigure", None)
        errors = "backslashreplace" if name == "stderr" else "strict"
        if (reconfigure is not None
                and (stream.encoding != "utf-8" or stream.errors != errors)):
            reconfigure(encoding="utf-8", errors=errors)


def utf8_env(source=None) -> dict:
    """Copy a child environment (or overrides) and force Python's UTF-8 policy."""
    env = {key: value for key, value in (os.environ if source is None else source).items()
           if key.upper() not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return env
