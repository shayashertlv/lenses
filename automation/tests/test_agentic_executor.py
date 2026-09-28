"""The worker protocol of ``modeler.agentic.executor``: bundles, ingestion, the three workers and the Docker doctor.

Everything here is offline: no network, no paid call, no Docker engine (the DockerWorker and the doctor run against a
scripted fake ``docker`` runner) and no generated code on the host. The only process this module may start is the
installed host Blender, in ONE test marked ``slow`` that runs the audited generic fixture through the
NativeFixtureWorker with its own hash allow-listed ("native fixture parity"); it skips when Blender is absent.
Docker isolation itself is NOT verified here: the argument vector is, the engine is not.

Temporary folders live under a short temp path (tests/test_agentic_support.py, lag-executor) because Windows path
length matters for bundles, staging and Blender output.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

import pytest

from modeler.agentic import executor as ex
from modeler.agentic.executor import (DockerWorker, FakeWorker, Ingested, NativeFixtureWorker, WorkerError, WorkerRefused, docker_doctor, harness_job_for,
                                      program_set_sha256, result_is_untrusted_receipt, validate_and_ingest_dir, validate_and_ingest_tar, validate_worker_config,
                                      worker_config_fingerprint, write_bundle)
from modeler.paths import BLENDER_DIR, blender_executable
from test_agentic_support import lag_root


MODS = {"setup": "gl.note('setup')\n", "frame": "front = gl.box((0, 0, 0), (140, 50, 6), 'front', 'frame', 'front')\n", "lenses": "gl.note('lenses')\n"}
ORDER = ["setup", "frame", "lenses"]
RENDERS = [{"id": "front", "kind": "textured", "width": 320, "height": 240, "camera": {"type": "orbit", "yaw": 0, "pitch": 0}},
           {"id": "side", "kind": "clay", "width": 200, "height": 150, "camera": {"type": "orbit", "yaw": 90, "pitch": 0}}]
EVIDENCE = {"product_id": "unit", "scale": {"front_width_mm": 140.0}, "views": {"img0001": {"view": "front", "flags": []}},
            "inputs": [{"id": "img0001", "view": "front", "held_out": False, "sha256": "ab" * 32}]}
IDENT = {"job_short": "0123456789ab", "operation_id": "op0001", "attempt": 1}
CID = "c" * 64
DIGEST = "lenses-worker@sha256:" + "a" * 64
REQUIRED_ENV = {"HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "BLENDER_USER_RESOURCES", "LENSES_TIME_LIMIT_S", "LP_NUM_THREADS"}
# the complete --env key set of a docker run: a new key must be added here deliberately (nothing of the host leaks by accident)
PINNED_ENV = REQUIRED_ENV | {"PYTHONDONTWRITEBYTECODE"}
CREDENTIAL_WORDS = ("KEY", "TOKEN", "SECRET", "OPENAI", "PASSWORD", "CREDENTIAL")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- temp folders
def _remove_tree(root: Path) -> None:
    """Remove junctions and directory links first (never descend into their targets), then the rest."""
    for dirpath, dirnames, _ in os.walk(root, topdown=True, followlinks=False):
        for d in list(dirnames):
            p = Path(dirpath) / d
            if ex.is_reparse_point(p):
                try:
                    os.rmdir(p)
                except OSError:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                dirnames.remove(d)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def tmp():
    d = Path(tempfile.mkdtemp(dir=lag_root("executor")))
    yield d
    _remove_tree(d)


def make_dir_link(target: Path, link: Path) -> bool:
    """A directory reparse point: a junction on Windows (needs no privilege), a symlink elsewhere; False when impossible."""
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    return ex.is_reparse_point(link)


def make_file_symlink(target: Path, link: Path) -> bool:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return ex.is_reparse_point(link)


def build_bundle(root: Path, **over) -> tuple[Path, dict]:
    kw = dict(operation_id="op0001", mode="build", modules=MODS, module_order=ORDER, renders=RENDERS, evidence=EVIDENCE, blend_bytes=None, time_limit_s=120)
    kw.update(over)
    bundle = root / "bundle"
    return bundle, write_bundle(bundle, **kw)


def make_tar(regular: dict[str, bytes], specials: list[tarfile.TarInfo] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in regular.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for info in specials:
            tf.addfile(info)
    return buf.getvalue()


def special(name: str, kind: bytes, **attrs) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    for k, v in attrs.items():
        setattr(info, k, v)
    return info


# --------------------------------------------------------------------------- write_bundle
class TestWriteBundle:
    def test_layout_of_a_build_bundle(self, tmp):
        bundle, op = build_bundle(tmp, module_order=["setup", "frame", "lenses", "hardware"])      # hardware is not in the modules: skipped
        assert json.loads((bundle / "operation.json").read_text(encoding="utf-8")) == op
        assert op["protocol"] == "lenses_agentic_worker_v1" and op["operation_id"] == "op0001" and op["mode"] == "build"
        for name in ("glasses_lib.py", "harness.py"):
            data = (bundle / "lib" / name).read_bytes()
            assert data == (BLENDER_DIR / name).read_bytes()
            assert op["lib_sha256"][name] == sha(data)
        assert [m["name"] for m in op["modules"]] == ["setup", "frame", "lenses"]                # module order, not dict order
        assert [m["file"] for m in op["modules"]] == ["program/setup.py", "program/frame.py", "program/lenses.py"]
        for name in ORDER:
            assert (bundle / "program" / f"{name}.py").read_text(encoding="utf-8") == MODS[name]
        assert sorted(p.name for p in (bundle / "program").iterdir()) == ["frame.py", "lenses.py", "setup.py"]
        assert json.loads((bundle / "input" / "evidence.json").read_text(encoding="utf-8")) == EVIDENCE
        assert op["evidence"] == "input/evidence.json" and op["blend"] is None
        assert not (bundle / "input" / "candidate.blend").exists()
        assert op["program_set_sha256"] == program_set_sha256(MODS)
        assert op["time_limit_s"] == 120 and op["samples"] == 16 and op["export"] is True and op["save_blend"] is True
        assert op["samples"] == ex.DEFAULT_SAMPLES        # the harness default; the observation asks for modeler.observe.RENDER_SAMPLES itself
        assert op["renders"] == RENDERS and op["output_limits"] == ex.OUTPUT_LIMITS
        assert sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()) == \
            ["input/evidence.json", "lib/glasses_lib.py", "lib/harness.py", "operation.json", "program/frame.py", "program/lenses.py", "program/setup.py"]

    def test_render_only_bundle_carries_the_blend(self, tmp):
        blend = b"BLENDER-v502RENDH" + b"\x00" * 64
        bundle, op = build_bundle(tmp, mode="render_only", modules={}, module_order=[], evidence=None, blend_bytes=blend, samples=8, export=False)
        assert (bundle / "input" / "candidate.blend").read_bytes() == blend
        assert op["blend"] == "input/candidate.blend" and op["evidence"] is None and op["modules"] == []
        assert op["samples"] == 8 and op["export"] is False
        assert not (bundle / "input" / "evidence.json").exists()

    def test_refuses_a_non_empty_folder(self, tmp):
        bundle = tmp / "bundle"
        bundle.mkdir()
        (bundle / "stale.txt").write_text("left over")
        with pytest.raises(WorkerError, match="not empty"):
            write_bundle(bundle, operation_id="op0001", mode="build", modules=MODS, module_order=ORDER, renders=[], evidence=None, blend_bytes=None, time_limit_s=10)
        assert [p.name for p in bundle.iterdir()] == ["stale.txt"]

    def test_refuses_render_only_without_a_blend(self, tmp):
        bundle = tmp / "bundle"
        with pytest.raises(WorkerError, match="render_only needs the candidate .blend"):
            write_bundle(bundle, operation_id="op0001", mode="render_only", modules={}, module_order=[], renders=RENDERS, evidence=None, blend_bytes=None, time_limit_s=10)
        assert not bundle.exists() or not any(bundle.iterdir())

    def test_refuses_an_unknown_mode(self, tmp):
        with pytest.raises(WorkerError, match="unknown operation mode"):
            write_bundle(tmp / "bundle", operation_id="op0001", mode="deploy", modules=MODS, module_order=ORDER, renders=[], evidence=None, blend_bytes=None, time_limit_s=10)

    @pytest.mark.parametrize("evidence", [
        dict(EVIDENCE, held_out={"img0004": {"view": "angled"}}),
        dict(EVIDENCE, inputs=EVIDENCE["inputs"] + [{"id": "img0004", "view": "angled", "held_out": True, "sha256": "cd" * 32}]),
    ], ids=["held_out_key", "held_out_input"])
    def test_sealed_evidence_never_enters_a_bundle(self, tmp, evidence):
        bundle = tmp / "bundle"
        with pytest.raises(WorkerError, match="sealed evidence"):
            write_bundle(bundle, operation_id="op0001", mode="build", modules=MODS, module_order=ORDER, renders=[], evidence=evidence, blend_bytes=None, time_limit_s=10)
        assert not (bundle / "input" / "evidence.json").exists()
        assert not any(bundle.rglob("*")) if bundle.exists() else True

    @pytest.mark.parametrize("bad", ["../evil", "Frame", "frame-1", "1frame", "frame.py", "a" * 33, "frame/../setup", "", "frame lenses"])
    def test_refuses_unsafe_module_names(self, tmp, bad):
        bundle = tmp / "bundle"
        with pytest.raises(WorkerError, match="not safe"):
            write_bundle(bundle, operation_id="op0001", mode="build", modules={**MODS, bad: "gl.note('x')\n"}, module_order=["setup", bad, "frame"], renders=[],
                         evidence=None, blend_bytes=None, time_limit_s=10)
        written = [p.relative_to(bundle).as_posix() for p in bundle.rglob("*.py") if p.is_file()]
        assert all(w.startswith("lib/") or w in ("program/setup.py",) for w in written), written
        assert not (bundle / "operation.json").exists()


class TestHarnessJob:
    def test_every_path_maps_into_the_bundle_root_and_out_dir(self, tmp):
        _, op = build_bundle(tmp)
        job = harness_job_for(op, bundle_root="/bundle", out_dir="/work/out")
        assert job["lib_dir"] == "/bundle/lib" and job["out_dir"] == "/work/out"
        assert job["modules"] == [{"name": "setup", "path": "/bundle/program/setup.py"}, {"name": "frame", "path": "/bundle/program/frame.py"},
                                  {"name": "lenses", "path": "/bundle/program/lenses.py"}]
        assert job["evidence_path"] == "/bundle/input/evidence.json"
        assert "blend_path" not in job
        assert job["mode"] == "build" and job["export"] is True and job["save_blend"] is True and job["renders"] == RENDERS and job["samples"] == 16
        paths = [job["lib_dir"], job["evidence_path"]] + [m["path"] for m in job["modules"]]
        assert all(p.startswith("/bundle/") for p in paths)
        # a Windows host root maps the same way
        job2 = harness_job_for(op, bundle_root="C:/Users/x/job/worker/op0001/bundle", out_dir="C:/Users/x/job/worker/op0001/work/out")
        assert job2["modules"][1]["path"] == "C:/Users/x/job/worker/op0001/bundle/program/frame.py"
        assert job2["out_dir"] == "C:/Users/x/job/worker/op0001/work/out"

    def test_render_only_job_names_the_blend(self, tmp):
        _, op = build_bundle(tmp, mode="render_only", modules={}, module_order=[], evidence=None, blend_bytes=b"BLENDER")
        job = harness_job_for(op, bundle_root="/bundle", out_dir="/work/out")
        assert job["blend_path"] == "/bundle/input/candidate.blend"
        assert job["evidence_path"] is None and job["modules"] == [] and job["mode"] == "render_only"


class TestProgramSetSha:
    def test_order_independent_and_content_sensitive(self):
        a = program_set_sha256({"setup": "x = 1\n", "frame": "y = 2\n"})
        b = program_set_sha256({"frame": "y = 2\n", "setup": "x = 1\n"})
        assert a == b and len(a) == 64
        assert program_set_sha256({"setup": "x = 1\n", "frame": "y = 3\n"}) != a
        assert program_set_sha256({"setup": "x = 1\n", "frame": "y = 2\n", "lenses": ""}) != a
        assert program_set_sha256({"setup2": "x = 1\n", "frame": "y = 2\n"}) != a          # the name is part of the identity
        assert program_set_sha256({}) == program_set_sha256({})


# --------------------------------------------------------------------------- validate_and_ingest_dir
class TestIngestDir:
    def test_copies_regular_files_and_hashes_them(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        (src / "renders").mkdir(parents=True)
        result = {"ok": True, "renders": [{"id": "front", "path": "/work/out/renders/front.png"}]}
        (src / "result.json").write_bytes(json.dumps(result).encode())
        (src / "renders" / "front.png").write_bytes(b"\x89PNG-not-really" * 3)
        (src / "blender.stdout.log").write_bytes(b"MODELER_HARNESS| done ok=True\n")
        ing = validate_and_ingest_dir(src, staging)
        assert [f["rel"] for f in ing.files] == ["blender.stdout.log", "renders/front.png", "result.json"]
        for f in ing.files:
            data = (staging / f["rel"]).read_bytes()
            assert data == (src / f["rel"]).read_bytes()
            assert f["sha256"] == sha(data) and f["bytes"] == len(data)
        assert ing.result == result and ing.problems is None and ing.synthetic is False
        assert ing.manifest_sha256 == sha(json.dumps(sorted(ing.files, key=lambda f: f["rel"]), sort_keys=True).encode())
        assert result_is_untrusted_receipt(ing) == []

    def test_staging_must_be_empty(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        src.mkdir()
        staging.mkdir()
        (staging / "old").write_text("x")
        with pytest.raises(WorkerError, match="staging must be empty"):
            validate_and_ingest_dir(src, staging)

    def test_refuses_a_junction_directory(self, tmp):
        src, staging, outside = tmp / "out", tmp / "staging", tmp / "outside"
        src.mkdir()
        outside.mkdir()
        (outside / "leak.txt").write_text("must not be ingested")
        (src / "ok.txt").write_text("fine")
        if not make_dir_link(outside, src / "jn"):
            pytest.skip("cannot create a directory junction/symlink here")
        ing = validate_and_ingest_dir(src, staging)
        assert [f["rel"] for f in ing.files] == ["ok.txt"]
        assert ing.problems and any(p.startswith("link directory refused: jn") for p in ing.problems), ing.problems
        assert not (staging / "jn").exists() and not list(staging.rglob("leak.txt"))
        assert (outside / "leak.txt").is_file()

    def test_refuses_a_file_symlink(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        src.mkdir()
        (tmp / "secret.txt").write_text("outside the output")
        (src / "ok.txt").write_text("fine")
        if not make_file_symlink(tmp / "secret.txt", src / "link.txt"):
            pytest.skip("symlinks need a privilege this account lacks")
        ing = validate_and_ingest_dir(src, staging)
        assert [f["rel"] for f in ing.files] == ["ok.txt"]
        assert ing.problems and any(p.startswith("link.txt:") for p in ing.problems), ing.problems
        assert not (staging / "link.txt").exists()

    def test_refuses_oversize_files(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        src.mkdir()
        (src / "small.bin").write_bytes(b"x" * 10)
        (src / "big.bin").write_bytes(b"y" * 20)
        limits = {"max_files": 10, "max_file_bytes": 12, "max_total_bytes": 1000, "max_log_bytes": 1000}
        ing = validate_and_ingest_dir(src, staging, limits=limits)
        assert [f["rel"] for f in ing.files] == ["small.bin"]
        assert not (staging / "big.bin").exists()
        assert ing.problems and any(p.startswith("big.bin:") and "20 bytes" in p for p in ing.problems), ing.problems

    def test_stops_at_max_files(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        (src / "a").mkdir(parents=True)
        (src / "b").mkdir()
        for i in range(3):
            (src / "a" / f"f{i}.bin").write_bytes(b"x" * 4)
            (src / "b" / f"g{i}.bin").write_bytes(b"x" * 4)
        limits = {"max_files": 2, "max_file_bytes": 1000, "max_total_bytes": 1000, "max_log_bytes": 1000}
        ing = validate_and_ingest_dir(src, staging, limits=limits)
        assert len(ing.files) == 2
        assert len([p for p in staging.rglob("*") if p.is_file()]) == 2
        assert ing.problems and any("file-count limit" in p for p in ing.problems)

    def test_stops_at_max_total_bytes(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        src.mkdir()
        for i in range(4):
            (src / f"f{i}.bin").write_bytes(b"x" * 10)
        limits = {"max_files": 100, "max_file_bytes": 1000, "max_total_bytes": 25, "max_log_bytes": 1000}
        ing = validate_and_ingest_dir(src, staging, limits=limits)
        assert len(ing.files) == 2 and sum(f["bytes"] for f in ing.files) == 20
        assert len([p for p in staging.rglob("*") if p.is_file()]) == 2
        assert ing.problems and any("total byte" in p for p in ing.problems)

    def test_result_json_problems_are_listed(self, tmp):
        src, staging = tmp / "out", tmp / "staging"
        src.mkdir()
        (src / "result.json").write_bytes(b"[1, 2, 3]")
        ing = validate_and_ingest_dir(src, staging)
        assert ing.result is None and ing.problems == ["result.json is not an object"]
        src2, staging2 = tmp / "out2", tmp / "staging2"
        src2.mkdir()
        (src2 / "result.json").write_bytes(b"{not json")
        ing2 = validate_and_ingest_dir(src2, staging2)
        assert ing2.result is None and ing2.problems and ing2.problems[0].startswith("result.json unreadable: JSONDecodeError")


# --------------------------------------------------------------------------- validate_and_ingest_tar
class TestIngestTar:
    def unsafe_stream(self, per_file_limit: int) -> tuple[bytes, dict]:
        result = {"ok": True, "renders": [{"id": "v", "path": "/work/out/renders/v.png"}], "parts_npz": "/work/out/parts.npz"}
        regular = {"out/result.json": json.dumps(result).encode(), "out/renders/v.png": b"PNGDATA", "out/parts.npz": b"NPZ",
                   "out/../escape.txt": b"escape", "/abs/abs.txt": b"absolute", "C:/win.txt": b"drive", "out/C:" + chr(92) + "x.txt": b"drive2",
                   "out/big.bin": b"z" * (per_file_limit + 1)}
        specials = [special("out/sub", tarfile.DIRTYPE), special("out/link", tarfile.SYMTYPE, linkname="result.json"),
                    special("out/hard", tarfile.LNKTYPE, linkname="out/result.json"), special("out/dev", tarfile.CHRTYPE, devmajor=1, devminor=3),
                    special("out/fifo", tarfile.FIFOTYPE)]
        return make_tar(regular, specials), result

    def test_only_safe_regular_files_land_in_staging(self, tmp):
        limits = {"max_files": 50, "max_file_bytes": 256, "max_total_bytes": 4096, "max_log_bytes": 1000}
        stream, result = self.unsafe_stream(limits["max_file_bytes"])
        staging = tmp / "staging"
        ing = validate_and_ingest_tar(stream, staging, strip_prefix="out/", limits=limits)
        assert [f["rel"] for f in ing.files] == ["parts.npz", "renders/v.png", "result.json"]
        assert sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()) == ["parts.npz", "renders/v.png", "result.json"]
        assert (staging / "renders" / "v.png").read_bytes() == b"PNGDATA"
        assert ing.result == result and result_is_untrusted_receipt(ing) == []
        assert not (tmp / "escape.txt").exists() and not (staging.parent / "escape.txt").exists()
        problems = "\n".join(ing.problems or [])
        for name in ("out/link", "out/hard", "out/dev", "out/fifo"):
            assert f"{name}: not a regular file" in problems, problems
        for name in ("out/../escape.txt", "/abs/abs.txt", "C:/win.txt", "out/C:" + chr(92) + "x.txt"):
            assert f"{name}: unsafe path refused" in problems, problems
        assert f"big.bin: {limits['max_file_bytes'] + 1} bytes above the per-file limit" in problems, problems
        assert not (staging / "big.bin").exists()
        assert "out/sub" not in problems                        # directories are simply not entries

    def test_strip_prefix_is_optional_and_exact(self, tmp):
        stream = make_tar({"out/result.json": b"{}", "other/x.txt": b"x"})
        ing = validate_and_ingest_tar(stream, tmp / "s1")
        assert [f["rel"] for f in ing.files] == ["other/x.txt", "out/result.json"]
        assert ing.result is None                             # result.json is only recognised at the staging root
        ing2 = validate_and_ingest_tar(stream, tmp / "s2", strip_prefix="out/")
        assert [f["rel"] for f in ing2.files] == ["other/x.txt", "result.json"]
        assert ing2.result == {}

    def test_stops_at_the_count_and_total_limits(self, tmp):
        stream = make_tar({f"out/f{i}.bin": b"x" * 10 for i in range(5)})
        limits = {"max_files": 3, "max_file_bytes": 100, "max_total_bytes": 1000, "max_log_bytes": 1000}
        ing = validate_and_ingest_tar(stream, tmp / "s1", strip_prefix="out/", limits=limits)
        assert len(ing.files) == 3 and len([p for p in (tmp / "s1").rglob("*") if p.is_file()]) == 3
        assert ing.problems and any("file-count limit" in p for p in ing.problems)
        limits2 = {"max_files": 100, "max_file_bytes": 100, "max_total_bytes": 25, "max_log_bytes": 1000}
        ing2 = validate_and_ingest_tar(stream, tmp / "s2", strip_prefix="out/", limits=limits2)
        assert len(ing2.files) == 2 and ing2.problems and any("total byte" in p for p in ing2.problems)

    def test_not_a_tar_and_non_empty_staging(self, tmp):
        with pytest.raises(WorkerError, match="not a tar archive"):
            validate_and_ingest_tar(b"this is not a tar stream at all" * 40, tmp / "s1")
        (tmp / "s2").mkdir()
        (tmp / "s2" / "old").write_text("x")
        with pytest.raises(WorkerError, match="staging must be empty"):
            validate_and_ingest_tar(make_tar({"a": b"a"}), tmp / "s2")

    def test_single_file_extractor(self, tmp):
        assert ex.validate_and_ingest_tar_bytes_single(make_tar({"READY.json": b'{"ok": true}'})) == b'{"ok": true}'
        with pytest.raises(WorkerError, match="exactly one small regular file"):
            ex.validate_and_ingest_tar_bytes_single(make_tar({"a": b"1", "b": b"2"}))
        with pytest.raises(WorkerError):
            ex.validate_and_ingest_tar_bytes_single(make_tar({}, [special("link", tarfile.SYMTYPE, linkname="x")]))


class TestUntrustedReceipt:
    def make(self, files: list[str], result: dict) -> Ingested:
        rows = [{"rel": f, "sha256": sha(f.encode()), "bytes": 1} for f in files]
        return Ingested(staging=Path("."), files=rows, result=result, manifest_sha256="0" * 64)

    def test_flags_files_named_but_not_ingested(self):
        result = {"ok": True, "parts_npz": "/work/out/parts.npz", "materials_json": "/work/out/materials.json", "blend": "/work/out/candidate.blend",
                  "renders": [{"id": "front", "path": "/work/out/front.png"}, {"id": "side", "path": "/work/out/side.png"}]}
        issues = result_is_untrusted_receipt(self.make(["result.json", "parts.npz", "front.png"], result))
        assert len(issues) == 3
        assert any("materials_json" in i for i in issues) and any("blend=" in i for i in issues) and any("render side" in i for i in issues)
        assert not any("parts_npz" in i for i in issues) and not any("render front" in i for i in issues)

    def test_complete_receipt_has_no_issues(self):
        result = {"ok": True, "parts_npz": "C:/job/work/out/parts.npz", "materials_json": "materials.json", "blend": None,
                  "renders": [{"id": "front", "path": "C:\\job\\work\\out\\renders\\front.png"}]}
        assert result_is_untrusted_receipt(self.make(["result.json", "parts.npz", "materials.json", "renders/front.png"], result)) == []
        assert result_is_untrusted_receipt(Ingested(staging=Path("."), files=[], result=None, manifest_sha256="0" * 64)) == []


# --------------------------------------------------------------------------- FakeWorker
class TestFakeWorker:
    def test_describe_says_synthetic(self):
        d = FakeWorker().describe()
        assert d["kind"] == "fake" and d["synthetic"] is True and "SYNTHETIC" in d["note"]
        assert FakeWorker.synthetic is True

    def test_successful_build_produces_labelled_synthetic_output(self, tmp):
        from PIL import Image
        bundle, op = build_bundle(tmp)
        worker = FakeWorker()
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
        assert identity["kind"] == "fake" and identity["ordinal"] == 1 and identity["operation_id"] == "op0001" and identity["job_short"] == IDENT["job_short"]
        outcome = worker.await_outcome(identity, deadline_s=60)
        assert outcome.status == "completed" and outcome.exit_code == 0 and "SYNTHETIC" in outcome.stdout_tail
        ing = worker.ingest(identity, tmp / "staging")
        assert ing.synthetic is True and ing.problems is None
        assert [f["rel"] for f in ing.files] == ["blender.stdout.log", "candidate.blend", "front.png", "result.json", "side.png"]
        result = ing.result
        assert result["ok"] is True and result["synthetic"] is True and result["blender_version"] == "SYNTHETIC" and result["blend"] == "candidate.blend"
        assert [m["ok"] for m in result["module_results"]] == [True, True, True] and result["error"] is None
        assert {r["id"]: r["path"] for r in result["renders"]} == {"front": "front.png", "side": "side.png"}
        assert result_is_untrusted_receipt(ing) == []
        assert (tmp / "staging" / "candidate.blend").read_bytes().startswith(b"SYNTHETIC-BLEND")
        with Image.open(tmp / "staging" / "front.png") as front, Image.open(tmp / "staging" / "side.png") as side:
            assert front.size == (320, 240) and side.size == (200, 150)
            assert front.convert("RGB").tobytes() != side.convert("RGB").tobytes()
            assert front.convert("RGB").crop((0, 0, 320, 240)).getcolors(100000) and len(front.convert("RGB").getcolors(100000)) > 1     # text drawn, not flat

    def test_scripted_failure_by_ordinal(self, tmp):
        bundle, op = build_bundle(tmp)
        worker = FakeWorker({1: {"ok": False, "error": "SyntheticError: scripted build failure in module lenses"}})
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
        outcome = worker.await_outcome(identity, deadline_s=60)
        assert outcome.status == "failed" and outcome.exit_code == 1
        ing = worker.ingest(identity, tmp / "staging")
        r = ing.result
        assert r["ok"] is False and r["error"] == "SyntheticError: scripted build failure in module lenses" and r["synthetic"] is True
        assert [m["ok"] for m in r["module_results"]] == [True, True, False]
        assert r["module_results"][-1]["error"] == "SyntheticError: scripted build failure in module lenses" and r["module_results"][-1]["traceback"]
        assert r["blend"] is None and r["renders"] == [] and r["inventory"] == []
        assert [f["rel"] for f in ing.files] == ["blender.stdout.log", "result.json"]
        assert ing.synthetic is True

    def test_scenario_by_operation_id_and_unlisted_succeeds(self, tmp):
        worker = FakeWorker({"op0002": {"ok": False, "error": "SyntheticError: second"}})
        b1, op1 = build_bundle(tmp / "one")
        b2, op2 = build_bundle(tmp / "two", operation_id="op0002")
        i1 = worker.launch(b1, op1, work_dir=tmp / "w1", deadline_s=5, identity=dict(IDENT))
        i2 = worker.launch(b2, op2, work_dir=tmp / "w2", deadline_s=5, identity=dict(IDENT, operation_id="op0002"))
        assert worker.await_outcome(i1, deadline_s=5).status == "completed"
        assert worker.await_outcome(i2, deadline_s=5).status == "failed"
        assert i2["ordinal"] == 2

    def test_hang_times_out_and_unknown_identity_is_lost(self, tmp):
        bundle, op = build_bundle(tmp)
        worker = FakeWorker({1: {"hang": True}})
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=7, identity=dict(IDENT))
        outcome = worker.await_outcome(identity, deadline_s=7)
        assert outcome.status == "timed_out" and outcome.seconds == 7.0 and outcome.exit_code is None
        lost = worker.await_outcome({"operation_id": "op9999"}, deadline_s=1)
        assert lost.status == "lost"
        assert worker.reattach(identity) == "lost"

    def test_cancel_check_cancels(self, tmp):
        bundle, op = build_bundle(tmp)
        worker = FakeWorker()
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=5, identity=dict(IDENT))
        assert worker.await_outcome(identity, deadline_s=5, cancel_check=lambda: True).status == "cancelled"

    def test_render_only_output_has_no_blend(self, tmp):
        bundle, op = build_bundle(tmp, mode="render_only", modules={}, module_order=[], evidence=None, blend_bytes=b"BLENDER")
        worker = FakeWorker()
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=5, identity=dict(IDENT))
        worker.await_outcome(identity, deadline_s=5)
        ing = worker.ingest(identity, tmp / "staging")
        assert ing.result["mode"] == "render_only" and ing.result["blend"] is None and ing.result["module_results"] == []
        assert [f["rel"] for f in ing.files] == ["blender.stdout.log", "front.png", "result.json", "side.png"]


# --------------------------------------------------------------------------- NativeFixtureWorker
class FakePopen:
    """Captures the argument vector and the environment; the 'process' has already exited with ``rc``."""
    instances: list["FakePopen"] = []
    rc = 0

    def __init__(self, cmd, *, stdout=None, stderr=None, env=None, cwd=None, **kw):
        self.cmd, self.env, self.cwd, self.kw = list(cmd), dict(env or {}), cwd, kw
        self.pid = 4242
        if stdout is not None:
            stdout.write(b"MODELER_HARNESS| done ok=True (fake)\n")
        if stderr is not None:
            stderr.write(b"fake stderr\n")
        FakePopen.instances.append(self)

    def poll(self):
        return self.rc

    def wait(self, timeout=None):
        return self.rc


class TestNativeFixtureWorker:
    def test_constructor_needs_an_allow_list(self):
        with pytest.raises(WorkerRefused, match="allow-list"):
            NativeFixtureWorker(set())
        with pytest.raises(WorkerRefused):
            NativeFixtureWorker(None)

    def test_unlisted_program_is_refused_before_any_process(self, tmp, monkeypatch):
        bundle, op = build_bundle(tmp)
        fake_exe = tmp / "blender.exe"
        fake_exe.write_bytes(b"not a program")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("subprocess.Popen was called for an unlisted program"))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("subprocess.run was called for an unlisted program"))
        worker = NativeFixtureWorker({"0" * 64}, blender=fake_exe)
        assert worker.describe()["synthetic"] is False and worker.describe()["allowed_program_sha256"] == ["0" * 64]
        with pytest.raises(WorkerRefused, match="not an allowed native fixture"):
            worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=10, identity=dict(IDENT))
        assert not (tmp / "work").exists() or not any((tmp / "work").iterdir())      # nothing was prepared for a refused program

    def test_environment_is_a_whitelist(self, tmp, monkeypatch):
        bundle, op = build_bundle(tmp)
        fake_exe = tmp / "blender.exe"
        fake_exe.write_bytes(b"not a program")
        monkeypatch.setenv("OPENAI_API_KEY", "dummy-not-a-key")
        monkeypatch.setenv("MESHY_API_KEY", "dummy-not-a-key")
        monkeypatch.setenv("APPDATA", str(tmp / "appdata"))
        FakePopen.instances = []
        FakePopen.rc = 0
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        worker = NativeFixtureWorker({op["program_set_sha256"]}, blender=fake_exe)
        work = tmp / "work"
        identity = worker.launch(bundle, op, work_dir=work, deadline_s=10, identity=dict(IDENT))
        assert len(FakePopen.instances) == 1
        proc = FakePopen.instances[0]
        assert set(proc.env) <= {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "PYTHONDONTWRITEBYTECODE"}, sorted(proc.env)
        assert "OPENAI_API_KEY" not in proc.env and "MESHY_API_KEY" not in proc.env and "APPDATA" not in proc.env
        assert not any(w in k.upper() for k in proc.env for w in CREDENTIAL_WORDS)
        assert proc.env["TEMP"] == str(work) and proc.env["HOME"] == str(work) and proc.env["USERPROFILE"] == str(work)
        assert proc.cmd[0] == str(fake_exe) and proc.cmd[1:4] == ["-b", "--factory-startup", "--python"]
        assert Path(proc.cmd[4]) == bundle / "lib" / "harness.py" and proc.cmd[5] == "--" and Path(proc.cmd[6]) == work / "job.json"
        assert proc.cwd == str(work)
        job = json.loads((work / "job.json").read_text(encoding="utf-8"))
        assert job["lib_dir"] == bundle.resolve().as_posix() + "/lib" and job["out_dir"] == (work / "out").resolve().as_posix()
        assert all(m["path"].startswith(bundle.resolve().as_posix() + "/program/") for m in job["modules"])
        assert identity["kind"] == "native-fixture" and identity["pid"] == 4242 and identity["operation_id"] == "op0001"
        outcome = worker.await_outcome(identity, deadline_s=10)
        assert outcome.status == "completed" and outcome.exit_code == 0 and "MODELER_HARNESS" in outcome.stdout_tail and "fake stderr" in outcome.stderr_tail
        ing = worker.ingest(identity, tmp / "staging")
        assert [f["rel"] for f in ing.files] == ["blender.stderr.log", "blender.stdout.log"] and ing.result is None and ing.synthetic is False

    def test_nonzero_exit_is_failed(self, tmp, monkeypatch):
        bundle, op = build_bundle(tmp)
        fake_exe = tmp / "blender.exe"
        fake_exe.write_bytes(b"not a program")
        FakePopen.instances = []
        FakePopen.rc = 3
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        worker = NativeFixtureWorker({op["program_set_sha256"]}, blender=fake_exe)
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=10, identity=dict(IDENT))
        outcome = worker.await_outcome(identity, deadline_s=10)
        assert outcome.status == "failed" and outcome.exit_code == 3
        assert worker.await_outcome({"operation_id": "op9999"}, deadline_s=1).status == "lost"
        assert worker.reattach(identity) == "lost"


# --------------------------------------------------------------------------- DockerWorker
def docker_cfg(**over) -> dict:
    cfg = {"image_digest": DIGEST, "image_ref": "lenses-worker:local", "blender_version": "5.2", "entry": "/opt/lenses/worker_entry.py",
           "resources": {"cpus": "2", "memory": "6g", "pids_limit": 512, "tmpfs_mb": 2048, "uid": 10001, "gid": 10001}}
    cfg.update(over)
    return cfg


def passing_doctor(cfg: dict) -> dict:
    return {"passed_utc": "2026-09-27T00:00:00", "fingerprint": worker_config_fingerprint(validate_worker_config(cfg))}


def inspect_body(cid: str, op_id: str, status: str = "running", exit_code: int = 0) -> bytes:
    return json.dumps([{"Id": cid, "State": {"Status": status, "ExitCode": exit_code}, "Config": {"Labels": {f"{ex.CONTAINER_LABEL}.operation": op_id}}}]).encode()


def ready_body(op_id: str, files: list[dict], *, ok: bool = True, exit_code: int = 0) -> bytes:
    return json.dumps({"protocol": "lenses_agentic_worker_v1", "operation_id": op_id, "status": "completed", "exit_code": exit_code, "ok": ok, "seconds": 1.5,
                       "files": files, "stdout_tail": "MODELER_HARNESS| done ok=%s" % ok, "stderr_tail": "warn: none", "note": "untrusted receipt"}).encode()


class FakeDocker:
    """A scripted ``docker`` command: ``runner(argv, input=None, timeout=None) -> (rc, stdout, stderr)``. Handlers are a
    (rc, out, err) triple, a list of triples consumed in order (the last one repeats), or a callable of argv."""

    def __init__(self, **handlers):
        self.handlers = handlers
        self.calls: list[tuple[list[str], object]] = []

    @staticmethod
    def key(argv: list[str]) -> str:
        sub = argv[1]
        if sub == "version":
            return "version"
        if sub == "image":
            return "image_inspect"
        if sub == "run":
            return "run_version" if argv[-1] == "--version" else "run"       # the doctor's probe, whichever way it names blender
        if sub == "exec":       # the output is streamed by tar inside the container (docker cp cannot read the tmpfs)
            return "cp_ready" if argv[-1] == "READY.json" else "cp_out"
        if sub == "cp":
            return "cp_ready" if argv[2].endswith("/READY.json") else "cp_out"
        return sub          # inspect | rm

    def __call__(self, argv, input=None, timeout=None):
        self.calls.append((list(argv), timeout))
        h = self.handlers.get(self.key(argv))
        if h is None:
            return 1, b"", f"unscripted docker call: {' '.join(argv[:3])}".encode()
        if callable(h):
            return h(argv)
        if isinstance(h, list):
            return h.pop(0) if len(h) > 1 else h[0]
        return h

    def subcommands(self) -> list[str]:
        return [self.key(argv) for argv, _ in self.calls]


def launched(tmp: Path, **handlers) -> tuple[DockerWorker, FakeDocker, dict, dict]:
    cfg = docker_cfg()
    cfg["doctor"] = passing_doctor(cfg)
    fake = FakeDocker(run=(0, (CID + "\n").encode(), b""), **handlers)
    worker = DockerWorker(cfg, runner=fake)
    bundle, op = build_bundle(tmp)
    identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
    return worker, fake, identity, op


class TestWorkerConfig:
    def test_a_local_image_id_is_a_valid_digest(self):
        """A locally built image has no registry digest (docker resolves <name>@sha256:<manifest> only for pulled/pushed images), so the
        config accepts the content-addressed image ID (docker image inspect --format '{{.Id}}') on its own; a tag never is."""
        local = "sha256:" + "b" * 64
        assert ex.validate_worker_config({"image_digest": local})["image_digest"] == local
        assert ex.validate_worker_config({"image_digest": "lenses-worker@" + local})["image_digest"] == "lenses-worker@" + local

    @pytest.mark.parametrize("over, msg", [
        ({"image_digest": "lenses-worker:latest"}, "image_digest"),
        ({"image_digest": "lenses-worker@sha256:abc"}, "image_digest"),
        ({"image_digest": "lenses-worker@sha256:" + "g" * 64}, "image_digest"),
        ({"resources": {"uid": 0}}, "positive integer|root"),          # uid 0 falls to the positive-integer rule first; either message refuses root
        ({"resources": {"gid": 0}}, "positive integer|root"),
        ({"resources": {"cpus": "two"}}, "cpus/memory"),
        ({"resources": {"memory": "6 gigs"}}, "cpus/memory"),
        ({"resources": {"memory": "6gb"}}, "cpus/memory"),
        ({"resources": {"pids_limit": 0}}, "positive integer"),
        ({"resources": {"tmpfs_mb": -1}}, "positive integer"),
        ({"resources": {"uid": "10001"}}, "positive integer"),
        ({"resources": {"gid": True}}, "positive integer"),
        ({"entry": "opt/lenses/worker_entry.py"}, "absolute path"),
    ])
    def test_rejections(self, over, msg):
        cfg = docker_cfg()
        if "resources" in over:
            over = {"resources": {**cfg["resources"], **over["resources"]}}
        cfg.update(over)
        with pytest.raises(WorkerError, match=msg):
            validate_worker_config(cfg)
        with pytest.raises(WorkerError, match=msg):
            DockerWorker(cfg, runner=FakeDocker())

    def test_not_an_object(self):
        with pytest.raises(WorkerError, match="must be an object"):
            validate_worker_config(["image"])

    def test_defaults_are_filled_and_the_input_untouched(self):
        cfg = {"image_digest": DIGEST}
        out = validate_worker_config(cfg)
        assert out["entry"] == "/opt/lenses/worker_entry.py" and out["resources"] == ex.DEFAULT_RESOURCES and out["image_digest"] == DIGEST
        assert cfg == {"image_digest": DIGEST}
        assert validate_worker_config(docker_cfg(resources={"cpus": 1.5, "memory": "4096m"}))["resources"]["cpus"] == 1.5


class TestDockerArgv:
    def test_isolation_argument_vector(self, tmp):
        cfg = docker_cfg()
        worker = DockerWorker(cfg, runner=FakeDocker())
        bundle, op = build_bundle(tmp)
        argv = worker.argv_run(bundle, op, dict(IDENT))
        assert argv[:3] == ["docker", "run", "--detach"]
        pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--")}
        assert pairs["--pull"] == "never" and pairs["--network"] == "none" and pairs["--cap-drop"] == "ALL"
        assert pairs["--security-opt"] == "no-new-privileges=true" and "--read-only" in argv
        assert pairs["--user"] == "10001:10001" and not pairs["--user"].startswith("0:") and not pairs["--user"].endswith(":0")
        assert pairs["--cpus"] == "2" and pairs["--memory"] == "6g" and pairs["--memory-swap"] == pairs["--memory"] and pairs["--pids-limit"] == "512"
        assert pairs["--name"] == f"lenses-agentic-{IDENT['job_short']}-op0001-1"
        tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
        assert len(tmpfs) == 1 and tmpfs[0].startswith("/work:") and "noexec" in tmpfs[0] and "size=2048m" in tmpfs[0] and "nosuid" in tmpfs[0]
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
        assert len(mounts) == 1
        m = dict(kv.split("=", 1) if "=" in kv else (kv, True) for kv in mounts[0].split(","))
        assert m["type"] == "bind" and m["source"] == bundle.resolve().as_posix() and m["target"] == "/bundle" and m.get("readonly") is True
        for forbidden in ("-v", "--volume", "--privileged", "--device", "--cap-add", "--network=host", "--pid"):
            assert forbidden not in argv
        assert argv.count("--mount") + argv.count("--volume") + argv.count("-v") == 1
        envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
        env = dict(e.split("=", 1) for e in envs)
        assert REQUIRED_ENV <= set(env), sorted(env)
        assert set(env) == PINNED_ENV, sorted(env)
        assert env["LENSES_TIME_LIMIT_S"] == "120" and env["HOME"].startswith("/work/") and env["TMPDIR"].startswith("/work/")
        assert env["LP_NUM_THREADS"] == pairs["--cpus"] == "2"         # llvmpipe's threads follow the CPU quota
        assert not any(w in k.upper() for k in env for w in CREDENTIAL_WORDS), sorted(env)
        assert "--env-file" not in argv
        i = argv.index(DIGEST)
        assert pairs["--entrypoint"] == "python3" and argv.index("--entrypoint") < i     # the process is named, whatever the image's ENTRYPOINT
        assert argv[i + 1:] == ["/opt/lenses/worker_entry.py", "/bundle/operation.json", "/work/out"]
        assert all(not a.startswith("--") for a in argv[i:])                       # nothing after the image is an option
        labels = [argv[k + 1] for k, a in enumerate(argv) if a == "--label"]
        assert f"{ex.CONTAINER_LABEL}.operation=op0001" in labels and f"{ex.CONTAINER_LABEL}.job={IDENT['job_short']}" in labels

    @pytest.mark.parametrize("cpus, threads", [("2", "2"), ("8", "8"), (1.5, "1"), ("0.5", "1"), ("16", "16")])
    def test_llvmpipe_threads_equal_the_integer_cpu_quota(self, tmp, cpus, threads):
        """LP_NUM_THREADS is the integer part of resources.cpus (never 0): unset, llvmpipe starts a thread per CPU the VM
        reports and the cgroup quota throttles them all (test-pilot-001: 9 of every 12 build minutes were EEVEE renders)."""
        assert ex.llvmpipe_threads(cpus) == int(threads)
        cfg = docker_cfg()
        cfg["resources"] = {**cfg["resources"], "cpus": cpus}
        worker = DockerWorker(cfg, runner=FakeDocker())
        bundle, op = build_bundle(tmp)
        argv = worker.argv_run(bundle, op, dict(IDENT))
        env = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--env")
        assert env["LP_NUM_THREADS"] == threads
        assert argv[argv.index("--cpus") + 1] == str(cpus)
        assert env["LP_NUM_THREADS"].isdigit() and int(env["LP_NUM_THREADS"]) >= 1
        assert argv.count("--env") == len(PINNED_ENV)

    def test_refuses_a_linked_bundle_path(self, tmp):
        worker = DockerWorker(docker_cfg(), runner=FakeDocker())
        bundle, op = build_bundle(tmp)
        link = tmp / "bundle_link"
        if not make_dir_link(bundle, link):
            pytest.skip("cannot create a directory junction/symlink here")
        with pytest.raises(WorkerError, match="link"):
            worker.argv_run(link, op, dict(IDENT))


class TestDockerLaunch:
    def test_launch_without_a_doctor_record_never_calls_docker(self, tmp):
        fake = FakeDocker(run=(0, (CID + "\n").encode(), b""))
        worker = DockerWorker(docker_cfg(), runner=fake)
        bundle, op = build_bundle(tmp)
        with pytest.raises(WorkerRefused, match="doctor"):
            worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
        assert fake.calls == []
        assert worker.describe()["doctor"] is None and worker.describe()["synthetic"] is False and worker.describe()["image_digest"] == DIGEST

    def test_launch_with_a_stale_doctor_fingerprint_is_refused(self, tmp):
        """The doctor record is bound to the config/image/harness fingerprint (docker_doctor, selftest); a record whose
        fingerprint no longer matches must not enable a launch (resume never re-runs the doctor)."""
        cfg = docker_cfg()
        cfg["doctor"] = {"passed_utc": "2026-09-27T00:00:00", "fingerprint": "0" * 64}
        fake = FakeDocker(run=(0, (CID + "\n").encode(), b""))
        worker = DockerWorker(cfg, runner=fake)
        bundle, op = build_bundle(tmp)
        with pytest.raises(WorkerRefused, match="doctor"):
            worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
        assert fake.calls == []

    def test_launch_runs_the_container_and_records_its_identity(self, tmp):
        worker, fake, identity, op = launched(tmp)
        assert fake.subcommands() == ["run"]
        argv, timeout = fake.calls[0]
        assert argv[:2] == ["docker", "run"] and DIGEST in argv and timeout is not None
        assert identity["kind"] == "docker" and identity["container_id"] == CID and identity["image_digest"] == DIGEST
        assert identity["container_name"] == f"lenses-agentic-{IDENT['job_short']}-op0001-1" and identity["operation_id"] == "op0001"
        assert isinstance(identity["started_unix"], float)
        assert argv[argv.index("--name") + 1] == identity["container_name"]

    def test_docker_run_failure_is_a_worker_error(self, tmp):
        cfg = docker_cfg()
        cfg["doctor"] = passing_doctor(cfg)
        worker = DockerWorker(cfg, runner=FakeDocker(run=(125, b"", b"docker: Error response from daemon: no such image")))
        bundle, op = build_bundle(tmp)
        with pytest.raises(WorkerError, match="docker run failed \\(125\\)"):
            worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))


class TestDockerAwait:
    def test_running_then_ready_is_completed_with_tails(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001"), b""),
                                             cp_ready=[(1, b"", b"no such file"), (0, make_tar({"READY.json": ready_body("op0001", [])}), b"")])
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.status == "completed" and outcome.exit_code == 0
        assert outcome.stdout_tail == "MODELER_HARNESS| done ok=True" and outcome.stderr_tail == "warn: none"
        assert fake.subcommands() == ["run", "inspect", "cp_ready"] or fake.subcommands()[:2] == ["run", "inspect"]
        assert "rm" not in fake.subcommands()                    # a finished output is held until ingestion

    def test_ready_not_ok_is_failed(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001"), b""),
                                             cp_ready=(0, make_tar({"READY.json": ready_body("op0001", [], ok=False, exit_code=1)}), b""))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.status == "failed" and outcome.exit_code == 1

    def test_container_not_found_is_lost(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(1, b"[]", b"Error: No such object"))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.status == "lost" and "not found" in outcome.note

    def test_exited_before_ready_is_interrupted(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001", status="exited", exit_code=137), b""), cp_ready=(1, b"", b"no such file"))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.status == "interrupted" and outcome.exit_code == 137 and "READY.json" in outcome.note

    def test_identity_mismatch_is_lost(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0007"), b""),
                                             cp_ready=(0, make_tar({"READY.json": ready_body("op0007", [])}), b""))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.status == "lost" and "another identity" in outcome.note
        assert "cp_ready" not in fake.subcommands()             # a foreign container's receipt is never read
        worker2, _fake2, identity2, _ = launched(tmp / "second", inspect=(0, inspect_body("d" * 64, "op0001"), b""))
        assert worker2.await_outcome(identity2, deadline_s=30).status == "lost"

    def test_deadline_stops_the_container(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001"), b""), cp_ready=(1, b"", b"no such file"), rm=(0, b"", b""))
        outcome = worker.await_outcome(identity, deadline_s=0)
        assert outcome.status == "timed_out"
        assert fake.subcommands()[-1] == "rm" and fake.calls[-1][0] == ["docker", "rm", "-f", identity["container_name"]]

    def test_cancel_stops_the_container(self, tmp):
        worker, fake, identity, _ = launched(tmp, rm=(0, b"", b""))
        outcome = worker.await_outcome(identity, deadline_s=30, cancel_check=lambda: True)
        assert outcome.status == "cancelled"
        assert fake.subcommands() == ["run", "rm"]


class TestDockerIngest:
    def output_tar(self, receipt_files: list[dict] | None, files: dict[str, bytes]) -> bytes:
        entries = {f"out/{rel}": data for rel, data in files.items()}
        if receipt_files is not None:
            entries["out/READY.json"] = ready_body("op0001", receipt_files)
        return make_tar(entries)

    def test_ingest_copies_validates_cross_checks_and_removes(self, tmp):
        result = {"ok": True, "renders": [{"id": "front", "path": "/work/out/renders/front.png"}], "blend": "/work/out/candidate.blend"}
        res_b, png, blend, log = json.dumps(result).encode(), ex.synthetic_png("front", "op0001", 64, 48), b"BLENDER-fake", b"log line\n"
        files = {"result.json": res_b, "renders/front.png": png, "candidate.blend": blend, "blender.stdout.log": log}
        receipt = [{"rel": rel, "sha256": sha(data), "bytes": len(data)} for rel, data in sorted(files.items())]
        worker, fake, identity, _ = launched(tmp, cp_out=(0, self.output_tar(receipt, files), b""), rm=(0, b"", b""))
        ing = worker.ingest(identity, tmp / "staging")
        assert fake.subcommands() == ["run", "cp_out", "rm"]
        assert fake.calls[1][0] == ["docker", "exec", identity["container_name"], "tar", "-C", "/work", "-cf", "-", "out"]
        assert fake.calls[2][0] == ["docker", "rm", "-f", identity["container_name"]]
        assert [f["rel"] for f in ing.files] == ["READY.json", "blender.stdout.log", "candidate.blend", "renders/front.png", "result.json"]
        assert (tmp / "staging" / "renders" / "front.png").read_bytes() == png and (tmp / "staging" / "result.json").read_bytes() == res_b
        assert ing.result == result and ing.problems is None and ing.synthetic is False
        assert result_is_untrusted_receipt(ing) == []

    def test_a_disagreeing_receipt_is_a_problem(self, tmp):
        res_b, png = b'{"ok": true}', b"PNG-bytes"
        files = {"result.json": res_b, "renders/front.png": png}
        receipt = [{"rel": "result.json", "sha256": sha(res_b), "bytes": len(res_b)}, {"rel": "renders/front.png", "sha256": "f" * 64, "bytes": len(png)},
                   {"rel": "parts.npz", "sha256": "e" * 64, "bytes": 5}]
        worker, fake, identity, _ = launched(tmp, cp_out=(0, self.output_tar(receipt, files), b""), rm=(0, b"", b""))
        ing = worker.ingest(identity, tmp / "staging")
        assert ing.problems is not None
        assert "worker receipt disagrees for renders/front.png" in ing.problems and "worker receipt disagrees for parts.npz" in ing.problems
        assert not any("result.json" in p for p in ing.problems)
        assert fake.subcommands()[-1] == "rm"

    def test_receipt_check_appends_to_existing_problems(self, tmp):
        res_b = b'{"ok": true}'
        receipt = [{"rel": "result.json", "sha256": "0" * 64, "bytes": 1}]
        tar = make_tar({"out/result.json": res_b, "out/READY.json": ready_body("op0001", receipt)}, [special("out/link", tarfile.SYMTYPE, linkname="x")])
        worker, fake, identity, _ = launched(tmp, cp_out=(0, tar, b""), rm=(0, b"", b""))
        ing = worker.ingest(identity, tmp / "staging")
        assert len(ing.problems) == 2 and any("not a regular file" in p for p in ing.problems) and "worker receipt disagrees for result.json" in ing.problems

    def test_cp_failure_is_a_worker_error_and_nothing_lands(self, tmp):
        worker, fake, identity, _ = launched(tmp, cp_out=(1, b"", b"Error: No such container"))
        with pytest.raises(WorkerError, match="docker exec tar failed"):
            worker.ingest(identity, tmp / "staging")
        assert not (tmp / "staging").exists() or not any((tmp / "staging").iterdir())

    def test_stop_is_bounded(self, tmp):
        worker, fake, identity, _ = launched(tmp, rm=(0, b"", b""))
        worker.stop(identity, timeout_s=5)
        argv, timeout = fake.calls[-1]
        assert argv == ["docker", "rm", "-f", identity["container_name"]]
        assert isinstance(timeout, (int, float)) and 5 <= timeout <= 60


class TestDockerReattach:
    def test_running_finished_lost(self, tmp):
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001"), b""), cp_ready=(1, b"", b"no such file"))
        assert worker.reattach(identity) == "running"
        worker, fake, identity, _ = launched(tmp / "b", inspect=(0, inspect_body(CID, "op0001", status="created"), b""), cp_ready=(1, b"", b""))
        assert worker.reattach(identity) == "running"
        worker, fake, identity, _ = launched(tmp / "c", inspect=(0, inspect_body(CID, "op0001"), b""),
                                             cp_ready=(0, make_tar({"READY.json": ready_body("op0001", [])}), b""))
        assert worker.reattach(identity) == "finished"
        worker, fake, identity, _ = launched(tmp / "d", inspect=(1, b"[]", b"No such object"))
        assert worker.reattach(identity) == "lost"
        worker, fake, identity, _ = launched(tmp / "e", inspect=(0, inspect_body(CID, "op0042"), b""))
        assert worker.reattach(identity) == "lost"
        worker, fake, identity, _ = launched(tmp / "f", inspect=(0, inspect_body(CID, "op0001", status="exited", exit_code=1), b""), cp_ready=(1, b"", b""))
        assert worker.reattach(identity) == "lost"


# --------------------------------------------------------------------------- docker_doctor
GOOD_VERSION = (0, b"27.5.1|27.5.1\n", b"")
IMAGE_OK = (0, b"sha256:" + b"a" * 64 + b"\n", b"")
BLENDER_OK = (0, b"Blender 5.2.0\n\tbuild date: 2026-06-01\n", b"")


class TestDockerDoctor:
    def test_cli_absent_is_blocking(self):
        fake = FakeDocker(version=(127, b"", b"docker executable not found"))
        out = docker_doctor(docker_cfg(), runner=fake)
        assert out["docker_cli"] == "absent" and out["blocking"] == ["docker CLI not found"]
        assert fake.subcommands() == ["version"]

    def test_engine_unavailable_is_blocking(self):
        fake = FakeDocker(version=(1, b"27.5.1|\n", b"Cannot connect to the Docker daemon at npipe:////./pipe/docker_engine"))
        out = docker_doctor(docker_cfg(), runner=fake)
        assert out["docker_cli"] == "27.5.1" and out["engine"] is None and out["config_valid"] is True
        assert any(b.startswith("Docker engine unavailable: Cannot connect") for b in out["blocking"])
        assert out["image_present"] is None and "image_inspect" not in fake.subcommands() and "run_version" not in fake.subcommands()
        assert out["doctor_pass"] is False and any("doctor self-test" in b for b in out["blocking"])

    def test_no_config_is_blocking(self):
        fake = FakeDocker(version=GOOD_VERSION)
        out = docker_doctor(None, runner=fake)
        assert out["config_valid"] is False and any(b.startswith("no worker config") for b in out["blocking"])
        assert fake.subcommands() == ["version"]

    def test_invalid_config_is_blocking(self):
        fake = FakeDocker(version=GOOD_VERSION)
        out = docker_doctor(docker_cfg(image_digest="lenses-worker:latest"), runner=fake)
        assert out["config_valid"] is False and any(b.startswith("worker config invalid") for b in out["blocking"])
        assert fake.subcommands() == ["version"]

    def test_image_absent_is_blocking(self):
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=(1, b"", b"Error: No such image"))
        out = docker_doctor(docker_cfg(), runner=fake)
        assert out["image_present"] is False and any("not present locally" in b for b in out["blocking"])
        assert "run_version" not in fake.subcommands()
        assert fake.calls[1][0] == ["docker", "image", "inspect", DIGEST, "--format", "{{.Id}}"]

    def test_blender_version_mismatch_is_blocking(self):
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=(0, b"Blender 4.2.3 LTS\n", b""))
        out = docker_doctor(docker_cfg(blender_version="5.2"), runner=fake)
        assert out["image_present"] is True and out["image_blender_version"] == "Blender 4.2.3 LTS"
        assert any("differs from the configured" in b for b in out["blocking"])
        run_argv = fake.calls[2][0]
        assert run_argv[:3] == ["docker", "run", "--rm"] and "--network" in run_argv and run_argv[run_argv.index("--network") + 1] == "none"
        assert run_argv[run_argv.index("--pull") + 1] == "never" and DIGEST in run_argv
        assert run_argv[run_argv.index("--entrypoint") + 1] == "blender" and run_argv[run_argv.index(DIGEST) + 1:] == ["--version"]

    def test_blender_version_probe_failure_is_blocking(self):
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=(1, b"", b"exec failed"))
        out = docker_doctor(docker_cfg(), runner=fake)
        assert out["image_blender_version"] is None and any("blender --version failed" in b for b in out["blocking"])

    def test_all_good_with_a_bound_doctor_record_passes(self):
        cfg = docker_cfg()
        cfg["doctor"] = passing_doctor(cfg)
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=BLENDER_OK)
        out = docker_doctor(cfg, runner=fake)
        assert out["blocking"] == [] and out["doctor_pass"] is True and out["engine"] == "27.5.1" and out["image_present"] is True
        assert out["fingerprint"] == cfg["doctor"]["fingerprint"] and out["config_valid"] is True
        assert fake.subcommands() == ["version", "image_inspect", "run_version"]

    def test_a_different_fingerprint_blocks_and_names_the_self_test(self):
        cfg = docker_cfg()
        cfg["doctor"] = {"passed_utc": "2026-09-27T00:00:00", "fingerprint": "1" * 64}
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=BLENDER_OK)
        out = docker_doctor(cfg, runner=fake)
        assert out["doctor_pass"] is False and len(out["blocking"]) == 1 and "doctor self-test" in out["blocking"][0] and "fingerprint" in out["blocking"][0]
        cfg2 = docker_cfg()
        cfg2["doctor"] = {"passed_utc": None, "fingerprint": passing_doctor(cfg2)["fingerprint"]}
        assert docker_doctor(cfg2, runner=FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=BLENDER_OK))["doctor_pass"] is False


class TestFingerprint:
    def test_changes_with_the_harness_bytes(self, tmp, monkeypatch):
        cfg = validate_worker_config(docker_cfg())
        base = worker_config_fingerprint(cfg)
        assert base == worker_config_fingerprint(dict(cfg, doctor={"passed_utc": "x", "fingerprint": "y"}))      # the record is not part of what it binds
        copy = tmp / "blender"
        copy.mkdir()
        for name in ("glasses_lib.py", "harness.py"):
            shutil.copyfile(BLENDER_DIR / name, copy / name)
        monkeypatch.setattr(ex, "BLENDER_DIR", copy)
        assert worker_config_fingerprint(cfg) == base
        with open(copy / "harness.py", "ab") as f:
            f.write(b"\n# one more comment line\n")
        changed = worker_config_fingerprint(cfg)
        assert changed != base
        with open(copy / "glasses_lib.py", "ab") as f:
            f.write(b"\n# lib changed\n")
        assert worker_config_fingerprint(cfg) not in (base, changed)
        assert worker_config_fingerprint(dict(cfg, blender_version="5.3")) != worker_config_fingerprint(cfg)

    def test_bundle_lib_hashes_match_the_fingerprinted_files(self, tmp):
        _, op = build_bundle(tmp)
        assert op["lib_sha256"] == {name: sha((BLENDER_DIR / name).read_bytes()) for name in ("glasses_lib.py", "harness.py")}


# --------------------------------------------------------------------------- native fixture parity (slow, host Blender)
GENERIC_FRAME = '''
# generic test frame (mm). Front plate with two lens holes, bevelled; lenses; straight temples.
W, Hh = 140.0, 50.0
outer = gl.rounded_rect(W, Hh, 10.0, n=160)
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(-32.0, -2.0))
front = gl.plate_with_holes(outer, [lensR, lensL], z_front=0.0, thickness=6.0, name="front_plate", part="frame", component="front")
gl.bevel(front, 1.5, segments=3)
acetate = gl.material_acetate("acetate_navy", (28, 40, 70))
gl.assign(front, acetate)
gold = gl.material_metal("gold", (212, 175, 90))
plate = gl.box((60.0, -5.0, -3.0), (10.0, 2.0, 8.0), "logo_R", "frame", "logo_R")
gl.assign(plate, gold)
gl.mirror_x(plate, "logo_L", "frame", "logo_L")
optics = gl.lens_optics(transmission_top_rgb=(0.25, 0.20, 0.14), transmission_bottom_rgb=(0.75, 0.70, 0.62))
lens_mat = gl.material_lens("lens", optics)
for side, outline in (("R", lensR), ("L", lensL)):
    lens = gl.lens_solid(gl.offset_closed(outline, 0.8), z_front=-1.5, name=f"lens_{side}", part=f"lens_{side}", base_curve=4.0, thickness=2.0)
    gl.assign(lens, lens_mat)
for side, sx in (("R", 1.0), ("L", -1.0)):
    secs = []
    for k, z in enumerate(np.linspace(-3.0, -140.0, 12)):
        w = 6.0 if k < 8 else 6.0 - (k - 8) * 0.8
        h = 8.0 if k < 6 else 8.0 - (k - 6) * 0.6
        y = 0.0 if k < 8 else -(k - 8) * 3.0
        secs.append(gl.section_rect((sx * (W / 2 - 3.0), y, z), w, h, (1, 0, 0), (0, 1, 0), radius=1.5, n=24))
    t = gl.loft(secs, f"temple_{side}", f"temple_{side}", "arm")
    gl.assign(t, acetate)
gl.set_bridge_underside((0.0, -7.0, -3.0))
gl.note("generic fixture built")
'''


@pytest.mark.slow
@pytest.mark.skipif(blender_executable() is None, reason="host Blender not installed")
class TestNativeFixtureParity:
    """The audited generic fixture through the NativeFixtureWorker: bundle -> host Blender -> ingest -> GLB -> contract.
    This is native fixture parity with the legacy worker, NOT a Docker isolation check (no engine here)."""

    def test_generic_fixture_builds_ingests_and_exports_a_contract_glb(self, tmp):
        from PIL import Image
        import numpy as np
        from modeler import export as mexport
        modules = {"frame": GENERIC_FRAME}
        renders = [{"id": "front_clay", "kind": "clay", "width": 160, "height": 80,
                    "camera": {"type": "orbit", "yaw": 0, "pitch": 0, "roll": 0, "ortho": True, "px_per_mm": 1.0, "target": "bbox"}}]
        bundle, op = build_bundle(tmp, modules=modules, module_order=["frame"], renders=renders, evidence=None, time_limit_s=240, samples=2)
        assert op["program_set_sha256"] == program_set_sha256(modules)
        worker = NativeFixtureWorker({op["program_set_sha256"]})
        identity = worker.launch(bundle, op, work_dir=tmp / "work", deadline_s=240, identity=dict(IDENT))
        outcome = worker.await_outcome(identity, deadline_s=240)
        assert outcome.status == "completed", (outcome.status, outcome.exit_code, outcome.stderr_tail[-800:], outcome.stdout_tail[-800:])
        assert "MODELER_HARNESS| done ok=True" in outcome.stdout_tail
        ing = worker.ingest(identity, tmp / "staging")
        assert ing.problems is None and ing.synthetic is False
        r = ing.result
        assert r is not None and r["ok"] is True, json.dumps(r, indent=1)[:3000]
        assert [m["ok"] for m in r["module_results"]] == [True] and "generic fixture built" in r["notes"]
        rels = {f["rel"] for f in ing.files}
        assert {"result.json", "parts.npz", "materials.json", "candidate.blend", "blender.stdout.log", "front_clay.png"} <= rels, sorted(rels)
        assert result_is_untrusted_receipt(ing) == []
        head = (tmp / "staging" / "candidate.blend").read_bytes()[:7]
        assert head == b"BLENDER" or head[:4] == b"\x28\xb5\x2f\xfd", head        # Blender 5.x saves zstd-compressed .blend files by default
        with Image.open(tmp / "staging" / "front_clay.png") as im:
            assert im.size == (160, 80)
            assert int((np.asarray(im.convert("RGBA"))[..., 3] > 0).sum()) > 200, "clay render is empty"
        rec = mexport.export_glb(tmp / "staging" / "parts.npz", tmp / "staging" / "materials.json", tmp / "model.glb")
        assert (tmp / "model.glb").is_file() and rec["bytes"] == (tmp / "model.glb").stat().st_size
        assert set(rec["parts"]) == {"frame", "lens_R", "lens_L", "temple_R", "temple_L"}
        c = rec["contract"]
        assert c["ok"], json.dumps({k: v for k, v in c.get("checks", {}).items() if not v.get("pass")}, indent=1, default=str)


# --------------------------------------------------------------------------- Docker ENTRYPOINT semantics (review finding "docker-argv")
# ``docker run [options] IMAGE [ARGS]`` runs  ENTRYPOINT + ARGS  (ARGS replace the image's CMD; ``--entrypoint X`` replaces the
# ENTRYPOINT with [X]). The worker image declares its supervisor as the ENTRYPOINT, so an argv that names the supervisor again
# after the image would make PID 1  python3 worker_entry.py python3 worker_entry.py operation.json /work/out  and entry.py
# would read op_path="python3". These tests compute the effective container command from the real Dockerfile.
WORKER_DIR = Path(ex.__file__).resolve().parent / "worker"
ENTRY_PY = WORKER_DIR / "entry.py"


def dockerfile_entrypoint_and_cmd(path: Path = WORKER_DIR / "Dockerfile") -> tuple[list[str], list[str]]:
    """The image's ENTRYPOINT and CMD as Docker stores them (the last instruction of each wins). Exec form is required: the
    shell form wraps the command in ``/bin/sh -c`` and ignores the run arguments entirely."""
    logical: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if logical and logical[-1].endswith("\\"):
            logical[-1] = logical[-1][:-1].rstrip() + " " + line
        else:
            logical.append(line)
    entrypoint: list[str] = []
    cmd: list[str] = []
    for line in logical:
        word, _, rest = line.partition(" ")
        if word.upper() not in ("ENTRYPOINT", "CMD"):
            continue
        rest = rest.strip()
        assert rest.startswith("["), f"{word} must use the exec (JSON array) form: {line!r}"
        value = json.loads(rest)
        assert isinstance(value, list) and all(isinstance(v, str) for v in value), line
        if word.upper() == "ENTRYPOINT":
            entrypoint = value
        else:
            cmd = value
    return entrypoint, cmd


def effective_container_command(argv: list[str], image: str) -> list[str]:
    """What PID 1 executes for this ``docker run`` argv against the image the Dockerfile builds."""
    i = argv.index(image)
    options, args = argv[:i], argv[i + 1:]
    entrypoint, cmd = dockerfile_entrypoint_and_cmd()
    override = None
    for k, tok in enumerate(options):
        if tok == "--entrypoint":
            override = options[k + 1]
        elif tok.startswith("--entrypoint="):
            override = tok.split("=", 1)[1]
    if override is not None:
        entrypoint = [override] if override else []          # --entrypoint "" clears the image's entrypoint
    return entrypoint + (args if args else cmd)


def load_entry_module():
    """Import worker/entry.py (stdlib only; nothing runs at import) so its argument contract can be unit-tested on the host."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("lenses_agentic_worker_entry", ENTRY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDockerEntrypointSemantics:
    def test_dockerfile_declares_the_supervisor_as_an_exec_form_entrypoint(self):
        entrypoint, cmd = dockerfile_entrypoint_and_cmd()
        assert entrypoint == ["python3", "/opt/lenses/worker_entry.py"] and cmd == []

    def test_run_argv_makes_pid1_the_supervisor_with_exactly_its_two_arguments(self, tmp):
        cfg = docker_cfg()
        worker = DockerWorker(cfg, runner=FakeDocker())
        bundle, op = build_bundle(tmp)
        argv = worker.argv_run(bundle, op, dict(IDENT))
        command = effective_container_command(argv, DIGEST)
        assert command == ["python3", cfg["entry"], "/bundle/operation.json", "/work/out"], command
        assert load_entry_module().parse_args(command[2:]) == ("/bundle/operation.json", "/work/out")

    def test_run_argv_names_the_entrypoint_explicitly_before_the_image(self, tmp):
        worker = DockerWorker(docker_cfg(entry="/opt/lenses/other_entry.py"), runner=FakeDocker())
        bundle, op = build_bundle(tmp)
        argv = worker.argv_run(bundle, op, dict(IDENT))
        k, i = argv.index("--entrypoint"), argv.index(DIGEST)
        assert k < i and argv[k + 1] == "python3" and argv.count("--entrypoint") == 1
        assert argv[i + 1:] == ["/opt/lenses/other_entry.py", "/bundle/operation.json", "/work/out"]       # the configured entry is honoured
        assert all(not a.startswith("-") for a in argv[i + 1:])                                             # nothing after the image is an option
        assert effective_container_command(argv, DIGEST) == ["python3", "/opt/lenses/other_entry.py", "/bundle/operation.json", "/work/out"]

    def test_doctor_version_probe_runs_blender_not_the_supervisor(self):
        fake = FakeDocker(version=GOOD_VERSION, image_inspect=IMAGE_OK, run_version=BLENDER_OK)
        out = docker_doctor(docker_cfg(), runner=fake)
        run_argv = fake.calls[2][0]
        assert run_argv[:3] == ["docker", "run", "--rm"]
        assert effective_container_command(run_argv, DIGEST) == ["blender", "--version"], run_argv
        i = run_argv.index(DIGEST)
        assert all(not a.startswith("-") for a in run_argv[i + 1:] if a != "--version")
        assert out["image_blender_version"] == "Blender 5.2.0" and "blender --version failed inside the image" not in out["blocking"]

    @pytest.mark.parametrize("argv", [
        [],
        ["/bundle/operation.json"],
        ["python3", "/opt/lenses/worker_entry.py", "/bundle/operation.json", "/work/out"],      # a repeated ENTRYPOINT (the old argv)
        ["python3", "/opt/lenses/worker_entry.py"],
        ["/bundle/operation.json", "/work/out", "extra"],
        ["/work/out", "/bundle/operation.json"],
        ["/bundle/operation.json", "/work/out/"],
        ["/bundle/operation.json", "/work/output"],
        ["/bundle/operation.jsonx", "/work/out"],
        ["operation.json", "/work/out"],
    ])
    def test_entry_refuses_anything_but_its_two_arguments(self, argv, capsys):
        entry = load_entry_module()
        with pytest.raises(SystemExit) as e:
            entry.parse_args(list(argv))
        assert e.value.code not in (0, None)
        assert "operation.json" in capsys.readouterr().err

    def test_entry_accepts_exactly_the_launch_contract(self):
        entry = load_entry_module()
        assert entry.parse_args(["/bundle/operation.json", "/work/out"]) == ("/bundle/operation.json", "/work/out")

    def test_entry_forwards_only_a_positive_integer_lp_num_threads_into_blenders_environment(self):
        """The supervisor hands Blender a fixed whitelist plus LP_NUM_THREADS when the host set it on the container (the
        integer --cpus quota, TestDockerArgv); anything else of the container environment never reaches Blender."""
        entry = load_entry_module()
        fixed = {"HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "BLENDER_USER_RESOURCES", "PATH", "PYTHONDONTWRITEBYTECODE"}
        base = entry.blender_env({})
        assert set(base) == fixed and base["HOME"] == "/work/home" and base["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        forwarded = entry.blender_env({"LP_NUM_THREADS": "8", "OPENAI_API_KEY": "dummy-not-a-key", "LENSES_TIME_LIMIT_S": "300", "LD_PRELOAD": "/x.so"})
        assert set(forwarded) == fixed | {"LP_NUM_THREADS"} and forwarded["LP_NUM_THREADS"] == "8"
        assert not any(w in k.upper() for k in forwarded for w in CREDENTIAL_WORDS)
        assert entry.blender_env({"LP_NUM_THREADS": " 2 "})["LP_NUM_THREADS"] == "2"
        for bad in ("", "0", "-1", "abc", "2.5", "8; rm -rf /"):
            assert "LP_NUM_THREADS" not in entry.blender_env({"LP_NUM_THREADS": bad}), bad
        assert entry.blender_env({"LP_NUM_THREADS": "8"}) == entry.blender_env({"LP_NUM_THREADS": "8"})     # a fresh dict each call, the constant untouched
        assert "LP_NUM_THREADS" not in entry.BLENDER_ENV
        assert entry.DEFAULT_SAMPLES == ex.DEFAULT_SAMPLES == 16

    @pytest.mark.parametrize("value", ["²", "¹²", "٣", "８", "8²"])
    def test_entry_refuses_non_ascii_digits_without_raising(self, value):
        """str.isdigit() is True for superscripts ('²') and other Unicode digits, and int() raises on some of them: the
        supervisor crashed before Blender started. Only ASCII digits are forwarded; a Unicode digit int() would accept
        ('٣', fullwidth '８') is refused too, so what reaches Blender is always the plain ASCII number."""
        entry = load_entry_module()
        env = entry.blender_env({"LP_NUM_THREADS": value})
        assert "LP_NUM_THREADS" not in env, (value, env)

    def test_entry_reads_lp_num_threads_from_its_own_process_environment(self, monkeypatch):
        entry = load_entry_module()
        monkeypatch.setenv("LP_NUM_THREADS", "8")
        assert entry.blender_env()["LP_NUM_THREADS"] == "8"
        monkeypatch.delenv("LP_NUM_THREADS")
        assert "LP_NUM_THREADS" not in entry.blender_env()

    def test_entry_process_refuses_a_repeated_entrypoint_before_any_work(self, tmp):
        # the host interpreter stands in for the container's python3; the refusal must come before any directory is created
        argv = [sys.executable, "-B", str(ENTRY_PY), "python3", "/opt/lenses/worker_entry.py", "/bundle/operation.json", "/work/out"]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=str(tmp))
        assert p.returncode == 2, (p.returncode, p.stderr[-500:])
        assert "operation.json" in p.stderr and "/work/out" in p.stderr and p.stdout == ""
        assert sorted(q.name for q in tmp.iterdir()) == []



# --------------------------------------------------------------------------- worker provenance (review 2026-09-28: MVP-10, INF-09, INF-12, INF-14, INF-17)
# test-pilot-002: every text mesh fell back to Blender's default font (the image had no fonts), the render logs carried three
# EGL_BAD_MATCH lines per render op and never named the rasterizer, the doctor's passed_utc was local time labelled UTC, and
# nothing recorded the host code that MEASURES a build (observer, cameras, see-through, lens colour, the AR harness).
# The real stderr of test-pilot-002 op0002 (byte-identical in all six render ops and the rebuild):
RUN2_STDERR = (
    "/bundle/lib/harness.py:188: DeprecationWarning: 'World.use_nodes' is expected to be removed in Blender 6.0\n"
    "  world.use_nodes = True\n"
    + "EGL Error (0x3009): EGL_BAD_MATCH: Arguments are inconsistent (for example, a valid context requires buffers not supplied by a valid surface).\n" * 3
    + "/bundle/lib/harness.py:214: DeprecationWarning: 'Material.use_nodes' is expected to be removed in Blender 6.0\n"
    "  mat.use_nodes = True\n")
LLVMPIPE = {"renderer": "llvmpipe (LLVM 15.0.6, 256 bits)", "vendor": "Mesa", "version": "4.5 (Core Profile) Mesa 22.3.6", "backend": "OPENGL", "device": "SOFTWARE"}
# the font files glasses_lib resolves first for 'sans' / 'serif' / 'mono' inside the worker (Debian bookworm package paths)
WORKER_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf")


def dockerfile_runs(path: Path = WORKER_DIR / "Dockerfile") -> list[str]:
    """The RUN instructions of the worker Dockerfile as single logical lines, in order."""
    logical: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if logical and logical[-1].endswith("\\"):
            logical[-1] = logical[-1][:-1].rstrip() + " " + line
        else:
            logical.append(line)
    return [line[4:].strip() for line in logical if line.upper().startswith("RUN ")]


class TestWorkerImageFonts:
    def test_the_image_installs_the_font_packages_and_fails_its_build_without_the_files(self):
        runs = dockerfile_runs()
        font_runs = [r for r in runs if "fonts-dejavu-core" in r]
        assert len(font_runs) == 1, runs
        run = font_runs[0]
        assert "fonts-liberation" in run and "--no-install-recommends" in run
        for path in WORKER_FONTS:
            assert f"test -s {path}" in run, path          # a renamed package fails the build instead of falling back silently
        # the font layer sits after the Blender layer, so adding it never re-downloads Blender, and the checksum stays enforced
        blender = next(i for i, r in enumerate(runs) if "sha256sum -c" in r)
        assert '"$BLENDER_SHA256  /tmp/blender.tar.xz"' in runs[blender]
        assert runs.index(run) > blender

    def test_the_image_records_the_font_bytes_and_the_base_is_pinned(self):
        run = next(r for r in dockerfile_runs() if "fonts-dejavu-core" in r)
        assert "sha256sum" in run and "/opt/lenses/fonts.sha256" in run
        text = (WORKER_DIR / "Dockerfile").read_text(encoding="utf-8")
        base = [line for line in text.splitlines() if line.startswith("FROM ")]
        assert len(base) == 1 and "@sha256:" in base[0], base          # the base image by digest: a rebuild cannot drift
        assert "REPLACE_WITH" in text                                  # the Blender checksum stays a required build argument

    def test_entry_reads_the_font_inventory_into_its_receipt(self, tmp):
        entry = load_entry_module()
        inv = tmp / "fonts.sha256"
        inv.write_text("".join(f"{'ab' * 32}  {p}\n" for p in WORKER_FONTS) + "garbage line\n", encoding="utf-8")
        fonts = entry.font_inventory(str(inv))
        assert fonts == {p: "ab" * 32 for p in WORKER_FONTS}
        assert entry.font_inventory(str(tmp / "absent")) == {}


class TestWorkerGlProbe:
    def test_blender_argv_installs_the_probe_before_the_harness(self):
        entry = load_entry_module()
        argv = entry.blender_argv("/bundle", "/work/job.json")
        assert argv[:3] == ["blender", "-b", "--factory-startup"]
        k, h = argv.index("--python-expr"), argv.index("--python")
        assert k < h and argv[k + 1] == entry.GL_PROBE and argv[h + 1] == "/bundle/lib/harness.py"
        assert argv[-2:] == ["--", "/work/job.json"]
        compile(entry.GL_PROBE, "<gl-probe>", "exec")
        assert "persistent" in entry.GL_PROBE and "render_post" in entry.GL_PROBE       # survives open_mainfile (render_only)

    def test_parse_gl_reads_the_probe_line_and_bounds_it(self):
        entry = load_entry_module()
        out = "00:02 blend | Read\n" + entry.GL_MARK + json.dumps(LLVMPIPE) + "\nMODELER_HARNESS| done ok=True\n"
        assert entry.parse_gl(out) == LLVMPIPE
        assert entry.parse_gl("no probe line\n") is None
        long = entry.parse_gl(entry.GL_MARK + json.dumps({"renderer": "x" * 5000, "other": 1}) + "\n")
        assert long == {"renderer": "x" * 200}                     # known keys only, bounded
        assert entry.parse_gl(entry.GL_MARK + "{not json\n") == {"error": "unreadable probe line"}

    def test_tidy_stderr_removes_the_egl_probe_lines_only_when_a_context_was_made(self):
        entry = load_entry_module()
        tidy, removed = entry.tidy_stderr(RUN2_STDERR, LLVMPIPE)
        assert removed == 3 and "EGL_BAD_MATCH: Arguments" not in tidy
        assert "use_nodes" in tidy and tidy.count("DeprecationWarning") == 2              # everything else is kept, in order
        last = tidy.rstrip("\n").splitlines()[-1]
        assert last.startswith("[worker supervisor]") and "3" in last and "EGL_BAD_MATCH" in last and LLVMPIPE["renderer"] in last
        # no GL context (no render, or the probe failed): the lines are the diagnosis and stay verbatim
        for gl in (None, {"error": "SystemError: GPU not initialised"}):
            assert entry.tidy_stderr(RUN2_STDERR, gl) == (RUN2_STDERR, 0)
        assert entry.tidy_stderr("plain warning\n", LLVMPIPE) == ("plain warning\n", 0)

    def test_finish_logs_rewrites_stderr_and_returns_the_gl_record(self, tmp):
        entry = load_entry_module()
        so, se = tmp / "blender.stdout.log", tmp / "blender.stderr.log"
        so.write_text("x\n" + entry.GL_MARK + json.dumps(LLVMPIPE) + "\n", encoding="utf-8")
        se.write_text(RUN2_STDERR, encoding="utf-8")
        gl = entry.finish_logs(str(so), str(se))
        assert gl == dict(LLVMPIPE, egl_bad_match_lines_removed=3)
        assert "EGL_BAD_MATCH: Arguments" not in se.read_text(encoding="utf-8")
        assert entry.finish_logs(str(tmp / "none.log"), str(tmp / "none2.log")) is None

    def test_the_docker_outcome_carries_the_gl_record_from_the_receipt(self, tmp):
        body = json.loads(ready_body("op0001", []))
        body["gl"] = dict(LLVMPIPE, egl_bad_match_lines_removed=3, junk="x" * 900)
        body["fonts"] = {WORKER_FONTS[0]: "cd" * 32}
        worker, fake, identity, _ = launched(tmp, inspect=(0, inspect_body(CID, "op0001"), b""), cp_ready=(0, make_tar({"READY.json": json.dumps(body).encode()}), b""))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.gl == dict(LLVMPIPE, egl_bad_match_lines_removed=3)          # untrusted receipt: known keys only
        assert outcome.fonts == {WORKER_FONTS[0]: "cd" * 32}
        assert outcome.to_dict()["gl"]["renderer"] == LLVMPIPE["renderer"]
        # an older image writes neither: nothing is invented
        (tmp / "old").mkdir()
        worker, fake, identity, _ = launched(tmp / "old", inspect=(0, inspect_body(CID, "op0001"), b""), cp_ready=(0, make_tar({"READY.json": ready_body("op0001", [])}), b""))
        outcome = worker.await_outcome(identity, deadline_s=30)
        assert outcome.gl is None and outcome.fonts is None

    @pytest.mark.slow
    @pytest.mark.skipif(blender_executable() is None, reason="host Blender not installed")
    def test_the_probe_names_the_renderer_in_real_blender(self, tmp):
        entry = load_entry_module()
        script = tmp / "render.py"
        script.write_text("import bpy, os\nsc = bpy.context.scene\nsc.render.engine = 'BLENDER_EEVEE'\nsc.render.resolution_x, sc.render.resolution_y = 32, 24\n"
                          "sc.render.filepath = os.path.join(os.getcwd(), 't.png')\nbpy.ops.render.render(write_still=True)\n", encoding="utf-8")
        p = subprocess.run([str(blender_executable()), "-b", "--factory-startup", "--python-expr", entry.GL_PROBE, "--python", str(script)],
                           capture_output=True, text=True, timeout=300, cwd=str(tmp))
        gl = entry.parse_gl(p.stdout)
        assert gl and gl.get("renderer") and "error" not in gl, (p.stdout[-800:], p.stderr[-800:])
        assert p.stdout.count(entry.GL_MARK) == 1


class TestMeasurementFingerprint:
    def test_it_covers_the_measurement_code_and_the_ar_harness(self):
        fp = ex.measurement_fingerprint()
        py, ar = fp["python"], fp["ar"]
        for rel in ("modeler/observe.py", "modeler/evaluate.py", "modeler/see_through.py", "modeler/lens_colour.py", "modeler/export.py", "bsa/cameras.py",
                    "bsa/archeck.py", "bsa/raster.py", "bsa/core.py", "bsa/front.py", "bsa/lens.py", "bsa/tryon.py", "bsa/intake.py", "bsa/export.py",
                    "bsa/contract.py", "reconstruction/camera.py", "reconstruction/mesh.py", "qa/provider_comparison.py"):
            assert rel in py, rel
        assert not any(k.startswith("modeler/agentic/") for k in py)          # the route itself is python_sources (cli.source_fingerprints)
        assert "modeler/blender/glasses_lib.py" not in py                     # the library is the bundle's lib_sha256
        for rel in ("qa/provider-comparison.mjs", "qa/provider-comparison.html", "qa/provider-comparison-ar.html", "package.json", "src/render/renderer.ts"):
            assert rel in ar, rel
        assert all(len(v) == 64 for v in list(py.values()) + list(ar.values()))
        assert fp["sha256"] == ex.measurement_fingerprint()["sha256"] and fp["missing"] == []

    def test_it_changes_with_one_measurement_file_and_not_with_the_worker(self, tmp):
        root = tmp / "repo"
        shutil.copytree(ex.AUTOMATION / "modeler", root / "automation" / "modeler", ignore=shutil.ignore_patterns("__pycache__", "blender", "agentic"))
        for pkg in ("bsa", "reconstruction", "qa"):
            shutil.copytree(ex.AUTOMATION / pkg, root / "automation" / pkg, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "ar").mkdir()
        shutil.copytree(ex.AR_ROOT / "qa", root / "ar" / "qa")
        shutil.copytree(ex.AR_ROOT / "src", root / "ar" / "src")
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ex.AR_ROOT / name, root / "ar" / name)
        a = ex.measurement_fingerprint(automation=root / "automation", ar=root / "ar")
        assert a["python"] == ex.measurement_fingerprint()["python"] and a["sha256"] == ex.measurement_fingerprint()["sha256"]
        doctor = worker_config_fingerprint(validate_worker_config(docker_cfg()))
        with open(root / "automation" / "bsa" / "cameras.py", "ab") as f:
            f.write(b"\n# a camera-fit change\n")
        b = ex.measurement_fingerprint(automation=root / "automation", ar=root / "ar")
        assert b["sha256"] != a["sha256"] and [k for k in a["python"] if a["python"][k] != b["python"][k]] == ["bsa/cameras.py"]
        with open(root / "ar" / "qa" / "provider-comparison-ar.html", "ab") as f:
            f.write(b"\n<!-- harness change -->\n")
        c = ex.measurement_fingerprint(automation=root / "automation", ar=root / "ar")
        assert [k for k in c["ar"] if c["ar"][k] != b["ar"][k]] == ["qa/provider-comparison-ar.html"]
        # the doctor record binds the worker (config, harness, library), never the host's measurement code
        assert worker_config_fingerprint(validate_worker_config(docker_cfg())) == doctor
        (root / "automation" / "bsa" / "lens.py").unlink()
        assert "bsa/lens.py" in ex.measurement_fingerprint(automation=root / "automation", ar=root / "ar")["missing"]

    def test_every_bundle_records_it(self, tmp):
        _, op = build_bundle(tmp)
        fp = ex.measurement_fingerprint()
        assert op["measurement_fingerprint"] == fp
        _, op2 = build_bundle(tmp / "r", mode="render_only", modules={}, module_order=[], evidence=None, blend_bytes=b"BLEND")
        assert op2["measurement_fingerprint"]["sha256"] == fp["sha256"]

    def test_diff_names_every_changed_added_and_removed_file(self):
        a = {"python": {"bsa/lens.py": "1" * 64, "modeler/observe.py": "2" * 64, "bsa/front.py": "3" * 64}, "ar": {"src/a.ts": "4" * 64}}
        b = {"python": {"bsa/lens.py": "1" * 64, "modeler/observe.py": "9" * 64, "bsa/tryon.py": "5" * 64}, "ar": {"src/a.ts": "8" * 64}}
        assert ex.fingerprint_diff(a["python"], b["python"]) == {"changed": ["modeler/observe.py"], "added": ["bsa/tryon.py"], "removed": ["bsa/front.py"]}
        assert ex.fingerprint_diff(a["ar"], a["ar"]) == {"changed": [], "added": [], "removed": []}


class TestUtcStamps:
    def test_utc_stamp_carries_its_zone(self):
        s = ex.utc_stamp()
        assert s.endswith("+00:00") and len(s) == len("2026-09-28T10:04:00+00:00")

    def test_the_fake_worker_start_time_is_utc(self, tmp):
        bundle, op = build_bundle(tmp)
        identity = FakeWorker().launch(bundle, op, work_dir=tmp / "work", deadline_s=60, identity=dict(IDENT))
        assert identity["started_utc"].endswith("+00:00")
