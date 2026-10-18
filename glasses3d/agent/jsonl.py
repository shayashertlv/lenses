"""Crash-tolerant JSONL records, delimited only by physical LF bytes.

Complete records are strict. Only an invalid final, unterminated record may be
ignored; its bytes are preserved before a subsequent append repairs that tail.
Callers serialize writers, as for the checkpoint index and the receipt log.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JsonlRead:
    rows: list
    recovery: dict | None = None
    needs_newline: bool = False


def read_jsonl(path, *, repair_torn_tail: bool = False) -> JsonlRead:
    """Read records and report a torn tail without accepting corruption earlier in the file.

    With ``repair_torn_tail``, save the rejected bytes beside the log and truncate
    to the exact original good prefix. The original bytes otherwise stay intact.
    A valid final record without LF is retained and separated on the next append.
    """
    path = Path(path)
    payload = path.read_bytes() if path.is_file() else b""
    parts = payload.split(b"\n")
    rows = []
    offset = 0
    recovery = None
    for number, line in enumerate(parts, 1):
        if line.strip():
            try:
                rows.append(json.loads(line.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if number != len(parts) or payload.endswith(b"\n"):
                    raise
                recovery = {"kind": "torn_jsonl_tail", "path": str(path), "line": number,
                            "offset": offset, "bytes": len(line),
                            "error": f"{type(error).__name__}: {error}", "repaired": False}
                if repair_torn_tail:
                    digest = hashlib.sha256(line).hexdigest()
                    archive = path.with_name(path.name + f".torn-{digest[:16]}.bin")
                    if archive.exists():
                        if archive.read_bytes() != line:
                            raise OSError(f"torn JSONL archive does not match: {archive}")
                    else:
                        # Publish only complete evidence. A crash while preserving
                        # the tail must not leave a partial archive that blocks repair.
                        from .procs import replace_with_retry
                        fd, name = tempfile.mkstemp(prefix=".jtail-", suffix=".tmp", dir=path.parent)
                        temporary = Path(name)
                        try:
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(line)
                                stream.flush()
                                os.fsync(stream.fileno())
                            replace_with_retry(temporary, archive)
                        finally:
                            temporary.unlink(missing_ok=True)
                    with path.open("r+b") as stream:
                        stream.truncate(offset)
                        stream.flush()
                        os.fsync(stream.fileno())
                    recovery.update(repaired=True, archive=str(archive))
                break
        offset += len(line) + 1
    return JsonlRead(rows, recovery, bool(payload and not payload.endswith(b"\n") and recovery is None))


def append_jsonl(path, row, *, default=None) -> dict | None:
    """Append one durable record, preserving and repairing a torn tail if needed.

    Return the recovery report so the caller can surface it in its diagnostics.
    Serialize before any repair: an invalid new row must not change the log.
    """
    path = Path(path)
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         default=default).encode("utf-8") + b"\n"
    # Ordinary appends are O(1) in log size. Complete records were validated when
    # read, and appending must never rewrite them. Only a missing LF needs repair.
    current = JsonlRead([])
    if path.is_file():
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if size:
                stream.seek(-1, os.SEEK_END)
                needs_tail_read = stream.read(1) != b"\n"
            else:
                needs_tail_read = False
        if needs_tail_read:
            current = read_jsonl(path, repair_torn_tail=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        if current.needs_newline:
            stream.write(b"\n")
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return current.recovery
