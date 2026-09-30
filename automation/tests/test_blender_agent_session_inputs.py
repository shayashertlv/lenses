"""Resume proof comes from SDK replay items, never a request/manifest receipt."""
import asyncio
import base64
import copy
import hashlib
import json

import pytest

from blender_agent.session_inputs import ReferenceInput, plan_session_input


def photo(payload=b"original-image", path="C:/current/front.png", label="Reference 1 (front.png)", detail="high"):
    return ReferenceInput(label, path, "data:image/png;base64," + base64.b64encode(payload).decode(), detail)


def plan(history=(), prompt="Build the photographed glasses.", photos=None):
    return plan_session_input(history, prompt=prompt, photos=photos if photos is not None else [photo()],
                              invocation_text="Output directory: C:/output\nContinue with the existing $20 cap.")


def message(content):
    return {"role": "user", "content": content}


def legacy(prompt="Build the photographed glasses.", photos=None):
    content = [{"type": "input_text", "text": prompt +
                "\nOutput directory: C:\\old-output\nThis invocation allows up to 120 model responses. "
                "The entire trial has a $20 total cap; $20.000000 remains before this invocation. "
                "Use budget_status to monitor it. Save useful progress regularly."}]
    for index, reference in enumerate(photos if photos is not None else [photo()]):
        content.extend([{"type": "input_text", "text": f"Reference {index + 1}: C:/old/reference-{index + 1}.png"},
                        {"type": "input_image", "image_url": reference.image_url, "detail": reference.detail}])
    return message(content)


def test_empty_history_sends_full_bundle_even_if_prior_attempt_had_manifest(tmp_path):
    # A saved host manifest/receipt cannot establish that the SDK persisted input.
    (tmp_path / "run-config.json").write_text(json.dumps({"prompt_sha256": "already-attempted"}))
    result = plan()
    assert result.receipt["brief"]["action"] == "send"
    assert result.receipt["images_sent"] == 1
    assert result.content[0]["text"].endswith("Build the photographed glasses.")


@pytest.mark.parametrize("make_previous", [lambda: message(plan().content), legacy])
def test_exact_persisted_bundle_is_not_resent_and_receipt_is_byte_free(make_previous):
    history = [make_previous()]
    original = copy.deepcopy(history)
    result = plan(history)
    assert result.receipt["brief"]["action"] == "reuse_history"
    assert result.receipt["images_sent"] == 0
    assert result.receipt["images_reused_from_history"] == 1
    assert result.receipt["references"][0]["history_item_index"] == 0
    assert result.receipt["references"][0]["sha256"] == hashlib.sha256(b"original-image").hexdigest()
    assert history == original
    encoded = json.dumps(result.receipt)
    assert "base64" not in encoded and "Build the photographed glasses" not in encoded


def test_legacy_brief_requires_entire_exact_host_suffix_not_prefix_coincidence():
    previous = legacy()
    previous["content"][0]["text"] += "\nAnd a later changed requirement."
    result = plan([previous])
    assert result.receipt["brief"]["action"] == "send"
    assert result.receipt["images_sent"] == 0


def test_changes_are_appended_independently_and_old_brief_can_be_restored():
    initial = message(plan().content)
    changed = plan([initial], prompt="Use the newly supplied temple photograph.",
                   photos=[photo(), photo(b"new-angle", label="Reference 2 (angle.png)")])
    assert changed.receipt["brief"]["action"] == "send"
    assert [row["action"] for row in changed.receipt["references"]] == ["reuse_history", "send"]
    assert changed.receipt["images_sent"] == 1
    restored = plan([initial, message(changed.content)])
    assert restored.receipt["brief"]["action"] == "send"
    assert restored.receipt["brief"]["reason"] == "latest_brief_changed"
    assert restored.receipt["images_sent"] == 0


def test_brief_whitespace_change_is_not_silently_normalized():
    result = plan([legacy()], prompt="Build the photographed glasses.\n")
    assert result.receipt["brief"]["action"] == "send"


