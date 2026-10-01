import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from blender_agent.studio_description import (SPEC_FIELDS, SpecificsError, credentials, generate_specifics,
    DEFAULT_MODEL, REPAIR_MODEL, _source_filter)

URL = "https://manufacturer.example/product/OO9208-44"
NAME = "Oakley OO9208 44"


def facts():
    value = {"identity": {"brand": "Oakley", "model": "OO9208", "variant": "44", "matched": True},
             "specs": dict.fromkeys(SPEC_FIELDS), "evidence": {k: [] for k in ("identity", *SPEC_FIELDS)}, "uncertainties": []}
    value["evidence"]["identity"] = [{"url": URL, "quote": "Oakley OO9208 44 Polished Black, size 138"}]
    value["specs"].update(frame_color="Polished Black", temple_length_mm="128")
    value["evidence"]["frame_color"] = [{"url": URL, "quote": "Frame color: Polished Black"}]
    value["evidence"]["temple_length_mm"] = [{"url": URL, "quote": "Temple length: 128 mm"}]
    return value


def response(value=None, finish="STOP", *, text=None, grounded=True):
    text = json.dumps(value or facts()) if text is None else text
    candidate = {"finishReason": finish, "content": {"parts": [{"text": text}]}}
    if grounded:
        candidate["groundingMetadata"] = {"webSearchQueries": [NAME], "groundingChunks": [{"web": {"uri": URL, "title": "Manufacturer"}}],
            "groundingSupports": [{"segment": {"partIndex": 0, "startIndex": 0, "endIndex": len(text.encode())}, "groundingChunkIndices": [0]}],
            "searchEntryPoint": {"renderedContent": "<div>Search suggestions</div>"}}
    return {"candidates": [candidate], "usageMetadata": {"promptTokenCount": 123}}


class Client:
    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        status, body = result if isinstance(result, tuple) else (200, result)
        def parse():
            if isinstance(body, Exception):
                raise body
            return body
        return SimpleNamespace(status_code=status, json=parse)


def run(client, images=None, **kwargs):
    return generate_specifics(images or [], NAME, {}, key="test-secret", client=client, sleep=lambda _: None, **kwargs)


def test_grounded_specifics_use_search_schema_images_and_header_key(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (2000, 1000), "blue").save(path)
    original = path.read_bytes()
    client = Client(response())
    result = run(client, [{"path": str(path), "original_name": "OO9208-44-front.png", "view": "front"}])
    url, request = client.calls[0]
    assert DEFAULT_MODEL in url and "test-secret" not in url
    assert request["headers"] == {"x-goog-api-key": "test-secret"}
    assert request["json"]["tools"] == [{"googleSearch": {}}]
    assert request["json"]["generationConfig"]["responseMimeType"] == "application/json"
    parts = request["json"]["contents"][0]["parts"]
    assert "OO9208-44-front.png" in parts[1]["text"] and parts[2]["inlineData"]["mimeType"] == "image/jpeg"
    assert result["input_images"][0]["width"] == 1600 and path.read_bytes() == original
    assert result["specs"]["frame_color"] == "Polished Black"
    assert result["specs"]["temple_length_mm"] == "128"
    assert result["sources"] == [{"url": URL, "title": "Manufacturer"}]
    assert result["search_suggestions_html"]
    assert "test-secret" not in json.dumps(result) and "response_text" not in result["attempts"][0]


@pytest.mark.parametrize("bad", [(401, {"secret": "test-secret"}), (429, {}), (500, {}), httpx.ReadTimeout("test-secret"),
    json.JSONDecodeError("bad envelope", "", 0), {"candidates": []}, response(finish="MAX_TOKENS"),
    response(grounded=False), {"candidates": [{"finishReason": "STOP", "content": "broken"}]}])
def test_provider_and_envelope_failures_resend_pro_never_repair(bad):
    if isinstance(bad, json.JSONDecodeError):
        bad = (200, bad)
    client = Client(bad, response())
    result = run(client)
    assert result["identity"]["matched"]
    assert len(client.calls) == 2 and all(DEFAULT_MODEL in url for url, _ in client.calls)
    assert "test-secret" not in json.dumps(result)


def test_exhaustion_receipts_mark_uncertain_billing_and_no_formatter():
    client = Client(httpx.ReadTimeout("test-secret"), (503, {}))
    receipts = []
    with pytest.raises(SpecificsError) as caught:
        run(client, on_attempt=lambda r: receipts.append(copy.deepcopy(r)))
    assert "test-secret" not in str(caught.value)
    assert len(caught.value.attempts) == 2 and all("possibly charged" in r["billing"] for r in caught.value.attempts)
    assert len(receipts) >= 4
    assert receipts[0]["attempts"][0]["state"] == "submitted"


def test_only_completed_grounded_invalid_json_calls_syntax_repair():
    original = json.dumps(facts())
    broken = original.replace(', "specs":', ' "specs":', 1)
    client = Client(response(text=broken), response(text=original, grounded=False))
    result = run(client)
    assert len(client.calls) == 2 and REPAIR_MODEL in client.calls[1][0]
    assert "tools" not in client.calls[1][1]["json"]
    assert result["attempts"][1]["syntax_only_verified"] is True
    assert result["specs"]["frame_color"] == "Polished Black"


