import base64
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from blender_agent.studio_description import credentials, describe, DEFAULT_MODEL


class Client:
    def __init__(self, body=None, status=200):
        self.calls = []
        self.body, self.status = body, status

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


def response(value, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(value)}]}}]}


def test_description_uses_explicit_images_schema_and_header_key(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (2000, 1000), "blue").save(path)
    original = path.read_bytes()
    expected = {"description": "Black wrapped shield", "specs": {"lens_type": "mirrored"}, "uncertainties": ["Scale unknown"]}
    client = Client(response(expected))
    result = describe([{"path": str(path), "mime_type": "image/png", "view": "front"}], "User text", {}, key="test-secret", client=client)
    url, request = client.calls[0]
    assert DEFAULT_MODEL in url and "test-secret" not in url
    assert request["headers"] == {"x-goog-api-key": "test-secret"}
    encoded = request["json"]["contents"][0]["parts"][2]["inlineData"]
    assert encoded["mimeType"] == "image/jpeg"
    assert result["input_images"][0]["width"] == 1600
    assert path.read_bytes() == original
    assert request["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert result["description"] == expected["description"]
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_failures_are_redacted_and_never_retried(status):
    client = Client({"error": "test-secret detailed provider body"}, status)
    with pytest.raises(RuntimeError) as caught:
        describe([], "", {}, key="test-secret", client=client)
    assert "test-secret" not in str(caught.value)
    assert len(client.calls) == 1


def test_incomplete_json_description_is_not_silently_used():
    with pytest.raises(RuntimeError):
        describe([], "", {}, key="key", client=Client(response({}, "MAX_TOKENS")))


def test_dotenv_precedence_and_only_known_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=new-key\nGEMINI_API_KEY=gemini-key\nUNRELATED_SECRET=no\n")
    assert credentials(env)["OPENAI_API_KEY"] == "new-key"
    assert "UNRELATED_SECRET" not in credentials(env)