def test_moved_reference_receives_current_usable_crop_path_without_new_image(tmp_path):
    moved = tmp_path / "renamed.png"
    moved.write_bytes(b"original-image")
    result = plan([legacy()], photos=[photo(path=str(moved), label="Reference 1 (renamed.png)")])
    assert result.receipt["images_sent"] == 0
    labels = "\n".join(part.get("text", "") for part in result.content)
    assert str(moved) in labels
    assert "C:/old/reference-1.png" not in labels
    assert "use this current path for crops" in labels
    assert moved.read_bytes() == b"original-image"


def test_reordered_labels_and_repeated_current_bytes_have_explicit_mapping():
    first, second = photo(b"one"), photo(b"two", label="Reference 2")
    result = plan([legacy(photos=[first, second])], photos=[second, first])
    assert result.receipt["images_sent"] == 0
    assert [row["history_content_index"] for row in result.receipt["references"]] == [4, 2]
    repeated = plan(photos=[first, first])
    assert repeated.receipt["images_sent"] == 1
    assert repeated.receipt["references"][1]["action"] == "reuse_current"
    assert repeated.receipt["references"][1]["same_as_reference_index"] == 0


@pytest.mark.parametrize("unproven", [
    {"type": "input_image", "file_id": "file-history"},
    {"type": "input_image", "image_url": "https://example.invalid/front.png"},
    {"type": "input_image", "image_url": "data:image/png;base64,!!corrupt!!"},
    {"type": "input_image", "image_url": {"saved_image": "front.png", "sha256": hashlib.sha256(b"original-image").hexdigest()}},
])
def test_remote_cleaned_or_corrupt_history_image_is_not_identity_proof(unproven):
    result = plan([message([unproven])])
    assert result.receipt["images_sent"] == 1
    assert result.receipt["history_unverifiable_images"] == 1


@pytest.mark.parametrize("role", ["assistant", "system", "developer"])
def test_non_user_messages_are_not_proof(role):
    previous = legacy()
    previous["role"] = role
    result = plan([previous])
    assert result.receipt["brief"]["action"] == "send"
    assert result.receipt["images_sent"] == 1


def test_requested_higher_detail_is_not_suppressed_by_prior_low_detail():
    result = plan([legacy(photos=[photo(detail="low")])])
    assert result.receipt["images_sent"] == 1


def test_malformed_historical_detail_cannot_prove_matching_vision_input():
    previous = legacy()
    previous["content"][-1]["detail"] = {"unexpected": "high"}
    result = plan([previous])
    assert result.receipt["images_sent"] == 1
    assert result.receipt["history_unverifiable_images"] == 1


def test_partial_persistence_requires_each_missing_piece():
    previous = legacy()
    only_brief = plan([message(previous["content"][:1])])
    assert only_brief.receipt["brief"]["action"] == "reuse_history"
    assert only_brief.receipt["images_sent"] == 1
    only_image = plan([message(previous["content"][1:])])
    assert only_image.receipt["brief"]["action"] == "send"
    assert only_image.receipt["images_sent"] == 0


def test_multiple_resumes_keep_latest_brief_proof_without_new_image_occurrences():
    history = [message(plan().content)]
    for _ in range(3):
        result = plan(history)
        assert result.receipt["brief"]["action"] == "reuse_history"
        assert result.receipt["images_sent"] == 0
        history.append(message(result.content))
    assert sum(part["type"] == "input_image" for item in history for part in item["content"]) == 1


def test_production_invocation_suffix_is_not_mistaken_for_a_changed_product_brief():
    history = []
    for remaining in ("20.000000", "15.500000", "12.000000"):
        result = plan_session_input(history, prompt="Build the photographed glasses.", photos=[photo()],
            invocation_text="Output directory: C:/output\nThis invocation allows up to 120 model responses. "
            f"The entire trial has a $20 total cap; ${remaining} remains before this invocation. "
            "Use budget_status to monitor it. Save useful progress regularly.")
        assert result.receipt["brief"]["action"] == ("send" if not history else "reuse_history")
        assert result.receipt["images_sent"] == (1 if not history else 0)
        history.append(message(result.content))
    changed = plan_session_input(history, prompt="Use a champagne tint.", photos=[photo()], invocation_text="Next invocation")
    assert changed.receipt["brief"]["action"] == "send"


