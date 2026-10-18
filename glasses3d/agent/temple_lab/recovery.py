"""Private-scene recovery keeps fatal worker errors and never resumes an unverified head."""
from ..journal import WorkerFailed
from ..loop import FATAL_TOOL_ERRORS


def restore_head(session, head, call_id, *, original=None):
    """Restore a recorded head; failed cleanup cannot hide the original fatal exception."""
    try:
        result = session.restore_checkpoint(head["id"], call_id=call_id)
        if result.get("status") != "ok":
            raise WorkerFailed(f"private checkpoint recovery refused: {result}", session.head)
        if session.fingerprint() != head["fingerprint"]:
            raise WorkerFailed("private checkpoint recovery fingerprint differs from the original head", session.head)
        return result
    except Exception as recovery:
        detail = f"Checkpoint recovery also failed: {type(recovery).__name__}: {recovery}"
        if original is not None and isinstance(original, FATAL_TOOL_ERRORS):
            original.add_note(detail)
            raise original
        if isinstance(recovery, FATAL_TOOL_ERRORS):
            raise
        raise WorkerFailed(detail, session.head) from (original or recovery)
