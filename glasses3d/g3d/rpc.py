"""Length-prefixed JSON frames between the host and the g3d Blender worker (glasses3d/plan/archive/20261019/WP2_DESIGN.md §3). Stdlib only: imported by
the worker inside Blender and by agent/worker_client.py on the host.

A frame is a 4-byte big-endian length followed by that many bytes of UTF-8 JSON (one object, at most 64 MiB, NaN and
Infinity refused both ways). Requests are `{id, method, params, deadline_s}` (the first frame is `{"method": "hello",
"token": T}`); responses are `{id, ok, result | error{type, message, traceback}, elapsed_s}`. Ids strictly increase and a
response must carry its request's id. Images never cross the socket: methods write files under the job root and return
paths and sha256.
"""
from __future__ import annotations

import json
import math
import socket
import struct

MAX_FRAME_BYTES = 64 << 20
HEADER = struct.Struct(">I")
SCHEMA = "g3d.rpc/1"


class FrameError(Exception):
    """A frame that cannot be read or written (bad length, bad JSON, wrong shape)."""


class PeerClosed(FrameError):
    """The other side closed the connection (EOF)."""


def _reject_constant(name: str):
    raise FrameError(f"non-finite number {name} in a frame")


def encode(obj) -> bytes:
    """Header + UTF-8 JSON of `obj` (a dict); FrameError when it is not JSON-able, not finite or too large."""
    if not isinstance(obj, dict):
        raise FrameError(f"a frame is a JSON object, not {type(obj).__name__}")
    try:
        data = json.dumps(obj, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FrameError(f"frame not encodable: {exc}") from None
    if len(data) > MAX_FRAME_BYTES:
        raise FrameError(f"frame of {len(data)} bytes exceeds {MAX_FRAME_BYTES}")
    return HEADER.pack(len(data)) + data


def decode(data: bytes) -> dict:
    try:
        obj = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except FrameError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrameError(f"frame is not UTF-8 JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise FrameError(f"a frame is a JSON object, not {type(obj).__name__}")
    return obj


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise PeerClosed(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return bytes(buf)


def send_frame(sock: socket.socket, obj: dict) -> int:
    data = encode(obj)
    sock.sendall(data)
    return len(data)


def recv_frame(sock: socket.socket, max_bytes: int = MAX_FRAME_BYTES) -> dict:
    (n,) = HEADER.unpack(recv_exact(sock, HEADER.size))
    if n == 0:
        raise FrameError("empty frame")
    if n > max_bytes:
        raise FrameError(f"frame of {n} bytes exceeds {max_bytes}")
    return decode(recv_exact(sock, n))


# ------------------------------------------------------------------------------------------------ message shapes
def request(req_id: int, method: str, params: dict | None = None, deadline_s: float | None = None) -> dict:
    return {"id": req_id, "method": method, "params": params or {}, "deadline_s": deadline_s}


def check_request(msg: dict, last_id: int) -> str | None:
    """Why `msg` is not an acceptable request after `last_id`, or None."""
    rid = msg.get("id")
    if not isinstance(rid, int) or isinstance(rid, bool):
        return "request id must be an integer"
    if rid <= last_id:
        return f"request id {rid} does not increase (last {last_id})"
    if not isinstance(msg.get("method"), str):
        return "request method must be a string"
    if not isinstance(msg.get("params", {}), dict):
        return "request params must be an object"
    dl = msg.get("deadline_s")
    if dl is not None and (not isinstance(dl, (int, float)) or isinstance(dl, bool) or not dl > 0):
        return "deadline_s must be a positive number or null"
    return None


def ok(req_id, result, elapsed_s: float) -> dict:
    return {"id": req_id, "ok": True, "result": result, "elapsed_s": round(float(elapsed_s), 4)}


def error(req_id, etype: str, message: str, tb: str | None = None, elapsed_s: float = 0.0) -> dict:
    return {"id": req_id, "ok": False, "error": {"type": etype, "message": message, "traceback": tb},
            "elapsed_s": round(float(elapsed_s), 4)}


def check_response(msg: dict, expect_id: int) -> str | None:
    """Why `msg` is not the response to request `expect_id`, or None."""
    if msg.get("id") != expect_id:
        return f"response id {msg.get('id')!r} does not match request {expect_id}"
    if not isinstance(msg.get("ok"), bool):
        return "response has no boolean 'ok'"
    if not msg["ok"] and not isinstance(msg.get("error"), dict):
        return "error response has no error object"
    return None


# ------------------------------------------------------------------------------------------------ values
def to_jsonable(x, depth: int = 0, *, max_items: int = 20_000):
    """A JSON-safe copy of a snippet result: numpy arrays/scalars, mathutils vectors/matrices, sets and tuples become
    lists; non-finite floats become None (strict JSON); anything else becomes its repr (bounded)."""
    if depth > 8:
        return repr(x)[:200]
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): to_jsonable(v, depth + 1, max_items=max_items) for k, v in list(x.items())[:max_items]}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [to_jsonable(v, depth + 1, max_items=max_items) for v in list(x)[:max_items]]
    for attr in ("tolist", "to_list"):                     # numpy arrays/scalars; mathutils Matrix rows
        fn = getattr(type(x), attr, None)
        if fn is not None:
            try:
                return to_jsonable(fn(x), depth + 1, max_items=max_items)
            except Exception:  # noqa: BLE001 - fall through to the other conversions
                pass
    if hasattr(type(x), "__float__") and not hasattr(type(x), "__len__"):
        try:
            return to_jsonable(float(x), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(type(x), "__len__") and (hasattr(type(x), "__iter__") or hasattr(type(x), "__getitem__"))             and not isinstance(x, (bytes, bytearray)):          # mathutils Vector/Matrix: the sequence protocol
        try:
            return [to_jsonable(v, depth + 1, max_items=max_items) for v in list(x)[:max_items]]
        except Exception:  # noqa: BLE001
            pass
    return repr(x)[:500]