def test_invalid_incoming_url_cannot_create_false_reuse_receipt():
    with pytest.raises(ValueError, match="data URL"):
        plan(photos=[ReferenceInput("Reference 1", "C:/front.png", "data:image/png;base64,broken")])


def test_actual_sdk_sqlite_presaves_failed_input_and_resume_replays_once(tmp_path):
    """The real runner/session, with an offline model, reproduces the quota case."""
    agents = pytest.importorskip("agents")
    import httpx
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.retry import ModelRetrySettings
    from agents.usage import Usage
    from openai import RateLimitError
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    class OfflineModel(Model):
        def __init__(self, fail=False):
            self.fail, self.inputs = fail, []

        async def get_response(self, *args, **kwargs):
            self.inputs.append(copy.deepcopy(kwargs["input"]))
            if self.fail:
                raise RateLimitError("offline quota fixture", response=httpx.Response(
                    429, request=httpx.Request("POST", "https://offline.invalid/responses")), body={})
            return ModelResponse(output=[ResponseOutputMessage(
                id="offline-message", role="assistant", status="completed", type="message",
                content=[ResponseOutputText(type="output_text", text="Offline continuation.", annotations=[])])],
                usage=Usage(), response_id="offline-response")

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("Streaming is not used")
            yield  # Make the required abstract method an async iterator.

    async def exercise():
        database = tmp_path / "session.sqlite"
        session = agents.SQLiteSession("blender", database)
        hold = tmp_path / "trial-budget.json"
        hold.write_text('{"unresolved_hold_usd": "1.465363", "maximum_usd": 20}', encoding="utf-8")
        before_hold = hold.read_bytes()
        settings = agents.ModelSettings(retry=ModelRetrySettings(max_retries=0))
        config = agents.RunConfig(tracing_disabled=True)
        first = plan(await session.get_items())
        failing = OfflineModel(fail=True)
        try:
            with pytest.raises(RateLimitError, match="offline quota fixture"):
                await agents.Runner.run(agents.Agent(name="offline", model=failing, model_settings=settings),
                                        [message(first.content)], session=session, run_config=config)
            saved_after_failure = await session.get_items()
            assert saved_after_failure == [message(first.content)]
        finally:
            session.close()
        resumed_session = agents.SQLiteSession("blender", database)
        try:
            continued = plan(await resumed_session.get_items())
            assert continued.receipt["images_sent"] == 0
            model = OfflineModel()
            result = await agents.Runner.run(agents.Agent(name="offline", model=model, model_settings=settings),
                                            [message(continued.content)], session=resumed_session, run_config=config)
            assert result.final_output == "Offline continuation."
            replay = model.inputs[0]
            assert replay[:1] == saved_after_failure
            assert replay[-1] == message(continued.content)
            assert sum(part.get("type") == "input_image" for item in replay
                       if item.get("role") == "user" for part in item.get("content", [])) == 1
            assert hold.read_bytes() == before_hold
        finally:
            resumed_session.close()

    asyncio.run(exercise())


def test_actual_empty_sqlite_database_does_not_suppress_failed_unpersisted_bundle(tmp_path):
    agents = pytest.importorskip("agents")

    async def exercise():
        database = tmp_path / "session.sqlite"
        session = agents.SQLiteSession("blender", database)
        try:
            history = await session.get_items()
            assert database.is_file() and history == []
            result = plan(history)
            assert result.receipt["brief"]["action"] == "send"
            assert result.receipt["images_sent"] == 1
            assert await session.get_items() == []  # Planning itself cannot persist a receipt as proof.
        finally:
            session.close()

    asyncio.run(exercise())
