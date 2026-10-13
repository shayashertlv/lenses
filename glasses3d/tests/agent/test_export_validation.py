"""M0: validation follows the exported bytes, including old cached exports ($0)."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from glasses3d.agent import seeing
from glasses3d.tests import glbfab


class ExportSession:
    def __init__(self, root, payload):
        self.root = root
        self.payload = payload
        self.head = {"id": "c0001", "fingerprint": "a" * 64}
        self.calls = 0

    def new_call_id(self, name):
        return "export-1"

    def call_read(self, name, method, args, **kwargs):
        self.calls += 1
        path = Path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload)
        return {"status": "ok", "result": {
            "status": "exported", "path": str(path), "bytes": len(self.payload),
            "sha256": hashlib.sha256(self.payload).hexdigest(),
            "fingerprint_before": self.head["fingerprint"], "receipt": {}}}


def test_exports_validate_and_reuse_report_by_sha_not_head(tmp_path, monkeypatch):
    validate = Mock(wraps=seeing.spec.validate_spec)
    monkeypatch.setattr(seeing.spec, "validate_spec", validate)
    session = ExportSession(tmp_path, glbfab.build())
    tool = seeing.Seeing(session)
    first = tool.export_head()
    assert first["validate_spec"]["ok"]
    assert first["seat_check"] is None
    assert first["validation_sha256"] == first["sha256"]
    assert tool.export_head()["cached"]
    session.head = {"id": "c0002", "fingerprint": "b" * 64}
    assert tool.export_head()["validate_spec"] == first["validate_spec"]
    assert validate.call_count == 1
    assert session.calls == 2


def test_invalid_export_is_inspectable_with_failure_report(tmp_path):
    out = seeing.Seeing(ExportSession(tmp_path, glbfab.build(scale=1))).export_head()
    assert out["status"] == "exported"
    assert not out["validate_spec"]["ok"]
    assert out["validate_spec"]["errors"]


def test_old_index_is_validated_and_tampered_bytes_are_reexported(tmp_path):
    session = ExportSession(tmp_path, glbfab.build())
    first = seeing.Seeing(session).export_head()
    index = tmp_path / "work/exports/index.json"
    old = {k: v for k, v in first.items() if k not in ("validate_spec", "seat_check", "validation_sha256")}
    index.write_text(json.dumps({session.head["fingerprint"]: old}))
    out = seeing.Seeing(session).export_head()
    assert out["cached"] and out["validate_spec"]["ok"]
    Path(out["path"]).write_bytes(b"changed")
    fresh = seeing.Seeing(session).export_head()
    assert not fresh["cached"] and fresh["validate_spec"]["ok"]
    assert session.calls == 2


@pytest.mark.parametrize("seat", [{"frame_type": "acetate_full"}, "malformed"])
def test_declared_lens_seating_checked_even_for_bad_metadata(tmp_path, monkeypatch, seat):
    def patch(doc, _):
        next(n for n in doc["nodes"] if n.get("name") == "Lens_L").setdefault("extras", {})["g3d_seat"] = seat
    check = Mock(return_value={"ok": False, "error": "synthetic seating failure"})
    monkeypatch.setattr(seeing.seat_check, "check_glb", check)
    out = seeing.Seeing(ExportSession(tmp_path, glbfab.build(patch=patch))).export_head()
    assert out["seat_check"] == check.return_value
    assert check.call_count == 1


def test_unreadable_export_returns_spec_failure(tmp_path):
    out = seeing.Seeing(ExportSession(tmp_path, b"not a glb")).export_head()
    assert not out["validate_spec"]["ok"]
    assert not out["seat_check"]["ok"]
