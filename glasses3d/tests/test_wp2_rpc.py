"""WP2 stage A: the length-prefixed JSON framing between host and worker (g3d/rpc.py). Host only."""
from __future__ import annotations

import math
import socket
import struct
import threading

import numpy as np
import pytest

from glasses3d.g3d import rpc


def pair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    return a, b


def test_round_trip_and_header():
    a, b = pair()
    msg = {"id": 3, "method": "exec", "params": {"code": "x = 'ü'"}, "deadline_s": 12.5}
    n = rpc.send_frame(a, msg)
    assert rpc.recv_frame(b) == msg
    raw = rpc.encode(msg)
    assert len(raw) == n and struct.unpack(">I", raw[:4])[0] == n - 4
    a.close()
    b.close()


def test_large_frame_round_trip_across_many_recv_calls():
    a, b = pair()
    big = {"id": 1, "blob": "x" * (3 << 20)}
    t = threading.Thread(target=rpc.send_frame, args=(a, big))
    t.start()
    assert rpc.recv_frame(b) == big
    t.join()


@pytest.mark.parametrize("bad", [{"x": math.nan}, {"x": math.inf}, {"x": -math.inf}])
def test_non_finite_numbers_are_refused_on_encode(bad):
    with pytest.raises(rpc.FrameError):
        rpc.encode(bad)


@pytest.mark.parametrize("text", [b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}'])
def test_non_finite_numbers_are_refused_on_decode(text):
    with pytest.raises(rpc.FrameError, match="non-finite"):
        rpc.decode(text)


def test_frame_must_be_an_object_and_utf8():
    with pytest.raises(rpc.FrameError):
        rpc.encode([1, 2])
    with pytest.raises(rpc.FrameError):
        rpc.decode(b"[1, 2]")
    with pytest.raises(rpc.FrameError):
        rpc.decode(b"\xff\xfe")


def test_oversized_and_empty_frames_are_refused_before_reading_the_body():
    a, b = pair()
    a.sendall(struct.pack(">I", 1000))
    with pytest.raises(rpc.FrameError, match="exceeds"):
        rpc.recv_frame(b, max_bytes=999)
    a2, b2 = pair()
    a2.sendall(struct.pack(">I", 0))
    with pytest.raises(rpc.FrameError, match="empty"):
        rpc.recv_frame(b2)
    assert rpc.MAX_FRAME_BYTES == 64 << 20


def test_eof_mid_frame_is_peer_closed():
    a, b = pair()
    a.sendall(struct.pack(">I", 50) + b'{"id":')
    a.close()
    with pytest.raises(rpc.PeerClosed):
        rpc.recv_frame(b)


def test_request_ids_must_strictly_increase():
    assert rpc.check_request(rpc.request(5, "ping"), 4) is None
    assert "does not increase" in rpc.check_request(rpc.request(4, "ping"), 4)
    assert "integer" in rpc.check_request({"id": True, "method": "ping"}, 0)
    assert "method" in rpc.check_request({"id": 9, "method": 3}, 0)
    assert "params" in rpc.check_request({"id": 9, "method": "x", "params": []}, 0)
    assert "deadline" in rpc.check_request({"id": 9, "method": "x", "deadline_s": -1}, 0)


def test_response_must_carry_its_request_id():
    assert rpc.check_response(rpc.ok(7, {"a": 1}, 0.1), 7) is None
    assert "does not match" in rpc.check_response(rpc.ok(6, {}, 0.1), 7)
    assert "error object" in rpc.check_response({"id": 7, "ok": False}, 7)
    e = rpc.error(7, "KeyError", "nope", "tb", 0.5)
    assert e["error"] == {"type": "KeyError", "message": "nope", "traceback": "tb"} and not e["ok"]


def test_to_jsonable_converts_numpy_vectors_sets_and_non_finite():
    class Vec:                                   # mathutils-like: iterable with a length, no tolist
        def __init__(self, *v):
            self.v = v

        def __len__(self):
            return len(self.v)

        def __iter__(self):
            return iter(self.v)

    out = rpc.to_jsonable({"a": np.arange(3), "b": np.float32(1.5), "c": {1, 2}, "d": (1, float("nan")),
                           "e": Vec(1.0, 2.0), "f": object(), 3: np.int64(4)})
    assert out["a"] == [0, 1, 2] and out["b"] == 1.5 and sorted(out["c"]) == [1, 2]
    assert out["d"] == [1, None] and out["e"] == [1.0, 2.0] and out["3"] == 4
    assert isinstance(out["f"], str)
    rpc.encode({"r": out})                       # strict JSON


def test_to_jsonable_bounds_depth_and_length():
    deep = cur = {}
    for _ in range(20):
        cur["x"] = {}
        cur = cur["x"]
    rpc.encode({"r": rpc.to_jsonable(deep)})
    assert len(rpc.to_jsonable(list(range(50_000)))) == 20_000
