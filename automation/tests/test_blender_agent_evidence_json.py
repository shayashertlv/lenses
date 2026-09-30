"""Offline evidence access: explicit registration, immutable identity and bounded output."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("agents")
from agents.tool_context import ToolContext
from blender_agent.evidence_json import (EvidenceJSONRegistry, INDEX_NAME, MAX_FILE_BYTES,
                                         MAX_OUTPUT_CHARS, make_read_evidence_json)


def artifact(root, relative="ar/candidate-1234/preview-result.json", content=None):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content if content is not None else {"continuity_failure": "missing section"}), encoding="utf-8")
    return path


def test_only_registered_identity_is_readable_and_survives_resume(tmp_path):
    path = artifact(tmp_path)
    registry = EvidenceJSONRegistry(tmp_path)
    with pytest.raises(ValueError, match="not registered"):
        registry.read(str(path))
    registry.register(path, "preview")
    resumed = EvidenceJSONRegistry(tmp_path)
    result = resumed.read(str(path), "/continuity_failure")
    assert result["data"] == "missing section"
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["truncated"] is False
    path.write_text('{"secret":"replacement"}')
    with pytest.raises(ValueError, match="bytes changed"):
        resumed.read(str(path))
    with pytest.raises(ValueError, match="changed"):
        resumed.register(path, "preview")


@pytest.mark.parametrize("relative", ["arbitrary.json", "runs/session/transport/0001-request.json",
    "reviews/.env.json", ".env", "ar/candidate/preview/transport.json", "inspections/subfolder/private.json",
    "portrait/candidate/other/report.json", INDEX_NAME])
def test_arbitrary_transport_secret_and_other_layouts_cannot_register(tmp_path, relative):
    path = artifact(tmp_path, relative)
    registry = EvidenceJSONRegistry(tmp_path) if relative != INDEX_NAME else None
    if registry is None:
        with pytest.raises(ValueError, match="registry"):
            EvidenceJSONRegistry(tmp_path)
    else:
        with pytest.raises(ValueError, match="Only preview"):
            registry.register(path, "preview")
        with pytest.raises(ValueError, match="not registered"):
            registry.read(str(path))


def test_external_and_symlink_paths_are_not_authorized(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    outside = artifact(tmp_path, "outside.json")
    registry = EvidenceJSONRegistry(root)
    with pytest.raises(ValueError, match="inside"):
        registry.register(outside, "preview")
    with pytest.raises(ValueError, match="inside"):
        registry.read("../outside.json")
    link = root / "reviews" / "alias.json"
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("OS does not allow test symlinks")
    with pytest.raises(ValueError, match="inside"):
        registry.register(link, "review")


@pytest.mark.parametrize("relative,kind", [("reviews/20260930T130753-deadbeef.json", "review"),
    ("inspections/export-abcdef.json", "inspection"), ("ar/test-1234/preview/report.json", "preview"),
    ("portrait/test-1234/portrait/report.json", "preview")])
def test_producer_artifact_kinds(tmp_path, relative, kind):
    path = artifact(tmp_path, relative)
    registry = EvidenceJSONRegistry(tmp_path)
    registry.register(path, kind)
    assert registry.read(relative)["kind"] == kind


def test_json_pointer_selects_exact_escaped_keys_and_array_members(tmp_path):
    path = artifact(tmp_path, content={"a/b": {"~key": [False, {"fit": None}]}})
    registry = EvidenceJSONRegistry(tmp_path)
    registry.register(path, "preview")
    assert registry.read(str(path), "/a~1b/~0key/1/fit")["data"] is None
    assert registry.read(str(path), "/a~1b/~0key/0")["data"] is False
    for pointer in ["a/b", "/a~2b", "/a~1b/~0key/01", "/a~1b/~0key/-1", "/missing", "/" * 33]:
        with pytest.raises(ValueError, match="pointer"):
            registry.read(str(path), pointer)


def test_file_and_output_caps_do_not_return_partial_json_as_complete_evidence(tmp_path):
    path = artifact(tmp_path, content={"small": {"failure": "exact diagnostic"}, "large": "x" * MAX_OUTPUT_CHARS})
    registry = EvidenceJSONRegistry(tmp_path)
    registry.register(path, "preview")
    bounded = registry.read(str(path))
    assert bounded["truncated"] is True and bounded["data"] is None
    assert bounded["keys"] == ["small", "large"]
    assert len(json.dumps(bounded, ensure_ascii=False)) <= MAX_OUTPUT_CHARS
    assert registry.read(str(path), "/small")["data"] == {"failure": "exact diagnostic"}
    huge = artifact(tmp_path, "inspections/huge.json", {"large": "x" * MAX_FILE_BYTES})
    with pytest.raises(ValueError, match="file limit"):
        registry.register(huge, "inspection")
    assert "inspections/huge.json" not in registry.entries


def test_tampered_persistent_index_cannot_enable_transport_reader(tmp_path):
    path = artifact(tmp_path, "runs/session/transport/0001-response.json", {"never": "expose"})
    (tmp_path / INDEX_NAME).write_text(json.dumps({"schema_version": 1, "entries": {
        "runs/session/transport/0001-response.json": {"kind": "preview", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}}))
    with pytest.raises(ValueError, match="registry entry"):
        EvidenceJSONRegistry(tmp_path)


def test_escaped_unicode_output_is_also_bounded(tmp_path):
    path = artifact(tmp_path, content={"item": "\U0001f642" * 5000})
    registry = EvidenceJSONRegistry(tmp_path)
    registry.register(path, "preview")
    result = registry.read(str(path))
    assert result["truncated"] is True
    assert len(json.dumps(result)) <= MAX_OUTPUT_CHARS


def test_reader_sdk_tool_and_schema(tmp_path):
    path = artifact(tmp_path)
    registry = EvidenceJSONRegistry(tmp_path)
    registry.register(path, "preview")
    tool = make_read_evidence_json(registry)
    encoded = json.dumps({"path": str(path), "pointer": "/continuity_failure"})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="offline", tool_arguments=encoded)
    result = asyncio.run(tool.on_invoke_tool(context, encoded))
    assert result["data"] == "missing section"
    assert set(tool.params_json_schema["properties"]) == {"path", "pointer"}
