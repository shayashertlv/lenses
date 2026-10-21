"""The only UI bridge to the C1/C2/C3 core façades; no model transport here."""
from importlib import import_module

from .contracts import APIError


def _facade(module, function):
    name = f"glasses3d.agent.{module}"
    try:
        imported = import_module(name)
    except ModuleNotFoundError as error:
        if error.name and error.name != name:
            raise
        raise APIError("not_built_yet", f"{function.replace('_', ' ')} is not built yet.", 501) from error
    try:
        return getattr(imported, function)
    except AttributeError as error:
        raise APIError("not_built_yet", f"{function.replace('_', ' ')} is not built yet.", 501) from error


class CoreAdapter:
    def read_lens_materials(self, source_glb):
        return _facade("owner_lens_edit", "read_lens_materials")(source_glb)

    def patch_lens_glb(self, source_glb, values):
        return _facade("owner_lens_edit", "patch_lens_glb")(source_glb, values)

    def create_lens_preview(self, source_root, delivery_id, values, destination=None):
        return _facade("owner_lens_edit", "create_preview")(
            source_root, delivery_id, values, destination=destination)

    def save_lens_edit(self, source_root, delivery_id, values, destination=None):
        return _facade("owner_lens_edit", "save_lens_edit")(
            source_root, delivery_id, values, destination=destination)

    def validate_capture(self, payload, viewer_png, annotated_png, source_glb):
        return _facade("owner_captures", "validate_capture")(payload, viewer_png, annotated_png, source_glb)

    def create_capture_understand(self, source_root, delivery_id, items, destination=None):
        return _facade("owner_captures", "create_capture_understand")(
            source_root, delivery_id, items, destination=destination)

    def read_understanding(self, round_root):
        return _facade("owner_choices", "read_understanding")(round_root)

    def apply_choices(self, round_root, understanding_sha256, choices, skipped, confirmation_text, destination=None):
        return _facade("owner_choices", "apply_choices")(
            round_root, understanding_sha256, choices, skipped, confirmation_text, destination=destination)

    def create_full_edit_item(self, round_root, item_id, destination=None):
        return _facade("owner_fallback", "create_full_edit_item")(round_root, item_id, destination=destination)

    def create_job(self, **kwargs):
        from glasses3d.agent.jobs import create_job
        return create_job(**kwargs)

    def load_delivery(self, root, delivery_id=None):
        from glasses3d.agent.delivery import load_delivery
        return load_delivery(root, delivery_id)

    def review(self, root, delivery_id):
        from glasses3d.agent.review import review_job
        return review_job(root, delivery_id=delivery_id, port=0)
