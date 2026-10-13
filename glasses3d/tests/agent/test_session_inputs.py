"""The resume planner (agent/session_inputs.py): the Studio's test_blender_agent_session_inputs.py ported (minus the
legacy-suffix cases, PORT_REVIEW section 3), plus the view field and owner notes. Proof comes from SDK replay items,
never from a receipt."""
import asyncio
import base64
import copy
import hashlib
import json

import pytest

from glasses3d.agent.session_inputs import (BRIEF_PREFIX, INVOCATION_PREFIX, NOTE_PREFIX, ReferenceInput,
                                            plan_session_input)

PROMPT = "Build the photographed glasses."


def photo(payload=b"original-image", path="images/reference-01.png", label="Reference 1 of 1", detail="high",
          view="front", size_px=(4032, 3024)):
    return ReferenceInput(label, path, "data:image/png;base64," + base64.b64encode(payload).decode(), detail,
                          view=view, size_px=size_px)


def plan(history=(), prompt=PROMPT, photos=None, notes=()):
    return plan_session_input(history, prompt=prompt, photos=photos if photos is not None else [photo()],
                              invocation_text="Run r1.\nThe scene is empty.", owner_notes=notes)


def message(content):
    return {"role": "user", "content": content}


def test_empty_history_sends_the_full_bundle_even_if_a_prior_attempt_wrote_a_receipt(tmp_path):
    (tmp_path / "input-receipt.json").write_text(json.dumps({"images_sent": 1}))   # a receipt is never proof
    result = plan()
    assert result.receipt["brief"]["action"] == "send" and result.receipt["images_sent"] == 1
    assert result.content[0] == {"type": "input_text", "text": BRIEF_PREFIX + PROMPT}
    assert [p["type"] for p in result.content] == ["input_text", "input_text", "input_image", "input_text"]
    assert result.content[-1]["text"].startswith(INVOCATION_PREFIX)


def test_each_image_is_immediately_preceded_by_its_view_text():
    result = plan(photos=[photo(), photo(b"side", path="images/reference-02.jpg", label="Reference 2 of 2",
                                         view="side", size_px=None)])
    texts = [part for part in result.content if part["type"] == "input_text"]
    sha1 = hashlib.sha256(b"original-image").hexdigest()
    assert texts[1]["text"] == (f"Reference 1 of 1: call intake, revision source:{sha1[:12]}, "
                                "view 'front', images/reference-01.png, 4032x3024 px, "
                                f"sha256 {sha1[:12]}.\nImage attached below.")
    for index, part in enumerate(result.content):
        if part["type"] == "input_image":
            assert result.content[index - 1]["type"] == "input_text"
            assert "view '" in result.content[index - 1]["text"]
            assert "call intake, revision source:" in result.content[index - 1]["text"]
    assert [ref["view"] for ref in result.receipt["references"]] == ["front", "side"]


def test_the_exact_persisted_bundle_is_not_resent_and_the_receipt_is_byte_free():
    history = [message(plan().content)]
    original = copy.deepcopy(history)
    result = plan(history)
    assert result.receipt["brief"]["action"] == "reuse_history"
    assert result.receipt["images_sent"] == 0 and result.receipt["images_reused_from_history"] == 1
    assert result.receipt["references"][0]["history_item_index"] == 0
    assert result.receipt["references"][0]["sha256"] == hashlib.sha256(b"original-image").hexdigest()
    assert history == original
    encoded = json.dumps(result.receipt)
    assert "base64" not in encoded and PROMPT not in encoded


def test_reference_without_declared_view_still_has_a_complete_image_label():
    content = plan(photos=[photo(view=None)]).content
    label = content[next(i for i, part in enumerate(content) if part["type"] == "input_image") - 1]["text"]
    assert "call intake, revision source:" in label and "view 'unspecified'" in label


