"""Current-source half of the offline Astra request-byte regression."""
import json
from pathlib import Path

import pytest

from glasses3d.claude_test.astra_bytes_check import (
    CLAUDE_MODULES,
    IMPORT_MODES,
    assert_same_requests,
    run_check,
)


def test_current_astra_requests_survive_claude_imports(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "astra-bytes"
    result = run_check(source_root=root, output_dir=output)
    assert result["equal"] is True
    assert result["baseline_ref"] is None
    assert set(result["captures"]) == set(IMPORT_MODES)
    clean = result["captures"]["clean"]
    assert clean["claude_imports"] == []
    opening = json.loads((output / "clean/opening.json").read_bytes())
    followup = json.loads((output / "clean/tool-followup.json").read_bytes())
    for body in (opening, followup):
        assert body["model"] == "gpt-6-astra"
        assert body["reasoning"] == {"effort": "max", "summary": "auto"}
        assert body["tool_choice"] == "required"
        assert body["parallel_tool_calls"] is False
        assert body["service_tier"] == "default"
        assert body["max_output_tokens"] == 16_384
        # The ordinary registry includes its modeling, inspection and finish
        # tools even though this lightweight fixture invokes only budget_status.
        assert {"execute_blender_code", "read_image", "budget_status", "temple_stage",
                "finish"} <= {tool["name"] for tool in body["tools"]}
    assert opening["instructions"] == followup["instructions"]
    assert opening["tools"] == followup["tools"]
    for mode, capture in result["captures"].items():
        assert capture["source_root"] == str(root.resolve())
        assert capture["requests"] == clean["requests"]
        assert capture["mock_inference_requests"] == capture["mock_count_requests"] == 2
        assert capture["production_requests"] == 0
        assert capture["non_loopback_attempts"] == []
        assert capture["production_transport_constructions"] == []
        if mode != "clean":
            assert set(CLAUDE_MODULES) <= set(capture["claude_imports"])

    # A changed request must fail, including a field unrelated to the prompts.
    changed = output / "claude-after/tool-followup.json"
    original = changed.read_bytes()
    assert b'"parallel_tool_calls":false' in original
    changed.write_bytes(original.replace(b'"parallel_tool_calls":false',
                                        b'"parallel_tool_calls":true', 1))
    with pytest.raises(AssertionError, match="tool-followup request bytes differ"):
        assert_same_requests(output / "clean", changed.parent)
