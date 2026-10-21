"""Renderer bundle route confinement and cache invalidation without a browser."""
from types import SimpleNamespace

import pytest

from glasses3d.dev_ui import lens_renderer as renderer


def test_build_cache_rebuilds_changed_source_and_missing_output(tmp_path, monkeypatch):
    build = tmp_path / "bundle"
    monkeypatch.setattr(renderer, "BUILD_ROOT", build)
    version = ["first"]
    monkeypatch.setattr(renderer, "_source_digest", lambda: version[0])
    calls = []

    def build_bundle(*args, **kwargs):
        calls.append(args)
        for name in renderer._ALLOWED:
            target = build / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", build_bundle)
    assert renderer.ensure_renderer_build() == build
    assert renderer.ensure_renderer_build() == build
    assert len(calls) == 1
    version[0] = "changed-ar-renderer-source"
    renderer.ensure_renderer_build()
    assert len(calls) == 2
    (build / "models/canonical-face.json").unlink()
    renderer.ensure_renderer_build()
    assert len(calls) == 3


@pytest.mark.parametrize("name", ["../server.py", "build.json", "models/../../api.py", "C:/secret.js", "lens-renderer.js/../build.json"])
def test_only_fixed_public_assets_can_be_served(name, monkeypatch):
    def never_build():
        pytest.fail("Invalid paths must refuse before building or reading a file.")
    monkeypatch.setattr(renderer, "ensure_renderer_build", never_build)
    with pytest.raises(FileNotFoundError):
        renderer.renderer_asset(name)


def test_allowed_asset_resolves_inside_generated_directory(tmp_path, monkeypatch):
    build = tmp_path / "bundle"
    build.mkdir()
    file = build / "lens-renderer.js"
    file.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(renderer, "ensure_renderer_build", lambda: build)
    assert renderer.renderer_asset("lens-renderer.js") == file


def test_failed_build_does_not_write_success_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "BUILD_ROOT", tmp_path)
    monkeypatch.setattr(renderer, "_source_digest", lambda: "source")
    monkeypatch.setattr(renderer.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr="synthetic build refusal", stdout=""))
    with pytest.raises(RuntimeError, match="synthetic build refusal"):
        renderer.ensure_renderer_build()
    assert not (tmp_path / "build.json").exists()