def test_changes_are_appended_independently_and_an_old_brief_can_be_restored():
    initial = message(plan().content)
    changed = plan([initial], prompt="Use the newly supplied temple photograph.",
                   photos=[photo(), photo(b"new-angle", label="Reference 2 of 2", view="angled")])
    assert changed.receipt["brief"]["action"] == "send"
    assert [row["action"] for row in changed.receipt["references"]] == ["reuse_history", "send"]
    assert changed.receipt["images_sent"] == 1
    restored = plan([initial, message(changed.content)])
    assert restored.receipt["brief"]["action"] == "send"
    assert restored.receipt["brief"]["reason"] == "latest_brief_changed"
    assert restored.receipt["images_sent"] == 0


def test_a_whitespace_change_in_the_brief_is_not_normalized_away():
    assert plan([message(plan().content)], prompt=PROMPT + "\n").receipt["brief"]["action"] == "send"


def test_a_moved_reference_gets_its_current_path_without_a_new_image():
    result = plan([message(plan().content)], photos=[photo(path="images/renamed.png")])
    assert result.receipt["images_sent"] == 0
    labels = "\n".join(part.get("text", "") for part in result.content)
    assert "images/renamed.png" in labels and "use this current path for crops" in labels


def test_reordered_and_repeated_references_have_an_explicit_mapping():
    first, second = photo(b"one"), photo(b"two", label="Reference 2 of 2", view="side")
    history = [message(plan(photos=[first, second]).content)]
    result = plan(history, photos=[second, first])
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
    {"type": "input_image", "image_url": {"sha256": hashlib.sha256(b"original-image").hexdigest(), "bytes": 14,
                                          "mime": "image/png"}},     # a cleaned receipt is not identity proof
])
def test_remote_cleaned_or_corrupt_history_images_are_not_proof(unproven):
    result = plan([message([unproven])])
    assert result.receipt["images_sent"] == 1 and result.receipt["history_unverifiable_images"] == 1


@pytest.mark.parametrize("role", ["assistant", "system", "developer"])
def test_non_user_messages_are_not_proof(role):
    previous = message(plan().content)
    previous["role"] = role
    result = plan([previous])
    assert result.receipt["brief"]["action"] == "send" and result.receipt["images_sent"] == 1


def test_a_higher_requested_detail_is_not_suppressed_by_an_earlier_low_detail():
    assert plan([message(plan(photos=[photo(detail="low")]).content)]).receipt["images_sent"] == 1


def test_a_malformed_history_detail_cannot_prove_the_vision_input():
    previous = message(plan().content)
    next(item for item in previous["content"] if item["type"] == "input_image")["detail"] = {"unexpected": "high"}
    result = plan([previous])
    assert result.receipt["images_sent"] == 1 and result.receipt["history_unverifiable_images"] == 1


def test_partial_persistence_requires_each_missing_piece():
    content = plan().content
    only_brief = plan([message(content[:1])])
    assert only_brief.receipt["brief"]["action"] == "reuse_history" and only_brief.receipt["images_sent"] == 1
    only_image = plan([message(content[1:])])
    assert only_image.receipt["brief"]["action"] == "send" and only_image.receipt["images_sent"] == 0


def test_multiple_resumes_keep_one_image_occurrence():
    history = [message(plan().content)]
    for _ in range(3):
        result = plan(history)
        assert result.receipt["brief"]["action"] == "reuse_history" and result.receipt["images_sent"] == 0
        history.append(message(result.content))
    assert sum(part["type"] == "input_image" for item in history for part in item["content"]) == 1


def test_an_invocation_message_is_never_mistaken_for_a_changed_brief():
    history = []
    for remaining in ("15.000000", "12.500000", "9.000000"):
        result = plan_session_input(history, prompt=PROMPT, photos=[photo()],
                                    invocation_text=f"Run r.\nThis run's budget cap: ${remaining}.")
        assert result.receipt["brief"]["action"] == ("send" if not history else "reuse_history")
        history.append(message(result.content))


