"""Offline implementation receipt coverage, including Windows argv limits."""
import hashlib
import json
import subprocess

from glasses3d.agent import snapshot as S


def test_snapshot_diff_argv_does_not_grow_with_2000_tracked_paths(tmp_path, monkeypatch):
    names = [f"glasses3d/agent/generated_source_{i:04d}_long_module_name.py" for i in range(2000)]
    # All paths are deleted locally: their provenance must remain in the diff,
    # even though none should reappear in the frozen source archive.
    listing = ("\0".join(names) + "\0").encode()
    calls = []

    def git(root, *args):
        calls.append(args)
        if args[0] == "ls-files":
            return listing
        if args[0] == "diff":
            return b"synthetic tracked deletions\n"
        if args == ("rev-parse", "HEAD"):
            return b"fixture-head\n"
        raise AssertionError(args)

    monkeypatch.setattr(S, "_git", git)
    monkeypatch.setattr(S, "runtime_versions", lambda: {})
    S.capture(tmp_path / "run", repo_root=tmp_path / "repo")
    diff_args, = [args for args in calls if args[0] == "diff"]
    assert diff_args[:5] == ("diff", "HEAD", "--binary", "--", "glasses3d")
    assert len(subprocess.list2cmdline(["git", *diff_args])) < 2048
    assert not set(names).intersection(diff_args)
    receipt = json.loads((tmp_path / "run/implementation.json").read_text())
    assert receipt["files"] == {}
    assert receipt["diff_head_sha256"] == hashlib.sha256(b"synthetic tracked deletions\n").hexdigest()


def test_snapshot_diff_preserves_deletions_and_component_exclusions(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout

    included = ["glasses3d/agent/changed.py", "glasses3d/agent/deleted.py",
                "glasses3d/agent/data_rules.py", "glasses3d/agent/environment.py"]
    excluded = [f"glasses3d/{prefix}{part}/private.txt"
                for prefix in ("", "agent/nested/")
                for part in ("data", ".venv", "node_modules", "__pycache__", ".env.local")]
    excluded += [f"glasses3d/{prefix}{part}"
                 for prefix in ("", "agent/nested/") for part in (".env", ".env.production")]
    outside = ["other_application.py"]
    for name in included + excluded + outside:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before\n")
    git("init")
    git("add", "--", "glasses3d", "other_application.py")
    git("-c", "user.name=Offline Test", "-c", "user.email=test@invalid.example", "commit", "-m", "fixture")
    for name in included + excluded + outside:
        (repo / name).write_text("after\n")
    (repo / included[1]).unlink()
    # Excluded deletions must stay excluded too.
    (repo / excluded[0]).unlink()
    expected_diff = git("diff", "HEAD", "--binary", "--", *included)
    assert b"deleted file mode" in expected_diff
    monkeypatch.setattr(S, "runtime_versions", lambda: {})
    S.capture(tmp_path / "run", repo_root=repo)
    receipt = json.loads((tmp_path / "run/implementation.json").read_text())
    assert receipt["diff_head_sha256"] == hashlib.sha256(expected_diff).hexdigest()
    assert set(receipt["files"]) == set(included) - {included[1]}


def test_diff_pathspecs_exclude_component_named_like_a_directory(tmp_path):
    """The archive's component filter also excludes regular files named data, etc."""
    repo = tmp_path / "repo"
    source = repo / "glasses3d/agent"
    source.mkdir(parents=True)

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout

    names = ["data", "node_modules", "__pycache__", ".venv", ".env.secret"]
    for name in names:
        (source / name).write_text("before\n")
    git("init")
    git("add", "--", "glasses3d")
    git("-c", "user.name=Offline Test", "-c", "user.email=test@invalid.example", "commit", "-m", "fixture")
    for name in names:
        (source / name).write_text("after\n")
    assert git("diff", "HEAD", "--binary", "--", *S.DIFF_PATHSPECS) == b""
