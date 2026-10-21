"""Named, approval-bound teaching profiles; legacy text is data, never executable code."""
from __future__ import annotations

from copy import copy, deepcopy
from functools import lru_cache
import json
from pathlib import Path

DEFAULT_PROFILE = "decision-20261021"
LEGACY_PROFILE = "legacy-20261021"
PROFILES = (DEFAULT_PROFILE, LEGACY_PROFILE)


def resolve_profile(name=None) -> str:
    """Resolve the default explicitly and refuse typos before a run starts."""
    if name is None:
        return DEFAULT_PROFILE
    if name not in PROFILES:
        raise ValueError(f"unknown instruction profile: {name!r}; choose {', '.join(PROFILES)}")
    return name


@lru_cache(maxsize=1)
def _legacy():
    data = json.loads(Path(__file__).with_name("legacy_20261021.json").read_text(encoding="utf-8"))
    if data.get("schema") != "g3d.instruction_profile/1" or data.get("profile") != LEGACY_PROFILE:
        raise ValueError("invalid legacy instruction snapshot")
    return data


def legacy_text(key: str) -> str:
    value = _legacy()[key]
    if not isinstance(value, str):
        raise ValueError(f"legacy snapshot field is not text: {key}")
    return value


def legacy_instruction_parts() -> dict:
    """Independent copies let owner-edit teaching retain its separate authority contract."""
    return deepcopy(_legacy()["instruction_parts"])


def legacy_tool_text(tools, *, owner_edit=None) -> list:
    """Restore transmitted metadata and exclude the new decision-state tool.

    Tool callbacks still use the selected scene profile. Copy the SDK objects so selecting legacy never
    mutates another profile's registry. Owner-edit-only tools and the unchanged owner specialist
    description retain their scoped metadata; the product snapshot is not their authority contract.
    """
    metadata = {row["name"]: row for row in _legacy()["registry"]}
    result = []
    for original in tools:
        if original.name in {"record_difference", "compare_lens_candidates", "choose_camera_orientation"}:
            continue
        tool = copy(original)
        if tool.name in metadata:
            if not (owner_edit and tool.name == "temple_stage"):
                tool.description = metadata[tool.name]["description"]
            tool.params_json_schema = deepcopy(metadata[tool.name]["parameters"])
        result.append(tool)
    return result