def test_an_invalid_incoming_url_cannot_create_a_false_reuse_receipt():
    with pytest.raises(ValueError, match="data URL"):
        plan(photos=[ReferenceInput("Reference 1", "images/front.png", "data:image/png;base64,broken")])


def test_owner_notes_are_sent_verbatim_once_even_across_a_crash():
    note = "The bridge should sit 2 mm lower.\nKeep the temples."
    first = plan(notes=[note])
    assert first.receipt["notes_sent"] == 1
    assert {"type": "input_text", "text": NOTE_PREFIX + note} in first.content
    row = first.receipt["owner_notes"][0]
    assert row["sha256"] == hashlib.sha256(note.encode()).hexdigest() and row["action"] == "send"
    assert note not in json.dumps(first.receipt)
    # the SDK saved that input, then the run crashed: the same note is not sent again
    again = plan([message(first.content)], notes=[note])
    assert again.receipt["notes_sent"] == 0 and again.receipt["owner_notes"][0]["action"] == "reuse_history"
    assert all(NOTE_PREFIX not in part.get("text", "") for part in again.content)
    duplicate = plan(notes=[note, note])
    assert duplicate.receipt["notes_sent"] == 1 and duplicate.receipt["owner_notes"][1]["action"] == "reuse_current"
    with pytest.raises(ValueError):
        plan(notes=["   "])


def test_the_real_sdk_session_presaves_failed_input_and_a_resume_replays_it_once(tmp_path):
    """The Studio's quota witness on the real runner and session, with an offline model (no transport at all)."""
    agents = pytest.importorskip("agents")
    import httpx2
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.retry import ModelRetrySettings
    from agents.usage import Usage
    from openai import RateLimitError
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    from glasses3d.agent.offline import disable_sdk_tracing

    class OfflineModel(Model):
        def __init__(self, fail=False):
            self.fail, self.inputs = fail, []

        async def get_response(self, *args, **kwargs):
            self.inputs.append(copy.deepcopy(kwargs["input"]))
            if self.fail:
                raise RateLimitError("offline quota fixture", response=httpx2.Response(
                    429, request=httpx2.Request("POST", "https://offline.invalid/responses")), body={})
            return ModelResponse(output=[ResponseOutputMessage(
                id="offline-message", role="assistant", status="completed", type="message",
                content=[ResponseOutputText(type="output_text", text="Offline continuation.", annotations=[])])],
                usage=Usage(), response_id="offline-response")

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("Streaming is not used")
            yield  # noqa: unreachable - makes the abstract method an async iterator

    async def exercise():
        disable_sdk_tracing()
        database = tmp_path / "session.sqlite"
        settings = agents.ModelSettings(retry=ModelRetrySettings(max_retries=0))
        config = agents.RunConfig(tracing_disabled=True)
        session = agents.SQLiteSession("astra", database)
        try:
            first = plan(await session.get_items())
            with pytest.raises(RateLimitError):
                await agents.Runner.run(agents.Agent(name="offline", model=OfflineModel(fail=True),
                                                     model_settings=settings),
                                        [message(first.content)], session=session, run_config=config)
            saved = await session.get_items()
            assert saved == [message(first.content)]
        finally:
            session.close()
        session = agents.SQLiteSession("astra", database)
        try:
            continued = plan(await session.get_items())
            assert continued.receipt["images_sent"] == 0
            model = OfflineModel()
            result = await agents.Runner.run(agents.Agent(name="offline", model=model, model_settings=settings),
                                             [message(continued.content)], session=session, run_config=config)
            assert result.final_output == "Offline continuation."
            replay = model.inputs[0]
            assert replay[:1] == saved and replay[-1] == message(continued.content)
            assert sum(part.get("type") == "input_image" for item in replay if item.get("role") == "user"
                       for part in item.get("content", [])) == 1
        finally:
            session.close()
    asyncio.run(exercise())
