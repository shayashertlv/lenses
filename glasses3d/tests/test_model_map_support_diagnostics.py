"""Selection failures must distinguish missing targets from unsupported geometry."""
import pytest

from glasses3d.agent.tools.model_map_geometry import support


def test_support_reports_empty_selection_before_geometry_lookup():
    with pytest.raises(ValueError, match="selection is empty"):
        support({"objects": []}, {}, [])


def test_support_reports_unknown_targets_before_geometry_lookup():
    with pytest.raises(ValueError, match="unknown catalogue target ids: F0042"):
        support({"objects": []}, {}, ["F0042"])


def test_support_does_not_confuse_multiple_objects_with_uneditable_geometry():
    targets = {
        "F1": {"object_id": "O1", "source_editable": True},
        "F2": {"object_id": "O2", "source_editable": True},
    }
    with pytest.raises(ValueError, match="one actual object"):
        support({"objects": []}, targets, ["F1", "F2"])
    targets["F1"]["source_editable"] = False
    with pytest.raises(ValueError, match="editable targets with source mesh correspondence"):
        support({"objects": []}, targets, ["F1"])
