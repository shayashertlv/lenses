"""Local resume planning over the exact saved SDK history; no provider calls."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from . import verdicts
from .receipts import BlobStore


def saved_items(root, *, recovery=False):
    """Use the same SDK/blob reader as execution, also when called from an async job runner."""
    from .loop import BlobSession, SESSION_ID
    root = Path(root)
    path = root / "session/session.sqlite"
    if not path.is_file():
        raise ValueError("resume needs this job's session/session.sqlite, which does not exist")

    async def read():
        session = BlobSession(SESSION_ID, path, blobs=BlobStore(root / "blobs"))
        try:
            return await session.recovery_items() if recovery else await session.get_items()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(read())).result()


def binding(root, resume, *, items=None):
    if not resume:
        return {"resume": False}
    items = saved_items(root) if items is None else items
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    recovery = saved_items(root, recovery=True)
    recovery_raw = json.dumps(recovery, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"resume": True, "history_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "recovery_history_sha256": hashlib.sha256(recovery_raw.encode("utf-8")).hexdigest(),
            "history_items": len(items), "reviewed_checkpoint": verdicts.resume_checkpoint(root, items)}


def scene_note(bound):
    target = bound.get("reviewed_checkpoint")
    if target:
        return (f"Resume from the owner's reviewed delivery {target['delivery_id']}: checkpoint "
                f"{target['id']}, fingerprint {target['fingerprint']}. The host restores that reviewed "
                "state before this run; earlier conversation experiments remain in history.")
    return "Resume the saved head after recovering any interrupted tool call; no new delivery review selects another state."


def context_estimate(items, *, instructions, tools, brief, photos, notes, invocation):
    """Price all replayed text/images plus only new input, using the existing offline planning model.

    This is explicitly a local forecast, not the guard's live provider token count.
    Base64 is excluded from text, and every replayed image occurrence is counted.
    """
    from .replay import estimate_tokens
    from .session_inputs import plan_session_input
    plan = plan_session_input(items, prompt=brief, photos=photos, invocation_text=invocation, owner_notes=notes)
    payload = {"instructions": instructions,
               "tools": [{"name": t.name, "description": t.description, "parameters": t.params_json_schema}
                         for t in tools],
               "input": [*items, {"role": "user", "content": plan.content}]}
    return estimate_tokens(payload)
