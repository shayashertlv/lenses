import asyncio
import json
from pathlib import Path
import struct

import pytest

pytest.importorskip("agents")
from agents.tool_context import ToolContext
from blender_agent.inspect_export import glb_summary, make_inspect_export


def write_glb(path: Path):
    chunk = json.dumps({"asset": {"version": "2.0"}, "materials": [{"name": "native lens"}],
                       "meshes": [], "nodes": []}).encode()
    chunk += b" " * (-len(chunk) % 4)
    path.write_bytes(b"glTF" + struct.pack("<4I", 2, len(chunk) + 20, len(chunk), 0x4E4F534A) + chunk)


def test_missing_volume_is_reported_not_inferred(tmp_path):
    path = tmp_path / "model.glb"
    write_glb(path)
    result = glb_summary(path)
    assert result["materials"] == [{"name": "native lens"}]
    assert result["extensions_used"] == []


def test_tool_cannot_read_outside_assigned_output(tmp_path):
    path = tmp_path / "private.glb"
    write_glb(path)
    tool = make_inspect_export(tmp_path / "assigned")
    arguments = json.dumps({"path": str(path)})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="test", tool_arguments=arguments)
    result = asyncio.run(tool.on_invoke_tool(context, arguments))
    assert "inside the output directory" in result


def test_invalid_header_is_rejected(tmp_path):
    path = tmp_path / "bad.glb"
    write_glb(path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="header"):
        glb_summary(path)