def test_repair_cannot_change_existing_scalar_even_to_a_source_backed_value():
    original = json.dumps(facts())
    broken = original.replace(', "specs":', ' "specs":', 1)
    changed = original.replace('"frame_color": "Polished Black"', '"frame_color": "Black"')
    client = Client(response(text=broken), response(text=changed, grounded=False), response())
    result = run(client)
    assert [r["purpose"] for r in result["attempts"]] == ["research", "syntax_repair", "research"]
    assert result["attempts"][1]["state"] == "rejected"


@pytest.mark.parametrize("change", [lambda v: v["specs"].update(unsupported="invented"),
    lambda v: v["specs"].update(frame_width_mm=123), lambda v: v.update(description="removed")])
def test_valid_json_schema_errors_resend_pro_without_formatter(change):
    value = facts(); change(value)
    client = Client(response(value), response())
    run(client)
    assert len(client.calls) == 2 and all(DEFAULT_MODEL in url for url, _ in client.calls)


@pytest.mark.parametrize("text", [json.dumps(facts()).replace('"matched": true', '"matched": true, "matched": true'),
    json.dumps(facts()).replace('"frame_width_mm": null', '"frame_width_mm": NaN')])
def test_duplicate_keys_and_nonfinite_json_are_not_syntax_repaired(text):
    client = Client(response(text=text), response())
    run(client)
    assert all(DEFAULT_MODEL in url for url, _ in client.calls)


@pytest.mark.parametrize("edit,field", [
    (lambda v: v["evidence"]["frame_color"][0].update(url="https://invented.example/"), "frame_color"),
    (lambda v: v["specs"].update(temple_length_mm="128-140"), "temple_length_mm"),
    (lambda v: v["evidence"]["temple_length_mm"][0].update(quote="Lens width: 128 mm"), "temple_length_mm"),
    (lambda v: v["evidence"]["frame_color"][0].update(quote="Polished Black option available"), "frame_color"),
    (lambda v: v["evidence"]["frame_color"][0].update(quote="Not Polished Black"), "frame_color"),
])
def test_unsupported_wrong_field_and_family_options_are_null(edit, field):
    value = facts(); edit(value)
    result = run(Client(response(value)))
    assert result["specs"][field] is None and result["evidence"][field] == []


def test_wrong_variant_substring_and_no_requested_code_return_unknown_without_retry():
    value = facts(); value["evidence"]["identity"][0]["quote"] = "Oakley OO9208 440 Polished Black"
    client = Client(response(value))
    result = run(client)
    assert len(client.calls) == 1 and result["identity"]["matched"] is False
    assert all(v is None for v in result["specs"].values())
    result = generate_specifics([], "Oakley family", {}, key="key", client=Client(response()), sleep=lambda _: None)
    assert result["identity"]["matched"] is False
    result = generate_specifics([], "Oakley OO9208", {}, key="key", client=Client(response()), sleep=lambda _: None)
    assert result["identity"]["matched"] is False  # Family-only input cannot select variant 44 itself.


@pytest.mark.parametrize("web", [None, [], "bad"])
def test_malformed_grounding_resends_pro(web):
    body = response()
    body["candidates"][0]["groundingMetadata"]["groundingChunks"] = [{"web": web}]
    client = Client(body, response())
    run(client)
    assert len(client.calls) == 2 and all(DEFAULT_MODEL in url for url, _ in client.calls)


@pytest.mark.parametrize("material_quote", ["Nose pads and earsocks: Unobtainium", "Frame: O Matter; nose pads: Unobtainium"])
def test_dimension_value_belongs_to_its_own_label_and_material_to_its_own_part(material_quote):
    value = facts()
    value["specs"].update(frame_width_mm="38", frame_material="Unobtainium", lens_color="Black")
    value["evidence"].update(frame_width_mm=[{"url": URL, "quote": "Frame width 138 mm; lens width 38 mm."}],
                             frame_material=[{"url": URL, "quote": material_quote}],
                             lens_color=[{"url": URL, "quote": "Frame color: Black"}])
    result = run(Client(response(value)))
    assert all(result["specs"][field] is None for field in ("frame_width_mm", "frame_material", "lens_color"))


def test_support_must_cover_field_evidence_not_unrelated_text():
    body = response()
    candidate = body["candidates"][0]
    candidate["groundingMetadata"]["groundingSupports"][0]["segment"] = {"partIndex": 0, "text": "Oakley OO9208 44 Polished Black, size 138"}
    result = run(Client(body))
    assert result["identity"]["matched"]
    assert result["specs"]["frame_color"] is None


def test_utf8_offsets_are_bytes_and_part_specific():
    value = facts(); body = response(value)
    candidate = body["candidates"][0]
    text = candidate["content"]["parts"][0]["text"]
    candidate["content"]["parts"] = [{"text": "thought", "thought": True}, {"text": "שלום " + text}]
    candidate["groundingMetadata"]["groundingSupports"][0]["segment"] = {
        "partIndex": 1, "startIndex": len("שלום ".encode()), "endIndex": len(("שלום " + text).encode())}
    result = _source_filter(value, candidate, NAME)
    assert result["specs"]["frame_color"] == "Polished Black"


def test_dotenv_precedence_and_only_known_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=new-key\nGEMINI_API_KEY=gemini-key\nUNRELATED_SECRET=no\n")
    assert credentials(env)["OPENAI_API_KEY"] == "new-key"
    assert "UNRELATED_SECRET" not in credentials(env)
